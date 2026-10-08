"""Durable, versioned manager structure diagram shared by HTTP and MCP."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any

from ..config import get_telegram_behavior_owner_login
from ..storage.file_lock import ProcessFileLock
from .errors import ServiceError
from .manager_structure_routing import (
    RouteUnavailable,
    RoutingTimeout,
    _anchor_from_point,
    _anchor_point,
    _manual_routes_clear,
    _parse_svg_path,
    _points_from_path,
    _reanchor_path,
    _route_endpoints,
    route_conflicts,
    route_diagram,
)
from .manager_tool_catalog import bundle_metadata, installed_metadata, load_bundle
from .manager_tool_status import (
    apply_status,
    durable_digest,
    status_scope_digest,
    validate_statuses,
)
from .telegram_behavior_graph import SETTING_KEY, graph_from_settings

SCHEMA = "autostopcrm.manager-structure.v1"
ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}\Z")
COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}\Z")
PATH_RE = re.compile(r"[MmLlHhVvQqCcSsTtZz0-9., +\-]+\Z")
NODE_FIELDS = frozenset(
    {
        "id",
        "title",
        "description",
        "instruction",
        "x",
        "y",
        "width",
        "height",
        "parent",
        "group",
        "tone",
        "color",
        "kind",
        "lines",
        "icon",
        "compact",
        "indicator",
        "indicator_mode",
        "indicator_state",
    }
)
EDGE_FIELDS = frozenset(
    {
        "id",
        "from",
        "to",
        "label",
        "description",
        "protocol",
        "kind",
        "direction",
        "path",
        "route_mode",
        "from_anchor",
        "to_anchor",
        "label_mode",
        "label_x",
        "label_y",
        "show_label",
        "compact_label",
        "label_max_width",
        "auto_hidden_label",
        "tone",
        "color",
    }
)
ICON_NAMES = frozenset(
    {
        "terminal",
        "document",
        "telegram",
        "person",
        "people",
        "bell",
        "gateway",
        "gear",
        "book",
        "database",
        "car",
        "cloud",
        "globe",
        "camera",
        "plug",
        "cart",
        "box",
        "check",
        "chart",
        "message",
        "mail",
        "folder",
        "spark",
        "clock",
        "photo",
        "audio",
        "video",
        "brain",
        "decision",
        "note",
        "",
    }
)


def _bad(message: str) -> None:
    raise ServiceError("manager_structure_invalid", message, status_code=422)


def _text(value: Any, name: str, limit: int, *, required: bool = False) -> None:
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        _bad(f"Некорректное поле {name}.")


def _number(value: Any, name: str, minimum: int, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not minimum <= value <= maximum
    ):
        _bad(f"Некорректное поле {name}.")


def _validate(diagram: dict[str, Any], *, check_attachment: bool = True) -> None:
    validate_statuses(diagram)
    if diagram.get("schema_version") != SCHEMA:
        _bad("Неизвестная версия формата схемы.")
    canvas = diagram.get("canvas")
    if not isinstance(canvas, dict) or set(canvas) != {"width", "height"}:
        _bad("Некорректный холст.")
    for name in ("width", "height"):
        _number(canvas[name], f"canvas.{name}", 320, 10000)
    elements, relations = diagram.get("elements"), diagram.get("relations")
    if (
        not isinstance(elements, list)
        or len(elements) > 250
        or not isinstance(relations, list)
        or len(relations) > 500
    ):
        _bad("Превышен предел элементов или связей.")
    ids: set[str] = set()
    nodes: dict[str, dict] = {}
    for node in elements:
        if not isinstance(node, dict) or set(node) - NODE_FIELDS:
            _bad("Некорректные свойства модуля.")
        ident = node.get("id")
        if not isinstance(ident, str) or not ID_RE.fullmatch(ident) or ident in ids:
            _bad("ID модуля должен быть уникальным.")
        ids.add(ident)
        nodes[ident] = node
        for field, limit in (
            ("title", 160),
            ("description", 10000),
            ("instruction", 30000),
            ("group", 32),
            ("tone", 32),
        ):
            _text(node.get(field, ""), field, limit, required=field == "title")
        if node.get("kind") not in {"module", "item", "storage", "condition"}:
            _bad("Неизвестный вид модуля.")
        for field in ("x", "y", "width", "height"):
            _number(node.get(field), field, 24 if field in {"width", "height"} else 0, 10000)
        if (
            node["x"] + node["width"] > canvas["width"]
            or node["y"] + node["height"] > canvas["height"]
        ):
            _bad(f"Модуль {ident} выходит за пределы холста.")
        if node.get("parent") is not None and not isinstance(node["parent"], str):
            _bad("Некорректный родитель модуля.")
        if node.get("icon", "") not in ICON_NAMES:
            _bad("Неизвестная иконка.")
        if "color" in node and (
            not isinstance(node["color"], str) or not COLOR_RE.fullmatch(node["color"])
        ):
            _bad("Цвет задаётся в формате #RRGGBB.")
        if "compact" in node and not isinstance(node["compact"], bool):
            _bad("Некорректный компактный режим.")
        if node.get("indicator", "off") not in {"off", "green", "yellow", "red"}:
            _bad("Индикатор должен быть выключен, зелёным, жёлтым или красным.")
        if node.get("indicator_mode", "none") not in {"none", "manual", "automation"}:
            _bad("Некорректный режим индикатора.")
        if node.get("indicator_state", "green") not in {"green", "yellow", "red"}:
            _bad("Некорректный цвет индикатора.")
        lines = node.get("lines", [])
        if (
            not isinstance(lines, list)
            or len(lines) > 4
            or any(not isinstance(line, str) or len(line) > 140 for line in lines)
        ):
            _bad("Некорректные короткие подписи.")
    for node in elements:
        parent_id = node.get("parent")
        if parent_id:
            parent = nodes.get(parent_id)
            if parent is None or parent_id == node["id"] or parent.get("parent"):
                _bad("Вложенный модуль должен иметь внешний модуль родителем.")
            # Parent is a logical hierarchy: a compact satellite can be placed
            # outside its parent card (D5 in the retained reference map).
    for edge in relations:
        if not isinstance(edge, dict) or set(edge) - EDGE_FIELDS:
            _bad("Некорректные свойства связи.")
        ident = edge.get("id")
        if not isinstance(ident, str) or not ID_RE.fullmatch(ident) or ident in ids:
            _bad("ID связи должен быть уникальным.")
        ids.add(ident)
        if edge.get("from") not in nodes or edge.get("to") not in nodes:
            _bad(f"Связь {ident} ссылается на отсутствующий модуль.")
        for field, limit in (
            ("label", 200),
            ("description", 10000),
            ("protocol", 200),
            ("tone", 32),
        ):
            _text(edge.get(field, ""), field, limit)
        if edge.get("kind") not in {"exchange", "event"} or edge.get("direction") not in {
            "forward",
            "reverse",
            "both",
            "none",
        }:
            _bad("Некорректный вид или направление связи.")
        if edge.get("route_mode", "auto") not in {"auto", "manual"}:
            _bad("Маршрут должен быть автоматическим или ручным.")
        if edge.get("label_mode", "auto") not in {"auto", "manual"}:
            _bad("Положение подписи должно быть автоматическим или ручным.")
        path = edge.get("path")
        if (
            not isinstance(path, str)
            or len(path) > 2000
            or not PATH_RE.fullmatch(path)
            or _parse_svg_path(path) is None
        ):
            _bad("Некорректный SVG-маршрут связи.")
        for field in ("from_anchor", "to_anchor"):
            anchor = edge.get(field)
            if anchor is None:
                continue
            if not isinstance(anchor, dict) or set(anchor) != {"side", "offset"}:
                _bad("Точка крепления задаётся стороной и смещением.")
            if anchor["side"] not in {"auto", "left", "right", "top", "bottom"}:
                _bad("Неизвестная сторона точки крепления.")
            if (
                isinstance(anchor["offset"], bool)
                or not isinstance(anchor["offset"], (int, float))
                or not 0 <= anchor["offset"] <= 1
            ):
                _bad("Смещение точки крепления должно быть от 0 до 1.")
            if edge.get("route_mode", "auto") == "manual" and anchor["side"] == "auto":
                _bad("Для ручного маршрута задайте сторону точки крепления.")
        if edge.get("route_mode", "auto") == "manual":
            parsed = _parse_svg_path(path)
            coordinates = [parsed[0]]
            for segment in parsed[1]:
                if segment["command"] == "Q":
                    coordinates.append(tuple(segment["values"][:2]))
                elif segment["command"] == "C":
                    coordinates.extend(
                        (tuple(segment["values"][:2]), tuple(segment["values"][2:4]))
                    )
                coordinates.append(segment["end"])
            if any(
                not 0 <= x <= diagram["canvas"]["width"]
                or not 0 <= y <= diagram["canvas"]["height"]
                for x, y in coordinates
            ):
                _bad("Ручной маршрут выходит за пределы холста.")
            start, end = parsed[0], parsed[1][-1]["end"]
            if check_attachment:
                for endpoint, point in (("from", start), ("to", end)):
                    anchor = edge.get(f"{endpoint}_anchor")
                    if anchor is None:
                        continue
                    expected = _anchor_point(nodes[edge[endpoint]], anchor)
                    if abs(point[0] - expected[0]) > 0.01 or abs(point[1] - expected[1]) > 0.01:
                        _bad("Концы ручного маршрута должны совпадать с точками крепления.")
        for field in ("label_x", "label_y"):
            _number(edge.get(field), field, 0, 10000)
        if "label_max_width" in edge:
            _number(edge["label_max_width"], "label_max_width", 40, 800)
        for field in ("show_label", "compact_label", "auto_hidden_label"):
            if field in edge and not isinstance(edge[field], bool):
                _bad(f"Некорректное поле {field}.")
        if "color" in edge and (
            not isinstance(edge["color"], str) or not COLOR_RE.fullmatch(edge["color"])
        ):
            _bad("Цвет задаётся в формате #RRGGBB.")


class ManagerStructureService:
    def __init__(
        self,
        state_file: Path,
        legacy_settings_loader: Callable[[], dict[str, Any]] | None = None,
        tool_catalog_loader: Callable[[], dict[str, Any]] = load_bundle,
    ) -> None:
        self._file = state_file
        self._lock = ProcessFileLock(state_file.with_suffix(".lock"))
        self._legacy_settings_loader = legacy_settings_loader
        self._tool_catalog_loader = tool_catalog_loader

    @staticmethod
    def _owner_login() -> str:
        return (
            os.environ.get("AUTOSTOP_MANAGER_STRUCTURE_OWNER_LOGIN", "").strip().upper()
            or get_telegram_behavior_owner_login()
        )

    @classmethod
    def _can_edit(cls, payload: dict | None) -> bool:
        session = (payload or {}).get("_operator_session")
        owner = cls._owner_login()
        if not owner or not isinstance(session, dict) or session.get("is_admin") is not True:
            return False
        if session.get("service_identity") is True:
            return bool(
                session.get("token") == "service-identity"
                and str(session.get("audit_actor_name") or "").strip().upper() == owner
                and (payload or {}).get("source") == "mcp_agent_gateway_v2"
            )
        return bool(
            session.get("service_identity") is not True
            and str(session.get("token") or "") not in {"", "service-identity"}
            and str(session.get("username") or "").strip().upper() == owner
        )

    @classmethod
    def _require_editor(cls, payload: dict | None) -> None:
        if not cls._can_edit(payload):
            raise ServiceError(
                "manager_structure_owner_required",
                "Изменять структуру может только владелец CRM.",
                status_code=403,
            )

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "version": 0,
            "schema_version": SCHEMA,
            "canvas": {"width": 3200, "height": 1800},
            "elements": [],
            "relations": [],
            "receipts": {},
        }

    @staticmethod
    def _prepare_manual_routes(diagram: dict[str, Any]) -> None:
        nodes = {node["id"]: node for node in diagram["elements"]}
        for edge in diagram["relations"]:
            if edge.get("route_mode", "auto") != "manual":
                continue
            endpoints = _route_endpoints(edge["path"])
            if endpoints is None:
                _bad("Некорректный SVG-маршрут связи.")
            for endpoint, point in zip(("from", "to"), endpoints):
                field = f"{endpoint}_anchor"
                anchor = edge.get(field)
                node = nodes.get(edge.get(endpoint))
                if node is None:
                    _bad("Ручной маршрут ссылается на отсутствующий модуль.")
                if anchor is None:
                    edge[field] = _anchor_from_point(node, point)
                elif not isinstance(anchor, dict) or set(anchor) != {"side", "offset"}:
                    _bad("Точка крепления задаётся стороной и смещением.")
                elif anchor.get("side") == "auto":
                    edge[field] = _anchor_from_point(node, point)
            start = _anchor_point(nodes[edge["from"]], edge["from_anchor"])
            end = _anchor_point(nodes[edge["to"]], edge["to_anchor"])
            edge["path"] = _reanchor_path(edge["path"], start, end)

    @staticmethod
    def _verify_manual_routes(diagram: dict[str, Any]) -> None:
        if not any(edge.get("route_mode") == "manual" for edge in diagram["relations"]):
            return
        if not _manual_routes_clear(diagram):
            _bad("Ручной маршрут проходит через постороннюю карточку.")
        if not all(
            _points_from_path(edge["path"]) is not None for edge in diagram["relations"]
        ) or route_conflicts(diagram, parallel_gap=10):
            _bad("Ручной маршрут пересекает или перекрывает существующую связь.")

    def _read(self) -> dict[str, Any]:
        if not self._file.exists():
            if self._legacy_settings_loader is not None:
                settings = self._legacy_settings_loader()
                if SETTING_KEY in settings:
                    graph, revision = graph_from_settings(settings)
                    data = self._from_legacy_graph(graph, revision)
                    _validate(data)
                    return data
            return self._empty()
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ServiceError(
                "manager_structure_corrupt",
                "Не удалось прочитать сохранённую схему.",
                status_code=500,
            ) from error
        if (
            not isinstance(data, dict)
            or type(data.get("version")) is not int
            or not isinstance(data.get("receipts"), dict)
        ):
            raise ServiceError(
                "manager_structure_corrupt", "Формат сохранённой схемы повреждён.", status_code=500
            )
        _validate(data)
        return data

    @classmethod
    def _from_legacy_graph(cls, graph: dict[str, Any], revision: int) -> dict[str, Any]:
        data = cls._empty()
        data["version"] = revision
        data["canvas"] = copy.deepcopy(graph["canvas"])
        palette = {
            "A": "#79b9d9",
            "agent": "#5ecde9",
            "B": "#73c4e8",
            "C": "#dfb461",
            "D": "#b497de",
            "E": "#ec8492",
            "F": "#8ed6ad",
            "G": "#68dba8",
            "H": "#d99bc9",
            "J": "#7adbb1",
            "N": "#a2c2d5",
        }
        nodes = {}
        for old in graph["elements"]:
            node = {
                **old,
                "kind": "module",
                "parent": None,
                "instruction": old["description"],
                "lines": [],
                "group": old.get("tone", "N"),
                "color": palette.get(old.get("tone"), "#a2c2d5"),
                "indicator": "off",
            }
            data["elements"].append(node)
            nodes[node["id"]] = node
        for old in graph["relations"]:
            source, target = nodes[old["from"]], nodes[old["to"]]
            x1 = source["x"] + source["width"]
            y1 = source["y"] + source["height"] // 2
            x2 = target["x"]
            y2 = target["y"] + target["height"] // 2
            middle = (x1 + x2) // 2
            data["relations"].append(
                {
                    **old,
                    "kind": "exchange",
                    "direction": "forward",
                    "path": f"M{x1} {y1} H{middle} V{y2} H{x2}",
                    "label_x": middle,
                    "label_y": min(y1, y2) + abs(y2 - y1) // 2,
                    "color": palette.get(old.get("tone"), source["color"]),
                    "show_label": True,
                }
            )
        return data

    @staticmethod
    def _public(data: dict[str, Any]) -> dict[str, Any]:
        return {key: copy.deepcopy(value) for key, value in data.items() if key != "receipts"}

    def read(self, payload: dict | None = None) -> dict[str, Any]:
        with self._lock.acquire():
            data = self._public(self._read())
        data["can_edit"] = self._can_edit(payload)
        try:
            data["catalog_metadata"] = (
                installed_metadata()
                if self._tool_catalog_loader is load_bundle
                else bundle_metadata(self._tool_catalog_loader())
            )
        except ServiceError:
            data["catalog_metadata"] = {"available": False}
        data["can_edit_tool_status"] = data["can_edit"] and data["catalog_metadata"]["available"]
        return data

    def tool_catalog(self, payload: dict | None = None) -> dict[str, Any]:
        return self._tool_catalog_loader()

    @staticmethod
    def _route_layout(
        data: dict[str, Any],
        previous: dict[str, Any],
        moved_id: str | None = None,
        changed_relation: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Find the nearest routeable position without changing stored state."""
        requested = next((n for n in data["elements"] if n["id"] == moved_id), None)
        original = next((n for n in previous["elements"] if n["id"] == moved_id), None)
        positions = [(0, 0)]
        if requested is not None:
            for radius in (12, 24, 36):
                positions.extend(
                    (dx, dy)
                    for dx, dy in (
                        (radius, 0),
                        (-radius, 0),
                        (0, radius),
                        (0, -radius),
                        (radius, radius),
                        (radius, -radius),
                        (-radius, radius),
                        (-radius, -radius),
                    )
                )
        failure = None
        blocked_by = None
        deadline = monotonic() + 2
        for dx, dy in positions:
            candidate = copy.deepcopy(data)
            if requested is not None:
                node = next(n for n in candidate["elements"] if n["id"] == moved_id)
                node["x"] = max(
                    0, min(candidate["canvas"]["width"] - node["width"], requested["x"] + dx)
                )
                node["y"] = max(
                    0, min(candidate["canvas"]["height"] - node["height"], requested["y"] + dy)
                )
                if (
                    original is not None
                    and any(
                        requested[field] != original[field]
                        for field in ("x", "y", "width", "height")
                    )
                    and all(
                        node[field] == original[field] for field in ("x", "y", "width", "height")
                    )
                ):
                    continue
                current_blocker = None
                for other in candidate["elements"]:
                    if (
                        other["id"] == node["id"]
                        or other["id"]
                        in {
                            node.get("parent"),
                        }
                        or node["id"] == other.get("parent")
                    ):
                        continue
                    if (
                        node["x"] < other["x"] + other["width"]
                        and node["x"] + node["width"] > other["x"]
                        and node["y"] < other["y"] + other["height"]
                        and node["y"] + node["height"] > other["y"]
                    ):
                        current_blocker = other["id"]
                        break
                if current_blocker:
                    blocked_by = current_blocker
                    continue
            try:
                route_diagram(
                    candidate,
                    previous=previous,
                    changed_node=moved_id,
                    changed_relation=changed_relation,
                    deadline=deadline,
                )
                _validate(candidate)
                return candidate, bool(dx or dy)
            except RouteUnavailable as error:
                failure = error
            except RoutingTimeout as error:
                raise ServiceError(
                    "manager_structure_routing_timeout",
                    str(error)
                    + (
                        " Изменение связи не сохранено."
                        if changed_relation
                        else " Перемещение не сохранено."
                    ),
                    status_code=422,
                    details={"relation_id": error.relation_id},
                ) from error
        raise ServiceError(
            "manager_structure_route_unavailable",
            (
                f"Не удалось провести связь {failure.relation_id} с допустимым зазором. "
                if failure
                else f"Модуль пересекает {blocked_by}. "
            )
            + (
                "Изменение связи не сохранено; она остаётся черновиком."
                if changed_relation
                else "Не найдена допустимая позиция рядом. Перемещение не сохранено."
            ),
            status_code=422,
        )

    def apply(self, payload: dict | None) -> dict[str, Any]:
        self._require_editor(payload)
        if not isinstance(payload, dict):
            _bad("Не задана операция.")
        expected = payload.get("expected_version")
        key = payload.get("idempotency_key")
        operation = payload.get("operation")
        preview = payload.get("preview", False)
        if not isinstance(preview, bool):
            _bad("Некорректный режим предварительного просмотра.")
        if (
            type(expected) is not int
            or expected < 0
            or not isinstance(key, str)
            or not 8 <= len(key) <= 128
        ):
            _bad("Нужны актуальная версия и ключ повторного запроса.")
        if operation not in {
            "upsert_element",
            "upsert_relation",
            "layout_element",
            "layout_relation",
            "remove_element",
            "remove_relation",
            "set_canvas",
            "replace",
            "reroute",
            "set_tool_status",
            "clear_tool_status",
        }:
            _bad("Неизвестная операция конструктора.")
        request = {
            field: payload.get(field)
            for field in (
                "operation",
                "element",
                "relation",
                "id",
                "canvas",
                "diagram",
                "tool_status",
            )
            if field in payload
        }
        if preview:
            request["preview"] = True
        digest = hashlib.sha256(
            json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self._lock.acquire():
            data = self._read()
            receipt = data["receipts"].get(key)
            if receipt:
                if receipt["digest"] != digest:
                    raise ServiceError(
                        "manager_structure_idempotency_conflict",
                        "Ключ уже использован для другого изменения.",
                        status_code=409,
                    )
                return {
                    **receipt.get("result", {"version": receipt["version"]}),
                    "deduplicated": True,
                }
            if expected != data["version"]:
                raise ServiceError(
                    "manager_structure_version_conflict",
                    "Схема изменилась. Прочитайте её повторно.",
                    status_code=409,
                    details={"current_version": data["version"]},
                )
            next_data = copy.deepcopy(data)
            status_operation = operation in {"set_tool_status", "clear_tool_status"}
            instruction_only = False
            if status_operation:
                apply_status(next_data, payload, self._tool_catalog_loader())
            elif operation == "replace":
                diagram = payload.get("diagram")
                if not isinstance(diagram, dict):
                    _bad("Нужен переносимый шаблон схемы.")
                for field in ("schema_version", "canvas", "elements", "relations"):
                    next_data[field] = copy.deepcopy(diagram.get(field))
            elif operation == "reroute":
                pass
            elif operation == "set_canvas":
                next_data["canvas"] = copy.deepcopy(payload.get("canvas"))
            elif operation in {
                "upsert_element",
                "upsert_relation",
                "layout_element",
                "layout_relation",
            }:
                field, singular = (
                    ("elements", "element")
                    if operation in {"upsert_element", "layout_element"}
                    else ("relations", "relation")
                )
                item = payload.get(singular)
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    _bad("Нужен модуль или связь с ID.")
                items = next_data[field]
                match = next(
                    (index for index, old in enumerate(items) if old["id"] == item["id"]), None
                )
                if match is None:
                    new_item = copy.deepcopy(item)
                    if operation == "layout_relation":
                        new_item.setdefault("path", "M0 0 L0 0")
                        new_item.setdefault("label_x", 0)
                        new_item.setdefault("label_y", 0)
                    items.append(new_item)
                else:
                    instruction_only = operation == "upsert_element" and set(item) == {
                        "id",
                        "instruction",
                    }
                    items[match] = {**items[match], **copy.deepcopy(item)}
            else:
                field = "elements" if operation == "remove_element" else "relations"
                ident = payload.get("id")
                if not isinstance(ident, str) or not any(
                    item["id"] == ident for item in next_data[field]
                ):
                    _bad("Элемент для удаления не найден.")
                if field == "elements" and (
                    any(item.get("parent") == ident for item in next_data["elements"])
                    or any(
                        item["from"] == ident or item["to"] == ident
                        for item in next_data["relations"]
                    )
                ):
                    _bad("Сначала удалите вложенные модули и связи.")
                next_data[field] = [item for item in next_data[field] if item["id"] != ident]
            _validate(next_data, check_attachment=False)
            if not status_operation and operation != "replace" and not instruction_only:
                self._prepare_manual_routes(next_data)
            _validate(next_data)
            adjusted = False
            if operation == "reroute":
                try:
                    route_diagram(next_data)
                    _validate(next_data)
                except (RouteUnavailable, RoutingTimeout) as error:
                    raise ServiceError(
                        "manager_structure_route_unavailable"
                        if isinstance(error, RouteUnavailable)
                        else "manager_structure_routing_timeout",
                        str(error),
                        status_code=422,
                        details={"relation_id": error.relation_id},
                    ) from error
            if operation in {"layout_element", "layout_relation"}:
                next_data, adjusted = self._route_layout(
                    next_data,
                    data,
                    item["id"]
                    if operation == "layout_element"
                    and any(field in item for field in ("x", "y", "width", "height"))
                    else None,
                    item["id"] if operation == "layout_relation" else None,
                )
            if not status_operation and not instruction_only:
                self._verify_manual_routes(next_data)
            if preview:
                return {
                    "version": data["version"],
                    "diagram": self._public(next_data),
                    "adjusted": adjusted,
                    "preview": True,
                    "saved_digest": durable_digest(data),
                    "routes": {
                        relation["id"]: relation["path"] for relation in next_data["relations"]
                    },
                    "labels": {
                        relation["id"]: {
                            field: relation.get(field)
                            for field in ("label_x", "label_y", "auto_hidden_label")
                        }
                        for relation in next_data["relations"]
                    },
                }
            next_data["version"] += 1
            result = {
                "version": next_data["version"],
                "deduplicated": False,
                "adjusted": adjusted,
            }
            if status_operation:
                ident = payload["tool_status"]["operation_id"]
                result["tool_status"] = copy.deepcopy(next_data["tool_statuses"].get(ident))
                result["unchanged_scope_digest"] = status_scope_digest(data, ident)
            if operation in {"upsert_relation", "layout_relation"}:
                accepted_relation = next(
                    relation for relation in next_data["relations"] if relation["id"] == item["id"]
                )
                result["accepted_relation"] = copy.deepcopy(accepted_relation)
            if operation in {"layout_element", "layout_relation", "reroute"}:
                result["routes"] = {
                    relation["id"]: relation["path"] for relation in next_data["relations"]
                }
                result["labels"] = {
                    relation["id"]: {
                        field: relation.get(field)
                        for field in ("label_x", "label_y", "auto_hidden_label")
                    }
                    for relation in next_data["relations"]
                }
            if operation == "layout_element":
                accepted = next(node for node in next_data["elements"] if node["id"] == item["id"])
                result["accepted_element"] = {
                    field: accepted[field] for field in ("id", "x", "y", "width", "height")
                }
            next_data["receipts"][key] = {
                "digest": digest,
                "version": next_data["version"],
                "result": result,
            }
            if len(next_data["receipts"]) > 1000:
                next_data["receipts"].pop(next(iter(next_data["receipts"])))
            self._file.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix="manager-structure-", suffix=".tmp", dir=self._file.parent
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(next_data, handle, ensure_ascii=False, separators=(",", ":"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, self._file)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            return result
