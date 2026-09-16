from __future__ import annotations

from ..models import Column

PARTS_STORE_COLUMN_ID = "parts_store"
PARTS_STORE_COLUMN_LABEL = "Магазин автозапчастей"


def ensure_parts_store_column(columns: list[Column]) -> bool:
    """Keep the dedicated parts store at the end without moving existing cards."""

    column = next((item for item in columns if item.id == PARTS_STORE_COLUMN_ID), None)
    changed = False
    if column is None:
        column = Column(
            id=PARTS_STORE_COLUMN_ID,
            label=PARTS_STORE_COLUMN_LABEL,
            position=len(columns),
        )
        columns.append(column)
        changed = True
    elif column.label != PARTS_STORE_COLUMN_LABEL:
        column.label = PARTS_STORE_COLUMN_LABEL
        changed = True

    if columns[-1] is not column:
        columns.remove(column)
        columns.append(column)
        changed = True
    for position, item in enumerate(columns):
        if item.position != position:
            item.position = position
            changed = True
    return changed
