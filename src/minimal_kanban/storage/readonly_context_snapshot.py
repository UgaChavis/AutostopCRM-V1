"""Independent context-read models with checks against unsaved legacy identities."""

from copy import deepcopy

import orjson

from ..json_safety import DEFAULT_JSON_MAX_DEPTH
from ..models import Card, ClientProfile, normalize_text, parse_datetime
from ..repair_order import RepairOrderPayment, RepairOrderRow, normalize_repair_order_payments
from .json_store import StateFileCorruptedError, _validate_snapshot_financial_fields


def _index(raw, *, alias=None):
    result = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        key = normalize_text(item.get("id") or (item.get(alias) if alias else ""), limit=128)
        if key:
            result.setdefault(key, []).append(item)
    return result


class ReadonlyContextSnapshot(dict):
    def __init__(self, bundle, state, *, source_indexes=None):
        super().__init__(bundle)
        if source_indexes is None:
            self._cards = _index(state.get("cards"))
            self._clients = _index(state.get("clients"))
            self._events = _index(state.get("events"))
        else:
            self._cards, self._clients, self._events = source_indexes
        self._link_clients = {client.id: client for client in bundle["clients"]}

    @staticmethod
    def _source(index, key, *, alias=None):
        rows = index.get(key, [])
        if len(rows) != 1:
            raise StateFileCorruptedError("Недостоверный идентификатор в снимке состояния.")
        source = rows[0]
        raw_id = source.get("id") or (source.get(alias) if alias else "")
        if not isinstance(raw_id, str) or raw_id != key:
            raise StateFileCorruptedError("Недостоверный идентификатор в снимке состояния.")
        return source

    def require_card(self, card, *, include_attachments=False):
        source = self._source(self._cards, card.id)
        if not parse_datetime(source.get("created_at")) or not parse_datetime(
            source.get("updated_at")
        ):
            raise StateFileCorruptedError("Недостоверная версия карточки в снимке состояния.")
        order = source.get("repair_order")
        if order is not None and not isinstance(order, dict):
            raise StateFileCorruptedError("Данные заказ-наряда повреждены.")
        if order:
            _validate_snapshot_financial_fields(order)
        for field in ("client_id", "client_vehicle_id"):
            raw_value = source.get(field)
            if raw_value not in (None, "") and raw_value != getattr(card, field):
                raise StateFileCorruptedError("Недостоверная связь карточки в снимке состояния.")
        if include_attachments:
            raw_attachments = source.get("attachments")
            if raw_attachments is None:
                raw_attachments = []
            if not isinstance(raw_attachments, list) or len(raw_attachments) != len(
                card.attachments
            ):
                raise StateFileCorruptedError("Данные вложений карточки повреждены.")
            attachments = _index(raw_attachments)
            for attachment in card.attachments:
                raw = self._source(attachments, attachment.id)
                if not parse_datetime(raw.get("created_at")):
                    raise StateFileCorruptedError("Недостоверная дата вложения в снимке состояния.")

    def require_client(self, client):
        source = self._source(self._clients, client.id)
        if not all(
            isinstance(source.get(field), str)
            and source[field]
            and source[field] == getattr(client, field)
            for field in ("created_at", "updated_at")
        ):
            raise StateFileCorruptedError("Недостоверная версия клиента в снимке состояния.")
        raw_vehicles = source.get("vehicles", [])
        vehicles = _index(
            raw_vehicles if isinstance(raw_vehicles, list) else [raw_vehicles],
            alias="client_vehicle_id",
        )
        for vehicle in client.vehicles:
            self._source(vehicles, vehicle.id, alias="client_vehicle_id")

    def require_card_client_links(self, card):
        if not card.client_id:
            if card.client_vehicle_id:
                raise StateFileCorruptedError("Связь автомобиля не подтверждена клиентом.")
            return
        source = self._source(self._clients, card.client_id)
        client = self._link_clients.get(card.client_id)
        if client is None:
            try:
                client = ClientProfile.from_dict(source)
            except (OverflowError, TypeError, ValueError):
                raise StateFileCorruptedError("Запись связанного клиента повреждена.") from None
            self._link_clients[card.client_id] = client
        if not card.client_vehicle_id:
            return
        raw = source.get("vehicles", [])
        vehicles = _index(raw if isinstance(raw, list) else [raw], alias="client_vehicle_id")
        self._source(vehicles, card.client_vehicle_id, alias="client_vehicle_id")
        if not any(vehicle.id == card.client_vehicle_id for vehicle in client.vehicles):
            raise StateFileCorruptedError("Связь автомобиля не подтверждена клиентом.")

    def require_event(self, event):
        source = self._source(self._events, event.id)
        if not parse_datetime(source.get("timestamp")):
            raise StateFileCorruptedError("Недостоверная дата события в снимке состояния.")

    def card_warnings(self, card):
        """Describe deterministic values synthesized by legacy normalization."""
        source = self._source(self._cards, card.id)
        warnings = []
        if not parse_datetime(source.get("deadline_timestamp")):
            warnings.append("deadline_calculated_from_created_at")
        order = source.get("repair_order") or {}
        for kind, rows in (
            ("row", order.get("works", [])),
            ("row", order.get("materials", [])),
            ("payment", order.get("payments", order.get("payment_history", []))),
        ):
            if not isinstance(rows, list):
                continue
            seen = set()
            for index, raw in enumerate(rows, start=1):
                if not isinstance(raw, dict):
                    continue
                model = (
                    RepairOrderRow.from_dict(raw, fallback_id=f"row-{index}")
                    if kind == "row"
                    else RepairOrderPayment.from_dict(raw, fallback_id=f"payment-{index}")
                )
                if model.is_empty():
                    continue
                actual_id = model.id or (f"payment-{index}" if kind == "payment" else "")
                raw_id = raw.get("id")
                if (
                    not isinstance(raw_id, str)
                    or raw_id != actual_id
                    or not actual_id
                    or actual_id in seen
                ):
                    warning = f"repair_order_{kind}_identity_unverified"
                    if warning not in warnings:
                        warnings.append(warning)
                seen.add(actual_id)
        raw_payments = order.get("payments", order.get("payment_history", []))
        if not normalize_repair_order_payments(raw_payments) and card.repair_order.payments:
            warning = "repair_order_payment_identity_unverified"
            if warning not in warnings:
                warnings.append(warning)
        return warnings


