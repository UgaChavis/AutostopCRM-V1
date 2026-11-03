"""Attachment status lookup preserves the first matching legacy identity."""

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
import base64
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.models import Card
from minimal_kanban.storage.json_store import StateFileCorruptedError
from minimal_kanban.storage.readonly_context_snapshot import ReadonlyContextSnapshot


class CardAttachmentLookupTests(CardServiceCase):
    def legacy_serialize(
        self,
        card,
        events,
        *,
        column_labels=None,
        event_counts=None,
        include_removed_attachments=False,
        include_attachment_status=True,
        viewer_username=None,
        compact=False,
    ):
        count = (
            sum(1 for event in events if event.card_id == card.id)
            if event_counts is None
            else event_counts.get(card.id, 0)
        )
        payload = card.to_dict(
            events_count=count,
            include_removed_attachments=include_removed_attachments,
            viewer_username=viewer_username,
            compact=compact,
        )
        if (
            include_attachment_status
            and not compact
            and isinstance(payload.get("attachments"), list)
        ):
            for row in payload["attachments"]:
                if not isinstance(row, dict):
                    continue
                identity = str(row.get("id", "") or "").strip()
                found = next(
                    (item for item in card.attachments if str(item.id or "").strip() == identity),
                    None,
                )
                row["exists_on_disk"] = (
                    self.service._attachment_exists_on_disk(card.id, found) if found else False
                )
        payload["column_label"] = (column_labels or {}).get(card.column, card.column)
        return payload

    def test_first_match_and_invalid_ids_preserve_the_full_payload(self):
        attachments = [
            {
                "id": f"file-{i}",
                "file_name": f"file-{i}.txt",
                "stored_name": f"stored-{i}.txt",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
            for i in range(6)
        ]
        card = Card.from_dict(
            {"id": "synthetic-card", "title": "Synthetic", "attachments": attachments}
        )
        card.attachments[0].id = "duplicate"
        card.attachments[0].removed = True
        card.attachments[1].id = "duplicate"
        card.attachments[2].id = ""
        card.attachments[3].id = " padded "
        card.attachments[4].id = ["legacy"]
        original_to_dict = Card.to_dict

        def enriched(current, *args, **kwargs):
            payload = original_to_dict(current, *args, **kwargs)
            if "attachments" in payload:
                payload["attachments"].extend([{"id": "unknown"}, None])
            return payload

        def exists(_card_id, item):
            return item.file_name == "file-0.txt"

        with (
            patch.object(Card, "to_dict", new=enriched),
            patch.object(self.service, "_attachment_exists_on_disk", side_effect=exists),
            patch(
                "minimal_kanban.models.utc_now",
                return_value=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ):
            for removed in (False, True):
                for status in (False, True):
                    for compact in (False, True):
                        with self.subTest(removed=removed, status=status, compact=compact):
                            options = {
                                "include_removed_attachments": removed,
                                "include_attachment_status": status,
                                "compact": compact,
                            }
                            expected = self.legacy_serialize(card, [], **options)
                            actual = self.service._serialize_card(card, [], **options)
                            self.assertEqual(actual, expected)
                            if status and not compact:
                                duplicate = next(
                                    row
                                    for row in actual["attachments"]
                                    if isinstance(row, dict) and row.get("id") == "duplicate"
                                )
                                self.assertTrue(duplicate["exists_on_disk"])
                                self.assertFalse(actual["attachments"][-2]["exists_on_disk"])

    def test_get_card_preserves_attachment_response_and_rejects_duplicate_id(self):
        card_id = self.service.create_card({"title": "Synthetic"})["card"]["id"]
        for index in range(2):
            self.service.add_card_attachment(
                {
                    "card_id": card_id,
                    "file_name": f"synthetic-{index}.txt",
                    "content_base64": base64.b64encode(b"synthetic").decode(),
                }
            )
        snapshot = self.service._snapshot_service
        payload = {"card_id": card_id}
        with patch("minimal_kanban.models.utc_now", return_value=datetime(2026, 1, 1, tzinfo=UTC)):
            with patch.object(snapshot, "_serialize_card", side_effect=self.legacy_serialize):
                expected = self.service.get_card(payload)
            actual = self.service.get_card(payload)
        self.assertEqual(actual, expected)
        self.assertTrue(all(row["exists_on_disk"] for row in actual["card"]["attachments"]))

        source = self.store._read_single_card_snapshot(card_id)
        card = deepcopy(source["cards"][0])
        card.attachments[1].id = card.attachments[0].id
        data = dict(source)
        data["cards"] = [card]
        corrupt = ReadonlyContextSnapshot(
            data, {}, source_indexes=({card.id: [card.to_storage_dict()]}, {}, {})
        )
        with patch.object(self.store, "_read_single_card_snapshot", return_value=corrupt):
            with self.assertRaises(StateFileCorruptedError):
                self.service.get_card(payload)
