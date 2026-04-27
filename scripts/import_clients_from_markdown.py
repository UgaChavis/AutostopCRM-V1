from __future__ import annotations
# ruff: noqa: E402,I001

import argparse
import json
import logging
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.models import AuditEvent, ClientProfile, utc_now_iso
from minimal_kanban.storage.json_store import JsonStore


@dataclass(slots=True)
class ImportResult:
    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    companies: int = 0
    persons: int = 0
    with_inn: int = 0
    with_vehicles: int = 0
    errors: list[dict[str, str]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "companies": self.companies,
            "persons": self.persons,
            "with_inn": self.with_inn,
            "with_vehicles": self.with_vehicles,
            "errors": list(self.errors or []),
        }


def _digits(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _phone_keys(value: Any) -> set[str]:
    digits = _digits(value)
    if len(digits) < 7:
        return set()
    keys = {digits}
    if len(digits) >= 10:
        last_ten = digits[-10:]
        keys.update({last_ten, "7" + last_ten, "8" + last_ten})
    return keys


def _client_type(record: dict[str, Any]) -> str:
    source_type = str(record.get("client_type") or "").strip().lower()
    name = str(record.get("company_name") or record.get("full_name") or "").strip().upper()
    if source_type == "person":
        return "person"
    if name.startswith("ИП "):
        return "ip"
    if name.startswith("ООО"):
        return "ooo"
    return "company"


def _first(items: Any) -> str:
    return str(items[0]).strip() if isinstance(items, list) and items else ""


def _vehicle_payloads(record: dict[str, Any]) -> list[dict[str, str]]:
    vehicles = record.get("vehicles")
    if not isinstance(vehicles, list):
        return []
    payloads: list[dict[str, str]] = []
    for vehicle in vehicles:
        if not isinstance(vehicle, dict):
            continue
        payloads.append(
            {
                "brand": str(vehicle.get("brand") or "").strip(),
                "model": str(vehicle.get("model") or "").strip(),
                "vin": str(vehicle.get("vin") or "").strip(),
                "license_plate": str(
                    vehicle.get("license_plate")
                    or vehicle.get("plate")
                    or vehicle.get("registration_plate")
                    or ""
                ).strip(),
                "year": str(vehicle.get("year") or "").strip(),
            }
        )
    return payloads


def _comment(record: dict[str, Any]) -> str:
    parts: list[str] = []
    info = record.get("additional_info") if isinstance(record.get("additional_info"), dict) else {}
    quality = record.get("data_quality") if isinstance(record.get("data_quality"), dict) else {}
    if record.get("client_id"):
        parts.append(f"Источник import_id: {record['client_id']}")
    rows = info.get("source_rows") if isinstance(info, dict) else []
    if isinstance(rows, list) and rows:
        parts.append("Исходные строки: " + ", ".join(str(item) for item in rows))
    notes = info.get("notes") if isinstance(info, dict) else []
    if isinstance(notes, list) and notes:
        parts.append("Заметки из импорта: " + "; ".join(str(item) for item in notes if item))
    flags = quality.get("flags") if isinstance(quality, dict) else []
    if isinstance(flags, list) and flags:
        parts.append("Флаги качества: " + ", ".join(str(item) for item in flags))
    return "\n".join(parts)


def _profile_payload(record: dict[str, Any]) -> dict[str, Any]:
    req = record.get("requisites") if isinstance(record.get("requisites"), dict) else {}
    phones = record.get("phones") if isinstance(record.get("phones"), list) else []
    emails = record.get("emails") if isinstance(record.get("emails"), list) else []
    addresses = record.get("address") if isinstance(record.get("address"), list) else []
    name = str(record.get("company_name") or record.get("full_name") or "").strip()
    return {
        "id": str(record.get("client_id") or uuid.uuid4()),
        "client_type": _client_type(record),
        "last_name": record.get("last_name") or "",
        "first_name": record.get("first_name") or "",
        "middle_name": record.get("patronymic") or "",
        "display_name": name,
        "legal_name": record.get("company_name") or "",
        "short_name": record.get("company_name") or record.get("full_name") or "",
        "phone": _first(phones),
        "phones": phones,
        "email": _first(emails),
        "actual_address": "; ".join(str(item).strip() for item in addresses if str(item).strip()),
        "inn": req.get("inn") or "",
        "kpp": req.get("kpp") or "",
        "checking_account": req.get("settlement_account") or "",
        "bank_name": req.get("bank") or "",
        "legal_address": req.get("legal_address") or "",
        "contact_person": req.get("director_or_contact") or "",
        "comment": _comment(record),
        "vehicles": _vehicle_payloads(record),
    }


def read_jsonl_records(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    in_jsonl_block = False
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if text == "```jsonl":
            in_jsonl_block = True
            continue
        if text == "```" and in_jsonl_block:
            break
        if not in_jsonl_block:
            continue
        if not text.startswith("{"):
            continue
        try:
            record = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {line_no}: {exc}") from exc
        if isinstance(record, dict):
            records.append(record)
        if limit is not None and len(records) >= limit:
            break
    return records


def _merge_profiles(
    existing: ClientProfile, incoming: ClientProfile, *, prefer_incoming_id: bool = False
) -> ClientProfile:
    before = existing.to_storage_dict()
    merged = dict(before)
    incoming_payload = incoming.to_storage_dict()
    for key, value in incoming_payload.items():
        if key in {"id", "created_at"}:
            continue
        if key in {"phones", "vehicles"}:
            merged[key] = [*before.get(key, []), *incoming_payload.get(key, [])]
        elif value:
            merged[key] = value
    merged["id"] = incoming.id if prefer_incoming_id else existing.id
    merged["created_at"] = existing.created_at
    merged["updated_at"] = utc_now_iso()
    return ClientProfile.from_dict(merged)


def _index_clients(clients: list[ClientProfile]) -> dict[str, dict[str, ClientProfile]]:
    indexes: dict[str, dict[str, ClientProfile]] = {
        "id": {},
        "inn": {},
        "phone": {},
        "name": {},
    }
    for client in clients:
        indexes["id"][client.id] = client
        if client.inn:
            indexes["inn"][_digits(client.inn)] = client
        for phone in client.phones:
            for key in _phone_keys(phone):
                indexes["phone"][key] = client
        name = _norm(client.name())
        if name:
            indexes["name"][name] = client
    return indexes


def _match_existing(indexes: dict[str, dict[str, ClientProfile]], profile: ClientProfile) -> ClientProfile | None:
    if profile.id in indexes["id"]:
        return indexes["id"][profile.id]
    if profile.inn and _digits(profile.inn) in indexes["inn"]:
        return indexes["inn"][_digits(profile.inn)]
    name = _norm(profile.name())
    for phone in profile.phones:
        for key in _phone_keys(phone):
            candidate = indexes["phone"].get(key)
            if candidate and (candidate.client_type != "person" or _norm(candidate.name()) == name):
                return candidate
    return indexes["name"].get(name)


def import_records(
    path: Path,
    *,
    apply: bool = False,
    limit: int | None = None,
    state_file: Path | None = None,
) -> dict[str, Any]:
    records = read_jsonl_records(path, limit=limit)
    store = JsonStore(state_file=state_file, logger=logging.getLogger("import-clients"))
    bundle = store.read_bundle()
    clients = list(bundle["clients"])
    clients_by_id = {client.id: client for client in clients}
    preexisting_manual_clients = [
        client
        for client in clients
        if not client.id.startswith("import-")
        and not re.fullmatch(r"[0-9a-fA-F]{12}", client.id or "")
    ]
    preexisting_manual_indexes = _index_clients(preexisting_manual_clients)
    result = ImportResult(errors=[])
    now = utc_now_iso()
    id_changes: dict[str, str] = {}
    for record in records:
        result.total += 1
        try:
            profile = ClientProfile.from_dict(_profile_payload(record))
            if not profile.name() or profile.name() in {"Без имени", "Без названия"}:
                result.skipped += 1
                result.errors.append({"name": str(record.get("client_id") or ""), "reason": "empty_name"})
                continue
            if profile.client_type == "person":
                result.persons += 1
            else:
                result.companies += 1
            if profile.inn:
                result.with_inn += 1
            if profile.vehicles:
                result.with_vehicles += 1
            existing = clients_by_id.get(profile.id) or clients_by_id.get("import-" + profile.id)
            if existing is None:
                existing = _match_existing(preexisting_manual_indexes, profile)
                if existing is not None:
                    existing = clients_by_id.get(existing.id, existing)
            if existing is None:
                result.created += 1
                if apply:
                    clients.append(profile)
                    clients_by_id[profile.id] = profile
            else:
                result.updated += 1
                if apply:
                    prefer_incoming_id = (
                        existing.id.startswith("import-") and profile.id not in clients_by_id
                    )
                    updated = _merge_profiles(
                        existing, profile, prefer_incoming_id=prefer_incoming_id
                    )
                    if updated.id != existing.id:
                        id_changes[existing.id] = updated.id
                        clients_by_id.pop(existing.id, None)
                    clients[clients.index(existing)] = updated
                    clients_by_id[updated.id] = updated
        except Exception as exc:  # noqa: BLE001 - import report must keep moving.
            result.skipped += 1
            result.errors.append(
                {
                    "name": str(record.get("company_name") or record.get("full_name") or record.get("client_id") or ""),
                    "reason": str(exc),
                }
            )
    backup_file = ""
    if apply:
        state_file = store._state_file  # noqa: SLF001 - operational script needs the exact state path.
        if state_file.exists():
            backup_path = state_file.with_name(
                state_file.stem
                + f".before_full_clients_import_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                + state_file.suffix
            )
            shutil.copy2(state_file, backup_path)
            backup_file = str(backup_path)
        events = list(bundle["events"])
        cards = list(bundle["cards"])
        if id_changes:
            for card in cards:
                if card.client_id in id_changes:
                    card.client_id = id_changes[card.client_id]
        events.append(
            AuditEvent(
                id=str(uuid.uuid4()),
                timestamp=now,
                actor_name="Codex clients import",
                source="api",
                action="clients_bulk_imported",
                message="Импортирован справочник клиентов из Markdown JSONL",
                details=result.to_dict(),
                card_id=None,
            )
        )
        store.write_bundle(
            columns=bundle["columns"],
            cards=cards,
            clients=clients,
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=events,
            settings=bundle["settings"],
        )
    return {
        "applied": apply,
        "backup_file": backup_file,
        "clients_before": len(bundle["clients"]),
        "clients_after": len(clients) if apply else len(bundle["clients"]) + result.created,
        **result.to_dict(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import AutoStop CRM clients from Markdown JSONL.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--apply", action="store_true", help="Write changes. Without this, dry-run only.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--state-file", type=Path, default=None)
    args = parser.parse_args()
    report = import_records(args.path, apply=args.apply, limit=args.limit, state_file=args.state_file)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report.get("errors") else 2


if __name__ == "__main__":
    raise SystemExit(main())
