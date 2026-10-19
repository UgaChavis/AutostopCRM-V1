"""Inert cold source/config staging under the original controller lock.

No selector, original unit, barrier, start grant, application import or process
is changed. The producer independently validates source/CI and native runtime
inputs; a staging receipt is not an execution permission.
"""

from __future__ import annotations

import os
import re
import stat
import time
from pathlib import Path, PurePosixPath

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_guard as guard
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_io as io

MAX_SOURCE_BYTES = 256 * 1024**2
MAX_FILE_BYTES = 64 * 1024**2
SOURCE = Path(__file__).parent


def policy_argv(binding, role, interpreter_ref):
    cold.need(role in epochs.ROLES.values(), "install_role")
    cold.reference(interpreter_ref)
    executable = interpreter_ref["path"]
    root = "/opt/autostop-manager-cold-releases/" + binding["candidate_manager_sha"]
    cold.need(
        executable.startswith(root + "/venv/bin/") and " " not in executable,
        "install_physical_candidate_interpreter",
    )
    return [executable, "-I", "-B", root + "/source/scripts/cold-role-start.py", "--role", role]


def _relative(name):
    cold.need(
        type(name) is str
        and name
        and name == str(PurePosixPath(name))
        and not name.startswith("/")
        and not {".", "..", ".git", "__pycache__"}.intersection(name.split("/"))
        and not name.endswith((".pyc", ".pyo")),
        "install_relative_source",
    )
    return name


class NativeBackend:
    scope = "production_native"

    def hold(self, descriptor, binding):
        cold.need(os.geteuid() == 0, "install_root_required")
        native.held_descriptor(
            native.controller(os.getpid()), descriptor, binding["deployment_lock_identity"]
        )
        actual = native.DEPLOY_LOCK.lstat()
        cold.need(
            [actual.st_dev, actual.st_ino] == binding["deployment_lock_identity"],
            "install_deployment_lock_changed",
        )

    def read(self, reference):
        return io.referenced(reference)

    def directory(self, path, *, mode=0o755, uid=0, gid=0, empty=False):
        path = Path(path)
        if not path.exists() and not path.is_symlink():
            self.directory(path.parent)
            path.mkdir(mode=mode)
            path.chown(uid, gid)
            path.chmod(mode)
            io.fsync_directory(path.parent)
        info = path.lstat()
        cold.need(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == uid
            and info.st_gid == gid
            and stat.S_IMODE(info.st_mode) == mode,
            "install_directory_conflict",
        )
        if empty:
            cold.need(not any(path.iterdir()), "install_fresh_state_not_empty")

    def publish(self, path, raw, *, mode, gid=0):
        path = Path(path)
        self.directory(path.parent)
        if path.exists() or path.is_symlink():
            info = path.lstat()
            cold.need(
                io.read_protected(path, MAX_FILE_BYTES) == raw
                and stat.S_IMODE(info.st_mode) == mode
                and info.st_gid == gid,
                "install_immutable_conflict",
            )
            return io.reference(path)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        try:
            os.fchown(descriptor, 0, gid)
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        io.fsync_directory(path.parent)
        return io.reference(path)


