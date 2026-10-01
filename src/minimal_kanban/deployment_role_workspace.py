"""Bounded evidence snapshots of durable role files; never restore over live work."""

from __future__ import annotations

import io
import os
import stat
import tarfile
from pathlib import Path, PurePosixPath

MAX_FILES = 4096
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + 1024 * 1024


class RoleWorkspaceError(ValueError):
    pass


def _regular_file_bytes(path: Path) -> bytes:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
        raise RoleWorkspaceError("Role workspace contains an unsupported or oversized file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise RoleWorkspaceError("Role workspace file changed while opening")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            encoded = handle.read(MAX_FILE_BYTES + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(encoded) > MAX_FILE_BYTES or (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) != (before.st_size, before.st_mtime_ns, before.st_ctime_ns):
        raise RoleWorkspaceError("Role workspace file changed while reading")
    return encoded


def _walk_error(error: OSError) -> None:
    raise error


def snapshot_workspace(source: Path, destination: Path) -> bool:
    """Archive regular files if the workspace exists, without creating or locking it."""
    if not source.exists() and not source.is_symlink():
        return False
    if source.is_symlink() or not stat.S_ISDIR(source.lstat().st_mode):
        raise RoleWorkspaceError("Role workspace is not a regular directory")
    total_bytes = 0
    file_count = 0
    with tarfile.open(destination, "w:gz") as archive:
        for directory, directories, files in os.walk(
            source, onerror=_walk_error, followlinks=False
        ):
            directories.sort()
            for name in directories:
                if not stat.S_ISDIR((Path(directory) / name).lstat().st_mode):
                    raise RoleWorkspaceError("Role workspace contains an unsupported directory")
            for name in sorted(files):
                file_count += 1
                if file_count > MAX_FILES:
                    raise RoleWorkspaceError("Role workspace exceeds the bounded file count")
                path = Path(directory) / name
                encoded = _regular_file_bytes(path)
                total_bytes += len(encoded)
                if total_bytes > MAX_TOTAL_BYTES:
                    raise RoleWorkspaceError("Role workspace exceeds the bounded total size")
                member = tarfile.TarInfo(path.relative_to(source).as_posix())
                member.size = len(encoded)
                member.mode = 0o600
                archive.addfile(member, io.BytesIO(encoded))
    destination.chmod(0o600)
    return True


def verify_workspace_snapshot(path: Path) -> None:
    """Read and validate evidence without extracting or modifying the live workspace."""
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise RoleWorkspaceError("Role workspace archive exceeds the bounded size")
    names: set[str] = set()
    total_bytes = 0
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                relative = PurePosixPath(member.name)
                if (
                    not member.isfile()
                    or not member.name
                    or not relative.parts
                    or relative.is_absolute()
                    or relative.as_posix() != member.name
                    or ".." in relative.parts
                    or "\\" in member.name
                    or ":" in member.name
                    or member.name in names
                    or member.size < 0
                    or member.size > MAX_FILE_BYTES
                ):
                    raise RoleWorkspaceError("Role workspace archive has an unsupported member")
                names.add(member.name)
                total_bytes += member.size
                if len(names) > MAX_FILES or total_bytes > MAX_TOTAL_BYTES:
                    raise RoleWorkspaceError("Role workspace archive exceeds its bounds")
                content = archive.extractfile(member)
                if content is None or len(content.read(MAX_FILE_BYTES + 1)) != member.size:
                    raise RoleWorkspaceError("Role workspace archive has an incomplete member")
    except (tarfile.TarError, EOFError) as exc:
        raise RoleWorkspaceError("Role workspace archive is invalid") from exc
