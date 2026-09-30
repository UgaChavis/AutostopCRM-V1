"""Run connection probes without retaining or accessing their dialog."""

from threading import Thread

from PySide6.QtCore import QObject, Signal


class SettingsConnectionCheck(QObject):
    completed = Signal(object, object)
    failed = Signal()

    def __init__(self, service, settings):
        super().__init__()
        self._service = service
        self._settings = settings

    def start(self):
        Thread(target=self._run, name="settings-connection-check", daemon=True).start()

    def _run(self):
        try:
            summary = self._service.test_connections(self._settings)
        except Exception:
            # Probe errors can contain credentials or private endpoint details.
            self.failed.emit()
        else:
            self.completed.emit(self._settings, summary)
