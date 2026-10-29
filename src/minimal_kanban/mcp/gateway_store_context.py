from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from .agent_gateway_support import AGENT_GATEWAY_FORMAT, _compact_object

StoreInvoker = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def bootstrap_store_context_requests(
    *,
    query: Any,
    intent: Any,
    include_store_context: bool | None = False,
    invoke: StoreInvoker,
) -> tuple[bool, list[Awaitable[dict[str, Any]]]]:
    """Build bounded Store reads only for an explicit authorized-case opt-in."""

    context_hint = str(query or intent or "").strip()
    requested = include_store_context is True
    if not requested:
        return False, []
    arguments = {
        "query": context_hint,
        "filters": {},
        "cursor": None,
        "limit": 4,
    }
    return True, [
        invoke("store_search", {"entity": "store_quote_request", **arguments}),
        invoke("store_search", {"entity": "store_part", **arguments}),
    ]


def _store_data(results: Sequence[Any], index: int) -> Mapping[str, Any]:
    result = results[index] if len(results) > index else {}
    return result.get("data") if isinstance(result, Mapping) else {}


def bootstrap_store_context_summary(
    *,
    requested: bool,
    results: Sequence[Any],
    compact: Callable[..., Any],
) -> tuple[dict[str, Any], list[str]]:
    """Keep optional Store failure advisory so CRM bootstrap remains usable."""

    if not requested:
        return {"requested": False, "ok": None, "status": "not_requested"}, []
    ready = all(isinstance(result, Mapping) and bool(result.get("ok")) for result in results)
    summary = {
        "requested": True,
        "ok": ready,
        "status": "ready" if ready else "degraded",
        "quote_requests": compact(_store_data(results, 0), item_limit=4),
        "parts": compact(_store_data(results, 1), item_limit=4),
    }
    return summary, [] if ready else ["store_context_degraded"]


def runtime_status_payload(
    crm_payload: dict[str, Any], store_payload: dict[str, Any], *, store_requested: bool
) -> dict[str, Any]:
    """Keep optional Store diagnostics separate from CRM availability."""
    crm_ok = bool(crm_payload.get("ok"))
    store_ok = bool(store_payload.get("ok"))
    data = dict(crm_payload.get("data") or {}) if isinstance(crm_payload.get("data"), dict) else {}
    data["store"] = {
        "ok": store_ok if store_requested else None,
        "requested": store_requested,
        "status": str(store_payload.get("status") or ("ready" if store_ok else "degraded")),
        "summary": _compact_object(store_payload.get("summary") or {}, item_limit=10),
        "data": _compact_object(store_payload.get("data"), item_limit=10, key_limit=40),
        "warnings": [str(item) for item in store_payload.get("warnings") or []][:10],
    }
    warnings = [str(item) for item in crm_payload.get("warnings") or []]
    if store_requested and not store_ok:
        warnings.append("store_adapter_degraded")
    payload = dict(crm_payload)
    payload.update(
        {
            "ok": crm_ok,
            "status": "ready"
            if crm_ok and (store_ok or not store_requested)
            else "degraded"
            if crm_ok
            else "failed",
            "data": data,
            "warnings": list(dict.fromkeys(warnings)),
        }
    )
    payload.setdefault("format", AGENT_GATEWAY_FORMAT)
    payload.setdefault("summary", {})
    payload.setdefault("next_actions", [])
    payload.setdefault("verification", {})
    payload.setdefault("page", {})
    payload.setdefault("meta", {})
    return payload
