"""A bounded cold-request profile of the existing native S/A/C/E adapter.

Request E permits preparation under one continuously held D. It is distinct
from all six application entries and the final CRM replacement permission,
which additionally require the sealed cold plan and signed off-host restore.
"""

from __future__ import annotations

import socket
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_io as io
from scripts import j1_forward_launch_native as native
from scripts import j1_forward_launch_permission as journal
from scripts import j1_forward_state as state

SCHEMA = "autostop.j1.cold-controller-request.v1"
CONTEXT_SCHEMA = "autostop.j1.cold-controller-launch-context.v1"
REQUESTS = Path("/var/lib/autostop-crm-forward/cold-requests")
JOURNALS = Path("/var/lib/autostop-crm-forward/cold-controller-launch")
FORWARD = native.CRM / "scripts/j1_forward_cold_release.py"
FIELDS = {
    "schema",
    "scope",
    "attempt_id",
    "host",
    "boot_id",
    "owner_generation",
    "source",
    "candidate",
    "candidate_manager_sha",
    "inputs_ref",
    "ci_ref",
    "qualification_ref",
    "created_at",
    "deadline_utc",
    "runtime_seconds",
    "old_writer_allowed",
}


def validate_request(request, *, now=None):
    cold.fields(request, FIELDS, "controller_request_fields")
    cold.need(
        request["schema"] == SCHEMA and request["scope"] == "bounded_cold_prepare",
        "controller_request_scope",
    )
    cold.need(
        all(cold._uuid(request[key]) for key in ("attempt_id", "boot_id", "owner_generation")),
        "controller_request_identity",
    )
    cold.need(
        request["old_writer_allowed"] is False
        and type(request["runtime_seconds"]) is int
        and request["runtime_seconds"] == 900,
        "controller_request_limits",
    )
    source, candidate = request["source"], request["candidate"]
    cold.fields(source, {"revision", "tree", "consumers"}, "controller_source_fields")
    cold.fields(
        candidate, {"revision", "tree", "image", "consumer_sha256"}, "controller_candidate_fields"
    )
    cold.need(
        all(
            type(source[key]) is str
            and cold.SHA1.fullmatch(source[key])
            and source[key] == candidate[key]
            for key in ("revision", "tree")
        )
        and type(request["candidate_manager_sha"]) is str
        and cold.SHA1.fullmatch(request["candidate_manager_sha"]),
        "controller_source_identity",
    )
    cold.need(
        type(source["consumers"]) is dict
        and 8 <= len(source["consumers"]) <= 80
        and native.COLD_SUPPLEMENTAL <= set(source["consumers"])
        and cold.digest(source["consumers"]) == candidate["consumer_sha256"],
        "controller_consumer_binding",
    )
    import re

    cold.need(
        type(candidate["image"]) is str
        and re.fullmatch(r"sha256:[0-9a-f]{64}", candidate["image"]),
        "controller_candidate_image",
    )
    for key in ("inputs_ref", "ci_ref", "qualification_ref"):
        cold.reference(request[key])
    cold.need(
        type(request["host"]) is str and 0 < len(request["host"]) <= 253, "controller_request_host"
    )
    start, end = state._timestamp(request["created_at"]), state._timestamp(request["deadline_utc"])
    clock = datetime.now(UTC) if now is None else now
    cold.need(
        start <= clock < end and 0 < (end - start).total_seconds() <= 600,
        "controller_request_window",
    )
    return request


def request_path(attempt):
    return REQUESTS / journal._uuid(attempt) / "request.json"


def worker_command(attempt):
    return [
        "/usr/bin/python3",
        "-I",
        "-S",
        "-B",
        str(FORWARD),
        "controller",
        "--attempt",
        journal._uuid(attempt),
    ]


def launch_command(attempt):
    return [
        "/usr/bin/systemd-run",
        "--quiet",
        "--wait",
        "--collect",
        "--unit=autostop-crm-forward-" + journal._uuid(attempt),
        "--property=Type=exec",
        "--property=RuntimeMaxSec=900",
        "--property=TimeoutStopSec=120",
        "--property=KillSignal=SIGTERM",
        "--property=ExecStopPost=/usr/bin/python3 -I -S -B "
        + str(FORWARD)
        + " settle --attempt "
        + attempt,
        "--setenv=LD_LIBRARY_PATH=" + native.WORKER_ENVIRONMENT["LD_LIBRARY_PATH"],
        *worker_command(attempt),
    ]


def _store(attempt):
    return journal.LaunchStore(JOURNALS / journal._uuid(attempt), owner=native.ROOT_UID)


