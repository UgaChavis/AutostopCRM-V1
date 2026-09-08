from __future__ import annotations

import threading
import webbrowser
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..connection_card import (
    build_board_share_url,
    derive_board_root_url,
    resolve_connector_auth_mode,
    resolve_local_api_bearer_token,
)
from ..desktop_connector_files import write_connector_files
from ..settings_models import is_http_url
from ..settings_service import SettingsService
from ..texts import APP_DISPLAY_NAME, TOOLTIP_SETTINGS

if TYPE_CHECKING:
    from ..integration_runtime import McpRuntimeController
    from ..tunnel_runtime import TunnelRuntimeController
    from .settings_window import SettingsWindow


APP_STYLES = """
QMainWindow, QWidget {
    background-color: #18211b;
    color: #f1efe4;
    font-family: Segoe UI;
    font-size: 13px;
}
QFrame#Panel {
    background-color: #232d27;
    border: 1px solid #586257;
}
QPushButton {
    background-color: #232d27;
    color: #f1efe4;
    border: 1px solid #586257;
    padding: 10px 14px;
}
QPushButton:hover {
    background-color: #2b362f;
}
QLabel[role="label"] {
    color: #c9c8bc;
    font-family: Consolas;
    text-transform: uppercase;
}
QLabel[role="value"] {
    font-family: Consolas;
    font-size: 14px;
}
QFrame#StatusPanel {
    background-color: #202923;
    border: 1px solid #586257;
}
QLabel#StatusLead {
    color: #c9c8bc;
    font-family: Consolas;
    text-transform: uppercase;
    letter-spacing: 0.08em;
}
QLabel#StatusText {
    color: #ece8d8;
    font-size: 14px;
}
"""


