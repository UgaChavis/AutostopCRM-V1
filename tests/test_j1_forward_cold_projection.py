"""Actual State bytes and actual flock protect the public admission cut."""

from __future__ import annotations

import os
import sys
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_projection as projection
from scripts import j1_forward_cold_state as cold
from scripts import j1_forward_io as io
from scripts import j1_forward_recovery_state as recovery
from tests import test_j1_forward_state as state_tests


@unittest.skipUnless(sys.platform.startswith("linux"), "Requires real Linux read-only intent flock")
class ColdProjectionTests(unittest.TestCase):
    def setUp(self):
        self.files = state_tests.ForwardStateTests()
        self.files.setUp()
        self.addCleanup(self.files.doCleanups)
        self.root = self.files.root
        execution = self.root / "execution"
        execution.mkdir()
        dispatch = execution / "dispatch"
        dispatch.mkdir()
        lock = dispatch / "dispatch.lock"
        lock.write_bytes(b"")
        info = lock.stat()
        self.common = {key: str(uuid.uuid4()) for key in epochs.COMMON}
        self.common["generation"] = self.common["epoch_id"]
        self.fence = {
            "schema": "autostop.j1.cold-dispatch-fence.v1",
            **self.common,
            "state_path": str(dispatch / "state.json"),
            "dispatch_lock_path": str(lock),
            "dispatch_lock_identity": {
                "device": info.st_dev,
                "inode": info.st_ino,
                "ctime_ns": info.st_ctime_ns,
            },
        }
        fence_ref = io.write_new(dispatch / "fence.json", self.fence)
        refs = {
            role: io.write_new(
                execution / (role + ".json"),
                {**self.common, "role": role, "ingress_fence_ref": fence_ref},
            )
            for role in epochs.CONSUMERS
        }
        self.package_ref = io.write_new(execution / "package.json", {"fixture": True})
        self.epoch = {
            "schema": epochs.SCHEMA,
            "scope": "native_fixture",
            **self.common,
            "package_ref": self.package_ref,
            "runtime_bindings": refs,
            "journal_root": str(execution / "journals"),
        }
        self.epoch_ref = io.write_new(execution / "epoch.json", self.epoch)
        self.plan = self.files.plan(attempt=self.common["attempt_id"])
        self.plan.update(
            schema=cold.PLAN_SCHEMA,
            scope="crm_and_manager_all_profiles_cold",
            cold_authority_package_ref=self.package_ref,
            dispatch_epoch_ref=self.epoch_ref,
            candidate_manager_sha="a" * 40,
            cold_crm_spec_ref=self.files.plan()["baseline_ref"],
        )
        self.plan_ref = io.write_new(execution / "plan.json", self.plan)
        self.store = cold.ColdStore(self.files.store_root, self.files.base)
        self.store.begin(self.plan)
        self.commit_ref = self.store.commit(self.plan, self.files.proofs())
        self.provider = types.SimpleNamespace(scope="native_fixture", read_reference=io.referenced)
        self.closed = {
            "schema": "autostop.j1.cold-dispatch-state.v1",
            **self.common,
            "state": "closed",
            "plan_ref": None,
            "commit_ref": None,
            "admission_ref": None,
        }
        io.write_new(dispatch / "state.json", self.closed)

    def publish(self, descriptor):
        # Native package/7-manifest contract has its own real process fixture.
        # Here only that observation seam is substituted; actual State, source
        # hashes, public file custody, fdinfo EX ownership and flock stay real.
        with (
            patch.object(projection.native, "verify_package", return_value={"package": {}}),
            patch.object(projection.epochs, "verify"),
        ):
            return projection.publish(
                self.plan_ref,
                self.commit_ref,
                self.epoch_ref,
                store=self.store,
                provider=self.provider,
                dispatch_fd=descriptor,
            )

    def test_projection_copies_exact_committed_bytes_and_has_readable_modes(self):
        descriptor = epochs.locked_fence(self.fence, exclusive=True)
        try:
            reference = self.publish(descriptor)
            document = io.document(io.referenced(reference))
            self.assertEqual(projection.SCHEMA, document["schema"])
            for name in projection.NAMES:
                self.assertEqual(
                    io.referenced(document["source_refs"][name]),
                    io.referenced(document["projected_refs"][name]),
                )
                self.assertEqual(
                    0o644, Path(document["projected_refs"][name]["path"]).stat().st_mode & 0o777
                )
            self.assertEqual(reference, self.publish(descriptor))
            self.assertEqual(self.closed, io.document(io.read_protected(self.fence["state_path"])))
        finally:
            os.close(descriptor)

    def test_shared_lock_and_changed_actual_head_cannot_publish(self):
        shared = epochs.locked_fence(self.fence, exclusive=False)
        try:
            with self.assertRaises(io.Refusal):
                self.publish(shared)
        finally:
            os.close(shared)
        descriptor = epochs.locked_fence(self.fence, exclusive=True)
        try:
            io.atomic_replace(
                self.store.head_path,
                {"schema": "autostop.j1.crm-forward-head.v1", "sequence": 1, "sha256": "b" * 64},
            )
            with self.assertRaises(io.Refusal):
                self.publish(descriptor)
            self.assertFalse((Path(self.fence["state_path"]).parent / "proofs").exists())
        finally:
            os.close(descriptor)

    def test_next_mutation_closes_epoch_before_body_and_holds_ex_through_it(self):
        opened = {
            **self.closed,
            "state": "open",
            "plan_ref": self.plan_ref,
            "commit_ref": self.commit_ref,
            "admission_ref": self.plan_ref,
        }
        io.atomic_replace(self.fence["state_path"], opened)
        before = self.store.head_path.read_bytes()
        with projection.close_before_mutation(self.store):
            self.assertEqual(self.closed, io.document(io.read_protected(self.fence["state_path"])))
            with self.assertRaises(BlockingIOError):
                epochs.locked_fence(self.fence, exclusive=False)
        self.assertEqual(before, self.store.head_path.read_bytes())
        self.assertEqual(self.closed, io.document(io.read_protected(self.fence["state_path"])))

    def test_recovery_refusal_closes_actual_published_open_before_validation(self):
        descriptor = epochs.locked_fence(self.fence, exclusive=True)
        try:
            admission = self.publish(descriptor)
        finally:
            os.close(descriptor)
        opened = {
            **self.closed,
            "state": "open",
            "plan_ref": self.plan_ref,
            "commit_ref": self.commit_ref,
            "admission_ref": admission,
        }
        changed_plan = {
            **{name: dict(self.plan_ref) for name in recovery.REFERENCE_FIELDS},
            "schema": cold.RECOVERY_SCHEMA,
            "recovery_id": str(uuid.uuid4()),
            "nonce": "c" * 64,
            "deployment_lock_identity": [1, 123],
            "plan_ref": {**self.plan_ref, "sha256": "f" * 64},
        }
        head = self.store.head_path.read_bytes()
        for method in ("commit_recovery", "recover_recovery"):
            for envelope, reason in (
                ({}, "cold_recovery_fields"),
                (changed_plan, "cold_state_reference_hash"),
            ):
                with self.subTest(method=method, refusal=reason):
                    io.atomic_replace(self.fence["state_path"], opened)
                    arguments = (envelope, {}) if method == "commit_recovery" else (envelope,)
                    with self.assertRaisesRegex(io.Refusal, reason):
                        getattr(self.store, method)(*arguments)
                    self.assertEqual(
                        self.closed, io.document(io.read_protected(self.fence["state_path"]))
                    )
                    self.assertEqual(head, self.store.head_path.read_bytes())
                    self.assertNotIn("_historical_validation", self.store.__dict__)

    def test_recovery_of_history_before_head_closes_unpublished_epoch(self):
        # Actual durable commit is present, but simulate process death before
        # the first HEAD publication. This is an existing recoverable state.
        self.store.head_path.unlink()
        with self.assertRaisesRegex(io.Refusal, "state_head_mismatch"):
            self.store.head()
        io.atomic_replace(
            self.fence["state_path"],
            {
                **self.closed,
                "state": "open",
                "plan_ref": self.plan_ref,
                "commit_ref": self.commit_ref,
                "admission_ref": self.plan_ref,
            },
        )
        original = state_tests.state.Store._publish_head

        def publish_head(store, *arguments):
            self.assertEqual(self.closed, io.document(io.read_protected(self.fence["state_path"])))
            return original(store, *arguments)

        with patch.object(state_tests.state.Store, "_publish_head", publish_head):
            self.store.recover(self.plan)
        self.assertEqual(self.commit_ref["sha256"], self.store.head()["sha256"])


if __name__ == "__main__":
    unittest.main()
