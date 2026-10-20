from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.runtime_skip_contract import NATIVE_LAUNCH_REASON, PRIVATE_COLD_REASON, unexpected_skips
from tests.runtime_suite_runner import record_result


class RuntimeSkipContractTests(unittest.TestCase):
    def test_native_exclusion_is_specific_to_the_other_operating_system(self):
        skipped = [(self, "Windows job handle lifetime")]
        self.assertEqual(unexpected_skips(skipped, "linux"), [])
        self.assertEqual(len(unexpected_skips(skipped, "win32")), 1)
        skipped = [(self, "Requires real Linux flock, AF_UNIX and SO_PEERCRED")]
        self.assertEqual(unexpected_skips(skipped, "win32"), [])
        self.assertEqual(len(unexpected_skips(skipped, "linux")), 1)

    def test_dependency_or_sqlite_gaps_cannot_be_called_native_exclusions(self):
        for platform in ("linux", "win32"):
            for reason in (
                "Playwright is not installed",
                "Playwright Chromium is not installed",
                "Node.js is required",
                "requires supported native/offhost SQLite",
                "requires supported SQLite",
            ):
                with self.subTest(platform=platform, reason=reason):
                    self.assertEqual(len(unexpected_skips([(self, reason)], platform)), 1)

    def test_linux_root_owned_launch_exclusion_is_scoped_to_its_two_native_families(self):
        for family in ("LaunchPermissionTests", "NativeLaunchTests"):
            native = type(family, (unittest.TestCase,), {"test_case": lambda self: None})(
                "test_case"
            )
            self.assertEqual(unexpected_skips([(native, NATIVE_LAUNCH_REASON)], "win32"), [])
            self.assertEqual(len(unexpected_skips([(native, NATIVE_LAUNCH_REASON)], "linux")), 1)
        self.assertEqual(len(unexpected_skips([(self, NATIVE_LAUNCH_REASON)], "win32")), 1)

    def test_unknown_platform_and_new_reasons_remain_closed(self):
        self.assertEqual(len(unexpected_skips([(self, "new unsupported scenario")], "linux")), 1)
        self.assertEqual(
            len(unexpected_skips([(self, "Windows job handle lifetime")], "darwin")), 1
        )

    def test_private_cold_exclusion_requires_exact_module_class_reason_and_windows(self):
        for module, family in (
            ("test_j1_forward_cold_state", "ColdRecoveryStateTests"),
            ("test_j1_forward_cold_consumers", "NativeColdSourceTests"),
        ):
            cold = type(
                family,
                (unittest.TestCase,),
                {"__module__": module, "test_case": lambda self: None},
            )("test_case")
            self.assertEqual(unexpected_skips([(cold, PRIVATE_COLD_REASON)], "win32"), [])
            self.assertEqual(len(unexpected_skips([(cold, PRIVATE_COLD_REASON)], "linux")), 1)
            self.assertEqual(len(unexpected_skips([(cold, "another private reason")], "win32")), 1)
        self.assertEqual(len(unexpected_skips([(self, PRIVATE_COLD_REASON)], "win32")), 1)

    def test_retained_wal_exclusions_require_the_exact_linux_only_storage_case(self):
        from tests.runtime_skip_contract import WINDOWS_LINUX_STORAGE_CASES

        methods = {name: lambda self: None for name in WINDOWS_LINUX_STORAGE_CASES}
        storage = type(
            "ChangeFeedCheckpointLifecycleTests",
            (unittest.TestCase,),
            {"__module__": "test_change_feed_checkpoint_lifecycle", **methods},
        )
        for name, reason in WINDOWS_LINUX_STORAGE_CASES.items():
            test = storage(name)
            self.assertEqual(unexpected_skips([(test, reason)], "win32"), [])
            self.assertEqual(len(unexpected_skips([(test, reason)], "linux")), 1)
            self.assertEqual(len(unexpected_skips([(self, reason)], "win32")), 1)
            self.assertEqual(len(unexpected_skips([(test, "other reason")], "win32")), 1)

    def test_strict_unknown_skip_fails_and_keeps_the_complete_skip_receipt(self):
        result = unittest.TestResult()
        result.addSkip(self, "new unknown exclusion")
        with (
            tempfile.TemporaryDirectory() as name,
            patch.dict(
                "os.environ",
                {"AUTOSTOP_REHEARSAL_STRICT_SKIPS": "1", "AUTOSTOP_REHEARSAL_RECEIPT_DIR": name},
            ),
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            self.assertEqual(record_result(result), 1)
            receipt = json.loads(next(Path(name).glob("runtime-suite-*.json")).read_text())
        self.assertIn("Unexpected rehearsal test skips", errors.getvalue())
        self.assertFalse(receipt["successful"])
        self.assertEqual(
            receipt["unexplained_skips"], [{"test": self.id(), "reason": "new unknown exclusion"}]
        )

    def test_missing_unix_sockets_requires_exact_windows_cases_and_reason(self):
        from tests.runtime_skip_contract import WINDOWS_UNIX_SOCKET_CASES

        reason = "Unix-domain sockets are unavailable"
        methods = {name: lambda self: None for name in WINDOWS_UNIX_SOCKET_CASES}
        for module in ("test_automation_center", "tests.test_automation_center"):
            family = type(
                "AutomationControlClientTests",
                (unittest.TestCase,),
                {"__module__": module, **methods},
            )
            for name in WINDOWS_UNIX_SOCKET_CASES:
                test = family(name)
                self.assertEqual(unexpected_skips([(test, reason)], "win32"), [])
                self.assertEqual(len(unexpected_skips([(test, reason)], "linux")), 1)
                self.assertEqual(len(unexpected_skips([(test, reason + " changed")], "win32")), 1)
        for module, family_name, method in (
            ("unrelated", "AutomationControlClientTests", "test_bounded_protocol_round_trip"),
            ("test_automation_center", "AnotherClass", "test_bounded_protocol_round_trip"),
            ("test_automation_center", "AutomationControlClientTests", "test_future_socket_case"),
        ):
            family = type(
                family_name, (unittest.TestCase,), {"__module__": module, method: lambda self: None}
            )
            self.assertEqual(len(unexpected_skips([(family(method), reason)], "win32")), 1)

    def test_known_native_skip_cannot_turn_an_actual_failure_into_success(self):
        result = unittest.TestResult()
        result.addSkip(self, "Windows job handle lifetime")
        try:
            raise AssertionError("synthetic guard failure")
        except AssertionError:
            import sys

            result.addFailure(self, sys.exc_info())
        with (
            patch("tests.runtime_suite_runner.sys.platform", "linux"),
            patch.dict(
                "os.environ",
                {"AUTOSTOP_REHEARSAL_STRICT_SKIPS": "1", "AUTOSTOP_REHEARSAL_RECEIPT_DIR": ""},
            ),
        ):
            self.assertEqual(record_result(result), 1)

    def test_installed_barrier_file_cases_require_the_exact_windows_exclusion(self):
        from tests.runtime_skip_contract import WINDOWS_INSTALLED_BARRIER_CASES

        reason = "Linux installed-barrier files"
        methods = {name: lambda self: None for name in WINDOWS_INSTALLED_BARRIER_CASES}
        family = type(
            "BarrierInspectionTests",
            (unittest.TestCase,),
            {"__module__": "tests.test_j1_forward_barrier_inspection", **methods},
        )
        for name in WINDOWS_INSTALLED_BARRIER_CASES:
            test = family(name)
            self.assertEqual(unexpected_skips([(test, reason)], "win32"), [])
            self.assertEqual(len(unexpected_skips([(test, reason)], "linux")), 1)
            self.assertEqual(len(unexpected_skips([(test, reason + " changed")], "win32")), 1)
        self.assertEqual(len(unexpected_skips([(self, reason)], "win32")), 1)