class MainWindow(QMainWindow):
    publication_ready = Signal(object, object)
    publication_failed = Signal(str)

    def __init__(
        self,
        local_board_url: str,
        network_board_url: str,
        settings_service: SettingsService,
        mcp_controller: McpRuntimeController | None = None,
        tunnel_controller: TunnelRuntimeController | None = None,
    ) -> None:
        super().__init__()
        self._local_board_url = local_board_url
        self._network_board_url = network_board_url
        self._settings_service = settings_service
        self._mcp_controller = mcp_controller
        self._tunnel_controller = tunnel_controller
        self._settings_window: SettingsWindow | None = None
        self._auto_open_done = False
        self._public_board_url = ""
        self._access_board_url = ""
        self._effective_mcp_url = ""
        self._publication_thread: threading.Thread | None = None
        self._publication_in_progress = False
        self._load_publish_urls()
        self.publication_ready.connect(self._on_publication_ready)
        self.publication_failed.connect(self._on_publication_failed)

        self.setWindowTitle(f"{APP_DISPLAY_NAME} / Канбан-хост")
        self.resize(760, 360)
        self.setStyleSheet(APP_STYLES)

        title = QLabel("КАНБАН / ХОСТ")
        title.setStyleSheet(
            "font-family: Consolas; font-size: 22px; font-weight: 700; letter-spacing: 2px;"
        )
        subtitle = QLabel("Этот компьютер держит доску и раздаёт её в сеть.")
        subtitle.setStyleSheet("color: #c9c8bc;")

        self.status_label = QLabel(
            "Сервер активен. Можно открывать доску и раздавать адрес коллегам."
        )
        self.status_label.setObjectName("StatusText")
        self.status_label.setWordWrap(True)
        status_panel = self._build_status_panel()
        mcp_panel, self.mcp_value_label, self.mcp_open_button, self.mcp_copy_button = (
            self._build_address_panel(
                "MCP URL для ChatGPT",
                self._effective_mcp_url or self._format_public_mcp_placeholder(),
                self.open_mcp_endpoint,
                self.copy_mcp_url,
            )
        )
        self._sync_publish_panel()

        open_button = QPushButton("Открыть доску")
        open_button.clicked.connect(self.open_local_board)

        settings_button = QPushButton("Настройки GPT / MCP")
        settings_button.setAccessibleName("Настройки")
        settings_button.setToolTip(TOOLTIP_SETTINGS)
        settings_button.clicked.connect(self.open_settings)
        self.settings_button = settings_button

        connect_gpt_button = QPushButton("Подключить к ChatGPT")
        connect_gpt_button.setAccessibleName("Подключить к ChatGPT")
        connect_gpt_button.clicked.connect(self.open_chatgpt_setup)
        self.connect_gpt_button = connect_gpt_button

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(10)
        action_row.addWidget(open_button)
        action_row.addWidget(connect_gpt_button)
        action_row.addWidget(settings_button)
        action_row.addStretch(1)

        self.compact_hint_label = QLabel(
            "Откройте доску, дождитесь публичного HTTPS MCP URL и подключите ChatGPT "
            "с режимом авторизации из настроек интеграции."
        )
        self.compact_hint_label.setStyleSheet("color: #b8b7ab; line-height: 1.4;")

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(status_panel)
        layout.addWidget(mcp_panel)
        layout.addLayout(action_row)
        layout.addWidget(self.compact_hint_label)
        layout.addStretch(1)
        self.setCentralWidget(root)

        if self._mcp_controller is not None:
            QTimer.singleShot(0, self._autostart_mcp_if_enabled)
        QTimer.singleShot(700, self._open_once_after_start)

    def _build_status_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("StatusPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        lead = QLabel("Состояние сеанса")
        lead.setObjectName("StatusLead")

        layout.addWidget(lead)
        layout.addWidget(self.status_label)
        return panel

    def _build_address_panel(self, label_text: str, value_text: str, open_callback, copy_callback):
        panel = QFrame()
        panel.setObjectName("Panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        label = QLabel(label_text)
        label.setProperty("role", "label")
        value = QLabel(value_text)
        value.setProperty("role", "value")
        value.setWordWrap(True)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        open_button = QPushButton("Открыть")
        open_button.clicked.connect(open_callback)
        copy_button = QPushButton("Копировать")
        copy_button.clicked.connect(copy_callback)
        buttons.addWidget(open_button)
        buttons.addWidget(copy_button)
        buttons.addStretch(1)

        layout.addWidget(label)
        layout.addWidget(value)
        layout.addLayout(buttons)
        return panel, value, open_button, copy_button

    def _format_public_mcp_placeholder(self) -> str:
        return ""

    def _load_publish_urls(self, settings=None) -> None:
        current_settings = settings or self._settings_service.load()
        self._public_board_url = derive_board_root_url(
            current_settings.local_api.local_api_base_url_override
        )
        token = (
            resolve_local_api_bearer_token(current_settings)
            if current_settings.local_api.local_api_auth_mode == "bearer"
            else ""
        )
        self._access_board_url = build_board_share_url(
            self._public_board_url or self._network_board_url, token
        )
        effective_mcp_url = current_settings.mcp.effective_mcp_url.strip()
        self._effective_mcp_url = (
            effective_mcp_url if effective_mcp_url.startswith("https://") else ""
        )

    def _sync_publish_panel(self) -> None:
        mcp_value = self._effective_mcp_url or self._format_public_mcp_placeholder()
        self.mcp_value_label.setText(mcp_value)
        mcp_enabled = bool(self._effective_mcp_url)
        self.mcp_open_button.setEnabled(mcp_enabled)
        self.mcp_copy_button.setEnabled(mcp_enabled)

    def _copy_text(self, value: str, message: str) -> None:
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(value)
        self._set_status_text(message)

    def _set_status_text(self, message: str) -> None:
        self.status_label.setText(message)

    def _copy_optional_text(
        self, value: str, *, missing_message: str, success_message: str
    ) -> None:
        if not value:
            self._set_status_text(missing_message)
            return
        self._copy_text(value, success_message)

    def _open_url(self, url: str, *, success_message: str) -> None:
        normalized_url = str(url or "").strip()
        if not is_http_url(normalized_url):
            self._set_status_text("Не удалось открыть ссылку: URL должен быть HTTP(S).")
            return
        webbrowser.open(normalized_url)
        self._set_status_text(success_message)

    def _open_optional_url(self, url: str, *, missing_message: str, success_message: str) -> None:
        if not url:
            self._set_status_text(missing_message)
            return
        self._open_url(url, success_message=success_message)

    def copy_local_url(self) -> None:
        self._copy_text(self._local_board_url, "Локальный адрес скопирован в буфер обмена.")

    def copy_network_url(self) -> None:
        self._copy_text(self._network_board_url, "Сетевой адрес скопирован в буфер обмена.")

    def copy_access_url(self) -> None:
        self._copy_optional_text(
            self._access_board_url,
            missing_message="Ссылка доступа пока не настроена.",
            success_message="Ссылка доступа скопирована в буфер обмена.",
        )

    def copy_mcp_url(self) -> None:
        self._copy_optional_text(
            self._effective_mcp_url,
            missing_message="MCP URL для ChatGPT пока не настроен.",
            success_message="MCP URL для ChatGPT скопирован в буфер обмена.",
        )

    def _publish_connector_files(self, settings=None) -> None:
        current_settings = settings or self._settings_service.load()
        mcp_url = (current_settings.mcp.effective_mcp_url or "").strip()
        if not mcp_url.startswith("https://"):
            return
        local_api_url = (current_settings.local_api.effective_local_api_url or "").strip()

        try:
            write_connector_files(
                mcp_url,
                local_api_url,
                auth_mode=resolve_connector_auth_mode(current_settings),
            )
            QGuiApplication.clipboard().setText(mcp_url)
        except OSError:
            return

    def open_local_board(self) -> None:
        self._open_url(
            self._local_board_url, success_message="Доска открыта в браузере на этом компьютере."
        )

    def open_network_board(self) -> None:
        self._open_url(self._network_board_url, success_message="Открыт сетевой адрес доски.")

    def open_access_board(self) -> None:
        self._open_optional_url(
            self._access_board_url,
            missing_message="Ссылка доступа пока не настроена.",
            success_message="Открыта ссылка доступа к доске.",
        )

    def open_mcp_endpoint(self) -> None:
        self._open_optional_url(
            self._effective_mcp_url,
            missing_message="MCP URL для ChatGPT пока не настроен.",
            success_message="Открыт итоговый MCP URL для ChatGPT.",
        )

    def build_settings_window(self) -> SettingsWindow:
        from .settings_window import SettingsWindow

        dialog = SettingsWindow(
            self._settings_service,
            self._local_board_url,
            mcp_controller=self._mcp_controller,
            tunnel_controller=self._tunnel_controller,
            parent=self,
        )
        dialog.settings_saved.connect(self._on_settings_saved)
        self._settings_window = dialog
        return dialog

    def _on_settings_saved(self, settings) -> None:
        self._load_publish_urls(settings)
        self._sync_publish_panel()
        self._publish_connector_files(settings)

    def open_settings(self) -> None:
        dialog = self.build_settings_window()
        dialog.exec()

    def open_chatgpt_setup(self) -> None:
        self._ensure_publication_runtime_for_connect()
        dialog = self.build_settings_window()
        QTimer.singleShot(0, dialog.open_chatgpt_wizard)
        dialog.exec()

    def _open_once_after_start(self) -> None:
        if self._auto_open_done:
            return
        self._auto_open_done = True
        self.open_local_board()

    def _autostart_mcp_if_enabled(self) -> None:
        if self._mcp_controller is None:
            return
        try:
            settings = self._settings_service.load()
            if not settings.general.integration_enabled:
                return
            if not settings.general.auto_connect_on_startup:
                return
            if not settings.mcp.mcp_enabled:
                return
            self._start_publication_runtime_async(
                settings, status_text="Поднимаю MCP и внешний доступ в фоне..."
            )
        except Exception as exc:
            self._show_error(f"Не удалось автоматически запустить MCP сервер.\n\n{exc}")

    def _ensure_publication_runtime_for_connect(self) -> None:
        if self._mcp_controller is None:
            return
        try:
            settings = self._settings_service.load()
            if not settings.general.integration_enabled or not settings.mcp.mcp_enabled:
                return
            self._start_publication_runtime_async(
                settings, status_text="Поднимаю MCP и внешний доступ в фоне..."
            )
        except Exception as exc:
            self.status_label.setText(f"MCP не поднялся автоматически: {exc}")

    def _start_publication_runtime_async(self, settings, *, status_text: str) -> None:
        if self._publication_in_progress:
            return
        self._publication_in_progress = True
        self.status_label.setText(status_text)

        def worker() -> None:
            try:
                state, updated_settings = self._start_publication_runtime_core(settings)
            except Exception as exc:  # pragma: no cover - UI threading path
                self.publication_failed.emit(str(exc))
                return
            self.publication_ready.emit(updated_settings, state)

        self._publication_thread = threading.Thread(
            target=worker,
            name="minimal-kanban-publication-start",
            daemon=True,
        )
        self._publication_thread.start()

    def _start_publication_runtime_core(self, settings):
        if self._mcp_controller is None:
            return None, settings
        state = (
            self._mcp_controller.restart(settings)
            if self._mcp_controller.state.running
            else self._mcp_controller.start(settings)
        )
        if not state.running:
            return state, settings
        needs_public_tunnel = (
            not settings.mcp.full_mcp_url_override and not settings.mcp.public_https_base_url
        )
        if self._tunnel_controller is not None and needs_public_tunnel:
            tunnel_state = self._tunnel_controller.start(settings)
            settings = self._settings_service.update_section(
                "mcp",
                {"tunnel_url": tunnel_state.public_url if tunnel_state.running else ""},
                settings=settings,
                persist=True,
            )
            # Restart MCP after the tunnel is known so public URL and allowed hosts/origins match the live endpoint.
            state = self._mcp_controller.restart(settings)
        return state, settings

    def _on_publication_ready(self, settings, state) -> None:
        self._publication_in_progress = False
        self._load_publish_urls(settings)
        self._sync_publish_panel()
        self._publish_connector_files(settings)
        if self._settings_window is not None:
            self._settings_window.refresh_publication_runtime(settings, state)
        if state is None:
            return
        if state.running:
            if self._effective_mcp_url:
                self.status_label.setText("MCP и внешний доступ готовы. Можно подключать ChatGPT.")
            else:
                self.status_label.setText("Локальный MCP поднят. Публичный адрес ещё догружается.")
        elif state.error:
            self.status_label.setText(f"MCP не поднялся автоматически: {state.error}")

    def _on_publication_failed(self, message: str) -> None:
        self._publication_in_progress = False
        self.status_label.setText(f"MCP не поднялся автоматически: {message}")

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Ошибка", message)
