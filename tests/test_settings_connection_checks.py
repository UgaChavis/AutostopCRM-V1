from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.source_path_support import prepend_source_path

prepend_source_path()

from PySide6.QtCore import QCoreApplication, QEvent, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from shiboken6 import isValid  # noqa: E402

from minimal_kanban.settings_service import ConnectionCheckResult, SettingsService  # noqa: E402
from minimal_kanban.settings_store import SettingsStore  # noqa: E402
from minimal_kanban.ui.settings_connection_check import SettingsConnectionCheck  # noqa: E402
from minimal_kanban.ui.settings_window import SettingsWindow  # noqa: E402
from tests.test_settings_ui import FakeMcpController  # noqa: E402


class SettingsConnectionChecksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.logger = logging.getLogger(f"test.connection-check.{self._testMethodName}")
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.service = SettingsService(
            SettingsStore(Path(self.directory) / "settings.json", self.logger), self.logger
        )
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            if isValid(dialog):
                dialog.close()
        self.app.processEvents()
        self.logger.handlers.clear()

    def dialog(self):
        dialog = SettingsWindow(
            self.service, "http://127.0.0.1:41731", mcp_controller=FakeMcpController()
        )
        self.dialogs.append(dialog)
        return dialog

    def wizard(self, *, public_url="https://public.example", running=True):
        dialog = self.dialog()
        dialog.mcp_enabled_checkbox.setChecked(True)
        dialog.mcp_public_base_input.setText(public_url)
        if running:
            dialog._start_mcp_runtime()
        dialog._open_chatgpt_connect_dialog()
        return dialog, dialog._connect_dialog

    def wait_until(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.001)
        self.assertTrue(predicate())

    def wait_check(self, dialog):
        self.wait_until(lambda: dialog._connection_check is None)

    @staticmethod
    def result(target, status="success"):
        return ConnectionCheckResult(
            target, status, f"Synthetic {target}: {status}", "synthetic-time"
        )

    def test_each_single_probe_is_responsive_exclusive_and_persisted_on_gui_thread(self):
        gui_thread = threading.get_ident()
        for target in ("local_api", "mcp", "external", "openai"):
            with self.subTest(target=target):
                dialog = self.dialog()
                started, release, heartbeat = (
                    threading.Event(),
                    threading.Event(),
                    threading.Event(),
                )
                worker_threads, apply_threads = [], []
                apply_result = self.service.apply_test_result

                def probe(settings, requested):
                    worker_threads.append(threading.get_ident())
                    started.set()
                    if not release.wait(2):
                        raise RuntimeError("test probe timed out")
                    return self.result(requested)

                def apply(*args, **kwargs):
                    apply_threads.append(threading.get_ident())
                    return apply_result(*args, **kwargs)

                with (
                    patch.object(self.service, "test_target", side_effect=probe) as check,
                    patch.object(self.service, "apply_test_result", side_effect=apply),
                ):
                    try:
                        QTimer.singleShot(10, heartbeat.set)
                        self.assertTrue(dialog._run_single_test(target))
                        self.assertTrue(started.wait(1))
                        self.assertFalse(dialog._run_single_test("external"))
                        dialog._test_connections()
                        self.wait_until(heartbeat.is_set)
                        check.assert_called_once()
                        self.assertFalse(dialog.save_button.isEnabled())
                        self.assertTrue(dialog.cancel_button.isEnabled())
                    finally:
                        release.set()
                        self.wait_check(dialog)
                self.assertNotEqual(worker_threads, [gui_thread])
                self.assertEqual(set(apply_threads), {gui_thread})
                self.assertEqual(
                    getattr(self.service.load().diagnostics, f"{target}_status"), "success"
                )
                self.assertTrue(dialog.save_button.isEnabled())
                dialog.close()

    def test_wizard_waits_for_mcp_before_external_and_ignores_repeat_clicks(self):
        dialog, wizard = self.wizard()
        releases = {target: threading.Event() for target in ("mcp", "external")}
        started = {target: threading.Event() for target in releases}
        calls = []
        heartbeat = threading.Event()

        def probe(settings, target):
            calls.append(target)
            started[target].set()
            if not releases[target].wait(2):
                raise RuntimeError("test probe timed out")
            return self.result(target)

        with patch.object(self.service, "test_target", side_effect=probe):
            try:
                QTimer.singleShot(10, heartbeat.set)
                wizard._check_before_connect()
                self.assertTrue(started["mcp"].wait(1))
                wizard._check_before_connect()
                self.wait_until(heartbeat.is_set)
                self.assertEqual(calls, ["mcp"])
                self.assertFalse(wizard.check_mcp_button.isEnabled())
                self.assertTrue(wizard.close_button.isEnabled())
                releases["mcp"].set()
                self.wait_until(started["external"].is_set)
                self.assertEqual(calls, ["mcp", "external"])
                self.assertEqual(self.service.load().diagnostics.mcp_status, "success")
                self.assertFalse(wizard.check_mcp_button.isEnabled())
            finally:
                for event in releases.values():
                    event.set()
                self.wait_check(dialog)
        self.assertTrue(wizard.check_mcp_button.isEnabled())
        self.assertIn("Synthetic mcp", wizard.preflight_status_label.text())
        self.assertIn("Synthetic external", wizard.preflight_status_label.text())

    def test_wizard_preserves_endpoint_selection_and_result_tones(self):
        cases = (
            ("success", "success", "success"),
            ("success", "failed", "error"),
            ("success", "skipped", "warning"),
            ("skipped", "success", "warning"),
            ("skipped", "failed", "error"),
            ("failed", "success", "error"),
            ("failed", "skipped", "error"),
            ("warning", "success", "error"),
        )
        for mcp_status, external_status, tone in cases:
            with self.subTest(mcp=mcp_status, external=external_status):
                dialog, wizard = self.wizard()
                with patch.object(
                    self.service,
                    "test_target",
                    side_effect=[
                        self.result("mcp", mcp_status),
                        self.result("external", external_status),
                    ],
                ) as check:
                    wizard._check_before_connect()
                    self.wait_check(dialog)
                self.assertEqual(
                    [call.args[1] for call in check.call_args_list], ["mcp", "external"]
                )
                self.assertEqual(wizard.preflight_status_label.property("tone"), tone)
                wizard.close()
                dialog.close()
        for url in ("", "http://public.example", "https://127.0.0.1"):
            with self.subTest(url=url):
                dialog, wizard = self.wizard(public_url=url)
                with patch.object(
                    self.service, "test_target", return_value=self.result("mcp")
                ) as check:
                    wizard._check_before_connect()
                    self.wait_check(dialog)
                self.assertEqual(check.call_count, 1)
                self.assertEqual(check.call_args.args[1], "mcp")
                self.assertEqual(wizard.preflight_status_label.property("tone"), "warning")
                wizard.close()
                dialog.close()

    def test_wizard_requires_running_mcp_and_valid_saved_form(self):
        dialog, wizard = self.wizard(running=False)
        with patch.object(self.service, "test_target") as check:
            wizard._check_before_connect()
            check.assert_not_called()
        self.assertIn("Сначала запустите", wizard.preflight_status_label.text())
        dialog._mcp_controller.start(self.service.load())
        wizard.refresh_publication_state(self.service.load(), dialog._mcp_controller.state)
        with (
            patch.object(dialog, "_save_form_settings", return_value=None),
            patch.object(self.service, "test_target") as check,
        ):
            wizard._check_before_connect()
            check.assert_not_called()
        self.assertIsNone(wizard._preflight_request)
        self.assertTrue(wizard.check_mcp_button.isEnabled())
        self.assertIn("не выполнена", wizard.preflight_status_label.text())

    def test_cancelled_or_deleted_wizard_does_not_persist_or_advance_either_stage(self):
        for stage in ("mcp", "external"):
            for action in ("close", "delete", "parent_close", "replace"):
                with self.subTest(stage=stage, action=action):
                    dialog, wizard = self.wizard()
                    started, release = threading.Event(), threading.Event()
                    calls = []

                    def probe(settings, target):
                        calls.append(target)
                        if target == stage:
                            started.set()
                            if not release.wait(2):
                                raise RuntimeError("test probe timed out")
                        return self.result(target)

                    with patch.object(self.service, "test_target", side_effect=probe):
                        try:
                            wizard._check_before_connect()
                            self.wait_until(started.is_set)
                            before = self.service.load()
                            if action == "delete":
                                wizard.deleteLater()
                                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                                dialog.refresh_publication_runtime(
                                    before, dialog._mcp_controller.state
                                )
                            elif action == "parent_close":
                                dialog.reject()
                            elif action == "replace":
                                dialog._open_chatgpt_connect_dialog()
                                dialog._connect_dialog._check_before_connect()
                            else:
                                wizard.reject()
                            self.assertFalse(dialog._run_single_test("local_api"))
                        finally:
                            release.set()
                            self.wait_check(dialog)
                    self.assertEqual(self.service.load(), before)
                    self.assertEqual(calls, ["mcp"] if stage == "mcp" else ["mcp", "external"])
                    self.assertTrue(dialog.save_button.isEnabled())
                    if action != "parent_close":
                        self.assertEqual(
                            dialog.status_label.text(), "Проверка соединения отменена."
                        )
                    dialog.close()

    def test_wizard_discards_foreign_results_and_keeps_changed_configuration(self):
        dialog, wizard = self.wizard()
        snapshots = []

        def probe(settings, target):
            snapshots.append((target, settings.local_api.local_api_port))
            if target == "mcp":
                self.service.update_section("local_api", {"local_api_port": 44356}, persist=True)
            return self.result(target)

        with patch.object(self.service, "test_target", side_effect=probe):
            wizard._check_before_connect()
            wizard._finish_preflight_check(object(), self.result("mcp"))
            self.wait_check(dialog)
        self.assertEqual([item[0] for item in snapshots], ["mcp", "external"])
        self.assertEqual(snapshots[1][1], 44356)
        self.assertEqual(self.service.load().local_api.local_api_port, 44356)
        self.assertIn("изменились", wizard.preflight_status_label.text())
        self.assertEqual(wizard.preflight_status_label.property("tone"), "error")

    def test_probe_and_persistence_failures_restore_controls_without_private_details(self):
        for failure in ("mcp", "external", "persist"):
            with self.subTest(failure=failure):
                dialog, wizard = self.wizard()

                def probe(settings, target):
                    if target == failure:
                        raise RuntimeError("private-token")
                    return self.result(target)

                with patch.object(self.service, "test_target", side_effect=probe):
                    if failure == "persist":
                        with patch.object(
                            self.service, "apply_test_result", side_effect=OSError("private-token")
                        ):
                            wizard._check_before_connect()
                            self.wait_check(dialog)
                    else:
                        wizard._check_before_connect()
                        self.wait_check(dialog)
                self.assertTrue(dialog.save_button.isEnabled())
                self.assertTrue(wizard.check_mcp_button.isEnabled())
                self.assertEqual(wizard.preflight_status_label.property("tone"), "error")
                self.assertNotIn("private-token", wizard.preflight_status_label.text())
                self.assertNotIn("private-token", dialog.status_label.text())
                wizard.close()
                dialog.close()

    def test_worker_start_failure_restores_controls_and_finishes_wizard(self):
        dialog, wizard = self.wizard()
        with patch.object(
            SettingsConnectionCheck, "start", side_effect=RuntimeError("private-token")
        ):
            wizard._check_before_connect()
        self.assertIsNone(dialog._connection_check)
        self.assertIsNone(wizard._preflight_request)
        self.assertTrue(dialog.save_button.isEnabled())
        self.assertTrue(wizard.check_mcp_button.isEnabled())
        self.assertEqual(wizard.preflight_status_label.property("tone"), "error")

    def test_destroyed_settings_window_drops_single_probe_result(self):
        dialog = self.dialog()
        started, release, delivered = threading.Event(), threading.Event(), threading.Event()

        def probe(settings, target):
            started.set()
            release.wait(2)
            return self.result(target)

        with (
            patch.object(self.service, "test_target", side_effect=probe),
            patch.object(self.service, "apply_test_result") as apply,
        ):
            try:
                dialog._run_single_test("local_api")
                self.assertTrue(started.wait(1))
                task = dialog._connection_check
                task.completed.connect(lambda settings, result: delivered.set())
                dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            finally:
                release.set()
                self.wait_until(delivered.is_set)
            apply.assert_not_called()
