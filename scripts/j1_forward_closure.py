"""Owned, reversible writer fence for a CRM-only accepted-warm successor.

The orchestrator holds the original deployment lock before preparing ownership.
It establishes maintenance, a Scheduler hold and paused Work before normal
closure; prepared ownership also permits fencing after an earlier drain failure.
This module never starts a unit, accepts a release, runs a native guard, or
invokes a shell. Test effects are injected by replacing ``RUNNER``; receipts
never select effect commands.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts import j1_forward_host as host_policy
from scripts import j1_forward_io as io
from scripts import j1_forward_process_scope as process_scope
from scripts import j1_forward_state as forward_state
from scripts import j1_forward_store as store_drain

SCHEMA = "autostop.j1.crm-forward-closure.v2"
UNITS = tuple(
    sorted(
        (
            "autostop-j1.service",
            "autostop-manager-mcp.service",
            "autostop-manager-scheduler.service",
            "autostop-work-telegram.service",
            "autostop-telegram.service",
            "autostop-codex-wake.service",
            "autostop-codex-start.service",
            "autostop-j1-browser.service",
        )
    )
)
SYSTEMD = Path("/etc/systemd/system")
CRM = Path("/opt/autostopcrm")
FORWARD_SCRIPT = "/opt/autostopcrm/scripts/j1_forward_release.py"
ATTEMPTS = Path("/var/lib/autostop-crm-forward/attempts")
DOCKER = ["/usr/bin/docker", "--host", "unix:///var/run/docker.sock"]
DROPIN = "zz-j1-forward-closed.conf"
DENY_BYTES = (
    b"[Service]\nExecCondition=/usr/bin/false\nExecStartPre=\n"
    b"ExecStartPost=\nExecStop=\nExecStopPost=\nExecReload=\n"
)
HOOKS = ("ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost", "ExecReload")
JOB_QUERY = (
    "/usr/bin/busctl",
    "--system",
    "--json=short",
    "call",
    "org.freedesktop.systemd1",
    "/org/freedesktop/systemd1",
    "org.freedesktop.systemd1.Manager",
    "ListJobs",
)
WRITER_ROOTS = (
    "/opt/autostopcrm/data",
    "/opt/AutostopManager/data",
    "/var/cache/autostop-j1",
    "/run/autostop-j1-cache-view-private/rw",
    "/var/lib/autostop-manager",
    "/var/lib/autostop-manager-scheduler",
    "/var/lib/autostop-work-telegram",
    "/var/lib/autostop-telegram",
    "/root/.codex",
)
ACTOR_FIELDS = (
    "pid",
    "start_ticks",
    "exe",
    "exe_sha256",
    "exe_identity",
    "argv_sha256",
    "cgroup",
    "uid",
    "mount_namespace",
)
IDENTITY_FIELDS = tuple(key for key in ACTOR_FIELDS if key != "exe_sha256")
ACCEPTED_PIDS = {538285, 538314, 538487}
_LOCK = threading.RLock()
_CACHE = {}
_HASH_PASS = threading.local()
_EXE_METADATA = (
    "st_dev",
    "st_ino",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
    "st_uid",
    "st_gid",
    "st_mode",
    "st_nlink",
)


@contextmanager
def _executable_hash_pass():
    """Share byte hashes only within this thread's one explicit actor pass."""
    previous = getattr(_HASH_PASS, "cache", None)
    _HASH_PASS.cache = {}
    try:
        yield
    finally:
        if previous is None:
            del _HASH_PASS.cache
        else:
            _HASH_PASS.cache = previous


def _executable_sha(path, initial, *, exe, pid, birth):
    """Hash an opened executable, rechecking its identity even on a pass hit."""
    key = tuple(getattr(initial, field) for field in _EXE_METADATA)
    io.require(stat.S_ISREG(initial.st_mode), "actor_executable_not_regular")
    cache = getattr(_HASH_PASS, "cache", None)
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        io.require(
            tuple(getattr(before, field) for field in _EXE_METADATA) == key,
            "actor_executable_changed",
        )
        digest = None if cache is None else cache.get(key)
        if digest is None:
            hasher = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024**2), b""):
                hasher.update(chunk)
            digest = hasher.hexdigest()
        after = os.fstat(stream.fileno())
        final = path.stat()
        io.require(
            tuple(getattr(after, field) for field in _EXE_METADATA) == key
            and tuple(getattr(final, field) for field in _EXE_METADATA) == key
            and os.readlink(path) == exe,
            "actor_executable_changed",
        )
        io.require(_stat(pid)[0] == birth, "actor_reused_during_read")
    if cache is not None:
        cache[key] = digest
    return digest


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    io.require(remaining > 0, "closure_deadline")
    return min(remaining, 30.0)


def _utc(value):
    io.require(type(value) is str and len(value) <= 40, "ingress_utc_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise io.Refusal("ingress_utc_invalid") from None
    io.require(
        parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0), "ingress_utc_invalid"
    )
    return parsed


def _under(path, roots=WRITER_ROOTS):
    return any(path == root or path.startswith(root + "/") for root in roots)


def _proc_bytes(path, limit=4 * 1024**2):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    io.require(len(raw) <= limit, "proc_limit")
    return raw


def _stat(pid):
    raw = _proc_bytes(Path("/proc") / str(pid) / "stat")
    fields = raw.rsplit(b") ", 1)[1].split()
    return int(fields[19]), int(fields[1]), fields[0].decode("ascii")


def _argv(raw):
    # Remove only the terminator; an extra empty argument is still an argument.
    if not raw:
        return ()
    values = raw[:-1].split(b"\0") if raw.endswith(b"\0") else [raw]
    return tuple(os.fsdecode(value) for value in values)


def _forward_commands(attempt):
    isolated = ("/usr/bin/python3", "-I", "-S", FORWARD_SCRIPT)
    isolated_no_bytecode = ("/usr/bin/python3", "-I", "-S", "-B", FORWARD_SCRIPT)
    worker = ("/usr/bin/python3", "-B", FORWARD_SCRIPT)
    commands = {
        (*isolated_no_bytecode, operation, "--attempt", attempt): operation
        for operation in ("run", "settle")
    }
    for prefix in (isolated, isolated_no_bytecode):
        commands[(*prefix, "apply", "--attempt", attempt)] = "apply"
    for prefix in (isolated, isolated_no_bytecode, worker):
        for stage in ("closed", "activated"):
            commands[(*prefix, "inspect", "--attempt", attempt, "--stage", stage)] = "inspect"
        commands[(*prefix, "export-delta", "--attempt", attempt)] = "export-delta"
        for kind in ("offhost_restore", "native_acceptance", "runtime_acceptance"):
            commands[(*prefix, "receive-proof", "--attempt", attempt, "--kind", kind)] = (
                "receive-proof"
            )
    return commands


def _systemd_launch_command(attempt):
    return (
        "/usr/bin/systemd-run",
        "--quiet",
        "--wait",
        "--collect",
        "--unit=autostop-crm-forward-" + attempt,
        "--property=Type=exec",
        "--property=RuntimeMaxSec=900",
        "--property=TimeoutStopSec=120",
        "--property=KillSignal=SIGTERM",
        "--property=ExecStopPost=/usr/bin/python3 -I -S -B "
        + FORWARD_SCRIPT
        + " settle --attempt "
        + attempt,
        "--setenv=LD_LIBRARY_PATH=/opt/autostop-sqlite/3.51.3/lib",
        "/usr/bin/python3",
        "-I",
        "-S",
        "-B",
        FORWARD_SCRIPT,
        "run",
        "--attempt",
        attempt,
    )


