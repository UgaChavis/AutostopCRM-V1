from __future__ import annotations

# ruff: noqa: E402
import argparse
import hashlib
import json
import logging
import math
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.config import get_state_file
from minimal_kanban.json_safety import reject_deeply_nested_json
from minimal_kanban.models import format_money_minor, parse_datetime
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore
from minimal_kanban.storage.limited_io import copy_file_limited

TARGET_EVENT_ID = "fed7fc32-998f-4454-897c-82aef5ede458"
TARGET_EVENT_TIMESTAMP = "2026-05-29T13:37:37Z"
TARGET_FIX_KIND = "create_missing_payment_cash_transaction"
NORMALIZATION_NOTE = "Нормализация кассы: откат ошибочного автопереноса оплат ЗН 29.05.2026"
NORMALIZATION_KIND = "cashbox_normalization"
CORRECTION_NOTE_MARKERS = ("корректировка кассы", "нормализация кассы")
STATE_FILE_MAX_BYTES = 100 * 1024 * 1024
ARCHIVE_EVENT_LINE_MAX_BYTES = 2 * 1024 * 1024
ARCHIVE_EVENT_DISCARD_CHUNK_BYTES = 64 * 1024
MONEY_MINOR_ABS_MAX = 100_000_000_000_000


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            _read_text_bounded(path),
            parse_constant=_reject_json_constant,
        )
    except RecursionError as exc:
        raise ValueError("state file JSON is too deeply nested") from exc
    reject_deeply_nested_json(payload, message="state file JSON is too deeply nested")
    if not isinstance(payload, dict):
        raise ValueError("state file must contain a JSON object")
    return payload


def _read_text_bounded(path: Path) -> str:
    with path.open("rb") as handle:
        raw = handle.read(STATE_FILE_MAX_BYTES + 1)
    if len(raw) > STATE_FILE_MAX_BYTES:
        raise ValueError("cashbox normalization state file is too large")
    return raw.decode("utf-8")


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


