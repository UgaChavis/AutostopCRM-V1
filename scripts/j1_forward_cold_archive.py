"""Bounded cold authority graph; restoring it never grants native authority.

Only captured bytes and an authenticated capture clock are consulted during
semantic restore. Archived Python and the restoring host's native state are
never used as validators or freshness evidence.
"""

from __future__ import annotations

import types
import uuid
from pathlib import Path, PurePosixPath

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_state as cold_state
from scripts import j1_forward_io as io
from scripts import j1_forward_launch_permission as launch

MAX_FILES = 4096
MAX_BYTES = 96 * 1024**2
MAX_FILE_BYTES = 32 * 1024**2
SLOT_NAMESPACE = uuid.UUID("8770ab7b-a54d-41ce-9525-a9cf66494f18")


def _references(document):
    pending = [document]
    count = 0
    while pending:
        value = pending.pop()
        count += 1
        io.require(count <= 250_000, "cold_archive_graph_limit")
        if type(value) is dict:
            if set(value) == {"path", "sha256"}:
                yield cold.reference(value)
            pending.extend(child for name, child in value.items() if name != "origin_pending_ref")
        elif type(value) is list:
            pending.extend(value)


def collect(
    plan, *, root, stage, capture_clock, read_reference=None, read_file=None, list_directory=None
):
    """Pure byte semantics when supplied restored readers; no live permits."""
    reader = io.referenced if read_reference is None else read_reference
    file_reader = io.read_protected if read_file is None else read_file
    listing = (
        (lambda path: sorted(str(item) for item in Path(path).iterdir()))
        if list_directory is None
        else list_directory
    )
    root = PurePosixPath(root)
    cold_state.validate_plan(plan, plan["base_sha256"])
    io.require(stage in {"closed", "terminal"}, "cold_archive_stage")
    io.require(
        type(capture_clock) is dict
        and set(capture_clock) == {"boot_id", "monotonic_ns"}
        and type(capture_clock["monotonic_ns"]) is int,
        "cold_archive_capture_clock",
    )
    seen, pending = (
        {},
        [
            plan["cold_authority_package_ref"],
            plan["dispatch_epoch_ref"],
            plan["cold_crm_spec_ref"],
            plan["baseline_ref"],
        ],
    )

    def read(reference):
        cold.reference(reference)
        path = reference["path"]
        raw = reader(reference)
        io.require(
            type(raw) is bytes and io.sha(raw) == reference["sha256"],
            "cold_archive_reference_changed",
        )
        io.require(path not in seen or seen[path] == raw, "cold_archive_reference_alias")
        seen[path] = raw
        io.require(
            len(seen) <= MAX_FILES
            and len(raw) <= MAX_FILE_BYTES
            and sum(map(len, seen.values())) <= MAX_BYTES,
            "cold_archive_budget",
        )
        return raw

    def walk():
        visited = set()
        while pending:
            reference = pending.pop()
            if reference["path"] in visited:
                read(reference)
                continue
            raw = read(reference)
            visited.add(reference["path"])
            if reference["path"].endswith(".json"):
                pending.extend(_references(io.document(raw)))

    walk()
    value = cold.validate_package(
        plan["cold_authority_package_ref"],
        read_reference=read,
        now_ns=capture_clock["monotonic_ns"],
    )
    package, binding = value["package"], value["package"]["binding"]
    io.require(capture_clock["boot_id"] == binding["boot_id"], "cold_archive_capture_boot")
    epoch = io.document(read(plan["dispatch_epoch_ref"]))
    io.require(
        epoch["package_ref"] == plan["cold_authority_package_ref"]
        and binding["attempt_id"] == plan["attempt_id"]
        and binding["candidate_crm_sha"] == plan["candidate"]["revision"]
        and binding["candidate_manager_sha"] == plan["candidate_manager_sha"],
        "cold_archive_plan_binding",
    )
    private_root = PurePosixPath(plan["dispatch_epoch_ref"]["path"]).parent.parent
    provider = types.SimpleNamespace(scope=package["scope"], root=private_root, read_reference=read)
    epochs.verify(epoch, package, provider, measure_lock=False)
    expected_slots = {
        unit: str(uuid.uuid5(SLOT_NAMESPACE, binding["attempt_id"] + binding["generation"] + unit))
        for unit in cold.ALLOWED
    }
    io.require(epoch["slots"] == expected_slots, "cold_archive_slot_identity")
    directory = PurePosixPath(plan["dispatch_epoch_ref"]["path"]).parent
    io.require(epoch["journal_root"] == str(directory / "journals"), "cold_archive_journal_path")
    for unit, identifier in epoch["slots"].items():
        journal = directory / "journals" / identifier
        fixed = {"ticket.json", "context.json", "state.json", "operation.lock", "events"}
        state_raw = file_reader(str(journal / "state.json"), MAX_FILE_BYTES)
        state_doc = io.document(state_raw)
        if state_doc["acceptance"] is not None:
            fixed.add("native-acceptance.json")
        io.require(
            {PurePosixPath(path).name for path in listing(str(journal))} == fixed,
            "cold_archive_journal_inventory",
        )
        paths = [str(journal / name) for name in sorted(fixed - {"events"})]
        event_paths = listing(str(journal / "events"))
        io.require(len(event_paths) <= 8192, "cold_archive_journal_limit")
        paths.extend(event_paths)
        for path in paths:
            raw = file_reader(path, MAX_FILE_BYTES)
            read({"path": path, "sha256": io.sha(raw)})
        ticket = launch.validate_ticket(io.document(seen[str(journal / "ticket.json")]))
        context_raw = seen[str(journal / "context.json")]
        context = io.document(context_raw)
        io.require(
            ticket["attempt_id"] == identifier
            and ticket["plan_ref"] == epoch["package_ref"]
            and ticket["source_sha256"] == binding["consumer_sha256"]
            and ticket["context_sha256"] == io.sha(context_raw)
            and context["logical_unit"] == unit
            and context["binding"] == binding
            and context["epoch_ref"] == plan["dispatch_epoch_ref"]
            and context["policy_ref"] == package["policies"][unit],
            "cold_archive_ticket_binding",
        )
        current = launch._state(state_doc, io.sha(seen[str(journal / "ticket.json")]))
        previous = launch._initial(current["ticket_sha256"])
        io.require(
            event_paths
            == [
                str(journal / "events" / f"{n:020d}.json")
                for n in range(1, current["sequence"] + 1)
            ],
            "cold_archive_journal_sequence",
        )
        for path in event_paths:
            event = io.document(seen[path])
            io.require(
                set(event) == {"schema", "state"}
                and event["schema"] == "autostop.j1.forward-launch-event.v1",
                "cold_archive_journal_event",
            )
            launch._successor(previous, event["state"], ticket=ticket)
            previous = event["state"]
        io.require(previous == current, "cold_archive_journal_state")
        if stage == "terminal":
            io.require(current["execution_possible"] is True, "cold_archive_unaccepted_slot")
        if current["acceptance"] is not None:
            launch._verify_acceptance(
                ticket,
                current["ticket_sha256"],
                current["acceptance"],
                0,
                reader=lambda path, _owner: seen[str(path)],
            )
    if stage == "terminal":
        for name in (
            "release-admission.json",
            "crm-accepted.json",
            "commit.json",
            "opened.json",
            "complete.json",
        ):
            path = str(directory / name)
            raw = file_reader(path, MAX_FILE_BYTES)
            pending.append({"path": path, "sha256": io.sha(raw)})
        walk()
        completed = io.document(seen[str(directory / "complete.json")])
        io.require(
            completed.get("old_hold_released") is False
            and completed.get("old_barriers_opened") is False,
            "cold_archive_old_writer_permission",
        )
    # Old origin and public review key are part of this same bounded graph.
    cold_state.validate_origin(io.document(read(plan["baseline_ref"])), read_reference=read)
    for path, raw in seen.items():
        io.require(
            reader({"path": path, "sha256": io.sha(raw)}) == raw, "cold_archive_source_changed"
        )
    return {path: raw for path, raw in seen.items() if not PurePosixPath(path).is_relative_to(root)}
