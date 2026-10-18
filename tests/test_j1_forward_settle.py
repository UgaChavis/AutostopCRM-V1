"""Crash boundaries between durable acceptance, final readback and ExecStopPost."""

from __future__ import annotations

import io as stream_io
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts import j1_forward_io as io
from scripts import j1_forward_release as release


class SettleCrashTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.attempt = "00000000-0000-4000-8000-000000000001"
        self.directory = self.root / self.attempt
        self.directory.mkdir()
        self.marker = self.root / "maintenance.json"
        self.barrier = self.root / "writer-closed.conf"
        self.plan = {
            "attempt_id": self.attempt,
            "base_sha256": "a" * 64,
            "sequence": 1,
            "predecessor_sha256": "a" * 64,
            "candidate": {"revision": "b" * 40, "image": "sha256:" + "c" * 64},
        }
        self.publish("plan.json", self.plan)
        self.plan_ref = self.reference(self.directory / "plan.json")
        self.baseline = {
            "deployment_lock_identity": [12, 34],
            "maintenance_parent": {"path": str(self.root)},
            "native": {"desired_inbound": True},
            "previous_container": {"image": "previous"},
        }
        self.ownership = {
            "schema": release.closure.SCHEMA,
            "attempt_id": self.attempt,
            "attempt_dir": str(self.directory),
            "baseline_sha256": io.sha(io.canonical(self.baseline["native"])),
            "dropins": [{"path": str(self.barrier)}],
            "bytes_sha256": io.sha(release.closure.DENY_BYTES),
        }
        self.publish("closure-owned.json", self.ownership)
        self.commit = {"path": str(self.root / "commit.json"), "sha256": "d" * 64}
        self.completion = {"ok": True, "plan_ref": self.plan_ref, "commit": self.commit}
        self.publish("completion.json", self.completion)
        self.publish(
            "progress.json",
            {"phase": "complete", "completion": self.reference(self.directory / "completion.json")},
        )
        self.held = False
        self.trace = []
        self.store = Mock()
        self.store.pending.return_value = None
        self.store.head.return_value = {
            "record": {"plan": self.plan},
            "sequence": 1,
            "sha256": self.commit["sha256"],
        }
        self.store.confirm_finished.side_effect = self.confirm_finished
        self.host = Mock()
        self.host.job_status.return_value = {"enabled": True}
        self.host.container.return_value = {"candidate": True}
        self.install(release, "MARKER", self.marker)
        self.install(release.marker, "require_owned", self.require_owned_marker)
        self.install(release, "Store", Mock(return_value=self.store))
        self.load = self.install(
            release,
            "load_attempt",
            Mock(return_value=(self.directory, self.plan_ref, self.plan, self.baseline, self.host)),
        )
        self.install(release, "deployment_lock", self.lock)
        self.install(release, "attempt_directory", Mock(return_value=self.directory))
        self.install(release, "remaining", Mock(return_value=60))
        self.install(io, "read_protected", lambda path, **_: Path(path).read_bytes())
        self.install(io, "reference", self.reference)
        self.durable = self.install(io, "confirm_durable", Mock(side_effect=self.confirm_durable))
        self.native = self.install(
            release.authority, "verify_preserved", Mock(return_value={"ok": True})
        )
        self.container = self.install(release, "check_container_preserved", Mock())
        self.install(release, "work_counts", lambda value: value)
        self.refence = self.install(
            release, "record_failure", Mock(side_effect=self.record_failure)
        )

    def install(self, owner, name, value):
        replacement = patch.object(owner, name, value)
        replacement.start()
        self.addCleanup(replacement.stop)
        return value

    def publish(self, name, value):
        (self.directory / name).write_bytes(io.canonical(value))

    @staticmethod
    def reference(path):
        return {"path": str(path), "sha256": io.sha(Path(path).read_bytes())}

    @contextmanager
    def lock(self, identity, **_):
        self.assertEqual(identity, self.baseline["deployment_lock_identity"])
        self.assertFalse(self.held)
        self.held = True
        self.trace.append("lock")
        try:
            yield
        finally:
            self.trace.append("unlock")
            self.held = False

    def confirm_finished(self, plan):
        self.assertEqual(plan, self.plan)
        self.trace.append("state-durable")
        return self.commit

    def confirm_durable(self, ref):
        self.assertEqual(ref, self.reference(Path(ref["path"])))
        self.trace.append(Path(ref["path"]).name + "-durable")

    def record_failure(self, directory, plan_ref, plan, baseline, host, ownership, error):
        self.assertTrue(self.held, "Failure restoration must retain the original deployment lock")
        self.assertEqual((directory, plan_ref, plan), (self.directory, self.plan_ref, self.plan))
        self.assertEqual(baseline, self.baseline)
        self.assertIs(host, self.host)
        self.assertEqual(ownership, self.ownership)
        self.assertIsInstance(error, BaseException)
        self.trace.append("refence")
        self.marker.write_bytes(io.canonical(release.marker_value(self.plan_ref, self.plan)))
        self.barrier.write_bytes(release.closure.DENY_BYTES)
        return {"closure": {"observation": {"closed": True}, "errors": []}, "errors": []}

    def owned_marker(self):
        self.marker.write_bytes(io.canonical(release.marker_value(self.plan_ref, self.plan)))
        record_name = "marker-identity-" + "a" * 32 + ".json"
        self.publish(
            record_name,
            {
                "plan_ref": self.plan_ref,
                "parent": self.baseline["maintenance_parent"],
                "identity": self.reference(self.marker),
            },
        )
        self.publish(
            "marker-owned.json",
            {
                "plan_ref": self.plan_ref,
                "record": self.reference(self.directory / record_name),
            },
        )

    def require_owned_marker(self, pin, payload, *, expected_identity=None):
        io.require(pin == self.baseline["maintenance_parent"], "fixture_marker_parent_changed")
        io.require(
            self.marker.read_bytes() == io.canonical(payload), "owned_maintenance_marker_changed"
        )
        identity = self.reference(self.marker)
        io.require(identity == expected_identity, "marker_owned_identity_changed")
        return identity

    def assert_refenced(self):
        result = release.settle(self.attempt)
        self.assertEqual(result, {"ok": False, "failure_closed": True})
        self.refence.assert_called_once()
        self.store.finish.assert_not_called()
        self.store.recover.assert_not_called()
        self.assertTrue(self.marker.exists())
        self.assertTrue(self.barrier.exists())
        self.assertLess(self.trace.index("refence"), self.trace.index("unlock"))

    def test_pending_with_completion_after_cleanup_is_never_finished(self):
        self.store.pending.return_value = self.plan
        self.publish("failure.json", {"ok": False})
        self.owned_marker()
        self.assert_refenced()
        self.store.confirm_finished.assert_not_called()

    def test_interrupted_history_publication_does_not_block_matching_pending_cleanup(self):
        self.store.pending.return_value = self.plan
        self.store.head.side_effect = io.Refusal("state_head_mismatch")
        self.assert_refenced()
        self.store.head.assert_not_called()
        self.store.confirm_finished.assert_not_called()

    def test_matching_pending_ignores_partial_completion_from_killed_writer(self):
        self.store.pending.return_value = self.plan
        (self.directory / "completion.json").write_bytes(b'{"ok":')
        self.assert_refenced()
        self.store.confirm_finished.assert_not_called()

    def test_kill_after_completion_before_finish_keeps_pending_and_refences(self):
        self.store.pending.return_value = self.plan
        self.assert_refenced()
        self.store.confirm_finished.assert_not_called()

    def test_foreign_pending_never_changes_its_marker_or_writers(self):
        self.store.pending.return_value = {
            **self.plan,
            "attempt_id": "00000000-0000-4000-8000-000000000002",
        }
        self.owned_marker()
        before = self.marker.read_bytes()
        with self.assertRaises(io.Refusal):
            release.settle(self.attempt)
        self.refence.assert_not_called()
        self.store.finish.assert_not_called()
        self.assertEqual(self.marker.read_bytes(), before)
        self.assertFalse(self.barrier.exists())

    def assert_foreign_head_untouched(self):
        before = self.marker.read_bytes() if self.marker.exists() else None
        self.store.head.return_value = {
            "sequence": 2,
            "sha256": "e" * 64,
            "record": {
                "plan": {
                    **self.plan,
                    "attempt_id": "00000000-0000-4000-8000-000000000002",
                    "sequence": 2,
                }
            },
        }
        with self.assertRaises(io.Refusal):
            release.settle(self.attempt)
        self.refence.assert_not_called()
        self.store.finish.assert_not_called()
        self.store.confirm_finished.assert_not_called()
        self.assertEqual(self.marker.read_bytes() if self.marker.exists() else None, before)
        self.assertFalse(self.barrier.exists())

    def test_old_settle_cannot_refence_a_later_committed_generation(self):
        self.assert_foreign_head_untouched()

    def test_old_failure_receipt_cannot_close_a_later_committed_generation(self):
        self.publish("failure.json", {"ok": False})
        self.assert_foreign_head_untouched()

    def test_old_partial_completion_cannot_close_a_later_committed_generation(self):
        (self.directory / "completion.json").write_bytes(b'{"ok":')
        self.assert_foreign_head_untouched()

    def test_later_head_overrides_stale_owned_marker_and_failure_receipt(self):
        self.owned_marker()
        self.publish("failure.json", {"ok": False})
        self.assert_foreign_head_untouched()

    def test_exact_predecessor_with_owned_marker_can_refence_interrupted_attempt(self):
        self.store.head.return_value = {
            "record": None,
            "sequence": 0,
            "sha256": self.plan["predecessor_sha256"],
        }
        self.owned_marker()
        self.assert_refenced()

    def test_prepared_attempt_without_mutations_is_not_fenced(self):
        self.store.head.return_value = {
            "record": None,
            "sequence": 0,
            "sha256": self.plan["predecessor_sha256"],
        }
        (self.directory / "completion.json").unlink()
        self.publish("progress.json", {"phase": "prepared", "plan_ref": self.plan_ref})
        result = release.settle(self.attempt)
        self.assertEqual(result, {"ok": True, "writer_mutation_not_begun": True})
        self.refence.assert_not_called()
        self.store.finish.assert_not_called()
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.barrier.exists())

    def test_foreign_closure_record_is_not_adopted_for_matching_pending(self):
        self.store.pending.return_value = self.plan
        self.publish(
            "closure-owned.json",
            {**self.ownership, "attempt_id": "00000000-0000-4000-8000-000000000002"},
        )
        with self.assertRaises(io.Refusal):
            release.settle(self.attempt)
        self.refence.assert_not_called()
        self.assertFalse(self.marker.exists())

    def test_partial_pending_is_refenced_with_exact_owned_marker(self):
        self.store.pending.side_effect = ValueError("partial pending JSON")
        self.owned_marker()
        self.assert_refenced()

    def test_partial_pending_with_foreign_marker_cannot_be_adopted(self):
        self.store.pending.side_effect = ValueError("partial pending JSON")
        self.marker.write_bytes(io.canonical({"attempt_id": "foreign-attempt"}))
        with self.assertRaises((io.Refusal, ValueError)):
            release.settle(self.attempt)
        self.refence.assert_not_called()
        self.assertFalse(self.barrier.exists())

    def test_partial_pending_requires_the_exact_plan_marker(self):
        self.store.pending.side_effect = ValueError("partial pending JSON")
        marker = release.marker_value(self.plan_ref, self.plan)
        marker["plan_sha256"] = "f" * 64
        self.marker.write_bytes(io.canonical(marker))
        with self.assertRaises((io.Refusal, ValueError)):
            release.settle(self.attempt)
        self.refence.assert_not_called()

    def test_partial_pending_cannot_adopt_foreign_closure_with_owned_marker(self):
        self.store.pending.side_effect = ValueError("partial pending JSON")
        self.owned_marker()
        self.publish("closure-owned.json", {**self.ownership, "baseline_sha256": "f" * 64})
        with self.assertRaises((io.Refusal, ValueError)):
            release.settle(self.attempt)
        self.refence.assert_not_called()

    def test_partial_pending_with_partial_closure_refuses_unproven_mutation(self):
        self.store.pending.side_effect = ValueError("partial pending JSON")
        self.owned_marker()
        (self.directory / "closure-owned.json").write_bytes(b'{"schema":')
        with self.assertRaises((io.Refusal, ValueError)):
            release.settle(self.attempt)
        self.refence.assert_not_called()

    def test_partial_pending_without_owned_marker_refuses_mutation(self):
        self.store.pending.side_effect = ValueError("partial pending JSON")
        with self.assertRaises((io.Refusal, ValueError, FileNotFoundError)):
            release.settle(self.attempt)
        self.refence.assert_not_called()

    def test_interrupted_head_with_absent_pending_and_owned_marker_is_refenced(self):
        self.store.head.side_effect = io.Refusal("state_head_mismatch")
        self.owned_marker()
        self.assert_refenced()

    def test_terminal_success_reestablishes_state_and_completion_durability(self):
        result = release.settle(self.attempt)
        self.assertEqual(result, {"ok": True, "completed": True})
        self.store.confirm_finished.assert_called_once_with(self.plan)
        self.assertIn("completion.json-durable", self.trace)
        self.assertIn("progress.json-durable", self.trace)
        self.assertLess(self.trace.index("lock"), self.trace.index("state-durable"))
        self.assertLess(self.trace.index("lock"), self.trace.index("completion.json-durable"))
        self.assertLess(self.trace.index("lock"), self.trace.index("progress.json-durable"))
        self.assertLess(self.trace.index("state-durable"), self.trace.index("unlock"))
        self.assertLess(self.trace.index("completion.json-durable"), self.trace.index("unlock"))
        self.assertLess(self.trace.index("progress.json-durable"), self.trace.index("unlock"))
        self.native.assert_called_once_with(self.baseline["native"], running=True)
        self.container.assert_called_once()
        self.host.validate_manager.assert_called_once_with(
            self.host.manager.return_value, self.attempt, held=False
        )
        self.refence.assert_not_called()
        self.store.finish.assert_not_called()

    def test_later_failure_receipt_overrides_old_completion_without_pending(self):
        self.publish("failure.json", {"ok": False})
        self.owned_marker()
        self.assert_refenced()

    def test_incomplete_progress_does_not_certify_old_completion(self):
        self.publish("progress.json", {"phase": "committed-opening"})
        self.assert_refenced()

    def test_complete_progress_must_reference_the_same_completion(self):
        self.publish(
            "progress.json",
            {"phase": "complete", "completion": {"path": "foreign", "sha256": "f" * 64}},
        )
        self.assert_refenced()

    def test_completion_must_reference_the_committed_record(self):
        self.publish(
            "completion.json", {**self.completion, "commit": {**self.commit, "sha256": "f" * 64}}
        )
        self.publish(
            "progress.json",
            {"phase": "complete", "completion": self.reference(self.directory / "completion.json")},
        )
        self.assert_refenced()

    def test_malformed_completion_after_pending_unlink_is_refenced(self):
        (self.directory / "completion.json").write_bytes(b'{"ok":')
        self.assert_refenced()

    def test_fresh_native_refusal_restores_closure_instead_of_aborting_settle(self):
        self.native.side_effect = io.Refusal("native_desired_state_changed")
        self.assert_refenced()

    def test_fresh_container_refusal_restores_closure(self):
        self.container.side_effect = io.Refusal("live_release_identity_failed")
        self.assert_refenced()

    def test_changed_work_duty_restores_closure(self):
        self.host.job_status.return_value = {"enabled": False}
        self.assert_refenced()

    def test_changed_scheduler_hold_restores_closure(self):
        self.host.validate_manager.side_effect = io.Refusal("scheduler_hold_changed")
        self.assert_refenced()

    def test_terminal_state_sync_failure_cannot_report_completion(self):
        self.store.confirm_finished.side_effect = OSError("state directory fsync failed")
        self.assert_refenced()

    def test_completion_sync_failure_cannot_report_completion(self):
        self.durable.side_effect = OSError("completion fsync failed")
        self.assert_refenced()

    def test_existing_barrier_refuses_success_and_keeps_writers_closed(self):
        self.barrier.write_bytes(release.closure.DENY_BYTES)
        self.assert_refenced()

    def test_successful_launcher_requires_durable_terminal_state(self):
        result = release.launch(self.attempt)
        self.assertTrue(result["ok"])
        self.store.confirm_finished.assert_called_once_with(self.plan)
        command = self.host.run.call_args.args[0]
        self.assertIn("--wait", command)
        self.assertIn("--property=Type=exec", command)
        self.assertIn("--property=RuntimeMaxSec=900", command)
        self.assertTrue(
            any(
                item.startswith("--property=ExecStopPost=")
                and item.endswith(" settle --attempt " + self.attempt)
                for item in command
            )
        )

    def test_launcher_rejects_old_completion_after_stop_post_refences(self):
        self.publish("failure.json", {"ok": False})
        with self.assertRaises(io.Refusal):
            release.launch(self.attempt)
        self.store.finish.assert_not_called()

    def test_launcher_rejects_nonterminal_progress(self):
        self.publish("progress.json", {"phase": "failure-closure-incomplete"})
        with self.assertRaises(io.Refusal):
            release.launch(self.attempt)

    def test_launcher_propagates_stop_post_failure(self):
        self.host.run.side_effect = io.Refusal("forward_command_failed_forward-service")
        with self.assertRaisesRegex(io.Refusal, "forward_command_failed"):
            release.launch(self.attempt)
        self.store.confirm_finished.assert_not_called()


class SettleExitTests(unittest.TestCase):
    def test_unsuccessful_settle_is_nonzero_even_when_closure_is_proven(self):
        result = {"ok": False, "failure_closed": True}
        stdout = SimpleNamespace(buffer=stream_io.BytesIO())
        stderr = SimpleNamespace(buffer=stream_io.BytesIO())
        with (
            patch.object(release.sys, "argv", ["j1-forward", "settle", "--attempt", "attempt"]),
            patch.object(release.sys, "stdout", stdout),
            patch.object(release.sys, "stderr", stderr),
            patch.object(release.os, "name", "posix"),
            patch.object(release.os, "geteuid", return_value=0, create=True),
            patch.object(release, "settle", return_value=result),
        ):
            status = release.main()
        self.assertEqual(status, 2)
        self.assertEqual(io.document(stdout.buffer.getvalue()), result)


if __name__ == "__main__":
    unittest.main()
