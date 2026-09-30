from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.source_path_support import prepend_source_path

prepend_source_path()

from PySide6.QtCore import QCoreApplication, QEvent, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from shiboken6 import isValid  # noqa: E402

from minimal_kanban.app import _shutdown_desktop_runtime  # noqa: E402
from minimal_kanban.publication_runtime import publication_operation_gate  # noqa: E402
from minimal_kanban.settings_service import SettingsService  # noqa: E402
from minimal_kanban.settings_store import SettingsStore  # noqa: E402
from minimal_kanban.ui.main_window import MainWindow  # noqa: E402
from minimal_kanban.ui.settings_window import SettingsWindow  # noqa: E402
from tests.test_settings_ui import FakeMcpController, FakeTunnelController  # noqa: E402


class SettingsRuntimeOperationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.logger = logging.getLogger(f"test.runtime-operation.{self._testMethodName}")
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.service = SettingsService(
            SettingsStore(Path(directory) / "settings.json", self.logger), self.logger
        )
        self.mcp = FakeMcpController()
        self.tunnel = FakeTunnelController()
        self.dialogs = []
        self.threads = []
        self.enterContext(patch("minimal_kanban.ui.settings_window.QMessageBox.warning"))

    def tearDown(self):
        for thread in self.threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        for dialog in self.dialogs:
            if isValid(dialog):
                dialog.close()
        self.app.processEvents()
        self.logger.handlers.clear()

    def dialog(self):
        dialog = SettingsWindow(
            self.service,
            "http://127.0.0.1:41731",
            mcp_controller=self.mcp,
            tunnel_controller=self.tunnel,
        )
        dialog.mcp_enabled_checkbox.setChecked(True)
        self.dialogs.append(dialog)
        return dialog

    def wait_until(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.001)
        self.assertTrue(predicate())

    def wait_operation(self, dialog):
        self.wait_until(lambda: dialog._runtime_operation is None)

    def start(self, dialog):
        dialog._start_mcp_runtime()
        self.wait_operation(dialog)

    def test_all_actions_keep_gui_responsive_and_reject_duplicate_and_probe_requests(self):
        for action, method in (("start", "start"), ("restart", "stop"), ("stop", "stop")):
            with self.subTest(action=action):
                self.mcp, self.tunnel = FakeMcpController(), FakeTunnelController()
                dialog = self.dialog()
                if action != "start":
                    self.start(dialog)
                original = getattr(self.tunnel, method)
                started, release, heartbeat = (
                    threading.Event(),
                    threading.Event(),
                    threading.Event(),
                )
                worker_ids = []

                def block(*args):
                    worker_ids.append(threading.get_ident())
                    started.set()
                    release.wait(3)
                    return original(*args)

                with patch.object(self.tunnel, method, side_effect=block) as call:
                    before = time.monotonic()
                    getattr(dialog, f"_{action}_mcp_runtime")()
                    self.assertLess(time.monotonic() - before, 0.15)
                    self.assertTrue(started.wait(1))
                    task = dialog._runtime_operation
                    self.threads.append(task.thread)
                    try:
                        QTimer.singleShot(0, heartbeat.set)
                        self.app.processEvents()
                        self.assertTrue(heartbeat.is_set())
                        self.assertFalse(dialog.save_button.isEnabled())
                        for duplicate in ("start", "restart", "stop"):
                            getattr(dialog, f"_{duplicate}_mcp_runtime")()
                        self.assertIs(dialog._runtime_operation, task)
                        self.assertFalse(dialog._begin_connection_check(target="mcp"))
                        self.assertEqual(call.call_count, 1)
                        self.assertNotEqual(worker_ids, [threading.get_ident()])
                    finally:
                        release.set()
                    self.wait_operation(dialog)
                self.assertTrue(dialog.save_button.isEnabled())
                self.assertEqual(self.mcp.state.running, action != "stop")
                self.assertEqual(self.tunnel.state.running, action != "stop")
                dialog.close()

    def test_manual_and_automatic_paths_preserve_distinct_process_order(self):
        calls = []
        dialog = self.dialog()
        with (
            patch.object(self.tunnel, "start", wraps=self.tunnel.start) as tunnel_start,
            patch.object(self.tunnel, "stop", wraps=self.tunnel.stop) as tunnel_stop,
            patch.object(self.mcp, "start", wraps=self.mcp.start) as mcp_start,
            patch.object(self.mcp, "stop", wraps=self.mcp.stop) as mcp_stop,
        ):
            recorder = Mock()
            for name, call in (
                ("tunnel_start", tunnel_start),
                ("tunnel_stop", tunnel_stop),
                ("mcp_start", mcp_start),
                ("mcp_stop", mcp_stop),
            ):
                recorder.attach_mock(call, name)
            self.start(dialog)
            calls.append([call[0] for call in recorder.mock_calls])
            recorder.reset_mock()
            dialog._restart_mcp_runtime()
            self.wait_operation(dialog)
            calls.append([call[0] for call in recorder.mock_calls])
            recorder.reset_mock()
            dialog._stop_mcp_runtime()
            self.wait_operation(dialog)
            calls.append([call[0] for call in recorder.mock_calls])
            recorder.reset_mock()
            window = MainWindow(
                "http://localhost",
                "http://localhost",
                self.service,
                mcp_controller=self.mcp,
                tunnel_controller=self.tunnel,
            )
            self.dialogs.append(window)
            window._start_publication_runtime_async(self.service.load(), status_text="Starting")
            self.wait_until(lambda: not window._publication_in_progress)
            calls.append([call[0] for call in recorder.mock_calls])
        self.assertEqual(
            calls,
            [
                ["tunnel_start", "mcp_start"],
                ["tunnel_stop", "mcp_stop", "tunnel_start", "mcp_start"],
                ["tunnel_stop", "mcp_stop"],
                ["mcp_start", "tunnel_start", "mcp_stop", "mcp_start"],
            ],
        )

    def test_closed_or_deleted_dialog_does_not_abandon_process_or_persistence(self):
        for action in ("start", "restart", "stop"):
            for delete in (False, True):
                with self.subTest(action=action, delete=delete):
                    self.mcp, self.tunnel = FakeMcpController(), FakeTunnelController()
                    dialog = self.dialog()
                    if action != "start":
                        self.start(dialog)
                    original = self.tunnel.start if action == "start" else self.tunnel.stop
                    started, release = threading.Event(), threading.Event()

                    def block(*args):
                        started.set()
                        release.wait(3)
                        return original(*args)

                    with patch.object(
                        self.tunnel, "start" if action == "start" else "stop", side_effect=block
                    ):
                        getattr(dialog, f"_{action}_mcp_runtime")()
                        self.assertTrue(started.wait(1))
                        task = dialog._runtime_operation
                        self.threads.append(task.thread)
                        try:
                            dialog.close()
                            if delete:
                                dialog.deleteLater()
                                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                                self.assertFalse(isValid(dialog))
                            other = self.dialog()
                            other._start_mcp_runtime()
                            self.assertIsNone(other._runtime_operation)
                            self.assertIn("Другая операция", other.status_label.text())
                        finally:
                            release.set()
                        task.thread.join(3)
                        self.assertFalse(task.thread.is_alive())
                    self.app.processEvents()
                    self.assertEqual(self.mcp.state.running, action != "stop")
                    saved = self.service.load()
                    self.assertEqual(
                        saved.mcp.tunnel_url,
                        "" if action == "stop" else "https://demo.ngrok-free.app",
                    )
                    self.assertEqual(
                        saved.diagnostics.mcp_status, "warning" if action == "stop" else "success"
                    )
                    reopened = self.dialog()
                    self.assertEqual(bool(reopened.runtime_mcp_url_input.text()), action != "stop")

    def test_automatic_and_manual_operations_share_exclusive_gate(self):
        for automatic_first in (True, False):
            with self.subTest(automatic_first=automatic_first):
                self.mcp, self.tunnel = FakeMcpController(), FakeTunnelController()
                dialog = self.dialog()
                self.service.save(dialog._collect_settings())
                window = MainWindow(
                    "http://localhost",
                    "http://localhost",
                    self.service,
                    mcp_controller=self.mcp,
                    tunnel_controller=self.tunnel,
                )
                self.dialogs.append(window)
                started, release = threading.Event(), threading.Event()
                original = self.mcp.start

                def block(settings):
                    started.set()
                    release.wait(3)
                    return original(settings)

                with patch.object(self.mcp, "start", side_effect=block) as call:
                    if automatic_first:
                        window._start_publication_runtime_async(
                            self.service.load(), status_text="Start"
                        )
                    else:
                        dialog._start_mcp_runtime()
                    self.assertTrue(started.wait(1))
                    try:
                        if automatic_first:
                            dialog._stop_mcp_runtime()
                            self.assertIsNone(dialog._runtime_operation)
                        else:
                            window._start_publication_runtime_async(
                                self.service.load(), status_text="Start"
                            )
                            self.assertFalse(window._publication_in_progress)
                        self.assertEqual(call.call_count, 1)
                    finally:
                        release.set()
                    self.wait_operation(dialog)
                    self.wait_until(lambda: not window._publication_in_progress)

    def test_window_reopened_during_operation_refreshes_state_without_replacing_draft(self):
        dialog = self.dialog()
        started, release = threading.Event(), threading.Event()
        original = self.mcp.start

        def block(settings):
            started.set()
            release.wait(3)
            return original(settings)

        with patch.object(self.mcp, "start", side_effect=block):
            dialog._start_mcp_runtime()
            self.assertTrue(started.wait(1))
            task = dialog._runtime_operation
            self.threads.append(task.thread)
            try:
                dialog.close()
                reopened = self.dialog()
                reopened.local_api_port_input.setValue(44387)
            finally:
                release.set()
            task.thread.join(3)
            self.assertFalse(task.thread.is_alive())
        self.wait_until(lambda: bool(reopened.runtime_mcp_url_input.text()))
        self.assertEqual(reopened.mcp_tunnel_url_input.text(), "https://demo.ngrok-free.app")
        self.assertEqual(reopened.local_api_port_input.value(), 44387)
        self.assertNotEqual(self.service.load().local_api.local_api_port, 44387)
        self.assertEqual(reopened.mcp_status_input.text(), "Успешно")

    def test_validation_and_disabled_restart_retain_stop_before_save_behavior(self):
        dialog = self.dialog()
        self.start(dialog)
        dialog.mcp_public_base_input.setText("invalid-url")
        dialog._restart_mcp_runtime()
        self.wait_operation(dialog)
        self.assertFalse(self.mcp.state.running)
        self.assertFalse(self.tunnel.state.running)
        self.assertIn("http", dialog.status_label.text().lower())
        dialog.mcp_public_base_input.setText("")
        self.start(dialog)
        dialog.mcp_enabled_checkbox.setChecked(False)
        dialog._restart_mcp_runtime()
        self.wait_operation(dialog)
        self.assertFalse(self.mcp.state.running)
        self.assertFalse(self.tunnel.state.running)
        self.assertIn("включите", dialog.status_label.text())

    def test_io_failures_restore_controls_release_gate_and_show_actual_state(self):
        for owner, method, action in (
            ("tunnel", "start", "start"),
            ("mcp", "start", "start"),
            ("tunnel", "stop", "stop"),
            ("mcp", "stop", "stop"),
            ("service", "save", "start"),
            ("service", "apply_test_result", "start"),
        ):
            with self.subTest(owner=owner, method=method):
                self.mcp, self.tunnel = FakeMcpController(), FakeTunnelController()
                dialog = self.dialog()
                if action == "stop":
                    self.start(dialog)
                with patch.object(
                    getattr(self, owner), method, side_effect=RuntimeError("private-token")
                ):
                    getattr(dialog, f"_{action}_mcp_runtime")()
                    self.wait_operation(dialog)
                self.assertTrue(dialog.save_button.isEnabled())
                self.assertFalse(publication_operation_gate(self.mcp).busy)
                self.assertEqual(dialog.runtime_mcp_status_input.text(), self.mcp.state.message)
                self.assertNotIn("private-token", dialog.status_label.text())
                self.assertIn("Не удалось", dialog.status_label.text())
                if action == "stop":
                    self.assertTrue(self.mcp.state.running)
                    self.assertNotIn("остановлены", dialog.status_label.text())

    def test_thread_start_failure_releases_reservation(self):
        dialog = self.dialog()
        with patch(
            "minimal_kanban.ui.settings_runtime_operation.Thread.start",
            side_effect=RuntimeError("private-token"),
        ):
            dialog._start_mcp_runtime()
        self.assertIsNone(dialog._runtime_operation)
        self.assertTrue(dialog.save_button.isEnabled())
        self.assertFalse(publication_operation_gate(self.mcp).busy)
        self.start(dialog)
        self.assertTrue(self.mcp.state.running)

    def test_late_completion_displays_current_state_after_another_stop(self):
        dialog = self.dialog()
        dialog._start_mcp_runtime()
        task = dialog._runtime_operation
        task.thread.join(3)
        self.assertFalse(task.thread.is_alive())
        self.mcp.stop()
        self.wait_operation(dialog)
        self.assertEqual(dialog.runtime_mcp_status_input.text(), self.mcp.state.message)
        self.assertIn("Состояние MCP изменилось", dialog.status_label.text())

    def test_automatic_completion_tolerates_destroyed_settings_and_main_windows(self):
        for destroy_main in (False, True):
            with self.subTest(destroy_main=destroy_main):
                self.mcp, self.tunnel = FakeMcpController(), FakeTunnelController()
                dialog = self.dialog()
                self.service.save(dialog._collect_settings())
                window = MainWindow(
                    "http://localhost",
                    "http://localhost",
                    self.service,
                    mcp_controller=self.mcp,
                    tunnel_controller=self.tunnel,
                )
                self.dialogs.append(window)
                settings = window.build_settings_window()
                settings.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.assertFalse(isValid(settings))
                window._start_publication_runtime_async(self.service.load(), status_text="Start")
                thread = window._publication_thread
                self.threads.append(thread)
                if destroy_main:
                    window.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    self.assertFalse(isValid(window))
                thread.join(3)
                self.assertFalse(thread.is_alive())
                self.app.processEvents()
                self.assertTrue(self.mcp.state.running)
                self.assertEqual(self.service.load().mcp.tunnel_url, "https://demo.ngrok-free.app")

    def test_shutdown_drains_started_operation_then_retains_tunnel_exit_policy(self):
        for stop_tunnel in (False, True):
            with self.subTest(stop_tunnel=stop_tunnel):
                self.mcp, self.tunnel = FakeMcpController(), FakeTunnelController()
                dialog = self.dialog()
                started, release, cleaned = threading.Event(), threading.Event(), threading.Event()
                original = self.mcp.start

                def block(settings):
                    started.set()
                    release.wait(3)
                    return original(settings)

                with patch.object(self.mcp, "start", side_effect=block):
                    dialog._start_mcp_runtime()
                    self.assertTrue(started.wait(1))
                    task = dialog._runtime_operation
                    self.threads.append(task.thread)
                    gate = publication_operation_gate(self.mcp)
                    calls = []
                    self.tunnel.preserve_for_reuse = lambda: calls.append("preserve")
                    stop = self.tunnel.stop

                    def stop_with_record():
                        calls.append("stop")
                        return stop()

                    def shutdown():
                        _shutdown_desktop_runtime(
                            instance_guard=None,
                            instance_guard_entered=False,
                            splash=None,
                            tunnel_controller=self.tunnel,
                            agent_control=None,
                            mcp_controller=self.mcp,
                            api_server=None,
                            logger=None,
                            modules={},
                        )
                        cleaned.set()

                    with (
                        patch.object(self.tunnel, "stop", side_effect=stop_with_record),
                        patch("minimal_kanban.app._stop_tunnel_on_exit", return_value=stop_tunnel),
                    ):
                        thread = threading.Thread(target=shutdown)
                        self.threads.append(thread)
                        thread.start()
                        try:
                            self.assertFalse(cleaned.wait(0.02))
                            self.assertFalse(gate.begin())
                            self.assertEqual(calls, [])
                        finally:
                            release.set()
                        thread.join(3)
                        self.assertTrue(cleaned.is_set())
                    self.assertEqual(calls, ["stop" if stop_tunnel else "preserve"])
                    self.assertFalse(self.mcp.state.running)
                    self.assertFalse(gate.begin())


if __name__ == "__main__":
    unittest.main()
