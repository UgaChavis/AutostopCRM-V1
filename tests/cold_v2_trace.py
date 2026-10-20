"""A concrete private trace, with actual files, crypto, restore and six entries."""

from __future__ import annotations

import json
import os
import select
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from scripts import cold_trace as trace
from scripts import j1_forward_archive as archive
from scripts import j1_forward_cold_authority as authority
from scripts import j1_forward_cold_backup as cold_backup
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_fixture as topology
from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_cold_release as coordinator
from scripts import j1_forward_io as io
from scripts import j1_forward_rehearsal_crm as crm_boundary
from scripts import j1_forward_release as release
from scripts import j1_forward_release_faults as faults
from scripts import j1_forward_release_rehearsal as rehearsal
from scripts import j1_forward_variant as variant
from scripts.cold_v2_rehearsal import (
    TypedFixture,
    private_guard_status,
    stage_voice_models,
    verify_restored_models,
)


def _crash_after_history(store, plan, perform):
    """Crash this owned child after the actual supplied transaction's write.

    The joined trace supplies its existing ColdStore/envelope/proofs directly;
    neither a second State root nor a warm transaction replaces that call.
    """
    io.require(threading.active_count() == 1, "private_crash_requires_one_thread")
    target = store.history / f"{plan['sequence']:020d}.json"
    child = os.fork()
    if child == 0:
        original = io.write_new

        def write(path, value):
            reference = original(path, value)
            if Path(path) == target:
                os._exit(faults.CRASH_EXIT)
            return reference

        try:
            with patch.object(io, "write_new", side_effect=write):
                perform()
        except BaseException:
            import traceback

            traceback.print_exc()
            sys.stderr.flush()
            os._exit(78)
        os._exit(79)
    descriptor = None
    reaped = False
    try:
        descriptor = os.pidfd_open(child, 0)
        io.require(bool(select.select([descriptor], [], [], 15)[0]), "private_crash_wait_timeout")
        waited, status = os.waitpid(child, 0)
        reaped = True
        io.require(
            waited == child
            and os.WIFEXITED(status)
            and os.WEXITSTATUS(status) == faults.CRASH_EXIT,
            "private_recovery_crash_missed",
        )
    finally:
        if not reaped:
            # This unreaped PID is still our child; it cannot have been reused.
            if descriptor is None:
                os.kill(child, signal.SIGKILL)
            else:
                try:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            os.waitpid(child, 0)
        if descriptor is not None:
            os.close(descriptor)


def _accept(controller, unit):
    try:
        return controller.accept(unit)
    except Exception:
        ticket_unit = "autostop-crm-forward-" + controller.epoch["slots"][unit] + ".service"
        try:
            status = private_guard_status(controller.backend, ticket_unit)
            snapshot = controller.store(unit).read()["state"]
            status["journal"] = {
                "sequence": snapshot["sequence"],
                "submitted": snapshot["dispatch_possible"],
                "accepted": snapshot["acceptance"] is not None,
                "claimed": snapshot["claim"] is not None,
                "entered": snapshot["execution_possible"],
            }
        except Exception:
            status = {"diagnostics_unavailable": True}
        print("PRIVATE_COLD_GUARD " + json.dumps(status, sort_keys=True), flush=True)
        raise


@contextmanager
def _phase(name):
    started = time.perf_counter()
    try:
        yield
    finally:
        print(
            "PRIVATE_COLD_PHASE_TIMING "
            + io.canonical({"phase": name, "wall_seconds": time.perf_counter() - started})
            .decode()
            .strip(),
            flush=True,
        )


