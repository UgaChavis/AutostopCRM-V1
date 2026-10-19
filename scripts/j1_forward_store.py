"""Bound one Store pre-stop handshake to the current native Manager incarnation."""

from __future__ import annotations

import math
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from scripts import j1_forward_io as io

UNIT = "autostop-manager-mcp.service"
LEGACY_REVISION = "89af29082e0a6f72c5d85ae448acb6d4deec5d94"
ADDRESS = ("127.0.0.1", 41931)
PROC = Path("/proc")
CGROUP = Path("/sys/fs/cgroup")
MAX_RESPONSE = 65536
REQUEST_SECONDS = 2.0
POLL_SECONDS = 0.25
STATUS_PATH = "/internal/store-workers"


class RequestUncertain(io.Refusal):
    """A transport failure cannot prove whether a drain POST reached Manager."""


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    io.require(remaining > 0, "store_drain_deadline")
    return remaining


def _read(path, limit=65536):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    io.require(len(raw) <= limit, "store_identity_too_large")
    return raw


def _birth(pid):
    fields = _read(PROC / str(pid) / "stat").rsplit(b") ", 1)[1].split()
    io.require(fields[0] not in (b"Z", b"X", b"x"), "store_process_dead")
    start = fields[19].decode("ascii")
    io.require(start.isdecimal() and int(start) > 0, "store_process_birth_invalid")
    return start


def _empty_group():
    io.require((CGROUP / "cgroup.controllers").is_file(), "store_cgroup_v2_required")
    group = CGROUP / "system.slice" / UNIT
    try:
        group.lstat()
    except FileNotFoundError:
        return
    fields = dict(line.split() for line in _read(group / "cgroup.events").decode().splitlines())
    io.require(fields.get("populated") == "0", "store_inactive_cgroup_populated")


def _identity(native, command, deadline):
    """Read systemd and /proc afresh; no prepared PID or prior receipt is authority."""
    _remaining(deadline)
    io.require(sys.platform == "linux", "store_native_linux_required")
    revision = native.get("manager_revision")
    binding = native.get("profiles", {}).get(UNIT)
    io.require(
        type(revision) is str and re.fullmatch(r"[0-9a-f]{40}", revision) and type(binding) is dict,
        "store_native_binding_required",
    )
    for name in ("source_root", "cwd", "interpreter", "interpreter_realpath"):
        value = binding.get(name)
        io.require(
            type(value) is str and value.startswith("/") and ".." not in value.split("/"),
            "store_native_binding_required",
        )
    io.require(
        binding.get("module") == "autostop_manager.mcp_server"
        and type(binding.get("arguments")) is list
        and all(type(value) is str for value in binding["arguments"]),
        "store_native_binding_required",
    )
    raw = command(
        [
            "/usr/bin/systemctl",
            "show",
            UNIT,
            "--no-pager",
            "--property=MainPID,ActiveState,SubState,ControlGroup,InvocationID",
        ],
        deadline,
    )
    io.require(type(raw) is bytes and len(raw) <= 65536, "store_systemd_response_invalid")
    rows = [line.split("=", 1) for line in raw.decode().splitlines()]
    io.require(
        all(len(row) == 2 for row in rows) and len({row[0] for row in rows}) == len(rows),
        "store_systemd_response_invalid",
    )
    fields = dict(rows)
    pid_text = fields.get("MainPID", "")
    io.require(pid_text.isascii() and pid_text.isdecimal(), "store_main_pid_invalid")
    pid = int(pid_text)
    group = "/system.slice/" + UNIT
    boot = _read(PROC / "sys/kernel/random/boot_id").decode().strip()
    io.require(
        re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot),
        "store_boot_identity_invalid",
    )
    if pid == 0:
        io.require(
            (fields.get("ActiveState"), fields.get("SubState"))
            in {("inactive", "dead"), ("failed", "failed")}
            and fields.get("ControlGroup") in ("", group),
            "store_inactive_state_unproven",
        )
        _empty_group()
        _remaining(deadline)
        return {"pid": 0, "start_ticks": None, "boot_id": boot, "revision": revision}
    io.require(
        pid > 1
        and fields.get("ActiveState") == "active"
        and fields.get("SubState") == "running"
        and fields.get("ControlGroup") == group
        and re.fullmatch(r"[0-9a-f]{32}", fields.get("InvocationID", "")),
        "store_active_process_required",
    )
    start = _birth(pid)
    process = PROC / str(pid)
    io.require(
        _read(process / "cgroup").decode().strip() == "0::" + group
        and os.readlink(process / "cwd") == binding["cwd"]
        and os.readlink(process / "exe") == binding["interpreter_realpath"],
        "store_process_binding_changed",
    )
    expected = [binding["interpreter"], "-P", "-m", binding["module"], *binding["arguments"]]
    io.require(
        _read(process / "cmdline").split(b"\0") == [value.encode() for value in expected] + [b""],
        "store_process_argv_changed",
    )
    source = Path(binding["source_root"])
    io.require(
        io.read_protected(source / "REVISION", limit=128).decode().strip() == revision,
        "store_process_revision_changed",
    )
    environment = _read(process / "environ").split(b"\0")
    source_values = [row for row in environment if row.startswith(b"PYTHONPATH=")]
    io.require(
        source_values == [b"PYTHONPATH=" + binding["source_root"].encode()]
        and _birth(pid) == start,
        "store_process_source_changed",
    )
    _remaining(deadline)
    return {
        "pid": pid,
        "start_ticks": start,
        "boot_id": boot,
        "revision": revision,
        "invocation_id": fields["InvocationID"],
        "source_root": binding["source_root"],
    }


