from __future__ import annotations

# The fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import StateFileCorruptedError


class ClientContextReadSafetyTests(CardServiceCase):
    def setUp(self):
        self.app_data = tempfile.TemporaryDirectory(prefix="m2-client-context-appdata-")
        self.addCleanup(self.app_data.cleanup)
        environment = patch.dict(os.environ, {"APPDATA": self.app_data.name})
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()
        self.service = self._build_service()
        client = self.service.create_client(
            {
                "display_name": "SYNTHETIC_CLIENT",
                "vehicles": [{"vehicle": "SYNTHETIC_VEHICLE"}],
            }
        )["client"]
        self.client_id = client["id"]
        self.vehicle_id = client["vehicles"][0]["id"]
        self.card_ids = []
        for index in range(3):
            card = self.service.create_card(
                {
                    "title": f"SYNTHETIC_ORDER_{index}",
                    "client_id": self.client_id,
                    "client_vehicle_id": self.vehicle_id,
                }
            )["card"]
            self.card_ids.append(card["id"])
            self.service.update_card(
                {
                    "card_id": card["id"],
                    "repair_order": {
                        "client": "SYNTHETIC_CLIENT",
                        "works": [
                            {
                                "name": "SYNTHETIC_WORK",
                                "quantity": "1",
                                "price": str((index + 1) * 100),
                            }
                        ],
                    },
                }
            )

        # Put two derived files into states that the old read would rewrite.
        cards = {card.id: card for card in self.store.read_bundle()["cards"]}
        self.newest_path = self.service._repair_order_text_path(cards[self.card_ids[-1]])
        oldest_path = self.service._repair_order_text_path(cards[self.card_ids[0]])
        self.newest_path.unlink(missing_ok=True)
        oldest_path.write_text("SYNTHETIC_STALE_ARTIFACT", encoding="utf-8")
        (oldest_path.parent / "unrelated.txt").write_text("SYNTHETIC_UNRELATED", encoding="utf-8")

    def snapshot(self):
        result = {}
        for label, root in (
            ("state", Path(self.temp_dir.name)),
            ("appdata", Path(self.app_data.name)),
        ):
            for path in root.rglob("*"):
                stat = path.stat()
                key = (label, str(path.relative_to(root)))
                metadata = (stat.st_ino, stat.st_mode, stat.st_mtime_ns, stat.st_ctime_ns)
                if path.is_file():
                    result[key] = ("file", metadata, path.read_bytes())
                elif path.is_dir():
                    result[key] = ("directory", metadata)
        return result

    def edit_state(self, update):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        update(state)
        self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def selected_client(self, state):
        return next(client for client in state["clients"] if client["id"] == self.client_id)

    def test_profile_preserves_order_limit_shape_totals_links_and_all_files(self):
        before = self.snapshot()
        self.assertFalse(self.newest_path.exists())
        for limit in (1, 3, 1):
            with self.subTest(order_limit=limit):
                result = self.service.get_client(
                    {"client_id": self.client_id, "order_limit": limit}
                )
                self.assertEqual(set(result), {"client", "vehicles", "repair_orders", "meta"})
                self.assertEqual(result["client"]["id"], self.client_id)
                self.assertEqual(result["client"]["display_name"], "SYNTHETIC_CLIENT")
                self.assertEqual(result["client"]["stats"]["repair_orders_total"], 3)
                self.assertTrue(
                    any(vehicle["id"] == self.vehicle_id for vehicle in result["vehicles"])
                )
                self.assertEqual(
                    [order["card_id"] for order in result["repair_orders"]],
                    list(reversed(self.card_ids))[:limit],
                )
                self.assertEqual(
                    [order["grand_total"] for order in result["repair_orders"]],
                    ["300", "200", "100"][:limit],
                )
                self.assertEqual(result["meta"]["repair_orders_total"], 3)
                self.assertEqual(result["meta"]["repair_orders_returned"], limit)
                self.assertEqual(result["meta"]["order_limit"], limit)
                self.assertEqual(result["repair_orders"][0]["file_path"], str(self.newest_path))
                self.assertFalse(self.newest_path.exists())
                self.assertTrue(before == self.snapshot(), "Profile read changed persisted files")

    def test_legacy_unnormalized_state_is_not_repaired_by_client_read(self):
        self.edit_state(lambda state: state.update({"settings": "SYNTHETIC_LEGACY"}))
        before = self.snapshot()
        for _ in range(2):
            result = self.service.get_client({"client_id": self.client_id, "order_limit": 1})
            self.assertEqual(result["client"]["id"], self.client_id)
            self.assertEqual(result["meta"]["repair_orders_total"], 3)
            self.assertTrue(before == self.snapshot(), "Profile read changed persisted files")

    def test_unknown_client_does_not_repair_legacy_state_or_change_feed(self):
        self.edit_state(lambda state: state.update({"settings": "SYNTHETIC_LEGACY"}))
        before = self.snapshot()
        for _ in range(2):
            with self.assertRaises(ServiceError) as raised:
                self.service.get_client({"client_id": "SYNTHETIC_UNKNOWN_CLIENT"})
            self.assertEqual(raised.exception.code, "not_found")
            self.assertTrue(before == self.snapshot(), "Profile read changed persisted files")

    def test_missing_selected_client_id_does_not_invent_or_persist_identity(self):
        self.edit_state(lambda state: self.selected_client(state).update({"id": ""}))
        before = self.snapshot()
        for _ in range(2):
            with self.assertRaises((StateFileCorruptedError, ServiceError)):
                self.service.get_client({"client_id": self.client_id})
            self.assertTrue(before == self.snapshot(), "Profile read changed persisted files")

    def test_missing_selected_client_timestamps_fail_without_invented_revisions(self):
        original = json.loads(self.state_file.read_text(encoding="utf-8"))
        for field in ("created_at", "updated_at"):
            with self.subTest(field=field):
                state = deepcopy(original)
                self.selected_client(state)[field] = ""
                self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                before = self.snapshot()
                for _ in range(2):
                    with self.assertRaises((StateFileCorruptedError, ServiceError)):
                        self.service.get_client({"client_id": self.client_id})
                    self.assertTrue(
                        before == self.snapshot(), "Profile read changed persisted files"
                    )

    def test_missing_stored_vehicle_id_fails_without_invented_identity(self):
        self.edit_state(lambda state: self.selected_client(state)["vehicles"][0].update({"id": ""}))
        before = self.snapshot()
        for _ in range(2):
            with self.assertRaises((StateFileCorruptedError, ServiceError)):
                self.service.get_client({"client_id": self.client_id})
            self.assertTrue(before == self.snapshot(), "Profile read changed persisted files")

    def test_stored_legacy_vehicle_id_alias_is_stable_without_persisting_it(self):
        original = json.loads(self.state_file.read_text(encoding="utf-8"))
        for include_empty_id in (False, True):
            with self.subTest(include_empty_id=include_empty_id):
                state = deepcopy(original)
                vehicle = self.selected_client(state)["vehicles"][0]
                vehicle["client_vehicle_id"] = vehicle.pop("id")
                if include_empty_id:
                    vehicle["id"] = ""
                self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                before = self.snapshot()
                for _ in range(2):
                    result = self.service.get_client(
                        {"client_id": self.client_id, "order_limit": 1}
                    )
                    self.assertEqual(result["client"]["vehicles"][0]["id"], self.vehicle_id)
                    self.assertTrue(
                        any(item["id"] == self.vehicle_id for item in result["vehicles"])
                    )
                    self.assertEqual(result["repair_orders"][0]["card_id"], self.card_ids[-1])
                    self.assertTrue(
                        before == self.snapshot(), "Profile read changed persisted files"
                    )

    def test_missing_related_card_identity_or_timestamps_do_not_invent_links(self):
        original = json.loads(self.state_file.read_text(encoding="utf-8"))
        for field in ("id", "created_at", "updated_at"):
            with self.subTest(field=field):
                state = deepcopy(original)
                card = next(card for card in state["cards"] if card["id"] == self.card_ids[0])
                card[field] = ""
                self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                before = self.snapshot()
                for _ in range(2):
                    with self.assertRaises((StateFileCorruptedError, ServiceError)):
                        self.service.get_client({"client_id": self.client_id})
                    self.assertTrue(
                        before == self.snapshot(), "Profile read changed persisted files"
                    )

    def test_related_legacy_payment_is_signalled_and_output_is_detached(self):
        def update(state):
            card = next(card for card in state["cards"] if card["id"] == self.card_ids[0])
            card["repair_order"]["payments"] = [{"amount": "7"}]

        self.edit_state(update)
        before = self.snapshot()
        first = self.service.get_client({"client_id": self.client_id, "order_limit": 3})
        self.assertIn("repair_order_payment_identity_unverified", first["meta"]["read_warnings"])
        first["client"]["vehicles"][0]["vehicle"] = "MUTATED"
        first["repair_orders"][0]["payments"].append({"id": "MUTATED"})
        second = self.service.get_client({"client_id": self.client_id, "order_limit": 3})
        self.assertEqual(second["client"]["vehicles"][0]["vehicle"], "SYNTHETIC_VEHICLE")
        self.assertFalse(
            any(item.get("id") == "MUTATED" for item in second["repair_orders"][0]["payments"])
        )
        self.assertEqual(before, self.snapshot())

    def test_related_card_with_truncated_link_fails_closed(self):
        state = json.loads(self.state_file.read_text())
        client = self.selected_client(state)
        client["id"] = "x" * 128
        card = next(card for card in state["cards"] if card["id"] == self.card_ids[0])
        card["client_id"] = "x" * 128 + "A"
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(StateFileCorruptedError):
            self.service.get_client({"client_id": "x" * 128})
        self.assertEqual(before, self.snapshot())

    def test_text_match_without_exact_client_link_is_marked_unverified(self):
        for unrelated_id in ("", "SYNTHETIC_DIFFERENT_CLIENT"):
            with self.subTest(client_id=unrelated_id):
                self.edit_state(
                    lambda state: next(
                        card for card in state["cards"] if card["id"] == self.card_ids[0]
                    ).update({"client_id": unrelated_id})
                )
                before = self.snapshot()
                result = self.service.get_client({"client_id": self.client_id})
                self.assertIn(self.card_ids[0], [row["card_id"] for row in result["repair_orders"]])
                self.assertIn(
                    "related_card_client_match_unverified", result["meta"]["read_warnings"]
                )
                self.assertEqual(before, self.snapshot())
