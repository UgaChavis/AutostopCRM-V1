# ruff: noqa: E402,I001
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.operator_activity import (
    DEFAULT_DETAIL_RETENTION_DAYS,
    OperatorActivityService,
)


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


def _bounded_retention_days(value: object) -> int:
    if isinstance(value, bool):
        return DEFAULT_DETAIL_RETENTION_DAYS
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return DEFAULT_DETAIL_RETENTION_DAYS
    if not math.isfinite(numeric) or not numeric.is_integer():
        return DEFAULT_DETAIL_RETENTION_DAYS
    if numeric < 1:
        return 1
    if numeric > 3650:
        return 3650
    return int(numeric)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compact old AutoStop CRM operator activity rows into aggregate counters."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Only report planned compaction.")
    mode.add_argument("--apply", action="store_true", help="Compact activity storage under lock.")
    parser.add_argument("--backup", action="store_true", help="Required with --apply.")
    parser.add_argument("--activity-dir", type=Path, default=None)
    parser.add_argument("--retention-days", default=DEFAULT_DETAIL_RETENTION_DAYS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
    args.retention_days = _bounded_retention_days(args.retention_days)
    return args


def compact_operator_activity(args: argparse.Namespace) -> dict[str, Any]:
    service = OperatorActivityService(activity_dir=args.activity_dir)
    return service.compact_activity(
        {
            "apply": args.apply,
            "dry_run": args.dry_run,
            "backup": args.backup,
            "retention_days": args.retention_days,
        }
    )


def print_text(result: dict[str, Any]) -> None:
    for key, value in result.items():
        print(f"{key}: {value}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = compact_operator_activity(args)
    except (OSError, RuntimeError, ValueError) as exc:
        if args.json:
            print(_json_dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"error: {exc}")
        return 2
    if args.json:
        print(_json_dumps(result))
    else:
        print_text(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
