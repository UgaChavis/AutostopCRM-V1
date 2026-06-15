from __future__ import annotations

import re
import uuid
from typing import Any

from ..models import (
    Card,
    ClientProfile,
    ClientVehicle,
    normalize_text,
    short_entity_id,
    utc_now_iso,
)
from ..repair_order import REPAIR_ORDER_STATUS_CLOSED

_PHONE_PATTERN = re.compile(
    r"(?:\+7|8)\s*(?:\(\s*\d{3}\s*\)|\d{3})\s*[\- ]?\s*\d{3}\s*[\- ]?\s*\d{2}\s*[\- ]?\s*\d{2}"
)


class CardServiceClientsMixin:
    def list_clients(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            limit = self._validated_limit(payload.get("limit"), default=200, maximum=1000)
            include_stats = self._validated_optional_bool(payload, "include_stats", default=True)
            bundle = self._store.read_bundle()
            clients = self._ordered_clients(bundle["clients"])
            selected = clients[:limit]
            related_cards_by_client_id = (
                self._client_related_cards_map(selected, bundle["cards"]) if include_stats else {}
            )
            return {
                "clients": [
                    self._serialize_client(
                        client,
                        bundle["cards"],
                        include_stats=include_stats,
                        compact=True,
                        include_vehicle_preview=include_stats,
                        related_cards=related_cards_by_client_id.get(client.id),
                    )
                    for client in selected
                ],
                "meta": {
                    "total": len(clients),
                    "returned": min(len(clients), limit),
                    "has_more": len(clients) > limit,
                    "include_stats": include_stats,
                },
            }

    def search_clients(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            query = normalize_text(payload.get("query"), default="", limit=240)
            limit = self._validated_limit(payload.get("limit"), default=10, maximum=100)
            bundle = self._store.read_bundle()
            clients = self._ordered_clients(bundle["clients"])
            matches = self._rank_client_matches(clients, query, bundle["cards"])
            selected = [client for _, client in matches[:limit]]
            related_cards_by_client_id = self._client_related_cards_map(selected, bundle["cards"])
            return {
                "clients": [
                    self._serialize_client(
                        client,
                        bundle["cards"],
                        include_stats=True,
                        compact=True,
                        query=query,
                        related_cards=related_cards_by_client_id.get(client.id, []),
                    )
                    for client in selected
                ],
                "meta": {
                    "query": query,
                    "total": len(matches),
                    "returned": len(selected),
                    "has_more": len(matches) > len(selected),
                },
            }

    def get_client(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            order_limit = self._validated_limit(payload.get("order_limit"), default=30, maximum=200)
            bundle = self._store.read_bundle()
            client = self._find_client(bundle["clients"], payload.get("client_id"))
            return self._client_profile_payload(client, bundle["cards"], order_limit=order_limit)

    def get_client_stats(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            client = self._find_client(bundle["clients"], payload.get("client_id"))
            return {
                "client": self._serialize_client(
                    client, bundle["cards"], include_stats=True, compact=True
                ),
                "stats": self._client_stats(client, bundle["cards"]),
            }

    def create_client(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            clients = list(bundle["clients"])
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            client = self._validated_client_profile(payload)
            explicit_id = bool(
                normalize_text(payload.get("client_id") or payload.get("id"), default="", limit=128)
            )
            duplicate = None if explicit_id else self._find_duplicate_client(clients, client)
            if duplicate is not None:
                return {
                    "client": self._serialize_client(
                        duplicate, bundle["cards"], include_stats=True
                    ),
                    "meta": {
                        "created": False,
                        "duplicate": True,
                        "duplicate_of": duplicate.id,
                    },
                }
            clients.append(client)
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="client_created",
                message=f"{actor_name} создал клиента",
                card_id=None,
                details={"client_id": client.id, "client_name": client.name()},
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                clients=clients,
                events=events,
            )
            return {
                "client": self._serialize_client(client, bundle["cards"], include_stats=True),
                "meta": {"created": True},
            }

    def update_client(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            clients = list(bundle["clients"])
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            client = self._find_client(clients, payload.get("client_id") or payload.get("id"))
            before = client.to_storage_dict()
            merged = {**before, **self._client_patch_payload(payload)}
            merged["id"] = client.id
            merged["created_at"] = client.created_at
            merged["updated_at"] = utc_now_iso()
            next_client = ClientProfile.from_dict(merged)
            changed = before != next_client.to_storage_dict()
            if changed:
                index = clients.index(client)
                clients[index] = next_client
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="client_updated",
                    message=f"{actor_name} обновил клиента",
                    card_id=None,
                    details={"client_id": next_client.id, "client_name": next_client.name()},
                )
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=bundle["cards"],
                    clients=clients,
                    events=events,
                )
                client = next_client
            return {
                "client": self._serialize_client(client, bundle["cards"], include_stats=True),
                "meta": {"changed": changed},
            }

    def link_card_to_client(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            clients = bundle["clients"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            client = self._find_client(clients, payload.get("client_id"))
            actor_name, source = self._audit_identity(payload, default_source="api")
            sync_fields = self._validated_optional_bool(payload, "sync_fields", default=True)
            overwrite = self._validated_optional_bool(
                payload, "overwrite_card_fields", default=False
            )
            sync_vehicle_fields = self._validated_optional_bool(
                payload, "sync_vehicle_fields", default=True
            )
            create_vehicle_from_card = self._validated_optional_bool(
                payload, "create_vehicle_from_card", default=False
            )
            client_vehicle_id = normalize_text(
                payload.get("client_vehicle_id") or payload.get("vehicle_id"),
                default="",
                limit=128,
            )
            vehicle: ClientVehicle | None = None
            clients_changed = False
            if create_vehicle_from_card:
                vehicle = self._client_vehicle_from_card(card)
                client.vehicles.append(vehicle)
                client.vehicles = self._dedupe_client_vehicles(client.vehicles)
                client.updated_at = utc_now_iso()
                client_vehicle_id = vehicle.id
                clients_changed = True
            elif client_vehicle_id:
                vehicle = self._find_client_vehicle(client, client_vehicle_id)

            changed = card.client_id != client.id
            card.client_id = client.id
            if client_vehicle_id and card.client_vehicle_id != client_vehicle_id:
                card.client_vehicle_id = client_vehicle_id
                changed = True
            if sync_fields:
                changed = (
                    self._sync_card_client_fields(card, client, overwrite=overwrite) or changed
                )
            if vehicle is not None and sync_vehicle_fields:
                changed = self._sync_card_vehicle_fields(card, vehicle, overwrite=True) or changed
            if changed:
                self._touch_card(card, actor_name)
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="card_client_linked",
                    message=f"{actor_name} связал карточку с клиентом",
                    card_id=card.id,
                    details={
                        "client_id": client.id,
                        "client_name": client.name(),
                        "client_vehicle_id": card.client_vehicle_id,
                        "vehicle_created": create_vehicle_from_card,
                    },
                )
                if self._card_has_repair_order(card):
                    self._ensure_repair_order_text_file(card, force=True)
            if clients_changed and not changed:
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="client_vehicle_created",
                    message=f"{actor_name} добавил автомобиль клиента",
                    card_id=card.id,
                    details={"client_id": client.id, "client_vehicle_id": client_vehicle_id},
                )
            if changed or clients_changed:
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=cards,
                    clients=clients,
                    events=events,
                )
            return {
                "card": self._serialize_card(
                    card,
                    events,
                    column_labels=self._column_labels(bundle["columns"]),
                    include_removed_attachments=True,
                ),
                "client": self._serialize_client(client, cards, include_stats=True),
                "meta": {
                    "changed": changed or clients_changed,
                    "sync_fields": sync_fields,
                    "sync_vehicle_fields": sync_vehicle_fields,
                    "client_vehicle_id": card.client_vehicle_id,
                    "vehicle_created": create_vehicle_from_card,
                },
            }

    def upsert_client_vehicle(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            clients = list(bundle["clients"])
            cards = bundle["cards"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            client = self._find_client(clients, payload.get("client_id"))
            vehicle_id = normalize_text(
                payload.get("client_vehicle_id") or payload.get("vehicle_id"),
                default="",
                limit=128,
            )
            card = (
                self._find_card(cards, payload.get("card_id")) if payload.get("card_id") else None
            )
            if card is not None and not payload.get("vehicle"):
                next_vehicle = self._client_vehicle_from_card(card, vehicle_id=vehicle_id)
            else:
                next_vehicle = self._validated_client_vehicle(payload)
                if vehicle_id:
                    next_vehicle.id = vehicle_id

            existing = self._find_client_vehicle_or_none(client, vehicle_id)
            created = existing is None
            changed = created
            if existing is None:
                client.vehicles.append(next_vehicle)
            else:
                merged = self._merge_client_vehicle(existing, next_vehicle)
                changed = existing.to_dict() != merged.to_dict()
                if changed:
                    self._replace_client_vehicle(client, merged)
                next_vehicle = merged
            client.vehicles = self._dedupe_client_vehicles(client.vehicles)
            next_vehicle_key = self._client_vehicle_identity_key(
                next_vehicle.vehicle,
                next_vehicle.vin,
                next_vehicle.license_plate,
                next_vehicle.year,
            )
            if next_vehicle_key and next_vehicle_key in set(client.deleted_vehicle_keys or []):
                client.deleted_vehicle_keys = [
                    key for key in client.deleted_vehicle_keys if key != next_vehicle_key
                ]
                changed = True
            sync_linked_cards = self._validated_optional_bool(
                payload, "sync_linked_cards", default=True
            )
            synced_card_ids: list[str] = []
            if sync_linked_cards and next_vehicle.id:
                for linked_card in cards:
                    if (
                        linked_card.client_id == client.id
                        and linked_card.client_vehicle_id == next_vehicle.id
                    ):
                        if self._sync_card_vehicle_fields(
                            linked_card, next_vehicle, overwrite=True
                        ):
                            self._touch_card(linked_card, actor_name)
                            synced_card_ids.append(linked_card.id)
                            self._append_event(
                                events,
                                actor_name=actor_name,
                                source=source,
                                action="card_client_vehicle_synced",
                                message=f"{actor_name} обновил паспорт автомобиля из профиля клиента",
                                card_id=linked_card.id,
                                details={
                                    "client_id": client.id,
                                    "client_vehicle_id": next_vehicle.id,
                                },
                            )
            if changed or synced_card_ids:
                client.updated_at = utc_now_iso()
                if changed:
                    self._append_event(
                        events,
                        actor_name=actor_name,
                        source=source,
                        action="client_vehicle_created" if created else "client_vehicle_updated",
                        message=(
                            f"{actor_name} добавил автомобиль клиента"
                            if created
                            else f"{actor_name} обновил автомобиль клиента"
                        ),
                        card_id=card.id if card is not None else None,
                        details={"client_id": client.id, "client_vehicle_id": next_vehicle.id},
                    )
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=cards,
                    clients=clients,
                    events=events,
                )
            return {
                "client": self._serialize_client(client, cards, include_stats=True),
                "vehicle": next_vehicle.to_dict(),
                "meta": {
                    "changed": changed or bool(synced_card_ids),
                    "created": created,
                    "synced_card_ids": synced_card_ids,
                },
            }

    def delete_client_vehicle(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            clients = list(bundle["clients"])
            cards = bundle["cards"]
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            client = self._find_client(clients, payload.get("client_id"))
            vehicle = self._find_client_vehicle(
                client, payload.get("client_vehicle_id") or payload.get("vehicle_id")
            )
            unlink_cards = self._validated_optional_bool(payload, "unlink_cards", default=True)
            linked_cards = [
                card
                for card in cards
                if card.client_id == client.id and card.client_vehicle_id == vehicle.id
            ]
            if linked_cards and not unlink_cards:
                self._fail(
                    "client_vehicle_has_linked_cards",
                    "Нельзя удалить автомобиль клиента, пока к нему привязаны карточки.",
                    status_code=409,
                    details={
                        "client_id": client.id,
                        "client_vehicle_id": vehicle.id,
                        "linked_card_ids": [card.id for card in linked_cards],
                    },
                )
            client.vehicles = [
                candidate for candidate in client.vehicles if candidate.id != vehicle.id
            ]
            deleted_key = self._client_vehicle_identity_key(
                vehicle.vehicle,
                vehicle.vin,
                vehicle.license_plate,
                vehicle.year,
            )
            if deleted_key and deleted_key not in set(client.deleted_vehicle_keys or []):
                client.deleted_vehicle_keys.append(deleted_key)
            unlinked_card_ids: list[str] = []
            for card in linked_cards:
                card.client_vehicle_id = ""
                self._touch_card(card, actor_name)
                unlinked_card_ids.append(card.id)
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="card_client_vehicle_unlinked",
                    message=f"{actor_name} убрал связь карточки с удалённым автомобилем клиента",
                    card_id=card.id,
                    details={"client_id": client.id, "client_vehicle_id": vehicle.id},
                )
            client.updated_at = utc_now_iso()
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="client_vehicle_deleted",
                message=f"{actor_name} удалил автомобиль клиента",
                card_id=None,
                details={
                    "client_id": client.id,
                    "client_vehicle_id": vehicle.id,
                    "unlinked_card_ids": unlinked_card_ids,
                },
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=cards,
                clients=clients,
                events=events,
            )
            return {
                "client": self._serialize_client(client, cards, include_stats=True),
                "vehicle": vehicle.to_dict(),
                "meta": {
                    "deleted": True,
                    "unlinked_card_ids": unlinked_card_ids,
                    "linked_cards_unlinked": len(unlinked_card_ids),
                },
            }

    def unlink_card_from_client(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            events = bundle["events"]
            card = self._find_card(cards, payload.get("card_id"))
            actor_name, source = self._audit_identity(payload, default_source="api")
            previous_client_id = card.client_id
            previous_client_vehicle_id = card.client_vehicle_id
            changed = bool(previous_client_id)
            if changed:
                card.client_id = ""
                card.client_vehicle_id = ""
                self._touch_card(card, actor_name)
                self._append_event(
                    events,
                    actor_name=actor_name,
                    source=source,
                    action="card_client_unlinked",
                    message=f"{actor_name} убрал связь карточки с клиентом",
                    card_id=card.id,
                    details={
                        "previous_client_id": previous_client_id,
                        "previous_client_vehicle_id": previous_client_vehicle_id,
                    },
                )
                self._save_bundle(
                    bundle,
                    columns=bundle["columns"],
                    cards=cards,
                    clients=bundle["clients"],
                    events=events,
                )
            return {
                "card": self._serialize_card(
                    card,
                    events,
                    column_labels=self._column_labels(bundle["columns"]),
                    include_removed_attachments=True,
                ),
                "meta": {
                    "changed": changed,
                    "previous_client_id": previous_client_id,
                    "previous_client_vehicle_id": previous_client_vehicle_id,
                },
            }

    def delete_client(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            cards = bundle["cards"]
            clients = list(bundle["clients"])
            events = bundle["events"]
            actor_name, source = self._audit_identity(payload, default_source="api")
            client = self._find_client(clients, payload.get("client_id") or payload.get("id"))
            allow_linked = self._validated_optional_bool(payload, "allow_linked", default=False)
            linked_cards = [card for card in cards if card.client_id == client.id]
            if linked_cards and not allow_linked:
                self._fail(
                    "client_has_linked_cards",
                    "Нельзя удалить клиента, пока к нему привязаны карточки.",
                    status_code=409,
                    details={
                        "client_id": client.id,
                        "linked_card_ids": [card.id for card in linked_cards],
                    },
                )
            if linked_cards:
                for card in linked_cards:
                    card.client_id = ""
                    card.client_vehicle_id = ""
                    self._touch_card(card, actor_name)
                    self._append_event(
                        events,
                        actor_name=actor_name,
                        source=source,
                        action="card_client_unlinked",
                        message=f"{actor_name} убрал связь карточки с удаляемым клиентом",
                        card_id=card.id,
                        details={"previous_client_id": client.id},
                    )
            clients = [item for item in clients if item.id != client.id]
            self._append_event(
                events,
                actor_name=actor_name,
                source=source,
                action="client_deleted",
                message=f"{actor_name} удалил клиента",
                card_id=None,
                details={"client_id": client.id, "client_name": client.name()},
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=cards,
                clients=clients,
                events=events,
            )
            return {
                "client": self._serialize_client(client, cards, include_stats=True),
                "meta": {
                    "deleted": True,
                    "allow_linked": allow_linked,
                    "unlinked_cards": len(linked_cards),
                },
            }

    def suggest_clients_for_card(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            limit = self._validated_limit(payload.get("limit"), default=8, maximum=30)
            bundle = self._store.read_bundle()
            card = self._find_card(bundle["cards"], payload.get("card_id"))
            query = normalize_text(payload.get("query"), default="", limit=240)
            if not query:
                query = " ".join(
                    part
                    for part in (
                        card.vehicle_profile.customer_name,
                        card.vehicle_profile.customer_phone,
                        *list(card.vehicle_profile.customer_phones or []),
                        card.repair_order.client,
                        card.repair_order.phone,
                        card.vehicle_profile.vin,
                        card.repair_order.vin,
                        card.repair_order.license_plate,
                        card.vehicle_profile.registration_plate,
                    )
                    if part
                )
            if not query.strip():
                return {
                    "card": self._serialize_card(
                        card,
                        bundle["events"],
                        column_labels=self._column_labels(bundle["columns"]),
                        compact=True,
                    ),
                    "clients": [],
                    "meta": {"query": query, "total": 0, "returned": 0},
                }
            matches = self._rank_client_matches(bundle["clients"], query, bundle["cards"])
            selected = [client for _, client in matches[:limit]]
            related_cards_by_client_id = self._client_related_cards_map(selected, bundle["cards"])
            return {
                "card": self._serialize_card(
                    card,
                    bundle["events"],
                    column_labels=self._column_labels(bundle["columns"]),
                    compact=True,
                ),
                "clients": [
                    self._serialize_client(
                        client,
                        bundle["cards"],
                        include_stats=True,
                        compact=True,
                        query=query,
                        related_cards=related_cards_by_client_id.get(client.id, []),
                    )
                    for client in selected
                ],
                "meta": {"query": query, "total": len(matches), "returned": len(selected)},
            }

    def _ordered_clients(self, clients: list[ClientProfile]) -> list[ClientProfile]:
        return sorted(clients, key=lambda item: (item.name().casefold(), item.created_at, item.id))

    def _find_client(self, clients: list[ClientProfile], client_id: str | None) -> ClientProfile:
        client = self._find_client_or_none(clients, client_id)
        if client is None:
            self._fail(
                "not_found",
                "Клиент не найден.",
                status_code=404,
                details={"client_id": normalize_text(client_id, default="", limit=128)},
            )
        return client

    def _find_client_or_none(
        self, clients: list[ClientProfile], client_id: str | None
    ) -> ClientProfile | None:
        requested_id = normalize_text(client_id, default="", limit=128)
        if not requested_id:
            return None
        requested_short_id = requested_id.upper()
        for client in clients:
            if (
                client.id == requested_id
                or short_entity_id(client.id, prefix="CL").upper() == requested_short_id
            ):
                return client
        return None

    def _canonical_client_phone_keys(self, client: ClientProfile) -> set[str]:
        keys: set[str] = set()
        for phone in [client.phone, *list(client.phones or [])]:
            digits = re.sub(r"\D+", "", str(phone or ""))
            if len(digits) < 7:
                continue
            keys.add("7" + digits[-10:] if len(digits) >= 10 else digits)
        return keys

    def _client_duplicate_vehicle_keys(self, client: ClientProfile) -> set[str]:
        keys: set[str] = set()
        for vehicle in client.vehicles:
            key = self._client_vehicle_identity_key(
                vehicle.vehicle,
                vehicle.vin,
                vehicle.license_plate,
                vehicle.year,
            )
            if key:
                keys.add(key)
        return keys

    def _find_duplicate_client(
        self, clients: list[ClientProfile], client: ClientProfile
    ) -> ClientProfile | None:
        name_key = self._normalize_search_text(client.name())
        phone_keys = self._canonical_client_phone_keys(client)
        if not name_key or not phone_keys:
            return None
        vehicle_keys = self._client_duplicate_vehicle_keys(client)
        matches: list[ClientProfile] = []
        for existing in clients:
            if existing.id == client.id:
                continue
            if self._normalize_search_text(existing.name()) != name_key:
                continue
            if not phone_keys.intersection(self._canonical_client_phone_keys(existing)):
                continue
            existing_vehicle_keys = self._client_duplicate_vehicle_keys(existing)
            if vehicle_keys:
                if not existing_vehicle_keys:
                    continue
                if not vehicle_keys.intersection(existing_vehicle_keys):
                    continue
            matches.append(existing)
        if not matches:
            return None
        return max(
            matches,
            key=lambda item: (
                len(item.vehicles),
                item.updated_at,
                item.created_at,
                item.id,
            ),
        )

    def _client_patch_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        source_payload: dict[str, Any] = {}
        for nested_key in ("client", "patch"):
            nested_payload = payload.get(nested_key)
            if isinstance(nested_payload, dict):
                source_payload.update(nested_payload)
        source_payload.update(
            {key: value for key, value in payload.items() if key not in {"client", "patch"}}
        )
        allowed = {
            "client_type",
            "type",
            "last_name",
            "first_name",
            "middle_name",
            "display_name",
            "phone",
            "phones",
            "email",
            "emails",
            "comment",
            "note",
            "legal_name",
            "short_name",
            "inn",
            "kpp",
            "ogrn",
            "ogrnip",
            "checking_account",
            "account",
            "bank_name",
            "bank",
            "bik",
            "correspondent_account",
            "corr_account",
            "legal_address",
            "actual_address",
            "contact_person",
            "contact_position",
            "vehicles",
        }
        return {key: value for key, value in source_payload.items() if key in allowed}

    def _validated_client_profile(self, payload: dict[str, Any]) -> ClientProfile:
        now_iso = utc_now_iso()
        raw_payload = self._client_patch_payload(payload)
        raw_payload["id"] = normalize_text(
            payload.get("client_id") or payload.get("id") or str(uuid.uuid4()),
            default=str(uuid.uuid4()),
            limit=128,
        )
        raw_payload["created_at"] = now_iso
        raw_payload["updated_at"] = now_iso
        client = ClientProfile.from_dict(raw_payload)
        if not client.name().strip() or client.name() in {"Без имени", "Без названия"}:
            self._fail(
                "validation_error",
                "У клиента должно быть ФИО или название организации.",
                details={"field": "display_name"},
            )
        return client

    def _client_stats(self, client: ClientProfile, cards: list[Card]) -> dict[str, Any]:
        return self._client_stats_from_related(
            client,
            cards,
            self._client_related_cards(client, cards),
        )

    def _client_stats_from_related(
        self,
        client: ClientProfile,
        cards: list[Card],
        related_cards: list[Card],
        *,
        vehicles_total: int | None = None,
    ) -> dict[str, Any]:
        repair_order_cards = [card for card in related_cards if self._card_has_repair_order(card)]
        closed_orders = [
            card
            for card in repair_order_cards
            if card.repair_order.status == REPAIR_ORDER_STATUS_CLOSED
        ]
        active_orders = [
            card
            for card in repair_order_cards
            if card.repair_order.status != REPAIR_ORDER_STATUS_CLOSED
        ]
        last_visit = ""
        for card in sorted(
            repair_order_cards or related_cards,
            key=lambda item: (
                item.repair_order.closed_at
                or item.repair_order.opened_at
                or item.updated_at
                or item.created_at,
                item.id,
            ),
            reverse=True,
        ):
            last_visit = (
                card.repair_order.closed_at
                or card.repair_order.opened_at
                or card.updated_at
                or card.created_at
            )
            break
        return {
            "cards_total": len(related_cards),
            "repair_orders_total": len(repair_order_cards),
            "active_repair_orders": len(active_orders),
            "closed_repair_orders": len(closed_orders),
            "vehicles_total": (
                vehicles_total
                if vehicles_total is not None
                else len(self._client_vehicles(client, cards, related_cards=related_cards))
            ),
            "last_visit": last_visit,
        }

    def _client_related_cards(self, client: ClientProfile, cards: list[Card]) -> list[Card]:
        keys = self._client_match_keys(client)
        related: list[Card] = []
        for card in cards:
            if card.client_id == client.id:
                related.append(card)
                continue
            card_values = self._card_client_values(card)
            if any(key and key in card_values for key in keys):
                related.append(card)
        related.sort(key=lambda item: (item.updated_at, item.created_at, item.id), reverse=True)
        return related

    def _client_related_cards_map(
        self, clients: list[ClientProfile], cards: list[Card]
    ) -> dict[str, list[Card]]:
        if not clients:
            return {}
        clients_by_id = {client.id: client for client in clients}
        client_ids_by_key: dict[str, set[str]] = {}
        for client in clients:
            for key in self._client_match_keys(client):
                if key:
                    client_ids_by_key.setdefault(key, set()).add(client.id)

        related_by_client_id: dict[str, list[Card]] = {client.id: [] for client in clients}
        seen_cards_by_client: dict[str, set[str]] = {client.id: set() for client in clients}

        def add_card(client_id: str, card: Card) -> None:
            if client_id not in clients_by_id:
                return
            seen_cards = seen_cards_by_client.setdefault(client_id, set())
            if card.id in seen_cards:
                return
            seen_cards.add(card.id)
            related_by_client_id.setdefault(client_id, []).append(card)

        for card in cards:
            if card.client_id:
                add_card(card.client_id, card)
            matched_client_ids: set[str] = set()
            for key in self._card_client_values(card):
                matched_client_ids.update(client_ids_by_key.get(key, set()))
            for client_id in matched_client_ids:
                add_card(client_id, card)

        for related_cards in related_by_client_id.values():
            related_cards.sort(
                key=lambda item: (item.updated_at, item.created_at, item.id),
                reverse=True,
            )
        return related_by_client_id

    def _client_related_vehicle_fields_index(
        self, clients: list[ClientProfile], cards: list[Card]
    ) -> dict[str, list[str]]:
        clients_by_id = {client.id: client for client in clients}
        client_ids_by_key: dict[str, set[str]] = {}
        for client in clients:
            for key in self._client_match_keys(client):
                if key:
                    client_ids_by_key.setdefault(key, set()).add(client.id)

        related_fields: dict[str, list[str]] = {}
        seen_cards_by_client: dict[str, set[str]] = {}

        def add_card(client_id: str, card: Card, *, explicit_link: bool = False) -> None:
            if client_id not in clients_by_id:
                return
            seen_cards = seen_cards_by_client.setdefault(client_id, set())
            if card.id in seen_cards:
                return
            seen_cards.add(card.id)
            fields = [
                card.vehicle_display(),
                card.vehicle_profile.make_display,
                card.vehicle_profile.model_display,
                card.vehicle_profile.registration_plate,
                self._client_vehicle_search_vin(card.vehicle_profile.vin),
                card.repair_order.vehicle,
                card.repair_order.license_plate,
                self._client_vehicle_search_vin(card.repair_order.vin),
                card.repair_order.number,
            ]
            if explicit_link:
                fields.extend(
                    [
                        card.vehicle_profile.customer_name,
                        card.vehicle_profile.customer_phone,
                        *list(card.vehicle_profile.customer_phones or []),
                        card.repair_order.client,
                        card.repair_order.phone,
                    ]
                )
            related_fields.setdefault(client_id, []).extend(
                field for field in fields if str(field or "").strip()
            )

        for card in cards:
            if card.client_id:
                add_card(card.client_id, card, explicit_link=True)
            matched_client_ids: set[str] = set()
            for key in self._card_client_values(card):
                matched_client_ids.update(client_ids_by_key.get(key, set()))
            for client_id in matched_client_ids:
                add_card(client_id, card)

        return related_fields

    def _client_related_vehicle_fields_index_for(
        self, clients: list[ClientProfile], cards: list[Card]
    ) -> dict[str, list[str]]:
        signature = (
            tuple(self._client_search_index_key(client) for client in clients),
            tuple(
                (
                    card.id,
                    card.updated_at,
                    card.client_id,
                    card.client_vehicle_id,
                )
                for card in cards
            ),
        )
        if signature == self._client_related_vehicle_fields_index_signature:
            return self._client_related_vehicle_fields_index_cache

        related_fields = self._client_related_vehicle_fields_index(clients, cards)
        self._client_related_vehicle_fields_index_signature = signature
        self._client_related_vehicle_fields_index_cache = related_fields
        return related_fields

    def _score_client_related_search_fields(
        self,
        fields: list[str],
        *,
        query_variants: list[str],
        query_digits: str,
        query_phone_variants: set[str],
    ) -> int:
        if not fields:
            return 0
        related_searchable = [self._normalize_search_text(value) for value in fields if value]
        related_compact_searchable = [
            re.sub(r"[\W_]+", "", value) for value in related_searchable if value
        ]
        related_phone_keys: set[str] = set()
        for value in fields:
            related_phone_keys.update(self._phone_match_keys(value))
        score = 0
        for variant in query_variants:
            if not variant:
                continue
            for value in related_searchable:
                if value == variant:
                    score += 7
                elif variant in value:
                    score += 5
                elif all(part in value for part in variant.split()):
                    score += 3
            compact_variant = re.sub(r"[\W_]+", "", variant)
            if compact_variant and any(
                compact_variant in value for value in related_compact_searchable
            ):
                score += 5
        if len(query_digits) >= 4 and related_phone_keys:
            if any(query_digits in key or key in query_digits for key in related_phone_keys):
                score += 10
        if query_phone_variants and related_phone_keys:
            if any(
                query_variant in related_key or related_key in query_variant
                for query_variant in query_phone_variants
                for related_key in related_phone_keys
            ):
                score += 10
        return score

    def _client_vehicle_identity_key(
        self, vehicle: str = "", vin: str = "", license_plate: str = "", year: str = ""
    ) -> str:
        return "|".join(
            part.casefold()
            for part in (vehicle, vin, license_plate, year)
            if str(part or "").strip()
        )

    def _client_vehicles(
        self,
        client: ClientProfile,
        cards: list[Card],
        *,
        query: str = "",
        related_cards: list[Card] | None = None,
    ) -> list[dict[str, str]]:
        vehicles: list[dict[str, str]] = []
        seen: set[str] = set()
        deleted_keys = set(client.deleted_vehicle_keys or [])
        for stored_vehicle in client.vehicles:
            payload = stored_vehicle.to_dict()
            key = self._client_vehicle_identity_key(
                payload.get("vehicle", ""),
                payload.get("vin", ""),
                payload.get("license_plate", ""),
                payload.get("year", ""),
            )
            if not key or key in seen:
                continue
            seen.add(key)
            vehicles.append({**payload, "card_id": ""})
        for card in (
            related_cards
            if related_cards is not None
            else self._client_related_cards(client, cards)
        ):
            vehicle = card.vehicle_display() or card.repair_order.vehicle
            vin = card.vehicle_profile.vin or card.repair_order.vin
            plate = card.vehicle_profile.registration_plate or card.repair_order.license_plate
            year = str(card.vehicle_profile.production_year or "")
            key = self._client_vehicle_identity_key(vehicle, vin, plate, year)
            if not key or key in seen or key in deleted_keys:
                continue
            seen.add(key)
            vehicles.append(
                {
                    "id": card.client_vehicle_id,
                    "vehicle": vehicle,
                    "vin": vin,
                    "license_plate": plate,
                    "year": year,
                    "mileage": str(card.vehicle_profile.mileage or card.repair_order.mileage or ""),
                    "body_number": card.vehicle_profile.body_number,
                    "chassis_number": card.vehicle_profile.chassis_number,
                    "engine_code": card.vehicle_profile.engine_code,
                    "engine_model": card.vehicle_profile.engine_model,
                    "gearbox_type": card.vehicle_profile.gearbox_type,
                    "gearbox_model": card.vehicle_profile.gearbox_model,
                    "drivetrain": card.vehicle_profile.drivetrain,
                    "card_id": card.id,
                }
            )
        query_text = self._normalize_search_text(query)
        query_compact = re.sub(r"[\W_]+", "", query_text)
        query_digits = re.sub(r"\D+", "", query)
        if query_text or query_digits:

            def vehicle_score(item: dict[str, str]) -> int:
                score = 0
                values = [
                    item.get("vin", ""),
                    item.get("license_plate", ""),
                    item.get("body_number", ""),
                    item.get("chassis_number", ""),
                    item.get("vehicle", ""),
                    item.get("brand", ""),
                    item.get("model", ""),
                    item.get("year", ""),
                ]
                for value in values:
                    normalized = self._normalize_search_text(value)
                    compact = re.sub(r"[\W_]+", "", normalized)
                    digits = re.sub(r"\D+", "", value)
                    if query_text and normalized == query_text:
                        score += 20
                    elif query_text and query_text in normalized:
                        score += 8
                    if query_compact and compact == query_compact:
                        score += 18
                    elif query_compact and query_compact in compact:
                        score += 6
                    if query_digits and digits and query_digits in digits:
                        score += 10
                return score

            vehicles.sort(key=vehicle_score, reverse=True)
        return vehicles

    def _client_orders(self, client: ClientProfile, cards: list[Card]) -> list[dict[str, Any]]:
        return [
            self._serialize_repair_order_list_item(card)
            for card in self._client_related_cards(client, cards)
            if self._card_has_repair_order(card)
        ]

    def _serialize_client(
        self,
        client: ClientProfile,
        cards: list[Card],
        *,
        include_stats: bool = False,
        compact: bool = False,
        include_vehicle_preview: bool = True,
        query: str = "",
        related_cards: list[Card] | None = None,
    ) -> dict[str, Any]:
        payload = client.to_dict()
        payload["short_id"] = short_entity_id(client.id, prefix="CL")
        preview_vehicles = (
            self._client_vehicles(client, cards, query=query, related_cards=related_cards)
            if include_vehicle_preview
            else [vehicle.to_dict() for vehicle in client.vehicles[:2]]
        )
        payload["vehicles_preview"] = (
            preview_vehicles[:2] if include_vehicle_preview else preview_vehicles
        )
        if include_stats:
            resolved_related_cards = (
                related_cards
                if related_cards is not None
                else self._client_related_cards(client, cards)
            )
            payload["stats"] = self._client_stats_from_related(
                client,
                cards,
                resolved_related_cards,
                vehicles_total=len(preview_vehicles) if include_vehicle_preview else None,
            )
        if compact:
            keep = {
                "id",
                "short_id",
                "client_type",
                "type_label",
                "name",
                "full_name",
                "display_name",
                "last_name",
                "first_name",
                "middle_name",
                "phone",
                "phones",
                "email",
                "emails",
                "inn",
                "kpp",
                "ogrn",
                "contact_person",
                "vehicles_preview",
                "vehicles",
                "updated_at",
                "stats",
            }
            return {key: value for key, value in payload.items() if key in keep}
        return payload

    def _client_profile_payload(
        self, client: ClientProfile, cards: list[Card], *, order_limit: int
    ) -> dict[str, Any]:
        related_cards = self._client_related_cards(client, cards)
        orders = [
            self._serialize_repair_order_list_item(card)
            for card in related_cards
            if self._card_has_repair_order(card)
        ]
        vehicles = self._client_vehicles(client, cards, related_cards=related_cards)
        return {
            "client": self._serialize_client(
                client,
                cards,
                include_stats=True,
                related_cards=related_cards,
            ),
            "vehicles": vehicles,
            "repair_orders": orders[:order_limit],
            "meta": {
                "repair_orders_total": len(orders),
                "repair_orders_returned": min(len(orders), order_limit),
                "vehicles_total": len(vehicles),
                "order_limit": order_limit,
            },
        }

    def _client_match_keys(self, client: ClientProfile) -> set[str]:
        values = {
            client.name(),
            client.full_name(),
            client.display_name,
            client.phone,
            client.email,
            *client.emails,
            client.inn,
            client.contact_person,
            *client.phones,
        }
        for vehicle in client.vehicles:
            values.update(
                {
                    vehicle.vehicle,
                    vehicle.brand,
                    vehicle.model,
                    self._client_vehicle_search_vin(vehicle.vin),
                    vehicle.license_plate,
                    vehicle.year,
                    vehicle.body_number,
                    vehicle.chassis_number,
                    vehicle.engine_code,
                    vehicle.engine_model,
                    vehicle.gearbox_model,
                    vehicle.drivetrain,
                }
            )
        keys: set[str] = set()
        for value in values:
            normalized = self._normalize_search_text(value)
            if normalized:
                keys.add(normalized)
            keys.update(self._phone_match_keys(value))
        return keys

    def _client_search_digits_blob(self, client: ClientProfile) -> str:
        values = [
            client.name(),
            client.full_name(),
            client.display_name,
            client.inn,
            client.ogrn,
            client.contact_person,
        ]
        for phone in [client.phone, *client.phones]:
            digits = re.sub(r"\D+", "", str(phone or ""))
            if len(digits) < 7:
                continue
            values.append(digits)
            if len(digits) >= 10:
                last_ten = digits[-10:]
                values.extend([last_ten, "7" + last_ten, "8" + last_ten])
        for vehicle in client.vehicles:
            values.extend(
                [
                    vehicle.vehicle,
                    vehicle.brand,
                    vehicle.model,
                    self._client_vehicle_search_vin(vehicle.vin),
                    vehicle.license_plate,
                    vehicle.year,
                    vehicle.body_number,
                    vehicle.chassis_number,
                    vehicle.engine_code,
                    vehicle.engine_model,
                    vehicle.gearbox_model,
                    vehicle.drivetrain,
                ]
            )
        return "".join(re.sub(r"\D+", "", str(value or "")) for value in values)

    def _client_direct_digit_values(self, client: ClientProfile) -> set[str]:
        values = [
            client.name(),
            client.full_name(),
            client.display_name,
            client.phone,
            *client.phones,
            client.inn,
            client.ogrn,
            client.contact_person,
        ]
        digits_values: set[str] = set()
        for value in values:
            digits = re.sub(r"\D+", "", str(value or ""))
            if len(digits) < 3:
                continue
            digits_values.add(digits)
            if len(digits) >= 4 and digits[0] in "78":
                digits_values.add(digits[1:])
            if len(digits) >= 10:
                last_ten = digits[-10:]
                digits_values.update({last_ten, "7" + last_ten, "8" + last_ten})
        return digits_values

    def _card_client_values(self, card: Card) -> set[str]:
        values = [
            card.vehicle_profile.customer_name,
            card.vehicle_profile.customer_phone,
            *list(card.vehicle_profile.customer_phones or []),
            card.repair_order.client,
            card.repair_order.phone,
        ]
        normalized_values: set[str] = set()
        for value in values:
            normalized = self._normalize_search_text(value)
            if normalized:
                normalized_values.add(normalized)
            normalized_values.update(self._phone_match_keys(value))
        return normalized_values

    def _phone_match_keys(self, value: Any) -> set[str]:
        text = str(value or "")
        candidates = [text]
        candidates.extend(match.group(0) for match in _PHONE_PATTERN.finditer(text))
        candidates.extend(match.group(0) for match in re.finditer(r"\d[\d\s()+-]{7,}\d", text))
        keys: set[str] = set()
        for candidate in candidates:
            digits = re.sub(r"\D+", "", candidate)
            if len(digits) < 7:
                continue
            keys.add(digits)
            if len(digits) >= 10:
                last_ten = digits[-10:]
                keys.add(last_ten)
                keys.add("7" + last_ten)
                keys.add("8" + last_ten)
        return keys

    def _phone_search_variants(self, value: Any) -> set[str]:
        text = str(value or "")
        digits = re.sub(r"\D+", "", text)
        if len(digits) < 3:
            return set()
        variants = {digits}
        if len(digits) >= 4 and digits[0] in "78":
            variants.add(digits[1:])
        if len(digits) >= 10:
            last_ten = digits[-10:]
            variants.add(last_ten)
            if len(last_ten) >= 4 and last_ten[0] in "78":
                variants.add(last_ten[1:])
        return {variant for variant in variants if len(variant) >= 3}

    def _client_search_index_key(self, client: ClientProfile) -> tuple[Any, ...]:
        vehicle_keys = tuple(
            (
                vehicle.vehicle,
                vehicle.brand,
                vehicle.model,
                vehicle.vin,
                vehicle.license_plate,
                vehicle.year,
                vehicle.body_number,
                vehicle.chassis_number,
                vehicle.engine_code,
                vehicle.engine_model,
                vehicle.gearbox_model,
                vehicle.drivetrain,
            )
            for vehicle in client.vehicles
        )
        return (
            client.id,
            client.updated_at,
            client.client_type,
            client.last_name,
            client.first_name,
            client.middle_name,
            client.display_name,
            client.phone,
            tuple(client.phones),
            client.email,
            tuple(client.emails),
            client.inn,
            client.ogrn,
            client.contact_person,
            vehicle_keys,
        )

    def _client_search_index_for(self, clients: list[ClientProfile]) -> dict[str, dict[str, Any]]:
        signature = tuple(self._client_search_index_key(client) for client in clients)
        if signature == self._client_search_index_signature:
            return self._client_search_index

        index: dict[str, dict[str, Any]] = {}
        for client in clients:
            fields = [
                client.name(),
                client.full_name(),
                client.display_name,
                client.phone,
                " ".join(client.phones),
                client.email,
                " ".join(client.emails),
                client.inn,
                client.ogrn,
                client.contact_person,
            ]
            vehicle_fields: list[str] = []
            for vehicle in client.vehicles:
                vehicle_fields.extend(
                    [
                        vehicle.vehicle,
                        vehicle.brand,
                        vehicle.model,
                        self._client_vehicle_search_vin(vehicle.vin),
                        vehicle.license_plate,
                        vehicle.year,
                        vehicle.body_number,
                        vehicle.chassis_number,
                        vehicle.engine_code,
                        vehicle.engine_model,
                        vehicle.gearbox_model,
                        vehicle.drivetrain,
                    ]
                )
            searchable = [self._normalize_search_text(value) for value in fields if value]
            vehicle_searchable = [
                self._normalize_search_text(value) for value in vehicle_fields if value
            ]
            compact_searchable = [
                re.sub(r"[\W_]+", "", value)
                for value in [*searchable, *vehicle_searchable]
                if value
            ]
            phone_variants: set[str] = set()
            for phone in [client.phone, *client.phones]:
                phone_variants.update(self._phone_search_variants(phone))
            index[client.id] = {
                "searchable": searchable,
                "vehicle_searchable": vehicle_searchable,
                "compact_searchable": compact_searchable,
                "digits_blob": self._client_search_digits_blob(client),
                "direct_digit_values": self._client_direct_digit_values(client),
                "match_keys": self._client_match_keys(client),
                "phone_variants": phone_variants,
            }

        self._client_search_index_signature = signature
        self._client_search_index = index
        return index

    def _client_vehicle_search_vin(self, value: Any) -> str:
        raw = normalize_text(value, default="", limit=160).upper()
        if not raw:
            return ""
        compact = re.sub(r"[^A-Z0-9]+", "", raw)
        if len(compact) < 6:
            return ""
        if len(set(compact)) <= 1:
            return ""
        return raw

    def _rank_client_matches(
        self, clients: list[ClientProfile], query: str, cards: list[Card] | None = None
    ) -> list[tuple[int, ClientProfile]]:
        query = normalize_text(query, default="", limit=500)
        if not query:
            return [(1, client) for client in self._ordered_clients(clients)]
        query_variants = self._search_text_variants(query)
        query_digits = re.sub(r"\D+", "", query)
        query_phone_variants = self._phone_search_variants(query)
        phone_like_query = bool(query_digits) and not re.search(r"[A-Za-zА-Яа-я]", query)
        client_search_index = self._client_search_index_for(clients)
        related_fields_by_client_id = (
            self._client_related_vehicle_fields_index_for(clients, cards) if cards else {}
        )

        if phone_like_query:
            ranked: list[tuple[int, ClientProfile]] = []
            for client in clients:
                score = 0
                indexed = client_search_index.get(client.id, {})
                digits_blob = indexed.get("digits_blob", "")
                direct_digit_values = indexed.get("direct_digit_values", set())
                if direct_digit_values:
                    if query_digits in direct_digit_values:
                        score += 1000
                    elif any(
                        query_digits in value or value in query_digits
                        for value in direct_digit_values
                    ):
                        score += 200
                    if query_phone_variants.intersection(direct_digit_values):
                        score += 1000
                if digits_blob and query_digits in digits_blob:
                    score += 10
                score += self._score_client_related_search_fields(
                    related_fields_by_client_id.get(client.id, []),
                    query_variants=query_variants,
                    query_digits=query_digits,
                    query_phone_variants=query_phone_variants,
                )
                if score > 0:
                    ranked.append((score, client))
            ranked.sort(
                key=lambda item: (item[0], item[1].updated_at, item[1].name()), reverse=True
            )
            return ranked

        ranked: list[tuple[int, ClientProfile]] = []
        cards = cards or []
        for client in clients:
            indexed = client_search_index.get(client.id, {})
            searchable = indexed.get("searchable", [])
            vehicle_searchable = indexed.get("vehicle_searchable", [])
            compact_searchable = indexed.get("compact_searchable", [])
            score = 0
            for variant in query_variants:
                if not variant:
                    continue
                for value in searchable:
                    if value == variant:
                        score += 8
                    elif variant in value:
                        score += 4
                    elif all(part in value for part in variant.split()):
                        score += 2
                for value in vehicle_searchable:
                    if value == variant:
                        score += 7
                    elif variant in value:
                        score += 5
                    elif all(part in value for part in variant.split()):
                        score += 3
                compact_variant = re.sub(r"[\W_]+", "", variant)
                if compact_variant and any(
                    compact_variant in value for value in compact_searchable
                ):
                    score += 5
            if len(query_digits) >= 4:
                phone_digits = " ".join(re.sub(r"\D+", "", phone) for phone in client.phones)
                if query_digits in phone_digits:
                    score += 10
            if query_phone_variants:
                client_phone_variants = indexed.get("phone_variants", set())
                if client_phone_variants and any(
                    query_variant in client_variant or client_variant in query_variant
                    for query_variant in query_phone_variants
                    for client_variant in client_phone_variants
                ):
                    score += 10
                elif query_phone_variants.intersection(indexed.get("match_keys", set())):
                    score += 10
            score += self._score_client_related_search_fields(
                related_fields_by_client_id.get(client.id, []),
                query_variants=query_variants,
                query_digits=query_digits,
                query_phone_variants=query_phone_variants,
            )
            if score > 0:
                ranked.append((score, client))
        ranked.sort(key=lambda item: (item[0], item[1].updated_at, item[1].name()), reverse=True)
        return ranked

    def _sync_card_client_fields(
        self, card: Card, client: ClientProfile, *, overwrite: bool = False
    ) -> bool:
        changed = False
        client_name = client.name()
        client_phone = client.phone
        client_phones = list(client.phones or ([client_phone] if client_phone else []))
        if client_name and (overwrite or not card.vehicle_profile.customer_name):
            if card.vehicle_profile.customer_name != client_name:
                card.vehicle_profile.customer_name = client_name
                changed = True
        if client_phone and (overwrite or not card.vehicle_profile.customer_phone):
            if card.vehicle_profile.customer_phone != client_phone:
                card.vehicle_profile.customer_phone = client_phone
                changed = True
        if client_phones and (
            overwrite or not getattr(card.vehicle_profile, "customer_phones", [])
        ):
            if list(card.vehicle_profile.customer_phones) != client_phones:
                card.vehicle_profile.customer_phones = client_phones
                changed = True
        if self._card_has_repair_order(card):
            if client_name and (overwrite or not card.repair_order.client):
                if card.repair_order.client != client_name:
                    card.repair_order.client = client_name
                    changed = True
            if client_phone and (overwrite or not card.repair_order.phone):
                if card.repair_order.phone != client_phone:
                    card.repair_order.phone = client_phone
                    changed = True
        return changed

    def _find_client_vehicle_or_none(
        self, client: ClientProfile, vehicle_id: str | None
    ) -> ClientVehicle | None:
        requested_id = normalize_text(vehicle_id, default="", limit=128)
        if not requested_id:
            return None
        requested_short_id = requested_id.upper()
        for vehicle in client.vehicles:
            if (
                vehicle.id == requested_id
                or short_entity_id(vehicle.id, prefix="CV").upper() == requested_short_id
            ):
                return vehicle
        return None

    def _find_client_vehicle(self, client: ClientProfile, vehicle_id: str | None) -> ClientVehicle:
        vehicle = self._find_client_vehicle_or_none(client, vehicle_id)
        if vehicle is None:
            self._fail(
                "not_found",
                "Автомобиль клиента не найден.",
                status_code=404,
                details={
                    "client_id": client.id,
                    "client_vehicle_id": normalize_text(vehicle_id, default="", limit=128),
                },
            )
        return vehicle

    def _replace_client_vehicle(self, client: ClientProfile, vehicle: ClientVehicle) -> None:
        for index, candidate in enumerate(client.vehicles):
            if candidate.id == vehicle.id:
                client.vehicles[index] = vehicle
                return
        client.vehicles.append(vehicle)

    def _dedupe_client_vehicles(self, vehicles: list[ClientVehicle]) -> list[ClientVehicle]:
        return ClientProfile(
            id=str(uuid.uuid4()),
            display_name="temporary",
            vehicles=vehicles,
        ).vehicles

    def _validated_client_vehicle(self, payload: dict[str, Any]) -> ClientVehicle:
        source_payload: dict[str, Any] = {}
        nested = payload.get("vehicle")
        if isinstance(nested, dict):
            source_payload.update(nested)
        source_payload.update(
            {
                key: value
                for key, value in payload.items()
                if not (key == "vehicle" and isinstance(nested, dict))
                and key
                in {
                    "id",
                    "client_vehicle_id",
                    "vehicle",
                    "brand",
                    "make",
                    "model",
                    "vin",
                    "license_plate",
                    "registration_plate",
                    "plate",
                    "year",
                    "mileage",
                    "body_number",
                    "chassis_number",
                    "engine_code",
                    "engine_model",
                    "gearbox_type",
                    "gearbox_model",
                    "drivetrain",
                    "drive_type",
                    "notes",
                    "comment",
                }
            }
        )
        vehicle = ClientVehicle.from_value(source_payload)
        if vehicle is None:
            self._fail(
                "validation_error",
                "У автомобиля клиента должны быть модель, VIN, госномер или номер кузова.",
                details={"field": "vehicle"},
            )
        return vehicle

    def _client_vehicle_from_card(self, card: Card, *, vehicle_id: str = "") -> ClientVehicle:
        profile = card.vehicle_profile
        order = card.repair_order
        year = str(profile.production_year or "").strip()
        mileage = str(profile.mileage or order.mileage or "").strip()
        vehicle = ClientVehicle.from_value(
            {
                "id": vehicle_id or str(uuid.uuid4()),
                "vehicle": card.vehicle_display() or order.vehicle,
                "brand": profile.make_display,
                "model": profile.model_display,
                "vin": profile.vin or order.vin,
                "license_plate": profile.registration_plate or order.license_plate,
                "year": year,
                "mileage": mileage,
                "body_number": profile.body_number,
                "chassis_number": profile.chassis_number,
                "engine_code": profile.engine_code,
                "engine_model": profile.engine_model,
                "gearbox_type": profile.gearbox_type,
                "gearbox_model": profile.gearbox_model,
                "drivetrain": profile.drivetrain,
            }
        )
        if vehicle is None:
            self._fail(
                "validation_error",
                "В карточке нет достаточных данных для создания автомобиля клиента.",
                details={"card_id": card.id},
            )
        return vehicle

    def _merge_client_vehicle(
        self, existing: ClientVehicle, incoming: ClientVehicle
    ) -> ClientVehicle:
        payload = existing.to_dict()
        incoming_payload = incoming.to_dict()
        for key, value in incoming_payload.items():
            if key == "id":
                continue
            if str(value or "").strip():
                payload[key] = value
        payload["id"] = existing.id
        return ClientVehicle.from_value(payload) or existing

    def _client_vehicle_profile_patch(self, vehicle: ClientVehicle) -> dict[str, Any]:
        patch: dict[str, Any] = {
            "make_display": vehicle.brand,
            "model_display": vehicle.model,
            "vin": vehicle.vin,
            "registration_plate": vehicle.license_plate,
            "body_number": vehicle.body_number,
            "chassis_number": vehicle.chassis_number,
            "engine_code": vehicle.engine_code,
            "engine_model": vehicle.engine_model,
            "gearbox_type": vehicle.gearbox_type,
            "gearbox_model": vehicle.gearbox_model,
            "drivetrain": vehicle.drivetrain,
        }
        if str(vehicle.year or "").strip().isdigit():
            patch["production_year"] = int(str(vehicle.year).strip())
        if str(vehicle.mileage or "").strip().isdigit():
            patch["mileage"] = int(str(vehicle.mileage).strip())
        return {key: value for key, value in patch.items() if value not in ("", None)}

    def _sync_card_vehicle_fields(
        self, card: Card, vehicle: ClientVehicle, *, overwrite: bool = True
    ) -> bool:
        changed = False
        vehicle_label = (
            vehicle.vehicle
            or " ".join(
                part for part in (vehicle.brand, vehicle.model, vehicle.year) if part
            ).strip()
        )
        if vehicle_label and (overwrite or not card.vehicle):
            next_vehicle = self._validated_vehicle(vehicle_label)
            if card.vehicle != next_vehicle:
                card.vehicle = next_vehicle
                changed = True
        patch = self._client_vehicle_profile_patch(vehicle)
        if not overwrite:
            current = card.vehicle_profile.to_dict()
            patch = {
                key: value
                for key, value in patch.items()
                if not str(current.get(key) or "").strip()
            }
        if patch:
            profile, _changed_fields = self._merge_vehicle_profile_patch(
                card.vehicle_profile, patch
            )
            if profile.to_storage_dict() != card.vehicle_profile.to_storage_dict():
                card.vehicle_profile = profile
                card.vehicle = self._resolved_card_vehicle_label(card.vehicle, profile)
                changed = True
        return changed

    def _sync_linked_client_vehicle_from_card(
        self, clients: list[ClientProfile], card: Card
    ) -> bool:
        if not card.client_id or not card.client_vehicle_id:
            return False
        client = self._find_client_or_none(clients, card.client_id)
        if client is None:
            return False
        existing = self._find_client_vehicle_or_none(client, card.client_vehicle_id)
        if existing is None:
            return False
        incoming = self._client_vehicle_from_card(card, vehicle_id=existing.id)
        merged = self._merge_client_vehicle(existing, incoming)
        if merged.to_dict() == existing.to_dict():
            return False
        self._replace_client_vehicle(client, merged)
        client.updated_at = utc_now_iso()
        return True
