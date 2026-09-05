from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

_DEFAULT_LIMIT = 4
_MAX_LIMIT = 8
_MAX_QUERY_CHARS = 200
_QUOTE_FIELDS = (
    "entity_type",
    "id",
    "updated_at",
    "request_number",
    "status",
    "items_count",
    "created_at",
    "notes_count",
    "agent_draft_count",
    "published_offer_count",
)
_PART_FIELDS = (
    "entity_type",
    "id",
    "updated_at",
    "sku",
    "name",
    "manufacturer_name",
    "is_active",
    "physical_qty",
    "reserved_qty",
    "available_qty",
    "low_stock",
)


class StoreSearchClient(Protocol):
    def search(
        self,
        *,
        entity: str,
        query_text: str = "",
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]: ...


StoreClientFactory = Callable[[], StoreSearchClient | None]


def _manager_store_client() -> StoreSearchClient | None:
    # Keep the optional sibling dependency out of ordinary CRM startup. The
    # factory constructs a read-token-only client and does not read .env files.
    from ..mcp.manager_registration import build_autostop_manager_store_read_client

    return build_autostop_manager_store_read_client()


def _text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _query(query: Any, intent: Any) -> str:
    values: list[str] = []
    for value in (_text(query), _text(intent)):
        if value and value.casefold() not in {item.casefold() for item in values}:
            values.append(value)
    return " ".join(values)[:_MAX_QUERY_CHARS].strip()


def _limit(value: Any) -> int:
    if isinstance(value, bool):
        return _DEFAULT_LIMIT
    try:
        numeric = int(value)
    except (TypeError, ValueError, OverflowError):
        return _DEFAULT_LIMIT
    return max(1, min(numeric, _MAX_LIMIT))


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


def _project_items(
    response: Mapping[str, Any], fields: tuple[str, ...], limit: int
) -> list[dict[str, Any]]:
    source_items = response.get("items")
    if not isinstance(source_items, list):
        return []
    projected: list[dict[str, Any]] = []
    for source in source_items[:limit]:
        if not isinstance(source, Mapping):
            continue
        item = {
            field: safe_value
            for field in fields
            if (safe_value := _safe_scalar(source.get(field))) is not None
        }
        if item:
            projected.append(item)
    return projected


class StoreQuotePartContext:
    """Bounded read-only Store context for the autonomous local agent."""

    def __init__(self, client_factory: StoreClientFactory | None = None) -> None:
        self._client_factory = client_factory or _manager_store_client
        self._client: StoreSearchClient | None = None

    def lookup(self, *, query: Any = "", intent: Any = "", limit: Any = None) -> dict[str, Any]:
        search_query = _query(query, intent)
        if not search_query:
            return {
                "ok": False,
                "status": "blocked",
                "data": {"quote_requests": [], "parts": []},
                "warnings": ["store_context_query_required"],
                "meta": {"source": "autostop_manager_store_read", "read_only": True},
            }

        client = self._client
        if client is None:
            try:
                client = self._client_factory()
            except Exception:
                client = None
            if client is not None:
                self._client = client
        if client is None:
            return {
                "ok": False,
                "status": "unavailable",
                "data": {"quote_requests": [], "parts": []},
                "warnings": ["store_context_unavailable"],
                "meta": {"source": "autostop_manager_store_read", "read_only": True},
            }

        effective_limit = _limit(limit)
        try:
            quote_response = client.search(
                entity="store_quote_request",
                query_text=search_query,
                limit=effective_limit,
            )
            part_response = client.search(
                entity="store_part",
                query_text=search_query,
                limit=effective_limit,
            )
        except Exception:
            return {
                "ok": False,
                "status": "degraded",
                "data": {"quote_requests": [], "parts": []},
                "warnings": ["store_context_unavailable"],
                "meta": {"source": "autostop_manager_store_read", "read_only": True},
            }

        quote_payload = quote_response if isinstance(quote_response, Mapping) else {}
        part_payload = part_response if isinstance(part_response, Mapping) else {}
        ok = bool(quote_payload.get("ok")) and bool(part_payload.get("ok"))
        return {
            "ok": ok,
            "status": "completed" if ok else "degraded",
            "data": {
                "quote_requests": _project_items(quote_payload, _QUOTE_FIELDS, effective_limit),
                "parts": _project_items(part_payload, _PART_FIELDS, effective_limit),
            },
            "warnings": [] if ok else ["store_context_partial"],
            "meta": {
                "source": "autostop_manager_store_read",
                "read_only": True,
                "entities": ["store_quote_request", "store_part"],
            },
        }
