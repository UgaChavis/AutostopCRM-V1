"""Sealed v1 native verifier with an exact permanent deny overlay for two units."""

from __future__ import annotations

import copy
import hashlib
import os
import re
import stat
import types
from pathlib import Path

INSTALL = Path("/usr/local/libexec/autostop-manager-mcp-successor/v2")
STATE = Path("/var/lib/autostop-manager-mcp-successor/v2")
BASE = INSTALL / "base-native.py"
BASE_SHA256 = "9e3baea7905f59fbf01cbc9422f0533eb9d646ad7cfcf24230a74a4adaf6f33c"
DENY_NAME = "zz-j1-forward-closed.conf"
DENY_BYTES = (
    b"[Service]\nExecCondition=/usr/bin/false\nExecStartPre=\nExecStartPost=\n"
    b"ExecStop=\nExecStopPost=\nExecReload=\n"
)


def validate_command_records(raw, expected):
    """Require exact executable and non-ignored conditions, retaining all records."""
    records = re.findall(r"\{([^{}]*)\}", raw)
    if re.sub(r"\{[^{}]*\}", "", raw).strip() or len(records) != len(expected):
        raise ValueError("v2_loaded_command_records")
    for body, command in zip(records, expected, strict=True):
        rows = [part.strip().partition("=") for part in body.split(";") if part.strip()]
        if any(not separator for _name, separator, _value in rows) or len(
            {row[0] for row in rows}
        ) != len(rows):
            raise ValueError("v2_loaded_command_fields")
        fields = {name: value for name, _separator, value in rows}
        if (
            fields.get("path") != command.split(" ", 1)[0]
            or fields.get("argv[]") != command
            or fields.get("ignore_errors") != "no"
        ):
            raise ValueError("v2_loaded_command_binding")


def _read(path, *, native=True):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("v2_native_bootstrap_path")
    for parent in reversed(path.parents):
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or native
            and (info.st_uid != 0 or info.st_mode & 0o022)
        ):
            raise ValueError("v2_native_bootstrap_directory")
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or native
        and before.st_uid != 0
        or before.st_nlink != 1
        or native
        and before.st_mode & 0o7022
        or getattr(before, "st_file_attributes", 0) & 0x400
        or not 0 <= before.st_size <= 4 * 1024**2
    ):
        raise ValueError("v2_native_bootstrap_file")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        opened = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(4 * 1024**2 + 1)
        after, final = os.fstat(descriptor), path.lstat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(raw) != before.st_size or any(
            getattr(before, key) != getattr(opened, key)
            or getattr(before, key) != getattr(after, key)
            or getattr(before, key) != getattr(final, key)
            for key in fields
        ):
            raise ValueError("v2_native_bootstrap_changed")
        return raw
    finally:
        os.close(descriptor)


def load_native(*, read=None, source_mode=None):
    source_mode = __package__ == "scripts" if source_mode is None else source_mode
    path = Path(__file__).with_name("j1_manager_successor_native.py") if source_mode else BASE
    reader = read or (lambda location: _read(location, native=not source_mode))
    raw = reader(path)
    if hashlib.sha256(raw).hexdigest() != BASE_SHA256:
        raise ValueError("v2_base_native_changed")
    module = types.ModuleType("_sealed_manager_successor_native_v2_base")
    module.__file__ = str(INSTALL / "native.py")
    exec(compile(raw, str(path), "exec"), module.__dict__)
    module.INSTALL, module.STATE = INSTALL, STATE
    if reader(path) != raw:
        raise ValueError("v2_base_native_changed")
    return module


class NativeIO:
    def __new__(cls, deadline):
        base = load_native()

        class PermanentDeniedIO(base.NativeIO):
            def verify_units(self, profile, **kwargs):
                before = self._systemd()
                super().verify_units(profile, **kwargs)
                fields = self._systemd()
                base.require(before == fields, "v2_loaded_unit_changed_during_check")
                prefix = f"/usr/bin/python3 -I -S -B {INSTALL}/guard.py"
                for key, operation in (("ExecCondition", "check"), ("ExecStart", "launch")):
                    validate_command_records(
                        fields[key],
                        [
                            f"{prefix} {operation} --binding-sha256 {kwargs['binding_sha256']} --unit {base.UNIT}"
                        ],
                    )

            def verify_preserved_authority(self, policy, forward):
                augmented = copy.deepcopy(policy)
                profile = augmented["bindings"][sorted(augmented["bindings"])[0]]
                for unit in base.DENIED_UNITS:
                    path = str(base.SYSTEMD / (unit + ".d") / DENY_NAME)
                    base.require(self.read(path) == DENY_BYTES, "v2_permanent_deny_changed")
                    reference = {"path": path, "sha256": base.sha(DENY_BYTES)}
                    base.require(
                        not any(row["path"] == path for row in profile["unit_files"]),
                        "v2_deny_not_legacy_inventory",
                    )
                    profile["unit_files"].append(reference)
                return super().verify_preserved_authority(augmented, forward)

        return PermanentDeniedIO(deadline)
