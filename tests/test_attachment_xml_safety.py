from __future__ import annotations

import base64
import unittest
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from tests import test_api as api_fixtures
from tests.attachment_samples import minimal_docx_bytes, minimal_xlsx_bytes


def office_with_entity(kind: str, *, encoding: str, padding: int) -> bytes:
    original = minimal_docx_bytes("SAFE") if kind == "docx" else minimal_xlsx_bytes("SAFE")
    member = "word/document.xml" if kind == "docx" else "xl/worksheets/sheet1.xml"
    root = "w:document" if kind == "docx" else "worksheet"
    buffer = BytesIO()
    with ZipFile(BytesIO(original)) as source, ZipFile(buffer, "w", ZIP_DEFLATED) as target:
        for item in source.infolist():
            content = source.read(item)
            if item.filename == member:
                body = content.decode("utf-8").split("?>", 1)[1]
                xml = f'<?xml version="1.0" encoding="{encoding}"?>'
                xml += "<!--" + "x" * padding + "-->"
                xml += f'<!DOCTYPE {root} [<!ENTITY test "MUST-NOT-EXPAND">]>'
                xml += body.replace("SAFE", "&test;")
                content = xml.encode(encoding)
            target.writestr(item, content)
    return buffer.getvalue()


class AttachmentXmlSafetyTests(unittest.TestCase):
    setUp = api_fixtures.ApiServerTests.setUp
    request = api_fixtures.ApiServerTests.request

    def upload(self, content: bytes, extension: str) -> dict:
        status, created = self.request("/api/create_card", {"title": "Synthetic XML fixture"})
        self.assertEqual(status, 200, created)
        card_id = created["data"]["card"]["id"]
        status, uploaded = self.request(
            "/api/add_card_attachment",
            {
                "card_id": card_id,
                "file_name": f"fixture.{extension}",
                "content_base64": base64.b64encode(content).decode("ascii"),
            },
        )
        self.assertEqual(status, 200, uploaded)
        return {"card_id": card_id, "attachment_id": uploaded["data"]["attachment"]["id"]}

    def test_rejected_office_xml_keeps_metadata_and_original_bytes_readable(self) -> None:
        for kind in ("docx", "xlsx"):
            for encoding, padding in (("utf-8", 0), ("utf-8", 3000), ("utf-16", 0)):
                with self.subTest(kind=kind, encoding=encoding, padding=padding):
                    content = office_with_entity(kind, encoding=encoding, padding=padding)
                    payload = self.upload(content, kind)
                    state_path = Path(self.temp_dir.name) / "state.json"
                    before = state_path.read_bytes()
                    for mode in ("text", "preview", "base64"):
                        status, response = self.request(
                            "/api/read_card_attachment", {**payload, "mode": mode}
                        )
                        self.assertEqual(status, 200, response)
                        result = response["data"]["content"]
                        self.assertEqual(result["text"], "")
                        self.assertEqual(result["extraction_status"], "empty")
                        if mode == "base64":
                            self.assertEqual(base64.b64decode(result["base64"]), content)
                        self.assertEqual(state_path.read_bytes(), before)

    def test_plain_office_xml_still_extracts_text(self) -> None:
        for kind, builder in (("docx", minimal_docx_bytes), ("xlsx", minimal_xlsx_bytes)):
            with self.subTest(kind=kind):
                payload = self.upload(builder("Normal <text> & content"), kind)
                status, response = self.request(
                    "/api/read_card_attachment", {**payload, "mode": "text"}
                )
                self.assertEqual(status, 200, response)
                self.assertEqual(response["data"]["content"]["extraction_status"], "ok")
                self.assertIn("Normal <text> & content", response["data"]["content"]["text"])

    def test_rejected_shared_strings_do_not_prevent_reading_safe_worksheet(self) -> None:
        buffer = BytesIO()
        original = minimal_xlsx_bytes("Safe worksheet")
        with ZipFile(BytesIO(original)) as source, ZipFile(buffer, "w", ZIP_DEFLATED) as target:
            for item in source.infolist():
                target.writestr(item, source.read(item))
            target.writestr(
                "xl/sharedStrings.xml",
                (
                    "<!--" + "x" * 3000 + "-->"
                    '<!DOCTYPE sst [<!ENTITY test "MUST-NOT-EXPAND">]>'
                    '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                    "<si><t>&test;</t></si></sst>"
                ).encode(),
            )
        payload = self.upload(buffer.getvalue(), "xlsx")
        status, response = self.request("/api/read_card_attachment", {**payload, "mode": "text"})
        self.assertEqual(status, 200, response)
        self.assertIn("Safe worksheet", response["data"]["content"]["text"])
        self.assertNotIn("MUST-NOT-EXPAND", response["data"]["content"]["text"])
