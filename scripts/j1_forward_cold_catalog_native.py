"""Independent, fixed read-only readers for the preserved old ingress scope.

Importing this module does not read native state or invoke Docker. These are
measurement predicates used by the standalone guard; no controller or generic
Host implementation belongs to that bootstrap dependency closure.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_history as historical
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io

ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
DOCKER = ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock"]
CONTEXT_FIELDS = frozenset(
    (
        "Id",
        "Names",
        "LoadState",
        "Type",
        "NotifyAccess",
        "FragmentPath",
        "SourcePath",
        "NeedDaemonReload",
    )
)
CATALOG_DEFAULTS = {
    "autostop-telegram.service": "/var/lib/autostop-telegram",
    "autostop-work-telegram.service": "/var/lib/autostop-work-telegram",
    "autostop-codex-wake.service": "/var/lib/autostop-work-telegram",
}


def _read(provider, ref):
    cold.reference(ref)
    raw = provider.read_reference(ref)
    cold.need(io.sha(raw) == ref["sha256"], "producer_reference_changed")
    return io.document(raw)


def _files(document):
    """Decode the original guard's pinned environment-file inventory."""
    value = document["environment_files"]
    cold.need(type(value) is list, "catalog_original_environment_inventory")
    for row in value:
        cold.need(
            type(row) is dict and {"path", "sha256"} <= set(row), "catalog_original_environment_pin"
        )
        yield {name: row[name] for name in ("path", "sha256")}


