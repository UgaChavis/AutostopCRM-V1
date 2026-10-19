"""Bounded control-evidence copies and independent, read-only semantic restore.

This supplements the fixed data-backup profile. It copies only the referenced
forward state/attempt/origin graph and pinned consumer bytes, never an entire
changing attempts tree. Restored evidence is inspected through normal Store
readers; archived Python is never imported or executed. No production restore,
native admission, writer closure or signature truth is supplied by this module.
"""

from __future__ import annotations

import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from scripts import j1_forward_io as io
from scripts import j1_forward_launch_permission as launch
from scripts import j1_forward_recovery as recovery
from scripts import j1_forward_recovery_state as recovery_state
from scripts import j1_forward_state as state
from scripts import j1_forward_variant as variant

SCHEMA = "autostop.j1.crm-forward-control-archive.v1"
MIXED_SCHEMA = "autostop.j1.crm-manager-mcp-forward-control-archive.v2"
COLD_V3_SCHEMA = "autostop.j1.crm-manager-cold-forward-control-archive.v3"
COLD_SCHEMA = "autostop.j1.crm-manager-cold-forward-control-archive.v4"
RESTORE_SCHEMA = "autostop.j1.crm-forward-control-restore.v1"
MIXED_RESTORE_SCHEMA = "autostop.j1.crm-manager-mcp-forward-control-restore.v2"
COLD_V3_RESTORE_SCHEMA = "autostop.j1.crm-manager-cold-forward-control-restore.v3"
COLD_RESTORE_SCHEMA = "autostop.j1.crm-manager-cold-forward-control-restore.v4"
ROOT = Path("/var/lib/autostop-crm-forward")
SOURCE = Path("/opt/autostopcrm")
MAX_FILES = 8192
MAX_FILE_BYTES = 8 * 1024**2
COLD_MAX_FILE_BYTES = 32 * 1024**2
MAX_TOTAL_BYTES = 128 * 1024**2
COLD_RUNTIME_MAX_FILE_BYTES = 384 * 1024**2
COLD_RUNTIME_MAX_TOTAL_BYTES = 512 * 1024**2
MAX_NODES = 250_000
CONSUMER = re.compile(
    r"scripts/(?:j1_forward_[a-z_]+|j1_manager_(?:transition|transition_source|successor_guard|successor_native|successor_guard_v2|successor_native_v2|successor_transition))\.py\Z"
)
CLAIMS = {
    "production_restore_authorized": False,
    "native_acceptance_granted": False,
    "writer_closure_proven": False,
    "external_dependencies_restored": False,
}
CONTROL_PREFIXES = {"state", "attempts", "origins", "launch-permits"}


def _budget(deadline):
    io.require(time.monotonic() < deadline, "control_archive_deadline")


def _cold(plan):
    from scripts import j1_forward_cold_state

    return j1_forward_cold_state.is_cold(plan)


def _schema(plan, *, restore=False, codec=4):
    if _cold(plan):
        if codec == 3:
            return COLD_V3_RESTORE_SCHEMA if restore else COLD_V3_SCHEMA
        return COLD_RESTORE_SCHEMA if restore else COLD_SCHEMA
    if variant.is_mixed(plan):
        return MIXED_RESTORE_SCHEMA if restore else MIXED_SCHEMA
    return RESTORE_SCHEMA if restore else SCHEMA


def transport_total_limit(codec=3):
    io.require(type(codec) is int and codec in {3, 4}, "control_archive_codec")
    return COLD_RUNTIME_MAX_TOTAL_BYTES if codec == 4 else MAX_TOTAL_BYTES


def _runtime_reader(reader, raw):
    from scripts import j1_forward_cold_authority

    # The typed collector already authenticated this leaf and its exact kind.
    # Final revalidation uses that same capability, never a larger JSON reader.
    return j1_forward_cold_authority.opaque_reader(reader, capsule=raw.startswith(b"\x1f\x8b"))


def _runtime_blobs(manifest):
    """Bound declared large leaves before loading their opaque payloads.

    These declarations cannot authenticate a runtime. The restored typed cold
    graph must independently derive the exact same map before acceptance.
    """
    if manifest.get("schema") != COLD_SCHEMA:
        io.require("runtime_blobs" not in manifest, "control_archive_unexpected_runtime_blobs")
        return {}
    blobs = manifest.get("runtime_blobs")
    io.require(type(blobs) is dict and len(blobs) <= 16, "control_archive_runtime_blobs")
    entries = manifest["artifacts"]
    io.require(
        type(entries) is list
        and len(entries) <= MAX_FILES
        and all(
            type(entry) is dict
            and {"logical_name", "sha256", "bytes"} <= set(entry)
            and type(entry["logical_name"]) is str
            for entry in entries
        ),
        "control_archive_runtime_blob_entries",
    )
    io.require(
        len({entry["logical_name"] for entry in entries}) == len(entries),
        "control_archive_duplicate_path",
    )
    for path, value in blobs.items():
        io.require(
            type(path) is str
            and str(PurePosixPath(path)) == path
            and PurePosixPath(path).is_absolute()
            and ".." not in PurePosixPath(path).parts
            and not path.endswith(
                (".json", ".jsonl", ".py", ".toml", ".md", ".txt", ".conf", ".env")
            )
            and type(value) is dict
            and set(value) == {"sha256", "bytes"}
            and state._hash(value["sha256"])
            and type(value["bytes"]) is int
            and COLD_MAX_FILE_BYTES < value["bytes"] <= COLD_RUNTIME_MAX_FILE_BYTES,
            "control_archive_runtime_blob",
        )
    declared = {"authority" + path: value for path, value in blobs.items()}
    large = {
        entry["logical_name"]: {key: entry[key] for key in ("sha256", "bytes")}
        for entry in entries
        if type(entry.get("bytes")) is int and entry["bytes"] > COLD_MAX_FILE_BYTES
    }
    io.require(declared == large, "control_archive_runtime_blob_inventory")
    return blobs


