"""Publish a bounded admission cut without exposing the private control graph.

Only the controller publishes this cut under the actual epoch EX lock. It is
not a worker's fresh probe of private HEAD. ColdStore closes the previous epoch
under that same lock before every subsequent pending/history/HEAD mutation.
"""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager
from pathlib import Path

from scripts import j1_forward_cold_authority as contract
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_cold_state as cold_state
from scripts import j1_forward_io as io

SCHEMA = "autostop.j1.cold-dispatch-admission.v1"
NAMES = ("plan", "commit", "head", "epoch")
MAX_DOCUMENT_BYTES = 2 * 1024**2


def _fence(epoch, reader):
    references = epoch["runtime_bindings"]
    contract.need(set(references) == epochs.CONSUMERS, "projection_seven_bindings")
    fences = []
    for role, reference in references.items():
        manifest = io.document(reader(reference))
        contract.need(
            manifest["role"] == role and all(manifest[key] == epoch[key] for key in epochs.COMMON),
            "projection_manifest_context",
        )
        fences.append(manifest["ingress_fence_ref"])
    contract.need(all(value == fences[0] for value in fences), "projection_fence_split")
    fence = io.document(reader(fences[0]))
    contract.need(
        fence["schema"] == "autostop.j1.cold-dispatch-fence.v1"
        and all(fence[key] == epoch[key] for key in epochs.COMMON),
        "projection_fence_context",
    )
    root = Path(fence["state_path"]).parent
    contract.need(
        fence["dispatch_lock_path"] == str(root / "dispatch.lock"), "projection_fence_paths"
    )
    expected = (
        Path(f"/var/lib/autostop-manager-cold/{epoch['epoch_id']}/dispatch")
        if epoch["scope"] == "production_native"
        else Path(epoch["journal_root"]).parent / "dispatch"
    )
    contract.need(root == expected, "projection_dispatch_root")
    return fence


def _exclusive(fence, descriptor):
    contract.need(type(descriptor) is int and descriptor >= 0, "projection_descriptor")
    info = os.fstat(descriptor)
    linked = Path(fence["dispatch_lock_path"]).lstat()
    expected = fence["dispatch_lock_identity"]
    contract.need(
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == linked.st_nlink == 1
        and (info.st_dev, info.st_ino, info.st_ctime_ns)
        == (linked.st_dev, linked.st_ino, linked.st_ctime_ns)
        == (expected["device"], expected["inode"], expected["ctime_ns"]),
        "projection_lock_replaced",
    )
    native.held_descriptor(native.controller(os.getpid()), descriptor, [info.st_dev, info.st_ino])


def _bytes(reader, reference):
    contract.reference(reference)
    raw = reader(reference)
    contract.need(
        type(raw) is bytes
        and len(raw) <= MAX_DOCUMENT_BYTES
        and io.sha(raw) == reference["sha256"]
        and io.canonical(io.document(raw)) == raw,
        "projection_source_bytes",
    )
    return raw


def _public_new(path, raw):
    if path.exists() or path.is_symlink():
        contract.need(io.read_protected(path) == raw, "projection_immutable_conflict")
    else:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
            os.fchmod(descriptor, 0o644)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        io.fsync_directory(path.parent)
    info = path.lstat()
    contract.need(stat.S_IMODE(info.st_mode) == 0o644, "projection_readable_file")
    return io.reference(path)


