"""Fixed legacy maintenance marker in the application's pinned data directory.

The root-private pending/committed state and original deployment lock remain
the authority. Mutation callers provide a fresh authority check; this marker
is only the legacy application's operational gate. Its application-owned
parent permits unlink/rename, so callers must exclude concurrent marker-name
writers. Directory and file readbacks refuse observable replacement races;
Linux unlinkat does not provide an atomic expected-inode comparison.

No ancestor or data-directory permission is changed. A partially created
marker is retained on failure, keeping the legacy is_file() gate closed.
"""

from __future__ import annotations

import os
import re
import stat
import sys
import uuid
from contextlib import contextmanager
from os import close as _close
from os import fstat as _fstat
from os import fsync as _fsync
from os import open as _open
from os import read as _read
from os import stat as _stat
from os import unlink as _unlink
from os import write as _write

from scripts import j1_forward_io as io

CRM = "/opt/autostopcrm"
PARENT = CRM + "/data"
NAME = ".agent-gateway-maintenance"
PATH = PARENT + "/" + NAME
PARENT_SCHEMA = "autostop.j1.crm-forward-marker-parent.v1"
IDENTITY_SCHEMA = "autostop.j1.crm-forward-marker-identity.v1"
PAYLOAD_SCHEMA = "autostop.j1.crm-forward-maintenance.v1"
MAX_BYTES = 4096
_DIRECTORY_FIELDS = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode")
_FILE_FIELDS = (*_DIRECTORY_FIELDS, "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")


def _native():
    io.require(
        sys.platform == "linux" and os.geteuid() == 0 and os.getegid() == 0,
        "marker_native_root_required",
    )


def _fields(info, names):
    return tuple(getattr(info, name) for name in names)


def _payload(value):
    io.require(
        type(value) is dict
        and set(value) == {"schema", "attempt_id", "plan_sha256"}
        and value["schema"] == PAYLOAD_SCHEMA
        and type(value["attempt_id"]) is str
        and type(value["plan_sha256"]) is str
        and re.fullmatch(r"[0-9a-f]{64}", value["plan_sha256"]),
        "marker_payload_invalid",
    )
    try:
        valid = str(uuid.UUID(value["attempt_id"])) == value["attempt_id"]
    except ValueError:
        valid = False
    io.require(valid, "marker_attempt_invalid")
    raw = io.canonical(value)
    io.require(len(raw) <= MAX_BYTES, "marker_payload_limit")
    return raw


def _pin(info):
    io.require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == 10001
        and info.st_gid == 10001
        and stat.S_IMODE(info.st_mode) == 0o700,
        "marker_data_directory_invalid",
    )
    return {
        "schema": PARENT_SCHEMA,
        "path": PARENT,
        "dev": info.st_dev,
        "ino": info.st_ino,
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
    }


def validate_parent(pin: dict) -> None:
    """Validate the portable parent pin; live identity checks remain separate."""
    io.require(
        type(pin) is dict
        and set(pin) == {"schema", "path", "dev", "ino", "uid", "gid", "mode"}
        and pin["schema"] == PARENT_SCHEMA
        and pin["path"] == PARENT
        and all(type(pin[key]) is int for key in ("dev", "ino", "uid", "gid", "mode"))
        and pin["dev"] >= 0
        and pin["ino"] > 0
        and pin["uid"] == 10001
        and pin["gid"] == 10001
        and pin["mode"] == 0o700,
        "marker_parent_pin_invalid",
    )


@contextmanager
def _directory(expected=None):
    _native()
    if expected is not None:
        validate_parent(expected)
    io.protected_directory(CRM)
    root_before = _stat(CRM, follow_symlinks=False)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root_fd = _open(CRM, flags)
    data_fd = None
    try:
        io.require(
            _fields(_fstat(root_fd), _DIRECTORY_FIELDS) == _fields(root_before, _DIRECTORY_FIELDS),
            "marker_root_directory_changed",
        )
        before = _stat("data", dir_fd=root_fd, follow_symlinks=False)
        pin = _pin(before)
        io.require(expected is None or pin == expected, "marker_parent_changed")
        data_fd = _open("data", flags, dir_fd=root_fd)

        def refresh():
            io.protected_directory(CRM)
            io.require(
                _fields(_fstat(root_fd), _DIRECTORY_FIELDS)
                == _fields(_stat(CRM, follow_symlinks=False), _DIRECTORY_FIELDS)
                == _fields(root_before, _DIRECTORY_FIELDS),
                "marker_root_directory_changed",
            )
            io.require(
                _pin(_fstat(data_fd))
                == _pin(_stat("data", dir_fd=root_fd, follow_symlinks=False))
                == _pin(_stat(PARENT, follow_symlinks=False))
                == pin,
                "marker_parent_changed",
            )

        refresh()
        yield data_fd, pin, refresh
    finally:
        if data_fd is not None:
            _close(data_fd)
        _close(root_fd)


