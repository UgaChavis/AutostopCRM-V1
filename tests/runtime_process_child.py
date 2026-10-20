"""Run an unchanged Python test entrypoint with a deadline traceback."""

from __future__ import annotations

import faulthandler
import os
import runpy
import sys
import threading
import time
from pathlib import Path


def _deadline_stack(deadline, stack):
    time.sleep(max(0.01, deadline - time.monotonic() - 0.5))
    # dump_traceback_later walks live frames without the GIL and can itself
    # crash during imports/rapid calls (CPython #116008/#131580). This Python
    # thread holds the GIL while dump_traceback walks those frames. A native
    # stall holding the GIL may prevent this diagnostic; the external process
    # controller still owns the hard deadline.
    os.write(stack, b"Timeout: deadline Python traceback (GIL acquired)\n")
    faulthandler.dump_traceback(file=stack, all_threads=True)


def main():
    deadline = float(sys.argv[1])
    stack_path = Path(sys.argv[2])
    arguments = sys.argv[3:]
    # Retain the raw descriptor until OS process exit, including native crashes
    # during interpreter/Qt teardown after the Python entrypoint returns.
    stack = os.open(stack_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    faulthandler.enable(file=stack, all_threads=True)
    threading.Thread(
        target=_deadline_stack,
        args=(deadline, stack),
        name="runtime-deadline-stack",
        daemon=True,
    ).start()
    # Keep the daemon available through atexit/native cleanup. The owning Job
    # or session enforces the deadline; the OS closes the raw fd on process exit.
    if arguments[0] == "-m":
        sys.argv = arguments[1:]
        runpy.run_module(arguments[1], run_name="__main__", alter_sys=True)
    elif arguments[0] == "-c":
        sys.argv = ["-c", *arguments[2:]]
        exec(compile(arguments[1], "<string>", "exec"), {"__name__": "__main__"})
    else:
        sys.argv = arguments
        sys.path[0] = str(Path(arguments[0]).resolve().parent)
        runpy.run_path(arguments[0], run_name="__main__")


if __name__ == "__main__":
    main()
