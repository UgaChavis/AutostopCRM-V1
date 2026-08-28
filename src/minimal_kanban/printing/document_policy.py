from __future__ import annotations

from .errors import PrintModuleError

LOCKED_TEMPLATE_DOCUMENT_TYPES = frozenset(
    {
        "completion_act",
        "technical_repair_order",
    }
)


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
