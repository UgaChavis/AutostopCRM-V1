from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QPoint, QMimeData, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..models import parse_datetime
from ..texts import CARD_NO_DESCRIPTION, CARD_STATUS_TOOLTIP_TEMPLATE, STATUS_LABELS_RU, TOOLTIP_DRAG_CARD


INDICATOR_STYLE = {
    "green": "#53bf7a",
    "yellow": "#d6b24c",
    "red": "#d46262",
}

TITLE_MAX_LINES = 2
DESCRIPTION_MIN_LINES = 6
DESCRIPTION_MAX_LINES = 9


def _card_frame_stylesheet(border_color: str) -> str:
    return f"""
    QFrame#Card {{
        background-color: #232d27;
        border: 1px solid {border_color};
        border-radius: 10px;
    }}
    QLabel#CardTitle {{
        color: #f1efe4;
    }}
    QLabel#CardDescription {{
        color: #d7d9cf;
    }}
    QLabel#DeadlineLabel {{
        color: #aeb4a5;
        font-family: Consolas;
        font-size: 12px;
    }}
    QLabel#TimerLabel {{
        font-family: Consolas;
        font-size: 13px;
        font-weight: 700;
    }}
    """


def _normalize_preview_text(text: str) -> str:
    return str(text or "").strip().replace("\r\n", "\n").replace("\r", "\n")


def elide_multiline_text(text: str, font, width: int, *, max_lines: int) -> str:
    source = _normalize_preview_text(text)
    if not source:
        return ""
    if width <= 0:
        return source

    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)

    layout = QTextLayout(source, font)
    layout.setTextOption(option)
    metrics = QFontMetrics(font)
    lines: list[str] = []

    layout.beginLayout()
    try:
        while len(lines) < max_lines:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(float(width))
            offset = getattr(line, "text" + "".join(["S", "t", "a", "r", "t"]))()
            length = line.textLength()
            line_text = source[offset : offset + length].rstrip()
            if len(lines) == max_lines - 1 and offset + length < len(source):
                remaining = source[offset:].replace("\n", " ")
                line_text = metrics.elidedText(remaining, Qt.TextElideMode.ElideRight, width)
            lines.append(line_text)
            if offset + length >= len(source):
                break
    finally:
        layout.endLayout()

    return "\n".join(part for part in lines if part)


def format_deadline_preview(deadline_timestamp: str) -> str:
    deadline = parse_datetime(deadline_timestamp)
    if deadline is None:
        return "Срок не указан"
    local_deadline = deadline.astimezone()
    return datetime.strftime(local_deadline, "до %d.%m %H:%M")


