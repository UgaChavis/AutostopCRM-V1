import logging
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.repair_order_artifacts import publish_text  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class RepairOrderArtifactCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = JsonStore(self.root / "state.json")
        self.service = CardService(
            self.store,
            logging.getLogger(__name__),
            attachments_dir=self.root / "attachments",
            repair_orders_dir=self.root / "repair-orders",
        )
        card_id = self.service.create_card({"title": "Synthetic order"})["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Synthetic client",
                    "works": [{"name": "Test", "quantity": "1", "price": "100"}],
                },
            }
        )
        self.card = next(card for card in self.store.read_bundle()["cards"] if card.id == card_id)
        self.path = self.service._ensure_repair_order_text_file(self.card)
        self.content = self.service._render_repair_order_text(self.card, self.path)

    def test_publication_uses_lf_bytes_and_removes_temporary_file(self):
        path = self.root / "nested" / "text.txt"
        publish_text(path, "first\nsecond\n")
        self.assertEqual(path.read_bytes(), b"first\nsecond\n")
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_verified_unchanged_file_skips_render_read_and_publication(self):
        signature = self.service._repair_order_file_signature(self.path)
        with (
            patch.object(self.service, "_render_repair_order_text", side_effect=AssertionError),
            patch.object(self.service, "_read_repair_order_text_file", side_effect=AssertionError),
            patch(
                "minimal_kanban.services.repair_order_artifacts.publish_text",
                side_effect=AssertionError,
            ),
        ):
            self.assertEqual(self.service._ensure_repair_order_text_file(self.card), self.path)
        self.assertEqual(self.service._repair_order_file_signature(self.path), signature)

    def test_legacy_crlf_file_is_verified_without_rewriting(self):
        self.path.write_bytes(self.content.replace("\n", "\r\n").encode("utf-8"))
        signature = self.service._repair_order_file_signature(self.path)
        with patch(
            "minimal_kanban.services.repair_order_artifacts.publish_text",
            side_effect=AssertionError("legacy line endings need no rewrite"),
        ):
            self.service._ensure_repair_order_text_file(self.card)
            self.service._ensure_repair_order_text_file(self.card)
        self.assertEqual(self.service._repair_order_file_signature(self.path), signature)
        self.assertIn(b"\r\n", self.path.read_bytes())

    def test_external_corruption_deletion_and_invalid_utf8_regenerate(self):
        for contents in (b"corrupt", None, b"\xff\xfe"):
            with self.subTest(contents=contents):
                self.service._ensure_repair_order_text_file(self.card)
                if contents is None:
                    self.path.unlink()
                else:
                    self.path.write_bytes(contents)
                self.service._ensure_repair_order_text_file(self.card)
                self.assertEqual(self.path.read_bytes(), self.content.encode("utf-8"))

    def test_content_token_detects_model_changes_even_without_new_revision(self):
        for change in ("order", "heading", "timestamp"):
            with self.subTest(change=change):
                card = deepcopy(self.card)
                if change == "order":
                    card.repair_order.comment = "Changed comment without timestamp"
                elif change == "heading":
                    card.title = "Changed heading"
                else:
                    card.updated_at = "2026-09-08T12:00:00+00:00"
                path = self.service._ensure_repair_order_text_file(card)
                expected = self.service._render_repair_order_text(card, path)
                self.assertNotEqual(expected, self.content)
                self.assertEqual(path.read_bytes(), expected.encode("utf-8"))

    def test_force_bypasses_verified_cache(self):
        with patch(
            "minimal_kanban.services.repair_order_artifacts.publish_text", wraps=publish_text
        ) as publish:
            self.service._ensure_repair_order_text_file(self.card, force=True)
        publish.assert_called_once_with(self.path, self.content)
        self.assertNotIn(self.card.id, self.service._repair_order_verified_files)

    def test_list_payment_label_does_not_serialize_entire_order(self):
        order = self.card.repair_order
        expected = order.to_dict()["payment_method_label"]
        with patch.object(type(order), "to_dict", side_effect=AssertionError):
            item = self.service._serialize_repair_order_list_item(self.card)
        self.assertEqual(item["payment_method_label"], expected)

    def test_text_reads_preserve_model_identity_and_do_not_write_state(self):
        synchronize = self.service._synchronize_repair_order_numbers

        def check_original(cards):
            self.assertIs(next(card for card in cards if card.id == self.card.id), self.card)
            return synchronize(cards)

        with (
            patch.object(
                self.service, "_synchronize_repair_order_numbers", side_effect=check_original
            ),
            patch.object(self.service, "_save_bundle", side_effect=AssertionError),
        ):
            payload = self.service.get_repair_order_text({"card_id": self.card.id})
            path, name = self.service.get_repair_order_text_download(self.card.id)
        self.assertEqual(payload["text"], self.content)
        self.assertEqual((path, name), (self.path, self.path.name))

    def test_file_changed_during_verification_is_not_cached(self):
        self.service._repair_order_verified_files.clear()
        read = self.service._read_repair_order_text_file

        def replace_after_read(path):
            content = read(path)
            path.write_bytes(b"changed concurrently")
            return content

        with patch.object(
            self.service, "_read_repair_order_text_file", side_effect=replace_after_read
        ):
            self.service._ensure_repair_order_text_file(self.card)
        self.assertNotIn(self.card.id, self.service._repair_order_verified_files)
        self.service._ensure_repair_order_text_file(self.card)
        self.assertEqual(self.path.read_bytes(), self.content.encode("utf-8"))

    def test_verification_cache_is_bounded(self):
        with patch(
            "minimal_kanban.services.repair_order_artifacts.REPAIR_ORDER_FILE_RETENTION_LIMIT", 2
        ):
            for index in range(4):
                card = deepcopy(self.card)
                card.id = f"artifact-{index}"
                self.service._ensure_repair_order_text_file(card)
                self.service._ensure_repair_order_text_file(card)
                self.assertLessEqual(len(self.service._repair_order_verified_files), 2)
            self.assertEqual(
                set(self.service._repair_order_verified_files), {"artifact-2", "artifact-3"}
            )


if __name__ == "__main__":
    unittest.main()
