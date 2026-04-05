from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import get_log_file, get_mcp_startup_log_file
from ..connection_card import (
    CHATGPT_HOME_URL,
    MCP_TOOL_NAMES,
    OPENAI_APPS_CONNECT_GUIDE_URL,
    OPENAI_MCP_CONNECTORS_GUIDE_URL,
    build_chatgpt_connect_payload,
    build_chatgpt_connector_payload,
    build_connection_card,
    build_responses_api_payload,
    build_settings_export,
    get_mcp_setup_doc_path,
    resolve_connector_auth_mode,
    resolve_mcp_bearer_token,
)
from ..integration_runtime import McpRuntimeController
from ..models import utc_now_iso
from ..settings_models import (
    AuthSettings,
    DiagnosticsSettings,
    GeneralSettings,
    IntegrationSettings,
    LocalApiSettings,
    McpSettings,
    OpenAISettings,
    is_external_http_url,
    normalize_string_list,
)
from ..settings_service import ConnectionCheckResult, ConnectionTestSummary, SettingsService, SettingsValidationError
from ..texts import BUTTON_APPLY, BUTTON_CANCEL, BUTTON_COPY, BUTTON_HIDE_SECRET, BUTTON_RESET_DEFAULTS, BUTTON_SAVE, BUTTON_SHOW_SECRET
from ..tunnel_runtime import TunnelRuntimeController


WINDOW_TITLE = "Настройки интеграции"
WINDOW_SUBTITLE = (
    "Все параметры для локального API, MCP и GPT-агента собраны в одном месте. "
    "Большая часть адресов считается автоматически. Для ChatGPT на телефоне нужен внешний HTTPS MCP URL."
)

SECTION_GENERAL = "Общие флаги"
SECTION_LOCAL_API = "Локальный API"
SECTION_MCP = "MCP"
SECTION_OPENAI = "OpenAI / GPT"
SECTION_AUTH = "Авторизация и секреты"
SECTION_DIAGNOSTICS = "Проверки и диагностика"
SECTION_EXPORT = "Экспорт и быстрые действия"

STATUS_LABELS = {
    "not_tested": "Не проверялось",
    "success": "Успешно",
    "failed": "Ошибка",
    "skipped": "Пропущено",
    "warning": "Предупреждение",
}

FIELD_LABELS = {
    "general.integration_enabled": "Включить интеграцию",
    "general.use_local_api": "Использовать локальный API",
    "local_api.local_api_host": "Хост локального API",
    "local_api.local_api_port": "Порт локального API",
    "local_api.local_api_base_url_override": "Внешний URL доски / API override",
    "local_api.local_api_auth_mode": "Режим авторизации локального API",
    "mcp.mcp_host": "Хост MCP",
    "mcp.mcp_port": "Порт MCP",
    "mcp.mcp_path": "Путь MCP",
    "mcp.public_https_base_url": "Public HTTPS Base URL",
    "mcp.tunnel_url": "Tunnel URL",
    "mcp.full_mcp_url_override": "Full MCP URL override",
    "mcp.mcp_auth_mode": "Режим авторизации MCP",
    "openai.provider": "Provider",
    "openai.model": "Model",
    "openai.base_url": "Base URL",
    "openai.timeout_seconds": "Timeout",
    "auth.auth_mode": "Общий режим авторизации",
}

SETTINGS_STYLES = """
QDialog, QWidget {
    background-color: #090b0e;
    color: #e8edf2;
    font-family: Segoe UI;
    font-size: 13px;
}
QPushButton {
    background-color: #14181d;
    color: #e8edf2;
    border: 1px solid #222831;
    border-radius: 8px;
    padding: 8px 12px;
}
QPushButton:hover {
    background-color: #1a2028;
}
QPushButton:disabled {
    color: #697280;
    border-color: #15191f;
}
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {
    background-color: #111418;
    border: 1px solid #262d36;
    border-radius: 8px;
    padding: 8px;
    color: #f1f4f7;
}
QLineEdit[readOnly="true"], QPlainTextEdit[readOnly="true"] {
    color: #9ca8b6;
}
QLineEdit[invalid="true"], QSpinBox[invalid="true"], QComboBox[invalid="true"] {
    border: 1px solid #8b4a55;
}
QComboBox::drop-down {
    border: none;
}
QSpinBox::up-button, QSpinBox::down-button {
    width: 18px;
}
QCheckBox {
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
QCheckBox::indicator:unchecked {
    background-color: #111418;
    border: 1px solid #262d36;
    border-radius: 4px;
}
QCheckBox::indicator:checked {
    background-color: #4f8f65;
    border: 1px solid #5fa777;
    border-radius: 4px;
}
#SettingsSection {
    background-color: #0f1216;
    border: 1px solid #171d24;
    border-radius: 12px;
}
#SectionTitle {
    font-size: 15px;
    font-weight: 700;
}
#SectionHint, #StatusMessage {
    color: #94a0ae;
}
#SectionHint[variant="warning"] {
    color: #d6b24c;
}
#SectionHint[variant="error"] {
    color: #d87b7b;
}
#StatusMessage[tone="error"] {
    color: #d87b7b;
}
#StatusMessage[tone="success"] {
    color: #82c896;
}
#StatusMessage[tone="warning"] {
    color: #d6b24c;
}
"""


class CopyField(QWidget):
    copied = Signal(str)
    textChanged = Signal(str)

    def __init__(self, *, read_only: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.input = QLineEdit()
        self.input.setReadOnly(read_only)
        self.input.textChanged.connect(self.textChanged.emit)

        self.copy_button = QPushButton(BUTTON_COPY)
        self.copy_button.setFixedWidth(110)
        self.copy_button.clicked.connect(self._copy_value)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.input, 1)
        layout.addWidget(self.copy_button)

    def text(self) -> str:
        return self.input.text().strip()

    def setText(self, value: str) -> None:
        self.input.setText(value or "")

    def _copy_value(self) -> None:
        QGuiApplication.clipboard().setText(self.input.text())
        self.copied.emit("Значение скопировано в буфер обмена.")


class SecretField(QWidget):
    copied = Signal(str)
    textChanged = Signal(str)

    def __init__(self, *, allow_generate: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.input = QLineEdit()
        self.input.setEchoMode(QLineEdit.EchoMode.Password)
        self.input.textChanged.connect(self.textChanged.emit)

        self.toggle_button = QPushButton(BUTTON_SHOW_SECRET)
        self.toggle_button.setFixedWidth(90)
        self.toggle_button.clicked.connect(self._toggle_visibility)

        self.copy_button = QPushButton(BUTTON_COPY)
        self.copy_button.setFixedWidth(110)
        self.copy_button.clicked.connect(self._copy_value)

        self.generate_button: QPushButton | None = None
        if allow_generate:
            self.generate_button = QPushButton("Сгенерировать")
            self.generate_button.setFixedWidth(126)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.input, 1)
        if self.generate_button is not None:
            layout.addWidget(self.generate_button)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.copy_button)

    def value(self) -> str:
        return self.input.text().strip()

    def set_value(self, value: str) -> None:
        self.input.setText(value or "")
        self.input.setEchoMode(QLineEdit.EchoMode.Password)
        self.toggle_button.setText(BUTTON_SHOW_SECRET)

    def _toggle_visibility(self) -> None:
        if self.input.echoMode() == QLineEdit.EchoMode.Password:
            self.input.setEchoMode(QLineEdit.EchoMode.Normal)
            self.toggle_button.setText(BUTTON_HIDE_SECRET)
            return
        self.input.setEchoMode(QLineEdit.EchoMode.Password)
        self.toggle_button.setText(BUTTON_SHOW_SECRET)

    def _copy_value(self) -> None:
        QGuiApplication.clipboard().setText(self.input.text())
        self.copied.emit("Секрет скопирован в буфер обмена.")


class CopyTextArea(QWidget):
    copied = Signal(str)

    def __init__(self, *, read_only: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.input = QPlainTextEdit()
        self.input.setReadOnly(read_only)
        self.input.setFixedHeight(160)

        self.copy_button = QPushButton(BUTTON_COPY)
        self.copy_button.setFixedWidth(110)
        self.copy_button.clicked.connect(self._copy_value)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.input, 1)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addStretch(1)
        buttons.addWidget(self.copy_button)
        layout.addLayout(buttons)

    def text(self) -> str:
        return self.input.toPlainText().strip()

    def setText(self, value: str) -> None:
        self.input.setPlainText(value or "")

    def _copy_value(self) -> None:
        QGuiApplication.clipboard().setText(self.input.toPlainText())
        self.copied.emit("Список MCP tools скопирован в буфер обмена.")


def _set_text_lines(widget: QPlainTextEdit, values) -> None:
    widget.setPlainText("\n".join(normalize_string_list(values)))


def _read_text_lines(widget: QPlainTextEdit) -> tuple[str, ...]:
    return tuple(normalize_string_list(widget.toPlainText()))


def _apply_status_label_state(owner: QWidget, label: QLabel, message: str, *, tone: str = "info") -> None:
    label.setText(message)
    label.setProperty("tone", tone)
    owner.style().unpolish(label)
    owner.style().polish(label)


