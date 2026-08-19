from __future__ import annotations

# ruff: noqa: E402
import argparse
import json
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.config import get_state_file
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run/apply миграции циклов проведения заказ-нарядов."
    )
    parser.add_argument("--state-file", type=Path, default=get_state_file())
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    state_file = args.state_file.expanduser().resolve()
    if not state_file.is_file():
        parser.error(f"state file не найден: {state_file}")
    if args.apply and args.backup is None:
        parser.error("режим --apply требует --backup DIR")
    backup_path = ""
    if args.apply:
        backup_dir = args.backup.expanduser().resolve()
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = backup_dir / f"{state_file.name}.repair-order-cycles-{stamp}.backup"
        shutil.copy2(state_file, target)
        backup_path = str(target)
    logger = logging.getLogger("repair_order_cycle_migration")
    service = CardService(JsonStore(state_file=state_file, logger=logger), logger)
    result = service.migrate_repair_order_cycles(apply=args.apply)
    if backup_path:
        result["backup_path"] = backup_path
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
