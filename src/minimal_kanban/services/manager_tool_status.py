"""Manual commissioning state and durable graph projection, with no provider calls."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from .errors import ServiceError
from .manager_tool_catalog import TOOL_ID_RE

STATUS_STATES = ("not_commissioned", "temporarily_unavailable", "working")
COMPUTED_FIELDS = frozenset(
    {"can_edit", "can_edit_tool_status", "catalog_metadata", "runtime_metadata", "receipts"}
)
MAX_STATUSES = 2000


def durable_projection(data: dict[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in data.items() if key not in COMPUTED_FIELDS}


def durable_digest(data: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            durable_projection(data), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def status_scope_digest(data: dict[str, Any], ident: str) -> str:
    scope = durable_projection(data)
    scope.pop("version", None)
    scope.setdefault("tool_statuses", {}).pop(ident, None)
    return durable_digest(scope)


def invalid(message: str) -> None:
    raise ServiceError("manager_structure_invalid", message, status_code=422)


def validate_statuses(data: dict[str, Any]) -> None:
    statuses = data.get("tool_statuses", {})
    if not isinstance(statuses, dict) or len(statuses) > MAX_STATUSES:
        invalid("Превышен предел ручных отметок инструментов.")
    for ident, record in statuses.items():
        if not isinstance(ident, str) or not TOOL_ID_RE.fullmatch(ident):
            invalid("Некорректный ID ручной отметки.")
        if not isinstance(record, dict) or set(record) != {"state", "updated_at", "updated_by"}:
            invalid("Некорректная запись ручной отметки.")
        if record["state"] not in STATUS_STATES:
            invalid("Неизвестное состояние ручной отметки.")
        for field, limit in (("updated_at", 64), ("updated_by", 160)):
            if (
                not isinstance(record[field], str)
                or not record[field]
                or len(record[field]) > limit
            ):
                invalid("Некорректные служебные поля ручной отметки.")


def apply_status(data: dict[str, Any], payload: dict[str, Any], bundle: dict[str, Any]) -> None:
    status = payload.get("tool_status")
    clear = payload["operation"] == "clear_tool_status"
    fields = {"operation_id"} if clear else {"operation_id", "state"}
    if not isinstance(status, dict) or set(status) != fields:
        invalid("Нужны ID инструмента и ручное состояние.")
    ident = status.get("operation_id")
    if not isinstance(ident, str) or ident not in {tool["tool_id"] for tool in bundle["tools"]}:
        invalid("Инструмент отсутствует в установленном каталоге.")
    if not clear and status.get("state") not in STATUS_STATES:
        invalid("Неизвестное состояние ручной отметки.")
    statuses = data.setdefault("tool_statuses", {})
    if clear:
        statuses.pop(ident, None)
    else:
        session = payload["_operator_session"]
        actor = (
            session.get("audit_actor_name")
            if session.get("service_identity") is True
            else session.get("username")
        )
        statuses[ident] = {
            "state": status["state"],
            "updated_at": datetime.now(UTC).isoformat(),
            "updated_by": str(actor).strip().upper(),
        }
    validate_statuses(data)
