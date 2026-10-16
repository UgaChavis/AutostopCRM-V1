"""Run an unchanged Python test entrypoint with a deadline traceback."""

from __future__ import annotations

import faulthandler
import os
import runpy
import sys
import time
from pathlib import Path


def main():
    deadline = float(sys.argv[1])
    stack_path = Path(sys.argv[2])
    arguments = sys.argv[3:]
    # Retain the raw descriptor until OS process exit, including native crashes
    # during interpreter/Qt teardown after the Python entrypoint returns.
    stack = os.open(stack_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    faulthandler.enable(file=stack, all_threads=True)
    faulthandler.dump_traceback_later(max(0.01, deadline - time.monotonic() - 0.5), file=stack)
    try:
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
    finally:
        faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