def _money_minor(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        numeric = float(0 if value is None or value == "" else value)
    except (OverflowError, TypeError, ValueError):
        return 0
    if not math.isfinite(numeric) or not numeric.is_integer():
        return 0
    if abs(numeric) > MONEY_MINOR_ABS_MAX:
        return 0
    return int(numeric)


def _bounded_expected_source_count(value: object) -> int:
    if isinstance(value, bool):
        return 16
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return 16
    if not math.isfinite(numeric) or not numeric.is_integer():
        return 16
    if numeric < 0:
        return 0
    if numeric > 10_000:
        return 10_000
    return int(numeric)


def _money_display(amount_minor: object) -> str:
    return format_money_minor(_money_minor(amount_minor))


def _event_datetime(value: object):
    parsed = parse_datetime(str(value or "").replace("Z", "+00:00"))
    return parsed


def _iter_archive_events(archive_dir: Path | None) -> list[dict[str, Any]]:
    if archive_dir is None or not archive_dir.exists():
        return []
    events: list[dict[str, Any]] = []
    for path in sorted(archive_dir.glob("*.jsonl")):
        try:
            lines = _iter_archive_event_lines(path)
            for raw in lines:
                if not raw:
                    continue
                try:
                    payload = json.loads(raw, parse_constant=_reject_json_constant)
                except (ValueError, json.JSONDecodeError, RecursionError):
                    continue
                if isinstance(payload, dict) and isinstance(payload.get("event"), dict):
                    payload = payload["event"]
                if isinstance(payload, dict):
                    events.append(payload)
        except OSError:
            continue
    return events


def _iter_archive_event_lines(path: Path):
    with path.open("rb") as handle:
        while True:
            raw_line = handle.readline(ARCHIVE_EVENT_LINE_MAX_BYTES + 1)
            if not raw_line:
                break
            if len(raw_line) > ARCHIVE_EVENT_LINE_MAX_BYTES:
                if not raw_line.endswith(b"\n"):
                    _discard_archive_event_line_tail(handle)
                continue
            try:
                yield raw_line.decode("utf-8").strip()
            except UnicodeDecodeError:
                continue


def _discard_archive_event_line_tail(handle: BinaryIO) -> None:
    while True:
        chunk = handle.read(ARCHIVE_EVENT_DISCARD_CHUNK_BYTES)
        if not chunk:
            return
        newline_index = chunk.find(b"\n")
        if newline_index < 0:
            continue
        unread = len(chunk) - newline_index - 1
        if unread:
            handle.seek(-unread, 1)
        return


def _iter_events(state: dict[str, Any], archive_dir: Path | None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    state_events = state.get("events")
    if isinstance(state_events, list):
        events.extend(item for item in state_events if isinstance(item, dict))
    events.extend(_iter_archive_events(archive_dir))
    seen: set[object] = set()
    deduped: list[dict[str, Any]] = []
    for event in events:
        key = event.get("id") or (
            event.get("timestamp"),
            event.get("action"),
            event.get("message"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def _find_target_event(
    state: dict[str, Any],
    *,
    archive_dir: Path | None,
    event_id: str,
) -> dict[str, Any]:
    for event in _iter_events(state, archive_dir):
        if str(event.get("id") or "") == event_id:
            return event
    raise ValueError(f"Target finance audit event was not found: {event_id}")


def _cashbox_name(cashboxes_by_id: dict[str, dict[str, Any]], cashbox_id: str) -> str:
    return str(cashboxes_by_id.get(cashbox_id, {}).get("name") or cashbox_id)


def _source_transactions_from_event(
    state: dict[str, Any],
    event: dict[str, Any],
) -> list[dict[str, Any]]:
    details = event.get("details") if isinstance(event.get("details"), dict) else {}
    applied = details.get("applied") if isinstance(details.get("applied"), list) else []
    transactions_by_id = {
        str(item.get("id") or ""): item
        for item in state.get("cash_transactions", [])
        if isinstance(item, dict)
    }
    cashboxes_by_id = {
        str(item.get("id") or ""): item
        for item in state.get("cashboxes", [])
        if isinstance(item, dict)
    }
    source_transactions: list[dict[str, Any]] = []
    for item in applied:
        if not isinstance(item, dict) or item.get("kind") != TARGET_FIX_KIND:
            continue
        transaction_id = str(item.get("cash_transaction_id") or "")
        transaction = transactions_by_id.get(transaction_id, {})
        cashbox_id = str(item.get("cashbox_id") or transaction.get("cashbox_id") or "")
        amount_minor = _money_minor(transaction.get("amount_minor") or item.get("amount_minor"))
        direction = str(transaction.get("direction") or "income")
        if not cashbox_id or amount_minor <= 0:
            continue
        source_transactions.append(
            {
                "cashbox_id": cashbox_id,
                "cashbox_name": _cashbox_name(cashboxes_by_id, cashbox_id),
                "amount_minor": amount_minor,
                "amount_display": _money_display(amount_minor),
                "cash_transaction_id": transaction_id,
                "cash_transaction_present": bool(transaction),
                "cash_transaction_direction": direction,
                "repair_order_number": str(item.get("repair_order_number") or ""),
                "repair_order_payment_id": str(item.get("repair_order_payment_id") or ""),
                "card_id": str(item.get("card_id") or ""),
                "created_at": str(transaction.get("created_at") or item.get("created_at") or ""),
                "note": str(transaction.get("note") or ""),
            }
        )
    return sorted(
        source_transactions,
        key=lambda item: (
            str(item["cashbox_name"]).casefold(),
            str(item["created_at"]),
            str(item["cash_transaction_id"]),
        ),
    )


def _is_existing_correction(
    transaction: dict[str, Any],
    *,
    event_timestamp: object,
) -> bool:
    if transaction.get("direction") != "expense":
        return False
    note = str(transaction.get("note") or "").casefold()
    kind = str(transaction.get("transaction_kind") or "").casefold()
    if kind != NORMALIZATION_KIND and not any(marker in note for marker in CORRECTION_NOTE_MARKERS):
        return False
    event_dt = _event_datetime(event_timestamp)
    transaction_dt = _event_datetime(transaction.get("created_at"))
    if event_dt is not None and transaction_dt is not None and transaction_dt < event_dt:
        return False
    return _money_minor(transaction.get("amount_minor")) > 0


def calculate_normalization_plan(
    state: dict[str, Any],
    *,
    archive_dir: Path | None = None,
    event_id: str = TARGET_EVENT_ID,
    expected_source_count: int | None = 16,
) -> dict[str, Any]:
    event = _find_target_event(state, archive_dir=archive_dir, event_id=event_id)
    source_transactions = _source_transactions_from_event(state, event)
    if expected_source_count is not None and len(source_transactions) != expected_source_count:
        raise ValueError(
            f"Expected {expected_source_count} source transactions, "
            f"found {len(source_transactions)}."
        )

    cashboxes_by_id = {
        str(item.get("id") or ""): item
        for item in state.get("cashboxes", [])
        if isinstance(item, dict)
    }
    gross_by_cashbox: dict[str, int] = {}
    for item in source_transactions:
        cashbox_id = str(item["cashbox_id"])
        gross_by_cashbox[cashbox_id] = gross_by_cashbox.get(cashbox_id, 0) + _money_minor(
            item.get("amount_minor")
        )

    existing_corrections: list[dict[str, Any]] = []
    applied_correction_by_cashbox: dict[str, int] = {}
    for transaction in sorted(
        (
            item
            for item in state.get("cash_transactions", [])
            if isinstance(item, dict)
            and _is_existing_correction(item, event_timestamp=event.get("timestamp"))
        ),
        key=lambda item: str(item.get("created_at") or ""),
    ):
        cashbox_id = str(transaction.get("cashbox_id") or "")
        gross = gross_by_cashbox.get(cashbox_id, 0)
        if gross <= 0:
            continue
        amount_minor = _money_minor(transaction.get("amount_minor"))
        already = applied_correction_by_cashbox.get(cashbox_id, 0)
        if amount_minor > gross - already:
            continue
        applied_correction_by_cashbox[cashbox_id] = already + amount_minor
        existing_corrections.append(
            {
                "cashbox_id": cashbox_id,
                "cashbox_name": _cashbox_name(cashboxes_by_id, cashbox_id),
                "amount_minor": amount_minor,
                "amount_display": _money_display(amount_minor),
                "cash_transaction_id": str(transaction.get("id") or ""),
                "created_at": str(transaction.get("created_at") or ""),
                "note": str(transaction.get("note") or ""),
                "transaction_kind": str(transaction.get("transaction_kind") or ""),
            }
        )

    adjustments: list[dict[str, Any]] = []
    totals_by_cashbox: list[dict[str, Any]] = []
    for cashbox_id, gross_amount in sorted(
        gross_by_cashbox.items(),
        key=lambda item: _cashbox_name(cashboxes_by_id, item[0]).casefold(),
    ):
        existing_amount = applied_correction_by_cashbox.get(cashbox_id, 0)
        net_amount = max(gross_amount - existing_amount, 0)
        row = {
            "cashbox_id": cashbox_id,
            "cashbox_name": _cashbox_name(cashboxes_by_id, cashbox_id),
            "source_amount_minor": gross_amount,
            "source_amount_display": _money_display(gross_amount),
            "existing_correction_minor": existing_amount,
            "existing_correction_display": _money_display(existing_amount),
            "proposed_adjustment_minor": net_amount,
            "proposed_adjustment_display": _money_display(net_amount),
        }
        totals_by_cashbox.append(row)
        if net_amount > 0:
            adjustments.append(
                {
                    "cashbox_id": cashbox_id,
                    "cashbox_name": row["cashbox_name"],
                    "amount_minor": net_amount,
                    "amount_display": row["proposed_adjustment_display"],
                    "direction": "expense",
                    "transaction_kind": NORMALIZATION_KIND,
                    "note": NORMALIZATION_NOTE,
                }
            )

    return {
        "event": {
            "id": str(event.get("id") or ""),
            "timestamp": str(event.get("timestamp") or TARGET_EVENT_TIMESTAMP),
            "action": str(event.get("action") or ""),
            "actor_name": str(event.get("actor_name") or ""),
            "source": str(event.get("source") or ""),
        },
        "source_transactions": source_transactions,
        "totals_by_cashbox": totals_by_cashbox,
        "existing_corrections": existing_corrections,
        "adjustments": adjustments,
        "summary": {
            "source_transactions": len(source_transactions),
            "source_amount_minor": sum(
                _money_minor(item.get("amount_minor")) for item in source_transactions
            ),
            "existing_correction_minor": sum(
                _money_minor(item.get("amount_minor")) for item in existing_corrections
            ),
            "proposed_adjustment_minor": sum(
                _money_minor(item.get("amount_minor")) for item in adjustments
            ),
        },
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_state_file(state_file: Path) -> dict[str, str]:
    backup_dir = state_file.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_file = backup_dir / f"state-before-cashbox-normalization-{timestamp}.json"
    counter = 2
    while backup_file.exists():
        backup_file = (
            backup_dir / f"state-before-cashbox-normalization-{timestamp}-{counter:03d}.json"
        )
        counter += 1
    copy_file_limited(
        state_file,
        backup_file,
        max_bytes=STATE_FILE_MAX_BYTES,
        label="cashbox normalization state file",
    )
    return {"path": str(backup_file), "sha256": _sha256_file(backup_file)}


def run_normalization(
    *,
    state_file: Path,
    archive_dir: Path | None = None,
    apply: bool = False,
    backup: bool = False,
    event_id: str = TARGET_EVENT_ID,
    expected_source_count: int | None = 16,
) -> dict[str, Any]:
    state_file = state_file.expanduser().resolve()
    archive_dir = (archive_dir or (state_file.parent / "audit-archive")).expanduser().resolve()
    state = _read_json(state_file)
    plan = calculate_normalization_plan(
        state,
        archive_dir=archive_dir,
        event_id=event_id,
        expected_source_count=expected_source_count,
    )
    result: dict[str, Any] = {
        "ok": True,
        "dry_run": not apply,
        "state_file": str(state_file),
        "archive_dir": str(archive_dir),
        "backup": {},
        "created_transactions": [],
        "plan": plan,
    }
    if not apply:
        return result
    if not backup:
        raise ValueError("--apply requires --backup")
    if not plan["adjustments"]:
        return result

    result["backup"] = _backup_state_file(state_file)
    logger = logging.getLogger("normalize_cashboxes_after_safe_fix")
    logger.addHandler(logging.NullHandler())
    store = JsonStore(state_file=state_file, logger=logger)
    service = CardService(store, logger)
    for adjustment in plan["adjustments"]:
        response = service.create_cash_transaction(
            {
                "cashbox_id": adjustment["cashbox_id"],
                "direction": "expense",
                "amount_minor": adjustment["amount_minor"],
                "note": adjustment["note"],
                "actor_name": "CODEX",
                "source": "api",
                "transaction_kind": NORMALIZATION_KIND,
            }
        )
        transaction = response.get("transaction") if isinstance(response, dict) else {}
        result["created_transactions"].append(
            {
                "cashbox_id": adjustment["cashbox_id"],
                "cashbox_name": adjustment["cashbox_name"],
                "amount_minor": adjustment["amount_minor"],
                "amount_display": adjustment["amount_display"],
                "cash_transaction_id": transaction.get("id")
                if isinstance(transaction, dict)
                else "",
            }
        )
    post_apply_state = _read_json(state_file)
    post_apply_events = post_apply_state.get("events")
    if not isinstance(post_apply_events, list):
        post_apply_events = []
    if not any(
        isinstance(item, dict) and str(item.get("id") or "") == event_id
        for item in post_apply_events
    ):
        # JsonStore may age the already verified historical audit event out of
        # state while persisting the normalization transactions. Keep that
        # event only in this in-memory reread so post-apply verification uses
        # the same immutable source proof; do not write it back into CRM.
        source_event = _find_target_event(
            state,
            archive_dir=archive_dir,
            event_id=event_id,
        )
        post_apply_state = {
            **post_apply_state,
            "events": [*post_apply_events, source_event],
        }
    result["post_apply_plan"] = calculate_normalization_plan(
        post_apply_state,
        archive_dir=archive_dir,
        event_id=event_id,
        expected_source_count=expected_source_count,
    )
    return result


def _format_text(result: dict[str, Any]) -> str:
    plan = result["plan"]
    lines = [
        "AutoStop CRM cashbox normalization",
        f"dry_run: {result['dry_run']}",
        f"state_file: {result['state_file']}",
        f"event_id: {plan['event']['id']}",
        f"source_transactions: {plan['summary']['source_transactions']}",
        f"source_amount: {_money_display(plan['summary']['source_amount_minor'])}",
        f"existing_corrections: {_money_display(plan['summary']['existing_correction_minor'])}",
        f"proposed_adjustment: {_money_display(plan['summary']['proposed_adjustment_minor'])}",
    ]
    if result.get("backup"):
        lines.append(f"backup_file: {result['backup'].get('path')}")
        lines.append(f"backup_sha256: {result['backup'].get('sha256')}")
    lines.append("")
    lines.append("Totals by cashbox:")
    for row in plan["totals_by_cashbox"]:
        lines.append(
            "- "
            f"{row['cashbox_name']}: source={row['source_amount_display']} "
            f"existing={row['existing_correction_display']} "
            f"adjust={row['proposed_adjustment_display']}"
        )
    lines.append("")
    lines.append("Adjustments:")
    if plan["adjustments"]:
        for item in plan["adjustments"]:
            lines.append(f"- {item['cashbox_name']}: {item['amount_display']} {item['note']}")
    else:
        lines.append("- none")
    if result.get("created_transactions"):
        lines.append("")
        lines.append("Created transactions:")
        for item in result["created_transactions"]:
            lines.append(
                f"- {item['cashbox_name']}: {item['amount_display']} "
                f"id={item['cash_transaction_id']}"
            )
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize cashboxes after the 2026-05-29 finance audit safe-fix event."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", action="store_true", help="Required with --apply.")
    parser.add_argument("--state-file", type=Path, default=get_state_file())
    parser.add_argument("--archive-dir", type=Path, default=None)
    parser.add_argument("--event-id", default=TARGET_EVENT_ID)
    parser.add_argument("--expected-source-count", default=16)
    parser.add_argument("--allow-count-mismatch", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
    args.expected_source_count = _bounded_expected_source_count(args.expected_source_count)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    expected_source_count = None if args.allow_count_mismatch else args.expected_source_count
    try:
        result = run_normalization(
            state_file=args.state_file,
            archive_dir=args.archive_dir,
            apply=args.apply,
            backup=args.backup,
            event_id=args.event_id,
            expected_source_count=expected_source_count,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
        )
        return 2
    if args.json:
        print(json.dumps(_json_safe_value(result), ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(_format_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
