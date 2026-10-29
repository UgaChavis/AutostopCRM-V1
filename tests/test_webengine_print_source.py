from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing import pdf  # noqa: E402


class RequestInterceptor:
    def __init__(self, parent):
        self.parent = parent


class WebEnginePrintSourceTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(
            patch("PySide6.QtWebEngineCore.QWebEngineUrlRequestInterceptor", RequestInterceptor)
        )
        self.page = Mock()

    def test_large_unicode_html_is_loaded_as_private_file_and_removed(self):
        html = "<p>Счёт на оплату</p><!--" + "я" * 400_000 + "-->"
        with pdf._webengine_print_source(self.page, html):
            source = Path(self.page.load.call_args.args[0].toLocalFile())
            self.assertEqual(source.read_text(encoding="utf-8"), html)
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o600)
        self.assertFalse(source.exists())
        self.page.setHtml.assert_not_called()

    def test_source_is_removed_after_load_failure_or_print_timeout(self):
        for fail_during_load in (False, True):
            with self.subTest(fail_during_load=fail_during_load):
                self.page.load.side_effect = (
                    RuntimeError("load failed") if fail_during_load else None
                )
                with self.assertRaises(RuntimeError):
                    with pdf._webengine_print_source(self.page, "<p>Private synthetic text</p>"):
                        raise RuntimeError("print timed out")
                source = Path(self.page.load.call_args.args[0].toLocalFile())
                self.assertFalse(source.exists())

    def test_source_is_removed_after_write_failure(self):
        named_temporary = tempfile.NamedTemporaryFile
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(
                    pdf.tempfile,
                    "NamedTemporaryFile",
                    side_effect=lambda **kwargs: named_temporary(**{**kwargs, "dir": directory}),
                ),
                patch.object(Path, "write_text", side_effect=OSError("disk full")),
                self.assertRaises(OSError),
            ):
                with pdf._webengine_print_source(self.page, "<p>Synthetic</p>"):
                    self.fail("write failure must abort loading")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_file_resources_are_denied_without_changing_remote_or_script_support(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtWebEngineCore import QWebEngineSettings

        with pdf._webengine_print_source(self.page, "<p>Synthetic</p>"):
            source_url = self.page.load.call_args.args[0]
            interceptor = self.page._print_resource_interceptor
            cases = [
                (source_url, False),
                (source_url.resolved(QUrl("secret.txt")), True),
                (QUrl("file:///etc/passwd"), True),
                (QUrl("file:///C:/private.txt"), True),
                (QUrl("file://server/share/private.txt"), True),
                (QUrl("data:image/png;base64,AA=="), False),
                (QUrl("http://127.0.0.1/logo.png"), False),
                (QUrl("https://example.test/logo.png"), False),
            ]
            for url, blocked in cases:
                with self.subTest(url=url.toString()):
                    request = Mock()
                    request.requestUrl.return_value = url
                    interceptor.interceptRequest(request)
                    if blocked:
                        request.block.assert_called_once_with(True)
                    else:
                        request.block.assert_not_called()
            attributes = dict(
                call.args for call in self.page.settings().setAttribute.call_args_list
            )
            self.assertFalse(
                attributes[QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls]
            )
            self.assertTrue(
                attributes[QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls]
            )
            self.assertNotIn(QWebEngineSettings.WebAttribute.JavascriptEnabled, attributes)

    def test_late_load_cannot_start_pdf_print_after_timeout(self):
        pdf._webengine_pdf_handle_load_finished(
            self.page,
            Path("unused.pdf"),
            "A4",
            "portrait",
            {"done": True, "error": "timeout"},
            Mock(),
            True,
        )
        self.page.printToPdf.assert_not_called()
