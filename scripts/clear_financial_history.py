from __future__ import annotations
# ruff: noqa: E402,I001

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.config import get_state_file
from minimal_kanban.json_safety import reject_deeply_nested_json
from minimal_kanban.storage.file_lock import ProcessFileLock
from minimal_kanban.storage.financial_history_cleanup import sanitize_financial_history_state
from minimal_kanban.storage.limited_io import copy_file_limited

STATE_FILE_MAX_BYTES = 100 * 1024 * 1024


PAYROLL_ROW_FIELDS = (
    "executor_id",
    "executor_name",
    "work_executor_id_snapshot",
    "work_executor_name_snapshot",
    "work_quantity_snapshot",
    "work_price_snapshot",
    "work_total_snapshot",
    "salary_mode_snapshot",
    "base_salary_snapshot",
    "work_percent_snapshot",
    "salary_amount",
    "salary_accrued_at",
)
PAYMENT_LINK_FIELDS = ("cash_transaction_id",)
CASHBOX_STATISTIC_FIELDS = (
    "balance_minor",
    "transactions_total",
    "income_total_minor",
    "expense_total_minor",
)


def _json_bytes(value: Any) -> int:
    return len(
        json.dumps(
            _json_safe_value(value),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_state(state_file: Path) -> dict[str, Any]:
    if not state_file.exists():
        raise FileNotFoundError(f"State file not found: {state_file}")
    try:
        state = json.loads(
            _read_state_text(state_file),
            parse_constant=_reject_json_constant,
        )
    except RecursionError as exc:
        raise ValueError("financial history state file JSON is too deeply nested") from exc
    reject_deeply_nested_json(
        state,
        message="financial history state file JSON is too deeply nested",
    )
    if not isinstance(state, dict):
        raise ValueError("state file must contain a JSON object")
    return state


def _read_state_text(state_file: Path) -> str:
    with state_file.open("rb") as handle:
        raw = handle.read(STATE_FILE_MAX_BYTES + 1)
    if len(raw) > STATE_FILE_MAX_BYTES:
        raise ValueError("financial history state file is too large")
    return raw.decode("utf-8")


def _backup_state_file(state_file: Path) -> dict[str, str]:
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_file = state_file.with_name(f"{state_file.name}.backup-financial-history-{timestamp}")
    counter = 1
    while backup_file.exists():
        backup_file = state_file.with_name(
            f"{state_file.name}.backup-financial-history-{timestamp}-{counter:03d}"
        )
        counter += 1
    copy_file_limited(
        state_file,
        backup_file,
        max_bytes=STATE_FILE_MAX_BYTES,
        label="financial history state file",
    )
    return {"path": str(backup_file), "sha256": _sha256_file(backup_file)}


def _write_state_file(state_file: Path, state: dict[str, Any]) -> None:
    payload = json.dumps(
        _json_safe_value(state),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(payload.encode("utf-8")) > STATE_FILE_MAX_BYTES:
        raise ValueError("financial history state file is too large")
    temp_file = state_file.with_name(f".{state_file.name}.{uuid4().hex}.financial-history.tmp")
    try:
        temp_file.write_text(payload, encoding="utf-8")
        temp_file.replace(state_file)
    finally:
        temp_file.unlink(missing_ok=True)


def _iter_repair_orders(state: dict[str, Any]):
    cards = state.get("cards")
    if not isinstance(cards, list):
        return
    for card in cards:
        if not isinstance(card, dict):
            continue
        repair_order = card.get("repair_order")
        if isinstance(repair_order, dict):
            yield repair_order


def _count_existing_repair_order_values(
    state: dict[str, Any],
    *,
    row_keys: tuple[str, ...],
    fields: tuple[str, ...],
) -> int:
    total = 0
    for repair_order in _iter_repair_orders(state):
        for row_key in row_keys:
            rows = repair_order.get(row_key)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                total += sum(1 for field in fields if str(row.get(field) or "").strip())
    return total


def _cashbox_statistic_needs_reset(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    try:
        numeric = float(0 if value is None or value == "" else value)
    except (OverflowError, TypeError, ValueError):
        return bool(str(value or "").strip())
    if not math.isfinite(numeric):
        return True
    return numeric != 0


def _count_reset_cashbox_statistics(state: dict[str, Any]) -> int:
    cashboxes = state.get("cashboxes")
    if not isinstance(cashboxes, list):
        return 0
    total = 0
    for cashbox in cashboxes:
        if not isinstance(cashbox, dict):
            continue
        statistics = cashbox.get("statistics")
        if not isinstance(statistics, dict):
            continue
        if any(
            _cashbox_statistic_needs_reset(statistics.get(field))
            for field in CASHBOX_STATISTIC_FIELDS
        ):
            total += 1
    return total


def _count_removed_financial_events(state: dict[str, Any], sanitized: dict[str, Any]) -> int:
    events = state.get("events") if isinstance(state.get("events"), list) else []
    sanitized_events = sanitized.get("events") if isinstance(sanitized.get("events"), list) else []
    return max(0, len(events) - len(sanitized_events))


def _build_summary(state: dict[str, Any], sanitized: dict[str, Any]) -> dict[str, Any]:
    cash_transactions = (
        state.get("cash_transactions") if isinstance(state.get("cash_transactions"), list) else []
    )
    sanitized_cash_transactions = (
        sanitized.get("cash_transactions")
        if isinstance(sanitized.get("cash_transactions"), list)
        else []
    )
    return {
        "changed": state != sanitized,
        "state_bytes_before": _json_bytes(state),
        "state_bytes_after": _json_bytes(sanitized),
        "cash_transactions_removed": max(
            0, len(cash_transactions) - len(sanitized_cash_transactions)
        ),
        "financial_events_removed": _count_removed_financial_events(state, sanitized),
        "repair_order_payment_links_cleared": _count_existing_repair_order_values(
            state,
            row_keys=("payments", "payment_history"),
            fields=PAYMENT_LINK_FIELDS,
        ),
        "payroll_fields_cleared": _count_existing_repair_order_values(
            state,
            row_keys=("works", "materials"),
            fields=PAYROLL_ROW_FIELDS,
        ),
        "cashbox_statistics_reset": _count_reset_cashbox_statistics(state),
    }


def build_financial_history_cleanup_result(
    state_file: Path | None = None,
    *,
    apply: bool = False,
    backup: bool = False,
) -> dict[str, Any]:
    state_file = (state_file or get_state_file()).expanduser().resolve()
    if apply and not backup:
        raise ValueError("--apply requires --backup")

    lock = ProcessFileLock(state_file.with_suffix(".lock"), timeout_seconds=60.0)
    with lock.acquire():
        raw_state = _read_state(state_file)
        sanitized_state = sanitize_financial_history_state(raw_state)
        summary = _build_summary(raw_state, sanitized_state)
        result: dict[str, Any] = {
            "schema": "financial_history_cleanup.v1",
            "dry_run": not apply,
            "applied": False,
            "state_file": str(state_file),
            "backup": {},
            "summary": summary,
        }
        if not apply or not summary["changed"]:
            return result
        result["backup"] = _backup_state_file(state_file)
        _write_state_file(state_file, sanitized_state)
        result["applied"] = True
        return result


def _format_text(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        "AutoStop CRM financial history cleanup",
        f"schema: {result['schema']}",
        f"dry_run: {result['dry_run']}",
        f"applied: {result['applied']}",
        f"state_file: {result['state_file']}",
        f"changed: {summary['changed']}",
        f"state_bytes_before: {summary['state_bytes_before']}",
        f"state_bytes_after: {summary['state_bytes_after']}",
        f"cash_transactions_removed: {summary['cash_transactions_removed']}",
        f"financial_events_removed: {summary['financial_events_removed']}",
        f"repair_order_payment_links_cleared: {summary['repair_order_payment_links_cleared']}",
        f"payroll_fields_cleared: {summary['payroll_fields_cleared']}",
        f"cashbox_statistics_reset: {summary['cashbox_statistics_reset']}",
    ]
    if result.get("backup"):
        lines.append(f"backup_file: {result['backup'].get('path')}")
        lines.append(f"backup_sha256: {result['backup'].get('sha256')}")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit or clear historical cash and payroll data from an AutoStop CRM state file."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Only report planned cleanup.")
    mode.add_argument("--apply", action="store_true", help="Sanitize state.json under file lock.")
    parser.add_argument("--backup", action="store_true", help="Required with --apply.")
    parser.add_argument(
        "--state-file",
        type=Path,
        default=get_state_file(),
        help="Path to the state.json file to sanitize.",
    )
    parser.add_argument("--format", choices={"text", "json"}, default="text")
    args = parser.parse_args(argv)
    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = build_financial_history_cleanup_result(
            args.state_file,
            apply=args.apply,
            backup=args.backup,
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
        print(_format_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
