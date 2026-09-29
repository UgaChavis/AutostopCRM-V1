from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