def _entry_limit(schema, entry, blobs):
    if schema == COLD_SCHEMA:
        name = entry["logical_name"]
        if name.startswith("authority/") and name.removeprefix("authority") in blobs:
            value = blobs[name.removeprefix("authority")]
            io.require(
                {key: entry[key] for key in ("sha256", "bytes")} == value,
                "control_archive_runtime_blob_binding",
            )
            return COLD_RUNTIME_MAX_FILE_BYTES
        return COLD_MAX_FILE_BYTES if name.startswith("authority/") else MAX_FILE_BYTES
    return COLD_MAX_FILE_BYTES if schema == COLD_V3_SCHEMA else MAX_FILE_BYTES


def _path(root, value):
    io.require(type(value) is str and "\x00" not in value, "control_archive_path")
    path = io.logical_path(value)
    io.require(
        path.is_absolute()
        and str(path) == value
        and ".." not in path.parts
        and path.is_relative_to(root)
        and len(path.relative_to(root).parts) >= 2
        and path.relative_to(root).parts[0] in CONTROL_PREFIXES,
        "control_archive_path",
    )
    return path


def _control_reference(root, reference, *, cold):
    """Keep cold authorities with their typed collector, even below root.

    A physical shared parent does not turn an epoch, request or kernel proof
    into a Store state document. The cold collector authenticates those graphs;
    the canonical Store graph retains its four existing namespaces.
    """
    path, logical_root = io.logical_path(reference["path"]), io.logical_path(root)
    if not path.is_relative_to(logical_root):
        return False
    parts = path.relative_to(logical_root).parts
    return not cold or len(parts) >= 2 and parts[0] in CONTROL_PREFIXES


def _references(value):
    pending, count = [(value, None, 0)], 0
    while pending:
        item, key, depth = pending.pop()
        count += 1
        io.require(count <= MAX_NODES and depth <= 64, "control_archive_graph_limit")
        if type(item) is dict:
            # This historical reference deliberately names live pending. Its
            # bytes are retained under origins and recoveries after consumption.
            if key != "origin_pending_ref" and {"path", "sha256"} <= set(item):
                reference = {name: item[name] for name in ("path", "sha256")}
                state._reference(reference)
                yield reference
            pending.extend((child, name, depth + 1) for name, child in item.items())
        elif type(item) is list:
            pending.extend((child, None, depth + 1) for child in item)


def _consumer_maps(documents):
    result = {}
    for doc in documents.values():
        if type(doc) is not dict or "source_consumers" not in doc:
            continue
        files = doc["source_consumers"]
        io.require(
            type(files) is dict
            and 0 < len(files) <= 128
            and all(
                type(name) is str and CONSUMER.fullmatch(name) and state._hash(digest)
                for name, digest in files.items()
            ),
            "control_archive_consumer_inventory",
        )
        digest = io.sha(io.canonical(files))
        if "consumer_sha256" in doc:
            io.require(doc["consumer_sha256"] == digest, "control_archive_consumer_binding")
        result[digest] = files
    return result


def _implicit_references(root, document):
    """Retained bytes derived by Store instead of serialized as references."""
    if type(document) is not dict:
        return []
    if document.get("schema") == recovery.BUNDLE_SCHEMA:
        origin = io.logical_path(document["origin_plan_ref"]["path"]).parent.name
        return [
            {
                "path": str(root / "origins" / origin / "pending.json"),
                "sha256": document["origin_plan_ref"]["sha256"],
            }
        ]
    if document.get("schema") in {
        recovery_state.RECOVERY_SCHEMA,
        "autostop.j1.crm-manager-cold-forward-recovery.v1",
    }:
        return [
            {
                "path": str(
                    root / "state/recoveries" / (document["recovery_id"] + ".pending.json")
                ),
                "sha256": document["origin_pending_ref"]["sha256"],
            }
        ]
    return []


def _launch_references(root, baseline):
    """Capture a named attempt journal, never the whole changing permit tree."""
    if (
        type(baseline) is not dict
        or "native" not in baseline
        or baseline.get("schema") == "autostop.j1.crm-manager-cold-forward-baseline.v1"
        or "scripts/j1_forward_launch_native.py" not in baseline.get("source_consumers", {})
    ):
        return []
    attempt = launch._uuid(baseline["attempt_id"])
    directory = root / "launch-permits" / attempt
    snapshot = launch.LaunchStore(directory, owner=io.ROOT_UID).read()
    io.require(snapshot["ticket"]["attempt_id"] == attempt, "control_archive_launch_attempt")
    fixed = {"ticket.json", "context.json", "state.json", "operation.lock", "events"}
    if snapshot["state"]["acceptance"] is not None:
        fixed.add("native-acceptance.json")
    io.require(
        {path.name for path in directory.iterdir()} == fixed, "control_archive_launch_inventory"
    )
    paths = [directory / name for name in sorted(fixed - {"events"})]
    paths.extend(sorted((directory / "events").iterdir()))
    return [io.reference(path) for path in paths]


