"""Fresh cold dispatch configuration and permanent old-operation tombstones.

Compilation creates configuration bytes, never a start or dispatch permission.
The native controller alone publishes OPEN after exact durable COMMIT/HEAD.
"""

from __future__ import annotations

import os
from pathlib import Path

from scripts import j1_forward_cold_authority as contract
from scripts import j1_forward_io as io

SCHEMA = "autostop.j1.cold-dispatch-epoch.v1"
ROLES = {
    "autostop-manager-mcp.service": "manager-mcp",
    "autostop-j1.service": "j1",
    "autostop-manager-scheduler.service": "scheduler",
    "autostop-work-telegram.service": "work-telegram",
    "autostop-telegram.service": "personal-telegram",
    "autostop-codex-wake.service": "wake",
}
CONSUMERS = frozenset((*ROLES.values(), "crm-embedded"))
COMMON = ("attempt_id", "boot_id", "generation", "epoch_id")
BINDING_ENV = "AUTOSTOP_COLD_RUNTIME_BINDING"
SHA_ENV = "AUTOSTOP_COLD_RUNTIME_BINDING_SHA256"


def common(binding):
    return {**{name: binding[name] for name in COMMON[:-1]}, "epoch_id": binding["generation"]}


def expected_paths(epoch, revision, role):
    """Matches the independent Manager cold-runtime configuration grammar."""
    contract.need(role in CONSUMERS, "epoch_role")
    etc = f"/etc/autostop-manager-cold/{epoch}/{role}"
    state = f"/var/lib/autostop-manager-cold/{epoch}/{role}"
    run = f"/run/autostop-manager-cold/{epoch}/{role}"
    source = f"/opt/autostop-manager-cold-releases/{revision}/source"
    result = {"source_root": source, "env_file": etc + "/runtime.env"}
    if role in {"manager-mcp", "crm-embedded"}:
        result.update(manager_db=state + "/autostop_manager.sqlite3")
    elif role == "j1":
        result.update(cache_dir=state + "/cache", vin_cache_dir=run + "/vin")
    elif role == "scheduler":
        result.update(registry_db=state + "/registry.sqlite3", socket_path=run + "/control.sock")
    elif role in {"work-telegram", "personal-telegram"}:
        result.update(
            credentials_path=etc + "/credentials",
            session_path=state + "/account",
            state_dir=state,
            socket_path=run + "/bridge.sock",
            model_dir=state + "/models/faster-whisper-small",
        )
        if role == "work-telegram":
            result["owner_config_path"] = etc + "/owner.json"
    else:
        result.update(
            config_path=etc + "/wake.json",
            socket_path=run + "/wake.sock",
            app_socket=run + "/app-server.sock",
            instruction_root=source,
        )
    return result


def tombstones(binding, pending, origin_attempt_id):
    rows = pending["requests"]
    contract.need(type(rows) is list and len(rows) <= 8192, "epoch_tombstone_limit")
    mapping = {
        "applied_readback": "applied",
        "not_applied_terminal": "known_not_applied",
        "unknown": "unknown",
    }
    result, ids = [], set()
    for row in rows:
        contract.need(row["op_id"] not in ids and row["retry"] == "forbidden", "epoch_old_identity")
        ids.add(row["op_id"])
        result.append(
            {
                "op_id": row["op_id"],
                "payload_sha256": row["payload_sha256"],
                "outcome": mapping[row["outcome"]],
                "retry": "forbidden",
            }
        )
    return {
        "schema": "autostop.j1.cold-dispatch-tombstones.v1",
        **common(binding),
        "origin_attempt_id": origin_attempt_id,
        "rows": sorted(result, key=lambda row: row["op_id"]),
    }


def closed_state(binding):
    return {
        "schema": "autostop.j1.cold-dispatch-state.v1",
        **common(binding),
        "state": "closed",
        "commit_ref": None,
        "plan_ref": None,
    }


def runtime_binding(binding, role, paths, tombstone_ref, fence_ref):
    return {
        "schema": "autostop.manager.cold-runtime-binding.v1",
        **common(binding),
        "source_revision": binding["candidate_manager_sha"],
        "role": role,
        "paths": paths,
        "tombstones_ref": tombstone_ref,
        "ingress_fence_ref": fence_ref,
    }


def fixture_inputs(fixture, pending_ref):
    """Private producer seam; never used by the production observer or CLI."""
    root, binding = fixture.root, fixture.binding
    pending = io.document(fixture.provider.read_reference(pending_ref))
    anchor = io.document(fixture.provider.read_reference(fixture._cold_predecessor))
    base = "execution"
    fixture._mkdir(base + "/dispatch")
    fixture.publish(base + "/dispatch/dispatch.lock", b"", raw=True)
    info = (root / base / "dispatch/dispatch.lock").stat()
    tombstone_ref = fixture.publish(
        base + "/dispatch/tombstones.json",
        tombstones(binding, pending, anchor["origin_attempt_id"]),
    )
    fixture.publish(base + "/dispatch/state.json", closed_state(binding))
    fence_ref = fixture.publish(
        base + "/dispatch/fence.json",
        {
            "schema": "autostop.j1.cold-dispatch-fence.v1",
            **common(binding),
            "state_path": str(root / base / "dispatch/state.json"),
            "dispatch_lock_path": str(root / base / "dispatch/dispatch.lock"),
            "dispatch_lock_identity": {
                "device": info.st_dev,
                "inode": info.st_ino,
                "ctime_ns": info.st_ctime_ns,
            },
        },
    )
    bindings = {}
    for role in sorted(CONSUMERS):
        folder = base + "/roles/" + role
        fixture._mkdir(folder)
        fixture._mkdir(folder + "/state")
        fixture._mkdir(folder + "/run")
        paths = {
            "state_dir": str(root / folder / "state"),
            "socket_path": str(root / folder / "run/control.sock"),
            "source_root": str(root / "source"),
        }
        bindings[role] = fixture.publish(
            folder + "/binding.json",
            runtime_binding(binding, role, paths, tombstone_ref, fence_ref),
        )
    fixture.runtime_bindings = bindings


