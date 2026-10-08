"""Build, export and restore a portable manager structure through the CRM API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "src/minimal_kanban/web_app_assets/source/manager_infrastructure.json"
DEFAULT_TEMPLATE = ROOT / "templates/manager_structure.json"
SCHEMA = "autostopcrm.manager-structure.v1"
PALETTE = {
    "A": "#79b9d9",
    "agent": "#5ecde9",
    "B": "#73c4e8",
    "C": "#dfb461",
    "D": "#b497de",
    "E": "#ec8492",
    "F": "#8ed6ad",
    "G": "#68dba8",
    "H": "#d99bc9",
    "I": "#e9a4b5",
    "J": "#7adbb1",
    "N": "#a2c2d5",
}
NODE_FIELDS = {
    "id",
    "title",
    "x",
    "y",
    "width",
    "height",
    "description",
    "instruction",
    "group",
    "tone",
    "kind",
    "lines",
    "icon",
    "parent",
    "compact",
    "indicator",
    "indicator_mode",
    "indicator_state",
    "color",
}
EDGE_FIELDS = {
    "id",
    "from",
    "to",
    "label",
    "protocol",
    "description",
    "path",
    "label_x",
    "label_y",
    "direction",
    "kind",
    "tone",
    "show_label",
    "compact_label",
    "label_max_width",
    "route_mode",
    "from_anchor",
    "to_anchor",
    "label_mode",
    "auto_hidden_label",
    "color",
}


class Client:
    def __init__(self, url: str, session: str, bearer: str = "") -> None:
        self.url = url.rstrip("/")
        self.session = session
        self.bearer = bearer

    def call(self, path: str, payload: dict | None = None) -> dict:
        headers = {"Accept": "application/json", "X-Operator-Session": self.session}
        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.url + path,
            headers=headers,
            data=data,
            method="POST" if data is not None else "GET",
        )
        try:
            with urlopen(request, timeout=20) as response:
                result = json.load(response)
        except HTTPError as error:
            try:
                result = json.load(error)
                message = result.get("error", {}).get("message", error.reason)
            except (ValueError, AttributeError):
                message = error.reason
            raise RuntimeError(f"HTTP {error.code}: {message}") from error
        if not result.get("ok"):
            raise RuntimeError(result.get("error", {}).get("message", "CRM rejected request"))
        return result["data"]

    def read(self) -> dict:
        return self.call("/api/manager_structure")

    def apply(self, version: int, operation: str, **fields) -> dict:
        return self.call(
            "/api/manager_structure/apply",
            {
                "operation": operation,
                "expected_version": version,
                "idempotency_key": str(uuid.uuid4()),
                **fields,
            },
        )


def portable(data: dict) -> dict:
    return {key: data[key] for key in ("schema_version", "canvas", "elements", "relations")}


def build_reference(client: Client) -> dict:
    current = client.read()
    if current["elements"] or current["relations"]:
        raise RuntimeError("Схема уже содержит данные; build-reference её не перезаписывает.")
    source = json.loads(REFERENCE.read_text(encoding="utf-8"))
    version = current["version"]
    if current["canvas"] != source["canvas"]:
        version = client.apply(version, "set_canvas", canvas=source["canvas"])["version"]
    nodes = source["elements"]
    for node in [
        *filter(lambda n: not n.get("parent"), nodes),
        *filter(lambda n: n.get("parent"), nodes),
    ]:
        item = {key: value for key, value in node.items() if key in NODE_FIELDS}
        item.setdefault("instruction", str(node.get("purpose") or node.get("description") or ""))
        item.setdefault("color", PALETTE.get(node.get("tone") or node.get("group"), "#91b8ca"))
        item.setdefault(
            "indicator",
            {"B4": "green", "G1": "yellow", "E10": "yellow", "E11": "red"}.get(node["id"], "off"),
        )
        version = client.apply(version, "upsert_element", element=item)["version"]
    for edge in source["relations"]:
        item = {key: value for key, value in edge.items() if key in EDGE_FIELDS}
        item.setdefault(
            "color",
            PALETTE.get(
                edge.get("tone")
                or next(n for n in nodes if n["id"] == edge["from"]).get("tone")
                or next(n for n in nodes if n["id"] == edge["from"]).get("group"),
                "#91b8ca",
            ),
        )
        version = client.apply(version, "upsert_relation", relation=item)["version"]
    result = client.read()
    if len(result["elements"]) != len(nodes) or len(result["relations"]) != len(
        source["relations"]
    ):
        raise RuntimeError("Повторное чтение не подтвердило количество элементов.")
    return result


def export(client: Client, path: Path) -> dict:
    result = client.read()
    template = portable(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def restore(client: Client, path: Path, *, replace_existing: bool = False) -> dict:
    current = client.read()
    if (current["elements"] or current["relations"]) and not replace_existing:
        raise RuntimeError(
            "Локальная схема уже содержит данные. Для явной замены передайте --replace-existing."
        )
    template = json.loads(path.read_text(encoding="utf-8"))
    if template.get("schema_version") != SCHEMA:
        raise RuntimeError("Неизвестная версия переносимого шаблона.")
    client.apply(current["version"], "replace", diagram=portable(template))
    result = client.read()
    if portable(result) != portable(template):
        raise RuntimeError("Повторное чтение не подтвердило восстановление шаблона.")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build-reference", "export", "restore"])
    parser.add_argument("--url", default="http://127.0.0.1:41731")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args(argv)
    session = os.environ.get("AUTOSTOP_MANAGER_STRUCTURE_SESSION", "")
    if not session:
        parser.error("Нужен AUTOSTOP_MANAGER_STRUCTURE_SESSION с токеном владельца локальной CRM.")
    client = Client(args.url, session, os.environ.get("AUTOSTOP_MANAGER_STRUCTURE_BEARER", ""))
    if args.command == "build-reference":
        result = build_reference(client)
    elif args.command == "export":
        result = export(client, args.template)
    else:
        result = restore(client, args.template, replace_existing=args.replace_existing)
    print(
        f"{args.command}: version={result['version']} modules={len(result['elements'])} relations={len(result['relations'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
