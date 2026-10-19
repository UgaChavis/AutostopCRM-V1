"""Strict source inputs for future cold preparation; references are not permits."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from pathlib import Path

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_io as io
from scripts import j1_forward_recovery_state as recovery
from scripts import j1_manager_transition_source as source

SCHEMA = "autostop.j1.cold-producer-inputs.v1"
FIELDS = {
    "schema",
    "scope",
    "binding",
    "predecessor_ref",
    "origin_ref",
    "manager",
    "runtime_artifact_ref",
    "runtime_artifact_provenance_ref",
    "runtime_env",
    "credentials",
    "kernel",
    "catalog_policy_ref",
    "review_key_ref",
    "fresh_role_material_ref",
    "telegram_frontier_policy_ref",
}
KERNEL_FIELDS = {"appserver_ref", "protocol_index_ref", "authorization_ref", "cache_source_ref"}
ORIGIN_FIELDS = recovery.ORIGIN_FIELDS - {"recovery_code_ref", "ci_ref"}


def read(reference, provider):
    cold.reference(reference)
    raw = provider.read_reference(reference)
    cold.need(type(raw) is bytes and io.sha(raw) == reference["sha256"], "producer_input_hash")
    return io.document(raw)


def validate(document, request):
    cold.fields(document, FIELDS, "producer_input_fields")
    cold.need(
        document["schema"] == SCHEMA and document["scope"] == "production_native",
        "producer_input_scope",
    )
    for key in FIELDS - {
        "schema",
        "scope",
        "binding",
        "manager",
        "runtime_env",
        "credentials",
        "kernel",
    }:
        cold.reference(document[key])
    cold._binding(document["binding"], now_ns=time.monotonic_ns())
    declared = document["binding"]
    cold.need(
        all(
            declared[key] == request[value]
            for key, value in (
                ("attempt_id", "attempt_id"),
                ("boot_id", "boot_id"),
                ("generation", "owner_generation"),
                ("candidate_manager_sha", "candidate_manager_sha"),
            )
        )
        and declared["candidate_crm_sha"] == request["candidate"]["revision"],
        "producer_declared_binding",
    )
    cold.fields(document["kernel"], KERNEL_FIELDS, "producer_kernel_inputs")
    for reference in document["kernel"].values():
        cold.reference(reference)
    manager = document["manager"]
    cold.fields(manager, {"checkout", "revision", "tree", "ci_ref"}, "producer_manager_source")
    cold.need(
        type(manager["checkout"]) is str
        and Path(manager["checkout"]).is_absolute()
        and ".." not in Path(manager["checkout"]).parts
        and manager["revision"] == request["candidate_manager_sha"]
        and type(manager["tree"]) is str
        and cold.SHA1.fullmatch(manager["tree"]),
        "producer_manager_identity",
    )
    cold.reference(manager["ci_ref"])
    for key in ("runtime_env", "credentials"):
        cold.fields(document[key], epochs.CONSUMERS, "producer_all_seven_" + key)
    for reference in document["runtime_env"].values():
        cold.reference(reference)
    for credentials in document["credentials"].values():
        cold.fields(credentials, {"uid", "gid", "groups"}, "producer_credentials")
        cold.need(
            all(type(credentials[key]) is int and credentials[key] >= 0 for key in ("uid", "gid"))
            and type(credentials["groups"]) is list
            and credentials["groups"] == sorted(set(credentials["groups"]))
            and all(type(group) is int and group >= 0 for group in credentials["groups"]),
            "producer_credentials_values",
        )
    return document


def binding(request, deployment_identity, *, boot_id, consumer_sha256, now=None, now_ns=None):
    from scripts import j1_forward_state as state

    current = datetime.now(UTC) if now is None else now
    monotonic = time.monotonic_ns() if now_ns is None else now_ns
    left = (state._timestamp(request["deadline_utc"]) - current).total_seconds()
    cold.need(0 < left <= 600 and request["boot_id"] == boot_id, "producer_request_deadline_boot")
    # The original E deadline is never extended by monotonic conversion.
    document = {
        "attempt_id": request["attempt_id"],
        "boot_id": boot_id,
        "generation": request["owner_generation"],
        "consumer_sha256": consumer_sha256,
        "candidate_crm_sha": request["candidate"]["revision"],
        "candidate_manager_sha": request["candidate_manager_sha"],
        "deployment_lock_identity": deployment_identity,
        "not_before_ns": monotonic,
        "deadline_ns": monotonic + int(left * 1e9),
    }
    return document


def manager_source(document, *, verify_github):
    """Read actual Git objects and working bytes; no fetch, checkout or app import."""
    manager = document["manager"]
    index, contents = source.collect_source(
        manager["checkout"], manager["revision"], manager["tree"]
    )
    cached = io.document(source.reference_bytes(manager["ci_ref"]))
    source.cached_ci(cached, "manager", manager["revision"])
    current = verify_github(cached["run"]["id"])
    verified = source.cached_ci(current, "manager", manager["revision"])
    cold.need(
        current["run"]["head_branch"] == "AutostopManager"
        and current["run"]["event"] == "push"
        and current["run"]["run_attempt"] == cached["run"]["run_attempt"],
        "producer_manager_production_push_ci",
    )
    # Preserve exact byte-clean source and explicit metadata. Git's OIDs in
    # index were recomputed by collect_source; CI is independently reread above.
    cold.need(
        {"scripts/cold-role-start.py", "autostop_manager/cold_runtime.py"} <= set(contents),
        "producer_manager_cold_source_required",
    )
    files = dict(contents)
    files["REVISION"] = (manager["revision"] + "\n").encode()
    names = sorted(files)
    manifest = "".join(f"{io.sha(files[name])}  ./{name}\n" for name in names).encode()
    files["MANIFEST.sha256"] = manifest
    files["MANIFEST.files0"] = b"".join(("./" + name).encode() + b"\0" for name in names)
    return (
        index,
        files,
        {**verified, "evidence_kind": "fresh_fixed_github_read", "fresh_github_verified": True},
    )


def environment(raw):
    cold.need(
        type(raw) is bytes and len(raw) <= 1024**2 and b"\0" not in raw, "producer_environment_size"
    )
    values = {}
    for line in raw.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        cold.need(
            separator
            and re.fullmatch(r"[A-Z_][A-Z0-9_]*", key)
            and key not in values
            and not any(char in value for char in "\r\n\0"),
            "producer_environment_encoding",
        )
        cold.need(
            key not in {"LD_PRELOAD", "PYTHONHOME", "PYTHONPATH", "CODEX_HOME", "HOME"},
            "producer_environment_unsafe",
        )
        cold.need(
            not key.startswith("LD_") or key == "LD_LIBRARY_PATH",
            "producer_loader_environment_unsafe",
        )
        values[key] = value
    return values
