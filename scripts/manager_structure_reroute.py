"""Explicitly reroute a portable diagram or a local CRM with a rollback backup.

Examples:
  python scripts/manager_structure_reroute.py --template templates/manager_structure.json --backup C:/backup/manager-structure-before.json
  python scripts/manager_structure_reroute.py --url http://127.0.0.1:41731 --backup C:/backup/manager-structure-before.json
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from manager_structure_template import Client, portable  # noqa: E402

from minimal_kanban.services.manager_structure_routing import (  # noqa: E402
    route_conflicts,
    route_diagram,
    route_intersections,
)


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="manager-reroute-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--template", type=Path, help="Portable JSON template to reroute")
    source.add_argument("--url", help="Exact local CRM base URL")
    parser.add_argument(
        "--backup", type=Path, required=True, help="New backup file outside the repository"
    )
    args = parser.parse_args(argv)
    backup = args.backup.resolve()
    if backup.is_relative_to(ROOT):
        parser.error("Резервная копия должна быть вне репозитория.")
    if backup.exists():
        parser.error("Резервная копия уже существует; выберите новое имя.")
    if args.template:
        target = args.template.resolve()
        if target == backup:
            parser.error("Резервная копия и схема должны быть разными файлами.")
        original = json.loads(target.read_text(encoding="utf-8"))
        client = None
    else:
        if not args.url.startswith("http://127.0.0.1:") and not args.url.startswith(
            "http://localhost:"
        ):
            parser.error("Скрипт принимает только локальный адрес CRM.")
        client = Client(
            args.url,
            os.environ.get("AUTOSTOP_MANAGER_STRUCTURE_SESSION", ""),
            os.environ.get("AUTOSTOP_MANAGER_STRUCTURE_BEARER", ""),
        )
        original = client.read()
        target = None
    before = portable(original)
    backup.parent.mkdir(parents=True, exist_ok=True)
    with backup.open("x", encoding="utf-8") as handle:
        json.dump(before, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    updated = route_diagram(copy.deepcopy(before))
    crossings = route_intersections(updated)
    conflicts = route_conflicts(updated)
    if conflicts:
        raise RuntimeError(f"Маршруты конфликтуют: {conflicts[:3]}")
    if target:
        save_json(target, updated)
        saved = json.loads(target.read_text(encoding="utf-8"))
    else:
        client.apply(original["version"], "replace", diagram=updated)
        saved = portable(client.read())
    if saved != updated:
        raise RuntimeError("Повторное чтение не подтвердило сохранение схемы.")
    print(
        f"Перестроено связей: {len(updated['relations'])}; пересечений с дугой: {len(crossings)}; резервная копия: {backup}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
