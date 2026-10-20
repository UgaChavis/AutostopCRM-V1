"""Bounded cold authority graph; restoring it never grants native authority.

Only captured bytes and an authenticated capture clock are consulted during
semantic restore. Archived Python and the restoring host's native state are
never used as validators or freshness evidence.
"""

from __future__ import annotations

import io as streams
import stat
import tarfile
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
V4_MAX_BYTES = 512 * 1024**2
V4_MAX_RUNTIME_BYTES = 384 * 1024**2
MAX_INTERPRETER_ALIASES = 6
INTERPRETER_MEMBERS = {"python/bin/python3.12", "venv/bin/python3.12"}
SLOT_NAMESPACE = uuid.UUID("8770ab7b-a54d-41ce-9525-a9cf66494f18")


class CapturedGraph(dict):
    """Full captured bytes plus exact large leaves and optional v5 aliases."""

    def __init__(self, files, runtime_blob_paths, runtime_aliases=None):
        super().__init__(files)
        self.runtime_blob_paths = dict(runtime_blob_paths)
        self.runtime_aliases = {} if runtime_aliases is None else dict(runtime_aliases)
        self.runtime_alias_versions = {}
        self.source_versions = {}


def validate_aliases(aliases):
    """The v5 extension describes only bounded regular CPython interpreters."""
    io.require(
        type(aliases) is dict and len(aliases) <= MAX_INTERPRETER_ALIASES,
        "cold_archive_alias_inventory",
    )
    for path, row in aliases.items():
        cold.reference({"path": path, "sha256": row.get("sha256") if type(row) is dict else None})
        cold.fields(
            row, {"runtime_index_ref", "member", "sha256", "bytes", "mode"}, "archive_alias_fields"
        )
        cold.reference(row["runtime_index_ref"])
        io.require(
            type(row["member"]) is str
            and row["member"] in INTERPRETER_MEMBERS
            and type(row["bytes"]) is int
            and 4 <= row["bytes"] <= MAX_FILE_BYTES
            and type(row["mode"]) is int
            and 0 <= row["mode"] <= 0o777
            and row["mode"] & 0o111
            and not row["mode"] & 0o022
            and PurePosixPath(path).name == "python3.12",
            "cold_archive_alias_shape",
        )
    return aliases


def _interpreter_aliases(package, read):
    """Candidates become usable only after collect validates the full package."""
    from scripts import j1_forward_cold_runtime_capsule as capsule

    result, indexes = {}, {}
    cold.fields(package["policies"], cold.ALLOWED, "archive_alias_policies")
    for unit, reference in package["policies"].items():
        policy = io.document(read(reference))
        cold.fields(policy, cold.POLICY_FIELDS, "archive_alias_policy")
        index_ref = cold.reference(policy["runtime_index_ref"])
        index_key = (index_ref["path"], index_ref["sha256"])
        raw = read(index_ref)
        if index_key not in indexes:
            index = io.document(raw)
            io.require(type(index) is dict, "cold_archive_alias_index")
            rows = (
                capsule.validate_index(index, binding=package["binding"], scope=package["scope"])
                if index.get("schema") == capsule.SCHEMA
                else None
            )
            indexes[index_key] = index, rows
        index, rows = indexes[index_key]
        if rows is None:
            continue
        interpreter = cold.reference(policy["interpreter_ref"])
        for member in INTERPRETER_MEMBERS:
            root, *parts = PurePosixPath(member).parts
            if (
                str(PurePosixPath(index["runtime_roots"][root]).joinpath(*parts))
                != interpreter["path"]
            ):
                continue
            row = rows.get(member)
            io.require(
                row is not None
                and row["kind"] == "file"
                and row["sha256"] == interpreter["sha256"],
                "cold_archive_alias_member",
            )
            alias = {
                "runtime_index_ref": index_ref,
                "member": member,
                **{key: row[key] for key in ("sha256", "bytes", "mode")},
            }
            io.require(
                interpreter["path"] not in result or result[interpreter["path"]] == alias,
                "cold_archive_alias_conflict",
            )
            result[interpreter["path"]] = alias
    return validate_aliases(result)


