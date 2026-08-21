from __future__ import annotations

import base64
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from logging import Logger

from ..config import get_api_port, get_api_port_fallback_limit
from .oauth_provider import OAUTH_AUDIT_ACTOR_HEADER, OAUTH_AUDIT_ASSERTION_HEADER


class BoardApiTransportError(RuntimeError):
    pass


def _normalize_timeout_seconds(value: object, *, default: float = 10.0) -> float:
    if isinstance(value, bool):
        value = default
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        numeric = default
    if not math.isfinite(numeric) or numeric <= 0:
        numeric = default
    return min(max(numeric, 0.1), 60.0)


def _normalize_int(
    value: object,
    *,
    default: int,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool):
        value = default
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        numeric = float(default)
    if not math.isfinite(numeric) or not numeric.is_integer():
        numeric = float(default)
    if numeric < minimum:
        return minimum
    if maximum is not None and numeric > maximum:
        return maximum
    normalized = int(numeric)
    return normalized


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON numeric constant: {value}")


def _authorization_bearer_header(token: str | None) -> str | None:
    normalized = str(token or "").strip()
    if not normalized:
        return None
    if "\r" in normalized or "\n" in normalized:
        raise BoardApiTransportError("Некорректный bearer token локального API.")
    return f"Bearer {normalized}"


_MAX_API_RESPONSE_BYTES = 32 * 1024 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _urlopen_no_redirect(request: urllib.request.Request, *, timeout: float):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


SUPPORTED_PRINT_DOCUMENT_TYPES = {
    "repair_order",
    "vehicle_acceptance_act",
    "invoice",
    "invoice_factura",
    "upd",
    "inspection_sheet",
    "completion_act",
    "parts_sale",
}

MANUAL_DOCUMENT_TYPE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "upd",
        (
            "upd",
            "упд",
            "универсальный передаточный документ",
            "универсальный передаточный",
            "передаточный документ",
        ),
    ),
    (
        "invoice_factura",
        (
            "invoice_factura",
            "invoice factura",
            "счет фактура",
            "счет-фактура",
            "счёт фактура",
            "счёт-фактура",
        ),
    ),
    (
        "vehicle_acceptance_act",
        (
            "vehicle_acceptance_act",
            "акт приема",
            "акт приемки",
            "акт приёма",
            "акт приёмки",
            "прием автомобиля",
            "приём автомобиля",
        ),
    ),
    (
        "completion_act",
        (
            "completion_act",
            "акт выполненных работ",
            "акт выполненных работ услуг",
            "акт работ",
            "акт оказанных услуг",
        ),
    ),
    (
        "inspection_sheet",
        (
            "inspection_sheet",
            "дефектовка",
            "дефектовочная ведомость",
            "дефектный акт",
            "inspection sheet",
        ),
    ),
    (
        "parts_sale",
        (
            "parts_sale",
            "продажа запчастей",
            "продажа деталей",
            "реализация запчастей",
            "запчасти без ремонта",
        ),
    ),
    (
        "repair_order",
        (
            "repair_order",
            "заказ наряд",
            "заказ-наряд",
            "зн",
            "наряд",
        ),
    ),
    ("invoice", ("invoice", "счет", "счёт", "счет на оплату", "счёт на оплату")),
)


def _manual_document_type_text(value: object) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"[_\-]+", " ", text)
    return " ".join(text.split())


def _normalize_manual_document_type(document_type: object, request_text: object) -> str:
    raw_document_type = str(document_type or "").strip()
    normalized_document_type = raw_document_type.strip().lower()
    if normalized_document_type in SUPPORTED_PRINT_DOCUMENT_TYPES:
        return normalized_document_type
    explicit_text = _manual_document_type_text(raw_document_type)
    source_text = explicit_text or _manual_document_type_text(request_text)
    if not source_text or source_text == "auto":
        source_text = _manual_document_type_text(request_text)
    for resolved_type, aliases in MANUAL_DOCUMENT_TYPE_ALIASES:
        if any(alias in source_text for alias in aliases):
            return resolved_type
    return normalized_document_type or "invoice"


def candidate_api_urls() -> list[str]:
    start_port = get_api_port()
    fallback_limit = get_api_port_fallback_limit()
    return [f"http://127.0.0.1:{port}" for port in range(start_port, start_port + fallback_limit)]


def discover_board_api(
    *, bearer_token: str | None = None, timeout_seconds: float = 1.0
) -> str | None:
    for base_url in candidate_api_urls():
        client = BoardApiClient(
            base_url, bearer_token=bearer_token, timeout_seconds=timeout_seconds
        )
        try:
            response = client.health()
        except BoardApiTransportError:
            continue
        if response.get("ok"):
            return base_url
    return None


class BoardApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | None = None,
        timeout_seconds: float = 10.0,
        logger: Logger | None = None,
        default_source: str = "mcp",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._timeout_seconds = _normalize_timeout_seconds(timeout_seconds)
        self._logger = logger
        self._default_source = default_source

    def health(self) -> dict:
        return self._request("/api/health", method="GET")

    def list_columns(self) -> dict:
        return self._request("/api/list_columns", method="GET")

    def create_column(
        self,
        label: str | None = None,
        *,
        name: str | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {}
        if label is not None:
            payload["label"] = label
        if name is not None:
            payload["name"] = name
        return self._request_with_identity("/api/create_column", payload, actor_name=actor_name)

    def rename_column(self, column_id: str, label: str, *, actor_name: str | None = None) -> dict:
        return self._request_with_identity(
            "/api/rename_column",
            {"column_id": column_id, "label": label},
            actor_name=actor_name,
        )

    def delete_column(self, column_id: str, *, actor_name: str | None = None) -> dict:
        return self._request_with_identity(
            "/api/delete_column", {"column_id": column_id}, actor_name=actor_name
        )

    def create_sticky(
        self,
        *,
        text: str,
        x: int = 0,
        y: int = 0,
        deadline: dict,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"text": text, "x": x, "y": y, "deadline": deadline}
        return self._request_with_identity("/api/create_sticky", payload, actor_name=actor_name)

    def get_cards(self, *, include_archived: bool = False, compact: bool = True) -> dict:
        return self._request(
            "/api/get_cards", {"include_archived": include_archived, "compact": compact}
        )

    def get_card(self, card_id: str) -> dict:
        return self._request("/api/get_card", {"card_id": card_id})

    def list_card_attachments(self, card_id: str, *, include_removed: bool = False) -> dict:
        return self._request(
            "/api/list_card_attachments",
            {"card_id": card_id, "include_removed": include_removed},
        )

    def get_card_attachment(self, card_id: str, attachment_id: str) -> dict:
        return self._request(
            "/api/get_card_attachment",
            {"card_id": card_id, "attachment_id": attachment_id},
        )

    def read_card_attachment(
        self,
        card_id: str,
        attachment_id: str,
        *,
        mode: str = "preview",
        max_chars: int = 12_000,
        include_base64: bool = False,
        max_base64_bytes: int = 1_048_576,
    ) -> dict:
        return self._request(
            "/api/read_card_attachment",
            {
                "card_id": card_id,
                "attachment_id": attachment_id,
                "mode": mode,
                "max_chars": _normalize_int(max_chars, default=12_000, minimum=1, maximum=50_000),
                "include_base64": include_base64,
                "max_base64_bytes": _normalize_int(
                    max_base64_bytes,
                    default=1_048_576,
                    maximum=4_194_304,
                ),
            },
        )

    def add_card_attachment(
        self,
        *,
        card_id: str,
        file_name: str,
        mime_type: str,
        content: bytes,
        actor_name: str | None = None,
    ) -> dict:
        return self._request_with_identity(
            "/api/add_card_attachment",
            {
                "card_id": card_id,
                "file_name": file_name,
                "mime_type": mime_type,
                "content_base64": base64.b64encode(content).decode("ascii"),
            },
            actor_name=actor_name,
        )

    def remove_card_attachment(
        self, *, card_id: str, attachment_id: str, actor_name: str | None = None
    ) -> dict:
        return self._request_with_identity(
            "/api/remove_card_attachment",
            {"card_id": card_id, "attachment_id": attachment_id},
            actor_name=actor_name,
        )

    def list_shared_files(self) -> dict:
        return self._request("/api/list_shared_files", method="GET")

    def get_shared_file_info(self, file_id: str) -> dict:
        return self._request("/api/get_shared_file_info", {"file_id": file_id})

    def download_shared_file(
        self,
        file_id: str,
        *,
        include_base64: bool = True,
        max_base64_bytes: int = 2_097_152,
    ) -> dict:
        return self._request(
            "/api/fetch_shared_file",
            {
                "file_id": file_id,
                "include_base64": include_base64,
                "max_base64_bytes": _normalize_int(
                    max_base64_bytes,
                    default=2_097_152,
                    maximum=8_388_608,
                ),
            },
        )

    def upload_shared_file(
        self,
        *,
        file_name: str,
        content_base64: str | None = None,
        content: bytes | None = None,
        mime_type: str = "application/octet-stream",
        x: int = 0,
        y: int = 0,
        actor_name: str | None = None,
    ) -> dict:
        if content_base64 is None and content is not None:
            content_base64 = base64.b64encode(content).decode("ascii")
        return self._request_with_identity(
            "/api/upload_shared_file",
            {
                "file_name": file_name,
                "mime_type": mime_type,
                "content_base64": content_base64 or "",
                "x": x,
                "y": y,
            },
            actor_name=actor_name,
        )

    def delete_shared_file(
        self,
        file_id: str,
        *,
        expected_updated_at: str | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"file_id": file_id}
        if expected_updated_at is not None:
            payload["expected_updated_at"] = expected_updated_at
        return self._request_with_identity(
            "/api/delete_shared_file", payload, actor_name=actor_name
        )

    def update_shared_file_position(
        self, file_id: str, *, x: int, y: int, actor_name: str | None = None
    ) -> dict:
        return self._request_with_identity(
            "/api/update_shared_file_position",
            {"file_id": file_id, "x": x, "y": y},
            actor_name=actor_name,
        )

    def get_card_context(
        self,
        card_id: str,
        *,
        event_limit: int = 20,
        include_repair_order_text: bool = True,
    ) -> dict:
        return self._request(
            "/api/get_card_context",
            {
                "card_id": card_id,
                "event_limit": event_limit,
                "include_repair_order_text": include_repair_order_text,
            },
        )

    def get_board_snapshot(
        self,
        *,
        archive_limit: int | None = None,
        compact: bool | None = None,
        include_archive: bool | None = None,
    ) -> dict:
        payload: dict[str, object] = {}
        if archive_limit is not None:
            payload["archive_limit"] = archive_limit
        if compact is not None:
            payload["compact"] = compact
        if include_archive is not None:
            payload["include_archive"] = include_archive
        if not payload:
            return self._request("/api/get_board_snapshot", method="GET")
        return self._request("/api/get_board_snapshot", payload, method="POST")

    def get_board_context(self) -> dict:
        return self._request("/api/get_board_context", method="GET")

    def get_board_content(
        self,
        *,
        include_archived: bool = True,
        view_mode: str = "agent",
    ) -> dict:
        payload: dict[str, object] = {
            "include_archived": include_archived,
            "view_mode": view_mode,
        }
        return self._request("/api/get_board_content", payload, method="POST")

    def get_board_events(
        self,
        *,
        event_limit: int = 100,
        include_archived: bool = True,
        view_mode: str = "audit",
    ) -> dict:
        payload: dict[str, object] = {
            "event_limit": event_limit,
            "include_archived": include_archived,
            "view_mode": view_mode,
        }
        return self._request("/api/get_board_events", payload, method="POST")

    def get_board_event_page(
        self,
        *,
        cursor: str | None = None,
        limit: int = 200,
        include_archived: bool = True,
    ) -> dict:
        return self._request(
            "/api/get_board_event_page",
            {"cursor": cursor, "limit": limit, "include_archived": include_archived},
            method="POST",
        )

    def review_board(
        self,
        *,
        stale_hours: int | None = None,
        overload_threshold: int | None = None,
        priority_limit: int | None = None,
        recent_event_limit: int | None = None,
    ) -> dict:
        payload: dict[str, object] = {}
        if stale_hours is not None:
            payload["stale_hours"] = stale_hours
        if overload_threshold is not None:
            payload["overload_threshold"] = overload_threshold
        if priority_limit is not None:
            payload["priority_limit"] = priority_limit
        if recent_event_limit is not None:
            payload["recent_event_limit"] = recent_event_limit
        if not payload:
            return self._request("/api/review_board", method="GET")
        return self._request("/api/review_board", payload, method="POST")

    def list_cashboxes(self, *, limit: int | None = None) -> dict:
        return self._request_optional_scalar_filter("/api/list_cashboxes", key="limit", value=limit)

    def list_inventory_items(self, *, query: str | None = None, limit: int | None = None) -> dict:
        payload: dict[str, object] = {}
        if query is not None:
            payload["query"] = query
        if limit is not None:
            payload["limit"] = limit
        if not payload:
            return self._request("/api/list_inventory_items", method="GET")
        return self._request("/api/list_inventory_items", payload, method="POST")

    def search_inventory_items(self, *, query: str = "", limit: int | None = None) -> dict:
        payload: dict[str, object] = {"query": query}
        if limit is not None:
            payload["limit"] = limit
        return self._request("/api/search_inventory_items", payload, method="POST")

    def get_inventory_item(self, item_id: str) -> dict:
        return self._request("/api/get_inventory_item", {"item_id": item_id}, method="POST")

    def list_inventory_movements(
        self,
        *,
        item_id: str | None = None,
        card_id: str | None = None,
        limit: int | None = None,
    ) -> dict:
        payload: dict[str, object] = {}
        if item_id is not None:
            payload["item_id"] = item_id
        if card_id is not None:
            payload["card_id"] = card_id
        if limit is not None:
            payload["limit"] = limit
        if not payload:
            return self._request("/api/list_inventory_movements", method="GET")
        return self._request("/api/list_inventory_movements", payload, method="POST")

    def save_inventory_item(
        self, item: dict[str, object], *, actor_name: str | None = None
    ) -> dict:
        return self._request_with_identity("/api/save_inventory_item", item, actor_name=actor_name)

    def replenish_inventory_item(
        self,
        item_id: str,
        quantity: str,
        *,
        expected_updated_at: str | None = None,
        cost_price: str | None = None,
        sale_price: str | None = None,
        note: str | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"item_id": item_id, "quantity": quantity}
        if expected_updated_at is not None:
            payload["expected_updated_at"] = expected_updated_at
        if cost_price is not None:
            payload["cost_price"] = cost_price
        if sale_price is not None:
            payload["sale_price"] = sale_price
        if note is not None:
            payload["note"] = note
        return self._request_with_identity(
            "/api/replenish_inventory_item", payload, actor_name=actor_name
        )

    def write_off_inventory_item(
        self,
        item_id: str,
        *,
        card_id: str,
        quantity: str,
        row_index: int | None = None,
        expected_updated_at: str | None = None,
        expected_card_updated_at: str | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {
            "item_id": item_id,
            "card_id": card_id,
            "quantity": quantity,
        }
        if row_index is not None:
            payload["row_index"] = row_index
        if expected_updated_at is not None:
            payload["expected_updated_at"] = expected_updated_at
        if expected_card_updated_at is not None:
            payload["expected_card_updated_at"] = expected_card_updated_at
        return self._request_with_identity(
            "/api/write_off_inventory_item", payload, actor_name=actor_name
        )

    def return_inventory_movement(
        self,
        movement_id: str,
        *,
        card_id: str | None = None,
        expected_updated_at: str | None = None,
        expected_card_updated_at: str | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"movement_id": movement_id}
        if card_id is not None:
            payload["card_id"] = card_id
        if expected_updated_at is not None:
            payload["expected_updated_at"] = expected_updated_at
        if expected_card_updated_at is not None:
            payload["expected_card_updated_at"] = expected_card_updated_at
        return self._request_with_identity(
            "/api/return_inventory_movement", payload, actor_name=actor_name
        )

    def get_cash_journal(
        self,
        *,
        months: int | None = None,
        limit: int | None = None,
        include_markdown: bool | None = None,
        compact_groups: bool | None = None,
    ) -> dict:
        payload: dict[str, object] = {}
        if months is not None:
            payload["months"] = months
        if limit is not None:
            payload["limit"] = limit
        if include_markdown is not None:
            payload["include_markdown"] = include_markdown
        if compact_groups is not None:
            payload["compact_groups"] = compact_groups
        if not payload:
            return self._request("/api/get_cash_journal", method="GET")
        return self._request("/api/get_cash_journal", payload, method="POST")

    def list_clients(self, *, limit: int | None = None, include_stats: bool = True) -> dict:
        payload: dict[str, object] = {"include_stats": include_stats}
        if limit is not None:
            payload["limit"] = limit
        return self._request_readonly_query("/api/list_clients", payload)

    def search_clients(self, *, query: str = "", limit: int | None = None) -> dict:
        payload: dict[str, object] = {"query": query}
        if limit is not None:
            payload["limit"] = limit
        return self._request_readonly_query("/api/search_clients", payload)

    def get_client(self, client_id: str, *, order_limit: int | None = None) -> dict:
        payload: dict[str, object] = {"client_id": client_id}
        if order_limit is not None:
            payload["order_limit"] = order_limit
        return self._request_readonly_query("/api/get_client", payload)

    def get_client_stats(self, client_id: str) -> dict:
        return self._request_readonly_query("/api/get_client_stats", {"client_id": client_id})

    def create_client(self, client: dict[str, object], *, actor_name: str | None = None) -> dict:
        return self._request_with_identity("/api/create_client", client, actor_name=actor_name)

    def update_client(
        self, client_id: str, patch: dict[str, object], *, actor_name: str | None = None
    ) -> dict:
        payload: dict[str, object] = {"client_id": client_id, **patch}
        return self._request_with_identity("/api/update_client", payload, actor_name=actor_name)

    def delete_client(
        self,
        client_id: str,
        *,
        allow_linked: bool = False,
        actor_name: str | None = None,
    ) -> dict:
        return self._request_with_identity(
            "/api/delete_client",
            {"client_id": client_id, "allow_linked": allow_linked},
            actor_name=actor_name,
        )

    def link_card_to_client(
        self,
        card_id: str,
        client_id: str,
        *,
        expected_card_updated_at: str,
        expected_client_updated_at: str,
        client_vehicle_id: str | None = None,
        create_vehicle_from_card: bool = False,
        sync_vehicle_fields: bool = True,
        sync_fields: bool = True,
        overwrite_card_fields: bool = False,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {
            "card_id": card_id,
            "client_id": client_id,
            "expected_card_updated_at": expected_card_updated_at,
            "expected_client_updated_at": expected_client_updated_at,
            "sync_fields": sync_fields,
            "overwrite_card_fields": overwrite_card_fields,
            "create_vehicle_from_card": create_vehicle_from_card,
            "sync_vehicle_fields": sync_vehicle_fields,
        }
        if client_vehicle_id:
            payload["client_vehicle_id"] = client_vehicle_id
        return self._request_with_identity(
            "/api/link_card_to_client",
            payload,
            actor_name=actor_name,
        )

    def upsert_client_vehicle(
        self,
        client_id: str,
        vehicle: dict[str, object] | None = None,
        *,
        client_vehicle_id: str | None = None,
        card_id: str | None = None,
        sync_linked_cards: bool | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"client_id": client_id}
        if vehicle is not None:
            payload["vehicle"] = vehicle
        if client_vehicle_id:
            payload["client_vehicle_id"] = client_vehicle_id
        if card_id:
            payload["card_id"] = card_id
        if sync_linked_cards is not None:
            payload["sync_linked_cards"] = bool(sync_linked_cards)
        return self._request_with_identity(
            "/api/upsert_client_vehicle", payload, actor_name=actor_name
        )

    def delete_client_vehicle(
        self,
        client_id: str,
        client_vehicle_id: str,
        *,
        unlink_cards: bool = True,
        actor_name: str | None = None,
    ) -> dict:
        return self._request_with_identity(
            "/api/delete_client_vehicle",
            {
                "client_id": client_id,
                "client_vehicle_id": client_vehicle_id,
                "unlink_cards": unlink_cards,
            },
            actor_name=actor_name,
        )

    def unlink_card_from_client(self, card_id: str, *, actor_name: str | None = None) -> dict:
        return self._request_with_identity(
            "/api/unlink_card_from_client", {"card_id": card_id}, actor_name=actor_name
        )

    def suggest_clients_for_card(
        self, card_id: str, *, query: str | None = None, limit: int | None = None
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id}
        if query is not None:
            payload["query"] = query
        if limit is not None:
            payload["limit"] = limit
        return self._request("/api/suggest_clients_for_card", payload)

    def get_cashbox(
        self,
        cashbox_id: str,
        *,
        transaction_limit: int | None = None,
        transaction_offset: int | None = None,
    ) -> dict:
        payload: dict[str, object] = {"cashbox_id": cashbox_id}
        if transaction_limit is not None:
            payload["transaction_limit"] = _normalize_int(
                transaction_limit, default=300, minimum=1, maximum=5000
            )
        if transaction_offset is not None:
            payload["transaction_offset"] = _normalize_int(
                transaction_offset, default=0, maximum=1_000_000
            )
        return self._request("/api/get_cashbox", payload)

    def create_cashbox(
        self,
        name: str,
        *,
        expected_cashbox_ids: list[str] | None = None,
        attestation_run_id: str | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"name": name}
        if expected_cashbox_ids is not None:
            payload["expected_cashbox_ids"] = expected_cashbox_ids
        if attestation_run_id is not None:
            payload["attestation_run_id"] = attestation_run_id
        return self._request_with_identity("/api/create_cashbox", payload, actor_name=actor_name)

    def delete_cashbox(self, cashbox_id: str, *, actor_name: str | None = None) -> dict:
        return self._request_with_identity(
            "/api/delete_cashbox", {"cashbox_id": cashbox_id}, actor_name=actor_name
        )

    def create_cash_transaction(
        self,
        *,
        cashbox_id: str,
        direction: str,
        amount_minor: int | None = None,
        amount: str | int | float | None = None,
        note: str = "",
        expected_updated_at: str | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {
            "cashbox_id": cashbox_id,
            "direction": direction,
            "note": note,
        }
        if amount_minor is not None:
            payload["amount_minor"] = amount_minor
        elif amount is not None:
            payload["amount"] = amount
        if expected_updated_at is not None:
            payload["expected_updated_at"] = expected_updated_at
        return self._request_with_identity(
            "/api/create_cash_transaction", payload, actor_name=actor_name
        )

    def update_board_settings(self, *, board_scale: float, actor_name: str | None = None) -> dict:
        payload: dict[str, object] = {"board_scale": board_scale}
        return self._request_with_identity(
            "/api/update_board_settings", payload, actor_name=actor_name
        )

    def get_gpt_wall(
        self,
        *,
        include_archived: bool = True,
        event_limit: int | None = None,
        compact: bool | None = None,
    ) -> dict:
        payload: dict[str, object] = {"include_archived": include_archived}
        if event_limit is not None:
            payload["event_limit"] = event_limit
        if compact is not None:
            payload["compact"] = compact
        return self._request("/api/get_gpt_wall", payload, method="POST")

    def cleanup_card_content(self, *, card_id: str, actor_name: str | None = None) -> dict:
        return self._request_with_identity(
            "/api/cleanup_card_content", {"card_id": card_id}, actor_name=actor_name
        )

    def autofill_repair_order(
        self,
        *,
        card_id: str,
        overwrite: bool = False,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id, "overwrite": overwrite}
        return self._request_with_identity(
            "/api/autofill_repair_order", payload, actor_name=actor_name
        )

    def get_card_log(
        self,
        card_id: str,
        *,
        limit: int | None = None,
        compact: bool | None = None,
        include_full_details: bool | None = None,
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id}
        if limit is not None:
            payload["limit"] = limit
        if compact is not None:
            payload["compact"] = compact
        if include_full_details is not None:
            payload["include_full_details"] = include_full_details
        return self._request("/api/get_card_log", payload)

    def get_repair_order(self, card_id: str, *, create_if_missing: bool | None = None) -> dict:
        payload: dict[str, object] = {"card_id": card_id}
        if create_if_missing is not None:
            payload["create_if_missing"] = create_if_missing
        return self._request("/api/get_repair_order", payload)

    def preview_repair_order_reopen(self, card_id: str, *, expected_updated_at: str) -> dict:
        return self._request(
            "/api/preview_repair_order_reopen",
            {"card_id": card_id, "expected_updated_at": expected_updated_at},
        )

    def get_repair_order_cycles(self, card_id: str) -> dict:
        return self._request("/api/get_repair_order_cycles", {"card_id": card_id})

    def get_repair_order_text(self, card_id: str) -> dict:
        return self._request("/api/get_repair_order_text", {"card_id": card_id})

    def download_repair_order_print_pdf(
        self,
        *,
        card_id: str,
        selected_document_ids: list[str] | None = None,
        selected_template_ids: dict[str, str] | None = None,
        template_overrides: dict[str, str] | None = None,
        print_settings: dict[str, object] | None = None,
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id}
        if selected_document_ids is not None:
            payload["selected_document_ids"] = selected_document_ids
        if selected_template_ids is not None:
            payload["selected_template_ids"] = selected_template_ids
        if template_overrides is not None:
            payload["template_overrides"] = template_overrides
        if print_settings is not None:
            payload["print_settings"] = print_settings
        return self._request("/api/export_repair_order_print_pdf", payload)

    def create_document_without_card_pdf(
        self,
        *,
        request_text: str,
        document_type: str = "",
        manual_document: dict[str, object] | None = None,
        selected_template_ids: dict[str, str] | None = None,
        print_settings: dict[str, object] | None = None,
    ) -> dict:
        resolved_document_type = _normalize_manual_document_type(document_type, request_text)
        payload: dict[str, object] = {
            "document_without_card": True,
            "request_text": request_text,
            "selected_document_ids": [resolved_document_type],
        }
        if manual_document is not None:
            payload["manual_document"] = manual_document
        if selected_template_ids is not None:
            payload["selected_template_ids"] = selected_template_ids
        if print_settings is not None:
            payload["print_settings"] = print_settings
        return self._request("/api/export_repair_order_print_pdf", payload)

    def list_archived_cards(self, *, limit: int | None = None, compact: bool | None = None) -> dict:
        payload: dict[str, object] = {}
        if limit is not None:
            payload["limit"] = limit
        if compact is not None:
            payload["compact"] = compact
        if not payload:
            return self._request("/api/list_archived_cards", method="GET")
        return self._request("/api/list_archived_cards", payload, method="POST")

    def list_repair_orders(
        self,
        *,
        limit: int | None = None,
        status: str | None = None,
        card_id: str | None = None,
        number: str | None = None,
        query: str | None = None,
        sort_by: str | None = None,
        sort_dir: str | None = None,
        compact: bool | None = None,
        redact_private: bool | None = None,
    ) -> dict:
        payload: dict[str, object] = {}
        if limit is not None:
            payload["limit"] = limit
        if status:
            payload["status"] = status
        if card_id:
            payload["card_id"] = card_id
        if number:
            payload["number"] = number
        if query is not None:
            payload["query"] = query
        if sort_by:
            payload["sort_by"] = sort_by
        if sort_dir:
            payload["sort_dir"] = sort_dir
        if compact is not None:
            payload["compact"] = compact
        if redact_private is not None:
            payload["redact_private"] = redact_private
        if not payload:
            return self._request("/api/list_repair_orders", method="GET")
        return self._request("/api/list_repair_orders", payload, method="POST")

    def manager_board_scan(self, *, limit: int | None = None) -> dict:
        payload: dict[str, object] = {}
        if limit is not None:
            payload["limit"] = limit
        if not payload:
            return self._request("/api/manager_board_scan", method="GET")
        return self._request("/api/manager_board_scan", payload, method="POST")

    def list_ready_unpaid_cards(self, *, limit: int | None = None) -> dict:
        payload: dict[str, object] = {}
        if limit is not None:
            payload["limit"] = limit
        if not payload:
            return self._request("/api/list_ready_unpaid_cards", method="GET")
        return self._request("/api/list_ready_unpaid_cards", payload, method="POST")

    def triage_inbox_cards(self, *, limit: int | None = None) -> dict:
        payload: dict[str, object] = {}
        if limit is not None:
            payload["limit"] = limit
        if not payload:
            return self._request("/api/triage_inbox_cards", method="GET")
        return self._request("/api/triage_inbox_cards", payload, method="POST")

    def list_cards_missing_manager_data(
        self, *, limit: int | None = None, kinds: list[str] | None = None
    ) -> dict:
        payload: dict[str, object] = {}
        if limit is not None:
            payload["limit"] = limit
        if kinds is not None:
            payload["kinds"] = kinds
        if not payload:
            return self._request("/api/list_cards_missing_manager_data", method="GET")
        return self._request("/api/list_cards_missing_manager_data", payload, method="POST")

    def audit_repair_order_consistency(self, *, limit: int | None = None) -> dict:
        payload: dict[str, object] = {}
        if limit is not None:
            payload["limit"] = limit
        if not payload:
            return self._request("/api/audit_repair_order_consistency", method="GET")
        return self._request("/api/audit_repair_order_consistency", payload, method="POST")

    def audit_client_links(
        self,
        *,
        limit: int | None = None,
        candidate_limit: int | None = None,
        redact_private: bool | None = None,
    ) -> dict:
        payload: dict[str, object] = {}
        if limit is not None:
            payload["limit"] = limit
        if candidate_limit is not None:
            payload["candidate_limit"] = candidate_limit
        if redact_private is not None:
            payload["redact_private"] = redact_private
        if not payload:
            return self._request("/api/audit_client_links", method="GET")
        return self._request("/api/audit_client_links", payload, method="POST")

    def search_cards(
        self,
        *,
        query: str | None = None,
        include_archived: bool = False,
        column: str | None = None,
        tag: str | None = None,
        indicator: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> dict:
        payload: dict[str, object] = {"include_archived": include_archived}
        if query is not None:
            payload["query"] = query
        if column:
            payload["column"] = column
        if tag:
            payload["tag"] = tag
        if indicator:
            payload["indicator"] = indicator
        if status:
            payload["status"] = status
        if limit is not None:
            payload["limit"] = limit
        return self._request("/api/search_cards", payload)

    def create_card(
        self,
        *,
        vehicle: str = "",
        title: str,
        description: str = "",
        column: str | None = None,
        tags: list[str | dict[str, object]] | None = None,
        deadline: dict | None = None,
        vehicle_profile: dict[str, object] | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {
            "vehicle": vehicle,
            "title": title,
            "description": description,
        }
        if deadline is not None:
            payload["deadline"] = self._normalize_card_deadline(deadline)
        if column:
            payload["column"] = column
        if tags is not None:
            payload["tags"] = tags
        if vehicle_profile is not None:
            payload["vehicle_profile"] = vehicle_profile
        return self._request_with_identity("/api/create_card", payload, actor_name=actor_name)

    def update_card(
        self,
        *,
        card_id: str,
        vehicle: str | None = None,
        title: str | None = None,
        description: str | None = None,
        tags: list[str | dict[str, object]] | None = None,
        deadline: dict | None = None,
        vehicle_profile: dict[str, object] | None = None,
        actor_name: str | None = None,
        expected_updated_at: str | None = None,
        response_mode: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id}
        if vehicle is not None:
            payload["vehicle"] = vehicle
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        if tags is not None:
            payload["tags"] = tags
        if deadline is not None:
            payload["deadline"] = deadline
        if vehicle_profile is not None:
            payload["vehicle_profile"] = vehicle_profile
        if expected_updated_at:
            payload["expected_updated_at"] = expected_updated_at
        if response_mode:
            payload["response_mode"] = response_mode
        return self._request_with_identity("/api/update_card", payload, actor_name=actor_name)

    def set_card_board_summary(
        self,
        *,
        card_id: str,
        summary: str,
        actor_name: str | None = None,
        response_mode: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id, "summary": summary}
        if response_mode:
            payload["response_mode"] = response_mode
        return self._request_with_identity(
            "/api/set_card_board_summary", payload, actor_name=actor_name
        )

    def update_repair_order(
        self,
        *,
        card_id: str,
        repair_order: dict[str, object],
        expected_updated_at: str | None = None,
        expected_cashbox_id: str | None = None,
        expected_cashbox_updated_at: str | None = None,
        attestation_run_id: str | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id, "repair_order": repair_order}
        if expected_updated_at:
            payload["expected_updated_at"] = expected_updated_at
        if expected_cashbox_id:
            payload["expected_cashbox_id"] = expected_cashbox_id
        if expected_cashbox_updated_at:
            payload["expected_cashbox_updated_at"] = expected_cashbox_updated_at
        if attestation_run_id:
            payload["attestation_run_id"] = attestation_run_id
        return self._request_with_identity(
            "/api/update_repair_order", payload, actor_name=actor_name
        )

    def set_repair_order_status(
        self,
        *,
        card_id: str,
        status: str,
        expected_updated_at: str | None = None,
        idempotency_key: str | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id, "status": status}
        if expected_updated_at:
            payload["expected_updated_at"] = expected_updated_at
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        return self._request_with_identity(
            "/api/set_repair_order_status", payload, actor_name=actor_name
        )

    def reopen_repair_order(
        self,
        *,
        card_id: str,
        expected_updated_at: str,
        reason_code: str,
        reason_note: str,
        idempotency_key: str,
        target_column_id: str | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {
            "card_id": card_id,
            "expected_updated_at": expected_updated_at,
            "reason_code": reason_code,
            "reason_note": reason_note,
            "idempotency_key": idempotency_key,
        }
        if target_column_id:
            payload["target_column_id"] = target_column_id
        return self._request_with_identity(
            "/api/reopen_repair_order", payload, actor_name=actor_name
        )

    def mark_card_ready(
        self,
        *,
        card_id: str,
        actor_name: str | None = None,
    ) -> dict:
        return self._request_with_identity(
            "/api/mark_card_ready", {"card_id": card_id}, actor_name=actor_name
        )

    def replace_repair_order_works(
        self,
        *,
        card_id: str,
        rows: list[dict[str, object]],
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id, "rows": rows}
        return self._request_with_identity(
            "/api/replace_repair_order_works", payload, actor_name=actor_name
        )

    def replace_repair_order_materials(
        self,
        *,
        card_id: str,
        rows: list[dict[str, object]],
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id, "rows": rows}
        return self._request_with_identity(
            "/api/replace_repair_order_materials", payload, actor_name=actor_name
        )

    def update_sticky(
        self,
        *,
        sticky_id: str,
        text: str | None = None,
        deadline: dict | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"sticky_id": sticky_id}
        if text is not None:
            payload["text"] = text
        if deadline is not None:
            payload["deadline"] = deadline
        return self._request_with_identity("/api/update_sticky", payload, actor_name=actor_name)

    def move_sticky(self, *, sticky_id: str, x: int, y: int, actor_name: str | None = None) -> dict:
        payload: dict[str, object] = {"sticky_id": sticky_id, "x": x, "y": y}
        return self._request_with_identity("/api/move_sticky", payload, actor_name=actor_name)

    def delete_sticky(self, *, sticky_id: str, actor_name: str | None = None) -> dict:
        return self._request_with_identity(
            "/api/delete_sticky", {"sticky_id": sticky_id}, actor_name=actor_name
        )

    def set_card_deadline(
        self,
        *,
        card_id: str,
        deadline: dict,
        actor_name: str | None = None,
        response_mode: str | None = None,
    ) -> dict:
        payload = {"card_id": card_id, "deadline": deadline}
        if response_mode:
            payload["response_mode"] = response_mode
        return self._request_with_identity("/api/set_card_deadline", payload, actor_name=actor_name)

    def start_card_timer(
        self,
        *,
        card_id: str,
        deadline: dict | None = None,
        expected_updated_at: str | None = None,
        actor_name: str | None = None,
        response_mode: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id}
        if deadline is not None:
            payload["deadline"] = deadline
        if expected_updated_at:
            payload["expected_updated_at"] = expected_updated_at
        if response_mode:
            payload["response_mode"] = response_mode
        return self._request_with_identity("/api/start_card_timer", payload, actor_name=actor_name)

    def stop_card_timer(
        self,
        *,
        card_id: str,
        expected_updated_at: str | None = None,
        actor_name: str | None = None,
        response_mode: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id}
        if expected_updated_at:
            payload["expected_updated_at"] = expected_updated_at
        if response_mode:
            payload["response_mode"] = response_mode
        return self._request_with_identity("/api/stop_card_timer", payload, actor_name=actor_name)

    def set_card_indicator(
        self,
        *,
        card_id: str,
        indicator: str,
        actor_name: str | None = None,
        response_mode: str | None = None,
    ) -> dict:
        payload = {"card_id": card_id, "indicator": indicator}
        if response_mode:
            payload["response_mode"] = response_mode
        return self._request_with_identity(
            "/api/set_card_indicator", payload, actor_name=actor_name
        )

    def move_card(
        self,
        *,
        card_id: str,
        column: str,
        before_card_id: str | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id, "column": column}
        if before_card_id:
            payload["before_card_id"] = before_card_id
        return self._request_with_identity("/api/move_card", payload, actor_name=actor_name)

    def bulk_move_cards(
        self,
        *,
        card_ids: list[str],
        column: str,
        actor_name: str | None = None,
        response_mode: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {"card_ids": card_ids, "column": column}
        if response_mode:
            payload["response_mode"] = response_mode
        return self._request_with_identity("/api/bulk_move_cards", payload, actor_name=actor_name)

    def bulk_set_deadline_if_below(
        self,
        *,
        mode: str | None = None,
        min_total_seconds: int | None = None,
        target_total_seconds: int | None = None,
        limit: int | None = None,
        include_archived: bool | None = None,
        card_ids: list[str] | None = None,
        expected_updated_at_by_card_id: dict[str, str] | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {}
        if mode:
            payload["mode"] = mode
        if min_total_seconds is not None:
            payload["min_total_seconds"] = min_total_seconds
        if target_total_seconds is not None:
            payload["target_total_seconds"] = target_total_seconds
        if limit is not None:
            payload["limit"] = limit
        if include_archived is not None:
            payload["include_archived"] = include_archived
        if card_ids is not None:
            payload["card_ids"] = card_ids
        if expected_updated_at_by_card_id is not None:
            payload["expected_updated_at_by_card_id"] = expected_updated_at_by_card_id
        return self._request_with_identity(
            "/api/bulk_set_deadline_if_below", payload, actor_name=actor_name
        )

    def bulk_refresh_board_summaries(
        self,
        *,
        mode: str | None = None,
        limit: int | None = None,
        only_missing: bool | None = None,
        only_stale: bool | None = None,
        card_ids: list[str] | None = None,
        expected_updated_at_by_card_id: dict[str, str] | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {}
        if mode:
            payload["mode"] = mode
        if limit is not None:
            payload["limit"] = limit
        if only_missing is not None:
            payload["only_missing"] = only_missing
        if only_stale is not None:
            payload["only_stale"] = only_stale
        if card_ids is not None:
            payload["card_ids"] = card_ids
        if expected_updated_at_by_card_id is not None:
            payload["expected_updated_at_by_card_id"] = expected_updated_at_by_card_id
        return self._request_with_identity(
            "/api/bulk_refresh_board_summaries", payload, actor_name=actor_name
        )

    def cleanup_card(
        self,
        *,
        card_id: str,
        mode: str | None = None,
        actor_name: str | None = None,
        expected_updated_at: str | None = None,
        response_mode: str | None = None,
        refresh_summary: bool | None = None,
        summary: str | None = None,
        vehicle: str | None = None,
        title: str | None = None,
        description: str | None = None,
        tags: list[str | dict[str, object]] | None = None,
        deadline: dict | None = None,
        vehicle_profile: dict[str, object] | None = None,
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id}
        for key, value in {
            "mode": mode,
            "expected_updated_at": expected_updated_at,
            "response_mode": response_mode,
            "refresh_summary": refresh_summary,
            "summary": summary,
            "vehicle": vehicle,
            "title": title,
            "description": description,
            "tags": tags,
            "deadline": deadline,
            "vehicle_profile": vehicle_profile,
        }.items():
            if value is not None:
                payload[key] = value
        return self._request_with_identity("/api/cleanup_card", payload, actor_name=actor_name)

    def apply_ready_unpaid_followups(
        self,
        *,
        mode: str | None = None,
        target_total_seconds: int | None = None,
        limit: int | None = None,
        refresh_summary: bool | None = None,
        card_ids: list[str] | None = None,
        expected_updated_at_by_card_id: dict[str, str] | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {}
        if mode:
            payload["mode"] = mode
        if target_total_seconds is not None:
            payload["target_total_seconds"] = target_total_seconds
        if limit is not None:
            payload["limit"] = limit
        if refresh_summary is not None:
            payload["refresh_summary"] = refresh_summary
        if card_ids is not None:
            payload["card_ids"] = card_ids
        if expected_updated_at_by_card_id is not None:
            payload["expected_updated_at_by_card_id"] = expected_updated_at_by_card_id
        return self._request_with_identity(
            "/api/apply_ready_unpaid_followups", payload, actor_name=actor_name
        )

    def run_manager_operation(
        self,
        *,
        operation: str,
        payload: dict[str, object] | None = None,
        mode: str | None = None,
        actor_name: str | None = None,
        limit: int | None = None,
    ) -> dict:
        request_payload: dict[str, object] = {"operation": operation}
        if payload is not None:
            request_payload["payload"] = payload
        if mode:
            request_payload["mode"] = mode
        if limit is not None:
            request_payload["limit"] = limit
        return self._request_with_identity(
            "/api/run_manager_operation", request_payload, actor_name=actor_name
        )

    def rollback_manager_run(
        self,
        *,
        mode: str | None = None,
        rollback_actions: list[dict[str, object]] | None = None,
        actor_name: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {}
        if mode:
            payload["mode"] = mode
        if rollback_actions is not None:
            payload["rollback_actions"] = rollback_actions
        return self._request_with_identity(
            "/api/rollback_manager_run", payload, actor_name=actor_name
        )

    def archive_card(self, *, card_id: str, actor_name: str | None = None) -> dict:
        return self._request_with_identity(
            "/api/archive_card", {"card_id": card_id}, actor_name=actor_name
        )

    def restore_card(
        self, *, card_id: str, column: str | None = None, actor_name: str | None = None
    ) -> dict:
        payload: dict[str, object] = {"card_id": card_id}
        if column:
            payload["column"] = column
        return self._request_with_identity("/api/restore_card", payload, actor_name=actor_name)

    def list_overdue_cards(self, *, include_archived: bool = False) -> dict:
        return self._request(
            "/api/list_overdue_cards", {"include_archived": include_archived}, method="POST"
        )

    def _with_identity(
        self, payload: dict[str, object], *, actor_name: str | None = None
    ) -> dict[str, object]:
        enriched = dict(payload)
        enriched["source"] = self._default_source
        if actor_name:
            enriched["actor_name"] = actor_name
        return enriched

    def _request_optional_scalar_filter(self, path: str, *, key: str, value: object | None) -> dict:
        if value is None:
            return self._request(path, method="GET")
        return self._request(path, {key: value}, method="POST")

    def _request_readonly_query(self, path: str, payload: dict[str, object]) -> dict:
        query = urllib.parse.urlencode(
            {
                key: str(value).lower() if isinstance(value, bool) else value
                for key, value in payload.items()
                if value is not None
            }
        )
        return self._request(f"{path}?{query}" if query else path, method="GET")

    def _request_with_identity(
        self, path: str, payload: dict[str, object], *, actor_name: str | None = None
    ) -> dict:
        return self._request(path, self._with_identity(payload, actor_name=actor_name))

    def _normalize_card_deadline(self, deadline: dict | None) -> dict[str, int]:
        if not isinstance(deadline, dict):
            return {"days": 1, "hours": 0, "minutes": 0, "seconds": 0}
        normalized = {
            "days": self._normalize_deadline_part(deadline.get("days"), maximum=365),
            "hours": self._normalize_deadline_part(deadline.get("hours"), maximum=23),
            "minutes": self._normalize_deadline_part(deadline.get("minutes"), maximum=59),
            "seconds": self._normalize_deadline_part(deadline.get("seconds"), maximum=59),
        }
        total_seconds = self._normalize_deadline_part(
            deadline.get("total_seconds"), maximum=31_536_000
        )
        if total_seconds > 0:
            return {**normalized, "total_seconds": total_seconds}
        if not any(normalized.values()):
            return {"days": 1, "hours": 0, "minutes": 0, "seconds": 0}
        return normalized

    def _normalize_deadline_part(self, value: object, *, maximum: int) -> int:
        return _normalize_int(value, default=0, maximum=maximum)

    def _request(
        self,
        path: str,
        payload: dict | None = None,
        *,
        method: str = "POST",
        extra_headers: Mapping[str, str] | None = None,
        _allow_retry: bool = True,
    ) -> dict:
        data = None
        if payload is not None:
            try:
                data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except (OverflowError, TypeError, ValueError) as exc:
                message = f"Нельзя сериализовать JSON payload для {path}."
                raise BoardApiTransportError(message) from exc
        headers = {"Content-Type": "application/json"}
        authorization_header = _authorization_bearer_header(self._bearer_token)
        if authorization_header:
            headers["Authorization"] = authorization_header
        if extra_headers:
            for name, value in extra_headers.items():
                normalized_name = str(name or "").strip()
                normalized_value = str(value or "").strip()
                if (
                    normalized_name
                    in {
                        "X-Autostop-Agent-Identity",
                        "X-Autostop-Agent-Token",
                        OAUTH_AUDIT_ACTOR_HEADER,
                        OAUTH_AUDIT_ASSERTION_HEADER,
                        "X-Autostop-Release-Smoke-Revision",
                        "X-Autostop-Release-Smoke-Proof",
                    }
                    and normalized_value
                ):
                    headers[normalized_name] = normalized_value
        try:
            request = urllib.request.Request(
                self._compose_url(path),
                data=data,
                headers=headers,
                method=method,
            )
        except (TypeError, ValueError) as exc:
            message = f"Некорректный URL локального API для {path}."
            raise BoardApiTransportError(message) from exc
        try:
            with _urlopen_no_redirect(request, timeout=self._timeout_seconds) as response:
                parsed = self._parse_json_payload(
                    self._read_response_body(response, path=path), path=path
                )
                self._log("board_api_request path=%s status=%s", path, response.status)
                return parsed
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                message = f"Локальный API вернул перенаправление для {path}."
                raise BoardApiTransportError(message) from exc
            try:
                payload = self._parse_json_payload(
                    self._read_response_body(exc, path=path), path=path
                )
                self._log(
                    "board_api_request path=%s status=%s error=%s",
                    path,
                    exc.code,
                    payload.get("error"),
                )
                return payload
            finally:
                exc.close()
        except (OSError, ValueError, urllib.error.URLError, TimeoutError) as exc:
            if str(method or "POST").strip().upper() == "GET" and _allow_retry:
                self._log("board_api_request path=%s retry_after_transport_error=%s", path, exc)
                return self._request(
                    path,
                    payload,
                    method=method,
                    extra_headers=extra_headers,
                    _allow_retry=False,
                )
            message = f"Не удалось подключиться к локальному API по адресу {self.base_url}."
            raise BoardApiTransportError(message) from exc

    def _read_response_body(self, response, *, path: str) -> bytes:
        raw = response.read(_MAX_API_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_API_RESPONSE_BYTES:
            message = f"Локальный API вернул слишком большой JSON для {path}."
            raise BoardApiTransportError(message)
        return raw

    def _parse_json_payload(self, raw: bytes, *, path: str) -> dict:
        try:
            decoded = raw.decode("utf-8")
            parsed = json.loads(decoded, parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, ValueError, RecursionError) as parse_error:
            message = f"Локальный API вернул некорректный JSON для {path}."
            raise BoardApiTransportError(message) from parse_error
        if not isinstance(parsed, dict):
            message = f"Локальный API вернул JSON не-объект для {path}."
            raise BoardApiTransportError(message)
        return parsed

    def _log(self, message: str, *args) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)

    def _compose_url(self, path: str) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        if self.base_url.endswith("/api") and normalized_path.startswith("/api/"):
            normalized_path = normalized_path[4:]
        return f"{self.base_url}{normalized_path}"
