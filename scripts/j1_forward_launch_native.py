"""Linux producer and consumer of the one-attempt forward launch journal.

These fixed production entrypoints use root-protected files and independently
read systemd and /proc.  Import and bootstrap do not run application code, Git,
Docker, sockets, diagnostics or a state-store repair.  The only subprocess
before E is the bounded read-only systemctl observation.  Tests substitute this
native boundary; there is no environment-variable production bypass.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import socket
import stat
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from scripts import j1_forward_launch_permission as journal

CRM = Path("/opt/autostopcrm")
ROOT_UID = 0
FORWARD = CRM / "scripts/j1_forward_release.py"
ATTEMPTS = Path("/var/lib/autostop-crm-forward/attempts")
LOCK = CRM / ".autostop-deploy.lock"
MARKER = CRM / "data/.agent-gateway-maintenance"
ENVIRONMENT = {"PATH": "/usr/bin:/bin", "LANG": "C"}
WORKER_ENVIRONMENT = {"LD_LIBRARY_PATH": "/opt/autostop-sqlite/3.51.3/lib"}
PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "MainPID",
    "InvocationID",
    "ControlGroup",
)
require = journal.require


def worker_command(attempt):
    journal._uuid(attempt)
    return ["/usr/bin/python3", "-I", "-S", "-B", str(FORWARD), "run", "--attempt", attempt]


def launch_command(attempt):
    return [
        "/usr/bin/systemd-run",
        "--quiet",
        "--wait",
        "--collect",
        "--unit=autostop-crm-forward-" + journal._uuid(attempt),
        "--property=Type=exec",
        "--property=RuntimeMaxSec=900",
        "--property=TimeoutStopSec=120",
        "--property=KillSignal=SIGTERM",
        "--property=ExecStopPost=/usr/bin/python3 -I -S -B "
        + str(FORWARD)
        + " settle --attempt "
        + attempt,
        "--setenv=LD_LIBRARY_PATH=" + WORKER_ENVIRONMENT["LD_LIBRARY_PATH"],
        *worker_command(attempt),
    ]


def _root():
    require(os.name == "posix" and os.geteuid() == ROOT_UID, "launch_linux_root_required")


def _store(attempt):
    return journal.LaunchStore(journal.ROOT / journal._uuid(attempt), owner=ROOT_UID)


def boot():
    return journal._uuid(Path("/proc/sys/kernel/random/boot_id").read_text().strip())


def _file_digest(path):
    """Bounded protected executable/source digest, including stable inode."""
    path = Path(path)
    journal._directory(path.parent, ROOT_UID)
    before = path.lstat()
    require(
        stat.S_ISREG(before.st_mode)
        and before.st_uid in {0, ROOT_UID}
        and before.st_nlink == 1
        and not before.st_mode & 0o022
        and before.st_size <= 64 * 1024**2,
        "launch_source_file",
    )
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    digest = hashlib.sha256()
    try:
        require(
            journal._metadata(before) == journal._metadata(os.fstat(descriptor)),
            "launch_source_open_changed",
        )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while block := stream.read(1024**2):
                digest.update(block)
        require(
            journal._metadata(before)
            == journal._metadata(os.fstat(descriptor))
            == journal._metadata(path.lstat()),
            "launch_source_read_changed",
        )
    finally:
        os.close(descriptor)
    return {
        "path": str(path),
        "sha256": digest.hexdigest(),
        "identity": [before.st_dev, before.st_ino],
    }


def _lock_identity():
    journal._directory(LOCK.parent, ROOT_UID)
    info = LOCK.lstat()
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == ROOT_UID
        and info.st_nlink == 1
        and not info.st_mode & 0o022,
        "launch_original_lock_required",
    )
    return [info.st_dev, info.st_ino]


def _proc_stat(pid):
    raw = (Path("/proc") / str(pid) / "stat").read_bytes()
    fields = raw.rsplit(b") ", 1)[1].split()
    require(len(fields) >= 20 and fields[0] not in {b"Z", b"X"}, "launch_worker_exited")
    return int(fields[19])


def owner():
    return {"pid": os.getpid(), "start_ticks": _proc_stat(os.getpid())}


def _sources(consumers):
    require(
        type(consumers) is dict
        and 8 <= len(consumers) <= 48
        and "scripts/j1_forward_launch_native.py" in consumers
        and "scripts/j1_forward_launch_permission.py" in consumers,
        "launch_published_consumers_required",
    )
    for relative, digest in consumers.items():
        require(
            type(relative) is str and re.fullmatch(r"scripts/j1_forward_[a-z_]+\.py", relative),
            "launch_source_name",
        )
        journal._hash(digest)
        require(_file_digest(CRM / relative)["sha256"] == digest, "launch_consumer_changed")
    return journal.sha(journal.canonical(consumers))


def issue(plan_ref, consumers, baseline):
    """Called once by reviewed prepare, under the original deployment lock."""
    _root()
    plan_raw = journal._read(Path(plan_ref["path"]), ROOT_UID)
    require(journal.sha(plan_raw) == plan_ref["sha256"], "launch_plan_changed")
    plan = journal.document(plan_raw)
    attempt = journal._uuid(plan["attempt_id"])
    require(
        Path(plan_ref["path"]) == ATTEMPTS / attempt / "plan.json"
        and plan["candidate_may_have_written"] is True
        and plan["old_writer_allowed"] is False
        and plan["host"] == socket.gethostname(),
        "launch_plan_scope",
    )
    source_hash = _sources(consumers)
    require(source_hash == plan["candidate"]["consumer_sha256"], "launch_plan_source")
    lock_identity = _lock_identity()
    require(lock_identity == baseline["deployment_lock_identity"], "launch_lock_changed")
    now = time.time()
    deadline = datetime.fromisoformat(plan["deadline_utc"].replace("Z", "+00:00")).timestamp()
    require(0 < deadline - now <= 600, "launch_original_deadline")
    executable = _file_digest(Path("/usr/bin/python3").resolve(strict=True))
    context = {
        "schema": "autostop.j1.forward-launch-context.v1",
        "plan_ref": plan_ref,
        "baseline_ref": plan["baseline_ref"],
        "consumers": consumers,
        "deployment_lock_identity": lock_identity,
        "interpreter": executable,
        "worker_argv": worker_command(attempt),
        "launch_argv": launch_command(attempt),
        "worker_cwd": "/",
        "launcher_cwd": str(CRM),
        "launcher_environment": ENVIRONMENT,
        "worker_environment": WORKER_ENVIRONMENT,
        "issuer": owner(),
    }
    root = journal.ROOT
    if not root.exists():
        journal._directory(root.parent, ROOT_UID)
        root.mkdir(mode=0o700)
        journal._sync(root.parent)
    ticket = {
        "schema": journal.SCHEMA,
        "attempt_id": attempt,
        "nonce": str(uuid.uuid4()),
        "plan_ref": plan_ref,
        "source_sha256": source_hash,
        "context_sha256": journal.sha(journal.canonical(context)),
        "host": socket.gethostname(),
        "boot_id": boot(),
        "owner_generation": str(uuid.uuid4()),
        "unit": "autostop-crm-forward-" + attempt + ".service",
        "not_before": now,
        "expires_at": deadline,
    }
    store = _store(attempt)
    receipt = store.create(ticket)
    journal._new(store.root / "context.json", journal.canonical(context))
    # Recheck after durable publication; failed issuance is not renewable.
    validate(attempt)
    return {
        **receipt,
        "ticket_ref": {"path": str(store.root / "ticket.json"), "sha256": receipt["ticket_sha256"]},
    }


def validate(attempt):
    """Pure bootstrap. A root caller cannot substitute a plan, code or executor."""
    _root()
    store = _store(attempt)
    snapshot = store.read()
    ticket = snapshot["ticket"]
    context_raw = journal._read(store.root / "context.json", ROOT_UID)
    require(journal.sha(context_raw) == ticket["context_sha256"], "launch_context_changed")
    context = journal.document(context_raw)
    require(
        context.get("schema") == "autostop.j1.forward-launch-context.v1"
        and ticket["plan_ref"] == context["plan_ref"]
        and Path(context["plan_ref"]["path"]) == ATTEMPTS / attempt / "plan.json"
        and context["worker_argv"] == worker_command(attempt)
        and context["launch_argv"] == launch_command(attempt)
        and context["worker_cwd"] == "/"
        and context["launcher_cwd"] == str(CRM)
        and context["launcher_environment"] == ENVIRONMENT
        and context["worker_environment"] == WORKER_ENVIRONMENT,
        "launch_context_scope",
    )
    require(
        ticket["host"] == socket.gethostname() and ticket["boot_id"] == boot(),
        "launch_host_or_boot",
    )
    require(_sources(context["consumers"]) == ticket["source_sha256"], "launch_source_binding")
    require(
        _file_digest(Path("/usr/bin/python3").resolve(strict=True)) == context["interpreter"],
        "launch_interpreter_changed",
    )
    require(_lock_identity() == context["deployment_lock_identity"], "launch_lock_changed")
    raw = journal._read(Path(context["plan_ref"]["path"]), ROOT_UID)
    require(journal.sha(raw) == context["plan_ref"]["sha256"], "launch_plan_changed")
    plan = journal.document(raw)
    require(
        plan["attempt_id"] == attempt
        and plan["baseline_ref"] == context["baseline_ref"]
        and plan["candidate_may_have_written"] is True
        and plan["old_writer_allowed"] is False,
        "launch_plan_binding",
    )
    raw = journal._read(Path(context["baseline_ref"]["path"]), ROOT_UID)
    require(journal.sha(raw) == context["baseline_ref"]["sha256"], "launch_baseline_changed")
    return store, snapshot, context


def _unit(unit):
    result = subprocess.run(
        ["/usr/bin/systemctl", "show", unit, "--no-pager", "--property=" + ",".join(PROPERTIES)],
        env=ENVIRONMENT,
        capture_output=True,
        check=False,
        timeout=3,
    )
    require(result.returncode == 0 and len(result.stdout) <= 16384, "launch_unit_unavailable")
    values = {}
    for line in result.stdout.decode("utf-8", "strict").splitlines():
        key, separator, value = line.partition("=")
        require(separator and key in PROPERTIES and key not in values, "launch_unit_properties")
        values[key] = value
    require(set(values) == set(PROPERTIES), "launch_unit_properties")
    return values


def observe_worker(ticket, context, *, self_only=False):
    """Actual invocation/PID/birth/argv/executable/cwd/environment/cgroup join."""
    before = _unit(ticket["unit"])
    require(
        before["Id"] == ticket["unit"]
        and before["LoadState"] == "loaded"
        and before["ActiveState"] in {"activating", "active"}
        and re.fullmatch(r"[0-9a-f]{32}", before["InvocationID"]),
        "launch_unit_identity",
    )
    require(before["MainPID"].isdecimal() and int(before["MainPID"]) > 0, "launch_unit_mainpid")
    pid = int(before["MainPID"])
    require(not self_only or pid == os.getpid(), "launch_not_unit_main_process")
    birth = _proc_stat(pid)
    proc = Path("/proc") / str(pid)
    argv = proc.joinpath("cmdline").read_bytes()
    require(
        argv == b"\0".join(os.fsencode(value) for value in context["worker_argv"]) + b"\0",
        "launch_worker_argv",
    )
    require(
        os.readlink(proc / "exe") == context["interpreter"]["path"]
        and os.readlink(proc / "cwd") == context["worker_cwd"],
        "launch_worker_executable_or_cwd",
    )
    process_env = {}
    for part in proc.joinpath("environ").read_bytes().split(b"\0"):
        if part:
            key, separator, value = part.partition(b"=")
            require(separator and key not in process_env, "launch_worker_environment_shape")
            process_env[key] = value
    require(
        process_env.get(b"LD_LIBRARY_PATH") == os.fsencode(WORKER_ENVIRONMENT["LD_LIBRARY_PATH"])
        and not any(key in process_env for key in (b"LD_PRELOAD", b"PYTHONHOME", b"PYTHONPATH")),
        "launch_worker_environment",
    )
    status = proc.joinpath("status").read_text()
    uid = next(line for line in status.splitlines() if line.startswith("Uid:")).split()[1:]
    require(uid == ["0", "0", "0", "0"], "launch_worker_uid")
    cgroups = proc.joinpath("cgroup").read_text().splitlines()
    require(
        before["ControlGroup"].endswith("/" + ticket["unit"])
        and any(line.split(":", 2)[-1] == before["ControlGroup"] for line in cgroups),
        "launch_worker_cgroup",
    )
    require(_proc_stat(pid) == birth and _unit(ticket["unit"]) == before, "launch_worker_replaced")
    return {"invocation_id": before["InvocationID"], "pid": pid, "start_ticks": birth}


def _transition(store, ticket, event, payload):
    return store.transition(
        event,
        request_id=str(uuid.uuid4()),
        payload=payload,
        boot_id=ticket["boot_id"],
        generation=ticket["owner_generation"],
    )


def publish_acceptance(store, snapshot, context):
    ticket = snapshot["ticket"]
    worker = observe_worker(ticket, context)
    evidence = {
        "schema": "autostop.j1.forward-launch-native-acceptance.v1",
        "ticket_sha256": snapshot["state"]["ticket_sha256"],
        **{
            key: ticket[key]
            for key in ("attempt_id", "nonce", "boot_id", "unit", "context_sha256", "source_sha256")
        },
        **worker,
    }
    raw = journal.canonical(evidence)
    path = store.root / "native-acceptance.json"
    if path.exists():
        require(journal._read(path, ROOT_UID) == raw, "launch_acceptance_evidence_changed")
    else:
        journal._new(path, raw)
    receipt = {**worker, "evidence_ref": {"path": str(path), "sha256": journal.sha(raw)}}
    # Reobserve the same native process immediately before the short journal CAS.
    require(observe_worker(ticket, context) == worker, "launch_acceptance_worker_changed")
    _transition(store, ticket, "accept", {"receipt": receipt})
    return receipt


def submit_and_wait(attempt, *, timeout=1100):
    store, snapshot, context = validate(attempt)
    ticket = snapshot["ticket"]
    result = _transition(store, ticket, "submit", {"owner": owner()})
    require(result["action_authorized"], "launch_submit_authority_consumed")
    # S is already durable. Exceptions and uncertain systemd acknowledgement
    # consume this submission permanently, even if no A is observed.
    process = subprocess.Popen(
        context["launch_argv"],
        cwd=context["launcher_cwd"],
        env=context["launcher_environment"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        until = min(time.monotonic() + 12, time.monotonic() + timeout)
        while True:
            try:
                publish_acceptance(store, store.read(), context)
                break
            except (
                FileNotFoundError,
                ProcessLookupError,
                journal.Refusal,
                subprocess.TimeoutExpired,
            ):
                require(
                    process.poll() is None and time.monotonic() < until,
                    "launch_acceptance_unknown_no_retry",
                )
                time.sleep(0.05)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill only our systemd-run client; fixed ExecStopPost still owns
            # service safety. Never resubmit or assume the unit was cancelled.
            process.kill()
            process.wait(timeout=5)
            raise journal.Refusal("launch_completion_unknown_no_retry") from None
        require(process.returncode == 0, "launch_service_failed_or_unknown")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


class Admission:
    """Hold D once, from before load_attempt until the existing opening point."""

    def __init__(self, attempt, lock_factory):
        self.attempt, self.lock_factory = attempt, lock_factory
        self.lease = None
        self.context = None

    def __enter__(self):
        store, snapshot, context = validate(self.attempt)
        ticket = snapshot["ticket"]
        until = time.monotonic() + 15
        while snapshot["state"]["acceptance"] is None:
            require(
                not snapshot["state"]["revoked"] and not snapshot["state"]["admission_closed"],
                "launch_revoked",
            )
            store._fresh(ticket, boot_id=boot(), generation=ticket["owner_generation"])
            require(time.monotonic() < until, "launch_acceptance_missing_no_retry")
            time.sleep(0.05)
            snapshot = store.read()
        worker = observe_worker(ticket, context, self_only=True)
        receipt = snapshot["state"]["acceptance"]
        require(worker == {key: receipt[key] for key in worker}, "launch_worker_not_accepted")
        _transition(store, ticket, "claim", {"worker": receipt})
        self.context = context
        self.lease = self.lock_factory(context["deployment_lock_identity"])
        self.lease.__enter__()
        try:
            store, snapshot, context = validate(self.attempt)
            require(
                observe_worker(ticket, context, self_only=True) == worker, "launch_worker_replaced"
            )
            result = _transition(store, ticket, "enter", {"worker": receipt})
            require(result["action_authorized"], "launch_entry_consumed")
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    @contextlib.contextmanager
    def execution(self, expected, *, on_failure):
        require(
            self.lease is not None and expected == self.context["deployment_lock_identity"],
            "launch_deployment_lock_binding",
        )
        try:
            yield
        except BaseException:
            on_failure()
            raise
        finally:
            self.__exit__(None, None, None)

    def __exit__(self, kind, value, traceback):
        if self.lease is not None:
            lease, self.lease = self.lease, None
            lease.__exit__(kind, value, traceback)


def revoke(attempt):
    store, snapshot, _ = validate(attempt)
    return {"ok": True, **_transition(store, snapshot["ticket"], "revoke", {})}


def settle_unentered(attempt, lock_factory):
    """Only a freshly proven clean new scope may bypass legacy safety settle.

    Recovery ownership, missing/corrupt protocol, or any unexpected attempt file
    follows the existing exact-owned refencing path. E=false alone never grants
    this shortcut. D covers the clean read through irreversible denial under O.
    """
    try:
        store, snapshot, context = validate(attempt)
        with lock_factory(context["deployment_lock_identity"]):
            _, snapshot, _ = validate(attempt)
            require(not snapshot["state"]["execution_possible"], "launch_already_entered")
            directory = ATTEMPTS / attempt
            require(
                {path.name for path in directory.iterdir()}
                == {"plan.json", "baseline.json", "progress.json"}
                and not os.path.lexists(MARKER),
                "launch_clean_scope_not_proven",
            )
            baseline = journal.document(journal._read(directory / "baseline.json", ROOT_UID))
            require("recovery_origin" not in baseline, "launch_recovery_requires_owned_settle")
            progress = journal.document(journal._read(directory / "progress.json", ROOT_UID))
            require(
                progress
                == {
                    "phase": "prepared",
                    "observed_at": progress["observed_at"],
                    "plan_ref": context["plan_ref"],
                },
                "launch_clean_progress_required",
            )
            state = ATTEMPTS.parent / "state"
            require(
                not any(
                    os.path.lexists(state / name)
                    for name in ("pending.json", "active-recovery.json", "publication-pending.json")
                ),
                "launch_clean_state_not_proven",
            )
            return {
                "ok": True,
                "writer_mutation_not_begun": True,
                **_transition(store, snapshot["ticket"], "deny", {}),
            }
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None
