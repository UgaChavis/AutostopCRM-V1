from __future__ import annotations

from collections.abc import Callable
from logging import Logger
from threading import RLock
from typing import Any

from ..models import COLUMN_LABEL_LIMIT, Column
from ..storage.json_store import JsonStore


class ColumnService:
    def __init__(
        self,
        store: JsonStore,
        logger: Logger,
        lock: RLock,
        *,
        audit_identity: Callable[[dict, str], tuple[str, str]],
        append_event: Callable[..., None],
        save_bundle: Callable[..., None],
        validated_column: Callable[[Any, list[Column]], str],
        fail: Callable[..., None],
    ) -> None:
        self._store = store
        self._logger = logger
        self._lock = lock
        self._audit_identity = audit_identity
        self._append_event = append_event
        self._save_bundle = save_bundle
        self._validated_column = validated_column
        self._fail = fail

    def list_columns(self, payload: dict | None = None) -> dict:
        with self._lock:
            _ = payload
            bundle = self._store.read_bundle()
            return {"columns": [column.to_dict() for column in bundle["columns"]]}

    def create_column(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            columns = bundle["columns"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, "api")
            label = self._validated_column_label(payload.get("label"), columns)
            column = Column(id=self._next_column_id(columns), label=label, position=len(columns))
            columns.append(column)
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="column_created",
                message=f"{actor_name} СЃРѕР·РґР°Р» СЃС‚РѕР»Р±РµС†",
                card_id=None,
                details={"column_id": column.id, "label": column.label},
            )
            self._save_bundle(bundle, columns=columns, cards=bundle["cards"], events=events)
            self._logger.info("create_column id=%s label=%s actor=%s source=%s", column.id, column.label, actor_name, source)
            return {
                "column": column.to_dict(),
                "columns": [item.to_dict() for item in columns],
            }

    def rename_column(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            columns = bundle["columns"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, "api")
            column_id = self._validated_column(payload.get("column_id") or payload.get("column"), columns)
            column = next((item for item in columns if item.id == column_id), None)
            if column is None:
                self._fail("not_found", "РЈРєР°Р·Р°РЅРЅС‹Р№ СЃС‚РѕР»Р±РµС† РЅРµ РЅР°Р№РґРµРЅ.", status_code=404, details={"column_id": column_id})
            previous_label = column.label
            label = self._validated_column_label(payload.get("label"), columns, exclude_column_id=column_id)
            if label == previous_label:
                return {
                    "column": column.to_dict(),
                    "columns": [item.to_dict() for item in columns],
                    "meta": {
                        "changed": False,
                        "previous_label": previous_label,
                    },
                }
            column.label = label
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="column_renamed",
                message=f"{actor_name} РїРµСЂРµРёРјРµРЅРѕРІР°Р» СЃС‚РѕР»Р±РµС†",
                card_id=None,
                details={"column_id": column.id, "before": previous_label, "after": column.label},
            )
            self._save_bundle(bundle, columns=columns, cards=bundle["cards"], events=events)
            self._logger.info(
                "rename_column id=%s before=%s after=%s actor=%s source=%s",
                column.id,
                previous_label,
                column.label,
                actor_name,
                source,
            )
            return {
                "column": column.to_dict(),
                "columns": [item.to_dict() for item in columns],
                "meta": {
                    "changed": True,
                    "previous_label": previous_label,
                },
            }

    def delete_column(self, payload: dict) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            columns = bundle["columns"]
            cards = bundle["cards"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, "api")
            column_id = self._validated_column(payload.get("column_id") or payload.get("column"), columns)
            column = next((item for item in columns if item.id == column_id), None)
            if column is None:
                self._fail("not_found", "РЈРєР°Р·Р°РЅРЅС‹Р№ СЃС‚РѕР»Р±РµС† РЅРµ РЅР°Р№РґРµРЅ.", status_code=404, details={"column_id": column_id})
            if len(columns) <= 1:
                self._fail(
                    "last_column",
                    "РќРµР»СЊР·СЏ СѓРґР°Р»РёС‚СЊ РїРѕСЃР»РµРґРЅРёР№ СЃС‚РѕР»Р±РµС† РґРѕСЃРєРё.",
                    status_code=409,
                    details={"column_id": column_id},
                )
            bound_cards = [card for card in cards if card.column == column_id and not card.archived]
            if bound_cards:
                self._fail(
                    "column_not_empty",
                    "РќРµР»СЊР·СЏ СѓРґР°Р»РёС‚СЊ РЅРµРїСѓСЃС‚РѕР№ СЃС‚РѕР»Р±РµС†. РЎРЅР°С‡Р°Р»Р° РїРµСЂРµРЅРµСЃРё РёР»Рё Р°СЂС…РёРІРёСЂСѓР№ СЃРІСЏР·Р°РЅРЅС‹Рµ РєР°СЂС‚РѕС‡РєРё.",
                    status_code=409,
                    details={
                        "column_id": column_id,
                        "cards_total": len(bound_cards),
                        "card_ids": [card.id for card in bound_cards[:10]],
                    },
                )
            deleted_column = column.to_dict()
            remaining_columns = [item for item in columns if item.id != column_id]
            for position, item in enumerate(remaining_columns):
                item.position = position
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="column_deleted",
                message=f"{actor_name} СѓРґР°Р»РёР» СЃС‚РѕР»Р±РµС†",
                card_id=None,
                details={"column_id": column.id, "label": column.label},
            )
            self._save_bundle(bundle, columns=remaining_columns, cards=cards, events=events)
            self._logger.info("delete_column id=%s label=%s actor=%s source=%s", column.id, column.label, actor_name, source)
            return {
                "deleted_column": deleted_column,
                "columns": [item.to_dict() for item in remaining_columns],
            }

    def _validated_column_label(
        self,
        value: Any,
        columns: list[Column],
        *,
        exclude_column_id: str | None = None,
    ) -> str:
        label = str(value or "").strip()
        if not label:
            self._fail(
                "validation_error",
                "РќСѓР¶РЅРѕ РїРµСЂРµРґР°С‚СЊ РЅРµРїСѓСЃС‚РѕР№ label РґР»СЏ РЅРѕРІРѕРіРѕ СЃС‚РѕР»Р±С†Р°.",
                details={"field": "label"},
            )
        if len(label) > COLUMN_LABEL_LIMIT:
            self._fail(
                "validation_error",
                f"РџРѕР»Рµ label РЅРµ РґРѕР»Р¶РЅРѕ РїСЂРµРІС‹С€Р°С‚СЊ {COLUMN_LABEL_LIMIT} СЃРёРјРІРѕР»РѕРІ.",
                details={"field": "label"},
            )
        existing_labels = {
            column.label.casefold()
            for column in columns
            if exclude_column_id is None or column.id != exclude_column_id
        }
        if label.casefold() in existing_labels:
            self._fail(
                "validation_error",
                "РЎС‚РѕР»Р±РµС† СЃ С‚Р°РєРёРј РЅР°Р·РІР°РЅРёРµРј СѓР¶Рµ СЃСѓС‰РµСЃС‚РІСѓРµС‚.",
                details={"field": "label"},
            )
        return label

    def _next_column_id(self, columns: list[Column]) -> str:
        existing_ids = {column.id for column in columns}
        index = 1
        while True:
            column_id = f"column_{index}"
            if column_id not in existing_ids:
                return column_id
            index += 1
