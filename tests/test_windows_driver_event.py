"""Synthetic attribution only: no Windows event log or client data."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import collect_windows_driver_event as collect


class WindowsDriverEventTests(unittest.TestCase):
    def setUp(self):
        self.witness = {
            "schema": "autostopcrm.windows-driver-witness.v1",
            "valid_witness": True,
            "errors": [],
            "driver_pid": 1234,
            "driver_creation_filetime": 134376484864274584,
            "intentional_cleanup_begin_ns": None,
            "native_exit": {
                "pid": 1234,
                "creation_filetime": 134376484864274584,
                "exit_code": 3221226505,
                "exit_filetime": 134376485070301696,
                "signaled": True,
            },
        }
        self.event = {
            "process_id": "0x4d2",
            "process_creation_time": hex(134376484864274584),
            "exception_code": "0xc0000409",
            "module_name": "ucrtbase.dll",
            "module_version": "10.0.0.0",
            "fault_offset": "0xabc",
            "event_utc": "2026-10-28T08:08:27.030169+00:00",
        }

    def check(self, items, *, limited=False):
        return collect.attribute(self.witness, {"matches": items, "limited": limited})

    def test_exact_pid_birth_code_and_time(self):
        result = self.check([self.event])
        self.assertEqual(result["status"], "exact_pid_birth_code_and_time")
        self.assertEqual(result["fault_module"], "ucrtbase.dll")
        self.assertEqual(result["exit_code_hex"], "0xc0000409")

    def test_exception_code_is_hex_with_or_without_prefix(self):
        for code in ("c0000409", "C0000409", "0xc0000409", "0XC0000409"):
            with self.subTest(code=code):
                result = self.check([{**self.event, "exception_code": code}])
                self.assertEqual(result["status"], "exact_pid_birth_code_and_time")
        self.witness["native_exit"]["exit_code"] = 0x80000003
        result = self.check([{**self.event, "exception_code": "80000003"}])
        self.assertEqual(result["status"], "exact_pid_birth_code_and_time")

    def test_exception_hex_does_not_change_decimal_pid_or_filetime(self):
        for pid, birth in (
            ("1234", "134376484864274584"),
            ("0x4d2", hex(134376484864274584)),
        ):
            with self.subTest(pid=pid):
                event = {
                    **self.event,
                    "process_id": pid,
                    "process_creation_time": birth,
                    "exception_code": "c0000409",
                }
                self.assertEqual(self.check([event])["status"], "exact_pid_birth_code_and_time")
                self.assertEqual(
                    self.check([{**event, "process_creation_time": "134376484864274585"}])[
                        "status"
                    ],
                    "no_matching_event",
                )

    def test_invalid_or_nonmatching_bare_exception_codes_are_not_attributed(self):
        for code in ("c0000005", "-c0000409", "+c0000409", "100000000", "c0000409z", True, None):
            with self.subTest(code=code):
                self.assertEqual(
                    self.check([{**self.event, "exception_code": code}])["status"],
                    "no_matching_event",
                )

    def test_pid_reuse_is_not_exact(self):
        result = self.check([{**self.event, "process_creation_time": hex(134376484864274585)}])
        self.assertEqual(result["status"], "no_matching_event")

    def test_birth_missing_is_not_exact(self):
        result = self.check([{**self.event, "process_creation_time": ""}])
        self.assertEqual(result["status"], "pid_code_and_time_only_birth_unknown")
        self.assertNotIn("fault_module", result)

    def test_missing_unavailable_limited_or_ambiguous(self):
        self.assertEqual(collect.attribute(self.witness, None)["status"], "event_unavailable")
        self.assertEqual(self.check([])["status"], "no_matching_event")
        self.assertEqual(
            self.check([], limited=True)["status"], "event_query_limit_reached_unknown"
        )
        limited_match = self.check([self.event], limited=True)
        self.assertEqual(limited_match["status"], "event_query_limit_reached_unknown")
        self.assertNotIn("fault_module", limited_match)
        self.assertEqual(
            self.check([self.event, self.event])["status"], "ambiguous_multiple_exact_events"
        )

    def test_wrong_code_pid_or_time_does_not_match(self):
        cases = [
            {**self.event, "exception_code": "0xc0000005"},
            {**self.event, "process_id": "1235"},
            {**self.event, "event_utc": "2026-10-28T08:08:50+00:00"},
        ]
        self.assertEqual(self.check(cases)["status"], "no_matching_event")

    def test_invalid_and_cleanup_witness_never_attributed(self):
        self.witness["valid_witness"] = False
        self.assertEqual(self.check([self.event])["status"], "invalid_witness")
        self.witness["valid_witness"] = True
        self.witness["intentional_cleanup_begin_ns"] = 1
        self.assertEqual(self.check([self.event])["status"], "exit_during_or_near_cleanup_unknown")
        self.witness["intentional_cleanup_begin_ns"] = None
        self.witness["native_exit"] = None
        self.assertEqual(self.check([self.event])["status"], "no_driver_exit_observed")

    def test_only_whitelisted_metadata_is_returned(self):
        event = {
            **self.event,
            "module_name": "C:\\private\\sensitive.dll",
            "fault_offset": "SECRET literal",
            "raw_message": "SECRET literal",
        }
        result = self.check([event])
        serialized = json.dumps(result)
        self.assertEqual(result["status"], "exact_pid_birth_code_and_time")
        self.assertIsNone(result["fault_module"])
        self.assertIsNone(result["fault_offset"])
        self.assertNotIn("SECRET", serialized)
        self.assertNotIn("private", serialized)

    def test_refuses_non_hosted_runner_before_accessing_paths(self):
        env = {
            "GITHUB_ACTIONS": "true",
            "RUNNER_OS": "Windows",
            "RUNNER_ENVIRONMENT": "self-hosted",
        }
        with (
            patch.object(collect.sys, "platform", "win32"),
            patch.dict(collect.os.environ, env, clear=True),
            patch.object(
                collect.sys,
                "argv",
                ["collector", "--witness-dir", "synthetic/missing", "--output", "unused"],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "hosted_synthetic_windows_only"):
                collect.main()

    def test_powershell_filters_mock_event_to_owned_pid(self):
        executable = shutil.which("pwsh")
        if executable is None:
            self.skipTest("PowerShell is not installed")
        fake = r"""
