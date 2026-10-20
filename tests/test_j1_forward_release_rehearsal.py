from __future__ import annotations

import copy
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_cold_history as history
from scripts import j1_forward_io as io
from scripts import j1_forward_rehearsal_crm as crm_boundary
from scripts import j1_forward_release_faults as faults
from scripts import j1_forward_release_rehearsal as rehearsal
from scripts import j1_forward_state as state


@unittest.skipUnless(
    sys.platform.startswith("linux") and hasattr(os, "pidfd_open"),
    "Requires real Linux flock, AF_UNIX and SO_PEERCRED",
)
class ReleaseRehearsalFilesystemTests(unittest.TestCase):
    def test_private_custodian_pins_host_interpreter_and_actual_dynamic_sqlite(self):
        with rehearsal.TraceFixture() as fixture, fixture.custody:
            reference = rehearsal.private_sqlite_runtime(fixture)
            value = io.document(io.referenced(reference))
            self.assertEqual(value["schema"], "autostop.j1.private-sqlite-runtime.v2")
            self.assertEqual(value["interpreter_version"][6], 0)
            command = [
                value["interpreter_ref"]["path"],
                "-I",
                "-S",
                "-B",
                "-c",
                "import json,sys; sys.path.insert(0,sys.argv[1]); "
                "from scripts.j1_forward_release_rehearsal import verify_private_sqlite; "
                "print(json.dumps(verify_private_sqlite(sys.argv[2],json.loads(sys.argv[3]))))",
                str(rehearsal.SOURCE),
                str(fixture.root),
                io.canonical(reference).decode(),
            ]

            def observe():
                return subprocess.run(
                    command,
                    env=rehearsal.private_sqlite_environment(fixture),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )

            result = observe()
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            actual = io.document(result.stdout)
            self.assertEqual(actual["sqlite_version"], "3.51.3")
            self.assertEqual(actual["library_ref"], value["library_ref"])
            self.assertEqual(
                actual["mapping_identity"][1], Path(value["library_ref"]["path"]).stat().st_ino
            )
            # The old fixture receipt remains readable with its original
            # exact SQLite/mapped-library checks and without a new permit.
            legacy = {
                key: item
                for key, item in value.items()
                if key not in {"interpreter_ref", "interpreter_version"}
            }
            legacy["schema"] = "autostop.j1.private-sqlite-runtime.v1"
            path = Path(reference["path"])
            path.write_bytes(io.canonical(legacy))
            command[-1] = io.canonical(fixture.provider.reference(path)).decode()
            old = observe()
            self.assertEqual(old.returncode, 0, old.stderr.decode())

            # Rehashing the fixture receipt cannot authorize another executable.
            value["interpreter_ref"]["sha256"] = "f" * 64
            path.write_bytes(io.canonical(value))
            command[-1] = io.canonical(fixture.provider.reference(path)).decode()
            refused = observe()
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn(b"rehearsal_sqlite_interpreter_identity", refused.stderr)

    def test_private_child_copies_only_selected_sqlite_and_rejects_changed_copy(self):
        with rehearsal.TraceFixture() as fixture, fixture.custody:
            with patch.dict(
                os.environ,
                {"LD_LIBRARY_PATH": "/untrusted/inherited", "GH_TOKEN": "ARTIFICIAL_UNUSED"},
            ):
                reference = rehearsal.private_sqlite_runtime(fixture)
                environment = rehearsal.private_sqlite_environment(fixture)
            value = io.document(io.referenced(reference))
            copied = Path(value["library_ref"]["path"])
            self.assertEqual(copied, fixture.root / "custodian-runtime/libsqlite3.so.0")
            self.assertEqual(value["library_ref"]["sha256"], value["source_sha256"])
            self.assertEqual(environment["LD_LIBRARY_PATH"], str(copied.parent))
            self.assertNotIn("GH_TOKEN", environment)
            copied.write_bytes(b"changed private copy")
            with self.assertRaisesRegex(io.Refusal, "sqlite_copy_changed"):
                rehearsal.private_sqlite_runtime(fixture)

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

    def test_artificial_crm_boundary_measures_peer_http_and_actual_unchanged_sqlite(self):
        import sqlite3

        with rehearsal.TraceFixture() as fixture:
            package_ref = fixture.produce()
            with fixture.custody:
                rehearsal.synthetic_data(fixture)
                epoch_ref = fixture._write(
                    fixture.root / "external-boundary-test-epoch.json",
                    {"scope": "external-boundary-test-only-not-dispatch-authority"},
                )
                crm = crm_boundary.PrivateCRM(
                    fixture,
                    revision=fixture.source["revision"],
                    image="sha256:" + "4" * 64,
                )
                first = crm.measure(epoch_ref, package_ref)
                self.assertEqual(crm.measure(epoch_ref, package_ref), first)
                self.assertFalse(first["production_crm_application_executed"])
                self.assertEqual(first["actor"]["pid"], crm.process.pid)
                with sqlite3.connect(crm.database) as connection:
                    connection.execute("INSERT INTO records VALUES(2,'changed')")
                with self.assertRaisesRegex(io.Refusal, "reference_hash_mismatch"):
                    crm.measure(epoch_ref, package_ref)
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