def _same(native, command, expected, deadline):
    io.require(
        _identity(native, command, deadline) == expected, "store_process_incarnation_changed"
    )


def _endpoint(address):
    # /proc/net/tcp encodes IPv4 in host byte order on Linux.
    return socket.inet_aton(address[0])[::-1].hex().upper() + ":" + format(address[1], "04X")


def _owns_connection(pid, connection):
    local = _endpoint(connection.getpeername())
    remote = _endpoint(connection.getsockname())
    matches = []
    for line in _read(PROC / str(pid) / "net/tcp", 2 * 1024**2).decode().splitlines()[1:]:
        fields = line.split()
        if len(fields) > 9 and fields[1:4] == [local, remote, "01"]:
            matches.append(fields[9])
    io.require(len(matches) <= 1, "store_socket_peer_ambiguous")
    if not matches or matches[0] == "0":
        return False
    expected = "socket:[" + matches[0] + "]"
    for descriptor in (PROC / str(pid) / "fd").iterdir():
        try:
            if os.readlink(descriptor) == expected:
                return True
        except FileNotFoundError:
            continue
    return False


def _receive(connection, deadline, size):
    connection.settimeout(_remaining(deadline))
    raw = connection.recv(size)
    if not raw:
        raise RequestUncertain("store_http_incomplete")
    return raw


def _response(connection, deadline):
    raw = bytearray()
    while b"\r\n\r\n" not in raw:
        io.require(len(raw) < 16384, "store_http_headers_too_large")
        raw.extend(_receive(connection, deadline, min(4096, 16384 - len(raw))))
    header, body = bytes(raw).split(b"\r\n\r\n", 1)
    lines = header.split(b"\r\n")
    io.require(
        re.fullmatch(rb"HTTP/1\.[01] [0-9]{3}(?: [\x20-\x7e]*)?", lines[0]) and len(lines) <= 33,
        "store_http_headers_invalid",
    )
    headers = {}
    for line in lines[1:]:
        name, separator, value = line.partition(b":")
        name = name.lower()
        io.require(
            separator
            and re.fullmatch(rb"[a-z0-9-]+", name)
            and name not in headers
            and all(32 <= char < 127 for char in value),
            "store_http_headers_invalid",
        )
        headers[name] = value.strip()
    length = headers.get(b"content-length", b"")
    io.require(
        re.fullmatch(rb"[0-9]{1,6}", length)
        and b"transfer-encoding" not in headers
        and b"content-encoding" not in headers
        and headers.get(b"content-type", b"").lower().split(b";", 1)[0] == b"application/json",
        "store_http_framing_invalid",
    )
    length = int(length)
    io.require(0 < length <= MAX_RESPONSE - len(header) - 4, "store_http_body_too_large")
    io.require(len(body) <= length, "store_http_extra_bytes")
    while len(body) < length:
        body += _receive(connection, deadline, min(4096, length - len(body)))
    _remaining(deadline)
    return int(lines[0].split()[1]), body


def _request(method, instance, identity, deadline):
    # Direct numeric sockets do not consult proxies, DNS, redirects or netrc.
    request_end = min(deadline, time.monotonic() + REQUEST_SECONDS)
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        connection.settimeout(_remaining(request_end))
        connection.connect(ADDRESS)
        io.require(connection.getpeername() == ADDRESS, "store_socket_peer_changed")
        while not _owns_connection(identity["pid"], connection):
            time.sleep(min(0.01, _remaining(request_end)))
        path = STATUS_PATH + ("/drain" if method == "POST" else "")
        headers = [
            f"{method} {path} HTTP/1.1",
            f"Host: {ADDRESS[0]}:{ADDRESS[1]}",
            "Accept: application/json",
            "Connection: close",
        ]
        if method == "POST":
            headers += ["Content-Length: 0", "X-Autostop-Store-Instance: " + instance]
        data = ("\r\n".join(headers) + "\r\n\r\n").encode("ascii")
        while data:
            connection.settimeout(_remaining(request_end))
            sent = connection.send(data)
            if sent == 0:
                raise RequestUncertain("store_http_send_incomplete")
            data = data[sent:]
        return _response(connection, request_end)
    except io.Refusal as exc:
        if str(exc) == "store_drain_deadline":
            raise RequestUncertain("store_http_deadline_unknown") from exc
        raise
    except OSError as exc:
        raise RequestUncertain("store_http_transport_unknown") from exc
    finally:
        connection.close()


