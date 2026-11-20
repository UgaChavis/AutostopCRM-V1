"""Independent context-read models with checks against unsaved legacy identities."""

import hashlib
import json
import sys
from copy import deepcopy
from threading import Lock

import orjson

from ..json_safety import DEFAULT_JSON_MAX_DEPTH
from ..models import (
    AuditEvent,
    Card,
    ClientProfile,
    normalize_text,
    parse_datetime,
    short_entity_id,
)
from ..repair_order import RepairOrderPayment, RepairOrderRow, normalize_repair_order_payments
from .json_store import (
    StateFileCorruptedError,
    _validate_cash_transaction_ids,
    _validate_raw_financial_references,
    _validate_snapshot_financial_fields,
)
from .state_shape import validate_state_shape


def _index(raw, *, alias=None):
    result = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        key = normalize_text(item.get("id") or (item.get(alias) if alias else ""), limit=128)
        if key:
            result.setdefault(key, []).append(item)
    return result


def _require_raw_search_identities(rows, *, unique):
    """Reject invented IDs before model normalization can drop or create records."""
    if not isinstance(rows, list):
        raise StateFileCorruptedError("Снимок поиска содержит поврежденный список.")
    seen = set()
    for row in rows:
        raw_id = row.get("id") if isinstance(row, dict) else None
        if (
            not isinstance(raw_id, str)
            or not raw_id
            or raw_id != normalize_text(raw_id, limit=128)
            or (unique and raw_id in seen)
        ):
            raise StateFileCorruptedError("Недостоверный идентификатор в исходных данных поиска.")
        seen.add(raw_id)


def _safe_to_detach_shallow_json(value):
    """Use the fast path only when deepcopy has ample recursion headroom."""
    if (
        sys.implementation.name != "cpython"
        or sys.version_info[:2] != (3, 12)
        or sys.getrecursionlimit() < 1000
        or sys.gettrace() is not None
        or sys.getprofile() is not None
    ):
        return False
    try:
        sys._getframe(128)
    except ValueError:
        pass
    else:
        return False
    type_of = type
    scalar_types = (str, int, float, bool, type(None))
    root_type = type_of(value)
    if root_type is not dict and root_type is not list:
        return root_type in scalar_types
    seen = set()
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        kind = type_of(item)
        identity = id(item)
        if depth > 32 or identity in seen:
            return False
        seen.add(identity)
        next_depth = depth + 1
        if kind is dict:
            for key, child in item.items():
                if type_of(key) is not str:
                    return False
                child_type = type_of(child)
                if child_type is dict or child_type is list:
                    pending.append((child, next_depth))
                elif child_type not in scalar_types:
                    return False
        else:
            for child in item:
                child_type = type_of(child)
                if child_type is dict or child_type is list:
                    pending.append((child, next_depth))
                elif child_type not in scalar_types:
                    return False
    return True


def _search_clients(raw_clients, *, known_clients=None):
    clients = []
    for raw in raw_clients:
        client = known_clients.get(raw["id"]) if known_clients is not None else None
        if client is None:
            try:
                client = ClientProfile.from_dict(raw)
            except (OverflowError, TypeError, ValueError):
                raise StateFileCorruptedError("Исходная запись клиента повреждена.") from None
        if client.id != raw["id"]:
            raise StateFileCorruptedError("Недостоверный идентификатор клиента в поиске.")
        clients.append(client)
    return sorted(clients, key=lambda item: (item.name().casefold(), item.created_at, item.id))


_SearchClientProfiles = tuple[
    tuple[bytes, ...], list[ClientProfile] | None, dict[bytes, ClientProfile]
]