def _launch_semantic(root, documents, files):
    count = 0
    for baseline in documents.values():
        if (
            type(baseline) is not dict
            or "native" not in baseline
            or baseline.get("schema") == "autostop.j1.crm-manager-cold-forward-baseline.v1"
            or "scripts/j1_forward_launch_native.py" not in baseline.get("source_consumers", {})
        ):
            continue
        attempt = launch._uuid(baseline["attempt_id"])
        directory = root / "launch-permits" / attempt
        raw = files.get(str(directory / "ticket.json"))
        io.require(raw is not None, "control_archive_launch_missing")
        ticket = launch.validate_ticket(io.document(raw))
        context_raw = files.get(str(directory / "context.json"))
        io.require(
            context_raw is not None and io.sha(context_raw) == ticket["context_sha256"],
            "control_archive_launch_context",
        )
        context = io.document(context_raw)
        source_hash = io.sha(io.canonical(baseline["source_consumers"]))
        io.require(
            ticket["source_sha256"] == source_hash
            and context["consumers"] == baseline["source_consumers"]
            and ticket["attempt_id"] == attempt
            and ticket["plan_ref"] == context["plan_ref"]
            and context["plan_ref"]["path"] == str(root / "attempts" / attempt / "plan.json"),
            "control_archive_launch_binding",
        )
        state_raw = files.get(str(directory / "state.json"))
        io.require(state_raw is not None, "control_archive_launch_missing")
        current = launch._state(io.document(state_raw), io.sha(raw))
        previous = launch._initial(io.sha(raw))
        for number in range(1, current["sequence"] + 1):
            event_raw = files.get(str(directory / "events" / f"{number:020d}.json"))
            io.require(event_raw is not None, "control_archive_launch_history_missing")
            event = io.document(event_raw)
            io.require(
                set(event) == {"schema", "state"}
                and event["schema"] == "autostop.j1.forward-launch-event.v1",
                "control_archive_launch_event",
            )
            launch._successor(previous, event["state"], ticket=ticket)
            previous = event["state"]
        io.require(
            previous == current and current["execution_possible"] is True,
            "control_archive_launch_not_admitted",
        )
        if current["acceptance"] is not None:
            launch._verify_acceptance(
                ticket,
                io.sha(raw),
                current["acceptance"],
                0,
                reader=lambda path, _owner: files[str(path)],
            )
        count += 1
    return count


class _RestoredStore(state.Store):
    """Use the production state validators against explicit restored bytes only."""

    def __init__(self, root, base_sha256, files, authorities=None):
        self.root, self.base_sha256 = io.logical_path(root), base_sha256
        io.require(self.root.is_absolute() and ".." not in self.root.parts, "state_root_absolute")
        io.require(state._hash(base_sha256), "state_base_invalid")
        self.history = self.root / "history"
        self.head_path = self.root / "HEAD.json"
        self.pending_path = self.root / "pending.json"
        self.files = files
        self.authorities = {} if authorities is None else authorities

    def _envelope(self, document):
        from scripts import j1_forward_cold_state

        return j1_forward_cold_state.envelope(self, document)

    def _logical_path(self, value):
        return io.logical_path(value)

    def _validate_plan(self, plan):
        return variant.validate_plan(plan, self.base_sha256)

    def _records(self):
        records, transitioned = super()._records(), 0
        for record, _reference in records:
            rank = 2 if _cold(record["plan"]) else int(variant.is_mixed(record["plan"]))
            io.require(rank >= transitioned, "control_archive_manager_downgrade")
            transitioned = rank
        return records

    @contextmanager
    def _locked(self, *, create=False):
        io.require(not create, "control_archive_read_only")
        yield True

    def _contains(self, path):
        path = str(path)
        return path in self.files or any(
            io.logical_path(name).is_relative_to(io.logical_path(path)) for name in self.files
        )

    def _raw(self, path):
        key = str(path)
        io.require(
            key in self.files or key in self.authorities, "control_archive_reference_missing"
        )
        return self.files[key] if key in self.files else self.authorities[key]

    def _read(self, path):
        raw = self._raw(path)
        return io.document(raw), {"path": str(path), "sha256": io.sha(raw)}

    def _reference(self, path):
        return {"path": str(path), "sha256": io.sha(self._raw(path))}

    def _read_reference(self, reference):
        state._reference(reference)
        raw = self._raw(reference["path"])
        io.require(io.sha(raw) == reference["sha256"], "control_archive_reference_changed")
        return raw

    def read_opaque_reference(self, reference):
        # The payload map is restored bytes, never a native filesystem reader.
        return self._read_reference(reference)

    def read_runtime_capsule(self, reference):
        return self._read_reference(reference)

    def _history_paths(self):
        return sorted(
            io.logical_path(name)
            for name in self.files
            if io.logical_path(name).parent == self.history
        )

    def _durable(self, reference):
        # The enclosing restore copied/fsynced bytes. A read-only semantic
        # reader must never open the logical production paths or claim fsync.
        self._read_reference(reference)
        return reference

    def _mutation_denied(self, *_args, **_kwargs):
        raise io.Refusal("control_archive_read_only")

    begin = commit = recover = finish = _mutation_denied
    begin_recovery = commit_recovery = recover_recovery = finish_recovery = _mutation_denied


