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


def _employee_ids(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        name, separator, employee_id = value.partition("=")
        if not separator or not name.strip() or not employee_id.strip():
            raise argparse.ArgumentTypeError("--employee-id должен иметь вид 'Имя сотрудника=ID'")
        result[name.strip()] = employee_id.strip()
    return result


def _backup_state(state_file: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{state_file.name}.payroll-20260713-{stamp}.backup"
    shutil.copy2(state_file, target)
    return target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run/apply миграции правил зарплаты с 13.07.2026."
    )
    parser.add_argument("--state-file", type=Path, default=get_state_file())
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--backup",
        type=Path,
        help="Каталог обязательной резервной копии перед --apply.",
    )
    parser.add_argument(
        "--employee-id",
        action="append",
        default=[],
        metavar="NAME=ID",
        help="Подтвержденная пара имени и ID; повторить для каждого сотрудника.",
    )
    parser.add_argument("--output", type=Path, help="Сохранить JSON-результат dry-run/apply.")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    state_file = args.state_file.expanduser().resolve()
    if not state_file.is_file():
        parser.error(f"state file не найден: {state_file}")
    if args.apply and args.backup is None:
        parser.error("режим --apply требует --backup DIR")
    try:
        expected_ids = _employee_ids(args.employee_id)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    backup_path = None
    if args.apply:
        backup_path = _backup_state(state_file, args.backup.expanduser().resolve())

    logger = logging.getLogger("payroll_policy_2026_07_13")
    store = JsonStore(state_file=state_file, logger=logger)
    service = CardService(store, logger)
    result = service.migrate_payroll_policy_2026_07_13(
        apply=args.apply,
        expected_employee_ids=expected_ids,
    )
    if backup_path is not None:
        result["backup_path"] = str(backup_path)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
