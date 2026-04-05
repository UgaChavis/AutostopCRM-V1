from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..models import CARD_DESCRIPTION_LIMIT, DEFAULT_DEADLINE_TOTAL_SECONDS, split_seconds_to_days_hours
from ..texts import (
    BUTTON_ARCHIVE,
    BUTTON_BACK,
    BUTTON_CANCEL,
    BUTTON_FINISH,
    BUTTON_NEXT,
    BUTTON_SAVE,
    CARD_DEADLINE_INVALID_MESSAGE,
    CARD_DESCRIPTION_LONG_MESSAGE,
    CARD_DIALOG_CREATE_TITLE,
    CARD_DIALOG_EDIT_TITLE,
    CARD_FIELD_DEADLINE,
    CARD_FIELD_DEADLINE_DAYS,
    CARD_FIELD_DEADLINE_HOURS,
    CARD_FIELD_DESCRIPTION,
    CARD_FIELD_TITLE,
    CARD_PLACEHOLDER_DESCRIPTION,
    CARD_PLACEHOLDER_TITLE,
    CARD_TITLE_EMPTY_MESSAGE,
    CARD_TITLE_LONG_MESSAGE,
    COLUMN_DIALOG_TITLE,
    COLUMN_FIELD_LABEL,
    COLUMN_LABEL_EMPTY_MESSAGE,
    COLUMN_LABEL_LONG_MESSAGE,
    COLUMN_PLACEHOLDER_LABEL,
    ERROR_VALIDATION_TITLE,
    HELP_DIALOG_TITLE,
    HELP_PROGRESS_TEMPLATE,
    ONBOARDING_PAGES,
    TOOLTIP_ARCHIVE,
)


