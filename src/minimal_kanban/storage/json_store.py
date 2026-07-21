from __future__ import annotations

import hashlib
import json
import math
import shutil
import time
from copy import deepcopy
from datetime import timedelta
from logging import Logger
from pathlib import Path
from typing import Any
from uuid import uuid4

import orjson

from ..config import get_app_data_dir, get_state_file
from ..json_safety import reject_deeply_nested_json
from ..models import (
    ARCHIVED_CARD_RETENTION_LIMIT,
    AUDIT_EVENT_RETENTION_DAYS,
    AUDIT_EVENT_RETENTION_LIMIT,
    DEFAULT_COLUMN_IDS,
    AuditEvent,
    Card,
    CashBox,
    CashTransaction,
    ClientProfile,
    Column,
    InventoryItem,
    InventoryMovement,
    StickyNote,
    parse_datetime,
    utc_now,
)
from ..performance import MeasuredRLock, record_timing
from ..services.ready_column import ensure_ready_column
from ..texts import COLUMN_LABELS_RU
from .change_feed_store import ChangeFeedPendingWriteError, ChangeFeedStore
from .file_lock import ProcessFileLock
from .limited_io import read_bytes_limited, read_text_limited

SLOW_STORAGE_OPERATION_MS = 250.0
_JSON_SAFE_MAX_DEPTH = 8
JSON_STORE_STATE_MAX_BYTES = 100 * 1024 * 1024


def default_columns() -> list[Column]:
    columns: list[Column] = []
    for position, column_id in enumerate(DEFAULT_COLUMN_IDS):
        columns.append(Column(id=column_id, label=COLUMN_LABELS_RU[column_id], position=position))
    return columns


def _json_safe_value(value: Any, *, depth: int = _JSON_SAFE_MAX_DEPTH) -> Any:
    if depth <= 0:
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item, depth=depth - 1)
            for key, item in value.items()
            if key is not None
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    return str(value)


def _json_safe_dict(value: Any) -> dict[str, Any]:
    safe = _json_safe_value(value)
    return safe if isinstance(safe, dict) else {}


def _serialized_state(
    state: dict[str, Any], *, already_safe: bool = False, fast_serializer: bool = False
) -> tuple[dict[str, Any], bytes, str]:
    safe_state = state if already_safe else _json_safe_dict(state)
    if fast_serializer and _supports_fast_state_serialization(safe_state):
        try:
            payload = orjson.dumps(safe_state)
        except orjson.JSONEncodeError:
            payload = _stdlib_state_payload(safe_state)
    else:
        payload = _stdlib_state_payload(safe_state)
    if len(payload) > JSON_STORE_STATE_MAX_BYTES:
        raise ValueError("state file is too large")
    fingerprint = hashlib.sha256(payload).hexdigest()
    return safe_state, payload, fingerprint


def _stdlib_state_payload(state: dict[str, Any]) -> bytes:
    return json.dumps(
        state,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _supports_fast_state_serialization(value: Any) -> bool:
    pending = [value]
    while pending:
        item = pending.pop()
        item_type = type(item)
        if item is None or item_type in {str, bool}:
            continue
        if item_type is int:
            if item < -(1 << 63) or item > (1 << 64) - 1:
                return False
            continue
        if item_type is float:
            if not math.isfinite(item):
                return False
            continue
        if item_type is dict:
            if any(type(key) is not str for key in item):
                return False
            pending.extend(item.values())
            continue
        if item_type in {list, tuple}:
            pending.extend(item)
            continue
        return False
    return True


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _domain_items(value: Any, expected_type: type) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, expected_type)]


DEFAULT_STATE = {
    "schema_version": 9,
    "columns": [column.to_dict() for column in default_columns()],
    "cards": [],
    "clients": [],
    "stickies": [],
    "cashboxes": [],
    "cash_transactions": [],
    "inventory_items": [],
    "inventory_movements": [],
    "events": [],
    "settings": {
        "has_seen_onboarding": False,
        "board_scale": 1.0,
        "ai_board_control": {
            "enabled": False,
            "interval_minutes": 20,
            "cooldown_minutes": 60,
        },
        "ready_column_id": "",
    },
}


class StateFileCorruptedError(RuntimeError):
    """Raised when state.json cannot be decoded without silently resetting data."""


class StateWriteConflictError(RuntimeError):
    """Raised when a cached bundle no longer matches the on-disk state."""