def _reuse_search_client_profiles(
    rows: list[dict], cached: _SearchClientProfiles | None
) -> tuple[list[ClientProfile], _SearchClientProfiles]:
    # The caller has validated detached JSON inputs. Preserve object key order
    # and JSON scalar types in each fingerprint.
    fingerprints = tuple(
        hashlib.sha256(
            json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        ).digest()
        for row in rows
    )
    previous = {}
    if cached is not None:
        old_fingerprints, complete_profiles, previous = cached
        if complete_profiles is not None and old_fingerprints == fingerprints:
            return complete_profiles, cached

    known = {
        row["id"]: previous[fingerprint]
        for row, fingerprint in zip(rows, fingerprints, strict=True)
        if fingerprint in previous
    }
    clients = _search_clients(rows, known_clients=known)
    by_id = {client.id: client for client in clients}
    stable = {}
    # Missing dates and vehicle IDs can be generated during legacy reads.
    for row, fingerprint in zip(rows, fingerprints, strict=True):
        if fingerprint in previous:
            stable[fingerprint] = previous[fingerprint]
            continue
        if any(
            not isinstance(row.get(key), str) or not row[key].strip()
            for key in ("created_at", "updated_at")
        ):
            continue
        vehicles = row.get("vehicles", [])
        if not isinstance(vehicles, list) or any(
            not isinstance(vehicle, dict)
            or not isinstance(vehicle.get("id"), str)
            or not vehicle["id"].strip()
            for vehicle in vehicles
        ):
            continue
        stable[fingerprint] = by_id[row["id"]]
    complete = clients if len(stable) == len(rows) else None
    return clients, (fingerprints, complete, stable)


def _search_cards_before_retention(raw_cards, columns):
    """Build a complete detached view to validate colliding identities."""
    valid_columns = {column.id for column in columns}
    try:
        return [
            Card.from_dict(
                item,
                valid_columns=valid_columns,
                default_column=columns[0].id,
                fallback_position=index,
            )
            for index, item in enumerate(raw_cards)
        ]
    except (OverflowError, TypeError, ValueError):
        raise StateFileCorruptedError("Исходная запись карточки повреждена.") from None


