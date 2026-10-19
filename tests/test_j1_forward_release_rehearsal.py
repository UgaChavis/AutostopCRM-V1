from __future__ import annotations

import copy
import os
import sys
import unittest
from unittest.mock import patch

from scripts import j1_forward_cold_history as history
from scripts import j1_forward_io as io
from scripts import j1_forward_release_faults as faults
from scripts import j1_forward_release_rehearsal as rehearsal
from scripts import j1_forward_state as state


@unittest.skipUnless(
    sys.platform.startswith("linux") and hasattr(os, "pidfd_open"),
    "Requires real Linux flock, AF_UNIX and SO_PEERCRED",
)
class ReleaseRehearsalFilesystemTests(unittest.TestCase):
    def test_real_state_backup_restore_and_ed25519_on_same_actual_cold_origin(self):
        with rehearsal.TraceFixture() as fixture:
            reference = fixture.produce()
            with fixture.custody:
                package = io.document(io.referenced(reference))
                retained = history.load(
                    package["predecessor_ref"], read_reference=io.referenced, fixture=True
                )
                old = io.document(io.referenced(fixture.origin["origin_plan_ref"]))
                self.assertEqual(fixture.store.pending(), old)
                self.assertEqual(retained["anchor"]["plan_ref"], fixture.origin["origin_plan_ref"])
                self.assertEqual(len(retained["inventory"]["actors"]), 6)
                original_pending = fixture.store.pending_path.read_bytes()
                roots = rehearsal.synthetic_data(fixture)
                output = fixture.root / "data-backup"
                restored = fixture.root / "independent-data-restore"
                with patch.object(rehearsal.backup, "ROOTS", roots):
                    context = {
                        "attempt_id": fixture.binding["attempt_id"],
                        "predecessor_sha256": fixture.base_sha256,
                        "candidate_crm_sha": fixture.binding["candidate_crm_sha"],
                        "candidate_manager_sha": fixture.binding["candidate_manager_sha"],
                        "scope_profile_sha256": rehearsal.backup.profile_sha256(),
                    }

                    def observer():
                        fixture.provider.verify_closure(
                            {
                                "package": package,
                                "closure": io.document(io.referenced(package["closure_ref"])),
                                "hold": io.document(io.referenced(package["hold_ref"])),
                            }
                        )
                        return {
                            **package["closure_ref"],
                            "attempt_id": fixture.binding["attempt_id"],
                        }

                    captured = rehearsal.backup.capture(output, context, observer, timeout=60)
                    receipt = rehearsal.backup.verify_and_restore(
                        output, captured["manifest_sha256"], restored, context, timeout=60
                    )
                self.assertTrue(receipt["provided_artifacts_restored_exactly"])
                self.assertTrue(receipt["source_evidence_unchanged"])
                self.assertFalse(receipt["production_restore_performed"])
                public = fixture.root / "config/reviewer.pem"
                custodian = rehearsal.TemporaryCustodian(fixture.root / "offhost-custodian", public)
                proof_path = fixture.root / "offhost-proof.json"
                proof = custodian.sign(
                    "offhost_restore", fixture.origin["origin_plan_ref"], receipt, proof_path
                )
                with patch.object(rehearsal.signatures, "REVIEW_KEY_PATH", str(public)):
                    value = rehearsal.signatures.verify_proof(
                        proof,
                        "offhost_restore",
                        fixture.origin["origin_plan_ref"],
                        io.reference(public),
                    )
                    self.assertEqual(value, receipt)
                    altered = copy.deepcopy(io.document(io.referenced(proof)))
                    altered["payload"]["actual_disposable_restore"] = False
                    altered_ref = io.write_new(fixture.root / "tampered-proof.json", altered)
                    with self.assertRaisesRegex(io.Refusal, "proof_signature_invalid"):
                        rehearsal.signatures.verify_proof(
                            altered_ref,
                            "offhost_restore",
                            fixture.origin["origin_plan_ref"],
                            io.reference(public),
                        )
                    separate = rehearsal.independent_restore(
                        fixture,
                        fixture.origin["origin_plan_ref"],
                        captured,
                        context,
                        roots,
                        custodian,
                    )
                    self.assertNotEqual(separate["pid"], os.getpid())
                    self.assertTrue(separate["data_restore"]["provided_artifacts_restored_exactly"])
                    self.assertIsNone(separate["control_restore"])
                    separate_payload = rehearsal.signatures.verify_proof(
                        separate["proof_ref"],
                        "offhost_restore",
                        fixture.origin["origin_plan_ref"],
                        io.reference(public),
                    )
                    self.assertEqual(separate_payload["scope"], "private_data_restore_only")
                    from scripts import j1_forward_release as release

                    with self.assertRaisesRegex(io.Refusal, "proof_payload_binding"):
                        release.validate_payload(
                            "offhost_restore",
                            separate_payload,
                            fixture.control / "attempts" / old["attempt_id"],
                            fixture.origin["origin_plan_ref"],
                            old,
                            io.document(io.referenced(old["baseline_ref"])),
                        )
                self.assertEqual(fixture.store.pending_path.read_bytes(), original_pending)
                self.assertGreater(rehearsal.measure_private_bytes(fixture.root), 0)

    def test_real_process_death_before_and_after_durable_store_boundaries(self):
        cases = (
            ("begin", "pending", "before"),
            ("begin", "pending", "after"),
            ("commit", "history", "before"),
            ("commit", "history", "after"),
            ("commit", "HEAD", "before"),
            ("commit", "HEAD", "after"),
            ("finish", "pending_unlink", "before"),
            ("finish", "pending_unlink", "after"),
            ("finish", "terminal_sync", "after"),
        )
        with rehearsal.TraceFixture() as fixture:
            fixture.produce()
            with fixture.custody:
                plan_ref = fixture.origin["origin_plan_ref"]
                plan = io.document(io.referenced(plan_ref))
                original_pending = fixture.store.pending_path.read_bytes()
                # These are deliberately non-acceptance artifacts. This test
                # covers real Store durability, not proof or release admission.
                proofs = {
                    kind: fixture._write(
                        fixture.root / "storage-only-artifacts" / (kind + ".json"),
                        {"schema": "private.storage-only-not-release-acceptance", "kind": kind},
                    )
                    for kind in state.PROOF_FIELDS
                }
                for index, (operation, target, phase) in enumerate(cases):
                    with self.subTest(operation=operation, target=target, phase=phase):
                        directory = fixture.root / "state-faults" / str(index)
                        directory.mkdir(mode=0o700, parents=True)
                        store = state.Store(directory / "state", fixture.base_sha256)
                        if operation != "begin":
                            store.begin(plan)
                        if operation == "finish":
                            store.commit(plan, proofs)
                        receipt = faults.crash(
                            fixture,
                            directory,
                            plan_ref,
                            operation=operation,
                            target=target,
                            phase=phase,
                            proofs=proofs,
                        )
                        self.assertEqual(receipt["exit"], 77)
                        if operation == "begin":
                            if phase == "before":
                                self.assertIsNone(store.pending())
                            else:
                                self.assertEqual(store.pending(), plan)
                            store.begin(plan)
                            committed = store.commit(plan, proofs)
                        elif operation == "commit":
                            if target == "history" and phase == "before":
                                self.assertEqual(store.head()["sequence"], 0)
                                committed = store.commit(plan, proofs)
                            else:
                                if target == "HEAD" and phase == "after":
                                    self.assertEqual(store.head()["sequence"], 1)
                                else:
                                    with self.assertRaisesRegex(io.Refusal, "state_head_mismatch"):
                                        store.head()
                                committed = store.recover(plan)
                        else:
                            if store.pending() is not None:
                                store.finish(plan)
                            committed = store.confirm_finished(plan)
                        # A lost reply never allocates a second history record.
                        self.assertEqual(len(list(store.history.iterdir())), 1)
                        self.assertEqual(store.head()["sha256"], committed["sha256"])
                        if store.pending() is not None:
                            repeated = store.commit(plan, proofs)
                            self.assertEqual(repeated, committed)
                            store.finish(plan)
                        self.assertEqual(store.confirm_finished(plan), committed)
                        with self.assertRaisesRegex(io.Refusal, "state_attempt_replayed"):
                            store.begin(plan)
                self.assertEqual(fixture.store.pending_path.read_bytes(), original_pending)


if __name__ == "__main__":
    unittest.main()
