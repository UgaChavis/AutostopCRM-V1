from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path


class PdfRenderError(RuntimeError):
    pass


def _ensure_qt_application():
    if threading.current_thread() is not threading.main_thread():
        raise PdfRenderError("PDF generation is only available from the main desktop thread.")
    if not os.environ.get("QT_QPA_PLATFORM") and os.name != "nt":
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover
        raise PdfRenderError("PySide6 недоступен для генерации PDF.") from exc
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
    return app


def render_html_to_pdf_bytes(
    html: str,
    *,
    paper_size: str = "A4",
    orientation: str = "portrait",
    title: str = "AutoStop CRM",
) -> bytes:
    _ensure_qt_application()
    try:
        from PySide6.QtCore import QMarginsF, QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
        from PySide6.QtPrintSupport import QPrinter
    except Exception as exc:  # pragma: no cover
        raise PdfRenderError("Qt PrintSupport недоступен для генерации PDF.") from exc

    page_sizes = {
        "A4": QPageSize(QPageSize.PageSizeId.A4),
        "A5": QPageSize(QPageSize.PageSizeId.A5),
        "LETTER": QPageSize(QPageSize.PageSizeId.Letter),
    }
    selected_size = page_sizes.get(str(paper_size or "A4").upper(), page_sizes["A4"])
    selected_orientation = (
        QPageLayout.Orientation.Landscape
        if str(orientation or "").strip().lower() == "landscape"
        else QPageLayout.Orientation.Portrait
    )

    with tempfile.NamedTemporaryFile(prefix="autostopcrm-print-", suffix=".pdf", delete=False) as tmp:
        pdf_path = Path(tmp.name)
    try:
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(pdf_path))
        printer.setDocName(str(title or "AutoStop CRM"))
        printer.setPageLayout(QPageLayout(selected_size, selected_orientation, QMarginsF(10, 10, 10, 10)))
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
        document.print(printer)
        if not pdf_path.exists():
            raise PdfRenderError("Qt не создал PDF-файл.")
        return pdf_path.read_bytes()
    finally:
        try:
            pdf_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            pass
