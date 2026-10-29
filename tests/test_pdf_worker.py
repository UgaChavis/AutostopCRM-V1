"""Worker routing/fallback contracts and a separate real Qt PDF integration."""

from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.pdf_worker_probe import WORKER_HTML
from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing import pdf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class PdfWorkerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(
            patch.dict(
                os.environ,
                {"MINIMAL_KANBAN_FORCE_FALLBACK_PDF": "0", "MINIMAL_KANBAN_PDF_RENDER_CHILD": "0"},
            )
        )
        self.enterContext(patch.object(sys, "frozen", False, create=True))
        self.in_process = self.enterContext(
            patch.object(
                pdf, "_render_webengine_pdf_bytes", side_effect=AssertionError("worker Qt")
            )
        )

    def _render_in_worker(self, *, allow_fallback=False) -> bytes:
        result = {}
        errors = []

        def render():
            try:
                result["pdf"] = pdf.render_html_to_pdf_bytes(
                    WORKER_HTML,
                    paper_size="A5",
                    orientation="landscape",
                    title="Worker contract",
                    allow_plain_text_fallback=allow_fallback,
                )
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=render, name="pdf-worker-contract")
        worker.start()
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive(), "controlled worker must finish without native Qt")
        self.in_process.assert_not_called()
        if errors:
            raise errors[0]
        return result["pdf"]

    def _assert_fallback_content(self, content):
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertNotIn(b"(????", content)
        self.assertNotIn(b"body { color", content)
        for text in ("Счет на оплату", "Владимир Регин"):
            # PDF Unicode text strings preserve the complete source codepoints.
            encoded = (b"\xfe\xff" + text.encode("utf-16-be")).hex().upper().encode("ascii")
            self.assertIn(encoded, content)

    def test_worker_uses_subprocess_and_preserves_render_options(self):
        native = b"%PDF-1.4 native response"
        with (
            patch.object(pdf, "_render_qt_pdf_in_subprocess", return_value=native) as renderer,
            patch.object(pdf, "_render_fallback_pdf_bytes") as fallback,
        ):
            self.assertEqual(native, self._render_in_worker())
        renderer.assert_called_once_with(
            WORKER_HTML, paper_size="A5", orientation="landscape", title="Worker contract"
        )
        fallback.assert_not_called()

    def test_worker_failure_requires_explicit_fallback(self):
        for error in (
            pdf.PdfRenderError("native unavailable"),
            subprocess.TimeoutExpired("qt", 45),
        ):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(pdf, "_render_qt_pdf_in_subprocess", side_effect=error),
                patch.object(pdf, "_render_fallback_pdf_bytes") as fallback,
            ):
                with self.assertRaisesRegex(pdf.PdfRenderError, "HTML-рендер PDF недоступен"):
                    self._render_in_worker()
                fallback.assert_not_called()

    def test_worker_explicit_fallback_preserves_unicode_after_native_failure(self):
        with patch.object(
            pdf,
            "_render_qt_pdf_in_subprocess",
            side_effect=pdf.PdfRenderError("native unavailable"),
        ):
            self._assert_fallback_content(self._render_in_worker(allow_fallback=True))

    def test_worker_explicit_fallback_preserves_unicode_after_native_timeout(self):
        with patch.object(
            pdf, "_render_qt_pdf_in_subprocess", side_effect=subprocess.TimeoutExpired("qt", 45)
        ):
            self._assert_fallback_content(self._render_in_worker(allow_fallback=True))

    def test_invalid_native_pdf_cannot_bypass_fallback_permission(self):
        with patch.object(pdf, "_render_qt_pdf_in_subprocess", return_value=b"not a PDF"):
            with self.assertRaises(pdf.PdfRenderError):
                self._render_in_worker()
            self._assert_fallback_content(self._render_in_worker(allow_fallback=True))

    def test_oversized_native_pdf_keeps_the_existing_output_limit(self):
        with (
            patch.object(pdf, "PDF_OUTPUT_MAX_BYTES", 4096),
            patch.object(pdf, "_render_qt_pdf_in_subprocess", return_value=b"%PDF" + b"x" * 4096),
        ):
            with self.assertRaises(pdf.PdfRenderError):
                self._render_in_worker()
            content = self._render_in_worker(allow_fallback=True)
            self._assert_fallback_content(content)
            self.assertLessEqual(len(content), 4096)

    def test_packaged_worker_uses_only_explicit_fallback(self):
        with (
            patch.object(sys, "frozen", True),
            patch.object(pdf, "_render_qt_pdf_in_subprocess") as renderer,
        ):
            with self.assertRaises(pdf.PdfRenderError):
                self._render_in_worker()
            self._assert_fallback_content(self._render_in_worker(allow_fallback=True))
            renderer.assert_not_called()

    def test_parent_cleans_child_artifacts_on_success_failure_and_timeout(self):
        content = b"%PDF-1.4 synthetic"
        for outcome in ("success", "failure", "timeout"):
            with self.subTest(outcome=outcome):
                observed = {}

                def run(*args, **kwargs):
                    directory = Path(kwargs["env"][pdf._PDF_WORKER_TEMP_ENV])
                    observed["directory"] = directory
                    if os.name != "nt":
                        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
                    (directory / "abandoned.html").write_text("Private synthetic HTML")
                    (directory / "abandoned.pdf").write_bytes(content)
                    if outcome == "timeout":
                        raise subprocess.TimeoutExpired("qt", 45)
                    return subprocess.CompletedProcess(
                        args[0],
                        0 if outcome == "success" else 1,
                        stdout=json.dumps(
                            {
                                "content_base64": base64.b64encode(content).decode("ascii"),
                            }
                        ).encode("utf-8"),
                        stderr=b"Synthetic error" if outcome == "failure" else b"",
                    )

                with patch.object(pdf.subprocess, "run", side_effect=run):
                    if outcome == "success":
                        self.assertEqual(pdf._render_qt_pdf_in_subprocess(WORKER_HTML), content)
                    else:
                        exception = (
                            subprocess.TimeoutExpired
                            if outcome == "timeout"
                            else pdf.PdfRenderError
                        )
                        with self.assertRaises(exception):
                            pdf._render_qt_pdf_in_subprocess(WORKER_HTML)
                self.assertFalse(observed["directory"].exists())