def _semantic(
    root,
    files,
    consumers,
    plan_ref,
    backup_ref,
    stage,
    authorities=None,
    capture_clock=None,
    *,
    codec=3,
    runtime_blobs=None,
):
    root = io.logical_path(root)
    io.require(stage in {"closed", "terminal"}, "control_archive_stage")
    _path(root, plan_ref["path"])
    raw_plan = files.get(plan_ref["path"])
    io.require(
        raw_plan is not None and io.sha(raw_plan) == plan_ref["sha256"],
        "control_archive_plan_binding",
    )
    plan = io.document(raw_plan)
    variant.validate_plan(plan, plan["base_sha256"])
    store = _RestoredStore(root / "state", plan["base_sha256"], files, authorities)
    directory = io.logical_path(plan_ref["path"]).parent
    captured, _ = store._read(directory / "backup-evidence.json")
    io.require(
        backup_ref["path"] == str(directory / "backup/manifest.json")
        and captured.get("manifest") == backup_ref
        and captured.get("manifest_sha256") == backup_ref["sha256"],
        "control_archive_backup_binding",
    )
    head, active = store.head(), store.active_plan()
    if stage == "closed":
        io.require(
            active is not None and active["plan"] == plan,
            "control_archive_active_plan_required",
        )
    else:
        io.require(
            active is None and head["record"] is not None and head["record"]["plan"] == plan,
            "control_archive_terminal_commit_required",
        )
        completed, completion_ref = store._read(directory / "completion.json")
        progress, _ = store._read(directory / "progress.json")
        io.require(
            completed.get("ok") is True
            and completed.get("plan_ref") == plan_ref
            and completed.get("commit")
            == store._reference(store.history / f"{plan['sequence']:020d}.json")
            and progress.get("phase") == "complete"
            and progress.get("completion") == completion_ref,
            "control_archive_terminal_receipt_binding",
        )
        for field in (
            ("cold_completion_ref",)
            if _cold(plan)
            else (
                "auxiliary_release_ref",
                *(("released_cohort_ref",) if "recovery_ref" in head["record"] else ()),
            )
        ):
            io.require(field in completed, "control_archive_terminal_evidence_missing")
            reference = completed[field]
            state._reference(reference)
            io.require(
                _cold(plan) or io.logical_path(reference["path"]).parent == directory,
                "control_archive_terminal_evidence_path",
            )
            store._read_reference(reference)
    documents = {name: io.document(raw) for name, raw in files.items() if name.endswith(".json")}
    for doc in documents.values():
        for reference in _references(doc):
            if io.logical_path(reference["path"]).is_relative_to(root):
                store._read_reference(reference)
        for reference in _implicit_references(root, doc):
            store._read_reference(reference)
        if type(doc) is dict and "recovery_origin" in doc:
            if doc.get("schema") == "autostop.j1.crm-manager-cold-forward-baseline.v1":
                from scripts import j1_forward_cold_state

                j1_forward_cold_state.validate_origin(doc, read_reference=store._read_reference)
            else:
                recovery.validate_origin(
                    doc["recovery_origin"],
                    root=root,
                    read_reference=store._read_reference,
                    read_file=store._raw,
                )
    required = _consumer_maps(documents)
    io.require(set(consumers) == set(required), "control_archive_consumer_bundle_missing")
    for digest, inventory in required.items():
        io.require(set(consumers[digest]) == set(inventory), "control_archive_consumer_files")
        io.require(
            all(io.sha(consumers[digest][name]) == value for name, value in inventory.items()),
            "control_archive_consumer_bytes_changed",
        )
    result = {
        "attempt_id": plan["attempt_id"],
        "head_sequence": head["sequence"],
        "head_sha256": head["sha256"],
        "pending_present": store.pending() is not None,
        "active_recovery_present": store._active_recovery() is not None,
        "consumer_bundle_count": len(consumers),
        "launch_journal_count": _launch_semantic(root, documents, files),
    }
    if _cold(plan):
        from scripts import j1_forward_cold_archive

        restored = {} if authorities is None else authorities
        combined = {**files, **restored}

        def listing(path):
            return j1_forward_cold_archive.restored_children(combined, path)

        expected = j1_forward_cold_archive.collect(
            plan,
            root=root,
            stage=stage,
            capture_clock=capture_clock,
            read_reference=store._read_reference,
            read_file=lambda path, _limit: store._raw(path),
            list_directory=listing,
            codec=codec,
        )
        io.require(expected == restored, "cold_archive_exact_graph_required")
        if codec == 4:
            io.require(
                expected.runtime_blob_paths == runtime_blobs, "cold_archive_exact_runtime_blobs"
            )
        result["cold_authority_graph_files"] = len(expected)
    elif variant.is_mixed(plan):
        from scripts import j1_forward_mixed_archive

        baseline = io.document(store._read_reference(plan["baseline_ref"]))
        expected_inventory = {
            name: baseline["source_consumers"][name] for name in variant.SUPPLEMENTAL
        }
        restored = {} if authorities is None else authorities

        def reference(ref):
            state._reference(ref)
            io.require(ref["path"] in restored, "mixed_archive_reference_missing")
            raw = restored[ref["path"]]
            io.require(io.sha(raw) == ref["sha256"], "mixed_archive_reference_changed")
            return raw

        def restored_file(path, _limit):
            io.require(path in restored, "mixed_archive_reference_missing")
            return restored[path]

        expected = j1_forward_mixed_archive.collect(
            plan,
            stage=stage,
            controller_inventory=expected_inventory,
            read_reference=reference,
            read_file=restored_file,
        )
        io.require(expected == restored, "mixed_archive_exact_graph_required")
        result["mixed_authority_graph_files"] = len(expected)
    else:
        io.require(not authorities, "control_archive_unexpected_authority")
    return result


