"""Existing order API replays keep their normalization and CAS behavior."""

import unittest
from pathlib import Path

from tests import test_api as api_fixtures


class RepairOrderTextReplayApiTests(unittest.TestCase):
    setUp = api_fixtures.ApiServerTests.setUp
    request = api_fixtures.ApiServerTests.request

    def create_card(self):
        status, result = self.request("/api/create_card", {"title": "Synthetic replay"})
        self.assertEqual(status, 200, result)
        return result["data"]["card"]

    def create_cashbox(self):
        status, result = self.request("/api/create_cashbox", {"name": "Наличный"})
        self.assertEqual(status, 200, result)
        return result["data"]["cashbox"]["id"]

    def assert_replay_and_cas(self, route, payload, old_version, saved):
        state_file = Path(self.temp_dir.name) / "state.json"
        before = state_file.read_bytes()
        transactions = self.store.read_bundle()["cash_transactions"]
        status, replay = self.request("/api/" + route, payload)
        self.assertEqual(status, 200, replay)
        self.assertFalse(replay["data"]["meta"]["changed"])
        self.assertEqual(replay["data"]["card"]["repair_order"], saved["repair_order"])
        self.assertEqual(state_file.read_bytes(), before)
        self.assertEqual(self.store.read_bundle()["cash_transactions"], transactions)

        status, stale = self.request(
            "/api/" + route, {**payload, "expected_updated_at": old_version}
        )
        self.assertEqual(status, 409, stale)
        self.assertEqual(stale["error"]["code"], "card_update_conflict")
        self.assertEqual(state_file.read_bytes(), before)
        self.assertEqual(self.store.read_bundle()["cash_transactions"], transactions)

        status, fresh = self.request(
            "/api/" + route, {**payload, "expected_updated_at": saved["updated_at"]}
        )
        self.assertEqual(status, 200, fresh)
        self.assertFalse(fresh["data"]["meta"]["changed"])
        self.assertEqual(fresh["data"]["card"]["repair_order"], saved["repair_order"])
        self.assertEqual(state_file.read_bytes(), before)
        self.assertEqual(self.store.read_bundle()["cash_transactions"], transactions)

    def test_order_and_card_update_replay_with_and_without_payment(self):
        cashbox_id = self.create_cashbox()
        for route in ("update_card", "update_repair_order"):
            for with_payment in (False, True):
                with self.subTest(route=route, with_payment=with_payment):
                    card = self.create_card()
                    row = {
                        "id": "synthetic-row",
                        "name": "N" * 241,
                        "partNumber": "P" * 161,
                        "quantity": "1",
                        "price": "10",
                    }
                    order = {"client": "C" * 161, "vehicle": "V" * 161, "materials": [row]}
                    if with_payment:
                        order["payments"] = [
                            {"id": "synthetic-payment", "amount": "1", "cashbox_id": cashbox_id}
                        ]
                    payload = {"card_id": card["id"], "repair_order": order}
                    status, first = self.request("/api/" + route, payload)
                    self.assertEqual(status, 200, first)
                    saved = first["data"]["card"]
                    actual = saved["repair_order"]
                    self.assertEqual(len(actual["client"]), 160)
                    self.assertEqual(len(actual["vehicle"]), 160)
                    self.assertEqual(len(actual["materials"][0]["catalog_number"]), 160)
                    self.assertEqual(len(actual["materials"][0]["name"]), 240)
                    if with_payment:
                        self.assertTrue(actual["payments"][0]["cash_transaction_id"])
                    self.assert_replay_and_cas(route, payload, card["updated_at"], saved)

    def test_row_replacement_replay_keeps_existing_payment(self):
        cashbox_id = self.create_cashbox()
        for section in ("works", "materials"):
            with self.subTest(section=section):
                card = self.create_card()
                status, paid = self.request(
                    "/api/update_repair_order",
                    {
                        "card_id": card["id"],
                        "repair_order": {
                            "payments": [
                                {"id": "synthetic-payment", "amount": "1", "cashbox_id": cashbox_id}
                            ]
                        },
                    },
                )
                self.assertEqual(status, 200, paid)
                payload = {
                    "card_id": card["id"],
                    "rows": [{"id": "synthetic-row", "name": "N" * 241, "partNumber": "P" * 161}],
                }
                route = "replace_repair_order_" + section
                status, result = self.request("/api/" + route, payload)
                self.assertEqual(status, 200, result)
                saved = result["data"]["card"]
                row = saved["repair_order"][section][0]
                self.assertEqual((len(row["name"]), len(row["catalog_number"])), (240, 160))
                self.assertTrue(saved["repair_order"]["payments"][0]["cash_transaction_id"])
                self.assert_replay_and_cas(
                    route, payload, paid["data"]["card"]["updated_at"], saved
                )
