from __future__ import annotations
# ruff: noqa: E402,I001

import argparse
import json
import re
import shutil
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.config import get_state_file
from minimal_kanban.models import AuditEvent, ClientProfile, utc_now_iso
from minimal_kanban.storage.file_lock import ProcessFileLock
from minimal_kanban.storage.json_store import DEFAULT_STATE

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


def _text(value: object) -> str:
    return str(value or "").strip()


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
    if len(compact) < 6:
        return "too_short"
    if len(set(compact)) <= 1:
        return "repeated_character"
    return ""


def _read_state(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        return deepcopy(DEFAULT_STATE)
    return json.loads(state_file.read_text(encoding="utf-8"))


def _write_state(state_file: Path, state: dict[str, Any]) -> None:
    payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    temp_file = state_file.with_suffix(".tmp")
    temp_file.write_text(payload, encoding="utf-8")
    temp_file.replace(state_file)


def _client_name(raw_client: dict[str, Any]) -> str:
    try:
        return ClientProfile.from_dict(raw_client).name()
    except (TypeError, ValueError):
        return _text(raw_client.get("display_name") or raw_client.get("last_name")) or "Без имени"


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
                    "client_index": client_index,
                    "vehicle_index": vehicle_index,
                    "client_id": client_id,
                    "client_name": _client_name(raw_client),
                    "vehicle_id": vehicle_id,
                    "vehicle_label": _vehicle_label(raw_vehicle),
                    "previous_vin": previous_vin,
                    "reason": reason,
                }
            )

    operations.sort(
        key=lambda item: (
            str(item["reason"]),
            str(item["client_name"]).casefold(),
            str(item["client_id"]),
            str(item["vehicle_id"]),
        )
    )
    return {
        "schema": "client_data_quality_maintenance.v1",
        "read_only": True,
        "state_file": str(state_file.resolve()),
        "summary": {
            "invalid_vehicle_vins": len(operations),
            "clients_affected": len(clients_affected),
            "vehicles_affected": len(vehicles_affected),
            "safe_fixes_available": len(operations),
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
        if not plan["operations"]:
            return {**plan, "read_only": False, "applied": False, "backup_file": ""}

        backup_file = state_file.with_name(
            f"{state_file.name}.backup-client-data-quality-"
            f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        )
        shutil.copy2(state_file, backup_file)

        raw_clients = state.get("clients") if isinstance(state.get("clients"), list) else []
        applied_operations: list[dict[str, Any]] = []
        touched_client_indexes: set[int] = set()
        for operation in plan["operations"]:
            client_index = int(operation["client_index"])
            vehicle_index = int(operation["vehicle_index"])
            try:
                raw_vehicle = raw_clients[client_index]["vehicles"][vehicle_index]
            except (IndexError, KeyError, TypeError):
                continue
            if not isinstance(raw_vehicle, dict):
                continue
            if invalid_client_vehicle_vin_reason(raw_vehicle.get("vin")):
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
                message="System очистил мусорные VIN автомобилей клиентов",
                card_id=None,
                details={
                    "operations": [
                        {
                            "client_id": operation["client_id"],
                            "client_name": operation["client_name"],
                            "vehicle_id": operation["vehicle_id"],
                            "vehicle_label": operation["vehicle_label"],
                            "previous_vin": operation["previous_vin"],
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
        f"clients_affected: {summary.get('clients_affected', 0)}",
        f"vehicles_affected: {summary.get('vehicles_affected', 0)}",
        f"safe_fixes_available: {summary.get('safe_fixes_available', 0)}",
    ]
    if "applied_fixes" in summary:
        lines.append(f"applied_fixes: {summary['applied_fixes']}")
    if result.get("backup_file"):
        lines.append(f"backup_file: {result['backup_file']}")
    for operation in result["operations"][: max(0, issue_limit)]:
        lines.append(
            "- "
            f"client={operation['client_id']} "
            f"name={operation['client_name']} "
            f"vehicle={operation['vehicle_id']} "
            f"vin={operation['previous_vin']} "
            f"reason={operation['reason']}"
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
    parser.add_argument("--issue-limit", type=int, default=50)
    args = parser.parse_args(argv)

    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
    result = (
        apply_client_data_quality_plan(args.state_file, backup=args.backup)
        if args.apply
        else build_client_data_quality_plan(args.state_file)
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(_format_text(result, issue_limit=args.issue_limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
