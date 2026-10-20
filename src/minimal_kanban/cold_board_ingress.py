"""Board transport cut before body reads, authentication or collaborators."""

from __future__ import annotations

import gzip
import json
import re
import socket
from html.parser import HTMLParser
from time import perf_counter, sleep
from urllib.parse import urlsplit

from .cold_http_ingress import STATUS_PATH


class ColdBoardIngress:
    COLD_HTTP_INGRESS = None

    def handle_expect_100(self):
        if self.COLD_HTTP_INGRESS is not None:
            parts = self.requestline.split()
            target = parts[1] if len(parts) in {2, 3} else ""
            if self.COLD_HTTP_INGRESS.strip_path(target, "board") is None:
                self._cold_response(409, {"error": "cold_ingress_generation_required"})
                return False
        return super().handle_expect_100()

    def parse_request(self):
        if not super().parse_request():
            return False
        config = self.COLD_HTTP_INGRESS
        if config is None:
            return True
        parts = self.requestline.split()
        target = parts[1] if len(parts) in {2, 3} else ""
        stripped = config.strip_path(target, "board")
        if stripped is None:
            self._cold_response(409, {"error": "cold_ingress_generation_required"})
            return False
        self.path = stripped
        if urlsplit(stripped).path == STATUS_PATH:
            if self.command != "GET":
                self._cold_response(405, {"error": "method_not_allowed"})
            else:
                self._cold_response(200, config.status("board"))
            return False
        return True

    def _cold_response(self, status, document):
        # An old POST body is deliberately neither read nor dispatched. Closing
        # also prevents it from being parsed as a subsequent keep-alive request.
        self.close_connection = True
        raw = json.dumps(document, separators=(",", ":")).encode()
        self.send_response(status)
        for key, value in (
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(raw))),
            ("Cache-Control", "no-store"),
            ("Connection", "close"),
        ):
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)


class _AddressedHtml(HTMLParser):
    def __init__(self, prefix):
        super().__init__(convert_charrefs=False)
        self.prefix, self.parts = prefix, []

    def _tag(self):
        self.parts.append(
            re.sub(
                r"(?i)(\b(?:href|src|action)\s*=\s*['\"])(/(?!/|__cold/))",
                lambda match: match[1] + self.prefix + match[2],
                self.get_starttag_text(),
            )
        )

    def handle_starttag(self, tag, attrs):
        self._tag()

    def handle_startendtag(self, tag, attrs):
        self._tag()

    def handle_endtag(self, tag):
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append("&" + name + ";")

    def handle_charref(self, name):
        self.parts.append("&#" + name + ";")

    def handle_comment(self, data):
        self.parts.append("<!--" + data + "-->")

    def handle_decl(self, decl):
        self.parts.append("<!" + decl + ">")


def html_body(handler, body: bytes, *, compressed: bool = False) -> bytes:
    config = getattr(handler, "COLD_HTTP_INGRESS", None)
    if config is None:
        return body
    raw = gzip.decompress(body) if compressed else body
    parser = _AddressedHtml(config.prefix("board"))
    parser.feed(raw.decode("utf-8"))
    parser.close()
    result = "".join(parser.parts).encode("utf-8")
    return gzip.compress(result, mtime=0) if compressed else result


def response_body(handler, body, content_type, extra_headers):
    if content_type.split(";", 1)[0].strip().lower() == "text/html":
        return html_body(
            handler, body, compressed=(extra_headers or {}).get("Content-Encoding") == "gzip"
        )
    return body


def wait_until_accepting(host, port, prefix, *, timeout_seconds: float = 5.0) -> None:
    deadline = perf_counter() + timeout_seconds
    last_error: BaseException | None = None
    connect_host = host
    if connect_host in {"0.0.0.0", "::", "[::]"}:
        connect_host = "127.0.0.1"
    elif connect_host.startswith("[") and connect_host.endswith("]"):
        connect_host = connect_host[1:-1]
    request = (
        f"GET {prefix}/api/health HTTP/1.1\r\nHost: {connect_host}\r\nConnection: close\r\n\r\n"
    ).encode("ascii")
    while perf_counter() < deadline:
        try:
            with socket.create_connection((connect_host, port), timeout=0.2) as sock:
                sock.settimeout(0.5)
                sock.sendall(request)
                status_line = sock.recv(64).split(b"\r\n", 1)[0]
                if status_line.startswith(b"HTTP/") and b" 200 " in status_line:
                    return
                last_error = RuntimeError(
                    f"Локальный API вернул неожиданный health status: {status_line!r}"
                )
        except (OSError, TimeoutError) as exc:
            last_error = exc
        sleep(0.02)
    raise RuntimeError("Локальный API запущен, но health endpoint не отвечает.") from last_error
