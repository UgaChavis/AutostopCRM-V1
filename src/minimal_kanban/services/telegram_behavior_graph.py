"""The shared, owner-edited Telegram agent behavior diagram."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from ..config import get_telegram_behavior_owner_login
from ..web_assets import TELEGRAM_AGENT_BEHAVIOR
from .errors import ServiceError

SETTING_KEY = "telegram_agent_behavior"
SCHEMA_VERSION = "autostopcrm.telegram-agent-behavior.v2"
_TONES = frozenset({"A", "B", "E", "agent"})
_MAX_GRAPH_BYTES = 2 * 1024 * 1024


def _invalid(message: str) -> None:
    raise ServiceError("validation_error", message, status_code=400)


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _invalid(f"{name}: требуется целое число от {minimum} до {maximum}.")
    return value


def _text(value: Any, name: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        _invalid(f"{name}: некорректный текст (до {maximum} символов).")
    return value


def _keys(
    value: Any,
    name: str,
    required: set[str],
    optional: set[str] | frozenset[str] = frozenset(),
) -> dict:
    if (
        not isinstance(value, dict)
        or not required <= value.keys()
        or value.keys() - required - optional
    ):
        _invalid(f"{name}: некорректный набор полей.")
    return value


def validate_graph(graph: Any) -> dict[str, Any]:
    """Return a JSON-safe, strict v2 copy. No SVG paths or client-owned markup."""

    graph = _keys(graph, "graph", {"schema_version", "canvas", "elements", "relations"})
    if graph["schema_version"] != SCHEMA_VERSION:
        _invalid("graph.schema_version: неподдерживаемая версия схемы.")
    canvas = _keys(graph["canvas"], "canvas", {"width", "height"})
    canvas_width = _integer(canvas["width"], "canvas.width", 500, 10000)
    canvas_height = _integer(canvas["height"], "canvas.height", 400, 10000)
    elements = graph["elements"]
    relations = graph["relations"]
    if not isinstance(elements, list) or len(elements) > 200:
        _invalid("elements: допускается не более 200 модулей.")
    if not isinstance(relations, list) or len(relations) > 400:
        _invalid("relations: допускается не более 400 связей.")

    clean_elements: list[dict[str, Any]] = []
    clean_relations: list[dict[str, Any]] = []
    element_ids: set[str] = set()
    all_ids: set[str] = set()
    for index, raw in enumerate(elements):
        name = f"elements[{index}]"
        element = _keys(
            raw,
            name,
            {"id", "title", "description", "x", "y", "width", "height"},
            {"tone"},
        )
        element_id = _text(element["id"], f"{name}.id", 80, required=True)
        if element_id != element_id.strip() or element_id in all_ids:
            _invalid(f"{name}.id: повтор или лишние пробелы.")
        all_ids.add(element_id)
        element_ids.add(element_id)
        x = _integer(element["x"], f"{name}.x", 0, canvas_width)
        y = _integer(element["y"], f"{name}.y", 0, canvas_height)
        width = _integer(element["width"], f"{name}.width", 140, 2000)
        height = _integer(element["height"], f"{name}.height", 90, 1200)
        if x + width > canvas_width or y + height > canvas_height:
            _invalid(f"{name}: модуль выходит за пределы холста.")
        clean: dict[str, Any] = {
            "id": element_id,
            "title": _text(element["title"], f"{name}.title", 120, required=True),
            "description": _text(element["description"], f"{name}.description", 10000),
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }
        if "tone" in element:
            tone = element["tone"]
            if not isinstance(tone, str) or tone not in _TONES:
                _invalid(f"{name}.tone: неизвестный цвет.")
            clean["tone"] = tone
        clean_elements.append(clean)

    for index, raw in enumerate(relations):
        name = f"relations[{index}]"
        relation = _keys(raw, name, {"id", "from", "to", "label", "description"}, {"tone"})
        relation_id = _text(relation["id"], f"{name}.id", 80, required=True)
        if relation_id != relation_id.strip() or relation_id in all_ids:
            _invalid(f"{name}.id: повтор или лишние пробелы.")
        all_ids.add(relation_id)
        source = _text(relation["from"], f"{name}.from", 80, required=True)
        target = _text(relation["to"], f"{name}.to", 80, required=True)
        if source == target or source not in element_ids or target not in element_ids:
            _invalid(f"{name}: связь должна соединять два разных существующих модуля.")
        clean = {
            "id": relation_id,
            "from": source,
            "to": target,
            "label": _text(relation["label"], f"{name}.label", 120),
            "description": _text(relation["description"], f"{name}.description", 10000),
        }
        if "tone" in relation:
            tone = relation["tone"]
            if not isinstance(tone, str) or tone not in _TONES:
                _invalid(f"{name}.tone: неизвестный цвет.")
            clean["tone"] = tone
        clean_relations.append(clean)
    clean_graph = {
        "schema_version": SCHEMA_VERSION,
        "canvas": {"width": canvas_width, "height": canvas_height},
        "elements": clean_elements,
        "relations": clean_relations,
    }
    if (
        len(json.dumps(clean_graph, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        > _MAX_GRAPH_BYTES
    ):
        _invalid("graph: размер схемы превышает 2 МиБ.")
    return clean_graph


def initial_graph() -> dict[str, Any]:
    """Read the shipped diagram without writing the database on first view."""

    seed = deepcopy(TELEGRAM_AGENT_BEHAVIOR)
    if seed.get("schema_version") == SCHEMA_VERSION:
        return validate_graph(seed)
    if seed.get("schema_version") != "autostopcrm.telegram-agent-behavior.v1":
        raise ServiceError("graph_seed_invalid", "Начальная схема недоступна.", status_code=503)
    graph = {
        "schema_version": SCHEMA_VERSION,
        "canvas": seed.get("canvas"),
        "elements": [
            {
                key: item[key]
                for key in ("id", "title", "description", "x", "y", "width", "height", "tone")
                if key in item
            }
            for item in seed.get("elements", [])
        ],
        "relations": [
            {
                key: item[key]
                for key in ("id", "from", "to", "label", "description", "tone")
                if key in item
            }
            for item in seed.get("relations", [])
        ],
    }
    return validate_graph(graph)


def graph_from_settings(settings: dict[str, Any]) -> tuple[dict[str, Any], int]:
    stored = settings.get(SETTING_KEY)
    if stored is None:
        return initial_graph(), 0
    if not isinstance(stored, dict):
        raise ServiceError("graph_corrupted", "Сохранённая схема повреждена.", status_code=503)
    revision = stored.get("revision")
    if type(revision) is not int or revision < 1:
        raise ServiceError(
            "graph_corrupted", "Версия сохранённой схемы повреждена.", status_code=503
        )
    try:
        graph = validate_graph(stored.get("graph"))
    except ServiceError as exc:
        raise ServiceError(
            "graph_corrupted", "Сохранённая схема повреждена.", status_code=503
        ) from exc
    return graph, revision


class TelegramBehaviorGraphMixin:
    @staticmethod
    def _can_edit_telegram_agent_behavior(payload: dict | None) -> bool:
        session = (payload or {}).get("_operator_session")
        if not isinstance(session, dict):
            return False
        configured_owner = get_telegram_behavior_owner_login()
        return bool(
            configured_owner
            and session.get("is_admin") is True
            and session.get("service_identity") is not True
            and str(session.get("token") or "") not in {"", "service-identity"}
            and str(session.get("username") or "").strip().upper() == configured_owner
        )

    def get_telegram_agent_behavior(self, payload: dict | None = None) -> dict:
        with self._lock:
            bundle = self._store.read_bundle()
            graph, revision = graph_from_settings(bundle["settings"])
        return {
            "graph": graph,
            "revision": revision,
            "can_edit": self._can_edit_telegram_agent_behavior(payload),
        }

    def save_telegram_agent_behavior(self, payload: dict | None = None) -> dict:
        if not self._can_edit_telegram_agent_behavior(payload):
            raise ServiceError(
                "forbidden", "Изменять схему может только владелец CRM.", status_code=403
            )
        payload = payload or {}
        expected_revision = payload.get("expected_revision")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ServiceError("validation_error", "Нужна текущая версия схемы.", status_code=400)
        graph = validate_graph(payload.get("graph"))
        with self._lock:
            bundle = self._read_bundle_for_update()
            _, revision = graph_from_settings(bundle["settings"])
            if expected_revision != revision:
                raise ServiceError(
                    "revision_conflict",
                    "Схема изменена в другой вкладке. Обновите её перед сохранением.",
                    status_code=409,
                    details={"current_revision": revision},
                )
            next_revision = revision + 1
            settings = dict(bundle["settings"])
            settings[SETTING_KEY] = {"revision": next_revision, "graph": graph}
            events = bundle["events"]
            session = payload["_operator_session"]
            actor_name = str(session["username"])
            self._append_event(
                events,
                actor_name=actor_name,
                source="ui",
                action="telegram_agent_behavior_updated",
                message=f"{actor_name} обновил схему поведения Telegram-агента",
                card_id=None,
                details={
                    "before_revision": revision,
                    "after_revision": next_revision,
                    "element_count": len(graph["elements"]),
                    "relation_count": len(graph["relations"]),
                },
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                events=events,
                settings=settings,
                require_compare_and_swap=True,
            )
        return {"graph": graph, "revision": next_revision, "can_edit": True}
