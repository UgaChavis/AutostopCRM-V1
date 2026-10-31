from __future__ import annotations

import http.client
import logging
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minimal_kanban.api.request_admission import ApiRequestAdmission  # noqa: E402
from minimal_kanban.api.server import (  # noqa: E402
    ApiServer,
    ReusableThreadingHTTPServer,
    _admitted,
    _ApiRequestHandler,
)
from minimal_kanban.mcp.main import _stop_embedded_api  # noqa: E402


class ApiAdmissionTests(unittest.TestCase):
    def test_embedded_shutdown_waits_for_handler_and_keeps_fence_closed(self) -> None:
        api = ApiServer(Mock(), logging.getLogger(__name__), start_port=0)
        gate = api._request_admission
        self.assertTrue(gate.enter())
        done = threading.Event()
        failures: list[BaseException] = []

        def stop() -> None:
            try:
                _stop_embedded_api(api)
            except BaseException as exc:
                failures.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=stop)
        worker.start()
        try:
            self.assertFalse(done.wait(0.1))
            self.assertFalse(gate.enter())
            gate.leave()
            self.assertTrue(done.wait(2))
            self.assertFalse(gate.enter())
            self.assertEqual([], failures)
        finally:
            worker.join(2)

    def test_embedded_shutdown_refuses_timeout(self) -> None:
        api = Mock()
        api.wait_request_drain.return_value = False
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            _stop_embedded_api(api)
        api.stop.assert_not_called()

    def test_owner_and_timed_out_drain_are_fail_closed(self) -> None:
        admission = ApiRequestAdmission()
        self.assertTrue(admission.enter())
        owner = object()
        admission.begin_drain(owner)
        self.assertFalse(admission.enter())
        self.assertFalse(admission.wait_drained(owner, 0))
        with self.assertRaisesRegex(RuntimeError, "already_active"):
            admission.begin_drain(object())
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
        owner = object()
        admission.begin_drain(owner)
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

            @_admitted
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
        owner = object()
        api.begin_request_drain(owner)
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

        @_admitted
        def fail(_handler):
            raise ValueError("synthetic failure")

        with self.assertRaises(ValueError):
            fail(handler)
        owner = object()
        admission.begin_drain(owner)
        self.assertTrue(admission.wait_drained(owner, 0))
        admission.resume(owner)

    def test_idle_socket_does_not_prevent_drain(self) -> None:
        gate = ApiRequestAdmission()
        accepted = threading.Event()

        class Handler(_ApiRequestHandler):
            REQUEST_ADMISSION = gate

        class Server(ReusableThreadingHTTPServer):
            def process_request(self, request, client_address):
                accepted.set()
                return super().process_request(request, client_address)

        server = Server(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with socket.create_connection(server.server_address, timeout=2) as client:
            self.assertTrue(accepted.wait(2))
            owner = object()
            gate.begin_drain(owner)
            try:
                self.assertTrue(gate.wait_drained(owner, 0))
                client.settimeout(2)
                try:
                    client.sendall(b"GET / HTTP/1.0\r\n\r\n")
                    self.assertEqual(b"", client.recv(1))
                except ConnectionResetError:
                    pass
            finally:
                gate.resume(owner)

    def test_interrupted_begin_can_release_only_its_preselected_owner(self) -> None:
        admission = ApiRequestAdmission()
        owner = object()
        with self.assertRaises(KeyboardInterrupt):
            try:
                admission.begin_drain(owner)
                raise KeyboardInterrupt
            finally:
                self.assertFalse(admission.resume_if_owned(object()))
                self.assertTrue(admission.resume_if_owned(owner))
        self.assertTrue(admission.enter())
        admission.leave()


if __name__ == "__main__":
    unittest.main()