def _seed(root, plan_ref, backup_ref, stage):
    seeds = [plan_ref, backup_ref]
    directory = Path(plan_ref["path"]).parent
    required = (
        "closure-owned.json",
        "process-scope.json",
        "ingress-drain.json",
        "backup-evidence.json",
    )
    if stage == "terminal":
        required += ("completion.json", "progress.json")
    seeds.extend(io.reference(directory / name) for name in required)
    for path in (
        *(root / "state" / name for name in ("HEAD.json", "pending.json", "active-recovery.json")),
    ):
        if path.exists() or path.is_symlink():
            seeds.append(io.reference(path))
    history = root / "state/history"
    if history.exists():
        io.protected_directory(history)
        paths = sorted(history.iterdir())
        io.require(len(paths) <= state.MAX_HISTORY, "state_history_limit")
        seeds.extend(io.reference(path) for path in paths)
    return seeds


def _package_directory(path, custody):
    if custody is None:
        io.protected_directory(path, private=True)
    else:
        custody(path, directory=True)


def _package_read(path, custody, limit=MAX_FILE_BYTES):
    if custody is None:
        return io.read_protected(path, limit)
    custody(path)
    before = path.lstat()
    io.require(
        stat.S_ISREG(before.st_mode)
        and before.st_nlink == 1
        and not getattr(before, "st_file_attributes", 0) & 0x400
        and 0 <= before.st_size <= limit,
        "control_archive_physical_payload_required",
    )
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after, final = os.fstat(descriptor), path.lstat()
        io.require(
            len(raw) == before.st_size <= limit and io.unchanged_read(before, opened, after, final),
            "control_archive_payload_changed_during_read",
        )
    finally:
        os.close(descriptor)
    custody(path)
    return raw


def _sync_directory(path):
    # Disposable workstation restore has no native crash-durability claim.
    # Windows file bytes are flushed; ACL custody is supplied by its worker.
    if os.name != "nt":
        io.fsync_directory(path)


def _write_bytes(path, raw, *, custody=None, limit=COLD_MAX_FILE_BYTES):
    io.require(len(raw) <= limit, "control_archive_file_limit")
    _package_directory(path.parent, custody)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(path.parent)
    io.require(
        _package_read(path, custody, limit) == raw,
        "control_archive_copy_changed",
    )


def _witness_files(root, witnesses, existing, end, *, cold=False):
    pending, result = list(witnesses), {}
    while pending:
        _budget(end)
        reference = pending.pop()
        path = _path(root, reference["path"])
        name = str(path)
        if name in existing or name in result:
            raw = existing.get(name, result.get(name))
            io.require(io.sha(raw) == reference["sha256"], "control_archive_witness_changed")
            continue
        io.require(len(existing) + len(result) < MAX_FILES, "control_archive_file_limit")
        raw = io.read_protected(Path(path), MAX_FILE_BYTES)
        io.require(io.sha(raw) == reference["sha256"], "control_archive_witness_changed")
        result[name] = raw
        io.require(
            sum(map(len, existing.values())) + sum(map(len, result.values())) <= MAX_TOTAL_BYTES,
            "control_archive_total_limit",
        )
        io.require(path.suffix == ".json", "control_archive_witness_document")
        doc = io.document(raw)
        pending.extend(ref for ref in _references(doc) if _control_reference(root, ref, cold=cold))
        pending.extend(_implicit_references(root, doc))
    return result


def _capture_graph(root, pending, files, launch_refs, end, *, cold):
    pending = list(pending)
    while pending:
        _budget(end)
        reference = pending.pop()
        path = _path(root, reference["path"])
        if str(path) in files:
            io.require(
                io.sha(files[str(path)]) == reference["sha256"], "control_archive_reference_changed"
            )
            continue
        io.require(len(files) < MAX_FILES, "control_archive_file_limit")
        raw = io.read_protected(Path(path), MAX_FILE_BYTES)
        io.require(io.sha(raw) == reference["sha256"], "control_archive_reference_changed")
        files[str(path)] = raw
        io.require(sum(map(len, files.values())) <= MAX_TOTAL_BYTES, "control_archive_total_limit")
        if path.suffix != ".json":
            io.require(
                (
                    path.is_relative_to(root / "origins")
                    and CONSUMER.fullmatch("/".join(path.parts[-2:]))
                )
                or (
                    path.is_relative_to(root / "launch-permits")
                    and path.name == "operation.lock"
                    and raw == b""
                ),
                "control_archive_unexpected_binary",
            )
            continue
        doc = io.document(raw)
        refs = _launch_references(root, doc)
        if refs:
            launch_refs[str(path)] = refs
            pending.extend(refs)
        pending.extend(ref for ref in _references(doc) if _control_reference(root, ref, cold=cold))
        pending.extend(_implicit_references(root, doc))


def _inward_references(root, authorities):
    """An external authority can point back into the canonical Store graph."""
    for name, raw in authorities.items():
        if name.endswith(".json"):
            for reference in _references(io.document(raw)):
                if _control_reference(root, reference, cold=True):
                    yield reference


def _consumer_bytes(documents, files, consumer_roots):
    required, consumers = _consumer_maps(documents), {}
    for digest, inventory in required.items():
        root = Path((consumer_roots or {}).get(digest, SOURCE))
        consumers[digest] = {}
        for name, expected in inventory.items():
            retained = [
                raw
                for path, raw in files.items()
                if io.logical_path(path).as_posix().endswith("/consumers/" + name)
                and io.sha(raw) == expected
            ]
            raw = retained[0] if retained else io.read_protected(root / name, MAX_FILE_BYTES)
            io.require(io.sha(raw) == expected, "control_archive_consumer_bytes_unavailable")
            consumers[digest][name] = raw
    return consumers


