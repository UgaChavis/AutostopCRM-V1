from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import contextmanager
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
class _ColdOpenFixture(unittest.TestCase):
    def prepare(self, *, wrong_proof=None):
        files = projection_tests.ColdProjectionTests()
        files.setUp()
        self.addCleanup(files.doCleanups)
        root = files.root
        files.epoch = {
            **files.epoch,
            "slots": {
                unit: lifecycle.slot_id(files.common, unit) for unit in lifecycle.contract.ALLOWED
            },
        }
        files.epoch_ref = io.write_new(root / "open-epoch.json", files.epoch)
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
            "dispatch_epoch_ref": files.epoch_ref,
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
        binding = {
            **{key: files.common[key] for key in ("attempt_id", "boot_id", "generation")},
            "consumer_sha256": "1" * 64,
            "candidate_crm_sha": plan["candidate"]["revision"],
            "candidate_manager_sha": plan["candidate_manager_sha"],
            "deployment_lock_identity": [1, 2],
            "not_before_ns": time.monotonic_ns() - 1,
            "deadline_ns": time.monotonic_ns() + 600 * 10**9,
        }
        value = {"package": {"binding": binding}}
        controller._unit_binding = binding
        controller._unit_history_exit = controller._unit_native_exit = lambda: None
        controller._unit_requalifications = []
        controller.provider.boot = lambda: binding["boot_id"]

        class UnitRequalification:
            # Explicit native/package seam: this old fixture has {fixture:true},
            # not a cold authority graph. Actual COLD history is tested below
            # in test_cold_v2_fault_contract, without this historical seam.
            def __init__(self, reference, *, provider, required_scope, max_calls):
                self.count = 0
                assert reference == controller.package_ref
                assert provider is controller.provider and required_scope == "native_fixture"
                assert max_calls == 2
                controller._unit_requalifications.append(self)

            def __enter__(self):
                return self

            def observe(self, reference, provider):
                self.count += 1
                assert self.count <= 2 and reference == controller.package_ref
                assert provider is controller.provider
                return value

            def __exit__(self, error_type, _error, _trace):
                if error_type is None:
                    controller._unit_native_exit()
                    lifecycle.contract._binding(binding, now_ns=time.monotonic_ns())

        @contextmanager
        def unit_history(_plan_ref):
            yield
            controller._unit_history_exit()

        store.historical_validation = unit_history
        controller.verify = lambda observation=None: (
            value
            if observation is None
            else observation.observe(controller.package_ref, controller.provider),
            files.fence,
        )
        self.enterContext(
            patch.object(lifecycle.native, "PackageRequalification", UnitRequalification)
        )
        self.enterContext(patch.object(lifecycle.native, "_mutable_package"))
        self.enterContext(
            patch.object(projection.native, "verify_package", return_value={"package": {}})
        )
        self.enterContext(patch.object(projection.epochs, "verify", return_value=files.fence))
        return files, controller, store, commit_ref


class ColdOpenCommitTests(_ColdOpenFixture):
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


