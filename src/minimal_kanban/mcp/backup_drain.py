"""Reversible pre-stop for the embedded CRM runtime, not host writer fencing."""

from __future__ import annotations

import time


class BackupDrain:
    def __init__(self, mcp, agent, api):
        if mcp is None or agent is None or api is None or mcp.backup_admission is None:
            raise RuntimeError("embedded CRM runtime required")
        self.mcp = mcp
        self.agent = agent
        self.api = api
        self.owner = object()
        self.ready = False
        self.committed = False

    def prepare(self, timeout, cancelled):
        deadline = time.monotonic() + timeout
        mcp_gate = self.mcp.backup_admission.requests
        tools_gate = self.mcp.backup_admission.tools
        agent_gate = self.agent.backup_admission
        api_gate = self.api._request_admission
        try:
            mcp_gate.begin_drain(self.owner)
            agent_gate.begin_drain(self.owner)
            while True:
                if cancelled() or time.monotonic() >= deadline:
                    return False
                if mcp_gate.wait_drained(self.owner, 0.05) and agent_gate.wait_drained(
                    self.owner, 0.05
                ):
                    break
            # Existing requests may still be dispatching their tool. Fence it
            # only after those HTTP requests finished; detached calls then drain.
            tools_gate.begin_drain(self.owner)
            while True:
                if cancelled() or time.monotonic() >= deadline:
                    return False
                if tools_gate.wait_drained(self.owner, 0.05) and self.mcp.backup_workers_idle():
                    break
            api_gate.begin_drain(self.owner)
            while True:
                if cancelled() or time.monotonic() >= deadline:
                    return False
                if api_gate.wait_drained(self.owner, 0.05):
                    break
            if cancelled() or not self.mcp.backup_workers_idle():
                return False
            self.ready = True
            return True
        finally:
            if not self.ready:
                self.abort()

    def commit(self):
        if not self.ready:
            raise RuntimeError("backup drain is not ready")
        self.committed = True
        self.mcp.backup_admission.close_idle_sse()

    def abort(self):
        if self.committed:
            return False
        self.api._request_admission.resume_if_owned(self.owner)
        self.agent.backup_admission.resume_if_owned(self.owner)
        self.mcp.backup_admission.tools.resume_if_owned(self.owner)
        self.mcp.backup_admission.requests.resume_if_owned(self.owner)
        self.ready = False
        return True


def controlled_wait(
    stop_requested, drain, status_path, *, prepare_timeout=30.0, lease_timeout=30.0
):
    """Optional Linux-only pre-stop using host signals and a private receipt.

    USR1 prepares; USR2 commits; HUP aborts only before commit. A committed
    drain is never reopened on timeout or disconnect: the host stop worker must
    own the unknown Docker-stop outcome. Host writers remain a separate concern.
    """
    import json
    import os
    import signal
    import tempfile
    import uuid
    from pathlib import Path

    status_path = Path(status_path)
    directory = status_path.parent
    if directory.is_symlink() or not directory.is_dir() or directory.stat().st_uid != os.geteuid():
        raise RuntimeError("backup status directory is unsafe")
    signals = (signal.SIGUSR1, signal.SIGUSR2, signal.SIGHUP)
    if any(signal.getsignal(sig) != signal.SIG_DFL for sig in signals):
        raise RuntimeError("backup control signal is already in use")
    requested = {"prepare": False, "commit": False, "abort": False}
    generation = uuid.uuid4().hex
    attempt = ""
    # /proc stat comm may contain spaces or parentheses; field 22 is starttime.
    start_ticks = int(Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19])
    old_handlers = {}

    def write_status(state):
        data = {
            "pid": os.getpid(),
            "start_ticks": start_ticks,
            "generation": generation,
            "attempt": attempt,
            "state": state,
        }
        fd, name = tempfile.mkstemp(prefix=".crm-backup-status-", dir=directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as output:
                json.dump(data, output, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, status_path)
            dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def set_flag(name):
        def handler(_signum, _frame):
            requested[name] = True

        return handler

    try:
        for sig, name in zip(signals, ("prepare", "commit", "abort"), strict=True):
            old_handlers[sig] = signal.signal(sig, set_flag(name))
        write_status("idle")
        lease_deadline = 0.0
        while not stop_requested[0]:
            if requested["prepare"]:
                requested["prepare"] = False
                if not drain.ready and not drain.committed:
                    requested["commit"] = False
                    requested["abort"] = False
                    attempt = uuid.uuid4().hex
                    write_status("preparing")
                    ok = drain.prepare(
                        prepare_timeout, lambda: requested["abort"] or stop_requested[0]
                    )
                    if ok and not stop_requested[0]:
                        lease_deadline = time.monotonic() + lease_timeout
                        write_status("ready")
                    else:
                        drain.abort()
                        write_status("aborted")
            if requested["abort"]:
                requested["abort"] = False
                if drain.abort():
                    write_status("aborted")
            if requested["commit"]:
                requested["commit"] = False
                if drain.ready and not drain.committed and time.monotonic() < lease_deadline:
                    drain.commit()
                    write_status("committed")
            if drain.ready and not drain.committed and time.monotonic() >= lease_deadline:
                drain.abort()
                write_status("aborted")
            time.sleep(0.1)
    finally:
        if drain.committed:
            # A committed receipt may already have become visible even if its
            # fsync failed. Never reopen or restore default signal handlers while
            # a Docker stop might be in flight.
            while not stop_requested[0]:
                time.sleep(0.1)
        else:
            drain.abort()
            # A failed fsync may have left a visible READY. Revoke it where
            # possible before returning to normal service.
            try:
                status_path.unlink(missing_ok=True)
            except OSError:
                pass
        for sig in old_handlers:
            # A delayed control signal must never regain its default action and
            # terminate CRM after the supervisor has failed or stopped.
            signal.signal(sig, signal.SIG_IGN)
