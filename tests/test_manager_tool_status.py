"""Manual commissioning must remain an owner-only, durable, exact mutation."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path, prepend_scripts_path

ensure_source_path()
prepend_scripts_path()

from manager_structure_template import portable  # noqa: E402

from minimal_kanban.mcp.manager_structure_gateway import (
    verify_manager_structure_readback,  # noqa: E402
)
from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.services.manager_structure import ManagerStructureService  # noqa: E402
from minimal_kanban.services.manager_tool_catalog import content_hash, validate_bundle  # noqa: E402
from minimal_kanban.services.manager_tool_status import (  # noqa: E402
    durable_digest,
    durable_projection,
)
from tests.manager_tool_catalog_fixture import fixture_bundle  # noqa: E402


class ManagerToolStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner_patch = patch.dict(
            "os.environ", {"AUTOSTOP_MANAGER_STRUCTURE_OWNER_LOGIN": "ADMIN"}
        )
        self.owner_patch.start()
        self.addCleanup(self.owner_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "manager_structure.json"
        self.bundle = fixture_bundle()
        self.service = ManagerStructureService(
            self.path, tool_catalog_loader=lambda: copy.deepcopy(self.bundle)
        )
        self.owner = {"is_admin": True, "username": "admin", "token": "synthetic-session"}

    def request(self, state="working", key="commissioning-001", **kwargs):
        return {
            "operation": "set_tool_status",
            "expected_version": self.service.read()["version"],
            "idempotency_key": key,
            "tool_status": {"operation_id": "demo.inspect", "state": state},
            "_operator_session": self.owner,
            **kwargs,
        }

    def test_old_v1_default_read_is_write_free_and_statuses_survive_restart(self):
        data = self.service.read({"_operator_session": self.owner})
        self.assertNotIn("tool_statuses", data)
        self.assertTrue(data["can_edit_tool_status"])
        self.assertFalse(self.path.exists())
        payload = self.request()
        with patch("socket.create_connection", side_effect=AssertionError("No network")):
            result = self.service.apply(payload)
        saved = ManagerStructureService(self.path, tool_catalog_loader=lambda: self.bundle).read()
        self.assertEqual(saved["tool_statuses"]["demo.inspect"]["state"], "working")
        self.assertEqual(saved["tool_statuses"]["demo.inspect"]["updated_by"], "ADMIN")
        self.assertEqual(result["tool_status"], saved["tool_statuses"]["demo.inspect"])
        self.assertEqual(self.service.apply(payload)["version"], 1)
        self.assertTrue(self.service.apply(payload)["deduplicated"])
        changed = {
            **payload,
            "tool_status": {"operation_id": "demo.inspect", "state": "temporarily_unavailable"},
        }
        with self.assertRaises(ServiceError) as conflict:
            self.service.apply(changed)
        self.assertEqual(conflict.exception.status_code, 409)

    def test_owner_guards_enum_unknown_and_version_reject_without_side_effect(self):
        self.service.apply(self.request())
        baseline = self.path.read_bytes()
        for session in (
            {"is_admin": False, "username": "admin", "token": "valid"},
            {"is_admin": True, "username": "other", "token": "valid"},
            {"is_admin": True, "username": "admin", "token": "service-identity"},
        ):
            with self.assertRaises(ServiceError) as forbidden:
                self.service.apply(self.request(key="forbidden-001", _operator_session=session))
            self.assertEqual(forbidden.exception.status_code, 403)
        for status in (
            {"operation_id": "missing", "state": "working"},
            {"operation_id": "demo.inspect", "state": "green"},
            {"operation_id": "demo.inspect", "state": "working", "updated_by": "forged"},
        ):
            with self.assertRaises(ServiceError):
                self.service.apply(self.request(key="invalid-status-001", tool_status=status))
        with self.assertRaises(ServiceError) as stale:
            self.service.apply(self.request(key="stale-status-001", expected_version=0))
        self.assertEqual(stale.exception.status_code, 409)
        self.assertEqual(self.path.read_bytes(), baseline)

    def test_preview_and_clear_restore_absence(self):
        payload = self.request(preview=True)
        result = self.service.apply(payload)
        self.assertEqual(result["diagram"]["tool_statuses"]["demo.inspect"]["state"], "working")
        self.assertFalse(self.path.exists())
        self.assertEqual(result["saved_digest"], durable_digest(self.service.read()))
        self.service.apply(self.request())
        self.service.apply(
            self.request(
                key="clear-status-001",
                operation="clear_tool_status",
                tool_status={"operation_id": "demo.inspect"},
            )
        )
        self.assertNotIn("demo.inspect", self.service.read()["tool_statuses"])

    def test_status_preserved_through_graph_edits_and_portable_export(self):
        self.service.apply(self.request())
        status = copy.deepcopy(self.service.read()["tool_statuses"])
        node = {
            "id": "E2",
            "title": "Синтетический модуль",
            "kind": "module",
            "x": 10,
            "y": 10,
            "width": 200,
            "height": 100,
        }
        for index, fields in enumerate(
            (
                {"operation": "upsert_element", "element": node},
                {"operation": "layout_element", "element": {"id": "E2", "x": 20}},
                {"operation": "reroute"},
                {"operation": "replace", "diagram": portable(self.service.read())},
            )
        ):
            self.service.apply(self.request(key=f"graph-preservation-{index}", **fields))
            self.assertEqual(self.service.read()["tool_statuses"], status)
        self.assertNotIn("tool_statuses", portable(self.service.read()))
        changed_bundle = copy.deepcopy(self.bundle)
        changed_bundle["source_revision"] = "1" * 40
        self.bundle = changed_bundle
        self.assertEqual(self.service.read()["tool_statuses"], status)

    def test_catalog_hash_binding_limits_and_stable_keys(self):
        self.assertEqual(validate_bundle(self.bundle), self.bundle)
        modified = copy.deepcopy(self.bundle)
        modified["source_revision"] = "1" * 40
        self.assertEqual(content_hash(modified), self.bundle["content_hash"])
        modified["tools"][0]["instruction_text"] = "tampered"
        with self.assertRaises(ValueError):
            validate_bundle(modified)
        modified["content_hash"] = content_hash(modified)
        modified["modules"][0]["tool_ids"] = ["absent"]
        modified["content_hash"] = content_hash(modified)
        with self.assertRaises(ValueError):
            validate_bundle(modified)
        for fields in (
            {"source_revision": 10**39},
            {"native_schemas": []},
            {"tools": [{**self.bundle["tools"][0], "input_schema_ref": "missing"}]},
            {"tools": [{**self.bundle["tools"][0], "limitations": "malformed"}]},
        ):
            invalid = {**copy.deepcopy(self.bundle), **fields}
            invalid["content_hash"] = content_hash(invalid)
            with self.assertRaises(ValueError):
                validate_bundle(invalid)
        self.service.apply(self.request())
        raw = json.loads(self.path.read_text())
        raw["tool_statuses"]["demo.inspect"]["updated_by"] = "x" * 161
        self.path.write_text(json.dumps(raw))
        with self.assertRaises(ServiceError):
            self.service.read()

    async def test_gateway_exact_readback_rejects_unrelated_graph_change(self):
        arguments = self.request()
        result = {"ok": True, "data": self.service.apply(arguments)}
        current = self.service.read()

        async def invoke(_name, _arguments):
            return {"ok": True, "data": current}

        good = await verify_manager_structure_readback(arguments, result, invoke)
        self.assertTrue(good["passed"])
        current["canvas"]["width"] += 1
        bad = await verify_manager_structure_readback(arguments, result, invoke)
        self.assertFalse(bad["passed"])
        self.assertFalse(bad["evidence"]["routes_exact"])

    def test_durable_projection_excludes_computed_fields_and_preserves_unknown_root(self):
        data = {
            "version": 3,
            "tool_statuses": {},
            "preserved_extension": {"value": 7},
            "can_edit": True,
            "can_edit_tool_status": True,
            "catalog_metadata": {"content_hash": "hash"},
            "runtime_metadata": {},
            "receipts": {},
        }
        self.assertEqual(
            durable_projection(data),
            {"version": 3, "tool_statuses": {}, "preserved_extension": {"value": 7}},
        )

    def test_atomic_replace_preserves_unrelated_manual_route_bytes(self):
        graph = {
            "schema_version": "autostopcrm.manager-structure.v1",
            "canvas": {"width": 800, "height": 600},
            "elements": [
                {
                    "id": ident,
                    "title": ident,
                    "kind": "module",
                    "x": x,
                    "y": 20,
                    "width": 100,
                    "height": 80,
                }
                for ident, x in (("A1", 20), ("A2", 400))
            ],
            "relations": [
                {
                    "id": "L1",
                    "from": "A1",
                    "to": "A2",
                    "kind": "exchange",
                    "direction": "both",
                    "route_mode": "manual",
                    "path": "M 120 60 H 400",
                    "from_anchor": {"side": "right", "offset": 0.5},
                    "to_anchor": {"side": "left", "offset": 0.5},
                    "label_x": 260,
                    "label_y": 60,
                }
            ],
        }
        self.service.apply(self.request(operation="replace", diagram=graph))
        self.assertEqual(self.service.read()["relations"], graph["relations"])
        self.assertEqual(self.service.read()["elements"], graph["elements"])