def capture(
    root,
    plan_ref,
    backup_manifest_ref,
    output,
    *,
    stage,
    observer,
    consumer_roots=None,
    timeout=60,
    authority_reader=None,
    authority_file_reader=None,
):
    """Copy one fenced control graph; caller retains the deployment lock."""
    root, output = Path(root), Path(output)
    io.require(root.is_absolute() and ".." not in root.parts, "control_archive_root")
    for reference in (plan_ref, backup_manifest_ref):
        state._reference(reference)
        _path(root, reference["path"])
    io.require(
        stage in {"closed", "terminal"} and callable(observer) and 0 < timeout <= 300,
        "control_archive_context",
    )
    io.protected_directory(root, private=True)
    io.protected_directory(output.parent, private=True)
    io.require(
        not output.exists() and not output.is_symlink() and not output.is_relative_to(root),
        "control_archive_new_external_destination_required",
    )
    end = time.monotonic() + timeout
    cold = _cold(io.document(io.referenced(plan_ref)))
    witnesses = []

    def fence():
        _budget(end)
        observed = observer()
        io.require(
            type(observed) is dict
            and set(observed) in ({"path", "sha256"}, {"attempt_id", "path", "sha256"}),
            "control_archive_observer_reference",
        )
        if "attempt_id" in observed:
            io.require(
                observed["attempt_id"] == io.document(io.referenced(plan_ref))["attempt_id"],
                "control_archive_observer_attempt",
            )
        reference = {name: observed[name] for name in ("path", "sha256")}
        state._reference(reference)
        io.referenced(reference)
        witnesses.append(dict(reference))

    fence()
    seeds = _seed(root, plan_ref, backup_manifest_ref, stage)
    files, launch_refs = {}, {}
    _capture_graph(root, seeds, files, launch_refs, end, cold=cold)
    authorities = {}
    plan = io.document(files[plan_ref["path"]])
    capture_clock = None
    runtime_blobs = {}
    if _cold(plan):
        from scripts import j1_forward_cold_archive

        package = io.document(
            (authority_reader or io.referenced)(plan["cold_authority_package_ref"])
        )
        capture_clock = {
            "boot_id": package["binding"]["boot_id"],
            "monotonic_ns": time.monotonic_ns(),
        }
        authorities = j1_forward_cold_archive.collect(
            plan,
            root=root,
            stage=stage,
            capture_clock=capture_clock,
            read_reference=authority_reader,
            read_file=authority_file_reader,
            codec=4,
        )
        runtime_blobs = authorities.runtime_blob_paths
        _capture_graph(
            root, _inward_references(root, authorities), files, launch_refs, end, cold=True
        )
    elif variant.is_mixed(plan):
        from scripts import j1_forward_mixed_archive

        baseline = io.document(files[plan["baseline_ref"]["path"]])
        authorities = j1_forward_mixed_archive.collect(
            plan,
            stage=stage,
            controller_inventory={
                name: baseline["source_consumers"][name] for name in variant.SUPPLEMENTAL
            },
        )
    documents = {name: io.document(raw) for name, raw in files.items() if name.endswith(".json")}
    consumers = _consumer_bytes(documents, files, consumer_roots)
    semantic = _semantic(
        root,
        files,
        consumers,
        plan_ref,
        backup_manifest_ref,
        stage,
        authorities,
        capture_clock,
        codec=4 if cold else 3,
        runtime_blobs=runtime_blobs,
    )
    fence()
    io.require(
        _seed(root, plan_ref, backup_manifest_ref, stage) == seeds, "control_archive_state_changed"
    )
    for path, raw in files.items():
        io.require(io.read_protected(path, MAX_FILE_BYTES) == raw, "control_archive_source_changed")
    output.mkdir(mode=0o700)
    io.fsync_directory(output.parent)
    artifacts = []
    payloads = [
        ("control/" + Path(path).relative_to(root).as_posix(), raw)
        for path, raw in sorted(files.items())
    ]
    payloads += [
        ("code/" + digest + "/" + name, raw)
        for digest, inventory in sorted(consumers.items())
        for name, raw in sorted(inventory.items())
    ]
    payloads += [("authority" + path, raw) for path, raw in sorted(authorities.items())]
    io.require(len(payloads) <= MAX_FILES, "control_archive_file_limit")
    io.require(
        sum(len(raw) for _, raw in payloads) <= transport_total_limit(4 if cold else 3),
        "control_archive_total_limit",
    )
    for index, (logical, raw) in enumerate(payloads):
        _budget(end)
        payload = f"{index:06d}.bin"
        entry = {
            "logical_name": logical,
            "payload": payload,
            "bytes": len(raw),
            "sha256": io.sha(raw),
        }
        io.require(
            len(raw) <= _entry_limit(_schema(plan), entry, runtime_blobs),
            "control_archive_entry_size",
        )
        _write_bytes(output / payload, raw, limit=_entry_limit(_schema(plan), entry, runtime_blobs))
        artifacts.append(entry)
    fence()
    # The terminal fence is itself part of the recoverable proof graph. Include
    # those exact immutable documents, rather than leaving dangling native refs.
    witnesses_raw = _witness_files(root, witnesses, files, end, cold=cold)
    for path, raw in sorted(witnesses_raw.items()):
        _budget(end)
        payload = f"{len(artifacts):06d}.bin"
        io.require(len(artifacts) < MAX_FILES, "control_archive_file_limit")
        _write_bytes(output / payload, raw)
        artifacts.append(
            {
                "logical_name": "control/" + Path(path).relative_to(root).as_posix(),
                "payload": payload,
                "bytes": len(raw),
                "sha256": io.sha(raw),
            }
        )
    files.update(witnesses_raw)
    io.require(
        sum(entry["bytes"] for entry in artifacts) <= transport_total_limit(4 if cold else 3),
        "control_archive_total_limit",
    )
    io.require(
        _semantic(
            root,
            files,
            consumers,
            plan_ref,
            backup_manifest_ref,
            stage,
            authorities,
            capture_clock,
            codec=4 if cold else 3,
            runtime_blobs=runtime_blobs,
        )
        == semantic,
        "control_archive_witness_semantics_changed",
    )
    io.require(
        _seed(root, plan_ref, backup_manifest_ref, stage) == seeds, "control_archive_state_changed"
    )
    for path, raw in files.items():
        _budget(end)
        io.require(io.read_protected(path, MAX_FILE_BYTES) == raw, "control_archive_source_changed")
    for path, raw in authorities.items():
        _budget(end)
        observed = (
            (_runtime_reader(authority_reader, raw) if path in runtime_blobs else authority_reader)(
                {"path": path, "sha256": io.sha(raw)}
            )
            if _cold(plan) and authority_reader is not None
            else io.read_protected(
                path,
                COLD_RUNTIME_MAX_FILE_BYTES
                if path in runtime_blobs
                else COLD_MAX_FILE_BYTES
                if _cold(plan)
                else MAX_FILE_BYTES,
            )
        )
        io.require(observed == raw, "mixed_archive_source_changed")
    for path, refs in launch_refs.items():
        io.require(
            _launch_references(root, documents[path]) == refs, "control_archive_launch_changed"
        )
    manifest = {
        "schema": _schema(plan),
        "stage": stage,
        "root": str(root),
        "plan_ref": plan_ref,
        "backup_manifest_ref": backup_manifest_ref,
        "artifacts": artifacts,
        "semantic": semantic,
        "witnesses": witnesses,
        **CLAIMS,
    }
    if _cold(plan):
        manifest["native_capture_clock"] = capture_clock
        manifest["runtime_blobs"] = runtime_blobs
        _runtime_blobs(manifest)
    reference = io.write_new(output / "manifest.json", manifest)
    return {"manifest": reference, "manifest_sha256": reference["sha256"], "stage": stage, **CLAIMS}


