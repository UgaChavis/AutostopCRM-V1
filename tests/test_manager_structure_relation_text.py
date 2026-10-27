from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.services.manager_structure import ManagerStructureService  # noqa: E402


def synthetic_diagram() -> dict:
    return {
        "schema_version": "autostopcrm.manager-structure.v1",
        "canvas": {"width": 800, "height": 600},
        "elements": [
            {
                "id": ident,
                "title": ident,
                "kind": "module",
                "x": x,
                "y": y,
                "width": 120,
                "height": 100,
            }
            for ident, x, y in (("A", 100, 100), ("B", 500, 100), ("C", 100, 350), ("D", 500, 350))
        ],
        "relations": [
            {
                "id": ident,
                "from": source,
                "to": target,
                "kind": "exchange",
                "direction": "forward",
                "route_mode": "manual",
                "path": path,
                "label_x": 360,
                "label_y": label_y,
                "description": "Original description",
                "protocol": "Original protocol",
            }
            for ident, source, target, path, label_y in (
                ("R1", "A", "B", "M220 150 H500", 150),
                ("R2", "C", "D", "M220 400 H500", 400),
            )
        ],
    }


class ManagerStructureRelationTextTests(unittest.TestCase):
    def setUp(self) -> None:
        owner = patch.dict("os.environ", {"AUTOSTOP_MANAGER_STRUCTURE_OWNER_LOGIN": "ADMIN"})
        owner.start()
        self.addCleanup(owner.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "graph.json"
        self.service = ManagerStructureService(self.path)
        self.apply(0, "relation-graph-01", "replace", diagram=synthetic_diagram())
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        saved["tool_statuses"] = {
            "synthetic.tool": {
                "state": "working",
                "updated_at": "2026-01-01T00:00:00Z",
                "updated_by": "SYNTHETIC",
            }
        }
        self.path.write_text(json.dumps(saved), encoding="utf-8")

    def apply(self, version: int, key: str, operation: str, **body):
        return self.service.apply(
            {
                "expected_version": version,
                "idempotency_key": key,
                "operation": operation,
                "_operator_session": {"is_admin": True, "username": "admin", "token": "synthetic"},
                **body,
            }
        )

    def test_relation_text_preserves_full_graph_preview_cas_and_idempotency(self) -> None:
        cases = (
            {"description": "New description"},
            {"protocol": "New protocol"},
            {"description": "Both description", "protocol": "Both protocol"},
        )
        for index, fields in enumerate(cases):
            with self.subTest(fields=fields):
                before = self.service.read()
                stored = self.path.read_bytes()
                version = before["version"]
                relation = {"id": "R1", **fields}
                preview = self.apply(
                    version,
                    f"rel-preview-{index:02}",
                    "upsert_relation",
                    relation=relation,
                    preview=True,
                )
                self.assertTrue(preview["preview"])
                self.assertEqual(self.path.read_bytes(), stored)
                expected = copy.deepcopy(before["relations"])
                expected[0].update(fields)
                self.assertEqual(preview["diagram"]["relations"], expected)
                excluded = {
                    "version",
                    "relations",
                    "can_edit",
                    "can_edit_tool_status",
                    "catalog_metadata",
                }
                unchanged = {k: v for k, v in before.items() if k not in excluded}
                self.assertEqual(
                    {k: v for k, v in preview["diagram"].items() if k not in excluded}, unchanged
                )
                key = f"rel-apply-{index:02}"
                result = self.apply(version, key, "upsert_relation", relation=relation)
                self.assertEqual(result["version"], version + 1)
                self.assertEqual(result["accepted_relation"], expected[0])
                after = ManagerStructureService(self.path).read()
                self.assertEqual(after["relations"], expected)
                self.assertEqual({k: v for k, v in after.items() if k not in excluded}, unchanged)
                self.assertTrue(
                    self.apply(version, key, "upsert_relation", relation=relation)["deduplicated"]
                )
                with self.assertRaisesRegex(ServiceError, "Ключ уже использован"):
                    self.apply(
                        version, key, "upsert_relation", relation={"id": "R1", "protocol": "Other"}
                    )
                with self.assertRaisesRegex(ServiceError, "Схема изменилась"):
                    self.apply(
                        version, f"rel-stale-{index:02}", "upsert_relation", relation=relation
                    )

    def test_relation_text_does_not_repair_old_blocked_path_or_accept_new_geometry(self) -> None:
        graph = synthetic_diagram()
        graph["elements"].append(
            {
                "id": "OB",
                "title": "Obstacle",
                "kind": "module",
                "x": 300,
                "y": 100,
                "width": 80,
                "height": 100,
            }
        )
        self.path.write_text(json.dumps({**graph, "version": 1, "receipts": {}}), encoding="utf-8")
        before = self.service.read()
        self.apply(
            1,
            "blocked-rel-text",
            "upsert_relation",
            relation={"id": "R1", "description": "Updated"},
        )
        after = self.service.read()
        self.assertEqual(after["relations"][0]["description"], "Updated")
        self.assertEqual(after["relations"][1], before["relations"][1])
        for index, fields in enumerate(
            (
                {"label": "New label"},
                {"path": "M220 150 H500"},
                {"description": "Mixed", "label": "New label"},
                {"protocol": "Mixed", "path": "M220 150 H500"},
                {"description": "Mixed", "from": "A"},
                {},
            )
        ):
            with (
                self.subTest(fields=fields),
                self.assertRaisesRegex(ServiceError, "постороннюю карточку"),
            ):
                self.apply(
                    2, f"blocked-rel-{index:02}", "upsert_relation", relation={"id": "R1", **fields}
                )
        with self.assertRaises(ServiceError):
            self.apply(
                2,
                "missing-rel-text",
                "upsert_relation",
                relation={"id": "MISSING", "description": "New"},
            )
        new_relation = copy.deepcopy(synthetic_diagram()["relations"][1])
        new_relation["id"] = "R3"
        with self.assertRaisesRegex(ServiceError, "постороннюю карточку"):
            self.apply(2, "blocked-new-rel", "upsert_relation", relation=new_relation)
        self.assertEqual(self.service.read(), after)

    def test_relation_text_keeps_authorization_and_field_validation(self) -> None:
        before = self.service.read()
        stored = self.path.read_bytes()
        with self.assertRaises(ServiceError):
            self.service.apply(
                {
                    "expected_version": 1,
                    "idempotency_key": "no-owner-rel-01",
                    "operation": "upsert_relation",
                    "relation": {"id": "R1", "description": "Not authorized"},
                }
            )
        with self.assertRaises(ServiceError):
            self.apply(
                1,
                "invalid-rel-text",
                "upsert_relation",
                relation={"id": "R1", "protocol": ["invalid"]},
            )
        for index, fields in enumerate(({"protocol": "p" * 201}, {"description": "d" * 10001})):
            with self.subTest(fields=tuple(fields)), self.assertRaises(ServiceError):
                self.apply(
                    1, f"oversize-rel-{index}", "upsert_relation", relation={"id": "R1", **fields}
                )
        self.assertEqual(self.path.read_bytes(), stored)
        self.assertEqual(self.service.read(), before)


if __name__ == "__main__":
    unittest.main()
