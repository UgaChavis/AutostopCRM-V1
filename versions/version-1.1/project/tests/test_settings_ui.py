from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

from minimal_kanban.integration_runtime import McpRuntimeState
from minimal_kanban.settings_service import ConnectionCheckResult, SettingsService
from minimal_kanban.settings_store import SettingsStore
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore
from minimal_kanban.ui.main_window import MainWindow
from minimal_kanban.ui.settings_window import SettingsWindow


class FakeMcpController:
    def __init__(self) -> None:
        self.state = McpRuntimeState(running=False, runtime_url="", message="MCP сервер не запущен.", error="")

    def start(self, settings) -> McpRuntimeState:
        self.state = McpRuntimeState(
            running=True,
            runtime_url=settings.mcp.local_mcp_url,
            message=f"MCP сервер запущен: {settings.mcp.local_mcp_url}",
            error="",
        )
        return self.state

    def stop(self) -> McpRuntimeState:
        self.state = McpRuntimeState(running=False, runtime_url="", message="MCP сервер остановлен.", error="")
        return self.state


class SettingsWindowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.settings_file = Path(self.temp_dir.name) / "settings.json"
        self.logger = logging.getLogger(f"test.settings.ui.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.board_store = JsonStore(state_file=self.state_file, logger=self.logger)
        self.settings_store = SettingsStore(settings_file=self.settings_file, logger=self.logger)
        self.settings_service = SettingsService(self.settings_store, self.logger)
        self.card_service = CardService(self.board_store, self.logger)
        self.card_service.set_onboarding_seen(True)
        self.controller = FakeMcpController()
        self.window = MainWindow(
            self.card_service,
            "http://127.0.0.1:41731",
            self.settings_service,
            mcp_controller=self.controller,
        )

    def tearDown(self) -> None:
        if getattr(self.window, "_settings_window", None) is not None:
            self.window._settings_window.close()
        self.window.close()
        self.temp_dir.cleanup()

    def test_settings_button_creates_dialog(self) -> None:
        dialog = self.window.build_settings_window()

        self.assertIsNotNone(self.window._settings_window)
        self.assertIs(dialog, self.window._settings_window)
        self.assertEqual(self.window.settings_button.accessibleName(), "Настройки")
        dialog.close()

    def test_connect_gpt_button_opens_settings_and_wizard(self) -> None:
        settings = self.settings_service.load()
        saved = self.settings_service.save(
            settings.__class__.from_dict(
                {
                    **settings.to_dict(),
                    "general": {
                        **settings.general.to_dict(),
                        "integration_enabled": True,
                    },
                    "mcp": {
                        **settings.mcp.to_dict(),
                        "mcp_enabled": True,
                    },
                }
            )
        )
        self.window._on_settings_saved(saved)
        dialog = Mock()

        with (
            patch.object(self.window, "build_settings_window", return_value=dialog) as build_dialog,
            patch("minimal_kanban.ui.main_window.QTimer.singleShot") as single_shot,
        ):
            self.window.open_chatgpt_setup()

        self.assertTrue(self.controller.state.running)
        build_dialog.assert_called_once_with()
        single_shot.assert_called_once_with(0, dialog.open_chatgpt_wizard)
        dialog.exec.assert_called_once_with()

    def test_settings_are_saved_from_ui_and_restored_after_reload(self) -> None:
        dialog = SettingsWindow(
            self.settings_service,
            "http://127.0.0.1:41731",
            mcp_controller=self.controller,
            parent=self.window,
        )
        dialog.integration_enabled_checkbox.setChecked(True)
        dialog.use_local_api_checkbox.setChecked(False)
        dialog.auto_connect_checkbox.setChecked(True)
        dialog.test_mode_checkbox.setChecked(True)
        dialog.local_api_host_input.setText("127.0.0.1")
        dialog.local_api_port_input.setValue(43020)
        dialog.local_api_base_url_input.setText("https://board.example/api")
        dialog.local_api_auth_mode_input.setCurrentIndex(dialog.local_api_auth_mode_input.findData("bearer"))
        dialog.auth_mode_input.setCurrentIndex(dialog.auth_mode_input.findData("bearer"))
        dialog.access_token_input.set_value("agent-ui")
        dialog.openai_api_key_input.set_value("sk-ui")
        dialog.local_api_token_input.set_value("api-ui")
        dialog.mcp_token_input.set_value("mcp-ui")
        dialog.provider_input.setText("openai-compatible")
        dialog.model_input.setText("gpt-settings")
        dialog.base_url_input.setText("https://example.test/v1")
        dialog.organization_input.setText("org-demo")
        dialog.project_input.setText("proj-demo")
        dialog.timeout_input.setValue(55)
        dialog.mcp_enabled_checkbox.setChecked(True)
        dialog.mcp_host_input.setText("127.0.0.1")
        dialog.mcp_port_input.setValue(41850)
        dialog.mcp_path_input.setText("/bridge")
        dialog.mcp_public_base_input.setText("https://public.example")
        dialog.mcp_tunnel_url_input.setText("https://demo.trycloudflare.com")
        dialog.mcp_full_url_input.setText("https://agent.example/tools/mcp")
        dialog.mcp_auth_mode_input.setCurrentIndex(dialog.mcp_auth_mode_input.findData("bearer"))
        dialog._apply()

        loaded = self.settings_service.load()
        self.assertTrue(loaded.general.integration_enabled)
        self.assertFalse(loaded.general.use_local_api)
        self.assertEqual(loaded.local_api.effective_local_api_url, "https://board.example/api")
        self.assertEqual(loaded.auth.auth_mode, "bearer")
        self.assertEqual(loaded.auth.access_token, "agent-ui")
        self.assertEqual(loaded.auth.local_api_bearer_token, "api-ui")
        self.assertEqual(loaded.openai.model, "gpt-settings")
        self.assertEqual(loaded.mcp.local_mcp_url, "http://127.0.0.1:41850/bridge")
        self.assertEqual(loaded.mcp.derived_public_mcp_url, "https://public.example/bridge")
        self.assertEqual(loaded.mcp.effective_mcp_url, "https://agent.example/tools/mcp")

        reloaded_service = SettingsService(SettingsStore(settings_file=self.settings_file, logger=self.logger), self.logger)
        reopened = SettingsWindow(
            reloaded_service,
            "http://127.0.0.1:41731",
            mcp_controller=self.controller,
            parent=self.window,
        )
        self.assertTrue(reopened.integration_enabled_checkbox.isChecked())
        self.assertTrue(reopened.auto_connect_checkbox.isChecked())
        self.assertEqual(reopened.runtime_local_api_url_input.text(), "http://127.0.0.1:43020")
        self.assertEqual(reopened.effective_local_api_url_input.text(), "https://board.example/api")
        self.assertEqual(reopened.mcp_local_url_input.text(), "http://127.0.0.1:41850/bridge")
        self.assertEqual(reopened.mcp_effective_url_input.text(), "https://agent.example/tools/mcp")
        self.assertEqual(reopened.provider_input.text(), "openai-compatible")
        self.assertEqual(reopened.openai_api_key_input.input.echoMode(), QLineEdit.EchoMode.Password)
        reopened.close()
        dialog.close()

    def test_copy_and_show_hide_controls_work(self) -> None:
        dialog = SettingsWindow(
            self.settings_service,
            "http://127.0.0.1:41731",
            mcp_controller=self.controller,
            parent=self.window,
        )
        dialog.mcp_full_url_input.setText("https://agent.example/tools/mcp")
        dialog.mcp_full_url_input.copy_button.click()
        self.assertEqual(QGuiApplication.clipboard().text(), "https://agent.example/tools/mcp")

        dialog.access_token_input.set_value("token-123")
        self.assertEqual(dialog.access_token_input.input.echoMode(), QLineEdit.EchoMode.Password)
        dialog.access_token_input.toggle_button.click()
        self.assertEqual(dialog.access_token_input.input.echoMode(), QLineEdit.EchoMode.Normal)
        dialog.access_token_input.copy_button.click()
        self.assertEqual(QGuiApplication.clipboard().text(), "token-123")
        dialog.close()

    def test_cancel_does_not_persist_changes(self) -> None:
        dialog = SettingsWindow(
            self.settings_service,
            "http://127.0.0.1:41731",
            mcp_controller=self.controller,
            parent=self.window,
        )
        dialog.local_api_base_url_input.setText("https://discard.example")
        dialog.reject()

        loaded = self.settings_service.load()
        self.assertEqual(loaded.local_api.local_api_base_url_override, "")

    def test_reset_restores_defaults_in_form(self) -> None:
        dialog = SettingsWindow(
            self.settings_service,
            "http://127.0.0.1:41731",
            mcp_controller=self.controller,
            parent=self.window,
        )
        dialog.local_api_base_url_input.setText("https://custom.example")
        dialog.auth_mode_input.setCurrentIndex(dialog.auth_mode_input.findData("bearer"))

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            dialog._reset_form()

        self.assertEqual(dialog.local_api_base_url_input.text(), "")
        self.assertEqual(dialog.auth_mode_input.currentData(), "none")
        dialog.close()

    def test_single_connection_button_updates_diagnostics(self) -> None:
        dialog = SettingsWindow(
            self.settings_service,
            "http://127.0.0.1:41731",
            mcp_controller=self.controller,
            parent=self.window,
        )
        with patch.object(
            self.settings_service,
            "test_target",
            return_value=ConnectionCheckResult(
                target="external",
                status="success",
                message="Внешний endpoint доступен по адресу https://agent.example/tools/mcp.",
                checked_at="2026-03-24T10:00:00Z",
            ),
        ):
            dialog.test_external_button.click()

        self.assertEqual(dialog.external_status_input.text(), "Успешно")
        self.assertIn("https://agent.example/tools/mcp", dialog.external_message_label.text())
        dialog.close()

    def test_runtime_buttons_and_connection_card_work(self) -> None:
        dialog = SettingsWindow(
            self.settings_service,
            "http://127.0.0.1:41731",
            mcp_controller=self.controller,
            parent=self.window,
        )
        dialog.mcp_enabled_checkbox.setChecked(True)
        dialog.mcp_host_input.setText("127.0.0.1")
        dialog.mcp_port_input.setValue(41831)
        dialog.mcp_path_input.setText("/mcp")
        dialog.mcp_public_base_input.setText("https://public.example")
        dialog.mcp_tunnel_url_input.setText("https://demo.ngrok-free.app")
        dialog._sync_derived_fields()
        self.assertIn("demo.ngrok-free.app", dialog.mcp_resolved_hosts_input.text())

        dialog.start_mcp_button.click()

        self.assertIn("MCP сервер запущен", dialog.runtime_mcp_status_input.text())
        self.assertEqual(dialog.mcp_public_endpoint_input.text(), "https://public.example/mcp")
        dialog.copy_connection_card_button.click()
        clipboard = QGuiApplication.clipboard().text()
        self.assertIn("MINIMAL KANBAN — КАРТОЧКА ПОДКЛЮЧЕНИЯ GPT / MCP", clipboard)
        self.assertIn("effective_mcp_url = https://public.example/mcp", clipboard)

        dialog.stop_mcp_button.click()
        self.assertIn("остановлен", dialog.runtime_mcp_status_input.text())
        dialog.close()

    def test_connect_to_chatgpt_wizard_shows_warning_and_copies_payload(self) -> None:
        dialog = SettingsWindow(
            self.settings_service,
            "http://127.0.0.1:41731",
            mcp_controller=self.controller,
            parent=self.window,
        )

        dialog.connect_chatgpt_button.click()
        wizard = dialog._connect_dialog

        self.assertIsNotNone(wizard)
        assert wizard is not None
        self.assertIn("Сначала запустите MCP сервер", wizard.warning_label.text())
        self.assertTrue(wizard.mcp_token_input.isHidden())

        wizard.copy_all_button.click()
        clipboard = QGuiApplication.clipboard().text()
        self.assertIn("effective_mcp_url = http://127.0.0.1:41831/mcp", clipboard)
        self.assertIn("local_mcp_url = http://127.0.0.1:41831/mcp", clipboard)
        self.assertIn("effective_local_api_url = http://127.0.0.1:41731", clipboard)
        self.assertIn("- list_columns", clipboard)

        wizard.close()
        dialog.close()

    def test_connect_to_chatgpt_wizard_shows_token_and_runs_preflight(self) -> None:
        dialog = SettingsWindow(
            self.settings_service,
            "http://127.0.0.1:41731",
            mcp_controller=self.controller,
            parent=self.window,
        )
        dialog.mcp_enabled_checkbox.setChecked(True)
        dialog.mcp_public_base_input.setText("https://public.example")
        dialog.mcp_auth_mode_input.setCurrentIndex(dialog.mcp_auth_mode_input.findData("bearer"))
        dialog.mcp_token_input.set_value("mcp-secret")
        dialog.start_mcp_button.click()

        dialog.connect_chatgpt_button.click()
        wizard = dialog._connect_dialog

        self.assertIsNotNone(wizard)
        assert wizard is not None
        self.assertFalse(wizard.mcp_token_input.isHidden())
        self.assertIn("токен", wizard.step_token_label.text().lower())

        with patch.object(
            self.settings_service,
            "test_target",
            side_effect=[
                ConnectionCheckResult(
                    target="mcp",
                    status="success",
                    message="Локальный MCP доступен.",
                    checked_at="2026-03-24T10:00:00Z",
                ),
                ConnectionCheckResult(
                    target="external",
                    status="success",
                    message="Внешний MCP endpoint доступен.",
                    checked_at="2026-03-24T10:00:01Z",
                ),
            ],
        ):
            wizard.check_mcp_button.click()

        self.assertIn("Локальный MCP доступен.", wizard.preflight_status_label.text())
        self.assertIn("Внешний MCP endpoint доступен.", wizard.preflight_status_label.text())
        wizard.mcp_token_input.copy_button.click()
        self.assertEqual(QGuiApplication.clipboard().text(), "mcp-secret")

        wizard.close()
        dialog.close()


if __name__ == "__main__":
    unittest.main()