def _unit_object_path(unit):
    io.require(unit in UNITS, "native_unit_required")
    escaped = "".join(
        character if character.isalnum() else f"_{ord(character):02x}" for character in unit
    )
    return "/org/freedesktop/systemd1/unit/" + escaped


def _empty_hooks_command(unit, hooks):
    io.require(
        bool(hooks) and tuple(sorted(set(hooks))) == tuple(hooks) and set(hooks) <= set(HOOKS),
        "fixed_empty_hook_query_required",
    )
    return [
        "/usr/bin/busctl",
        "--system",
        "--json=short",
        "get-property",
        "org.freedesktop.systemd1",
        _unit_object_path(unit),
        "org.freedesktop.systemd1.Service",
        *hooks,
    ]


def _show_fields(raw, expected, *, omitted=()):
    rows = [line.split("=", 1) for line in raw.decode().splitlines()]
    io.require(
        all(len(row) == 2 for row in rows) and len({row[0] for row in rows}) == len(rows),
        "unit_status_incomplete",
    )
    fields = dict(rows)
    io.require(
        set(fields) <= set(expected) and set(expected) - set(fields) <= set(omitted),
        "unit_status_incomplete",
    )
    return fields


def _queued_jobs(raw):
    value = io.document(raw)
    io.require(
        type(value) is dict
        and set(value) == {"type", "data"}
        and value["type"] == "a(usssoo)"
        and type(value["data"]) is list
        and len(value["data"]) == 1
        and type(value["data"][0]) is list,
        "job_list_shape_invalid",
    )
    jobs = value["data"][0]
    io.require(len(jobs) <= 65536, "job_list_limit")
    for row in jobs:
        io.require(
            type(row) is list
            and len(row) == 6
            and type(row[0]) is int
            and 0 < row[0] <= 2**32 - 1
            and all(type(item) is str and item for item in row[1:])
            and row[4] == "/org/freedesktop/systemd1/job/" + str(row[0])
            and re.fullmatch(r"/org/freedesktop/systemd1/unit/[A-Za-z0-9_]+", row[5]),
            "job_row_shape_invalid",
        )
    return jobs


def _readonly_control_child(argv):
    git = ("/usr/bin/git", "-C", CRM.as_posix())
    if argv in {
        (*git, "ls-files", "scripts/j1_forward_*.py"),
        (*git, "rev-parse", "HEAD"),
        (*git, "rev-parse", "HEAD^{tree}"),
        (*git, "branch", "--show-current"),
        (*git, "status", "--porcelain"),
        (*git, "ls-remote", "origin", "refs/heads/autostopcrm-v1"),
    }:
        return "/usr/bin/git"
    if argv == JOB_QUERY:
        return "/usr/bin/busctl"
    # Both native authority and closure use read-only systemctl show. The
    # property's value is one argv element, never interpreted by a shell.
    if (
        len(argv) in {5, 6}
        and argv[:2] == ("/usr/bin/systemctl", "show")
        and (argv[2] in UNITS or re.fullmatch(r"[A-Za-z0-9_.@:-]+\.(timer|path|socket)", argv[2]))
        and argv[3] == "--no-pager"
        and (len(argv) == 5 or argv[4] == "--all")
        and re.fullmatch(r"--property=[A-Za-z][A-Za-z,]*", argv[-1])
    ):
        return "/usr/bin/systemctl"
    if (
        8 <= len(argv) <= 7 + len(HOOKS)
        and argv[:5]
        == (
            "/usr/bin/busctl",
            "--system",
            "--json=short",
            "get-property",
            "org.freedesktop.systemd1",
        )
        and argv[5] in {_unit_object_path(unit) for unit in UNITS}
        and argv[6] == "org.freedesktop.systemd1.Service"
        and tuple(sorted(set(argv[7:]))) == argv[7:]
        and set(argv[7:]) <= set(HOOKS)
    ):
        return "/usr/bin/busctl"
    return None