def issue(reference, deployment_lock_identity):
    native._root()
    request = validate_request(io.document(io.referenced(reference)))
    attempt = request["attempt_id"]
    cold.need(
        reference["path"] == str(request_path(attempt))
        and request["host"] == socket.gethostname()
        and request["boot_id"] == native.boot(),
        "controller_request_location",
    )
    source_hash = native._sources(request["source"]["consumers"], cold=True)
    from scripts import j1_forward_cold_qualification

    j1_forward_cold_qualification.verify_qualification(
        request["qualification_ref"],
        {
            "revision": request["source"]["revision"],
            "tree": request["source"]["tree"],
            "consumer_sha256": source_hash,
        },
    )
    cold.need(deployment_lock_identity == native._lock_identity(), "controller_deployment_lock")
    for key in ("inputs_ref", "ci_ref", "qualification_ref"):
        io.referenced(request[key])
    context = {
        "schema": CONTEXT_SCHEMA,
        "plan_ref": reference,
        "consumers": request["source"]["consumers"],
        "deployment_lock_identity": deployment_lock_identity,
        "interpreter": native._file_digest(Path("/usr/bin/python3").resolve(strict=True)),
        "worker_argv": worker_command(attempt),
        "launch_argv": launch_command(attempt),
        "worker_cwd": "/",
        "launcher_cwd": str(native.CRM),
        "launcher_environment": native.ENVIRONMENT,
        "worker_environment": native.WORKER_ENVIRONMENT,
        "issuer": native.owner(),
    }
    now = time.time()
    ticket = {
        "schema": journal.SCHEMA,
        "attempt_id": attempt,
        "nonce": str(uuid.uuid4()),
        "plan_ref": reference,
        "source_sha256": source_hash,
        "context_sha256": journal.sha(journal.canonical(context)),
        "host": request["host"],
        "boot_id": request["boot_id"],
        "owner_generation": request["owner_generation"],
        "unit": "autostop-crm-forward-" + attempt + ".service",
        "not_before": now,
        "expires_at": state._timestamp(request["deadline_utc"]).timestamp(),
    }
    if not JOURNALS.exists():
        io.protected_directory(JOURNALS.parent, private=True)
        JOURNALS.mkdir(mode=0o700)
        journal._sync(JOURNALS.parent)
    store = _store(attempt)
    receipt = store.create(ticket)
    journal._new(store.root / "context.json", journal.canonical(context))
    validate(attempt)
    return {
        **receipt,
        "request_ref": reference,
        "ticket_ref": io.reference(store.root / "ticket.json"),
        "final_activation_authorized": False,
    }


def validate(attempt):
    native._root()
    store = _store(attempt)
    snapshot = native.coherent_snapshot(store)
    ticket = snapshot["ticket"]
    raw = journal._read(store.root / "context.json", native.ROOT_UID)
    cold.need(io.sha(raw) == ticket["context_sha256"], "controller_context_changed")
    context = io.document(raw)
    cold.fields(
        context,
        {
            "schema",
            "plan_ref",
            "consumers",
            "deployment_lock_identity",
            "interpreter",
            "worker_argv",
            "launch_argv",
            "worker_cwd",
            "launcher_cwd",
            "launcher_environment",
            "worker_environment",
            "issuer",
        },
        "controller_context_fields",
    )
    cold.need(
        context["schema"] == CONTEXT_SCHEMA
        and ticket["attempt_id"] == attempt
        and context["plan_ref"] == ticket["plan_ref"]
        and context["plan_ref"]["path"] == str(request_path(attempt))
        and context["worker_argv"] == worker_command(attempt)
        and context["launch_argv"] == launch_command(attempt)
        and context["worker_cwd"] == "/"
        and context["launcher_cwd"] == str(native.CRM)
        and context["launcher_environment"] == native.ENVIRONMENT
        and context["worker_environment"] == native.WORKER_ENVIRONMENT,
        "controller_context_scope",
    )
    request = validate_request(io.document(io.referenced(context["plan_ref"])))
    from scripts import j1_forward_cold_qualification

    j1_forward_cold_qualification.verify_qualification(
        request["qualification_ref"],
        {
            "revision": request["source"]["revision"],
            "tree": request["source"]["tree"],
            "consumer_sha256": request["candidate"]["consumer_sha256"],
        },
        replay=False,
    )
    cold.need(
        request["attempt_id"] == attempt
        and request["host"] == socket.gethostname()
        and request["boot_id"] == native.boot() == ticket["boot_id"]
        and request["owner_generation"] == ticket["owner_generation"]
        and request["source"]["consumers"] == context["consumers"]
        and native._sources(context["consumers"], cold=True) == ticket["source_sha256"]
        and native._file_digest(Path("/usr/bin/python3").resolve(strict=True))
        == context["interpreter"]
        and native._lock_identity() == context["deployment_lock_identity"],
        "controller_binding_changed",
    )
    return store, snapshot, context


class Admission(native.Admission):
    def _validate(self):
        return validate(self.attempt)