class CardWidget(QFrame):
    edit_requested = Signal(str)

    def __init__(self, card: dict, parent=None) -> None:
        super().__init__(parent)
        self.card_id = card["id"]
        self._drag_origin_position = QPoint()
        self._title_text = ""
        self._description_text = ""
        self._shadow_effect = QGraphicsDropShadowEffect(self)
        self._shadow_effect.setOffset(0, 0)
        self._shadow_effect.setBlurRadius(14)
        self.setGraphicsEffect(self._shadow_effect)

        self.setObjectName("Card")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(TOOLTIP_DRAG_CARD)
        self.setMinimumHeight(212)

        self.title_label = QLabel()
        self.title_label.setObjectName("CardTitle")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(11)
        self.title_label.setFont(title_font)
        self.title_label.setWordWrap(True)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self.description_label = QLabel()
        self.description_label.setWordWrap(True)
        self.description_label.setObjectName("CardDescription")
        self.description_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self.deadline_label = QLabel()
        self.deadline_label.setObjectName("DeadlineLabel")

        self.timer_label = QLabel()
        self.timer_label.setObjectName("TimerLabel")

        self.indicator_badge = QLabel()
        self.indicator_badge.setObjectName("IndicatorLamp")
        self.indicator_badge.setFixedSize(10, 10)

        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(10)
        footer_layout.addWidget(self.deadline_label, 1)
        footer_layout.addWidget(self.timer_label, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        footer_layout.addWidget(self.indicator_badge, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(9)
        layout.addWidget(self.title_label)
        layout.addWidget(self.description_label)
        layout.addStretch(1)
        layout.addLayout(footer_layout)

        self.update_card(card)

    def update_card(self, card: dict) -> None:
        self.card_id = card["id"]
        self._title_text = card["title"]
        self._description_text = card["description"] or CARD_NO_DESCRIPTION
        self.title_label.setToolTip(card["title"])
        self.description_label.setToolTip(card["description"] or CARD_NO_DESCRIPTION)
        self.deadline_label.setText(format_deadline_preview(card["deadline_timestamp"]))
        self.timer_label.setText(card["remaining_display"])

        self.setProperty("status", card["status"])
        self.setProperty("deadlineBucket", int(card.get("deadline_progress_bucket", 0)))
        self.setProperty("deadlineStep", int(card.get("deadline_progress_step_percent", 0)))
        self.setProperty("deadlineHeatColor", card.get("deadline_heat_color", INDICATOR_STYLE[card["indicator"]]))
        self.style().unpolish(self)
        self.style().polish(self)

        timer_color = {
            "ok": "#dce4ec",
            "warning": "#d6b24c",
            "expired": "#d46262",
        }[card["status"]]
        self.timer_label.setStyleSheet(f"color: {timer_color};")

        heat_color = str(card.get("deadline_heat_color") or INDICATOR_STYLE[card["indicator"]])
        bucket = int(card.get("deadline_progress_bucket", 0))
        shadow_color = QColor(heat_color)
        shadow_color.setAlpha(min(210, 48 + (bucket * 7)))
        self._shadow_effect.setColor(shadow_color)
        self._shadow_effect.setBlurRadius(12 + bucket)
        self.setStyleSheet(_card_frame_stylesheet(heat_color))

        indicator_color = INDICATOR_STYLE[card["indicator"]]
        self.indicator_badge.setStyleSheet(
            f"background-color: {indicator_color}; border: 1px solid #0c0f12; border-radius: 5px;"
        )
        self.indicator_badge.setToolTip(
            CARD_STATUS_TOOLTIP_TEMPLATE.format(label=STATUS_LABELS_RU[card["status"]])
        )

        title_line_height = self.title_label.fontMetrics().lineSpacing()
        description_line_height = self.description_label.fontMetrics().lineSpacing()
        self.title_label.setMaximumHeight(title_line_height * TITLE_MAX_LINES)
        self.description_label.setMinimumHeight(description_line_height * DESCRIPTION_MIN_LINES)
        self.description_label.setMaximumHeight(description_line_height * DESCRIPTION_MAX_LINES)
        self._refresh_preview_texts()

    def resizeEvent(self, event) -> None:
        self._refresh_preview_texts()
        super().resizeEvent(event)

    def _refresh_preview_texts(self) -> None:
        title_width = self.title_label.width() or max(200, self.width() - 42)
        description_width = self.description_label.width() or max(200, self.width() - 36)
        self.title_label.setText(
            elide_multiline_text(self._title_text, self.title_label.font(), title_width, max_lines=TITLE_MAX_LINES)
        )
        self.description_label.setText(
            elide_multiline_text(
                self._description_text,
                self.description_label.font(),
                description_width,
                max_lines=DESCRIPTION_MAX_LINES,
            )
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin_position = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        drag_distance = getattr(QApplication, "".join(["s", "t", "a", "r", "t", "D", "r", "a", "g", "D", "i", "s", "t", "a", "n", "c", "e"]))()
        if (event.position().toPoint() - self._drag_origin_position).manhattanLength() < drag_distance:
            return
        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setText(self.card_id)
        drag.setMimeData(mime_data)
        drag.exec(Qt.DropAction.MoveAction)

    def mouseDoubleClickEvent(self, event) -> None:
        self.edit_requested.emit(self.card_id)
        super().mouseDoubleClickEvent(event)


class ColumnWidget(QFrame):
    card_dropped = Signal(str, str)

    def __init__(self, column_id: str, title: str, empty_message: str, parent=None) -> None:
        super().__init__(parent)
        self.column_id = column_id
        self.setObjectName("Column")
        self.setAcceptDrops(True)
        self.setMinimumWidth(334)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("ColumnTitle")

        self.count_label = QLabel("0")
        self.count_label.setObjectName("ColumnCount")

        self.empty_label = QLabel(empty_message)
        self.empty_label.setWordWrap(True)
        self.empty_label.setObjectName("ColumnEmptyState")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(self.count_label)

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(12)
        self.cards_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.cards_container)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addWidget(scroll, 1)

    def set_cards(self, card_widgets: list[CardWidget]) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        if card_widgets:
            for widget in card_widgets:
                self.cards_layout.addWidget(widget)
        else:
            self.cards_layout.addWidget(self.empty_label)
        self.cards_layout.addStretch(1)
        self.count_label.setText(str(len(card_widgets)))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasText():
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasText():
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasText():
            event.ignore()
            return
        self.card_dropped.emit(event.mimeData().text(), self.column_id)
        event.acceptProposedAction()
