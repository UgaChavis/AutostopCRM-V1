"""Bounded pure restore of the exact warm successor authority proof graph.

Only reviewed source validators execute. Captured guards are opaque bytes.
No native check, authorize, process observation or application import occurs.
"""

from __future__ import annotations

import time
from pathlib import PurePosixPath
from types import SimpleNamespace

from scripts import j1_forward_io as io
from scripts import j1_forward_mixed_authority as mixed
from scripts import j1_manager_successor_guard_v2 as guard
from scripts import j1_manager_successor_native_v2 as native

MAX_FILES = 2048
MAX_FILE_BYTES = 8 * 1024**2
MAX_TOTAL_BYTES = 64 * 1024**2


def collect(plan, *, stage, controller_inventory, read_reference=None, read_file=None):
    """Rebuild known graph edges from bound documents; never walk an OS tree."""
    io.require(stage in {"closed", "terminal"}, "mixed_archive_stage")
    mixed.validate_plan(plan, plan["base_sha256"])
    read_reference = io.referenced if read_reference is None else read_reference
    read_file = io.read_protected if read_file is None else read_file
    files = {}

    def remember(path, raw):
        io.require(
            type(path) is str
            and PurePosixPath(path).is_absolute()
            and str(PurePosixPath(path)) == path
            and ".." not in PurePosixPath(path).parts
            and "\\" not in path,
            "mixed_archive_path",
        )
        io.require(
            type(raw) is bytes and len(raw) <= MAX_FILE_BYTES,
            "mixed_archive_reference_size",
        )
        io.require(files.get(path, raw) == raw, "mixed_archive_reference_conflict")
        files[path] = raw
        io.require(
            len(files) <= MAX_FILES and sum(map(len, files.values())) <= MAX_TOTAL_BYTES,
            "mixed_archive_limit",
        )
        return raw

    def referenced(ref):
        mixed.state._reference(ref)
        raw = read_reference(ref)
        io.require(io.sha(raw) == ref["sha256"], "mixed_archive_reference_changed")
        return remember(ref["path"], raw)

    receipt, binding = mixed.load_transition(
        plan["manager_transition_ref"],
        read_reference=referenced,
        controller_inventory=controller_inventory,
    )
    io.require(receipt["attempt_id"] == plan["attempt_id"], "mixed_archive_attempt_binding")
    for name in ("guard", "native"):
        referenced(binding[name])
    for path, digest in ((guard.BASE, guard.BASE_SHA256), (native.BASE, native.BASE_SHA256)):
        referenced({"path": str(path), "sha256": digest})

    class Reader(guard.Reader):
        def raw(self, ref):
            io.require(time.monotonic() < self.deadline, "mixed_archive_deadline")
            raw = referenced(ref)
            self.seen[ref["path"]] = dict(ref)
            return raw

    def fixed_state(path, limit):
        io.require(path == guard.STATE and stage == "terminal", "mixed_archive_fixed_read")
        raw = read_file(path, limit)
        io.require(len(raw) <= limit, "mixed_archive_reference_size")
        return remember(path, raw)

    reader = Reader(SimpleNamespace(read=fixed_state), time.monotonic() + 30)
    capsule, _index, before, policy, *_refs = guard._source_authority(binding, reader)
    guard._profile(binding, capsule, policy, reader)
    inputs = io.document(files[receipt["source_inputs_ref"]["path"]])
    io.require(
        type(inputs) is dict
        and inputs.get("schema") == "autostop.j1.manager-transition-source-inputs.v1"
        and inputs.get("attempt_id") == plan["attempt_id"],
        "mixed_archive_source_inputs",
    )
    from scripts import j1_manager_transition_source as source

    for project, revision in (
        ("manager", guard.REVISION),
        ("controller", capsule["controller"]["revision"]),
    ):
        item = inputs.get(project)
        io.require(type(item) is dict, "mixed_archive_ci_input")
        observed = source.cached_ci(
            io.document(referenced(item.get("ci"))),
            "crm" if project == "controller" else project,
            revision,
        )
        expected = (
            capsule["candidate"]["ci"] if project == "manager" else capsule["controller"]["ci"]
        )
        io.require(observed == expected, "mixed_archive_ci_binding")
    if stage == "terminal":
        boot_id = reader.doc(before["cache"]["state"])["boot_id"]
        guard._permit(binding, receipt["binding_ref"]["sha256"], {"boot_id": boot_id}, reader)
    return files
