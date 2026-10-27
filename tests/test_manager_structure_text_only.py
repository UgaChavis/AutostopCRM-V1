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
        "canvas": {"width": 800, "height": 400},
        "elements": [
            {
                "id": ident,
                "title": ident,
                "kind": "module",
                "x": x,
                "y": 100,
                "width": 120,
                "height": 100,
                "instruction": "Original instruction",
                "description": "Original description",
            }
            for ident, x in (("A", 100), ("B", 500))
        ],
        # Older saved graphs may have manual paths without explicit anchors.
        "relations": [
            {
                "id": "R1",
                "from": "A",
                "to": "B",
                "kind": "exchange",
                "direction": "forward",
                "route_mode": "manual",
                "path": "M220 150 C280 80 420 80 500 150",
                "label_x": 360,
                "label_y": 45,
            }
        ],
    }


class ManagerStructureTextOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        owner = patch.dict("os.environ", {"AUTOSTOP_MANAGER_STRUCTURE_OWNER_LOGIN": "ADMIN"})
        owner.start()
        self.addCleanup(owner.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "graph.json"
        self.service = ManagerStructureService(self.path)
        self.apply(0, "synthetic-graph-01", "replace", diagram=synthetic_diagram())
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
                "_operator_session": {
                    "is_admin": True,
                    "username": "admin",
                    "token": "local-test-session",
                },
                **body,
            }
        )

    def test_text_fields_preserve_manual_paths_with_preview_and_guards(self) -> None:
        cases = (
            {"instruction": "New instruction"},
            {"description": "New description"},
            {"instruction": "Both instruction", "description": "Both description"},
        )
        for index, fields in enumerate(cases):
            with self.subTest(fields=fields):
                before = self.service.read()
                original_bytes = self.path.read_bytes()
                version = before["version"]
                element = {"id": "A", **fields}
                preview = self.apply(
                    version,
                    f"text-preview-{index:02}",
                    "upsert_element",
                    element=element,
                    preview=True,
                )
                self.assertTrue(preview["preview"])
                self.assertEqual(self.path.read_bytes(), original_bytes)
                self.assertEqual(preview["diagram"]["relations"], before["relations"])
                self.assertEqual(preview["diagram"]["canvas"], before["canvas"])
                unchanged_root = {
                    key: value
                    for key, value in before.items()
                    if key
                    not in {
                        "version",
                        "elements",
                        "can_edit",
                        "can_edit_tool_status",
                        "catalog_metadata",
                    }
                }
                self.assertEqual(
                    {
                        key: value
                        for key, value in preview["diagram"].items()
                        if key not in {"version", "elements"}
                    },
                    unchanged_root,
                )
                expected = copy.deepcopy(before["elements"])
                expected[0].update(fields)
                self.assertEqual(preview["diagram"]["elements"], expected)

                key = f"text-apply-{index:02}"
                saved = self.apply(version, key, "upsert_element", element=element)
                self.assertEqual(saved["version"], version + 1)
                after = ManagerStructureService(self.path).read()
                self.assertEqual(after["elements"], expected)
                self.assertEqual(after["relations"], before["relations"])
                self.assertEqual(after["canvas"], before["canvas"])
                self.assertEqual(
                    {
                        key: value
                        for key, value in after.items()
                        if key
                        not in {
                            "version",
                            "elements",
                            "can_edit",
                            "can_edit_tool_status",
                            "catalog_metadata",
                        }
                    },
                    unchanged_root,
                )
                self.assertTrue(
                    self.apply(version, key, "upsert_element", element=element)["deduplicated"]
                )
                with self.assertRaisesRegex(ServiceError, "Ключ уже использован"):
                    self.apply(
                        version,
                        key,
                        "upsert_element",
                        element={"id": "A", "description": "Other"},
                    )
                with self.assertRaisesRegex(ServiceError, "Схема изменилась"):
                    self.apply(version, f"stale-text-{index:02}", "upsert_element", element=element)

    def test_text_edit_keeps_existing_invalid_geometry_without_accepting_new_layout(self) -> None:
        diagram = synthetic_diagram()
        diagram["elements"].append(
            {
                "id": "C",
                "title": "Obstacle",
                "kind": "module",
                "x": 300,
                "y": 100,
                "width": 80,
                "height": 100,
            }
        )
        diagram["relations"][0]["path"] = "M220 150 H500"
        # Model a previously saved, structurally valid graph with a blocked manual route.
        self.path.write_text(json.dumps({**diagram, "version": 1, "receipts": {}}))
        before = self.service.read()
        preview = self.apply(
            1,
            "blocked-text-prev",
            "upsert_element",
            element={"id": "A", "description": "Updated"},
            preview=True,
        )
        self.assertEqual(preview["diagram"]["relations"], before["relations"])
        self.apply(
            1, "blocked-text-apply", "upsert_element", element={"id": "A", "description": "Updated"}
        )
        after = self.service.read()
        self.assertEqual(after["relations"], before["relations"])
        for index, fields in enumerate(({"x": 110}, {"title": "Renamed"}, {})):
            with (
                self.subTest(fields=fields),
                self.assertRaisesRegex(ServiceError, "постороннюю карточку"),
            ):
                self.apply(
                    2, f"blocked-layout-{index}", "upsert_element", element={"id": "A", **fields}
                )
        with self.assertRaisesRegex(ServiceError, "постороннюю карточку"):
            self.apply(
                2,
                "blocked-new-node",
                "upsert_element",
                element={
                    "id": "D",
                    "title": "New",
                    "kind": "module",
                    "x": 600,
                    "y": 250,
                    "width": 100,
                    "height": 100,
                    "description": "New",
                },
            )
        self.assertEqual(self.service.read(), after)

    def test_text_update_retains_owner_and_text_validation(self) -> None:
        before = self.service.read()
        stored = self.path.read_bytes()
        with self.assertRaises(ServiceError):
            self.service.apply(
                {
                    "operation": "upsert_element",
                    "expected_version": before["version"],
                    "idempotency_key": "not-owner-text-01",
                    "element": {"id": "A", "description": "No owner"},
                }
            )
        with self.assertRaises(ServiceError):
            self.apply(
                before["version"],
                "bad-text-value-01",
                "upsert_element",
                element={"id": "A", "description": ["not a string"]},
            )
        with self.assertRaises(ServiceError):
            self.apply(
                before["version"],
                "missing-node-text-01",
                "upsert_element",
                element={"id": "MISSING", "description": "No such node"},
            )
        self.assertEqual(self.path.read_bytes(), stored)
        self.assertEqual(self.service.read(), before)


if __name__ == "__main__":
    unittest.main()