def restore_interpreters(aliases, authorities, *, _captured_packages=None):
    """Verify every capsule member, then reconstruct selected ELF bytes inertly."""
    from scripts import j1_forward_cold_runtime_capsule as capsule

    validate_aliases(aliases)
    result, groups = {}, {}
    for path, row in aliases.items():
        io.require(path not in authorities, "cold_archive_alias_payload_present")
        groups.setdefault(
            (row["runtime_index_ref"]["path"], row["runtime_index_ref"]["sha256"]), []
        ).append((path, row))
    for (index_path, digest), selected in groups.items():
        io.require(index_path in authorities, "cold_archive_alias_index_missing")
        raw_index = authorities[index_path]
        io.require(io.sha(raw_index) == digest, "cold_archive_alias_index_digest")
        index = io.document(raw_index)
        cold.fields(index, capsule.FIELDS, "archive_alias_index_fields")
        rows = capsule.validate_index(index, binding=index["binding"], scope=index["scope"])

        def read(reference):
            io.require(reference["path"] in authorities, "cold_archive_alias_reference_missing")
            raw = authorities[reference["path"]]
            io.require(io.sha(raw) == reference["sha256"], "cold_archive_alias_reference_changed")
            return raw

        if _captured_packages is None:
            capsule.validate(
                index, binding=index["binding"], scope=index["scope"], read_reference=read
            )
        else:
            io.require(
                type(_captured_packages) is cold._CapturedPackages, "captured_package_reader"
            )
            _captured_packages.runtime_index(
                {"path": index_path, "sha256": digest},
                binding=index["binding"],
                scope=index["scope"],
                read_reference=read,
            )
        wanted = {}
        for path, row in selected:
            entry = rows.get(row["member"])
            root, *parts = PurePosixPath(row["member"]).parts
            io.require(
                entry is not None
                and entry["kind"] == "file"
                and all(entry[key] == row[key] for key in ("sha256", "bytes", "mode"))
                and str(PurePosixPath(index["runtime_roots"][root]).joinpath(*parts)) == path,
                "cold_archive_alias_member_binding",
            )
            wanted[row["member"]] = (path, row)
        with tarfile.open(
            fileobj=streams.BytesIO(read(index["capsule_ref"])), mode="r|gz"
        ) as packed:
            for member in packed:
                if member.name not in wanted:
                    continue
                path, row = wanted.pop(member.name)
                io.require(
                    member.isfile() and member.mode == row["mode"] and member.size == row["bytes"],
                    "cold_archive_alias_tar_member",
                )
                stream = packed.extractfile(member)
                raw = stream.read(row["bytes"] + 1)
                io.require(
                    len(raw) == row["bytes"]
                    and io.sha(raw) == row["sha256"]
                    and raw.startswith(b"\x7fELF"),
                    "cold_archive_alias_elf",
                )
                result[path] = raw
        io.require(not wanted, "cold_archive_alias_member_missing")
    return result


class _ProtectedReader:
    def read_reference(self, reference):
        return io.read_protected(reference["path"], MAX_FILE_BYTES)

    def read_opaque_reference(self, reference):
        return io.read_protected(reference["path"], V4_MAX_RUNTIME_BYTES)

    read_runtime_capsule = read_opaque_reference


def _version(path):
    info = Path(path).lstat()
    return tuple(
        getattr(info, key)
        for key in (
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
            "st_mode",
            "st_uid",
            "st_gid",
            "st_nlink",
        )
    )


