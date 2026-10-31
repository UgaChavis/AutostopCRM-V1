from __future__ import annotations

import http.client
import logging
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minimal_kanban.api.request_admission import ApiRequestAdmission  # noqa: E402
from minimal_kanban.api.server import (  # noqa: E402
    ApiServer,
    ReusableThreadingHTTPServer,
    _ApiRequestHandler,
)


class ApiAdmissionTests(unittest.TestCase):
    def test_owner_and_timed_out_drain_are_fail_closed(self) -> None:
        admission = ApiRequestAdmission()
        self.assertTrue(admission.enter())
        owner = admission.begin_drain()
        self.assertFalse(admission.enter())
        self.assertFalse(admission.wait_drained(owner, 0))
        with self.assertRaisesRegex(RuntimeError, "already_active"):
            admission.begin_drain()
        with self.assertRaisesRegex(RuntimeError, "ownership_lost"):
            admission.resume(object())
        admission.leave()
        self.assertTrue(admission.wait_drained(owner, 0))
        admission.resume(owner)
        with self.assertRaisesRegex(RuntimeError, "ownership_lost"):
            admission.resume(owner)
        self.assertTrue(admission.enter())
        admission.leave()

    def test_invalid_timeout_does_not_open_the_fence(self) -> None:
        admission = ApiRequestAdmission()
        owner = admission.begin_drain()
        for timeout in (-1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                admission.wait_drained(owner, timeout)
        self.assertFalse(admission.enter())
        admission.resume(owner)

    def test_running_handler_finishes_before_drain_and_new_one_is_rejected(self) -> None:
        api = ApiServer(Mock(), logging.getLogger(__name__), start_port=0)
        gate = api._request_admission
        started = threading.Event()
        finish = threading.Event()
        calls: list[str] = []

        class Handler(_ApiRequestHandler):
            REQUEST_ADMISSION = gate

            def do_GET(self) -> None:
                calls.append("started")
                started.set()
                if not finish.wait(5):
                    raise RuntimeError("test_handler_timeout")
                self.send_response(200)
                self.end_headers()

        server = ReusableThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(finish.set)
        port = server.server_address[1]
        outcomes: list[int] = []
        failures: list[BaseException] = []

        def request() -> None:
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
                try:
                    connection.request("GET", "/")
                    response = connection.getresponse()
                    outcomes.append(response.status)
                    response.read()
                finally:
                    connection.close()
            except BaseException as exc:
                failures.append(exc)

        worker = threading.Thread(target=request)
        worker.start()
        self.assertTrue(started.wait(2))
        owner = api.begin_request_drain()
        still_held = [True]

        def release_if_held() -> None:
            if still_held[0]:
                api.resume_requests(owner)

        self.addCleanup(release_if_held)
        self.assertFalse(api.wait_request_drain(owner, 0.05))

        # Rejected connections may observe EOF/reset; no handler is dispatched.
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.settimeout(2)
            try:
                client.sendall(b"GET / HTTP/1.0\r\n\r\n")
                self.assertEqual(b"", client.recv(1))
            except ConnectionResetError:
                pass
        self.assertEqual(["started"], calls)
        finish.set()
        self.assertTrue(api.wait_request_drain(owner, 2))
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], failures)
        self.assertEqual([200], outcomes)
        api.resume_requests(owner)
        still_held[0] = False
        request()
        self.assertEqual([], failures)
        self.assertEqual([200, 200], outcomes)
        self.assertEqual(["started", "started"], calls)

    def test_exception_releases_admission(self) -> None:
        admission = ApiRequestAdmission()
        handler = object.__new__(_ApiRequestHandler)
        handler.REQUEST_ADMISSION = admission
        with patch.object(
            BaseHTTPRequestHandler,
            "handle_one_request",
            side_effect=ValueError("synthetic failure"),
        ):
            with self.assertRaises(ValueError):
                handler.handle_one_request()
        owner = admission.begin_drain()
        self.assertTrue(admission.wait_drained(owner, 0))
        admission.resume(owner)


if __name__ == "__main__":
    unittest.main()
