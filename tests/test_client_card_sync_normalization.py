"""Client link receipts reflect the normalized card written to storage."""

# The service fixture selects this checkout before importing application modules.
# ruff: noqa: I001
import json

from tests.services_case import CardServiceCase
from minimal_kanban.services.errors import ServiceError


class ClientCardSyncNormalizationTests(CardServiceCase):
    def raw_card(self, card_id):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        return next(card for card in state["cards"] if card["id"] == card_id)

    def assert_receipt_matches_storage(self, receipt, card_id):
        raw = self.raw_card(card_id)
        readback = self.service.get_card({"card_id": card_id})["card"]
        for key in ("customer_name", "customer_phone", "customer_phones"):
            self.assertEqual(receipt["vehicle_profile"][key], raw["vehicle_profile"][key])
            self.assertEqual(receipt["vehicle_profile"][key], readback["vehicle_profile"][key])
        for key in ("client", "phone"):
            self.assertEqual(receipt["repair_order"][key], raw["repair_order"][key])
            self.assertEqual(receipt["repair_order"][key], readback["repair_order"][key])

    def test_link_normalizes_each_target_before_comparison_and_replay(self):
        cases = (
            {"display_name": "N" * 119},
            {"display_name": "N" * 120},
            {"display_name": "N" * 121},
            {"display_name": "N" * 150},
            {"display_name": "A\u0085\u0085B  C"},
            {"last_name": "L" * 120, "first_name": "F" * 120, "middle_name": "M" * 120},
        )
        for index, fields in enumerate(cases):
            for overwrite in (False, True):
                with self.subTest(index=index, overwrite=overwrite):
                    client = self.service.create_client({**fields, "phone": "+7 999 111-22-33"})[
                        "client"
                    ]
                    card = self.service.create_card({"title": "SYNTHETIC LINK"})["card"]
                    order = self.service.update_repair_order(
                        {"card_id": card["id"], "repair_order": {"reason": "SYNTHETIC"}}
                    )["card"]
                    body = {
                        "card_id": card["id"],
                        "client_id": client["id"],
                        "overwrite_card_fields": overwrite,
                        "expected_card_updated_at": order["updated_at"],
                    }
                    result = self.service.link_card_to_client(body)
                    linked = result["card"]
                    normalized = " ".join(client["name"].split())
                    self.assertEqual(linked["vehicle_profile"]["customer_name"], normalized[:120])
                    self.assertEqual(linked["repair_order"]["client"], normalized[:160])
                    self.assertEqual(result["client"]["name"], client["name"])
                    self.assert_receipt_matches_storage(linked, card["id"])
                    before = self.state_file.read_bytes()
                    replay = self.service.link_card_to_client(
                        {**body, "expected_card_updated_at": linked["updated_at"]}
                    )
                    self.assertFalse(replay["meta"]["changed"])
                    self.assertEqual(before, self.state_file.read_bytes())
                    with self.assertRaises(ServiceError) as error:
                        self.service.link_card_to_client(body)
                    self.assertEqual(error.exception.code, "card_update_conflict")
                    self.assertEqual(before, self.state_file.read_bytes())

    def test_create_card_and_existing_fields_keep_link_semantics(self):
        client = self.service.create_client(
            {"display_name": "N" * 150, "phone": "+7 999 111-22-33"}
        )["client"]
        created = self.service.create_card(
            {"title": "SYNTHETIC CREATE", "client_id": client["id"]}
        )["card"]
        self.assertEqual(created["vehicle_profile"]["customer_name"], "N" * 120)
        self.assert_receipt_matches_storage(created, created["id"])
        preserved = self.service.create_card(
            {
                "title": "SYNTHETIC EXISTING",
                "vehicle_profile": {"customer_name": "EXISTING NAME"},
            }
        )["card"]
        result = self.service.link_card_to_client(
            {"card_id": preserved["id"], "client_id": client["id"], "overwrite_card_fields": False}
        )
        self.assertEqual(result["card"]["vehicle_profile"]["customer_name"], "EXISTING NAME")
        self.assert_receipt_matches_storage(result["card"], preserved["id"])
        overwritten = self.service.link_card_to_client(
            {"card_id": preserved["id"], "client_id": client["id"], "overwrite_card_fields": True}
        )
        self.assertEqual(overwritten["card"]["vehicle_profile"]["customer_name"], "N" * 120)
        self.assert_receipt_matches_storage(overwritten["card"], preserved["id"])
        unsynced = self.service.create_card({"title": "SYNTHETIC UNSYNCED"})["card"]
        result = self.service.link_card_to_client(
            {"card_id": unsynced["id"], "client_id": client["id"], "sync_fields": False}
        )
        self.assertEqual(result["card"]["vehicle_profile"]["customer_name"], "")
        self.assert_receipt_matches_storage(result["card"], unsynced["id"])