class _ReadSnapshot:
    """One capture's bytes, with mandatory full source revalidation at exit.

    The protected reader owns one descriptor per actual read. We retain no
    descriptor per graph entry. Pure restore supplies no version callback and
    never stats a logical native path or asserts current native authority.
    """

    def __init__(self, reader, version=None, *, codec=3):
        io.require(type(codec) is int and codec in {3, 4, 5}, "cold_archive_codec")
        self.reader, self.version = reader, version
        self.codec, self.kernel_v2 = codec, False
        self.files, self.references, self.versions = {}, {}, {}
        self.opaque, self.capsules, self.runtime_blob_paths = set(), set(), {}
        self.total = 0
        self.runtime_aliases = {}

    def read(self, reference, *, opaque=False, capsule=False):
        cold.reference(reference)
        path = reference["path"]
        if path in self.files:
            io.require(self.references[path] == reference, "cold_archive_reference_alias")
            self._kind(reference, self.files[path], opaque=opaque, capsule=capsule)
            return self.files[path]
        before = self.version(path) if self.version is not None else None
        # Only a v2 kernel in this package enables the separate large reader.
        # Ordinary references cannot opt into a larger source/JSON budget.
        large = (opaque or capsule) and self.codec in {4, 5} and self.kernel_v2
        read = cold.opaque_reader(self.reader, capsule=capsule) if large else self.reader
        raw = read(reference)
        io.require(
            type(raw) is bytes and io.sha(raw) == reference["sha256"],
            "cold_archive_reference_changed",
        )
        if self.version is not None:
            io.require(self.version(path) == before, "cold_archive_source_version_changed")
            self.versions[path] = before
        self._kind(reference, raw, opaque=opaque, capsule=capsule)
        alias = self.runtime_aliases.get(path)
        if alias is not None:
            io.require(
                reference["sha256"] == alias["sha256"]
                and len(raw) == alias["bytes"]
                and raw.startswith(b"\x7fELF"),
                "cold_archive_alias_elf",
            )
            if before is not None:
                io.require(
                    stat.S_ISREG(before[5])
                    and stat.S_IMODE(before[5]) == alias["mode"]
                    and before[-1] == 1,
                    "cold_archive_alias_source_metadata",
                )
        self.total += 0 if alias is not None else len(raw)
        io.require(
            len(self.files) < MAX_FILES
            and self.total <= (V4_MAX_BYTES if self.codec in {4, 5} else MAX_BYTES),
            "cold_archive_budget",
        )
        self.files[path], self.references[path] = raw, dict(reference)
        return raw

    def _kind(self, reference, raw, *, opaque, capsule=False):
        if opaque or capsule:
            cold.runtime_bytes(reference, raw, capsule=capsule)
            (self.capsules if capsule else self.opaque).add(reference["path"])
        if len(raw) > MAX_FILE_BYTES:
            io.require(
                (opaque or capsule) and self.codec in {4, 5} and self.kernel_v2,
                "cold_archive_large_runtime_untyped",
            )
            self.runtime_blob_paths[reference["path"]] = {
                "sha256": reference["sha256"],
                "bytes": len(raw),
            }

    def read_opaque_reference(self, reference):
        return self.read(reference, opaque=True)

    def read_runtime_capsule(self, reference):
        io.require(self.codec in {4, 5}, "cold_archive_capsule_codec")
        return self.read(reference, capsule=True)

    def captured(self, reference):
        """Walk an already typed leaf without converting it into source code."""
        return self.read(
            reference,
            opaque=reference["path"] in self.opaque,
            capsule=reference["path"] in self.capsules,
        )

    def recheck(self):
        for path, raw in self.files.items():
            if self.version is not None:
                io.require(
                    self.version(path) == self.versions[path], "cold_archive_source_version_changed"
                )
            large = path in self.opaque | self.capsules and self.codec in {4, 5} and self.kernel_v2
            read = (
                cold.opaque_reader(self.reader, capsule=path in self.capsules)
                if large
                else self.reader
            )
            observed = read(self.references[path])
            io.require(observed == raw, "cold_archive_source_changed")
            if self.version is not None:
                io.require(
                    self.version(path) == self.versions[path], "cold_archive_source_version_changed"
                )


def control_path(path, root):
    """Only the four established controller areas use the plain control codec.

    Cold epochs beneath the same physical root remain typed authorities. This
    classification works on the logical Linux paths during off-host restore.
    """
    path, root = PurePosixPath(path), PurePosixPath(root)
    if not path.is_relative_to(root):
        return False
    relative = path.relative_to(root)
    return bool(relative.parts) and relative.parts[0] in {
        "state",
        "attempts",
        "origins",
        "launch-permits",
    }


