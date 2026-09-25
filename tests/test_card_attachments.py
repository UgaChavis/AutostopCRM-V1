from __future__ import annotations  # noqa: I001

import base64
from unittest.mock import patch

from tests.services_case import CardServiceCase

# CardServiceCase adds src/ to sys.path before application imports.
from minimal_kanban.models import Attachment
from minimal_kanban.services import card_attachments, card_service
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore


class CardAttachmentPersistenceTests(CardServiceCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.state_file.parent
        self.card_id = self.service.create_card({"title": "Attachment fixture"})["card"]["id"]
        self.content = "Тестовый файл".encode()
        self.attachment = self.service.add_card_attachment(
            {
                "card_id": self.card_id,
                "file_name": "fixture.txt",
                "content_base64": base64.b64encode(self.content).decode(),
            }
        )["attachment"]
        self.payload = {"card_id": self.card_id, "attachment_id": self.attachment["id"]}
        self.path, _ = self.service.get_attachment_download(self.card_id, self.attachment["id"])

    def persisted_attachment(self) -> Attachment:
        store = JsonStore(self.root / "state.json", self.logger)
        card = next(card for card in store.read_bundle()["cards"] if card.id == self.card_id)
        return next(item for item in card.attachments if item.id == self.attachment["id"])

    def test_failed_state_save_keeps_active_file_and_does_not_leak_tombstone(self) -> None:
        for fast in (False, True):
            with (
                self.subTest(fast=fast),
                patch(
                    "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                    return_value=fast,
                ),
            ):
                with patch.object(self.store, "_write_state", side_effect=OSError("disk full")):
                    with self.assertRaises(OSError):
                        self.service.remove_card_attachment(self.payload)
                self.assertEqual(self.path.read_bytes(), self.content)
                self.assertFalse(self.persisted_attachment().removed)
                self.assertEqual(
                    self.service.get_card_attachment(self.payload)["attachment"]["removed"], False
                )
                self.service.update_card(
                    {"card_id": self.card_id, "description": "Unrelated successful write"}
                )
                self.assertFalse(self.persisted_attachment().removed)

    def test_bytes_are_removed_only_after_the_tombstone_is_durable(self) -> None:
        original = self.service._delete_attachment_file

        def checked_delete(card_id: str, stored_name: str) -> None:
            self.assertTrue(self.persisted_attachment().removed)
            self.assertTrue(self.path.exists())
            return original(card_id, stored_name)

        with patch.object(
            self.service, "_delete_attachment_file", side_effect=checked_delete
        ) as delete:
            self.service.remove_card_attachment(self.payload)
        delete.assert_called_once()
        self.assertFalse(self.path.exists())
        self.assertTrue(self.persisted_attachment().removed)

    def test_cleanup_failure_keeps_successful_tombstone_and_blocks_download(self) -> None:
        with (
            patch.object(
                self.service, "_delete_attachment_file", side_effect=PermissionError("locked")
            ),
            self.assertLogs(self.logger, level="WARNING") as logs,
        ):
            response = self.service.remove_card_attachment(self.payload)
        self.assertEqual(response["card"]["id"], self.card_id)
        self.assertTrue(self.persisted_attachment().removed)
        self.assertTrue(self.path.exists())
        self.assertTrue(any("attachment cleanup deferred" in line for line in logs.output))
        with self.assertRaises(ServiceError) as error:
            self.service.get_attachment_download(self.card_id, self.attachment["id"])
        self.assertEqual(error.exception.code, "not_found")

    def test_card_open_can_defer_attachment_disk_status_until_files_are_requested(self) -> None:
        with patch.object(
            self.service,
            "_attachment_exists_on_disk",
            wraps=self.service._attachment_exists_on_disk,
        ) as exists:
            overview = self.service.get_card(
                {"card_id": self.card_id, "include_attachment_status": False}
            )["card"]
        exists.assert_not_called()
        self.assertNotIn("exists_on_disk", overview["attachments"][0])

        with patch.object(
            self.service,
            "_attachment_exists_on_disk",
            wraps=self.service._attachment_exists_on_disk,
        ) as exists:
            files = self.service.get_card({"card_id": self.card_id})["card"]
        exists.assert_called_once()
        self.assertTrue(files["attachments"][0]["exists_on_disk"])

    def test_legacy_constant_imports_reexport_the_single_attachment_policy(self) -> None:
        names = [
            name
            for name in vars(card_attachments)
            if name.startswith(("_ATTACHMENT_", "_ALLOWED_ATTACHMENT"))
        ]
        for name in [*names, "_OLE_MAGIC", "MAX_ATTACHMENT_SIZE_BYTES"]:
            self.assertIs(getattr(card_service, name), getattr(card_attachments, name))
        self.assertIs(
            CardService.add_card_attachment,
            card_attachments.CardAttachmentsMixin.add_card_attachment,
        )