def bundle(package, manifest_sha256, output, *, timeout=60):
    """Bounded flat transport; the independent semantic restore remains mandatory."""
    from scripts import j1_forward_transport as transport

    package, output = Path(package), Path(output)
    end = transport.deadline(timeout)
    raw = io.read_protected(package / "manifest.json", MAX_FILE_BYTES)
    io.require(io.sha(raw) == manifest_sha256, "control_archive_manifest_changed")
    manifest = io.document(raw)
    entries = manifest["artifacts"]
    blobs = _runtime_blobs(manifest)
    codec = 4 if manifest.get("schema") == COLD_SCHEMA else 3
    io.require(len(entries) <= MAX_FILES, "control_archive_file_limit")
    names = {"manifest.json", *(f"{index:06d}.bin" for index in range(len(entries)))}
    io.require(
        {path.name for path in package.iterdir()} == names,
        "control_archive_exact_inventory_required",
    )
    files = [("manifest.json", package / "manifest.json", len(raw), manifest_sha256)]
    total = 0
    for index, entry in enumerate(entries):
        io.require(
            entry["payload"] == f"{index:06d}.bin"
            and type(entry["bytes"]) is int
            and 0 <= entry["bytes"] <= _entry_limit(manifest["schema"], entry, blobs),
            "control_archive_entry",
        )
        total += entry["bytes"]
        io.require(total <= transport_total_limit(codec), "control_archive_total_limit")
        files.append(
            (entry["payload"], package / entry["payload"], entry["bytes"], entry["sha256"])
        )
    return transport.pack(output, files, end)


def unbundle(envelope, envelope_sha256, package, *, timeout=60, codec=3):
    """Reject decompression bombs before allocating a full backup-sized payload."""
    from scripts import j1_forward_transport as transport

    return transport.extract(
        Path(envelope),
        envelope_sha256,
        Path(package),
        transport.deadline(timeout),
        max_members=MAX_FILES + 1,
        max_file=COLD_RUNTIME_MAX_FILE_BYTES if codec == 4 else COLD_MAX_FILE_BYTES,
        max_total=transport_total_limit(codec),
        max_manifest=MAX_FILE_BYTES,
    )