def read_epoch(reference, provider):
    epoch = io.document(provider.read_reference(reference))
    contract.fields(
        epoch,
        {"schema", "scope", *COMMON, "package_ref", "runtime_bindings", "slots", "journal_root"},
        "epoch_fields",
    )
    contract.need(epoch["schema"] == SCHEMA and epoch["scope"] == provider.scope, "epoch_scope")
    contract.need(
        set(epoch["runtime_bindings"]) == CONSUMERS and set(epoch["slots"]) == set(ROLES),
        "epoch_six_roles",
    )
    return epoch


def verify(epoch, package, provider, *, measure_lock=True):
    binding = package["binding"]
    contract.need(
        all(epoch[key] == value for key, value in common(binding).items()), "epoch_binding"
    )
    expected_rows = tombstones(
        binding,
        io.document(provider.read_reference(package["pending_ref"])),
        io.document(provider.read_reference(package["predecessor_ref"]))["origin_attempt_id"],
    )
    observed_fence = None
    for role in sorted(CONSUMERS):
        reference = epoch["runtime_bindings"][role]
        document = io.document(provider.read_reference(reference))
        contract.fields(
            document,
            {
                "schema",
                *COMMON,
                "source_revision",
                "role",
                "paths",
                "tombstones_ref",
                "ingress_fence_ref",
            },
            "runtime_binding_fields",
        )
        contract.need(
            document["schema"] == "autostop.manager.cold-runtime-binding.v1"
            and document["role"] == role
            and document["source_revision"] == binding["candidate_manager_sha"]
            and all(document[key] == epoch[key] for key in COMMON),
            "runtime_binding_context",
        )
        if provider.scope == "production_native":
            contract.need(
                document["paths"]
                == expected_paths(epoch["epoch_id"], binding["candidate_manager_sha"], role),
                "runtime_binding_paths",
            )
            contract.need(
                reference["path"]
                == f"/etc/autostop-manager-cold/{epoch['epoch_id']}/{role}/binding.json",
                "runtime_binding_path",
            )
        else:
            private = provider.root / "execution/roles" / role
            contract.need(
                document["paths"]
                == {
                    "state_dir": str(private / "state"),
                    "socket_path": str(private / "run/control.sock"),
                    "source_root": str(provider.root / "source"),
                },
                "fixture_runtime_paths",
            )
        rows = io.document(provider.read_reference(document["tombstones_ref"]))
        contract.need(rows == expected_rows, "epoch_tombstones_incomplete")
        fence = io.document(provider.read_reference(document["ingress_fence_ref"]))
        contract.need(observed_fence is None or observed_fence == fence, "epoch_fence_split")
        contract.fields(
            fence,
            {"schema", *COMMON, "state_path", "dispatch_lock_path", "dispatch_lock_identity"},
            "epoch_fence_fields",
        )
        contract.need(
            fence["schema"] == "autostop.j1.cold-dispatch-fence.v1"
            and all(fence[key] == epoch[key] for key in COMMON),
            "epoch_fence_binding",
        )
        state_root = (
            Path(f"/var/lib/autostop-manager-cold/{epoch['epoch_id']}/dispatch")
            if provider.scope == "production_native"
            else provider.root / "execution/dispatch"
        )
        contract.need(
            fence["state_path"] == str(state_root / "state.json")
            and fence["dispatch_lock_path"] == str(state_root / "dispatch.lock"),
            "epoch_fence_paths",
        )
        identity = fence["dispatch_lock_identity"]
        contract.fields(identity, {"device", "inode", "ctime_ns"}, "epoch_lock_identity")
        contract.need(
            all(
                type(identity[key]) is int and identity[key] >= (key != "device")
                for key in identity
            ),
            "epoch_lock_identity",
        )
        if measure_lock:
            info = Path(fence["dispatch_lock_path"]).lstat()
            contract.need(
                identity
                == {"device": info.st_dev, "inode": info.st_ino, "ctime_ns": info.st_ctime_ns}
                and info.st_nlink == 1
                and not info.st_mode & 0o022,
                "epoch_dispatch_lock_changed",
            )
        observed_fence = fence
    return observed_fence


def locked_fence(fence, *, exclusive):
    """Existing descriptor only; caller closes it after the protected effect."""
    import fcntl

    path = Path(fence["dispatch_lock_path"])
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        expected = fence["dispatch_lock_identity"]
        contract.need(
            (info.st_dev, info.st_ino, info.st_ctime_ns)
            == (expected["device"], expected["inode"], expected["ctime_ns"]),
            "epoch_dispatch_lock_changed",
        )
        fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        linked = path.lstat()
        contract.need(
            (linked.st_dev, linked.st_ino, linked.st_ctime_ns)
            == (info.st_dev, info.st_ino, info.st_ctime_ns),
            "epoch_dispatch_lock_changed",
        )
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise
