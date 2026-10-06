from __future__ import annotations

import unittest

if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.mcp.raw_gateway import (  # noqa: E402
    RAW_API_READ_ROUTES,
    RAW_API_WRITE_ROUTES,
    schema_hash,
    verify_virtual_api_write_readback,
    virtual_api_argument_errors,
    virtual_api_preflight_errors,
    virtual_api_schema,
)


class ManagerStructureGatewayTests(unittest.IsolatedAsyncioTestCase):
    def test_raw_capabilities_are_versioned_and_discoverable(self) -> None:
        self.assertIn("/api/manager_structure", RAW_API_READ_ROUTES)
        self.assertIn("/api/manager_structure/tool_catalog", RAW_API_READ_ROUTES)
        self.assertEqual(virtual_api_argument_errors("/api/manager_structure/tool_catalog", {}), [])
        self.assertIn("/api/manager_structure/apply", RAW_API_WRITE_ROUTES)
        schema = virtual_api_schema("/api/manager_structure/apply")
        self.assertTrue(schema_hash(schema))
        relation = schema["properties"]["relation"]
        self.assertEqual(
            relation["properties"]["direction"]["enum"],
            ["forward", "reverse", "both", "none"],
        )
        self.assertEqual(
            relation["properties"]["from_anchor"]["properties"]["offset"]["maximum"], 1
        )
        self.assertEqual(relation["properties"]["route_mode"]["enum"], ["auto", "manual"])
        self.assertIn("reroute", schema["properties"]["operation"]["enum"])
        self.assertIn("set_tool_status", schema["properties"]["operation"]["enum"])
        self.assertIn("clear_tool_status", schema["properties"]["operation"]["enum"])
        for element in (
            {"id": "M1", "x": 112.5, "parent": None},
            {"id": "M1", "instruction": "Context", "lines": ["Summary"]},
        ):
            self.assertEqual(
                virtual_api_argument_errors(
                    "/api/manager_structure/apply",
                    {
                        "operation": "layout_element",
                        "expected_version": 3,
                        "idempotency_key": "typed-module-001",
                        "element": element,
                    },
                ),
                [],
            )
        self.assertEqual(
            virtual_api_argument_errors(
                "/api/manager_structure/apply",
                {
                    "operation": "layout_relation",
                    "expected_version": 3,
                    "idempotency_key": "typed-anchor-001",
                    "relation": {"id": "L1", "from_anchor": {"side": "right", "offset": 1.1}},
                },
            ),
            ["arguments.relation.from_anchor.offset:maximum"],
        )
        self.assertFalse(
            virtual_api_argument_errors(
                "/api/manager_structure/apply",
                {
                    "operation": "upsert_relation",
                    "expected_version": 3,
                    "idempotency_key": "relation-partial-001",
                    "relation": {"id": "L29", "direction": "reverse"},
                },
            )
        )
        self.assertEqual(
            virtual_api_argument_errors("/api/manager_structure/apply", {}),
            [
                "arguments.expected_version:required",
                "arguments.idempotency_key:required",
                "arguments.operation:required",
            ],
        )
        self.assertFalse(
            virtual_api_argument_errors(
                "/api/manager_structure/apply",
                {
                    "operation": "upsert_element",
                    "expected_version": 3,
                    "idempotency_key": "example-001",
                    "element": {"id": "M1"},
                },
            )
        )
        self.assertEqual(
            virtual_api_preflight_errors(
                "/api/manager_structure/apply",
                {"operation": "remove_relation", "expected_version": 3, "id": "L1"},
                "outer-key-001",
            ),
            [],
        )

    async def test_raw_write_checks_exact_module_text_on_readback(self) -> None:
        async def invoke(name: str, arguments: dict) -> dict:
            self.assertEqual(name, "api:/api/manager_structure")
            self.assertEqual(arguments, {})
            return {
                "ok": True,
                "data": {
                    "version": 2,
                    "elements": [{"id": "M1", "instruction": "Сохранённый полный текст"}],
                    "relations": [],
                },
            }

        result = {"ok": True, "data": {"version": 2}}
        good = await verify_virtual_api_write_readback(
            "api:/api/manager_structure/apply",
            {
                "operation": "upsert_element",
                "element": {"id": "M1", "instruction": "Сохранённый полный текст"},
            },
            result,
            invoke,
        )
        self.assertTrue(good["passed"])
        bad = await verify_virtual_api_write_readback(
            "api:/api/manager_structure/apply",
            {"operation": "upsert_element", "element": {"id": "M1", "instruction": "Другой текст"}},
            result,
            invoke,
        )
        self.assertFalse(bad["passed"])

    async def test_layout_readback_checks_adjusted_position_and_server_routes(self) -> None:
        async def invoke(_name: str, _arguments: dict) -> dict:
            return {
                "ok": True,
                "data": {
                    "version": 7,
                    "elements": [{"id": "M1", "x": 112, "y": 40, "width": 180, "height": 90}],
                    "relations": [{"id": "R1", "path": "M292 85 H500"}],
                },
            }

        arguments = {"operation": "layout_element", "element": {"id": "M1", "x": 100}}
        result = {
            "ok": True,
            "data": {
                "version": 7,
                "adjusted": True,
                "accepted_element": {"id": "M1", "x": 112, "y": 40, "width": 180, "height": 90},
                "routes": {"R1": "M292 85 H500"},
                "labels": {"R1": {"label_x": None, "label_y": None, "auto_hidden_label": None}},
            },
        }
        good = await verify_virtual_api_write_readback(
            "api:/api/manager_structure/apply", arguments, result, invoke
        )
        self.assertTrue(good["passed"])
        result["data"]["routes"] = {"R1": "M0 0 H1"}
        bad = await verify_virtual_api_write_readback(
            "api:/api/manager_structure/apply", arguments, result, invoke
        )
        self.assertFalse(bad["passed"])

    async def test_manual_route_readback_accepts_and_checks_normalized_relation(self) -> None:
        saved = {
            "id": "R1",
            "from": "M1",
            "to": "M2",
            "kind": "exchange",
            "direction": "reverse",
            "route_mode": "manual",
            "path": "M220 150 H300 V200 H500",
            "from_anchor": {"side": "right", "offset": 0.5},
            "to_anchor": {"side": "left", "offset": 0.5},
            "label_mode": "manual",
            "label_x": 360,
            "label_y": 130,
        }

        async def invoke(_name: str, _arguments: dict) -> dict:
            return {"ok": True, "data": {"version": 9, "elements": [], "relations": [saved]}}

        checked = await verify_virtual_api_write_readback(
            "api:/api/manager_structure/apply",
            {
                "operation": "upsert_relation",
                "relation": {"id": "R1", "path": "M220 150 H300 V200 H500"},
            },
            {"ok": True, "data": {"version": 9, "accepted_relation": saved}},
            invoke,
        )
        self.assertTrue(checked["passed"])

    async def test_preview_verifies_unchanged_saved_snapshot_and_reroute_labels(self) -> None:
        import hashlib
        import json

        saved = {
            "schema_version": "autostopcrm.manager-structure.v1",
            "version": 7,
            "canvas": {"width": 800, "height": 600},
            "elements": [],
            "relations": [
                {
                    "id": "R1",
                    "path": "M100 100 H500",
                    "label_x": 300,
                    "label_y": 100,
                    "auto_hidden_label": False,
                }
            ],
        }

        async def invoke(_name, _arguments):
            return {"ok": True, "data": {**saved, "can_edit": True}}

        digest = hashlib.sha256(
            json.dumps(saved, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        preview = {
            "ok": True,
            "data": {
                "version": 7,
                "preview": True,
                "saved_digest": digest,
                "diagram": {**saved, "elements": [{"id": "M1", "title": "Draft only"}]},
            },
        }
        checked = await verify_virtual_api_write_readback(
            "api:/api/manager_structure/apply",
            {
                "operation": "upsert_element",
                "expected_version": 7,
                "preview": True,
                "element": {"id": "M1", "title": "Draft only"},
            },
            preview,
            invoke,
        )
        self.assertTrue(checked["passed"])
        self.assertEqual(checked["check"], "manager_structure_preview_non_mutating_readback")
        saved["relations"][0]["label_x"] = 310
        self.assertFalse(
            (
                await verify_virtual_api_write_readback(
                    "api:/api/manager_structure/apply",
                    {"operation": "reroute", "expected_version": 7, "preview": True},
                    preview,
                    invoke,
                )
            )["passed"]
        )
        result = {
            "ok": True,
            "data": {
                "version": 7,
                "routes": {"R1": "M100 100 H500"},
                "labels": {"R1": {"label_x": 310, "label_y": 100, "auto_hidden_label": False}},
            },
        }
        self.assertTrue(
            (
                await verify_virtual_api_write_readback(
                    "api:/api/manager_structure/apply", {"operation": "reroute"}, result, invoke
                )
            )["passed"]
        )
        result["data"]["labels"]["R1"]["label_y"] = 101
        self.assertFalse(
            (
                await verify_virtual_api_write_readback(
                    "api:/api/manager_structure/apply", {"operation": "reroute"}, result, invoke
                )
            )["passed"]
        )


if __name__ == "__main__":
    unittest.main()
