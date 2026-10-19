"""Fixed native cold-preparation effectors and independent evidence readers.

This module is inert on import. NativeBackend is used only by the qualified
single controller after its request E and while its original D stays held.
No CLI, environment switch, caller-selected command or production test mode is
provided. A private backend may replace these OS boundaries in unit tests.
"""

from __future__ import annotations

import hashlib
import os
import re
import select
import signal
import socket
import stat
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io
from scripts.j1_forward_cold_catalog_native import (
    accepted_original_inputs as accepted_original_inputs,
)
from scripts.j1_forward_cold_catalog_native import (
    verify_catalog_path as verify_catalog_path,
)
from scripts.j1_forward_cold_catalog_native import (
    verify_old_crm_ingress as verify_old_crm_ingress,
)

ENVIRONMENT = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
CRM_PRODUCTION_BRANCH = "autostopcrm-v1"
MODEL_NAMES = frozenset({"config.json", "model.bin", "tokenizer.json", "vocabulary.txt"})


def model_hashes(raw):
    """Only the four model payloads pinned by the qualified Manager source."""
    result = {}
    for line in raw.decode("ascii").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split()
        cold.need(
            len(fields) == 2
            and cold.HEX.fullmatch(fields[0])
            and fields[1] in MODEL_NAMES
            and fields[1] not in result,
            "producer_model_manifest",
        )
        result[fields[1]] = fields[0]
    cold.need(set(result) == MODEL_NAMES, "producer_model_manifest_inventory")
    return result