class ChatGPTConnectDialog(QDialog):
    def __init__(
        self,
        *,
        settings: IntegrationSettings,
        runtime_api_url: str,
        runtime_state,
        settings_provider,
        test_target_callback,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._runtime_api_url = runtime_api_url
        self._runtime_state = runtime_state
        self._settings_provider = settings_provider
        self._test_target_callback = test_target_callback

        self.setWindowTitle("Подключиться к ChatGPT")
        self.setModal(True)
        self.resize(780, 760)
        self.setStyleSheet(SETTINGS_STYLES)

        title_label = QLabel("Подключиться к ChatGPT")
        title_label.setStyleSheet("font-size: 18px; font-weight: 700;")

        subtitle_label = QLabel(
            "Короткий мастер показывает уже готовые данные из настроек. "
            "Для ChatGPT connector в bearer-режиме сервер теперь отдаёт встроенный OAuth / DCR, "
            "а legacy bearer token остаётся для Responses API и ручных MCP-клиентов."
        )
        subtitle_label.setObjectName("SectionHint")
        subtitle_label.setWordWrap(True)

        self.warning_label = QLabel("")
        self.warning_label.setObjectName("SectionHint")
        self.warning_label.setProperty("variant", "warning")
        self.warning_label.setWordWrap(True)

        steps_frame = QFrame()
        steps_frame.setObjectName("SettingsSection")
        steps_layout = QVBoxLayout(steps_frame)
        steps_layout.setContentsMargins(14, 14, 14, 14)
        steps_layout.setSpacing(8)

        steps_title = QLabel("Шаги подключения")
        steps_title.setObjectName("SectionTitle")
        steps_layout.addWidget(steps_title)

        step_texts = (
            "Шаг 1. Запустите локальный API и MCP сервер.",
            "Шаг 2. Откройте ChatGPT -> Settings -> Apps & Connectors -> Create.",
            "Шаг 3. Добавьте MCP Server.",
            "Шаг 4. Вставьте effective MCP URL.",
            "Шаг 6. Нажмите Connect и завершите linking, если ChatGPT его запросит.",
            "Шаг 7. Проверьте tools и get_gpt_wall.",
        )
        for text in step_texts[:4]:
            label = QLabel(text)
            label.setWordWrap(True)
            steps_layout.addWidget(label)
        self.step_token_label = QLabel("")
        self.step_token_label.setWordWrap(True)
        steps_layout.addWidget(self.step_token_label)
        for text in step_texts[4:]:
            label = QLabel(text)
            label.setWordWrap(True)
            steps_layout.addWidget(label)

        values_frame = QFrame()
        values_frame.setObjectName("SettingsSection")
        values_layout = QVBoxLayout(values_frame)
        values_layout.setContentsMargins(14, 14, 14, 14)
        values_layout.setSpacing(12)

        values_title = QLabel("Готовые значения для копирования")
        values_title.setObjectName("SectionTitle")
        values_layout.addWidget(values_title)

        self.chatgpt_home_input = CopyField(read_only=True)
        self.openai_guide_input = CopyField(read_only=True)
        self.openai_apps_guide_input = CopyField(read_only=True)
        self.effective_mcp_url_input = CopyField(read_only=True)
        self.local_mcp_url_input = CopyField(read_only=True)
        self.effective_local_api_url_input = CopyField(read_only=True)
        self.mcp_token_label = QLabel("Legacy bearer token MCP / Responses API")
        self.mcp_token_input = SecretField()
        self.tools_input = CopyTextArea(read_only=True)

        form = QFormLayout()
        self.diagnostics_form = form
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addRow("ChatGPT", self.chatgpt_home_input)
        form.addRow("OpenAI MCP guide", self.openai_guide_input)
        form.addRow("OpenAI Apps guide", self.openai_apps_guide_input)
        form.addRow("Итоговый MCP URL для ChatGPT", self.effective_mcp_url_input)
        form.addRow("Локальный MCP URL", self.local_mcp_url_input)
        form.addRow("Итоговый URL локального API", self.effective_local_api_url_input)
        values_layout.addLayout(form)
        values_layout.addWidget(self.mcp_token_label)
        values_layout.addWidget(self.mcp_token_input)

        tools_label = QLabel("Доступные MCP tools")
        tools_label.setStyleSheet("font-weight: 600;")
        values_layout.addWidget(tools_label)
        values_layout.addWidget(self.tools_input)

        self.preflight_status_label = QLabel("")
        self.preflight_status_label.setObjectName("StatusMessage")
        self.preflight_status_label.setWordWrap(True)

        self.copy_all_button = QPushButton("Скопировать всё для подключения")
        self.copy_all_button.clicked.connect(self._copy_all)

        self.open_chatgpt_button = QPushButton("Открыть ChatGPT")
        self.open_chatgpt_button.clicked.connect(self._open_chatgpt_home)

        self.open_openai_guide_button = QPushButton("Открыть MCP guide")
        self.open_openai_guide_button.clicked.connect(self._open_openai_guide)

        self.open_apps_guide_button = QPushButton("Открыть Apps guide")
        self.open_apps_guide_button.clicked.connect(self._open_openai_apps_guide)

        self.check_mcp_button = QPushButton("Проверить MCP перед подключением")
        self.check_mcp_button.clicked.connect(self._check_before_connect)

        self.open_settings_button = QPushButton("Открыть настройки интеграции")
        self.open_settings_button.clicked.connect(self._return_to_settings)

        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.accept)

        for widget in (
            self.chatgpt_home_input,
            self.openai_guide_input,
            self.openai_apps_guide_input,
            self.effective_mcp_url_input,
            self.local_mcp_url_input,
            self.effective_local_api_url_input,
            self.mcp_token_input,
            self.tools_input,
        ):
            widget.copied.connect(lambda message, self=self: self._set_status(message, tone="success"))

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        buttons.addWidget(self.open_chatgpt_button)
        buttons.addWidget(self.open_openai_guide_button)
        buttons.addWidget(self.open_apps_guide_button)
        buttons.addWidget(self.copy_all_button)
        buttons.addWidget(self.check_mcp_button)
        buttons.addStretch(1)
        buttons.addWidget(self.open_settings_button)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        layout.addWidget(self.warning_label)
        layout.addWidget(steps_frame)
        layout.addWidget(values_frame, 1)
        layout.addWidget(self.preflight_status_label)
        layout.addLayout(buttons)

        self._refresh_from_settings(settings)

    def _display_value(self, value: str) -> str:
        text = str(value or "").strip()
        return text or "<не задан>"

    def _build_warning_message(self, settings: IntegrationSettings) -> str:
        if self._runtime_state is None or not self._runtime_state.running:
            return "Сначала запустите MCP сервер. Пока runtime не поднят, подключать ChatGPT рано."
        url = settings.mcp.effective_mcp_url.strip()
        if not url:
            return "Итоговый MCP URL не задан. Для подключения из ChatGPT нужен внешний HTTPS URL."
        if not url.startswith("https://") or not is_external_http_url(url):
            return (
                "Локальный MCP работает, но для ChatGPT нужен внешний HTTPS URL "
                "через tunnel, reverse proxy или full MCP URL override."
            )
        return ""

    def _refresh_from_settings(self, settings: IntegrationSettings) -> None:
        self._settings = settings
        effective_public_mcp_url = settings.mcp.effective_mcp_url.strip()
        if not effective_public_mcp_url.startswith("https://"):
            effective_public_mcp_url = ""
        self.chatgpt_home_input.setText(self._display_value(CHATGPT_HOME_URL))
        self.openai_guide_input.setText(self._display_value(OPENAI_MCP_CONNECTORS_GUIDE_URL))
        self.openai_apps_guide_input.setText(self._display_value(OPENAI_APPS_CONNECT_GUIDE_URL))
        self.effective_mcp_url_input.setText(effective_public_mcp_url)
        self.local_mcp_url_input.setText(self._display_value(settings.mcp.local_mcp_url))
        self.effective_local_api_url_input.setText(self._display_value(settings.local_api.effective_local_api_url))
        self.tools_input.setText("\n".join(MCP_TOOL_NAMES))

        if settings.mcp.mcp_auth_mode == "bearer":
            self.step_token_label.setText("Шаг 5. Вставьте токен, если включён bearer auth.")
            self.mcp_token_label.show()
            self.mcp_token_input.show()
            self.mcp_token_input.set_value(resolve_mcp_bearer_token(settings))
        else:
            self.step_token_label.setText("Шаг 5. Токен не нужен: bearer auth для MCP выключен.")
            self.mcp_token_label.hide()
            self.mcp_token_input.hide()
            self.mcp_token_input.set_value("")

        warning = self._build_warning_message(settings)
        self.warning_label.setText(warning)
        self.warning_label.setVisible(bool(warning))
        self.style().unpolish(self.warning_label)
        self.style().polish(self.warning_label)
        if self._runtime_state is None or not self._runtime_state.running:
            self._set_status("Сначала запустите MCP сервер, затем возвращайтесь к подключению ChatGPT.", tone="warning")
        elif warning:
            self._set_status(warning, tone="warning")
        else:
            self._set_status("MCP runtime запущен. Можно копировать данные и подключать ChatGPT.", tone="success")

    def _copy_all(self) -> None:
        settings = self._settings_provider()
        payload = build_chatgpt_connect_payload(
            settings,
            runtime_api_url=self._runtime_api_url,
            runtime_state=self._runtime_state,
        )
        QGuiApplication.clipboard().setText(payload)
        self._set_status("Все данные для подключения скопированы в буфер обмена.", tone="success")

    def _check_before_connect(self) -> None:
        settings = self._settings_provider()
        self._refresh_from_settings(settings)

        if self._runtime_state is None or not self._runtime_state.running:
            self._set_status("Сначала запустите MCP сервер.", tone="error")
            return

        mcp_result = self._test_target_callback("mcp")
        if mcp_result is None:
            self._set_status("Проверка MCP не выполнена.", tone="error")
            return

        messages = [mcp_result.message]
        tone = "success" if mcp_result.status == "success" else "warning" if mcp_result.status == "skipped" else "error"

        effective_url = settings.mcp.effective_mcp_url.strip()
        if effective_url.startswith("https://") and is_external_http_url(effective_url):
            external_result = self._test_target_callback("external")
            if external_result is not None:
                messages.append(external_result.message)
                if external_result.status == "failed":
                    tone = "error"
                elif external_result.status != "success" and tone != "error":
                    tone = "warning"
        else:
            messages.append("Внешний HTTPS MCP URL пока не задан, поэтому проверка внешнего endpoint не выполнена.")
            if tone == "success":
                tone = "warning"

        self._set_status("\n".join(messages), tone=tone)

    def _return_to_settings(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            parent.raise_()
            parent.activateWindow()
        self.accept()

    def _open_chatgpt_home(self) -> None:
        QDesktopServices.openUrl(QUrl(CHATGPT_HOME_URL))
        self._set_status("ChatGPT открыт. Продолжайте: Settings -> Apps & Connectors -> Create.", tone="success")

    def _open_openai_guide(self) -> None:
        QDesktopServices.openUrl(QUrl(OPENAI_MCP_CONNECTORS_GUIDE_URL))
        self._set_status("Открыта официальная документация OpenAI по MCP connectors.", tone="success")

    def _open_openai_apps_guide(self) -> None:
        QDesktopServices.openUrl(QUrl(OPENAI_APPS_CONNECT_GUIDE_URL))
        self._set_status("Открыта официальная документация OpenAI по подключению из ChatGPT.", tone="success")

    def _set_status(self, message: str, *, tone: str = "info") -> None:
        _apply_status_label_state(self, self.preflight_status_label, message, tone=tone)


class SettingsWindow(QDialog):
    settings_saved = Signal(object)

    def __init__(
        self,
        settings_service: SettingsService,
        runtime_api_url: str,
        *,
        mcp_controller: McpRuntimeController | None = None,
        tunnel_controller: TunnelRuntimeController | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._settings_service = settings_service
        self._runtime_api_url = runtime_api_url.rstrip("/")
        self._mcp_controller = mcp_controller
        self._tunnel_controller = tunnel_controller
        self._settings = self._settings_service.load()
        self._runtime_reference = self._settings
        self._connect_dialog: ChatGPTConnectDialog | None = None
        self._validation_widgets: dict[str, object] = {}
        self._advanced_mode = False

        self.setWindowTitle(WINDOW_TITLE)
        self.setModal(True)
        self.resize(1040, 980)
        self.setStyleSheet(SETTINGS_STYLES)

        title_label = QLabel(WINDOW_TITLE)
        title_label.setStyleSheet("font-size: 20px; font-weight: 700;")
        subtitle_label = QLabel(WINDOW_SUBTITLE)
        subtitle_label.setObjectName("SectionHint")
        subtitle_label.setWordWrap(True)

        self.restart_hint_label = QLabel("")
        self.restart_hint_label.setObjectName("SectionHint")
        self.restart_hint_label.setProperty("variant", "warning")
        self.restart_hint_label.setWordWrap(True)
        self.show_advanced_checkbox = QCheckBox("Показать расширенные настройки")
        self.show_advanced_checkbox.toggled.connect(self._toggle_advanced_mode)

        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        header_layout.addWidget(title_label)
        header_layout.addWidget(subtitle_label)
        header_layout.addWidget(self.restart_hint_label)
        header_layout.addWidget(self.show_advanced_checkbox)

        content = QWidget()
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(14)

        self._build_general_section()
        self._build_local_api_section()
        self._build_mcp_section()
        self._build_openai_section()
        self._build_auth_section()
        self._build_diagnostics_section()
        self._build_export_section()
        self._wire_copy_feedback()
        self.content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)

        self.status_label = QLabel("")
        self.status_label.setObjectName("StatusMessage")
        self.status_label.setWordWrap(True)

        self.reset_button = QPushButton(BUTTON_RESET_DEFAULTS)
        self.reset_button.clicked.connect(self._reset_form)

        self.cancel_button = QPushButton(BUTTON_CANCEL)
        self.cancel_button.clicked.connect(self.reject)

        self.apply_button = QPushButton(BUTTON_APPLY)
        self.apply_button.clicked.connect(self._apply)

        self.save_button = QPushButton(BUTTON_SAVE)
        self.save_button.clicked.connect(self._save)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        footer.addWidget(self.status_label, 1)
        footer.addWidget(self.reset_button)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.apply_button)
        footer.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(16)
        layout.addLayout(header_layout)
        layout.addWidget(scroll, 1)
        layout.addLayout(footer)

        self._load_into_form(self._settings)
        self._toggle_advanced_mode(False)
        self._set_status("")

    def _toggle_advanced_mode(self, enabled: bool) -> None:
        self._advanced_mode = bool(enabled)
        for section_name in ("local_api_section", "openai_section", "auth_section"):
            section = getattr(self, section_name, None)
            if section is not None:
                section.setVisible(self._advanced_mode)

        advanced_widgets = (
            getattr(self, "mcp_host_input", None),
            getattr(self, "mcp_port_input", None),
            getattr(self, "mcp_path_input", None),
            getattr(self, "mcp_auth_mode_input", None),
            getattr(self, "mcp_public_base_input", None),
            getattr(self, "mcp_full_url_input", None),
            getattr(self, "mcp_public_endpoint_input", None),
            getattr(self, "mcp_tunnel_endpoint_input", None),
            getattr(self, "mcp_allowed_hosts_input", None),
            getattr(self, "mcp_allowed_origins_input", None),
            getattr(self, "mcp_resolved_hosts_input", None),
            getattr(self, "mcp_resolved_origins_input", None),
        )
        form = getattr(self, "mcp_form", None)
        if form is not None:
            for widget in advanced_widgets:
                if widget is not None:
                    try:
                        form.setRowVisible(widget, self._advanced_mode)
                    except Exception:
                        widget.setVisible(self._advanced_mode)

        diagnostics_advanced_widgets = (
            getattr(self, "settings_file_input", None),
            getattr(self, "log_file_input", None),
            getattr(self, "mcp_log_file_input", None),
            getattr(self, "runtime_api_input", None),
            getattr(self, "runtime_mcp_url_input", None),
            getattr(self, "openai_status_input", None),
            getattr(self, "last_local_api_check_input", None),
            getattr(self, "last_mcp_check_input", None),
            getattr(self, "last_external_check_input", None),
            getattr(self, "last_openai_check_input", None),
            getattr(self, "last_full_check_input", None),
        )
        diagnostics_form = getattr(self, "diagnostics_form", None)
        if diagnostics_form is not None:
            for widget in diagnostics_advanced_widgets:
                if widget is not None:
                    try:
                        diagnostics_form.setRowVisible(widget, self._advanced_mode)
                    except Exception:
                        widget.setVisible(self._advanced_mode)

        for widget_name in ("open_mcp_log_button", "copy_mcp_error_button"):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setVisible(self._advanced_mode)

        if hasattr(self, "test_openai_button"):
            self.test_openai_button.setVisible(self._advanced_mode)

    def _wire_copy_feedback(self) -> None:
        copy_widgets = [
            self.local_api_host_input,
            self.runtime_local_api_url_input,
            self.local_api_health_url_input,
            self.local_api_base_url_input,
            self.effective_local_api_url_input,
            self.mcp_host_input,
            self.mcp_path_input,
            self.mcp_local_url_input,
            self.mcp_public_base_input,
            self.mcp_tunnel_url_input,
            self.mcp_full_url_input,
            self.mcp_public_endpoint_input,
            self.mcp_tunnel_endpoint_input,
            self.mcp_effective_url_input,
            self.mcp_resolved_hosts_input,
            self.mcp_resolved_origins_input,
            self.base_url_input,
            self.settings_file_input,
            self.log_file_input,
            self.mcp_log_file_input,
            self.runtime_api_input,
            self.runtime_mcp_status_input,
            self.runtime_mcp_url_input,
            self.overall_status_input,
            self.local_api_status_input,
            self.mcp_status_input,
            self.external_status_input,
            self.openai_status_input,
            self.last_local_api_check_input,
            self.last_mcp_check_input,
            self.last_external_check_input,
            self.last_openai_check_input,
            self.last_full_check_input,
        ]
        for widget in copy_widgets:
            widget.copied.connect(self._set_status)

        for widget in (
            self.access_token_input,
            self.local_api_token_input,
            self.mcp_token_input,
            self.openai_api_key_input,
        ):
            widget.copied.connect(self._set_status)

    def _build_general_section(self) -> None:
        section, layout = self._section_frame(SECTION_GENERAL)
        self.general_section = section

        self.integration_enabled_checkbox = QCheckBox("Включить интеграцию")
        self.use_local_api_checkbox = QCheckBox("Использовать локальный API")
        self.auto_connect_checkbox = QCheckBox("Автоподключение MCP при запуске приложения")
        self.test_mode_checkbox = QCheckBox("Тестовый режим smoke checks")
        self.general_hint_label = self._hint_label()

        for checkbox in (
            self.integration_enabled_checkbox,
            self.use_local_api_checkbox,
            self.auto_connect_checkbox,
            self.test_mode_checkbox,
        ):
            checkbox.toggled.connect(self._sync_derived_fields)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addRow(self.integration_enabled_checkbox)
        form.addRow(self.use_local_api_checkbox)
        form.addRow(self.auto_connect_checkbox)
        form.addRow(self.test_mode_checkbox)
        layout.addLayout(form)
        layout.addWidget(self.general_hint_label)

        self.content_layout.addWidget(section)

    def _build_local_api_section(self) -> None:
        section, layout = self._section_frame(SECTION_LOCAL_API)
        self.local_api_section = section

        self.local_api_host_input = CopyField()
        self.local_api_port_input = QSpinBox()
        self.local_api_port_input.setRange(1, 65535)
        self.local_api_auth_mode_input = self._auth_mode_combo()
        self.runtime_local_api_url_input = CopyField(read_only=True)
        self.local_api_health_url_input = CopyField(read_only=True)
        self.local_api_base_url_input = CopyField()
        self.effective_local_api_url_input = CopyField(read_only=True)
        self.local_api_hint_label = self._hint_label()

        self._register_validation_widget("local_api.local_api_host", self.local_api_host_input.input)
        self._register_validation_widget("local_api.local_api_port", self.local_api_port_input)
        self._register_validation_widget("local_api.local_api_base_url_override", self.local_api_base_url_input.input)
        self._register_validation_widget("local_api.local_api_auth_mode", self.local_api_auth_mode_input)

        self.local_api_host_input.textChanged.connect(self._sync_derived_fields)
        self.local_api_port_input.valueChanged.connect(self._sync_derived_fields)
        self.local_api_auth_mode_input.currentIndexChanged.connect(self._sync_derived_fields)
        self.local_api_base_url_input.textChanged.connect(self._sync_derived_fields)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addRow("Хост локального API", self.local_api_host_input)
        form.addRow("Порт локального API", self.local_api_port_input)
        form.addRow("Режим авторизации локального API", self.local_api_auth_mode_input)
        form.addRow("Runtime URL локального API", self.runtime_local_api_url_input)
        form.addRow("URL health-check", self.local_api_health_url_input)
        form.addRow("Внешний URL доски / API override", self.local_api_base_url_input)
        form.addRow("Итоговый URL API для MCP и GPT-агента", self.effective_local_api_url_input)
        layout.addLayout(form)
        layout.addWidget(self.local_api_hint_label)

        self.content_layout.addWidget(section)

    def _build_mcp_section(self) -> None:
        section, layout = self._section_frame(SECTION_MCP)
        self.mcp_section = section

        self.mcp_enabled_checkbox = QCheckBox("Включить MCP")
        self.mcp_enabled_checkbox.toggled.connect(self._sync_derived_fields)

        self.mcp_host_input = CopyField()
        self.mcp_port_input = QSpinBox()
        self.mcp_port_input.setRange(1, 65535)
        self.mcp_path_input = CopyField()
        self.mcp_auth_mode_input = self._auth_mode_combo()
        self.mcp_local_url_input = CopyField(read_only=True)
        self.mcp_public_base_input = CopyField()
        self.mcp_tunnel_url_input = CopyField()
        self.mcp_full_url_input = CopyField()
        self.mcp_public_endpoint_input = CopyField(read_only=True)
        self.mcp_tunnel_endpoint_input = CopyField(read_only=True)
        self.mcp_effective_url_input = CopyField(read_only=True)
        self.mcp_tunnel_url_input.input.setReadOnly(True)
        self.mcp_allowed_hosts_input = QPlainTextEdit()
        self.mcp_allowed_hosts_input.setFixedHeight(82)
        self.mcp_allowed_origins_input = QPlainTextEdit()
        self.mcp_allowed_origins_input.setFixedHeight(82)
        self.mcp_resolved_hosts_input = CopyTextArea(read_only=True)
        self.mcp_resolved_hosts_input.input.setFixedHeight(92)
        self.mcp_resolved_origins_input = CopyTextArea(read_only=True)
        self.mcp_resolved_origins_input.input.setFixedHeight(92)
        self.mcp_hint_label = self._hint_label()
        self.mcp_external_hint_label = self._hint_label(variant="warning")

        self._register_validation_widget("mcp.mcp_host", self.mcp_host_input.input)
        self._register_validation_widget("mcp.mcp_port", self.mcp_port_input)
        self._register_validation_widget("mcp.mcp_path", self.mcp_path_input.input)
        self._register_validation_widget("mcp.public_https_base_url", self.mcp_public_base_input.input)
        self._register_validation_widget("mcp.tunnel_url", self.mcp_tunnel_url_input.input)
        self._register_validation_widget("mcp.full_mcp_url_override", self.mcp_full_url_input.input)
        self._register_validation_widget("mcp.mcp_auth_mode", self.mcp_auth_mode_input)

        for widget in (
            self.mcp_host_input,
            self.mcp_path_input,
            self.mcp_public_base_input,
            self.mcp_tunnel_url_input,
            self.mcp_full_url_input,
        ):
            widget.textChanged.connect(self._sync_derived_fields)
        self.mcp_allowed_hosts_input.textChanged.connect(self._sync_derived_fields)
        self.mcp_allowed_origins_input.textChanged.connect(self._sync_derived_fields)
        self.mcp_port_input.valueChanged.connect(self._sync_derived_fields)
        self.mcp_auth_mode_input.currentIndexChanged.connect(self._sync_derived_fields)

        form = QFormLayout()
        self.mcp_form = form
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addRow(self.mcp_enabled_checkbox)
        form.addRow("Хост MCP", self.mcp_host_input)
        form.addRow("Порт MCP", self.mcp_port_input)
        form.addRow("Path MCP", self.mcp_path_input)
        form.addRow("Режим авторизации MCP", self.mcp_auth_mode_input)
        form.addRow("Локальный MCP URL", self.mcp_local_url_input)
        form.addRow("Public HTTPS Base URL", self.mcp_public_base_input)
        form.addRow("Tunnel URL", self.mcp_tunnel_url_input)
        form.addRow("Full MCP URL override", self.mcp_full_url_input)
        form.addRow("Derived MCP URL по Public HTTPS", self.mcp_public_endpoint_input)
        form.addRow("Derived MCP URL по Tunnel", self.mcp_tunnel_endpoint_input)
        form.addRow("Итоговый MCP URL для GPT-агента", self.mcp_effective_url_input)
        form.addRow("Дополнительные allowed hosts", self.mcp_allowed_hosts_input)
        form.addRow("Дополнительные allowed origins", self.mcp_allowed_origins_input)
        form.addRow("Эффективные allowed hosts", self.mcp_resolved_hosts_input)
        form.addRow("Эффективные allowed origins", self.mcp_resolved_origins_input)
        layout.addLayout(form)

        runtime_row = QHBoxLayout()
        runtime_row.setContentsMargins(0, 0, 0, 0)
        runtime_row.setSpacing(8)
        self.start_mcp_button = QPushButton("Запустить MCP сервер")
        self.start_mcp_button.clicked.connect(self._start_mcp_runtime)
        self.restart_mcp_button = QPushButton("Перезапустить MCP сервер")
        self.restart_mcp_button.clicked.connect(self._restart_mcp_runtime)
        self.stop_mcp_button = QPushButton("Остановить MCP сервер")
        self.stop_mcp_button.clicked.connect(self._stop_mcp_runtime)
        self.open_mcp_log_button = QPushButton("Открыть лог MCP")
        self.open_mcp_log_button.clicked.connect(self._open_mcp_log)
        self.copy_mcp_error_button = QPushButton("Скопировать ошибку запуска")
        self.copy_mcp_error_button.clicked.connect(self._copy_mcp_startup_error)
        runtime_row.addWidget(self.start_mcp_button)
        runtime_row.addWidget(self.restart_mcp_button)
        runtime_row.addWidget(self.stop_mcp_button)
        runtime_row.addWidget(self.open_mcp_log_button)
        runtime_row.addWidget(self.copy_mcp_error_button)
        runtime_row.addStretch(1)
        layout.addLayout(runtime_row)
        layout.addWidget(self.mcp_hint_label)
        layout.addWidget(self.mcp_external_hint_label)

        self.content_layout.addWidget(section)

    def _build_openai_section(self) -> None:
        section, layout = self._section_frame(SECTION_OPENAI)
        self.openai_section = section

        self.provider_input = QLineEdit()
        self.model_input = QLineEdit()
        self.base_url_input = CopyField()
        self.organization_input = QLineEdit()
        self.project_input = QLineEdit()
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(1, 600)
        self.openai_hint_label = self._hint_label()

        self._register_validation_widget("openai.provider", self.provider_input)
        self._register_validation_widget("openai.model", self.model_input)
        self._register_validation_widget("openai.base_url", self.base_url_input.input)
        self._register_validation_widget("openai.timeout_seconds", self.timeout_input)

        self.provider_input.textChanged.connect(self._sync_derived_fields)
        self.model_input.textChanged.connect(self._sync_derived_fields)
        self.base_url_input.textChanged.connect(self._sync_derived_fields)
        self.organization_input.textChanged.connect(self._sync_derived_fields)
        self.project_input.textChanged.connect(self._sync_derived_fields)
        self.timeout_input.valueChanged.connect(self._sync_derived_fields)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addRow("Provider", self.provider_input)
        form.addRow("Model", self.model_input)
        form.addRow("Base URL", self.base_url_input)
        form.addRow("Organization ID", self.organization_input)
        form.addRow("Project ID", self.project_input)
        form.addRow("Timeout, сек", self.timeout_input)
        layout.addLayout(form)
        layout.addWidget(self.openai_hint_label)

        self.content_layout.addWidget(section)

    def _build_auth_section(self) -> None:
        section, layout = self._section_frame(SECTION_AUTH)
        self.auth_section = section

        self.auth_mode_input = self._auth_mode_combo()
        self.access_token_input = SecretField()
        self.local_api_token_input = SecretField(allow_generate=True)
        self.mcp_token_input = SecretField(allow_generate=True)
        self.openai_api_key_input = SecretField()
        self.auth_hint_label = self._hint_label(variant="warning")

        self._register_validation_widget("auth.auth_mode", self.auth_mode_input)

        self.auth_mode_input.currentIndexChanged.connect(self._sync_derived_fields)
        self.access_token_input.textChanged.connect(self._sync_derived_fields)
        self.local_api_token_input.textChanged.connect(self._sync_derived_fields)
        self.mcp_token_input.textChanged.connect(self._sync_derived_fields)
        self.openai_api_key_input.textChanged.connect(self._sync_derived_fields)
        assert self.local_api_token_input.generate_button is not None
        assert self.mcp_token_input.generate_button is not None
        self.local_api_token_input.generate_button.clicked.connect(lambda: self._generate_token_for(self.local_api_token_input, "локального API"))
        self.mcp_token_input.generate_button.clicked.connect(lambda: self._generate_token_for(self.mcp_token_input, "MCP"))

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addRow("Общий режим авторизации", self.auth_mode_input)
        form.addRow("Access token", self.access_token_input)
        form.addRow("Bearer token локального API", self.local_api_token_input)
        form.addRow("Legacy bearer token MCP", self.mcp_token_input)
        form.addRow("OpenAI API key", self.openai_api_key_input)
        layout.addLayout(form)
        layout.addWidget(self.auth_hint_label)

        self.content_layout.addWidget(section)

    def _build_diagnostics_section(self) -> None:
        section, layout = self._section_frame(SECTION_DIAGNOSTICS)
        self.diagnostics_section = section

        self.settings_file_input = CopyField(read_only=True)
        self.log_file_input = CopyField(read_only=True)
        self.mcp_log_file_input = CopyField(read_only=True)
        self.runtime_api_input = CopyField(read_only=True)
        self.runtime_mcp_status_input = CopyField(read_only=True)
        self.runtime_mcp_url_input = CopyField(read_only=True)
        self.overall_status_input = CopyField(read_only=True)
        self.local_api_status_input = CopyField(read_only=True)
        self.mcp_status_input = CopyField(read_only=True)
        self.external_status_input = CopyField(read_only=True)
        self.openai_status_input = CopyField(read_only=True)
        self.last_local_api_check_input = CopyField(read_only=True)
        self.last_mcp_check_input = CopyField(read_only=True)
        self.last_external_check_input = CopyField(read_only=True)
        self.last_openai_check_input = CopyField(read_only=True)
        self.last_full_check_input = CopyField(read_only=True)

        self.local_api_message_label = self._hint_label()
        self.mcp_message_label = self._hint_label()
        self.external_message_label = self._hint_label()
        self.openai_message_label = self._hint_label()

        self.warnings_output = QPlainTextEdit()
        self.warnings_output.setReadOnly(True)
        self.warnings_output.setPlaceholderText("Предупреждения появятся после проверки.")
        self.warnings_output.setFixedHeight(80)

        self.errors_output = QPlainTextEdit()
        self.errors_output.setReadOnly(True)
        self.errors_output.setPlaceholderText("Ошибки появятся после проверки.")
        self.errors_output.setFixedHeight(100)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addRow("Файл настроек", self.settings_file_input)
        form.addRow("Файл журнала", self.log_file_input)
        form.addRow("Файл MCP startup log", self.mcp_log_file_input)
        form.addRow("Текущий runtime URL локального API", self.runtime_api_input)
        form.addRow("Состояние MCP runtime", self.runtime_mcp_status_input)
        form.addRow("Текущий runtime URL MCP", self.runtime_mcp_url_input)
        form.addRow("Общий статус", self.overall_status_input)
        form.addRow("Статус локального API", self.local_api_status_input)
        form.addRow(self.local_api_message_label)
        form.addRow("Статус MCP", self.mcp_status_input)
        form.addRow(self.mcp_message_label)
        form.addRow("Статус внешнего endpoint", self.external_status_input)
        form.addRow(self.external_message_label)
        form.addRow("Статус OpenAI", self.openai_status_input)
        form.addRow(self.openai_message_label)
        form.addRow("Последняя проверка локального API", self.last_local_api_check_input)
        form.addRow("Последняя проверка MCP", self.last_mcp_check_input)
        form.addRow("Последняя проверка внешнего endpoint", self.last_external_check_input)
        form.addRow("Последняя проверка OpenAI", self.last_openai_check_input)
        form.addRow("Последняя полная проверка", self.last_full_check_input)
        layout.addLayout(form)

        warnings_label = QLabel("Предупреждения")
        warnings_label.setStyleSheet("font-weight: 600;")
        errors_label = QLabel("Ошибки")
        errors_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(warnings_label)
        layout.addWidget(self.warnings_output)
        layout.addWidget(errors_label)
        layout.addWidget(self.errors_output)

        tests_row = QHBoxLayout()
        tests_row.setContentsMargins(0, 0, 0, 0)
        tests_row.setSpacing(8)
        self.test_local_api_button = QPushButton("Проверить локальный API")
        self.test_local_api_button.clicked.connect(lambda: self._run_single_test("local_api"))
        self.test_mcp_button = QPushButton("Проверить MCP локально")
        self.test_mcp_button.clicked.connect(lambda: self._run_single_test("mcp"))
        self.test_external_button = QPushButton("Проверить внешний endpoint")
        self.test_external_button.clicked.connect(lambda: self._run_single_test("external"))
        self.test_openai_button = QPushButton("Проверить OpenAI")
        self.test_openai_button.clicked.connect(lambda: self._run_single_test("openai"))
        self.test_all_button = QPushButton("Проверить всё")
        self.test_all_button.clicked.connect(self._test_connections)
        tests_row.addWidget(self.test_local_api_button)
        tests_row.addWidget(self.test_mcp_button)
        tests_row.addWidget(self.test_external_button)
        tests_row.addWidget(self.test_openai_button)
        tests_row.addStretch(1)
        tests_row.addWidget(self.test_all_button)
        layout.addLayout(tests_row)

        self.content_layout.addWidget(section)

    def _build_export_section(self) -> None:
        section, layout = self._section_frame(SECTION_EXPORT)
        self.export_section = section

        self.include_secrets_checkbox = QCheckBox("Включить секреты в экспорт")
        self.include_secrets_checkbox.setChecked(False)

        self.connect_chatgpt_button = QPushButton("Подключиться к ChatGPT")
        self.connect_chatgpt_button.clicked.connect(self._open_chatgpt_connect_dialog)
        self.copy_connection_card_button = QPushButton("Скопировать сводку")
        self.copy_connection_card_button.clicked.connect(self._copy_connection_card)
        self.export_connection_card_button = QPushButton("Экспортировать карточку подключения")
        self.export_connection_card_button.clicked.connect(self._export_connection_card)
        self.export_chatgpt_connector_button = QPushButton("Экспорт ChatGPT connector JSON")
        self.export_chatgpt_connector_button.clicked.connect(self._export_chatgpt_connector_payload)
        self.export_responses_payload_button = QPushButton("Экспорт Responses API JSON")
        self.export_responses_payload_button.clicked.connect(self._export_responses_payload)
        self.export_settings_button = QPushButton("Экспортировать настройки интеграции")
        self.export_settings_button.clicked.connect(self._export_settings_snapshot)
        self.open_docs_button = QPushButton("Открыть инструкцию подключения")
        self.open_docs_button.clicked.connect(self._open_connection_docs)

        primary_buttons = QHBoxLayout()
        primary_buttons.setContentsMargins(0, 0, 0, 0)
        primary_buttons.setSpacing(8)
        primary_buttons.addWidget(self.connect_chatgpt_button)
        primary_buttons.addWidget(self.export_chatgpt_connector_button)
        primary_buttons.addWidget(self.export_responses_payload_button)
        primary_buttons.addStretch(1)
        primary_buttons.addWidget(self.open_docs_button)

        secondary_buttons = QHBoxLayout()
        secondary_buttons.setContentsMargins(0, 0, 0, 0)
        secondary_buttons.setSpacing(8)
        secondary_buttons.addWidget(self.copy_connection_card_button)
        secondary_buttons.addWidget(self.export_connection_card_button)
        secondary_buttons.addWidget(self.export_settings_button)
        secondary_buttons.addStretch(1)

        layout.addWidget(self.include_secrets_checkbox)
        layout.addLayout(primary_buttons)
        layout.addLayout(secondary_buttons)
        layout.addWidget(
            self._static_hint(
                "Карточка подключения собирается автоматически из текущих настроек. "
                "По умолчанию токены и ключи в экспорт не попадают.",
                variant="warning",
            )
        )
        layout.addWidget(
            self._static_hint(
                "ChatGPT connector JSON описывает внешний MCP endpoint и его OAuth-модель. "
                "Responses API JSON нужен для прямого вызова MCP из OpenAI API.",
            )
        )

        self.content_layout.addWidget(section)

    def _section_frame(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("SettingsSection")

        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        layout.addWidget(title_label)
        return frame, layout

    def _hint_label(self, *, variant: str | None = None) -> QLabel:
        label = QLabel("")
        label.setObjectName("SectionHint")
        label.setWordWrap(True)
        if variant:
            label.setProperty("variant", variant)
        return label

    def _static_hint(self, text: str, *, variant: str | None = None) -> QLabel:
        label = self._hint_label(variant=variant)
        label.setText(text)
        return label

    def _auth_mode_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.addItem("Без авторизации", "none")
        combo.addItem("Bearer token", "bearer")
        return combo

    def _register_validation_widget(self, field_name: str, widget) -> None:
        self._validation_widgets[field_name] = widget

    def _load_into_form(self, settings: IntegrationSettings) -> None:
        self._settings = self._settings_service.normalize(settings)

        general = self._settings.general
        self.integration_enabled_checkbox.setChecked(general.integration_enabled)
        self.use_local_api_checkbox.setChecked(general.use_local_api)
        self.auto_connect_checkbox.setChecked(general.auto_connect_on_startup)
        self.test_mode_checkbox.setChecked(general.test_mode)

        local_api = self._settings.local_api
        self.local_api_host_input.setText(local_api.local_api_host)
        self.local_api_port_input.setValue(local_api.local_api_port)
        self.local_api_base_url_input.setText(local_api.local_api_base_url_override)
        self.local_api_auth_mode_input.setCurrentIndex(max(0, self.local_api_auth_mode_input.findData(local_api.local_api_auth_mode)))

        mcp = self._settings.mcp
        self.mcp_enabled_checkbox.setChecked(mcp.mcp_enabled)
        self.mcp_host_input.setText(mcp.mcp_host)
        self.mcp_port_input.setValue(mcp.mcp_port)
        self.mcp_path_input.setText(mcp.mcp_path)
        self.mcp_public_base_input.setText(mcp.public_https_base_url)
        self.mcp_tunnel_url_input.setText(mcp.tunnel_url)
        self.mcp_full_url_input.setText(mcp.full_mcp_url_override)
        _set_text_lines(self.mcp_allowed_hosts_input, mcp.allowed_hosts)
        _set_text_lines(self.mcp_allowed_origins_input, mcp.allowed_origins)
        self.mcp_auth_mode_input.setCurrentIndex(max(0, self.mcp_auth_mode_input.findData(mcp.mcp_auth_mode)))

        openai = self._settings.openai
        self.provider_input.setText(openai.provider)
        self.model_input.setText(openai.model)
        self.base_url_input.setText(openai.base_url)
        self.organization_input.setText(openai.organization_id)
        self.project_input.setText(openai.project_id)
        self.timeout_input.setValue(openai.timeout_seconds)

        auth = self._settings.auth
        self.auth_mode_input.setCurrentIndex(max(0, self.auth_mode_input.findData(auth.auth_mode)))
        self.access_token_input.set_value(auth.access_token)
        self.local_api_token_input.set_value(auth.local_api_bearer_token or local_api.local_api_bearer_token)
        self.mcp_token_input.set_value(auth.mcp_bearer_token or mcp.mcp_bearer_token)
        self.openai_api_key_input.set_value(auth.openai_api_key)

        self.settings_file_input.setText(str(self._settings_service.settings_path))
        self.log_file_input.setText(str(get_log_file()))
        self.mcp_log_file_input.setText(str(get_mcp_startup_log_file()))
        self.runtime_api_input.setText(self._runtime_api_url)
        self._sync_derived_fields()
        self._render_diagnostics(self._settings.diagnostics)
        self._render_runtime_state()
        self._clear_validation_state()

    def _collect_settings(self) -> IntegrationSettings:
        diagnostics = self._settings.diagnostics if isinstance(self._settings.diagnostics, DiagnosticsSettings) else DiagnosticsSettings()
        return IntegrationSettings(
            schema_version=self._settings.schema_version,
            general=GeneralSettings(
                integration_enabled=self.integration_enabled_checkbox.isChecked(),
                use_local_api=self.use_local_api_checkbox.isChecked(),
                auto_connect_on_startup=self.auto_connect_checkbox.isChecked(),
                test_mode=self.test_mode_checkbox.isChecked(),
            ),
            local_api=LocalApiSettings(
                local_api_host=self.local_api_host_input.text(),
                local_api_port=int(self.local_api_port_input.value()),
                local_api_base_url_override=self.local_api_base_url_input.text(),
                local_api_auth_mode=str(self.local_api_auth_mode_input.currentData()),
                local_api_bearer_token=self.local_api_token_input.value(),
            ),
            mcp=McpSettings(
                mcp_enabled=self.mcp_enabled_checkbox.isChecked(),
                mcp_host=self.mcp_host_input.text(),
                mcp_port=int(self.mcp_port_input.value()),
                mcp_path=self.mcp_path_input.text(),
                public_https_base_url=self.mcp_public_base_input.text(),
                tunnel_url=self.mcp_tunnel_url_input.text(),
                full_mcp_url_override=self.mcp_full_url_input.text(),
                allowed_hosts=_read_text_lines(self.mcp_allowed_hosts_input),
                allowed_origins=_read_text_lines(self.mcp_allowed_origins_input),
                mcp_auth_mode=str(self.mcp_auth_mode_input.currentData()),
                mcp_bearer_token=self.mcp_token_input.value(),
            ),
            openai=OpenAISettings(
                provider=self.provider_input.text().strip(),
                model=self.model_input.text().strip(),
                base_url=self.base_url_input.text(),
                organization_id=self.organization_input.text().strip(),
                project_id=self.project_input.text().strip(),
                timeout_seconds=int(self.timeout_input.value()),
            ),
            auth=AuthSettings(
                auth_mode=str(self.auth_mode_input.currentData()),
                access_token=self.access_token_input.value(),
                local_api_bearer_token=self.local_api_token_input.value(),
                mcp_bearer_token=self.mcp_token_input.value(),
                openai_api_key=self.openai_api_key_input.value(),
            ),
            diagnostics=diagnostics,
        )

    def _current_form_settings(self) -> IntegrationSettings:
        return self._settings_service.normalize(self._collect_settings())

    def _sync_derived_fields(self) -> None:
        settings = self._current_form_settings()
        self.runtime_local_api_url_input.setText(settings.local_api.runtime_local_api_url)
        self.local_api_health_url_input.setText(settings.local_api.local_api_health_url)
        self.effective_local_api_url_input.setText(settings.local_api.effective_local_api_url)
        self.mcp_local_url_input.setText(settings.mcp.local_mcp_url)
        self.mcp_public_endpoint_input.setText(settings.mcp.derived_public_mcp_url)
        self.mcp_tunnel_endpoint_input.setText(settings.mcp.derived_tunnel_mcp_url)
        self.mcp_effective_url_input.setText(settings.mcp.effective_mcp_url)
        self.mcp_resolved_hosts_input.setText("\n".join(settings.mcp.resolved_allowed_hosts))
        self.mcp_resolved_origins_input.setText("\n".join(settings.mcp.resolved_allowed_origins))
        self._update_hints(settings)
        self._update_restart_hint(settings)
        self._clear_validation_state()

    def _update_hints(self, settings: IntegrationSettings) -> None:
        general_hints: list[str] = []
        if not settings.general.integration_enabled:
            general_hints.append("Интеграция отключена. Проверки OpenAI и внешний MCP будут пропускаться.")
        if settings.general.auto_connect_on_startup and not settings.mcp.mcp_enabled:
            general_hints.append("Автоподключение включено, но MCP выключен.")
        self.general_hint_label.setText(" ".join(general_hints))

        local_api_hints: list[str] = []
        if not settings.general.use_local_api:
            local_api_hints.append("Локальный API отключён в интеграции. Для внешних клиентов останется только override URL.")
        if settings.local_api.local_api_auth_mode == "bearer" and not self.local_api_token_input.value():
            local_api_hints.append("Для bearer-режима локального API нужен токен.")
        self.local_api_hint_label.setText(" ".join(local_api_hints))

        mcp_hints: list[str] = []
        if not settings.mcp.mcp_enabled:
            mcp_hints.append("MCP выключен. Включите его для подключения GPT-агента.")
        if settings.mcp.mcp_auth_mode == "bearer" and not self.mcp_token_input.value():
            mcp_hints.append("Для bearer-режима MCP нужен токен.")
        if settings.mcp.tunnel_url or settings.mcp.public_https_base_url or settings.mcp.full_mcp_url_override:
            mcp_hints.append("Host и Origin для внешнего MCP URL будут разрешены автоматически.")
        self.mcp_hint_label.setText(" ".join(mcp_hints))

        if settings.mcp.full_mcp_url_override:
            self.mcp_external_hint_label.setProperty("variant", "")
            self.mcp_external_hint_label.setText("Используется полный override URL. Его и нужно вставлять в ChatGPT.")
        elif settings.mcp.public_https_base_url:
            self.mcp_external_hint_label.setProperty("variant", "")
            self.mcp_external_hint_label.setText("Итоговый MCP URL собирается из Public HTTPS Base URL и path MCP.")
        elif settings.mcp.tunnel_url:
            self.mcp_external_hint_label.setProperty("variant", "")
            self.mcp_external_hint_label.setText("Итоговый MCP URL собирается из Tunnel URL и path MCP.")
        else:
            self.mcp_external_hint_label.setProperty("variant", "warning")
            self.mcp_external_hint_label.setText(
                "Внешний HTTPS URL не задан. Для ChatGPT на телефоне localhost не подойдёт. "
                "Укажите Public HTTPS Base URL, Tunnel URL или Full MCP URL override."
            )
        host_header_issue = "Host header" in (settings.diagnostics.external_message or "") or "Host header" in (settings.diagnostics.mcp_message or "")
        if host_header_issue:
            self.mcp_external_hint_label.setProperty("variant", "error")
            self.mcp_external_hint_label.setText(
                "MCP runtime отклоняет внешний Host header. Нужно разрешить host из Tunnel URL / external domain и перезапустить MCP сервер."
            )
        self.style().unpolish(self.mcp_external_hint_label)
        self.style().polish(self.mcp_external_hint_label)

        auth_hints = ["Секреты пока хранятся в settings.json без системного шифрования."]
        if self.auth_mode_input.currentData() == "bearer" and not self.access_token_input.value():
            auth_hints.append("Общий bearer-режим включён, но access token пустой.")
        self.auth_hint_label.setText(" ".join(auth_hints))

        openai_hints: list[str] = []
        if not self.openai_api_key_input.value() and not self.access_token_input.value():
            openai_hints.append("Для проверки OpenAI-compatible endpoint нужен API key или access token.")
        self.openai_hint_label.setText(" ".join(openai_hints))

    def _update_restart_hint(self, settings: IntegrationSettings) -> None:
        if self._runtime_signature(settings) != self._runtime_signature(self._runtime_reference):
            self.restart_hint_label.setText(
                "Изменены host, port, path или токены. Для уже запущенных процессов может потребоваться перезапуск приложения или MCP сервера."
            )
        else:
            self.restart_hint_label.setText("")

    def _runtime_signature(self, settings: IntegrationSettings) -> tuple:
        return (
            settings.local_api.local_api_host,
            settings.local_api.local_api_port,
            settings.local_api.local_api_auth_mode,
            settings.local_api.local_api_bearer_token,
            settings.mcp.mcp_host,
            settings.mcp.mcp_port,
            settings.mcp.mcp_path,
            settings.mcp.mcp_auth_mode,
            settings.mcp.mcp_bearer_token,
            settings.mcp.allowed_hosts,
            settings.mcp.allowed_origins,
        )

    def _render_runtime_state(self) -> None:
        if self._mcp_controller is None:
            self.runtime_mcp_status_input.setText("Управление MCP runtime недоступно")
            self.runtime_mcp_url_input.setText("")
            self.start_mcp_button.setEnabled(False)
            self.restart_mcp_button.setEnabled(False)
            self.stop_mcp_button.setEnabled(False)
            return
        state = self._mcp_controller.state
        self.runtime_mcp_status_input.setText(state.message)
        self.runtime_mcp_url_input.setText(state.runtime_url if state.running else "")
        self.start_mcp_button.setEnabled(not state.running)
        self.restart_mcp_button.setEnabled(state.running)
        self.stop_mcp_button.setEnabled(state.running)

    def _render_diagnostics(self, diagnostics: DiagnosticsSettings) -> None:
        self.overall_status_input.setText(STATUS_LABELS.get(diagnostics.overall_status, STATUS_LABELS["not_tested"]))
        self.local_api_status_input.setText(STATUS_LABELS.get(diagnostics.local_api_status, STATUS_LABELS["not_tested"]))
        self.mcp_status_input.setText(STATUS_LABELS.get(diagnostics.mcp_status, STATUS_LABELS["not_tested"]))
        self.external_status_input.setText(STATUS_LABELS.get(diagnostics.external_status, STATUS_LABELS["not_tested"]))
        self.openai_status_input.setText(STATUS_LABELS.get(diagnostics.openai_status, STATUS_LABELS["not_tested"]))
        self.last_local_api_check_input.setText(diagnostics.last_local_api_check or STATUS_LABELS["not_tested"])
        self.last_mcp_check_input.setText(diagnostics.last_mcp_check or STATUS_LABELS["not_tested"])
        self.last_external_check_input.setText(diagnostics.last_external_endpoint_check or STATUS_LABELS["not_tested"])
        self.last_openai_check_input.setText(diagnostics.last_openai_check or STATUS_LABELS["not_tested"])
        self.last_full_check_input.setText(diagnostics.last_full_check or STATUS_LABELS["not_tested"])
        self.local_api_message_label.setText(diagnostics.local_api_message)
        self.mcp_message_label.setText(diagnostics.mcp_message)
        self.external_message_label.setText(diagnostics.external_message)
        self.openai_message_label.setText(diagnostics.openai_message)
        self.warnings_output.setPlainText("\n".join(diagnostics.last_warnings))
        self.errors_output.setPlainText("\n".join(diagnostics.last_errors))

    def _apply(self) -> None:
        self._persist(close_after=False)

    def _save(self) -> None:
        self._persist(close_after=True)

    def _persist(self, *, close_after: bool) -> None:
        saved = self._save_form_settings()
        if saved is None:
            return

        restart_required = self._runtime_signature(saved) != self._runtime_signature(self._runtime_reference)
        self._load_into_form(saved)
        self.settings_saved.emit(saved)
        message = "Настройки сохранены." if close_after else "Настройки применены."
        if restart_required:
            message += " Для применения части параметров уже запущенным процессам нужен перезапуск."
        self._set_status(message, tone="warning" if restart_required else "success")
        if close_after:
            self.accept()

    def _reset_form(self) -> None:
        answer = QMessageBox.question(
            self,
            "Сброс настроек",
            "Вернуть настройки интеграции к значениям по умолчанию? Несохранённые изменения будут потеряны.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._load_into_form(IntegrationSettings.defaults())
        self._set_status("В форму подставлены значения по умолчанию. Нажмите «Применить» или «Сохранить».", tone="success")

    def _apply_runtime_state_to_diagnostics(self, state, *, status_override: str | None = None) -> None:
        settings = self._save_form_settings(show_errors=False)
        if settings is None:
            self._render_runtime_state()
            return

        result = ConnectionCheckResult(
            target="mcp",
            status=status_override or ("success" if state.running else "failed"),
            message=state.message,
            checked_at=utc_now_iso(),
            errors=(state.error,) if state.error else (),
        )
        self._apply_diagnostics_result(settings, "mcp", result)

    def _current_mcp_error_text(self) -> str:
        if self._mcp_controller is None:
            return "Управление MCP runtime недоступно."
        return (self._mcp_controller.state.details or self._mcp_controller.state.error or "Техническая ошибка запуска MCP пока отсутствует.").strip()

    def _start_mcp_runtime(self) -> None:
        if self._mcp_controller is None:
            self._set_status("Управление MCP runtime недоступно в этом запуске.", tone="error")
            return
        settings = self._save_form_settings()
        if settings is None:
            return
        if not settings.mcp.mcp_enabled:
            self._set_status("Сначала включите MCP в настройках.", tone="warning")
            return
        needs_public_tunnel = not settings.mcp.full_mcp_url_override and not settings.mcp.public_https_base_url
        if self._tunnel_controller is not None and needs_public_tunnel:
            tunnel_state = self._tunnel_controller.start(settings)
            settings = self._settings_service.update_section(
                "mcp",
                {"tunnel_url": tunnel_state.public_url if tunnel_state.running else ""},
                settings=settings,
                persist=True,
            )
        state = self._mcp_controller.restart(settings) if self._mcp_controller.state.running else self._mcp_controller.start(settings)
        self._render_runtime_state()
        self._apply_runtime_state_to_diagnostics(state)
        if state.running:
            self._load_into_form(settings)
            if self._tunnel_controller is not None and self._tunnel_controller.state.running:
                self._set_status(f"{state.message}\nTunnel: {self._tunnel_controller.state.public_url}", tone="success")
            else:
                self._set_status(state.message, tone="success")
        else:
            self._set_status(state.message, tone="error")

    def _restart_mcp_runtime(self) -> None:
        if self._mcp_controller is None:
            self._set_status("Управление MCP runtime недоступно в этом запуске.", tone="error")
            return
        if self._tunnel_controller is not None:
            self._tunnel_controller.stop()
        self._mcp_controller.stop()
        self._render_runtime_state()
        self._start_mcp_runtime()

    def _stop_mcp_runtime(self) -> None:
        if self._mcp_controller is None:
            self._set_status("Управление MCP runtime недоступно в этом запуске.", tone="error")
            return
        if self._tunnel_controller is not None:
            self._tunnel_controller.stop()
        state = self._mcp_controller.stop()
        settings = self._settings_service.save(
            self._settings_service.update_section("mcp", {"tunnel_url": ""}, settings=self._collect_settings(), persist=False)
        )
        self._render_runtime_state()
        self._apply_runtime_state_to_diagnostics(state, status_override="warning")
        self._load_into_form(settings)
        self._set_status("MCP и tunnel остановлены.", tone="success")

    def _open_mcp_log(self) -> None:
        path = get_mcp_startup_log_file()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self._set_status(f"Открыт MCP startup log: {path.name}", tone="success")

    def _copy_mcp_startup_error(self) -> None:
        QGuiApplication.clipboard().setText(self._current_mcp_error_text())
        self._set_status("Техническая ошибка запуска MCP скопирована в буфер обмена.", tone="success")

    def _test_connections(self) -> None:
        settings = self._save_form_settings()
        if settings is None:
            return
        summary = self._settings_service.test_connections(settings)
        updated = self._settings_service.apply_test_summary(settings, summary)
        self._reload_saved_settings(updated)
        tone = "success" if summary.overall_status not in {"failed", "warning"} else "warning" if summary.overall_status == "warning" else "error"
        self._set_status("Полная проверка соединений завершена.", tone=tone)
        QMessageBox.information(self, "Проверка соединений", self._format_summary(summary))

    def _run_single_test(self, target: str):
        settings = self._save_form_settings()
        if settings is None:
            return
        result = self._settings_service.test_target(settings, target)
        self._apply_diagnostics_result(settings, target, result)
        tone = "success" if result.status == "success" else "warning" if result.status == "skipped" else "error"
        self._set_status(result.message, tone=tone)
        return result

    def _generate_token_for(self, field: SecretField, label: str) -> None:
        token = self._settings_service.generate_token()
        field.set_value(token)
        self._sync_derived_fields()
        self._set_status(f"Сгенерирован новый bearer token для {label}. Если процесс уже запущен, может потребоваться его перезапуск.", tone="warning")

    def _open_chatgpt_connect_dialog(self) -> None:
        self._connect_dialog = ChatGPTConnectDialog(
            settings=self._current_form_settings(),
            runtime_api_url=self._runtime_api_url,
            runtime_state=self._mcp_controller.state if self._mcp_controller is not None else None,
            settings_provider=self._current_form_settings,
            test_target_callback=self._run_single_test,
            parent=self,
        )
        self._connect_dialog.show()
        self._connect_dialog.raise_()
        self._connect_dialog.activateWindow()

    def open_chatgpt_wizard(self) -> None:
        self._open_chatgpt_connect_dialog()

    def _normalized_form_settings(self) -> IntegrationSettings:
        return self._settings_service.normalize(self._collect_settings())

    def _connection_card_text(self, settings: IntegrationSettings | None = None) -> str:
        normalized = settings or self._normalized_form_settings()
        return build_connection_card(
            normalized,
            runtime_api_url=self._runtime_api_url,
            runtime_state=self._mcp_controller.state if self._mcp_controller is not None else None,
            include_secrets=self.include_secrets_checkbox.isChecked(),
        )

    def _choose_export_path(self, title: str, default_path: Path, file_filter: str) -> str | None:
        path, _ = QFileDialog.getSaveFileName(self, title, str(default_path), file_filter)
        return path or None

    def _write_export_file(self, path: str | Path, payload: str, success_message: str) -> None:
        target_path = Path(path)
        target_path.write_text(payload, encoding="utf-8")
        self._set_status(success_message.format(path=target_path), tone="success")

    def _export_payload_file(
        self,
        *,
        title: str,
        default_path: Path,
        file_filter: str,
        payload: str,
        success_message: str,
    ) -> None:
        path = self._choose_export_path(title, default_path, file_filter)
        if not path:
            return
        self._write_export_file(path, payload, success_message)

    def _copy_connection_card(self) -> None:
        text = self._connection_card_text()
        QGuiApplication.clipboard().setText(text)
        self._set_status("Карточка подключения скопирована в буфер обмена.", tone="success")

    def _export_connection_card(self) -> None:
        default_path = self._settings_service.settings_path.parent / "GPT_MCP_CONNECTION_CARD.txt"
        self._export_payload_file(
            title="??????? ???????? ???????????",
            default_path=default_path,
            file_filter="Text Files (*.txt)",
            payload=self._connection_card_text(),
            success_message="???????? ??????????? ??????????????: {path}",
        )

    def _export_chatgpt_connector_payload(self) -> None:
        settings = self._normalized_form_settings()
        default_path = self._settings_service.settings_path.parent / "chatgpt-connector.json"
        mode = resolve_connector_auth_mode(settings)
        self._export_payload_file(
            title="??????? ChatGPT connector JSON",
            default_path=default_path,
            file_filter="JSON Files (*.json)",
            payload=build_chatgpt_connector_payload(settings),
            success_message=f"ChatGPT connector JSON ????????????? ({mode}): {{path}}",
        )

    def _export_responses_payload(self) -> None:
        settings = self._normalized_form_settings()
        default_path = self._settings_service.settings_path.parent / "responses-api-mcp.json"
        self._export_payload_file(
            title="??????? Responses API JSON",
            default_path=default_path,
            file_filter="JSON Files (*.json)",
            payload=build_responses_api_payload(settings),
            success_message="Responses API JSON ?????????????: {path}",
        )

    def _export_settings_snapshot(self) -> None:
        default_path = self._settings_service.settings_path.parent / "integration-settings.export.json"
        settings = self._normalized_form_settings()
        self._export_payload_file(
            title="??????? ???????? ??????????",
            default_path=default_path,
            file_filter="JSON Files (*.json)",
            payload=build_settings_export(settings, include_secrets=self.include_secrets_checkbox.isChecked()),
            success_message="????????? ?????????? ??????????????: {path}",
        )

    def _open_connection_docs(self) -> None:
        path = get_mcp_setup_doc_path()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self._set_status(f"Открыта инструкция подключения: {path.name}", tone="success")

    def _format_summary(self, summary: ConnectionTestSummary) -> str:
        return (
            f"Локальный API: {summary.local_api.message}\n"
            f"MCP: {summary.mcp.message}\n"
            f"Внешний endpoint: {summary.external.message}\n"
            f"OpenAI: {summary.openai.message}"
        )

    def _show_validation_errors(self, errors: dict[str, str]) -> None:
        self._clear_validation_state()
        details = ["Исправьте следующие поля:"]
        for field_name, message in errors.items():
            label = FIELD_LABELS.get(field_name, field_name)
            details.append(f"- {label}: {message}")
            widget = self._validation_widgets.get(field_name)
            if widget is not None:
                widget.setProperty("invalid", True)
                self.style().unpolish(widget)
                self.style().polish(widget)
        text = "\n".join(details)
        self._set_status(text, tone="error")
        QMessageBox.warning(self, "Ошибки в настройках", text)

    def _clear_validation_state(self) -> None:
        for widget in self._validation_widgets.values():
            if widget.property("invalid"):
                widget.setProperty("invalid", False)
                self.style().unpolish(widget)
                self.style().polish(widget)

    def _save_form_settings(self, *, show_errors: bool = True) -> IntegrationSettings | None:
        try:
            return self._settings_service.save(self._collect_settings())
        except SettingsValidationError as exc:
            if show_errors:
                self._show_validation_errors(exc.errors)
            return None

    def _reload_saved_settings(self, settings: IntegrationSettings) -> IntegrationSettings:
        saved = self._settings_service.save(settings)
        self._load_into_form(saved)
        return saved

    def _apply_diagnostics_result(
        self,
        settings: IntegrationSettings,
        target: str,
        result: ConnectionCheckResult,
    ) -> IntegrationSettings:
        updated = self._settings_service.apply_test_result(settings, target, result)
        return self._reload_saved_settings(updated)

    def _set_status(self, message: str, *, tone: str = "info") -> None:
        _apply_status_label_state(self, self.status_label, message, tone=tone)
