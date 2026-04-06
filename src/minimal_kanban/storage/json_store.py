from __future__ import annotations

import json
from copy import deepcopy
from datetime import timedelta
from logging import Logger
from pathlib import Path
import threading
from typing import Any

from ..config import get_app_data_dir, get_state_file
from ..models import (
    ARCHIVED_CARD_RETENTION_LIMIT,
    AUDIT_EVENT_RETENTION_DAYS,
    AUDIT_EVENT_RETENTION_LIMIT,
    AuditEvent,
    Card,
    CashBox,
    CashTransaction,
    Column,
    DEFAULT_COLUMN_IDS,
    StickyNote,
    parse_datetime,
    utc_now,
)
from ..texts import COLUMN_LABELS_RU
from .file_lock import ProcessFileLock


def default_columns() -> list[Column]:
    columns: list[Column] = []
    for position, column_id in enumerate(DEFAULT_COLUMN_IDS):
        columns.append(Column(id=column_id, label=COLUMN_LABELS_RU[column_id], position=position))
    return columns


DEFAULT_STATE = {
    "schema_version": 7,
    "columns": [column.to_dict() for column in default_columns()],
    "cards": [],
    "stickies": [],
    "cashboxes": [],
    "cash_transactions": [],
    "events": [],
    "settings": {
        "has_seen_onboarding": False,
        "board_scale": 1.0,
    },
}