def restored_children(files, path):
    """Reconstruct only the canonical empty events directory at initial state.

    Capture checked the physical directory. A restored byte graph has no
    directory payloads; initial state proves the historical *empty topology*,
    never a no-effect outcome or any current execution permission.
    """
    parent = PurePosixPath(path)
    children = {
        str(parent / PurePosixPath(name).relative_to(parent).parts[0])
        for name in files
        if PurePosixPath(name).is_relative_to(parent) and PurePosixPath(name) != parent
    }
    required = {
        str(parent / name)
        for name in ("ticket.json", "context.json", "state.json", "operation.lock")
    }
    if parent.parent.name == "journals" and cold._uuid(parent.name) and required <= set(files):
        ticket_raw = files[str(parent / "ticket.json")]
        launch.validate_ticket(io.document(ticket_raw))
        initial = launch._initial(io.sha(ticket_raw))
        current = launch._state(io.document(files[str(parent / "state.json")]), io.sha(ticket_raw))
        if current == initial:
            children.add(str(parent / "events"))
    return sorted(children)


def _crm_entry(directory, plan, binding, *, read, file_reader, listing, seen):
    journal = directory / "crm-start" / plan["attempt_id"]
    paths = {
        "ticket.json",
        "context.json",
        "state.json",
        "operation.lock",
        "native-acceptance.json",
        "events",
    }
    io.require(
        {PurePosixPath(path).name for path in listing(str(journal))} == paths,
        "cold_archive_crm_journal_inventory",
    )
    events = listing(str(journal / "events"))
    io.require(len(events) <= 8192, "cold_archive_crm_journal_limit")
    for path in [*(str(journal / name) for name in sorted(paths - {"events"})), *events]:
        raw = file_reader(path, MAX_FILE_BYTES)
        read({"path": path, "sha256": io.sha(raw)})
    ticket_raw = seen[str(journal / "ticket.json")]
    ticket = launch.validate_ticket(io.document(ticket_raw))
    context_raw = seen[str(journal / "context.json")]
    context = io.document(context_raw)
    state = launch._state(io.document(seen[str(journal / "state.json")]), io.sha(ticket_raw))
    io.require(
        ticket["attempt_id"] == plan["attempt_id"]
        and ticket["boot_id"] == binding["boot_id"]
        and ticket["owner_generation"] == binding["generation"]
        and ticket["source_sha256"] == plan["candidate"]["consumer_sha256"]
        and ticket["plan_ref"]["sha256"] == io.sha(io.canonical(plan))
        and ticket["context_sha256"] == io.sha(context_raw)
        and context["schema"] == "autostop.j1.cold-crm-entry-context.v1"
        and context["role"] == "crm-embedded"
        and context["plan_ref"] == ticket["plan_ref"]
        and context["spec_ref"] == plan["cold_crm_spec_ref"]
        and context["package_ref"] == plan["cold_authority_package_ref"]
        and context["epoch_ref"] == plan["dispatch_epoch_ref"]
        and state["execution_possible"] is True,
        "cold_archive_crm_entry_binding",
    )
    io.require(
        events
        == [str(journal / "events" / f"{n:020d}.json") for n in range(1, state["sequence"] + 1)],
        "cold_archive_crm_sequence",
    )
    previous = launch._initial(io.sha(ticket_raw))
    for path in events:
        event = io.document(seen[path])
        io.require(
            set(event) == {"schema", "state"}
            and event["schema"] == "autostop.j1.forward-launch-event.v1",
            "cold_archive_crm_event",
        )
        launch._successor(previous, event["state"], ticket=ticket)
        previous = event["state"]
    io.require(previous == state, "cold_archive_crm_history")
    launch._verify_acceptance(
        ticket,
        io.sha(ticket_raw),
        state["acceptance"],
        0,
        reader=lambda path, _owner: seen[str(path)],
    )
    return context, state


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


