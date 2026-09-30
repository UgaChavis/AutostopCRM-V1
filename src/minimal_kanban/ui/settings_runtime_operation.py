from __future__ import annotations

from threading import Thread

from PySide6.QtCore import QObject, Signal

from ..publication_runtime import PublicationRuntime, publication_operation_gate
from ..settings_service import SettingsValidationError


class PublicationRuntimeUpdates(QObject):
    changed = Signal()


def publication_runtime_updates(controller) -> PublicationRuntimeUpdates:
    updates = vars(controller).get("_publication_runtime_updates")
    if updates is None:
        updates = PublicationRuntimeUpdates()
        controller._publication_runtime_updates = updates
    return updates


class SettingsRuntimeOperation(QObject):
    completed = Signal(object, object)
    failed = Signal(object)

    def __init__(self, service, mcp, tunnel, settings, *, action) -> None:
        super().__init__()
        self._runtime = PublicationRuntime(service, mcp, tunnel)
        self._gate = publication_operation_gate(mcp)
        self._updates = publication_runtime_updates(mcp)
        self._settings = settings
        self.action = action
        self.thread: Thread | None = None

    def start(self) -> bool:
        if not self._gate.begin():
            return False
        try:
            self.thread = Thread(
                target=self._run, name="desktop-publication-operation", daemon=True
            )
            self.thread.start()
        except RuntimeError:
            self._gate.finish()
            raise
        return True

    def _run(self) -> None:
        errors = None
        try:
            result = self._runtime.run(self.action, self._settings)
        except SettingsValidationError as exc:
            errors = exc.errors
            result = None
        except Exception:
            result = None
        finally:
            self._gate.finish()
        # Notify surviving/reopened windows before the initiating window's
        # completion slot; that window already has its own pending result.
        self._updates.changed.emit()
        if result is None:
            self.failed.emit(errors)
        else:
            self.completed.emit(*result)