def _snapshot(status, raw, method, pid, instance=None):
    io.require(status in ({200, 202} if method == "POST" else {200}), "store_http_status_invalid")
    value = io.document(raw)
    io.require(
        type(value) is dict
        and set(value)
        == {"format", "instance_id", "pid", "accepting", "status", "drained", "pending"}
        and value.get("format") == "store_worker_lifecycle_v1"
        and type(value.get("instance_id")) is str
        and re.fullmatch(r"[0-9a-f]{32}", value["instance_id"])
        and type(value.get("pid")) is int
        and value["pid"] == pid
        and type(value.get("accepting")) is bool
        and type(value.get("drained")) is bool,
        "store_status_schema_invalid",
    )
    io.require(instance in (None, value["instance_id"]), "store_instance_changed")
    pending = value.get("pending")
    io.require(
        type(pending) is dict
        and set(pending) == {"read", "stateful", "transport"}
        and all(type(count) is int and 0 <= count < 2**31 for count in pending.values()),
        "store_pending_invalid",
    )
    drained = not value["accepting"] and all(count == 0 for count in pending.values())
    expected = "accepting" if value["accepting"] else "drained" if drained else "draining"
    io.require(
        value["status"] == expected and value["drained"] is drained, "store_status_inconsistent"
    )
    if method == "POST":
        io.require(
            not value["accepting"] and status == (200 if drained else 202), "store_post_not_closed"
        )
    return value


def drain_before_stop(native: dict, *, deadline: float, command) -> dict:
    """Permit only this observed incarnation; any refusal leaves stopping to nobody."""
    io.require(type(deadline) in (int, float) and math.isfinite(deadline), "store_deadline_invalid")
    try:
        identity = _identity(native, command, deadline)
        report = {
            "ok": True,
            "stop_permitted": True,
            "identity": identity,
            "worker_drain_supported": False,
            "store_workers_drained": None,
        }
        if identity["pid"] == 0 or identity["revision"] == LEGACY_REVISION:
            _same(native, command, identity, deadline)
            mode = "no_active_process" if identity["pid"] == 0 else "legacy_unsupported"
            return {**report, "mode": mode}

        def request(method, instance=None):
            _same(native, command, identity, deadline)
            try:
                status, raw = _request(method, instance, identity, deadline)
            finally:
                _same(native, command, identity, deadline)
            return _snapshot(status, raw, method, identity["pid"], instance)

        snapshot = request("GET")
        instance = snapshot["instance_id"]
        unknown = False
        if snapshot["accepting"]:
            try:
                snapshot = request("POST", instance)
            except RequestUncertain:
                unknown = True
                snapshot = request("GET", instance)
        io.require(not snapshot["accepting"], "store_admission_still_open")
        while not snapshot["drained"]:
            time.sleep(min(POLL_SECONDS, _remaining(deadline)))
            snapshot = request("GET", instance)
            io.require(not snapshot["accepting"], "store_admission_reopened")
        _same(native, command, identity, deadline)
        return {
            **report,
            "mode": "store_workers_drained",
            "worker_drain_supported": True,
            "store_workers_drained": True,
            "instance_id": instance,
            "pending": snapshot["pending"],
            "post_outcome_recovered": unknown,
        }
    except io.Refusal:
        raise
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as exc:
        raise io.Refusal("store_stop_proof_unavailable") from exc


def verify_stop_identity(native: dict, report: dict, *, deadline: float, command) -> None:
    """Recheck after receipt persistence, immediately before the caller's stop."""
    io.require(type(deadline) in (int, float) and math.isfinite(deadline), "store_deadline_invalid")
    try:
        io.require(
            type(report) is dict
            and report.get("ok") is True
            and report.get("stop_permitted") is True
            and type(report.get("identity")) is dict
            and report.get("mode")
            in {"legacy_unsupported", "no_active_process", "store_workers_drained"},
            "store_stop_report_invalid",
        )
        _same(native, command, report["identity"], deadline)
    except io.Refusal:
        raise
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as exc:
        raise io.Refusal("store_stop_identity_unavailable") from exc