class ReadonlyContextSnapshot(dict):
    def __init__(
        self,
        bundle,
        state,
        *,
        source_indexes=None,
        cards_index=None,
        search_cards_need_retention=False,
        profile_clients=None,
    ):
        super().__init__(bundle)
        self.search_cards_need_retention = search_cards_need_retention
        self._raw_events = state.get("events", [])
        if source_indexes is None:
            self._cards = cards_index if cards_index is not None else _index(state.get("cards"))
            self._clients = _index(state.get("clients"))
            self._events = _index(state.get("events"))
        else:
            if cards_index is not None:
                raise ValueError("Conflicting source indexes")
            self._cards, self._clients, self._events = source_indexes
        self._link_clients = {client.id: client for client in bundle["clients"]}
        self._profile_clients = profile_clients
        # Prepared searches retain models and card relations from this snapshot.
        self.client_search_indexes = {}
        self.client_search_lock = Lock()

    def normalized_events(self, store):
        if not isinstance(self._raw_events, list):
            raise StateFileCorruptedError("Список событий отсутствует или поврежден.")
        return store._normalize_events({"events": deepcopy(self._raw_events)})[0]

    def profile_client_candidates(self, store, requested_id):
        if self._profile_clients is None:
            return self["clients"]
        requested = normalize_text(requested_id, default="")
        if not requested or len(requested) > 128:
            return []
        exact = self._clients.get(requested, [])
        if exact:
            clients, _ = store._normalize_clients({"clients": exact}, compare_storage=False)
            if clients:
                return clients
        key = requested.upper()
        candidates = [
            row
            for row in self._profile_clients
            if row["id"].upper() == key or short_entity_id(row["id"], prefix="CL").upper() == key
        ]
        return store._normalize_clients({"clients": candidates}, compare_storage=False)[0]

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

    def require_card_identity(self, card):
        return self._source(self._cards, card.id)

    def require_card(self, card, *, include_attachments=False):
        source = self.require_card_identity(card)
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

    def require_client_identity(self, client):
        return self._source(self._clients, client.id)

    def require_client_sort_key(self, client):
        source = self.require_client_identity(client)
        created_at = source.get("created_at")
        if not isinstance(created_at, str) or not created_at or created_at != client.created_at:
            raise StateFileCorruptedError("Недостоверная версия клиента в снимке состояния.")
        return source

    def require_client_version(self, client):
        source = self.require_client_sort_key(client)
        updated_at = source.get("updated_at")
        if not isinstance(updated_at, str) or not updated_at or updated_at != client.updated_at:
            raise StateFileCorruptedError("Недостоверная версия клиента в снимке состояния.")
        return source

    def require_client(self, client):
        source = self.require_client_version(client)
        raw_vehicles = source.get("vehicles", [])
        vehicles = _index(
            raw_vehicles if isinstance(raw_vehicles, list) else [raw_vehicles],
            alias="client_vehicle_id",
        )
        for vehicle in client.vehicles:
            self._source(vehicles, vehicle.id, alias="client_vehicle_id")

    def has_other_exact_client(self, client_id, selected_id):
        """Recognize only an exact, validated stored owner, never a short alias."""
        if not client_id or client_id == selected_id or client_id not in self._clients:
            return False
        source = self._source(self._clients, client_id)
        try:
            client = ClientProfile.from_dict(source)
        except (OverflowError, TypeError, ValueError):
            raise StateFileCorruptedError("Запись связанного клиента повреждена.") from None
        self.require_client(client)
        return client.id == client_id

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

    def card_link_warnings(self, card):
        """Allow viewing saved card fields when its former vehicle link is absent.

        The saved IDs remain untouched. A missing target conveys no linked vehicle
        data; ambiguous or normalized identities still fail the strict checks.
        Context and transaction reads continue to use require_card_client_links.
        """
        if card.client_id and card.client_vehicle_id:
            source = self._source(self._clients, card.client_id)
            raw = source.get("vehicles", [])
            vehicles = _index(raw if isinstance(raw, list) else [raw], alias="client_vehicle_id")
            if card.client_vehicle_id not in vehicles:
                for rows in self._clients.values():
                    for other in rows:
                        raw = other.get("vehicles", [])
                        candidates = _index(
                            raw if isinstance(raw, list) else [raw], alias="client_vehicle_id"
                        )
                        if card.client_vehicle_id in candidates:
                            raise StateFileCorruptedError(
                                "Связь автомобиля принадлежит другому клиенту."
                            )
                return ["client_vehicle_link_unavailable"]
        self.require_card_client_links(card)
        return []

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
        for child in values:
            child_type = type(child)
            if child_type is dict or child_type is list:
                pending.append((child, depth + 1))


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
    """Detached context from a verified local write, indexed for one-card reads."""

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
        self.context_state = {
            name: state[name]
            for name in ("columns", "cards", "clients", "events", "settings", "stickies")
            if name in state
        }
        self._contexts = {}
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

    def for_context(self, store, domains):
        """Reuse context projections while the caller retains this verified generation."""
        snapshot = self._contexts.get(domains)
        if snapshot is None:
            snapshot = build_context_snapshot(
                store,
                self.context_state,
                domains,
                read_search_clients=store._read_search_client_profiles,
            )
            self._contexts[domains] = snapshot
        return snapshot

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


def read_operator_audit_events(store) -> list[AuditEvent]:
    """Read the current audit stream for operator statistics."""
    if sys.platform == "win32":
        return store.read_bundle()["events"]
    with store._lock:
        bundle = store._read_cache_bundle
        if bundle is not None and store._current_read_bundle_cache_signature() is not None:
            return bundle["events"]
    try:
        events = store._read_context_bundle_snapshot(domains=("operator_audit_events",))["events"]
    except (FileNotFoundError, StateFileCorruptedError):
        events = None
    # Preserve the full reader's recovery and persistence for legacy events.
    return events if events is not None else store.read_bundle()["events"]


