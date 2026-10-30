from __future__ import annotations

import http.client
import io
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.client import BoardApiClient, BoardApiTransportError


class _SyntheticSocket:
    def __init__(self, wire: bytes) -> None:
        self.stream = io.BytesIO(wire)

    def makefile(self, mode: str) -> io.BytesIO:
        return self.stream


class _TimedOutBody(io.BytesIO):
    def read(self, size: int = -1) -> bytes:
        raise TimeoutError("synthetic response body timeout")


def _truncated_chunked_response() -> http.client.HTTPResponse:
    socket = _SyntheticSocket(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n10\r\n{")
    response = http.client.HTTPResponse(socket)
    response.begin()
    return response


def _timeout_http_error() -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://board.example/api/test", 503, "Unavailable", {}, _TimedOutBody()
    )


class BoardApiClientResponseFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = BoardApiClient("https://board.example/api")

    def test_truncated_success_is_wrapped_and_retried_only_for_get(self) -> None:
        for method, attempts in (("GET", 2), ("POST", 1)):
            with self.subTest(method=method):
                responses = [_truncated_chunked_response() for _ in range(attempts)]
                for response in responses:
                    self.addCleanup(response.close)
                with patch(
                    "minimal_kanban.mcp.client._urlopen_no_redirect", side_effect=responses
                ) as urlopen:
                    with self.assertRaises(BoardApiTransportError) as error:
                        self.client._request("/api/test", method=method)
                self.assertIsInstance(error.exception.__cause__, http.client.IncompleteRead)
                self.assertEqual(urlopen.call_count, attempts)
                self.assertTrue(all(response.isclosed() for response in responses))

    def test_error_body_timeout_is_wrapped_without_retry(self) -> None:
        for method in ("GET", "POST"):
            with self.subTest(method=method):
                errors = [_timeout_http_error()]
                bodies = [error.fp for error in errors]
                for body in bodies:
                    self.addCleanup(body.close)
                with patch(
                    "minimal_kanban.mcp.client._urlopen_no_redirect", side_effect=errors
                ) as urlopen:
                    with self.assertRaises(BoardApiTransportError) as error:
                        self.client._request("/api/test", method=method)
                self.assertIsInstance(error.exception.__cause__, TimeoutError)
                self.assertEqual(urlopen.call_count, 1)
                self.assertTrue(all(body.closed for body in bodies))

    def test_redirect_response_is_closed_without_retry(self) -> None:
        body = io.BytesIO(b"redirect response")
        self.addCleanup(body.close)
        redirect = urllib.error.HTTPError(
            "https://board.example/api/test",
            302,
            "Found",
            {"Location": "https://elsewhere.example/api/test"},
            body,
        )
        with patch(
            "minimal_kanban.mcp.client._urlopen_no_redirect", side_effect=redirect
        ) as urlopen:
            with self.assertRaises(BoardApiTransportError):
                self.client._request("/api/test", method="GET")
        self.assertEqual(urlopen.call_count, 1)
        self.assertTrue(body.closed)


if __name__ == "__main__":
    unittest.main()
