from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class SaveIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.logger = logging.getLogger(__name__)
        self.store = JsonStore(self.root / "state.json", self.logger)
        self.service = CardService(
            self.store,
            self.logger,
            attachments_dir=self.root / "attachments",
            repair_orders_dir=self.root / "repair-orders",
        )

    def create(self, title):
        return self.service.create_card({"title": title, "deadline": {"hours": 2}})["card"]["id"]

    def persisted(self):
        return JsonStore(self.root / "state.json", self.logger).read_bundle()

    def test_failed_card_creation_does_not_add_vehicle_to_shared_client(self):
        client_id = self.service.create_client({"display_name": "Vehicle owner"})["client"]["id"]
        original = self.store.read_bundle()
        client = next(c for c in original["clients"] if c.id == client_id)
        before = (self.root / "state.json").read_bytes()
        with patch.object(self.service, "_save_bundle", side_effect=OSError("prepare failed")):
            with self.assertRaises(OSError):
                self.service.create_card(
                    {
                        "title": "Rejected",
                        "client_id": client_id,
                        "vehicle": "Synthetic vehicle",
                        "create_vehicle_from_card": True,
                    }
                )
        self.assertEqual(client.vehicles, [])
        self.assertEqual((self.root / "state.json").read_bytes(), before)
        self.create("Unrelated accepted card")
        self.assertEqual(
            next(c for c in self.persisted()["clients"] if c.id == client_id).vehicles, []
        )

    def test_rejected_update_does_not_leak_to_cache_audit_or_later_write(self):
        for fast in (True, False):
            with (
                self.subTest(fast=fast),
                patch(
                    "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                    return_value=fast,
                ),
            ):
                card_id = self.create("Original")
                before = self.store.read_bundle()
                before_events = [event.to_dict() for event in before["events"]]
                before_bytes = (self.root / "state.json").read_bytes()
                with self.assertRaises(ServiceError):
                    self.service.update_card(
                        {
                            "card_id": card_id,
                            "title": "REJECTED TITLE",
                            "deadline": {"hours": "invalid"},
                        }
                    )
                current = self.store.read_bundle()
                self.assertEqual(
                    next(c for c in current["cards"] if c.id == card_id).title, "Original"
                )
                self.assertEqual([event.to_dict() for event in current["events"]], before_events)
                self.assertEqual((self.root / "state.json").read_bytes(), before_bytes)
                self.service.update_card({"card_id": card_id, "description": "Accepted"})
                saved = next(c for c in self.persisted()["cards"] if c.id == card_id)
                self.assertEqual(saved.title, "Original")
                self.assertEqual(saved.description, "Accepted")

    def test_created_and_moved_positions_survive_reopen_without_touching_neighbors(self):
        first, second, third = [self.create(title) for title in ("First", "Second", "Third")]
        before = {c.id: c.updated_at for c in self.store.read_bundle()["cards"]}
        column = self.store.read_bundle()["cards"][0].column
        self.service.move_card({"card_id": first, "column": column, "before_card_id": second})
        memory = sorted(self.store.read_bundle()["cards"], key=lambda c: c.position)
        reopened = sorted(self.persisted()["cards"], key=lambda c: c.position)
        self.assertEqual([c.id for c in memory], [third, first, second])
        self.assertEqual([c.id for c in reopened], [c.id for c in memory])
        for card in reopened:
            if card.id != first:
                self.assertEqual(card.updated_at, before[card.id])

    def test_post_commit_cleanup_failure_does_not_report_failed_save(self):
        card_id = self.create("Original")
        with patch.object(
            self.service,
            "_cleanup_runtime_artifacts_if_due",
            side_effect=PermissionError("cleanup"),
        ):
            result = self.service.update_card({"card_id": card_id, "title": "Committed"})
        self.assertEqual(result["card"]["title"], "Committed")
        self.assertEqual(
            next(c for c in self.persisted()["cards"] if c.id == card_id).title, "Committed"
        )

    def audit_files(self):
        directory = self.service._audit_archive.archive_dir
        return {p.name: p.read_bytes() for p in directory.glob("*.jsonl")}

    def order_files(self):
        return {p.name: p.read_bytes() for p in (self.root / "repair-orders").glob("*.txt")}

    def create_order(self):
        card_id = self.create("Original order")
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Synthetic client",
                    "works": [{"name": "Test", "quantity": "1", "price": "100"}],
                },
            }
        )
        return card_id

    def test_repair_order_read_reuses_unchanged_cards_without_writing(self):
        card_id = self.create_order()
        source = self.store.read_bundle()
        synchronize = self.service._synchronize_repair_order_numbers

        def check_cards(cards):
            self.assertEqual(len(cards), len(source["cards"]))
            for card, original in zip(cards, source["cards"], strict=True):
                self.assertIs(card, original)
            return synchronize(cards)

        with (
            patch.object(
                self.service, "_synchronize_repair_order_numbers", side_effect=check_cards
            ),
            patch.object(
                self.service, "_save_bundle", side_effect=AssertionError("unexpected save")
            ),
        ):
            result = self.service.list_repair_orders({"compact": True})
        self.assertEqual(result["repair_orders"][0]["card_id"], card_id)

    def test_rejected_client_link_keeps_order_files_and_success_publishes_new_details(self):
        card_id = self.create_order()
        client_id = self.service.create_client({"display_name": "New client"})["client"]["id"]
        before = self.order_files()
        state_before = (self.root / "state.json").read_bytes()
        payload = {
            "card_id": card_id,
            "client_id": client_id,
            "sync_fields": True,
            "overwrite_card_fields": True,
        }
        with patch.object(self.service, "_save_bundle", side_effect=OSError("rejected")):
            with self.assertRaises(OSError):
                self.service.link_card_to_client(payload)
        self.assertEqual(self.order_files(), before)
        self.assertEqual((self.root / "state.json").read_bytes(), state_before)
        self.service.link_card_to_client(payload)
        self.assertNotEqual(self.order_files(), before)
        self.assertTrue(any(b"New client" in content for content in self.order_files().values()))
        self.assertEqual(
            next(card for card in self.persisted()["cards"] if card.id == card_id).client_id,
            client_id,
        )

    def test_failed_legacy_order_number_assignment_preserves_retained_order(self):
        self.create_order()
        original = self.store.read_bundle()["cards"][0]
        # A retained legacy model can still need a missing number assigned on read.
        original.repair_order.number = ""
        before = (self.root / "state.json").read_bytes()
        with patch.object(self.service, "_save_bundle", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                self.service.list_repair_orders()
        self.assertEqual(original.repair_order.number, "")
        self.assertEqual((self.root / "state.json").read_bytes(), before)

    def test_move_reuses_unmodified_ledger_and_stock_objects(self):
        card_id = self.create_order()
        cashbox_id = self.service.create_cashbox({"name": "Synthetic cash"})["cashbox"]["id"]
        self.service.create_cash_transaction(
            {"cashbox_id": cashbox_id, "direction": "income", "amount": "20", "note": "Unrelated"}
        )
        self.service.save_inventory_item({"name": "Synthetic stock", "quantity": "10"})
        source = self.store.read_bundle()
        save = self.service._save_bundle

        def check_draft(bundle, **kwargs):
            for domain in ("cash_transactions", "inventory_items"):
                self.assertIsNot(bundle[domain], source[domain])
                for item, original in zip(bundle[domain], source[domain], strict=True):
                    self.assertIs(item, original)
            return save(bundle, **kwargs)

        ready_id = next(
            column["id"]
            for column in self.service.list_columns()["columns"]
            if column["label"] == "Готовые автомобили"
        )
        with patch.object(self.service, "_save_bundle", side_effect=check_draft):
            self.service.move_card({"card_id": card_id, "column": ready_id})
        persisted = self.persisted()
        self.assertEqual(persisted["cards"][0].repair_order.status, "ready")
        for domain in ("cash_transactions", "inventory_items"):
            self.assertEqual(persisted[domain], source[domain])

    def test_rejected_heavy_edit_never_appends_audit_details(self):
        card_id = self.create("Original")
        archives = self.audit_files()
        with self.assertRaises(ServiceError):
            self.service.update_card(
                {
                    "card_id": card_id,
                    "description": "Unaccepted " * 1000,
                    "deadline": {"hours": "invalid"},
                }
            )
        self.assertEqual(self.audit_files(), archives)

    def test_failed_state_write_restores_archive_and_preserves_order_derivatives(self):
        card_id = self.create_order()
        before = (self.root / "state.json").read_bytes()
        archives, files = self.audit_files(), self.order_files()
        original = self.store.read_bundle()
        original_card = next(c for c in original["cards"] if c.id == card_id)
        with patch.object(self.store, "_write_state", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.service.update_card(
                    {
                        "card_id": card_id,
                        "title": "Rejected order",
                        "description": "Unaccepted details " * 1000,
                    }
                )
        self.assertEqual((self.root / "state.json").read_bytes(), before)
        self.assertEqual(self.audit_files(), archives)
        self.assertEqual(self.order_files(), files)
        self.assertEqual(original_card.title, "Original order")

    def test_order_render_failure_is_before_state_or_audit_commit(self):
        card_id = self.create_order()
        before = (self.root / "state.json").read_bytes()
        archives = self.audit_files()
        with patch.object(
            self.service, "_render_repair_order_text", side_effect=ValueError("render failed")
        ):
            with self.assertRaises(ValueError):
                self.service.update_card({"card_id": card_id, "title": "Rejected"})
        self.assertEqual((self.root / "state.json").read_bytes(), before)
        self.assertEqual(self.audit_files(), archives)
        self.assertEqual(
            next(c for c in self.store.read_bundle()["cards"] if c.id == card_id).title,
            "Original order",
        )

    def test_order_publish_failure_does_not_repeat_write_and_read_repairs_derivative(self):
        card_id = self.create_order()
        with patch(
            "minimal_kanban.services.repair_order_artifacts.publish_text",
            side_effect=PermissionError("file busy"),
        ):
            result = self.service.update_card({"card_id": card_id, "title": "Saved title"})
        self.assertEqual(result["card"]["title"], "Saved title")
        self.assertEqual(
            next(c for c in self.persisted()["cards"] if c.id == card_id).title, "Saved title"
        )
        path, _ = self.service.get_repair_order_text_download(card_id)
        self.assertTrue(path.exists())
        self.assertIn("Saved title", path.name)

    def test_source_signature_cache_matches_full_projection_after_replacement_and_deletion(self):
        from copy import deepcopy

        from minimal_kanban.storage.change_feed_projection import (
            cached_crm_source_signatures,
            project_crm_source_signatures,
        )

        self.create_order()
        state = self.store._state_from_bundle(self.store.read_bundle())
        cache = {}
        for candidate in (state, state, deepcopy(state)):
            self.assertEqual(
                cached_crm_source_signatures(candidate, cache),
                project_crm_source_signatures(candidate),
            )
        changed = {**state, "cards": []}
        self.assertEqual(
            cached_crm_source_signatures(changed, cache), project_crm_source_signatures(changed)
        )
        self.assertFalse(cache["cards"])


if __name__ == "__main__":
    unittest.main()
