from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001

import json
from unittest.mock import patch

from tests.test_repair_order_gateway_read_safety import RepairOrderGatewayFixture
from minimal_kanban.storage.json_store import StateFileCorruptedError


class MalformedRepairOrderReworkTests(RepairOrderGatewayFixture):
    async def test_malformed_rework_gateway_never_drops_non_objects(self):
        before = self.snapshot()
        for rows in (
            ["invalid"],
            [{"card_id": self.card_id, "number": "42", "status": "open"}, "invalid"],
            [{}],
            [{"card_id": 0, "number": "1", "status": "open"}],
        ):
            with (
                self.subTest(rows=rows),
                patch.object(
                    self.board_api,
                    "list_repair_orders",
                    return_value={"ok": True, "data": {"repair_orders": rows}},
                ),
            ):
                result = await self.call("agent_search", {"entity": "repair_order", "limit": 10})
                self.assertFalse(result.structuredContent["ok"], result.structuredContent)
                for compact in (False, True):
                    for redact in (False, True):
                        with self.subTest(compact=compact, redact_private=redact):
                            raw = await self.call_list(
                                {"status": "all", "compact": compact, "redact_private": redact}
                            )
                            self.assertFalse(raw.structuredContent["ok"], raw.structuredContent)
                self.assertEqual(before, self.snapshot())

    async def test_malformed_rework_gateway_valid_empty_is_success(self):
        before = self.snapshot()
        with patch.object(
            self.board_api,
            "list_repair_orders",
            return_value={"ok": True, "data": {"repair_orders": []}},
        ):
            result = await self.call("agent_search", {"entity": "repair_order", "limit": 10})
            for compact in (False, True):
                for redact in (False, True):
                    with self.subTest(compact=compact, redact_private=redact):
                        raw = await self.call_list(
                            {"status": "all", "compact": compact, "redact_private": redact}
                        )
                        self.assertTrue(raw.structuredContent["ok"], raw.structuredContent)
        self.assertTrue(result.structuredContent["ok"], result.structuredContent)
        self.assertEqual(result.structuredContent["data"]["items"], [])
        self.assertEqual(before, self.snapshot())

    async def test_malformed_rework_non_boolean_ok_fails_closed(self):
        before = self.snapshot()
        for invalid in ("false", 1, None):
            with (
                self.subTest(ok=invalid),
                patch.object(
                    self.board_api,
                    "list_repair_orders",
                    return_value={"ok": invalid, "data": {"repair_orders": []}},
                ),
            ):
                result = await self.call("agent_search", {"entity": "repair_order"})
                self.assertFalse(result.structuredContent["ok"])
                for redact in (False, True):
                    raw = await self.call_list({"status": "all", "redact_private": redact})
                    self.assertFalse(raw.structuredContent["ok"])
                self.assertEqual(before, self.snapshot())

    async def test_malformed_rework_snapshot_rejects_nonlist_and_nonobject_items(self):
        original = json.loads(self.state_file.read_text())
        for invalid in (None, {}, ["invalid"], [original["cards"][0], "invalid"], [None]):
            with self.subTest(invalid=invalid):
                state = dict(original)
                state["cards"] = invalid
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                with self.assertRaises(StateFileCorruptedError):
                    self.service.list_repair_orders({"status": "all"})
                self.assertEqual(before, self.snapshot())

    async def test_malformed_rework_snapshot_missing_cards_fails_closed(self):
        state = json.loads(self.state_file.read_text())
        del state["cards"]
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(StateFileCorruptedError):
            self.service.list_repair_orders({"status": "all"})
        self.assertEqual(before, self.snapshot())

    async def test_malformed_rework_snapshot_explicit_empty_cards_succeeds(self):
        state = json.loads(self.state_file.read_text())
        state["cards"] = []
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        result = self.service.list_repair_orders({"status": "all"})
        self.assertEqual(result["repair_orders"], [])
        self.assertEqual(before, self.snapshot())

    async def test_malformed_rework_order_field_types_fail_closed(self):
        original = json.loads(self.state_file.read_text())
        for invalid in ([], "invalid", 1):
            with self.subTest(invalid=invalid):
                state = json.loads(json.dumps(original))
                state["cards"][0]["repair_order"] = invalid
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                with self.assertRaises(StateFileCorruptedError):
                    self.service.list_repair_orders({"status": "all"})
                self.assertEqual(before, self.snapshot())

    async def test_malformed_rework_missing_or_null_order_is_legacy_empty(self):
        original = json.loads(self.state_file.read_text())
        for remove in (False, True):
            with self.subTest(missing=remove):
                state = json.loads(json.dumps(original))
                if remove:
                    del state["cards"][0]["repair_order"]
                else:
                    state["cards"][0]["repair_order"] = None
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                result = self.service.list_repair_orders({"status": "all"})
                self.assertEqual(result["repair_orders"], [])
                self.assertEqual(before, self.snapshot())

    async def test_malformed_rework_gateway_legacy_string_id_is_accepted_redacted(self):
        before = self.snapshot()
        response = {
            "ok": True,
            "data": {
                "repair_orders": [{"card_id": "legacy-card", "number": "1", "status": "open"}]
            },
        }
        with patch.object(self.board_api, "list_repair_orders", return_value=response):
            result = await self.call("agent_search", {"entity": "repair_order", "limit": 10})
        self.assertTrue(result.structuredContent["ok"], result.structuredContent)
        self.assertEqual(len(result.structuredContent["data"]["items"]), 1)
        self.assertEqual(result.structuredContent["data"]["items"][0]["card_id"], "")
        self.assertEqual(before, self.snapshot())
