from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import json
from unittest.mock import patch

from tests.test_repair_order_gateway_read_safety import RepairOrderGatewayFixture
from minimal_kanban.storage.json_store import StateFileCorruptedError


class RepairOrderIdentityReworkTests(RepairOrderGatewayFixture):
    def setUp(self):
        super().setUp()
        self.original_state = json.loads(self.state_file.read_text())

    def put_cards(self, ids, *, second_order=True):
        state = json.loads(json.dumps(self.original_state))
        base = next(item for item in state["cards"] if item["id"] == self.card_id)
        cards = []
        for index, identity in enumerate(ids):
            card = json.loads(json.dumps(base))
            card["id"] = identity
            if index and not second_order:
                card["repair_order"] = {}
            cards.append(card)
        state["cards"] = cards
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

    async def test_invalid_or_colliding_identities_fail_closed_without_writes(self):
        cases = [
            (["x" * 128 + "a", "x" * 128 + "b"], True),
            (["duplicate", "duplicate"], True),
            (["duplicate", "duplicate"], False),
            (["x" * 129], True),
            ([0], True),
            ([None], True),
            ([" padded "], True),
        ]
        for ids, second_order in cases:
            with self.subTest(ids=ids, second_order=second_order):
                self.put_cards(ids, second_order=second_order)
                before = self.snapshot()
                for compact in (False, True):
                    for redact in (False, True):
                        with self.assertRaises(StateFileCorruptedError):
                            self.service.list_repair_orders(
                                {"status": "all", "compact": compact, "redact_private": redact}
                            )
                        self.assertEqual(before, self.snapshot())

    async def test_valid_legacy_identity_is_preserved_or_redacted(self):
        for legacy_id in ("legacy-card-42", "x" * 128):
            with self.subTest(legacy_id=legacy_id):
                self.put_cards([legacy_id])
                before = self.snapshot()
                for compact in (False, True):
                    for redact in (False, True):
                        result = self.service.list_repair_orders(
                            {"status": "all", "compact": compact, "redact_private": redact}
                        )
                        self.assertEqual(
                            result["repair_orders"][0]["card_id"], "" if redact else legacy_id
                        )
                        self.assertEqual(before, self.snapshot())

    async def test_gateway_rejects_collisions_without_writes(self):
        for ids, second_order in (
            (["x" * 128 + "a", "x" * 128 + "b"], True),
            (["duplicate", "duplicate"], False),
        ):
            with self.subTest(ids=ids):
                self.put_cards(ids, second_order=second_order)
                before = self.snapshot()

                def backend(**kwargs):
                    try:
                        return {"ok": True, "data": self.service.list_repair_orders(kwargs)}
                    except StateFileCorruptedError:
                        return {"ok": False, "error": {"code": "state_file_corrupted"}}

                with patch.object(self.board_api, "list_repair_orders", side_effect=backend):
                    result = await self.call(
                        "agent_search", {"entity": "repair_order", "include_archived": True}
                    )
                    self.assertFalse(result.structuredContent["ok"], result.structuredContent)
                    for compact in (False, True):
                        for redact in (False, True):
                            result = await self.call_list(
                                {"status": "all", "compact": compact, "redact_private": redact}
                            )
                            self.assertFalse(
                                result.structuredContent["ok"], result.structuredContent
                            )
                self.assertEqual(before, self.snapshot())

    async def test_gateway_legacy_identity_preserved_unredacted_and_blank_redacted(self):
        legacy_id = "legacy-card-42"
        self.put_cards([legacy_id])
        before = self.snapshot()
        search = await self.call(
            "agent_search", {"entity": "repair_order", "include_archived": True}
        )
        self.assertTrue(search.structuredContent["ok"], search.structuredContent)
        self.assertEqual(search.structuredContent["data"]["items"][0]["card_id"], "")
        self.assertNotIn("card_id_redacted", search.structuredContent["data"]["items"][0])
        for compact in (False, True):
            for redact in (False, True):
                result = await self.call_list(
                    {"status": "all", "compact": compact, "redact_private": redact}
                )
                self.assertTrue(result.structuredContent["ok"], result.structuredContent)
                rows = result.structuredContent["data"]["data"]["repair_orders"]
                self.assertEqual(rows[0]["card_id"], "" if redact else legacy_id)
                self.assertNotIn("card_id_redacted", rows[0])
        self.assertEqual(before, self.snapshot())

    async def test_missing_or_invalid_backend_identity_does_not_look_redacted(self):
        before = self.snapshot()
        for value in (None, "", " ", " padded ", "x" * 129, 0, []):
            for marker in (None, False, "true", True):
                if value == "" and marker is True:
                    continue  # The source asserts an intentionally hidden legacy ID.
                row = {"card_id": value, "number": "42", "status": "open"}
                if marker is not None:
                    row["card_id_redacted"] = marker
                with (
                    self.subTest(value=value, marker=marker),
                    patch.object(
                        self.board_api,
                        "list_repair_orders",
                        return_value={"ok": True, "data": {"repair_orders": [row]}},
                    ),
                ):
                    result = await self.call("agent_search", {"entity": "repair_order"})
                    self.assertFalse(result.structuredContent["ok"])
                    for compact in (False, True):
                        for redact in (False, True):
                            result = await self.call_list(
                                {"status": "all", "compact": compact, "redact_private": redact}
                            )
                            self.assertFalse(result.structuredContent["ok"])
                    self.assertEqual(before, self.snapshot())

        for marker in (None, True):
            row = {"number": "42", "status": "open"}
            if marker is True:
                row["card_id_redacted"] = marker
            with patch.object(
                self.board_api,
                "list_repair_orders",
                return_value={"ok": True, "data": {"repair_orders": [row]}},
            ):
                result = await self.call("agent_search", {"entity": "repair_order"})
                self.assertFalse(result.structuredContent["ok"])
                for compact in (False, True):
                    result = await self.call_list(
                        {"status": "all", "compact": compact, "redact_private": True}
                    )
                    self.assertFalse(result.structuredContent["ok"])
        self.assertEqual(before, self.snapshot())
