from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..agent.tools import AgentToolExecutor
from .client import BoardApiClient

WEB_RESEARCH_CAPABILITY_NAMES = frozenset(
    {
        "search_web_multi",
        "fetch_page_excerpt",
        "fetch_page_browser",
        "research_drive2_cases",
    }
)
WEB_RESEARCH_CAPABILITY_DESCRIPTIONS = {
    "search_web_multi": (
        "Search public web sources through the configured provider cascade and return compact "
        "deduplicated results."
    ),
    "fetch_page_excerpt": (
        "Fetch a bounded text excerpt from one public HTTP(S) page with SSRF protection."
    ),
    "fetch_page_browser": (
        "Render one public HTTP(S) page in the browser and return a bounded excerpt with access "
        "flags."
    ),
    "research_drive2_cases": (
        "Research bounded public Drive2 logbook cases for a vehicle symptom; returns compact "
        "case evidence and access status without account use or raw-page retention."
    ),
}
WEB_RESEARCH_CAPABILITY_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_web_multi": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 1000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 253},
                "maxItems": 20,
                "uniqueItems": True,
            },
            "providers": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 40},
                "maxItems": 10,
                "uniqueItems": True,
            },
        },
        "required": ["query"],
    },
    "fetch_page_excerpt": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string", "minLength": 1, "maxLength": 2048},
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8000,
                "default": 2500,
            },
        },
        "required": ["url"],
    },
    "fetch_page_browser": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "url": {"type": "string", "minLength": 1, "maxLength": 2048},
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8000,
                "default": 2500,
            },
            "wait_ms": {
                "type": "integer",
                "minimum": 0,
                "maximum": 5000,
                "default": 750,
            },
        },
        "required": ["url"],
    },
    "research_drive2_cases": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string", "minLength": 2, "maxLength": 480},
            "vehicle": {"type": "string", "minLength": 1, "maxLength": 240},
            "engine": {"type": "string", "minLength": 1, "maxLength": 80},
            "transmission": {"type": "string", "minLength": 1, "maxLength": 80},
            "dtc_codes": {
                "type": "array",
                "items": {"type": "string", "minLength": 3, "maxLength": 16},
                "maxItems": 8,
                "uniqueItems": True,
            },
            "max_cases": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
        },
        "required": ["query"],
    },
}


def create_web_tool_executor(board_api: BoardApiClient, *, actor_name: str) -> AgentToolExecutor:
    return AgentToolExecutor(board_api, actor_name=actor_name)


def invoke_web_research(
    executor: AgentToolExecutor, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    try:
        executor.reset_task_budget()
        result = dict(executor.execute(name, arguments))
        return result if "ok" in result else {"ok": True, "data": result}
    except Exception as exc:  # pragma: no cover - transport integration failure
        return {
            "ok": False,
            "error": {
                "code": "capability_failed",
                "message": "Web research capability failed.",
                "error_type": type(exc).__name__,
                "tool": name,
            },
        }


def web_research_argument_error(name: str, arguments: Mapping[str, Any]) -> str | None:
    schema = WEB_RESEARCH_CAPABILITY_SCHEMAS[name]
    properties = schema["properties"]
    if set(arguments).difference(properties):
        return "web_arguments_contain_unknown_fields"
    for required in schema.get("required", []):
        value = arguments.get(required)
        if not isinstance(value, str) or not value.strip():
            return f"web_argument_{required}_required"
    for field, value in arguments.items():
        error = _field_error(field, value, properties[field])
        if error:
            return error
    return None


def _field_error(field: str, value: Any, schema: Mapping[str, Any]) -> str | None:
    expected_type = schema.get("type")
    if expected_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"web_argument_{field}_invalid"
        if value < int(schema.get("minimum", value)) or value > int(schema.get("maximum", value)):
            return f"web_argument_{field}_out_of_range"
    elif expected_type == "string":
        if not isinstance(value, str):
            return f"web_argument_{field}_invalid"
        if len(value) < int(schema.get("minLength", 0)) or len(value) > int(
            schema.get("maxLength", len(value))
        ):
            return f"web_argument_{field}_out_of_range"
    elif expected_type == "array":
        if not isinstance(value, list) or len(value) > int(schema.get("maxItems", len(value))):
            return f"web_argument_{field}_invalid"
        if not all(isinstance(item, str) and item.strip() for item in value):
            return f"web_argument_{field}_invalid"
        if schema.get("uniqueItems") and len(set(value)) != len(value):
            return f"web_argument_{field}_duplicates"
    return None


__all__ = [
    "WEB_RESEARCH_CAPABILITY_DESCRIPTIONS",
    "WEB_RESEARCH_CAPABILITY_NAMES",
    "WEB_RESEARCH_CAPABILITY_SCHEMAS",
    "create_web_tool_executor",
    "invoke_web_research",
    "web_research_argument_error",
]