def _fixture_parent_history(body, plan, baseline, package, read):
    """Replay captured private S/A/C/E; never infer production admission."""
    cold.fields(
        body,
        {
            "schema",
            "scope",
            "request_ref",
            "context_ref",
            "acceptance_ref",
            "journal_files",
            "systemd_admission_claimed",
        },
        "archive_fixture_parent_fields",
    )
    binding = package["binding"]
    io.require(
        package["scope"] == body["scope"] == "native_fixture"
        and body["systemd_admission_claimed"] is False
        and body["request_ref"] == baseline["controller_request_ref"],
        "cold_archive_fixture_parent_scope",
    )
    request = io.document(read(body["request_ref"]))
    cold.fields(
        request,
        {"schema", "scope", "binding", "source", "consumer_sha256", "production_native_acceptance"},
        "archive_fixture_request_fields",
    )
    io.require(
        request["schema"] == "autostop.j1.private-parent-request.v1"
        and request["scope"] == "native_fixture"
        and request["production_native_acceptance"] is False
        and request["binding"] == binding
        and request["source"] == {key: plan["candidate"][key] for key in ("revision", "tree")}
        and request["consumer_sha256"]
        == plan["candidate"]["consumer_sha256"]
        == cold.digest(baseline["source_consumers"]),
        "cold_archive_fixture_request_binding",
    )
    context = io.document(read(body["context_ref"]))
    cold.fields(
        context,
        {"schema", "request_ref", "actor", "identity", "deployment_lock_identity"},
        "archive_fixture_parent_context_fields",
    )
    directory = PurePosixPath(body["request_ref"]["path"]).parent
    io.require(
        context["schema"] == "autostop.j1.private-parent-context.v1"
        and context["request_ref"] == body["request_ref"]
        and body["context_ref"]["path"] == str(directory / "context.json")
        and body["acceptance_ref"]["path"] == str(directory / "native-acceptance.json")
        and context["deployment_lock_identity"] == binding["deployment_lock_identity"]
        and type(body["journal_files"]) is list
        and 3 <= len(body["journal_files"]) <= 130,
        "cold_archive_fixture_parent_context",
    )
    names = {}
    for reference in body["journal_files"]:
        cold.reference(reference)
        path = PurePosixPath(reference["path"])
        io.require(path.is_relative_to(directory / "journal"), "cold_archive_fixture_journal_path")
        name = path.relative_to(directory / "journal").as_posix()
        io.require(name not in names, "cold_archive_fixture_journal_duplicate")
        names[name] = reference
    io.require(
        {"ticket.json", "state.json"} <= names.keys(), "cold_archive_fixture_journal_missing"
    )
    ticket_raw = read(names["ticket.json"])
    ticket = launch.validate_ticket(io.document(ticket_raw))
    current = launch._state(io.document(read(names["state.json"])), io.sha(ticket_raw))
    io.require(
        set(names)
        == {
            "ticket.json",
            "state.json",
            *("events/" + f"{number:020d}.json" for number in range(1, current["sequence"] + 1)),
        }
        and current["execution_possible"] is True
        and ticket["plan_ref"] == body["request_ref"]
        and ticket["context_sha256"] == body["context_ref"]["sha256"]
        and ticket["source_sha256"] == request["consumer_sha256"]
        and all(
            ticket[key] == binding[other]
            for key, other in (
                ("attempt_id", "attempt_id"),
                ("boot_id", "boot_id"),
                ("owner_generation", "generation"),
            )
        ),
        "cold_archive_fixture_parent_ticket",
    )
    previous = launch._initial(io.sha(ticket_raw))
    for number in range(1, current["sequence"] + 1):
        event = io.document(read(names["events/" + f"{number:020d}.json"]))
        cold.fields(event, {"schema", "state"}, "archive_fixture_parent_event_fields")
        io.require(
            event["schema"] == "autostop.j1.forward-launch-event.v1",
            "cold_archive_fixture_parent_event",
        )
        launch._successor(previous, event["state"], ticket=ticket)
        previous = event["state"]
    io.require(previous == current, "cold_archive_fixture_parent_history")
    acceptance = current["acceptance"]
    io.require(
        acceptance["evidence_ref"] == body["acceptance_ref"]
        and context["identity"]
        == {key: acceptance[key] for key in ("pid", "start_ticks", "invocation_id")}
        and type(context["actor"]) is dict
        and all(context["actor"].get(key) == acceptance[key] for key in ("pid", "start_ticks")),
        "cold_archive_fixture_parent_incarnation",
    )
    launch._verify_acceptance(
        ticket,
        io.sha(ticket_raw),
        acceptance,
        0,
        # This exact reference was matched to the receipt above. A restoring
        # host may format Path('/captured/...') with Windows separators.
        reader=lambda _path, _owner: read(body["acceptance_ref"]),
    )
    return body["acceptance_ref"]


