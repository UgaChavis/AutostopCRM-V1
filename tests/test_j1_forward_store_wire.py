"""Owned loopback HTTP and Linux own-process socket ownership checks."""

from __future__ import annotations

import copy
import json
import os
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from unittest.mock import Mock, patch

from scripts import j1_forward_store as store
from scripts.j1_forward_io import Refusal

IDENTITY = {
    "pid": 4242,
    "start_ticks": "12345",
    "boot_id": "8cf4a305-b6d2-46d9-8634-c09787770ca1",
    "revision": "b" * 40,
    "invocation_id": "c" * 32,
    "source_root": "/synthetic/sealed/manager",
}
INSTANCE = "a" * 32


def snapshot(state, *, instance=INSTANCE, pid=IDENTITY["pid"]):
    return {
        "format": "store_worker_lifecycle_v1",
        "instance_id": instance,
        "pid": pid,
        "accepting": state == "accepting",
        "status": state,
        "drained": state == "drained",
        "pending": {
            "read": 0,
            "stateful": int(state == "accepting"),
            "transport": int(state == "draining"),
        },
    }


class OwnedServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self):
        self.records = []
        self.mode = "normal"
        self.initial = "accepting"
        self.draining = False
        self.polls = 0
        self.done = threading.Event()
        super().__init__(("127.0.0.1", 0), StoreHandler)

    def server_bind(self):
        # HTTPServer.server_bind resolves getfqdn even for a numeric loopback host.
        TCPServer.server_bind(self)
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]

    def handle_error(self, request, client_address):
        # A deliberately lost response can leave the test peer with a reset.
        pass


class StoreHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.handle_store()

    def do_POST(self):
        self.handle_store()

    def handle_store(self):
        server = self.server
        self.close_connection = True
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        server.records.append((self.command, self.path, dict(self.headers), body))
        is_post = self.command == "POST"
        if is_post:
            server.draining = server.mode != "post_lost_unaccepted"
            if server.mode.startswith("post_lost"):
                self.connection.shutdown(socket.SHUT_RDWR)
                return
        if is_post:
            state = "draining"
        elif server.draining or server.initial == "draining":
            server.polls += 1
            state = "draining" if server.polls == 1 else "drained"
        else:
            state = server.initial
        payload = snapshot(state)
        status = 202 if is_post else 200
        mode = server.mode
        if mode == "wrong_pid":
            payload["pid"] += 1
        elif mode == "wrong_instance" and server.polls:
            payload["instance_id"] = "d" * 32
        elif mode == "post_wrong_instance" and is_post:
            payload["instance_id"] = "d" * 32
        elif mode == "admission_reopened" and server.polls:
            payload = snapshot("accepting")
        elif mode == "inconsistent_status":
            payload["drained"] = True
        raw = json.dumps(payload, separators=(",", ":")).encode()
        if mode == "invalid_json":
            raw = b'{"format":'
        elif mode == "duplicate_json":
            raw = raw[:-1] + b',"pid":4242}'
        if mode == "redirect_get" or (mode == "redirect_post" and is_post):
            status = 307
        headers = [
            f"HTTP/1.1 {status} Test",
            "Content-Type: application/json",
            f"Content-Length: {len(raw)}",
            "Cache-Control: no-store",
            "Connection: close",
        ]
        if status == 307:
            headers.append("Location: http://192.0.2.1:9/forbidden")
        if mode == "duplicate_content_length":
            headers.append(f"Content-Length: {len(raw)}")
        elif mode == "transfer_encoding":
            headers.append("Transfer-Encoding: chunked")
        header = ("\r\n".join(headers) + "\r\n\r\n").encode()
        try:
            if mode == "header_trickle":
                self.trickle(header + raw)
            else:
                self.connection.sendall(header)
                if mode == "body_trickle" or (mode == "post_body_trickle" and is_post):
                    self.trickle(raw)
                elif mode == "post_body_stall" and is_post:
                    server.done.wait(0.5)
                else:
                    self.connection.sendall(raw)
        except (OSError, ConnectionError):
            pass

    def trickle(self, raw):
        for char in raw:
            if self.server.done.wait(0.015):
                return
            self.connection.sendall(bytes([char]))


