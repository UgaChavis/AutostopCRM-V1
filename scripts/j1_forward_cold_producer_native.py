"""Fixed native cold-preparation effectors and independent evidence readers.

This module is inert on import. NativeBackend is used only by the qualified
single controller after its request E and while its original D stays held.
No CLI, environment switch, caller-selected command or production test mode is
provided. A private backend may replace these OS boundaries in unit tests.
"""

from __future__ import annotations

import os
import re
import select
import signal
import socket
import stat
import subprocess
import time
from pathlib import Path

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_history as historical
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io

ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
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
BUILDER_REVISION = "245b12308c9310a4a0cecaaeb2e052e9ac9d8b34"
BUILDER_TREE = "0f539eea21f0e57e3763e8418dbafec718632b86"
BUILDER_WORKFLOW = ".github/workflows/cold-installed-runtime.yml"
BUILDER_BRANCH = "codex/cold-runtime-artifact-20261019"
BUILDER_SOURCES = {
    ".github/workflows/cold-installed-runtime.yml": "fd1a71808efada3f158bee4892c63592602571addcea1e68adb073602d141724",
    "scripts/ci_cold_installed_runtime.py": "99c44cef813ae6bfb424dbb4554c267ed8032e5edc2b02b0b9e8ff3e102f4346",
    "scripts/test_ci_cold_installed_runtime.py": "7f997fe753e967ffd20f0453495e5317bd39f784b4016204cc80163b80929d7d",
}
CAPSULE_HELPER_REVISION = "e285da961b2e5f0f004f1428a268735090faf667"
PHYSICAL_PYTHON_SHA256 = "eaaa308032c4aa063df5880c4c293cd458b4629d503fbc2c03ec163d71f7f790"
CRM_PRODUCTION_BRANCH = "autostopcrm-v1"


def systemd_paths(value):
    import shlex

    cold.need(type(value) is str and "\\" not in value, "producer_systemd_path_encoding")
    result = shlex.split(value)
    cold.need(
        all(name.startswith("/") and ".." not in name.split("/") for name in result),
        "producer_systemd_path",
    )
    return result


def linked_libraries(raw):
    """Strict trusted-ELF ldd output; unresolved dependencies never disappear."""
    result = set()
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if (
            not line
            or line == "statically linked"
            or re.fullmatch(r"linux-vdso\.so\.1 \(0x[0-9a-f]+\)", line)
        ):
            continue
        if "=>" in line:
            _name, line = line.split("=>", 1)
            line = line.strip()
        match = re.fullmatch(r"(/[^\s()]+) \(0x[0-9a-f]+\)", line)
        cold.need(match is not None, "producer_host_dependency_unresolved")
        path = Path(match[1]).resolve(strict=True)
        cold.need(path.is_absolute() and ".." not in path.parts, "producer_host_dependency_path")
        result.add(path)
    return sorted(result)


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
    from scripts.j1_forward_host import DOCKER

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


