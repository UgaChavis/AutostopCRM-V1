"""Bounded, single-controller cold forward recovery.

The request entry grants preparation only. The same native process retains D
and the ingress descriptor through real capture, independent restore, final
plan issuance, seven consumers, durable COMMIT and finish. Original eight unit
barriers and the original scheduler hold are never removed here.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import signal
import socket
import stat
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import j1_forward_archive as archive
from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_backup
from scripts import j1_forward_cold_controller_native as parent
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_cold_marker as markers
from scripts import j1_forward_cold_native as native
from scripts import j1_forward_cold_state as transactions
from scripts import j1_forward_io as io
from scripts import j1_forward_proofs as signatures
from scripts import j1_forward_release as forward
from scripts import j1_forward_state as state
from scripts import j1_forward_variant as variant
from scripts.j1_forward_cold_host import ColdHost

PROVIDER_FIELDS = {
    "package_ref",
    "runtime_bindings",
    "origin",
    "host_baseline",
    "previous_container",
    "review_key_ref",
    "ci",
    "maintenance_parent",
}


def _write_once(path, body):
    if path.exists():
        io.require(io.read_protected(path) == io.canonical(body), "cold_immutable_conflict")
        return io.reference(path)
    return io.write_new(path, body)


def _progress(directory, phase, **fields):
    return io.atomic_replace(
        directory / "progress.json",
        {
            "phase": phase,
            "observed_at": forward.utc(),
            **fields,
        },
    )


def _load(attempt, *, fresh=True):
    directory = forward.attempt_directory(attempt)
    plan_ref = io.reference(directory / "plan.json")
    plan = io.document(io.referenced(plan_ref))
    transactions.validate_plan(plan, plan["base_sha256"])
    if fresh:
        forward.remaining(plan)
    baseline = io.document(io.referenced(plan["baseline_ref"]))
    io.require(
        baseline["schema"] == transactions.BASELINE_SCHEMA and baseline["attempt_id"] == attempt,
        "cold_baseline_binding",
    )
    host = ColdHost(baseline["native"], directory, host_baseline=baseline["host"])
    host.configure_cold(
        plan["dispatch_epoch_ref"], plan["cold_authority_package_ref"], plan["cold_crm_spec_ref"]
    )
    controller = lifecycle.Controller(
        plan["cold_authority_package_ref"], plan["dispatch_epoch_ref"]
    )
    return directory, plan_ref, plan, baseline, host, controller


def _native_observation(plan, controller, phase):
    value, _fence = controller.verify()
    if phase == "closed":
        controller._closed_slots()
    binding = value["package"]["binding"]
    observed = time.monotonic_ns()
    cold._binding(binding, now_ns=observed)
    return {
        "schema": "autostop.j1.cold-native-observation.v1",
        "scope": controller.provider.scope,
        "ok": True,
        "phase": phase,
        "attempt_id": plan["attempt_id"],
        "package_ref": plan["cold_authority_package_ref"],
        "epoch_ref": plan["dispatch_epoch_ref"],
        "binding_sha256": cold.digest(binding),
        "boot_id": binding["boot_id"],
        "observed_monotonic_ns": observed,
        "original_barriers_closed": True,
        "original_hold_retained": True,
        "old_writer_allowed": False,
        "candidate_running": phase == "activated",
        "all_six_policy_refs": value["package"]["policies"],
        "retired_scope_sha256": cold.digest(value["history"]["inventory"]),
    }


def _accepted(plan, controller):
    ready = controller.ready()
    accepted = ready["accepted"]
    raw = io.canonical(ready["crm"])
    result = {
        "schema": "autostop.j1.cold-seven-runtime-observation.v2"
        if controller.provider.scope == "production_native"
        else "autostop.j1.cold-seven-runtime-observation.v1",
        "attempt_id": plan["attempt_id"],
        "package_ref": plan["cold_authority_package_ref"],
        "epoch_ref": plan["dispatch_epoch_ref"],
        "accepted": accepted,
        "observations": {
            name: io.document(controller.provider.read_reference(ref))
            for name, ref in accepted.items()
        },
        "crm_ref": {"path": str(controller.root / "crm-accepted.json"), "sha256": io.sha(raw)},
        "crm": ready["crm"],
        "execution_acceptance_sha256": ready["acceptance_sha256"],
    }
    if controller.provider.scope == "production_native":
        result["telegram_frontiers"] = ready["telegram_frontiers"]
    return result


class Coordinator:
    def __init__(
        self,
        request_ref,
        request,
        producer,
        *,
        deployment_fd,
        root=forward.ROOT,
        archive_root=forward.CONTROL_ARCHIVES,
        host_factory=ColdHost,
    ):
        parent.validate_request(request)
        self.request_ref, self.request, self.producer = request_ref, request, producer
        self.root, self.archive_root, self.host_factory = (
            Path(root),
            Path(archive_root),
            host_factory,
        )
        self.provider, self.deployment_fd = producer.provider, deployment_fd
        self.directory = self.root / "attempts" / request["attempt_id"]
        self.controller = None
        self.phase = "closed"

    def prepare(self):
        prepared = self.producer.prepare()
        cold.fields(prepared, PROVIDER_FIELDS, "producer_result_fields")
        package_ref = prepared["package_ref"]
        package = io.document(self.provider.read_reference(package_ref))
        binding = package["binding"]
        cold.need(
            binding["attempt_id"] == self.request["attempt_id"]
            and binding["candidate_crm_sha"] == self.request["candidate"]["revision"]
            and binding["candidate_manager_sha"] == self.request["candidate_manager_sha"],
            "request_package_binding",
        )
        native.held_descriptor(
            native.controller(os.getpid()), self.deployment_fd, binding["deployment_lock_identity"]
        )
        remaining = (
            state._timestamp(self.request["deadline_utc"]) - datetime.now(UTC)
        ).total_seconds()
        cold.need(
            0 < remaining <= 600
            and binding["deadline_ns"] <= time.monotonic_ns() + int(remaining * 10**9),
            "request_package_deadline_extension",
        )
        epoch_ref = lifecycle.provision_epoch(
            package_ref, prepared["runtime_bindings"], provider=self.provider
        )
        controller = lifecycle.Controller(package_ref, epoch_ref, provider=self.provider)
        controller.stage()
        self.controller = controller
        io.protected_directory(self.directory, private=True)
        host = self.host_factory(
            {"cold_authority_package_ref": package_ref, "dispatch_epoch_ref": epoch_ref},
            self.directory,
            host_baseline=prepared["host_baseline"],
        )
        spec_ref = host.prepare_spec(
            self.request["candidate"], epoch_ref=epoch_ref, package_ref=package_ref
        )
        old = io.document(io.referenced(prepared["origin"]["origin_plan_ref"]))
        source = self.request["source"]
        code_ref = _write_once(
            self.directory / "recovery-code.json",
            {
                "schema": "autostop.j1.cold-recovery-source.v1",
                "revision": source["revision"],
                "tree": source["tree"],
                "source_consumers": source["consumers"],
                "consumer_sha256": self.request["candidate"]["consumer_sha256"],
            },
        )
        ci_ref = _write_once(self.directory / "quality.json", prepared["ci"])
        origin = {**prepared["origin"], "recovery_code_ref": code_ref, "ci_ref": ci_ref}
        capsule = parent.capture_capsule(
            self.request["attempt_id"], self.directory / "controller-launch-cut"
        )
        profile = j1_forward_cold_backup.engine(
            self.request["candidate_manager_sha"],
            binding["generation"],
            binding["attempt_id"],
            version=4,
            package_ref=package_ref,
        )
        baseline = {
            "schema": transactions.BASELINE_SCHEMA,
            "attempt_id": binding["attempt_id"],
            "native": {"cold_authority_package_ref": package_ref, "dispatch_epoch_ref": epoch_ref},
            "host": prepared["host_baseline"],
            "previous_container": prepared["previous_container"],
            "source_consumers": source["consumers"],
            "review_key_ref": prepared["review_key_ref"],
            "backup_profile_sha256": profile.profile_sha256(),
            "deployment_lock_identity": binding["deployment_lock_identity"],
            "ci": prepared["ci"],
            "maintenance_parent": prepared["maintenance_parent"],
            "recovery_origin": origin,
            "controller_request_ref": self.request_ref,
            "controller_launch_ref": capsule,
            "qualification_ref": self.request["qualification_ref"],
        }
        baseline_ref = _write_once(self.directory / "baseline.json", baseline)
        plan = {
            "schema": cold.PLAN_SCHEMA,
            "scope": cold.PLAN_SCOPE,
            "attempt_id": binding["attempt_id"],
            "host": socket.gethostname(),
            "sequence": old["sequence"],
            "predecessor_sha256": old["predecessor_sha256"],
            "base_sha256": old["base_sha256"],
            "candidate": self.request["candidate"],
            "baseline_ref": baseline_ref,
            "candidate_manager_sha": binding["candidate_manager_sha"],
            "cold_crm_spec_ref": spec_ref,
            "cold_authority_package_ref": package_ref,
            "dispatch_epoch_ref": epoch_ref,
            "created_at": forward.utc(),
            "deadline_utc": self.request["deadline_utc"],
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        }
        transactions.validate_plan(plan, old["base_sha256"])
        plan_ref = _write_once(self.directory / "plan.json", plan)
        envelope = {
            "schema": transactions.RECOVERY_SCHEMA,
            "recovery_id": binding["attempt_id"],
            **origin,
            "plan_ref": plan_ref,
        }
        store = transactions.ColdStore(self.root / "state", old["base_sha256"])
        store.begin_recovery(envelope)
        marker_ref = markers.retain(
            self.directory, plan_ref, baseline, controller=controller, store=store
        )
        _write_once(
            self.directory / "closure-owned.json",
            {
                "schema": "autostop.j1.cold-retained-closure-control.v1",
                "attempt_id": binding["attempt_id"],
                "package_ref": package_ref,
                "epoch_ref": epoch_ref,
                "marker_ref": marker_ref,
                "closure_ref": package["closure_ref"],
                "ownership_adopted": False,
            },
        )
        for filename, document in (
            (
                "process-scope.json",
                {
                    "schema": "autostop.j1.cold-retired-scope-reference.v1",
                    "origin_scope_ref": origin["origin_scope_ref"],
                    "retirement_ref": package["retirement_ref"],
                },
            ),
            (
                "ingress-drain.json",
                {
                    "schema": "autostop.j1.cold-quarantine-reference.v1",
                    "quarantine_ref": package["quarantine_ref"],
                    "pending_ref": package["pending_ref"],
                    "warm_idle_claimed": False,
                },
            ),
        ):
            _write_once(self.directory / filename, document)
        self.plan, self.plan_ref, self.baseline, self.store = plan, plan_ref, baseline, store
        self.envelope, self.marker_ref, self.host, self.profile = (
            envelope,
            marker_ref,
            host,
            profile,
        )
        _progress(self.directory, "cold-prepared", plan_ref=plan_ref)
        return plan_ref

    def observe(self):
        self._capture_gate()
        return forward.save(
            self.directory,
            "cold-native",
            _native_observation(self.plan, self.controller, self.phase),
        )

    def _capture_gate(self):
        forward.remaining(self.plan)
        markers.require(self.marker_ref, self.plan_ref)

    def capture(self):
        context = {
            "attempt_id": self.plan["attempt_id"],
            "predecessor_sha256": self.plan["predecessor_sha256"],
            "candidate_crm_sha": self.plan["candidate"]["revision"],
            "candidate_manager_sha": self.plan["candidate_manager_sha"],
            "scope_profile_sha256": self.profile.profile_sha256(),
        }
        capture_deadline = time.monotonic() + min(300, forward.remaining(self.plan))

        def capture_gate():
            self._capture_gate()
            io.require(time.monotonic() < capture_deadline, "cold_capture_deadline")

        with j1_forward_cold_backup.CaptureInterval(
            self.controller, self.plan_ref, self.directory, gate=capture_gate
        ) as interval:
            captured = self.profile.capture(
                self.directory / "backup",
                context,
                interval.observe,
                timeout=min(capture_deadline - time.monotonic(), forward.remaining(self.plan)),
                diagnostic_interval=interval,
            )
        captured = {**captured, "capture_interval_ref": interval.completion_ref}
        backup_ref = _write_once(self.directory / "backup-evidence.json", captured)
        restored = self.profile.verify_and_restore(
            self.directory / "backup",
            captured["manifest_sha256"],
            self.directory / "local-data-restore",
            context,
            timeout=min(120, forward.remaining(self.plan)),
        )
        _write_once(self.directory / "backup-local-restore.json", restored)
        target = self.archive_root / self.plan["attempt_id"]
        if not target.exists():
            io.protected_directory(target.parent, private=True)
            target.mkdir(mode=0o700)
            io.fsync_directory(target.parent)
        control = archive.capture(
            self.root,
            self.plan_ref,
            captured["manifest"],
            target / "closed",
            stage="closed",
            observer=self.observe,
            codec=5,
        )
        archive.verify_and_restore(
            target / "closed",
            control["manifest_sha256"],
            target / "local-closed-restore",
            plan_ref=self.plan_ref,
            backup_manifest_ref=captured["manifest"],
            stage="closed",
            logical_root=self.root,
        )
        _write_once(self.directory / "control-closed.json", control)
        engine = variant.transport_engine(
            self.plan, profile_sha256=self.baseline["backup_profile_sha256"]
        )
        inventory_ref = (
            io.reference(forward.CACHE_INVENTORY) if forward.CACHE_INVENTORY.exists() else None
        )
        delta = engine.bundle_delta(
            self.directory / "backup",
            captured["manifest_sha256"],
            inventory_ref,
            self.directory / "delta.tar.gz",
            timeout=min(120, forward.remaining(self.plan)),
        )
        _write_once(
            self.directory / "transport.json",
            {**delta["archive"], "manifest_sha256": captured["manifest_sha256"]},
        )
        control_delta = archive.bundle(
            target / "closed", control["manifest_sha256"], target / "closed.tar.gz"
        )
        _write_once(
            self.directory / "control-closed-transport.json",
            {
                **{key: control[key] for key in ("manifest", "manifest_sha256", "stage")},
                "archive": control_delta["archive"],
            },
        )
        _progress(self.directory, "awaiting-offhost-restore", plan_ref=self.plan_ref)
        return backup_ref

    def await_proof(self, kind):
        while not (self.directory / (kind + ".json")).exists():
            self.observe()
            time.sleep(0.3)
        reference = io.reference(self.directory / (kind + ".json"))
        _verify_payload(self.directory, self.plan_ref, self.plan, self.baseline, reference, kind)
        return reference

    def _fresh_http_cut(self):
        forward.remaining(self.plan)
        markers.require(self.marker_ref, self.plan_ref)
        report = self.host.http_cut(self.plan["candidate"], self.plan["attempt_id"])
        markers.require(self.marker_ref, self.plan_ref)
        # The marker callback and pre-OPEN cut each require current transport
        # receipts from the same accepted Telegram process incarnations.
        ready = self.controller.ready()
        cold.need("telegram_frontiers" in ready, "fresh_telegram_frontiers_required")
        forward.save(self.directory, "cold-http-ingress-cut", report)
        return report

    def run(self):
        self.prepare()
        backup_ref = self.capture()
        offhost = self.await_proof("offhost_restore")
        self.controller.issue(self.plan_ref, backup_ref, offhost)
        for unit in cold.ALLOWED:
            self.controller.submit(unit)
            self.controller.accept(unit)
        self.host.configure_cold(
            self.plan["dispatch_epoch_ref"],
            self.plan["cold_authority_package_ref"],
            self.plan["cold_crm_spec_ref"],
        )
        self.controller.consume_crm_start(self.request_ref)
        self.controller.accept_crm(self.host.replace_crm(self.plan["candidate"]))
        self.phase = "activated"
        _progress(self.directory, "awaiting-native-runtime-acceptance", plan_ref=self.plan_ref)
        native_proof = self.await_proof("native_acceptance")
        runtime_proof = self.await_proof("runtime_acceptance")
        closure = self.observe()
        proofs = {
            "closure": closure,
            "backup": backup_ref,
            "offhost_restore": offhost,
            "native_acceptance": native_proof,
            "runtime_acceptance": runtime_proof,
        }
        commit = self.store.commit_recovery(self.envelope, proofs)
        self.controller.commit(commit, store=self.store)
        if self.provider.scope == "production_native":
            self._fresh_http_cut()
        self.controller.open_ingress(commit, store=self.store)
        post_open = self.host.readonly_probes(
            self.plan["candidate"], self.plan["attempt_id"], phase="open"
        )
        post_open_ref = _write_once(self.directory / "post-open-readonly.json", post_open)
        markers.remove(
            self.marker_ref,
            self.plan_ref,
            commit,
            controller=self.controller,
            store=self.store,
            http_cut=self._fresh_http_cut,
        )
        complete = self.controller.finish(store=self.store)
        custody_ref = self.producer.adopt_runtime(commit, complete)
        completion = _write_once(
            self.directory / "completion.json",
            {
                "ok": True,
                "plan_ref": self.plan_ref,
                "commit": commit,
                "cold_completion_ref": complete,
                "kernel_custody_ref": custody_ref,
                "old_writer_allowed": False,
                "post_open_readonly_ref": post_open_ref,
                "business_acceptance": False,
                "production_operational_ready": False,
                "post_open_business_acceptance": "required_in_release_window",
            },
        )
        _progress(self.directory, "complete", completion=completion)
        self._terminal_archive()
        return {
            "ok": True,
            "completion": completion,
            "production_operational_ready": False,
            "post_open_business_acceptance": "required_in_release_window",
        }

    def _terminal_archive(self):
        captured = io.document(io.read_protected(self.directory / "backup-evidence.json"))
        target = self.archive_root / self.plan["attempt_id"]

        def terminal_observer():
            # The marker was independently removed after COMMIT. A terminal
            # evidence read cannot require it to remain installed or recreate it.
            io.require(
                self.store.head()["sha256"]
                == io.document(io.read_protected(self.directory / "completion.json"))["commit"][
                    "sha256"
                ],
                "cold_terminal_head_changed",
            )
            return forward.save(
                self.directory,
                "cold-terminal-native",
                _native_observation(self.plan, self.controller, "activated"),
            )

        terminal = archive.capture(
            self.root,
            self.plan_ref,
            captured["manifest"],
            target / "terminal",
            stage="terminal",
            observer=terminal_observer,
            codec=5,
        )
        archive.verify_and_restore(
            target / "terminal",
            terminal["manifest_sha256"],
            target / "local-terminal-restore",
            plan_ref=self.plan_ref,
            backup_manifest_ref=captured["manifest"],
            stage="terminal",
            logical_root=self.root,
        )
        _write_once(self.directory / "control-terminal.json", terminal)
        packed = archive.bundle(
            target / "terminal", terminal["manifest_sha256"], target / "terminal.tar.gz"
        )
        _write_once(
            self.directory / "control-terminal-transport.json",
            {
                **{key: terminal[key] for key in ("manifest", "manifest_sha256", "stage")},
                "archive": packed["archive"],
            },
        )


def _verify_payload(directory, plan_ref, plan, baseline, reference, kind):
    payload = signatures.verify_proof(
        reference, kind, plan_ref, baseline["review_key_ref"], plan_validator=variant.validate_plan
    )
    io.require(
        payload.get("plan_sha256") == plan_ref["sha256"]
        and payload.get("baseline_sha256") == plan["baseline_ref"]["sha256"],
        "cold_proof_binding",
    )
    phase = "closed" if kind == "offhost_restore" else "activated"
    key = "closed_inspection_sha256" if phase == "closed" else "inspection_sha256"
    digest = payload.get(key)
    matches = [
        path
        for path in directory.glob("inspection-*.json")
        if io.reference(path)["sha256"] == digest
    ]
    io.require(len(matches) == 1 and payload.get("phase") == phase, "cold_independent_inspection")
    inspected = io.document(io.read_protected(matches[0]))
    io.require(
        inspected["stage"] == phase
        and inspected["plan_sha256"] == plan_ref["sha256"]
        and inspected["baseline_sha256"] == plan["baseline_ref"]["sha256"],
        "cold_inspection_binding",
    )
    if phase == "closed":
        captured = io.document(io.read_protected(directory / "backup-evidence.json"))
        control = io.document(io.read_protected(directory / "control-closed.json"))
        io.require(
            payload.get("actual_restore") is True
            and payload.get("control_graph_restored") is True
            and payload.get("manifest_sha256") == captured["manifest_sha256"]
            and payload.get("control_archive_manifest_sha256") == control["manifest_sha256"]
            and payload.get("profile_sha256") == baseline["backup_profile_sha256"]
            and isinstance(payload.get("control_restore_receipt_sha256"), str)
            and cold.HEX.fullmatch(payload["control_restore_receipt_sha256"]),
            "cold_offhost_restore_required",
        )
    elif kind == "native_acceptance":
        io.require(
            payload.get("cold_guards_executed") is True
            and payload.get("execution_acceptance_sha256")
            == inspected["execution_acceptance_sha256"],
            "cold_independent_native_required",
        )
    else:
        io.require(
            payload.get("runtime_checks_passed") is True
            and payload.get("candidate_revision") == plan["candidate"]["revision"]
            and payload.get("candidate_image") == plan["candidate"]["image"],
            "cold_independent_runtime_required",
        )
    if reference["path"] == str(directory / (kind + ".json")):
        _write_once(
            directory / ("proof-binding-" + kind + ".json"),
            {
                "schema": "autostop.j1.cold-independent-proof-binding.v1",
                "kind": kind,
                "plan_ref": plan_ref,
                "proof_ref": reference,
                "inspection_ref": io.reference(matches[0]),
            },
        )
    return payload


def inspect(attempt, stage):
    directory, plan_ref, plan, baseline, host, controller = _load(attempt)
    markers.require(io.reference(directory / "cold-marker-owned.json"), plan_ref)
    progress = io.document(io.read_protected(directory / "progress.json"))
    io.require(
        (stage, progress["phase"])
        in {
            ("closed", "awaiting-offhost-restore"),
            ("activated", "awaiting-native-runtime-acceptance"),
        },
        "cold_inspection_phase",
    )
    evidence = {
        "native": _native_observation(plan, controller, stage),
        "package": io.document(io.referenced(plan["cold_authority_package_ref"])),
        "epoch": io.document(io.referenced(plan["dispatch_epoch_ref"])),
    }
    native_ref = forward.save(directory, "inspected-cold-native", evidence["native"])
    inspection = {
        "schema": variant.inspection_schema(plan),
        "stage": stage,
        "attempt_id": attempt,
        "plan_sha256": plan_ref["sha256"],
        "baseline_sha256": plan["baseline_ref"]["sha256"],
        "candidate": plan["candidate"],
        "package_ref": plan["cold_authority_package_ref"],
        "epoch_ref": plan["dispatch_epoch_ref"],
        "native_ref": native_ref,
        "original_barriers_closed": True,
        "original_hold_retained": True,
        "old_writer_allowed": False,
    }
    if stage == "closed":
        for name, filename in (
            ("backup_capture", "backup-evidence.json"),
            ("backup_restore", "backup-local-restore.json"),
        ):
            reference = io.reference(directory / filename)
            evidence[name] = io.document(io.referenced(reference))
            inspection[name + "_ref"] = reference
        inspection["writer_closure_confirmed"] = True
    else:
        evidence["accepted"] = _accepted(plan, controller)
        evidence["crm"] = evidence["accepted"]["crm"]
        evidence["runtime"] = host.probes(plan["candidate"], attempt, held=True)
        inspection.update(
            cold_guards_executed=True,
            runtime_checks_passed=True,
            execution_acceptance_sha256=evidence["accepted"]["execution_acceptance_sha256"],
            accepted_ref=forward.save(directory, "inspected-seven-accepted", evidence["accepted"]),
            crm_ref=evidence["accepted"]["crm_ref"],
            runtime_ref=forward.save(directory, "inspected-cold-runtime", evidence["runtime"]),
        )
    inspection_ref = forward.save(directory, "inspection", inspection)
    response = {
        "ok": True,
        "stage": stage,
        "plan": plan,
        "plan_ref": plan_ref,
        "baseline": baseline,
        "baseline_ref": plan["baseline_ref"],
        "review_key_ref": baseline["review_key_ref"],
        "inspection": inspection,
        "inspection_ref": inspection_ref,
        "evidence": evidence,
        "qualification": io.document(io.referenced(baseline["qualification_ref"])),
        "backup_context": forward.context(plan, profile_sha256=baseline["backup_profile_sha256"]),
        "scope_profile_sha256": baseline["backup_profile_sha256"],
        "recovery": io.document(
            io.referenced(io.reference(forward.STATE / "recoveries" / (attempt + ".json")))
        ),
    }
    if stage == "closed":
        response.update(
            transport=io.document(io.read_protected(directory / "transport.json")),
            control_archive=io.document(
                io.read_protected(directory / "control-closed-transport.json")
            ),
        )
    return response


def receive_proof(attempt, kind, raw):
    directory, plan_ref, plan, baseline, _host, _controller = _load(attempt)
    io.require(kind in signatures.KINDS and 0 < len(raw) <= 2 * 1024**2, "cold_proof_input")
    body = io.document(raw)
    incoming = forward.save(directory, "incoming-proof", body)
    _verify_payload(directory, plan_ref, plan, baseline, incoming, kind)
    return {"ok": True, "proof": _write_once(directory / (kind + ".json"), body)}


def settle(attempt):
    """Close only the new epoch even after lease expiry; never thaw old units."""
    directory = forward.attempt_directory(attempt)
    plan_path = directory / "plan.json"
    if not plan_path.exists():
        return {"ok": False, "preparation_incomplete": True, "old_writer_allowed": False}
    _directory, plan_ref, plan, _baseline, _host, controller = _load(attempt, fresh=False)
    epoch = io.document(io.referenced(plan["dispatch_epoch_ref"]))
    from scripts import j1_forward_cold_projection

    fence = j1_forward_cold_projection._fence(epoch, io.referenced)
    descriptor = epochs.locked_fence(fence, exclusive=True)
    try:
        j1_forward_cold_projection._exclusive(fence, descriptor)
        # Persist refusal before closing. A still-live controller cannot reopen
        # this epoch after a concurrently requested safety close.
        _write_once(
            controller.root / "safety-closed.json",
            {
                "schema": "autostop.j1.cold-terminal-safety-close.v1",
                "attempt_id": attempt,
                "plan_ref": plan_ref,
                "epoch_ref": plan["dispatch_epoch_ref"],
                "old_writer_allowed": False,
                "activation_authorized": False,
            },
        )
        j1_forward_cold_projection._closed(fence, epoch)
    finally:
        os.close(descriptor)
    # No reset, new permit, replay or source/ownership adoption follows a crash.
    return {
        "ok": True,
        "new_dispatch_closed": True,
        "old_writer_allowed": False,
        "manual_forward_reconciliation_required": True,
    }


def _export(path, reference, limit):
    """Hash before stdout, then stream through the same pinned descriptor."""
    cold.reference(reference)
    io.require(reference["path"] == str(path), "cold_export_path")
    io.protected_directory(path.parent)
    before = path.lstat()
    io.require(
        stat.S_ISREG(before.st_mode)
        and before.st_uid == io.ROOT_UID
        and before.st_nlink == 1
        and not before.st_mode & 0o077
        and 0 <= before.st_size <= limit,
        "cold_export_file",
    )
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:

        def unchanged():
            final, opened = path.lstat(), os.fstat(descriptor)
            keys = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            io.require(
                all(
                    getattr(before, key) == getattr(final, key) == getattr(opened, key)
                    for key in keys
                ),
                "cold_export_changed",
            )

        unchanged()
        digest = hashlib.sha256()
        while raw := os.read(descriptor, 1024**2):
            digest.update(raw)
        unchanged()
        io.require(digest.hexdigest() == reference["sha256"], "cold_export_digest")
        os.lseek(descriptor, 0, os.SEEK_SET)
        while raw := os.read(descriptor, 1024**2):
            sys.stdout.buffer.write(raw)
        unchanged()
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue-request")
    issue.add_argument("--request", required=True)
    issue.add_argument("--request-sha256", required=True)
    for operation in (
        "launch",
        "revoke",
        "controller",
        "settle",
        "inspect",
        "receive-proof",
        "export-delta",
        "export-control",
    ):
        child = commands.add_parser(operation)
        child.add_argument("--attempt", required=True)
        if operation in {"inspect", "export-control"}:
            child.add_argument(
                "--stage",
                choices=("closed", "activated")
                if operation == "inspect"
                else ("closed", "terminal"),
                required=True,
            )
        if operation == "receive-proof":
            child.add_argument("--kind", choices=sorted(signatures.KINDS), required=True)
    args = parser.parse_args()
    try:
        io.require(os.name == "posix" and os.geteuid() == 0, "cold_native_root_required")
        if args.command == "issue-request":
            reference = {"path": args.request, "sha256": args.request_sha256}
            request = parent.validate_request(io.document(io.referenced(reference)))
            from scripts.j1_forward_host import Host

            host = Host({}, Path(reference["path"]).parent)
            io.require(
                host.source() == {key: request["source"][key] for key in ("revision", "tree")},
                "cold_request_published_source",
            )
            ci = io.document(io.referenced(request["ci_ref"]))
            io.require(
                forward.verify_ci(ci["run_id"], request["source"]) == ci, "cold_request_ci_changed"
            )
            with forward.deployment_lock() as descriptor:
                info = os.fstat(descriptor)
                result = parent.issue(reference, [info.st_dev, info.st_ino])
        elif args.command == "launch":
            parent.launch(args.attempt)
            result = {"ok": True, "attempt_id": args.attempt}
        elif args.command == "revoke":
            result = parent.revoke(args.attempt)
        elif args.command == "controller":
            for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
                signal.signal(signum, forward.interrupted)
            with parent.Admission(args.attempt, forward.deployment_lock) as admitted:
                from scripts import j1_forward_cold_producer

                reference = admitted.context["plan_ref"]
                request = io.document(io.referenced(reference))
                with j1_forward_cold_producer.NativeProducer(
                    reference, request, deployment_fd=admitted.deployment_fd
                ) as producer:
                    coordinator = Coordinator(
                        reference, request, producer, deployment_fd=admitted.deployment_fd
                    )
                    try:
                        result = coordinator.run()
                    except BaseException:
                        if (coordinator.directory / "plan.json").exists():
                            settle(args.attempt)
                        raise
        elif args.command == "settle":
            result = settle(args.attempt)
        elif args.command == "inspect":
            result = inspect(args.attempt, args.stage)
        elif args.command == "receive-proof":
            result = receive_proof(args.attempt, args.kind, sys.stdin.buffer.read(2 * 1024**2 + 1))
        else:
            directory = forward.attempt_directory(args.attempt)
            if args.command == "export-delta":
                _directory, _ref, plan, _baseline, _host, _controller = _load(args.attempt)
                forward.remaining(plan)
                metadata = io.document(io.read_protected(directory / "transport.json"))
                reference = {key: metadata[key] for key in ("path", "sha256")}
                path, limit = directory / "delta.tar.gz", forward.transport.MAX_ENVELOPE
            else:
                metadata = io.document(
                    io.read_protected(directory / ("control-" + args.stage + "-transport.json"))
                )
                reference = metadata["archive"]
                path = forward.CONTROL_ARCHIVES / args.attempt / (args.stage + ".tar.gz")
                limit = archive.transport_total_limit(4) + archive.MAX_FILE_BYTES
            _export(path, reference, limit)
            return 0
        sys.stdout.buffer.write(io.canonical(result))
        return 0 if result.get("ok") is True else 2
    except Exception as error:
        code = (
            str(error)
            if isinstance(error, (io.Refusal, parent.journal.Refusal))
            else type(error).__name__
        )
        sys.stderr.buffer.write(io.canonical({"ok": False, "error": code}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