@contextmanager
def model_directory(base, *, uid, gid, work):
    """Keep role and both model directories open; never follow role symlinks."""
    io.protected_directory(base.parent)
    descriptors = []
    path, owner, mode = base, uid, 0o700
    try:
        for name in (None, "models", "faster-whisper-small"):
            parent = descriptors[-1][1] if descriptors else None
            if name is not None:
                path, owner, mode = (
                    path / name,
                    io.ROOT_UID if work else uid,
                    0o750 if work else 0o700,
                )
                try:
                    os.mkdir(name, mode, dir_fd=parent)
                except FileExistsError:
                    pass
                else:
                    os.chown(name, owner, gid, dir_fd=parent, follow_symlinks=False)
                    os.chmod(name, mode, dir_fd=parent, follow_symlinks=False)
                    os.fsync(parent)
            fd = os.open(
                path if parent is None else name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
            descriptors.append((path, fd, owner, mode))
            info, actual = path.lstat(), os.fstat(fd)
            cold.need(
                stat.S_ISDIR(info.st_mode)
                and info.st_dev == actual.st_dev
                and info.st_ino == actual.st_ino
                and info.st_uid == actual.st_uid == owner
                and info.st_gid == actual.st_gid == gid
                and stat.S_IMODE(info.st_mode) == stat.S_IMODE(actual.st_mode) == mode,
                "producer_model_directory_identity",
            )
        yield descriptors[-1][1]
        for path, fd, owner, mode in descriptors:
            info, actual = path.lstat(), os.fstat(fd)
            cold.need(
                info.st_uid == owner
                and info.st_gid == gid
                and stat.S_IMODE(info.st_mode) == mode
                and (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode)
                == (actual.st_dev, actual.st_ino, actual.st_uid, actual.st_gid, actual.st_mode),
                "producer_model_directory_changed",
            )
    finally:
        for _path, fd, _owner, _mode in reversed(descriptors):
            os.close(fd)


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
            os.chown(path, uid, gid, follow_symlinks=False)
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
        from scripts import j1_forward_cold_runtime_origin as origin

        return origin.verify_artifact(self, document, runtime)

    def install_model_file(self, source, base, row, binding, *, uid, gid, work):
        from scripts import j1_forward_backup as backup

        self.continuity(binding)
        io.protected_directory(source.parent)
        before = source.lstat()
        cold.need(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and before.st_uid == io.ROOT_UID
            and not before.st_mode & 0o022
            and before.st_size == row["bytes"] <= backup.MAX_FILE_BYTES,
            "producer_model_source_identity",
        )
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            opened = os.fstat(descriptor)
            with model_directory(base, uid=uid, gid=gid, work=work) as directory:
                target = os.open(
                    row["name"],
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=directory,
                )
                try:
                    count, digest = 0, hashlib.sha256()
                    while block := os.read(descriptor, 1024**2):
                        backup.budget(binding["deadline_ns"] / 1e9)
                        count += len(block)
                        cold.need(count <= row["bytes"], "producer_model_grew")
                        digest.update(block)
                        view = memoryview(block)
                        while view:
                            written = os.write(target, view)
                            cold.need(written > 0, "producer_model_short_write")
                            view = view[written:]
                    cold.need(
                        count == row["bytes"]
                        and digest.hexdigest() == row["content_sha256"]
                        and io.unchanged_read(before, opened, os.fstat(descriptor), source.lstat()),
                        "producer_model_source_changed",
                    )
                    os.fsync(target)
                    os.lseek(target, 0, os.SEEK_SET)
                    restored = hashlib.sha256()
                    while block := os.read(target, 1024**2):
                        backup.budget(binding["deadline_ns"] / 1e9)
                        restored.update(block)
                    cold.need(restored.digest() == digest.digest(), "producer_model_readback")
                    os.fchown(target, io.ROOT_UID if work else uid, gid)
                    os.fchmod(target, 0o640 if work else 0o600)
                    os.fsync(target)
                    info = os.stat(row["name"], dir_fd=directory, follow_symlinks=False)
                    actual = os.fstat(target)
                    cold.need(
                        info.st_nlink == actual.st_nlink == 1
                        and info.st_size == actual.st_size == row["bytes"]
                        and info.st_uid == (io.ROOT_UID if work else uid)
                        and info.st_gid == gid
                        and stat.S_IMODE(info.st_mode) == (0o640 if work else 0o600)
                        and (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
                        == (
                            actual.st_dev,
                            actual.st_ino,
                            actual.st_mode,
                            actual.st_uid,
                            actual.st_gid,
                        ),
                        "producer_model_destination_changed",
                    )
                    os.fsync(directory)
                finally:
                    os.close(target)
        finally:
            os.close(descriptor)
        self.continuity(binding)

    def install_fresh_role_material(self, document, binding, staged):
        from scripts import j1_forward_cold_telegram as telegram

        # The expected identities come from separately qualified staged-auth
        # inputs. A current process cut may only be compared with this policy;
        # it cannot authorize itself. Refuse v1 before installing any material.
        telegram.parse_frontier_policy(
            _read(self.provider, document["telegram_frontier_policy_ref"]), binding=binding
        )
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
        hashes = model_hashes(
            io.read_protected(
                Path(document["manager"]["checkout"])
                / "deploy/telegram/faster-whisper-small.sha256",
                limit=4096,
            )
        )
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
                and 0 < row["bytes"]
                and (kind == "model" or row["bytes"] <= 384 * 1024**2)
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
                    name in hashes and row["content_sha256"] == hashes[name],
                    "producer_model_not_source_pinned",
                )
                uid, gid = (document["credentials"][role][key] for key in ("uid", "gid"))
                self.install_model_file(
                    source / role / kind / name,
                    Path("/var/lib/autostop-manager-cold") / binding["generation"] / role,
                    row,
                    binding,
                    uid=uid,
                    gid=gid,
                    work=role == "work-telegram",
                )
                continue
            # The descriptor stores only a content hash. Secret bytes never
            # become canonical archive leaves through a {path,sha256} ref.
            raw = io.read_protected(source / role / kind / name, limit=384 * 1024**2)
            cold.need(
                len(raw) == row["bytes"] and io.sha(raw) == row["content_sha256"],
                "producer_material_bytes",
            )
            uid, gid = (document["credentials"][role][key] for key in ("uid", "gid"))
            target = Path("/etc/autostop-manager-cold") / binding["generation"] / role / name
            cold.need(
                not target.exists() and not target.is_symlink(), "producer_fresh_material_exists"
            )
            self.publish(target, raw, raw=True, mode=0o600, gid=gid)
            os.chown(target, uid, gid, follow_symlinks=False)
        cold.need(
            credentials == {"work-telegram", "personal-telegram"}
            and ("work-telegram", "owner", "owner.json") in seen
            and {(role, "model", name) for role in credentials for name in MODEL_NAMES} <= seen,
            "producer_fresh_telegram_material_missing",
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
