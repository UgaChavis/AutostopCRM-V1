"""Explicit cold-v3 data profile; the original fixed profile is unchanged."""

from __future__ import annotations

import hashlib
import re
import sys
import types
import uuid
from pathlib import Path

from scripts import j1_forward_backup as original
from scripts import j1_forward_cold_authority as cold

PROFILE = "crm_manager_all_profiles_cold_v3"
BASE_SHA256 = "e9691dd10f4a655ebe87b89fede6ba530f45361d0ef6602c35e22f2129f42157"
NAMESPACE = uuid.UUID("8770ab7b-a54d-41ce-9525-a9cf66494f18")


def engine(revision, generation, attempt):
    original.require(
        type(revision) is str and cold.SHA1.fullmatch(revision), "cold_backup_revision"
    )
    original.require(cold._uuid(generation) and cold._uuid(attempt), "cold_backup_epoch")
    key = revision + "_" + generation + "_" + attempt
    name = "_autostop_cold_backup_" + key
    if name in sys.modules:
        return sys.modules[name]
    path = Path(original.__file__)
    raw = path.read_bytes()
    original.require(hashlib.sha256(raw).hexdigest() == BASE_SHA256, "cold_backup_base_changed")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        exec(compile(raw, str(path), "exec"), module.__dict__)
        module.PROFILE = PROFILE
        roots = (
            ("cold_source", f"/opt/autostop-manager-cold-releases/{revision}/source"),
            ("cold_config", f"/etc/autostop-manager-cold/{generation}"),
            ("cold_state", f"/var/lib/autostop-manager-cold/{generation}"),
            ("cold_controller_code", "/usr/local/libexec/autostop-crm-forward-cold/v1"),
            ("cold_controller_state", "/var/lib/autostop-crm-forward/cold/v1"),
        )
        units = tuple(
            (
                "cold_unit_" + str(index),
                "/etc/systemd/system/autostop-crm-forward-"
                + str(uuid.uuid5(NAMESPACE, attempt + generation + unit))
                + ".service",
            )
            for index, unit in enumerate(cold.ALLOWED)
        )
        module.ROOTS = (*original.ROOTS, *roots, *units)
        return module
    except BaseException:
        sys.modules.pop(name, None)
        raise


def for_plan(plan):
    path = plan["dispatch_epoch_ref"]["path"]
    match = re.fullmatch(
        r"/var/lib/autostop-crm-forward/cold/v1/epochs/([0-9a-f-]{36})/epoch\.json", path
    )
    original.require(match is not None, "cold_backup_production_epoch_path")
    return engine(plan["candidate_manager_sha"], match[1], plan["attempt_id"])


def transport_engine(plan):
    from scripts import j1_forward_transport as transport

    backup = for_plan(plan)
    name = backup.__name__.replace("_backup_", "_transport_")
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    module.__file__ = transport.__file__
    sys.modules[name] = module
    try:
        exec(
            compile(Path(transport.__file__).read_bytes(), transport.__file__, "exec"),
            module.__dict__,
        )
        module.PROFILE_SHA256 = backup.profile_sha256()
        module._CORE = backup
        return module
    except BaseException:
        sys.modules.pop(name, None)
        raise