def inert_stage(
    binding,
    *,
    manager_files,
    controller_files,
    runtime_bindings,
    runtime_env,
    credentials,
    deployment_fd,
    backend=None,
    controller_source=None,
):
    """Stage exact immutable bytes and genuinely empty per-role state/run roots.

    Calling fixtures may substitute filesystem custody only. Production's
    controller calls the fixed backend; there is no CLI/backend environment
    switch. No authority, fence or mutable queue state is generated here.
    """
    backend = NativeBackend() if backend is None else backend
    cold.need(
        backend.scope in cold.SCOPES
        and (backend.scope != "production_native" or type(backend) is NativeBackend),
        "install_backend_scope",
    )
    cold._binding(binding, now_ns=time.monotonic_ns())
    backend.hold(deployment_fd, binding)
    cold.need(
        type(manager_files) is dict and 1 <= len(manager_files) <= 10000,
        "install_manager_source_inventory",
    )
    for mapping in (runtime_bindings, runtime_env, credentials):
        cold.fields(mapping, epochs.CONSUMERS, "install_seven_consumers")
    expected_controller = {*guard.BOOTSTRAP_HASHES, "j1_forward_cold_guard"}
    if backend.scope == "production_native" or controller_source is not None:
        cold.fields(
            controller_source,
            {"revision", "tree", "consumers"},
            "install_qualified_controller_source",
        )
        cold.need(
            controller_source["revision"] == binding["candidate_crm_sha"]
            and type(controller_source["consumers"]) is dict,
            "install_qualified_controller_revision",
        )
        expected_controller = set()
        for path, digest in controller_source["consumers"].items():
            cold.need(
                type(path) is str
                and re.fullmatch(r"scripts/j1_(?:forward|manager)_[a-z0-9_]+\.py", path)
                and type(digest) is str
                and cold.HEX.fullmatch(digest),
                "install_qualified_controller_member",
            )
            expected_controller.add(PurePosixPath(path).stem)
        cold.need(
            {
                *guard.BOOTSTRAP_HASHES,
                "j1_forward_cold_guard",
                "j1_forward_cold_kernel_supervisor",
                "j1_forward_cold_kernel_bootstrap",
            }
            <= expected_controller
            and len(expected_controller) <= 80,
            "install_controller_closure_required",
        )
    cold.fields(controller_files, expected_controller, "install_controller_inventory")
    manager_raw, controller_raw, env_raw = {}, {}, {}
    total = 0
    for name, reference in manager_files.items():
        _relative(name)
        cold.reference(reference)
        raw = backend.read(reference)
        cold.need(
            type(raw) is bytes
            and io.sha(raw) == reference["sha256"]
            and len(raw) <= MAX_FILE_BYTES,
            "install_manager_source_bytes",
        )
        total += len(raw)
        cold.need(total <= MAX_SOURCE_BYTES, "install_source_budget")
        manager_raw[name] = raw
    cold.need(
        manager_raw.get("REVISION") == (binding["candidate_manager_sha"] + "\n").encode()
        and {"scripts/cold-role-start.py", "autostop_manager/cold_runtime.py"} <= set(manager_raw),
        "install_manager_bootstrap_required",
    )
    for name, reference in controller_files.items():
        cold.reference(reference)
        raw = backend.read(reference)
        expected = (SOURCE / (name + ".py")).read_bytes()
        cold.need(
            raw == expected
            and io.sha(raw) == reference["sha256"]
            and (
                controller_source is None
                or reference["sha256"] == controller_source["consumers"]["scripts/" + name + ".py"]
            ),
            "install_controller_source_changed",
        )
        controller_raw[name] = raw
    for role, document in runtime_bindings.items():
        cold.fields(
            document,
            {
                "schema",
                *epochs.COMMON,
                "source_revision",
                "role",
                "paths",
                "tombstones_ref",
                "ingress_fence_ref",
            },
            "install_runtime_binding_fields",
        )
        cold.need(
            document["schema"] == "autostop.manager.cold-runtime-binding.v1"
            and all(document[key] == value for key, value in epochs.common(binding).items())
            and document["source_revision"] == binding["candidate_manager_sha"]
            and document["role"] == role
            and document["paths"]
            == epochs.expected_paths(binding["generation"], binding["candidate_manager_sha"], role),
            "install_runtime_binding",
        )
        for key in ("tombstones_ref", "ingress_fence_ref"):
            cold.reference(document[key])
            backend.read(document[key])
        cold.fields(credentials[role], {"uid", "gid", "groups"}, "install_credentials")
        cold.need(
            all(
                type(credentials[role][key]) is int and credentials[role][key] >= 0
                for key in ("uid", "gid")
            )
            and type(credentials[role]["groups"]) is list
            and all(type(item) is int and item >= 0 for item in credentials[role]["groups"]),
            "install_credentials_values",
        )
        cold.reference(runtime_env[role])
        raw = backend.read(runtime_env[role])
        cold.need(
            type(raw) is bytes
            and io.sha(raw) == runtime_env[role]["sha256"]
            and len(raw) <= 1024**2
            and b"\x00" not in raw,
            "install_runtime_environment",
        )
        env_raw[role] = raw
    # All untrusted shape/byte checks finish before the first filesystem write.
    backend.hold(deployment_fd, binding)
    source_root = (
        Path("/opt/autostop-manager-cold-releases") / binding["candidate_manager_sha"] / "source"
    )
    source_refs = {}
    for name, raw in sorted(manager_raw.items()):
        source_refs[name] = backend.publish(source_root / name, raw, mode=0o644)
    controller_refs = {}
    for name, raw in sorted(controller_raw.items()):
        filename = {
            "j1_forward_cold_authority": "authority.py",
            "j1_forward_cold_native": "native.py",
            "j1_forward_cold_guard": "guard.py",
        }.get(name, name + ".py")
        controller_refs[name] = backend.publish(native.INSTALL / filename, raw, mode=0o644)
    bindings = {}
    for role, document in sorted(runtime_bindings.items()):
        base = Path("/etc/autostop-manager-cold") / binding["generation"] / role
        uid, gid = (credentials[role][key] for key in ("uid", "gid"))
        for prefix in ("/var/lib/autostop-manager-cold", "/run/autostop-manager-cold"):
            backend.directory(
                Path(prefix) / binding["generation"] / role,
                mode=0o700,
                uid=uid,
                gid=gid,
                empty=True,
            )
        backend.publish(base / "runtime.env", env_raw[role], mode=0o640, gid=gid)
        bindings[role] = backend.publish(base / "binding.json", io.canonical(document), mode=0o644)
    backend.hold(deployment_fd, binding)
    cold._binding(binding, now_ns=time.monotonic_ns())
    return {
        "schema": "autostop.j1.cold-inert-installation.v1",
        "scope": backend.scope,
        "binding": binding,
        "manager_files": source_refs,
        "controller_files": controller_refs,
        "runtime_bindings": bindings,
        "application_imported": False,
        "activation_authorized": False,
        "original_controls_changed": False,
    }
