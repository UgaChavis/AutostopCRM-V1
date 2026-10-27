"""Revision-bound editorial changes without repair, payment or stock transitions."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import timedelta

from ..models import ClientVehicle, parse_datetime, utc_now
from ..repair_order import REPAIR_ORDER_ROW_NAME_LIMIT
from ..vehicle_profile import VEHICLE_ALL_FIELDS, normalize_vehicle_field_names

_CONTENT_FIELDS = {
    "vehicle",
    "title",
    "description",
    "tags",
    "vehicle_profile",
    "repair_order_names",
    "client_vehicle_patch",
}
_REQUEST_FIELDS = _CONTENT_FIELDS | {
    "card_id",
    "content_only",
    "mode",
    "actor_name",
    "source",
    "_operator_session",
    "expected_updated_at",
    "idempotency_key",
    "response_mode",
}
_VEHICLE_FIELDS = {
    "vehicle",
    "brand",
    "model",
    "vin",
    "license_plate",
    "year",
    "mileage",
    "body_number",
    "chassis_number",
    "engine_code",
    "engine_model",
    "gearbox_type",
    "gearbox_model",
    "drivetrain",
    "notes",
    "manual_fields",
    "autofilled_fields",
    "tentative_fields",
    "field_sources",
    "source_summary",
    "source_confidence",
    "source_links_or_refs",
}
_RECEIPTS_KEY = "card_editorial_receipts"


class CardServiceEditorialMixin:
    def _editorial_text(self, value, field, *, limit=128):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            self._fail("validation_error", f"Нужно передать {field}.", details={"field": field})
        return value.strip()

    def _editorial_repair_order_names(self, card, value, events, actor_name, source):
        if not isinstance(value, dict) or set(value) - {"works", "materials"}:
            self._fail(
                "validation_error", "Допустимы только имена существующих строк работ и материалов."
            )
        before = card.repair_order.to_storage_dict()
        changes = []
        for kind, patches in value.items():
            if not isinstance(patches, list) or len(patches) > 500:
                self._fail("validation_error", "Передайте список правок имён строк.")
            rows = getattr(card.repair_order, kind)
            seen = set()
            for patch in patches:
                if not isinstance(patch, dict) or set(patch) != {"row_id", "name"}:
                    self._fail("validation_error", "Правка строки содержит только row_id и name.")
                row_id = self._editorial_text(patch["row_id"], "row_id", limit=80)
                name = self._editorial_text(
                    patch["name"], "name", limit=REPAIR_ORDER_ROW_NAME_LIMIT
                )
                name = " ".join(name.split())
                matches = [row for row in rows if row.id == row_id]
                if row_id in seen or len(matches) != 1:
                    self._fail(
                        "editorial_row_conflict",
                        "Нужен уникальный ID существующей строки.",
                        status_code=409,
                        details={"kind": kind, "row_id": row_id},
                    )
                seen.add(row_id)
                row = matches[0]
                if row.name != name:
                    changes.append(
                        {"kind": kind, "row_id": row.id, "before": row.name, "after": name}
                    )
                    row.name = name
        if changes:
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="repair_order_updated",
                message=f"{actor_name} исправил названия строк заказ-наряда",
                card_id=card.id,
                details={
                    "before": before,
                    "after": card.repair_order.to_storage_dict(),
                    "editorial_only": True,
                    "row_names": changes,
                },
            )
        return bool(changes)

    def _editorial_client_vehicle(self, card, bundle, value, events, actor_name, source):
        required = {"client_id", "client_vehicle_id", "expected_updated_at", "vehicle"}
        if not isinstance(value, dict) or set(value) != required:
            self._fail(
                "validation_error",
                "Для автомобиля нужны точные ID клиента и автомобиля, revision и vehicle patch.",
            )
        if (
            value["client_id"] != card.client_id
            or value["client_vehicle_id"] != card.client_vehicle_id
        ):
            self._fail(
                "editorial_vehicle_link_conflict",
                "Автомобиль не совпадает с точной связью карточки.",
                status_code=409,
            )
        client = self._find_client(bundle["clients"], value["client_id"])
        expected = self._editorial_text(
            value["expected_updated_at"], "client_vehicle_patch.expected_updated_at", limit=80
        )
        if expected != client.updated_at:
            self._fail(
                "client_update_conflict",
                "Профиль клиента изменён. Перечитайте его перед правкой.",
                status_code=409,
            )
        existing = self._find_client_vehicle(client, value["client_vehicle_id"])
        patch = value["vehicle"]
        if not isinstance(patch, dict) or not patch or set(patch) - _VEHICLE_FIELDS:
            self._fail("validation_error", "Передайте только поля указанного автомобиля.")
        before = existing.to_dict()
        vehicle = ClientVehicle.from_value({**before, **deepcopy(patch)})
        if vehicle is None:
            self._fail("validation_error", "Правка не может удалить автомобиль.")
        after = vehicle.to_dict()
        changed = before != after
        if changed:
            self._replace_client_vehicle(client, vehicle)
            moment = utc_now()
            previous = parse_datetime(client.updated_at)
            if previous is not None and moment <= previous:
                moment = previous + timedelta(microseconds=1)
            client.updated_at = moment.isoformat()
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="client_vehicle_updated",
                message=f"{actor_name} уточнил автомобиль клиента",
                card_id=card.id,
                details={
                    "client_id": client.id,
                    "client_vehicle_id": vehicle.id,
                    "before": before,
                    "after": after,
                    "editorial_only": True,
                },
            )
        return changed, {"client_id": client.id, "updated_at": client.updated_at, "vehicle": after}

    def _cleanup_card_editorial(self, payload):
        unknown = set(payload) - _REQUEST_FIELDS
        if unknown or not _CONTENT_FIELDS.intersection(payload):
            self._fail(
                "validation_error",
                "Content-only принимает только редакторские поля.",
                details={"unsupported_fields": sorted(unknown)},
            )
        mode = self._manager_operation_mode(payload)
        actor_name, source = self._manager_actor_identity(payload, mode=mode)
        key = self._editorial_text(payload.get("idempotency_key"), "idempotency_key")
        self._editorial_text(payload.get("expected_updated_at"), "expected_updated_at", limit=80)
        response_mode = self._validated_response_mode(payload, default="compact")
        body = {
            key: value
            for key, value in payload.items()
            if key not in {"mode", "_operator_session", "response_mode"}
        }
        try:
            digest = hashlib.sha256(
                json.dumps(body, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
            ).hexdigest()
        except (TypeError, ValueError):
            self._fail("validation_error", "Правка должна содержать JSON-совместимые данные.")
        with self._lock:
            bundle = self._read_bundle_for_update(card_id=payload.get("card_id", ""))
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            receipts = bundle["settings"].setdefault(_RECEIPTS_KEY, {})
            receipt_key = hashlib.sha256(f"{card.id}\0{key}".encode()).hexdigest()
            receipt = receipts.get(receipt_key)
            if receipt:
                if receipt["fingerprint"] != digest or receipt["actor_name"] != actor_name:
                    self._fail(
                        "editorial_idempotency_conflict",
                        "Ключ уже связан с другой правкой.",
                        status_code=409,
                    )
                return self._editorial_result(
                    card,
                    bundle,
                    actor_name,
                    response_mode,
                    mode,
                    receipt["changed_fields"],
                    replayed=True,
                    applied_updated_at=receipt["updated_at"],
                )
            self._ensure_card_expected_updated_at(card, payload)
            events = bundle["events"]
            changed_fields = []
            for field, method in (
                ("vehicle", self._update_vehicle),
                ("vehicle_profile", self._update_vehicle_profile),
                ("title", self._update_title),
                ("description", self._update_description),
                ("tags", self._update_tags),
            ):
                if field not in payload:
                    continue
                value = payload[field]
                if field == "vehicle_profile":
                    allowed = set(VEHICLE_ALL_FIELDS) - {
                        "customer_name",
                        "customer_phone",
                        "customer_phones",
                    }
                    if not isinstance(value, dict) or set(value) - allowed:
                        self._fail("validation_error", "Допустимы только поля паспорта автомобиля.")
                    value = deepcopy(value)
                    if "tentative_fields" in value:
                        tentative = set(normalize_vehicle_field_names(value["tentative_fields"]))
                        value.setdefault(
                            "autofilled_fields",
                            sorted(set(card.vehicle_profile.autofilled_fields) | tentative),
                        )
                        value.setdefault(
                            "manual_fields",
                            sorted(set(card.vehicle_profile.manual_fields) - tentative),
                        )
                if method(card, value, events, actor_name, source):
                    changed_fields.append(field)
            if "repair_order_names" in payload and self._editorial_repair_order_names(
                card, payload["repair_order_names"], events, actor_name, source
            ):
                changed_fields.append("repair_order_names")
            client_vehicle = None
            if "client_vehicle_patch" in payload:
                changed, client_vehicle = self._editorial_client_vehicle(
                    card, bundle, payload["client_vehicle_patch"], events, actor_name, source
                )
                if changed:
                    changed_fields.append("client_vehicle_patch")
            if mode == "apply":
                if changed_fields:
                    self._touch_card(card, actor_name)
                receipts[receipt_key] = {
                    "fingerprint": digest,
                    "actor_name": actor_name,
                    "updated_at": card.updated_at,
                    "changed_fields": changed_fields,
                }
                if len(receipts) > 2000:
                    del receipts[next(iter(receipts))]
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    clients=bundle["clients"],
                    events=events,
                    require_compare_and_swap=True,
                )
            return self._editorial_result(
                card,
                bundle,
                actor_name,
                response_mode,
                mode,
                changed_fields,
                client_vehicle=client_vehicle,
            )

    def _editorial_result(
        self,
        card,
        bundle,
        actor_name,
        response_mode,
        mode,
        changed_fields,
        *,
        replayed=False,
        applied_updated_at=None,
        client_vehicle=None,
    ):
        item = {
            "card_id": card.id,
            "planned_fields": changed_fields,
            "card": self._response_card(
                card,
                bundle["events"],
                self._column_labels(bundle["columns"]),
                actor_name,
                response_mode,
            ),
            "changed": bool(changed_fields) and mode == "apply" and not replayed,
        }
        if client_vehicle is not None:
            item["client_vehicle"] = client_vehicle
        return self._manager_operation_result(
            "cleanup_card",
            mode,
            actor_name=actor_name,
            scanned=1,
            eligible=int(bool(changed_fields)),
            changed=int(item["changed"]),
            skipped=int(not changed_fields),
            errors=[],
            items=[item],
            verification={
                "passed": True,
                "content_only": True,
                "updated_at": card.updated_at,
                "applied_updated_at": applied_updated_at or card.updated_at,
            },
            extra_meta={
                "response_mode": response_mode,
                "content_only": True,
                "idempotent_replay": replayed,
            },
        )
