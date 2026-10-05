from __future__ import annotations

import os
import sqlite3
import threading
import weakref
from contextlib import contextmanager
from pathlib import Path

_ANCHOR_LOCK = threading.RLock()
_ANCHORS: weakref.WeakSet[IdleWalAnchor] = weakref.WeakSet()


def _close_connection(connection: sqlite3.Connection, owner_pid: int) -> None:
    if os.getpid() == owner_pid:
        with _ANCHOR_LOCK:
            connection.close()


class IdleWalAnchor:
    """Keep one WAL connection idle between exclusive consumer-page leases."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._owner_pid = os.getpid()
        self._finalizer: weakref.finalize | None = None
        with _ANCHOR_LOCK:
            _ANCHORS.add(self)

    def ensure_open(self) -> None:
        if os.getpid() != self._owner_pid:
            raise RuntimeError("An inherited change-feed store requires a fresh instance.")
        if self._finalizer is not None and self._finalizer.alive:
            return
        with _ANCHOR_LOCK:
            if self._finalizer is not None and self._finalizer.alive:
                return
            connection = sqlite3.connect(
                self._path, timeout=10.0, isolation_level=None, check_same_thread=False
            )
            try:
                connection.row_factory = sqlite3.Row
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

    @contextmanager
    def page_connection(self):
        """Lease this FULL connection exclusively for one consumer-page transaction."""
        if os.getpid() != self._owner_pid:
            raise RuntimeError("An inherited change-feed store requires a fresh instance.")
        with _ANCHOR_LOCK:
            self.ensure_open()
            info = self._finalizer.peek()
            connection = info[2][0]
            # A nested lease must not close or roll back its caller's transaction.
            if connection.in_transaction:
                raise RuntimeError("The change-feed page connection is already active.")
            try:
                cursor = connection.execute("PRAGMA synchronous")
                try:
                    synchronous = cursor.fetchone()[0]
                finally:
                    cursor.close()
                if synchronous != 2 or connection.row_factory is not sqlite3.Row:
                    raise RuntimeError("The change-feed page connection must retain FULL/Row.")
                yield connection
                if connection.in_transaction:
                    raise RuntimeError("The change-feed page connection retained a transaction.")
            except BaseException:
                self._close_open_connection(allow_active=True)
                raise

    def _close_open_connection(self, *, allow_active: bool = False) -> None:
        if self._finalizer is not None:
            info = self._finalizer.peek()
            if info is not None:
                connection = info[2][0]
                if connection.in_transaction and not allow_active:
                    raise RuntimeError("The active change-feed page connection cannot be closed.")
                _close_connection(connection, self._owner_pid)
                self._finalizer.detach()

    def close(self) -> None:
        if os.getpid() != self._owner_pid:
            return
        with _ANCHOR_LOCK:
            self._close_open_connection()


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