def launch(attempt):
    store, snapshot, context = validate(attempt)
    return native.submit_reserved(store, snapshot, context, timeout=1100)


def revoke(attempt):
    store, snapshot, _context = validate(attempt)
    return native._transition(store, snapshot["ticket"], "revoke", {})


def capture_capsule(attempt, destination):
    """Retain exact E history; this is a historical cut, never fresh permission."""
    store, snapshot, context = validate(attempt)
    cold.need(snapshot["state"]["execution_possible"] is True, "controller_not_entered")
    cold.need(not destination.exists(), "controller_capsule_exists")
    destination.mkdir(mode=0o700)
    io.fsync_directory(destination.parent)
    with store.locked():
        current = store.read()
        cold.need(current == snapshot, "controller_capsule_changed")
        names = ["ticket.json", "context.json", "state.json", "native-acceptance.json"]
        names += [
            "events/" + f"{number:020d}.json"
            for number in range(1, snapshot["state"]["sequence"] + 1)
        ]
        copied = {}
        for number, name in enumerate(names):
            raw = journal._read(store.root / name, native.ROOT_UID)
            path = destination / f"{number:06d}.json"
            journal._new(path, raw)
            copied[name] = io.reference(path)
        cold.need(store.read() == current, "controller_capsule_changed")
    reference = io.write_new(
        destination / "manifest.json",
        {
            "schema": "autostop.j1.cold-controller-history-cut.v1",
            "attempt_id": attempt,
            "request_ref": context["plan_ref"],
            "captured_sequence": snapshot["state"]["sequence"],
            "files": copied,
            "current_authority_asserted": False,
        },
    )
    validate_capsule(reference, read_reference=io.referenced)
    return reference


def validate_capsule(reference, *, read_reference):
    body = io.document(read_reference(reference))
    cold.fields(
        body,
        {
            "schema",
            "attempt_id",
            "request_ref",
            "captured_sequence",
            "files",
            "current_authority_asserted",
        },
        "controller_capsule_fields",
    )
    cold.need(
        body["schema"] == "autostop.j1.cold-controller-history-cut.v1"
        and body["current_authority_asserted"] is False,
        "controller_capsule_schema",
    )
    request = io.document(read_reference(body["request_ref"]))
    validate_request(request, now=state._timestamp(request["created_at"]))
    from scripts import j1_forward_cold_qualification

    j1_forward_cold_qualification.validate_document(
        io.document(read_reference(request["qualification_ref"])),
        {
            "revision": request["source"]["revision"],
            "tree": request["source"]["tree"],
            "consumer_sha256": request["candidate"]["consumer_sha256"],
        },
    )
    files = {name: read_reference(ref) for name, ref in body["files"].items()}
    ticket_raw = files["ticket.json"]
    ticket = journal.validate_ticket(io.document(ticket_raw))
    context = io.document(files["context.json"])
    current = journal._state(io.document(files["state.json"]), io.sha(ticket_raw))
    cold.need(
        set(files)
        == {
            "ticket.json",
            "context.json",
            "state.json",
            "native-acceptance.json",
            *("events/" + f"{number:020d}.json" for number in range(1, current["sequence"] + 1)),
        }
        and body["captured_sequence"] == current["sequence"]
        and ticket["attempt_id"] == body["attempt_id"] == request["attempt_id"]
        and ticket["plan_ref"] == body["request_ref"] == context["plan_ref"]
        and context["schema"] == CONTEXT_SCHEMA
        and ticket["context_sha256"] == io.sha(files["context.json"])
        and ticket["source_sha256"] == cold.digest(request["source"]["consumers"])
        and context["consumers"] == request["source"]["consumers"]
        and context["worker_argv"] == worker_command(body["attempt_id"])
        and context["launch_argv"] == launch_command(body["attempt_id"])
        and current["execution_possible"] is True,
        "controller_capsule_binding",
    )
    previous = journal._initial(io.sha(ticket_raw))
    for number in range(1, current["sequence"] + 1):
        event = io.document(files["events/" + f"{number:020d}.json"])
        cold.need(
            set(event) == {"schema", "state"}
            and event["schema"] == "autostop.j1.forward-launch-event.v1",
            "controller_capsule_event",
        )
        journal._successor(previous, event["state"], ticket=ticket)
        previous = event["state"]
    cold.need(previous == current, "controller_capsule_history")
    evidence = current["acceptance"]["evidence_ref"]
    raw = files["native-acceptance.json"]
    cold.need(io.sha(raw) == evidence["sha256"], "controller_capsule_acceptance")
    journal._verify_acceptance(
        ticket,
        io.sha(ticket_raw),
        current["acceptance"],
        0,
        reader=lambda path, _owner: raw if str(path) == evidence["path"] else b"",
    )
    return body
