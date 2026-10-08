from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path, prepend_scripts_path

ensure_source_path()
prepend_scripts_path()

from manager_structure_template import build_reference
from sync_manager_structure_instructions import A5_POINTER, canonical_instructions, prepare
from sync_manager_structure_instructions import main as sync_main

from minimal_kanban.services.errors import ServiceError
from minimal_kanban.services.manager_structure import SCHEMA, ManagerStructureService
from tests.manager_tool_catalog_fixture import fixture_bundle


def graph_fixture() -> dict:
    return {
        "version": 7,
        "schema_version": SCHEMA,
        "canvas": {"width": 900, "height": 500},
        "elements": [
            {
                "id": "E1",
                "title": "Источник",
                "kind": "module",
                "x": 100,
                "y": 100,
                "width": 120,
                "height": 100,
                "instruction": "Старое",
                "color": "#123456",
                "indicator": "red",
                "indicator_mode": "manual",
                "indicator_state": "yellow",
            },
            {
                "id": "E2",
                "title": "Получатель",
                "kind": "module",
                "x": 500,
                "y": 100,
                "width": 120,
                "height": 100,
                "instruction": "Тоже старое",
                "parent": "E1",
            },
        ],
        "relations": [
            {
                "id": "L1",
                "from": "E1",
                "to": "E2",
                "kind": "exchange",
                "direction": "both",
                "route_mode": "manual",
                "path": "M220 150 C280 80 420 80 500 150",
                "label_mode": "manual",
                "label_x": 360,
                "label_y": 45,
                "color": "#654321",
                "auto_hidden_label": False,
            },
        ],
        "receipts": {},
        "tool_statuses": {
            "demo.inspect": {
                "state": "working",
                "updated_at": "synthetic",
                "updated_by": "synthetic-owner",
            }
        },
    }


class ManagerInstructionRefreshTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.file = self.root / "manager_structure.json"
        self.before = graph_fixture()
        self.file.write_text(json.dumps(self.before), encoding="utf-8")
        self.service = ManagerStructureService(self.file)
        owner = patch.dict("os.environ", {"AUTOSTOP_MANAGER_STRUCTURE_OWNER_LOGIN": "ADMIN"})
        owner.start()
        self.addCleanup(owner.stop)

    def request(self, **overrides):
        return {
            "expected_version": 7,
            "idempotency_key": "instruction-test-001",
            "operation": "upsert_element",
            "element": {"id": "E2", "instruction": "Новое"},
            "_operator_session": {
                "is_admin": True,
                "username": "admin",
                "token": "synthetic-session",
            },
            **overrides,
        }

    def test_instruction_only_preserves_exact_routes_styles_statuses_and_preview(self):
        stored = self.file.read_bytes()
        with (
            patch.object(
                self.service,
                "_prepare_manual_routes",
                side_effect=AssertionError("instruction update must not reanchor"),
            ),
            patch.object(
                self.service,
                "_verify_manual_routes",
                side_effect=AssertionError("instruction update cannot change route conflicts"),
            ),
        ):
            preview = self.service.apply(self.request(preview=True))
            self.assertEqual(self.file.read_bytes(), stored)
            self.assertEqual(preview["diagram"]["relations"], self.before["relations"])
            result = self.service.apply(self.request())
            self.assertEqual(result["version"], 8)
            self.assertTrue(self.service.apply(self.request())["deduplicated"])
        saved = json.loads(self.file.read_text())
        expected = copy.deepcopy(self.before)
        expected["elements"][1]["instruction"] = "Новое"
        for field in ("canvas", "elements", "relations", "tool_statuses"):
            self.assertEqual(saved[field], expected[field])
        self.assertNotIn("from_anchor", saved["relations"][0])

    def test_instruction_only_keeps_rights_cas_text_and_idempotency_guards(self):
        stored = self.file.read_bytes()
        for overrides in (
            {"_operator_session": {"is_admin": False, "username": "viewer"}},
            {"expected_version": 6},
            {"element": {"id": "E2", "instruction": "x" * 30001}},
            {"element": {"id": "E99", "instruction": "missing node"}},
        ):
            with self.subTest(overrides=list(overrides)), self.assertRaises(ServiceError):
                self.service.apply(self.request(**overrides))
            self.assertEqual(self.file.read_bytes(), stored)
        self.service.apply(self.request())
        with self.assertRaisesRegex(ServiceError, "Ключ уже использован"):
            self.service.apply(self.request(element={"id": "E2", "instruction": "other"}))

    def test_other_upserts_still_prepare_manual_routes(self):
        with patch.object(
            self.service, "_prepare_manual_routes", wraps=self.service._prepare_manual_routes
        ) as prepare_routes:
            for extra in ({"title": "Заголовок"}, {"x": 500}, {"parent": "E1"}):
                self.service.apply(
                    self.request(
                        preview=True, element={"id": "E2", "instruction": "Новое", **extra}
                    )
                )
        self.assertEqual(prepare_routes.call_count, 3)

    def test_retained_baseline_conflict_allows_text_only_and_keeps_normal_geometry_gate(self):
        graph = json.loads(
            (
                Path(__file__).resolve().parents[1] / "templates" / "manager_structure.json"
            ).read_text(encoding="utf-8")
        )
        graph.update(version=7, receipts={}, tool_statuses=self.before["tool_statuses"])
        self.file.write_text(json.dumps(graph), encoding="utf-8")
        self.service.apply(self.request())
        expected = copy.deepcopy(graph)
        next(n for n in expected["elements"] if n["id"] == "E2")["instruction"] = "Новое"
        saved = json.loads(self.file.read_text())
        for field in ("canvas", "elements", "relations", "tool_statuses"):
            self.assertEqual(saved[field], expected[field])
        with patch.object(
            self.service, "_verify_manual_routes", wraps=self.service._verify_manual_routes
        ) as verify_routes:
            self.service.apply(
                self.request(
                    expected_version=8,
                    idempotency_key="instruction-normal-002",
                    element={"id": "E2", "instruction": "More", "title": "Same geometry"},
                )
            )
        verify_routes.assert_called_once()

    def test_builder_preserves_full_instruction_explicit_indicators_colors_and_routes(self):
        source = self.root / "blueprint.json"
        graph = graph_fixture()
        graph["elements"][0]["purpose"] = "Short purpose"
        graph["elements"][1].update(color="#abcdef", indicator="off")
        graph["relations"][0]["from_anchor"] = {"side": "right", "offset": 0.5}
        graph["relations"][0]["to_anchor"] = {"side": "left", "offset": 0.5}
        source.write_text(json.dumps(graph), encoding="utf-8")

        class LocalClient:
            def __init__(self):
                self.data = {
                    "version": 0,
                    "canvas": graph["canvas"],
                    "elements": [],
                    "relations": [],
                }

            def read(self):
                return copy.deepcopy(self.data)

            def apply(self, version, operation, **fields):
                assert version == self.data["version"]
                target = "elements" if operation == "upsert_element" else "relations"
                self.data[target].append(fields["element" if target == "elements" else "relation"])
                self.data["version"] += 1
                return {"version": self.data["version"]}

        with patch("manager_structure_template.REFERENCE", source):
            built = build_reference(LocalClient())
        expected = copy.deepcopy(graph["elements"])
        expected[0].pop("purpose")
        self.assertEqual(built["elements"], expected)
        self.assertEqual(built["relations"], graph["relations"])

    def test_offline_preparation_excludes_private_state_and_preserves_geometry(self):
        instructions = {"E1": "Canonical one", "E2": "Canonical two"}
        result = prepare(self.before, instructions)
        self.assertEqual(self.before, graph_fixture())
        self.assertEqual(result["template"]["relations"], self.before["relations"])
        self.assertEqual(result["template"]["canvas"], self.before["canvas"])
        self.assertNotIn("tool_statuses", result["template"])
        self.assertNotIn("receipts", result["blueprint"])
        self.assertNotIn("synthetic-owner", json.dumps(result))
        self.assertEqual([p["expected_version"] for p in result["patch"]["apply_requests"]], [7, 8])
        self.assertEqual(
            [p["expected_version"] for p in result["patch"]["preview_requests"]], [7, 7]
        )
        for request in result["patch"]["apply_requests"]:
            self.assertEqual(set(request["element"]), {"id", "instruction"})
        with self.assertRaisesRegex(ValueError, "exact node set"):
            prepare(self.before, {"E1": "partial"})

    def test_canonical_docs_bundle_parity_and_stable_a5_pointer(self):
        modules = self.root / "docs/agent/modules"
        modules.mkdir(parents=True)
        bundle = fixture_bundle()
        (self.root / "REVISION").write_text(bundle["source_revision"], encoding="utf-8")
        (modules / "E1.md").write_text("Full instruction", encoding="utf-8")
        (modules / "E2.md").write_text(bundle["modules"][0]["instruction_text"], encoding="utf-8")
        self.assertEqual(
            canonical_instructions(self.before, self.root, bundle)["E1"], "Full instruction"
        )
        (modules / "E2.md").write_text("Drift", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Pinned bundle differs"):
            canonical_instructions(self.before, self.root, bundle)
        (modules / "A5.md").write_text("Long catalog" * 4000, encoding="utf-8")
        graph = {"elements": [{"id": "A5"}]}
        self.assertEqual(canonical_instructions(graph, self.root)["A5"], A5_POINTER)

    def test_check_saved_reports_drift_without_writing_and_checks_source_revision(self):
        graph = json.loads(
            (
                Path(__file__).resolve().parents[1] / "templates" / "manager_structure.json"
            ).read_text(encoding="utf-8")
        )
        graph.update(version=7, receipts={}, tool_statuses={})
        self.file.write_text(json.dumps(graph), encoding="utf-8")
        stored = self.file.read_bytes()
        instructions = {n["id"]: n["instruction"] for n in graph["elements"]}
        instructions["E2"] = "Changed canonical instruction"
        output = io.StringIO()
        with (
            patch(
                "sync_manager_structure_instructions.canonical_instructions",
                return_value=instructions,
            ),
            patch("sys.stdout", output),
        ):
            code = sync_main(
                ["--graph", str(self.file), "--manager-root", str(self.root), "--check-saved"]
            )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["changed_ids"], ["E2"])
        self.assertFalse(json.loads(output.getvalue())["saved_matches"])
        self.assertEqual(self.file.read_bytes(), stored)
        modules = self.root / "docs/agent/modules"
        modules.mkdir(parents=True)
        bundle = fixture_bundle()
        (modules / "E1.md").write_text("Full", encoding="utf-8")
        (modules / "E2.md").write_text(bundle["modules"][0]["instruction_text"], encoding="utf-8")
        (self.root / "REVISION").write_text("f" * 40, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source revision"):
            canonical_instructions(self.before, self.root, bundle)


if __name__ == "__main__":
    unittest.main()
