from __future__ import annotations

import math
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from ..models import (
    InventoryItem,
    InventoryMovement,
    normalize_decimal_text,
    normalize_inventory_unit,
    normalize_text,
    utc_now_iso,
)
from ..repair_order import RepairOrderRow


class CardServiceInventoryMixin:
    def list_inventory_items(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            query = self._validated_search_query(payload.get("query"))
            limit = self._validated_limit(payload.get("limit"), default=200, maximum=500)
            bundle = self._store.read_bundle()
            items = self._filtered_inventory_items(bundle["inventory_items"], query)
            return {
                "items": [item.to_dict() for item in items[:limit]],
                "meta": {"total": len(items), "limit": limit, "query": query},
            }

    def search_inventory_items(self, payload: dict | None = None) -> dict:
        return self.list_inventory_items(payload)

    def get_inventory_item(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            item = self._find_inventory_item(bundle["inventory_items"], payload.get("item_id"))
            movements = self._inventory_movements_for_item(
                bundle["inventory_movements"], item.id, limit=50
            )
            return {
                "item": item.to_dict(),
                "movements": [movement.to_dict() for movement in movements],
            }

    def list_inventory_movements(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            item_id = normalize_text(payload.get("item_id"), default="", limit=128)
            card_id = normalize_text(payload.get("card_id"), default="", limit=128)
            limit = self._validated_limit(payload.get("limit"), default=200, maximum=500)
            bundle = self._store.read_bundle()
            movements = list(bundle["inventory_movements"])
            if item_id:
                movements = [movement for movement in movements if movement.item_id == item_id]
            if card_id:
                movements = [movement for movement in movements if movement.card_id == card_id]
            movements.sort(key=lambda movement: (movement.created_at, movement.id))
            return {
                "movements": [movement.to_dict() for movement in movements[-limit:]],
                "meta": {"total": len(movements), "limit": limit},
            }

    def save_inventory_item(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            actor_name, source = self._audit_identity(payload, default_source="api")
            bundle = self._store.read_bundle()
            items: list[InventoryItem] = list(bundle["inventory_items"])
            movements: list[InventoryMovement] = list(bundle["inventory_movements"])
            events = bundle["events"]
            item_id = normalize_text(
                payload.get("item_id", payload.get("id", "")), default="", limit=128
            )
            existing_index = self._inventory_item_index(items, item_id) if item_id else -1
            existing = items[existing_index] if existing_index >= 0 else None
            item = self._build_inventory_item_from_payload(payload, existing=existing)
            movement: InventoryMovement | None = None
            if existing is None:
                items.append(item)
                if self._inventory_decimal(item.quantity) > 0:
                    movement = self._build_inventory_movement(
                        item=item,
                        kind="incoming",
                        quantity=item.quantity,
                        quantity_delta=item.quantity,
                        actor_name=actor_name,
                        source=source,
                        note="Создание позиции",
                    )
                    movements.append(movement)
            else:
                items[existing_index] = item

            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="inventory_item_saved",
                message=f"{actor_name} сохранил позицию склада",
                card_id=None,
                details={"item_id": item.id, "name": item.name},
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                inventory_items=items,
                inventory_movements=movements,
                events=events,
            )
            return {
                "item": item.to_dict(),
                "movement": movement.to_dict() if movement else None,
                "meta": {"created": existing is None},
            }

    def replenish_inventory_item(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            actor_name, source = self._audit_identity(payload, default_source="api")
            quantity = self._validated_inventory_quantity(payload.get("quantity"))
            bundle = self._store.read_bundle()
            items: list[InventoryItem] = list(bundle["inventory_items"])
            movements: list[InventoryMovement] = list(bundle["inventory_movements"])
            events = bundle["events"]
            item_index = self._inventory_required_item_index(items, payload.get("item_id"))
            item = items[item_index]
            cost_price = self._optional_inventory_money(payload, "cost_price") or item.cost_price
            sale_price = self._optional_inventory_money(payload, "sale_price") or item.sale_price
            updated_item = InventoryItem(
                id=item.id,
                name=item.name,
                catalog_number=item.catalog_number,
                unit=item.unit,
                quantity=self._inventory_decimal_text(
                    self._inventory_decimal(item.quantity) + quantity
                ),
                cost_price=cost_price,
                sale_price=sale_price,
                created_at=item.created_at,
                updated_at=utc_now_iso(),
            )
            movement = self._build_inventory_movement(
                item=updated_item,
                kind="incoming",
                quantity=self._inventory_decimal_text(quantity),
                quantity_delta=self._inventory_decimal_text(quantity),
                actor_name=actor_name,
                source=source,
                note=normalize_text(payload.get("note"), default="Пополнение", limit=240),
            )
            items[item_index] = updated_item
            movements.append(movement)
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="inventory_item_replenished",
                message=f"{actor_name} пополнил складскую позицию",
                card_id=None,
                details={
                    "item_id": updated_item.id,
                    "quantity": movement.quantity,
                    "unit": updated_item.unit,
                },
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                inventory_items=items,
                inventory_movements=movements,
                events=events,
            )
            return {"item": updated_item.to_dict(), "movement": movement.to_dict()}

    def write_off_inventory_item(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            actor_name, source = self._audit_identity(payload, default_source="api")
            quantity = self._validated_inventory_quantity(payload.get("quantity"))
            quantity_text = self._inventory_decimal_text(quantity)
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            columns = bundle["columns"]
            items: list[InventoryItem] = list(bundle["inventory_items"])
            movements: list[InventoryMovement] = list(bundle["inventory_movements"])
            item_index = self._inventory_required_item_index(items, payload.get("item_id"))
            item = items[item_index]
            available = self._inventory_decimal(item.quantity)
            if quantity > available:
                self._fail(
                    "validation_error",
                    "Нельзя списать больше складского остатка.",
                    details={
                        "field": "quantity",
                        "available": item.quantity,
                        "requested": quantity_text,
                    },
                )
            card = self._find_card(cards, payload.get("card_id"))
            self._ensure_not_archived(card)
            movement_id = str(uuid.uuid4())
            rows = [row.to_dict() for row in card.repair_order.materials]
            row_index = self._inventory_target_row_index(payload.get("row_index"), rows)
            material_row = RepairOrderRow(
                name=item.name,
                catalog_number=item.catalog_number,
                quantity=quantity_text,
                cost_price=item.cost_price,
                price=item.sale_price,
                inventory_item_id=item.id,
                inventory_movement_id=movement_id,
                inventory_unit=item.unit,
            ).to_dict()
            if row_index >= len(rows):
                rows.append(material_row)
                row_index = len(rows) - 1
            else:
                rows[row_index] = material_row
            next_payload = card.repair_order.to_storage_dict()
            next_payload["materials"] = rows
            changed = self._update_repair_order(
                card,
                cards,
                next_payload,
                events,
                actor_name,
                source,
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
                settings=bundle["settings"],
            )
            updated_item = InventoryItem(
                id=item.id,
                name=item.name,
                catalog_number=item.catalog_number,
                unit=item.unit,
                quantity=self._inventory_decimal_text(available - quantity),
                cost_price=item.cost_price,
                sale_price=item.sale_price,
                created_at=item.created_at,
                updated_at=utc_now_iso(),
            )
            movement = self._build_inventory_movement(
                item=updated_item,
                kind="write_off",
                quantity=quantity_text,
                quantity_delta=self._inventory_decimal_text(quantity * Decimal("-1")),
                actor_name=actor_name,
                source=source,
                card_id=card.id,
                repair_order_number=card.repair_order.number,
                repair_order_row_index=row_index,
                movement_id=movement_id,
                note="Списание в заказ-наряд",
            )
            items[item_index] = updated_item
            movements.append(movement)
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="inventory_item_written_off",
                message=f"{actor_name} списал материал со склада",
                card_id=card.id,
                details={
                    "item_id": updated_item.id,
                    "movement_id": movement.id,
                    "quantity": quantity_text,
                    "unit": updated_item.unit,
                    "row_index": row_index,
                },
            )
            numbering_changed = self._synchronize_repair_order_numbers(cards)
            if changed or numbering_changed:
                self._touch_card(card, actor_name)
                self._refresh_card_ai_fingerprint_if_agent_changed(card, actor_name, source)
                if self._card_has_repair_order(card):
                    self._ensure_repair_order_text_file(card, force=True)
            self._save_bundle(
                bundle,
                columns=columns,
                cards=cards,
                inventory_items=items,
                inventory_movements=movements,
                events=events,
            )
            return {
                "item": updated_item.to_dict(),
                "movement": movement.to_dict(),
                "material_row": card.repair_order.materials[row_index].to_dict(),
                "repair_order": card.repair_order.to_dict(),
                "card": self._serialize_card(
                    card,
                    events,
                    column_labels=self._column_labels(columns),
                    viewer_username=actor_name,
                ),
                "meta": {"row_index": row_index},
            }

    def return_inventory_movement(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            actor_name, source = self._audit_identity(payload, default_source="api")
            movement_id = normalize_text(
                payload.get("movement_id", payload.get("inventory_movement_id", "")),
                default="",
                limit=128,
            )
            if not movement_id:
                self._fail(
                    "validation_error",
                    "Нужно передать movement_id для возврата.",
                    details={"field": "movement_id"},
                )
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            columns = bundle["columns"]
            events = bundle["events"]
            items: list[InventoryItem] = list(bundle["inventory_items"])
            movements: list[InventoryMovement] = list(bundle["inventory_movements"])
            source_movement = self._find_inventory_movement(movements, movement_id)
            if source_movement.kind != "write_off":
                self._fail(
                    "validation_error",
                    "Вернуть можно только складское списание.",
                    details={"field": "movement_id"},
                )
            if any(
                movement.kind == "return" and movement.related_movement_id == source_movement.id
                for movement in movements
            ):
                self._fail(
                    "validation_error",
                    "Это складское списание уже возвращено.",
                    details={"field": "movement_id"},
                )
            item_index = self._inventory_required_item_index(items, source_movement.item_id)
            item = items[item_index]
            quantity = self._inventory_decimal(source_movement.quantity)
            updated_item = InventoryItem(
                id=item.id,
                name=item.name,
                catalog_number=item.catalog_number,
                unit=item.unit,
                quantity=self._inventory_decimal_text(
                    self._inventory_decimal(item.quantity) + quantity
                ),
                cost_price=item.cost_price,
                sale_price=item.sale_price,
                created_at=item.created_at,
                updated_at=utc_now_iso(),
            )
            card_id = (
                normalize_text(payload.get("card_id"), default="", limit=128)
                or source_movement.card_id
            )
            card = self._find_card(cards, card_id) if card_id else None
            row_index = source_movement.repair_order_row_index
            changed = False
            if card is not None:
                self._ensure_not_archived(card)
                rows = [row.to_dict() for row in card.repair_order.materials]
                row_index = self._inventory_row_index_by_movement(
                    rows, source_movement.id, row_index
                )
                if 0 <= row_index < len(rows):
                    rows[row_index] = {
                        **rows[row_index],
                        "inventory_item_id": "",
                        "inventory_movement_id": "",
                        "inventory_unit": "",
                    }
                    next_payload = card.repair_order.to_storage_dict()
                    next_payload["materials"] = rows
                    changed = self._update_repair_order(
                        card,
                        cards,
                        next_payload,
                        events,
                        actor_name,
                        source,
                        cashboxes=bundle["cashboxes"],
                        cash_transactions=bundle["cash_transactions"],
                        settings=bundle["settings"],
                    )
            movement = self._build_inventory_movement(
                item=updated_item,
                kind="return",
                quantity=source_movement.quantity,
                quantity_delta=source_movement.quantity,
                actor_name=actor_name,
                source=source,
                card_id=card.id if card is not None else source_movement.card_id,
                repair_order_number=card.repair_order.number
                if card is not None
                else source_movement.repair_order_number,
                repair_order_row_index=row_index,
                related_movement_id=source_movement.id,
                note="Возврат складского списания",
            )
            items[item_index] = updated_item
            movements.append(movement)
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="inventory_write_off_returned",
                message=f"{actor_name} вернул складское списание",
                card_id=card.id if card is not None else None,
                details={
                    "item_id": updated_item.id,
                    "movement_id": movement.id,
                    "related_movement_id": source_movement.id,
                    "quantity": source_movement.quantity,
                },
            )
            numbering_changed = self._synchronize_repair_order_numbers(cards)
            if card is not None and (changed or numbering_changed):
                self._touch_card(card, actor_name)
                self._refresh_card_ai_fingerprint_if_agent_changed(card, actor_name, source)
                if self._card_has_repair_order(card):
                    self._ensure_repair_order_text_file(card, force=True)
            self._save_bundle(
                bundle,
                columns=columns,
                cards=cards,
                inventory_items=items,
                inventory_movements=movements,
                events=events,
            )
            return {
                "item": updated_item.to_dict(),
                "movement": movement.to_dict(),
                "repair_order": card.repair_order.to_dict() if card is not None else None,
                "card": self._serialize_card(
                    card,
                    events,
                    column_labels=self._column_labels(columns),
                    viewer_username=actor_name,
                )
                if card is not None
                else None,
                "meta": {"row_index": row_index},
            }

    def _build_inventory_item_from_payload(
        self, payload: dict[str, Any], *, existing: InventoryItem | None
    ) -> InventoryItem:
        now = utc_now_iso()
        name = normalize_text(
            payload.get("name", existing.name if existing else ""),
            default="",
            limit=180,
        )
        if not name:
            self._fail(
                "validation_error",
                "Нужно указать название складской позиции.",
                details={"field": "name"},
            )
        unit = normalize_inventory_unit(payload.get("unit", existing.unit if existing else "шт"))
        quantity = existing.quantity if existing else "0"
        if existing is None:
            quantity = self._validated_inventory_decimal_text(
                payload.get("quantity", "0"),
                field="quantity",
                allow_zero=True,
            )
        return InventoryItem(
            id=existing.id if existing else str(uuid.uuid4()),
            name=name,
            catalog_number=normalize_text(
                payload.get(
                    "catalog_number",
                    payload.get("catalogNumber", existing.catalog_number if existing else ""),
                ),
                default="",
                limit=120,
            ),
            unit=unit,
            quantity=quantity,
            cost_price=self._optional_inventory_money(payload, "cost_price")
            or (existing.cost_price if existing else "0"),
            sale_price=self._optional_inventory_money(payload, "sale_price")
            or (existing.sale_price if existing else "0"),
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

    def _build_inventory_movement(
        self,
        *,
        item: InventoryItem,
        kind: str,
        quantity: str,
        quantity_delta: str,
        actor_name: str,
        source: str,
        movement_id: str | None = None,
        card_id: str = "",
        repair_order_number: str = "",
        repair_order_row_index: int = -1,
        related_movement_id: str = "",
        note: str = "",
    ) -> InventoryMovement:
        return InventoryMovement.from_dict(
            {
                "id": movement_id or str(uuid.uuid4()),
                "item_id": item.id,
                "kind": kind,
                "quantity": quantity,
                "quantity_delta": quantity_delta,
                "unit": item.unit,
                "cost_price": item.cost_price,
                "sale_price": item.sale_price,
                "created_at": utc_now_iso(),
                "actor_name": actor_name,
                "source": source,
                "card_id": card_id,
                "repair_order_number": repair_order_number,
                "repair_order_row_index": repair_order_row_index,
                "related_movement_id": related_movement_id,
                "note": note,
            }
        )

    def _filtered_inventory_items(
        self, items: list[InventoryItem], query: str
    ) -> list[InventoryItem]:
        if not query:
            return list(items)
        needle = query.casefold()
        return [
            item
            for item in items
            if needle in item.name.casefold()
            or needle in item.catalog_number.casefold()
            or needle in item.id.casefold()
        ]

    def _inventory_item_index(self, items: list[InventoryItem], item_id: str) -> int:
        normalized_id = normalize_text(item_id, default="", limit=128)
        if not normalized_id:
            return -1
        for index, item in enumerate(items):
            if item.id == normalized_id:
                return index
        return -1

    def _inventory_required_item_index(self, items: list[InventoryItem], item_id: Any) -> int:
        index = self._inventory_item_index(items, normalize_text(item_id, default="", limit=128))
        if index < 0:
            self._fail(
                "not_found",
                "Складская позиция не найдена.",
                status_code=404,
                details={"field": "item_id"},
            )
        return index

    def _find_inventory_item(self, items: list[InventoryItem], item_id: Any) -> InventoryItem:
        return items[self._inventory_required_item_index(items, item_id)]

    def _find_inventory_movement(
        self, movements: list[InventoryMovement], movement_id: str
    ) -> InventoryMovement:
        for movement in movements:
            if movement.id == movement_id:
                return movement
        self._fail(
            "not_found",
            "Складское движение не найдено.",
            status_code=404,
            details={"field": "movement_id"},
        )

    def _inventory_movements_for_item(
        self, movements: list[InventoryMovement], item_id: str, *, limit: int
    ) -> list[InventoryMovement]:
        filtered = [movement for movement in movements if movement.item_id == item_id]
        filtered.sort(key=lambda movement: (movement.created_at, movement.id))
        return filtered[-limit:]

    def _validated_inventory_quantity(self, value: Any) -> Decimal:
        return self._validated_inventory_decimal(value, field="quantity", allow_zero=False)

    def _validated_inventory_decimal_text(self, value: Any, *, field: str, allow_zero: bool) -> str:
        return self._inventory_decimal_text(
            self._validated_inventory_decimal(value, field=field, allow_zero=allow_zero)
        )

    def _validated_inventory_decimal(self, value: Any, *, field: str, allow_zero: bool) -> Decimal:
        raw = str(value if value is not None else "").strip().replace(",", ".")
        if not raw:
            raw = "0" if allow_zero else ""
        try:
            parsed = Decimal(raw)
        except (InvalidOperation, ValueError):
            self._fail(
                "validation_error",
                "Количество должно быть числом.",
                details={"field": field},
            )
        if not parsed.is_finite() or parsed < 0 or (parsed == 0 and not allow_zero):
            self._fail(
                "validation_error",
                "Количество должно быть больше нуля.",
                details={"field": field},
            )
        return parsed

    def _optional_inventory_money(self, payload: dict[str, Any], field: str) -> str:
        aliases = {
            "cost_price": ("cost_price", "costPrice", "purchase_price", "cost"),
            "sale_price": ("sale_price", "salePrice", "price"),
        }[field]
        raw_value = None
        for alias in aliases:
            if alias in payload:
                raw_value = payload.get(alias)
                break
        if raw_value in (None, ""):
            return ""
        raw = str(raw_value).strip().replace(",", ".")
        try:
            parsed = Decimal(raw)
        except (InvalidOperation, ValueError):
            self._fail(
                "validation_error",
                "Цена должна быть числом.",
                details={"field": field},
            )
        if not parsed.is_finite() or parsed < 0:
            self._fail(
                "validation_error",
                "Цена не может быть отрицательной.",
                details={"field": field},
            )
        return self._inventory_decimal_text(parsed)

    def _inventory_decimal(self, value: Any) -> Decimal:
        return Decimal(normalize_decimal_text(value, default="0"))

    def _inventory_decimal_text(self, value: Any) -> str:
        return normalize_decimal_text(value, default="0")

    def _inventory_target_row_index(self, value: Any, rows: list[dict[str, Any]]) -> int:
        if value in (None, ""):
            return len(rows)
        if isinstance(value, bool):
            self._fail(
                "validation_error",
                "Индекс строки материалов должен быть целым числом.",
                details={"field": "row_index"},
            )
        try:
            numeric = float(value)
        except (OverflowError, TypeError, ValueError):
            self._fail(
                "validation_error",
                "Индекс строки материалов должен быть целым числом.",
                details={"field": "row_index"},
            )
        if not math.isfinite(numeric) or not numeric.is_integer():
            self._fail(
                "validation_error",
                "Индекс строки материалов должен быть целым числом.",
                details={"field": "row_index"},
            )
        row_index = int(numeric)
        if row_index < 0 or row_index > len(rows):
            self._fail(
                "validation_error",
                "Индекс строки материалов вне диапазона.",
                details={"field": "row_index"},
            )
        return row_index

    def _inventory_row_index_by_movement(
        self, rows: list[dict[str, Any]], movement_id: str, fallback_index: int
    ) -> int:
        if 0 <= fallback_index < len(rows):
            fallback_row = rows[fallback_index]
            if (
                isinstance(fallback_row, dict)
                and normalize_text(fallback_row.get("inventory_movement_id")) == movement_id
            ):
                return fallback_index
        for index, row in enumerate(rows):
            if (
                isinstance(row, dict)
                and normalize_text(row.get("inventory_movement_id")) == movement_id
            ):
                return index
        return -1
