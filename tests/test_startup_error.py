from __future__ import annotations

import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from types import TracebackType
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.app import run


class StartupErrorTests(unittest.TestCase):
    def test_blocked_port_socket_closes_when_bind_fails(self) -> None:
        class FailingSocket:
            def __init__(self) -> None:
                self.closed = False

            def __enter__(self) -> FailingSocket:
                return self

            def __exit__(
                self,
                _exc_type: type[BaseException] | None,
                _exc: BaseException | None,
                _traceback: TracebackType | None,
            ) -> bool:
                self.closed = True
                return False

            def bind(self, _address: tuple[str, int]) -> None:
                raise OSError("bind failed")

        blocker = FailingSocket()
        with (
            patch.object(socket, "socket", return_value=blocker),
            self.assertRaisesRegex(OSError, "bind failed"),
        ):
            self._with_blocked_api_port()

        self.assertTrue(blocker.closed)

    def _with_blocked_api_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen(1)
            blocked_port = blocker.getsockname()[1]
            with (
                tempfile.TemporaryDirectory() as tmp,
                patch.dict(
                    os.environ,
                    {
                        "APPDATA": tmp,
                        "MINIMAL_KANBAN_API_HOST": "127.0.0.1",
                        "MINIMAL_KANBAN_API_PORT": str(blocked_port),
                        "MINIMAL_KANBAN_API_PORT_FALLBACK_LIMIT": "1",
                        "MINIMAL_KANBAN_SUPPRESS_ERROR_DIALOGS": "1",
                    },
                    clear=False,
                ),
            ):
                return run()

    def test_run_returns_error_when_api_port_is_blocked(self) -> None:
        exit_code = self._with_blocked_api_port()
        self.assertEqual(exit_code, 1)

    def test_run_returns_error_when_api_port_is_blocked_with_existing_qapplication(self) -> None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            app = QApplication([])
            app.setQuitOnLastWindowClosed(False)

        exit_code = self._with_blocked_api_port()
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
