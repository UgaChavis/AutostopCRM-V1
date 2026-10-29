from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

from .models import _normalize_copy_count

_pending_print_jobs: list[tuple[object, object]] = []

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication


class PrinterBackendError(RuntimeError):
    pass


def _should_use_qt_printer_backend() -> bool:
    if str(os.environ.get("MINIMAL_KANBAN_ENABLE_QT_PRINTING", "")).strip().lower() not in {
        "1",
        "true",
        "yes",
    }:
        return False
    return threading.current_thread() is threading.main_thread()


def _ensure_qt_application() -> QApplication:
    if not os.environ.get("QT_QPA_PLATFORM") and os.name != "nt":
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    try:
        from .pdf import _ensure_qt_application as ensure_pdf_application

        return ensure_pdf_application()
    except Exception as exc:  # pragma: no cover
        raise PrinterBackendError("PySide6 недоступен для работы с принтерами.") from exc


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
    system_default_name = (
        system_default.printerName().strip() if not system_default.isNull() else ""
    )
    printers: list[dict[str, object]] = []
    for printer_info in QPrinterInfo.availablePrinters():
        name = printer_info.printerName().strip()
        if not name:
            continue
        printers.append(
            {
                "name": name,
                "label": name,
                "is_default": bool(
                    name == preferred_default
                    or (not preferred_default and name == system_default_name)
                ),
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
        from PySide6.QtCore import QMarginsF
        from PySide6.QtGui import QPageLayout, QPageSize
        from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
    except Exception as exc:  # pragma: no cover
        raise PrinterBackendError("Qt PrintSupport недоступен для печати.") from exc

    requested_name = str(printer_name or "").strip()
    if not requested_name:
        raise PrinterBackendError("Не выбран принтер для печати.")

    printer_info = next(
        (
            item
            for item in QPrinterInfo.availablePrinters()
            if item.printerName().strip() == requested_name
        ),
        None,
    )
    if printer_info is None:
        raise PrinterBackendError(f"Принтер недоступен: {requested_name}")

    printer = QPrinter(printer_info, QPrinter.PrinterMode.HighResolution)
    printer.setDocName(str(title or "AutoStop CRM"))
    printer.setCopyCount(_normalize_copy_count(copies))
    printer.setResolution(300)
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
    printer.setPageLayout(
        QPageLayout(
            selected_size, selected_orientation, QMarginsF(9, 9, 9, 9), QPageLayout.Unit.Millimeter
        )
    )
    _print_webengine_html(html, printer)


def _print_webengine_html(html: str, printer: object) -> None:
    """Use the PDF renderer's HTML engine and await native spool completion."""
    from .pdf import _webengine_print_source

    try:
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except Exception as exc:
        raise PrinterBackendError("Qt WebEngine недоступен для печати.") from exc
    view, loop, timer = QWebEngineView(), QEventLoop(), QTimer()
    state = {"done": False, "printing": False, "error": ""}
    job = (view, printer)

    def finish(error: str = "") -> None:
        if not state["done"]:
            state.update(done=True, error=error)
            loop.quit()

    def printed(success: bool) -> None:
        state["printing"] = False
        finish("" if success else "Принтер не подтвердил выполнение задания.")
        if job in _pending_print_jobs:
            _pending_print_jobs.remove(job)
            view.deleteLater()

    def loaded(success: bool) -> None:
        if state["done"]:
            return
        if not success:
            finish("Qt WebEngine не загрузил HTML для печати.")
            return
        state["printing"] = True
        try:
            view.print(printer)
        except (RuntimeError, TypeError):
            state["printing"] = False
            finish("Не удалось передать документ на принтер.")

    def timed_out() -> None:
        if state["printing"]:
            printer.abort()
        view.stop()
        finish("Истекло время передачи документа на принтер.")

    timer.setSingleShot(True)
    timer.timeout.connect(timed_out)
    view.loadFinished.connect(loaded)
    view.printFinished.connect(printed)
    try:
        with _webengine_print_source(view.page(), html):
            timer.start(45_000)
            loop.exec()
        if state["error"]:
            raise PrinterBackendError(state["error"])
    except OSError as exc:
        raise PrinterBackendError("Не удалось подготовить HTML для печати.") from exc
    finally:
        timer.stop()
        if state["printing"]:
            # Qt may finish asynchronously even after abort; retain its printer.
            _pending_print_jobs.append(job)
        else:
            view.deleteLater()
