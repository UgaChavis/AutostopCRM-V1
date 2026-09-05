from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

StoreInvoker = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def bootstrap_store_context_requests(
    *,
    query: Any,
    intent: Any,
    include_store_context: bool | None,
    invoke: StoreInvoker,
) -> tuple[bool, list[Awaitable[dict[str, Any]]]]:
    """Build the two bounded Store reads only when the caller supplies a signal."""

    context_hint = str(query or intent or "").strip()
    requested = (
        bool(include_store_context) if include_store_context is not None else bool(context_hint)
    )
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