class SyntheticEvent {
  [datetime]$TimeCreated
  [string]$PidText
  [string] ToXml() {
    return '<Event><EventData><Data Name="AppName">node.exe</Data><Data Name="ProcessId">' +
      $this.PidText + '</Data><Data Name="ProcessCreationTime">0x1dd07d702870f98</Data>' +
      '<Data Name="ExceptionCode">0xc0000409</Data><Data Name="ModuleName">ucrtbase.dll</Data>' +
      '<Data Name="ModuleVersion">10.0</Data><Data Name="FaultingOffset">0x123</Data></EventData></Event>'
  }
}
function Get-WinEvent {
  [CmdletBinding()]
  param($FilterHashtable, $MaxEvents)
  $a = [SyntheticEvent]::new(); $a.TimeCreated = [datetime]::Parse('2026-10-28T08:08:27Z'); $a.PidText = '0x4d2'
  $b = [SyntheticEvent]::new(); $b.TimeCreated = $a.TimeCreated; $b.PidText = '4321'
  return @($a,$b)
}
"""
        env = {
            key: os.environ[key]
            for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP")
            if key in os.environ
        }
        env.update(
            AUTOSTOP_DIAG_PID="1234",
            AUTOSTOP_DIAG_BEGIN="134376485020301696",
            AUTOSTOP_DIAG_END="134376485120301696",
        )
        proc = subprocess.run(
            [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                fake + collect.PS_READ,
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=15,
            check=True,
        )
        value = json.loads(proc.stdout.decode("utf-8-sig"))
        self.assertFalse(value["limited"])
        self.assertEqual(len(value["matches"]), 1)
        self.assertEqual(value["matches"][0]["process_id"], "0x4d2")
        self.assertEqual(value["matches"][0]["exception_code"], "0xc0000409")

    def test_event_subprocess_environment_excludes_secrets(self):
        fake = SimpleNamespace(returncode=0, stdout=b'{"limited":false,"matches":[]}')
        env = {"PATH": "synthetic", "SystemRoot": "synthetic", "SECRET": "must-not-be-inherited"}
        with (
            patch.dict(collect.os.environ, env, clear=True),
            patch.object(collect.shutil, "which", return_value="fake-pwsh"),
            patch.object(collect.subprocess, "run", return_value=fake) as run,
        ):
            self.assertEqual(collect.read_events(self.witness), {"limited": False, "matches": []})
        passed = run.call_args.kwargs["env"]
        self.assertNotIn("SECRET", passed)
        self.assertEqual(passed["AUTOSTOP_DIAG_PID"], "1234")

    def test_event_subprocess_failure_is_unknown(self):
        with (
            patch.object(collect.shutil, "which", return_value="fake-pwsh"),
            patch.object(
                collect.subprocess, "run", side_effect=subprocess.TimeoutExpired("fake", 20)
            ),
        ):
            self.assertIsNone(collect.read_events(self.witness))
        with patch.object(collect.shutil, "which", return_value=None):
            self.assertIsNone(collect.read_events(self.witness))

    def test_hosted_entrypoint_checks_source_and_writes_only_safe_result(self):
        with tempfile.TemporaryDirectory(prefix="windows-event-synthetic-") as directory:
            root = Path(directory)
            witness_dir = root / "driver-witness"
            witness_dir.mkdir()
            (witness_dir / "driver-synthetic.json").write_text(
                json.dumps(self.witness), encoding="utf-8"
            )
            source = {
                "source_sha": "a" * 40,
                "runner_os": "Windows",
                "scenario": "windows-profile",
                "production_access": False,
            }
            (root / "source.json").write_text(json.dumps(source), encoding="utf-8")
            output = root / "result.json"
            arguments = ["collector", "--witness-dir", str(witness_dir), "--output", str(output)]
            env = {
                "GITHUB_ACTIONS": "true",
                "RUNNER_OS": "Windows",
                "RUNNER_ENVIRONMENT": "github-hosted",
                "GITHUB_SHA": "a" * 40,
            }
            with (
                patch.object(collect, "sys", SimpleNamespace(platform="win32")),
                patch.object(sys, "argv", arguments),
                patch.dict(collect.os.environ, env, clear=True),
                patch.object(
                    collect, "read_events", return_value={"limited": False, "matches": [self.event]}
                ),
            ):
                self.assertEqual(collect.main(), 0)
                result = json.loads(output.read_text())
                self.assertFalse(result["raw_event_saved"])
                self.assertFalse(result["memory_dump_saved"])
                self.assertEqual(result["witnesses"][0]["status"], "exact_pid_birth_code_and_time")
                source["source_sha"] = "b" * 40
                (root / "source.json").write_text(json.dumps(source), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "source_receipt_mismatch"):
                    collect.main()


if __name__ == "__main__":
    unittest.main()
