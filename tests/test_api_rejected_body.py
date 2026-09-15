"""Rejected cross-origin HTTP requests stay bounded and retain their 403 response."""

from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.api.server import ApiServer, RequestContextFactory  # noqa: E402
from tests.services_case import CardServiceCase  # noqa: E402


class RejectedBodyDrainTests(unittest.TestCase):
    @contextmanager
    def connection(self, length):
        server, client = socket.socketpair()
        with server, client, server.makefile("rb") as reader:
            server.settimeout(3)
            yield (
                SimpleNamespace(
                    headers={"Content-Length": str(length)}, connection=server, rfile=reader
                ),
                client,
            )

    def test_only_declared_body_is_consumed_and_timeout_is_restored(self):
        with self.connection(2) as (handler, client):
            client.sendall(b"{}remaining")
            RequestContextFactory.drain_rejected_request_body(handler)
            self.assertEqual(handler.rfile.read(9), b"remaining")
            self.assertEqual(handler.connection.gettimeout(), 3)

    def test_declared_size_cannot_exceed_drain_cap(self):
        with self.connection(1000000) as (handler, client):
            client.sendall(b"x" * 32)
            with patch("minimal_kanban.api.server.OVERSIZED_JSON_DRAIN_BYTES", 16):
                RequestContextFactory.drain_rejected_request_body(handler)
            self.assertEqual(handler.rfile.read(16), b"x" * 16)

    def test_slow_body_has_a_total_deadline(self):
        with self.connection(1000000) as (handler, client):
            stop = threading.Event()

            def send_slowly():
                while not stop.wait(0.04):
                    try:
                        client.sendall(b"x")
                    except OSError:
                        return

            worker = threading.Thread(target=send_slowly, daemon=True)
            worker.start()
            try:
                started = time.monotonic()
                RequestContextFactory.drain_rejected_request_body(handler)
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(handler.connection.gettimeout(), 3)
            finally:
                stop.set()
                worker.join(timeout=2)


class RejectedBodyHttpTests(CardServiceCase):
    def test_delayed_cross_origin_body_returns_403_without_creating_card(self):
        service = self._build_service()
        api = ApiServer(service, self.logger, start_port=0)
        api.start()
        self.addCleanup(api.stop)
        with socket.create_connection(("127.0.0.1", api.port), timeout=3) as connection:
            # Headers declare a body that never arrives: rejection must not wait
            # indefinitely, and the forbidden route must never be dispatched.
            connection.sendall(
                (
                    f"POST /api/create_card HTTP/1.1\r\nHost: rebind.invalid:{api.port}\r\n"
                    f"Origin: http://rebind.invalid:{api.port}\r\n"
                    "Content-Type: text/plain\r\nContent-Length: 1000\r\n\r\n"
                ).encode("ascii")
            )
            response = http.client.HTTPResponse(connection)
            response.begin()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 403)
            self.assertEqual(payload["error"]["code"], "forbidden")
            self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
        self.assertEqual(service.get_cards({})["cards"], [])
