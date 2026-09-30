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
    requested = (
        arguments.get("element")
        if arguments.get("operation") in {"upsert_element", "layout_element"}
        else arguments.get("relation")
        if arguments.get("operation") in {"upsert_relation", "layout_relation"}
        else None
    )
    collection = (
        current.get("elements")
        if arguments.get("operation") in {"upsert_element", "layout_element"}
        else current.get("relations")
    )
    exact = True
    route_exact = True
    if isinstance(requested, Mapping):
        accepted = written.get("accepted_relation")
        expected_fields = (
            accepted
            if arguments.get("operation") in {"upsert_relation", "layout_relation"}
            and isinstance(accepted, Mapping)
            else requested
        )
        actual = next(
            (
                item
                for item in collection or []
                if isinstance(item, Mapping) and item.get("id") == requested.get("id")
            ),
            None,
        )
        exact = isinstance(actual, Mapping) and all(
            actual.get(key) == value
            for key, value in expected_fields.items()
            if key
            not in (
                {"x", "y"} if written.get("adjusted") and expected_fields is requested else set()
            )
            and not (
                expected_fields is requested
                and arguments.get("operation") == "layout_relation"
                and key in {"path", "label_x", "label_y"}
            )
        )
        if arguments.get("operation") == "layout_element":
            accepted = written.get("accepted_element")
            route_exact = (
                isinstance(accepted, Mapping)
                and isinstance(actual, Mapping)
                and all(
                    actual.get(field) == accepted.get(field)
                    for field in ("id", "x", "y", "width", "height")
                )
            )
    elif arguments.get("operation") == "replace":
        template = arguments.get("diagram") or {}
        exact = all(
            current.get(key) == template.get(key)
            for key in ("canvas", "elements", "relations", "schema_version")
        )
    elif arguments.get("operation") == "set_canvas":
        exact = current.get("canvas") == arguments.get("canvas")
    elif arguments.get("operation") in {"remove_element", "remove_relation"}:
        field = "elements" if arguments["operation"] == "remove_element" else "relations"
        exact = not any(
            isinstance(item, Mapping) and item.get("id") == arguments.get("id")
            for item in current.get(field) or []
        )
    if arguments.get("operation") in {"layout_element", "layout_relation"}:
        routes = written.get("routes")
        route_exact = (
            route_exact
            and isinstance(routes, Mapping)
            and routes
            == {
                item.get("id"): item.get("path")
                for item in current.get("relations") or []
                if isinstance(item, Mapping)
            }
        )
    return {
        "required": True,
        "passed": bool(version_exact and exact and route_exact),
        "check": "manager_structure_exact_readback",
        "evidence": {
            "expected_version": written.get("version"),
            "actual_version": current.get("version"),
            "exact": bool(exact),
            "routes_exact": bool(route_exact),
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
        anchor = {
            "type": "object",
            "properties": {
                "side": {"type": "string", "enum": ["left", "right", "top", "bottom"]},
                "offset": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["side", "offset"],
            "additionalProperties": False,
        }
        relation = {
            "type": "object",
            "properties": {
                "id": {"type": "string", "maxLength": 32},
                "from": {"type": "string", "maxLength": 32},
                "to": {"type": "string", "maxLength": 32},
                "kind": {"type": "string", "enum": ["exchange", "event"]},
                "label": {"type": "string", "maxLength": 200},
                "description": {"type": "string", "maxLength": 10000},
                "protocol": {"type": "string", "maxLength": 200},
                "tone": {"type": "string", "maxLength": 32},
                "color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
                "direction": {
                    "type": "string",
                    "enum": ["forward", "reverse", "both", "none"],
                },
                "path": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Absolute SVG path: M/L/H/V/Q/C. Required for manual routes.",
                },
                "route_mode": {"type": "string", "enum": ["auto", "manual"]},
                "from_anchor": anchor,
                "to_anchor": anchor,
                "label_mode": {"type": "string", "enum": ["auto", "manual"]},
                "label_x": {"type": "number", "minimum": 0, "maximum": 10000},
                "label_y": {"type": "number", "minimum": 0, "maximum": 10000},
                "show_label": {"type": "boolean"},
                "compact_label": {"type": "boolean"},
                "auto_hidden_label": {"type": "boolean"},
                "label_max_width": {"type": "number", "minimum": 40, "maximum": 800},
            },
            "required": ["id"],
            "additionalProperties": False,
        }
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
                        "layout_element",
                        "layout_relation",
                        "remove_element",
                        "remove_relation",
                        "set_canvas",
                        "replace",
                    ],
                },
                "expected_version": {"type": "integer", "minimum": 0},
                "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 128},
                "element": {"type": "object"},
                "relation": relation,
                "id": {"type": "string"},
                "canvas": {"type": "object"},
                "diagram": {"type": "object"},
                "preview": {"type": "boolean"},
            },
            "required": ["operation", "expected_version", "idempotency_key"],
            "additionalProperties": False,
        }
    return None
