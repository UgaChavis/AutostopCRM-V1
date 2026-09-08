"""Attachment contracts and bounded Office/image content extraction."""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import base64
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from minimal_kanban.models import MAX_ATTACHMENT_SIZE_BYTES, Attachment, utc_now
from minimal_kanban.services.card_service import CardService, ServiceError
from tests.attachment_samples import (
    GIF_1X1_BYTES,
    JPEG_1X1_BYTES,
    PNG_1X1_BYTES,
    minimal_docx_bytes,
    minimal_pdf_bytes,
    minimal_text_bytes,
    minimal_xlsx_bytes,
)
from tests.services_case import CardServiceCase


class CardServiceAttachmentTests(CardServiceCase):
    def test_archived_card_retention_cleans_up_orphan_attachment_directories(self) -> None:
        attachments_dir = Path(self.temp_dir.name) / "attachments"
        service = CardService(self.store, self.logger, attachments_dir=attachments_dir)

        with patch("minimal_kanban.storage.json_store.ARCHIVED_CARD_RETENTION_LIMIT", 1):
            first = service.create_card(
                {"vehicle": "KIA RIO", "title": "Archive one", "deadline": {"hours": 2}}
            )
            second = service.create_card(
                {"vehicle": "LADA VESTA", "title": "Archive two", "deadline": {"hours": 2}}
            )

            service.add_card_attachment(
                {
                    "card_id": first["card"]["id"],
                    "file_name": "first.txt",
                    "mime_type": "text/plain",
                    "content_base64": base64.b64encode(b"first").decode("ascii"),
                }
            )
            service.add_card_attachment(
                {
                    "card_id": second["card"]["id"],
                    "file_name": "second.txt",
                    "mime_type": "text/plain",
                    "content_base64": base64.b64encode(b"second").decode("ascii"),
                }
            )

            first_dir = attachments_dir / first["card"]["id"]
            second_dir = attachments_dir / second["card"]["id"]
            self.assertTrue(first_dir.exists())
            self.assertTrue(second_dir.exists())

            service.archive_card({"card_id": first["card"]["id"]})
            self.assertTrue(first_dir.exists())

            service.archive_card({"card_id": second["card"]["id"]})

        self.assertFalse(first_dir.exists())
        self.assertTrue(second_dir.exists())

    def test_attachment_directory_cleanup_unlinks_orphan_symlink_without_touching_target(
        self,
    ) -> None:
        attachments_dir = Path(self.temp_dir.name) / "attachments"
        target_dir = Path(self.temp_dir.name) / "outside-attachments"
        target_dir.mkdir()
        (target_dir / "keep.txt").write_text("outside", encoding="utf-8")
        service = CardService(self.store, self.logger, attachments_dir=attachments_dir)
        service.create_card({"vehicle": "KIA RIO", "title": "Keep", "deadline": {"hours": 2}})
        attachments_dir.mkdir(exist_ok=True)
        orphan_link = attachments_dir / "orphan-card"
        try:
            orphan_link.symlink_to(target_dir, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks are not available: {exc}")

        service._cleanup_attachment_directories(service._store.read_cards())

        self.assertFalse(orphan_link.exists())
        self.assertTrue(target_dir.exists())
        self.assertEqual((target_dir / "keep.txt").read_text(encoding="utf-8"), "outside")

    def test_remove_card_attachment_deletes_file_and_empty_card_directory(self) -> None:
        attachments_dir = Path(self.temp_dir.name) / "attachments"
        service = CardService(self.store, self.logger, attachments_dir=attachments_dir)
        created = service.create_card(
            {"vehicle": "KIA RIO", "title": "Attachment remove", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]

        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "report.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(b"hello").decode("ascii"),
            }
        )
        attachment_id = added["attachment"]["id"]
        file_path, _ = service.get_attachment_download(card_id, attachment_id)

        self.assertTrue(file_path.exists())
        self.assertTrue(file_path.parent.exists())

        removed = service.remove_card_attachment(
            {"card_id": card_id, "attachment_id": attachment_id}
        )

        self.assertFalse(file_path.exists())
        self.assertFalse(file_path.parent.exists())
        self.assertEqual(removed["card"]["attachment_count"], 0)

    def test_attachment_disk_paths_reject_traversal_segments(self) -> None:
        service = self._build_service()
        attachment = Attachment(
            id="attachment-id",
            file_name="safe.txt",
            stored_name="..\\outside.txt",
            mime_type="text/plain",
            size_bytes=1,
            created_at=utc_now().isoformat(),
            created_by="tester",
        )

        for card_id, stored_name in (
            ("../outside", "safe.txt"),
            ("..\\outside", "safe.txt"),
            ("card-id", "../outside.txt"),
            ("card-id", "..\\outside.txt"),
        ):
            with self.subTest(card_id=card_id, stored_name=stored_name):
                with self.assertRaises(ServiceError) as exc:
                    service._attachment_path(card_id, stored_name)
                self.assertEqual(exc.exception.code, "validation_error")

        self.assertFalse(service._attachment_exists_on_disk("../outside", attachment))

    def test_attachment_download_treats_directory_as_missing_file(self) -> None:
        attachments_dir = Path(self.temp_dir.name) / "attachments"
        service = CardService(self.store, self.logger, attachments_dir=attachments_dir)
        created = service.create_card(
            {"vehicle": "KIA RIO", "title": "Attachment directory", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "report.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(b"hello").decode("ascii"),
            }
        )
        attachment_id = added["attachment"]["id"]
        file_path, _ = service.get_attachment_download(card_id, attachment_id)
        file_path.unlink()
        file_path.mkdir()

        with self.assertRaises(ServiceError) as exc:
            service.get_attachment_download(card_id, attachment_id)

        self.assertEqual(exc.exception.code, "not_found")
        self.assertTrue(file_path.is_dir())

    def test_attachment_regular_file_check_rejects_symlink_before_file_stat(self) -> None:
        service = self._build_service()
        with (
            patch.object(Path, "is_symlink", return_value=True),
            patch.object(
                Path, "is_file", side_effect=AssertionError("must not stat symlink target")
            ),
        ):
            self.assertFalse(service._attachment_is_regular_file(Path("stored.txt")))

    def test_attachment_download_treats_symlink_as_missing_file(self) -> None:
        attachments_dir = Path(self.temp_dir.name) / "attachments"
        service = CardService(self.store, self.logger, attachments_dir=attachments_dir)
        created = service.create_card(
            {"vehicle": "KIA RIO", "title": "Attachment symlink", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "report.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(b"hello").decode("ascii"),
            }
        )
        attachment_id = added["attachment"]["id"]
        file_path, _ = service.get_attachment_download(card_id, attachment_id)
        target_file = Path(self.temp_dir.name) / "outside-target.txt"
        target_file.write_bytes(b"outside")
        file_path.unlink()
        try:
            file_path.symlink_to(target_file)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks are not available: {exc}")

        with self.assertRaises(ServiceError) as exc:
            service.get_attachment_download(card_id, attachment_id)

        self.assertEqual(exc.exception.code, "not_found")
        self.assertTrue(file_path.is_symlink())

    def test_delete_attachment_file_ignores_directory_at_attachment_path(self) -> None:
        attachments_dir = Path(self.temp_dir.name) / "attachments"
        service = CardService(self.store, self.logger, attachments_dir=attachments_dir)
        directory_path = attachments_dir / "card-id" / "stored.txt"
        directory_path.mkdir(parents=True)

        service._delete_attachment_file("card-id", "stored.txt")

        self.assertTrue(directory_path.is_dir())

    def test_write_attachment_file_does_not_leave_partial_file_when_write_fails(self) -> None:
        attachments_dir = Path(self.temp_dir.name) / "attachments"
        service = CardService(self.store, self.logger, attachments_dir=attachments_dir)
        original_write_bytes = Path.write_bytes

        def partial_temp_write(path: Path, data: bytes) -> int:
            original_write_bytes(path, b"partial")
            raise OSError("disk full")

        with (
            patch.object(Path, "write_bytes", partial_temp_write),
            self.assertRaises(OSError),
        ):
            service._write_attachment_file("card-id", "stored.txt", b"payload")

        card_dir = attachments_dir / "card-id"
        stored_files = [path for path in card_dir.glob("*") if path.is_file()]
        self.assertEqual(stored_files, [])

    def test_write_attachment_file_rejects_oversized_content_without_clobbering_existing_file(
        self,
    ) -> None:
        attachments_dir = Path(self.temp_dir.name) / "attachments"
        service = CardService(self.store, self.logger, attachments_dir=attachments_dir)
        attachment_path = service._write_attachment_file("card-id", "stored.txt", b"old")

        with (
            patch("minimal_kanban.services.card_attachments.MAX_ATTACHMENT_SIZE_BYTES", 4),
            self.assertRaisesRegex(ValueError, "attachment file is too large"),
        ):
            service._write_attachment_file("card-id", "stored.txt", b"toolarge")

        self.assertEqual(attachment_path.read_bytes(), b"old")
        self.assertEqual(list(attachment_path.parent.glob(f".{attachment_path.name}.*.tmp")), [])

    def test_attachment_content_rejects_oversized_base64_before_decode(self) -> None:
        service = self._build_service()
        with (
            patch(
                "minimal_kanban.services.card_attachments._ATTACHMENT_BASE64_ENCODED_MAX_CHARS", 8
            ),
            patch(
                "minimal_kanban.services.card_attachments.base64.b64decode",
                side_effect=AssertionError("oversized payload should not be decoded"),
            ) as decoder,
        ):
            with self.assertRaises(ServiceError) as exc:
                service._validated_attachment_content("A" * 12)

        self.assertEqual(exc.exception.code, "validation_error")
        self.assertEqual(exc.exception.details["field"], "content_base64")
        self.assertEqual(exc.exception.details["max_size_bytes"], MAX_ATTACHMENT_SIZE_BYTES)
        decoder.assert_not_called()

    def test_allowed_attachment_roundtrip_preserves_name_mime_and_bytes(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "KIA RIO", "title": "Attachment roundtrip", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        samples = [
            ("клиент фото.png", "image/png", PNG_1X1_BYTES),
            ("клиент фото.jpg", "image/jpeg", JPEG_1X1_BYTES),
            ("клиент фото.jpeg", "image/jpeg", JPEG_1X1_BYTES),
            ("preview.gif", "image/gif", GIF_1X1_BYTES),
            (
                "report.final.v1.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                minimal_docx_bytes(),
            ),
            (
                "report.final.v1.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                minimal_xlsx_bytes(),
            ),
            ("диагностика финал.txt", "text/plain", minimal_text_bytes()),
            ("счёт.final copy.pdf", "application/pdf", minimal_pdf_bytes()),
        ]

        for file_name, mime_type, payload in samples:
            with self.subTest(file_name=file_name):
                added = service.add_card_attachment(
                    {
                        "card_id": card_id,
                        "file_name": file_name,
                        "mime_type": mime_type,
                        "content_base64": base64.b64encode(payload).decode("ascii"),
                    }
                )
                attachment_id = added["attachment"]["id"]
                file_path, attachment = service.get_attachment_download(card_id, attachment_id)

                self.assertEqual(attachment.file_name, file_name)
                self.assertEqual(attachment.mime_type, mime_type)
                self.assertEqual(attachment.size_bytes, len(payload))
                self.assertEqual(file_path.suffix.lower(), Path(file_name).suffix.lower())
                self.assertEqual(file_path.read_bytes(), payload)

    def test_attachment_upload_generates_safe_name_for_missing_clipboard_image_name(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "BMW", "title": "Clipboard image", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]

        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(PNG_1X1_BYTES).decode("ascii"),
            }
        )

        attachment = added["attachment"]
        self.assertTrue(attachment["file_name"].startswith("attachment-"))
        self.assertTrue(attachment["file_name"].endswith(".png"))
        self.assertEqual(attachment["mime_type"], "image/png")

    def test_attachment_long_file_name_keeps_pdf_extension_after_truncation(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "BMW", "title": "Long attachment name", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        long_file_name = ("очень длинное имя файла." * 20) + "pdf"

        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": long_file_name,
                "mime_type": "application/pdf",
                "content_base64": base64.b64encode(minimal_pdf_bytes()).decode("ascii"),
            }
        )

        attachment = added["attachment"]
        self.assertLessEqual(len(attachment["file_name"]), 240)
        self.assertTrue(attachment["file_name"].endswith(".pdf"))

    def test_attachment_upload_rejects_disallowed_extensions_double_extensions_and_fake_mime(
        self,
    ) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "KIA RIO", "title": "Attachment validation", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        cases = [
            ("payload.exe", "application/x-msdownload", b"MZ\x90\x00", "Разрешены только"),
            ("payload.js", "application/javascript", b"alert(1);", "Разрешены только"),
            ("payload.exe.pdf", "application/pdf", minimal_pdf_bytes(), "двойное расширение"),
            ("payload.pdf", "application/pdf", b"MZ\x00\x02\x03\x00\x00", "не распознан"),
        ]

        for file_name, mime_type, payload, message_part in cases:
            with self.subTest(file_name=file_name):
                with self.assertRaises(ServiceError) as exc:
                    service.add_card_attachment(
                        {
                            "card_id": card_id,
                            "file_name": file_name,
                            "mime_type": mime_type,
                            "content_base64": base64.b64encode(payload).decode("ascii"),
                        }
                    )
                self.assertEqual(exc.exception.code, "validation_error")
                self.assertIn(message_part, exc.exception.message)

    def test_attachment_download_repairs_legacy_extension_mime_and_storage_name(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "AUDI", "title": "Legacy attachment", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        payload = minimal_pdf_bytes()
        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "Отчёт клиента.final.pdf",
                "mime_type": "application/pdf",
                "content_base64": base64.b64encode(payload).decode("ascii"),
            }
        )
        attachment_id = added["attachment"]["id"]
        current_path, _ = service.get_attachment_download(card_id, attachment_id)
        legacy_path = current_path.with_name(attachment_id)
        current_path.rename(legacy_path)

        bundle = self.store.read_bundle()
        card = next(item for item in bundle["cards"] if item.id == card_id)
        attachment = next(item for item in card.attachments if item.id == attachment_id)
        attachment.file_name = "Отчёт клиента.final"
        attachment.mime_type = "application/octet-stream"
        attachment.stored_name = attachment_id
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        repaired_path, repaired_attachment = service.get_attachment_download(card_id, attachment_id)

        self.assertEqual(repaired_attachment.file_name, "Отчёт клиента.final.pdf")
        self.assertEqual(repaired_attachment.mime_type, "application/pdf")
        self.assertEqual(repaired_path.name, f"{attachment_id}.pdf")
        self.assertEqual(repaired_path.read_bytes(), payload)

    def test_attachment_persistence_survives_service_restart(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "VW", "title": "Attachment persistence", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        payload = minimal_docx_bytes("Persistence check")
        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "Persistence финал.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "content_base64": base64.b64encode(payload).decode("ascii"),
            }
        )

        restarted = self._build_service()
        card = restarted.get_card({"card_id": card_id})["card"]
        attachment = card["attachments"][0]
        repaired_path, repaired_attachment = restarted.get_attachment_download(
            card_id, added["attachment"]["id"]
        )

        self.assertEqual(attachment["file_name"], "Persistence финал.docx")
        self.assertEqual(
            attachment["mime_type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertEqual(repaired_attachment.file_name, "Persistence финал.docx")
        self.assertEqual(repaired_path.read_bytes(), payload)

    def test_card_serialization_marks_missing_attachment_files(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "VW", "title": "Missing attachment marker", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "photo.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(PNG_1X1_BYTES).decode("ascii"),
            }
        )
        attachment_id = added["attachment"]["id"]
        file_path, _ = service.get_attachment_download(card_id, attachment_id)

        available_card = service.get_card({"card_id": card_id})["card"]
        self.assertTrue(available_card["attachments"][0]["exists_on_disk"])

        file_path.unlink()

        missing_card = service.get_card({"card_id": card_id})["card"]
        self.assertFalse(missing_card["attachments"][0]["exists_on_disk"])

    def test_card_serialization_marks_symlink_attachment_files_missing(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "VW", "title": "Symlink attachment marker", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "photo.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(PNG_1X1_BYTES).decode("ascii"),
            }
        )
        attachment_id = added["attachment"]["id"]
        file_path, _ = service.get_attachment_download(card_id, attachment_id)
        target_file = Path(self.temp_dir.name) / "outside-target.png"
        target_file.write_bytes(PNG_1X1_BYTES)
        file_path.unlink()
        try:
            file_path.symlink_to(target_file)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"symlinks are not available: {exc}")

        card = service.get_card({"card_id": card_id})["card"]

        self.assertFalse(card["attachments"][0]["exists_on_disk"])

    def test_agent_attachment_metadata_marks_oversized_disk_file_without_hashing(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "VW", "title": "Oversized attachment metadata", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "report.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(minimal_text_bytes()).decode("ascii"),
            }
        )
        attachment_id = added["attachment"]["id"]
        file_path, attachment = service.get_attachment_download(card_id, attachment_id)
        file_path.write_bytes(b"x" * 16)

        with patch("minimal_kanban.services.card_attachments.MAX_ATTACHMENT_SIZE_BYTES", 8):
            metadata = service._attachment_agent_dict(
                card_id, attachment, attachment_path=file_path
            )

        self.assertTrue(metadata["exists_on_disk"])
        self.assertTrue(metadata["oversized_on_disk"])
        self.assertNotIn("sha256", metadata)

    def test_agent_attachment_read_rejects_oversized_disk_file_before_loading(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "VW", "title": "Oversized attachment read", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "report.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(minimal_text_bytes()).decode("ascii"),
            }
        )
        attachment_id = added["attachment"]["id"]
        file_path, _ = service.get_attachment_download(card_id, attachment_id)
        file_path.write_bytes(b"x" * 16)

        with (
            patch("minimal_kanban.services.card_attachments.MAX_ATTACHMENT_SIZE_BYTES", 8),
            patch.object(Path, "read_bytes", side_effect=AssertionError("must not load file")),
        ):
            with self.assertRaises(ServiceError) as exc:
                service.read_card_attachment(
                    {"card_id": card_id, "attachment_id": attachment_id, "mode": "text"}
                )

        self.assertEqual(exc.exception.code, "validation_error")
        self.assertEqual(exc.exception.details["max_size_bytes"], 8)

    def test_docx_text_extraction_rejects_xml_doctype(self) -> None:
        service = self._build_service()
        buffer = BytesIO()
        with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(
                "word/document.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE w:document [<!ENTITY local "expanded">]>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>&local;</w:t></w:r></w:p></w:body>
</w:document>""",
            )

        self.assertEqual(service._extract_docx_text(buffer.getvalue()), "")

    def test_attachment_zip_member_reader_caps_decompressed_bytes(self) -> None:
        service = self._build_service()

        class FakeInfo:
            file_size = 32

        class FakeMember:
            read_size = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def read(self, size: int = -1) -> bytes:
                self.read_size = size
                return b"x" * size

        class FakeArchive:
            def __init__(self) -> None:
                self.member = FakeMember()

            def open(self, info):
                _ = info
                return self.member

        archive = FakeArchive()
        with patch("minimal_kanban.services.card_attachments._ATTACHMENT_XML_READ_MAX_BYTES", 32):
            self.assertIsNone(service._read_attachment_zip_member(archive, FakeInfo()))

        self.assertEqual(archive.member.read_size, 33)

    def test_xlsx_text_extraction_stops_when_text_limit_is_reached(self) -> None:
        service = self._build_service()
        buffer = BytesIO()
        cells = "\n".join(
            f'<c r="A{index}" t="inlineStr"><is><t>cell-{index}-{"x" * 40}</t></is></c>'
            for index in range(1, 51)
        )
        with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(
                "xl/worksheets/sheet1.xml",
                f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1">{cells}</row>
  </sheetData>
</worksheet>
""",
            )

        with patch.object(service, "_xlsx_cell_text", wraps=service._xlsx_cell_text) as cell_text:
            text = service._extract_xlsx_text(buffer.getvalue(), max_chars=24)

        self.assertEqual(len(text), 25)
        self.assertTrue(text.startswith("[sheet1]\nA1: cell-1-"))
        self.assertLess(cell_text.call_count, 50)

    def test_xlsx_text_extraction_keeps_unparseable_shared_string_index_as_text(self) -> None:
        service = self._build_service()
        huge_index = "9" * 5000
        buffer = BytesIO()
        with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(
                "xl/sharedStrings.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <si><t>AutoStop</t></si>
</sst>
""",
            )
            archive.writestr(
                "xl/worksheets/sheet1.xml",
                f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>{huge_index}</v></c></row>
  </sheetData>
</worksheet>
""",
            )

        text = service._extract_xlsx_text(buffer.getvalue(), max_chars=48)

        self.assertTrue(text.startswith("[sheet1]\nA1: 999"))

    def test_agent_attachment_read_extracts_text_office_and_image_payloads(self) -> None:
        service = self._build_service()
        created = service.create_card(
            {"vehicle": "VW", "title": "Agent attachment read", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        text_attachment = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "client-note.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(minimal_text_bytes()).decode("ascii"),
            }
        )["attachment"]
        docx_attachment = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "agent-report.docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "content_base64": base64.b64encode(minimal_docx_bytes("Agent DOCX text")).decode(
                    "ascii"
                ),
            }
        )["attachment"]
        image_attachment = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "photo.png",
                "mime_type": "image/png",
                "content_base64": base64.b64encode(PNG_1X1_BYTES).decode("ascii"),
            }
        )["attachment"]

        listed = service.list_card_attachments({"card_id": card_id})
        self.assertEqual(listed["meta"]["total"], 3)
        listed_by_id = {item["id"]: item for item in listed["attachments"]}
        self.assertEqual(listed_by_id[text_attachment["id"]]["content_kind"], "text")
        self.assertTrue(listed_by_id[docx_attachment["id"]]["readable_as_text"])
        self.assertEqual(listed_by_id[image_attachment["id"]]["content_kind"], "image")

        text_read = service.read_card_attachment(
            {"card_id": card_id, "attachment_id": text_attachment["id"], "mode": "text"}
        )
        self.assertIn("AutoStop CRM", text_read["content"]["text"])
        self.assertEqual(text_read["content"]["extraction_status"], "ok")

        docx_read = service.read_card_attachment(
            {"card_id": card_id, "attachment_id": docx_attachment["id"], "mode": "text"}
        )
        self.assertIn("Agent DOCX text", docx_read["content"]["text"])
        self.assertEqual(docx_read["content"]["encoding"], "office-openxml")

        image_read = service.read_card_attachment(
            {
                "card_id": card_id,
                "attachment_id": image_attachment["id"],
                "mode": "base64",
                "max_base64_bytes": 10000,
            }
        )
        self.assertEqual(image_read["content"]["image"], {"width": 1, "height": 1})
        self.assertTrue(image_read["content"]["base64_included"])
        self.assertTrue(image_read["content"]["data_url"].startswith("data:image/png;base64,"))
