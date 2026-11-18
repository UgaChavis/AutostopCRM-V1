from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.logging_setup import close_logger
from minimal_kanban.models import CashBox, CashTransaction
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore, StateFileCorruptedError


class FinanceClientReadIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_file = Path(self.temp_dir.name) / "state.json"
        logger = logging.getLogger(self.id())
        close_logger(logger)
        self.addCleanup(close_logger, logger)
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(self.state_file, logger)
        self.service = CardService(self.store, logger)

    def test_cold_cashbox_detail_uses_list_decode_and_detects_intervening_write(self) -> None:
        box_id = self.service.create_cashbox({"name": "Cold read"})["cashbox"]["id"]
        payload = {"cashbox_id": box_id, "compact": True, "transaction_limit": 100}
        for changed in (False, True):
            with self.subTest(changed=changed):
                reader = JsonStore(self.state_file)
                self.addCleanup(reader.close)
                service = CardService(reader, logging.getLogger(self.id()))
                with patch.object(reader, "_decode_state", wraps=reader._decode_state) as decode:
                    service.list_cashboxes({"limit": 200})
                    self.assertEqual(decode.call_count, 1)
                    if changed:
                        self.service.create_cash_transaction(
                            {"cashbox_id": box_id, "direction": "income", "amount_minor": 750}
                        )
                    detail = service.get_cashbox(payload)
                    self.assertEqual(decode.call_count, 2 if changed else 1)
                self.assertEqual(
                    detail["cashbox"]["statistics"]["balance_minor"], 750 if changed else 0
                )
                self.assertIsNone(reader._pending_cashbox_state)

    def test_late_list_decode_does_not_discard_a_newer_committed_bundle(self) -> None:
        box_id = self.service.create_cashbox({"name": "Cold read"})["cashbox"]["id"]
        reader = JsonStore(self.state_file)
        self.addCleanup(reader.close)
        service = CardService(reader, logging.getLogger(self.id()))
        decode = reader._decode_state
        wrote = False

        def write_after_capture(raw):
            nonlocal wrote
            state = decode(raw)
            if not wrote:
                wrote = True
                service.create_cash_transaction(
                    {"cashbox_id": box_id, "direction": "income", "amount_minor": 410}
                )
            return state

        with patch.object(reader, "_decode_state", side_effect=write_after_capture):
            listed = service.list_cashboxes({})["cashboxes"][0]
        self.assertEqual(listed["statistics"]["balance_minor"], 0)
        with patch.object(reader, "_decode_state", side_effect=AssertionError("unexpected decode")):
            detail = service.get_cashbox({"cashbox_id": box_id})
        self.assertEqual(detail["cashbox"]["statistics"]["balance_minor"], 410)

    def test_cold_detail_preserves_corrupt_replacement_after_a_valid_list(self) -> None:
        box_id = self.service.create_cashbox({"name": "Cold read"})["cashbox"]["id"]
        reader = JsonStore(self.state_file)
        self.addCleanup(reader.close)
        service = CardService(reader, logging.getLogger(self.id()))
        service.list_cashboxes({})
        corrupt = b'{"cash_transactions":'
        self.state_file.write_bytes(corrupt)
        with self.assertRaises(StateFileCorruptedError):
            service.get_cashbox({"cashbox_id": box_id})
        self.assertEqual(self.state_file.with_suffix(".corrupted.json").read_bytes(), corrupt)

    def test_cashbox_detail_detects_atomic_replacement_with_matching_size_and_mtime(self) -> None:
        box_id = self.service.create_cashbox({"name": "Cold read"})["cashbox"]["id"]
        self.service.list_cashboxes({})
        old_stat = self.state_file.stat()
        raw = self.state_file.read_bytes()
        self.assertIn(b"Cold read", raw)
        replacement = self.state_file.with_suffix(".tmp")
        replacement.write_bytes(raw.replace(b"Cold read", b"Warm read"))
        os.utime(replacement, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        replacement.replace(self.state_file)
        with patch.object(self.store, "_decode_state", wraps=self.store._decode_state) as decode:
            listed = self.service.list_cashboxes({})["cashboxes"][0]
            detail = self.service.get_cashbox({"cashbox_id": box_id})
            self.assertEqual(decode.call_count, 1)
        self.assertEqual(listed["name"], "Warm read")
        self.assertEqual(detail["cashbox"]["name"], "Warm read")

    @patch("minimal_kanban.storage.json_store._SNAPSHOT_REQUIRES_FILE_LOCK", True)
    def test_cashbox_detail_uses_windows_digest_when_file_metadata_matches(self) -> None:
        box_id = self.service.create_cashbox({"name": "Cold read"})["cashbox"]["id"]
        self.service.create_cash_transaction(
            {"cashbox_id": box_id, "direction": "income", "amount_minor": 12341}
        )
        self.service.list_cashboxes({})
        old_stat = self.state_file.stat()
        old_signature = self.store._repair_order_file_signature()
        raw = self.state_file.read_bytes()
        self.assertIn(b"12341", raw)
        self.state_file.write_bytes(raw.replace(b"12341", b"54321"))
        os.utime(self.state_file, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        with (
            patch.object(self.store, "_repair_order_file_signature", return_value=old_signature),
            patch.object(self.store, "_decode_state", wraps=self.store._decode_state) as decode,
        ):
            listed = self.service.list_cashboxes({})["cashboxes"][0]
            detail = self.service.get_cashbox({"cashbox_id": box_id})
            self.assertEqual(decode.call_count, 1)
        self.assertEqual(listed["statistics"]["balance_minor"], 54321)
        self.assertEqual(detail["transactions"][0]["amount_minor"], 54321)

    def test_cashbox_summaries_preserve_minor_units_rounding_and_latest_instant(self) -> None:
        bundle = self.store.read_bundle()
        bundle["cashboxes"] = [
            CashBox.from_dict({"id": name, "name": name}) for name in ("first", "second", "empty")
        ]
        bundle["cash_transactions"] = [
            CashTransaction.from_dict(
                {
                    "id": name,
                    "cashbox_id": box,
                    "direction": direction,
                    "amount_minor": amount,
                    "created_at": timestamp,
                }
            )
            for name, box, direction, amount, timestamp in (
                ("a", "first", "income", 149, "2026-01-01T12:00:00+07:00"),
                ("b", "first", "expense", 200, "2026-01-01T06:00:00+00:00"),
                ("c", "second", "income", 150, "2026-01-01T05:00:00+00:00"),
                ("d", "second", "income", 151, "2026-01-01T05:00:00+00:00"),
            )
        ]
        self.store.write_bundle(**bundle)
        with patch.object(
            self.service,
            "_cashbox_transactions",
            side_effect=AssertionError("summary must not sort"),
        ):
            result = self.service.list_cashboxes({})
        summaries = {item["id"]: item["statistics"] for item in result["cashboxes"]}
        self.assertEqual(
            summaries["first"],
            {
                "transactions_total": 2,
                "income_total_minor": 149,
                "income_total_display": "1 ₽",
                "expense_total_minor": 200,
                "expense_total_display": "2 ₽",
                "balance_minor": -51,
                "balance_display": "-1 ₽",
                "balance_sign": "negative",
                "last_transaction_at": "2026-01-01T06:00:00+00:00",
            },
        )
        self.assertEqual(summaries["second"]["balance_minor"], 301)
        self.assertEqual(summaries["second"]["balance_display"], "3 ₽")
        self.assertEqual(summaries["empty"]["transactions_total"], 0)
        self.assertEqual(summaries["empty"]["balance_display"], "0 ₽")
        self.assertIsNone(summaries["empty"]["last_transaction_at"])
        limited = self.service.list_cashboxes({"limit": 1})
        self.assertEqual(limited["cashboxes"][0], result["cashboxes"][0])
        self.assertTrue(limited["meta"]["has_more"])
        detail = self.service.get_cashbox({"cashbox_id": "first", "transaction_limit": 1})
        self.assertEqual(detail["cashbox"]["statistics"], summaries["first"])
        self.assertEqual([item["id"] for item in detail["transactions"]], ["b"])

    def _linked_client(self) -> tuple[str, str]:
        client = self.service.create_client({"display_name": "Index owner"})["client"]
        card = self.service.create_card(
            {"title": "Linked history", "vehicle": "OldVehicle", "client_id": client["id"]}
        )["card"]
        return client["id"], card["id"]

    def test_related_normalization_is_reused_then_invalidated_by_card_change(self) -> None:
        client_id, card_id = self._linked_client()
        self.assertEqual(
            self.service.search_clients({"query": "OldVehicle"})["clients"][0]["id"], client_id
        )
        cached_index = self.service._client_related_search_index_cache[1]
        self.service.search_clients({"query": "unmatched-query"})
        self.assertIs(self.service._client_related_search_index_cache[1], cached_index)
        self.service.update_card({"card_id": card_id, "vehicle": "NewVehicle"})
        self.assertEqual(
            self.service.search_clients({"query": "NewVehicle"})["clients"][0]["id"], client_id
        )
        self.assertIsNot(self.service._client_related_search_index_cache[1], cached_index)

    def test_external_reload_with_unchanged_revisions_invalidates_related_index(self) -> None:
        client_id, card_id = self._linked_client()
        self.service.search_clients({"query": "OldVehicle"})
        external = JsonStore(self.state_file)
        bundle = external.read_bundle()
        next(card for card in bundle["cards"] if card.id == card_id).vehicle = "ExternalVehicle"
        external.write_bundle(**bundle)
        self.assertEqual(
            self.service.search_clients({"query": "ExternalVehicle"})["clients"][0]["id"], client_id
        )

    def test_client_patch_invalidates_direct_index_without_changing_ranking_rules(self) -> None:
        client_id = self.service.create_client({"display_name": "Index owner"})["client"]["id"]
        self.service.search_clients({"query": "Index owner"})
        self.service.update_client({"client_id": client_id, "display_name": "Renamed customer"})
        self.assertEqual(
            self.service.search_clients({"query": "Renamed customer"})["clients"][0]["id"],
            client_id,
        )
        self.assertEqual(self.service.search_clients({"query": "Index owner"})["clients"], [])

    def test_failed_link_does_not_mutate_card_or_client_referenced_by_readers(self) -> None:
        client = self.service.create_client({"display_name": "Target client"})["client"]
        card = self.service.create_card({"title": "Unlinked", "vehicle": "Test vehicle"})["card"]
        source = self.store.read_bundle()
        original_client = next(item for item in source["clients"] if item.id == client["id"])
        original_card = next(item for item in source["cards"] if item.id == card["id"])
        before_client = original_client.to_storage_dict()
        before_card = original_card.to_storage_dict()
        before_events = list(source["events"])
        before_file = self.state_file.read_bytes()
        with patch.object(
            self.service, "_sync_card_client_fields", side_effect=RuntimeError("injected")
        ):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.service.link_card_to_client(
                    {
                        "card_id": card["id"],
                        "client_id": client["short_id"],
                        "create_vehicle_from_card": True,
                    }
                )
        self.assertEqual(original_client.to_storage_dict(), before_client)
        self.assertEqual(original_card.to_storage_dict(), before_card)
        self.assertEqual(source["events"], before_events)
        self.assertEqual(self.state_file.read_bytes(), before_file)
        self.service.update_card({"card_id": card["id"], "description": "Independent change"})
        persisted = JsonStore(self.state_file).read_bundle()
        self.assertEqual(persisted["cards"][0].client_id, "")
        self.assertEqual(persisted["clients"][0].vehicles, [])

    def test_failed_finance_write_does_not_publish_cashbox_revision_or_transaction(self) -> None:
        cashbox = self.service.create_cashbox({"name": "Test box"})["cashbox"]
        source = self.store.read_bundle()
        original = next(item for item in source["cashboxes"] if item.id == cashbox["id"])
        before = original.to_storage_dict()
        with patch.object(self.service, "_save_bundle", side_effect=OSError("injected")):
            with self.assertRaisesRegex(OSError, "injected"):
                self.service.create_cash_transaction(
                    {"cashbox_id": cashbox["id"], "direction": "income", "amount_minor": 123}
                )
        self.assertEqual(original.to_storage_dict(), before)
        self.assertEqual(source["cash_transactions"], [])
        self.assertEqual(self.store.read_bundle()["cash_transactions"], [])


if __name__ == "__main__":
    unittest.main()
