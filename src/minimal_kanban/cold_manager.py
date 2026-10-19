"""Resolve the separately accepted embedded Manager before application imports."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import stat
import sys
from pathlib import Path

BINDING_ENV = "AUTOSTOP_COLD_RUNTIME_BINDING"
HASH_ENV = "AUTOSTOP_COLD_RUNTIME_BINDING_SHA256"
SOURCE_PARENT = Path("/opt/autostop-manager-cold-releases")


class ColdManagerUnavailableError(RuntimeError):
    """A cold candidate cannot fall back to the installed legacy Manager."""


def _require(condition: object) -> None:
    if not condition:
        raise ColdManagerUnavailableError("Cold embedded Manager binding is unavailable.")


def _protected(path: Path, *, directory: bool) -> None:
    _require(path.is_absolute() and ".." not in path.parts)
    for current in [*reversed(path.parents), path]:
        info = current.lstat()
        expected_directory = directory or current != path
        _require(
            (stat.S_ISDIR(info.st_mode) if expected_directory else stat.S_ISREG(info.st_mode))
            and not info.st_mode & 0o022
            and info.st_uid == 0
            and not getattr(info, "st_file_attributes", 0) & 0x400
            and (expected_directory or info.st_nlink == 1)
        )


def _source_path() -> Path:
    configured = os.environ.get("AUTOSTOP_MANAGER_PATH", "")
    source = Path(configured)
    _require(
        source.parent.parent == SOURCE_PARENT
        and source.name == "source"
        and re.fullmatch(r"[0-9a-f]{40}", source.parent.name)
        and str(source) == configured
    )
    _protected(source, directory=True)
    revision = source / "REVISION"
    _protected(revision, directory=False)
    _require(revision.read_bytes() == (source.parent.name + "\n").encode())
    return source


def prepare_cold_manager() -> bool:
    """Validate explicit cold mode; absence alone retains the legacy resolver."""
    manifest = os.environ.get(BINDING_ENV)
    digest = os.environ.get(HASH_ENV)
    if manifest is None and digest is None:
        return False
    try:
        _require(manifest and digest and re.fullmatch(r"[0-9a-f]{64}", digest))
        source = _source_path()
        path = Path(manifest)
        _protected(path, directory=False)
        _require(path.stat().st_size <= 64 * 1024)
        raw = path.read_bytes()
        _require(hashlib.sha256(raw).hexdigest() == digest)
        document = json.loads(raw)
        _require(
            document["role"] == "crm-embedded"
            and document["paths"]["source_root"] == str(source)
            and document["source_revision"] == source.parent.name
        )
        for name, module in tuple(sys.modules.items()):
            if name == "autostop_manager" or name.startswith("autostop_manager."):
                location = getattr(module, "__file__", None)
                _require(location and Path(location).resolve().is_relative_to(source))
        if str(source) in sys.path:
            sys.path.remove(str(source))
        sys.path.insert(0, str(source))
        runtime = importlib.import_module("autostop_manager.cold_runtime")
        _require(Path(runtime.__file__).resolve().is_relative_to(source))
        current = runtime.binding()
        _require(current is not None)
        runtime.require_role("crm-embedded")
        _require(current.document == document)
        return True
    except ColdManagerUnavailableError:
        raise
    except Exception:
        raise ColdManagerUnavailableError("Cold embedded Manager binding is unavailable.") from None