class JsonStore:
    def __init__(self, state_file: Path | None = None, logger: Logger | None = None) -> None:
        self._state_file = state_file or get_state_file()
        self._logger = logger
        self._lock = MeasuredRLock("store_lock")
        self._process_lock = ProcessFileLock(
            self._state_file.with_suffix(".lock"), metric_name="file_lock"
        )
        self._change_feed_store = ChangeFeedStore(self._state_file.with_name("change_feed.sqlite3"))
        self._change_feed_initialized = False
        self._change_feed_state_signature: tuple[int, int, int, int] | None = None
        self._read_cache_signature: tuple[int, int, int, int] | None = None
        self._read_cache_bundle: dict[str, Any] | None = None
        self._validated_state_signature: tuple[int, int, int, int] | None = None
        self._trusted_card_versions: dict[str, tuple[int, str]] = {}
        self._trusted_client_versions: dict[str, tuple[int, str]] = {}
        self._trusted_sticky_versions: dict[str, tuple[int, str]] = {}
        self._trusted_cashbox_versions: dict[str, tuple[int, str]] = {}
        self._trusted_cash_transaction_objects: set[int] = set()
        self._trusted_inventory_item_versions: dict[str, tuple[int, str]] = {}
        self._trusted_inventory_movement_objects: set[int] = set()
        self._trusted_event_objects: set[int] = set()
        get_app_data_dir().mkdir(parents=True, exist_ok=True)
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._state_file.exists():
            with self._process_lock.acquire():
                if not self._state_file.exists():
                    self._change_feed_store.initialize_baseline([], state=DEFAULT_STATE)
                    self._change_feed_initialized = True
                    self._write_state(DEFAULT_STATE)

    @property
    def base_dir(self) -> Path:
        return self._state_file.parent

    @property
    def change_feed_store(self) -> ChangeFeedStore:
        return self._change_feed_store

    def reconcile_change_feed(self) -> None:
        """Publish a recovered/external state change before serving owner feed reads."""

        with self._lock:
            with self._process_lock.acquire():
                current_signature = self._state_signature()
                if (
                    current_signature is not None
                    and current_signature == self._change_feed_state_signature
                    and not self._change_feed_store.has_pending_state_write()
                ):
                    return
                self._reconcile_change_feed_locked()

    def _reconcile_change_feed_locked(self) -> None:
        state = self._read_state()
        self._reconcile_change_feed_state_locked(state)

    def _reconcile_change_feed_state_locked(self, state: dict[str, Any]) -> None:
        safe_state = _json_safe_dict(state)
        fingerprint = self._state_file_fingerprint()
        self._change_feed_store.reconcile_state(
            fingerprint,
            safe_state.get("events"),
            state=safe_state,
        )
        self._change_feed_initialized = True
        self._change_feed_state_signature = self._state_signature()

    def read_bundle(self) -> dict[str, Any]:
        return self.read_bundle_with_signature()[0]

    def read_bundle_with_signature(
        self,
    ) -> tuple[dict[str, Any], tuple[int, int, int, int] | None]:
        started_at = time.perf_counter()
        with self._lock:
            with self._process_lock.acquire():
                signature = self._state_signature()
                if (
                    signature is not None
                    and signature == self._read_cache_signature
                    and self._read_cache_bundle is not None
                ):
                    return self._read_cache_bundle, signature
                state = self._read_state()
                if (
                    not self._change_feed_initialized
                    or signature != self._change_feed_state_signature
                    or self._change_feed_store.has_pending_state_write()
                ):
                    self._reconcile_change_feed_state_locked(state)
                columns, columns_repaired = self._normalize_columns(state)
                cards, cards_repaired = self._normalize_cards(state, columns)
                clients, clients_repaired = self._normalize_clients(state)
                stickies, stickies_repaired = self._normalize_stickies(state)
                cashboxes, cashboxes_repaired = self._normalize_cashboxes(state)
                cash_transactions, cash_transactions_repaired = self._normalize_cash_transactions(
                    state, cashboxes
                )
                inventory_items, inventory_items_repaired = self._normalize_inventory_items(state)
                (
                    inventory_movements,
                    inventory_movements_repaired,
                ) = self._normalize_inventory_movements(state, inventory_items)
                events, events_repaired = self._normalize_events(state)
                settings, settings_repaired = self._normalize_settings(state)
                ready_column_repaired = ensure_ready_column(columns, settings)[1]
                if (
                    columns_repaired
                    or cards_repaired
                    or clients_repaired
                    or stickies_repaired
                    or cashboxes_repaired
                    or cash_transactions_repaired
                    or inventory_items_repaired
                    or inventory_movements_repaired
                    or events_repaired
                    or settings_repaired
                    or ready_column_repaired
                ):
                    state = {
                        "schema_version": DEFAULT_STATE["schema_version"],
                        "columns": [column.to_dict() for column in columns],
                        "cards": [card.to_storage_dict() for card in cards],
                        "clients": [client.to_storage_dict() for client in clients],
                        "stickies": [sticky.to_storage_dict() for sticky in stickies],
                        "cashboxes": [cashbox.to_storage_dict() for cashbox in cashboxes],
                        "cash_transactions": [
                            transaction.to_storage_dict() for transaction in cash_transactions
                        ],
                        "inventory_items": [item.to_storage_dict() for item in inventory_items],
                        "inventory_movements": [
                            movement.to_storage_dict() for movement in inventory_movements
                        ],
                        "events": [event.to_dict() for event in events],
                        "settings": settings,
                    }
                    self._write_state(state)
                bundle = {
                    "columns": columns,
                    "cards": cards,
                    "clients": clients,
                    "stickies": stickies,
                    "cashboxes": cashboxes,
                    "cash_transactions": cash_transactions,
                    "inventory_items": inventory_items,
                    "inventory_movements": inventory_movements,
                    "events": events,
                    "settings": settings,
                }
                self._set_read_cache(bundle, self._state_signature())
                self._log_slow_operation("read_bundle", started_at)
                return bundle, self._read_cache_signature

    def write_bundle(
        self,
        *,
        columns: list[Column],
        cards: list[Card],
        clients: list[ClientProfile] | None = None,
        stickies: list[StickyNote] | None = None,
        cashboxes: list[CashBox] | None = None,
        cash_transactions: list[CashTransaction] | None = None,
        inventory_items: list[InventoryItem] | None = None,
        inventory_movements: list[InventoryMovement] | None = None,
        events: list[AuditEvent],
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started_at = time.perf_counter()
        with self._lock:
            with self._process_lock.acquire():
                # The caller may already have mutated the cached domain objects. Drop
                # their provenance before any fallible normalization or I/O so a
                # failed legacy write can never be observed as persisted state.
                self._invalidate_read_cache()
                normalize_started_at = time.perf_counter()
                current_state: dict[str, Any] | None = None
                if (
                    settings is None
                    or clients is None
                    or stickies is None
                    or cashboxes is None
                    or cash_transactions is None
                    or inventory_items is None
                    or inventory_movements is None
                ):
                    current_state = self._read_state()
                if clients is None:
                    assert current_state is not None
                    clients, _ = self._normalize_clients(current_state)
                if settings is None:
                    assert current_state is not None
                    settings, _ = self._normalize_settings(current_state)
                if stickies is None:
                    assert current_state is not None
                    stickies, _ = self._normalize_stickies(current_state)
                if cashboxes is None:
                    assert current_state is not None
                    cashboxes, _ = self._normalize_cashboxes(current_state)
                if cash_transactions is None:
                    assert current_state is not None
                    cash_transactions, _ = self._normalize_cash_transactions(
                        current_state, cashboxes or []
                    )
                if inventory_items is None:
                    assert current_state is not None
                    inventory_items, _ = self._normalize_inventory_items(current_state)
                if inventory_movements is None:
                    assert current_state is not None
                    inventory_movements, _ = self._normalize_inventory_movements(
                        current_state, inventory_items or []
                    )
                normalized_columns = self._normalize_columns_payload(columns)
                normalized_cards = self._normalize_cards_payload(cards, normalized_columns)
                normalized_clients = self._normalize_clients_payload(clients or [])
                normalized_stickies = self._normalize_stickies_payload(stickies or [])
                normalized_cashboxes = self._normalize_cashboxes_payload(cashboxes or [])
                normalized_cash_transactions = self._normalize_cash_transactions_payload(
                    cash_transactions or [], normalized_cashboxes
                )
                normalized_inventory_items = self._normalize_inventory_items_payload(
                    inventory_items or []
                )
                normalized_inventory_movements = self._normalize_inventory_movements_payload(
                    inventory_movements or [], normalized_inventory_items
                )
                normalized_events = self._normalize_events_payload(events)
                normalized_settings = self._normalize_settings_payload(settings)
                bundle = {
                    "columns": normalized_columns,
                    "cards": normalized_cards,
                    "clients": normalized_clients,
                    "stickies": normalized_stickies,
                    "cashboxes": normalized_cashboxes,
                    "cash_transactions": normalized_cash_transactions,
                    "inventory_items": normalized_inventory_items,
                    "inventory_movements": normalized_inventory_movements,
                    "events": normalized_events,
                    "settings": normalized_settings,
                }
                normalize_ms = (time.perf_counter() - normalize_started_at) * 1000
                record_timing("normalize", normalize_ms)
                state_started_at = time.perf_counter()
                state = self._state_from_bundle(bundle)
                state_conversion_ms = (time.perf_counter() - state_started_at) * 1000
                record_timing("serialize", state_conversion_ms)
                json_serialize_ms, write_ms = self._write_state(state)
                self._set_read_cache(bundle, self._state_signature())
                total_ms = (time.perf_counter() - started_at) * 1000
                record_timing("storage", total_ms)
                self._log_write_metrics(
                    mode="normalized",
                    total_ms=total_ms,
                    normalize_ms=normalize_ms,
                    serialize_ms=state_conversion_ms + json_serialize_ms,
                    write_ms=write_ms,
                )
                return bundle

    def write_cached_bundle(
        self,
        source_bundle: dict[str, Any],
        *,
        columns: list[Column],
        cards: list[Card],
        clients: list[ClientProfile],
        stickies: list[StickyNote],
        cashboxes: list[CashBox],
        cash_transactions: list[CashTransaction],
        inventory_items: list[InventoryItem],
        inventory_movements: list[InventoryMovement],
        events: list[AuditEvent],
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a bundle already normalized by this store, failing closed on drift."""

        started_at = time.perf_counter()
        with self._lock:
            with self._process_lock.acquire():
                current_signature = self._state_signature()
                if (
                    source_bundle is not self._read_cache_bundle
                    or current_signature is None
                    or current_signature != self._read_cache_signature
                ):
                    self._invalidate_read_cache()
                    record_timing("storage", (time.perf_counter() - started_at) * 1000)
                    raise StateWriteConflictError(
                        "Cached state no longer matches state.json; reload before writing."
                    )
                try:
                    normalize_started_at = time.perf_counter()
                    bundle = self._prepare_cached_bundle(
                        columns=columns,
                        cards=cards,
                        clients=clients,
                        stickies=stickies,
                        cashboxes=cashboxes,
                        cash_transactions=cash_transactions,
                        inventory_items=inventory_items,
                        inventory_movements=inventory_movements,
                        events=events,
                        settings=settings,
                    )
                    normalize_ms = (time.perf_counter() - normalize_started_at) * 1000
                    record_timing("normalize", normalize_ms)
                    state_started_at = time.perf_counter()
                    state = self._state_from_bundle(bundle)
                    state_conversion_ms = (time.perf_counter() - state_started_at) * 1000
                    record_timing("serialize", state_conversion_ms)
                    json_serialize_ms, write_ms = self._write_state(
                        state,
                        already_safe=True,
                        fast_serializer=True,
                    )
                except Exception:
                    self._invalidate_read_cache()
                    record_timing("storage", (time.perf_counter() - started_at) * 1000)
                    raise
                source_bundle.clear()
                source_bundle.update(bundle)
                self._set_read_cache(source_bundle, self._state_signature())
                total_ms = (time.perf_counter() - started_at) * 1000
                record_timing("storage", total_ms)
                self._log_write_metrics(
                    mode="cached",
                    total_ms=total_ms,
                    normalize_ms=normalize_ms,
                    serialize_ms=state_conversion_ms + json_serialize_ms,
                    write_ms=write_ms,
                )
                return source_bundle

    def read_cards(self) -> list[Card]:
        return self.read_bundle()["cards"]

    def write_cards(self, cards: list[Card]) -> None:
        bundle = self.read_bundle()
        self.write_bundle(
            columns=bundle["columns"],
            cards=cards,
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

    def read_columns(self) -> list[Column]:
        return self.read_bundle()["columns"]

    def write_columns(self, columns: list[Column]) -> None:
        bundle = self.read_bundle()
        self.write_bundle(
            columns=columns,
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

    def read_events(self) -> list[AuditEvent]:
        return self.read_bundle()["events"]

    def write_events(self, events: list[AuditEvent]) -> None:
        bundle = self.read_bundle()
        self.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            events=events,
            settings=bundle["settings"],
        )

    def get_setting(self, key: str, default=None):
        bundle = self.read_bundle()
        return bundle["settings"].get(key, default)

    def set_setting(self, key: str, value) -> None:
        bundle = self.read_bundle()
        normalized_key = str(key or "").strip()
        if not normalized_key:
            return
        bundle["settings"][normalized_key] = _json_safe_value(value)
        self.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=bundle["stickies"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

    def read_stickies(self) -> list[StickyNote]:
        return self.read_bundle()["stickies"]

    def write_stickies(self, stickies: list[StickyNote]) -> None:
        bundle = self.read_bundle()
        self.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=bundle["clients"],
            stickies=stickies,
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

    def read_cashboxes(self) -> list[CashBox]:
        return self.read_bundle()["cashboxes"]

    def read_cash_transactions(self) -> list[CashTransaction]:
        return self.read_bundle()["cash_transactions"]

    def read_inventory_items(self) -> list[InventoryItem]:
        return self.read_bundle()["inventory_items"]

    def read_inventory_movements(self) -> list[InventoryMovement]:
        return self.read_bundle()["inventory_movements"]

    def read_clients(self) -> list[ClientProfile]:
        return self.read_bundle()["clients"]

    def write_clients(self, clients: list[ClientProfile]) -> None:
        bundle = self.read_bundle()
        self.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            clients=clients,
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

    @staticmethod
    def _validated_domain_list(values: list[Any], expected_type: type, label: str) -> list[Any]:
        if not isinstance(values, list) or any(
            not isinstance(item, expected_type) for item in values
        ):
            raise ValueError(f"Cached {label} bundle contains an invalid item type.")
        return list(values)

    @staticmethod
    def _require_unique(values: list[Any], key, label: str) -> None:
        seen: set[Any] = set()
        for item in values:
            marker = key(item)
            if marker in seen:
                raise ValueError(f"Cached {label} bundle contains a duplicate key.")
            seen.add(marker)

    def _prepare_cached_bundle(
        self,
        *,
        columns: list[Column],
        cards: list[Card],
        clients: list[ClientProfile],
        stickies: list[StickyNote],
        cashboxes: list[CashBox],
        cash_transactions: list[CashTransaction],
        inventory_items: list[InventoryItem],
        inventory_movements: list[InventoryMovement],
        events: list[AuditEvent],
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        trusted_columns = self._validated_domain_list(columns, Column, "columns")
        if not trusted_columns:
            raise ValueError("Cached columns bundle cannot be empty.")
        self._require_unique(trusted_columns, lambda item: item.id, "columns")
        self._require_unique(trusted_columns, lambda item: item.label.casefold(), "column labels")
        for position, column in enumerate(trusted_columns):
            column.position = position
        trusted_columns = self._normalize_columns_payload(trusted_columns)

        trusted_cards = self._validated_domain_list(cards, Card, "cards")
        self._require_unique(trusted_cards, lambda item: item.id, "cards")
        valid_column_ids = {column.id for column in trusted_columns}
        if any(card.column not in valid_column_ids for card in trusted_cards):
            raise ValueError("Cached cards bundle references an unknown column.")
        trusted_cards = [
            card
            if self._trusted_card_versions.get(card.id) == (id(card), card.updated_at)
            else Card.from_dict(
                card.to_storage_dict(),
                valid_columns=valid_column_ids,
                default_column=trusted_columns[0].id,
                fallback_position=index,
            )
            for index, card in enumerate(trusted_cards)
        ]
        trusted_cards, _ = self._apply_card_retention(trusted_cards)
        self._normalize_card_positions(trusted_cards)

        trusted_clients = self._validated_domain_list(clients, ClientProfile, "clients")
        self._require_unique(trusted_clients, lambda item: item.id, "clients")
        trusted_clients = [
            client
            if self._trusted_client_versions.get(client.id) == (id(client), client.updated_at)
            else ClientProfile.from_dict(client.to_storage_dict())
            for client in trusted_clients
        ]
        trusted_clients.sort(key=lambda item: (item.name().casefold(), item.created_at, item.id))

        trusted_stickies = self._validated_domain_list(stickies, StickyNote, "stickies")
        self._require_unique(trusted_stickies, lambda item: item.id, "stickies")
        trusted_stickies = [
            sticky
            if self._trusted_sticky_versions.get(sticky.id) == (id(sticky), sticky.updated_at)
            else StickyNote.from_dict(sticky.to_storage_dict())
            for sticky in trusted_stickies
        ]

        trusted_cashboxes = self._validated_domain_list(cashboxes, CashBox, "cashboxes")
        self._require_unique(trusted_cashboxes, lambda item: item.id, "cashboxes")
        self._require_unique(trusted_cashboxes, lambda item: item.name.casefold(), "cashbox names")
        trusted_cashboxes = [
            cashbox
            if self._trusted_cashbox_versions.get(cashbox.id) == (id(cashbox), cashbox.updated_at)
            else CashBox.from_dict(cashbox.to_storage_dict())
            for cashbox in trusted_cashboxes
        ]
        trusted_cashboxes = self._normalize_cashboxes_payload(trusted_cashboxes)

        trusted_cash_transactions = self._validated_domain_list(
            cash_transactions, CashTransaction, "cash transactions"
        )
        self._require_unique(trusted_cash_transactions, lambda item: item.id, "cash transactions")
        valid_cashbox_ids = {item.id for item in trusted_cashboxes}
        if any(item.cashbox_id not in valid_cashbox_ids for item in trusted_cash_transactions):
            raise ValueError("Cached cash transaction references an unknown cashbox.")
        trusted_cash_transactions = [
            transaction
            if id(transaction) in self._trusted_cash_transaction_objects
            else CashTransaction.from_dict(transaction.to_storage_dict())
            for transaction in trusted_cash_transactions
        ]
        trusted_cash_transactions.sort(key=lambda item: (item.created_at, item.id))

        trusted_inventory_items = self._validated_domain_list(
            inventory_items, InventoryItem, "inventory items"
        )
        self._require_unique(trusted_inventory_items, lambda item: item.id, "inventory items")
        trusted_inventory_items = [
            item
            if self._trusted_inventory_item_versions.get(item.id) == (id(item), item.updated_at)
            else InventoryItem.from_dict(item.to_storage_dict())
            for item in trusted_inventory_items
        ]
        trusted_inventory_items.sort(
            key=lambda item: (item.name.casefold(), item.catalog_number.casefold(), item.id)
        )

        trusted_inventory_movements = self._validated_domain_list(
            inventory_movements, InventoryMovement, "inventory movements"
        )
        self._require_unique(
            trusted_inventory_movements, lambda item: item.id, "inventory movements"
        )
        valid_inventory_ids = {item.id for item in trusted_inventory_items}
        if any(item.item_id not in valid_inventory_ids for item in trusted_inventory_movements):
            raise ValueError("Cached inventory movement references an unknown item.")
        trusted_inventory_movements = [
            movement
            if id(movement) in self._trusted_inventory_movement_objects
            else InventoryMovement.from_dict(movement.to_storage_dict())
            for movement in trusted_inventory_movements
        ]
        trusted_inventory_movements.sort(key=lambda item: (item.created_at, item.id))

        trusted_events = self._validated_domain_list(events, AuditEvent, "events")
        self._require_unique(trusted_events, lambda item: item.id, "events")
        trusted_events = [
            event
            if id(event) in self._trusted_event_objects
            else AuditEvent.from_dict(_json_safe_dict(event.to_dict()))
            for event in trusted_events
        ]
        trusted_events, _ = self._apply_event_retention(trusted_events)
        normalized_settings = self._normalize_settings_payload(settings)
        return {
            "columns": trusted_columns,
            "cards": trusted_cards,
            "clients": trusted_clients,
            "stickies": trusted_stickies,
            "cashboxes": trusted_cashboxes,
            "cash_transactions": trusted_cash_transactions,
            "inventory_items": trusted_inventory_items,
            "inventory_movements": trusted_inventory_movements,
            "events": trusted_events,
            "settings": normalized_settings,
        }

    @staticmethod
    def _state_from_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": DEFAULT_STATE["schema_version"],
            "columns": [column.to_dict() for column in bundle["columns"]],
            "cards": [card.to_storage_dict() for card in bundle["cards"]],
            "clients": [client.to_storage_dict() for client in bundle["clients"]],
            "stickies": [sticky.to_storage_dict() for sticky in bundle["stickies"]],
            "cashboxes": [cashbox.to_storage_dict() for cashbox in bundle["cashboxes"]],
            "cash_transactions": [
                transaction.to_storage_dict() for transaction in bundle["cash_transactions"]
            ],
            "inventory_items": [item.to_storage_dict() for item in bundle["inventory_items"]],
            "inventory_movements": [
                movement.to_storage_dict() for movement in bundle["inventory_movements"]
            ],
            "events": [event.to_dict() for event in bundle["events"]],
            "settings": bundle["settings"],
        }

    def _read_state(self) -> dict:
        if not self._state_file.exists():
            return deepcopy(DEFAULT_STATE)
        try:
            payload = json.loads(
                self._read_state_text(),
                parse_constant=_reject_json_constant,
            )
            reject_deeply_nested_json(
                payload,
                message="state file JSON is too deeply nested",
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError):
            self._validated_state_signature = None
            backup = self._preserve_corrupted_state()
            self._log_warning(
                "Файл состояния поврежден, резервная копия сохранена: %s",
                backup.name,
            )
            raise StateFileCorruptedError(
                f"Файл состояния поврежден; резервная копия сохранена как {backup.name}."
            ) from None
        if not isinstance(payload, dict):
            self._validated_state_signature = None
            backup = self._preserve_corrupted_state()
            self._log_warning(
                "Файл состояния имеет некорректный корневой тип, резервная копия сохранена: %s",
                backup.name,
            )
            raise StateFileCorruptedError(
                f"Файл состояния поврежден; резервная копия сохранена как {backup.name}."
            )
        self._validated_state_signature = self._state_signature()
        return payload

    def _read_state_text(self) -> str:
        return read_text_limited(
            self._state_file,
            max_bytes=JSON_STORE_STATE_MAX_BYTES,
            label="state file",
        )

    def _corrupted_backup_path(self) -> Path:
        backup = self._state_file.with_suffix(".corrupted.json")
        if not backup.exists():
            return backup
        stem = self._state_file.with_suffix("").name
        for index in range(2, 1000):
            candidate = self._state_file.with_name(f"{stem}.corrupted-{index}.json")
            if not candidate.exists():
                return candidate
        return self._state_file.with_name(f"{stem}.corrupted-{time.time_ns()}.json")

    @staticmethod
    def _files_have_same_content(left: Path, right: Path) -> bool:
        try:
            if left.stat().st_size != right.stat().st_size:
                return False
            with left.open("rb") as left_handle, right.open("rb") as right_handle:
                while left_chunk := left_handle.read(1024 * 1024):
                    if left_chunk != right_handle.read(len(left_chunk)):
                        return False
                return right_handle.read(1) == b""
        except OSError:
            return False

    def _preserve_corrupted_state(self) -> Path:
        stem = self._state_file.with_suffix("").name
        for candidate in sorted(self._state_file.parent.glob(f"{stem}.corrupted*.json")):
            if candidate.is_file() and self._files_have_same_content(self._state_file, candidate):
                return candidate

        backup = self._corrupted_backup_path()
        temp_backup = backup.with_name(f".{backup.name}.{uuid4().hex}.tmp")
        try:
            shutil.copyfile(self._state_file, temp_backup)
            temp_backup.replace(backup)
        finally:
            temp_backup.unlink(missing_ok=True)
        return backup

    def _write_state(
        self,
        state: dict,
        *,
        already_safe: bool = False,
        fast_serializer: bool = False,
    ) -> tuple[float, float]:
        existing_signature = self._state_signature()
        if existing_signature is not None and existing_signature != self._validated_state_signature:
            self._read_state()
        serialize_started_at = time.perf_counter()
        safe_state, payload, fingerprint = _serialized_state(
            state,
            already_safe=already_safe,
            fast_serializer=fast_serializer,
        )
        if not self._change_feed_initialized:
            if self._state_file.exists():
                self._reconcile_change_feed_locked()
            else:
                self._change_feed_store.initialize_baseline([], state=safe_state)
                self._change_feed_initialized = True
        payload_bytes = len(payload)
        serialize_ms = (time.perf_counter() - serialize_started_at) * 1000
        record_timing("serialize", serialize_ms)
        feed_prepare_started_at = time.perf_counter()
        try:
            self._change_feed_store.prepare_state_write(
                fingerprint,
                safe_state.get("events"),
                state=safe_state,
            )
        except ChangeFeedPendingWriteError:
            self._reconcile_change_feed_locked()
            self._change_feed_store.prepare_state_write(
                fingerprint,
                safe_state.get("events"),
                state=safe_state,
            )
        finally:
            record_timing(
                "change_feed_prepare",
                (time.perf_counter() - feed_prepare_started_at) * 1000,
            )
        temp_file = self._state_file.with_name(f".{self._state_file.name}.{uuid4().hex}.tmp")
        write_started_at = time.perf_counter()
        try:
            temp_file.write_bytes(payload)
            temp_file.replace(self._state_file)
        except Exception:
            try:
                self._change_feed_store.abort_state_write(fingerprint)
            except Exception as abort_exc:  # pragma: no cover - restart reconciliation is durable
                self._log_warning("Change-feed outbox abort deferred: %s", abort_exc)
            raise
        finally:
            temp_file.unlink(missing_ok=True)
            write_ms = (time.perf_counter() - write_started_at) * 1000
            record_timing("write", write_ms)
        written_signature = self._state_signature()
        self._change_feed_state_signature = written_signature
        if written_signature is None or written_signature[3] != payload_bytes:
            self._change_feed_store.abort_state_write(fingerprint)
            raise OSError("state file post-write verification failed")
        feed_commit_started_at = time.perf_counter()
        try:
            self._change_feed_store.commit_state_write(fingerprint)
        except Exception as exc:  # pragma: no cover - staged outbox recovers on next access
            self._log_warning(
                "Change-feed publish deferred; durable outbox will reconcile: %s",
                exc,
            )
        finally:
            record_timing(
                "change_feed_commit",
                (time.perf_counter() - feed_commit_started_at) * 1000,
            )
        self._validated_state_signature = written_signature
        return serialize_ms, write_ms

    def _state_signature(self) -> tuple[int, int, int, int] | None:
        try:
            stat = self._state_file.stat()
        except FileNotFoundError:
            return None
        return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size

    def _state_file_fingerprint(self) -> str:
        payload = read_bytes_limited(
            self._state_file,
            max_bytes=JSON_STORE_STATE_MAX_BYTES,
            label="state file",
        )
        return hashlib.sha256(payload).hexdigest()

    def _invalidate_read_cache(self) -> None:
        self._read_cache_signature = None
        self._read_cache_bundle = None
        self._trusted_card_versions.clear()
        self._trusted_client_versions.clear()
        self._trusted_sticky_versions.clear()
        self._trusted_cashbox_versions.clear()
        self._trusted_cash_transaction_objects.clear()
        self._trusted_inventory_item_versions.clear()
        self._trusted_inventory_movement_objects.clear()
        self._trusted_event_objects.clear()

    def _set_read_cache(
        self,
        bundle: dict[str, Any],
        signature: tuple[int, int, int, int] | None,
    ) -> None:
        self._read_cache_signature = signature
        self._read_cache_bundle = bundle
        self._trusted_card_versions = {
            card.id: (id(card), card.updated_at) for card in bundle["cards"]
        }
        self._trusted_client_versions = {
            client.id: (id(client), client.updated_at) for client in bundle["clients"]
        }
        self._trusted_sticky_versions = {
            sticky.id: (id(sticky), sticky.updated_at) for sticky in bundle["stickies"]
        }
        self._trusted_cashbox_versions = {
            cashbox.id: (id(cashbox), cashbox.updated_at) for cashbox in bundle["cashboxes"]
        }
        self._trusted_cash_transaction_objects = {
            id(transaction) for transaction in bundle["cash_transactions"]
        }
        self._trusted_inventory_item_versions = {
            item.id: (id(item), item.updated_at) for item in bundle["inventory_items"]
        }
        self._trusted_inventory_movement_objects = {
            id(movement) for movement in bundle["inventory_movements"]
        }
        self._trusted_event_objects = {id(event) for event in bundle["events"]}

    def _log_write_metrics(
        self,
        *,
        mode: str,
        total_ms: float,
        normalize_ms: float,
        serialize_ms: float,
        write_ms: float,
    ) -> None:
        if self._logger is None:
            return
        try:
            state_size = self._state_file.stat().st_size
        except OSError:
            state_size = 0
        self._logger.debug(
            "json_store_write_metrics mode=%s total_ms=%.1f normalize_ms=%.1f "
            "serialize_ms=%.1f write_ms=%.1f state_bytes=%s",
            mode,
            total_ms,
            normalize_ms,
            serialize_ms,
            write_ms,
            state_size,
        )

    def _log_slow_operation(self, operation: str, started_at: float) -> None:
        duration_ms = (time.perf_counter() - started_at) * 1000
        if duration_ms < SLOW_STORAGE_OPERATION_MS or self._logger is None:
            return
        try:
            state_size = self._state_file.stat().st_size
        except OSError:
            state_size = 0
        self._logger.info(
            "json_store_slow_operation operation=%s duration_ms=%.1f state_bytes=%s",
            operation,
            duration_ms,
            state_size,
        )

    def _normalize_columns(self, state: dict) -> tuple[list[Column], bool]:
        raw_columns = state.get("columns", [])
        repaired = False
        if not isinstance(raw_columns, list):
            self._log_warning(
                "Повреждено поле columns в state.json, список колонок будет восстановлен."
            )
            raw_columns = []
            repaired = True

        parsed_columns: list[Column] = []
        seen_ids: set[str] = set()
        seen_labels: set[str] = set()

        for index, item in enumerate(raw_columns):
            if not isinstance(item, dict):
                repaired = True
                self._log_warning(
                    "Пропущена поврежденная колонка с индексом %s в state.json.", index
                )
                continue
            try:
                column = Column.from_dict(item, fallback_position=index)
            except (OverflowError, TypeError, ValueError):
                repaired = True
                self._log_warning(
                    "Пропущена некорректная колонка с индексом %s в state.json.", index
                )
                continue
            normalized_label = column.label.casefold()
            if column.id in seen_ids or normalized_label in seen_labels:
                repaired = True
                self._log_warning("Пропущена дублирующаяся колонка с id=%s.", column.id)
                continue
            seen_ids.add(column.id)
            seen_labels.add(normalized_label)
            parsed_columns.append(column)

        if not parsed_columns:
            repaired = True
            parsed_columns = default_columns()

        parsed_columns.sort(key=lambda item: (item.position, item.label.casefold(), item.id))
        for position, column in enumerate(parsed_columns):
            if column.position != position:
                repaired = True
                column.position = position
        return parsed_columns, repaired

    def _normalize_columns_payload(self, columns: list[Column]) -> list[Column]:
        if not columns:
            return default_columns()
        normalized: list[Column] = []
        seen_ids: set[str] = set()
        seen_labels: set[str] = set()
        for position, column in enumerate(_domain_items(columns, Column)):
            candidate = Column.from_dict(column.to_dict(), fallback_position=position)
            if candidate.id in seen_ids or candidate.label.casefold() in seen_labels:
                continue
            candidate.position = position
            seen_ids.add(candidate.id)
            seen_labels.add(candidate.label.casefold())
            normalized.append(candidate)
        return normalized or default_columns()

    def _normalize_cards(self, state: dict, columns: list[Column]) -> tuple[list[Card], bool]:
        raw_cards = state.get("cards", [])
        repaired = False
        if not isinstance(raw_cards, list):
            self._log_warning(
                "Повреждено поле cards в state.json, список карточек будет восстановлен."
            )
            raw_cards = []
            repaired = True

        valid_column_ids = {column.id for column in columns}
        default_column_id = columns[0].id
        cards: list[Card] = []
        for index, item in enumerate(raw_cards):
            if not isinstance(item, dict):
                repaired = True
                self._log_warning(
                    "Пропущена поврежденная карточка с индексом %s в state.json.", index
                )
                continue
            card = Card.from_dict(
                item,
                valid_columns=valid_column_ids,
                default_column=default_column_id,
                fallback_position=index,
            )
            cards.append(card)
            if item != card.to_storage_dict():
                repaired = True
        cards, retention_repaired = self._apply_card_retention(cards)
        if retention_repaired:
            repaired = True
        if self._normalize_card_positions(cards):
            repaired = True
        return cards, repaired

    def _normalize_cards_payload(self, cards: list[Card], columns: list[Column]) -> list[Card]:
        valid_columns = {column.id for column in columns}
        default_column = columns[0].id
        normalized: list[Card] = []
        for index, card in enumerate(_domain_items(cards, Card)):
            normalized.append(
                Card.from_dict(
                    card.to_storage_dict(),
                    valid_columns=valid_columns,
                    default_column=default_column,
                    fallback_position=index,
                )
            )
        normalized, _ = self._apply_card_retention(normalized)
        self._normalize_card_positions(normalized)
        return normalized

    def _normalize_card_positions(self, cards: list[Card]) -> bool:
        changed = False
        cards_by_column: dict[str, list[Card]] = {}
        for card in cards:
            cards_by_column.setdefault(card.column, []).append(card)
        for column_cards in cards_by_column.values():
            ordered = sorted(
                column_cards,
                key=lambda item: (item.position, item.created_at, item.updated_at, item.id),
            )
            for position, card in enumerate(ordered):
                if card.position != position:
                    card.position = position
                    changed = True
        return changed

    def _apply_card_retention(self, cards: list[Card]) -> tuple[list[Card], bool]:
        active_cards = [card for card in cards if not card.archived]
        archived_cards = [card for card in cards if card.archived]
        archived_cards.sort(
            key=lambda item: (
                parse_datetime(item.updated_at) or parse_datetime(item.created_at) or utc_now(),
                item.id,
            ),
            reverse=True,
        )
        archived_repair_order_cards = [
            card for card in archived_cards if not card.repair_order.is_empty()
        ]
        archived_plain_cards = [card for card in archived_cards if card.repair_order.is_empty()]
        retained_cards = (
            active_cards
            + archived_repair_order_cards
            + archived_plain_cards[:ARCHIVED_CARD_RETENTION_LIMIT]
        )
        return retained_cards, len(retained_cards) != len(cards)

    def _normalize_clients(self, state: dict) -> tuple[list[ClientProfile], bool]:
        raw_clients = state.get("clients", [])
        repaired = False
        if not isinstance(raw_clients, list):
            raw_clients = []
            repaired = True
        clients: list[ClientProfile] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(raw_clients):
            if not isinstance(item, dict):
                repaired = True
                self._log_warning("Пропущен поврежденный клиент с индексом %s в state.json.", index)
                continue
            try:
                client = ClientProfile.from_dict(item)
            except (OverflowError, TypeError, ValueError):
                repaired = True
                self._log_warning("Пропущен некорректный клиент с индексом %s в state.json.", index)
                continue
            if client.id in seen_ids:
                repaired = True
                self._log_warning("Пропущен дублирующийся клиент с id=%s.", client.id)
                continue
            seen_ids.add(client.id)
            clients.append(client)
            if item != client.to_storage_dict():
                repaired = True
        clients.sort(key=lambda item: (item.name().casefold(), item.created_at, item.id))
        return clients, repaired

    def _normalize_clients_payload(self, clients: list[ClientProfile]) -> list[ClientProfile]:
        normalized: list[ClientProfile] = []
        seen_ids: set[str] = set()
        for client in _domain_items(clients, ClientProfile):
            candidate = ClientProfile.from_dict(client.to_storage_dict())
            if candidate.id in seen_ids:
                continue
            seen_ids.add(candidate.id)
            normalized.append(candidate)
        normalized.sort(key=lambda item: (item.name().casefold(), item.created_at, item.id))
        return normalized

    def _normalize_stickies(self, state: dict) -> tuple[list[StickyNote], bool]:
        raw_stickies = state.get("stickies", [])
        repaired = False
        if not isinstance(raw_stickies, list):
            self._log_warning(
                "Полевое stickies в state.json повреждено, список стикеров будет восстановлен."
            )
            raw_stickies = []
            repaired = True

        parsed_stickies: list[StickyNote] = []
        seen_ids: set[str] = set()
        for index, item in enumerate(raw_stickies):
            if not isinstance(item, dict):
                repaired = True
                self._log_warning("Пропущен поврежденный стикер с индексом %s в state.json.", index)
                continue
            try:
                sticky = StickyNote.from_dict(item)
            except (OverflowError, TypeError, ValueError):
                repaired = True
                self._log_warning("Пропущен некорректный стикер с индексом %s в state.json.", index)
                continue
            if sticky.id in seen_ids:
                repaired = True
                self._log_warning("Пропущен дублирующийся стикер с id=%s.", sticky.id)
                continue
            seen_ids.add(sticky.id)
            parsed_stickies.append(sticky)

        return parsed_stickies, repaired

    def _normalize_cashboxes(self, state: dict) -> tuple[list[CashBox], bool]:
        raw_cashboxes = state.get("cashboxes", [])
        repaired = False
        if not isinstance(raw_cashboxes, list):
            raw_cashboxes = []
            repaired = True
        parsed_cashboxes: list[CashBox] = []
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for index, item in enumerate(raw_cashboxes):
            if not isinstance(item, dict):
                repaired = True
                continue
            try:
                cashbox = CashBox.from_dict(item)
            except (OverflowError, TypeError, ValueError):
                repaired = True
                continue
            if cashbox.order != index:
                repaired = True
                cashbox.order = index
            normalized_name = cashbox.name.casefold()
            if cashbox.id in seen_ids or normalized_name in seen_names:
                repaired = True
                continue
            seen_ids.add(cashbox.id)
            seen_names.add(normalized_name)
            parsed_cashboxes.append(cashbox)
        parsed_cashboxes.sort(key=lambda item: (item.order, item.name.casefold(), item.id))
        return parsed_cashboxes, repaired

    def _normalize_cash_transactions(
        self, state: dict, cashboxes: list[CashBox]
    ) -> tuple[list[CashTransaction], bool]:
        raw_transactions = state.get("cash_transactions", [])
        repaired = False
        if not isinstance(raw_transactions, list):
            raw_transactions = []
            repaired = True
        valid_cashbox_ids = {item.id for item in cashboxes}
        parsed_transactions: list[CashTransaction] = []
        seen_ids: set[str] = set()
        for item in raw_transactions:
            if not isinstance(item, dict):
                repaired = True
                continue
            try:
                transaction = CashTransaction.from_dict(item)
            except (OverflowError, TypeError, ValueError):
                repaired = True
                continue
            if transaction.id in seen_ids or transaction.cashbox_id not in valid_cashbox_ids:
                repaired = True
                continue
            seen_ids.add(transaction.id)
            parsed_transactions.append(transaction)
        parsed_transactions.sort(key=lambda item: (item.created_at, item.id))
        return parsed_transactions, repaired

    def _normalize_inventory_items(self, state: dict) -> tuple[list[InventoryItem], bool]:
        raw_items = state.get("inventory_items", [])
        repaired = False
        if not isinstance(raw_items, list):
            raw_items = []
            repaired = True
        parsed_items: list[InventoryItem] = []
        seen_ids: set[str] = set()
        for item in raw_items:
            if not isinstance(item, dict):
                repaired = True
                continue
            try:
                inventory_item = InventoryItem.from_dict(item)
            except (OverflowError, TypeError, ValueError):
                repaired = True
                continue
            if inventory_item.id in seen_ids:
                repaired = True
                continue
            seen_ids.add(inventory_item.id)
            parsed_items.append(inventory_item)
        parsed_items.sort(
            key=lambda item: (item.name.casefold(), item.catalog_number.casefold(), item.id)
        )
        return parsed_items, repaired

    def _normalize_inventory_movements(
        self, state: dict, inventory_items: list[InventoryItem]
    ) -> tuple[list[InventoryMovement], bool]:
        raw_movements = state.get("inventory_movements", [])
        repaired = False
        if not isinstance(raw_movements, list):
            raw_movements = []
            repaired = True
        valid_item_ids = {item.id for item in inventory_items}
        parsed_movements: list[InventoryMovement] = []
        seen_ids: set[str] = set()
        for item in raw_movements:
            if not isinstance(item, dict):
                repaired = True
                continue
            try:
                movement = InventoryMovement.from_dict(item)
            except (OverflowError, TypeError, ValueError):
                repaired = True
                continue
            if movement.id in seen_ids or movement.item_id not in valid_item_ids:
                repaired = True
                continue
            seen_ids.add(movement.id)
            parsed_movements.append(movement)
        parsed_movements.sort(key=lambda item: (item.created_at, item.id))
        return parsed_movements, repaired

    def _normalize_stickies_payload(self, stickies: list[StickyNote]) -> list[StickyNote]:
        normalized: list[StickyNote] = []
        seen_ids: set[str] = set()
        for sticky in _domain_items(stickies, StickyNote):
            candidate = StickyNote.from_dict(sticky.to_storage_dict())
            if candidate.id in seen_ids:
                continue
            seen_ids.add(candidate.id)
            normalized.append(candidate)
        return normalized

    def _normalize_events(self, state: dict) -> tuple[list[AuditEvent], bool]:
        raw_events = state.get("events", [])
        repaired = False
        if not isinstance(raw_events, list):
            self._log_warning("Повреждено поле events в state.json, журнал будет восстановлен.")
            raw_events = []
            repaired = True

        events: list[AuditEvent] = []
        for index, item in enumerate(raw_events):
            if not isinstance(item, dict):
                repaired = True
                self._log_warning("Пропущена поврежденная запись журнала с индексом %s.", index)
                continue
            try:
                event = AuditEvent.from_dict(item)
            except (OverflowError, TypeError, ValueError):
                repaired = True
                self._log_warning("Пропущена некорректная запись журнала с индексом %s.", index)
                continue
            events.append(event)
            if item != event.to_dict():
                repaired = True
        events, retention_repaired = self._apply_event_retention(events)
        if retention_repaired:
            repaired = True
        return events, repaired

    def _normalize_events_payload(self, events: list[AuditEvent]) -> list[AuditEvent]:
        normalized: list[AuditEvent] = []
        for event in _domain_items(events, AuditEvent):
            try:
                normalized.append(AuditEvent.from_dict(_json_safe_dict(event.to_dict())))
            except (OverflowError, TypeError, ValueError):
                continue
        normalized, _ = self._apply_event_retention(normalized)
        return normalized

    def _normalize_cashboxes_payload(self, cashboxes: list[CashBox]) -> list[CashBox]:
        normalized: list[CashBox] = []
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        ordered_cashboxes = sorted(
            _domain_items(cashboxes, CashBox),
            key=lambda item: (item.order, item.name.casefold(), item.id),
        )
        for index, item in enumerate(ordered_cashboxes):
            if not isinstance(item, CashBox):
                continue
            if item.order != index:
                item = CashBox(
                    id=item.id,
                    name=item.name,
                    order=index,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                )
            if item.id in seen_ids or item.name.casefold() in seen_names:
                continue
            seen_ids.add(item.id)
            seen_names.add(item.name.casefold())
            normalized.append(item)
        return normalized

    def _normalize_cash_transactions_payload(
        self,
        transactions: list[CashTransaction],
        cashboxes: list[CashBox],
    ) -> list[CashTransaction]:
        normalized: list[CashTransaction] = []
        valid_cashbox_ids = {item.id for item in cashboxes}
        seen_ids: set[str] = set()
        for item in _domain_items(transactions, CashTransaction):
            if item.id in seen_ids or item.cashbox_id not in valid_cashbox_ids:
                continue
            seen_ids.add(item.id)
            normalized.append(item)
        normalized.sort(key=lambda item: (item.created_at, item.id))
        return normalized

    def _normalize_inventory_items_payload(
        self, inventory_items: list[InventoryItem]
    ) -> list[InventoryItem]:
        normalized: list[InventoryItem] = []
        seen_ids: set[str] = set()
        ordered_items = sorted(
            _domain_items(inventory_items, InventoryItem),
            key=lambda item: (item.name.casefold(), item.catalog_number.casefold(), item.id),
        )
        for item in ordered_items:
            if not isinstance(item, InventoryItem):
                continue
            candidate = InventoryItem.from_dict(item.to_storage_dict())
            if candidate.id in seen_ids:
                continue
            seen_ids.add(candidate.id)
            normalized.append(candidate)
        return normalized

    def _normalize_inventory_movements_payload(
        self,
        movements: list[InventoryMovement],
        inventory_items: list[InventoryItem],
    ) -> list[InventoryMovement]:
        normalized: list[InventoryMovement] = []
        valid_item_ids = {item.id for item in inventory_items}
        seen_ids: set[str] = set()
        for item in _domain_items(movements, InventoryMovement):
            if item.id in seen_ids or item.item_id not in valid_item_ids:
                continue
            seen_ids.add(item.id)
            normalized.append(InventoryMovement.from_dict(item.to_storage_dict()))
        normalized.sort(key=lambda item: (item.created_at, item.id))
        return normalized

    def _apply_event_retention(self, events: list[AuditEvent]) -> tuple[list[AuditEvent], bool]:
        window_start = utc_now() - timedelta(days=AUDIT_EVENT_RETENTION_DAYS)
        retained_events: list[AuditEvent] = []
        changed = False
        for event in events:
            timestamp = parse_datetime(event.timestamp)
            if timestamp is None or timestamp < window_start:
                changed = True
                continue
            retained_events.append(event)
        retained_events.sort(
            key=lambda item: (
                parse_datetime(item.timestamp) or utc_now(),
                item.id,
            )
        )
        if len(retained_events) > AUDIT_EVENT_RETENTION_LIMIT:
            retained_events = retained_events[-AUDIT_EVENT_RETENTION_LIMIT:]
            changed = True
        return retained_events, changed

    def _normalize_settings(self, state: dict) -> tuple[dict[str, Any], bool]:
        settings = state.get("settings", {})
        if not isinstance(settings, dict):
            self._log_warning("Повреждено поле settings в state.json, настройки будут сброшены.")
            return deepcopy(DEFAULT_STATE["settings"]), True
        normalized = self._normalize_settings_payload(settings)
        repaired = normalized != settings
        return normalized, repaired

    def _normalize_settings_payload(self, settings: Any) -> dict[str, Any]:
        if not isinstance(settings, dict):
            return deepcopy(DEFAULT_STATE["settings"])
        safe_settings = _json_safe_dict(settings)
        normalized = deepcopy(DEFAULT_STATE["settings"])
        normalized.update(safe_settings)
        board_control_settings = safe_settings.get("ai_board_control", {})
        if not isinstance(board_control_settings, dict):
            board_control_settings = {}
        normalized_board_control = deepcopy(DEFAULT_STATE["settings"]["ai_board_control"])
        normalized_board_control.update(_json_safe_dict(board_control_settings))
        normalized["ai_board_control"] = normalized_board_control
        return normalized

    def _log_warning(self, message: str, *args) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)
