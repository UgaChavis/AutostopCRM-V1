# ruff: noqa: E402,I001
from __future__ import annotations

import argparse
import json
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compact old AutoStop CRM operator activity rows into aggregate counters."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Only report planned compaction.")
    mode.add_argument("--apply", action="store_true", help="Compact activity storage under lock.")
    parser.add_argument("--backup", action="store_true", help="Required with --apply.")
    parser.add_argument("--activity-dir", type=Path, default=None)
    parser.add_argument("--retention-days", type=int, default=DEFAULT_DETAIL_RETENTION_DAYS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.backup:
        parser.error("--apply requires --backup")
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
    result = compact_operator_activity(args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