class CardDialog(QDialog):
    def __init__(self, *, title: str, initial: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        is_create = title == "create"
        is_edit = title == "edit"
        if is_create:
            title = CARD_DIALOG_CREATE_TITLE
        elif is_edit:
            title = CARD_DIALOG_EDIT_TITLE
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(460, 440)

        initial = initial or {}
        default_days, default_hours = split_seconds_to_days_hours(DEFAULT_DEADLINE_TOTAL_SECONDS)
        initial_days = default_days
        initial_hours = default_hours
        if initial:
            initial_days, initial_hours = split_seconds_to_days_hours(int(initial.get("remaining_seconds", 0)))
        self._initial_deadline = (initial_days, initial_hours)
        self._is_edit = is_edit
        self.archive_requested = False

        self.title_input = QLineEdit(initial.get("title", ""))
        self.title_input.setPlaceholderText(CARD_PLACEHOLDER_TITLE)

        self.description_input = QPlainTextEdit(initial.get("description", ""))
        self.description_input.setPlaceholderText(CARD_PLACEHOLDER_DESCRIPTION)

        self.deadline_days_input = QSpinBox()
        self.deadline_days_input.setRange(0, 365)
        self.deadline_days_input.setValue(initial_days)

        self.deadline_hours_input = QSpinBox()
        self.deadline_hours_input.setRange(0, 23)
        self.deadline_hours_input.setValue(initial_hours)

        deadline_layout = QHBoxLayout()
        deadline_layout.setContentsMargins(0, 0, 0, 0)
        deadline_layout.setSpacing(10)
        deadline_layout.addWidget(QLabel(CARD_FIELD_DEADLINE_DAYS))
        deadline_layout.addWidget(self.deadline_days_input, 1)
        deadline_layout.addWidget(QLabel(CARD_FIELD_DEADLINE_HOURS))
        deadline_layout.addWidget(self.deadline_hours_input, 1)

        form_layout = QVBoxLayout()
        form_layout.addWidget(QLabel(CARD_FIELD_TITLE))
        form_layout.addWidget(self.title_input)
        form_layout.addWidget(QLabel(CARD_FIELD_DESCRIPTION))
        form_layout.addWidget(self.description_input)
        form_layout.addWidget(QLabel(CARD_FIELD_DEADLINE))
        form_layout.addLayout(deadline_layout)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if save_button is not None:
            save_button.setText(BUTTON_SAVE)
        if cancel_button is not None:
            cancel_button.setText(BUTTON_CANCEL)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(10)
        if is_edit:
            archive_button = QPushButton(BUTTON_ARCHIVE)
            archive_button.setToolTip(TOOLTIP_ARCHIVE)
            archive_button.clicked.connect(self._request_archive)
            footer.addWidget(archive_button)
        footer.addStretch(1)
        footer.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addLayout(form_layout)
        layout.addStretch(1)
        layout.addLayout(footer)

    def payload(self) -> dict:
        payload = {
            "title": self.title_input.text().strip(),
            "description": self.description_input.toPlainText().strip(),
        }
        current_deadline = (self.deadline_days_input.value(), self.deadline_hours_input.value())
        if not self._is_edit or current_deadline != self._initial_deadline:
            payload["deadline"] = {
                "days": current_deadline[0],
                "hours": current_deadline[1],
            }
        return payload

    def accept(self) -> None:
        title = self.title_input.text().strip()
        description = self.description_input.toPlainText().strip()
        if not title:
            QMessageBox.warning(self, ERROR_VALIDATION_TITLE, CARD_TITLE_EMPTY_MESSAGE)
            self.title_input.setFocus()
            return
        if len(title) > 120:
            QMessageBox.warning(self, ERROR_VALIDATION_TITLE, CARD_TITLE_LONG_MESSAGE)
            self.title_input.setFocus()
            return
        if len(description) > CARD_DESCRIPTION_LIMIT:
            QMessageBox.warning(
                self,
                ERROR_VALIDATION_TITLE,
                CARD_DESCRIPTION_LONG_MESSAGE.replace("5000", str(CARD_DESCRIPTION_LIMIT)),
            )
            self.description_input.setFocus()
            return
        if self.deadline_days_input.value() == 0 and self.deadline_hours_input.value() == 0:
            QMessageBox.warning(self, ERROR_VALIDATION_TITLE, CARD_DEADLINE_INVALID_MESSAGE)
            self.deadline_hours_input.setFocus()
            return
        super().accept()

    def _request_archive(self) -> None:
        self.archive_requested = True
        super().accept()


class ColumnDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(COLUMN_DIALOG_TITLE)
        self.setModal(True)
        self.resize(380, 160)

        self.label_input = QLineEdit()
        self.label_input.setPlaceholderText(COLUMN_PLACEHOLDER_LABEL)

        form_layout = QVBoxLayout()
        form_layout.addWidget(QLabel(COLUMN_FIELD_LABEL))
        form_layout.addWidget(self.label_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if save_button is not None:
            save_button.setText(BUTTON_SAVE)
        if cancel_button is not None:
            cancel_button.setText(BUTTON_CANCEL)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addLayout(form_layout)
        layout.addStretch(1)
        layout.addWidget(buttons)

    def payload(self) -> dict:
        return {"label": self.label_input.text().strip()}

    def accept(self) -> None:
        label = self.label_input.text().strip()
        if not label:
            QMessageBox.warning(self, ERROR_VALIDATION_TITLE, COLUMN_LABEL_EMPTY_MESSAGE)
            self.label_input.setFocus()
            return
        if len(label) > 40:
            QMessageBox.warning(self, ERROR_VALIDATION_TITLE, COLUMN_LABEL_LONG_MESSAGE)
            self.label_input.setFocus()
            return
        super().accept()


class OnboardingDialog(QDialog):
    def __init__(self, api_url: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(HELP_DIALOG_TITLE)
        self.setModal(True)
        self.resize(540, 360)

        self._pages = [(title, body.format(api_url=api_url)) for title, body in ONBOARDING_PAGES]
        self._index = 0

        self.stack = QStackedWidget()
        for title, body in self._pages:
            self.stack.addWidget(self._build_page(title, body))

        self.progress_label = QLabel()
        self.back_button = QPushButton(BUTTON_BACK)
        self.next_button = QPushButton(BUTTON_NEXT)
        self.finish_button = QPushButton(BUTTON_FINISH)
        self.back_button.clicked.connect(self._go_back)
        self.next_button.clicked.connect(self._go_next)
        self.finish_button.clicked.connect(self.accept)

        controls = QHBoxLayout()
        controls.addWidget(self.progress_label)
        controls.addStretch(1)
        controls.addWidget(self.back_button)
        controls.addWidget(self.next_button)
        controls.addWidget(self.finish_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        layout.addWidget(self.stack)
        layout.addLayout(controls)
        self._update_state()

    def _build_page(self, title: str, body: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setObjectName("OnboardingTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        title_label.setWordWrap(True)

        body_label = QLabel(body)
        body_label.setObjectName("OnboardingBody")
        body_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        body_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(body_label)
        layout.addStretch(1)
        return widget

    def _go_back(self) -> None:
        self._index = max(0, self._index - 1)
        self._update_state()

    def _go_next(self) -> None:
        self._index = min(len(self._pages) - 1, self._index + 1)
        self._update_state()

    def _update_state(self) -> None:
        self.stack.setCurrentIndex(self._index)
        self.progress_label.setText(
            HELP_PROGRESS_TEMPLATE.format(current=self._index + 1, total=len(self._pages))
        )
        self.back_button.setEnabled(self._index > 0)
        self.next_button.setVisible(self._index < len(self._pages) - 1)
        self.finish_button.setVisible(self._index == len(self._pages) - 1)