def read_posix_committed_card_snapshot(store, card_id):
    cached = store._committed_card_source
    if cached is None:
        return None
    # Keep the detached generation pinned in memory during an overlapping write.
    if cached[0] == store._repair_order_file_signature():
        snapshot = cached[2].for_card(store, card_id)
        if cached[0] == store._repair_order_file_signature():
            return snapshot
    with store._lock:
        if store._committed_card_source is cached:
            store._committed_card_source = None
    return None


def build_context_snapshot(store, state, domains, *, read_search_clients=None):
    if domains == ("operator_audit_events",):
        try:
            validate_state_shape(state)
        except ValueError:
            return {"events": None}
        _validate_raw_financial_references(state)
        _validate_cash_transaction_ids(state)
        events, repaired = store._normalize_events(state)
        return {"events": None if repaired else events}
    needs_cards = "client_search" not in domains or "client_search_cards" in domains
    raw_cards = state.get("cards") if needs_cards else None
    if needs_cards and (
        not isinstance(raw_cards, list) or any(not isinstance(card, dict) for card in raw_cards)
    ):
        raise StateFileCorruptedError("Список карточек отсутствует или поврежден.")
    for name in ("clients", "stickies", "events"):
        if name not in domains and not (name == "clients" and "client_profile" in domains):
            continue
        if name in state and not isinstance(state[name], list):
            raise StateFileCorruptedError("Снимок состояния содержит поврежденный список.")
    raw_clients = state.get("clients", [])
    cards_index = None if needs_cards else {}
    search_cards_need_retention = False
    clients = []
    profile_clients = None
    if "client_search" in domains:
        _require_raw_search_identities(raw_clients, unique=True)
        # Deep or unusual input retains the original deepcopy and its failures.
        if _safe_to_detach_shallow_json(raw_clients):
            clients = (read_search_clients or _search_clients)(raw_clients)
        else:
            clients = _search_clients(deepcopy(raw_clients))
    if "client_search_cards" in domains:
        _require_raw_search_identities(raw_cards, unique=False)
        # Capture collisions from the unnormalized source, including entries that
        # card retention may omit from the normalized snapshot.
        cards_index = _index(raw_cards)
    if needs_cards:
        columns, _ = store._normalize_columns(state)
        if cards_index is not None and any(len(rows) > 1 for rows in cards_index.values()):
            # Check candidate relations against the complete source before retention
            # can remove one side of a collision.
            cards = _search_cards_before_retention(deepcopy(raw_cards), columns)
            search_cards_need_retention = True
        elif _safe_to_detach_shallow_json(raw_cards):
            # Detach nested order history without serializing cards for a repair check.
            cards, _ = store._normalize_cards(
                {"cards": raw_cards},
                columns,
                detach_order_history=True,
                compare_storage=False,
            )
        else:
            cards, _ = store._normalize_cards(
                {"cards": deepcopy(raw_cards)}, columns, compare_storage=False
            )
    else:
        columns, cards = [], []
    if "clients" in domains and "client_search" not in domains:
        clients = store._normalize_clients({"clients": raw_clients}, compare_storage=False)[0]
    if "client_profile" in domains:
        seen = set()
        for row in raw_clients:
            raw_id = row.get("id") if isinstance(row, dict) else None
            if (
                not isinstance(raw_id, str)
                or not raw_id
                or raw_id != normalize_text(raw_id, limit=128)
                or raw_id in seen
            ):
                clients = store._normalize_clients({"clients": raw_clients}, compare_storage=False)[
                    0
                ]
                break
            seen.add(raw_id)
        else:
            profile_clients = raw_clients
    stickies = store._normalize_stickies(state)[0] if "stickies" in domains else []
    events = (
        store._normalize_events({"events": deepcopy(state.get("events", []))})[0]
        if "events" in domains
        else []
    )
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
        cards_index=cards_index,
        search_cards_need_retention=search_cards_need_retention,
        profile_clients=profile_clients,
    )
