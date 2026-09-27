"""The shared, owner-edited Telegram agent behavior diagram."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from ..config import get_telegram_behavior_owner_login
from ..models import utc_now_iso
from ..web_assets import TELEGRAM_AGENT_BEHAVIOR
from .errors import ServiceError

SETTING_KEY = "telegram_agent_behavior"
SCHEMA_VERSION = "autostopcrm.telegram-agent-behavior.v2"
_TONES = frozenset({"A", "agent", "B", "C", "D", "E", "F", "G", "H", "J", "N"})
_ICONS = frozenset(
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
        "message",
        "clock",
        "photo",
        "audio",
        "video",
        "brain",
        "decision",
        "note",
    }
)
_MAX_GRAPH_BYTES = 2 * 1024 * 1024
_HISTORY_LIMIT = 20
_RECEIPT_LIMIT = 100


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
            {"tone", "icon"},
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
        if "icon" in element:
            icon = element["icon"]
            if not isinstance(icon, str) or icon not in _ICONS:
                _invalid(f"{name}.icon: неизвестная иконка.")
            clean["icon"] = icon
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
                for key in (
                    "id",
                    "title",
                    "description",
                    "x",
                    "y",
                    "width",
                    "height",
                    "tone",
                    "icon",
                )
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


def _stored_metadata(settings: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    stored = settings.get(SETTING_KEY)
    if not isinstance(stored, dict):
        return [], []
    history = stored.get("history")
    receipts = stored.get("patch_receipts")
    return (
        [item for item in history if isinstance(item, dict)][-_HISTORY_LIMIT:]
        if isinstance(history, list)
        else [],
        [item for item in receipts if isinstance(item, dict)][-_RECEIPT_LIMIT:]
        if isinstance(receipts, list)
        else [],
    )


def _changed_ids(before: dict, after: dict, key: str) -> list[str]:
    old = {item["id"]: item for item in before[key]}
    new = {item["id"]: item for item in after[key]}
    return sorted(
        item_id for item_id in old.keys() | new.keys() if old.get(item_id) != new.get(item_id)
    )


def _history_entry(before: dict, after: dict, revision: int, actor: str, source: str) -> dict:
    elements = _changed_ids(before, after, "elements")
    relations = _changed_ids(before, after, "relations")
    canvas_changed = before["canvas"] != after["canvas"]
    fragments = [f"{len(elements)} модулей", f"{len(relations)} связей"]
    if canvas_changed:
        fragments.append("холст")
    return {
        "revision": revision,
        "timestamp": utc_now_iso(),
        "actor": actor,
        "source": source,
        "summary": "Изменено: " + ", ".join(fragments),
        "changed_elements": elements,
        "changed_relations": relations,
    }


def _patch_graph(graph: dict, operations: Any) -> dict:
    if not isinstance(operations, list) or not 1 <= len(operations) <= 50:
        _invalid("operations: требуется от 1 до 50 операций.")
    result = deepcopy(graph)
    element_fields = {"title", "description", "x", "y", "width", "height", "tone", "icon"}
    relation_fields = {"from", "to", "label", "description", "tone"}
    for index, raw in enumerate(operations):
        name = f"operations[{index}]"
        if not isinstance(raw, dict) or not isinstance(raw.get("op"), str):
            _invalid(f"{name}: неизвестная операция.")
        kind = raw["op"]
        if kind == "resize_canvas":
            op = _keys(raw, name, {"op", "width", "height"})
            result["canvas"] = {"width": op["width"], "height": op["height"]}
            continue
        if kind not in {
            "add_element",
            "update_element",
            "delete_element",
            "add_relation",
            "update_relation",
            "delete_relation",
        }:
            _invalid(f"{name}.op: неизвестная операция.")
        collection = "elements" if kind.endswith("element") else "relations"
        singular = "element" if collection == "elements" else "relation"
        if kind.startswith("add_"):
            op = _keys(raw, name, {"op", singular})
            item = op[singular]
            if not isinstance(item, dict):
                _invalid(f"{name}.{singular}: требуется объект.")
            result[collection].append(deepcopy(item))
            continue
        if kind.startswith("update_"):
            op = _keys(raw, name, {"op", "id", "changes"})
            changes = op["changes"]
            permitted = element_fields if collection == "elements" else relation_fields
            if not isinstance(changes, dict) or not changes or changes.keys() - permitted:
                _invalid(f"{name}.changes: недопустимые поля.")
            target_id = _text(op["id"], f"{name}.id", 80, required=True)
            target = next(
                (
                    item
                    for item in result[collection]
                    if isinstance(item, dict) and item.get("id") == target_id
                ),
                None,
            )
            if target is None:
                _invalid(f"{name}.id: элемент не найден.")
            target.update(changes)
            continue
        op = _keys(raw, name, {"op", "id"})
        target_id = _text(op["id"], f"{name}.id", 80, required=True)
        filtered = [
            item
            for item in result[collection]
            if not isinstance(item, dict) or item.get("id") != target_id
        ]
        if len(filtered) == len(result[collection]):
            _invalid(f"{name}.id: элемент не найден.")
        result[collection] = filtered
        if collection == "elements":
            result["relations"] = [
                item
                for item in result["relations"]
                if not isinstance(item, dict)
                or (item.get("from") != target_id and item.get("to") != target_id)
            ]
    return validate_graph(result)


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

    @staticmethod
    def _can_agent_patch_telegram_behavior(payload: dict | None) -> bool:
        """Only a signed OAuth owner subject on the trusted local Gateway may patch."""

        session = (payload or {}).get("_operator_session")
        configured_owner = get_telegram_behavior_owner_login()
        return bool(
            configured_owner
            and isinstance(session, dict)
            and session.get("service_identity") is True
            and session.get("token") == "service-identity"
            and session.get("is_admin") is True
            and str(session.get("audit_actor_name") or "").strip().upper() == configured_owner
            and (payload or {}).get("source") == "mcp_agent_gateway_v2"
        )

    def get_telegram_agent_behavior(self, payload: dict | None = None) -> dict:
        since_revision = (payload or {}).get("since_revision")
        if since_revision is not None:
            if (
                isinstance(since_revision, str)
                and since_revision.isascii()
                and since_revision.isdecimal()
            ):
                since_revision = int(since_revision)
            if type(since_revision) is not int or since_revision < 0:
                _invalid("since_revision: требуется неотрицательная версия схемы.")
        with self._lock:
            bundle = self._store.read_bundle()
            graph, revision = graph_from_settings(bundle["settings"])
            history, _ = _stored_metadata(bundle["settings"])
        unchanged = since_revision is not None and since_revision == revision
        response = {
            "revision": revision,
            "can_edit": self._can_edit_telegram_agent_behavior(payload),
            "history": list(reversed(history)),
            "unchanged": unchanged,
        }
        if not unchanged:
            response["graph"] = graph
        return response

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
            before_graph, revision = graph_from_settings(bundle["settings"])
            if expected_revision != revision:
                raise ServiceError(
                    "revision_conflict",
                    "Схема изменена в другой вкладке. Обновите её перед сохранением.",
                    status_code=409,
                    details={"current_revision": revision},
                )
            next_revision = revision + 1
            settings = dict(bundle["settings"])
            history, receipts = _stored_metadata(settings)
            events = bundle["events"]
            session = payload["_operator_session"]
            actor_name = str(session["username"])
            change = _history_entry(before_graph, graph, next_revision, actor_name, "owner")
            history = (history + [change])[-_HISTORY_LIMIT:]
            settings[SETTING_KEY] = {
                "revision": next_revision,
                "graph": graph,
                "history": history,
                "patch_receipts": receipts,
            }
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
                    "changed_elements": change["changed_elements"],
                    "changed_relations": change["changed_relations"],
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
        return {
            "graph": graph,
            "revision": next_revision,
            "can_edit": True,
            "history": list(reversed(history)),
        }

    def patch_telegram_agent_behavior(self, payload: dict | None = None) -> dict:
        if not self._can_agent_patch_telegram_behavior(payload):
            raise ServiceError(
                "forbidden", "Правка схемы агентом требует OAuth-входа владельца.", status_code=403
            )
        payload = _keys(
            payload,
            "patch",
            {"expected_revision", "idempotency_key", "operations", "source", "_operator_session"},
        )
        expected_revision = payload["expected_revision"]
        if type(expected_revision) is not int or expected_revision < 0:
            _invalid("expected_revision: нужна текущая версия схемы.")
        idempotency_key = _text(payload["idempotency_key"], "idempotency_key", 128, required=True)
        if idempotency_key != idempotency_key.strip():
            _invalid("idempotency_key: лишние пробелы.")
        operations = payload["operations"]
        if not isinstance(operations, list) or not 1 <= len(operations) <= 50:
            _invalid("operations: требуется от 1 до 50 операций.")
        try:
            encoded = json.dumps(
                {"expected_revision": expected_revision, "operations": operations},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ServiceError(
                "validation_error", "Некорректный пакет операций.", status_code=400
            ) from exc
        fingerprint = hashlib.sha256(encoded).hexdigest()
        actor_name = str(payload["_operator_session"]["audit_actor_name"])
        with self._lock:
            bundle = self._read_bundle_for_update()
            before_graph, revision = graph_from_settings(bundle["settings"])
            history, receipts = _stored_metadata(bundle["settings"])
            for receipt in receipts:
                if receipt.get("key") != idempotency_key:
                    continue
                if receipt.get("fingerprint") != fingerprint or receipt.get("actor") != actor_name:
                    raise ServiceError(
                        "idempotency_conflict",
                        "Ключ повтора уже применён к другой правке.",
                        status_code=409,
                        details={"current_revision": revision},
                    )
                return {
                    "graph": before_graph,
                    "revision": revision,
                    "can_edit": False,
                    "history": list(reversed(history)),
                    "applied_revision": receipt["revision"],
                    "idempotent_replay": True,
                }
            if expected_revision != revision:
                raise ServiceError(
                    "revision_conflict",
                    "Схема изменилась. Прочитайте её перед правкой.",
                    status_code=409,
                    details={"current_revision": revision},
                )
            graph = _patch_graph(before_graph, operations)
            next_revision = revision + 1
            change = _history_entry(
                before_graph, graph, next_revision, f"Агент ({actor_name})", "agent"
            )
            history = (history + [change])[-_HISTORY_LIMIT:]
            receipts = (
                receipts
                + [
                    {
                        "key": idempotency_key,
                        "fingerprint": fingerprint,
                        "actor": actor_name,
                        "revision": next_revision,
                    }
                ]
            )[-_RECEIPT_LIMIT:]
            settings = dict(bundle["settings"])
            settings[SETTING_KEY] = {
                "revision": next_revision,
                "graph": graph,
                "history": history,
                "patch_receipts": receipts,
            }
            events = bundle["events"]
            self._append_event(
                events,
                actor_name=f"Агент ({actor_name})",
                source="mcp",
                action="telegram_agent_behavior_updated",
                message=f"Агент обновил схему поведения Telegram-агента по OAuth-входу {actor_name}",
                card_id=None,
                details={
                    "before_revision": revision,
                    "after_revision": next_revision,
                    "element_count": len(graph["elements"]),
                    "relation_count": len(graph["relations"]),
                    "changed_elements": change["changed_elements"],
                    "changed_relations": change["changed_relations"],
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
        return {
            "graph": graph,
            "revision": next_revision,
            "can_edit": False,
            "history": list(reversed(history)),
            "applied_revision": next_revision,
            "idempotent_replay": False,
        }
