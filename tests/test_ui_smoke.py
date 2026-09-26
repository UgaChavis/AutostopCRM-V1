from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
import webbrowser
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication, QFrame, QPushButton

from minimal_kanban.logging_setup import close_logger  # noqa: E402
from minimal_kanban.settings_service import SettingsService
from minimal_kanban.settings_store import SettingsStore
from minimal_kanban.texts import APP_DISPLAY_NAME, TOOLTIP_SETTINGS
from minimal_kanban.ui.main_window import MainWindow


class MainWindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        logger = logging.getLogger(self.id())
        close_logger(logger)
        self.addCleanup(close_logger, logger)
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        store = SettingsStore(Path(self.temp_dir.name) / "settings.json", logger)
        self.settings_service = SettingsService(store, logger)
        self.browser_open = self.enterContext(
            patch("minimal_kanban.ui.main_window.webbrowser.open")
        )
        self.scheduled = self.enterContext(patch("minimal_kanban.ui.main_window.QTimer.singleShot"))
        self.connector_files = self.enterContext(
            patch("minimal_kanban.ui.main_window.write_connector_files")
        )
        self.clipboard = Mock()
        self.enterContext(
            patch(
                "minimal_kanban.ui.main_window.QGuiApplication.clipboard",
                return_value=self.clipboard,
            )
        )
        self.window = MainWindow(
            "http://127.0.0.1:41731", "http://192.0.2.1:41731", self.settings_service
        )

    def tearDown(self) -> None:
        if self.window._settings_window is not None:
            self.window._settings_window.close()
        self.window.close()

    def test_host_texts_are_localized_and_settings_are_available(self) -> None:
        self.assertIn(APP_DISPLAY_NAME, self.window.windowTitle())
        self.assertEqual(self.window.settings_button.toolTip(), TOOLTIP_SETTINGS)
        self.assertIn("Сервер активен", self.window.status_label.text())
        self.assertEqual(self.window.mcp_value_label.text(), "")
        self.assertFalse(self.window.mcp_open_button.isEnabled())
        self.assertIn("Откройте доску", self.window.compact_hint_label.text())
        self.assertFalse(hasattr(self.window, "_card_widgets"))
        self.assertFalse(hasattr(self.window, "_service"))

    def test_only_actual_launcher_panels_are_constructed(self) -> None:
        panels = self.window.findChildren(QFrame)
        self.assertFalse(any(panel.objectName() == "SummaryCard" for panel in panels))
        self.assertEqual(sum(panel.objectName() == "Panel" for panel in panels), 1)
        self.assertEqual(
            {button.text() for button in self.window.findChildren(QPushButton)},
            {
                "Открыть доску",
                "Настройки GPT / MCP",
                "Подключить к ChatGPT",
                "Открыть",
                "Копировать",
            },
        )

    def test_startup_opens_browser_once_after_scheduled_delay(self) -> None:
        self.scheduled.assert_called_once_with(700, self.window._open_once_after_start)
        self.browser_open.assert_not_called()
        self.window._open_once_after_start()
        self.window._open_once_after_start()
        self.browser_open.assert_called_once_with("http://127.0.0.1:41731")

    def test_open_board_button_uses_local_http_server(self) -> None:
        self.browser_open.return_value = True
        button = next(
            item for item in self.window.findChildren(QPushButton) if item.text() == "Открыть доску"
        )
        button.click()
        self.browser_open.assert_called_once_with("http://127.0.0.1:41731")
        self.assertIn("Доска открыта", self.window.status_label.text())

    def test_open_board_reports_browser_failure_without_url_details(self) -> None:
        self.window._access_board_url = "https://board.example/?access_token=synthetic-secret"
        for failure in (False, OSError("synthetic-secret"), webbrowser.Error("synthetic-secret")):
            with self.subTest(failure=type(failure).__name__):
                self.browser_open.reset_mock()
                self.browser_open.side_effect = failure if isinstance(failure, Exception) else None
                self.browser_open.return_value = failure if failure is False else True
                self.window.open_access_board()
                self.browser_open.assert_called_once_with(self.window._access_board_url)
                self.assertEqual(
                    self.window.status_label.text(), "Не удалось открыть ссылку в браузере."
                )

    def test_copy_network_address_uses_configured_network_host(self) -> None:
        self.window.copy_network_url()
        self.clipboard.setText.assert_called_once_with("http://192.0.2.1:41731")
        self.assertIn("скопирован", self.window.status_label.text())

    def test_invalid_url_is_not_opened(self) -> None:
        self.window._open_url("file:///private.txt", success_message="opened")
        self.browser_open.assert_not_called()
        self.assertIn("HTTP(S)", self.window.status_label.text())

    def test_real_settings_dialog_uses_host_settings(self) -> None:
        dialog = self.window.build_settings_window()
        self.assertIs(dialog._settings_service, self.settings_service)
        self.assertIs(dialog.parent(), self.window)
        self.assertIs(self.window._settings_window, dialog)

    def test_access_link_updates_when_public_board_url_is_saved(self) -> None:
        saved = self.settings_service.update_section(
            "local_api",
            {
                "local_api_base_url_override": "https://board.example/api",
                "local_api_auth_mode": "bearer",
                "local_api_bearer_token": "synthetic-test-token",
            },
            persist=True,
        )
        self.window._on_settings_saved(saved)
        self.window.copy_access_url()
        self.clipboard.setText.assert_called_once_with(
            "https://board.example?access_token=synthetic-test-token"
        )
        self.window.open_access_board()
        self.browser_open.assert_called_once_with(
            "https://board.example?access_token=synthetic-test-token"
        )

    def test_mcp_url_updates_when_public_mcp_url_is_saved(self) -> None:
        settings = self.settings_service.update_section("general", {"integration_enabled": True})
        saved = self.settings_service.update_section(
            "mcp",
            {"mcp_enabled": True, "public_https_base_url": "https://mcp.example"},
            settings=settings,
            persist=True,
        )
        self.window._on_settings_saved(saved)
        self.assertEqual(self.window.mcp_value_label.text(), "https://mcp.example/mcp")
        self.assertTrue(self.window.mcp_open_button.isEnabled())
        self.assertTrue(self.window.mcp_copy_button.isEnabled())
        self.connector_files.assert_called_once()

    def test_publication_failure_keeps_local_launcher_available(self) -> None:
        self.window._publication_in_progress = True
        self.window._on_publication_failed("synthetic failure")
        self.assertFalse(self.window._publication_in_progress)
        self.assertIn("synthetic failure", self.window.status_label.text())
        self.window.open_local_board()
        self.browser_open.assert_called_once_with("http://127.0.0.1:41731")

    def test_tunnel_completion_preserves_settings_saved_during_start(self) -> None:
        initial = self.settings_service.load()
        mcp_state = Mock(running=True)
        mcp = Mock()
        mcp.state.running = False
        mcp.start.return_value = mcp_state
        mcp.restart.return_value = mcp_state
        self.window._mcp_controller = mcp

        def start_tunnel(_settings):
            self.settings_service.update_section(
                "local_api", {"local_api_port": 44123}, persist=True
            )
            return Mock(running=True, public_url="https://synthetic.example")

        tunnel = Mock()
        tunnel.start.side_effect = start_tunnel
        self.window._tunnel_controller = tunnel
        _, updated = self.window._start_publication_runtime_core(initial)

        self.assertEqual(updated.local_api.local_api_port, 44123)
        self.assertEqual(updated.mcp.tunnel_url, "https://synthetic.example")
        self.assertEqual(self.settings_service.load(), updated)
        mcp.restart.assert_called_once_with(updated)

    def test_queued_publication_result_uses_current_settings_for_display_and_export(self) -> None:
        completed = self.settings_service.update_section(
            "mcp", {"public_https_base_url": "https://old.synthetic.example"}, persist=True
        )
        current = self.settings_service.update_section(
            "mcp", {"public_https_base_url": "https://new.synthetic.example"}, persist=True
        )
        self.window._on_settings_saved(current)
        dialog = self.window.build_settings_window()
        self.connector_files.reset_mock()
        self.window.publication_ready.emit(completed, Mock(running=True))

        self.assertEqual(self.window.mcp_value_label.text(), "https://new.synthetic.example/mcp")
        self.assertEqual(
            self.connector_files.call_args.args[0], "https://new.synthetic.example/mcp"
        )
        self.assertEqual(dialog._runtime_reference, current)
        self.assertIn("изменились", self.window.status_label.text())
        self.assertEqual(self.settings_service.load(), current)

    def test_diagnostic_only_update_does_not_invalidate_publication_result(self) -> None:
        completed = self.settings_service.update_section(
            "mcp", {"public_https_base_url": "https://current.synthetic.example"}, persist=True
        )
        self.settings_service.update_section("diagnostics", {"mcp_status": "success"}, persist=True)
        self.window.publication_ready.emit(completed, Mock(running=True))
        self.assertIn("готовы", self.window.status_label.text())
        self.assertNotIn("изменились", self.window.status_label.text())

    def test_publication_read_failure_keeps_display_and_does_not_export_old_settings(self) -> None:
        completed = self.settings_service.load()
        before = self.window.mcp_value_label.text()
        with patch.object(self.settings_service, "load", side_effect=PermissionError("busy")):
            self.window._on_publication_ready(completed, Mock(running=True))
        self.assertEqual(self.window.mcp_value_label.text(), before)
        self.connector_files.assert_not_called()
        self.assertIn("Не удалось", self.window.status_label.text())


if __name__ == "__main__":
    unittest.main()