class NativeBackend:
    scope = "production_native"

    def __init__(self, request_ref, request, deployment_fd):
        self.request_ref, self.request, self.deployment_fd = request_ref, request, deployment_fd
        self.provider = native.NativeProvider()
        self.directory = native.STATE / "packages" / request["attempt_id"]
        self.dispatch_fd = None
        self.kernel_pidfd = None
        self.kernel_actor = None
        self.binding = None
        self.adopted = False
        self.kernel_unit_ref = None
        self.kernel_supervisor_argv = None
        self.kernel_start_attempted = False

    def boundary(self):
        from scripts import j1_forward_cold_controller_native as parent
        from scripts import j1_forward_launch_native as launch

        cold.need(
            os.name == "posix" and os.geteuid() == 0 and socket.gethostname() == native.HOST,
            "producer_fixed_native_boundary",
        )
        cold.need(
            self.request_ref["path"] == str(parent.request_path(self.request["attempt_id"]))
            and io.document(self.provider.read_reference(self.request_ref)) == self.request,
            "producer_request_reference",
        )
        _store, snapshot, context = parent.validate(self.request["attempt_id"])
        state = snapshot["state"]
        cold.need(
            state["execution_possible"] is True
            and state["acceptance"]["pid"] == os.getpid()
            and state["acceptance"]["start_ticks"] == native.birth(os.getpid()),
            "producer_actual_parent_E",
        )
        identity = launch._lock_identity()
        cold.need(context["deployment_lock_identity"] == identity, "producer_parent_D")
        native.held_descriptor(native.controller(os.getpid()), self.deployment_fd, identity)
        return identity

    def continuity(self, binding):
        cold._binding(binding, now_ns=time.monotonic_ns())
        cold.need(self.provider.boot() == binding["boot_id"], "producer_boot_changed")
        native.held_descriptor(
            native.controller(os.getpid()), self.deployment_fd, binding["deployment_lock_identity"]
        )
        if self.dispatch_fd is not None:
            actual = native.DISPATCH_LOCK.lstat()
            native.held_descriptor(
                native.controller(os.getpid()), self.dispatch_fd, [actual.st_dev, actual.st_ino]
            )

    def generation_custody(self, document, binding):
        import uuid

        cold.need(
            all(uuid.UUID(binding[key]).version == 4 for key in ("attempt_id", "generation")),
            "producer_fresh_uuid_generation_required",
        )
        # Attempt is disclosed by unit/argv. The private UUID generation and
        # prior nonpublication are separate from any parser or drain proof.
        refs = [
            self.request_ref,
            self.request["inputs_ref"],
            document["catalog_policy_ref"],
            document["fresh_role_material_ref"],
            document["telegram_frontier_policy_ref"],
            document["kernel"]["protocol_index_ref"],
            document["kernel"]["authorization_ref"],
            *document["runtime_env"].values(),
        ]
        observations = []
        for reference in refs:
            raw = self.provider.read_reference(reference)
            if binding["generation"].encode() not in raw and reference != self.request_ref:
                continue
            path = Path(reference["path"])
            io.protected_directory(path.parent)
            info = path.lstat()
            cold.need(
                stat.S_ISREG(info.st_mode)
                and info.st_uid == 0
                and info.st_gid == 0
                and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_nlink == 1,
                "producer_generation_document_private",
            )
            private = [
                parent
                for parent in [path.parent, *path.parent.parents]
                if stat.S_IMODE(parent.lstat().st_mode) == 0o700
            ]
            cold.need(
                private and any(parent != Path("/root") for parent in private),
                "producer_generation_private_parent",
            )
            observations.append(
                {
                    "reference": reference,
                    "identity": [
                        info.st_dev,
                        info.st_ino,
                        info.st_size,
                        info.st_mtime_ns,
                        info.st_ctime_ns,
                    ],
                    "private_parent": str(private[0]),
                }
            )
        return self.evidence(
            "generation-custody",
            {
                "schema": "autostop.j1.cold-generation-custody.v1",
                "scope": self.scope,
                "binding": binding,
                "request_ref": self.request_ref,
                "observed": observations,
                "generation_entropy_bits": 122,
                "attempt_visibility": "public_unit_name_and_argv",
                "historical_nonpublication": "source_and_operator_premise_not_measured",
                "leaked_or_prepublished_generation": "requires_new_request_and_window",
            },
        )

    def directory_at(self, path, *, mode=0o700, uid=0, gid=0):
        path = Path(path)
        if not path.exists() and not path.is_symlink():
            if not path.parent.exists():
                self.directory_at(path.parent, mode=0o755)
            else:
                io.protected_directory(path.parent)
            path.mkdir(mode=mode)
            path.chown(uid, gid)
            path.chmod(mode)
            io.fsync_directory(path.parent)
        value = path.lstat()
        cold.need(
            stat.S_ISDIR(value.st_mode)
            and value.st_uid == uid
            and value.st_gid == gid
            and stat.S_IMODE(value.st_mode) == mode,
            "producer_directory_conflict",
        )

    def publish(self, path, body, *, raw=False, mode=0o600, gid=0):
        path = Path(path)
        payload = body if raw else io.canonical(body)
        cold.need(type(payload) is bytes, "producer_publish_bytes")
        if not path.parent.exists():
            self.directory_at(
                path.parent, mode=0o700 if path.is_relative_to(self.directory) else 0o755
            )
        io.protected_directory(path.parent)
        if path.exists() or path.is_symlink():
            info = path.lstat()
            cold.need(
                io.read_protected(path, limit=max(32 * 1024**2, len(payload))) == payload
                and stat.S_IMODE(info.st_mode) == mode
                and info.st_gid == gid,
                "producer_immutable_conflict",
            )
            return {"path": str(path), "sha256": io.sha(payload)}
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode
        )
        try:
            os.fchown(descriptor, 0, gid)
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        io.fsync_directory(path.parent)
        return {"path": str(path), "sha256": io.sha(payload)}

    def evidence(self, name, body):
        cold.need(
            type(name) is str and name.replace("-", "").replace("_", "").isalnum(),
            "producer_evidence_name",
        )
        return self.publish(self.directory / "evidence" / (name + ".json"), body)

    def run(self, argv, *, timeout=10, environment=None):
        self.continuity(self.binding)
        remaining = (self.binding["deadline_ns"] - time.monotonic_ns()) / 1e9
        actual_environment = ENVIRONMENT if environment is None else environment
        if environment is not None:
            cold.need(
                environment
                == {
                    **ENVIRONMENT,
                    "LD_LIBRARY_PATH": "/opt/autostop-manager-cold-releases/"
                    + self.binding["candidate_manager_sha"]
                    + "/python/lib",
                },
                "producer_native_command_environment",
            )
        result = subprocess.run(
            argv,
            env=actual_environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=max(0.01, min(timeout, remaining)),
            check=False,
        )
        cold.need(
            result.returncode == 0 and len(result.stdout) <= 4 * 1024**2,
            "producer_native_command_failed",
        )
        self.continuity(self.binding)
        return result.stdout

    def host_startup_references(self, runtime, interpreter):
        from scripts import j1_forward_cold_kernel as kernel

        bootstrap = Path("/usr/bin/python3").resolve(strict=True)
        candidates = {Path(interpreter["path"]), bootstrap}
        for row in runtime["entries"]:
            if row["kind"] != "file" or ".so" not in Path(row["name"]).name:
                continue
            root, *parts = row["name"].split("/")
            candidates.add(Path(runtime["runtime_roots"][root]).joinpath(*parts))
        cold.need(len(candidates) <= 1024, "producer_native_dependency_budget")
        libraries = {bootstrap}
        for candidate in sorted(candidates):
            descriptor = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                header = os.read(descriptor, 4)
            finally:
                os.close(descriptor)
            if header != b"\x7fELF":
                continue
            raw = self.run(
                ["/usr/bin/ldd", str(candidate)],
                timeout=5,
                environment={
                    **ENVIRONMENT,
                    "LD_LIBRARY_PATH": runtime["runtime_roots"]["python"] + "/lib",
                },
            )
            libraries.update(linked_libraries(raw))
        roots = tuple(Path(value) for value in runtime["runtime_roots"].values())
        result = []
        for path in sorted(libraries):
            if any(path.is_relative_to(root) for root in roots):
                continue
            io.protected_directory(path.parent)
            result.append(kernel.file_digest(path))
        cold.need(result, "producer_host_runtime_inventory_empty")
        return result

    def github_manager(self, run_id):
        cold.need(type(run_id) is int and run_id > 0, "producer_manager_ci_id")
        from scripts import j1_manager_transition_source as source

        repository = source.REPOSITORIES["manager"]

        def get(suffix):
            result = subprocess.run(
                [
                    "/usr/bin/gh",
                    "api",
                    "--method",
                    "GET",
                    "repos/" + repository + "/actions/runs/" + str(run_id) + suffix,
                ],
                env={**ENVIRONMENT, "HOME": "/root"},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=15,
                check=False,
            )
            cold.need(
                result.returncode == 0 and len(result.stdout) <= 4 * 1024**2,
                "producer_manager_ci_read_failed",
            )
            return io.document(result.stdout)

        run = get("")
        jobs = get("/attempts/" + str(run["run_attempt"]) + "/jobs?per_page=100")
        return {"run": run, "jobs": jobs}

    def github_runtime_artifact(self, suffix):
        """One fixed repository; the ZIP itself is never downloaded here."""
        cold.need(type(suffix) is str and suffix.startswith("/"), "runtime_artifact_endpoint")
        self.continuity(self.binding)
        result = subprocess.run(
            ["/usr/bin/gh", "api", "--method", "GET", "repos/UgaChavis/AutostopManager" + suffix],
            env={**ENVIRONMENT, "HOME": "/root"},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=15,
            check=False,
        )
        cold.need(
            result.returncode == 0 and len(result.stdout) <= 4 * 1024**2,
            "runtime_artifact_fresh_github_read",
        )
        return io.document(result.stdout)

    def verify_crm_production_ci(self):
        """Production quality is a push on the fixed production branch.

        Exact-source rehearsal qualification is separate and cannot replace
        this branch/event gate. The parent independently checks that complete
        qualification before E; this adds the stricter production eligibility.
        """
        from scripts import j1_forward_cold_qualification as qualification

        source = {
            "revision": self.request["source"]["revision"],
            "tree": self.request["source"]["tree"],
            "consumer_sha256": cold.digest(self.request["source"]["consumers"]),
        }
        document = qualification.validate_document(
            _read(self.provider, self.request["qualification_ref"]), source
        )
        run = qualification._run(
            document["checks"]["quality"]["run_id"],
            qualification.QUALITY,
            source,
            qualification.github_read,
        )
        cold.need(
            run.get("head_branch") == CRM_PRODUCTION_BRANCH and run.get("event") == "push",
            "producer_crm_production_push_ci",
        )
        return run

    def require_original_marker(self, history):
        from scripts import j1_forward_marker as marker
        from scripts import j1_forward_release as release

        plan_ref = history["anchor"]["plan_ref"]
        plan = _read(self.provider, plan_ref)
        baseline = _read(self.provider, plan["baseline_ref"])
        record = release.marker_record(Path(plan_ref["path"]).parent, plan_ref, baseline)
        marker.require_owned(
            record["parent"],
            release.marker_value(plan_ref, plan),
            expected_identity=record["identity"],
        )
        return record["parent"]

    def close_original_ingress(self, binding, history):
        import fcntl

        self.binding = binding
        self.continuity(binding)
        original = _read(self.provider, history["anchor"]["closure_ref"])
        barriers = {
            row["unit"]: {key: row[key] for key in ("path", "sha256")}
            for row in original["dropins"]
        }
        cold.need(set(barriers) == set(cold.UNITS), "producer_owned_eight_barriers")
        for unit, ref in barriers.items():
            cold.need(
                ref["path"] == "/etc/systemd/system/" + unit + ".d/zz-j1-forward-closed.conf"
                and self.provider.read_reference(ref) == cold.DENY_BYTES,
                "producer_original_false_changed",
            )
        self.directory_at(native.DISPATCH_LOCK.parent)
        if not native.DISPATCH_LOCK.exists():
            self.publish(native.DISPATCH_LOCK, b"", raw=True)
        self.dispatch_fd = os.open(native.DISPATCH_LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        fcntl.flock(self.dispatch_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        marker_ref = self.publish(
            native.hold_path(binding["attempt_id"]),
            {
                "schema": "autostop.j1.cold-ingress-held.v1",
                "binding": binding,
                "deployment_descriptor": self.deployment_fd,
            },
        )
        common = {"scope": self.scope, "binding": binding}
        closure = self.evidence(
            "closure", {"schema": "autostop.j1.cold-closure.v1", **common, "barriers": barriers}
        )
        hold = self.evidence(
            "hold",
            {
                "schema": "autostop.j1.cold-hold.v1",
                **common,
                "marker_ref": marker_ref,
                "dispatch_lock_ref": io.reference(native.DISPATCH_LOCK),
                "controller": native.controller(os.getpid()),
                "descriptor": self.dispatch_fd,
            },
        )
        self.provider.verify_closure(
            {
                "package": {"binding": binding},
                "closure": _read(self.provider, closure),
                "hold": _read(self.provider, hold),
                "history": history,
            }
        )
        return closure, hold

    def retire_original(self, binding, history, *, closure_ref, hold_ref):
        from scripts import j1_forward_process_scope as prior

        self.continuity(binding)
        cold.need(
            history["scope"]["boot_id"] == self.provider.boot(), "producer_original_boot_changed"
        )
        actors = history["inventory"]["actors"]
        observer = prior.LinuxObserver()
        observer.deadline = binding["deadline_ns"] / 1e9
        # Pin every still-live accepted incarnation before the first signal.
        owned = []
        try:
            for expected in actors:
                pid = expected["pid"]
                try:
                    birth = native.birth(pid)
                except (FileNotFoundError, ProcessLookupError):
                    continue
                if birth != expected["start_ticks"]:
                    continue
                descriptor = os.pidfd_open(pid, 0)
                owned.append((expected, descriptor))
                birth, ppid, state = prior._stat(pid)
                detail, _states = observer.actor(
                    pid,
                    {
                        "start_ticks": birth,
                        "ppid": ppid,
                        "state": state,
                        "argv": prior._read(Path("/proc") / str(pid) / "cmdline"),
                        "cgroup": prior._read(Path("/proc") / str(pid) / "cgroup").decode().strip(),
                    },
                )
                cold.need(
                    detail["actor"] == expected and pid != os.getpid(), "producer_actor_not_owned"
                )
            self.evidence(
                "retirement-intent",
                {
                    "schema": "autostop.j1.cold-retirement-intent.v1",
                    "scope": self.scope,
                    "binding": binding,
                    "predecessor_scope_ref": history["anchor"]["scope_ref"],
                    "actors": actors,
                },
            )
            for expected, descriptor in owned:
                self.continuity(binding)
                if not select.select([descriptor], [], [], 0)[0]:
                    # pidfd refers to the checked incarnation, never a reused PID.
                    cold.need(
                        native.birth(expected["pid"]) == expected["start_ticks"],
                        "producer_actor_birth_changed",
                    )
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL, None, 0)
                cold.need(
                    bool(
                        select.select(
                            [descriptor],
                            [],
                            [],
                            min(5, max(0, (binding["deadline_ns"] - time.monotonic_ns()) / 1e9)),
                        )[0]
                    ),
                    "producer_retirement_timeout",
                )
        finally:
            for _expected, descriptor in owned:
                os.close(descriptor)
        roots = sorted({"/sys/fs/cgroup" + actor["cgroup"].removeprefix("0::") for actor in actors})
        common = {"scope": self.scope, "binding": binding}
        containment = self.evidence(
            "population",
            {
                "schema": "autostop.j1.cold-closed-population.v1",
                **common,
                "predecessor_scope_ref": history["anchor"]["scope_ref"],
                "actors_sha256": cold.digest(actors),
                "cgroup_roots": roots,
            },
        )
        origin = self.evidence(
            "actors",
            {
                "schema": "autostop.j1.cold-actor-scope.v1",
                **common,
                "actors": actors,
                "containment_ref": containment,
                "predecessor_scope_ref": history["anchor"]["scope_ref"],
            },
        )
        retirement = self.evidence(
            "retirement",
            {
                "schema": "autostop.j1.cold-retirement.v1",
                **common,
                "origin_actor_scope_ref": origin,
                "actors": actors,
                "completed_ns": time.monotonic_ns(),
            },
        )
        quarantine = self.evidence(
            "quarantine",
            {
                "schema": "autostop.j1.cold-quarantine.v1",
                **common,
                "retirement_ref": retirement,
                "hold_ref": hold_ref,
                "endpoints": history["inventory"]["endpoints"],
                "pipe_inodes": history["inventory"]["pipe_inodes"],
                "unparsed_ingress": "retired_without_replay",
                "closed_population_ref": containment,
            },
        )
        check = {
            "package": {"binding": binding},
            "history": history,
            "origin": _read(self.provider, origin),
            "retirement": _read(self.provider, retirement),
            "quarantine": _read(self.provider, quarantine),
            "closure": _read(self.provider, closure_ref),
            "hold": _read(self.provider, hold_ref),
        }
        self.provider.verify_retirement(check)
        self.provider.verify_ingress(check)
        self.provider.verify_closure(check)
        return retirement, quarantine

    def stop_old_crm(self, binding, history, config_ref):
        from scripts.j1_forward_host import DOCKER, Host

        self.require_original_marker(history)
        rows = io.document(self.run(DOCKER + ["inspect", "autostopcrm"]))
        cold.need(type(rows) is list and len(rows) == 1, "producer_old_crm_ambiguous")
        current = rows[0]
        baseline = _read(self.provider, history["anchor"]["baseline_ref"])
        cold.need(
            current["Id"] == baseline["previous_container"]["id"]
            and current["Config"]["Labels"].get("com.docker.compose.project") == "autostopcrm",
            "producer_old_crm_not_origin",
        )
        pid = current["State"]["Pid"]
        running = current["State"]["Running"]
        cold.need(
            type(pid) is int and type(running) is bool and (pid > 0) == running,
            "producer_old_crm_pid",
        )
        birth = native.birth(pid) if running else 0
        descriptor = os.pidfd_open(pid, 0) if running else None
        # Revalidate actual identity after pidfd creation and before intent.
        cold.need(not running or native.birth(pid) == birth, "producer_old_crm_birth_changed")
        intent_body = {
            "schema": "autostop.j1.cold-old-crm-stop-intent.v1",
            "scope": self.scope,
            "binding": binding,
            "origin_plan_ref": history["anchor"]["plan_ref"],
            "container_id": current["Id"],
            "init_pid": pid,
            "init_start_ticks": birth,
            "running": running,
        }
        intent_path = self.directory / "evidence/old-crm-stop-intent.json"
        already = intent_path.exists() or intent_path.is_symlink()
        intent_ref = self.evidence("old-crm-stop-intent", intent_body)
        try:
            if running and not already:
                # An exception/unknown reply never causes another stop call.
                # Readback below may establish that this one call completed.
                try:
                    self.run(DOCKER + ["stop", "--time", "10", current["Id"]], timeout=15)
                except (io.Refusal, subprocess.TimeoutExpired):
                    pass
            if descriptor is not None:
                cold.need(
                    bool(select.select([descriptor], [], [], 5)[0]),
                    "producer_old_crm_outcome_unknown",
                )
        finally:
            if descriptor is not None:
                os.close(descriptor)
        ref = self.evidence(
            "old-crm-ingress",
            {
                "schema": "autostop.j1.cold-old-crm-ingress.v1",
                "scope": self.scope,
                "binding": binding,
                "origin_plan_ref": history["anchor"]["plan_ref"],
                "stop_intent_ref": intent_ref,
                "container_id": current["Id"],
                "init_pid": pid,
                "init_start_ticks": birth,
                "http_ingress_config_ref": config_ref,
            },
        )
        verify_old_crm_ingress(ref, provider=self.provider, binding=binding)
        # This baseline intentionally contains no warm RPC observations.
        host = Host({}, self.directory)
        host.run = lambda argv, **_kwargs: self.run(argv)
        previous = host.container()
        return (
            ref,
            previous,
            {
                "schema": "autostop.j1.cold-prepared-host-baseline.v1",
                "origin_plan_ref": history["anchor"]["plan_ref"],
                "old_crm_ingress_ref": ref,
            },
        )

    def original_hold_checkpoint(self, binding, history):
        from scripts import j1_forward_cold_hold as hold

        self.continuity(binding)
        database = hold.DATABASE
        identity = hold.file_identity(database)
        raw = io.read_protected(database, limit=128 * 1024**2)
        ref = {"path": str(database), "sha256": io.sha(raw)}
        measured = hold.measure(
            database,
            identity,
            ref["sha256"],
            history["anchor"]["origin_attempt_id"],
            read_file=lambda path: io.read_protected(path, limit=128 * 1024**2),
        )
        quiet = self.publish(self.directory / "evidence/original-hold-quiet.sqlite3", raw, raw=True)
        result = self.evidence(
            "hold-checkpoint",
            {
                "schema": "autostop.j1.cold-hold-checkpoint.v1",
                "scope": self.scope,
                "binding": binding,
                "origin_attempt_id": history["anchor"]["origin_attempt_id"],
                "original_hold_ref": history["anchor"]["original_hold_ref"],
                "database_ref": ref,
                "database_identity": identity,
                "quiet_backup_ref": quiet,
                "measurement": measured,
            },
        )
        hold.verify(
            {
                "hold_checkpoint": _read(self.provider, result),
                "package": {"original_hold_ref": history["anchor"]["original_hold_ref"]},
            },
            self.provider,
        )
        return result

    def install_cache(self, binding, checkpoint_ref):
        checkpoint = _read(self.provider, checkpoint_ref)
        cold.fields(
            checkpoint,
            {"schema", "scope", "binding", "root", "files"},
            "producer_cache_checkpoint_fields",
        )
        cold.need(
            checkpoint["schema"] == "autostop.j1.cold-cache-checkpoint.v1"
            and checkpoint["scope"] == self.scope
            and checkpoint["binding"] == binding,
            "producer_cache_checkpoint_binding",
        )
        source = self.directory / "kernel/cache-source"
        target = self.directory / "kernel/cache-view"
        cold.need(checkpoint["root"] == str(source), "producer_cache_source_fixed")
        rows = checkpoint["files"]
        cold.need(
            type(rows) is list
            and 0 < len(rows) <= 8192
            and [row["name"] for row in rows] == sorted({row["name"] for row in rows}),
            "producer_cache_inventory",
        )
        # The input is a separately staged protected copy, never the writable
        # original cache. No bytes are renamed or inferred from a DB status.
        observed = set()
        for path in source.rglob("*"):
            info = path.lstat()
            cold.need(not stat.S_ISLNK(info.st_mode), "producer_cache_symlink")
            if stat.S_ISREG(info.st_mode):
                observed.add(path.relative_to(source).as_posix())
        cold.need(observed == {row["name"] for row in rows}, "producer_cache_inventory_changed")
        for row in rows:
            cold.fields(row, {"name", "bytes", "sha256", "mode"}, "producer_cache_entry")
            cold.need(
                type(row["name"]) is str
                and not row["name"].startswith("/")
                and ".." not in row["name"].split("/"),
                "producer_cache_relative_path",
            )
            path = source / row["name"]
            raw = io.read_protected(path, limit=128 * 1024**2)
            cold.need(
                len(raw) == row["bytes"]
                and io.sha(raw) == row["sha256"]
                and stat.S_IMODE(path.lstat().st_mode) == row["mode"],
                "producer_cache_entry_changed",
            )
        self.directory_at(target)
        cold.need(not any(target.iterdir()), "producer_cache_target_not_empty")
        self.evidence(
            "cache-mount-intent",
            {
                "schema": "autostop.j1.cold-cache-mount-intent.v1",
                "scope": self.scope,
                "binding": binding,
                "checkpoint_ref": checkpoint_ref,
                "source": str(source),
                "target": str(target),
            },
        )
        self.run(["/usr/bin/mount", "--bind", str(source), str(target)])
        self.run(["/usr/bin/mount", "-o", "remount,bind,ro,nosuid,nodev,noexec", str(target)])
        matches = [
            line.split()
            for line in Path("/proc/1/mountinfo").read_text().splitlines()
            if line.split()[4] == str(target)
        ]
        cold.need(
            len(matches) == 1
            and {"ro", "nosuid", "nodev", "noexec"} <= set(matches[0][5].split(",")),
            "producer_cache_mount_not_closed",
        )
        return self.evidence(
            "cache-view",
            {
                "schema": "autostop.j1.cold-cache-view.v2",
                "binding": binding,
                "kind": "kernel_read_only_checkpoint_mount",
                "path": str(target),
                "mount_id": int(matches[0][0]),
                "checkpoint_ref": checkpoint_ref,
                "source_identity": [source.stat().st_dev, source.stat().st_ino],
                "target_identity": [target.stat().st_dev, target.stat().st_ino],
            },
        )

    def prepare_cache_checkpoint(self, binding, history, source_ref):
        """Actually copy the retired old cache; an input JSON is not a snapshot."""
        import sqlite3
        from urllib.parse import quote

        from scripts import j1_forward_cold_kernel as kernel

        _profile, original, _environment = accepted_original_inputs(
            "autostop-j1.service", provider=self.provider, history=history
        )
        cold.need(
            source_ref["path"]
            == original["ordinary_database"]["path"]
            == "/var/cache/autostop-j1/research.sqlite3",
            "producer_cache_original_source",
        )
        source = Path(source_ref["path"])
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                Path(str(source) + suffix).lstat()
            except FileNotFoundError:
                continue
            raise io.Refusal("cold_producer_cache_requires_quiet_checkpoint")
        before = kernel.file_version(source.lstat())
        raw = self.provider.read_reference(source_ref)
        connection = sqlite3.connect(
            "file:" + quote(str(source), safe="/") + "?mode=ro&immutable=1", uri=True, timeout=2
        )
        try:
            connection.execute("PRAGMA query_only=ON")
            cold.need(
                connection.execute("PRAGMA quick_check").fetchall() == [("ok",)],
                "producer_cache_integrity",
            )
        finally:
            connection.close()
        cold.need(
            kernel.file_version(source.lstat()) == before
            and self.provider.read_reference(source_ref) == raw,
            "producer_cache_source_changed",
        )
        root = self.directory / "kernel/cache-source"
        self.directory_at(root)
        cold.need(not any(root.iterdir()), "producer_cache_checkpoint_already_exists")
        self.publish(root / "research.sqlite3", raw, raw=True, mode=0o400)
        checkpoint = self.evidence(
            "cache-checkpoint",
            {
                "schema": "autostop.j1.cold-cache-checkpoint.v1",
                "scope": self.scope,
                "binding": binding,
                "root": str(root),
                "files": [
                    {
                        "name": "research.sqlite3",
                        "bytes": len(raw),
                        "sha256": source_ref["sha256"],
                        "mode": 0o400,
                    }
                ],
            },
        )
        self.cache_checkpoint_source_ref = self.evidence(
            "cache-checkpoint-source",
            {
                "schema": "autostop.j1.cold-cache-checkpoint-source.v1",
                "scope": self.scope,
                "binding": binding,
                "source_ref": source_ref,
                "source_identity": before,
                "checkpoint_ref": checkpoint,
            },
        )
        return checkpoint

    def verify_runtime_artifact(self, document, runtime):
        """Verify fresh GitHub ZIP identity and actual member bytes, no exec.

        The reviewed request explicitly names the immutable builder source.
        This is separate from Manager source's production-push quality gate.
        The large outer ZIP remains private tooling, never another control leaf.
        """
        import hashlib
        import zipfile

        from scripts import j1_forward_cold_kernel as kernel

        provenance = _read(self.provider, document["runtime_artifact_provenance_ref"])
        cold.fields(
            provenance,
            {
                "schema",
                "repository",
                "source_revision",
                "source_tree",
                "builder_revision",
                "builder_tree",
                "builder_sources",
                "workflow_path",
                "event",
                "run_id",
                "run_attempt",
                "artifact_id",
                "archive_path",
                "archive_sha256",
                "archive_bytes",
                "capsule_member",
                "index_member",
                "manifest_member",
            },
            "runtime_artifact_provenance_fields",
        )
        cold.need(
            provenance["schema"] == "autostop.j1.cold-runtime-artifact-provenance.v1"
            and provenance["repository"] == "UgaChavis/AutostopManager"
            and provenance["source_revision"] == document["manager"]["revision"]
            and provenance["source_tree"] == document["manager"]["tree"]
            and provenance["event"] == "workflow_dispatch",
            "runtime_artifact_source_domains",
        )
        cold.need(
            all(
                type(provenance[key]) is int and provenance[key] > 0
                for key in ("run_id", "run_attempt", "artifact_id")
            )
            and all(
                type(provenance[key]) is str and cold.SHA1.fullmatch(provenance[key])
                for key in ("builder_revision", "builder_tree")
            ),
            "runtime_artifact_identity",
        )
        cold.need(
            provenance["builder_revision"] == BUILDER_REVISION
            and provenance["builder_tree"] == BUILDER_TREE
            and provenance["workflow_path"] == BUILDER_WORKFLOW
            and provenance["builder_sources"] == BUILDER_SOURCES,
            "runtime_artifact_approved_builder",
        )
        workflow = provenance["workflow_path"]
        cold.need(
            type(workflow) is str
            and re.fullmatch(r"\.github/workflows/[a-z0-9_-]+\.yml", workflow)
            and type(provenance["builder_sources"]) is dict
            and workflow in provenance["builder_sources"]
            and 2 <= len(provenance["builder_sources"]) <= 16,
            "runtime_artifact_reviewed_builder_sources",
        )
        repository = provenance["repository"]

        get = self.github_runtime_artifact

        run = get("/actions/runs/" + str(provenance["run_id"]))
        cold.need(
            run["id"] == provenance["run_id"]
            and run["repository"]["full_name"] == repository
            and run["head_repository"]["full_name"] == repository
            and run["head_sha"] == provenance["builder_revision"]
            and run["path"] == workflow
            and run["event"] == provenance["event"]
            and run["head_branch"] == BUILDER_BRANCH
            and run["run_attempt"] == provenance["run_attempt"]
            and run["status"] == "completed"
            and run["conclusion"] == "success",
            "runtime_artifact_builder_run",
        )
        commit = get("/git/commits/" + provenance["builder_revision"])
        cold.need(
            commit["sha"] == provenance["builder_revision"]
            and commit["tree"]["sha"] == provenance["builder_tree"],
            "runtime_artifact_builder_tree",
        )
        import base64

        for name, expected in provenance["builder_sources"].items():
            cold.need(
                type(name) is str
                and not name.startswith("/")
                and ".." not in name.split("/")
                and (name == workflow or re.fullmatch(r"scripts/[a-z0-9_]+\.py", name))
                and type(expected) is str
                and cold.HEX.fullmatch(expected),
                "runtime_artifact_builder_member",
            )
            item = get("/contents/" + name + "?ref=" + provenance["builder_revision"])
            cold.need(
                item["type"] == "file"
                and item["encoding"] == "base64"
                and io.sha(base64.b64decode(item["content"])) == expected,
                "runtime_artifact_builder_source_changed",
            )
        artifact = get("/actions/artifacts/" + str(provenance["artifact_id"]))
        cold.need(
            artifact["id"] == provenance["artifact_id"]
            and artifact["name"]
            == "manager-installed-runtime-"
            + provenance["source_revision"]
            + "-"
            + str(provenance["run_id"])
            and artifact["expired"] is False
            and artifact["workflow_run"]["id"] == provenance["run_id"]
            and artifact["workflow_run"]["head_sha"] == provenance["builder_revision"]
            and artifact["digest"] == "sha256:" + provenance["archive_sha256"]
            and artifact["size_in_bytes"] == provenance["archive_bytes"],
            "runtime_artifact_authenticated_digest",
        )
        archive = Path(provenance["archive_path"])
        cold.need(
            archive
            == native.CONFIG / "inputs" / self.binding["attempt_id"] / "runtime-artifact.zip",
            "runtime_artifact_private_fixed_zip",
        )
        io.protected_directory(archive.parent, private=True)
        before = kernel.file_version(archive.lstat())
        cold.need(
            0 < provenance["archive_bytes"] <= 256 * 1024**2
            and before[2] == provenance["archive_bytes"]
            and kernel.file_digest(archive, limit=256 * 1024**2)["sha256"]
            == provenance["archive_sha256"],
            "runtime_artifact_zip_bytes",
        )
        names = {
            key: provenance[key] for key in ("capsule_member", "index_member", "manifest_member")
        }
        cold.need(
            names
            == {
                "capsule_member": "cold-installed-runtime/manager-runtime.tar.gz",
                "index_member": "cold-installed-runtime/runtime-index.json",
                "manifest_member": "cold-installed-runtime/manifest.json",
            }
            and len(set(names.values())) == 3
            and all(
                type(name) is str
                and name.startswith("cold-installed-runtime/")
                and not name.startswith("/")
                and ".." not in name.split("/")
                for name in names.values()
            ),
            "runtime_artifact_members",
        )
        with zipfile.ZipFile(archive) as stream:
            entries = stream.infolist()
            cold.need(
                len(entries) <= 32 and len({entry.filename for entry in entries}) == len(entries),
                "runtime_artifact_zip_inventory",
            )

            def member(name, limit):
                entry = stream.getinfo(name)
                cold.need(
                    not entry.flag_bits & 1 and not entry.is_dir() and entry.file_size <= limit,
                    "runtime_artifact_zip_member_size",
                )
                with stream.open(entry) as inner:
                    value = inner.read(limit + 1)
                cold.need(
                    len(value) == entry.file_size <= limit, "runtime_artifact_zip_member_changed"
                )
                return value

            index = io.document(member(names["index_member"], 16 * 1024**2))
            cold.need(
                {
                    key: value
                    for key, value in index.items()
                    if key not in {"binding", "scope", "capsule_ref"}
                }
                == {
                    key: value
                    for key, value in runtime.items()
                    if key not in {"binding", "scope", "capsule_ref"}
                },
                "runtime_artifact_installed_index",
            )
            entry = stream.getinfo(names["capsule_member"])
            cold.need(
                entry.file_size <= 192 * 1024**2 and not entry.flag_bits & 1,
                "runtime_artifact_capsule_size",
            )
            digest = hashlib.sha256()
            with stream.open(entry) as inner:
                while chunk := inner.read(1024**2):
                    digest.update(chunk)
            cold.need(
                digest.hexdigest()
                == index["capsule_ref"]["sha256"]
                == runtime["capsule_ref"]["sha256"],
                "runtime_artifact_capsule_member",
            )
            manifest = io.document(member(names["manifest_member"], 4 * 1024**2))
            imports = io.document(member("cold-installed-runtime/dependency-check.json", 4096))
            cold.need(
                imports
                == {
                    "dependency_imports": True,
                    "roles_started": False,
                    "cold_binding_acceptance": False,
                },
                "runtime_artifact_dependency_scope",
            )
        files = [row for row in runtime["entries"] if row["kind"] == "file"]
        cold.need(
            manifest["schema"] == "autostop.manager.installed-runtime-artifact.v1"
            and manifest["controller_revision"]
            == manifest["workflow_revision"]
            == provenance["builder_revision"]
            and manifest["run_id"] == str(provenance["run_id"])
            and manifest["run_attempt"] == str(provenance["run_attempt"])
            and manifest["source_revision"] == provenance["source_revision"]
            and manifest["source_tree"] == provenance["source_tree"]
            and manifest["helper_revision"] == CAPSULE_HELPER_REVISION
            and manifest["runtime_roots"] == runtime["runtime_roots"]
            and manifest["entries"] == len(runtime["entries"])
            and manifest["files"] == len(files)
            and manifest["expanded_bytes"] == runtime["unpacked_bytes"]
            and manifest["capsule_bytes"] == entry.file_size
            and manifest["physical_python_sha256"] == PHYSICAL_PYTHON_SHA256
            and all(
                next(row for row in files if row["name"] == name)["sha256"]
                == PHYSICAL_PYTHON_SHA256
                for name in ("python/bin/python3.12", "venv/bin/python3.12")
            )
            and manifest["final_prefix_bytes_created_before_seal"] is True
            and manifest["final_modes_sealed_before_index"] is True
            and manifest["cfg_rewritten_on_restore"] is False
            and manifest["six_role_dependency_imports"] is True
            and all(
                manifest[key] is False
                for key in ("native_acceptance", "production_updated", "release_authority")
            )
            and manifest["independent_full_restore"]
            == {
                "schema": "autostop.j1.cold-runtime-restored.v1",
                "members": len(runtime["entries"]),
                "files": len(files),
                "bytes": runtime["unpacked_bytes"],
                "archived_code_executed": False,
            },
            "runtime_artifact_manifest_source_or_scope",
        )
        dependency_id = manifest["dependency_run_id"]
        cold.need(
            (type(dependency_id) is int and dependency_id > 0)
            or (type(dependency_id) is str and re.fullmatch(r"[1-9][0-9]{0,19}", dependency_id)),
            "runtime_artifact_dependency_run",
        )
        dependency_run = get("/actions/runs/" + str(dependency_id))
        cold.need(
            dependency_run["repository"]["full_name"] == repository
            and dependency_run["head_repository"]["full_name"] == repository
            and dependency_run["status"] == "completed"
            and dependency_run["conclusion"] == "success",
            "runtime_artifact_dependency_run_failed",
        )
        cold.need(
            kernel.file_version(archive.lstat()) == before
            and kernel.file_digest(archive, limit=256 * 1024**2)["sha256"]
            == provenance["archive_sha256"],
            "runtime_artifact_zip_changed",
        )
        cold.need(
            get("/actions/runs/" + str(provenance["run_id"])) == run
            and get("/actions/artifacts/" + str(provenance["artifact_id"])) == artifact,
            "runtime_artifact_origin_changed_during_read",
        )
        return self.evidence(
            "runtime-artifact-authority",
            {
                "schema": "autostop.j1.cold-runtime-artifact-verification.v1",
                "scope": self.scope,
                "binding": self.binding,
                "provenance_ref": document["runtime_artifact_provenance_ref"],
                "runtime_index_ref": document["runtime_artifact_ref"],
                "run": {
                    key: run[key]
                    for key in (
                        "id",
                        "run_attempt",
                        "head_sha",
                        "path",
                        "event",
                        "status",
                        "conclusion",
                    )
                },
                "artifact_id": artifact["id"],
                "zip_digest": artifact["digest"],
                "zip_bytes": artifact["size_in_bytes"],
                "member_mapping": names,
                "manifest_sha256": cold.digest(manifest),
                "offline_zip_provenance_restored": False,
            },
        )

    def install_fresh_role_material(self, document, binding, staged):
        material = _read(self.provider, document["fresh_role_material_ref"])
        cold.fields(material, {"schema", "scope", "binding", "files"}, "producer_material_fields")
        cold.need(
            material["schema"] == "autostop.j1.cold-role-material.v1"
            and material["scope"] == self.scope
            and material["binding"] == binding
            and type(material["files"]) is list
            and 1 <= len(material["files"]) <= 8192,
            "producer_material_binding",
        )
        source = native.CONFIG / "credentials" / binding["attempt_id"] / "roles"
        seen, credentials = set(), set()
        for row in material["files"]:
            cold.fields(
                row, {"role", "kind", "name", "content_sha256", "bytes"}, "producer_material_entry"
            )
            role, kind, name = row["role"], row["kind"], row["name"]
            cold.need(
                role in {"work-telegram", "personal-telegram"}
                and kind in {"credential", "owner", "model"}
                and type(name) is str
                and name
                and not name.startswith("/")
                and ".." not in name.split("/")
                and "\\" not in name,
                "producer_material_scope",
            )
            key = (role, kind, name)
            cold.need(
                key not in seen
                and type(row["bytes"]) is int
                and 0 < row["bytes"] <= 384 * 1024**2
                and type(row["content_sha256"]) is str
                and cold.HEX.fullmatch(row["content_sha256"]),
                "producer_material_identity",
            )
            seen.add(key)
            if kind == "credential":
                cold.need(name == "credentials", "producer_only_api_credential_file")
                credentials.add(role)
            elif kind == "owner":
                cold.need(
                    role == "work-telegram" and name == "owner.json", "producer_owner_material"
                )
            else:
                cold.need(
                    not name.endswith((".py", ".so", ".session", ".sqlite3")),
                    "producer_model_not_executable_or_session",
                )
            # The descriptor stores only a content hash. Secret bytes never
            # become canonical archive leaves through a {path,sha256} ref.
            raw = io.read_protected(source / role / kind / name, limit=384 * 1024**2)
            cold.need(
                len(raw) == row["bytes"] and io.sha(raw) == row["content_sha256"],
                "producer_material_bytes",
            )
            uid, gid = (document["credentials"][role][key] for key in ("uid", "gid"))
            if kind in {"credential", "owner"}:
                target = Path("/etc/autostop-manager-cold") / binding["generation"] / role / name
            else:
                target = (
                    Path("/var/lib/autostop-manager-cold")
                    / binding["generation"]
                    / role
                    / "models/faster-whisper-small"
                    / name
                )
                current = target.parent
                missing = []
                while not current.exists():
                    missing.append(current)
                    current = current.parent
                for parent in reversed(missing):
                    self.directory_at(parent, uid=uid, gid=gid)
            cold.need(
                not target.exists() and not target.is_symlink(), "producer_fresh_material_exists"
            )
            self.publish(target, raw, raw=True, mode=0o600, gid=gid)
            target.chown(uid, gid)
        cold.need(
            credentials == {"work-telegram", "personal-telegram"}
            and ("work-telegram", "owner", "owner.json") in seen,
            "producer_fresh_telegram_material_missing",
        )
        # These are source qualification expectations only. Actual authenticated
        # provider frontier receipts are read after each guarded actor starts.
        frontier = _read(self.provider, document["telegram_frontier_policy_ref"])
        cold.fields(
            frontier,
            {"schema", "scope", "binding", "source_revision", "roles", "cut"},
            "producer_frontier_policy_fields",
        )
        cold.need(
            frontier
            == {
                "schema": "autostop.j1.cold-telegram-frontier-policy.v1",
                "scope": self.scope,
                "binding": binding,
                "source_revision": binding["candidate_manager_sha"],
                "roles": ["personal-telegram", "work-telegram"],
                "cut": "same_owned_process_getme_getstate_before_ingress_pts_strictly_greater_no_history_replay",
            },
            "producer_frontier_policy",
        )
        return self.evidence(
            "fresh-role-material-installed",
            {
                "schema": "autostop.j1.cold-role-material-installation.v1",
                "scope": self.scope,
                "binding": binding,
                "material_ref": document["fresh_role_material_ref"],
                "telegram_frontier_policy_ref": document["telegram_frontier_policy_ref"],
                "files": len(seen),
                "old_session_or_queue_copied": False,
            },
        )

    def start_kernel(
        self, document, binding, *, interpreter, startup_ref, cache_view_ref, context_ref
    ):
        from scripts import j1_forward_cold_kernel as kernel
        from scripts import j1_forward_cold_kernel_supervisor as supervisor

        unit = native.kernel_unit(binding["generation"])
        source = io.reference(native.INSTALL / "j1_forward_cold_kernel_supervisor.py")
        rpc = io.reference(native.INSTALL / "j1_forward_cold_kernel_rpc.py")
        executable = document["kernel"]["appserver_ref"]
        cold.need(
            kernel.file_digest(executable["path"]) == executable
            and executable["sha256"] == kernel.BINARY_SHA256,
            "producer_kernel_binary_not_pinned",
        )
        protocol = _read(self.provider, document["kernel"]["protocol_index_ref"])
        cold.fields(
            protocol, {"schema", "scope", "binding", "files"}, "producer_kernel_protocol_fields"
        )
        cold.need(
            protocol["schema"] == "autostop.j1.cold-kernel-protocol-index.v1"
            and protocol["scope"] == self.scope
            and protocol["binding"] == binding
            and source in protocol["files"]
            and rpc in protocol["files"],
            "producer_kernel_protocol_binding",
        )
        cold.need(
            [row["path"] for row in protocol["files"]]
            == sorted({row["path"] for row in protocol["files"]}),
            "producer_kernel_protocol_order",
        )
        for reference in protocol["files"]:
            self.provider.read_reference(reference)
        cwd = "/opt/autostop-manager-cold-releases/" + binding["candidate_manager_sha"] + "/source"
        argv = [
            interpreter["path"],
            "-I",
            "-S",
            "-B",
            source["path"],
            "--attempt",
            binding["attempt_id"],
        ]
        home_environment = {
            **supervisor.ENVIRONMENT,
            "LD_LIBRARY_PATH": "/opt/autostop-manager-cold-releases/"
            + binding["candidate_manager_sha"]
            + "/python/lib",
        }

        def quoted(value):
            cold.need(
                type(value) is str and value and not any(c in value for c in "\n\r\0%"),
                "producer_kernel_unit_value",
            )
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

        unit_raw = (
            "[Unit]\nDescription=AutoStop owned cold kernel "
            + binding["generation"]
            + "\n[Service]\nType=notify\nNotifyAccess=main\nRestart=no\nKillMode=control-group\nTimeoutStartSec=30\nTimeoutStopSec=10\nWorkingDirectory="
            + cwd
            + "\n"
            + "".join(
                "Environment=" + quoted(key + "=" + value) + "\n"
                for key, value in sorted(home_environment.items())
            )
            + "ExecStart="
            + " ".join(quoted(value) for value in argv)
            + "\n"
        ).encode()
        unit_ref = self.publish(Path("/etc/systemd/system") / unit, unit_raw, raw=True, mode=0o644)
        self.kernel_unit_ref = unit_ref
        self.kernel_supervisor_argv = argv
        self.run(["/usr/bin/systemctl", "daemon-reload"])
        before = self.provider.systemd(unit)
        cold.need(
            before["ActiveState"] == "inactive"
            and before["MainPID"] == "0"
            and before["Restart"] == "no"
            and before["RuntimeMaxUSec"] == "infinity",
            "producer_kernel_unit_not_fresh",
        )
        environment = self.evidence(
            "kernel-seed-environment",
            {
                "schema": "autostop.j1.cold-environment.v1",
                "scope": self.scope,
                "binding": binding,
                "unit": unit,
                "values": home_environment,
            },
        )
        app_environment = self.evidence(
            "kernel-appserver-environment",
            {
                "schema": "autostop.j1.cold-environment.v1",
                "scope": self.scope,
                "binding": binding,
                "unit": unit,
                "values": supervisor.environment(binding),
            },
        )
        seed_unit = self.evidence(
            "kernel-seed-unit",
            {
                "schema": "autostop.j1.cold-unit.v1",
                "scope": self.scope,
                "binding": binding,
                "unit": unit,
                "properties": before,
                "files": [unit_ref],
            },
        )
        policy = {
            "schema": "autostop.j1.cold-kernel-policy.v2",
            "scope": self.scope,
            "binding": binding,
            "unit": unit,
            "source_ref": source,
            "interpreter_ref": interpreter,
            "runtime_index_ref": document["runtime_artifact_ref"],
            "startup_index_ref": startup_ref,
            "environment_ref": environment,
            "unit_ref": seed_unit,
            "argv": argv,
            "cwd": cwd,
            "credentials": {"uid": 0, "gid": 0, "groups": [0]},
            "appserver_ref": executable,
            "appserver_argv": supervisor.appserver_argv(binding, executable["path"]),
            "appserver_environment_ref": app_environment,
            "protocol_index_ref": document["kernel"]["protocol_index_ref"],
            "rpc_ref": rpc,
        }
        seed = {
            "schema": "autostop.j1.cold-kernel-supervisor-seed.v1",
            "scope": self.scope,
            "binding": binding,
            "policy": policy,
            "deployment_controller": native.controller(os.getpid()),
            "deployment_descriptor": self.deployment_fd,
            "authorization_ref": document["kernel"]["authorization_ref"],
            "cache_view_ref": cache_view_ref,
            "source_ref": source,
            "rpc_ref": rpc,
            "protocol_index_ref": document["kernel"]["protocol_index_ref"],
            "context_ref": context_ref,
        }
        kernel_directory = supervisor.paths(binding)["directory"]
        self.publish(kernel_directory / "supervisor-seed.json", seed)
        # Persist before exactly one start. An unknown systemd outcome is read
        # back once; this method never repeats systemctl start on the same ID.
        intent = kernel_directory / "supervisor-start-intent.json"
        cold.need(
            not intent.exists() and not intent.is_symlink(),
            "producer_kernel_start_already_consumed",
        )
        self.publish(
            intent,
            {
                "schema": "autostop.j1.cold-kernel-supervisor-start-intent.v1",
                "scope": self.scope,
                "binding": binding,
                "unit": unit,
                "unit_ref": unit_ref,
            },
        )
        self.kernel_start_attempted = True
        try:
            self.run(["/usr/bin/systemctl", "start", unit], timeout=35)
        except (io.Refusal, subprocess.TimeoutExpired):
            pass
        actual = self.provider.systemd(unit)
        cold.need(
            actual["ActiveState"] == "active"
            and actual["SubState"] == "running"
            and actual["MainPID"].isdigit()
            and int(actual["MainPID"]) > 0,
            "producer_kernel_start_outcome_unknown",
        )
        # Retain the supervisor before a fallible READY read. An unknown reply
        # must not create an ownership gap in subsequent safety cleanup.
        supervisor_pid = int(actual["MainPID"])
        self.kernel_pidfd = os.pidfd_open(supervisor_pid, 0)
        self.kernel_actor = native.actor(supervisor_pid)
        cold.need(
            self.kernel_actor["exe_ref"] == interpreter
            and self.kernel_actor["argv_sha256"]
            == io.sha(b"\0".join(value.encode() for value in argv) + b"\0")
            and self.kernel_actor["uid"] == 0
            and self.kernel_actor["cgroup"] == "0::" + actual["ControlGroup"],
            "producer_kernel_supervisor_not_owned",
        )
        ready_ref = io.reference(kernel_directory / "ready.json")
        ready = _read(self.provider, ready_ref)
        cold.need(
            ready["binding"] == binding
            and ready["supervisor_actor"]["pid"] == int(actual["MainPID"]),
            "producer_kernel_ready_not_owned",
        )
        cold.need(
            ready["supervisor_actor"] == self.kernel_actor
            and native.actor(self.kernel_actor["pid"]) == self.kernel_actor,
            "producer_kernel_supervisor_reused",
        )
        self.kernel_child_pidfd = os.pidfd_open(ready["actor"]["pid"], 0)
        cold.need(
            native.actor(ready["actor"]["pid"]) == ready["actor"], "producer_kernel_child_reused"
        )
        actual_env = native._environment(
            (Path("/proc") / actual["MainPID"] / "environ").read_bytes()
        )
        final_environment = self.evidence(
            "kernel-environment",
            {
                "schema": "autostop.j1.cold-environment.v1",
                "scope": self.scope,
                "binding": binding,
                "unit": unit,
                "values": actual_env,
            },
        )
        final_unit = self.evidence(
            "kernel-unit",
            {
                "schema": "autostop.j1.cold-unit.v1",
                "scope": self.scope,
                "binding": binding,
                "unit": unit,
                "properties": actual,
                "files": [unit_ref],
            },
        )
        final_policy = self.evidence(
            "kernel-policy",
            {
                **policy,
                "environment_ref": final_environment,
                "unit_ref": final_unit,
                "credentials": {key: self.kernel_actor[key] for key in ("uid", "gid", "groups")},
            },
        )
        result = self.evidence(
            "kernel",
            {
                "schema": "autostop.j1.cold-kernel.v2",
                "scope": self.scope,
                "binding": binding,
                "unit": unit,
                "actor": ready["actor"],
                "supervisor_actor": ready["supervisor_actor"],
                "policy_ref": final_policy,
                "ready_ref": ready_ref,
                "cache_view_ref": cache_view_ref,
                "preparation_ref": ready["preparation_ref"],
                "empty_thread_ref": ready["empty_thread_ref"],
            },
        )
        return result

    def configure_wake(self, binding, kernel_ref):
        kernel = _read(self.provider, kernel_ref)
        thread = _read(self.provider, kernel["empty_thread_ref"])
        source = (
            "/opt/autostop-manager-cold-releases/" + binding["candidate_manager_sha"] + "/source"
        )
        self.publish(
            Path("/etc/autostop-manager-cold") / binding["generation"] / "wake/wake.json",
            {
                "thread_id": thread["thread_id"],
                "project_dir": source,
                "app_socket": "/run/autostop-manager-cold/"
                + binding["generation"]
                + "/wake/app-server.sock",
                "instruction_root": source,
                "instruction_revision": binding["candidate_manager_sha"],
            },
        )

    def close(self, *, success):
        keep_runtime = success and self.adopted
        failures = []

        def attempt(action):
            try:
                action()
            except Exception as error:
                failures.append(error)

        # Each independent safety closure must be attempted even if another
        # observer fails. The original D is still held by our caller throughout.
        if not keep_runtime:
            attempt(self.close_new_dispatch)
        for name in ("kernel_child_pidfd", "kernel_pidfd"):
            descriptor = getattr(self, name, None)
            if descriptor is None:
                continue

            def stop(descriptor=descriptor):
                if not select.select([descriptor], [], [], 0)[0]:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL, None, 0)
                cold.need(
                    bool(select.select([descriptor], [], [], 5)[0]),
                    "producer_owned_kernel_cleanup_timeout",
                )

            if not keep_runtime:
                attempt(stop)
            attempt(lambda descriptor=descriptor: os.close(descriptor))
            setattr(self, name, None)
        if not keep_runtime and self.kernel_start_attempted:
            attempt(self.stop_owned_kernel_unit)
        if self.dispatch_fd is not None:
            attempt(lambda: os.close(self.dispatch_fd))
            self.dispatch_fd = None
        if failures:
            raise failures[0]

    def close_new_dispatch(self):
        import fcntl

        if self.binding is None:
            return
        from scripts import j1_forward_cold_epoch as epochs

        base = Path("/var/lib/autostop-manager-cold") / self.binding["generation"] / "dispatch"
        if not base.exists():
            return
        native.held_descriptor(
            native.controller(os.getpid()),
            self.deployment_fd,
            self.binding["deployment_lock_identity"],
        )
        descriptor = os.open(base / "dispatch.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not (base / "state.json").exists():
                return  # Missing state denies every dispatch; never open it.
            current = io.document(io.read_protected(base / "state.json"))
            cold.need(
                all(current[key] == value for key, value in epochs.common(self.binding).items()),
                "producer_cleanup_dispatch_not_owned",
            )
            # Closure is a safety action after expiry; it never revives grants.
            io.atomic_replace(base / "state.json", epochs.closed_state(self.binding))
        finally:
            os.close(descriptor)

    def stop_owned_kernel_unit(self):
        reference = self.kernel_unit_ref
        cold.need(
            io.reference(reference["path"]) == reference, "producer_cleanup_kernel_unit_changed"
        )
        unit = native.kernel_unit(self.binding["generation"])
        actual = self.provider.systemd(unit)
        cold.need(
            native.commands(actual["ExecStart"]) == [" ".join(self.kernel_supervisor_argv)]
            and actual["KillMode"] == "control-group",
            "producer_cleanup_kernel_not_owned",
        )
        native.held_descriptor(
            native.controller(os.getpid()),
            self.deployment_fd,
            self.binding["deployment_lock_identity"],
        )
        result = subprocess.run(
            ["/usr/bin/systemctl", "stop", unit],
            env=ENVIRONMENT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=15,
            check=False,
        )
        cold.need(result.returncode == 0, "producer_kernel_unit_cleanup_unknown")
        final = self.provider.systemd(unit)
        cold.need(
            final["ActiveState"] == "inactive" and final["MainPID"] == "0",
            "producer_kernel_unit_not_stopped",
        )
