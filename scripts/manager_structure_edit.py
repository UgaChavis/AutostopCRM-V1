"""Make one reviewed manager-structure edit through the local CRM API.

Examples (set AUTOSTOP_MANAGER_STRUCTURE_SESSION first):
  python scripts/manager_structure_edit.py list
  python scripts/manager_structure_edit.py module M40 --title "Новый модуль" --x 100 --y 200
  python scripts/manager_structure_edit.py instruction M40 --file instruction.md
  python scripts/manager_structure_edit.py relation L40 --from M40 --to A2 --label "Запрос"
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from manager_structure_template import Client


def instruction_text(path: Path) -> str:
    if path.suffix.lower() not in {".txt", ".md"}:
        raise ValueError("Инструкция должна быть файлом .txt или .md.")
    if path.stat().st_size > 120_000:
        raise ValueError("Размер файла инструкции превышает 120 КБ.")
    text = path.read_text(encoding="utf-8-sig")
    if len(text) > 30_000:
        raise ValueError("Инструкция длиннее 30 000 символов.")
    return text


def route_for(source: dict, target: dict) -> dict:
    ax = source["x"] + source["width"] / 2
    ay = source["y"] + source["height"] / 2
    bx = target["x"] + target["width"] / 2
    by = target["y"] + target["height"] / 2
    midpoint = (ax + bx) / 2
    return {
        "path": f"M{ax:g} {ay:g} H{midpoint:g} V{by:g} H{bx:g}",
        "label_x": midpoint,
        "label_y": (ay + by) / 2,
    }


def find_item(items: list[dict], ident: str, name: str) -> dict:
    item = next((item for item in items if item["id"] == ident), None)
    if item is None:
        raise ValueError(f"{name} {ident} не найден.")
    return item


def apply_checked(client: Client, current: dict, operation: str, field: str, item: dict) -> int:
    result = client.apply(current["version"], operation, **{field: item})
    saved = client.read()
    collection = "elements" if field == "element" else "relations"
    actual = find_item(saved[collection], item["id"], "Элемент")
    if saved["version"] != result["version"] or any(
        actual.get(name) != value
        for name, value in item.items()
        if not (result.get("adjusted") and name in {"x", "y"})
    ):
        raise RuntimeError("Повторное чтение не подтвердило точное сохранение изменения.")
    return saved["version"]


def edit(client: Client, args: argparse.Namespace) -> str:
    current = client.read()
    if args.command == "list":
        lines = [
            f"Версия {current['version']}; модулей {len(current['elements'])}; связей {len(current['relations'])}"
        ]
        lines.extend(f"{item['id']}  {item['title']}" for item in current["elements"])
        lines.extend(
            f"{item['id']}  {item['from']} → {item['to']}  {item.get('label', '')}"
            for item in current["relations"]
        )
        return "\n".join(lines)

    if args.command == "instruction":
        find_item(current["elements"], args.id, "Модуль")
        body = {"id": args.id, "instruction": instruction_text(args.file)}
        version = apply_checked(client, current, "upsert_element", "element", body)
        return f"Модуль {args.id}: инструкция сохранена, {len(body['instruction'])} символов, версия {version}."

    if args.command == "module":
        previous = next((item for item in current["elements"] if item["id"] == args.id), None)
        names = (
            "title",
            "kind",
            "parent",
            "description",
            "x",
            "y",
            "width",
            "height",
            "color",
            "icon",
            "indicator_mode",
            "indicator_state",
        )
        body = {
            "id": args.id,
            **{name: getattr(args, name) for name in names if getattr(args, name) is not None},
        }
        if args.instruction_file is not None:
            body["instruction"] = instruction_text(args.instruction_file)
        if previous is None:
            if not body.get("title") or body.get("x") is None or body.get("y") is None:
                raise ValueError("Для нового модуля нужны --title, --x и --y.")
            nested = body.get("kind") == "item"
            body.setdefault("kind", "module")
            body.setdefault("width", 150 if nested else 280)
            body.setdefault("height", 48 if nested else 140)
            body.setdefault("color", "#79b9d9")
            body.setdefault("icon", "")
            body.setdefault("instruction", "")
        if body.get("parent"):
            find_item(current["elements"], body["parent"], "Родитель")
        geometric = previous is not None and any(
            name in body for name in ("x", "y", "width", "height")
        )
        version = apply_checked(
            client, current, "layout_element" if geometric else "upsert_element", "element", body
        )
        return f"Модуль {args.id} сохранён, версия {version}."

    if args.command == "relation":
        previous = next((item for item in current["relations"] if item["id"] == args.id), None)
        names = (
            "from_id",
            "to_id",
            "label",
            "description",
            "protocol",
            "kind",
            "direction",
            "color",
            "path",
        )
        body = {
            "id": args.id,
            **{
                ("from" if name == "from_id" else "to" if name == "to_id" else name): getattr(
                    args, name
                )
                for name in names
                if getattr(args, name) is not None
            },
        }
        source_id = body.get("from", previous.get("from") if previous else None)
        target_id = body.get("to", previous.get("to") if previous else None)
        if not source_id or not target_id:
            raise ValueError("Для новой связи нужны --from и --to.")
        find_item(current["elements"], source_id, "Модуль")
        find_item(current["elements"], target_id, "Модуль")
        if previous is None:
            body.update({"from": source_id, "to": target_id})
            body.setdefault("kind", "exchange")
            body.setdefault("direction", "forward")
            body.setdefault("color", "#91b8ca")
        reroute = previous is None or "from" in body or "to" in body or args.auto_path
        version = apply_checked(
            client,
            current,
            "layout_relation" if reroute and args.path is None else "upsert_relation",
            "relation",
            body,
        )
        return f"Связь {args.id} сохранена, версия {version}."

    raise ValueError("Неизвестная команда.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:41731", help="Точный адрес CRM")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Показать ID и названия без текстов инструкций")
    instruction = commands.add_parser("instruction", help="Заменить инструкцию текстом UTF-8 файла")
    instruction.add_argument("id")
    instruction.add_argument("--file", type=Path, required=True)
    module = commands.add_parser("module", help="Создать или точечно изменить модуль")
    module.add_argument("id")
    for name in (
        "title",
        "kind",
        "parent",
        "description",
        "color",
        "icon",
        "indicator_mode",
        "indicator_state",
    ):
        module.add_argument("--" + name.replace("_", "-"), dest=name)
    for name in ("x", "y", "width", "height"):
        module.add_argument("--" + name, type=int)
    module.add_argument("--instruction-file", type=Path)
    relation = commands.add_parser("relation", help="Создать или точечно изменить связь")
    relation.add_argument("id")
    for name in (
        "from_id",
        "to_id",
        "label",
        "description",
        "protocol",
        "kind",
        "direction",
        "color",
        "path",
    ):
        relation.add_argument("--" + name.replace("_id", "").replace("_", "-"), dest=name)
    relation.add_argument("--auto-path", action="store_true", help="Перестроить SVG-маршрут")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    session = os.environ.get("AUTOSTOP_MANAGER_STRUCTURE_SESSION", "")
    if not session:
        parser.error("Нужен AUTOSTOP_MANAGER_STRUCTURE_SESSION с сессией оператора CRM.")
    client = Client(args.url, session, os.environ.get("AUTOSTOP_MANAGER_STRUCTURE_BEARER", ""))
    try:
        print(edit(client, args))
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Ошибка: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