def reject_deeply_nested_native_json(value):
    """Apply the state depth limit to freshly decoded JSON objects."""
    if type(value) not in (dict, list):
        return
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth >= DEFAULT_JSON_MAX_DEPTH and item:
            raise ValueError("state file JSON is too deeply nested")
        values = item.values() if type(item) is dict else item
        pending.extend((child, depth + 1) for child in values if type(child) in (dict, list))


def encoder_enforces_state_depth():
    """Only skip the depth walk when this encoder rejects over-depth JSON."""
    for container_type in (dict, list, None):
        value = 0
        for index in range(DEFAULT_JSON_MAX_DEPTH + 1):
            value = (
                {"child": value}
                if container_type is dict or (container_type is None and index % 2)
                else [value]
            )
        try:
            orjson.dumps(value)
        except (TypeError, ValueError):
            continue
        return False
    return True


_ENCODER_ENFORCES_STATE_DEPTH = encoder_enforces_state_depth()


class CommittedCardSource:
    """Private, decoded bytes from a verified local write, indexed for one-card reads."""

    def __init__(self, store, state):
        cards = state.get("cards")
        clients = state.get("clients", [])
        events = state.get("events", [])
        if (
            not isinstance(cards, list)
            or any(not isinstance(card, dict) for card in cards)
            or not isinstance(clients, list)
            or not isinstance(events, list)
        ):
            raise StateFileCorruptedError("Снимок состояния поврежден.")
        self._cards = _index(cards)
        self._clients = _index(clients)
        self._positions = {}
        for position, card in enumerate(cards):
            card_id = normalize_text(card.get("id"), limit=128)
            if card_id:
                self._positions.setdefault(card_id, position)
        self._events_by_card = {}
        for event in events:
            if isinstance(event, dict):
                card_id = normalize_text(event.get("card_id"), limit=128)
                if card_id:
                    self._events_by_card.setdefault(card_id, []).append(event)
        self._columns, _ = store._normalize_columns(state)

    def for_card(self, store, requested_id):
        key = str(requested_id) if requested_id else ""
        cards = []
        if key in self._cards:
            source = ReadonlyContextSnapshot._source(self._cards, key)
            cards.append(
                Card.from_dict(
                    deepcopy(source),
                    valid_columns={column.id for column in self._columns},
                    default_column=self._columns[0].id,
                    fallback_position=self._positions[key],
                )
            )
        events, _ = store._normalize_events({"events": deepcopy(self._events_by_card.get(key, []))})
        return ReadonlyContextSnapshot(
            {"columns": deepcopy(self._columns), "cards": cards, "clients": [], "events": events},
            {},
            source_indexes=(self._cards, self._clients, {}),
        )


def build_context_snapshot(store, state, domains):
    raw_cards = state.get("cards")
    if not isinstance(raw_cards, list) or any(not isinstance(card, dict) for card in raw_cards):
        raise StateFileCorruptedError("Список карточек отсутствует или поврежден.")
    for name in ("clients", "stickies", "events"):
        if name not in domains:
            continue
        if name in state and not isinstance(state[name], list):
            raise StateFileCorruptedError("Снимок состояния содержит поврежденный список.")
    columns, _ = store._normalize_columns(state)
    cards, _ = store._normalize_cards(state, columns)
    clients = store._normalize_clients(state)[0] if "clients" in domains else []
    stickies = store._normalize_stickies(state)[0] if "stickies" in domains else []
    events = store._normalize_events(state)[0] if "events" in domains else []
    settings = store._normalize_settings(state)[0] if "settings" in domains else {}
    return ReadonlyContextSnapshot(
        {
            "columns": columns,
            "cards": cards,
            "clients": clients,
            "stickies": stickies,
            "events": events,
            "settings": settings,
        },
        state,
    )
