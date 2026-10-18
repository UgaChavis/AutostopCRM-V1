"""Strict root-owned files for the post-arm CRM successor protocol."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
from pathlib import Path

ROOT_UID = 0


class Refusal(ValueError):
    """A bounded protocol error, safe to expose without source contents."""


def require(condition: object, code: str) -> None:
    if not condition:
        raise Refusal(code)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _constant(_value):
    raise Refusal("nonfinite_json")


def _finite_float(value):
    parsed = float(value)
    require(math.isfinite(parsed), "nonfinite_json")
    return parsed


def document(raw: bytes) -> object:
    try:
        return json.loads(
            raw, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_finite_float
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise Refusal("invalid_json") from exc


def protected_directory(path: Path | str, *, private: bool = False) -> Path:
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "absolute_physical_path_required")
    for part in [*reversed(path.parents), path]:
        info = part.lstat()
        require(
            stat.S_ISDIR(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and not getattr(info, "st_file_attributes", 0) & 0x400,
            "physical_directory_required",
        )
        require(
            info.st_uid == ROOT_UID and not info.st_mode & 0o022,
            "root_protected_directory_required",
        )
    if private:
        require(stat.S_IMODE(path.stat().st_mode) == 0o700, "private_directory_required")
    return path


def read_protected(path: Path | str, limit: int = 2 * 1024**2) -> bytes:
    path = Path(path)
    protected_directory(path.parent)
    before = path.lstat()
    require(
        stat.S_ISREG(before.st_mode)
        and before.st_nlink == 1
        and before.st_uid == ROOT_UID
        and not before.st_mode & 0o022
        and not getattr(before, "st_file_attributes", 0) & 0x400
        and 0 <= before.st_size <= limit,
        "root_protected_regular_file_required",
    )
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        opened = os.fstat(descriptor)
        require(
            (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
            "file_identity_changed",
        )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(descriptor)
        final = path.lstat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        require(
            len(raw) == before.st_size <= limit
            and all(
                getattr(before, key)
                == getattr(opened, key)
                == getattr(after, key)
                == getattr(final, key)
                for key in fields
            ),
            "protected_file_changed",
        )
        return raw
    finally:
        os.close(descriptor)


def reference(path: Path | str) -> dict:
    return {"path": str(Path(path)), "sha256": sha(read_protected(path))}


def referenced(ref: dict) -> bytes:
    require(type(ref) is dict and set(ref) == {"path", "sha256"}, "reference_shape_invalid")
    require(
        type(ref["path"]) is str
        and type(ref["sha256"]) is str
        and len(ref["sha256"]) == 64
        and all(c in "0123456789abcdef" for c in ref["sha256"]),
        "reference_invalid",
    )
    raw = read_protected(ref["path"])
    require(sha(raw) == ref["sha256"], "reference_hash_mismatch")
    return raw


def confirm_durable(ref: dict) -> None:
    """Re-establish a verified immutable file's file and directory barriers."""
    referenced(ref)
    path = Path(ref["path"])
    before = path.lstat()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        require(
            (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino), "durable_file_changed"
        )
        os.fsync(descriptor)
        fsync_directory(path.parent)
        referenced(ref)
        after = path.lstat()
        require(
            (after.st_dev, after.st_ino) == (opened.st_dev, opened.st_ino), "durable_file_changed"
        )
    finally:
        os.close(descriptor)


def fsync_directory(path: Path | str) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_new(path: Path | str, value: dict) -> dict:
    path = Path(path)
    protected_directory(path.parent)
    raw = canonical(value)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_directory(path.parent)
    return {"path": str(path), "sha256": sha(raw)}


def atomic_replace(path: Path | str, value: dict) -> dict:
    """Only for checked state pointers/progress; immutable evidence uses write_new."""
    path = Path(path)
    protected_directory(path.parent)
    if path.exists() or path.is_symlink():
        read_protected(path)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        raw = canonical(value)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
        return {"path": str(path), "sha256": sha(raw)}
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
