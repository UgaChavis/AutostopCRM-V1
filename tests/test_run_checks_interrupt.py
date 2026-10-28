from __future__ import annotations

import errno
import os
import select
import shutil
import signal
import subprocess
import tempfile
import time
import traceback
import unittest
from pathlib import Path

try:
    import pty
except ImportError:  # pragma: no cover - Windows cannot run the PTY regression.
    pty = None


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "ci_gate_completion.ps1"
GATE = ROOT / "scripts" / "run_checks.ps1"


class CiGateSourceTests(unittest.TestCase):
    def test_gate_uses_the_tested_completion_guard(self) -> None:
        source = GATE.read_text(encoding="utf-8")
        self.assertIn('. (Join-Path $PSScriptRoot "ci_gate_completion.ps1")', source)
        self.assertIn("Initialize-CiProfileCompletion", source)
        self.assertIn("$ciProfileFailed = $true", source)
        self.assertIn(
            "Assert-CiProfileCompletion -Completed ($ciBodyCompleted -and $ciCleanupCompleted) -Failed $ciProfileFailed",
            source,
        )


@unittest.skipUnless(
    os.name == "posix" and pty is not None and shutil.which("pwsh"),
    "requires POSIX PTY and pwsh",
)
class CiGateInterruptTests(unittest.TestCase):
    def test_interrupt_exits_nonzero_without_pass(self) -> None:
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                exit_code, output = self._run_child("interrupt")
                self.assertEqual(130, exit_code, output)
                self.assertIn("Local CI profile interrupted before completion.", output)
                self.assertNotIn("HARNESS_PASS", output)

    def test_failed_child_keeps_original_error(self) -> None:
        exit_code, output = self._run_child("failure")
        self.assertEqual(1, exit_code, output)
        self.assertIn("Step failed with exit code 7.", output)
        self.assertNotIn("Local CI profile interrupted before completion.", output)
        self.assertNotIn("HARNESS_PASS", output)

    def _run_child(self, mode: str) -> tuple[int, str]:
        python = str(Path(os.sys.executable)).replace("'", "''")
        helper = str(HELPER).replace("'", "''")
        source = f"""\
param([string]$Mode)
$ErrorActionPreference = "Stop"
. '{helper}'
Initialize-CiProfileCompletion
$completed = $false
$failed = $false
try {{
    if ($Mode -eq "interrupt") {{
        & '{python}' -c 'import time; print("CHILD_STARTED", flush=True); time.sleep(30)'
    }}
    else {{
        & '{python}' -c 'import sys; sys.exit(7)'
    }}
    if ($LASTEXITCODE -ne 0) {{
        throw "Step failed with exit code $LASTEXITCODE."
    }}
    $completed = $true
}}
catch {{
    $failed = $true
    throw
}}
finally {{
    Assert-CiProfileCompletion -Completed $completed -Failed $failed
}}
Write-Host "HARNESS_PASS"
"""
        with tempfile.TemporaryDirectory(prefix="crm-ci-gate-pty-") as temp_name:
            script = Path(temp_name) / "probe.ps1"
            script.write_text(source, encoding="utf-8")
            assert pty is not None
            pid, master_fd = pty.fork()
            if pid == 0:
                signal.signal(signal.SIGINT, signal.SIG_DFL)
                pwsh = shutil.which("pwsh")
                assert pwsh is not None
                os.execv(pwsh, ["pwsh", "-NoProfile", "-File", str(script), "-Mode", mode])
            output = bytearray()
            status = None
            interrupted = False
            deadline = time.monotonic() + 15
            try:
                while status is None and time.monotonic() < deadline:
                    ready, _, _ = select.select([master_fd], [], [], 0.1)
                    if ready:
                        try:
                            output.extend(os.read(master_fd, 65536))
                        except OSError as exc:
                            if exc.errno != errno.EIO:
                                raise
                    if mode == "interrupt" and not interrupted and b"CHILD_STARTED" in output:
                        os.write(master_fd, b"\x03")
                        interrupted = True
                    done_pid, child_status = os.waitpid(pid, os.WNOHANG)
                    if done_pid:
                        status = child_status
                if status is None:
                    captured = output.decode("utf-8", errors="replace")
                    self.fail(f"PowerShell gate probe did not terminate; output={captured!r}")
                # Process exit may precede reading its final stderr message.
                while select.select([master_fd], [], [], 0)[0]:
                    try:
                        chunk = os.read(master_fd, 65536)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
            finally:
                if status is None:
                    os.killpg(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                os.close(master_fd)
            return os.waitstatus_to_exitcode(status), output.decode("utf-8", errors="replace")


@unittest.skipUnless(shutil.which("pwsh"), "requires pwsh")
class CiCompletionProcessExitTests(unittest.TestCase):
    def run_probe(self, body: str) -> subprocess.CompletedProcess[str]:
        helper = str(HELPER).replace("'", "''")
        source = (
            "param([string]$ReadyPath)\n"
            f". '{helper}'\n"
            "[Console]::Error.WriteLine('CI_PROBE_INITIALIZING')\n"
            "Initialize-CiProfileCompletion\n"
            "[Console]::Error.WriteLine('CI_PROBE_READY')\n"
            "[IO.File]::WriteAllText($ReadyPath, '')\n"
            "$null = [Console]::In.ReadLine()\n"
            f"{body}\n"
        )
        with tempfile.TemporaryDirectory(prefix="crm-ci-gate-exit-") as directory:
            script = Path(directory) / "probe.ps1"
            ready = Path(directory) / "ready"
            script.write_text(source, encoding="utf-8")
            with subprocess.Popen(
                [
                    shutil.which("pwsh"),
                    "-NoProfile",
                    "-File",
                    str(script),
                    "-ReadyPath",
                    str(ready),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            ) as process:
                # Cold Add-Type compilation precedes the behavior under test.
                # Keep startup bounded without consuming the original exit deadline.
                startup_deadline = time.monotonic() + 30
                try:
                    while not ready.exists() and process.poll() is None:
                        remaining = startup_deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(process.args, 30)
                        time.sleep(min(0.05, remaining))
                    stdout, stderr = process.communicate(
                        input="\n" if ready.exists() else None, timeout=15
                    )
                except BaseException as error:
                    process.kill()
                    stdout, stderr = process.communicate()
                    if isinstance(error, subprocess.TimeoutExpired):
                        error.output, error.stderr = stdout, stderr
                        phase = "execution" if ready.exists() else "startup"
                        error.add_note(f"PowerShell probe timeout phase: {phase}")
                        error.add_note(f"PowerShell probe partial stdout: {stdout!r}")
                        error.add_note(f"PowerShell probe partial stderr: {stderr!r}")
                    raise
                self.assertTrue(ready.exists(), f"PowerShell probe failed to initialize: {stderr}")
                return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)

    def test_incomplete_zero_exit_is_rejected_without_finally_command(self) -> None:
        result = self.run_probe('Write-Host "ABANDONED"; exit 0')
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        self.assertIn("Local CI profile interrupted before completion.", result.stderr)

    def test_completed_run_exits_zero_without_interruption_message(self) -> None:
        result = self.run_probe(
            'Assert-CiProfileCompletion -Completed $true -Failed $false; Write-Host "HARNESS_PASS"'
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("HARNESS_PASS", result.stdout)
        self.assertNotIn("interrupted", result.stderr)

    def test_failure_keeps_original_exit_and_error(self) -> None:
        result = self.run_probe(
            'try { throw "ORIGINAL_ERROR" } finally { '
            "Assert-CiProfileCompletion -Completed $false -Failed $true }"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ORIGINAL_ERROR", result.stderr)
        self.assertNotIn("interrupted", result.stderr)

    def test_repeated_runs_in_one_host_reset_the_guard(self) -> None:
        result = self.run_probe(
            "Assert-CiProfileCompletion -Completed $true -Failed $false; "
            "Initialize-CiProfileCompletion; exit 0"
        )
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)

    def test_cancellation_cannot_be_cleared_by_the_pipeline_failure_flag(self) -> None:
        result = self.run_probe(
            "[AutoStop.Ci.CompletionGuard]::Interrupt(); "
            "Assert-CiProfileCompletion -Completed $false -Failed $true; exit 0"
        )
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        self.assertIn("Local CI profile interrupted before completion.", result.stderr)

    def test_timeout_preserves_partial_output_in_the_failure_report(self) -> None:
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            self.run_probe(
                "[Console]::Out.WriteLine('PROBE_STDOUT_BEFORE_TIMEOUT'); "
                "[Console]::Error.WriteLine('PROBE_STDERR_BEFORE_TIMEOUT'); "
                "Start-Sleep -Seconds 30; Write-Host 'HARNESS_PASS'"
            )
        error = caught.exception
        self.assertEqual(15, error.timeout)
        report = "".join(traceback.format_exception(error))
        self.assertIn("PROBE_STDOUT_BEFORE_TIMEOUT", report)
        self.assertIn("PROBE_STDERR_BEFORE_TIMEOUT", report)
        self.assertIn("CI_PROBE_READY", report)
        self.assertNotIn("HARNESS_PASS", report)


if __name__ == "__main__":
    unittest.main()
