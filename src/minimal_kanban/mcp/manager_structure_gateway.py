"""Raw Gateway schema and exact readback for the manager structure."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

VirtualInvoker = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


async def verify_manager_structure_readback(
    arguments: Mapping[str, Any],
    result: Mapping[str, Any],
    invoke: VirtualInvoker,
) -> dict[str, Any]:
    readback = await invoke("api:/api/manager_structure", {})
    written = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    current = readback.get("data") if isinstance(readback.get("data"), Mapping) else {}
    version_exact = bool(
        result.get("ok") and readback.get("ok") and written.get("version") == current.get("version")
    )
    action = arguments.get("operation")
    field = "elements" if action == "upsert_element" else "relations"
    requested = arguments.get("element" if action == "upsert_element" else "relation")
    exact = True
    if action in {"upsert_element", "upsert_relation"}:
        actual = next(
            (
                item
                for item in current.get(field) or []
                if isinstance(item, Mapping)
                and isinstance(requested, Mapping)
                and item.get("id") == requested.get("id")
            ),
            None,
        )
        exact = (
            isinstance(actual, Mapping)
            and isinstance(requested, Mapping)
            and all(actual.get(key) == value for key, value in requested.items())
        )
    elif action == "replace":
        template = arguments.get("diagram") or {}
        exact = all(
            current.get(key) == template.get(key)
            for key in ("schema_version", "canvas", "elements", "relations")
        )
    elif action == "set_canvas":
        exact = current.get("canvas") == arguments.get("canvas")
    elif action in {"remove_element", "remove_relation"}:
        field = "elements" if action == "remove_element" else "relations"
        exact = not any(
            item.get("id") == arguments.get("id")
            for item in current.get(field) or []
            if isinstance(item, Mapping)
        )
    return {
        "required": True,
        "passed": bool(version_exact and exact),
        "check": "manager_structure_exact_readback",
        "evidence": {
            "expected_version": written.get("version"),
            "actual_version": current.get("version"),
            "exact": bool(exact),
        },
    }


def manager_structure_schema(route: str) -> dict[str, Any] | None:
    if route == "/api/manager_structure":
        return {
            "$id": f"autostopcrm-agent-gateway:{route}",
            "title": "Прочитать структуру менеджера",
            "type": "object",
            "additionalProperties": False,
        }
    if route == "/api/manager_structure/apply":
        return {
            "$id": f"autostopcrm-agent-gateway:{route}",
            "title": "Изменить структуру менеджера",
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "upsert_element",
                        "upsert_relation",
                        "remove_element",
                        "remove_relation",
                        "set_canvas",
                        "replace",
                    ],
                },
                "expected_version": {"type": "integer", "minimum": 0},
                "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 128},
                "element": {"type": "object"},
                "relation": {"type": "object"},
                "id": {"type": "string"},
                "canvas": {"type": "object"},
                "diagram": {"type": "object"},
            },
            "required": ["operation", "expected_version", "idempotency_key"],
            "additionalProperties": False,
        }
    return None