class LinuxRunner:
    """Fixed-effect backend. Every command argument is code-owned."""

    def command(self, args, deadline):
        result = subprocess.run(
            args,
            cwd=CRM,
            capture_output=True,
            timeout=_remaining(deadline),
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
        io.require(result.returncode == 0, "closure_command_failed")
        io.require(len(result.stdout) <= 16 * 1024**2, "closure_output_limit")
        return result.stdout

    def boot_id(self):
        return _proc_bytes(Path("/proc/sys/kernel/random/boot_id"), 100).decode().strip()

    def save(self, directory, value):
        io.protected_directory(directory, private=True)
        io.write_new(directory / "closure-owned.json", value)

    def load(self, directory):
        return io.document(io.read_protected(directory / "closure-owned.json"))

    def save_scope(self, directory, value):
        io.protected_directory(directory, private=True)
        return io.write_new(directory / "process-scope.json", value)

    def load_scope(self, reference):
        return io.document(io.referenced(reference))

    def capture_scope(self, native, closure):
        return process_scope.capture(
            native,
            attempt_id=closure["attempt_id"],
            attempt_dir=Path(closure["attempt_dir"]),
            consumer_sha256=closure["consumer_sha256"],
        )

    def verify_scope(self, scope, *, frozen):
        return process_scope.verify(scope, frozen=frozen)

    def scope_actors(self, scope):
        return process_scope.actor_sets(scope)

    def activation_scope(self, scope, *, released=False):
        return (
            process_scope.verify_released(scope)
            if released
            else process_scope.verify_activation(scope)
        )

    def staging_scope(self, scope):
        return process_scope.verify_staging(scope)

    def admission_window(self, closure):
        plan = io.document(io.read_protected(Path(closure["attempt_dir"]) / "plan.json"))
        scope = self.load_scope(closure["process_scope_ref"])
        forward_state.validate_plan(plan, scope["base_sha256"])
        io.require(
            plan["attempt_id"] == closure["attempt_id"]
            and plan["candidate"]["consumer_sha256"] == closure["consumer_sha256"],
            "ingress_plan_binding",
        )
        return _utc(plan["created_at"]), _utc(plan["deadline_utc"])

    def idle_receipt(self, closure, reference):
        io.require(
            type(reference) is dict and set(reference) == {"path", "sha256"},
            "ingress_idle_reference",
        )
        path = Path(reference["path"])
        io.require(
            path.parent == Path(closure["attempt_dir"])
            and re.fullmatch(r"ingress-idle-[0-9a-f]{32}\.json", path.name),
            "ingress_idle_reference",
        )
        value = io.document(io.referenced(reference))
        start, end = self.admission_window(closure)
        io.require(
            type(value) is dict
            and set(value)
            == {
                "observed_at",
                "metadata_only",
                "all_loaded_and_wake_idle",
                "active_turns",
                "loaded_count",
                "rpc_sha256",
                "wake_sha256",
            }
            and value.get("metadata_only") is True
            and value.get("all_loaded_and_wake_idle") is True
            and type(value.get("active_turns")) is int
            and value["active_turns"] == 0
            and type(value.get("loaded_count")) is int
            and value["loaded_count"] >= 0
            and value.get("rpc_sha256") == host_policy.RPC_SHA
            and value.get("wake_sha256") == host_policy.WAKE_SHA
            and start <= _utc(value["observed_at"]) <= end,
            "ingress_idle_not_proven",
        )
        return value

    def save_drain(self, closure, value):
        return io.write_new(Path(closure["attempt_dir"]) / "ingress-drain.json", value)

    def load_drain(self, closure):
        path = Path(closure["attempt_dir"]) / "ingress-drain.json"
        try:
            value = io.document(io.read_protected(path))
        except FileNotFoundError:
            return None, None
        return value, io.reference(path)

    def committed(self, closure, commit_ref):
        directory = Path(closure["attempt_dir"])
        plan = io.document(io.read_protected(directory / "plan.json"))
        scope = self.load_scope(closure["process_scope_ref"])
        forward_state.validate_plan(plan, scope["base_sha256"])
        root = ATTEMPTS.parent / "state"
        io.require(
            type(commit_ref) is dict
            and commit_ref.get("path") == str(root / "history" / f"{plan['sequence']:020d}.json"),
            "auxiliary_commit_reference",
        )
        record = io.document(io.referenced(commit_ref))
        self.control_bindings(closure)
        io.require(
            record.get("plan") == plan
            and plan["attempt_id"] == closure["attempt_id"]
            and plan["candidate"]["consumer_sha256"] == closure["consumer_sha256"]
            and record.get("proofs", {}).get("closure")
            == io.reference(directory / "closure-owned.json"),
            "auxiliary_commit_binding",
        )
        store = forward_state.Store(root, scope["base_sha256"])
        expected = {"sequence": plan["sequence"], "sha256": commit_ref["sha256"], "record": record}
        io.require(store.head() == expected, "auxiliary_commit_head")
        io.confirm_durable(commit_ref)
        io.confirm_durable(io.reference(root / "HEAD.json"))
        io.require(store.head() == expected, "auxiliary_commit_head")

    def read_barrier(self, path):
        try:
            return io.read_protected(path, 4096)
        except FileNotFoundError:
            return None

    def put_barrier(self, path):
        io.protected_directory(SYSTEMD)
        if not path.parent.exists():
            path.parent.mkdir(mode=0o755)
            io.fsync_directory(SYSTEMD)
        io.protected_directory(path.parent)
        temporary = path.with_name("." + path.name + "." + str(uuid.uuid4()))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(DENY_BYTES)
                stream.flush()
                os.fsync(stream.fileno())
            # Atomic, exclusive publication: neither short writes nor a competing
            # owner can leave an incomplete effective systemd drop-in.
            os.link(temporary, path, follow_symlinks=False)
        finally:
            temporary.unlink()
            io.fsync_directory(path.parent)

    def remove_barrier(self, path):
        io.require(self.read_barrier(path) == DENY_BYTES, "barrier_not_owned")
        path.unlink()
        io.fsync_directory(path.parent)

    def reload(self, deadline):
        self.command(["/usr/bin/systemctl", "daemon-reload"], deadline)

    def _store_stop(self, ownership, deadline):
        # Reconstruct from the same protected plan in the independent settlement
        # process too. A receipt from an earlier PID or activation is no permit.
        plan, baseline = self._control_baseline(ownership)
        self.control_bindings(ownership)
        native = baseline["native"]
        io.require(
            native.get("sha256")
            == io.sha(
                io.canonical({key: value for key, value in native.items() if key != "sha256"})
            ),
            "store_stop_native_digest",
        )
        binding = {
            "schema": "autostop.j1.crm-forward-store-stop.v1",
            "attempt_id": ownership["attempt_id"],
            "consumer_sha256": ownership["consumer_sha256"],
            "native_baseline_sha256": ownership["baseline_sha256"],
            "baseline_ref": plan["baseline_ref"],
            "process_scope_ref": ownership["process_scope_ref"],
        }
        try:
            drain_deadline = min(deadline, time.monotonic() + 120)
            result = store_drain.drain_before_stop(
                native, deadline=drain_deadline, command=self.command
            )
            io.require(
                type(result) is dict
                and result.get("ok") is True
                and result.get("stop_permitted") is True,
                "store_stop_not_permitted",
            )
            receipt = self._store_stop_receipt(ownership, binding, result)
            store_drain.verify_stop_identity(
                native, result, deadline=drain_deadline, command=self.command
            )
        except Exception as error:
            self._store_stop_receipt(
                ownership,
                binding,
                {
                    "ok": False,
                    "stop_permitted": False,
                    "error_code": str(error)
                    if isinstance(error, io.Refusal)
                    else "store_drain_failed",
                },
            )
            raise
        return receipt

    @staticmethod
    def _store_stop_receipt(ownership, binding, result):
        return io.write_new(
            Path(ownership["attempt_dir"]) / ("store-drain-" + uuid.uuid4().hex + ".json"),
            {
                **binding,
                "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "result": result,
            },
        )

    def stop(self, deadline, *, ownership):
        # This gate precedes BOTH systemd and Docker effects. Normal closure and
        # failure/ExecStopPost settlement must never bypass a pending Store write.
        self._store_stop(ownership, deadline)
        failures = []
        for args in (
            ["/usr/bin/systemctl", "stop", *UNITS],
            [
                *DOCKER,
                "compose",
                "--file",
                str(CRM / "docker-compose.yml"),
                "--project-directory",
                str(CRM),
                "stop",
                "autostopcrm",
            ],
        ):
            try:
                self.command(args, deadline)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise io.Refusal("writer_stop_failed") from failures[0]

    def actor(self, expected, *, full=False):
        pid = expected["pid"]
        root = Path("/proc") / str(pid)
        birth, _parent, state = _stat(pid)
        exe = os.readlink(root / "exe")
        info = (root / "exe").stat()
        uid_line = next(
            line for line in _proc_bytes(root / "status").splitlines() if line.startswith(b"Uid:")
        )
        uids = [int(value) for value in uid_line.split()[1:]]
        io.require(len(set(uids)) == 1, "actor_mixed_uid")
        actor = dict(
            pid=pid,
            start_ticks=birth,
            exe=exe,
            exe_identity=[info.st_dev, info.st_ino],
            argv_sha256=io.sha(_proc_bytes(root / "cmdline")),
            cgroup=_proc_bytes(root / "cgroup").decode().strip(),
            uid=uids[0],
            mount_namespace=os.readlink(root / "ns/mnt"),
        )
        if full:
            actor["exe_sha256"] = _executable_sha(root / "exe", info, exe=exe, pid=pid, birth=birth)
        tasks = []
        for task in (root / "task").iterdir():
            task_state = _proc_bytes(task / "stat").rsplit(b") ", 1)[1].split()[0]
            tasks.append(task_state == b"T")
        actor["stopped"] = state == "T" and bool(tasks) and all(tasks)
        io.require(_stat(pid)[0] == birth, "actor_reused_during_read")
        return actor

    def signal_actor(self, expected, stop, *, deadline=None):
        io.require(
            hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"), "pidfd_required"
        )
        fd = os.pidfd_open(expected["pid"], 0)
        try:
            actual = self.actor(expected, full=True)
            _same_actor(expected, actual, full=True)
            if deadline is not None:
                _remaining(deadline)
            signal.pidfd_send_signal(fd, signal.SIGSTOP if stop else signal.SIGCONT)
            observed_until = (
                min(deadline, time.monotonic() + 5)
                if deadline is not None
                else time.monotonic() + 5
            )
            while True:
                actual = self.actor(expected)
                _same_actor(expected, actual)
                if actual["stopped"] is stop:
                    break
                io.require(time.monotonic() < observed_until, "actor_signal_not_observed")
                time.sleep(0.01)
        finally:
            os.close(fd)

    def processes(self):
        result = {}
        for root in Path("/proc").iterdir():
            if not root.name.isdecimal():
                continue
            pid = int(root.name)
            try:
                birth, parent, state = _stat(pid)
                argv = _proc_bytes(root / "cmdline")
                cgroup = _proc_bytes(root / "cgroup").decode().strip()
                suspect = any(
                    word in argv
                    for word in (
                        b"autostop_manager",
                        b"minimal_kanban",
                        b"codex",
                        b"/opt/autostopcrm",
                        b"/var/cache/autostop-j1",
                        b"/root/.codex",
                    )
                )
                if _stat(pid)[0] != birth:
                    io.require(False, "process_changed_during_scan")
                result[pid] = {
                    "start_ticks": birth,
                    "ppid": parent,
                    "state": state,
                    "cgroup": cgroup,
                    "suspect": suspect,
                    # Kept only inside this metadata scan; never emitted in a
                    # receipt or shared report.
                    "argv": _argv(argv),
                }
            except (FileNotFoundError, ProcessLookupError):
                # An exited process cannot keep a descriptor or write mapping.
                io.require(not root.exists(), "process_unreadable")
        io.require(len(result) <= 65536, "process_scan_limit")
        return result

    def _control_baseline(self, closure):
        directory = ATTEMPTS / closure["attempt_id"]
        io.require(str(directory) == closure["attempt_dir"], "control_attempt_directory")
        plan = io.document(io.read_protected(directory / "plan.json"))
        baseline = io.document(io.referenced(plan["baseline_ref"]))
        io.require(
            plan["attempt_id"] == closure["attempt_id"]
            and plan["baseline_ref"]["path"] == str(directory / "baseline.json")
            and baseline["attempt_id"] == closure["attempt_id"]
            and io.sha(io.canonical(baseline["native"])) == closure["baseline_sha256"],
            "control_baseline_binding",
        )
        return plan, baseline

    def control_bindings(self, closure):
        """Bind the fixed script to this attempt's already protected plan."""
        plan, baseline = self._control_baseline(closure)
        consumers = baseline["source_consumers"]
        io.require(
            type(consumers) is dict
            and 8 <= len(consumers) <= 32
            and "scripts/j1_forward_release.py" in consumers
            and io.sha(io.canonical(consumers)) == plan["candidate"]["consumer_sha256"],
            "control_consumer_binding",
        )
        for name, digest in consumers.items():
            io.require(
                re.fullmatch(r"scripts/j1_forward_[a-z_]+\.py", name), "control_consumer_path"
            )
            io.require(io.reference(CRM / name)["sha256"] == digest, "control_consumer_changed")
        digest = plan["candidate"]["consumer_sha256"]
        io.require(closure.get("consumer_sha256", digest) == digest, "control_consumer_binding")
        return digest

    @staticmethod
    def control_identity(pid, row, program, *, unit=None):
        """Match a live PID incarnation to the protected executable and root."""
        root = Path("/proc") / str(pid)
        try:
            before = _stat(pid)
            io.require(before[:2] == (row["start_ticks"], row["ppid"]), "control_pid_changed")
            actual_argv = _argv(_proc_bytes(root / "cmdline"))
            io.require(actual_argv == row["argv"], "control_argv_changed")
            uid = next(
                line
                for line in _proc_bytes(root / "status").splitlines()
                if line.startswith(b"Uid:")
            )
            io.require(uid.split()[1:] == [b"0"] * 4, "control_root_required")
            trusted = Path(program).resolve(strict=True)
            trusted_bytes = io.read_protected(trusted, 64 * 1024**2)
            expected = trusted.stat()
            actual = (root / "exe").stat()
            io.require(
                os.readlink(root / "exe") == str(trusted)
                and (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino)
                and io.sha(_proc_bytes(root / "exe", 64 * 1024**2)) == io.sha(trusted_bytes),
                "control_executable_changed",
            )
            namespace = os.readlink(root / "ns/mnt")
            hostroot, processroot = Path("/").stat(), (root / "root").stat()
            io.require(
                namespace == os.readlink(Path("/proc/self/ns/mnt"))
                and (hostroot.st_dev, hostroot.st_ino) == (processroot.st_dev, processroot.st_ino),
                "control_namespace_changed",
            )
            cgroup = _proc_bytes(root / "cgroup").decode().strip()
            io.require(cgroup == row["cgroup"], "control_cgroup_changed")
            if unit is not None:
                io.require(cgroup == "0::/system.slice/" + unit, "control_service_required")
            io.require(_stat(pid)[:2] == before[:2], "control_pid_changed")
            return True
        except (FileNotFoundError, ProcessLookupError):
            # It grants no live exception. The descriptor scan still detects
            # reuse of a PID that was present in this metadata snapshot.
            io.require(not root.exists(), "control_process_unreadable")
            return False

    def control_processes(self, closure, processes):
        """Admit exact read-only coordination processes, never FD exemptions."""
        commands = _forward_commands(closure["attempt_id"])
        candidates = {
            pid: commands[row["argv"]]
            for pid, row in processes.items()
            if row.get("argv") in commands and row["state"] not in {"Z", "X"}
        }
        if not candidates:
            return set()
        self.control_bindings(closure)
        admitted = set()
        live = {}
        unit = "autostop-crm-forward-" + closure["attempt_id"] + ".service"
        for pid, operation in candidates.items():
            if self.control_identity(
                pid,
                processes[pid],
                "/usr/bin/python3",
                unit=unit if operation in {"run", "settle"} else None,
            ):
                live[pid] = operation
            admitted.add(pid)
        launch = _systemd_launch_command(closure["attempt_id"])
        for pid, row in processes.items():
            argv = row.get("argv", ())
            parent = row["ppid"]
            if argv == launch and live.get(parent) == "apply":
                self.control_identity(pid, row, "/usr/bin/systemd-run")
                admitted.add(pid)
            elif parent in live and (program := _readonly_control_child(argv)) is not None:
                self.control_identity(pid, row, program)
                admitted.add(pid)
        # SSH may retain a shell parent. Accept only a literal command equal to
        # its already authenticated child's argv, with no shell grammar.
        for child in live:
            parent = processes[child]["ppid"]
            if parent not in processes:
                continue
            row = processes[parent]
            argv = row.get("argv", ())
            if (
                len(argv) == 3
                and argv[0] in {"bash", "/bin/bash", "sh", "/bin/sh"}
                and argv[1] == "-c"
                and argv[2] == " ".join(processes[child]["argv"])
            ):
                program = "/bin/bash" if argv[0] in {"bash", "/bin/bash"} else "/bin/sh"
                self.control_identity(parent, row, program)
                admitted.add(parent)
        return admitted

    def source_roots(self):
        result = []
        for name in WRITER_ROOTS:
            path = Path(name)
            try:
                info = path.lstat()
            except FileNotFoundError:
                result.append({"path": name, "present": False})
                continue
            io.require(stat.S_ISDIR(info.st_mode) and not path.is_symlink(), "writer_root_type")
            result.append({"path": name, "present": True, "dev": info.st_dev, "ino": info.st_ino})
        return result

    def units(self, deadline):
        results = {}
        properties = (
            "LoadState",
            "ActiveState",
            "SubState",
            "NeedDaemonReload",
            "MainPID",
            "ExecCondition",
            *HOOKS,
            "TriggeredBy",
            "Job",
        )
        for unit in UNITS:
            raw = self.command(
                [
                    "/usr/bin/systemctl",
                    "show",
                    unit,
                    "--no-pager",
                    "--all",
                    "--property=" + ",".join(properties),
                ],
                deadline,
            )
            fields = _show_fields(raw, properties, omitted=HOOKS)
            missing = tuple(sorted(set(properties) - set(fields)))
            if missing:
                # systemctl can omit empty command arrays even with --all.
                # Only the fixed typed D-Bus empty value proves those omissions.
                typed = self.command(_empty_hooks_command(unit, missing), deadline)
                values = [io.document(line) for line in typed.splitlines()]
                io.require(len(values) == len(missing), "unit_empty_hooks_incomplete")
                for hook, value in zip(missing, values, strict=True):
                    io.require(
                        value == {"type": "a(sasbttttuii)", "data": []},
                        "unit_empty_hook_not_proven",
                    )
                    fields[hook] = ""
            io.require(set(fields) == set(properties), "unit_status_incomplete")
            results[unit] = fields
            for trigger in fields["TriggeredBy"].split():
                io.require(
                    re.fullmatch(r"[A-Za-z0-9_.@:-]+\.(timer|path|socket)", trigger),
                    "trigger_name_invalid",
                )
                status = self.command(
                    [
                        "/usr/bin/systemctl",
                        "show",
                        trigger,
                        "--no-pager",
                        "--all",
                        "--property=ActiveState,Job",
                    ],
                    deadline,
                )
                parsed = _show_fields(status, ("ActiveState", "Job"))
                io.require(
                    parsed.get("ActiveState") in {"inactive", "failed"} and not parsed.get("Job"),
                    "trigger_active",
                )
        jobs = _queued_jobs(self.command(list(JOB_QUERY), deadline))
        io.require(
            not any(row[1] in UNITS for row in jobs),
            "native_job_queued",
        )
        return results

    def containers(self, deadline):
        ids = self.command([*DOCKER, "ps", "--quiet", "--no-trunc"], deadline).decode().split()
        io.require(all(re.fullmatch("[0-9a-f]{64}", item) for item in ids), "container_id_invalid")
        if not ids:
            return []
        template = (
            '{"Id":{{json .Id}},"Name":{{json .Name}},'
            '"Service":{{json (index .Config.Labels "com.docker.compose.service")}},'
            '"Mounts":{{json .Mounts}}}'
        )
        rows = [
            json.loads(line)
            for line in self.command(
                [*DOCKER, "inspect", "--format", template, *ids], deadline
            ).splitlines()
        ]
        io.require(type(rows) is list and len(rows) == len(ids), "container_scan_incomplete")
        for row in rows:
            io.require(row.get("Id") in ids, "container_identity_invalid")
            io.require(
                row.get("Name") != "/autostopcrm" and row.get("Service") != "autostopcrm",
                "crm_container_running",
            )
            for mount in row.get("Mounts", []):
                source = mount.get("Source", "")
                io.require(
                    not (
                        mount.get("RW") is True
                        and (
                            _under(source)
                            or any(
                                root.startswith(source.rstrip("/") + "/") for root in WRITER_ROOTS
                            )
                        )
                    ),
                    "alternative_container_writer",
                )
        return [{"id": row["Id"]} for row in rows]

    def cache_views(self, deadline):
        parent = Path("/run/autostop-j1-cache-view-private")
        io.protected_directory(parent, private=True)
        for target, required in (("/var/cache/autostop-j1", "ro"), (str(parent / "rw"), "rw")):
            rows = json.loads(
                self.command(
                    [
                        "/usr/bin/findmnt",
                        "--json",
                        "--target",
                        target,
                        "--output",
                        "TARGET,OPTIONS",
                    ],
                    deadline,
                )
            )["filesystems"]
            io.require(
                len(rows) == 1
                and rows[0]["target"] == target
                and required in rows[0]["options"].split(","),
                "cache_mount_mode",
            )
        visible = Path("/var/cache/autostop-j1/research.sqlite3").stat()
        private = (parent / "rw/research.sqlite3").stat()
        io.require(
            (visible.st_dev, visible.st_ino) == (private.st_dev, private.st_ino),
            "cache_alias_identity",
        )

    def descriptor_scan(self, processes, frozen, own):
        inodes = self.source_inodes()
        frozen_fds = coordination = 0
        for pid, proc in processes.items():
            root = Path("/proc") / str(pid)
            try:
                for fd in (root / "fd").iterdir():
                    try:
                        target = os.readlink(fd).removesuffix(" (deleted)")
                        info = fd.stat()
                        if not _under(target) and (info.st_dev, info.st_ino) not in inodes:
                            continue
                        details = dict(
                            line.split(":", 1)
                            for line in _proc_bytes(root / "fdinfo" / fd.name).decode().splitlines()
                        )
                        io.require(
                            os.readlink(fd).removesuffix(" (deleted)") == target,
                            "source_descriptor_replaced",
                        )
                    except FileNotFoundError:
                        # A descriptor closed during enumeration cannot remain a
                        # writer. The process birth is still checked below.
                        continue
                    writable = int(details["flags"].strip(), 8) & os.O_ACCMODE != os.O_RDONLY
                    if not writable:
                        continue
                    if pid in frozen:
                        frozen_fds += 1
                        continue
                    if pid in own and self.coordination_path(target):
                        coordination += 1
                        continue
                    io.require(False, "unexpected_source_write_descriptor")
                for line in _proc_bytes(root / "maps", 16 * 1024**2).decode().splitlines():
                    fields = line.split(None, 5)
                    if len(fields) == 6 and "w" in fields[1] and "s" in fields[1]:
                        target = fields[5].removesuffix(" (deleted)")
                        major, minor = (int(item, 16) for item in fields[3].split(":"))
                        identity = (os.makedev(major, minor), int(fields[4]))
                        if _under(target) or identity in inodes:
                            io.require(
                                pid in frozen or (pid in own and self.coordination_path(target)),
                                "unexpected_source_write_mapping",
                            )
                io.require(_stat(pid)[0] == proc["start_ticks"], "descriptor_process_reused")
            except (FileNotFoundError, ProcessLookupError):
                io.require(not root.exists(), "descriptor_scan_incomplete")
        return {"frozen_owned_write_fds": frozen_fds, "owned_sqlite_coordination_fds": coordination}

    @staticmethod
    def source_inodes():
        """Metadata only; include hardlink and mount-namespace aliases of sources."""
        result = set()

        def failed(_error):
            raise io.Refusal("source_inventory_incomplete")

        for name in WRITER_ROOTS:
            path = Path(name)
            if not path.exists():
                continue
            for directory, directories, files in os.walk(path, followlinks=False, onerror=failed):
                directories[:] = [
                    child for child in directories if not (Path(directory) / child).is_symlink()
                ]
                for child in files:
                    info = (Path(directory) / child).lstat()
                    if stat.S_ISREG(info.st_mode):
                        result.add((info.st_dev, info.st_ino))
                    io.require(len(result) <= 200000, "source_inventory_limit")
        return result

    @staticmethod
    def coordination_path(target):
        if not _under(target) or not target.endswith("-shm"):
            return False
        primary = Path(target[:-4])
        info = primary.lstat()
        io.require(
            stat.S_ISREG(info.st_mode) and not primary.is_symlink(), "coordination_primary_invalid"
        )
        # Only an actual SQLite primary's exact auxiliary path qualifies.
        with primary.open("rb") as stream:
            return stream.read(16) == b"SQLite format 3\0"


RUNNER = LinuxRunner()


def _same_actor(expected, actual, *, full=False):
    fields = ACTOR_FIELDS if full else IDENTITY_FIELDS
    io.require(all(actual.get(key) == expected.get(key) for key in fields), "warm_actor_changed")


def _actors(value):
    io.require(type(value) is list and len(value) == 3, "accepted_warm_actors_required")
    io.require({row.get("pid") for row in value} == ACCEPTED_PIDS, "warm_pid_set")
    for actor in value:
        io.require(
            type(actor) is dict and all(key in actor for key in ACTOR_FIELDS), "actor_fields"
        )
        io.require(
            type(actor["pid"]) is int
            and type(actor["start_ticks"]) is int
            and actor["start_ticks"] > 0
            and type(actor["uid"]) is int,
            "actor_numbers",
        )
        for key in ("argv_sha256", "exe_sha256"):
            io.require(
                type(actor[key]) is str and re.fullmatch("[0-9a-f]{64}", actor[key]), "actor_hash"
            )
        io.require(
            type(actor["exe"]) is str
            and actor["exe"].startswith("/")
            and type(actor["cgroup"]) is str
            and actor["cgroup"].startswith("0::/")
            and not any(unit in actor["cgroup"] for unit in UNITS),
            "warm_actor_group",
        )
    return value


def _paths():
    return [
        {"unit": unit, "path": str(SYSTEMD / (unit + ".d") / DROPIN), "sha256": io.sha(DENY_BYTES)}
        for unit in UNITS
    ]


def _validate(closure):
    io.require(type(closure) is dict and closure.get("schema") == SCHEMA, "closure_schema")
    io.require(
        closure.get("dropins") == _paths() and closure.get("bytes_sha256") == io.sha(DENY_BYTES),
        "closure_fixed_barriers",
    )
    _actors(closure.get("warm_actors"))
    directory = Path(closure["attempt_dir"])
    io.require(
        directory.is_absolute()
        and ".." not in directory.parts
        and str(uuid.UUID(closure["attempt_id"])) == closure["attempt_id"],
        "closure_attempt",
    )
    io.require(RUNNER.load(directory) == closure, "closure_ownership_changed")
    io.require(RUNNER.boot_id() == closure["boot_id"], "closure_boot_changed")
    _scope(closure)
    return directory


def _scope(closure):
    reference = closure.get("process_scope_ref")
    io.require(
        type(reference) is dict
        and set(reference) == {"path", "sha256"}
        and reference["path"] == str(Path(closure["attempt_dir"]) / "process-scope.json"),
        "closure_scope_reference",
    )
    scope = RUNNER.load_scope(reference)
    _scope_binding(closure, scope)
    return scope


def _scope_binding(closure, scope):
    io.require(
        scope.get("attempt_id") == closure["attempt_id"]
        and scope.get("attempt_dir") == closure["attempt_dir"]
        and scope.get("consumer_sha256") == closure["consumer_sha256"]
        and scope.get("native_sha256") == closure["baseline_sha256"]
        and scope.get("boot_id") == closure["boot_id"]
        and scope.get("original_actors")
        == [
            {key: actor[key] for key in ACTOR_FIELDS}
            for actor in sorted(closure["warm_actors"], key=lambda row: row["pid"])
        ],
        "closure_scope_binding",
    )
    frozen, transport = RUNNER.scope_actors(scope)
    io.require(
        frozen == closure["freeze_actors"]
        and frozen
        and frozen[0]["pid"] == 538285
        and 538314 not in {actor["pid"] for actor in frozen}
        and not {actor["pid"] for actor in frozen} & {actor["pid"] for actor in transport},
        "closure_freeze_cohort",
    )
    return frozen, transport


def _ingress_drain(closure, *, required=False):
    value, reference = RUNNER.load_drain(closure)
    if value is None:
        io.require(not required, "ingress_drain_missing")
        return None
    io.require(
        type(value) is dict
        and set(value)
        == {
            "schema",
            "attempt_id",
            "process_scope_ref",
            "idle_observation_ref",
            "staging_observation",
            "observed_at",
        }
        and value.get("schema") == "autostop.j1.crm-forward-ingress-drain.v1"
        and value.get("attempt_id") == closure["attempt_id"]
        and value.get("process_scope_ref") == closure["process_scope_ref"],
        "ingress_drain_binding",
    )
    idle = RUNNER.idle_receipt(closure, value["idle_observation_ref"])
    start, end = RUNNER.admission_window(closure)
    io.require(
        start <= _utc(idle["observed_at"]) <= _utc(value["observed_at"]) <= end,
        "ingress_admission_window",
    )
    scope = _scope(closure)
    staging = value.get("staging_observation", {})
    io.require(
        type(staging) is dict
        and set(staging)
        == {
            "schema",
            "scope_sha256",
            "auxiliary_released",
            "freeze_actors",
            "transport_actors",
            "native_control_peer",
            "topology_sha256",
        }
        and staging.get("schema") == "autostop.j1.crm-forward-process-scope-staging.v1"
        and staging.get("scope_sha256") == closure["process_scope_ref"]["sha256"]
        and staging.get("auxiliary_released") is False
        and staging.get("native_control_peer", "missing") is None
        and staging.get("topology_sha256") == io.sha(io.canonical(scope["observation"])),
        "ingress_staging_binding",
    )
    writers, transports = RUNNER.scope_actors(scope)
    for field, expected in (("freeze_actors", writers), ("transport_actors", transports)):
        actual = staging[field]
        io.require(type(actual) is list and len(actual) == len(expected), "ingress_staging_actors")
        for row, actor in zip(actual, expected, strict=True):
            stopped = field == "freeze_actors" and actor["pid"] != 538285
            io.require(
                type(row) is dict
                and set(row) == {*ACTOR_FIELDS, "stopped", "thread_states"}
                and {key: row[key] for key in ACTOR_FIELDS} == actor
                and row["stopped"] is stopped
                and type(row["thread_states"]) is list
                and bool(row["thread_states"])
                and all(
                    state == "T"
                    if stopped
                    else type(state) is str and state in {"R", "S", "D", "I", "K", "W", "P"}
                    for state in row["thread_states"]
                ),
                "ingress_staging_actors",
            )
    return reference


def _related(processes, seeds):
    found = set(seeds)
    while True:
        more = {pid for pid, row in processes.items() if row["ppid"] in found}
        if more <= found:
            return found
        found |= more


def _process_policy(closure, processes, *, runtime_closed=True):
    scope = _scope(closure)
    writers, transport_actors = RUNNER.scope_actors(scope)
    frozen = {actor["pid"] for actor in writers}
    transport = {actor["pid"] for actor in transport_actors}
    cohort = frozen | transport
    io.require(_related(processes, cohort) == cohort, "unknown_warm_descendant")
    for actor in (*writers, *transport_actors):
        row = processes.get(actor["pid"], {})
        io.require(
            row.get("start_ticks") == actor["start_ticks"] and row.get("cgroup") == actor["cgroup"],
            "scope_process_changed",
        )
    own = _related(processes, {os.getpid()})
    ancestors = set()
    current = os.getpid()
    while current in processes and current not in ancestors:
        ancestors.add(current)
        current = processes[current]["ppid"]
    io.require(not frozen & ancestors, "refuse_caller_freeze")
    controls = RUNNER.control_processes(closure, processes) if runtime_closed else set()
    for pid, row in processes.items():
        native_group = any(
            row["cgroup"] == "0::/system.slice/" + unit
            or row["cgroup"].startswith("0::/system.slice/" + unit + "/")
            for unit in UNITS
        )
        io.require(not (runtime_closed and native_group), "native_cgroup_not_empty")
        io.require(
            not (
                runtime_closed
                and row["suspect"]
                and pid not in cohort | own | ancestors | controls
                and row["state"] not in {"Z", "X"}
            ),
            "unexpected_runtime_actor",
        )
    return frozen, own


def _barriers(closure):
    for entry in closure["dropins"]:
        io.require(RUNNER.read_barrier(Path(entry["path"])) == DENY_BYTES, "barrier_changed")


def _unit_policy(units, *, stopped=True):
    io.require(set(units) == set(UNITS), "unit_set_changed")
    for status in units.values():
        io.require(
            status.get("LoadState") == "loaded"
            and status.get("NeedDaemonReload") == "no"
            and "path=/usr/bin/false" in status.get("ExecCondition", "")
            and all(not status.get(key) for key in HOOKS),
            "loaded_barrier_invalid",
        )
        if stopped:
            io.require(
                status.get("ActiveState") in {"inactive", "failed"}
                and status.get("MainPID") == "0"
                and not status.get("Job"),
                "unit_not_stopped",
            )


def _closed_actors(closure, deadline, *, full=False):
    scope = _scope(closure)
    writers, transport = RUNNER.scope_actors(scope)
    actors = []
    transports = []
    with _executable_hash_pass() if full else nullcontext():
        for expected in writers:
            _remaining(deadline)
            actual = RUNNER.actor(expected, full=full)
            _same_actor(expected, actual, full=full)
            io.require(actual.get("stopped") is True, "warm_actor_not_frozen")
            actors.append(
                {"pid": actual["pid"], "start_ticks": actual["start_ticks"], "stopped": True}
            )
        for expected in transport:
            _remaining(deadline)
            actual = RUNNER.actor(expected, full=full)
            _same_actor(expected, actual, full=full)
            io.require(actual.get("stopped") is False, "scope_transport_stopped")
            transports.append(
                {"pid": actual["pid"], "start_ticks": actual["start_ticks"], "stopped": False}
            )
    return scope, writers, actors, transports


def sample_closed(closure: dict) -> dict:
    """Sample a closed generation without a full scan or its shared lock.

    The caller must stop and join its samplers before any closure mutation.
    This sample cannot replace a completed full observation or authorize a
    phase transition. Both clocks are local to this process; consumers bound
    freshness from the start so a delayed sample cannot appear newly checked.
    """
    started = time.monotonic()
    deadline = started + 30
    _validate(closure)
    _barriers(closure)
    drain_ref = _ingress_drain(closure, required=True)
    _scope_value, _writers, actors, transports = _closed_actors(closure, deadline)
    _barriers(closure)
    _remaining(deadline)
    return {
        "schema": "autostop.j1.crm-forward-closure-sample.v1",
        "attempt_id": closure["attempt_id"],
        "boot_id": closure["boot_id"],
        "process_id": os.getpid(),
        "closure_sha256": io.sha(io.canonical(closure)),
        "process_scope_ref": dict(closure["process_scope_ref"]),
        "scope_sha256": closure["process_scope_ref"]["sha256"],
        "ingress_drain_ref": drain_ref,
        "freeze_actors": actors,
        "transport_actors": transports,
        "sampled_started_monotonic": started,
        "sampled_at": time.time(),
        "sampled_monotonic": time.monotonic(),
    }


def _observe(closure, *, force_full=False, deadline=None, require_admission=False):
    deadline = deadline or time.monotonic() + 30
    _validate(closure)
    _barriers(closure)
    drain_ref = _ingress_drain(closure, required=require_admission)
    now = time.monotonic()
    key = (os.getpid(), io.sha(io.canonical(closure)))
    cached = _CACHE.get(key)
    full = force_full or cached is None or now - cached[0] >= 1.0
    scope, writers, actors, transports = _closed_actors(closure, deadline, full=full)
    if full:
        scope_observation = RUNNER.verify_scope(scope, frozen=True)
        units = RUNNER.units(deadline)
        _unit_policy(units)
        processes = RUNNER.processes()
        frozen, own = _process_policy(closure, processes)
        io.require(RUNNER.source_roots() == closure["source_roots"], "writer_roots_changed")
        RUNNER.cache_views(deadline)
        containers = RUNNER.containers(deadline)
        descriptors = RUNNER.descriptor_scan(processes, frozen, own)
        _remaining(deadline)
        # Recheck after the expensive scan; a mid-scan thaw is never hidden.
        _barriers(closure)
        for expected in writers:
            actual = RUNNER.actor(expected)
            _same_actor(expected, actual)
            io.require(actual["stopped"], "actor_thawed_during_scan")
        scope_observation = RUNNER.verify_scope(scope, frozen=True)
        result = {
            "schema": "autostop.j1.crm-forward-closure-observation.v2",
            "attempt_id": closure["attempt_id"],
            "boot_id": closure["boot_id"],
            "full_scan_at": time.time(),
            "full_scan_count": 1 if cached is None else cached[1]["full_scan_count"] + 1,
            "units_stopped": list(UNITS),
            "other_container_count": len(containers),
            "process_scope_ref": closure["process_scope_ref"],
            "scope_sha256": closure["process_scope_ref"]["sha256"],
            "freeze_actors": actors,
            "transport_actors": transports,
            "process_scope_observation": scope_observation,
            "ingress_drain_ref": drain_ref,
            **descriptors,
        }
        _CACHE[key] = (time.monotonic(), result)
    else:
        result = cached[1]
    return {
        **result,
        "sampled_at": time.time(),
        "full_scan_this_sample": full,
        "full_scan_age_seconds": time.monotonic() - _CACHE[key][0],
    }


def observe_closed(closure: dict, *, force_full=False) -> dict:
    """Fresh identity/barrier checks; full metadata scan at least once/second."""
    with _LOCK:
        return _observe(closure, force_full=force_full, require_admission=True)


def _activation_actors(closure, *, released):
    scope = _scope(closure)
    writers, transports = RUNNER.scope_actors(scope)
    observed_writers, observed_transports = [], []
    with _executable_hash_pass():
        for group, output in ((writers, observed_writers), (transports, observed_transports)):
            for expected in group:
                actual = RUNNER.actor(expected, full=True)
                _same_actor(expected, actual, full=True)
                stopped = group is writers and expected["pid"] != 538285 and not released
                io.require(actual.get("stopped") is stopped, "activation_actor_stop_state")
                output.append(
                    {"pid": actual["pid"], "start_ticks": actual["start_ticks"], "stopped": stopped}
                )
    return observed_writers, observed_transports


def _open_barriers(closure):
    for entry in closure["dropins"]:
        io.require(RUNNER.read_barrier(Path(entry["path"])) is None, "activation_barrier_present")


def observe_activation(closure: dict) -> dict:
    """Observe the guarded daemon online while owned auxiliaries remain frozen."""
    with _LOCK:
        _validate(closure)
        _open_barriers(closure)
        drain_ref = _ingress_drain(closure, required=True)
        scope_observation = RUNNER.activation_scope(_scope(closure))
        writers, transports = _activation_actors(closure, released=False)
        return {
            "schema": "autostop.j1.crm-forward-activation-observation.v2",
            "attempt_id": closure["attempt_id"],
            "boot_id": closure["boot_id"],
            "sampled_at": time.time(),
            "process_scope_ref": closure["process_scope_ref"],
            "scope_sha256": closure["process_scope_ref"]["sha256"],
            "freeze_actors": writers,
            "transport_actors": transports,
            "process_scope_observation": scope_observation,
            "ingress_drain_ref": drain_ref,
        }


def _prepared_ownership(baseline: dict, attempt_dir: Path, *, reuse: bool) -> dict:
    """Observe immutable ownership inputs without changing writer state."""
    actors = _actors(baseline.get("warm_actors"))
    attempt_id = str(uuid.UUID(attempt_dir.name))
    io.require(
        attempt_dir.is_absolute()
        and ".." not in attempt_dir.parts
        and attempt_dir.name == attempt_id,
        "closure_attempt",
    )
    closure = {
        "schema": SCHEMA,
        "attempt_id": attempt_id,
        "attempt_dir": str(attempt_dir),
        "boot_id": RUNNER.boot_id(),
        "dropins": _paths(),
        "bytes_sha256": io.sha(DENY_BYTES),
        "warm_actors": actors,
        "baseline_sha256": io.sha(io.canonical(baseline)),
        "source_roots": RUNNER.source_roots(),
    }
    closure["consumer_sha256"] = RUNNER.control_bindings(closure)
    for actor in actors:
        _same_actor(actor, RUNNER.actor(actor, full=True), full=True)
    for entry in closure["dropins"]:
        io.require(RUNNER.read_barrier(Path(entry["path"])) is None, "barrier_already_exists")
    existing = None
    if reuse:
        try:
            existing = RUNNER.load(attempt_dir)
        except FileNotFoundError:
            pass
    if existing is not None:
        io.require(
            all(existing.get(key) == value for key, value in closure.items()),
            "prepared_closure_changed",
        )
        _validate(existing)
        RUNNER.verify_scope(_scope(existing), frozen=False)
        _process_policy(existing, RUNNER.processes(), runtime_closed=False)
        return existing
    scope = RUNNER.capture_scope(baseline, closure)
    closure["freeze_actors"] = RUNNER.scope_actors(scope)[0]
    _scope_binding(closure, scope)
    RUNNER.verify_scope(scope, frozen=False)
    closure["process_scope_ref"] = RUNNER.save_scope(attempt_dir, scope)
    _process_policy(closure, RUNNER.processes(), runtime_closed=False)
    RUNNER.save(attempt_dir, closure)
    return closure


def prepare_closure(baseline: dict, attempt_dir: Path) -> dict:
    """Save exclusive ownership before drain; no barrier, stop or signal effects."""
    with _LOCK:
        return _prepared_ownership(baseline, attempt_dir, reuse=False)


def close_writers(baseline: dict, attempt_dir: Path, *, deadline: float, idle_observer) -> dict:
    """Reuse exact prepared ownership or persist it before writer effects."""
    with _LOCK:
        io.require(callable(idle_observer), "ingress_idle_observer_required")
        _remaining(deadline)
        closure = _prepared_ownership(baseline, attempt_dir, reuse=True)
        _remaining(deadline)
        try:
            for entry in closure["dropins"]:
                RUNNER.put_barrier(Path(entry["path"]))
            RUNNER.reload(deadline)
            _unit_policy(RUNNER.units(deadline), stopped=False)
            RUNNER.stop(deadline, ownership=closure)
            scope = _scope(closure)
            RUNNER.verify_scope(scope, frozen=False)
            _process_policy(closure, RUNNER.processes())
            roles = scope["observation"]["roles"]
            auxiliaries = sorted(
                closure["freeze_actors"][1:],
                key=lambda actor: (roles[str(actor["pid"])] != "proxy", actor["pid"]),
            )
            for actor in auxiliaries:
                actual = RUNNER.actor(actor, full=True)
                _same_actor(actor, actual, full=True)
                io.require(actual.get("stopped") is False, "writer_already_stopped")
                RUNNER.signal_actor(actor, True)
            _activation_actors(closure, released=False)
            _remaining(deadline)
            idle_ref = idle_observer()
            RUNNER.idle_receipt(closure, idle_ref)
            staging = RUNNER.staging_scope(scope)
            RUNNER.save_drain(
                closure,
                {
                    "schema": "autostop.j1.crm-forward-ingress-drain.v1",
                    "attempt_id": closure["attempt_id"],
                    "process_scope_ref": closure["process_scope_ref"],
                    "idle_observation_ref": idle_ref,
                    "staging_observation": staging,
                    "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                },
            )
            _remaining(deadline)
            daemon = closure["freeze_actors"][0]
            actual = RUNNER.actor(daemon, full=True)
            _same_actor(daemon, actual, full=True)
            io.require(actual.get("stopped") is False, "writer_already_stopped")
            RUNNER.signal_actor(daemon, True)
            _observe(closure, force_full=True, deadline=deadline, require_admission=True)
            return closure
        except BaseException:
            # Never thaw or remove a partially installed fence on failure.
            fail_closed(closure)
            raise


def fail_closed(closure: dict) -> dict:
    """Best-effort restoration, preserving unknown files and PID incarnations."""
    with _LOCK:
        _validate(closure)
        deadline = time.monotonic() + 90
        errors = []
        for entry in closure["dropins"]:
            try:
                path = Path(entry["path"])
                current = RUNNER.read_barrier(path)
                io.require(current in (None, DENY_BYTES), "foreign_barrier_preserved")
                if current is None:
                    RUNNER.put_barrier(path)
            except Exception:
                errors.append("barrier_restore_failed")
        safe_to_stop = False
        try:
            RUNNER.reload(deadline)
            _unit_policy(RUNNER.units(deadline), stopped=False)
            safe_to_stop = True
        except Exception:
            errors.append("loaded_barrier_restore_failed")
        if safe_to_stop:
            try:
                RUNNER.stop(deadline, ownership=closure)
            except Exception:
                errors.append("writer_stop_failed")
        for actor in closure["freeze_actors"]:
            try:
                RUNNER.signal_actor(actor, True)
            except Exception:
                errors.append("owned_actor_freeze_failed")
        observation = None
        try:
            observation = _observe(closure, force_full=True, deadline=deadline)
        except Exception:
            errors.append("closure_readback_failed")
        return {
            "attempt_id": closure["attempt_id"],
            "errors": sorted(set(errors)),
            "observation": observation,
        }


def reopen_native(closure: dict, *, deadline=None) -> None:
    """Open the owned daemon for guarded activation; auxiliary writers stay T."""
    with _LOCK:
        deadline = deadline if deadline is not None else time.monotonic() + 90
        _observe(
            closure,
            force_full=True,
            require_admission=True,
            deadline=min(deadline, time.monotonic() + 30),
        )
        try:
            for entry in closure["dropins"]:
                _remaining(deadline)
                RUNNER.remove_barrier(Path(entry["path"]))
            RUNNER.reload(deadline)
            _remaining(deadline)
            RUNNER.signal_actor(closure["freeze_actors"][0], False, deadline=deadline)
            _open_barriers(closure)
            _activation_actors(closure, released=False)
            _remaining(deadline)
        except BaseException:
            fail_closed(closure)
            raise


def release_auxiliary(closure: dict, commit_ref: dict) -> dict:
    """Release only stored auxiliaries after exact durable HEAD, under caller's old lock."""
    with _LOCK:
        _validate(closure)
        RUNNER.committed(closure, commit_ref)
        observe_activation(closure)
        try:
            # The daemon is already online; no signal targets a transport.
            for actor in reversed(closure["freeze_actors"][1:]):
                actual = RUNNER.actor(actor, full=True)
                _same_actor(actor, actual, full=True)
                io.require(actual.get("stopped") is True, "auxiliary_not_owned_stopped")
                RUNNER.signal_actor(actor, False)
            scope_observation = RUNNER.activation_scope(_scope(closure), released=True)
            writers, transports = _activation_actors(closure, released=True)
            return {
                "schema": "autostop.j1.crm-forward-auxiliary-release.v2",
                "attempt_id": closure["attempt_id"],
                "commit_ref": commit_ref,
                "process_scope_ref": closure["process_scope_ref"],
                "freeze_actors": writers,
                "transport_actors": transports,
                "process_scope_observation": scope_observation,
                "sampled_at": time.time(),
            }
        except BaseException:
            fail_closed(closure)
            raise
