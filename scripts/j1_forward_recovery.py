"""A new, bounded successor for one retained, failed forward transaction.

Old plan, pending, consumer bytes and failure evidence remain immutable. This
module never resets native J1 authority or treats earlier proofs as admission.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from scripts import j1_forward_io as io
from scripts import j1_forward_recovery_state as recovery_state
from scripts import j1_forward_state as state
from scripts.j1_forward_host import CRM, Host, manager_data, scheduler_counts

ROOT = Path("/var/lib/autostop-crm-forward")
ORIGINS = ROOT / "origins"
ATTEMPTS = ROOT / "attempts"
STATE = ROOT / "state"
BUNDLE_SCHEMA = "autostop.j1.crm-forward-origin-bundle.v2"
CODE_SCHEMA = "autostop.j1.crm-forward-recovery-code.v2"
HOLD_SCHEMA = "autostop.j1.crm-forward-recovery-hold.v2"
ORIGIN_FIELDS = {
    "nonce",
    "origin_pending_ref",
    "origin_plan_ref",
    "origin_closure_ref",
    "origin_scope_ref",
    "origin_bundle_ref",
    "origin_failure_ref",
    "recovery_code_ref",
    "ci_ref",
    "hold_ref",
    "deployment_lock_identity",
}


def _document(reference):
    return io.document(io.referenced(reference))


def _origin_documents(origin, *, root=None, read_reference=None, read_file=None):
    read_reference = io.referenced if read_reference is None else read_reference
    read_file = io.read_protected if read_file is None else read_file
    attempts, origins, state_root = (
        (ATTEMPTS, ORIGINS, STATE)
        if root is None
        else (
            PurePosixPath(root) / "attempts",
            PurePosixPath(root) / "origins",
            PurePosixPath(root) / "state",
        )
    )
    io.require(type(origin) is dict and set(origin) == ORIGIN_FIELDS, "recovery_origin_fields")
    io.require(state._hash(origin["nonce"]), "recovery_origin_nonce")
    plan = io.document(read_reference(origin["origin_plan_ref"]))
    state.validate_plan(plan, plan["base_sha256"])
    directory = attempts / plan["attempt_id"]
    paths = {
        "origin_pending_ref": state_root / "pending.json",
        "origin_plan_ref": directory / "plan.json",
        "origin_closure_ref": directory / "closure-owned.json",
        "origin_scope_ref": directory / "process-scope.json",
        "origin_failure_ref": directory / "failure.json",
        "origin_bundle_ref": origins / plan["attempt_id"] / "bundle.json",
    }
    io.require(
        all(origin[key]["path"] == str(path) for key, path in paths.items())
        and plan["baseline_ref"]["path"] == str(directory / "baseline.json"),
        "recovery_origin_paths",
    )
    retained = read_file(origins / plan["attempt_id"] / "pending.json")
    io.require(
        io.sha(retained)
        == origin["origin_pending_ref"]["sha256"]
        == origin["origin_plan_ref"]["sha256"]
        and retained == io.canonical(plan),
        "recovery_origin_pending_archive",
    )
    return plan, {
        "plan": plan,
        "baseline": io.document(read_reference(plan["baseline_ref"])),
        "closure": io.document(read_reference(origin["origin_closure_ref"])),
        "scope": io.document(read_reference(origin["origin_scope_ref"])),
        "failure": io.document(read_reference(origin["origin_failure_ref"])),
        "bundle": io.document(read_reference(origin["origin_bundle_ref"])),
    }


def validate_origin(origin, *, root=None, read_reference=None, read_file=None):
    """Validate retained provenance without depending on consumed live pending."""
    from scripts import j1_forward_process_scope as process_scope

    read_reference = io.referenced if read_reference is None else read_reference
    origins = ORIGINS if root is None else PurePosixPath(root) / "origins"
    plan, result = _origin_documents(
        origin, root=root, read_reference=read_reference, read_file=read_file
    )
    baseline, closure, scope, bundle = (
        result[key] for key in ("baseline", "closure", "scope", "bundle")
    )
    process_scope.validate_scope_shape(scope)
    io.require(
        baseline.get("attempt_id") == plan["attempt_id"]
        and baseline["native"]["base_sha256"] == plan["base_sha256"]
        and closure.get("attempt_id") == scope["attempt_id"] == plan["attempt_id"]
        and closure.get("process_scope_ref") == origin["origin_scope_ref"]
        and closure.get("baseline_sha256")
        == scope["native_sha256"]
        == io.sha(io.canonical(baseline["native"]))
        and closure.get("consumer_sha256")
        == scope["consumer_sha256"]
        == plan["candidate"]["consumer_sha256"]
        and result["failure"].get("ok") is False
        and result["failure"].get("plan_ref") == origin["origin_plan_ref"]
        and result["failure"].get("production_restore_attempted") is False,
        "recovery_origin_binding",
    )
    io.require(
        type(bundle) is dict
        and set(bundle)
        == {
            "schema",
            "origin_attempt_id",
            "origin_plan_ref",
            "origin_baseline_ref",
            "source_revision",
            "source_tree",
            "source_consumers",
            "consumer_sha256",
            "files",
        }
        and bundle["schema"] == BUNDLE_SCHEMA
        and bundle["origin_attempt_id"] == plan["attempt_id"]
        and bundle["origin_plan_ref"] == origin["origin_plan_ref"]
        and bundle["origin_baseline_ref"] == plan["baseline_ref"]
        and bundle["source_revision"] == plan["candidate"]["revision"]
        and bundle["source_tree"] == plan["candidate"]["tree"]
        and bundle["source_consumers"] == baseline["source_consumers"]
        and bundle["consumer_sha256"]
        == plan["candidate"]["consumer_sha256"]
        == io.sha(io.canonical(bundle["source_consumers"]))
        and set(bundle["files"]) == set(bundle["source_consumers"]),
        "recovery_origin_bundle",
    )
    for name, digest in bundle["source_consumers"].items():
        io.require(
            re.fullmatch(r"scripts/j1_forward_[a-z_]+\.py", name), "recovery_origin_consumer_path"
        )
        reference = bundle["files"][name]
        io.require(
            reference
            == {"path": str(origins / plan["attempt_id"] / "consumers" / name), "sha256": digest},
            "recovery_origin_consumer_binding",
        )
        read_reference(reference)
    return result


def _write_bytes(path, raw):
    io.protected_directory(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    io.fsync_directory(path.parent)
    io.require(io.read_protected(path, max(len(raw), 1)) == raw, "recovery_copy_readback")
    return io.reference(path)


def no_old_controller(attempt):
    """Do not race a late ExecStopPost belonging to the failed transaction."""
    script = str(CRM / "scripts/j1_forward_release.py").encode()
    for root in Path("/proc").iterdir():
        if not root.name.isdecimal() or int(root.name) == os.getpid():
            continue
        try:
            argv = (root / "cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError):
            continue
        io.require(
            not (
                script in argv
                and attempt.encode() in argv
                and any(operation in argv for operation in (b"run", b"settle", b"apply"))
            ),
            "recovery_original_controller_present",
        )


def seal_origin(attempt):
    """Retain exact failed-consumer bytes before fast-forwarding its checkout."""
    from scripts import j1_forward_release as release

    io.require(os.geteuid() == 0, "native_root_required")
    directory = release.attempt_directory(attempt)
    plan_ref = io.reference(directory / "plan.json")
    plan = _document(plan_ref)
    state.validate_plan(plan, plan["base_sha256"])
    baseline = _document(plan["baseline_ref"])
    with release.deployment_lock(baseline["deployment_lock_identity"]):
        no_old_controller(attempt)
        store = state.Store(STATE, plan["base_sha256"])
        io.require(
            store.pending() == plan and store.active_plan()["recovery_ref"] is None,
            "recovery_original_pending_required",
        )
        io.require(
            _document(io.reference(directory / "failure.json")).get("ok") is False,
            "recovery_original_failure_required",
        )
        consumers = release.source_consumers()
        io.require(
            consumers == baseline["source_consumers"]
            and io.sha(io.canonical(consumers)) == plan["candidate"]["consumer_sha256"],
            "recovery_original_source_changed",
        )
        for folder in (
            ORIGINS,
            ORIGINS / attempt,
            ORIGINS / attempt / "consumers",
            ORIGINS / attempt / "consumers/scripts",
        ):
            if not folder.exists():
                folder.mkdir(mode=0o700)
                io.fsync_directory(folder.parent)
            io.protected_directory(folder, private=True)
        target = ORIGINS / attempt
        pending_raw = io.read_protected(store.pending_path)
        io.require(pending_raw == io.referenced(plan_ref), "recovery_original_pending_bytes")
        if not (target / "pending.json").exists():
            _write_bytes(target / "pending.json", pending_raw)
        io.require(
            io.read_protected(target / "pending.json") == pending_raw,
            "recovery_origin_pending_archive",
        )
        files = {}
        for name, digest in consumers.items():
            raw = io.read_protected(CRM / name)
            io.require(io.sha(raw) == digest, "recovery_original_source_changed")
            destination = target / "consumers" / name
            if not destination.exists():
                _write_bytes(destination, raw)
            files[name] = io.reference(destination)
            io.require(files[name]["sha256"] == digest, "recovery_origin_consumer_binding")
        value = {
            "schema": BUNDLE_SCHEMA,
            "origin_attempt_id": attempt,
            "origin_plan_ref": plan_ref,
            "origin_baseline_ref": plan["baseline_ref"],
            "source_revision": plan["candidate"]["revision"],
            "source_tree": plan["candidate"]["tree"],
            "source_consumers": consumers,
            "consumer_sha256": plan["candidate"]["consumer_sha256"],
            "files": files,
        }
        if not (target / "bundle.json").exists():
            io.write_new(target / "bundle.json", value)
        reference = io.reference(target / "bundle.json")
        io.require(_document(reference) == value, "recovery_origin_bundle_changed")
        io.confirm_durable(reference)
        return {"ok": True, "origin_attempt_id": attempt, "origin_bundle_ref": reference}


class RecoveryHost(Host):
    """Use the original owned hold and original desired-state baseline."""

    def __init__(self, native, directory, *, host_baseline):
        super().__init__(native, directory, host_baseline=host_baseline)
        self.origin = validate_origin(native["recovery_origin"])
        self.origin_attempt = self.origin["plan"]["attempt_id"]

    def _baseline(self):
        expected = self.origin["baseline"]["host"]
        io.require(self.host_baseline == expected, "recovery_host_baseline_changed")
        io.referenced(expected["coordinated_manifest"])
        return expected

    def manager(self, operation, attempt=None):
        if attempt is not None:
            io.require(
                attempt in {self.directory.name, self.origin_attempt}, "recovery_hold_attempt"
            )
            attempt = self.origin_attempt
        return super().manager(operation, attempt)

    def validate_manager(self, value, attempt, *, held):
        io.require(attempt in {self.directory.name, self.origin_attempt}, "recovery_hold_attempt")
        return super().validate_manager(value, self.origin_attempt, held=held)

    def begin_drain(self, attempt_id, baseline):
        io.require(
            attempt_id == self.directory.name and baseline == self._baseline(),
            "recovery_host_baseline_changed",
        )
        held = self.manager("hold", attempt_id)
        status = self.manager("status")
        self.validate_manager(status, attempt_id, held=True)
        counts = scheduler_counts(status)
        io.require(
            counts["active_leases"] == counts["outbox_sending"] == 0, "scheduler_not_drained"
        )
        return {
            "ok": True,
            "hold": held,
            "scheduler_counts": counts,
            "work": self.pause_work(),
            "closure_continuity_proven": False,
        }


def envelope_for(plan, baseline):
    return {
        "schema": recovery_state.RECOVERY_SCHEMA,
        "recovery_id": plan["attempt_id"],
        **baseline["recovery_origin"],
        "plan_ref": io.reference(ATTEMPTS / plan["attempt_id"] / "plan.json"),
    }


class RecoveryStore:
    """Adapt common release stages to the explicit v2 state transaction."""

    def __init__(self, plan, baseline):
        self.plan = plan
        self.envelope = envelope_for(plan, baseline)
        self.store = state.Store(STATE, plan["base_sha256"])
        self.head_path = self.store.head_path

    def pending(self):
        active = self.store.active_plan()
        if active is None:
            return None
        if active["recovery_ref"] is None:
            io.require(
                active["plan"] == _document(self.envelope["origin_plan_ref"]),
                "recovery_original_pending_changed",
            )
            return None
        io.require(
            active["plan"] == self.plan
            and active["recovery_ref"] == self.store._recovery_ref(self.envelope),
            "state_recovery_mismatch",
        )
        return self.plan

    def begin(self, plan):
        io.require(plan == self.plan, "state_recovery_mismatch")
        return self.store.begin_recovery(self.envelope)

    def head(self):
        return self.store.head()

    def commit(self, plan, proofs):
        io.require(plan == self.plan, "state_recovery_mismatch")
        return self.store.commit_recovery(self.envelope, proofs)

    def finish(self, plan):
        io.require(plan == self.plan, "state_recovery_mismatch")
        return self.store.finish_recovery(self.envelope)

    def confirm_finished(self, plan):
        io.require(plan == self.plan, "state_recovery_mismatch")
        with self.store._locked():
            io.require(
                self.store._active_recovery() is None and self.store._pending() is None,
                "state_terminal_pending_present",
            )
            reference = self.store._committed_recovery(self.envelope)
            io.require(
                self.store._active_recovery() is None and self.store._pending() is None,
                "state_terminal_pending_changed",
            )
            return reference


def store_for(plan, baseline=None):
    if baseline is None:
        baseline = _document(plan["baseline_ref"])
    return (
        RecoveryStore(plan, baseline)
        if "recovery_origin" in baseline
        else state.Store(STATE, plan["base_sha256"])
    )


def response_evidence(plan, baseline):
    """Private inspect payload; raw customer data is never included."""
    envelope = envelope_for(plan, baseline)
    store = state.Store(STATE, plan["base_sha256"])
    reference = store._recovery_ref(envelope)
    io.require(_document(reference) == envelope, "state_recovery_mismatch")
    evidence = {
        name.removesuffix("_ref"): _document(envelope[name])
        for name in (
            "origin_plan_ref",
            "origin_closure_ref",
            "origin_scope_ref",
            "origin_bundle_ref",
            "origin_failure_ref",
            "recovery_code_ref",
            "ci_ref",
            "hold_ref",
        )
    }
    evidence["origin_pending"] = io.document(
        io.read_protected(ORIGINS / evidence["origin_plan"]["attempt_id"] / "pending.json")
    )
    evidence["origin_baseline"] = _document(evidence["origin_plan"]["baseline_ref"])
    evidence["hold_status"] = _document(evidence["hold"]["status_ref"])
    return {"envelope": envelope, "envelope_ref": reference, "evidence": evidence}


def prepare(image, ci_run_id, origin_attempt):
    """Refuse unprovable topology before acquiring any new writer effects."""
    from scripts import j1_forward_authority as authority
    from scripts import j1_forward_closure as closure
    from scripts import j1_forward_marker as marker
    from scripts import j1_forward_recovery_scope as scope_module
    from scripts import j1_forward_release as release

    io.require(os.geteuid() == 0, "native_root_required")
    old_directory = release.attempt_directory(origin_attempt)
    old_plan_ref = io.reference(old_directory / "plan.json")
    old_plan = _document(old_plan_ref)
    old_baseline = _document(old_plan["baseline_ref"])
    store = state.Store(STATE, old_plan["base_sha256"])
    with release.deployment_lock(old_baseline["deployment_lock_identity"]):
        no_old_controller(origin_attempt)
        active = store.active_plan()
        io.require(
            active is not None and active["recovery_ref"] is None and active["plan"] == old_plan,
            "recovery_original_pending_required",
        )
        io.require(
            store.head()["sequence"] + 1 == old_plan["sequence"], "recovery_original_predecessor"
        )
        parent = marker.prepare()
        # This full scope observation is deliberately before new plan/state,
        # marker, hold/pause, stop or signal effects. Unknown peers/backlog are
        # not a drain waiver and cannot become a recovery admission.
        prior_scope = _document(io.reference(old_directory / "process-scope.json"))
        scope_module.OBSERVER.snapshot(
            scope_module.native_profile(old_baseline["native"]), origin=prior_scope
        )
        attempt = str(uuid.uuid4())
        directory = ATTEMPTS / attempt
        directory.mkdir(mode=0o700)
        io.fsync_directory(ATTEMPTS)
        host = Host(old_baseline["native"], directory)
        source = host.source()
        ci = release.verify_ci(ci_run_id, source)
        candidate = host.image(image)
        io.require(candidate == {**source, "image": image}, "candidate_image_source_mismatch")
        io.require(
            host.container() == old_baseline["previous_container"],
            "recovery_original_container_changed",
        )
        consumers = release.source_consumers()
        code_ref = io.write_new(
            directory / "recovery-code.json",
            {
                "schema": CODE_SCHEMA,
                **source,
                "consumer_sha256": io.sha(io.canonical(consumers)),
                "source_consumers": consumers,
            },
        )
        ci_ref = io.write_new(directory / "quality.json", ci)
        status = host.manager("status")
        hold = manager_data(status).get("global_hold", {})
        expected_hash = io.sha(("release-attempt:" + origin_attempt).encode())
        io.require(
            hold.get("enabled") is True
            and hold.get("reason") == "release"
            and hold.get("attempt_hash") == expected_hash,
            "recovery_original_hold_required",
        )
        counts = scheduler_counts(status)
        io.require(
            counts["active_leases"] == counts["outbox_sending"] == 0, "scheduler_not_drained"
        )
        status_ref = io.write_new(directory / "recovery-hold-status.json", status)
        hold_ref = io.write_new(
            directory / "recovery-hold.json",
            {
                "schema": HOLD_SCHEMA,
                "origin_attempt_id": origin_attempt,
                "attempt_hash": expected_hash,
                "reason": "release",
                "enabled": True,
                "scheduler_counts": counts,
                "status_ref": status_ref,
            },
        )
        origin = {
            "nonce": uuid.uuid4().hex + uuid.uuid4().hex,
            "origin_pending_ref": io.reference(store.pending_path),
            "origin_plan_ref": old_plan_ref,
            "origin_closure_ref": io.reference(old_directory / "closure-owned.json"),
            "origin_scope_ref": io.reference(old_directory / "process-scope.json"),
            "origin_failure_ref": io.reference(old_directory / "failure.json"),
            "origin_bundle_ref": io.reference(ORIGINS / origin_attempt / "bundle.json"),
            "recovery_code_ref": code_ref,
            "ci_ref": ci_ref,
            "hold_ref": hold_ref,
            "deployment_lock_identity": old_baseline["deployment_lock_identity"],
        }
        old = validate_origin(origin)
        native = authority.snapshot_recovery(
            old["baseline"]["native"], origin, release.projection(old["closure"])
        )
        baseline = {
            **{key: value for key, value in old_baseline.items() if key != "source_consumers"},
            "attempt_id": attempt,
            "native": native,
            "source_consumers": consumers,
            "maintenance_parent": parent,
            "ci": ci,
            "recovery_origin": origin,
        }
        io.require(io.reference(release.KEY) == baseline["review_key_ref"], "review_key_changed")
        baseline_ref = io.write_new(directory / "baseline.json", baseline)
        now = datetime.now(UTC)
        plan = {
            **old_plan,
            "attempt_id": attempt,
            "baseline_ref": baseline_ref,
            "candidate": {**candidate, "consumer_sha256": io.sha(io.canonical(consumers))},
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "deadline_utc": (now + timedelta(seconds=release.MAX_WINDOW))
            .isoformat()
            .replace("+00:00", "Z"),
        }
        state.validate_plan(plan, plan["base_sha256"])
        plan_ref = io.write_new(directory / "plan.json", plan)
        scope = scope_module.capture(
            old["scope"],
            origin["origin_scope_ref"],
            native,
            attempt_id=attempt,
            attempt_dir=directory,
            consumer_sha256=plan["candidate"]["consumer_sha256"],
        )
        scope_ref = closure.RUNNER.save_scope(directory, scope)
        ownership = {
            "schema": closure.SCHEMA,
            "attempt_id": attempt,
            "attempt_dir": str(directory),
            "boot_id": scope["boot_id"],
            "dropins": closure._paths(),
            "bytes_sha256": io.sha(closure.DENY_BYTES),
            "warm_actors": native["warm_actors"],
            "baseline_sha256": io.sha(io.canonical(native)),
            "source_roots": closure.RUNNER.source_roots(),
            "consumer_sha256": plan["candidate"]["consumer_sha256"],
            "freeze_actors": scope["freeze_actors"],
            "process_scope_ref": scope_ref,
        }
        closure.RUNNER.save(directory, ownership)
        closure.RUNNER.control_bindings(ownership)
        envelope = envelope_for(plan, baseline)
        store._envelope(envelope)
        release.remaining(plan)
        release.progress(directory, "prepared", plan_ref=plan_ref)
        launch_ticket = release.launch_native.issue(plan_ref, consumers, baseline)
        return {
            "ok": True,
            "attempt_id": attempt,
            "plan_ref": plan_ref,
            "deadline_utc": plan["deadline_utc"],
            "origin_attempt_id": origin_attempt,
            "launch_ticket": launch_ticket,
        }


def prepared_ownership(directory, baseline):
    from scripts import j1_forward_closure as closure
    from scripts import j1_forward_recovery_scope as scope

    owned = closure.RUNNER.load(directory)
    closure._validate(owned)
    current_scope = closure._scope(owned)
    io.require(
        current_scope["schema"] == scope.SCHEMA
        and owned["baseline_sha256"] == io.sha(io.canonical(baseline["native"])),
        "recovery_owned_closure_binding",
    )
    scope.verify(current_scope, frozen=False)
    return owned