def _controller_history(plan, baseline, package, read):
    reference = baseline["controller_launch_ref"]
    body = io.document(read(reference))
    io.require(type(body) is dict, "cold_archive_controller_document")
    if body.get("schema") == "autostop.j1.private-parent-execution.v1":
        return _fixture_parent_history(body, plan, baseline, package, read)
    from scripts import j1_forward_cold_controller_native

    capsule = j1_forward_cold_controller_native.validate_capsule(reference, read_reference=read)
    io.require(
        capsule["request_ref"] == baseline["controller_request_ref"],
        "cold_archive_controller_request",
    )
    return capsule["files"]["native-acceptance.json"]


def _crm_parent(context, crm_state, baseline, parent_a, read):
    io.require(
        context["request_ref"] == baseline["controller_request_ref"]
        and context["parent_acceptance_ref"]["sha256"] == parent_a["sha256"],
        "cold_archive_crm_parent_acceptance",
    )
    accepted_parent = io.document(read(parent_a))
    io.require(
        all(
            crm_state["acceptance"][key] == accepted_parent[key]
            for key in ("pid", "start_ticks", "invocation_id")
        ),
        "cold_archive_crm_parent_incarnation",
    )


def collect(
    plan,
    *,
    root,
    stage,
    capture_clock,
    read_reference=None,
    read_file=None,
    list_directory=None,
    codec=3,
    _captured_packages=None,
):
    """Pure byte semantics when supplied restored readers; no live permits."""
    reader = _ProtectedReader().read_reference if read_reference is None else read_reference
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
    snapshot = _ReadSnapshot(reader, _version if list_directory is None else None, codec=codec)
    if _captured_packages is not None:
        io.require(type(_captured_packages) is cold._CapturedPackages, "captured_package_reader")
        _captured_packages.bind_clock(capture_clock)
    snapshot._captured_packages = _captured_packages
    seen, pending = (
        snapshot.files,
        [
            plan["cold_authority_package_ref"],
            plan["dispatch_epoch_ref"],
            plan["cold_crm_spec_ref"],
            plan["baseline_ref"],
        ],
    )

    read, deferred, visited = snapshot.read, {}, set()

    def walk(*, binary=False):
        while pending:
            reference = pending.pop()
            if reference["path"] in visited:
                snapshot.captured(reference)
                continue
            if not reference["path"].endswith(".json"):
                previous = deferred.setdefault(reference["path"], reference)
                io.require(previous == reference, "cold_archive_reference_alias")
                continue
            raw = read(reference)
            visited.add(reference["path"])
            pending.extend(_references(io.document(raw)))
        if binary:
            for path, reference in deferred.items():
                snapshot.captured(reference)
                visited.add(path)

    walk()
    package_header = io.document(read(plan["cold_authority_package_ref"]))
    kernel_header = io.document(read(package_header["kernel_ref"]))
    io.require(type(kernel_header) is dict, "cold_archive_kernel_document")
    snapshot.kernel_v2 = kernel_header.get("schema") == "autostop.j1.cold-kernel.v2"
    if codec == 5 and snapshot.kernel_v2:
        # Conditional transport accounting is not acceptance: the unchanged
        # package validator below still verifies the complete capsule and all
        # policies before this function may return any captured graph.
        snapshot.runtime_aliases = _interpreter_aliases(package_header, read)
    baseline = io.document(read(plan["baseline_ref"]))
    if baseline.get("controller_launch_ref") is not None:
        parent_a = _controller_history(plan, baseline, package_header, read)
    value = cold.validate_package(
        plan["cold_authority_package_ref"],
        read_reference=read,
        now_ns=capture_clock["monotonic_ns"],
    )
    package, binding = value["package"], value["package"]["binding"]
    io.require(not snapshot.runtime_blob_paths or snapshot.kernel_v2, "cold_archive_runtime_kernel")
    walk(binary=True)
    if package["scope"] == "production_native":
        io.require(
            baseline.get("controller_launch_ref") is not None
            and baseline.get("qualification_ref") is not None,
            "cold_archive_production_controller_evidence",
        )
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
        else:
            io.require(current["dispatch_possible"] is False, "cold_archive_closed_slot_dispatched")
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
            *("accepted/" + unit + ".json" for unit in cold.ALLOWED),
        ):
            path = str(directory / name)
            raw = file_reader(path, MAX_FILE_BYTES)
            pending.append({"path": path, "sha256": io.sha(raw)})
        walk(binary=True)
        context, crm_state = _crm_entry(
            directory, plan, binding, read=read, file_reader=file_reader, listing=listing, seen=seen
        )
        pending.extend(_references(context))
        if baseline.get("controller_launch_ref") is not None:
            _crm_parent(context, crm_state, baseline, parent_a, read)
        first = io.document(read(next(iter(epoch["runtime_bindings"].values()))))
        fence = io.document(read(first["ingress_fence_ref"]))
        raw = file_reader(fence["state_path"], MAX_FILE_BYTES)
        dispatch_ref = {"path": fence["state_path"], "sha256": io.sha(raw)}
        dispatch = io.document(read(dispatch_ref))
        io.require(
            dispatch["state"] == "open"
            and dispatch["admission_ref"] is not None
            and all(dispatch[key] == epoch[key] for key in epochs.COMMON),
            "cold_archive_terminal_dispatch",
        )
        pending.extend(_references(dispatch))
        # Independent signatures bind inspection hashes, not their paths. Keep
        # the controller's exact local inspection links and full evidence graph.
        if package["scope"] == "production_native":
            attempt = PurePosixPath(plan["baseline_ref"]["path"]).parent
            for kind in ("offhost_restore", "native_acceptance", "runtime_acceptance"):
                path = str(attempt / ("proof-binding-" + kind + ".json"))
                raw = file_reader(path, MAX_FILE_BYTES)
                pending.append({"path": path, "sha256": io.sha(raw)})
        walk(binary=True)
        completed = io.document(seen[str(directory / "complete.json")])
        io.require(
            completed.get("old_hold_released") is False
            and completed.get("old_barriers_opened") is False,
            "cold_archive_old_writer_permission",
        )
    # Old origin and public review key are part of this same bounded graph.
    captured_baseline = io.document(read(plan["baseline_ref"]))
    cold_state._origin_references(captured_baseline, read_reference=read)
    io.require(
        captured_baseline["native"]["cold_authority_package_ref"]
        == plan["cold_authority_package_ref"],
        "cold_recovery_native_refs",
    )
    # This exact package was fully validated above at this captured clock.
    # Repeat the origin binding checks, not another decompression of its bytes.
    cold_state._origin_binding(captured_baseline, value)
    snapshot.recheck()
    if codec == 5:
        io.require(
            snapshot.runtime_aliases == _interpreter_aliases(package, read)
            if snapshot.kernel_v2
            else not snapshot.runtime_aliases,
            "cold_archive_alias_derivation_changed",
        )
    graph = CapturedGraph(
        {path: raw for path, raw in seen.items() if not control_path(path, root)},
        snapshot.runtime_blob_paths,
        snapshot.runtime_aliases,
    )
    graph.runtime_alias_versions = {
        path: snapshot.versions[path]
        for path in snapshot.runtime_aliases
        if path in snapshot.versions
    }
    graph.source_versions = {
        path: snapshot.versions[path] for path in graph if path in snapshot.versions
    }
    return graph
