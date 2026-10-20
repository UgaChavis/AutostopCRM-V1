"""Recovery orchestration with real immutable files; no native service effects."""

from __future__ import annotations

import copy
import os
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_io as io
from scripts import j1_forward_recovery as recovery
from scripts import j1_forward_recovery_scope as scope
from scripts import j1_forward_release as release
from tests import test_j1_forward_closure as closure_tests
from tests import test_j1_forward_release as release_tests
from tests import test_j1_forward_state as state_tests
from tests.test_j1_forward_recovery_scope import fixture as scope_fixture


class OriginTests(unittest.TestCase):
    def setUp(self):
        self.files = state_tests.ForwardStateTests()
        self.files.setUp()
        self.addCleanup(self.files.doCleanups)
        self.root = self.files.root
        for module, name, value in (
            (recovery, "ROOT", self.root),
            (recovery, "STATE", self.root / "state"),
            (recovery, "ATTEMPTS", self.root / "attempts"),
            (recovery, "ORIGINS", self.root / "origins"),
            (recovery, "CRM", self.root / "checkout"),
            (release, "ATTEMPTS", self.root / "attempts"),
        ):
            self.install(module, name, value)
        self.install(os, "geteuid", lambda: 0, create=True)
        if os.name == "nt":
            self.install(os, "O_NOFOLLOW", 0, create=True)
        native, old_scope, _observed = scope_fixture()
        self.old = recovery.ATTEMPTS / old_scope["attempt_id"]
        self.retained = recovery.ORIGINS / old_scope["attempt_id"]
        self.old.mkdir(parents=True)
        (self.retained / "consumers/scripts").mkdir(parents=True)
        (recovery.CRM / "scripts").mkdir(parents=True)
        self.raw = b"# immutable original consumer; never executed\n"
        name = "scripts/j1_forward_release.py"
        (recovery.CRM / name).write_bytes(self.raw)
        (self.retained / "consumers" / name).write_bytes(self.raw)
        self.consumers = {name: io.sha(self.raw)}
        consumer_sha = io.sha(io.canonical(self.consumers))
        native["base_sha256"] = self.files.base
        coordinated = io.write_new(self.old / "coordinated.json", {"desired": "original"})
        self.baseline = {
            "attempt_id": self.old.name,
            "native": native,
            "source_consumers": self.consumers,
            "deployment_lock_identity": [1, 55],
            "host": {"coordinated_manifest": coordinated},
            "previous_container": {"image": "original"},
        }
        self.plan = self.files.plan(attempt=self.old.name)
        self.plan["candidate"]["consumer_sha256"] = consumer_sha
        self.plan["baseline_ref"] = io.write_new(self.old / "baseline.json", self.baseline)
        plan_ref = io.write_new(self.old / "plan.json", self.plan)
        pending_ref = self.files.store.begin(self.plan)
        io.write_new(self.retained / "pending.json", self.plan)
        old_scope.update(
            consumer_sha256=consumer_sha,
            native_sha256=io.sha(io.canonical(native)),
            base_sha256=self.files.base,
        )
        scope_ref = io.write_new(self.old / "process-scope.json", old_scope)
        closure_ref = io.write_new(
            self.old / "closure-owned.json",
            {
                "attempt_id": self.old.name,
                "process_scope_ref": scope_ref,
                "baseline_sha256": old_scope["native_sha256"],
                "consumer_sha256": consumer_sha,
            },
        )
        failure_ref = io.write_new(
            self.old / "failure.json",
            {"ok": False, "plan_ref": plan_ref, "production_restore_attempted": False},
        )
        bundle = {
            "schema": recovery.BUNDLE_SCHEMA,
            "origin_attempt_id": self.old.name,
            "origin_plan_ref": plan_ref,
            "origin_baseline_ref": self.plan["baseline_ref"],
            "source_revision": self.plan["candidate"]["revision"],
            "source_tree": self.plan["candidate"]["tree"],
            "source_consumers": self.consumers,
            "consumer_sha256": consumer_sha,
            "files": {name: io.reference(self.retained / "consumers" / name)},
        }
        self.origin = {
            "nonce": "a" * 64,
            "origin_pending_ref": pending_ref,
            "origin_plan_ref": plan_ref,
            "origin_scope_ref": scope_ref,
            "origin_closure_ref": closure_ref,
            "origin_failure_ref": failure_ref,
            "origin_bundle_ref": io.write_new(self.retained / "bundle.json", bundle),
            "deployment_lock_identity": self.baseline["deployment_lock_identity"],
            **{
                key: io.write_new(self.old / (key + ".json"), {"technical": key})
                for key in ("recovery_code_ref", "ci_ref", "hold_ref")
            },
        }

    def install(self, module, name, value, **kwargs):
        patcher = patch.object(module, name, value, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_origin_validation_reads_real_scope_bundle_and_retained_pending(self):
        original = self.files.store.pending_path.read_bytes()
        result = recovery.validate_origin(self.origin)
        self.assertEqual(result["plan"], self.plan)
        self.assertEqual(result["baseline"], self.baseline)
        self.assertEqual(self.files.store.pending_path.read_bytes(), original)
        self.files.store.pending_path.unlink()
        self.assertEqual(recovery.validate_origin(self.origin), result)

    def test_origin_archive_tamper_is_not_hidden_by_valid_live_pending(self):
        archive = self.retained / "pending.json"
        archive.write_bytes(archive.read_bytes() + b"\n")
        with self.assertRaisesRegex(io.Refusal, "recovery_origin_pending_archive"):
            recovery.validate_origin(self.origin)
        self.assertEqual(self.files.store.pending(), self.plan)

    def test_sealed_source_tamper_is_not_hidden_by_matching_bundle(self):
        path = self.retained / "consumers/scripts/j1_forward_release.py"
        path.write_bytes(self.raw + b"# changed\n")
        with self.assertRaises(io.Refusal):
            recovery.validate_origin(self.origin)

    def test_foreign_scope_reference_is_rejected_even_when_bytes_match(self):
        altered = copy.deepcopy(self.origin)
        foreign = self.old / "foreign-scope.json"
        foreign.write_bytes(io.referenced(altered["origin_scope_ref"]))
        altered["origin_scope_ref"] = io.reference(foreign)
        with self.assertRaisesRegex(io.Refusal, "recovery_origin_paths"):
            recovery.validate_origin(altered)

    def test_seal_preserves_checkout_and_live_pending_without_source_update(self):
        (self.retained / "bundle.json").unlink()
        archived = self.retained / "consumers/scripts/j1_forward_release.py"
        archived.unlink()
        before_source = (recovery.CRM / "scripts/j1_forward_release.py").read_bytes()
        before_pending = self.files.store.pending_path.read_bytes()

        def portable_durable(reference):
            raw = io.referenced(reference)
            # Windows refuses fsync on O_RDONLY; retain the actual file barrier.
            with Path(reference["path"]).open("r+b") as stream:
                os.fsync(stream.fileno())
            self.assertEqual(io.referenced(reference), raw)

        with (
            patch.object(release, "deployment_lock", return_value=nullcontext()) as lock,
            patch.object(recovery, "no_old_controller") as controller,
            patch.object(release, "source_consumers", return_value=self.consumers),
            patch.object(io, "confirm_durable", side_effect=portable_durable) as durable,
        ):
            result = recovery.seal_origin(self.old.name)
        lock.assert_called_once_with(self.baseline["deployment_lock_identity"])
        controller.assert_called_once_with(self.old.name)
        self.assertTrue(result["ok"])
        self.assertEqual(archived.read_bytes(), before_source)
        self.assertEqual(
            (recovery.CRM / "scripts/j1_forward_release.py").read_bytes(), before_source
        )
        self.assertEqual(self.files.store.pending_path.read_bytes(), before_pending)
        durable.assert_called_once_with(result["origin_bundle_ref"])

    def test_unknown_backlog_refuses_prepare_before_new_attempt_or_any_writer_effect(self):
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        pending = self.files.store.pending_path.read_bytes()
        with (
            patch.object(release, "deployment_lock", return_value=nullcontext()),
            patch.object(recovery, "no_old_controller"),
            patch.object(release.marker, "prepare", return_value={"fixed": "parent"}),
            patch.object(
                scope.OBSERVER, "snapshot", side_effect=io.Refusal("unattributed_listener_backlog")
            ),
            patch.object(recovery.uuid, "uuid4") as new_id,
            patch.object(recovery, "Host") as host,
            patch.object(release.marker, "ensure") as marker,
            patch.object(release.closure.RUNNER, "stop") as stop,
            patch.object(release.closure.RUNNER, "signal_actor") as signal,
            patch.object(io, "write_new", wraps=io.write_new) as write,
            patch.object(recovery.state.Store, "begin_recovery") as begin,
        ):
            with self.assertRaisesRegex(io.Refusal, "unattributed_listener_backlog"):
                recovery.prepare("sha256:" + "f" * 64, 123, self.old.name)
        for effect in (new_id, host, marker, stop, signal, write, begin):
            effect.assert_not_called()
        self.assertEqual(self.files.store.pending_path.read_bytes(), pending)
        self.assertEqual(
            sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")), before
        )

    def test_recovery_candidate_ci_refusal_never_requests_hold_pause_or_writer_effect(self):
        source = {"revision": "c" * 40, "tree": "d" * 40}
        with (
            patch.object(release, "deployment_lock", return_value=nullcontext()),
            patch.object(recovery, "no_old_controller"),
            patch.object(release.marker, "prepare", return_value={"fixed": "parent"}),
            patch.object(scope.OBSERVER, "snapshot", return_value={"metadata": "preflight"}),
            patch.object(recovery, "Host") as host,
            patch.object(
                release, "verify_ci", side_effect=io.Refusal("candidate_ci_not_successful")
            ) as ci,
            patch.object(release.marker, "ensure") as marker,
            patch.object(release.closure.RUNNER, "stop") as stop,
            patch.object(release.closure.RUNNER, "signal_actor") as signal,
        ):
            host.return_value.source.return_value = source
            with self.assertRaisesRegex(io.Refusal, "candidate_ci_not_successful"):
                recovery.prepare("sha256:" + "f" * 64, 123, self.old.name)
        ci.assert_called_once_with(123, source)
        host.return_value.manager.assert_not_called()
        host.return_value.pause_work.assert_not_called()
        for effect in (marker, stop, signal):
            effect.assert_not_called()
        self.assertEqual(self.files.store.pending(), self.plan)

    def test_changed_original_consumer_cannot_be_sealed_as_historical_source(self):
        before = self.files.store.pending_path.read_bytes()
        with (
            patch.object(release, "deployment_lock", return_value=nullcontext()),
            patch.object(recovery, "no_old_controller"),
            patch.object(
                release,
                "source_consumers",
                return_value={"scripts/j1_forward_release.py": "f" * 64},
            ),
            patch.object(io, "write_new", wraps=io.write_new) as write,
        ):
            with self.assertRaisesRegex(io.Refusal, "recovery_original_source_changed"):
                recovery.seal_origin(self.old.name)
        write.assert_not_called()
        self.assertEqual(self.files.store.pending_path.read_bytes(), before)

    def test_recovery_host_routes_hold_to_origin_and_preserves_coordinated_baseline(self):
        new = self.root / "new-attempt"
        native = {**self.baseline["native"], "recovery_origin": self.origin}
        host = recovery.RecoveryHost(native, new, host_baseline=self.baseline["host"])
        self.assertEqual(host._baseline(), self.baseline["host"])
        with (
            patch.object(recovery.Host, "manager", return_value={"ok": True}) as manager,
            patch.object(recovery.Host, "validate_manager", return_value={"ok": True}) as validate,
        ):
            host.manager("hold", new.name)
            host.manager("release-hold", new.name)
            host.validate_manager({"ok": True}, new.name, held=True)
        self.assertEqual(
            [call.args for call in manager.call_args_list],
            [("hold", self.old.name), ("release-hold", self.old.name)],
        )
        validate.assert_called_once_with({"ok": True}, self.old.name, held=True)
        with self.assertRaisesRegex(io.Refusal, "recovery_hold_attempt"):
            host.manager("hold", "foreign")
        host.host_baseline = {"changed": True}
        with self.assertRaisesRegex(io.Refusal, "recovery_host_baseline_changed"):
            host._baseline()

    def test_recovery_drain_requires_current_zero_leases_and_sending_before_pause(self):
        native = {**self.baseline["native"], "recovery_origin": self.origin}
        host = recovery.RecoveryHost(
            native, self.root / "new-attempt", host_baseline=self.baseline["host"]
        )
        for jobs, sending in (([{"lease_until": "2099-01-01T00:00:00Z"}], 0), ([], 1)):
            status = {
                "ok": True,
                "data": {
                    "jobs": jobs,
                    "readiness": {
                        "execution_packet": {
                            "outbox": {"by_status": {"sending": sending}, "total_count": sending}
                        }
                    },
                },
            }
            with (
                self.subTest(jobs=jobs, sending=sending),
                patch.object(recovery.Host, "manager", return_value=status),
                patch.object(recovery.Host, "validate_manager"),
                patch.object(host, "pause_work") as pause,
            ):
                with self.assertRaisesRegex(io.Refusal, "scheduler_not_drained"):
                    host.begin_drain(host.directory.name, self.baseline["host"])
                pause.assert_not_called()


class RecoveryStoreTests(unittest.TestCase):
    def setUp(self):
        self.files = state_tests.ForwardStateTests()
        self.files.setUp()
        self.addCleanup(self.files.doCleanups)
        self.origin, self.plan, self.envelope, self.proofs = self.files.recovery_fixture()
        for name, value in (
            ("STATE", self.files.store_root),
            ("ATTEMPTS", self.files.root / "attempts"),
        ):
            patcher = patch.object(recovery, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.baseline = io.document(io.referenced(self.plan["baseline_ref"]))
        self.adapter = recovery.RecoveryStore(self.plan, self.baseline)

    def test_original_pending_is_only_adapted_by_explicit_v2_begin(self):
        raw = self.files.store.pending_path.read_bytes()
        self.assertIsNone(self.adapter.pending())
        self.assertEqual(self.files.store.active_plan()["plan"], self.origin)
        self.adapter.begin(self.plan)
        self.assertEqual(self.adapter.pending(), self.plan)
        self.assertEqual(self.files.store.pending_path.read_bytes(), raw)
        self.assertEqual(
            self.files.store.active_plan()["recovery_ref"],
            self.files.store._recovery_ref(self.envelope),
        )

    def test_foreign_original_pending_cannot_be_presented_as_empty(self):
        foreign = {**self.origin, "attempt_id": "11111111-1111-4111-8111-111111111111"}
        self.files.store.pending_path.write_bytes(io.canonical(foreign))
        with self.assertRaisesRegex(io.Refusal, "recovery_original_pending_changed"):
            self.adapter.pending()

    def test_interrupted_pointer_publication_leaves_original_pending_and_no_marker_authority(self):
        original = io.write_new

        def fail_pointer(path, value):
            if Path(path) == self.files.store.recovery_path:
                raise OSError("pointer write failed")
            return original(path, value)

        with patch.object(io, "write_new", side_effect=fail_pointer):
            with self.assertRaisesRegex(OSError, "pointer write failed"):
                self.adapter.begin(self.plan)
        self.assertIsNone(self.adapter.pending())
        self.assertEqual(self.files.store.pending(), self.origin)
        with self.assertRaises(io.Refusal):
            self.adapter.confirm_finished(self.plan)
        self.adapter.begin(self.plan)
        self.assertEqual(self.adapter.pending(), self.plan)

    def test_terminal_adapter_requires_v2_commit_and_retains_original_archive(self):
        self.adapter.begin(self.plan)
        with self.assertRaises(io.Refusal):
            self.adapter.confirm_finished(self.plan)
        commit = self.adapter.commit(self.plan, self.proofs)
        self.adapter.finish(self.plan)
        self.assertIsNone(self.adapter.pending())
        self.assertEqual(self.adapter.confirm_finished(self.plan), commit)
        self.assertEqual(
            io.document(io.referenced(self.files.store._archive_ref(self.envelope))), self.origin
        )

    def test_terminal_confirmation_rejects_pending_reappearing_during_durability(self):
        self.adapter.begin(self.plan)
        self.adapter.commit(self.plan, self.proofs)
        self.adapter.finish(self.plan)
        durable = self.adapter.store._durable
        foreign = self.files.plan(sequence=2)

        def inject_pending(reference):
            result = durable(reference)
            if not self.files.store.pending_path.exists():
                self.files.store.pending_path.write_bytes(io.canonical(foreign))
            return result

        with patch.object(self.adapter.store, "_durable", side_effect=inject_pending):
            with self.assertRaises(io.Refusal):
                self.adapter.confirm_finished(self.plan)


class RecoveryApplyTests(unittest.TestCase):
    def setUp(self):
        self.f = release_tests.ForwardReleaseTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.baseline["recovery_origin"] = {"synthetic": "explicit-origin"}
        self.f.install(recovery, "RecoveryStore", lambda _plan, _baseline: self.f.store)
        self.f.install(recovery, "prepared_ownership", self.owned)
        self.f.install(release.closure, "close_recovery", self.closed)
        self.f.install(release.closure, "release_auxiliary", self.release_auxiliary)
        self.f.install(release.closure, "observe_released", self.observe_released)
        self.released_failure = None
        self.actual_release = None
        self.actual_readback = None

    def owned(self, directory, _baseline):
        self.f.event("recovery.ownership")
        return self.f.prepare_closure({}, directory)

    def closed(self, owned, **kwargs):
        self.assertEqual(owned, self.f.ownership)
        self.f.event("recovery.close")
        return self.f.close_writers({}, self.f.directory, **kwargs)

    def released_payload(self, owned):
        return {
            "schema": "autostop.j1.crm-forward-released-observation.v2",
            "attempt_id": self.f.plan["attempt_id"],
            "process_scope_ref": owned["process_scope_ref"],
            "freeze_actors": [
                {"pid": pid, "start_ticks": scope.STARTS[pid], "stopped": pid in scope.ORPHANS}
                for pid in scope.FREEZE
            ],
            "transport_actors": [
                {"pid": pid, "start_ticks": scope.STARTS[pid], "stopped": False}
                for pid in scope.TRANSPORTS
            ],
            "process_scope_observation": {
                "scope_sha256": owned["process_scope_ref"]["sha256"],
                "auxiliary_released": True,
                "retained_frozen": list(scope.ORPHANS),
            },
            "disposition": scope.DISPOSITION,
            "retained_frozen": list(scope.ORPHANS),
            "sampled_at": 123.0,
        }

    def release_auxiliary(self, owned, commit_ref):
        self.f.release_auxiliary(owned, commit_ref)
        self.actual_release = {
            **self.released_payload(owned),
            "schema": "autostop.j1.crm-forward-auxiliary-release.v2",
            "commit_ref": commit_ref,
        }
        return copy.deepcopy(self.actual_release)

    def observe_released(self, owned):
        self.assertTrue(self.f.auxiliary_live)
        self.assertFalse(self.f.barriers)
        self.assertEqual(owned, self.f.ownership)
        self.f.event("released.fresh_readback")
        if self.released_failure:
            raise io.Refusal(self.released_failure)
        self.actual_readback = self.released_payload(owned)
        return copy.deepcopy(self.actual_readback)

    def test_recovery_uses_normal_backup_restore_native_acceptance_and_commit_pipeline(self):
        result = release.apply(self.f.plan["attempt_id"])
        self.assertTrue(result["ok"])
        for before, after in (
            ("recovery.ownership", "authority.running"),
            ("authority.running", "state.begin"),
            ("state.begin", "marker.create"),
            ("drain.begin", "recovery.close"),
            ("recovery.close", "backup.capture"),
            ("backup.capture", "transport.bundle"),
            ("phase.awaiting-offhost-restore", "candidate.replace"),
            ("native.start", "guards.fresh"),
            ("guards.fresh", "commit.begin"),
            ("commit.durable", "auxiliary.release"),
            ("runtime.final", "released.fresh_readback"),
            ("released.fresh_readback", "state.finish"),
        ):
            self.f.assert_before(before, after)
        completed = io.document(io.referenced(result["completion"]))
        for key, label, actual in (
            ("auxiliary_release_ref", "auxiliary-release", self.actual_release),
            ("released_cohort_ref", "released-cohort", self.actual_readback),
        ):
            reference = completed[key]
            self.assertEqual(Path(reference["path"]).parent, self.f.directory)
            self.assertRegex(Path(reference["path"]).name, "^" + label + r"-[0-9a-f]{32}\.json$")
            self.assertEqual(io.document(io.referenced(reference)), actual)
            self.assertEqual(actual["retained_frozen"], list(scope.ORPHANS))
            self.assertEqual(actual["process_scope_ref"], self.f.ownership["process_scope_ref"])
        self.assertEqual(self.actual_release["commit_ref"], completed["commit"])

    def test_final_released_readback_failure_refences_and_cannot_complete(self):
        self.released_failure = "orphan_proxy_thawed"
        with self.assertRaisesRegex(io.Refusal, "orphan_proxy_thawed"):
            release.apply(self.f.plan["attempt_id"])
        self.assertIn("released.fresh_readback", self.f.events())
        self.assertIn("writers.fail_closed", self.f.events())
        self.assertNotIn("state.finish", self.f.events())
        self.assertFalse((self.f.directory / "completion.json").exists())
        self.assertTrue(self.f.marker.exists())
        self.assertFalse(any(self.f.writers.values()))
        self.assertFalse(self.f.auxiliary_live)
        self.assertEqual(self.f.store.pending(), self.f.plan)

    def test_recovery_preclose_authority_refusal_precedes_begin_marker_and_drain(self):
        def reject(_native, **kwargs):
            self.assertTrue(kwargs["preclose"])
            self.assertTrue(kwargs["running"])
            self.assertEqual(kwargs["owned_closure"], release.projection(self.f.ownership))
            raise io.Refusal("original_guard_sequence_changed")

        self.f.install(release.authority, "verify_preserved", reject)
        with self.assertRaisesRegex(io.Refusal, "original_guard_sequence_changed"):
            release.apply(self.f.plan["attempt_id"])
        for event in (
            "state.begin",
            "marker.create",
            "drain.begin",
            "recovery.close",
            "backup.capture",
            "writers.fail_closed",
        ):
            self.assertNotIn(event, self.f.events())

    def test_competing_begin_failure_cannot_authorize_marker_or_writer_cleanup(self):
        foreign = {**self.f.plan, "attempt_id": "11111111-1111-4111-8111-111111111111"}

        def begin(_plan):
            self.f.store.pending_plan = foreign
            raise io.Refusal("state_recovery_mismatch")

        self.f.store.begin = begin
        with self.assertRaisesRegex(io.Refusal, "state_recovery_mismatch"):
            release.apply(self.f.plan["attempt_id"])
        self.assertEqual(self.f.store.pending(), foreign)
        self.assertFalse(self.f.marker.exists())
        self.assertTrue(all(self.f.writers.values()))
        for event in (
            "drain.begin",
            "recovery.close",
            "writers.fail_closed",
            "marker.create",
            "manager.hold",
        ):
            self.assertNotIn(event, self.f.events())

    def test_partial_begin_publication_with_exact_new_pending_still_refences(self):
        def begin(plan):
            self.f.store.pending_plan = plan
            raise OSError("pending published but acknowledgment lost")

        self.f.store.begin = begin
        with self.assertRaisesRegex(OSError, "acknowledgment lost"):
            release.apply(self.f.plan["attempt_id"])
        self.assertEqual(self.f.store.pending(), self.f.plan)
        self.assertTrue(self.f.marker.exists())
        self.assertFalse(any(self.f.writers.values()))
        self.assertFalse(self.f.auxiliary_live)
        self.assertIn("writers.fail_closed", self.f.events())
        self.assertIn("manager.hold", self.f.events())
        self.assertNotIn("recovery.close", self.f.events())


class ReleasedCohortReadbackTests(unittest.TestCase):
    def setUp(self):
        self.f = closure_tests.RecoveryClosureTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.close()
        release.closure.reopen_native(self.f.owned)
        self.f.runner.native_peer_live = True
        self.f.runner.commit_ready = True
        release.closure.release_auxiliary(self.f.owned, {"commit": "owned"})
        self.f.runner.events.clear()

    def test_fresh_released_readback_records_exact_orphans_and_has_no_signal_effect(self):
        observed = release.closure.observe_released(self.f.owned)
        self.assertEqual(observed["retained_frozen"], list(scope.ORPHANS))
        self.assertEqual(observed["disposition"], scope.DISPOSITION)
        self.assertEqual(
            [row["pid"] for row in observed["freeze_actors"] if row["stopped"]], list(scope.ORPHANS)
        )
        self.assertFalse(
            any(isinstance(event, tuple) and event[0] == "signal" for event in self.f.runner.events)
        )

    def test_late_orphan_thaw_is_rejected_without_signals_or_new_admission(self):
        self.f.runner.actors[scope.ORPHANS[0]]["stopped"] = False
        with self.assertRaisesRegex(io.Refusal, "scope_actor_stop_state"):
            release.closure.observe_released(self.f.owned)
        self.assertFalse(
            any(isinstance(event, tuple) and event[0] == "signal" for event in self.f.runner.events)
        )

    def test_late_helper_stop_is_rejected_by_fresh_released_readback(self):
        self.f.runner.actors[scope.HELPERS[0]]["stopped"] = True
        with self.assertRaisesRegex(io.Refusal, "scope_actor_stop_state"):
            release.closure.observe_released(self.f.owned)


if __name__ == "__main__":
    unittest.main()
