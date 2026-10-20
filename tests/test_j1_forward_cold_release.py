from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import j1_forward_cold_lifecycle as lifecycle
from scripts import j1_forward_cold_projection as projection
from scripts import j1_forward_cold_release as release
from scripts import j1_forward_cold_state as cold_state
from scripts import j1_forward_io as io
from scripts import j1_forward_release_rehearsal as rehearsal
from tests import test_j1_forward_cold_projection as projection_tests


class ColdObservationTests(unittest.TestCase):
    def test_expiry_during_closed_slot_readback_cannot_emit_a_native_proof(self):
        binding = {
            **dict.fromkeys(("attempt_id", "boot_id", "generation"), str(uuid.uuid4())),
            "consumer_sha256": "1" * 64,
            "candidate_crm_sha": "2" * 40,
            "candidate_manager_sha": "3" * 40,
            "deployment_lock_identity": [1, 2],
            "not_before_ns": 1,
            "deadline_ns": 100,
        }
        package = {"binding": binding, "policies": {}}
        clock = [99]
        controller = SimpleNamespace(
            verify=lambda: ({"package": package, "history": {"inventory": {}}}, None),
            _closed_slots=lambda: clock.__setitem__(0, 100),
            provider=SimpleNamespace(scope="native_fixture"),
        )
        with patch.object(release.time, "monotonic_ns", side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(io.Refusal, "binding_expired_or_window"):
                release._native_observation({}, controller, "closed")


@unittest.skipUnless(sys.platform.startswith("linux"), "Requires real Linux read-only intent flock")
class ColdOpenCommitTests(unittest.TestCase):
    def prepare(self, *, wrong_proof=None):
        files = projection_tests.ColdProjectionTests()
        files.setUp()
        self.addCleanup(files.doCleanups)
        root = files.root
        signer = rehearsal.TemporaryCustodian(root / "reviewer", root / "reviewer.pem")
        self.enterContext(patch.object(rehearsal.signatures, "REVIEW_KEY_PATH", str(signer.public)))
        baseline = io.write_new(
            root / "open-baseline.json", {"review_key_ref": io.reference(signer.public)}
        )
        now = datetime.now(UTC)
        plan = {
            **files.plan,
            "baseline_ref": baseline,
            "created_at": now.isoformat(),
            "deadline_utc": (now + timedelta(seconds=600)).isoformat(),
        }
        plan_ref = io.write_new(root / "open-plan.json", plan)
        accepted = "9" * 64
        payload = {
            "phase": "activated",
            "plan_sha256": plan_ref["sha256"],
            "baseline_sha256": baseline["sha256"],
            "cold_guards_executed": True,
            "execution_acceptance_sha256": accepted,
            "runtime_checks_passed": True,
            "candidate_revision": plan["candidate"]["revision"],
            "candidate_image": plan["candidate"]["image"],
        }
        proofs = {kind: baseline for kind in ("closure", "backup", "offhost_restore")}
        for kind in ("native_acceptance", "runtime_acceptance"):
            body = dict(payload)
            if kind == wrong_proof:
                body[
                    "execution_acceptance_sha256"
                    if kind == "native_acceptance"
                    else "candidate_revision"
                ] = "e" * (64 if kind == "native_acceptance" else 40)
            proofs[kind] = signer.sign(kind, plan_ref, body, root / (kind + ".json"))
        store = cold_state.ColdStore(root / "open-state", files.files.base)
        store.begin(plan)
        commit_ref = store.commit(plan, proofs)
        controller = lifecycle.Controller.__new__(lifecycle.Controller)
        controller.root = root / "controller"
        controller.root.mkdir(mode=0o700)
        controller.provider, controller.epoch = files.provider, files.epoch
        controller.package_ref, controller.epoch_ref = files.package_ref, files.epoch_ref
        # This substitutes only independent native readiness. Real State HEAD,
        # signed activation proofs, dispatch EX, projection and OPEN stay real.
        controller.ready = lambda: {"acceptance_sha256": accepted}
        controller.admitted = lambda: {
            "plan_ref": plan_ref,
            "backup_ref": baseline,
            "offhost_ref": baseline,
        }
        controller.verify = lambda: ({"package": {}}, files.fence)
        self.enterContext(
            patch.object(projection.native, "verify_package", return_value={"package": {}})
        )
        self.enterContext(patch.object(projection.epochs, "verify"))
        return files, controller, store, commit_ref

    def test_open_without_an_earlier_commit_checks_signed_proofs_and_publishes_projection(self):
        files, controller, store, commit_ref = self.prepare()
        self.assertFalse((controller.root / "commit.json").exists())
        opened = controller.open_ingress(commit_ref, store=store)
        self.assertEqual(io.document(io.referenced(opened))["commit_ref"], commit_ref)
        committed = io.document(io.read_protected(controller.root / "commit.json"))
        self.assertEqual(committed["commit_ref"], commit_ref)
        dispatch = io.document(io.read_protected(files.fence["state_path"]))
        self.assertEqual(dispatch["state"], "open")
        self.assertEqual(dispatch["commit_ref"], commit_ref)
        projected = io.document(io.referenced(dispatch["admission_ref"]))
        self.assertEqual(
            io.referenced(projected["projected_refs"]["commit"]), io.referenced(commit_ref)
        )

    def test_wrong_signed_activation_payload_keeps_dispatch_closed_without_an_earlier_commit(self):
        for kind, refusal in (
            ("native_acceptance", "slot_independent_native_required"),
            ("runtime_acceptance", "slot_independent_runtime_required"),
        ):
            with self.subTest(kind=kind):
                files, controller, store, commit_ref = self.prepare(wrong_proof=kind)
                with self.assertRaisesRegex(io.Refusal, refusal):
                    controller.open_ingress(commit_ref, store=store)
                self.assertEqual(
                    io.document(io.read_protected(files.fence["state_path"])), files.closed
                )
                self.assertFalse((controller.root / "commit.json").exists())
                self.assertFalse((controller.root / "opened.json").exists())

    def test_changed_actual_head_keeps_dispatch_closed_without_an_earlier_commit(self):
        files, controller, store, commit_ref = self.prepare()
        io.atomic_replace(
            store.head_path,
            {"schema": "autostop.j1.crm-forward-head.v1", "sequence": 1, "sha256": "f" * 64},
        )
        with self.assertRaisesRegex(io.Refusal, "state_head_mismatch"):
            controller.open_ingress(commit_ref, store=store)
        self.assertEqual(io.document(io.read_protected(files.fence["state_path"])), files.closed)
        self.assertFalse((controller.root / "commit.json").exists())


@unittest.skipUnless(os.name == "posix", "Requires real Linux flock, AF_UNIX and SO_PEERCRED")
class ColdExportTests(unittest.TestCase):
    def test_bounded_stream_hashes_before_stdout_and_rejects_bad_digest(self):
        with tempfile.TemporaryDirectory(dir=Path.home(), prefix="cold-export-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            path = root / "delta.tar.gz"
            raw = b"private synthetic envelope" * 150_000
            with rehearsal.PrivateFilesystem(root):
                path.write_bytes(raw)
                path.chmod(0o600)
                stdout = SimpleNamespace(buffer=Mock())
                reference = {"path": str(path), "sha256": io.sha(raw)}
                with patch.object(release.sys, "stdout", stdout):
                    release._export(path, reference, len(raw))
                chunks = [call.args[0] for call in stdout.buffer.write.call_args_list]
                self.assertEqual(b"".join(chunks), raw)
                self.assertGreater(len(chunks), 1)
                self.assertLessEqual(max(map(len, chunks)), 1024**2)
                stdout.buffer.reset_mock()
                with (
                    patch.object(release.sys, "stdout", stdout),
                    self.assertRaisesRegex(io.Refusal, "digest"),
                ):
                    release._export(path, {**reference, "sha256": "0" * 64}, len(raw))
                stdout.buffer.write.assert_not_called()
                with (
                    patch.object(release.sys, "stdout", stdout),
                    self.assertRaisesRegex(io.Refusal, "file"),
                ):
                    release._export(path, reference, len(raw) - 1)
                stdout.buffer.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