def _entry(directory):
    try:
        return _stat(NAME, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _regular(info):
    io.require(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == 0
        and info.st_gid == 0
        and stat.S_IMODE(info.st_mode) == 0o600
        and info.st_nlink == 1
        and 0 <= info.st_size <= MAX_BYTES,
        "marker_owned_file_required",
    )


def _identity(info, pin, raw):
    return {
        "schema": IDENTITY_SCHEMA,
        "path": PATH,
        "parent": dict(pin),
        "file": {name.removeprefix("st_"): getattr(info, name) for name in _FILE_FIELDS},
        "sha256": io.sha(raw),
    }


def _read_owned(directory, pin, raw, refresh, *, durable=False):
    refresh()
    before = _entry(directory)
    io.require(before is not None, "owned_maintenance_marker_missing")
    _regular(before)
    descriptor = _open(
        NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory
    )
    try:
        opened = _fstat(descriptor)
        _regular(opened)
        io.require(
            _fields(before, _FILE_FIELDS) == _fields(opened, _FILE_FIELDS), "marker_file_changed"
        )
        value = bytearray()
        while len(value) <= MAX_BYTES:
            part = _read(descriptor, MAX_BYTES + 1 - len(value))
            if not part:
                break
            value.extend(part)
        after, final = _fstat(descriptor), _entry(directory)
        io.require(
            final is not None
            and _fields(before, _FILE_FIELDS)
            == _fields(after, _FILE_FIELDS)
            == _fields(final, _FILE_FIELDS),
            "marker_file_changed",
        )
        io.require(
            bytes(value) == raw and len(value) == before.st_size, "owned_maintenance_marker_changed"
        )
        if durable:
            _fsync(descriptor)
            _fsync(directory)
            durable_entry = _entry(directory)
            io.require(
                _fields(_fstat(descriptor), _FILE_FIELDS) == _fields(after, _FILE_FIELDS)
                and durable_entry is not None
                and _fields(durable_entry, _FILE_FIELDS) == _fields(after, _FILE_FIELDS),
                "marker_file_changed",
            )
        refresh()
        return _identity(after, pin, raw)
    finally:
        _close(descriptor)


def prepare() -> dict:
    """Pin the legitimate data leaf and require marker absence before pending."""
    with _directory() as (directory, pin, refresh):
        io.require(_entry(directory) is None, "preexisting_maintenance_marker")
        refresh()
        io.require(_entry(directory) is None, "preexisting_maintenance_marker")
        return pin


def require_owned(pin: dict, payload: dict, *, expected_identity=None) -> dict:
    """Read the fixed marker without taking the writer's deployment lock."""
    validate_parent(pin)
    raw = _payload(payload)
    with _directory(pin) as (directory, current, refresh):
        identity = _read_owned(directory, current, raw, refresh)
        if expected_identity is not None:
            io.require(
                type(expected_identity) is dict
                and io.canonical(identity) == io.canonical(expected_identity),
                "marker_owned_identity_changed",
            )
        return identity


def ensure(pin: dict, payload: dict, *, authorize) -> dict:
    """Create or verify the pending marker; authorize must raise on rejection."""
    validate_parent(pin)
    raw = _payload(payload)
    io.require(callable(authorize), "marker_authority_required")
    authorize()
    with _directory(pin) as (directory, current, refresh):
        if _entry(directory) is not None:
            return _read_owned(directory, current, raw, refresh, durable=True)
        descriptor = _open(
            NAME,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory,
        )
        try:
            created = _fstat(descriptor)
            _regular(created)
            # Persist the empty is_file() gate before any fallible authority or
            # payload write. Partial-write failure must not lose the name on crash.
            _fsync(descriptor)
            _fsync(directory)
            authorize()
            refresh()
            entry = _entry(directory)
            io.require(
                entry is not None
                and _fields(created, _FILE_FIELDS) == _fields(entry, _FILE_FIELDS),
                "marker_file_changed",
            )
            offset = 0
            while offset < len(raw):
                written = _write(descriptor, raw[offset:])
                io.require(written > 0, "marker_write_incomplete")
                offset += written
            _fsync(descriptor)
            final = _fstat(descriptor)
            _regular(final)
            io.require(final.st_size == len(raw), "marker_write_incomplete")
        finally:
            _close(descriptor)
        _fsync(directory)
        identity = _read_owned(directory, current, raw, refresh)
        io.require(
            identity["file"]["dev"] == created.st_dev and identity["file"]["ino"] == created.st_ino,
            "marker_file_changed",
        )
        return identity


def remove_owned(pin: dict, payload: dict, *, expected_identity: dict, authorize) -> None:
    """Remove the exact observed marker under the caller's committed authority.

    The caller must exclude concurrent marker-name mutation; unlinkat cannot
    condition deletion on an inode. Any replacement visible to readback refuses.
    The authorize callback must raise if the caller's current authority is lost.
    """
    validate_parent(pin)
    raw = _payload(payload)
    io.require(type(expected_identity) is dict and callable(authorize), "marker_authority_required")
    authorize()
    with _directory(pin) as (directory, current, refresh):
        identity = _read_owned(directory, current, raw, refresh)
        io.require(
            io.canonical(identity) == io.canonical(expected_identity),
            "marker_owned_identity_changed",
        )
        authorize()
        refresh()
        entry = _entry(directory)
        io.require(
            entry is not None and _identity(entry, current, raw) == identity,
            "marker_file_changed",
        )
        _unlink(NAME, dir_fd=directory)
        _fsync(directory)
        refresh()
        io.require(_entry(directory) is None, "marker_reappeared")
