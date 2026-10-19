from __future__ import annotations

import copy
import os
import sys
import unittest
from unittest.mock import patch

from scripts import j1_forward_cold_history as history
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal


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
                self.assertEqual(fixture.store.pending_path.read_bytes(), original_pending)
                self.assertGreater(rehearsal.measure_private_bytes(fixture.root), 0)


if __name__ == "__main__":
    unittest.main()
