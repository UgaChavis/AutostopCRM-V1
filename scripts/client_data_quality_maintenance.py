from __future__ import annotations
# ruff: noqa: E402,I001

import argparse
import json
import math
import re
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.config import get_state_file
from minimal_kanban.models import AuditEvent, ClientProfile, utc_now_iso
from minimal_kanban.storage.file_lock import ProcessFileLock
from minimal_kanban.storage.json_store import DEFAULT_STATE
from minimal_kanban.storage.limited_io import copy_file_limited

STATE_FILE_MAX_BYTES = 100 * 1024 * 1024

_VIN_PLACEHOLDER_KEYS = {
    "ABSENT",
    "NA",
    "NAN",
    "NET",
    "NETDANNYH",
    "NO",
    "NONE",
    "NOVIN",
    "UNKNOWN",
}
_SAFE_FIX_REASONS = {"empty_compact", "placeholder", "repeated_character"}


def _text(value: object) -> str:
    return str(value or "").strip()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _json_safe_value(value: Any, *, depth: int = 8) -> Any:
    if depth <= 0:
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item, depth=depth - 1)
            for key, item in value.items()
            if key is not None
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    return str(value)


def _normalize_search_text(value: object) -> str:
    text = _text(value).casefold()
    text = re.sub(r"[\s_./\\|,;:()\[\]{}<>\"'`~!@#$%^&*+=?]+", " ", text)
    return " ".join(text.split())


def _phone_key(value: object) -> str:
    digits = re.sub(r"\D+", "", _text(value))
    if len(digits) < 7:
        return ""
    if len(digits) >= 10:
        return "7" + digits[-10:]
    return digits


def _is_phone_like_text(value: object) -> bool:
    text = _text(value)
    digits = re.sub(r"\D+", "", text)
    letters = re.findall(r"[A-Za-zА-Яа-я]", text)
    return len(digits) >= 7 and not letters


