from __future__ import annotations

import base64
import html as html_lib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


class PdfRenderError(RuntimeError):
    pass


def _ensure_qt_application():
    if not os.environ.get("QT_QPA_PLATFORM") and os.name != "nt":
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    if not os.environ.get("QTWEBENGINE_DISABLE_SANDBOX"):
        os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover
        raise PdfRenderError("PySide6 недоступен для генерации PDF.") from exc
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
    return app


def _should_use_qt_renderer() -> bool:
    if str(os.environ.get("MINIMAL_KANBAN_FORCE_FALLBACK_PDF", "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return False
    if str(os.environ.get("MINIMAL_KANBAN_PDF_RENDER_CHILD", "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return True
    # QTextDocument/QPrinter from non-main threads is unstable on Windows and
    # can crash the process at interpreter shutdown after otherwise successful
    # requests. Worker threads use a short-lived subprocess so the full HTML
    # renderer still handles Cyrillic invoices correctly.
    if threading.current_thread() is not threading.main_thread():
        return False
    return True


def _should_use_qt_subprocess_renderer() -> bool:
    if str(os.environ.get("MINIMAL_KANBAN_FORCE_FALLBACK_PDF", "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return False
    if str(os.environ.get("MINIMAL_KANBAN_PDF_RENDER_CHILD", "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return False
    if getattr(sys, "frozen", False):
        return False
    return threading.current_thread() is not threading.main_thread()


def render_html_to_pdf_bytes(
    html: str,
    *,
    paper_size: str = "A4",
    orientation: str = "portrait",
    title: str = "AutoStop CRM",
) -> bytes:
    if _should_use_qt_renderer():
        try:
            return _render_preferred_qt_pdf_bytes(
                html,
                paper_size=paper_size,
                orientation=orientation,
                title=title,
            )
        except Exception:
            pass
    if _should_use_qt_subprocess_renderer():
        try:
            return _render_qt_pdf_in_subprocess(
                html,
                paper_size=paper_size,
                orientation=orientation,
                title=title,
            )
        except Exception:
            pass
    return _render_fallback_pdf_bytes(
        html,
        paper_size=paper_size,
        orientation=orientation,
        title=title,
    )


def _render_preferred_qt_pdf_bytes(
    html: str,
    *,
    paper_size: str = "A4",
    orientation: str = "portrait",
    title: str = "AutoStop CRM",
) -> bytes:
    try:
        return _render_webengine_pdf_bytes(
            html,
            paper_size=paper_size,
            orientation=orientation,
            title=title,
        )
    except Exception:
        return _render_qt_pdf_bytes(
            html,
            paper_size=paper_size,
            orientation=orientation,
            title=title,
        )


def _qt_page_size(paper_size: str):
    from PySide6.QtGui import QPageSize

    page_sizes = {
        "A4": QPageSize(QPageSize.PageSizeId.A4),
        "A5": QPageSize(QPageSize.PageSizeId.A5),
        "LETTER": QPageSize(QPageSize.PageSizeId.Letter),
    }
    return page_sizes.get(str(paper_size or "A4").upper(), page_sizes["A4"])


def _qt_page_orientation(orientation: str):
    from PySide6.QtGui import QPageLayout

    return (
        QPageLayout.Orientation.Landscape
        if str(orientation or "").strip().lower() == "landscape"
        else QPageLayout.Orientation.Portrait
    )


def _render_webengine_pdf_bytes(
    html: str,
    *,
    paper_size: str = "A4",
    orientation: str = "portrait",
    title: str = "AutoStop CRM",
) -> bytes:
    del title
    _ensure_qt_application()
    try:
        from PySide6.QtCore import QEventLoop, QMarginsF, QTimer, QUrl
        from PySide6.QtGui import QPageLayout
        from PySide6.QtWebEngineCore import QWebEnginePage
    except Exception as exc:
        raise PdfRenderError("Qt WebEngine недоступен для генерации красивого PDF.") from exc

    with tempfile.NamedTemporaryFile(
        prefix="autostopcrm-print-web-", suffix=".pdf", delete=False
    ) as tmp:
        pdf_path = Path(tmp.name)

    page = QWebEnginePage()
    loop = QEventLoop()
    state: dict[str, object] = {"done": False, "error": ""}
    timer = QTimer()
    timer.setSingleShot(True)

    def finish(error: str = "") -> None:
        if state["done"]:
            return
        state["done"] = True
        state["error"] = error
        loop.quit()

    def handle_pdf_finished(file_path: str, success: bool) -> None:
        if not success:
            finish("Qt WebEngine не создал PDF-файл.")
            return
        if Path(file_path) != pdf_path:
            finish("Qt WebEngine вернул неожиданный путь PDF.")
            return
        finish()

    def handle_load_finished(success: bool) -> None:
        if not success:
            finish("Qt WebEngine не загрузил HTML для PDF.")
            return
        layout = QPageLayout(
            _qt_page_size(paper_size),
            _qt_page_orientation(orientation),
            QMarginsF(9, 9, 9, 9),
            QPageLayout.Unit.Millimeter,
        )
        page.pdfPrintingFinished.connect(handle_pdf_finished)
        page.printToPdf(str(pdf_path), layout)

    timer.timeout.connect(lambda: finish("Истекло время генерации PDF через Qt WebEngine."))
    page.loadFinished.connect(handle_load_finished)
    try:
        try:
            pdf_path.unlink(missing_ok=True)
        except OSError:
            pass
        page.setHtml(str(html or ""), QUrl("about:blank"))
        timer.start(45_000)
        loop.exec()
        timer.stop()
        error = str(state["error"] or "")
        if error:
            raise PdfRenderError(error)
        if not pdf_path.exists():
            raise PdfRenderError("Qt WebEngine не создал PDF-файл.")
        pdf_bytes = pdf_path.read_bytes()
        if not pdf_bytes.startswith(b"%PDF"):
            raise PdfRenderError("Qt WebEngine вернул не PDF.")
        return pdf_bytes
    finally:
        try:
            page.deleteLater()
        except RuntimeError:
            pass
        try:
            pdf_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass


def _render_qt_pdf_bytes(
    html: str,
    *,
    paper_size: str = "A4",
    orientation: str = "portrait",
    title: str = "AutoStop CRM",
) -> bytes:
    _ensure_qt_application()
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QTextDocument
    from PySide6.QtPrintSupport import QPrinter

    selected_size = _qt_page_size(paper_size)
    selected_orientation = _qt_page_orientation(orientation)

    with tempfile.NamedTemporaryFile(
        prefix="autostopcrm-print-", suffix=".pdf", delete=False
    ) as tmp:
        pdf_path = Path(tmp.name)
    try:
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(pdf_path))
        printer.setDocName(str(title or "AutoStop CRM"))
        printer.setPageLayout(
            QPageLayout(selected_size, selected_orientation, QMarginsF(10, 10, 10, 10))
        )
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultStyleSheet(
            "body { color: #171717; font-family: 'Segoe UI', Arial, sans-serif; } "
            "table { border-collapse: collapse; width: 100%; } "
            "th, td { border: 1px solid #cfcfcf; }"
        )
        document.setHtml(str(html or ""))
        page_size = printer.pageRect(QPrinter.Unit.Point).size()
        document.setPageSize(QSizeF(page_size.width(), page_size.height()))
        document.print_(printer)
        if not pdf_path.exists():
            raise PdfRenderError("Qt не создал PDF-файл.")
        return pdf_path.read_bytes()
    finally:
        try:
            pdf_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass


def _render_qt_pdf_in_subprocess(
    html: str,
    *,
    paper_size: str = "A4",
    orientation: str = "portrait",
    title: str = "AutoStop CRM",
) -> bytes:
    script_path = Path(__file__).resolve()
    if not script_path.exists():
        raise PdfRenderError("Не найден модуль генерации PDF для subprocess.")
    payload = json.dumps(
        {
            "html": str(html or ""),
            "paper_size": str(paper_size or "A4"),
            "orientation": str(orientation or "portrait"),
            "title": str(title or "AutoStop CRM"),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    env = os.environ.copy()
    env["MINIMAL_KANBAN_PDF_RENDER_CHILD"] = "1"
    env.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    if not os.environ.get("QT_QPA_PLATFORM") and os.name != "nt":
        env["QT_QPA_PLATFORM"] = "offscreen"
    completed = subprocess.run(
        [sys.executable, str(script_path)],
        input=payload,
        capture_output=True,
        env=env,
        timeout=45,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PdfRenderError(f"Qt subprocess не создал PDF: {stderr}")
    try:
        response = json.loads(completed.stdout.decode("utf-8"))
        pdf_bytes = base64.b64decode(str(response["content_base64"]))
    except Exception as exc:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PdfRenderError(f"Qt subprocess вернул некорректный PDF: {stderr}") from exc
    if not pdf_bytes.startswith(b"%PDF"):
        raise PdfRenderError("Qt subprocess вернул не PDF.")
    return pdf_bytes


def _render_fallback_pdf_bytes(
    html: str,
    *,
    paper_size: str = "A4",
    orientation: str = "portrait",
    title: str = "AutoStop CRM",
) -> bytes:
    text = _html_to_plain_text(html, default_title=title)
    return _render_plain_text_pdf(text, paper_size=paper_size, orientation=orientation, title=title)


def _html_to_plain_text(html: str, *, default_title: str = "AutoStop CRM") -> str:
    text = str(html or "")
    text = re.sub(r"(?is)<\s*head[^>]*>.*?</\s*head\s*>", "", text)
    text = re.sub(r"(?is)<\s*(style|script)[^>]*>.*?</\s*\1\s*>", "", text)
    replacements = [
        (r"(?i)<\s*br\s*/?>", "\n"),
        (r"(?i)</\s*(p|div|tr|h1|h2|h3|h4|h5|h6)\s*>", "\n"),
        (r"(?i)<\s*li[^>]*>", "- "),
        (r"(?i)</\s*li\s*>", "\n"),
        (r"(?i)</\s*td\s*>", " | "),
        (r"(?i)</\s*th\s*>", " | "),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    compact = [line for line in lines if line]
    return "\n".join(compact).strip() or str(default_title or "AutoStop CRM")


def _page_size_points(paper_size: str, orientation: str) -> tuple[int, int]:
    sizes = {
        "A4": (595, 842),
        "A5": (420, 595),
        "LETTER": (612, 792),
    }
    width, height = sizes.get(str(paper_size or "A4").upper(), sizes["A4"])
    if str(orientation or "").strip().lower() == "landscape":
        return height, width
    return width, height


def _escape_pdf_text(value: str) -> str:
    safe = str(value or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return safe.encode("ascii", "replace").decode("ascii")


def _build_pdf_bytes(objects: list[bytes]) -> bytes:
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{index} 0 obj\n".encode("ascii"))
        out.extend(body)
        if not body.endswith(b"\n"):
            out.extend(b"\n")
        out.extend(b"endobj\n")
    xref_offset = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode(
            "ascii"
        )
    )
    return bytes(out)


def _render_plain_text_pdf(
    text: str,
    *,
    paper_size: str = "A4",
    orientation: str = "portrait",
    title: str = "AutoStop CRM",
) -> bytes:
    width, height = _page_size_points(paper_size, orientation)
    margin_x = 48
    margin_y = 54
    line_height = 14
    usable_height = max(height - (margin_y * 2), line_height)
    lines_per_page = max(1, usable_height // line_height)
    raw_lines = [line.rstrip() for line in str(text or title or "AutoStop CRM").splitlines()]
    lines = raw_lines or [str(title or "AutoStop CRM")]
    pages = [
        lines[index : index + lines_per_page] for index in range(0, len(lines), lines_per_page)
    ] or [[str(title or "AutoStop CRM")]]

    font_object = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n"
    objects: list[bytes] = [b"", b"", font_object]
    page_refs: list[str] = []

    for page_index, page_lines in enumerate(pages):
        page_object_id = 4 + (page_index * 2)
        content_object_id = page_object_id + 1
        page_refs.append(f"{page_object_id} 0 R")
        commands = [
            "BT",
            "/F1 11 Tf",
            f"{line_height} TL",
            f"{margin_x} {height - margin_y} Td",
        ]
        for line_index, line in enumerate(page_lines):
            if line_index:
                commands.append("T*")
            commands.append(f"({_escape_pdf_text(line)}) Tj")
        commands.append("ET")
        stream = "\n".join(commands).encode("ascii", "replace")
        page_object = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_object_id} 0 R >>\n"
        ).encode("ascii")
        content_object = (
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream\n"
        )
        objects.append(page_object)
        objects.append(content_object)

    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>\n"
    objects[1] = (
        f"<< /Type /Pages /Count {len(page_refs)} /Kids [{' '.join(page_refs)}] >>\n".encode(
            "ascii"
        )
    )
    return _build_pdf_bytes(objects)


def _render_pdf_cli() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        pdf_bytes = _render_preferred_qt_pdf_bytes(
            str(payload.get("html", "") or ""),
            paper_size=str(payload.get("paper_size", "A4") or "A4"),
            orientation=str(payload.get("orientation", "portrait") or "portrait"),
            title=str(payload.get("title", "AutoStop CRM") or "AutoStop CRM"),
        )
    except Exception as exc:
        sys.stderr.write(str(exc))
        return 1
    sys.stdout.write(json.dumps({"content_base64": base64.b64encode(pdf_bytes).decode("ascii")}))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess tests
    raise SystemExit(_render_pdf_cli())
