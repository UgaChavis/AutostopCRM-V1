from __future__ import annotations

import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.app import run


class StartupErrorTests(unittest.TestCase):
    def test_run_returns_error_when_api_port_is_blocked(self) -> None:
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        blocked_port = blocker.getsockname()[1]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "APPDATA": tmp,
                    "MINIMAL_KANBAN_API_HOST": "127.0.0.1",
                    "MINIMAL_KANBAN_API_PORT": str(blocked_port),
                    "MINIMAL_KANBAN_API_PORT_FALLBACK_LIMIT": "1",
                    "MINIMAL_KANBAN_SUPPRESS_ERROR_DIALOGS": "1",
                },
                clear=False,
            ):
                exit_code = run()

        blocker.close()
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
