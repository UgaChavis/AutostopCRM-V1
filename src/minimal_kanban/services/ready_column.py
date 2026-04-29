from __future__ import annotations

from typing import Any

from ..models import Column, normalize_text

READY_COLUMN_LABEL = "Готовые автомобили"
READY_CARD_TAG_LABEL = "Готов"
READY_CARD_TAG_COLOR = "green"
READY_COLUMN_SETTINGS_KEY = "ready_column_id"
LEGACY_READY_COLUMN_ID = "done"


def _next_column_id(columns: list[Column]) -> str:
    existing_ids = {column.id for column in columns}
    index = 1
    while True:
        column_id = f"column_{index}"
        if column_id not in existing_ids:
            return column_id
        index += 1


def _column_by_id(columns: list[Column], column_id: str) -> Column | None:
    return next((column for column in columns if column.id == column_id), None)


def _column_by_label(columns: list[Column], label: str) -> Column | None:
    normalized = label.casefold()
    return next((column for column in columns if column.label.casefold() == normalized), None)


def ensure_ready_column(columns: list[Column], settings: dict[str, Any]) -> tuple[str, bool]:
    changed = False
    configured_id = normalize_text(
        settings.get(READY_COLUMN_SETTINGS_KEY), default="", limit=128
    )

    configured_column = _column_by_id(columns, configured_id) if configured_id else None
    label_column = _column_by_label(columns, READY_COLUMN_LABEL)
    legacy_column = _column_by_id(columns, LEGACY_READY_COLUMN_ID)
    ready_column = configured_column or label_column or legacy_column

    if ready_column is None:
        ready_column = Column(
            id=_next_column_id(columns), label=READY_COLUMN_LABEL, position=len(columns)
        )
        columns.append(ready_column)
        changed = True
    elif ready_column.label != READY_COLUMN_LABEL:
        if label_column is not None and label_column.id != ready_column.id:
            ready_column = label_column
        else:
            ready_column.label = READY_COLUMN_LABEL
            changed = True

    if settings.get(READY_COLUMN_SETTINGS_KEY) != ready_column.id:
        settings[READY_COLUMN_SETTINGS_KEY] = ready_column.id
        changed = True

    for position, column in enumerate(columns):
        if column.position != position:
            column.position = position
            changed = True

    return ready_column.id, changed


def configured_ready_column_id(settings: dict[str, Any]) -> str:
    return normalize_text(settings.get(READY_COLUMN_SETTINGS_KEY), default="", limit=128)
