from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.runtime import McpServerRuntime, _normalize_startup_timeout


class McpServerRuntimeUnitTests(unittest.TestCase):
    def _runtime(self, host: str, *, port: int = 41831, path: str = "/mcp") -> McpServerRuntime:
        logger = logging.getLogger(f"test.mcp.runtime.unit.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        server = SimpleNamespace(
            settings=SimpleNamespace(host=host, port=port, streamable_http_path=path)
        )
        return McpServerRuntime(server, logger)

    def test_is_port_open_strips_brackets_from_ipv6_literal_host(self) -> None:
        runtime = self._runtime("[::1]", port=43124, path="/bridge")
        mock_socket = unittest.mock.MagicMock()
        mock_socket.__enter__.return_value = mock_socket
        mock_socket.__exit__.return_value = None
        with patch(
            "minimal_kanban.mcp.runtime.socket.create_connection", return_value=mock_socket
        ) as create_connection:
            self.assertTrue(runtime._is_port_open())

        self.assertEqual(runtime.base_url, "http://[::1]:43124/bridge")
        create_connection.assert_called_once_with(("::1", 43124), timeout=0.5)

    def test_constructor_normalizes_empty_host_and_relative_path(self) -> None:
        runtime = self._runtime("", port=43125, path="bridge")

        self.assertEqual(runtime.host, "127.0.0.1")
        self.assertEqual(runtime.path, "/bridge")
        self.assertEqual(runtime.base_url, "http://127.0.0.1:43125/bridge")

    def test_startup_timeout_normalizer_rejects_non_positive_and_non_finite_values(self) -> None:
        self.assertEqual(_normalize_startup_timeout(True), 30.0)
        self.assertEqual(_normalize_startup_timeout(0), 30.0)
        self.assertEqual(_normalize_startup_timeout(-1), 30.0)
        self.assertEqual(_normalize_startup_timeout(float("inf")), 30.0)
        self.assertEqual(_normalize_startup_timeout(1e308), 300.0)
        self.assertEqual(_normalize_startup_timeout(0.25), 0.25)

    def test_probe_endpoint_reads_bounded_error_prefix(self) -> None:
        runtime = self._runtime("127.0.0.1", port=43126, path="/mcp")

        class Response:
            status_code = 500
            reason_phrase = ""
            chunk_size = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                _ = (exc_type, exc, tb)

            def iter_bytes(self, *, chunk_size=None):
                self.chunk_size = chunk_size
                yield b"x" * int(chunk_size or 1)

        response = Response()
        with (
            patch("minimal_kanban.mcp.runtime.READINESS_RESPONSE_MAX_BYTES", 4),
            patch("minimal_kanban.mcp.runtime.httpx.stream", return_value=response),
        ):
            ready, status_code, detail = runtime._probe_endpoint_once()

        self.assertFalse(ready)
        self.assertEqual(status_code, 500)
        self.assertEqual(detail, "xxxx")
        self.assertEqual(response.chunk_size, 5)

    def test_stop_fails_closed_when_server_thread_remains_alive(self) -> None:
        runtime = self._runtime("127.0.0.1")
        uvicorn_server = SimpleNamespace(should_exit=False)
        thread = unittest.mock.Mock()
        thread.is_alive.return_value = True
        runtime._uvicorn_server = uvicorn_server
        runtime._thread = thread

        with self.assertRaisesRegex(RuntimeError, "не остановился"):
            runtime.stop()

        self.assertTrue(uvicorn_server.should_exit)
        thread.join.assert_called_once_with(timeout=10)
        self.assertIs(uvicorn_server, runtime._uvicorn_server)
        self.assertIs(thread, runtime._thread)


if __name__ == "__main__":
    unittest.main()