def verify_and_restore(
    package,
    manifest_sha256,
    output,
    *,
    plan_ref,
    backup_manifest_ref,
    stage,
    logical_root=ROOT,
    timeout=60,
    custody=None,
):
    """Restore private copies; Windows worker supplies its accepted ACL custody."""
    package, output, root = Path(package), Path(output), io.logical_path(logical_root)
    io.require(state._hash(manifest_sha256) and 0 < timeout <= 300, "control_archive_context")
    io.require(custody is None or callable(custody), "control_archive_custody_required")
    _package_directory(package, custody)
    _package_directory(output.parent, custody)
    io.require(
        not output.exists() and not output.is_symlink(), "control_archive_new_restore_required"
    )
    io.require(
        not io.logical_path(output).is_relative_to(root),
        "control_archive_production_restore_refused",
    )
    end = time.monotonic() + timeout
    raw = _package_read(package / "manifest.json", custody)
    io.require(io.sha(raw) == manifest_sha256, "control_archive_manifest_changed")
    manifest = io.document(raw)
    io.require(
        type(manifest) is dict
        and set(manifest)
        == {
            "schema",
            "stage",
            "root",
            "plan_ref",
            "backup_manifest_ref",
            "artifacts",
            "semantic",
            "witnesses",
            *CLAIMS,
            *(
                ("native_capture_clock",)
                if manifest.get("schema") in {COLD_V3_SCHEMA, COLD_SCHEMA}
                else ()
            ),
            *(("runtime_blobs",) if manifest.get("schema") == COLD_SCHEMA else ()),
        }
        and manifest["schema"] in {SCHEMA, MIXED_SCHEMA, COLD_V3_SCHEMA, COLD_SCHEMA}
        and manifest["stage"] == stage
        and manifest["root"] == str(root)
        and manifest["plan_ref"] == plan_ref
        and manifest["backup_manifest_ref"] == backup_manifest_ref
        and all(manifest[key] is False for key in CLAIMS),
        "control_archive_manifest_binding",
    )
    entries = manifest["artifacts"]
    codec = 4 if manifest["schema"] == COLD_SCHEMA else 3
    blobs = _runtime_blobs(manifest)
    io.require(
        type(manifest["witnesses"]) is list and len(manifest["witnesses"]) == 3,
        "control_archive_witness_inventory",
    )
    for reference in manifest["witnesses"]:
        state._reference(reference)
    io.require(
        type(entries) is list and 0 < len(entries) <= MAX_FILES, "control_archive_file_limit"
    )
    io.require(
        {path.name for path in package.iterdir()}
        == {"manifest.json", *(f"{index:06d}.bin" for index in range(len(entries)))},
        "control_archive_exact_inventory_required",
    )
    files, consumers, authorities, names, total = {}, {}, {}, set(), 0
    output.mkdir(mode=0o700)
    _package_directory(output, custody)
    _sync_directory(output.parent)
    for index, entry in enumerate(entries):
        _budget(end)
        io.require(
            type(entry) is dict and set(entry) == {"logical_name", "payload", "bytes", "sha256"},
            "control_archive_entry",
        )
        name = entry["logical_name"]
        io.require(
            type(name) is str and name not in names and "\\" not in name,
            "control_archive_logical_name",
        )
        parts = PurePosixPath(name).parts
        io.require(
            parts
            and not PurePosixPath(name).is_absolute()
            and ".." not in parts
            and str(PurePosixPath(name)) == name,
            "control_archive_logical_name",
        )
        names.add(name)
        io.require(
            entry["payload"] == f"{index:06d}.bin"
            and type(entry["bytes"]) is int
            and 0 <= entry["bytes"] <= _entry_limit(manifest["schema"], entry, blobs)
            and state._hash(entry["sha256"]),
            "control_archive_entry",
        )
        file_limit = _entry_limit(manifest["schema"], entry, blobs)
        raw = _package_read(package / entry["payload"], custody, file_limit)
        io.require(
            len(raw) == entry["bytes"] and io.sha(raw) == entry["sha256"],
            "control_archive_payload_changed",
        )
        total += len(raw)
        io.require(total <= transport_total_limit(codec), "control_archive_total_limit")
        _write_bytes(output / entry["payload"], raw, custody=custody, limit=file_limit)
        restored = _package_read(output / entry["payload"], custody, file_limit)
        if parts[0] == "control":
            path = _path(root, str(root.joinpath(*parts[1:])))
            io.require(str(path) not in files, "control_archive_duplicate_path")
            files[str(path)] = restored
        elif parts[0] == "authority":
            io.require(
                manifest["schema"] in {MIXED_SCHEMA, COLD_V3_SCHEMA, COLD_SCHEMA}
                and len(parts) > 1,
                "mixed_archive_schema",
            )
            path = "/" + "/".join(parts[1:])
            state._reference({"path": path, "sha256": io.sha(restored)})
            io.require(path not in authorities, "mixed_archive_duplicate_path")
            authorities[path] = restored
        else:
            io.require(
                len(parts) == 4
                and parts[0] == "code"
                and state._hash(parts[1])
                and CONSUMER.fullmatch("/".join(parts[2:])),
                "control_archive_consumer_path",
            )
            consumers.setdefault(parts[1], {})["/".join(parts[2:])] = restored
    plan = io.document(files[plan_ref["path"]])
    io.require(
        manifest["schema"] == _schema(plan, codec=codec),
        "mixed_archive_schema",
    )
    actual = _semantic(
        root,
        files,
        consumers,
        plan_ref,
        backup_manifest_ref,
        stage,
        authorities,
        manifest.get("native_capture_clock"),
        codec=codec,
        runtime_blobs=blobs,
    )
    io.require(actual == manifest["semantic"], "control_archive_semantic_result_changed")
    io.require(
        backup_manifest_ref["path"] in files
        and io.sha(files[backup_manifest_ref["path"]]) == backup_manifest_ref["sha256"],
        "control_archive_backup_binding",
    )
    receipt = {
        "schema": _schema(plan, restore=True, codec=codec),
        "stage": stage,
        "manifest_sha256": manifest_sha256,
        "plan_ref": plan_ref,
        "backup_manifest_ref": backup_manifest_ref,
        "actual_disposable_restore": True,
        "semantic_graph_verified": True,
        "file_count": len(entries),
        "semantic": actual,
        **CLAIMS,
    }
    _write_bytes(output / "restore.json", io.canonical(receipt), custody=custody)
    return receipt
