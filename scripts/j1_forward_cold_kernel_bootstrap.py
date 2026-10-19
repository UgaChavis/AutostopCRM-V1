"""Stdlib-only fixed installation loader; no application or native effects."""

from __future__ import annotations

import hashlib
import importlib.abc
import importlib.util
import json
import os
import re
import socket
import stat
import sys
import types
import uuid
from pathlib import Path

INSTALL = Path("/usr/local/libexec/autostop-crm-forward-cold/v1")
REQUESTS = Path("/var/lib/autostop-crm-forward/cold-requests")
HOST = "vps26457.mnogoweb.in"
ALIASES = {
    "j1_forward_cold_authority": "authority.py",
    "j1_forward_cold_native": "native.py",
    "j1_forward_cold_guard": "guard.py",
}


def protected(path, *, limit=2 * 1024**2):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("kernel_bootstrap_path")
    for parent in [*reversed(path.parent.parents), path.parent]:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("kernel_bootstrap_directory")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_mode & 0o022
            or before.st_nlink != 1
            or before.st_size > limit
        ):
            raise ValueError("kernel_bootstrap_file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        fields = (
            "st_dev",
            "st_ino",
            "st_size",
            "st_ctime_ns",
            "st_mtime_ns",
            "st_mode",
            "st_uid",
            "st_gid",
        )
        if len(raw) != before.st_size or any(
            getattr(before, key) != getattr(os.fstat(descriptor), key)
            or getattr(before, key) != getattr(path.lstat(), key)
            for key in fields
        ):
            raise ValueError("kernel_bootstrap_changed")
        return raw
    finally:
        os.close(descriptor)


def _unique(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("kernel_bootstrap_duplicate_key")
        value[name] = item
    return value


class SourceLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Only exact qualified-request modules in the fixed immutable installation."""

    def __init__(self, consumers):
        self.consumers = consumers

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith("scripts."):
            return None
        name = fullname.removeprefix("scripts.")
        if not re.fullmatch(r"j1_(?:forward|manager)_[a-z0-9_]+", name):
            raise ImportError("kernel_bootstrap_module_denied")
        key = "scripts/" + name + ".py"
        if key not in self.consumers:
            raise ImportError("kernel_bootstrap_module_unqualified")
        return importlib.util.spec_from_loader(
            fullname, self, origin=str(INSTALL / ALIASES.get(name, name + ".py"))
        )

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        name = module.__name__.removeprefix("scripts.")
        path = INSTALL / ALIASES.get(name, name + ".py")
        raw = protected(path)
        if hashlib.sha256(raw).hexdigest() != self.consumers["scripts/" + name + ".py"]:
            raise ImportError("kernel_bootstrap_source_changed")
        module.__file__ = str(path)
        exec(compile(raw, str(path), "exec"), module.__dict__)


def install(here, argv):
    """Bootstrap fixed production main; ordinary imports perform no I/O."""
    if (
        not sys.flags.isolated
        or not sys.flags.no_site
        or not sys.dont_write_bytecode
        or os.geteuid() != 0
        or os.name != "posix"
        or socket.gethostname() != HOST
        or Path(here) != INSTALL / "j1_forward_cold_kernel_supervisor.py"
        or len(argv) != 2
        or argv[0] != "--attempt"
    ):
        raise ValueError("kernel_bootstrap_boundary")
    attempt = argv[1]
    if str(uuid.UUID(attempt)) != attempt:
        raise ValueError("kernel_bootstrap_attempt")
    request = json.loads(protected(REQUESTS / attempt / "request.json"), object_pairs_hook=_unique)
    if (
        request.get("schema") != "autostop.j1.cold-controller-request.v1"
        or request.get("scope") != "bounded_cold_prepare"
        or request.get("attempt_id") != attempt
        or request.get("host") != HOST
        or request.get("old_writer_allowed") is not False
    ):
        raise ValueError("kernel_bootstrap_request")
    consumers = request["source"]["consumers"]
    if not isinstance(consumers, dict) or not 8 <= len(consumers) <= 80:
        raise ValueError("kernel_bootstrap_consumers")
    for name in ("j1_forward_cold_kernel_supervisor", "j1_forward_cold_kernel_bootstrap"):
        raw = protected(INSTALL / (name + ".py"))
        if hashlib.sha256(raw).hexdigest() != consumers.get("scripts/" + name + ".py"):
            raise ValueError("kernel_bootstrap_entrypoint_changed")
    package = types.ModuleType("scripts")
    package.__path__ = []
    sys.modules["scripts"] = package
    sys.modules["scripts.j1_forward_cold_kernel_supervisor"] = sys.modules["__main__"]
    package.j1_forward_cold_kernel_supervisor = sys.modules["__main__"]
    sys.meta_path.insert(0, SourceLoader(consumers))
    return request
