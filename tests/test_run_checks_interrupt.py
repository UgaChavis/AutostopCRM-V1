from __future__ import annotations

import errno
import os
import select
import shutil
import signal
import tempfile
import time
import unittest
from pathlib import Path

try:
    import pty
except ImportError:  # pragma: no cover - Windows cannot run the PTY regression.
    pty = None


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "ci_gate_completion.ps1"
GATE = ROOT / "scripts" / "run_checks.ps1"


@unittest.skipUnless(
    os.name == "posix" and pty is not None and shutil.which("pwsh"),
    "requires POSIX PTY and pwsh",
)
class CiGateInterruptTests(unittest.TestCase):
    def test_gate_uses_the_tested_completion_guard(self) -> None:
        source = GATE.read_text(encoding="utf-8")
        self.assertIn('. (Join-Path $PSScriptRoot "ci_gate_completion.ps1")', source)
        self.assertIn("$ciProfileFailed = $true", source)
        self.assertIn(
            "Assert-CiProfileCompletion -Completed ($ciBodyCompleted -and $ciCleanupCompleted) -Failed $ciProfileFailed",
            source,
        )

    def test_interrupt_exits_nonzero_without_pass(self) -> None:
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
            finally:
                if status is None:
                    os.killpg(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                os.close(master_fd)
            return os.waitstatus_to_exitcode(status), output.decode("utf-8", errors="replace")


if __name__ == "__main__":
    unittest.main()