class JsonStore:
    def __init__(self, state_file: Path | None = None, logger: Logger | None = None) -> None:
        self._state_file = state_file or get_state_file()
        self._logger = logger
        self._lock = threading.RLock()
        self._process_lock = ProcessFileLock(self._state_file.with_suffix(".lock"))
        get_app_data_dir().mkdir(parents=True, exist_ok=True)
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._state_file.exists():
            with self._process_lock.acquire():
                self._write_state(DEFAULT_STATE)

    @property
    def base_dir(self) -> Path:
        return self._state_file.parent

    def read_bundle(self) -> dict[str, Any]:
        with self._lock:
            with self._process_lock.acquire():
                state = self._read_state()
                columns, columns_repaired = self._normalize_columns(state)
                cards, cards_repaired = self._normalize_cards(state, columns)
                stickies, stickies_repaired = self._normalize_stickies(state)
                cashboxes, cashboxes_repaired = self._normalize_cashboxes(state)
                cash_transactions, cash_transactions_repaired = self._normalize_cash_transactions(state, cashboxes)
                events, events_repaired = self._normalize_events(state)
                settings, settings_repaired = self._normalize_settings(state)
                if columns_repaired or cards_repaired or stickies_repaired or cashboxes_repaired or cash_transactions_repaired or events_repaired or settings_repaired:
                    state = {
                        "schema_version": DEFAULT_STATE["schema_version"],
                        "columns": [column.to_dict() for column in columns],
                        "cards": [card.to_storage_dict() for card in cards],
                        "stickies": [sticky.to_storage_dict() for sticky in stickies],
                        "cashboxes": [cashbox.to_storage_dict() for cashbox in cashboxes],
                        "cash_transactions": [transaction.to_storage_dict() for transaction in cash_transactions],
                        "events": [event.to_dict() for event in events],
                        "settings": settings,
                    }
                    self._write_state(state)
                return {
                    "columns": columns,
                    "cards": cards,
                    "stickies": stickies,
                    "cashboxes": cashboxes,
                    "cash_transactions": cash_transactions,
                    "events": events,
                    "settings": settings,
                }

    def write_bundle(
        self,
        *,
        columns: list[Column],
        cards: list[Card],
        stickies: list[StickyNote] | None = None,
        cashboxes: list[CashBox] | None = None,
        cash_transactions: list[CashTransaction] | None = None,
        events: list[AuditEvent],
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            with self._process_lock.acquire():
                current_state: dict[str, Any] | None = None
                if settings is None or stickies is None or cashboxes is None or cash_transactions is None:
                    current_state = self._read_state()
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
                    cash_transactions, _ = self._normalize_cash_transactions(current_state, cashboxes or [])
                normalized_columns = self._normalize_columns_payload(columns)
                normalized_cards = self._normalize_cards_payload(cards, normalized_columns)
                normalized_stickies = self._normalize_stickies_payload(stickies or [])
                normalized_cashboxes = self._normalize_cashboxes_payload(cashboxes or [])
                normalized_cash_transactions = self._normalize_cash_transactions_payload(cash_transactions or [], normalized_cashboxes)
                normalized_events = self._normalize_events_payload(events)
                state = {
                    "schema_version": DEFAULT_STATE["schema_version"],
                    "columns": [column.to_dict() for column in normalized_columns],
                    "cards": [card.to_storage_dict() for card in normalized_cards],
                    "stickies": [sticky.to_storage_dict() for sticky in normalized_stickies],
                    "cashboxes": [cashbox.to_storage_dict() for cashbox in normalized_cashboxes],
                    "cash_transactions": [transaction.to_storage_dict() for transaction in normalized_cash_transactions],
                    "events": [event.to_dict() for event in normalized_events],
                    "settings": settings if isinstance(settings, dict) else deepcopy(DEFAULT_STATE["settings"]),
                }
                self._write_state(state)
                return {
                    "columns": normalized_columns,
                    "cards": normalized_cards,
                    "stickies": normalized_stickies,
                    "cashboxes": normalized_cashboxes,
                    "cash_transactions": normalized_cash_transactions,
                    "events": normalized_events,
                    "settings": state["settings"],
                }

    def read_cards(self) -> list[Card]:
        return self.read_bundle()["cards"]

    def write_cards(self, cards: list[Card]) -> None:
        bundle = self.read_bundle()
        self.write_bundle(
            columns=bundle["columns"],
            cards=cards,
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
            stickies=bundle["stickies"],
            events=events,
            settings=bundle["settings"],
        )

    def get_setting(self, key: str, default=None):
        bundle = self.read_bundle()
        return bundle["settings"].get(key, default)

    def set_setting(self, key: str, value) -> None:
        bundle = self.read_bundle()
        bundle["settings"][key] = value
        self.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
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

    def _read_state(self) -> dict:
        if not self._state_file.exists():
            return deepcopy(DEFAULT_STATE)
        try:
            return json.loads(self._state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            backup = self._state_file.with_suffix(".corrupted.json")
            self._log_warning(
                "Файл состояния поврежден, выполняется резервное копирование: %s",
                backup.name,
            )
            if backup.exists():
                backup.unlink()
            self._state_file.replace(backup)
            self._write_state(DEFAULT_STATE)
            return deepcopy(DEFAULT_STATE)

    def _write_state(self, state: dict) -> None:
        payload = json.dumps(state, ensure_ascii=False, indent=2)
        temp_file = self._state_file.with_suffix(".tmp")
        temp_file.write_text(payload, encoding="utf-8")
        temp_file.replace(self._state_file)

    def _normalize_columns(self, state: dict) -> tuple[list[Column], bool]:
        raw_columns = state.get("columns", [])
        repaired = False
        if not isinstance(raw_columns, list):
            self._log_warning("Повреждено поле columns в state.json, список колонок будет восстановлен.")
            raw_columns = []
            repaired = True

        parsed_columns: list[Column] = []
        seen_ids: set[str] = set()
        seen_labels: set[str] = set()

        for index, item in enumerate(raw_columns):
            if not isinstance(item, dict):
                repaired = True
                self._log_warning("Пропущена поврежденная колонка с индексом %s в state.json.", index)
                continue
            try:
                column = Column.from_dict(item, fallback_position=index)
            except (TypeError, ValueError):
                repaired = True
                self._log_warning("Пропущена некорректная колонка с индексом %s в state.json.", index)
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
        for position, column in enumerate(columns):
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
            self._log_warning("Повреждено поле cards в state.json, список карточек будет восстановлен.")
            raw_cards = []
            repaired = True

        valid_column_ids = {column.id for column in columns}
        default_column_id = columns[0].id
        cards: list[Card] = []
        for index, item in enumerate(raw_cards):
            if not isinstance(item, dict):
                repaired = True
                self._log_warning("Пропущена поврежденная карточка с индексом %s в state.json.", index)
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
        normalized = [
            Card.from_dict(
                card.to_storage_dict(),
                valid_columns=valid_columns,
                default_column=default_column,
                fallback_position=index,
            )
            for index, card in enumerate(cards)
        ]
        normalized, _ = self._apply_card_retention(normalized)
        self._normalize_card_positions(normalized)
        return normalized

    def _normalize_card_positions(self, cards: list[Card]) -> bool:
        changed = False
        cards_by_column: dict[str, list[Card]] = {}
        for card in cards:
            cards_by_column.setdefault(card.column, []).append(card)
        for column_cards in cards_by_column.values():
            ordered = sorted(column_cards, key=lambda item: (item.position, item.created_at, item.updated_at, item.id))
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
        retained_cards = active_cards + archived_cards[:ARCHIVED_CARD_RETENTION_LIMIT]
        return retained_cards, len(retained_cards) != len(cards)

    def _normalize_stickies(self, state: dict) -> tuple[list[StickyNote], bool]:
        raw_stickies = state.get("stickies", [])
        repaired = False
        if not isinstance(raw_stickies, list):
            self._log_warning("Полевое stickies в state.json повреждено, список стикеров будет восстановлен.")
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
            except (TypeError, ValueError):
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
        for item in raw_cashboxes:
            if not isinstance(item, dict):
                repaired = True
                continue
            try:
                cashbox = CashBox.from_dict(item)
            except (TypeError, ValueError):
                repaired = True
                continue
            normalized_name = cashbox.name.casefold()
            if cashbox.id in seen_ids or normalized_name in seen_names:
                repaired = True
                continue
            seen_ids.add(cashbox.id)
            seen_names.add(normalized_name)
            parsed_cashboxes.append(cashbox)
        parsed_cashboxes.sort(key=lambda item: (item.name.casefold(), item.id))
        return parsed_cashboxes, repaired

    def _normalize_cash_transactions(self, state: dict, cashboxes: list[CashBox]) -> tuple[list[CashTransaction], bool]:
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
            except (TypeError, ValueError):
                repaired = True
                continue
            if transaction.id in seen_ids or transaction.cashbox_id not in valid_cashbox_ids:
                repaired = True
                continue
            seen_ids.add(transaction.id)
            parsed_transactions.append(transaction)
        parsed_transactions.sort(key=lambda item: (item.created_at, item.id))
        return parsed_transactions, repaired

    def _normalize_stickies_payload(self, stickies: list[StickyNote]) -> list[StickyNote]:
        normalized: list[StickyNote] = []
        seen_ids: set[str] = set()
        for sticky in stickies:
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
            except (TypeError, ValueError):
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
        normalized = [AuditEvent.from_dict(event.to_dict()) for event in events]
        normalized, _ = self._apply_event_retention(normalized)
        return normalized

    def _normalize_cashboxes_payload(self, cashboxes: list[CashBox]) -> list[CashBox]:
        normalized: list[CashBox] = []
        seen_ids: set[str] = set()
        seen_names: set[str] = set()
        for item in cashboxes:
            if not isinstance(item, CashBox):
                continue
            if item.id in seen_ids or item.name.casefold() in seen_names:
                continue
            seen_ids.add(item.id)
            seen_names.add(item.name.casefold())
            normalized.append(item)
        normalized.sort(key=lambda item: (item.name.casefold(), item.id))
        return normalized

    def _normalize_cash_transactions_payload(
        self,
        transactions: list[CashTransaction],
        cashboxes: list[CashBox],
    ) -> list[CashTransaction]:
        normalized: list[CashTransaction] = []
        valid_cashbox_ids = {item.id for item in cashboxes}
        seen_ids: set[str] = set()
        for item in transactions:
            if not isinstance(item, CashTransaction):
                continue
            if item.id in seen_ids or item.cashbox_id not in valid_cashbox_ids:
                continue
            seen_ids.add(item.id)
            normalized.append(item)
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
        normalized = deepcopy(DEFAULT_STATE["settings"])
        normalized.update(settings)
        repaired = normalized != settings
        return normalized, repaired

    def _log_warning(self, message: str, *args) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)
