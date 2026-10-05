from __future__ import annotations

import os
import sqlite3
import threading
import weakref
from pathlib import Path

_ANCHOR_LOCK = threading.RLock()
_ANCHORS: weakref.WeakSet[IdleWalAnchor] = weakref.WeakSet()


def _close_connection(connection: sqlite3.Connection, owner_pid: int) -> None:
    if os.getpid() == owner_pid:
        with _ANCHOR_LOCK:
            connection.close()


class IdleWalAnchor:
    """Keep a WAL index alive without sharing business SQL or a read snapshot."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._owner_pid = os.getpid()
        self._finalizer: weakref.finalize | None = None
        with _ANCHOR_LOCK:
            _ANCHORS.add(self)

    def ensure_open(self) -> None:
        if os.getpid() != self._owner_pid:
            raise RuntimeError("An inherited change-feed store requires a fresh instance.")
        with _ANCHOR_LOCK:
            if self._finalizer is not None and self._finalizer.alive:
                return
            connection = sqlite3.connect(
                self._path, timeout=10.0, isolation_level=None, check_same_thread=False
            )
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 10000")
                connection.execute("PRAGMA synchronous = FULL")
                cursor = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                )
                try:
                    cursor.fetchall()
                finally:
                    cursor.close()
                if connection.in_transaction:
                    raise RuntimeError("The idle WAL anchor must not retain a transaction.")
            except BaseException:
                connection.close()
                raise
            self._finalizer = weakref.finalize(self, _close_connection, connection, self._owner_pid)

    def close(self) -> None:
        if os.getpid() != self._owner_pid:
            return
        with _ANCHOR_LOCK:
            if self._finalizer is not None:
                info = self._finalizer.peek()
                if info is not None:
                    _close_connection(info[2][0], self._owner_pid)
                    self._finalizer.detach()


def _before_fork() -> None:
    _ANCHOR_LOCK.acquire()
    first_error: BaseException | None = None
    for anchor in tuple(_ANCHORS):
        try:
            anchor.close()
        except BaseException as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


def _after_fork_parent() -> None:
    _ANCHOR_LOCK.release()


def _after_fork_child() -> None:
    global _ANCHOR_LOCK, _ANCHORS
    _ANCHOR_LOCK = threading.RLock()
    _ANCHORS = weakref.WeakSet()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=_before_fork,
        after_in_parent=_after_fork_parent,
        after_in_child=_after_fork_child,
    )
