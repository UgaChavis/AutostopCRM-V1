"""Bound one Python test command without retries; retain owned-process diagnostics."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import time
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_EXIT = 124
CONTROLLER_ERROR_EXIT = 125


class _Cancelled(BaseException):
    def __init__(self, number):
        self.number = number


def _cancel(number, frame):
    del frame
    signal.signal(number, signal.SIG_IGN)
    raise _Cancelled(number)


def _write(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run(command, *, timeout, label, diagnostics):
    started = time.monotonic()
    deadline = started + timeout
    diagnostics.mkdir(parents=True, exist_ok=True)
    identity = uuid.uuid4().hex
    report_path = diagnostics / f"{identity}.json"
    stack_path = diagnostics / f"{identity}.stack.txt"
    report = {
        "schema": "autostopcrm.runtime-process.v1",
        "controller_pid": os.getpid(),
        "command": command,
        "cwd": str(Path.cwd()),
        "label": label,
        "timeout_seconds": timeout,
        "started_at": datetime.now(UTC).isoformat(),
        "state": "starting",
        "stack_path": str(stack_path),
    }
    _write(report_path, report)
    process = None
    exit_code = CONTROLLER_ERROR_EXIT
    reason = "controller_error"
    try:
        if sys.platform == "win32":
            from tests.runtime_process_windows import WindowsProcess as Process
        elif sys.platform.startswith("linux"):
            from tests.runtime_process_linux import LinuxProcess as Process
        else:
            raise RuntimeError("Runtime process ownership supports Windows and Linux")
        child_command = [
            command[0],
            "-m",
            "tests.runtime_process_child",
            str(deadline),
            str(stack_path),
            *command[1:],
        ]
        process = Process(child_command, cwd=Path.cwd())
        report.update(state="running", child_pid=process.pid)
        _write(report_path, report)
        raw_exit = process.wait(timeout=max(0, deadline - time.monotonic()))
        report["raw_child_exit"] = raw_exit
        exit_code = 128 - raw_exit if raw_exit < 0 else raw_exit
        reason = "completed" if exit_code == 0 else "child_failed"
        if exit_code == 0:
            # Allow normal asynchronous OS teardown only within the same budget.
            grace = min(deadline, time.monotonic() + 2)
            while process.members() and time.monotonic() < grace:
                time.sleep(0.05)
            if time.monotonic() >= deadline:
                exit_code, reason = TIMEOUT_EXIT, "timeout"
            elif process.members():
                exit_code, reason = CONTROLLER_ERROR_EXIT, "owned_processes_left_after_success"
    except subprocess.TimeoutExpired:
        exit_code, reason = TIMEOUT_EXIT, "timeout"
    except _Cancelled as error:
        exit_code, reason = 128 + error.number, "interrupted"
    except KeyboardInterrupt:
        exit_code, reason = 130, "interrupted"
    except Exception:
        report["controller_error"] = traceback.format_exc()
        exit_code, reason = CONTROLLER_ERROR_EXIT, "controller_error"
    finally:
        if process is not None:
            try:
                report["owned_before_cleanup"] = process.members()
                if report["owned_before_cleanup"]:
                    process.kill(exit_code or CONTROLLER_ERROR_EXIT)
                process.wait(timeout=5)
                cleanup_deadline = time.monotonic() + 5
                while process.members() and time.monotonic() < cleanup_deadline:
                    process.kill(exit_code or CONTROLLER_ERROR_EXIT)
                    time.sleep(0.05)
                report["owned_after_cleanup"] = process.members()
                if report["owned_after_cleanup"] and exit_code == 0:
                    exit_code, reason = CONTROLLER_ERROR_EXIT, "cleanup_incomplete"
            except Exception:
                report["cleanup_error"] = traceback.format_exc()
                if exit_code == 0:
                    exit_code, reason = CONTROLLER_ERROR_EXIT, "cleanup_error"
            finally:
                try:
                    process.close()
                except Exception:
                    report["close_error"] = traceback.format_exc()
                    if exit_code == 0:
                        exit_code, reason = CONTROLLER_ERROR_EXIT, "cleanup_error"
        report.update(
            state="finished",
            reason=reason,
            exit_code=exit_code,
            elapsed_seconds=time.monotonic() - started,
            finished_at=datetime.now(UTC).isoformat(),
        )
        _write(report_path, report)
    if exit_code:
        print(
            f"Runtime group {label!r} failed ({reason}, exit {exit_code}); diagnostics: {report_path}",
            file=sys.stderr,
            flush=True,
        )
    return exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--label", default="runtime")
    parser.add_argument("--diagnostics-dir", type=Path, default=ROOT / "output" / "runtime-process")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        parser.error("timeout must be a finite positive number")
    if len(command) < 2:
        parser.error("a Python executable and entrypoint are required after --")
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, _cancel)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _cancel)
    code = run(
        command,
        timeout=args.timeout_seconds,
        label=args.label,
        diagnostics=args.diagnostics_dir.resolve(),
    )
    # Keep native Windows DWORD failures and POSIX signal failures nonzero.
    return code if code <= 0x7FFFFFFF else code - 0x100000000


if __name__ == "__main__":
    raise SystemExit(main())
