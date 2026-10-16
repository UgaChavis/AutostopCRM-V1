"""Owned descendants of a dedicated Linux test controller, including orphans."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
from pathlib import Path


def _process_tree():
    processes = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(")", 1)[1].split()
            processes[int(path.parent.name)] = {
                "pid": int(path.parent.name),
                "ppid": int(fields[1]),
                "start_ticks": int(fields[19]),
                "state": fields[0],
            }
        except (OSError, ValueError, IndexError):
            continue
    owned = {os.getpid()}
    while True:
        children = {pid for pid, item in processes.items() if item["ppid"] in owned}
        if children <= owned:
            break
        owned.update(children)
    return {pid: item for pid, item in processes.items() if pid in owned and pid != os.getpid()}


class LinuxProcess:
    def __init__(self, command, *, cwd):
        # This module is only used by a dedicated controller CLI, which creates
        # one test command. Adopt detached/double-forked descendants on exit.
        # An inherited SIG_IGN would auto-reap children and hide their exit code.
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.argtypes = [
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        libc.prctl.restype = ctypes.c_int
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "Could not establish test descendant ownership")
        self.child = subprocess.Popen(command, cwd=cwd, start_new_session=True)
        self.pid = self.child.pid

    def wait(self, timeout):
        return self.child.wait(timeout=max(0, timeout))

    def members(self):
        return [item for item in _process_tree().values() if item["state"] != "Z"]

    def kill(self, exit_code):
        del exit_code
        for pid, observed in _process_tree().items():
            try:
                descriptor = os.pidfd_open(pid)
            except ProcessLookupError:
                continue
            try:
                # Validate ownership again after opening the stable process
                # handle; no process-name matching and no reused-PID kill.
                current = _process_tree().get(pid)
                if current is not None and current["start_ticks"] == observed["start_ticks"]:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except ProcessLookupError:
                pass
            finally:
                os.close(descriptor)

    def close(self):
        # The controller waits for its direct Popen child before reaping the
        # adopted descendants, retaining the real exit/signal result.
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if not pid:
                break