class StoreWireTests(unittest.TestCase):
    def setUp(self):
        for name in ("getaddrinfo", "getfqdn", "gethostbyaddr"):
            self.enterContext(
                patch.object(socket, name, side_effect=AssertionError("DNS is forbidden"))
            )
        self.server = OwnedServer()
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()
        self.addCleanup(self.close_server)
        self.address = self.server.server_address
        real_connect = socket.socket.connect
        real_connect_ex = socket.socket.connect_ex

        def allowed(address):
            if address != self.address:
                raise AssertionError(f"Network outside owned loopback port: {address!r}")

        def connect(connection, address):
            allowed(address)
            return real_connect(connection, address)

        def connect_ex(connection, address):
            allowed(address)
            return real_connect_ex(connection, address)

        self.enterContext(patch.object(socket.socket, "connect", connect))
        self.enterContext(patch.object(socket.socket, "connect_ex", connect_ex))
        self.enterContext(patch.object(store, "ADDRESS", self.address))
        self.enterContext(patch.object(store, "POLL_SECONDS", 0.005))
        self.identity = self.enterContext(
            patch.object(store, "_identity", side_effect=lambda *_: copy.deepcopy(IDENTITY))
        )

        def owns_connection(pid, connection):
            self.assertEqual(pid, IDENTITY["pid"])
            self.assertEqual(connection.getpeername(), self.address)
            return True

        self.owner = self.enterContext(
            patch.object(store, "_owns_connection", side_effect=owns_connection)
        )
        self.command = Mock(side_effect=AssertionError("No OS command is permitted"))

    def close_server(self):
        self.server.done.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def drain(self, timeout=2):
        return store.drain_before_stop(
            {"manager_revision": IDENTITY["revision"]},
            deadline=time.monotonic() + timeout,
            command=self.command,
        )

    def methods(self):
        return [row[0] for row in self.server.records]

    def assert_one_post(self):
        self.assertEqual(self.methods().count("POST"), 1)
        row = next(row for row in self.server.records if row[0] == "POST")
        self.assertEqual(row[1], "/internal/store-workers/drain")
        self.assertEqual(row[2]["X-Autostop-Store-Instance"], INSTANCE)
        self.assertEqual(row[2]["Content-Length"], "0")
        self.assertEqual(row[2]["Host"], f"{self.address[0]}:{self.address[1]}")
        self.assertEqual(row[3], b"")

    def test_real_http_get_post_poll_reaches_drained_without_proxy_or_dns(self):
        with patch.dict("os.environ", {"HTTP_PROXY": "http://192.0.2.1:9", "NO_PROXY": ""}):
            result = self.drain()
        self.assertEqual(self.methods(), ["GET", "POST", "GET", "GET"])
        self.assert_one_post()
        self.assertTrue(result["stop_permitted"])
        self.assertTrue(result["store_workers_drained"])
        self.assertFalse(result["post_outcome_recovered"])
        self.assertEqual(result["instance_id"], INSTANCE)
        self.assertEqual(result["pending"], {"read": 0, "stateful": 0, "transport": 0})
        self.assertEqual(self.owner.call_count, 4)
        self.assertGreaterEqual(self.identity.call_count, 9)
        self.command.assert_not_called()

    def test_lost_post_response_is_resolved_only_by_get(self):
        self.server.mode = "post_lost_accepted"
        result = self.drain()
        self.assertEqual(self.methods(), ["GET", "POST", "GET", "GET"])
        self.assertTrue(result["post_outcome_recovered"])
        self.assertTrue(result["stop_permitted"])
        self.assert_one_post()

    def test_post_timeout_with_remaining_budget_is_resolved_without_second_post(self):
        self.server.mode = "post_body_stall"
        with patch.object(store, "REQUEST_SECONDS", 0.08):
            result = self.drain()
        self.assertTrue(result["post_outcome_recovered"])
        self.assertTrue(result["stop_permitted"])
        self.assert_one_post()

    def test_trickling_post_response_is_recovered_without_second_post(self):
        self.server.mode = "post_body_trickle"
        with patch.object(store, "REQUEST_SECONDS", 0.08):
            result = self.drain()
        self.assertTrue(result["post_outcome_recovered"])
        self.assertTrue(result["stop_permitted"])
        self.assertEqual(self.methods(), ["GET", "POST", "GET", "GET"])
        self.assert_one_post()

    def test_lost_unaccepted_post_does_not_authorize_a_second_post(self):
        self.server.mode = "post_lost_unaccepted"
        with self.assertRaisesRegex(Refusal, "store_admission_still_open"):
            self.drain()
        self.assertEqual(self.methods(), ["GET", "POST", "GET"])
        self.assert_one_post()

    def test_existing_drain_is_polled_without_post(self):
        self.server.initial = "draining"
        self.assertTrue(self.drain()["stop_permitted"])
        self.assertEqual(self.methods(), ["GET", "GET"])

    def test_existing_completed_drain_needs_only_get(self):
        self.server.initial = "drained"
        self.assertTrue(self.drain()["stop_permitted"])
        self.assertEqual(self.methods(), ["GET"])

    def test_redirect_get_is_not_followed(self):
        self.server.mode = "redirect_get"
        with self.assertRaisesRegex(Refusal, "store_http_status_invalid"):
            self.drain()
        self.assertEqual(self.methods(), ["GET"])

    def test_redirect_post_is_not_replayed(self):
        self.server.mode = "redirect_post"
        with self.assertRaisesRegex(Refusal, "store_http_status_invalid"):
            self.drain()
        self.assertEqual(self.methods(), ["GET", "POST"])
        self.assert_one_post()

    def test_changed_instance_after_post_refuses_stop(self):
        self.server.mode = "wrong_instance"
        with self.assertRaisesRegex(Refusal, "store_instance_changed"):
            self.drain()
        self.assertEqual(self.methods(), ["GET", "POST", "GET"])
        self.assert_one_post()

    def test_changed_instance_in_post_response_is_not_retried(self):
        self.server.mode = "post_wrong_instance"
        with self.assertRaisesRegex(Refusal, "store_instance_changed"):
            self.drain()
        self.assertEqual(self.methods(), ["GET", "POST"])
        self.assert_one_post()

    def test_admission_reopening_refuses_stop(self):
        self.server.mode = "admission_reopened"
        with self.assertRaisesRegex(Refusal, "store_admission_reopened"):
            self.drain()
        self.assert_one_post()

    def test_process_identity_changed_during_response_refuses_before_post(self):
        calls = 0

        def identity(*_):
            nonlocal calls
            calls += 1
            value = copy.deepcopy(IDENTITY)
            if calls >= 3:
                value["start_ticks"] = "99999"
            return value

        self.identity.side_effect = identity
        with self.assertRaisesRegex(Refusal, "store_process_incarnation_changed"):
            self.drain()
        self.assertEqual(self.methods(), ["GET"])

    def test_process_identity_is_rechecked_after_receipt_without_new_post(self):
        result = self.drain()
        previous_calls = self.identity.call_count
        store.verify_stop_identity({}, result, deadline=time.monotonic() + 1, command=self.command)
        self.assertEqual(self.identity.call_count, previous_calls + 1)
        self.identity.side_effect = lambda *_: {**IDENTITY, "start_ticks": "99999"}
        with self.assertRaisesRegex(Refusal, "store_process_incarnation_changed"):
            store.verify_stop_identity(
                {}, result, deadline=time.monotonic() + 1, command=self.command
            )
        self.assertEqual(self.methods(), ["GET", "POST", "GET", "GET"])
        self.assert_one_post()

    def test_unowned_socket_sends_no_http_or_post(self):
        self.owner.side_effect = None
        self.owner.return_value = False
        with self.assertRaises(Refusal):
            self.drain(timeout=0.05)
        self.assertEqual(self.methods(), [])

    def test_hostile_framing_and_status_are_refused_before_post(self):
        cases = (
            ("wrong_pid", "store_status_schema_invalid"),
            ("inconsistent_status", "store_status_inconsistent"),
            ("invalid_json", "invalid_json"),
            ("duplicate_json", "duplicate_json_key"),
            ("duplicate_content_length", "store_http_headers_invalid"),
            ("transfer_encoding", "store_http_framing_invalid"),
        )
        for mode, error in cases:
            with self.subTest(mode=mode):
                self.server.mode = mode
                self.server.records.clear()
                with self.assertRaisesRegex(Refusal, error):
                    self.drain()
                self.assertEqual(self.methods(), ["GET"])

    def test_body_trickle_cannot_extend_absolute_deadline(self):
        self.check_trickle("body_trickle")

    def test_header_trickle_cannot_extend_absolute_deadline(self):
        self.check_trickle("header_trickle")

    def check_trickle(self, mode):
        self.server.mode = mode
        started = time.monotonic()
        with self.assertRaises(Refusal):
            self.drain(timeout=0.12)
        self.assertLess(time.monotonic() - started, 0.8)
        self.assertEqual(self.methods(), ["GET"])