class ColdOpenDeadlineTests(_ColdOpenFixture):
    def assert_closed(self, files):
        self.assertEqual(io.document(io.read_protected(files.fence["state_path"])), files.closed)
        descriptor = lifecycle.epochs.locked_fence(files.fence, exclusive=False)
        os.close(descriptor)

    def test_ready_stays_before_ex_and_scope_exits_precede_open_with_two_original_cuts(self):
        files, controller, store, commit_ref = self.prepare()
        original_ready = controller.ready
        order = []

        def ready():
            descriptor = lifecycle.epochs.locked_fence(files.fence, exclusive=False)
            os.close(descriptor)
            order.append("ready_before_ex")
            return original_ready()

        def exit_scope(name):
            self.assertEqual(
                files.closed, io.document(io.read_protected(files.fence["state_path"]))
            )
            with self.assertRaises(BlockingIOError):
                lifecycle.epochs.locked_fence(files.fence, exclusive=False)
            order.append(name)

        controller.ready = ready
        controller._unit_history_exit = lambda: exit_scope("historical_exit_under_ex")
        controller._unit_native_exit = lambda: exit_scope("native_exit_under_ex")
        opened = controller.open_ingress(commit_ref, store=store)
        self.assertEqual(
            order, ["ready_before_ex", "historical_exit_under_ex", "native_exit_under_ex"]
        )
        self.assertEqual(controller._unit_requalifications[0].count, 2)
        # The repeated OPEN is the same immutable receipt, never a second start.
        controller._unit_history_exit = controller._unit_native_exit = lambda: None
        self.assertEqual(opened, controller.open_ingress(commit_ref, store=store))
        self.assertEqual([item.count for item in controller._unit_requalifications], [2, 2])

    def expiry(self, phase):
        files, controller, store, commit_ref = self.prepare()
        clock = [time.monotonic_ns()]
        self.enterContext(
            patch.object(lifecycle.time, "monotonic_ns", side_effect=lambda: clock[0])
        )

        def expired():
            clock[0] = controller._unit_binding["deadline_ns"]

        if phase == "last_head":
            original, calls = store.head, []

            def head():
                result = original()
                calls.append(result)
                if len(calls) == 3:
                    expired()
                return result

            store.head = head
        elif phase == "historical_exit":
            controller._unit_history_exit = expired
        elif phase == "native_exit":
            controller._unit_native_exit = expired
        elif phase == "temp_fsync":
            original = lifecycle.journal._new

            def write(path, raw):
                result = original(path, raw)
                if Path(path).name.startswith(".state-"):
                    expired()
                return result

            self.enterContext(patch.object(lifecycle.journal, "_new", side_effect=write))
        elif phase == "open_fsync":
            original = lifecycle.journal._sync

            def sync(path):
                result = original(path)
                state = io.document(io.read_protected(files.fence["state_path"]))
                if state["state"] == "open":
                    expired()
                return result

            self.enterContext(patch.object(lifecycle.journal, "_sync", side_effect=sync))
        elif phase == "opened_receipt":
            original = lifecycle._publish

            def publish(path, *args, **kwargs):
                result = original(path, *args, **kwargs)
                if Path(path).name == "opened.json":
                    expired()
                return result

            self.enterContext(patch.object(lifecycle, "_publish", side_effect=publish))
        else:
            self.fail("unknown unit boundary")
        original_close, closed = projection._closed, []

        def close(fence, epoch):
            with self.assertRaises(BlockingIOError):
                lifecycle.epochs.locked_fence(fence, exclusive=False)
            closed.append(True)
            return original_close(fence, epoch)

        with patch.object(projection, "_closed", side_effect=close):
            with self.assertRaisesRegex(io.Refusal, "binding_expired_or_window"):
                controller.open_ingress(commit_ref, store=store)
        self.assertEqual(closed, [True])
        self.assert_closed(files)
        self.assertEqual((controller.root / "opened.json").exists(), phase == "opened_receipt")

    def test_expiry_after_last_actual_head_denies_before_open(self):
        self.expiry("last_head")

    def test_expiry_at_historical_exit_denies_before_open(self):
        self.expiry("historical_exit")

    def test_expiry_at_native_raw_exit_denies_before_open(self):
        self.expiry("native_exit")

    def test_expiry_during_temp_fsync_denies_before_rename(self):
        self.expiry("temp_fsync")

    def test_expiry_during_open_fsync_closes_before_unlock(self):
        self.expiry("open_fsync")

    def test_expiry_during_opened_receipt_closes_before_unlock(self):
        self.expiry("opened_receipt")

    def test_late_boot_admission_and_head_changes_deny(self):
        for boundary in ("boot", "admission", "head"):
            with self.subTest(boundary=boundary):
                files, controller, store, commit_ref = self.prepare()

                def changed():
                    if boundary == "boot":
                        controller.provider.boot = lambda: str(uuid.uuid4())
                    elif boundary == "admission":
                        original = controller.admitted
                        controller.admitted = lambda: {**original(), "backup_ref": files.epoch_ref}
                    else:
                        io.atomic_replace(
                            store.head_path,
                            {**io.document(io.read_protected(store.head_path)), "sha256": "f" * 64},
                        )

                controller._unit_history_exit = changed
                with self.assertRaises(io.Refusal):
                    controller.open_ingress(commit_ref, store=store)
                self.assert_closed(files)
                self.assertFalse((controller.root / "opened.json").exists())


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
