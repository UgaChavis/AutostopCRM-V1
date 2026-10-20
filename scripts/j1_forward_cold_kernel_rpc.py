"""New cold-only bounded Unix WebSocket transport for pinned Codex alpha.11."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import time

MAX_MESSAGE = 2 * 1024 * 1024
METHODS = frozenset(
    {
        "initialize",
        "account/read",
        "thread/loaded/list",
        "thread/read",
        "thread/turns/list",
        "thread/items/list",
    }
)


class Deny(Exception):
    """A closed boundary; the message is a fixed code, never response data."""


def require(condition, code):
    if not condition:
        raise Deny(code)


def unique(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "duplicate_json_key")
        value[key] = item
    return value


def decode(raw):
    def invalid(_):
        raise Deny("nonfinite_json")

    try:
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise Deny("invalid_json") from exc
    require(type(value) is dict, "json_object_required")
    return value


def canonical(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


class RPC:
    """One fixed deadline. Public calls are strictly read-only metadata methods."""

    def __init__(self, path, deadline):
        self.deadline = deadline
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.buffer = bytearray()
        self.counter = 0
        try:
            self.remaining()
            self.socket.connect(str(path))
            self.peer = struct.unpack(
                "3i", self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            self.handshake()
            self.initialized = self.call(
                "initialize",
                {
                    "clientInfo": {"name": "autostop_cold_kernel_preparation", "version": "1"},
                    "capabilities": {"experimentalApi": False},
                },
            )
            self.frame(1, canonical({"method": "initialized"}))
        except BaseException:
            self.socket.close()
            raise

    def remaining(self):
        left = self.deadline - time.monotonic()
        require(left > 0, "startup_deadline")
        self.socket.settimeout(min(10, left))

    def handshake(self):
        key = base64.b64encode(os.urandom(16)).decode()
        self.remaining()
        self.socket.sendall(
            (
                "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                "Sec-WebSocket-Key: " + key + "\r\n\r\n"
            ).encode()
        )
        while b"\r\n\r\n" not in self.buffer:
            self.remaining()
            chunk = self.socket.recv(4096)
            require(chunk, "websocket_closed")
            self.buffer.extend(chunk)
            require(len(self.buffer) <= 16384, "oversized_handshake")
        header, rest = bytes(self.buffer).split(b"\r\n\r\n", 1)
        self.buffer = bytearray(rest)
        rows = header.decode("ascii").split("\r\n")
        require(rows[0].split()[1:2] == ["101"], "websocket_upgrade_failed")
        fields = {}
        for row in rows[1:]:
            key_name, value = row.split(":", 1)
            key_name = key_name.lower().strip()
            require(key_name not in fields, "duplicate_handshake_header")
            fields[key_name] = value.strip()
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        require(
            fields.get("sec-websocket-accept") == expected
            and fields.get("upgrade", "").lower() == "websocket"
            and "upgrade"
            in {part.strip() for part in fields.get("connection", "").lower().split(",")}
            and "sec-websocket-extensions" not in fields,
            "invalid_websocket_handshake",
        )

    def take(self, size):
        while len(self.buffer) < size:
            self.remaining()
            raw = self.socket.recv(min(65536, size - len(self.buffer)))
            require(raw, "websocket_closed")
            self.buffer.extend(raw)
        raw = bytes(self.buffer[:size])
        del self.buffer[:size]
        return raw

    def frame(self, opcode, raw):
        require(len(raw) <= MAX_MESSAGE, "outgoing_message_too_large")
        self.remaining()
        mask = os.urandom(4)
        length = len(raw)
        prefix = bytes([0x80 | opcode])
        if length < 126:
            prefix += bytes([0x80 | length])
        elif length < 65536:
            prefix += b"\xfe" + struct.pack("!H", length)
        else:
            prefix += b"\xff" + struct.pack("!Q", length)
        self.socket.sendall(
            prefix + mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(raw))
        )

    def receive(self):
        assembled = bytearray()
        started = False
        for _ in range(128):
            first, second = self.take(2)
            require(not first & 0x70 and not second & 0x80, "unsupported_frame_flags")
            final, opcode = bool(first & 0x80), first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self.take(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.take(8))[0]
            require(
                length <= MAX_MESSAGE and len(assembled) + length <= MAX_MESSAGE,
                "incoming_message_too_large",
            )
            raw = self.take(length)
            if opcode == 8:
                raise Deny("websocket_closed")
            if opcode in (9, 10):
                require(final and length <= 125, "invalid_control_frame")
                if opcode == 9:
                    self.frame(10, raw)
                continue
            require(
                (opcode == 1 and not started) or (opcode == 0 and started),
                "unsupported_message_frame",
            )
            started = True
            assembled.extend(raw)
            if final:
                return decode(bytes(assembled))
        raise Deny("too_many_websocket_fragments")

    def call(self, method, params):
        require(method in METHODS, "rpc_method_denied")
        return self._exchange(method, params)

    def _exchange(self, method, params):
        # Only the reviewed preparation owner may issue a single thread/start
        # after its durable intent. No ordinary caller gets that public verb.
        self.counter += 1
        self.frame(1, canonical({"id": self.counter, "method": method, "params": params}))
        for _ in range(128):
            value = self.receive()
            if type(value.get("id")) is int and value["id"] == self.counter:
                require(
                    "error" not in value and type(value.get("result")) is dict,
                    "native_rpc_error",
                )
                return value["result"]
            require(
                type(value.get("method")) is str and "id" not in value,
                "unexpected_native_request",
            )
        raise Deny("too_many_native_notifications")

    def close(self):
        self.socket.close()
