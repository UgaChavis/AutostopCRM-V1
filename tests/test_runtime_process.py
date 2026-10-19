from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_PYTHON = sys._base_executable or sys.executable
WORKER = """
import json, os, subprocess, sys, time
from pathlib import Path
root = Path(sys.argv[1])
role = sys.argv[2]
if role == 'grandchild':
    (root / 'grandchild.json').write_text(json.dumps({'pid': os.getpid()}))
else:
    options = {'creationflags': 8} if os.name == 'nt' else {'start_new_session': True}
    child = subprocess.Popen(
        [sys.executable, __file__, str(root), 'grandchild'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options,
    )
    (root / 'worker.json').write_text(json.dumps({'pid': os.getpid(), 'child_pid': child.pid}))
    if role == 'orphan':
        while not (root / 'grandchild.json').exists():
            time.sleep(0.01)
        raise SystemExit(0)
while not (root / 'release').exists():
    time.sleep(0.02)
"""


def alive(pid):
    if os.name != "nt":
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
        except FileNotFoundError:
            return False
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return False
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0x102
    finally:
        kernel.CloseHandle(handle)


class RuntimeProcessTests(unittest.TestCase):
    def test_windows_early_wait_timeout_does_not_reject_a_live_budget(self):
        from tests.runtime_process_windows import WindowsProcess

        process = object.__new__(WindowsProcess)
        process.command = ["controlled-kernel-wait"]
        process.process = None
        process.kernel = Mock()
        process.kernel.WaitForSingleObject.side_effect = [0x102, 0]

        def completed(handle, result):
            result._obj.value = 7
            return True

        process.kernel.GetExitCodeProcess.side_effect = completed
        self.assertEqual(7, process.wait(timeout=10))
        self.assertEqual(2, process.kernel.WaitForSingleObject.call_count)

    def launch(self, directory, arguments, *, timeout=10):
        return subprocess.Popen(
            [
                CONTROLLER_PYTHON,
                "-m",
                "tests.runtime_process",
                "--timeout-seconds",
                str(timeout),
                "--label",
                "synthetic-regression",
                "--diagnostics-dir",
                str(directory / "diagnostics"),
                "--",
                sys.executable,
                *map(str, arguments),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def finish(self, controller, directory, *, timeout=20):
        try:
            output, error = controller.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # The fixture can release itself even if the controller under test
            # is broken. Never leak it or kill processes by name.
            (directory / "release").touch()
            controller.kill()
            output, error = controller.communicate(timeout=10)
            self.fail(f"External controller did not finish: {output}\n{error}")
        reports = list((directory / "diagnostics").glob("*.json"))
        self.assertEqual(1, len(reports), output + error)
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        return controller.returncode, output + error, report

    def wait_json(self, path):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                time.sleep(0.02)
        self.fail(f"Fixture did not publish {path}")

    def fixture(self, directory):
        script = directory / "worker.py"
        script.write_text(WORKER, encoding="utf-8")
        return script

    def test_success_preserves_real_parallel_coverage_unicode_arguments_and_environment(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            script = directory / "проверка с пробелом.py"
            result_path = directory / "result.json"
            script.write_text(
                "import json, os, sys\nfrom pathlib import Path\n"
                "Path(sys.argv[1]).write_text(json.dumps({'argument': sys.argv[2], "
                "'cwd': os.getcwd(), 'coverage': os.environ['COVERAGE_FILE'], "
                "'pythonpath': os.environ['PYTHONPATH']}), encoding='utf-8')\n",
                encoding="utf-8",
            )
            coverage_path = str(directory / ".coverage-probe")
            arguments = [
                "-m",
                "coverage",
                "run",
                "--parallel-mode",
                script,
                result_path,
                "счёт с пробелом",
            ]
            with patch.dict(os.environ, COVERAGE_FILE=coverage_path, PYTHONPATH="existing-marker"):
                controller = self.launch(directory, arguments)
                code, output, report = self.finish(controller, directory)
            self.assertEqual(0, code, output)
            self.assertEqual("completed", report["reason"])
            self.assertEqual([], report["owned_after_cleanup"])
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual("счёт с пробелом", result["argument"])
            self.assertEqual(str(ROOT), result["cwd"])
            self.assertEqual(coverage_path, result["coverage"])
            self.assertEqual("existing-marker", result["pythonpath"])
            self.assertTrue(list(directory.glob(".coverage-probe.*")), output)
            self.assertEqual([sys.executable, *map(str, arguments)], report["command"])

    def test_child_failure_is_propagated_without_a_success_result(self):
        for expected in (7, 139):
            with self.subTest(exit=expected), tempfile.TemporaryDirectory() as name:
                directory = Path(name)
                controller = self.launch(directory, ["-c", f"raise SystemExit({expected})"])
                code, output, report = self.finish(controller, directory)
                self.assertEqual(expected, code, output)
                self.assertEqual(expected, report["exit_code"])
                self.assertEqual("child_failed", report["reason"])
                self.assertEqual([], report["owned_after_cleanup"])

    def test_deadline_stack_survives_entrypoint_return_and_stalled_atexit(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            controller = self.launch(
                directory,
                [
                    "-c",
                    "import atexit,time\n"
                    "def native_cleanup_stand_in(): time.sleep(30)\n"
                    "atexit.register(native_cleanup_stand_in)\n"
                    "print('ENTRYPOINT_RETURNED', flush=True)\n",
                ],
                timeout=3,
            )
            code, output, report = self.finish(controller, directory)
            self.assertEqual(code, 124, output)
            self.assertIn("ENTRYPOINT_RETURNED", output)
            stack = Path(report["stack_path"]).read_text(encoding="utf-8")
            self.assertIn("Timeout", stack)
            self.assertIn("native_cleanup_stand_in", stack)
            self.assertEqual([], report["owned_after_cleanup"])

    def test_timeout_kills_owned_detached_descendants_and_preserves_unrelated_process(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            script = self.fixture(directory)
            sentinel_release = directory / "sentinel-release"
            sentinel = subprocess.Popen(
                [
                    CONTROLLER_PYTHON,
                    "-c",
                    "import sys,time; from pathlib import Path\n"
                    "while not Path(sys.argv[1]).exists(): time.sleep(0.02)",
                    str(sentinel_release),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                controller = self.launch(directory, [script, directory, "wait"], timeout=3)
                code, output, report = self.finish(controller, directory)
                self.assertEqual(124, code, output)
                self.assertEqual("timeout", report["reason"])
                self.assertGreaterEqual(report["elapsed_seconds"], 3)
                self.assertLess(report["elapsed_seconds"], 8)
                self.assertGreaterEqual(len(report["owned_before_cleanup"]), 2)
                self.assertEqual([], report["owned_after_cleanup"])
                for path in ("worker.json", "grandchild.json"):
                    self.assertFalse(alive(self.wait_json(directory / path)["pid"]), path)
                self.assertIsNone(
                    sentinel.poll(), "unrelated child must survive controller cleanup"
                )
                stack = Path(report["stack_path"]).read_text(encoding="utf-8")
                self.assertIn("worker.py", stack)
            finally:
                (directory / "release").touch()
                sentinel_release.touch()
                sentinel.wait(timeout=10)

    def test_success_with_an_orphan_is_rejected_and_the_orphan_is_cleaned(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            script = self.fixture(directory)
            try:
                controller = self.launch(directory, [script, directory, "orphan"])
                code, output, report = self.finish(controller, directory)
                self.assertEqual(125, code, output)
                self.assertEqual("owned_processes_left_after_success", report["reason"])
                self.assertEqual(0, report["raw_child_exit"])
                self.assertTrue(report["owned_before_cleanup"])
                self.assertEqual([], report["owned_after_cleanup"])
                self.assertFalse(alive(self.wait_json(directory / "grandchild.json")["pid"]))
            finally:
                (directory / "release").touch()

    @unittest.skipUnless(sys.platform.startswith("linux"), "POSIX native signal contract")
    def test_native_sigsegv_preserves_exit_139_and_fault_diagnostics(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            controller = self.launch(
                directory,
                [
                    "-c",
                    "import os,resource,signal; resource.setrlimit(resource.RLIMIT_CORE,(0,0)); "
                    "os.kill(os.getpid(),signal.SIGSEGV)",
                ],
            )
            code, output, report = self.finish(controller, directory)
            self.assertEqual(139, code, output)
            self.assertEqual(-11, report["raw_child_exit"])
            self.assertIn("Fatal Python error", Path(report["stack_path"]).read_text())

    @unittest.skipUnless(sys.platform.startswith("linux"), "POSIX SIGCHLD inheritance")
    def test_ignored_inherited_sigchld_cannot_turn_child_failure_into_success(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            # Execute the controller in a fresh interpreter with SIGCHLD ignored,
            # rather than using unsafe preexec_fn in the threaded unit process.
            arguments = [
                "tests.runtime_process",
                "--timeout-seconds",
                "10",
                "--diagnostics-dir",
                str(directory / "ignored-sigchld"),
                "--",
                sys.executable,
                "-c",
                "raise SystemExit(7)",
            ]
            code = (
                "import runpy,signal,sys; signal.signal(signal.SIGCHLD,signal.SIG_IGN); "
                f"sys.argv={arguments!r}; "
                "runpy.run_module('tests.runtime_process',run_name='__main__')"
            )
            result = subprocess.run(
                [CONTROLLER_PYTHON, "-c", code],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=False,
            )
            self.assertEqual(7, result.returncode, result.stdout + result.stderr)
            reports = list((directory / "ignored-sigchld").glob("*.json"))
            self.assertEqual(1, len(reports))
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            self.assertEqual(7, report["raw_child_exit"])
            self.assertEqual("child_failed", report["reason"])

    @unittest.skipUnless(sys.platform.startswith("linux"), "POSIX SIGTERM cancellation")
    def test_sigterm_cancellation_cleans_detached_descendants(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            script = self.fixture(directory)
            controller = self.launch(directory, [script, directory, "wait"])
            try:
                grandchild = self.wait_json(directory / "grandchild.json")
                controller.terminate()
                code, output, report = self.finish(controller, directory)
                self.assertEqual(143, code, output)
                self.assertEqual("interrupted", report["reason"])
                self.assertEqual([], report["owned_after_cleanup"])
                self.assertFalse(alive(grandchild["pid"]))
            finally:
                (directory / "release").touch()

    @unittest.skipUnless(os.name == "nt", "Windows job handle lifetime")
    def test_killing_the_controller_closes_the_job_and_its_owned_descendants(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            script = self.fixture(directory)
            controller = self.launch(directory, [script, directory, "wait"])
            try:
                worker = self.wait_json(directory / "worker.json")
                grandchild = self.wait_json(directory / "grandchild.json")
                controller.kill()
                code, output, report = self.finish(controller, directory)
                self.assertNotEqual(0, code, output)
                self.assertEqual("running", report["state"])
                self.assertFalse(alive(worker["pid"]))
                self.assertFalse(alive(grandchild["pid"]))
            finally:
                (directory / "release").touch()

    @unittest.skipUnless(os.name == "nt", "Windows native wait cancellation")
    def test_cancellation_interrupts_native_wait_before_the_group_deadline(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            ready_file = directory / "child-started"
            payload = (
                f"from pathlib import Path; import time; Path({str(ready_file)!r}).touch(); "
                "time.sleep(30)"
            )
            arguments = [
                "tests.runtime_process",
                "--timeout-seconds",
                "5",
                "--diagnostics-dir",
                str(directory / "diagnostics"),
                "--",
                sys.executable,
                "-c",
                payload,
            ]
            signal_file = directory / "signal-time"
            code = (
                "import _thread,json,runpy,sys,threading,time\nfrom pathlib import Path\n"
                "def interrupt_when_running():\n"
                f"  while not Path({str(ready_file)!r}).exists(): time.sleep(0.01)\n"
                f"  Path({str(signal_file)!r}).write_text(str(time.monotonic()))\n"
                "  _thread.interrupt_main()\n"
                "threading.Thread(target=interrupt_when_running,daemon=True).start()\n"
                f"sys.argv={arguments!r}\n"
                "runpy.run_module('tests.runtime_process',run_name='__main__')\n"
            )
            controller = subprocess.Popen(
                [CONTROLLER_PYTHON, "-c", code],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            code, output, report = self.finish(controller, directory)
            self.assertEqual(130, code, output)
            self.assertEqual("interrupted", report["reason"])
            self.assertEqual([], report["owned_after_cleanup"])
            self.assertLess(time.monotonic() - float(signal_file.read_text()), 1.5)
