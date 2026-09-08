from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.services.card_service import CardService, ServiceError
from minimal_kanban.storage.json_store import JsonStore


class BundleDraftTests(unittest.TestCase):
    def test_post_replace_stat_failure_preserves_archive_and_recovers_pending_feed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            logger = logging.getLogger(self.id())
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            store = JsonStore(state_file, logger)
            service = CardService(store, logger)
            card_id = service.create_card({"title": "Original"})["card"]["id"]
            before = state_file.read_bytes()
            original_signature = store._state_signature

            def fail_post_replace_verification():
                signature = original_signature()
                if signature is not None and state_file.read_bytes() != before:
                    return (*signature[:3], signature[3] + 1)
                return signature

            description = ("Committed description " * 100).strip()
            with patch.object(
                store, "_state_signature", side_effect=fail_post_replace_verification
            ):
                result = service.update_card({"card_id": card_id, "description": description})
            self.assertEqual(result["card"]["description"], description.strip())
            self.assertTrue(store.change_feed_store.has_pending_state_write())
            reloaded = JsonStore(state_file, logger)
            bundle = reloaded.read_bundle()
            self.assertEqual(bundle["cards"][0].description, description.strip())
            self.assertFalse(reloaded.change_feed_store.has_pending_state_write())
            event = next(item for item in bundle["events"] if item.action == "description_changed")
            details = service._audit_archive.load_details(
                event.details["full_details_ref"], event_id=event.id
            )
            self.assertIsNotNone(details)
            self.assertEqual(details["after"], description.strip())

    def test_failed_restore_does_not_reposition_neighbours_retained_by_readers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            logger = logging.getLogger(self.id())
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            store = JsonStore(state_file, logger)
            service = CardService(store, logger)
            ids = [service.create_card({"title": title})["card"]["id"] for title in ("A", "B", "C")]
            service.archive_card({"card_id": ids[1]})
            retained = next(card for card in store.read_bundle()["cards"] if card.id == ids[0])
            previous_position = retained.position
            with patch.object(store, "_write_state", side_effect=OSError("injected")):
                with self.assertRaises(OSError):
                    service.restore_card({"card_id": ids[1], "column": "in_progress"})
            self.assertEqual(retained.position, previous_position)
            persisted = JsonStore(state_file).read_bundle()
            self.assertEqual(
                next(card for card in persisted["cards"] if card.id == ids[0]).position,
                previous_position,
            )

    def test_earlier_draft_cannot_overwrite_commit_using_same_source_bundle(self) -> None:
        for fast_writes in (True, False):
            with self.subTest(fast_writes=fast_writes), tempfile.TemporaryDirectory() as tmp:
                state_file = Path(tmp) / "state.json"
                logger = logging.getLogger(self.id())
                logger.addHandler(logging.NullHandler())
                logger.propagate = False
                store = JsonStore(state_file, logger)
                service = CardService(store, logger)
                card_id = service.create_card({"title": "Original"})["card"]["id"]
                earlier = service._read_bundle_for_update(card_id=card_id)
                current = service._read_bundle_for_update(card_id=card_id)
                self.assertIs(earlier.source, current.source)
                current["cards"][0].title = "Committed"
                service._touch_card(current["cards"][0])
                service._save_bundle(
                    current,
                    columns=current["columns"],
                    cards=current["cards"],
                    events=current["events"],
                )
                earlier["cards"][0].title = "Stale"
                service._touch_card(earlier["cards"][0])
                with (
                    patch(
                        "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                        return_value=fast_writes,
                    ),
                    self.assertRaises(ServiceError) as raised,
                ):
                    service._save_bundle(
                        earlier,
                        columns=earlier["columns"],
                        cards=earlier["cards"],
                        events=earlier["events"],
                    )
                self.assertEqual(raised.exception.code, "state_write_conflict")
                self.assertEqual(JsonStore(state_file).read_bundle()["cards"][0].title, "Committed")


class InventoryPayrollDraftTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state_file = self.root / "state.json"
        logger = logging.getLogger(self.id())
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(self.state_file, logger)
        self.service = CardService(self.store, logger, repair_orders_dir=self.root / "orders")

    def assert_failed_save_is_isolated(self, operation, payload) -> None:
        retained = self.store.read_bundle()
        before = deepcopy(retained)
        state_bytes = self.state_file.read_bytes()
        files_before = {path: path.read_bytes() for path in self.root.rglob("*.txt")}
        with patch.object(self.store, "_write_state", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                operation(payload)
        self.assertEqual(retained, before)
        self.assertEqual(self.state_file.read_bytes(), state_bytes)
        self.assertEqual(
            {path: path.read_bytes() for path in self.root.rglob("*.txt")}, files_before
        )

    def test_inventory_failures_preserve_models_events_and_order_files(self) -> None:
        card_id = self.service.create_card({"title": "Inventory isolation"})["card"]["id"]
        item_id = self.service.save_inventory_item(
            {"name": "Oil", "quantity": "10", "cost_price": "100", "sale_price": "150"}
        )["item"]["id"]
        movement_id = self.service.write_off_inventory_item(
            {"item_id": item_id, "card_id": card_id, "quantity": "1.5"}
        )["movement"]["id"]
        for operation, payload in (
            (self.service.save_inventory_item, {"item_id": item_id, "name": "Rejected"}),
            (self.service.replenish_inventory_item, {"item_id": item_id, "quantity": "2"}),
            (
                self.service.write_off_inventory_item,
                {"item_id": item_id, "card_id": card_id, "quantity": "1"},
            ),
            (self.service.return_inventory_movement, {"movement_id": movement_id}),
        ):
            with self.subTest(operation=operation.__name__):
                self.assert_failed_save_is_isolated(operation, payload)

    def test_employee_failures_preserve_nested_settings_and_events(self) -> None:
        employee_id = self.service.save_employee(
            {"name": "Employee isolation", "work_percent": "30"}
        )["employee"]["id"]
        for operation, payload in (
            (
                self.service.save_employee,
                {"employee_id": employee_id, "name": "Rejected", "work_percent": "40"},
            ),
            (self.service.toggle_employee, {"employee_id": employee_id}),
            (self.service.delete_employee, {"employee_id": employee_id}),
        ):
            with self.subTest(operation=operation.__name__):
                self.assert_failed_save_is_isolated(operation, payload)


if __name__ == "__main__":
    unittest.main()
