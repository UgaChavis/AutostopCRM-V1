"""One bounded diagnostic, never a replacement for the quality workflow."""

from __future__ import annotations

import base64
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
import traceback
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODE = sys.argv[1]
OUT = ROOT / "output" / "pwsh-completion" / MODE
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
EXPECTED = {
    "tests/test_run_checks_interrupt.py": "a17292935575c2f1c8b9008af6a4630bfa53d0bb",
    "scripts/ci_gate_completion.ps1": "a0da43f8093ac56bb8a97794a7025cd853f58a1c",
    "scripts/run_checks.ps1": "5c6e71e2c6f3e7b0b0eeb7036c8c08f7e2ed6dd9",
}


def write(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def captured(value):
    raw = value.encode() if isinstance(value, str) else value or b""
    return {"text": raw.decode("utf-8", errors="replace"), "base64": base64.b64encode(raw).decode()}


metadata = {
    "mode": MODE,
    "python": sys.version,
    "platform": sys.platform,
    "pwsh": shutil.which("pwsh"),
    "runner_image": os.environ.get("ImageVersion"),
    "runner_os_image": os.environ.get("ImageOS"),
    "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
    "source_blobs": {},
    "safe_environment": {
        key: os.environ.get(key)
        for key in (
            "TERM",
            "POWERSHELL_TELEMETRY_OPTOUT",
            "DOTNET_CLI_TELEMETRY_OPTOUT",
            "DOTNET_SKIP_FIRST_TIME_EXPERIENCE",
        )
    },
    "original_timeout_seconds": 15,
    "original_test_assertions_skips_and_helper_unchanged": True,
    "original_failure_partial_output": "Not retained by the original unittest traceback/artifact; new probe preserves it if reproduced",
    "quality_workflow_rerun": False,
}
for name, expected in EXPECTED.items():
    blob = subprocess.check_output(["git", "rev-parse", f"HEAD:{name}"], cwd=ROOT).decode().strip()
    assert blob == expected, (name, blob)
    metadata["source_blobs"][name] = blob
write("metadata.json", metadata)


class EvidenceResult(unittest.TextTestResult):
    def startTest(self, test):
        self.current_started = time.monotonic()
        self.current = {"test": test.id(), "status": "passed"}
        self.records.append(self.current)
        super().startTest(test)

    def addError(self, test, err):
        error = err[1]
        self.current.update(
            status="error",
            error_type=type(error).__name__,
            traceback="".join(traceback.format_exception(*err)),
        )
        if isinstance(error, subprocess.TimeoutExpired):
            self.current.update(
                command=error.cmd,
                timeout=error.timeout,
                stdout=captured(error.stdout),
                stderr=captured(error.stderr),
            )
        super().addError(test, err)

    def addFailure(self, test, err):
        self.current.update(status="failure", traceback="".join(traceback.format_exception(*err)))
        super().addFailure(test, err)

    def addSkip(self, test, reason):
        self.current.update(status="skipped", reason=reason)
        super().addSkip(test, reason)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.current["status"] = "failed_subtest"
            self.current.setdefault("subtests", []).append(
                {"test": str(subtest), "traceback": "".join(traceback.format_exception(*err))}
            )
        super().addSubTest(test, subtest, err)

    def stopTest(self, test):
        self.current["elapsed_seconds"] = time.monotonic() - self.current_started
        write("original-results-in-progress.json", self.records)
        super().stopTest(test)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []


def original_tests():
    # No pwsh command, version check or Add-Type warmup precedes this test run.
    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_run_checks_interrupt")
    result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
    write(
        "original-results.json",
        {
            "tests": result.testsRun,
            "errors": len(result.errors),
            "failures": len(result.failures),
            "skips": len(result.skipped),
            "records": result.records,
        },
    )
    return not result.wasSuccessful()


def proc_snapshot(pid):
    result = {}
    for name in ("status", "wchan", "syscall"):
        try:
            result[name] = (Path("/proc") / str(pid) / name).read_text(encoding="utf-8")
        except OSError as error:
            result[name] = str(error)
    return result


def phase_sample(index, guard):
    helper = str(ROOT / "scripts" / "ci_gate_completion.ps1").replace("'", "''")
    source = """function Mark([string]$Name) { [Console]::Error.WriteLine('PHASE|' + $Name + '|' + [Diagnostics.Stopwatch]::GetTimestamp()) }
[Console]::Error.WriteLine('CLOCK|' + [Diagnostics.Stopwatch]::Frequency)
Mark 'main'
"""
    if guard:
        source += f". '{helper}'\nMark 'source-loaded'\nInitialize-CiProfileCompletion\nMark 'initialized'\nAssert-CiProfileCompletion -Completed $true -Failed $false\nMark 'finished'\n"
    source += "Write-Host 'HARNESS_PASS'\nMark 'script-end'\n"
    script = OUT / f"phase-{index}.ps1"
    script.write_text(source, encoding="utf-8")
    command = [shutil.which("pwsh"), "-NoProfile", "-File", str(script)]
    started = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        pidfd = os.pidfd_open(process.pid) if hasattr(os, "pidfd_open") else None
    except ProcessLookupError:
        pidfd = None
    selector = selectors.DefaultSelector()
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    eof, arrivals, exit_observed, timeout_snapshot = {}, [], None, None
    assert process.stdout is not None and process.stderr is not None
    for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    try:
        while time.monotonic() - started < 15:
            for key, _ in selector.select(0.05):
                data = os.read(key.fd, 65536)
                elapsed = time.monotonic() - started
                if data:
                    streams[key.data].extend(data)
                    arrivals.append(
                        {"stream": key.data, "elapsed_seconds": elapsed, "bytes": len(data)}
                    )
                else:
                    eof[key.data] = elapsed
                    selector.unregister(key.fileobj)
            if process.poll() is not None and exit_observed is None:
                exit_observed = time.monotonic() - started
            if exit_observed is not None and not selector.get_map():
                break
        timed_out = exit_observed is None or bool(selector.get_map())
        if timed_out:
            timeout_snapshot = {
                "process_exit_code": process.poll(),
                "open_streams": [key.data for key in selector.get_map().values()],
                "proc": proc_snapshot(process.pid),
            }
    finally:
        if process.poll() is None:
            try:
                if pidfd is not None and hasattr(signal, "pidfd_send_signal"):
                    signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        selector.close()
        process.stdout.close()
        process.stderr.close()
        if pidfd is not None:
            os.close(pidfd)
    report = {
        "index": index,
        "guard": guard,
        "command": command,
        "pid": process.pid,
        "timeout_seconds": 15,
        "timed_out": timed_out,
        "exit_code": process.returncode,
        "process_exit_observed_seconds": exit_observed,
        "pipe_eof_seconds": eof,
        "arrivals": arrivals,
        "stdout": captured(bytes(streams["stdout"])),
        "stderr": captured(bytes(streams["stderr"])),
        "timeout_snapshot": timeout_snapshot,
        "elapsed_seconds": time.monotonic() - started,
    }
    write(f"phase-{index}.json", report)
    return report


if MODE == "original":
    outcome = original_tests()
elif MODE == "phases":
    reports = []
    # One cold guarded host followed by two fixed warm controls, no retry-until-pass.
    for number, guard in enumerate((True, False, True)):
        report = phase_sample(number, guard)
        reports.append(report)
        if report["timed_out"]:
            break
    write("phase-results.json", reports)
    outcome = any(report["timed_out"] or report["exit_code"] != 0 for report in reports)
elif MODE == "metadata":
    outcome = False
else:
    raise ValueError(MODE)

if MODE != "metadata":
    try:
        version = subprocess.run(
            [shutil.which("pwsh"), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        write(
            "pwsh-version-after-probe.json",
            {"exit_code": version.returncode, "stdout": version.stdout, "stderr": version.stderr},
        )
    except subprocess.TimeoutExpired as error:
        write(
            "pwsh-version-after-probe.json",
            {
                "timeout": error.timeout,
                "stdout": captured(error.stdout),
                "stderr": captured(error.stderr),
            },
        )
sys.exit(bool(outcome))
