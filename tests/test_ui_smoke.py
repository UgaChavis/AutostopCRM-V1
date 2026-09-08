from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication, QFrame, QPushButton

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
        button = next(
            item for item in self.window.findChildren(QPushButton) if item.text() == "Открыть доску"
        )
        button.click()
        self.browser_open.assert_called_once_with("http://127.0.0.1:41731")
        self.assertIn("Доска открыта", self.window.status_label.text())

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


if __name__ == "__main__":
    unittest.main()
