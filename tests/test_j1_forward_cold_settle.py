"""Actual private HEAD/history, source bytes and D/epoch flock on stop.

The original native origin and current kernel/systemd observation are explicit
test boundaries. No production process or service is read or changed.
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import j1_forward_cold_authority as cold
from scripts import j1_forward_cold_controller_native as parent
from scripts import j1_forward_cold_epoch as epochs
from scripts import j1_forward_cold_kernel as kernel
from scripts import j1_forward_cold_projection as projection
from scripts import j1_forward_cold_release as release
from scripts import j1_forward_cold_state as transactions
from scripts import j1_forward_io as io
from scripts import j1_forward_recovery_state as recovery
from scripts import j1_forward_state as state
from tests import test_j1_forward_cold_controller as parent_tests
from tests import test_j1_forward_cold_runtime as runtime_tests

SUCCESS = {
    "SERVICE_RESULT": "success",
    "EXIT_CODE": "exited",
    "EXIT_STATUS": "0",
    "INVOCATION_ID": "e" * 32,
}


class ServiceExitTests(unittest.TestCase):
    def test_all_three_exact_service_values_are_required(self):
        self.assertTrue(release._successful_service_exit(SUCCESS))
        for values in (
            {},
            {**SUCCESS, "SERVICE_RESULT": "timeout"},
            {**SUCCESS, "EXIT_CODE": "killed", "EXIT_STATUS": "TERM"},
            {**SUCCESS, "EXIT_STATUS": "1"},
            {**SUCCESS, "EXIT_STATUS": "00"},
            {key: value for key, value in SUCCESS.items() if key != "EXIT_CODE"},
        ):
            with self.subTest(values=values):
                self.assertFalse(release._successful_service_exit(values))


@unittest.skipUnless(
    sys.platform.startswith("linux"), "Requires real Linux flock, AF_UNIX and SO_PEERCRED"
)
class ColdSettleTests(unittest.TestCase):
    def setUp(self):
        parent_tests.ColdControllerAdmissionTests.setUp(self)
        self.custody = parent_tests.rehearsal.PrivateFilesystem(self.root)
        self.system_directory = parent.journal._directory
        self.install(patch.object(parent.journal, "_directory", self.private_directory))
        self.launch_store = parent_tests.ColdControllerAdmissionTests.accepted(self)
        snapshot = self.launch_store.read()
        for event in ("claim", "enter"):
            parent.native._transition(
                self.launch_store,
                snapshot["ticket"],
                event,
                {"worker": snapshot["state"]["acceptance"]},
            )
        for module, name, value in (
            (release.forward, "ROOT", self.root),
            (release.forward, "ATTEMPTS", self.attempts),
            (release.forward, "STATE", self.root / "state"),
            (release.forward, "LOCK", self.lock),
            (release.native, "STATE", self.root / "cold/v1"),
            (release.native, "CONFIG", self.root / "cold/config"),
        ):
            self.install(patch.object(module, name, value))
        # Actual unprivileged flock in one owned private tree. Product D
        # custody remains root-only; this substitutes only its OS namespace.
        self.install(patch.object(release.forward, "deployment_lock", self.private_deployment))
        self.binding = {
            "attempt_id": self.attempt,
            "boot_id": self.request["boot_id"],
            "generation": self.request["owner_generation"],
            "candidate_crm_sha": self.request["candidate"]["revision"],
            "candidate_manager_sha": self.request["candidate_manager_sha"],
            "consumer_sha256": self.request["candidate"]["consumer_sha256"],
            "deployment_lock_identity": self.baseline["deployment_lock_identity"],
            "not_before_ns": time.monotonic_ns() - 10**9,
            "deadline_ns": time.monotonic_ns() + 120 * 10**9,
        }
        self.epoch_root = release.native.STATE / "epochs" / self.binding["generation"]
        self.dispatch = self.root / "dispatch"
        self.dispatch.mkdir(mode=0o700)
        lock = self.dispatch / "dispatch.lock"
        lock.write_bytes(b"")
        lock.chmod(0o600)
        info = lock.stat()
        self.fence = {
            "schema": "autostop.j1.cold-dispatch-fence.v1",
            **epochs.common(self.binding),
            "state_path": str(self.dispatch / "state.json"),
            "dispatch_lock_path": str(lock),
            "dispatch_lock_identity": {
                "device": info.st_dev,
                "inode": info.st_ino,
                "ctime_ns": info.st_ctime_ns,
            },
        }
        self.properties = {"ActiveState": "active", "SubState": "running", "MainPID": "333"}
        unit_ref = self.write(self.root / "unit.json", {"properties": self.properties})
        policy_ref = self.write(
            self.root / "kernel-policy.json",
            {
                "schema": "autostop.j1.cold-kernel-policy.v2",
                "binding": self.binding,
                "unit_ref": unit_ref,
            },
        )
        kernel_ref = self.write(
            self.root / "kernel.json",
            {
                "schema": "autostop.j1.cold-kernel.v2",
                "binding": self.binding,
                "unit": "private-kernel.service",
                "policy_ref": policy_ref,
                "supervisor_actor": {"pid": 333},
            },
        )
        self.package = {
            "scope": "production_native",
            "binding": self.binding,
            "kernel_ref": kernel_ref,
        }
        package_ref = self.write(release.native.package_path(self.attempt), self.package)
        self.epoch = {
            "schema": epochs.SCHEMA,
            "scope": "production_native",
            **epochs.common(self.binding),
            "package_ref": package_ref,
            "runtime_bindings": dict.fromkeys(epochs.CONSUMERS, unit_ref),
            "slots": dict.fromkeys(epochs.ROLES, self.attempt),
            "journal_root": str(self.epoch_root / "journals"),
        }
        epoch_ref = self.write(self.epoch_root / "epoch.json", self.epoch)
        self.baseline = {
            "schema": transactions.BASELINE_SCHEMA,
            "attempt_id": self.attempt,
            "deployment_lock_identity": self.binding["deployment_lock_identity"],
            "native": {"cold_authority_package_ref": package_ref, "dispatch_epoch_ref": epoch_ref},
            "source_consumers": self.consumers,
            "controller_request_ref": self.reference,
        }
        baseline_ref = self.write(self.directory / "baseline.json", self.baseline)
        self.plan = {
            "schema": cold.PLAN_SCHEMA,
            "scope": cold.PLAN_SCOPE,
            "host": self.request["host"],
            "attempt_id": self.attempt,
            "sequence": 1,
            "predecessor_sha256": "a" * 64,
            "base_sha256": "a" * 64,
            "candidate": self.request["candidate"],
            "baseline_ref": baseline_ref,
            "created_at": self.request["created_at"],
            "deadline_utc": self.request["deadline_utc"],
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
            "candidate_manager_sha": self.request["candidate_manager_sha"],
            "cold_authority_package_ref": package_ref,
            "dispatch_epoch_ref": epoch_ref,
            "cold_crm_spec_ref": unit_ref,
        }
        self.plan_ref = self.write(self.directory / "plan.json", self.plan)
        proofs = {
            name: self.write(self.directory / (name + ".json"), {"artificial_proof_boundary": name})
            for name in state.PROOF_FIELDS
        }
        origin = {**self.plan, "attempt_id": "00000000-0000-4000-8000-000000000001"}
        record = {
            "schema": recovery.COMMIT_SCHEMA,
            "plan": self.plan,
            "origin_plan": origin,
            "recovery_ref": unit_ref,
            "proofs": proofs,
            "previous_sha256": self.plan["base_sha256"],
            "sequence": 1,
        }
        self.commit_ref = self.write(
            release.forward.STATE / "history/00000000000000000001.json", record
        )
        self.head_ref = self.write(
            release.forward.STATE / "HEAD.json",
            {"schema": state.HEAD_SCHEMA, "sequence": 1, "sha256": self.commit_ref["sha256"]},
        )
        # Only native origin/proof semantics are substituted. Real ColdStore
        # still checks history inventory, record/plan shape, sequence, hashes,
        # predecessor chain, actual HEAD, source files and actual state flock.
        self.install(
            patch.object(
                transactions.ColdStore,
                "_recovery_record",
                side_effect=lambda value: value["origin_plan"]["attempt_id"],
            )
        )
        complete_ref = self.write(
            self.epoch_root / "complete.json",
            {
                "ok": True,
                "plan_ref": self.plan_ref,
                "commit_ref": self.commit_ref,
                "epoch_ref": epoch_ref,
                "old_hold_released": False,
                "old_barriers_opened": False,
            },
        )
        custody_ref = self.write(
            release.native.STATE / "packages" / self.attempt / "kernel/runtime-custody.json",
            {
                "schema": "autostop.j1.cold-kernel-runtime-custody.v1",
                "scope": "production_native",
                "binding": self.binding,
                "package_ref": package_ref,
                "kernel_ref": kernel_ref,
                "commit_ref": self.commit_ref,
                "cold_completion_ref": complete_ref,
            },
        )
        post_open_ref = self.write(
            self.directory / "post-open-readonly.json",
            runtime_tests.report(
                candidate=self.plan["candidate"], attempt=self.attempt, phase="open"
            ),
        )
        self.completion_ref = self.write(
            self.directory / "completion.json",
            {
                "ok": True,
                "plan_ref": self.plan_ref,
                "commit": self.commit_ref,
                "cold_completion_ref": complete_ref,
                "kernel_custody_ref": custody_ref,
                "old_writer_allowed": False,
                "post_open_readonly_ref": post_open_ref,
                "business_acceptance": False,
                "production_operational_ready": False,
                "post_open_business_acceptance": "required_in_release_window",
            },
        )
        self.write(
            self.directory / "progress.json",
            {
                "phase": "complete",
                "observed_at": release.forward.utc(),
                "completion": self.completion_ref,
            },
        )
        source_refs = {
            "plan": self.plan_ref,
            "commit": self.commit_ref,
            "head": self.head_ref,
            "epoch": epoch_ref,
        }
        copied = {
            name: self.write(
                self.dispatch / "proofs" / (name + ".json"), io.document(io.referenced(reference))
            )
            for name, reference in source_refs.items()
        }
        admission = self.write(
            self.dispatch / "proofs/admission.json",
            {
                "schema": projection.SCHEMA,
                **epochs.common(self.epoch),
                "source_refs": source_refs,
                "projected_refs": copied,
            },
        )
        self.opened = {
            "schema": "autostop.j1.cold-dispatch-state.v1",
            **epochs.common(self.epoch),
            "state": "open",
            "plan_ref": self.plan_ref,
            "commit_ref": self.commit_ref,
            "admission_ref": admission,
        }
        self.write(Path(self.fence["state_path"]), self.opened)
        self.provider = SimpleNamespace(
            scope="production_native",
            read_reference=io.referenced,
            systemd=Mock(return_value=self.properties),
            _verify_kernel_cache=Mock(),
            _verify_kernel_libraries=Mock(),
        )
        self.install(patch.object(release.native, "NativeProvider", return_value=self.provider))
        # Fixed production namespace paths are represented only by this owned
        # private fixture; the actual dispatch descriptor/ctime/flock is real.
        self.install(patch.object(projection, "_fence", return_value=self.fence))
        self.kernel_observer = self.install(patch.object(kernel, "verify_runtime_identity"))
        self.install(patch.dict(os.environ, SUCCESS, clear=True))

    def install(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def private_directory(self, path, owner):
        self.assertEqual(owner, os.geteuid())
        if path == Path("/usr/bin/python3").resolve().parent:
            return self.system_directory(path, owner)
        self.custody.directory(path)
        return path.lstat()

    def write(self, path, body):
        self.assertTrue(path.is_absolute() and path.is_relative_to(self.root))
        self.assertNotIn("..", path.parts)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        io.atomic_replace(path, body)
        return io.reference(path)

    @contextmanager
    def private_deployment(self, expected=None, *, on_failure=None):
        import fcntl

        info = self.lock.lstat()
        self.assertEqual(info.st_uid, os.geteuid())
        self.assertEqual(info.st_nlink, 1)
        self.assertFalse(info.st_mode & 0o022)
        identity = [info.st_dev, info.st_ino]
        self.assertTrue(expected is None or expected == identity)
        descriptor = os.open(self.lock, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            release._settle_deployment(descriptor)
            try:
                yield descriptor
            except BaseException:
                if on_failure is not None:
                    on_failure()
                raise
        finally:
            os.close(descriptor)

    def test_fixture_refuses_an_outside_path_before_mkdir(self):
        with self.assertRaises(AssertionError):
            self.write(self.root.parent / "outside-private-settle" / "file.json", {})

    def assert_closed(self):
        self.assertEqual(
            io.document(io.read_protected(self.fence["state_path"])),
            epochs.closed_state(self.epoch),
        )
        self.assertTrue((self.epoch_root / "safety-closed.json").is_file())

    def test_completed_normal_exit_keeps_only_the_existing_open_runtime(self):
        original = Path(self.fence["state_path"]).read_bytes()
        with patch.object(
            parent.native,
            "observe_worker",
            side_effect=AssertionError("exited parent is not live authority"),
        ):
            result = release.settle(self.attempt, completed_exit=True)
        self.assertTrue(result["completed_runtime_retained"])
        self.assertFalse(result["activation_authorized"])
        self.assertEqual(Path(self.fence["state_path"]).read_bytes(), original)
        self.assertFalse((self.epoch_root / "safety-closed.json").exists())
        self.kernel_observer.assert_called_once()

    def test_unknown_or_failed_service_exit_fences_even_a_completed_runtime(self):
        with patch.dict(os.environ, {}, clear=True):
            result = release.settle(self.attempt, completed_exit=True)
        self.assertTrue(result["new_dispatch_closed"])
        self.assert_closed()

    def test_failed_exit_zero_status_cannot_keep_open(self):
        with patch.dict(os.environ, {**SUCCESS, "SERVICE_RESULT": "timeout"}, clear=True):
            release.settle(self.attempt, completed_exit=True)
        self.assert_closed()

    def test_inprocess_exception_fences_under_the_already_held_d(self):
        with release.forward.deployment_lock() as descriptor:
            with patch.object(
                release.forward,
                "deployment_lock",
                side_effect=AssertionError("must not reacquire D"),
            ):
                release.settle(self.attempt, deployment_fd=descriptor)
        self.assert_closed()

    def test_shared_or_unheld_deployment_descriptor_is_not_authority(self):
        descriptor = os.open(self.lock, os.O_RDWR)
        try:
            with self.assertRaisesRegex(io.Refusal, "hold_lock_not_owned"):
                release.settle(self.attempt, completed_exit=True, deployment_fd=descriptor)
        finally:
            os.close(descriptor)
        self.assertEqual(io.document(io.read_protected(self.fence["state_path"])), self.opened)

    def test_busy_d_cannot_be_reacquired_by_a_separate_settle(self):
        with release.forward.deployment_lock():
            with self.assertRaises(BlockingIOError):
                release.settle(self.attempt, completed_exit=True)
        self.assertEqual(io.document(io.read_protected(self.fence["state_path"])), self.opened)

    def test_missing_completion_closes(self):
        Path(self.completion_ref["path"]).unlink()
        release.settle(self.attempt, completed_exit=True)
        self.assert_closed()

    def test_tampered_actual_head_closes(self):
        self.write(
            Path(self.head_ref["path"]),
            {"schema": state.HEAD_SCHEMA, "sequence": 1, "sha256": "f" * 64},
        )
        release.settle(self.attempt, completed_exit=True)
        self.assert_closed()

    def test_changed_pinned_controller_source_closes(self):
        self.cold_script.write_bytes(b"# drifted source\n")
        release.settle(self.attempt, completed_exit=True)
        self.assert_closed()

    def test_pending_or_recovery_pointer_prevents_retention(self):
        self.write(release.forward.STATE / "active-recovery.json", self.reference)
        release.settle(self.attempt, completed_exit=True)
        self.assert_closed()

    def test_pending_plan_prevents_retention(self):
        self.write(release.forward.STATE / "pending.json", self.plan)
        release.settle(self.attempt, completed_exit=True)
        self.assert_closed()

    def test_kernel_readback_runs_under_actual_d_and_epoch_ex(self):
        def observed(*_args):
            import fcntl

            for path in (self.lock, Path(self.fence["dispatch_lock_path"])):
                descriptor = os.open(path, os.O_RDWR | os.O_CLOEXEC)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(descriptor)

        self.kernel_observer.side_effect = observed
        self.assertTrue(
            release.settle(self.attempt, completed_exit=True)["completed_runtime_retained"]
        )

    def test_current_kernel_unknown_closes(self):
        self.kernel_observer.side_effect = io.Refusal("private_kernel_changed")
        release.settle(self.attempt, completed_exit=True)
        self.assert_closed()

    def test_foreign_service_invocation_closes(self):
        with patch.dict(os.environ, {**SUCCESS, "INVOCATION_ID": "f" * 32}, clear=True):
            release.settle(self.attempt, completed_exit=True)
        self.assert_closed()

    def test_expired_window_can_only_retain_proven_completion_or_fence(self):
        with patch.object(
            release.time, "monotonic_ns", return_value=self.binding["deadline_ns"] + 1
        ):
            with patch.object(
                release.forward, "remaining", side_effect=AssertionError("no lease extension")
            ):
                result = release.settle(self.attempt, completed_exit=True)
        self.assertTrue(result["completed_runtime_retained"])
        self.assertEqual(
            io.document(io.referenced(self.plan["cold_authority_package_ref"]))["binding"],
            self.binding,
        )
        Path(self.completion_ref["path"]).unlink()
        with patch.object(
            release.time, "monotonic_ns", return_value=self.binding["deadline_ns"] + 1
        ):
            release.settle(self.attempt, completed_exit=True)
        self.assert_closed()


if __name__ == "__main__":
    unittest.main()
