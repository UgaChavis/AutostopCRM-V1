from __future__ import annotations

import threading
from typing import TYPE_CHECKING
import webbrowser

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

from ..connection_card import build_board_share_url, derive_board_root_url, resolve_local_api_bearer_token
from ..desktop_connector_files import write_connector_files
from ..services.card_service import CardService, ServiceError
from ..settings_service import SettingsService
from ..texts import (
    API_LABEL_PREFIX,
    APP_DISPLAY_NAME,
    BUTTON_HELP,
    BUTTON_NEW_CARD,
    BUTTON_NEW_COLUMN,
    TOOLTIP_SETTINGS,
    get_column_empty_message,
)
from .widgets import CardWidget, ColumnWidget

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
QFrame#SummaryCard {
    background-color: #202923;
    border: 1px solid #586257;
    min-height: 84px;
}
QLabel[role="summaryLabel"] {
    color: #c9c8bc;
    font-family: Consolas;
    text-transform: uppercase;
    letter-spacing: 0.08em;
}
QLabel[role="summaryValue"] {
    color: #f1efe4;
    font-family: Consolas;
    font-size: 18px;
    font-weight: 700;
}
QLabel[role="summaryHint"] {
    color: #b8b7ab;
    font-size: 12px;
}
"""


class MainWindow(QMainWindow):
    publication_ready = Signal(object, object)
    publication_failed = Signal(str)

    def __init__(
        self,
        local_board_url_or_service,
        network_board_url_or_api_url: str,
        settings_service: SettingsService,
        mcp_controller: McpRuntimeController | None = None,
        tunnel_controller: TunnelRuntimeController | None = None,
    ) -> None:
        super().__init__()
        self._service: CardService | None = None
        if isinstance(local_board_url_or_service, str):
            self._local_board_url = local_board_url_or_service
            self._network_board_url = network_board_url_or_api_url
        else:
            self._service = local_board_url_or_service
            self._local_board_url = network_board_url_or_api_url
            self._network_board_url = network_board_url_or_api_url

        self._settings_service = settings_service
        self._mcp_controller = mcp_controller
        self._tunnel_controller = tunnel_controller
        self._settings_window: SettingsWindow | None = None
        self._auto_open_done = False
        self._render_signature = None
        self._column_signature: tuple[tuple[str, str], ...] = ()
        self._card_widgets: dict[str, CardWidget] = {}
        self.columns: dict[str, ColumnWidget] = {}
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
        title.setStyleSheet("font-family: Consolas; font-size: 22px; font-weight: 700; letter-spacing: 2px;")
        subtitle = QLabel("Этот компьютер держит доску и раздаёт её в сеть.")
        subtitle.setStyleSheet("color: #c9c8bc;")

        self.status_label = QLabel("Сервер активен. Можно открывать доску и раздавать адрес коллегам.")
        self.status_label.setObjectName("StatusText")
        self.status_label.setWordWrap(True)
        self.api_label = QLabel(f"{API_LABEL_PREFIX} {self._local_board_url}")
        self.api_label.hide()
        self.help_button = QPushButton(BUTTON_HELP)
        self.help_button.hide()
        self.new_card_button = QPushButton(BUTTON_NEW_CARD)
        self.new_card_button.hide()
        self.new_column_button = QPushButton(BUTTON_NEW_COLUMN)
        self.new_column_button.hide()
        status_panel = self._build_status_panel()
        summary_panel = self._build_summary_panel()

        local_panel, self.local_value_label, _, _ = self._build_address_panel(
            "Локальный адрес",
            self._local_board_url,
            self.open_local_board,
            self.copy_local_url,
        )
        network_panel, self.network_value_label, _, _ = self._build_address_panel(
            "Сетевой адрес",
            self._network_board_url,
            self.open_network_board,
            self.copy_network_url,
        )
        access_panel, self.access_value_label, self.access_open_button, self.access_copy_button = self._build_address_panel(
            "Ссылка доступа",
            self._access_board_url or self._format_address_placeholder(),
            self.open_access_board,
            self.copy_access_url,
        )
        mcp_panel, self.mcp_value_label, self.mcp_open_button, self.mcp_copy_button = self._build_address_panel(
            "MCP URL для ChatGPT",
            self._effective_mcp_url or self._format_public_mcp_placeholder(),
            self.open_mcp_endpoint,
            self.copy_mcp_url,
        )
        self._sync_publish_panel()

        open_button = QPushButton("Открыть доску")
        open_button.clicked.connect(self.open_local_board)

        copy_button = QPushButton("Копировать сетевой адрес")
        copy_button.clicked.connect(self.copy_network_url)

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
        action_row.addWidget(copy_button)
        action_row.addWidget(connect_gpt_button)
        action_row.addWidget(settings_button)
        action_row.addStretch(1)

        note = QLabel(
            "Как работать:\n"
            "1. Нажми «Открыть доску».\n"
            "2. Для доступа из сети дай коллегам сетевой адрес.\n"
            "3. Для интернета или bearer-защиты копируй «Ссылку доступа».\n"
            "4. Для ChatGPT копируй «MCP URL для ChatGPT» или жми «Подключить к ChatGPT».\n"
            "5. Все действия попадут в общий журнал карточек."
        )
        note.setStyleSheet("color: #c9c8bc; line-height: 1.5;")
        compact_hint = QLabel(
            "Open the board, wait for the public HTTPS MCP URL, then copy it into ChatGPT with No authentication."
        )
        compact_hint.setStyleSheet("color: #b8b7ab; line-height: 1.4;")
        summary_panel.hide()
        local_panel.hide()
        network_panel.hide()
        access_panel.hide()
        note.hide()
        copy_button.hide()
        self._hidden_summary_panel = summary_panel
        self._hidden_local_panel = local_panel
        self._hidden_network_panel = network_panel
        self._hidden_access_panel = access_panel

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(status_panel)
        layout.addWidget(mcp_panel)
        layout.addLayout(action_row)
        layout.addWidget(compact_hint)
        layout.addStretch(1)
        self.setCentralWidget(root)

        if self._service is not None:
            self.refresh_board(force=True)
        if self._mcp_controller is not None:
            QTimer.singleShot(0, self._autostart_mcp_if_enabled)
        if self._service is None:
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

    def _build_summary_panel(self) -> QWidget:
        panel = QWidget()
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        local_card, self.local_state_value_label, self.local_state_hint_label = self._build_summary_card(
            "Локальная доска"
        )
        access_card, self.access_state_value_label, self.access_state_hint_label = self._build_summary_card(
            "Общий доступ"
        )
        mcp_card, self.mcp_state_value_label, self.mcp_state_hint_label = self._build_summary_card("ChatGPT / MCP")
        columns_card, self.columns_total_value_label, self.columns_total_hint_label = self._build_summary_card(
            "Столбцы"
        )
        cards_card, self.cards_total_value_label, self.cards_total_hint_label = self._build_summary_card(
            "Активные карточки"
        )

        for card in (local_card, access_card, mcp_card, columns_card, cards_card):
            layout.addWidget(card, 1)

        self._sync_overview_panel()
        self._sync_board_summary()
        return panel

    def _build_summary_card(self, label_text: str) -> tuple[QFrame, QLabel, QLabel]:
        panel = QFrame()
        panel.setObjectName("SummaryCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        label = QLabel(label_text)
        label.setProperty("role", "summaryLabel")
        value = QLabel("-")
        value.setProperty("role", "summaryValue")
        hint = QLabel("")
        hint.setProperty("role", "summaryHint")
        hint.setWordWrap(True)

        layout.addWidget(label)
        layout.addWidget(value)
        layout.addWidget(hint)
        layout.addStretch(1)
        return panel, value, hint

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

    def _format_address_placeholder(self) -> str:
        return "НЕ НАСТРОЕНА"

    def _format_public_mcp_placeholder(self) -> str:
        return ""

    def _load_publish_urls(self, settings=None) -> None:
        current_settings = settings or self._settings_service.load()
        self._public_board_url = derive_board_root_url(current_settings.local_api.local_api_base_url_override)
        token = resolve_local_api_bearer_token(current_settings) if current_settings.local_api.local_api_auth_mode == "bearer" else ""
        self._access_board_url = build_board_share_url(self._public_board_url or self._network_board_url, token)
        effective_mcp_url = current_settings.mcp.effective_mcp_url.strip()
        self._effective_mcp_url = effective_mcp_url if effective_mcp_url.startswith("https://") else ""

    def _sync_publish_panel(self) -> None:
        if not hasattr(self, "access_value_label"):
            return
        value = self._access_board_url or self._format_address_placeholder()
        self.access_value_label.setText(value)
        enabled = bool(self._access_board_url)
        self.access_open_button.setEnabled(enabled)
        self.access_copy_button.setEnabled(enabled)
        mcp_value = self._effective_mcp_url or self._format_public_mcp_placeholder()
        self.mcp_value_label.setText(mcp_value)
        mcp_enabled = bool(self._effective_mcp_url)
        self.mcp_open_button.setEnabled(mcp_enabled)
        self.mcp_copy_button.setEnabled(mcp_enabled)
        self._sync_overview_panel()

    def _sync_overview_panel(self) -> None:
        if not hasattr(self, "local_state_value_label"):
            return

        self.local_state_value_label.setText("АКТИВЕН")
        self.local_state_hint_label.setText("Локальное окно и API на этом компьютере")

        access_ready = bool(self._access_board_url)
        self.access_state_value_label.setText("ГОТОВО" if access_ready else "ЛОКАЛЬНО")
        self.access_state_hint_label.setText(
            "Можно делиться ссылкой с другими пользователями"
            if access_ready
            else "Внешний адрес доски пока не задан"
        )

        public_mcp_ready = self._effective_mcp_url.startswith("https://")
        mcp_state = "ГОТОВО" if public_mcp_ready else ("ЛОКАЛЬНО" if self._effective_mcp_url else "ОЖИДАНИЕ")
        self.mcp_state_value_label.setText(mcp_state)
        self.mcp_state_hint_label.setText(
            "MCP URL готов для подключения ChatGPT"
            if public_mcp_ready
            else "Для ChatGPT нужен внешний HTTPS MCP URL"
        )

    def _sync_board_summary(self, columns: list[dict] | None = None, cards: list[dict] | None = None) -> None:
        if not hasattr(self, "columns_total_value_label"):
            return

        if columns is None:
            columns = list(self.columns.values()) if self.columns else None
        if cards is None:
            cards = list(self._card_widgets.values()) if self._card_widgets else None

        if columns is None:
            self.columns_total_value_label.setText("-")
            self.columns_total_hint_label.setText("Сводка появится после загрузки доски")
        else:
            self.columns_total_value_label.setText(str(len(columns)))
            self.columns_total_hint_label.setText("Столбцов в текущей доске")

        if cards is None:
            self.cards_total_value_label.setText("-")
            self.cards_total_hint_label.setText("Нет данных о карточках")
        else:
            self.cards_total_value_label.setText(str(len(cards)))
            self.cards_total_hint_label.setText("Карточек в работе сейчас")

    def _copy_text(self, value: str, message: str) -> None:
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(value)
        self._set_status_text(message)

    def _set_status_text(self, message: str) -> None:
        self.status_label.setText(message)

    def _copy_optional_text(self, value: str, *, missing_message: str, success_message: str) -> None:
        if not value:
            self._set_status_text(missing_message)
            return
        self._copy_text(value, success_message)

    def _open_url(self, url: str, *, success_message: str) -> None:
        webbrowser.open(url)
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
                auth_mode=current_settings.mcp.mcp_auth_mode,
            )
            QGuiApplication.clipboard().setText(mcp_url)
        except OSError:
            return

    def open_local_board(self) -> None:
        self._open_url(self._local_board_url, success_message="Доска открыта в браузере на этом компьютере.")

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
            self._start_publication_runtime_async(settings, status_text="Поднимаю MCP и внешний доступ в фоне...")
        except Exception as exc:
            self._show_error(f"Не удалось автоматически запустить MCP сервер.\n\n{exc}")

    def _ensure_publication_runtime_for_connect(self) -> None:
        if self._mcp_controller is None:
            return
        try:
            settings = self._settings_service.load()
            if not settings.general.integration_enabled or not settings.mcp.mcp_enabled:
                return
            state = self._start_publication_runtime(settings)
            if state.running:
                self.status_label.setText("MCP сервер поднят. Можно сразу переходить к подключению ChatGPT.")
            elif state.error:
                self.status_label.setText(f"MCP не поднялся автоматически: {state.error}")
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
        state = self._mcp_controller.restart(settings) if self._mcp_controller.state.running else self._mcp_controller.start(settings)
        if not state.running:
            return state, settings
        needs_public_tunnel = not settings.mcp.full_mcp_url_override and not settings.mcp.public_https_base_url
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

    def _start_publication_runtime(self, settings):
        state, updated_settings = self._start_publication_runtime_core(settings)
        self._load_publish_urls(updated_settings)
        self._sync_publish_panel()
        self._publish_connector_files(updated_settings)
        return state

    def _on_publication_ready(self, settings, state) -> None:
        self._publication_in_progress = False
        self._load_publish_urls(settings)
        self._sync_publish_panel()
        self._publish_connector_files(settings)
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

    def _load_cards(self) -> list[dict] | None:
        if self._service is None:
            return []
        try:
            return self._service.get_cards({"include_archived": False})["cards"]
        except ServiceError as exc:
            self._show_error(exc.message)
        return None

    def _load_columns(self) -> list[dict] | None:
        if self._service is None:
            return []
        try:
            return self._service.list_columns()["columns"]
        except ServiceError as exc:
            self._show_error(exc.message)
        return None

    def refresh_board(
        self,
        *,
        force: bool = False,
        cards: list[dict] | None = None,
        columns: list[dict] | None = None,
    ) -> None:
        if self._service is None:
            return
        if columns is None:
            columns = self._load_columns()
        if columns is None:
            return
        if cards is None:
            cards = self._load_cards()
        if cards is None:
            return

        self._sync_board_summary(columns=columns, cards=cards)

        column_signature = tuple((column["id"], column["label"]) for column in columns)
        if force or column_signature != self._column_signature:
            self._sync_columns(columns)

        signature = tuple(
            [column_signature]
            + [
                (
                    card["id"],
                    card["title"],
                    card["description"],
                    card["column"],
                    card["archived"],
                    card["deadline_timestamp"],
                    card["updated_at"],
                )
                for card in cards
            ]
        )
        if not force and signature == self._render_signature:
            return
        self._render_signature = signature

        grouped = {column["id"]: [] for column in columns}
        new_widgets: dict[str, CardWidget] = {}
        for card in cards:
            if card["column"] not in grouped and columns:
                card["column"] = columns[0]["id"]
            widget = self._card_widgets.get(card["id"])
            if widget is None:
                widget = CardWidget(card)
            else:
                widget.update_card(card)
            grouped[card["column"]].append(widget)
            new_widgets[card["id"]] = widget

        self._card_widgets = new_widgets
        for column in columns:
            self.columns[column["id"]].set_cards(grouped[column["id"]])

    def _sync_columns(self, columns: list[dict]) -> None:
        self.columns = {}
        self._card_widgets = {}
        for column in columns:
            widget = ColumnWidget(
                column["id"],
                column["label"],
                get_column_empty_message(column["id"], column["label"]),
            )
            self.columns[column["id"]] = widget
        self._column_signature = tuple((column["id"], column["label"]) for column in columns)
