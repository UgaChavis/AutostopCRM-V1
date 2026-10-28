from __future__ import annotations

# Fixture import selects this checkout and uses temporary APPDATA/state.
# ruff: noqa: I001
import json
from itertools import product

from tests.test_repair_order_gateway_read_safety import RepairOrderGatewayFixture
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import StateFileCorruptedError


class RepairOrderFinancialReworkTests(RepairOrderGatewayFixture):
    def set_order(self, payload):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        card = next(item for item in state["cards"] if item["id"] == self.card_id)
        card["repair_order"] = {"number": "42", "status": "open", **payload}
        self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    async def test_invalid_works_item_fails_closed_without_changes(self):
        self.set_order({"works": ["synthetic-invalid"]})
        before = self.snapshot()
        for compact, redact in product((False, True), repeat=2):
            with self.subTest(compact=compact, redact_private=redact):
                with self.assertRaises((StateFileCorruptedError, ServiceError)):
                    self.service.list_repair_orders(
                        {"status": "all", "compact": compact, "redact_private": redact}
                    )
                self.assertEqual(before, self.snapshot())
        result = await self.call("agent_search", {"entity": "repair_order"})
        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual(before, self.snapshot())

    async def test_damaged_financial_collections_and_amounts_fail_closed(self):
        cases = (
            {"works": "synthetic-invalid"},
            {"works": [{"name": "ok", "quantity": "1", "price": "10"}, "invalid"]},
            {"materials": ["synthetic-invalid"]},
            {"materials": {"row": "synthetic-invalid"}},
            {"payments": ["synthetic-invalid"]},
            {"payments": "synthetic-invalid"},
            {"payment_history": ["synthetic-invalid"]},
            {"works": [{"name": "x", "quantity": "not-a-number", "price": "10"}]},
            {"works": [{"name": "x", "quantity": "1", "price": "not-a-number"}]},
            {"works": [{"name": "x", "total": "not-a-number"}]},
            {"materials": [{"name": "x", "qty": "1", "price": "not-a-number"}]},
            {"payments": [{"id": "p1", "amount": "not-a-number"}]},
            {"payment_history": [{"id": "p1", "value": "not-a-number"}]},
            {"prepayment": "not-a-number"},
            {"advancePayment": "not-a-number"},
            {"works": [{"name": "x", "quantity": "1", "price": "9" * 41}]},
            {"payments": [{"id": "p1", "amount": "9" * 41}]},
            {"works": [{"name": "x", "quantity": "1", "price": "NaN"}]},
            {"payments": [{"id": "p1", "amount": "Infinity"}]},
            {"works": [{"name": "x", "quantity": "1", "price": True}]},
            {"works": [{"name": "x", "quantity": "1", "price": "1e999999"}]},
            {"payments": [{"id": "p1", "amount": "1e999999"}]},
            {"payments": [{"id": "empty"}], "prepayment": "not-a-number"},
            {"works": [{"name": "x", "quantity": "1", "price": "1"}] * 151},
            {"payments": [{"id": "p", "amount": "1"}] * 201},
        )
        for index, payload in enumerate(cases):
            with self.subTest(case=index):
                self.set_order(payload)
                before = self.snapshot()
                for compact, redact in product((False, True), repeat=2):
                    with self.assertRaises((StateFileCorruptedError, ServiceError)):
                        self.service.list_repair_orders(
                            {"status": "all", "compact": compact, "redact_private": redact}
                        )
                    self.assertEqual(before, self.snapshot())
                result = await self.call("agent_search", {"entity": "repair_order"})
                self.assertFalse(result.structuredContent["ok"])
                self.assertEqual(before, self.snapshot())

    async def test_supported_empty_and_legacy_aliases_remain_readable(self):
        cases = (
            ({"works": None, "materials": None, "payments": None}, "0", "0"),
            ({"works": [], "materials": [], "payments": []}, "0", "0"),
            ({"works": [{"name": "x", "quantity": None, "price": ""}]}, "0", "0"),
            ({"payments": [{"id": "legacy-empty"}]}, "0", "0"),
            (
                {
                    "works": [{"name": "x", "qty": "2", "price": "15,50", "sum": "31"}],
                    "materials": [{"name": "y", "quantity": "1", "price": "10"}],
                    "payment_history": [{"id": "p1", "value": "3"}],
                },
                "41",
                "3",
            ),
            ({"works": [{"name": "x", "qty": 2, "price": 5}], "advancePayment": "3"}, "10", "3"),
            (
                {
                    "works": [{"name": "x", "quantity": "2", "qty": "unused", "price": "5"}],
                    "payments": [{"id": "p", "amount": "3", "value": "unused"}],
                    "prepayment": "unused",
                    "payment_history": "unused",
                },
                "10",
                "3",
            ),
        )
        for payload, total, paid in cases:
            with self.subTest(payload=payload):
                self.set_order(payload)
                before = self.snapshot()
                for compact in (False, True):
                    result = self.service.list_repair_orders({"status": "all", "compact": compact})
                    item = result["repair_orders"][0]
                    self.assertEqual(item["grand_total"], total)
                    self.assertEqual(item["paid_total"], paid)
                    self.assertEqual(before, self.snapshot())
                gateway = await self.call("agent_search", {"entity": "repair_order"})
                self.assertTrue(gateway.structuredContent["ok"], gateway.structuredContent)
                item = gateway.structuredContent["data"]["items"][0]
                self.assertEqual(item["grand_total"], total)
                self.assertEqual(item["paid_total"], paid)
                self.assertEqual(before, self.snapshot())
