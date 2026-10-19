"""Versioned MCP guard: original warm chain plus two permanent denied barriers.

The reviewed v1 consumer is sealed as executable bytes, then configured in its
own module namespace. No installed v1 file, shared guard or old policy changes.
The v2 source capsule is a separately derived artifact, never a v1 reinterpretation.
"""

from __future__ import annotations

import hashlib
import os
import stat
import types
from pathlib import Path

CONTRACT = "manager_mcp_only_preserved_warm_permanent_denied_v2"
INSTALL = "/usr/local/libexec/autostop-manager-mcp-successor/v2"
CONFIG = "/etc/autostop-manager-mcp-successor/v2"
STATE_DIR = "/var/lib/autostop-manager-mcp-successor/v2"
GUARD, NATIVE = INSTALL + "/guard.py", INSTALL + "/native.py"
BINDING, CAPSULE = CONFIG + "/binding.json", CONFIG + "/source-capsule.json"
INDEX, BASELINE = CONFIG + "/source-index.json", CONFIG + "/predecessor-baseline.json"
RUNTIME = CONFIG + "/runtime-acceptance.json"
STATE, ADMISSION = STATE_DIR + "/armed.json", STATE_DIR + "/admission.json"
BASE = INSTALL + "/base-guard.py"
BASE_SHA256 = "19ab9bb2e800fcde15aad7fadd2af73b96ba4a3a6f322d162c7d693ed415dc06"
_BASE_MODULE = None


def _read(path, limit=32 * 1024**2, *, native=True):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("v2_bootstrap_path")
    for parent in reversed(path.parents):
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or native
            and (info.st_uid != 0 or info.st_mode & 0o022)
        ):
            raise ValueError("v2_bootstrap_directory")
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or native
        and before.st_uid != 0
        or before.st_nlink != 1
        or native
        and before.st_mode & 0o7022
        or getattr(before, "st_file_attributes", 0) & 0x400
        or not 0 <= before.st_size <= limit
    ):
        raise ValueError("v2_bootstrap_file")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        opened = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after, final = os.fstat(descriptor), path.lstat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(raw) != before.st_size or any(
            getattr(before, key) != getattr(opened, key)
            or getattr(before, key) != getattr(after, key)
            or getattr(before, key) != getattr(final, key)
            for key in fields
        ):
            raise ValueError("v2_bootstrap_changed")
        return raw
    finally:
        os.close(descriptor)


def load_guard(*, read=None, source_mode=None):
    """Source imports and installed entry have separate fixed base locators."""
    global _BASE_MODULE
    if _BASE_MODULE is not None and read is None and source_mode is None:
        return _BASE_MODULE
    source_mode = __package__ == "scripts" if source_mode is None else source_mode
    path = str(Path(__file__).with_name("j1_manager_successor_guard.py")) if source_mode else BASE
    reader = read or (lambda location: _read(location, native=not source_mode))
    raw = reader(path)
    if hashlib.sha256(raw).hexdigest() != BASE_SHA256:
        raise ValueError("v2_base_guard_changed")
    module = types.ModuleType("_sealed_manager_successor_guard_v2_base")
    module.__file__ = GUARD
    exec(compile(raw, path, "exec"), module.__dict__)
    for name in (
        "CONTRACT",
        "INSTALL",
        "CONFIG",
        "STATE_DIR",
        "GUARD",
        "NATIVE",
        "BINDING",
        "CAPSULE",
        "INDEX",
        "BASELINE",
        "RUNTIME",
        "STATE",
        "ADMISSION",
    ):
        setattr(module, name, globals()[name])
    if reader(path) != raw:
        raise ValueError("v2_base_guard_changed")
    if read is None:
        _BASE_MODULE = module
    return module


def __getattr__(name):
    return getattr(load_guard(), name)


if __name__ == "__main__":
    try:
        if Path(__file__) != Path(GUARD):
            raise ValueError("v2_installed_entry_required")
        raise SystemExit(load_guard(source_mode=False).main())
    except Exception:
        print('{"ok":false,"error":"v2_bootstrap_refused"}')
        raise SystemExit(2) from None
