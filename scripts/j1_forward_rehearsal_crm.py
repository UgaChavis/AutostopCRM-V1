"""Owned artificial HTTP/SQLite boundary for the private cold release trace.

This process is deliberately not the CRM application or a Docker container.
It replaces that external boundary while the actual release controller,
proof verification, backup, restore and launch journal remain unchanged.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

from scripts import j1_forward_cold_fixture as fixture_module
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io

WORKER = r"""
import hashlib
import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import UnixStreamServer
from urllib.parse import quote

root = Path(sys.argv[1])
config = json.loads((root / "private-crm-config.json").read_bytes())
database = Path(config["database"])
attachment = Path(config["attachment"])
assert database.is_relative_to(root) and attachment.is_relative_to(root)
assert ".." not in database.parts and ".." not in attachment.parts
assert not database.is_symlink() and not attachment.is_symlink()
identity = database.stat()
assert identity.st_uid == os.geteuid() and identity.st_nlink == 1

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/fixture-readiness":
            self.send_error(404)
            return
        before = database.stat()
        with sqlite3.connect("file:" + quote(str(database)) + "?mode=ro&immutable=1", uri=True) as db:
            db.execute("PRAGMA query_only=ON")
            records = db.execute("SELECT id,value FROM records ORDER BY id").fetchall()
        after = database.stat()
        assert (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        value = {
            "schema": "autostop.j1.artificial-crm-readiness.v1",
            "scope": "artificial_business_http",
            "source_revision": config["source_revision"],
            "candidate_image": config["candidate_image"],
            "pid": os.getpid(),
            "records": records,
            "attachment_sha256": hashlib.sha256(attachment.read_bytes()).hexdigest(),
        }
        raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass

with UnixStreamServer(str(root / "private-crm.sock"), Handler) as server:
    temporary = root / ".private-crm-ready"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(str(os.getpid()).encode())
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, root / "private-crm-ready")
    server.serve_forever(poll_interval=0.05)
"""


class PrivateCRM:
    def __init__(self, fixture, *, revision, image, start=True):
        io.require(fixture.provider.scope == "native_fixture", "trace_crm_fixture_scope")
        self.fixture, self.root = fixture, fixture.root
        self.revision, self.image = revision, image
        self.database = self.root / "business-fixture/crm/kanban.sqlite3"
        self.attachment = self.root / "business-fixture/crm/attachment.bin"
        self.database_ref = io.reference(self.database)
        self.attachment_ref = io.reference(self.attachment)
        self.config_ref = fixture._write(
            self.root / "private-crm-config.json",
            {
                "database": str(self.database),
                "attachment": str(self.attachment),
                "source_revision": revision,
                "candidate_image": image,
            },
        )
        self.worker_ref = fixture._copy(self.root / "private-crm.py", WORKER.encode())
        self.process = self.actor = self.spec_ref = None
        if start:
            self.start()

    def publish_spec(self, epoch_ref, package_ref):
        io.require(self.spec_ref is None, "trace_crm_spec_already_published")
        binding_ref = self.fixture._write(
            self.root / "private-crm-binding.json",
            {
                "schema": "autostop.j1.private-crm-instance-binding.v1",
                "scope": "native_fixture",
                "epoch_ref": epoch_ref,
                "package_ref": package_ref,
                "binding": self.fixture.binding,
                "worker_ref": self.worker_ref,
                "config_ref": self.config_ref,
                "sqlite_ref": self.database_ref,
                "attachment_ref": self.attachment_ref,
            },
        )
        self.spec_ref = self.fixture._write(
            self.root / "private-crm-spec.json",
            {
                "schema": "autostop.j1.cold-crm-container-spec.v1",
                "scope": "native_fixture",
                "role": "crm-embedded",
                "epoch_ref": epoch_ref,
                "package_ref": package_ref,
                "binding_ref": binding_ref,
                "boundary": "artificial_business_http",
                "production_container_created": False,
            },
        )
        return self.spec_ref

    def start(self):
        io.require(self.process is None, "trace_crm_start_already_consumed")
        io.referenced(self.worker_ref)
        io.referenced(self.config_ref)
        process = subprocess.Popen(
            [
                str(Path(os.sys.executable).resolve()),
                "-I",
                "-S",
                "-B",
                self.worker_ref["path"],
                str(self.root),
            ],
            cwd=self.root,
            env=fixture_module.ENVIRONMENT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.fixture.children.append(process)
        self.fixture.pidfds[process.pid] = os.pidfd_open(process.pid)
        self.process, self.actor = process, native.actor(process.pid)
        deadline = time.monotonic() + 5
        while not (self.root / "private-crm-ready").exists():
            io.require(process.poll() is None, "trace_crm_worker_exited")
            io.require(time.monotonic() < deadline, "trace_crm_start_deadline")
            time.sleep(0.01)
        io.require(
            io.read_protected(self.root / "private-crm-ready") == str(process.pid).encode(),
            "trace_crm_ready_identity",
        )

    def measure(self, epoch_ref, package_ref):
        io.require(self.process is not None, "trace_crm_not_started")
        io.require(native.actor(self.process.pid) == self.actor, "trace_crm_actor_changed")
        io.referenced(self.worker_ref)
        io.referenced(self.database_ref)
        io.referenced(self.attachment_ref)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(self.root / "private-crm.sock"))
            import struct

            peer = struct.unpack("3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            io.require(
                peer == (self.process.pid, os.geteuid(), os.getegid()), "trace_crm_peer_changed"
            )
            client.sendall(b"GET /fixture-readiness HTTP/1.0\r\nHost: private-fixture\r\n\r\n")
            raw = b""
            while chunk := client.recv(65536):
                raw += chunk
                io.require(len(raw) <= 65536, "trace_crm_response_bound")
        header, separator, body = raw.partition(b"\r\n\r\n")
        io.require(
            separator and header.split(b"\r\n", 1)[0] == b"HTTP/1.0 200 OK",
            "trace_crm_http_failed",
        )
        value = io.document(body)
        io.require(
            value
            == {
                "schema": "autostop.j1.artificial-crm-readiness.v1",
                "scope": "artificial_business_http",
                "source_revision": self.revision,
                "candidate_image": self.image,
                "pid": self.process.pid,
                "records": [[1, "synthetic"]],
                "attachment_sha256": self.attachment_ref["sha256"],
            },
            "trace_crm_readback_mismatch",
        )
        io.referenced(self.database_ref)
        io.referenced(self.attachment_ref)
        io.require(native.actor(self.process.pid) == self.actor, "trace_crm_actor_raced")
        result = {
            "role": "crm-embedded",
            "scope": "native_fixture",
            "epoch_ref": epoch_ref,
            "package_ref": package_ref,
            "actor": self.actor,
            "worker_ref": self.worker_ref,
            "readback_sha256": io.sha(body),
            "sqlite_ref": self.database_ref,
            "attachment_ref": self.attachment_ref,
            "production_crm_application_executed": False,
        }
        if self.spec_ref is not None:
            io.referenced(self.spec_ref)
            result["spec_ref"] = self.spec_ref
        return result


def fixture_backend(fixture, crm):
    from scripts import j1_forward_cold_lifecycle

    class Backend(j1_forward_cold_lifecycle.FixtureBackend):
        def parent_executor(self, request_ref):
            return fixture.parent.observe(request_ref)

        def observe_crm(self, reference, epoch_ref, package_ref):
            measured = crm.measure(epoch_ref, package_ref)
            io.require(
                io.document(io.referenced(reference)) == measured,
                "trace_crm_evidence_changed",
            )
            return measured

    return Backend(fixture)
