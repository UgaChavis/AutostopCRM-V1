"""Real ExecStopPost recovery across the durable v2 process-scope boundary.

Only Linux observation and effects are simulated. The release recovery,
ownership/hash checks, closure restoration and failure evidence are real.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import j1_forward_closure as closure
from scripts import j1_forward_io as io
from scripts import j1_forward_release as release
from tests import test_j1_forward_closure as fixtures
from tests import test_j1_forward_release as release_fixtures


class ProcessScopeRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="j1-scope-recovery-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.attempt = fixtures.ATTEMPT
        self.directory = self.root / self.attempt
        self.directory.mkdir()
        self.marker = self.root / "maintenance.json"
        self.runner = fixtures.FakeRunner()
        self.held = False
        self.native = {
            "base_sha256": "a" * 64,
            "warm_actors": [fixtures.actor(pid) for pid in sorted(closure.ACCEPTED_PIDS)],
        }
        self.original_native = copy.deepcopy(self.native)
        self.plan = {"attempt_id": self.attempt, "base_sha256": "a" * 64}
        self.baseline = {"native": self.native, "deployment_lock_identity": [123, 456]}
        self.store = Mock()
        self.store.pending.return_value = self.plan
        self.store.head.side_effect = AssertionError("Matching pending must precede HEAD reads")
        self.host = Mock()
        self.install(closure, "RUNNER", self.runner)
        closure._CACHE.clear()
        self.addCleanup(closure._CACHE.clear)
        self.install(
            io, "protected_directory", release_fixtures.ForwardReleaseTests.physical_directory
        )
        self.install(io, "read_protected", release_fixtures.ForwardReleaseTests.read_physical)
        self.install(io, "fsync_directory", Mock())
        self.plan_ref = io.write_new(self.directory / "plan.json", self.plan)
        self.owned = closure.prepare_closure(self.native, self.directory)
        # FakeRunner's storage stands in for protected Linux storage. Materialize
        # the exact immutable ownership for the real release loader as well.
        io.write_new(self.directory / "closure-owned.json", self.owned)
        self.install(release, "MARKER", self.marker)
        self.install(release, "Store", Mock(return_value=self.store))
        self.install(release, "deployment_lock", self.lock)
        self.install(release, "cleanup_signals", nullcontext)
        self.install(
            release,
            "load_attempt",
            Mock(return_value=(self.directory, self.plan_ref, self.plan, self.baseline, self.host)),
        )
        self.runner.on_signal = self.signal_under_lock

    def install(self, owner, name, value):
        replacement = patch.object(owner, name, value)
        replacement.start()
        self.addCleanup(replacement.stop)

    @contextmanager
    def lock(self, identity, **_kwargs):
        self.assertEqual(identity, self.baseline["deployment_lock_identity"])
        self.assertFalse(self.held)
        self.held = True
        self.runner.events.append("lock")
        try:
            yield
        finally:
            self.runner.events.append("unlock")
            self.held = False

    def signal_under_lock(self, _expected, _stop):
        self.assertTrue(self.held, "Recovery may not release the old lock before freezing writers")

    def stopped(self, pid):
        self.runner.actors[pid]["stopped"] = True
        self.runner.procs[pid]["state"] = "T"

    def assert_preserved_scope(self):
        self.assertEqual(self.native, self.original_native)
        self.assertEqual(self.owned["warm_actors"], self.original_native["warm_actors"])
        self.assertEqual(self.runner.scope_captures, 1, "Recovery must never recapture live actors")
        self.assertEqual(self.runner.events.count("save_scope"), 1)
        self.assertEqual(self.runner.events.count("save"), 1)
        signaled = [
            event
            for event in self.runner.events
            if isinstance(event, tuple) and event[0] == "signal"
        ]
        self.assertEqual({event[1] for event in signaled}, set(fixtures.FREEZE_PIDS))
        self.assertTrue(all(event[2] is True for event in signaled))
        self.assertEqual(
            signaled[0][1], 538285, "The daemon closes ingress before auxiliary writers"
        )
        self.assertTrue(
            all(not self.runner.actors[pid]["stopped"] for pid in fixtures.TRANSPORT_PIDS)
        )
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(self.runner.stopped)
        self.assertTrue(self.marker.exists())
        self.assertEqual(
            io.document(io.read_protected(self.marker)),
            release.marker_value(self.plan_ref, self.plan),
        )
        self.store.head.assert_not_called()
        self.store.finish.assert_not_called()
        self.store.confirm_finished.assert_not_called()
        self.assertEqual(self.runner.events[-1], "unlock")
        self.assertFalse(self.held)

    def test_exec_stop_post_before_first_freeze_uses_durable_13_actor_scope(self):
        self.assertEqual(release.settle(self.attempt), {"ok": False, "failure_closed": True})
        self.assert_preserved_scope()
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in fixtures.FREEZE_PIDS))
        self.assertEqual(self.runner.last_descriptor_sets[0], set(fixtures.FREEZE_PIDS))
        self.assertFalse(set(fixtures.TRANSPORT_PIDS) & self.runner.last_descriptor_sets[1])
        failure = io.document(io.read_protected(self.directory / "failure.json"))
        self.assertEqual(
            failure["closure"]["observation"]["process_scope_ref"], self.owned["process_scope_ref"]
        )
        self.assertEqual(
            io.document(io.read_protected(self.directory / "progress.json"))["phase"],
            "failed-closed",
        )

    def test_partial_daemon_first_freeze_is_completed_from_same_record(self):
        for pid in fixtures.FREEZE_PIDS[:4]:
            self.stopped(pid)
        self.assertEqual(release.settle(self.attempt), {"ok": False, "failure_closed": True})
        self.assert_preserved_scope()
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in fixtures.FREEZE_PIDS))

    def test_pre_idle_failure_fence_cannot_authorize_backup_or_daemon_reopen(self):
        self.assertEqual(release.settle(self.attempt), {"ok": False, "failure_closed": True})
        self.assert_preserved_scope()
        failure = io.document(io.read_protected(self.directory / "failure.json"))
        self.assertIsNone(failure["closure"]["observation"]["ingress_drain_ref"])
        with self.assertRaises(io.Refusal):
            closure.observe_closed(self.owned, force_full=True)
        with self.assertRaises(io.Refusal):
            closure.reopen_native(self.owned)
        self.assertEqual(len(self.runner.barriers), 8)
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in fixtures.FREEZE_PIDS))
        self.assertFalse(
            any(
                isinstance(event, tuple)
                and (event[0] == "remove" or event[0] == "signal" and event[2] is False)
                for event in self.runner.events
            )
        )

    def test_daemon_only_activation_crash_refreezes_daemon_and_keeps_auxiliary_owned(self):
        for pid in fixtures.FREEZE_PIDS[1:]:
            self.stopped(pid)
        self.assertFalse(self.runner.actors[538285]["stopped"])
        self.assertEqual(release.settle(self.attempt), {"ok": False, "failure_closed": True})
        self.assert_preserved_scope()
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in fixtures.FREEZE_PIDS))
        first_signal = next(
            index
            for index, event in enumerate(self.runner.events)
            if isinstance(event, tuple) and event[0] == "signal"
        )
        self.assertLess(self.runner.events.index("stop"), first_signal)

    def test_partial_completion_does_not_skip_real_scope_refence(self):
        (self.directory / "completion.json").write_bytes(b'{"ok":')
        self.assertEqual(release.settle(self.attempt), {"ok": False, "failure_closed": True})
        self.assert_preserved_scope()
        self.assertEqual((self.directory / "completion.json").read_bytes(), b'{"ok":')

    def test_new_peer_refusal_still_freezes_owned_cohort_but_never_claims_closure(self):
        self.runner.scope_error = "process_scope_unknown_daemon_peer"
        self.assertEqual(release.settle(self.attempt), {"ok": False, "failure_closed": False})
        self.assert_preserved_scope()
        self.assertTrue(all(self.runner.actors[pid]["stopped"] for pid in fixtures.FREEZE_PIDS))
        failure = io.document(io.read_protected(self.directory / "failure.json"))
        self.assertIsNone(failure["closure"]["observation"])
        self.assertIn("closure_readback_failed", failure["closure"]["errors"])
        self.assertEqual(
            io.document(io.read_protected(self.directory / "progress.json"))["phase"],
            "failure-closure-incomplete",
        )

    def test_foreign_descendant_is_never_added_to_recovery_signal_set(self):
        self.runner.procs[999999] = {
            "start_ticks": 9999999,
            "ppid": 538285,
            "state": "S",
            "cgroup": fixtures.actor(538285)["cgroup"],
            "suspect": True,
        }
        self.assertEqual(release.settle(self.attempt), {"ok": False, "failure_closed": False})
        self.assert_preserved_scope()
        self.assertEqual(self.runner.procs[999999]["state"], "S")

    def test_corrupt_scope_refuses_signals_and_reports_incomplete_recovery(self):
        self.runner.scopes[self.owned["process_scope_ref"]["path"]]["consumer_sha256"] = "d" * 64
        self.assertEqual(release.settle(self.attempt), {"ok": False, "failure_closed": False})
        self.assertEqual(self.runner.scope_captures, 1)
        self.assertFalse(
            any(isinstance(event, tuple) and event[0] == "signal" for event in self.runner.events)
        )
        failure = io.document(io.read_protected(self.directory / "failure.json"))
        self.assertIn("owned_closure_restore_failed", failure["errors"])
        self.assertIsNone(failure["closure"])
        self.assertTrue(self.marker.exists())
        self.assertEqual(
            io.document(io.read_protected(self.directory / "progress.json"))["phase"],
            "failure-closure-incomplete",
        )


if __name__ == "__main__":
    unittest.main()