def publish(plan_ref, commit_ref, epoch_ref, *, store, provider, dispatch_fd, _observation=None):
    """Copy exact committed bytes; publishing a cut alone never opens dispatch."""
    plan_raw = _bytes(store._read_reference, plan_ref)
    plan = io.document(plan_raw)
    cold_state.validate_plan(plan, store.base_sha256)
    epoch_raw = _bytes(provider.read_reference, epoch_ref)
    epoch = io.document(epoch_raw)
    contract.need(plan["dispatch_epoch_ref"] == epoch_ref, "projection_plan_epoch")
    fence = _fence(epoch, provider.read_reference)
    _exclusive(fence, dispatch_fd)
    with native.borrow_dispatch(provider, dispatch_fd):
        if _observation is None:
            verified = native.verify_package(
                plan["cold_authority_package_ref"], provider=provider, required_scope=provider.scope
            )
        else:
            contract.need(
                type(_observation) is native.PackageRequalification,
                "projection_requalification_type",
            )
            verified = _observation.observe(plan["cold_authority_package_ref"], provider)
        epochs.verify(epoch, verified["package"], provider)
    commit_raw = _bytes(store._read_reference, commit_ref)
    commit = io.document(commit_raw)
    head = store.head()
    head_ref = store._reference(store.head_path)
    head_raw = _bytes(store._read_reference, head_ref)
    contract.need(
        plan["attempt_id"] == epoch["attempt_id"]
        and epoch["package_ref"] == plan["cold_authority_package_ref"]
        and commit["plan"] == plan
        and head["record"] == commit
        and head["sha256"] == commit_ref["sha256"]
        and commit_ref["path"] == str(store.history / f"{head['sequence']:020d}.json")
        and io.document(head_raw)
        == {
            "schema": "autostop.j1.crm-forward-head.v1",
            "sequence": head["sequence"],
            "sha256": commit_ref["sha256"],
        },
        "projection_actual_head_chain",
    )
    root = Path(fence["state_path"]).parent / "proofs"
    if not root.exists():
        root.mkdir(mode=0o755)
        root.chmod(0o755)
        io.fsync_directory(root.parent)
    io.protected_directory(root)
    contract.need(stat.S_IMODE(root.stat().st_mode) == 0o755, "projection_readable_directory")
    source_refs = {"plan": plan_ref, "commit": commit_ref, "head": head_ref, "epoch": epoch_ref}
    documents = dict(zip(NAMES, (plan_raw, commit_raw, head_raw, epoch_raw), strict=True))
    projected_refs = {name: _public_new(root / (name + ".json"), documents[name]) for name in NAMES}
    # The same EX is retained through caller's OPEN publication. A second read
    # catches a caller which bypassed the required State mutation boundary.
    contract.need(store.head() == head, "projection_head_changed")
    _exclusive(fence, dispatch_fd)
    return _public_new(
        root / "admission.json",
        io.canonical(
            {
                "schema": SCHEMA,
                **{key: epoch[key] for key in epochs.COMMON},
                "source_refs": source_refs,
                "projected_refs": projected_refs,
            }
        ),
    )


def _closed(fence, epoch):
    path = Path(fence["state_path"])
    current = io.document(io.read_protected(path))
    expected = {
        "schema": "autostop.j1.cold-dispatch-state.v1",
        **{key: epoch[key] for key in epochs.COMMON},
    }
    contract.need(
        all(current.get(key) == value for key, value in expected.items())
        and set(current) == {*expected, "state", "plan_ref", "commit_ref", "admission_ref"}
        and current["state"] in {"open", "closed"},
        "projection_previous_dispatch_state",
    )
    closed = {
        **expected,
        "state": "closed",
        "plan_ref": None,
        "commit_ref": None,
        "admission_ref": None,
    }
    if current != closed:
        io.atomic_replace(path, closed)
        path.chmod(0o644)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        io.fsync_directory(path.parent)
    contract.need(io.document(io.read_protected(path)) == closed, "projection_closed_readback")


@contextmanager
def close_before_mutation(store):
    """Keep previous epoch CLOSED through the caller's complete State mutation."""
    snapshot, plans = _mutation_snapshot(store)
    if not plans:
        yield
        return
    bindings = {}
    for plan in plans:
        epoch = io.document(store._read_reference(plan["dispatch_epoch_ref"]))
        fence = _fence(epoch, store._read_reference)
        key = fence["dispatch_lock_path"]
        contract.need(
            key not in bindings or bindings[key] == (epoch, fence), "projection_epoch_alias"
        )
        bindings[key] = (epoch, fence)
    acquired = []
    try:
        for key in sorted(bindings):
            epoch, fence = bindings[key]
            descriptor = epochs.locked_fence(fence, exclusive=True)
            acquired.append((descriptor, fence))
            _exclusive(fence, descriptor)
        contract.need(
            _mutation_snapshot(store)[0] == snapshot, "projection_head_raced_before_close"
        )
        for epoch, fence in bindings.values():
            _closed(fence, epoch)
        yield
        for descriptor, fence in acquired:
            _exclusive(fence, descriptor)
    finally:
        for descriptor, _fence_value in reversed(acquired):
            os.close(descriptor)


def _mutation_snapshot(store):
    """Recognize only the existing crash case: one durable tip before HEAD.

    A regular head() deliberately rejects that state. Recovery needs to close
    both the previously published epoch and that unpublished tip, then let the
    existing strict Store.recover validate/complete its original publication.
    Corrupt HEAD, broken history and larger gaps still stop before mutation.
    """
    with store._locked() as present:
        if not present:
            return {"head": None, "records": []}, []
        records, head = store._records(), store._head_record()
        sequence = head["sequence"]
        contract.need(sequence <= len(records) <= sequence + 1, "projection_recovery_history_gap")
        expected = records[sequence - 1][1]["sha256"] if sequence else store.base_sha256
        contract.need(head["sha256"] == expected, "projection_recovery_head_corrupt")
        selected = [records[sequence - 1][0]["plan"]] if sequence else []
        if len(records) > sequence:
            selected.append(records[-1][0]["plan"])
        return {"head": head, "records": [reference for _record, reference in records]}, [
            plan for plan in selected if cold_state.is_cold(plan)
        ]
