"""Attachment bytes survive committed writes and are reclaimed on rejected CAS."""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402
from tests.services_case import CardServiceCase  # noqa: E402


class AttachmentWriteIntegrityTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.service = self._build_service()
        self.card_id = self.service.create_card(
            {"title": "Synthetic attachments", "deadline": {"hours": 2}}
        )["card"]["id"]

    def upload(self, content=b"Synthetic attachment", file_name="note.txt", service=None):
        return (service or self.service).add_card_attachment(
            {
                "card_id": self.card_id,
                "file_name": file_name,
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
        )

    def peer(self):
        return CardService(
            JsonStore(self.state_file, self.logger),
            self.logger,
            attachments_dir=Path(self.temp_dir.name) / "attachments",
            repair_orders_dir=Path(self.temp_dir.name) / "repair-orders",
        )

    def files(self):
        return list((Path(self.temp_dir.name) / "attachments").rglob("*.txt"))

    def test_conflicting_upload_reclaims_only_its_own_bytes(self):
        for fast in (True, False):
            with self.subTest(fast=fast):
                accepted = self.upload(b"Existing bytes")
                peer = self.peer()
                save_bundle = self.service._save_bundle
                peer_attachment = None
                peer_state = None

                def save_after_peer(bundle, **kwargs):
                    nonlocal peer_attachment, peer_state
                    peer_attachment = self.upload(b"Peer bytes", service=peer)["attachment"]
                    peer_state = self.state_file.read_bytes()
                    return save_bundle(bundle, **kwargs)

                with (
                    patch(
                        "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                        return_value=fast,
                    ),
                    patch.object(self.service, "_save_bundle", side_effect=save_after_peer),
                    self.assertRaises(ServiceError) as caught,
                ):
                    self.upload(b"Rejected bytes")

                self.assertEqual(caught.exception.code, "state_write_conflict")
                self.assertEqual(self.state_file.read_bytes(), peer_state)
                reopened = self.peer()
                active = reopened.list_card_attachments({"card_id": self.card_id})["attachments"]
                self.assertEqual(len(self.files()), len(active))
                self.assertNotIn(b"Rejected bytes", [path.read_bytes() for path in self.files()])
                for attachment, content in (
                    (accepted["attachment"], b"Existing bytes"),
                    (peer_attachment, b"Peer bytes"),
                ):
                    path, _ = reopened.get_attachment_download(self.card_id, attachment["id"])
                    self.assertEqual(path.read_bytes(), content)

    def test_cleanup_failure_keeps_original_conflict(self):
        save_bundle = self.service._save_bundle

        def conflict(bundle, **kwargs):
            self.peer().create_card({"title": "Peer mutation", "deadline": {"hours": 2}})
            return save_bundle(bundle, **kwargs)

        with (
            patch.object(self.service, "_save_bundle", side_effect=conflict),
            patch.object(
                self.service, "_delete_attachment_file", side_effect=OSError("busy")
            ) as delete,
            patch.object(self.logger, "warning") as warning,
            self.assertRaises(ServiceError) as caught,
        ):
            self.upload()

        self.assertEqual(caught.exception.code, "state_write_conflict")
        self.assertEqual(
            self.peer().list_card_attachments({"card_id": self.card_id})["attachments"], []
        )
        self.assertEqual(len(self.files()), 1)
        delete.assert_called_once()
        self.assertTrue(
            any(
                call.args[0] == "attachment upload cleanup deferred card_id=%s attachment_id=%s"
                for call in warning.call_args_list
            )
        )

    def test_late_save_error_preserves_committed_attachment_bytes(self):
        save_bundle = self.service._save_bundle

        def committed_then_failed(bundle, **kwargs):
            save_bundle(bundle, **kwargs)
            raise OSError("Late response failure")

        with (
            patch.object(self.service, "_save_bundle", side_effect=committed_then_failed),
            self.assertRaisesRegex(OSError, "Late response failure"),
        ):
            self.upload()

        reopened = self.peer()
        attachments = reopened.list_card_attachments({"card_id": self.card_id})["attachments"]
        self.assertEqual(len(attachments), 1)
        path, _ = reopened.get_attachment_download(self.card_id, attachments[0]["id"])
        self.assertEqual(path.read_bytes(), b"Synthetic attachment")

    def test_utf16_bom_text_is_decoded_before_single_byte_fallback(self):
        text = "Заказ 123 — готов\nПроверка текста"
        for encoding, bom in (("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")):
            with self.subTest(encoding=encoding):
                content = bom + text.encode(encoding)
                added = self.upload(content)
                result = self.service.read_card_attachment(
                    {"card_id": self.card_id, "attachment_id": added["attachment"]["id"]}
                )["content"]
                self.assertEqual(result["text"], text)
                self.assertEqual(result["encoding"], "utf-16")
                self.assertEqual(result["text_length"], len(text))
                path, _ = self.service.get_attachment_download(
                    self.card_id, added["attachment"]["id"]
                )
                self.assertEqual(path.read_bytes(), content)

    def test_legacy_text_encodings_keep_their_content(self):
        text = "Заказ 123 готов\nПроверка текста"
        for encoding in ("utf-8", "utf-8-sig", "cp1251"):
            with self.subTest(encoding=encoding):
                added = self.upload(text.encode(encoding))
                content = self.service.read_card_attachment(
                    {"card_id": self.card_id, "attachment_id": added["attachment"]["id"]}
                )["content"]
                self.assertEqual(content["text"], text)

    def test_utf16_character_crossing_detection_sample_boundary_is_accepted(self):
        text = "a" * 4094 + "\U0001f697" + " End"
        for encoding, bom in (("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")):
            with self.subTest(encoding=encoding):
                added = self.upload(bom + text.encode(encoding))
                content = self.service.read_card_attachment(
                    {
                        "card_id": self.card_id,
                        "attachment_id": added["attachment"]["id"],
                        "max_chars": 5000,
                    }
                )["content"]
                self.assertEqual(content["text"], text)
