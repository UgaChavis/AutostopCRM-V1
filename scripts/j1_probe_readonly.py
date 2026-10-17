#!/usr/bin/env python3
"""Bounded J1 observer using the parent's verified native SQLite runtime.

This denies Python effects and SQL/schema writes, not SQLite's ordinary WAL
reader coordination in -shm. The parent owns source/runtime admission and the
outer timeout; this is not a sandbox for arbitrary hostile Python/native code.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import signal
import sqlite3
import stat
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

CACHE_PATH = Path("/var/cache/autostop-j1/research.sqlite3")
MAPS_PATH = Path("/proc/self/maps")
MIN_SQLITE = (3, 51, 3)
SCHEMA = "autostop.j1.research.v1"
TIMEOUT_SECONDS = 20
MAX_LIBRARY_BYTES = 64 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}\Z")


class ProbeDenied(RuntimeError):
    pass


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ProbeDenied(code)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_field")
        result[key] = value
    return result


def sqlite_payload(path: str) -> bool:
    name = Path(path).name.lower()
    return name.startswith(("_sqlite3", "libsqlite3", "sqlite3")) and (
        ".so" in name or name.endswith((".dll", ".pyd"))
    )


def parse_expected(raw: str) -> dict[str, str]:
    require(len(raw) <= 32_768, "sqlite_files_too_large")
    entries = json.loads(raw, object_pairs_hook=unique)
    require(isinstance(entries, list) and 2 <= len(entries) <= 8, "sqlite_files_invalid")
    result = {}
    for item in entries:
        require(isinstance(item, dict) and set(item) == {"path", "sha256"}, "sqlite_file_invalid")
        path, digest = item["path"], item["sha256"]
        require(isinstance(path, str) and Path(path).is_absolute(), "sqlite_path_invalid")
        require(str(Path(path).resolve(strict=True)) == path, "sqlite_path_not_physical")
        require(sqlite_payload(path) and path not in result, "sqlite_payload_invalid")
        require(isinstance(digest, str) and HEX.fullmatch(digest), "sqlite_hash_invalid")
        result[path] = digest
    names = [Path(path).name.lower() for path in result]
    require(any(name.startswith("_sqlite3") for name in names), "sqlite_extension_missing")
    require(
        any(name.startswith(("libsqlite3", "sqlite3")) for name in names), "sqlite_library_missing"
    )
    return result


def mapped_sqlite_files() -> dict[str, int]:
    with MAPS_PATH.open("r", encoding="utf-8", errors="strict") as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    require(len(raw) <= 4 * 1024 * 1024, "maps_too_large")
    result = {}
    for line in raw.splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6:
            continue
        path = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[5])
        if not sqlite_payload(path.removesuffix(" (deleted)")):
            continue
        require(not path.endswith(" (deleted)"), "sqlite_mapping_deleted")
        require(Path(path).is_absolute(), "sqlite_mapping_invalid")
        require(str(Path(path).resolve(strict=True)) == path, "sqlite_mapping_not_physical")
        inode = int(fields[4])
        require(inode > 0 and result.get(path, inode) == inode, "sqlite_mapping_changed")
        result[path] = inode
    require(bool(result), "sqlite_mappings_missing")
    return result


def payload_digest(path: str, inode: int) -> str:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_ino == inode, "sqlite_mapping_replaced")
        require(0 < before.st_size <= MAX_LIBRARY_BYTES, "sqlite_payload_size")
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            require(bool(chunk), "sqlite_payload_truncated")
            digest.update(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        require(
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
            "sqlite_payload_changed",
        )
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def verify_runtime(raw: str) -> None:
    require(sqlite3.sqlite_version_info >= MIN_SQLITE, "sqlite_version_unsupported")
    expected = parse_expected(raw)
    mapped = mapped_sqlite_files()
    require(set(mapped) == set(expected), "sqlite_mappings_mismatch")
    for path, digest in expected.items():
        require(payload_digest(path, mapped[path]) == digest, "sqlite_payload_hash_mismatch")
    require(mapped_sqlite_files() == mapped, "sqlite_mappings_changed")


class ReadOnlyConnection(sqlite3.Connection):
    def set_authorizer(self, *args, **kwargs):
        raise ProbeDenied("authorizer_replacement_denied")

    def enable_load_extension(self, *args, **kwargs):
        raise ProbeDenied("extension_loading_denied")

    def load_extension(self, *args, **kwargs):
        raise ProbeDenied("extension_loading_denied")

    def deserialize(self, *args, **kwargs):
        raise ProbeDenied("deserialize_denied")

    def setconfig(self, *args, **kwargs):
        raise ProbeDenied("connection_reconfiguration_denied")

    def blobopen(self, table, column, row, /, *, readonly=False, name="main"):
        require(readonly is True, "blob_write_denied")
        return super().blobopen(table, column, row, readonly=True, name=name)


class ReadOnlyGuard:
    def __init__(self):
        self.uri = CACHE_PATH.resolve(strict=True).as_uri() + "?mode=ro"
        self.original_connect = sqlite3.connect
        self.opening = False
        self.violation = ""

    def deny(self, code: str):
        self.violation = self.violation or code
        raise ProbeDenied(code)

    def audit(self, event, args):
        if event == "sqlite3.connect":
            if not self.opening or args != (self.uri,):
                self.deny("unguarded_sqlite_connection")
        elif event in {"sqlite3.enable_load_extension", "sqlite3.load_extension"}:
            self.deny("extension_loading_denied")
        elif event == "open":
            _, mode, flags = args
            write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
            if (flags & write_flags) or (mode is not None and any(c in mode for c in "wax+")):
                self.deny("filesystem_write_denied")
        elif event in {
            "os.mkdir",
            "os.remove",
            "os.rename",
            "os.rmdir",
            "os.link",
            "os.symlink",
            "os.chmod",
            "os.chown",
            "os.truncate",
            "os.utime",
            "os.setxattr",
            "os.removexattr",
            "os.chdir",
            "os.putenv",
            "os.unsetenv",
            "os.system",
            "os.exec",
            "os.posix_spawn",
            "os.spawn",
            "os.fork",
            "os.forkpty",
            "os.kill",
            "os.killpg",
            "pty.spawn",
            "subprocess.Popen",
            "socket.connect",
            "socket.bind",
            "socket.sendto",
            "socket.getaddrinfo",
            "socket.gethostbyname",
            "socket.gethostbyaddr",
            "socket.getnameinfo",
            "ctypes.dlopen",
            "ctypes.dlsym",
        }:
            self.deny("external_effect_denied")

    def authorize(self, action, first, second, database, source):
        if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_RECURSIVE}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and (second or "").lower() != "load_extension":
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA:
            name = (first or "").lower()
            if name == "busy_timeout" and second in {None, "5000"}:
                return sqlite3.SQLITE_OK
            if name in {
                "query_only",
                "data_version",
                "schema_version",
                "table_info",
                "table_xinfo",
            }:
                if second is None or name in {"table_info", "table_xinfo"}:
                    return sqlite3.SQLITE_OK
        self.violation = self.violation or "sql_operation_denied"
        return sqlite3.SQLITE_DENY

    def connect(self, database, *args, **kwargs):
        if (
            database != self.uri
            or args
            or set(kwargs) - {"uri", "timeout"}
            or kwargs.get("uri") is not True
            or kwargs.get("timeout", 5) != 5
        ):
            self.deny("sqlite_connection_arguments_denied")
        self.opening = True
        try:
            connection = self.original_connect(
                self.uri, uri=True, timeout=5, factory=ReadOnlyConnection
            )
        finally:
            self.opening = False
        try:
            connection.execute("PRAGMA query_only=ON")
            require(connection.execute("PRAGMA query_only").fetchone() == (1,), "query_only_failed")
            sqlite3.Connection.set_authorizer(connection, self.authorize)
            return connection
        except BaseException:
            connection.close()
            raise

    def install(self):
        sys.addaudithook(self.audit)
        sqlite3.connect = self.connect


class QuietOutput:
    def write(self, value):
        return len(value)

    def flush(self):
        pass


def run_probe(raw: str) -> dict:
    verify_runtime(raw)
    guard = ReadOnlyGuard()
    guard.install()
    with redirect_stdout(QuietOutput()), redirect_stderr(QuietOutput()):
        research = importlib.import_module("autostop_manager.j1_research")
        result = research.probe()
    require(not guard.violation, "probe_attempted_effect")
    verify_runtime(raw)
    require(isinstance(result, dict) and result.get("schema") == SCHEMA, "probe_result_invalid")
    require(
        all(type(result.get(key)) is bool for key in ("ok", "cache_ready", "search_configured")),
        "probe_result_invalid",
    )
    return {key: result[key] for key in ("ok", "schema", "cache_ready", "search_configured")}


def timed_out(signum, frame):
    raise ProbeDenied("probe_timeout")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--sqlite-files-json", required=True)
    args = parser.parse_args(argv)
    result = {"ok": False, "schema": SCHEMA, "cache_ready": False, "search_configured": False}
    status = 2
    try:
        require(sys.platform == "linux", "linux_required")
        require(
            sys.dont_write_bytecode and os.environ.get("PYTHONDONTWRITEBYTECODE") == "1",
            "bytecode_writes_enabled",
        )
        signal.signal(signal.SIGALRM, timed_out)
        signal.alarm(TIMEOUT_SECONDS)
        result = run_probe(args.sqlite_files_json)
        status = 0 if result["ok"] and result["cache_ready"] else 1
    except Exception:
        pass  # Only the fixed technical result may leave this observer.
    finally:
        if sys.platform == "linux":
            signal.alarm(0)
    print(json.dumps(result, separators=(",", ":")))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