def _compact_vin(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", _text(value).upper())


def invalid_client_vehicle_vin_reason(value: object) -> str:
    raw = _text(value).upper()
    if not raw:
        return ""
    compact = _compact_vin(raw)
    if compact in _VIN_PLACEHOLDER_KEYS:
        return "placeholder"
    if not compact:
        return "empty_compact"
    if len(set(compact)) <= 1:
        return "repeated_character"
    if len(compact) < 6:
        return "too_short"
    return ""


def _safe_fix_available(reason: str) -> bool:
    return reason in _SAFE_FIX_REASONS


def _bounded_issue_limit(value: object) -> int:
    if isinstance(value, bool):
        return 50
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return 50
    if not math.isfinite(numeric) or not numeric.is_integer():
        return 50
    if numeric < 0:
        return 0
    if numeric > 500:
        return 500
    return int(numeric)


def _read_state(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return deepcopy(DEFAULT_STATE)
    try:
        state = json.loads(
            _read_state_text(state_file),
            parse_constant=_reject_json_constant,
        )
    except RecursionError as exc:
        raise ValueError("client data quality state file JSON is too deeply nested") from exc
    if not isinstance(state, dict):
        raise ValueError("state file must contain a JSON object")
    return state


def _read_state_text(state_file: Path) -> str:
    with state_file.open("rb") as handle:
        raw = handle.read(STATE_FILE_MAX_BYTES + 1)
    if len(raw) > STATE_FILE_MAX_BYTES:
        raise ValueError("client data quality state file is too large")
    return raw.decode("utf-8")


def _write_state(state_file: Path, state: dict[str, Any]) -> None:
    payload = json.dumps(
        _json_safe_value(state),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(payload.encode("utf-8")) > STATE_FILE_MAX_BYTES:
        raise ValueError("client data quality state file is too large")
    temp_file = state_file.with_name(f".{state_file.name}.{uuid4().hex}.tmp")
    try:
        temp_file.write_text(payload, encoding="utf-8")
        temp_file.replace(state_file)
    finally:
        temp_file.unlink(missing_ok=True)


def _backup_state_file(state_file: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_file = state_file.with_name(f"{state_file.name}.backup-client-data-quality-{timestamp}")
    counter = 2
    while backup_file.exists():
        backup_file = state_file.with_name(
            f"{state_file.name}.backup-client-data-quality-{timestamp}-{counter:03d}"
        )
        counter += 1
    copy_file_limited(
        state_file,
        backup_file,
        max_bytes=STATE_FILE_MAX_BYTES,
        label="client data quality state file",
    )
    return backup_file


def _client_name(raw_client: dict[str, Any]) -> str:
    try:
        return ClientProfile.from_dict(raw_client).name()
    except (OverflowError, TypeError, ValueError):
        return _text(raw_client.get("display_name") or raw_client.get("last_name")) or "Без имени"


def _client_full_name(raw_client: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            _text(raw_client.get("last_name")),
            _text(raw_client.get("first_name")),
            _text(raw_client.get("middle_name")),
        )
        if part
    ).strip()


def _client_phone_keys(raw_client: dict[str, Any]) -> set[str]:
    values = [raw_client.get("phone")]
    phones = raw_client.get("phones")
    if isinstance(phones, list):
        values.extend(phones)
    return {key for key in (_phone_key(value) for value in values) if key}


def _card_phone_keys(raw_card: dict[str, Any]) -> set[str]:
    values: list[object] = []
    profile = raw_card.get("vehicle_profile")
    if isinstance(profile, dict):
        values.append(profile.get("customer_phone"))
        phones = profile.get("customer_phones")
        if isinstance(phones, list):
            values.extend(phones)
    repair_order = raw_card.get("repair_order")
    if isinstance(repair_order, dict):
        values.append(repair_order.get("phone"))
    return {key for key in (_phone_key(value) for value in values) if key}


def _card_client_name_candidates(raw_card: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    profile = raw_card.get("vehicle_profile")
    if isinstance(profile, dict):
        candidates.append(_text(profile.get("customer_name")))
    repair_order = raw_card.get("repair_order")
    if isinstance(repair_order, dict):
        candidates.append(_text(repair_order.get("client")))
    normalized: dict[str, str] = {}
    for candidate in candidates:
        if not candidate or _is_phone_like_text(candidate):
            continue
        key = _normalize_search_text(candidate)
        if not key or key in {"нет данных", "нет", "клиент"}:
            continue
        normalized.setdefault(key, candidate)
    return list(normalized.values())


def _candidate_client_names_for_phone_like_client(
    raw_client: dict[str, Any], raw_cards: list[Any]
) -> tuple[list[str], list[str]]:
    client_id = _text(raw_client.get("id"))
    phone_keys = _client_phone_keys(raw_client)
    names: dict[str, str] = {}
    related_card_ids: list[str] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        card_id = _text(raw_card.get("id"))
        is_related = bool(client_id and _text(raw_card.get("client_id")) == client_id)
        if not is_related and phone_keys:
            is_related = bool(phone_keys.intersection(_card_phone_keys(raw_card)))
        if not is_related:
            continue
        for candidate in _card_client_name_candidates(raw_card):
            names.setdefault(_normalize_search_text(candidate), candidate)
        if card_id:
            related_card_ids.append(card_id)
    return list(names.values()), related_card_ids


def _vehicle_label(raw_vehicle: dict[str, Any]) -> str:
    parts = [
        _text(raw_vehicle.get("vehicle") or raw_vehicle.get("name") or raw_vehicle.get("title")),
        _text(raw_vehicle.get("brand") or raw_vehicle.get("make")),
        _text(raw_vehicle.get("model")),
        _text(raw_vehicle.get("license_plate") or raw_vehicle.get("plate")),
    ]
    return " ".join(part for part in parts if part).strip()


def _build_plan_from_state(state: dict[str, Any], state_file: Path) -> dict[str, Any]:
    raw_clients = state.get("clients") if isinstance(state.get("clients"), list) else []
    raw_cards = state.get("cards") if isinstance(state.get("cards"), list) else []
    operations: list[dict[str, Any]] = []
    clients_affected: set[str] = set()
    vehicles_affected: set[str] = set()
    for client_index, raw_client in enumerate(raw_clients):
        if not isinstance(raw_client, dict):
            continue
        client_id = _text(raw_client.get("id")) or f"client-index-{client_index}"
        vehicles = raw_client.get("vehicles")
        if not isinstance(vehicles, list):
            continue
        for vehicle_index, raw_vehicle in enumerate(vehicles):
            if not isinstance(raw_vehicle, dict):
                continue
            previous_vin = _text(raw_vehicle.get("vin"))
            reason = invalid_client_vehicle_vin_reason(previous_vin)
            if not reason:
                continue
            vehicle_id = _text(raw_vehicle.get("id")) or f"vehicle-index-{vehicle_index}"
            clients_affected.add(client_id)
            vehicles_affected.add(f"{client_id}:{vehicle_id}:{vehicle_index}")
            operations.append(
                {
                    "kind": "clear_invalid_vehicle_vin",
                    "client_index": client_index,
                    "vehicle_index": vehicle_index,
                    "client_id": client_id,
                    "client_name": _client_name(raw_client),
                    "vehicle_id": vehicle_id,
                    "vehicle_label": _vehicle_label(raw_vehicle),
                    "previous_vin": previous_vin,
                    "reason": reason,
                    "safe_fix_available": _safe_fix_available(reason),
                }
            )
        client_name = _client_name(raw_client)
        if _is_phone_like_text(client_name):
            candidate_names, related_card_ids = _candidate_client_names_for_phone_like_client(
                raw_client, raw_cards
            )
            safe_fix_available = len(candidate_names) == 1
            operations.append(
                {
                    "kind": "replace_phone_like_client_name",
                    "client_index": client_index,
                    "client_id": client_id,
                    "client_name": client_name,
                    "previous_name": client_name,
                    "replacement_name": candidate_names[0] if safe_fix_available else "",
                    "candidate_names": candidate_names,
                    "related_card_ids": related_card_ids,
                    "reason": "phone_like_name",
                    "safe_fix_available": safe_fix_available,
                }
            )
            clients_affected.add(client_id)

    operations.sort(
        key=lambda item: (
            str(item["kind"]),
            str(item["reason"]),
            str(item["client_name"]).casefold(),
            str(item["client_id"]),
            str(item.get("vehicle_id", "")),
        )
    )
    safe_fix_count = sum(1 for operation in operations if operation["safe_fix_available"])
    invalid_vehicle_vins = sum(
        1 for operation in operations if operation["kind"] == "clear_invalid_vehicle_vin"
    )
    phone_like_client_names = sum(
        1 for operation in operations if operation["kind"] == "replace_phone_like_client_name"
    )
    return {
        "schema": "client_data_quality_maintenance.v1",
        "read_only": True,
        "state_file": str(state_file.resolve()),
        "summary": {
            "invalid_vehicle_vins": invalid_vehicle_vins,
            "phone_like_client_names": phone_like_client_names,
            "clients_affected": len(clients_affected),
            "vehicles_affected": len(vehicles_affected),
            "safe_fixes_available": safe_fix_count,
            "review_required": len(operations) - safe_fix_count,
        },
        "operations": operations,
    }


def build_client_data_quality_plan(state_file: Path | None = None) -> dict[str, Any]:
    state_file = state_file or get_state_file()
    lock = ProcessFileLock(state_file.with_suffix(".lock"))
    with lock.acquire():
        return _build_plan_from_state(_read_state(state_file), state_file)


def apply_client_data_quality_plan(
    state_file: Path | None = None,
    *,
    backup: bool = False,
) -> dict[str, Any]:
    state_file = state_file or get_state_file()
    if not backup:
        raise ValueError("apply requires backup=True")

    lock = ProcessFileLock(state_file.with_suffix(".lock"))
    with lock.acquire():
        state = _read_state(state_file)
        plan = _build_plan_from_state(state, state_file)
        operations_to_apply = [
            operation for operation in plan["operations"] if operation["safe_fix_available"]
        ]
        if not operations_to_apply:
            return {**plan, "read_only": False, "applied": False, "backup_file": ""}

        backup_file = _backup_state_file(state_file)

        raw_clients = state.get("clients") if isinstance(state.get("clients"), list) else []
        applied_operations: list[dict[str, Any]] = []
        touched_client_indexes: set[int] = set()
        for operation in operations_to_apply:
            kind = _text(operation.get("kind"))
            client_index = int(operation["client_index"])
            if kind == "replace_phone_like_client_name":
                try:
                    raw_client = raw_clients[client_index]
                except (IndexError, TypeError):
                    continue
                if not isinstance(raw_client, dict):
                    continue
                current_name = _client_name(raw_client)
                replacement_name = _text(operation.get("replacement_name"))
                if not replacement_name or not _is_phone_like_text(current_name):
                    continue
                if _is_phone_like_text(_client_full_name(raw_client)):
                    raw_client["last_name"] = ""
                    raw_client["first_name"] = ""
                    raw_client["middle_name"] = ""
                raw_client["display_name"] = replacement_name
                applied_operations.append(operation)
                touched_client_indexes.add(client_index)
                continue

            if kind != "clear_invalid_vehicle_vin":
                continue
            vehicle_index = int(operation["vehicle_index"])
            try:
                raw_vehicle = raw_clients[client_index]["vehicles"][vehicle_index]
            except (IndexError, KeyError, TypeError):
                continue
            if not isinstance(raw_vehicle, dict):
                continue
            reason = invalid_client_vehicle_vin_reason(raw_vehicle.get("vin"))
            if reason and _safe_fix_available(reason):
                raw_vehicle["vin"] = ""
                applied_operations.append(operation)
                touched_client_indexes.add(client_index)

        timestamp = utc_now_iso()
        for client_index in touched_client_indexes:
            raw_client = raw_clients[client_index]
            if isinstance(raw_client, dict):
                raw_client["updated_at"] = timestamp

        raw_events = state.get("events")
        if not isinstance(raw_events, list):
            raw_events = []
            state["events"] = raw_events
        raw_events.append(
            AuditEvent(
                id=f"client-data-quality-{datetime.now(UTC).timestamp()}",
                timestamp=timestamp,
                actor_name="System",
                source="system",
                action="client_vehicle_vin_placeholders_cleared",
                message="System применил безопасные правки качества клиентских данных",
                card_id=None,
                details={
                    "operations": [
                        {
                            "client_id": operation["client_id"],
                            "client_name": operation["client_name"],
                            "kind": operation["kind"],
                            "vehicle_id": operation.get("vehicle_id", ""),
                            "vehicle_label": operation.get("vehicle_label", ""),
                            "previous_vin": operation.get("previous_vin", ""),
                            "previous_name": operation.get("previous_name", ""),
                            "replacement_name": operation.get("replacement_name", ""),
                            "reason": operation["reason"],
                        }
                        for operation in applied_operations
                    ],
                },
            ).to_dict()
        )
        _write_state(state_file, state)
        return {
            **plan,
            "read_only": False,
            "applied": True,
            "backup_file": str(backup_file),
            "summary": {
                **plan["summary"],
                "applied_fixes": len(applied_operations),
            },
        }


def _format_text(result: dict[str, Any], *, issue_limit: int) -> str:
    summary = result["summary"]
    lines = [
        "AutoStop CRM client data quality maintenance",
        f"schema: {result['schema']}",
        f"read_only: {result['read_only']}",
        f"invalid_vehicle_vins: {summary.get('invalid_vehicle_vins', 0)}",
        f"phone_like_client_names: {summary.get('phone_like_client_names', 0)}",
        f"clients_affected: {summary.get('clients_affected', 0)}",
        f"vehicles_affected: {summary.get('vehicles_affected', 0)}",
        f"safe_fixes_available: {summary.get('safe_fixes_available', 0)}",
        f"review_required: {summary.get('review_required', 0)}",
    ]
    if "applied_fixes" in summary:
        lines.append(f"applied_fixes: {summary['applied_fixes']}")
    if result.get("backup_file"):
        lines.append(f"backup_file: {result['backup_file']}")
    for operation in result["operations"][: max(0, issue_limit)]:
        if operation.get("kind") == "replace_phone_like_client_name":
            lines.append(
                "- "
                f"client={operation['client_id']} "
                f"name={operation['client_name']} "
                f"replacement={operation.get('replacement_name') or operation.get('candidate_names')} "
                f"reason={operation['reason']} "
                f"safe_fix={'yes' if operation['safe_fix_available'] else 'no'}"
            )
        else:
            lines.append(
                "- "
                f"client={operation['client_id']} "
                f"name={operation['client_name']} "
                f"vehicle={operation['vehicle_id']} "
                f"vin={operation['previous_vin']} "
                f"reason={operation['reason']} "
                f"safe_fix={'yes' if operation['safe_fix_available'] else 'no'}"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit or clear placeholder VIN values in client vehicle profiles."
    )
    parser.add_argument("--state-file", type=Path, default=get_state_file())
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", action="store_true", help="Required with --apply.")
    parser.add_argument("--format", choices={"text", "json"}, default="text")
    parser.add_argument("--issue-limit", default=50)
    args = parser.parse_args(argv)
    args.issue_limit = _bounded_issue_limit(args.issue_limit)

    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
    try:
        result = (
            apply_client_data_quality_plan(args.state_file, backup=args.backup)
            if args.apply
            else build_client_data_quality_plan(args.state_file)
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc)}
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
        else:
            print(f"ok: False\nerror: {exc}")
        return 2
    if args.format == "json":
        print(json.dumps(_json_safe_value(result), ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(_format_text(result, issue_limit=args.issue_limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
