"""Confirm cached state against a Windows file held against writers."""

from __future__ import annotations

import ctypes
import mmap
import os
import stat
import sys
from contextlib import contextmanager
from ctypes import wintypes


def _open_read_without_writers(path):
    """Open an existing file while denying write and delete sharing."""
    if sys.platform != "win32":
        raise OSError("Windows file sharing is unavailable")
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    handle = create(str(path), 0x80000000, 0x00000001, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    try:
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def _identity(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


class _VerifiedFile:
    def __init__(self, path, handle, initial):
        self.path = path
        self.handle = handle
        self.initial = initial

    def current(self):
        try:
            return (
                _identity(os.fstat(self.handle.fileno())) == self.initial
                and _identity(self.path.stat()) == self.initial
            )
        except OSError:
            return False


@contextmanager
def verified_committed_file(path, payload):
    """Yield a pinned verified file, or None when verification is unavailable.

    The Windows sharing mode rejects concurrent ordinary writers and renames for
    the lifetime of the handle. A failed check is only a cache miss; callers
    retain the normal locked snapshot path.
    """
    handle = None
    mapping = None
    verified = None
    try:
        handle = _open_read_without_writers(path)
        initial = os.fstat(handle.fileno())
        if (
            stat.S_ISREG(initial.st_mode)
            and initial.st_ino
            and initial.st_size == len(payload)
            and payload
        ):
            candidate = _VerifiedFile(path, handle, _identity(initial))
            if candidate.current():
                mapping = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
                if len(mapping) == len(payload) and mapping.find(payload) == 0:
                    verified = candidate
    except (OSError, OverflowError, ValueError):
        verified = None
    try:
        yield verified
    finally:
        if mapping is not None:
            mapping.close()
        if handle is not None:
            handle.close()
