from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.services.manager_structure import ManagerStructureService  # noqa: E402
from minimal_kanban.services.manager_structure_routing import (  # noqa: E402
    _blocked_by_node,
    _points_from_path,
    _segment_through_box,
    _segments,
    route_conflicts,
    route_intersections,
)
from minimal_kanban.services.telegram_behavior_graph import initial_graph  # noqa: E402


class ManagerStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        owner = patch.dict("os.environ", {"AUTOSTOP_MANAGER_STRUCTURE_OWNER_LOGIN": "ADMIN"})
        owner.start()
        self.addCleanup(owner.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "manager_structure.json"
        self.service = ManagerStructureService(self.path)

    def apply(self, version: int, key: str, operation: str, **body):
        return self.service.apply(
            {
                "expected_version": version,
                "idempotency_key": key,
                "operation": operation,
                "_operator_session": {
                    "is_admin": True,
                    "username": "admin",
                    "token": "local-test-session",
                },
                **body,
            }
        )

    def test_nested_instructions_relations_reload_and_guards(self) -> None:
        outer = {
            "id": "M1",
            "title": "Менеджер",
            "kind": "module",
            "x": 10,
            "y": 10,
            "width": 300,
            "height": 220,
            "instruction": "Полная инструкция владельца",
            "lines": ["Краткая подпись"],
            "icon": "person",
            "color": "#79b9d9",
        }
        child = {
            "id": "M2",
            "title": "Запись клиента",
            "kind": "item",
            "parent": "M1",
            "x": 30,
            "y": 100,
            "width": 180,
            "height": 50,
            "instruction": "Детальная инструкция вложенного блока",
            "lines": [],
            "icon": "document",
            "color": "#68dba8",
            "compact": True,
        }
        edge = {
            "id": "R1",
            "from": "M1",
            "to": "M2",
            "kind": "exchange",
            "direction": "both",
            "path": "M160 80 V100",
            "label": "Передача",
            "label_x": 180,
            "label_y": 80,
            "show_label": True,
            "color": "#dfb461",
        }
        self.assertEqual(self.apply(0, "outer-0001", "upsert_element", element=outer)["version"], 1)
        self.assertEqual(self.apply(1, "child-0001", "upsert_element", element=child)["version"], 2)
        self.assertEqual(
            self.apply(2, "edge-00001", "upsert_relation", relation=edge)["version"], 3
        )
        restarted = ManagerStructureService(self.path)
        data = restarted.read()
        self.assertEqual(data["elements"][0]["instruction"], outer["instruction"])
        self.assertEqual(data["elements"][1]["instruction"], child["instruction"])
        self.assertEqual(data["relations"][0]["direction"], "both")
        with self.assertRaisesRegex(ServiceError, "Схема изменилась"):
            self.apply(2, "stale-0001", "upsert_element", element={"id": "M2", "title": "Устарело"})
        self.assertTrue(
            restarted.apply(
                {
                    "expected_version": 0,
                    "idempotency_key": "edge-00001",
                    "operation": "upsert_relation",
                    "relation": edge,
                    "_operator_session": {
                        "is_admin": True,
                        "username": "admin",
                        "token": "local-test-session",
                    },
                }
            )["deduplicated"]
        )
        with self.assertRaisesRegex(ServiceError, "Ключ уже использован"):
            self.apply(3, "edge-00001", "remove_relation", id="R1")
        with self.assertRaisesRegex(ServiceError, "Сначала удалите"):
            self.apply(3, "remove-001", "remove_element", id="M1")
        self.assertEqual(
            self.apply(
                3,
                "child-text-001",
                "upsert_element",
                element={"id": "M2", "instruction": "Новый полный текст"},
            )["version"],
            4,
        )
        self.assertEqual(
            ManagerStructureService(self.path).read()["elements"][1]["instruction"],
            "Новый полный текст",
        )

    def test_invalid_parent_and_unsafe_path_rejected_without_writing(self) -> None:
        node = {
            "id": "M1",
            "title": "Модуль",
            "kind": "module",
            "x": 0,
            "y": 0,
            "width": 200,
            "height": 100,
        }
        self.apply(0, "outer-0001", "upsert_element", element=node)
        with self.assertRaises(ServiceError):
            self.apply(
                1,
                "bad-child-001",
                "upsert_element",
                element={
                    "id": "M2",
                    "title": "Нет родителя",
                    "kind": "item",
                    "parent": "M9",
                    "x": 300,
                    "y": 0,
                    "width": 50,
                    "height": 40,
                },
            )
        with self.assertRaises(ServiceError):
            self.apply(
                1,
                "bad-edge-0001",
                "upsert_relation",
                relation={
                    "id": "R1",
                    "from": "M1",
                    "to": "M1",
                    "kind": "exchange",
                    "direction": "forward",
                    "path": 'M1 1" onload=alert(1)',
                    "label_x": 10,
                    "label_y": 10,
                },
            )
        self.assertEqual(self.service.read()["version"], 1)

    def test_saved_legacy_graph_is_copied_without_deleting_original(self) -> None:
        original = {"revision": 1, "graph": initial_graph()}
        original["graph"]["elements"][0]["icon"] = "clock"
        settings = {"telegram_agent_behavior": original}
        migrated = ManagerStructureService(self.path, legacy_settings_loader=lambda: settings)
        data = migrated.read()
        self.assertEqual(data["version"], 1)
        self.assertEqual(
            data["elements"][0]["instruction"], original["graph"]["elements"][0]["description"]
        )
        self.assertEqual(data["elements"][0]["icon"], "clock")
        self.assertEqual(len(data["relations"]), len(original["graph"]["relations"]))
        migrated.apply(
            {
                "operation": "upsert_element",
                "element": {"id": data["elements"][0]["id"], "instruction": "Новый текст"},
                "expected_version": 1,
                "idempotency_key": "migrate-001",
                "_operator_session": {
                    "is_admin": True,
                    "username": "admin",
                    "token": "local-test-session",
                },
            }
        )
        self.assertEqual(settings["telegram_agent_behavior"], original)
        self.assertTrue(self.path.exists())
        self.assertEqual(migrated.read()["elements"][0]["instruction"], "Новый текст")

    def test_unconfigured_or_other_admin_cannot_edit(self) -> None:
        with self.assertRaisesRegex(ServiceError, "только владелец"):
            self.service.apply(
                {
                    "operation": "set_canvas",
                    "canvas": {"width": 900, "height": 900},
                    "expected_version": 0,
                    "idempotency_key": "wrong-owner-001",
                    "_operator_session": {
                        "is_admin": True,
                        "username": "another",
                        "token": "admin-session",
                    },
                }
            )

    def test_layout_preview_and_atomic_reroute(self) -> None:
        nodes = [
            {
                "id": "M1",
                "title": "Отправитель",
                "kind": "module",
                "x": 40,
                "y": 80,
                "width": 160,
                "height": 80,
            },
            {
                "id": "M2",
                "title": "Получатель",
                "kind": "module",
                "x": 500,
                "y": 300,
                "width": 160,
                "height": 80,
            },
        ]
        self.apply(0, "layout-node-001", "upsert_element", element=nodes[0])
        self.apply(1, "layout-node-002", "upsert_element", element=nodes[1])
        self.apply(
            2,
            "layout-edge-001",
            "layout_relation",
            relation={
                "id": "R1",
                "from": "M1",
                "to": "M2",
                "kind": "exchange",
                "direction": "forward",
            },
        )
        before = self.service.read()
        self.assertEqual(route_intersections(before), [])
        preview = self.service.apply(
            {
                "operation": "layout_element",
                "element": {"id": "M2", "x": 600},
                "expected_version": before["version"],
                "idempotency_key": "layout-preview-001",
                "preview": True,
                "_operator_session": {
                    "is_admin": True,
                    "username": "admin",
                    "token": "local-test-session",
                },
            }
        )
        self.assertTrue(preview["preview"])
        self.assertEqual(self.service.read(), before)
        result = self.apply(
            before["version"], "layout-move-001", "layout_element", element={"id": "M2", "x": 600}
        )
        after = self.service.read()
        self.assertEqual(result["version"], before["version"] + 1)
        self.assertEqual(result["accepted_element"]["x"], 600)
        self.assertEqual(result["routes"], {"R1": after["relations"][0]["path"]})
        self.assertEqual(after["elements"][1]["x"], 600)
        self.assertNotEqual(after["relations"][0]["path"], before["relations"][0]["path"])
        self.assertEqual(route_intersections(after), [])

    def test_portable_reference_routes_are_separate(self) -> None:
        reference = json.loads(
            (
                Path(__file__).resolve().parents[1] / "templates" / "manager_structure.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(len(reference["elements"]), 40)
        self.assertEqual(len(reference["relations"]), 32)
        self.assertEqual(route_conflicts(reference), [])
        self.assertGreater(len(route_intersections(reference)), 0)
        self.assertEqual(
            len({tuple(relation["path"].split()[:2]) for relation in reference["relations"]}),
            32,
        )
        nodes = {node["id"]: node for node in reference["elements"]}
        for relation in reference["relations"]:
            segments = _segments(_points_from_path(relation["path"]))
            excluded = {
                relation["from"],
                relation["to"],
                nodes[relation["from"]].get("parent"),
                nodes[relation["to"]].get("parent"),
            }
            for node in reference["elements"]:
                if node["id"] not in excluded:
                    self.assertFalse(
                        any(_blocked_by_node(a, b, node) for a, b in segments),
                        (relation["id"], node["id"]),
                    )
        for label in reference["relations"]:
            if label.get("auto_hidden_label") or label.get("show_label") is False:
                continue
            content = label["id"]
            width = max(38, min(label.get("label_max_width", 220), len(content) * 7.1 + 16))
            box = (
                label["label_x"] - width / 2,
                label["label_y"] - 14,
                label["label_x"] + width / 2,
                label["label_y"] + 14,
            )
            for relation in reference["relations"]:
                if relation["id"] != label["id"]:
                    self.assertFalse(
                        any(
                            _segment_through_box(segment, box)
                            for segment in _segments(_points_from_path(relation["path"]))
                        ),
                        (relation["id"], label["id"]),
                    )

    def test_impossible_layout_keeps_previous_version(self) -> None:
        diagram = {
            "schema_version": "autostopcrm.manager-structure.v1",
            "canvas": {"width": 1000, "height": 600},
            "elements": [
                {
                    "id": ident,
                    "title": ident,
                    "kind": "module",
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                }
                for ident, x, y, width, height in (
                    ("A", 50, 250, 100, 80),
                    ("B", 850, 250, 100, 80),
                    ("C", 450, 100, 100, 400),
                )
            ],
            "relations": [],
        }
        self.apply(0, "impossible-fixture-001", "replace", diagram=diagram)
        self.apply(
            1,
            "impossible-edge-001",
            "layout_relation",
            relation={
                "id": "R",
                "from": "A",
                "to": "B",
                "kind": "exchange",
                "direction": "forward",
            },
        )
        before = self.service.read()
        with self.assertRaisesRegex(ServiceError, "Перемещение не сохранено"):
            self.apply(
                2,
                "impossible-resize-001",
                "layout_element",
                element={"id": "C", "y": 0, "height": 600},
            )
        self.assertEqual(self.service.read(), before)
        blocked = json.loads(json.dumps(diagram))
        blocked["elements"][2]["y"] = 0
        blocked["elements"][2]["height"] = 600
        self.apply(2, "blocked-fixture-001", "replace", diagram=blocked)
        blocked_before = self.service.read()
        with self.assertRaisesRegex(ServiceError, "Изменение связи не сохранено"):
            self.apply(
                3,
                "blocked-edge-001",
                "layout_relation",
                relation={
                    "id": "R",
                    "from": "A",
                    "to": "B",
                    "kind": "exchange",
                    "direction": "forward",
                },
            )
        self.assertEqual(self.service.read(), blocked_before)

    def test_overlapping_move_snaps_to_nearest_free_candidate(self) -> None:
        diagram = {
            "schema_version": "autostopcrm.manager-structure.v1",
            "canvas": {"width": 700, "height": 400},
            "elements": [
                {
                    "id": ident,
                    "title": ident,
                    "kind": "module",
                    "x": x,
                    "y": 100,
                    "width": 100,
                    "height": 100,
                }
                for ident, x in (("A", 100), ("B", 280))
            ],
            "relations": [],
        }
        self.apply(0, "snap-fixture-001", "replace", diagram=diagram)
        preview = self.service.apply(
            {
                "operation": "layout_element",
                "element": {"id": "A", "x": 200},
                "expected_version": 1,
                "idempotency_key": "snap-preview-001",
                "preview": True,
                "_operator_session": {
                    "is_admin": True,
                    "username": "admin",
                    "token": "local-test-session",
                },
            }
        )
        self.assertTrue(preview["adjusted"])
        self.assertEqual(preview["diagram"]["elements"][0]["x"], 176)
        self.assertEqual(self.service.read()["version"], 1)
        saved = self.apply(1, "snap-save-001", "layout_element", element={"id": "A", "x": 200})
        self.assertTrue(saved["adjusted"])
        self.assertEqual(saved["accepted_element"]["x"], 176)
        self.assertEqual(ManagerStructureService(self.path).read()["elements"][0]["x"], 176)

    def test_reference_move_rebuilds_all_paths_without_conflicts(self) -> None:
        reference = json.loads(
            (
                Path(__file__).resolve().parents[1] / "templates" / "manager_structure.json"
            ).read_text(encoding="utf-8")
        )
        self.apply(0, "reference-fixture-001", "replace", diagram=reference)
        current = next(node for node in reference["elements"] if node["id"] == "G1")
        saved = self.apply(
            1,
            "reference-move-001",
            "layout_element",
            element={"id": "G1", "x": current["x"] + 30},
        )
        after = ManagerStructureService(self.path).read()
        self.assertEqual(saved["accepted_element"]["x"], current["x"] + 30)
        self.assertEqual(len(saved["routes"]), 32)
        self.assertEqual(route_conflicts(after), [])


if __name__ == "__main__":
    unittest.main()
