from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..json_safety import find_mapping
from ..storage.change_feed_store import (
    CHANGE_FEED_CONSUMER_MAX_LENGTH,
    CHANGE_FEED_PAGE_DEFAULT,
    CHANGE_FEED_PAGE_MAX,
    CHANGE_FEED_TOKEN_MAX_LENGTH,
)

CHANGE_FEED_BOOTSTRAP_ROUTE = "/api/change_feed/bootstrap"
CHANGE_FEED_READ_ROUTE = "/api/change_feed/read"
CHANGE_FEED_ACK_ROUTE = "/api/change_feed/ack"
CHANGE_FEED_REGISTER_ROUTE = "/api/change_feed/register"
CHANGE_FEED_SUMMARIZE_ROUTE = "/api/change_feed/summarize"
CHANGE_FEED_ROUTES = frozenset(
    {
        CHANGE_FEED_BOOTSTRAP_ROUTE,
        CHANGE_FEED_READ_ROUTE,
        CHANGE_FEED_ACK_ROUTE,
        CHANGE_FEED_REGISTER_ROUTE,
        CHANGE_FEED_SUMMARIZE_ROUTE,
    }
)
CHANGE_FEED_WRITE_ROUTES = frozenset(
    {CHANGE_FEED_BOOTSTRAP_ROUTE, CHANGE_FEED_ACK_ROUTE, CHANGE_FEED_REGISTER_ROUTE}
)

VirtualInvoker = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def change_feed_schema(route: str) -> dict[str, Any] | None:
    consumer = {
        "type": "string",
        "minLength": 1,
        "maxLength": CHANGE_FEED_CONSUMER_MAX_LENGTH,
        "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$",
    }
    properties: dict[str, Any] = {"consumer_id": consumer}
    required = ["consumer_id"]
    if route == CHANGE_FEED_READ_ROUTE:
        properties.update(
            {
                "cursor": {
                    "anyOf": [
                        {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": CHANGE_FEED_TOKEN_MAX_LENGTH,
                        },
                        {"type": "null"},
                    ],
                    "default": None,
                    "description": "Opaque replay cursor returned by the preceding page.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": CHANGE_FEED_PAGE_MAX,
                    "default": CHANGE_FEED_PAGE_DEFAULT,
                },
            }
        )
    elif route in {CHANGE_FEED_ACK_ROUTE, CHANGE_FEED_SUMMARIZE_ROUTE}:
        properties["ack"] = {
            "type": "string",
            "minLength": 1,
            "maxLength": CHANGE_FEED_TOKEN_MAX_LENGTH,
            "description": "Opaque ACK token returned with one delivered page.",
        }
        required.append("ack")
    elif route == CHANGE_FEED_REGISTER_ROUTE:
        properties["start_at"] = {
            "type": "string",
            "enum": ["latest", "beginning"],
            "default": "latest",
        }
    elif route != CHANGE_FEED_BOOTSTRAP_ROUTE:
        return None
    return {
        "$id": f"autostopcrm-agent-gateway:{route}",
        "title": route,
        "type": "object",
        "description": {
            CHANGE_FEED_BOOTSTRAP_ROUTE: (
                "Read the durable feed checkpoint without opening or acknowledging a delivery."
            ),
            CHANGE_FEED_READ_ROUTE: (
                "Read one replay-safe ordered CRM change-feed page without advancing ACK state."
            ),
            CHANGE_FEED_ACK_ROUTE: "Explicitly acknowledge one contiguous CRM change-feed page.",
            CHANGE_FEED_REGISTER_ROUTE: (
                "Atomically register a typed feed consumer at the latest checkpoint or beginning."
            ),
            CHANGE_FEED_SUMMARIZE_ROUTE: (
                "Freeze a bounded PII-free digest for one exact unacknowledged feed page."
            ),
        }[route],
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


async def verify_change_feed_checkpoint_readback(
    operation: str,
    arguments: Mapping[str, Any],
    result: Mapping[str, Any],
    invoke: VirtualInvoker,
) -> dict[str, Any] | None:
    checks = {
        f"api:{CHANGE_FEED_BOOTSTRAP_ROUTE}": "exact_change_feed_bootstrap_checkpoint",
        f"api:{CHANGE_FEED_ACK_ROUTE}": "exact_change_feed_ack_checkpoint",
        f"api:{CHANGE_FEED_REGISTER_ROUTE}": "exact_change_feed_registration_checkpoint",
    }
    check = checks.get(operation)
    if check is None:
        return None
    consumer_id = str(arguments.get("consumer_id") or "").strip()
    expected = find_mapping(result, "consumer_id", consumer_id) if consumer_id else None
    readback = (
        await invoke(f"api:{CHANGE_FEED_BOOTSTRAP_ROUTE}", {"consumer_id": consumer_id})
        if consumer_id
        else {}
    )
    actual = find_mapping(readback, "consumer_id", consumer_id) if consumer_id else None
    expected_generation = str((expected or {}).get("generation") or "")
    expected_acked = (expected or {}).get("acked_sequence")
    passed = bool(
        result.get("ok")
        and readback.get("ok")
        and expected_generation
        and expected_acked is not None
        and str((actual or {}).get("generation") or "") == expected_generation
        and (actual or {}).get("acked_sequence") == expected_acked
    )
    return {
        "required": True,
        "passed": passed,
        "check": check,
        "evidence": {
            "consumer_id": consumer_id,
            "generation": expected_generation,
            "acked_sequence": expected_acked,
            "readback_ok": bool(readback.get("ok")),
        },
    }