@unittest.skipUnless(sys.platform == "linux", "Own-process /proc socket proof requires Linux")
class StoreKernelOwnershipTests(unittest.TestCase):
    def test_kernel_proves_accepted_socket_belongs_to_own_process(self):
        # This separate class leaves _owns_connection and its /proc reads real.
        # /proc/<our PID>/net/tcp and fd are aliases of our own /proc/self views.
        for name in ("getaddrinfo", "getfqdn", "gethostbyaddr"):
            self.enterContext(
                patch.object(socket, name, side_effect=AssertionError("DNS is forbidden"))
            )
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(1)
            address = listener.getsockname()
            real_connect = socket.socket.connect
            real_connect_ex = socket.socket.connect_ex

            def allowed(target):
                if target != address:
                    raise AssertionError(f"Network outside owned loopback port: {target!r}")

            def connect(connection, target):
                allowed(target)
                return real_connect(connection, target)

            def connect_ex(connection, target):
                allowed(target)
                return real_connect_ex(connection, target)

            with (
                patch.object(socket.socket, "connect", connect),
                patch.object(socket.socket, "connect_ex", connect_ex),
                socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client,
            ):
                client.settimeout(1)
                client.connect(address)
                peer, _ = listener.accept()
                with peer:
                    self.assertTrue(store._owns_connection(os.getpid(), client))


if __name__ == "__main__":
    unittest.main()
