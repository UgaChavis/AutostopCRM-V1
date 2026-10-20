"""A concrete private trace, with actual files, crypto, restore and six entries."""

from __future__ import annotations

import copy
import os
import socket
import sys
import time
import unittest
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts import cold_trace as trace
from scripts import j1_forward_archive as archive
from scripts import j1_forward_cold_authority as authority
from scripts import j1_forward_cold_backup as cold_backup
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_io as io
from scripts import j1_forward_rehearsal_crm as crm_boundary
from scripts import j1_forward_release as release
from scripts import j1_forward_release_faults as faults
from scripts import j1_forward_release_rehearsal as rehearsal
from scripts import j1_forward_variant as variant
from scripts.cold_v2_rehearsal import TypedFixture, stage_voice_models, verify_restored_models


@unittest.skipUnless(
    sys.platform.startswith("linux") and hasattr(os, "pidfd_open"),
    "Requires real Linux flock, AF_UNIX and SO_PEERCRED",
)
class ColdV2JoinedTraceTests(unittest.TestCase):
    def test_failed_origin_full_restore_six_entries_crash_head_commit_and_finish(self):
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
                changed = copy.deepcopy(io.document(io.referenced(offhost)))
                changed["payload"]["actual_restore"] = False
                corrupt_ref = fixture._write(attempt["directory"] / "corrupt-offhost.json", changed)
                before_children = len(fixture.children)
                with self.assertRaisesRegex(io.Refusal, "proof_signature_invalid"):
                    controller.issue(attempt["plan_ref"], captured["backup_ref"], corrupt_ref)
                with self.assertRaisesRegex(io.Refusal, "proof_deadline_expired"):
                    rehearsal.signatures.verify_proof(
                        offhost,
                        "offhost_restore",
                        attempt["plan_ref"],
                        io.reference(signer.public),
                        now=datetime.now(UTC) + timedelta(seconds=700),
                        plan_validator=variant.validate_plan,
                    )
                self.assertEqual(len(fixture.children), before_children)
                controller.issue(attempt["plan_ref"], captured["backup_ref"], offhost)
                mark("actual_signed_restore_issued")
                accepted = {}
                for unit in authority.ALLOWED:
                    controller.submit(unit)
                    accepted[unit] = controller.accept(unit)
                    snapshot = controller.store(unit).read()
                    self.assertTrue(snapshot["state"]["execution_possible"])
                    self.assertTrue(controller.submit(unit)["historical"])
                    self.assertEqual(controller.store(unit).read(), snapshot)
                    mark("actual_native_entry_accepted:" + unit)
                entered_crm = controller.consume_crm_start(fixture.parent.request_ref)
                self.assertTrue(entered_crm["entry_consumed"])
                with self.assertRaisesRegex(io.Refusal, "already_consumed_no_retry"):
                    controller.consume_crm_start(fixture.parent.request_ref)
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
                faults.crash(
                    fixture,
                    fixture.root / "state-faults/actual-cold-head",
                    attempt["plan_ref"],
                    operation="commit_recovery",
                    target="history",
                    phase="after",
                    proofs=proofs,
                    recovery_ref=attempt["recovery_ref"],
                    state_root=store.root,
                )
                with self.assertRaisesRegex(io.Refusal, "state_head_mismatch"):
                    store.head()
                committed = store.recover_recovery(attempt["envelope"])
                self.assertEqual(store.commit_recovery(attempt["envelope"], proofs), committed)
                self.assertEqual(len(list(store.history.iterdir())), 1)
                mark("actual_history_crash_recovered_to_durable_head")
                controller.commit(committed, store=store)
                controller.open_ingress(committed, store=store)
                for unit in authority.ALLOWED:
                    endpoint = (
                        fixture.root / "execution/roles" / epochs.ROLES[unit] / "run/control.sock"
                    )
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.settimeout(3)
                        client.connect(str(endpoint))
                        client.sendall(io.canonical(unknown))
                        self.assertEqual(client.recv(128), b"DENY\n")
                completed = controller.finish(store=store)
                self.assertIsNone(store.pending())
                self.assertIsNone(store._active_recovery())
                self.assertEqual(store.head()["sha256"], committed["sha256"])
                self.assertEqual(controller.finish(store=store), completed)
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
                terminal = archive.capture(
                    fixture.control,
                    attempt["plan_ref"],
                    captured["captured"]["manifest"],
                    fixture.root / "control-terminal-package",
                    stage="terminal",
                    codec=5,
                    observer=lambda: {
                        **captured["closure_ref"],
                        "attempt_id": attempt["plan"]["attempt_id"],
                    },
                    consumer_roots={
                        attempt["plan"]["candidate"]["consumer_sha256"]: attempt["consumer_root"]
                    },
                    authority_reader=fixture.provider.read_reference,
                    authority_file_reader=fixture.custody.read,
                )
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
                        "history_crash_and_recovery",
                        "durable_HEAD",
                        "fresh_namespace_open",
                        "finish",
                        "terminal_graph_restore",
                    ],
                )
                print("PRIVATE_COLD_TRACE " + io.canonical(receipt).decode().strip(), flush=True)


if __name__ == "__main__":
    unittest.main()
