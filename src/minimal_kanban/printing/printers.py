from __future__ import annotations

import os
import threading


class PrinterBackendError(RuntimeError):
    pass


def _should_use_qt_printer_backend() -> bool:
    if str(os.environ.get("MINIMAL_KANBAN_ENABLE_QT_PRINTING", "")).strip().lower() not in {"1", "true", "yes"}:
        return False
    return threading.current_thread() is threading.main_thread()


def _ensure_qt_application():
    if not os.environ.get("QT_QPA_PLATFORM") and os.name != "nt":
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover
        raise PrinterBackendError("PySide6 недоступен для работы с принтерами.") from exc
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
    return app


def list_printers(*, default_name: str = "") -> list[dict[str, object]]:
    if not _should_use_qt_printer_backend():
        return []
    try:
        _ensure_qt_application()
        from PySide6.QtPrintSupport import QPrinterInfo
    except Exception:
        return []
    preferred_default = str(default_name or "").strip()
    system_default = QPrinterInfo.defaultPrinter()
    system_default_name = system_default.printerName().strip() if not system_default.isNull() else ""
    printers: list[dict[str, object]] = []
    for printer_info in QPrinterInfo.availablePrinters():
        name = printer_info.printerName().strip()
        if not name:
            continue
        printers.append(
            {
                "name": name,
                "label": name,
                "is_default": bool(name == preferred_default or (not preferred_default and name == system_default_name)),
                "is_available": True,
            }
        )
    printers.sort(key=lambda item: (not item["is_default"], str(item["label"]).lower()))
    return printers


def print_html(
    html: str,
    *,
    printer_name: str,
    copies: int = 1,
    paper_size: str = "A4",
    orientation: str = "portrait",
    title: str = "AutoStop CRM",
) -> None:
    if not _should_use_qt_printer_backend():
        raise PrinterBackendError("Прямая печать недоступна в текущем окружении.")
    _ensure_qt_application()
    try:
        from PySide6.QtCore import QMarginsF, QSizeF
        from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
        from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
    except Exception as exc:  # pragma: no cover
        raise PrinterBackendError("Qt PrintSupport недоступен для печати.") from exc

    requested_name = str(printer_name or "").strip()
    if not requested_name:
        raise PrinterBackendError("Не выбран принтер для печати.")

    printer_info = next(
        (item for item in QPrinterInfo.availablePrinters() if item.printerName().strip() == requested_name),
        None,
    )
    if printer_info is None:
        raise PrinterBackendError(f"Принтер недоступен: {requested_name}")

    printer = QPrinter(printer_info, QPrinter.PrinterMode.HighResolution)
    printer.setDocName(str(title or "AutoStop CRM"))
    printer.setCopyCount(max(1, min(20, int(copies or 1))))
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
    printer.setPageLayout(QPageLayout(selected_size, selected_orientation, QMarginsF(10, 10, 10, 10)))
    document = QTextDocument()
    document.setDocumentMargin(0)
    document.setHtml(str(html or ""))
    page_size = printer.pageRect(QPrinter.Unit.Point).size()
    document.setPageSize(QSizeF(page_size.width(), page_size.height()))
    document.print_(printer)
