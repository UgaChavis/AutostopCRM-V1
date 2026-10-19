"""Actual private S/A/C/E files; only the native incarnation/submit is mocked."""

from __future__ import annotations

import contextlib
import os
import socket
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from scripts import j1_forward_cold_controller_native as controller
from scripts import j1_forward_cold_qualification as qualification
from scripts import j1_forward_io as io
from scripts import j1_forward_launch_native as native
from scripts import j1_forward_launch_permission as journal
from scripts import j1_forward_release_rehearsal as rehearsal
from tests import test_j1_forward_cold_qualification as qualified
from tests import test_j1_forward_launch_native as base


@unittest.skipUnless(os.name == "posix", "Linux root-owned launch journal and systemd admission")
class ColdControllerAdmissionTests(unittest.TestCase):
    def setUp(self):
        base.NativeLaunchTests.setUp(self)
        custody = rehearsal.PrivateFilesystem(self.root)
        custody.__enter__()
        self.addCleanup(custody.__exit__, None, None, None)
        for name, value in (
            ("REQUESTS", self.root / "requests"),
            ("JOURNALS", self.root / "cold-controller-launch"),
            ("FORWARD", self.scripts / "j1_forward_cold_release.py"),
        ):
            patcher = patch.object(controller, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cold_script = controller.FORWARD
        journal._new(self.cold_script, b"# immutable cold fixture consumer, never executed\n")
        self.consumers["scripts/j1_forward_cold_release.py"] = io.sha(self.cold_script.read_bytes())
        controller.REQUESTS.mkdir(mode=0o700)
        request_dir = controller.REQUESTS / self.attempt
        request_dir.mkdir(mode=0o700)
        source = {"revision": "1" * 40, "tree": "2" * 40, "consumers": self.consumers}
        inputs = io.write_new(request_dir / "inputs.json", {"scope": "native_boundary_fixture"})
        now = datetime.now(UTC)
        self.request = {
            "schema": controller.SCHEMA,
            "scope": "bounded_cold_prepare",
            "attempt_id": self.attempt,
            "host": socket.gethostname(),
            "boot_id": native.boot(),
            "owner_generation": str(uuid.uuid4()),
            "source": source,
            "candidate": {
                "revision": source["revision"],
                "tree": source["tree"],
                "image": "sha256:" + "3" * 64,
                "consumer_sha256": io.sha(io.canonical(self.consumers)),
            },
            "candidate_manager_sha": "4" * 40,
            "inputs_ref": inputs,
            "ci_ref": inputs,
            "qualification_ref": inputs,
            "created_at": now.isoformat(),
            "deadline_utc": (now + timedelta(seconds=120)).isoformat(),
            "runtime_seconds": 900,
            "old_writer_allowed": False,
        }
        source = {
            **{key: source[key] for key in ("revision", "tree")},
            "consumer_sha256": self.request["candidate"]["consumer_sha256"],
        }
        github_rows = qualified.responses()
        proof = qualification.measure(
            source, quality_run=101, rehearsal_run=102, fetch=github_rows.__getitem__
        )
        self.request["qualification_ref"] = io.write_new(request_dir / "qualified.json", proof)
        verify = qualification.verify_qualification
        gh_boundary = patch.object(
            qualification,
            "verify_qualification",
            side_effect=lambda reference, source, replay=True: verify(
                reference, source, replay=replay, fetch=github_rows.__getitem__
            ),
        )
        gh_boundary.start()
        self.addCleanup(gh_boundary.stop)
        self.reference = io.write_new(controller.request_path(self.attempt), self.request)

    def accepted(self):
        receipt = controller.issue(self.reference, self.baseline["deployment_lock_identity"])
        self.assertFalse(receipt["final_activation_authorized"])
        store, snapshot, context = controller.validate(self.attempt)
        native._transition(store, snapshot["ticket"], "submit", {"owner": native.owner()})
        self.worker = {"pid": 101, "start_ticks": 50, "invocation_id": "e" * 32}
        with patch.object(native, "observe_worker", return_value=self.worker):
            native.publish_acceptance(store, store.read(), context)
        return store

    @contextlib.contextmanager
    def deployment_lock(self, expected):
        with base.NativeLaunchTests.deployment_lock(self, expected):
            yield 42

    def test_request_entry_retains_one_d_and_capsule_is_historical_not_final_permission(self):
        store = self.accepted()
        with patch.object(native, "observe_worker", return_value=self.worker):
            with controller.Admission(self.attempt, self.deployment_lock) as admitted:
                self.assertTrue(self.locked)
                self.assertEqual(admitted.deployment_fd, 42)
                self.assertTrue(store.read()["state"]["execution_possible"])
                capsule = controller.capture_capsule(self.attempt, self.root / "capsule")
                captured = controller.validate_capsule(capsule, read_reference=io.referenced)
                self.assertFalse(captured["current_authority_asserted"])
                self.assertEqual(captured["request_ref"], self.reference)
        self.assertFalse(self.locked)
        self.assertEqual(store.read()["state"]["sequence"], captured["captured_sequence"])

    def test_revoke_during_d_acquisition_blocks_preparation_entry(self):
        store = self.accepted()

        @contextlib.contextmanager
        def revoke_before_lock(expected):
            controller.revoke(self.attempt)
            with self.deployment_lock(expected) as descriptor:
                yield descriptor

        with patch.object(native, "observe_worker", return_value=self.worker):
            with self.assertRaisesRegex(journal.Refusal, "revoked"):
                with controller.Admission(self.attempt, revoke_before_lock):
                    self.fail("revoked request must not enter preparation")
        self.assertFalse(store.read()["state"]["execution_possible"])
        self.assertFalse(self.locked)

    def test_context_or_source_drift_and_float_runtime_are_refused(self):
        self.accepted()
        self.cold_script.write_bytes(b"# changed\n")
        with self.assertRaisesRegex(journal.Refusal, "consumer_changed"):
            controller.validate(self.attempt)
        for changed in (900.0, True, 901):
            with self.subTest(runtime=changed), self.assertRaises(io.Refusal):
                controller.validate_request({**self.request, "runtime_seconds": changed})


if __name__ == "__main__":
    unittest.main()
