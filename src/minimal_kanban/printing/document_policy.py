from __future__ import annotations

from .errors import PrintModuleError

LOCKED_TEMPLATE_DOCUMENT_TYPES = frozenset(
    {
        "completion_act",
        "technical_repair_order",
    }
)
TECHNICAL_REPAIR_ORDER_DESCRIPTION_MAX_CHARS = 2_000
TECHNICAL_REPAIR_ORDER_DESCRIPTION_MAX_LINES = 25


def is_template_locked(document_type: str) -> bool:
    return document_type in LOCKED_TEMPLATE_DOCUMENT_TYPES


def require_template_unlocked(document_type: str) -> None:
    if not is_template_locked(document_type):
        return
    if document_type == "completion_act":
        code = "completion_act_template_locked"
        message = "Для акта используется только встроенный стандартный шаблон."
    else:
        code = "technical_repair_order_template_locked"
        message = "Для технического заказ-наряда используется только встроенный шаблон."
    raise PrintModuleError(code, message, status_code=409)


def validate_document_description(document_type: str, description: object) -> None:
    if document_type != "technical_repair_order":
        return
    text = str(description or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    line_count = len(text.split("\n")) if text else 0
    if (
        len(text) <= TECHNICAL_REPAIR_ORDER_DESCRIPTION_MAX_CHARS
        and line_count <= TECHNICAL_REPAIR_ORDER_DESCRIPTION_MAX_LINES
    ):
        return
    raise PrintModuleError(
        "technical_repair_order_description_limit",
        "Для печати технического заказ-наряда сократите описание карточки "
        "до 2 000 символов и 25 строк.",
        details={
            "document_type": document_type,
            "field": "card.description",
            "max_chars": TECHNICAL_REPAIR_ORDER_DESCRIPTION_MAX_CHARS,
            "max_lines": TECHNICAL_REPAIR_ORDER_DESCRIPTION_MAX_LINES,
            "actual_chars": len(text),
            "actual_lines": line_count,
        },
    )
