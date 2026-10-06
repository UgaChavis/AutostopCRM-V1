"""Raw Gateway schema and exact readback for the manager structure."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..services.manager_tool_status import durable_digest, status_scope_digest

VirtualInvoker = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


async def verify_manager_structure_readback(
    arguments: Mapping[str, Any],
    result: Mapping[str, Any],
    invoke: VirtualInvoker,
) -> dict[str, Any]:
    readback = await invoke("api:/api/manager_structure", {})
    written = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    current = readback.get("data") if isinstance(readback.get("data"), Mapping) else {}
    if arguments.get("preview") is True:
        digest = durable_digest(dict(current))
        unchanged = bool(
            result.get("ok")
            and readback.get("ok")
            and written.get("preview") is True
            and current.get("version")
            == arguments.get("expected_version")
            == written.get("version")
            and written.get("saved_digest") == digest
        )
        return {
            "required": True,
            "passed": unchanged,
            "check": "manager_structure_preview_non_mutating_readback",
            "evidence": {"actual_version": current.get("version"), "unchanged": unchanged},
        }
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
    elif arguments.get("operation") in {"set_tool_status", "clear_tool_status"}:
        status = arguments.get("tool_status") or {}
        ident = status.get("operation_id")
        actual = (current.get("tool_statuses") or {}).get(ident)
        exact = actual == written.get("tool_status")
        if arguments["operation"] == "set_tool_status":
            exact = (
                exact and isinstance(actual, Mapping) and actual.get("state") == status.get("state")
            )
        else:
            exact = exact and actual is None
        route_exact = written.get("unchanged_scope_digest") == status_scope_digest(
            dict(current), ident
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
    if arguments.get("operation") in {"layout_element", "layout_relation", "reroute"}:
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
        labels = written.get("labels")
        route_exact = (
            route_exact
            and isinstance(labels, Mapping)
            and labels
            == {
                item.get("id"): {
                    field: item.get(field) for field in ("label_x", "label_y", "auto_hidden_label")
                }
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
    if route in {"/api/manager_structure", "/api/manager_structure/tool_catalog"}:
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
                "side": {"type": "string", "enum": ["auto", "left", "right", "top", "bottom"]},
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
        element = {
            "type": "object",
            "properties": {
                "id": {"type": "string", "maxLength": 32},
                "title": {"type": "string", "maxLength": 160},
                "description": {"type": "string", "maxLength": 10000},
                "instruction": {"type": "string", "maxLength": 30000},
                "parent": {"type": ["string", "null"], "maxLength": 32},
                "kind": {"type": "string", "enum": ["module", "item", "storage", "condition"]},
                "lines": {
                    "type": "array",
                    "maxItems": 4,
                    "items": {"type": "string", "maxLength": 140},
                },
                "icon": {"type": "string", "maxLength": 32},
                "color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
                "compact": {"type": "boolean"},
                "indicator": {"type": "string", "enum": ["off", "green", "yellow", "red"]},
                "indicator_mode": {"type": "string", "enum": ["none", "manual", "automation"]},
                "indicator_state": {"type": "string", "enum": ["green", "yellow", "red"]},
                **{field: {"type": "string", "maxLength": 32} for field in ("group", "tone")},
                **{
                    field: {
                        "type": "number",
                        "minimum": 24 if field in {"width", "height"} else 0,
                        "maximum": 10000,
                    }
                    for field in ("x", "y", "width", "height")
                },
            },
            "required": ["id"],
            "additionalProperties": False,
            "description": "Partial module update by stable ID. Use layout_element for geometry; preserve omitted instructions and fields.",
        }
        canvas = {
            "type": "object",
            "properties": {
                field: {"type": "number", "minimum": 320, "maximum": 10000}
                for field in ("width", "height")
            },
            "required": ["width", "height"],
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
                        "reroute",
                        "set_tool_status",
                        "clear_tool_status",
                    ],
                },
                "expected_version": {"type": "integer", "minimum": 0},
                "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 128},
                "element": element,
                "relation": relation,
                "id": {"type": "string"},
                "canvas": canvas,
                "diagram": {
                    "type": "object",
                    "properties": {
                        "schema_version": {"const": "autostopcrm.manager-structure.v1"},
                        "canvas": canvas,
                        "elements": {"type": "array", "maxItems": 250, "items": element},
                        "relations": {"type": "array", "maxItems": 500, "items": relation},
                    },
                    "required": ["schema_version", "canvas", "elements", "relations"],
                },
                "preview": {"type": "boolean"},
                "tool_status": {
                    "type": "object",
                    "properties": {
                        "operation_id": {"type": "string", "minLength": 1, "maxLength": 128},
                        "state": {
                            "type": "string",
                            "enum": ["not_commissioned", "temporarily_unavailable", "working"],
                        },
                    },
                    "required": ["operation_id"],
                    "additionalProperties": False,
                },
            },
            "required": ["operation", "expected_version", "idempotency_key"],
            "additionalProperties": False,
        }
    return None