def _dotenv(raw):
    # This is the old config.py's literal setdefault parser, not shell eval.
    result = {}
    for line in raw.decode("utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result.setdefault(key, value)
    return result


def accepted_original_inputs(unit, *, provider, history):
    """Read preserved inputs as provenance, never call or inherit warm guards."""
    from scripts import j1_forward_authority as old

    capsule_ref = {"path": str(old.CAPSULE), "sha256": old.PINS[old.CAPSULE]}
    capsule = _read(provider, capsule_ref)
    policy = _read(provider, capsule["policy"])
    profile = capsule["profiles"][unit]
    accepted = _read(provider, profile["input"])
    request_raw = provider.read_reference(profile["request"])
    request = io.document(request_raw)
    baseline = _read(provider, history["anchor"]["baseline_ref"])
    binding = policy["bindings"][unit]
    preserved = baseline["native"]["profiles"][unit]
    environment = accepted["stable_input_environment"]
    cold.need(
        accepted["schema"] == "autostop.j1.input-observation.v1"
        and accepted["scope"] == "production_quiet_window"
        and accepted["attempt_id"] == request["attempt_id"]
        and accepted["unit"] == request["unit"] == unit
        and accepted["request_sha256"] == io.sha(request_raw)
        and cold.digest(environment)
        == accepted["stable_environment_sha256"]
        == binding["stable_environment_sha256"]
        == preserved["stable_environment_sha256"]
        and cold.digest(binding) == preserved["binding_sha256"]
        and all(binding[key] == preserved[key] for key in preserved if key != "binding_sha256"),
        "catalog_original_input_chain",
    )
    cold.need(
        type(environment) is dict
        and all(type(k) is str and type(v) is str for k, v in environment.items()),
        "catalog_original_environment",
    )
    environment = dict(environment)
    # Local dotenv files can change DB paths. Every file must have belonged to
    # the original source-qualified binding, including its exact bytes.
    pinned = {row["path"]: row for row in _files(binding)}
    env_file = environment.get("AUTOSTOP_MANAGER_ENV_FILE")
    paths = (
        [part.strip() for part in env_file.split(os.pathsep) if part.strip()]
        if env_file
        else [binding["source_root"] + "/.env", binding["source_root"] + "/.env.local"]
    )
    for path in paths:
        if path == "/dev/null":
            continue
        if path not in pinned:
            try:
                Path(path).lstat()
            except FileNotFoundError:
                continue
            raise io.Refusal("cold_catalog_unpinned_original_dotenv")
        if pinned[path]["sha256"] is None:
            try:
                Path(path).lstat()
            except FileNotFoundError:
                continue
            raise io.Refusal("cold_catalog_original_absence_changed")
        for key, value in _dotenv(provider.read_reference(pinned[path])).items():
            environment.setdefault(key, value)
    return profile, binding, environment


def verify_catalog_path(unit, source, policy, *, provider, history):
    cold.need(
        provider.scope == "production_native" and unit in cold.ALLOWED, "catalog_native_scope"
    )
    profile, original, environment = accepted_original_inputs(
        unit, provider=provider, history=history
    )
    cold.need(source["path_binding_ref"] == profile["input"], "catalog_source_not_original_input")
    kind = source["kind"]
    if kind == "manager_sqlite":
        path = environment.get(
            "AUTOSTOP_MANAGER_DB", original["source_root"] + "/data/autostop_manager.sqlite3"
        )
    elif kind == "scheduler_sqlite":
        path = environment.get(
            "AUTOSTOP_AUTOMATION_DB", "/var/lib/autostop-manager-scheduler/registry.sqlite3"
        )
    elif kind == "j1_sqlite":
        root = (
            environment.get("AUTOSTOP_J1_CACHE_DIR")
            or environment.get("AUTOSTOP_J1_DATA_DIR")
            or "/var/cache/autostop-j1"
        )
        path = root + "/research.sqlite3"
        cold.need(original["ordinary_database"]["path"] == path, "catalog_j1_original_database")
    else:
        cold.need(unit in CATALOG_DEFAULTS, "catalog_profile_source_kind")
        # The original reviewed Telegram CLI accepts a state-dir argument;
        # custom paths require an explicitly source-defined decoder, not seeds.
        arguments = original["arguments"]
        cold.need(type(arguments) is list, "catalog_original_arguments")
        root = CATALOG_DEFAULTS[unit]
        if "--state-dir" in arguments:
            index = arguments.index("--state-dir")
            cold.need(index + 1 < len(arguments), "catalog_state_argument")
            root = arguments[index + 1]
        path = root + ("/inbound.sqlite3" if kind == "telegram_sqlite" else "/idempotency.json")
    cold.need(
        type(path) is str and path.startswith("/") and "~" not in path and source["path"] == path,
        "catalog_source_path_not_effective",
    )
    # Current old unit metadata may no longer have a PID; its source/context
    # still must be the preserved one with the owned false condition retained.
    context = _read(provider, profile["unit_context_ref"])
    cold.need(context == profile["unit_context"], "catalog_original_unit_context")
    current = provider.systemd(unit)
    cold.need(
        all(current[key] == context[key] for key in CONTEXT_FIELDS if key in current),
        "catalog_original_unit_changed",
    )


def verify_old_crm_ingress(reference, *, provider, binding):
    """Container retirement does not by itself attest an external proxy cut."""
    document = _read(provider, reference)
    cold.fields(
        document,
        {
            "schema",
            "scope",
            "binding",
            "origin_plan_ref",
            "stop_intent_ref",
            "container_id",
            "init_pid",
            "init_start_ticks",
            "http_ingress_config_ref",
        },
        "old_crm_ingress_fields",
    )
    cold.need(
        document["schema"] == "autostop.j1.cold-old-crm-ingress.v1"
        and document["scope"] == provider.scope
        and document["binding"] == binding,
        "old_crm_ingress_binding",
    )
    cold.need(document["origin_plan_ref"] == historical.ORIGIN_REFS["plan_ref"], "old_crm_origin")
    intent = _read(provider, document["stop_intent_ref"])
    cold.need(
        intent["binding"] == binding
        and intent["container_id"] == document["container_id"]
        and intent["init_pid"] == document["init_pid"]
        and intent["init_start_ticks"] == document["init_start_ticks"],
        "old_crm_stop_identity",
    )
    if document["init_pid"]:
        native.retired_incarnation(document["init_pid"], document["init_start_ticks"])
    else:
        cold.need(
            document["init_start_ticks"] == 0 and intent["running"] is False,
            "old_crm_unbound_birth",
        )

    def run(argv):
        result = subprocess.run(
            DOCKER + argv,
            env=ENVIRONMENT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5,
            check=False,
        )
        cold.need(
            result.returncode == 0 and len(result.stdout) <= 4 * 1024**2, "old_crm_inspect_failed"
        )
        return result.stdout

    container_ids = (
        run(["container", "ls", "--all", "--no-trunc", "--format", "{{.ID}}"]).decode().splitlines()
    )
    cold.need(
        len(container_ids) <= 4096
        and all(re.fullmatch(r"[0-9a-f]{64}", row) for row in container_ids),
        "old_crm_container_inventory",
    )
    if document["container_id"] in container_ids:
        rows = io.document(run(["inspect", document["container_id"]]))
        cold.need(
            type(rows) is list
            and len(rows) == 1
            and rows[0]["Id"] == document["container_id"]
            and rows[0]["State"]["Running"] is False
            and rows[0]["State"]["Pid"] == 0,
            "old_crm_not_retired",
        )
    config = _read(provider, document["http_ingress_config_ref"])
    cold.need(
        config["schema"] == "autostop.j1.cold-http-ingress.v1"
        and all(
            config[key] == value
            for key, value in (
                ("attempt_id", binding["attempt_id"]),
                ("boot_id", binding["boot_id"]),
                ("generation", binding["generation"]),
                ("epoch_id", binding["generation"]),
                ("candidate_crm_sha", binding["candidate_crm_sha"]),
                ("candidate_manager_sha", binding["candidate_manager_sha"]),
            )
        ),
        "old_crm_fresh_http_namespace",
    )
    # Actual new-process/mount/readback enforcement is required at ready and
    # marker removal, after the replacement exists; this is closed preparation.
    return document
