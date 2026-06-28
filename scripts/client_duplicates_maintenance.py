from __future__ import annotations
# ruff: noqa: E402,I001

import argparse
import json
import logging
import math
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.config import get_state_file
from minimal_kanban.models import (
    AuditEvent,
    Card,
    ClientProfile,
    normalize_client_vehicles,
    utc_now_iso,
)
from minimal_kanban.storage.json_store import JsonStore
from minimal_kanban.storage.limited_io import copy_file_limited

STATE_FILE_MAX_BYTES = 100 * 1024 * 1024


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _json_safe_value(value: Any, *, depth: int = 8) -> Any:
    if depth <= 0:
        return str(value)
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item, depth=depth - 1) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    return str(value)


def _json_dumps(payload: Any) -> str:
    return json.dumps(
        _json_safe_value(payload),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )


def _text(value: object) -> str:
    return str(value or "").strip()


def _read_state_text(state_file: Path) -> str:
    with state_file.open("rb") as handle:
        raw = handle.read(STATE_FILE_MAX_BYTES + 1)
    if len(raw) > STATE_FILE_MAX_BYTES:
        raise ValueError("client duplicates state file is too large")
    return raw.decode("utf-8")


def _read_state_for_plan(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return {"clients": [], "cards": []}
    try:
        state = json.loads(
            _read_state_text(state_file),
            parse_constant=_reject_json_constant,
        )
    except RecursionError as exc:
        raise ValueError("client duplicates state file JSON is too deeply nested") from exc
    if not isinstance(state, dict):
        raise ValueError("state file must contain a JSON object")
    return state


def _clients_from_state(state: dict[str, Any]) -> list[ClientProfile]:
    raw_clients = state.get("clients") if isinstance(state.get("clients"), list) else []
    clients: list[ClientProfile] = []
    for item in raw_clients:
        try:
            clients.append(ClientProfile.from_dict(item))
        except (OverflowError, TypeError, ValueError):
            continue
    return clients


def _cards_from_state(state: dict[str, Any]) -> list[Card]:
    raw_cards = state.get("cards") if isinstance(state.get("cards"), list) else []
    cards: list[Card] = []
    for index, item in enumerate(raw_cards):
        try:
            cards.append(Card.from_dict(item, fallback_position=index))
        except (OverflowError, TypeError, ValueError):
            continue
    return cards


def _normalize_search_text(value: object) -> str:
    text = _text(value).casefold()
    text = re.sub(r"[\s_./\\|,;:()\\[\\]{}<>\"'`~!@#$%^&*+=?]+", " ", text)
    return " ".join(text.split())


def _phone_key(value: object) -> str:
    digits = re.sub(r"\D+", "", _text(value))
    if len(digits) < 7:
        return ""
    if len(digits) >= 10:
        return "7" + digits[-10:]
    return digits


def _client_name_key(client: ClientProfile) -> str:
    name = client.name()
    if name in {"Без имени", "Без названия"}:
        return ""
    return _normalize_search_text(name)


def _client_phone_keys(client: ClientProfile) -> set[str]:
    keys: set[str] = set()
    for phone in [client.phone, *list(client.phones or [])]:
        key = _phone_key(phone)
        if key:
            keys.add(key)
    return keys


def _client_phone_like_name_key(client: ClientProfile) -> str:
    return _phone_key(client.name())


def _client_name_quality(client: ClientProfile) -> int:
    name_key = _client_name_key(client)
    if not name_key:
        return 0
    return 0 if _client_phone_like_name_key(client) else 1


def _vehicle_key(vehicle: Any) -> str:
    return "|".join(
        part.casefold()
        for part in (
            _text(getattr(vehicle, "vehicle", "")),
            _text(getattr(vehicle, "brand", "")),
            _text(getattr(vehicle, "model", "")),
            _text(getattr(vehicle, "vin", "")),
            _text(getattr(vehicle, "license_plate", "")),
            _text(getattr(vehicle, "year", "")),
        )
        if part
    )


def _vehicle_merge_identity(vehicle: Any) -> str:
    vin = re.sub(r"[^A-Z0-9]+", "", _text(getattr(vehicle, "vin", "")).upper())
    if len(vin) >= 6:
        return f"vin:{vin}"
    plate = _normalize_search_text(getattr(vehicle, "license_plate", ""))
    if plate:
        return f"plate:{plate}"
    return _vehicle_key(vehicle)


def _mergeable_duplicate_vehicle_payloads(
    canonical: ClientProfile, duplicate_clients: list[ClientProfile]
) -> list[dict[str, Any]]:
    seen = {
        identity
        for identity in (_vehicle_merge_identity(vehicle) for vehicle in canonical.vehicles)
        if identity
    }
    payloads: list[dict[str, Any]] = []
    for client in duplicate_clients:
        for vehicle in client.vehicles:
            identity = _vehicle_merge_identity(vehicle)
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            payloads.append(vehicle.to_dict())
    return payloads


def _card_sort_key(card: Any) -> tuple[str, str, str]:
    repair_order = getattr(card, "repair_order", None)
    return (
        _text(getattr(repair_order, "closed_at", "")),
        _text(getattr(card, "updated_at", "")),
        _text(getattr(card, "id", "")),
    )


def _client_snapshot(client: ClientProfile, linked_cards: list[Any]) -> dict[str, Any]:
    return {
        "id": client.id,
        "name": client.name(),
        "phones": list(client.phones),
        "vehicles": [
            {
                "id": vehicle.id,
                "vehicle": vehicle.vehicle,
                "vin": vehicle.vin,
                "license_plate": vehicle.license_plate,
                "year": vehicle.year,
            }
            for vehicle in client.vehicles
        ],
        "linked_card_ids": [card.id for card in linked_cards],
        "created_at": client.created_at,
        "updated_at": client.updated_at,
    }


def _choose_canonical_client(
    clients: list[ClientProfile], linked_cards_by_client: dict[str, list[Any]]
) -> ClientProfile:
    return max(
        clients,
        key=lambda client: (
            _client_name_quality(client),
            len(linked_cards_by_client.get(client.id, [])),
            len(client.vehicles),
            len(client.phones),
            client.updated_at,
            client.created_at,
            client.id,
        ),
    )


def _build_client_duplicate_indexes(
    clients: list[ClientProfile],
) -> tuple[
    dict[str, dict[str, ClientProfile]],
    dict[str, dict[str, set[str]]],
    dict[str, dict[str, ClientProfile]],
    dict[str, dict[str, ClientProfile]],
]:
    clients_by_name: dict[str, dict[str, ClientProfile]] = defaultdict(dict)
    phone_clients_by_name: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    phone_name_clients_by_key: dict[str, dict[str, ClientProfile]] = defaultdict(dict)
    actual_phone_clients_by_key: dict[str, dict[str, ClientProfile]] = defaultdict(dict)
    for client in clients:
        name_key = _client_name_key(client)
        phone_keys = _client_phone_keys(client)
        for phone_key in phone_keys:
            actual_phone_clients_by_key[phone_key][client.id] = client
        phone_name_key = _client_phone_like_name_key(client)
        if phone_name_key and not phone_keys:
            phone_name_clients_by_key[phone_name_key][client.id] = client
        if not name_key:
            continue
        for phone_key in phone_keys:
            clients_by_name[name_key][client.id] = client
            phone_clients_by_name[name_key][phone_key].add(client.id)
    return (
        clients_by_name,
        phone_clients_by_name,
        phone_name_clients_by_key,
        actual_phone_clients_by_key,
    )


def _append_duplicate_group(
    groups: list[dict[str, Any]],
    seen_group_client_ids: set[frozenset[str]],
    *,
    linked_cards_by_client: dict[str, list[Any]],
    name_key: str,
    phone_keys: list[str],
    unique_clients: list[ClientProfile],
) -> None:
    group_ids = frozenset(client.id for client in unique_clients)
    if len(group_ids) < 2 or group_ids in seen_group_client_ids or not phone_keys:
        return
    seen_group_client_ids.add(group_ids)
    canonical = _choose_canonical_client(unique_clients, linked_cards_by_client)
    duplicate_clients = [client for client in unique_clients if client.id != canonical.id]
    cards_to_relink = [
        card for client in duplicate_clients for card in linked_cards_by_client.get(client.id, [])
    ]
    groups.append(
        {
            "name_key": name_key,
            "phone_key": phone_keys[0],
            "phone_keys": phone_keys,
            "canonical_id": canonical.id,
            "duplicate_ids": [client.id for client in duplicate_clients],
            "clients": [
                _client_snapshot(client, linked_cards_by_client.get(client.id, []))
                for client in unique_clients
            ],
            "cards_to_relink": [card.id for card in cards_to_relink],
            "vehicles_to_merge": len(
                _mergeable_duplicate_vehicle_payloads(canonical, duplicate_clients)
            ),
        }
    )


def _duplicate_group_client_ids_for_name_key(
    clients_map: dict[str, ClientProfile],
    phone_clients: dict[str, set[str]],
) -> list[list[str]]:
    parent = {client_id: client_id for client_id in clients_map}

    def find(client_id: str) -> str:
        while parent[client_id] != client_id:
            parent[client_id] = parent[parent[client_id]]
            client_id = parent[client_id]
        return client_id

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for linked_ids in phone_clients.values():
        linked = sorted(linked_ids)
        if len(linked) < 2:
            continue
        first_id = linked[0]
        for client_id in linked[1:]:
            union(first_id, client_id)

    component_ids: dict[str, list[str]] = defaultdict(list)
    for client_id in sorted(clients_map):
        component_ids[find(client_id)].append(client_id)

    return [
        group_client_ids
        for group_client_ids in component_ids.values()
        if len(group_client_ids) >= 2
    ]


def build_client_duplicate_plan(state_file: Path | None = None) -> dict[str, Any]:
    state_file = state_file or get_state_file()
    state = _read_state_for_plan(state_file)
    clients = _clients_from_state(state)
    cards = _cards_from_state(state)
    linked_cards_by_client: dict[str, list[Any]] = defaultdict(list)
    for card in cards:
        if getattr(card, "client_id", ""):
            linked_cards_by_client[card.client_id].append(card)
    for linked_cards in linked_cards_by_client.values():
        linked_cards.sort(key=_card_sort_key, reverse=True)

    groups: list[dict[str, Any]] = []
    seen_group_client_ids: set[frozenset[str]] = set()
    (
        clients_by_name,
        phone_clients_by_name,
        phone_name_clients_by_key,
        actual_phone_clients_by_key,
    ) = _build_client_duplicate_indexes(clients)

    for name_key, clients_map in sorted(clients_by_name.items()):
        phone_clients = phone_clients_by_name[name_key]
        for group_client_ids in _duplicate_group_client_ids_for_name_key(
            clients_map, phone_clients
        ):
            group_client_id_set = set(group_client_ids)
            shared_phone_keys = sorted(
                phone_key
                for phone_key, linked_ids in phone_clients.items()
                if len(group_client_id_set.intersection(linked_ids)) > 1
            )
            if not shared_phone_keys:
                continue
            unique_clients = [clients_map[client_id] for client_id in group_client_ids]
            _append_duplicate_group(
                groups,
                seen_group_client_ids,
                linked_cards_by_client=linked_cards_by_client,
                name_key=name_key,
                phone_keys=shared_phone_keys,
                unique_clients=unique_clients,
            )

    for phone_key, phone_name_clients in sorted(phone_name_clients_by_key.items()):
        clients_map = dict(phone_name_clients)
        clients_map.update(actual_phone_clients_by_key.get(phone_key, {}))
        has_real_phone_profile = bool(actual_phone_clients_by_key.get(phone_key))
        if len(clients_map) < 2 or not has_real_phone_profile:
            continue
        _append_duplicate_group(
            groups,
            seen_group_client_ids,
            linked_cards_by_client=linked_cards_by_client,
            name_key=f"phone-only-name:{phone_key}",
            phone_keys=[phone_key],
            unique_clients=list(clients_map.values()),
        )

    return {
        "schema": "client_duplicates_maintenance.v1",
        "read_only": True,
        "state_file": str((state_file or get_state_file()).resolve()),
        "summary": {
            "groups_total": len(groups),
            "clients_to_remove": sum(len(group["duplicate_ids"]) for group in groups),
            "cards_to_relink": sum(len(group["cards_to_relink"]) for group in groups),
            "vehicles_to_merge": sum(int(group["vehicles_to_merge"]) for group in groups),
        },
        "groups": groups,
    }


def _merge_phone_lists(canonical: ClientProfile, duplicate_clients: list[ClientProfile]) -> None:
    values = [canonical.phone, *list(canonical.phones or [])]
    for client in duplicate_clients:
        values.extend([client.phone, *list(client.phones or [])])
    for client in [canonical, *duplicate_clients]:
        if _client_phone_like_name_key(client):
            values.append(client.name())
    canonical.phones = []
    canonical.phone = ""
    merged = ClientProfile.from_dict({**canonical.to_storage_dict(), "phone": "", "phones": values})
    canonical.phone = merged.phone
    canonical.phones = merged.phones


def _merge_client_fields(canonical: ClientProfile, duplicate_clients: list[ClientProfile]) -> None:
    _merge_phone_lists(canonical, duplicate_clients)
    canonical.vehicles = normalize_client_vehicles(
        [
            *[vehicle.to_dict() for vehicle in canonical.vehicles],
            *_mergeable_duplicate_vehicle_payloads(canonical, duplicate_clients),
        ]
    )
    deleted_keys = list(canonical.deleted_vehicle_keys or [])
    for client in duplicate_clients:
        for key in client.deleted_vehicle_keys or []:
            if key and key not in deleted_keys:
                deleted_keys.append(key)
        if not canonical.email and client.email:
            canonical.email = client.email
        if not canonical.comment and client.comment:
            canonical.comment = client.comment
    canonical.deleted_vehicle_keys = deleted_keys
    canonical.updated_at = utc_now_iso()


def _backup_state_file(state_file: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_file = state_file.with_name(f"{state_file.name}.backup-client-duplicates-{timestamp}")
    counter = 2
    while backup_file.exists():
        backup_file = state_file.with_name(
            f"{state_file.name}.backup-client-duplicates-{timestamp}-{counter:03d}"
        )
        counter += 1
    copy_file_limited(
        state_file,
        backup_file,
        max_bytes=STATE_FILE_MAX_BYTES,
        label="client duplicates state file",
    )
    return backup_file


def _apply_client_duplicate_group(
    group: dict[str, Any],
    *,
    clients_by_id: dict[str, ClientProfile],
    cards: list[Any],
) -> tuple[set[str], int, AuditEvent | None]:
    canonical = clients_by_id.get(group["canonical_id"])
    if canonical is None:
        return set(), 0, None
    duplicate_clients = [
        clients_by_id[client_id]
        for client_id in group["duplicate_ids"]
        if client_id in clients_by_id
    ]
    old_vehicle_id_to_key: dict[str, str] = {}
    for client in [canonical, *duplicate_clients]:
        for vehicle in client.vehicles:
            key = _vehicle_key(vehicle)
            if key:
                old_vehicle_id_to_key[vehicle.id] = key
    _merge_client_fields(canonical, duplicate_clients)
    canonical_vehicle_id_by_key = {
        _vehicle_key(vehicle): vehicle.id for vehicle in canonical.vehicles if _vehicle_key(vehicle)
    }
    duplicate_ids = {client.id for client in duplicate_clients}
    cards_relinked = 0
    for card in cards:
        if card.client_id not in duplicate_ids:
            continue
        card.client_id = canonical.id
        if card.client_vehicle_id:
            vehicle_key = old_vehicle_id_to_key.get(card.client_vehicle_id, "")
            card.client_vehicle_id = canonical_vehicle_id_by_key.get(
                vehicle_key, card.client_vehicle_id
            )
        cards_relinked += 1
    event = AuditEvent(
        id=f"client-merge-{datetime.now(UTC).timestamp()}-{canonical.id}",
        timestamp=utc_now_iso(),
        actor_name="System",
        source="system",
        action="client_duplicates_merged",
        message="System объединил точные дубли клиентов",
        card_id=None,
        details={
            "canonical_client_id": canonical.id,
            "removed_client_ids": sorted(duplicate_ids),
            "phone_key": group["phone_key"],
            "name_key": group["name_key"],
            "cards_relinked": list(group["cards_to_relink"]),
        },
    )
    return duplicate_ids, cards_relinked, event


def apply_client_duplicate_plan(
    state_file: Path | None = None,
    *,
    backup: bool = False,
) -> dict[str, Any]:
    state_file = state_file or get_state_file()
    if not backup:
        raise ValueError("apply requires backup=True")
    plan = build_client_duplicate_plan(state_file)
    if not plan["groups"]:
        return {**plan, "read_only": False, "applied": False, "backup_file": ""}

    backup_file = _backup_state_file(state_file)

    store = JsonStore(state_file=state_file, logger=logging.getLogger(__name__))
    bundle = store.read_bundle()
    clients: list[ClientProfile] = list(bundle["clients"])
    cards = bundle["cards"]
    events = list(bundle["events"])
    clients_by_id = {client.id: client for client in clients}
    removed_ids: set[str] = set()
    cards_relinked = 0

    for group in plan["groups"]:
        duplicate_ids, relinked_count, event = _apply_client_duplicate_group(
            group,
            clients_by_id=clients_by_id,
            cards=cards,
        )
        if not duplicate_ids:
            continue
        removed_ids.update(duplicate_ids)
        cards_relinked += relinked_count
        if event is not None:
            events.append(event)

    next_clients = [client for client in clients if client.id not in removed_ids]
    store.write_bundle(
        columns=bundle["columns"],
        cards=cards,
        clients=next_clients,
        stickies=bundle["stickies"],
        cashboxes=bundle["cashboxes"],
        cash_transactions=bundle["cash_transactions"],
        events=events,
        settings=bundle["settings"],
    )
    return {
        **plan,
        "read_only": False,
        "applied": True,
        "backup_file": str(backup_file),
        "summary": {
            **plan["summary"],
            "clients_removed": len(removed_ids),
            "cards_relinked": cards_relinked,
        },
    }


def _format_text(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "AutoStop CRM client duplicates maintenance",
        f"schema: {result['schema']}",
        f"read_only: {result['read_only']}",
        f"groups_total: {summary.get('groups_total', 0)}",
        f"clients_to_remove: {summary.get('clients_to_remove', 0)}",
        f"cards_to_relink: {summary.get('cards_to_relink', 0)}",
    ]
    if result.get("backup_file"):
        lines.append(f"backup_file: {result['backup_file']}")
    for group in result["groups"][:20]:
        lines.append(
            "- "
            f"phone={group['phone_key']} canonical={group['canonical_id']} "
            f"remove={','.join(group['duplicate_ids'])} "
            f"cards={','.join(group['cards_to_relink']) or '-'}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit or merge exact duplicate clients.")
    parser.add_argument("--state-file", type=Path, default=get_state_file())
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", action="store_true", help="Required with --apply.")
    parser.add_argument("--format", choices={"text", "json"}, default="text")
    args = parser.parse_args(argv)

    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
    try:
        result = (
            apply_client_duplicate_plan(args.state_file, backup=args.backup)
            if args.apply
            else build_client_duplicate_plan(args.state_file)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        if args.format == "json":
            print(_json_dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"error: {exc}")
        return 2
    if args.format == "json":
        print(_json_dumps(result))
    else:
        print(_format_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