class PdfWorkerNativeIntegrationTests(unittest.TestCase):
    def test_real_worker_renders_large_qt_pdf_without_plain_text_fallback_or_file_access(self):
        environment = dict(
            os.environ,
            PYTHONUTF8="1",
            QT_QPA_PLATFORM="offscreen",
            QTWEBENGINE_DISABLE_SANDBOX="1",
            MINIMAL_KANBAN_FORCE_FALLBACK_PDF="0",
            MINIMAL_KANBAN_PDF_RENDER_CHILD="0",
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "worker.pdf"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "coverage",
                    "run",
                    "--parallel-mode",
                    "-m",
                    "tests.pdf_worker_probe",
                    str(output),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                # 45s production rendering deadline plus isolated startup/inspection.
                timeout=60,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            content = output.read_bytes()
            self.assertTrue(content.startswith(b"%PDF"))
            self.assertNotIn(b"(????", content)
            self.assertNotIn(b"body { color", content)
            self.assertLessEqual(len(content), pdf.PDF_OUTPUT_MAX_BYTES)
            report = json.loads(result.stdout)
            self.assertGreater(report["pages"], 0)
            text = " ".join(report["text"].split())
            self.assertIn("Счет на оплату", text)
            self.assertIn("Владимир Регин", text)
            self.assertIn("LOCAL_FILE_BLOCKED", text)
            self.assertNotIn("PRIVATE_FILE_MARKER_DO_NOT_RENDER", text)
            self.assertGreater(report["html_encoded_bytes"], 2 * 1024 * 1024)
            self.assertNotIn("body { color", text)
            self.assertEqual(
                report["source_file"], str((ROOT / "src/minimal_kanban/printing/pdf.py").resolve())
            )
