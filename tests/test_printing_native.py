from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.printing.printers import (  # noqa: E402
    PrinterBackendError,
    _pending_print_jobs,
    _print_webengine_html,
)


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class NativePrintingTests(unittest.TestCase):
    def renderer(self, outcome):
        view = Mock(loadFinished=Signal(), printFinished=Signal())
        timer = Mock(timeout=Signal())
        loop = Mock()
        printer = Mock()

        def run():
            view.loadFinished.emit(outcome != "load_failed")
            if outcome == "timeout":
                timer.timeout.emit()

        if outcome in {"ok", "printer_failed"}:
            view.print.side_effect = lambda _: view.printFinished.emit(outcome == "ok")
        loop.exec.side_effect = run
        patches = (
            patch("PySide6.QtWebEngineWidgets.QWebEngineView", return_value=view),
            patch("PySide6.QtCore.QEventLoop", return_value=loop),
            patch("PySide6.QtCore.QTimer", return_value=timer),
            patch("minimal_kanban.printing.pdf._webengine_print_source"),
        )
        patches = [self.enterContext(item) for item in patches]
        return SimpleNamespace(view=view, timer=timer, printer=printer, patches=patches)

    def test_native_print_uses_full_html_and_waits_for_spool_acknowledgement(self):
        scenario = self.renderer("ok")
        html = "<table><tr><td>1,2345 × 6 = 7,41</td></tr></table>"
        _print_webengine_html(html, scenario.printer)
        scenario.view.print.assert_called_once_with(scenario.printer)
        scenario.patches[3].assert_called_once_with(scenario.view.page(), html)
        scenario.view.setHtml.assert_not_called()
        scenario.view.deleteLater.assert_called_once()
        scenario.timer.stop.assert_called_once()

    def test_native_print_rejects_load_or_spool_failure(self):
        for outcome in ("load_failed", "printer_failed"):
            with self.subTest(outcome=outcome):
                scenario = self.renderer(outcome)
                with self.assertRaises(PrinterBackendError):
                    _print_webengine_html("<p>Synthetic</p>", scenario.printer)
                if outcome == "load_failed":
                    scenario.view.print.assert_not_called()

    def test_timeout_aborts_but_retains_printer_until_late_completion(self):
        scenario = self.renderer("timeout")
        before = len(_pending_print_jobs)
        with self.assertRaises(PrinterBackendError):
            _print_webengine_html("<p>Synthetic</p>", scenario.printer)
        scenario.printer.abort.assert_called_once()
        scenario.view.deleteLater.assert_not_called()
        self.assertEqual(len(_pending_print_jobs), before + 1)
        scenario.view.printFinished.emit(False)
        self.assertEqual(len(_pending_print_jobs), before)
        scenario.view.deleteLater.assert_called_once()

    def test_native_print_reports_source_io_failure(self):
        scenario = self.renderer("ok")
        scenario.patches[3].side_effect = OSError("Synthetic disk failure")
        with self.assertRaisesRegex(PrinterBackendError, "подготовить HTML"):
            _print_webengine_html("<p>Synthetic</p>", scenario.printer)
        scenario.view.print.assert_not_called()
        scenario.view.deleteLater.assert_called_once()