@unittest.skipUnless(
    sys.platform.startswith("linux") and hasattr(os, "pidfd_open"),
    "Requires real Linux flock, AF_UNIX and SO_PEERCRED",
)
class ColdV2EntryTests(unittest.TestCase):
    def test_one_actual_kernel2_entry_requires_e_and_keeps_ingress_closed(self):
        # Only the independent-restore admission boundary is a direct fixture
        # assumption here. The joined trace separately proves actual restores.
        started = time.monotonic()
        with TypedFixture(ttl_seconds=600) as fixture, fixture.custody:
            package_ref = fixture.produce()
            epoch_ref = lifecycle.provision_epoch(
                package_ref, fixture.runtime_bindings, provider=fixture.provider
            )
            backend = lifecycle.FixtureBackend(fixture)
            controller = lifecycle.Controller(
                package_ref, epoch_ref, provider=fixture.provider, backend=backend
            )
            controller.stage()
            directory = fixture.control / "attempts" / fixture.binding["attempt_id"]
            baseline = fixture._write(
                directory / "entry-only-baseline.json",
                {
                    "controller_request_ref": fixture.parent.request_ref,
                    "controller_launch_ref": fixture.parent.manifest_ref,
                },
            )
            plan = fixture._plan(fixture.binding["attempt_id"])
            plan.update(
                schema=authority.PLAN_SCHEMA,
                scope=authority.PLAN_SCOPE,
                baseline_ref=baseline,
                candidate_manager_sha=fixture.binding["candidate_manager_sha"],
                cold_authority_package_ref=package_ref,
                dispatch_epoch_ref=epoch_ref,
                cold_crm_spec_ref=baseline,
            )
            plan_ref = fixture._write(directory / "entry-only-plan.json", plan)
            lifecycle._publish(
                controller.root / "release-admission.json",
                {
                    "schema": "autostop.j1.cold-slot-release-admission.v1",
                    "package_ref": package_ref,
                    "epoch_ref": epoch_ref,
                    "plan_ref": plan_ref,
                    "backup_ref": baseline,
                    "offhost_ref": baseline,
                    "candidate_may_have_written": True,
                    "old_writer_allowed": False,
                },
                controller.root,
            )
            unit = authority.ALLOWED[0]
            with self.assertRaisesRegex(io.Refusal, "fixture_candidate_not_entered"):
                topology.candidate_inspection(
                    fixture.provider, unit, authority.digest(fixture.binding)
                )
            controller.submit(unit)
            reference = _accept(controller, unit)
            observed = io.document(fixture.provider.read_reference(reference))
            self.assertFalse(observed["business_acceptance"])
            slot, snapshot, context = controller.context(unit)
            self.assertTrue(snapshot["state"]["execution_possible"])
            self.assertTrue(controller.submit(unit)["historical"])
            self.assertEqual(slot.read(), snapshot)
            duplicate = subprocess.run(
                context["worker_argv"],
                env=context["bootstrap_environment"],
                cwd=context["worker_cwd"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertEqual(slot.read(), snapshot)
            endpoint = fixture.root / "execution/roles" / epochs.ROLES[unit] / "run/control.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(3)
                client.connect(str(endpoint))
                client.sendall(
                    io.canonical({"op_id": str(uuid.uuid4()), "payload_sha256": "f" * 64})
                )
                self.assertEqual(client.recv(128), b"HELD\n")
            self.assertEqual(len(list((fixture.root / "barriers").iterdir())), 8)
            dispatch = io.document(
                fixture.provider.read_path(fixture.root / "execution/dispatch/state.json")
            )
            self.assertEqual(dispatch["state"], "closed")
            print(
                "PRIVATE_COLD_ENTRY_ONLY "
                + json.dumps(
                    {
                        "wall_seconds": time.monotonic() - started,
                        "direct_fixture_admission": True,
                        "independent_restore_exercised": False,
                        "production_authority": False,
                        "entered": True,
                        "ingress_closed": True,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )


@unittest.skipUnless(
    sys.platform.startswith("linux") and hasattr(os, "pidfd_open"),
    "Requires real Linux flock, AF_UNIX and SO_PEERCRED",
)
class ColdV2JoinedTraceTests(unittest.TestCase):
    def test_failed_origin_full_restore_six_entries_commit_and_finish(self):
        started = time.perf_counter()

        def mark(stage):
            print(
                "PRIVATE_COLD_STAGE "
                + io.canonical({"stage": stage, "elapsed_seconds": time.perf_counter() - started})
                .decode()
                .strip(),
                flush=True,
            )

        with TypedFixture(ttl_seconds=600) as fixture:
            package_ref = fixture.produce()
            mark("old_scope_retired_package_produced")
            with fixture.custody, ExitStack() as aliases:
                models = (
                    stage_voice_models(fixture, os.environ["CRM_COLD_V2_MODELS"])
                    if os.environ.get("CRM_COLD_V2_MODELS")
                    else []
                )
                original_pending = fixture.store.pending_path.read_bytes()
                package = io.document(fixture.provider.read_reference(package_ref))
                pending = io.document(fixture.provider.read_reference(package["pending_ref"]))
                self.assertEqual(pending["schema"], "autostop.j1.cold-pending-disposition.v2")
                self.assertTrue(all(row["outcome"] == "unknown" for row in pending["requests"]))
                unknown = {"op_id": pending["requests"][0]["op_id"], "payload_sha256": "f" * 64}
                business_roots = rehearsal.synthetic_data(fixture)
                epoch_ref = lifecycle.provision_epoch(
                    package_ref, fixture.runtime_bindings, provider=fixture.provider
                )
                crm = crm_boundary.PrivateCRM(
                    fixture,
                    revision=fixture.source["revision"],
                    image="sha256:" + "4" * 64,
                    start=False,
                )
                spec_ref = crm.publish_spec(epoch_ref, package_ref)
                engine = cold_backup.engine(
                    fixture.binding["candidate_manager_sha"],
                    fixture.binding["generation"],
                    fixture.binding["attempt_id"],
                    version=4,
                    package_ref=package_ref,
                )
                roots = trace.private_profile(fixture, engine, business_roots)
                aliases.enter_context(rehearsal.PrivateFilesystem(fixture.root, engines=(engine,)))
                for name, value in rehearsal.backup_settings(engine, roots, fixture.root).items():
                    aliases.enter_context(patch.object(engine, name, value))
                signer = rehearsal.TemporaryCustodian(
                    fixture.root / "custodian", fixture.root / "config/reviewer.pem"
                )
                aliases.enter_context(
                    patch.object(rehearsal.signatures, "REVIEW_KEY_PATH", str(signer.public))
                )
                attempt = trace.prepare_attempt(
                    fixture, package_ref, epoch_ref, spec_ref, signer, engine=engine
                )
                backend = crm_boundary.fixture_backend(fixture, crm)
                controller = lifecycle.Controller(
                    package_ref, epoch_ref, provider=fixture.provider, backend=backend
                )
                controller.stage()
                self.assertEqual(attempt["store"].pending_path.read_bytes(), original_pending)
                self.assertIsNone(crm.process)
                for unit in authority.ALLOWED:
                    self.assertEqual(controller.store(unit).read()["state"]["sequence"], 0)
                mark("cold_recovery_and_six_journals_staged")
                captured = trace.capture_closed(fixture, attempt, controller)
                mark("actual_full_data_and_control_captured")
                data_manifest = io.document(io.referenced(captured["captured"]["manifest"]))
                first_restore = attempt["directory"] / "local-data-restore"
                first = engine.verify_and_restore(
                    Path(captured["captured"]["manifest"]["path"]).parent,
                    captured["captured"]["manifest_sha256"],
                    first_restore,
                    captured["context"],
                    timeout=min(120, release.remaining(attempt["plan"])),
                )
                self.assertTrue(first["provided_artifacts_restored_exactly"])
                model_receipts = [
                    verify_restored_models(
                        models,
                        data_manifest,
                        first_restore,
                        deadline=time.monotonic() + release.remaining(attempt["plan"]),
                    )
                ]
                mark("local_actual_data_restore")
                control = {
                    "package": str(Path(captured["archived"]["manifest"]["path"]).parent),
                    "manifest_sha256": captured["archived"]["manifest_sha256"],
                    "logical_root": str(fixture.control),
                    "backup_manifest_ref": captured["captured"]["manifest"],
                    "stage": "closed",
                    "closed_inspection_ref": captured["closed_inspection_ref"],
                }
                peer = rehearsal.independent_restore(
                    fixture,
                    attempt["plan_ref"],
                    captured["captured"],
                    captured["context"],
                    roots,
                    signer,
                    control=control,
                    proof_path=attempt["directory"] / "offhost-restore-proof.json",
                )
                self.assertNotEqual(peer["pid"], os.getpid())
                self.assertTrue(peer["control_restore"]["semantic_graph_verified"])
                model_receipts.append(
                    verify_restored_models(
                        models,
                        data_manifest,
                        fixture.root / "peer-restore/data",
                        deadline=time.monotonic() + release.remaining(attempt["plan"]),
                    )
                )
                if models:
                    print(
                        "PRIVATE_MODEL_RESTORES " + io.canonical(model_receipts).decode().strip(),
                        flush=True,
                    )
                # Legacy launch journals are absent from this genuine older
                # predecessor. The cold graph validator checks all six exact
                # current epoch journals separately, including their events.
                self.assertEqual(peer["control_restore"]["semantic"]["launch_journal_count"], 0)
                self.assertGreater(
                    peer["control_restore"]["semantic"]["cold_authority_graph_files"], 0
                )
                mark("independent_actual_data_and_control_restore")
                offhost = peer["proof_ref"]
                payload = rehearsal.signatures.verify_proof(
                    offhost,
                    "offhost_restore",
                    attempt["plan_ref"],
                    io.reference(signer.public),
                    plan_validator=variant.validate_plan,
                )
                release.validate_payload(
                    "offhost_restore",
                    payload,
                    attempt["directory"],
                    attempt["plan_ref"],
                    attempt["plan"],
                    attempt["baseline"],
                )
                trace.bind_verified_inspection(
                    fixture,
                    attempt,
                    signer,
                    "offhost_restore",
                    offhost,
                    captured["closed_inspection_ref"],
                )
                controller.issue(attempt["plan_ref"], captured["backup_ref"], offhost)
                mark("actual_signed_restore_issued")
                accepted = {}
                for unit in authority.ALLOWED:
                    controller.submit(unit)
                    accepted[unit] = _accept(controller, unit)
                    snapshot = controller.store(unit).read()
                    self.assertTrue(snapshot["state"]["execution_possible"])
                    mark("actual_native_entry_accepted:" + unit)
                entered_crm = controller.consume_crm_start(fixture.parent.request_ref)
                self.assertTrue(entered_crm["entry_consumed"])
                crm.start()
                crm_receipt = fixture._write(
                    attempt["directory"] / "crm-readback.json", crm.measure(epoch_ref, package_ref)
                )
                controller.accept_crm(crm_receipt)
                ready = controller.ready()
                self.assertEqual(ready["accepted"], accepted)
                mark("all_six_and_private_crm_accepted")
                inspection = fixture._write(
                    attempt["directory"] / "inspection-activated.json",
                    {
                        "stage": "activated",
                        "scope": "native_fixture",
                        "plan_sha256": attempt["plan_ref"]["sha256"],
                        "baseline_sha256": attempt["plan"]["baseline_ref"]["sha256"],
                        "acceptance_sha256": ready["acceptance_sha256"],
                        "accepted_refs": accepted,
                        "crm_ref": crm_receipt,
                    },
                )
                common = {
                    "phase": "activated",
                    "plan_sha256": attempt["plan_ref"]["sha256"],
                    "baseline_sha256": attempt["plan"]["baseline_ref"]["sha256"],
                    "inspection_sha256": inspection["sha256"],
                    "inspection_ref": inspection,
                    "scope": "native_fixture",
                    "production_acceptance": False,
                }
                native_payload = {
                    **common,
                    "cold_guards_executed": all(
                        controller.store(unit).read()["state"]["execution_possible"]
                        for unit in authority.ALLOWED
                    ),
                    "execution_acceptance_sha256": ready["acceptance_sha256"],
                }
                runtime_payload = {
                    **common,
                    "runtime_checks_passed": crm.measure(epoch_ref, package_ref)
                    == io.document(io.referenced(crm_receipt)),
                    "candidate_revision": attempt["plan"]["candidate"]["revision"],
                    "candidate_image": attempt["plan"]["candidate"]["image"],
                }
                proofs = {
                    "closure": captured["closure_ref"],
                    "backup": captured["backup_ref"],
                    "offhost_restore": offhost,
                }
                for kind, body in (
                    ("native_acceptance", native_payload),
                    ("runtime_acceptance", runtime_payload),
                ):
                    proofs[kind] = signer.sign(
                        kind, attempt["plan_ref"], body, attempt["directory"] / (kind + ".json")
                    )
                    trace.bind_verified_inspection(
                        fixture, attempt, signer, kind, proofs[kind], inspection
                    )
                store = attempt["store"]
                mark("actual_signed_activation_proofs")
                with _phase("commit_recovery"):
                    committed = store.commit_recovery(attempt["envelope"], proofs)
                self.assertEqual(store.head()["sha256"], committed["sha256"])
                self.assertEqual(len(list(store.history.iterdir())), 1)
                mark("actual_durable_head_committed")
                with _phase("open_ingress"):
                    controller.open_ingress(committed, store=store)
                mark("new_ingress_opened")
                for unit in authority.ALLOWED:
                    endpoint = (
                        fixture.root / "execution/roles" / epochs.ROLES[unit] / "run/control.sock"
                    )
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.settimeout(3)
                        client.connect(str(endpoint))
                        client.sendall(io.canonical(unknown))
                        self.assertEqual(client.recv(128), b"DENY\n")
                with _phase("finish"):
                    completed = controller.finish(store=store)
                self.assertIsNone(store.pending())
                self.assertIsNone(store._active_recovery())
                self.assertEqual(store.head()["sha256"], committed["sha256"])
                mark("committed_opened_and_finished")
                for unit in authority.UNITS:
                    self.assertEqual(
                        (fixture.root / "barriers" / unit).read_bytes(), authority.DENY_BYTES
                    )
                self.assertEqual(
                    io.referenced(fixture.origin["hold_ref"]),
                    io.referenced(package["original_hold_ref"]),
                )
                fixture._write(
                    attempt["directory"] / "completion.json",
                    {
                        "ok": True,
                        "plan_ref": attempt["plan_ref"],
                        "commit": committed,
                        "cold_completion_ref": completed,
                    },
                )
                fixture._write(
                    attempt["directory"] / "progress.json",
                    {
                        "phase": "complete",
                        "completion": io.reference(attempt["directory"] / "completion.json"),
                    },
                )
                terminal_cuts = []

                def terminal_observer():
                    self.assertEqual(store.head()["sha256"], committed["sha256"])
                    observed = coordinator._native_observation(
                        attempt["plan"], controller, "activated"
                    )
                    terminal_cuts.append(observed["observed_monotonic_ns"])
                    return release.save(attempt["directory"], "cold-terminal-native", observed)

                with _phase("terminal_control_capture"):
                    terminal = archive.capture(
                        fixture.control,
                        attempt["plan_ref"],
                        captured["captured"]["manifest"],
                        fixture.root / "control-terminal-package",
                        stage="terminal",
                        codec=5,
                        observer=terminal_observer,
                        consumer_roots={
                            attempt["plan"]["candidate"]["consumer_sha256"]: attempt[
                                "consumer_root"
                            ]
                        },
                        authority_reader=fixture.provider.read_reference,
                        authority_file_reader=fixture.custody.read,
                    )
                # Final live cut uses the real clock, after every captured
                # source byte/version reread. Historical restore below grants
                # no permission and uses its own bounded deadline.
                with _phase("terminal_capture_exit_freshness"):
                    terminal_observer()
                print(
                    "PRIVATE_COLD_CAPTURE_FRESHNESS "
                    + io.canonical(
                        {
                            "last_fresh_monotonic_ns": terminal_cuts[-1],
                            "binding_deadline_ns": fixture.binding["deadline_ns"],
                            "terminal_native_cuts": len(terminal_cuts),
                            "historical_restore_authority": False,
                        }
                    )
                    .decode()
                    .strip(),
                    flush=True,
                )
                mark("terminal_control_captured")
                with _phase("terminal_control_restore"):
                    restored = archive.verify_and_restore(
                        fixture.root / "control-terminal-package",
                        terminal["manifest_sha256"],
                        fixture.root / "terminal-restore",
                        plan_ref=attempt["plan_ref"],
                        backup_manifest_ref=captured["captured"]["manifest"],
                        stage="terminal",
                        logical_root=fixture.control,
                        custody=fixture.custody,
                    )
                self.assertTrue(restored["semantic_graph_verified"])
                self.assertFalse(restored["semantic"]["pending_present"])
                self.assertFalse(restored["semantic"]["active_recovery_present"])
                mark("terminal_graph_independently_restored")
                self.assertFalse(
                    list((fixture.root / "execution/roles").glob("*/state/requests/*/effect.json"))
                )
                receipt = trace.measured_receipt(
                    fixture,
                    started,
                    stages=[
                        "failed_origin",
                        "parent_actual_SACE",
                        "cold_recovery",
                        "full_data_and_control_restore",
                        "six_actual_native_entries",
                        "private_crm_readback",
                        "crm_final_plan_entry",
                        "durable_HEAD",
                        "fresh_namespace_open",
                        "finish",
                        "terminal_graph_restore",
                    ],
                )
                print("PRIVATE_COLD_TRACE " + io.canonical(receipt).decode().strip(), flush=True)


if __name__ == "__main__":
    unittest.main()
