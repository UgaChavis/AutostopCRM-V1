"""Typed mixed warm release orchestration with private evidence and fake effects."""

from __future__ import annotations

import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from scripts import j1_forward_closure as closure
from scripts import j1_forward_io as io
from scripts import j1_forward_mixed_authority as mixed
from scripts import j1_forward_mixed_backup as mixed_backup
from scripts import j1_forward_release as release
from scripts import j1_forward_variant as variant
from scripts import j1_manager_successor_guard_v2 as guard
from scripts import j1_manager_successor_transition as transition
from tests import test_j1_forward_closure as closure_tests
from tests import test_j1_forward_release as release_tests
from tests import test_j1_forward_worker as worker_tests


class MixedReleaseTests(unittest.TestCase):
    def setUp(self):
        f = self.f = release_tests.ForwardReleaseTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        transition_ref = io.write_new(f.directory / "transition.json", {"fixture": True})
        f.plan.update(
            schema=mixed.PLAN_SCHEMA,
            scope=mixed.SCOPE,
            manager_transition_ref=transition_ref,
            candidate_manager_sha=guard.REVISION,
        )
        engine = mixed_backup.engine(guard.SOURCE_ROOT)
        f.baseline.update(
            schema=variant.MIXED_BASELINE, backup_profile_sha256=engine.profile_sha256()
        )
        f.baseline["native"].update(
            schema=mixed.SCHEMA,
            sha256="a" * 64,
            predecessor_sha256="b" * 64,
            manager_transition_ref=transition_ref,
        )
        Path(f.plan["baseline_ref"]["path"]).write_bytes(io.canonical(f.baseline))
        f.plan["baseline_ref"] = io.reference(f.directory / "baseline.json")
        (f.directory / "plan.json").write_bytes(io.canonical(f.plan))
        f.plan_ref = io.reference(f.directory / "plan.json")
        f.install(release, "store_for", lambda *_: f.store)
        f.install(variant, "verify_native", lambda _p, _b, **kw: f.verify_authority(None, **kw))
        f.install(
            variant,
            "backup_engine",
            lambda _p: SimpleNamespace(capture=f.capture, profile_sha256=engine.profile_sha256),
        )
        f.install(variant, "transport_engine", lambda _p: release.transport)
        self.issued = []

        def issue(*args, **kwargs):
            f.event("successor.issue")
            f.assertTrue(f.locked)
            f.assertTrue(f.barriers)
            f.assertFalse(any(f.writers.values()))
            f.assertEqual(args[0], transition_ref)
            f.assertEqual(args[1], f.plan_ref)
            f.assertEqual(args[5]["path"], str(f.directory / ("store-drain-" + "0" * 32 + ".json")))
            f.assertEqual(kwargs["deadline"], f.host.deadline)
            self.issued.append((args, kwargs))
            f.fault("successor_issue")
            return {"fixture": "actual producer is independently covered"}

        f.install(transition, "issue_locked", issue)
        old_close = f.close_writers

        def close(*args, **kwargs):
            result = old_close(*args, **kwargs)
            now = datetime.now(UTC)
            for name, observed in (("f" * 32, now - timedelta(milliseconds=10)), ("0" * 32, now)):
                io.write_new(
                    f.directory / ("store-drain-" + name + ".json"),
                    {"observed_at": observed.isoformat(), "result": {"ok": True}},
                )
            return result

        f.install(release.closure, "close_writers", close)

        def mixed_proof(kind, envelope):
            if kind == "native_acceptance":
                envelope["payload"].pop("original_guards_executed")
                envelope["payload"]["mixed_guards_executed"] = True

        f.proof_change = mixed_proof

    def test_restore_and_stopped_cohort_precede_issue_and_six_opening(self):
        self.assertTrue(release.apply(self.f.plan["attempt_id"])["ok"])
        self.assertEqual(len(self.issued), 1)
        for before, after in (
            ("signature.offhost_restore", "successor.issue"),
            ("monitor.stop", "successor.issue"),
            ("successor.issue", "writers.reopen"),
            ("writers.reopen", "native.start"),
            ("signature.native_acceptance", "commit.begin"),
            ("commit.durable", "marker.remove"),
        ):
            self.f.assert_before(before, after)
        self.assertEqual(release.context(self.f.plan)["candidate_manager_sha"], guard.REVISION)

    def test_issuer_failure_cannot_remove_barriers_or_marker_or_finish_pending(self):
        self.f.failure = "successor_issue"
        with self.assertRaisesRegex(RuntimeError, "successor_issue"):
            release.apply(self.f.plan["attempt_id"])
        self.f.assert_failed_closed()
        self.assertNotIn("writers.reopen", self.f.events())
        self.assertEqual(self.f.store.pending(), self.f.plan)
        self.assertIsNone(self.f.store.record)

    def test_original_guard_claim_cannot_attest_mixed_native_acceptance(self):
        self.f.proof_change = None
        with self.assertRaisesRegex(io.Refusal, "native_acceptance_required"):
            release.apply(self.f.plan["attempt_id"])
        self.f.assert_failed_closed()
        self.assertIsNone(self.f.store.record)

    def test_latest_stop_failure_is_not_replaced_by_older_positive_receipt(self):
        self.f.close_writers(None, self.f.directory, idle_observer=lambda: None)
        older = self.f.directory / ("store-drain-" + "f" * 32 + ".json")
        newer = self.f.directory / ("store-drain-" + "0" * 32 + ".json")
        io.write_new(older, {"observed_at": "2026-10-19T00:00:00Z", "result": {"ok": True}})
        io.write_new(newer, {"observed_at": "2026-10-19T00:00:01Z", "result": {"ok": False}})
        self.assertEqual(release.latest_store_drain(self.f.directory), io.reference(newer))


class MixedClosureTests(unittest.TestCase):
    def setUp(self):
        f = self.f = closure_tests.ClosureTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.ref = {"path": str(f.directory / "transition.json"), "sha256": "1" * 64}
        f.baseline.update(schema=mixed.SCHEMA, manager_transition_ref=self.ref)
        self.denied = set(closure.UNITS) - set(closure.authority.NATIVE_UNITS)
        for entry in closure._paths():
            if entry["unit"] in self.denied:
                f.runner.barriers[entry["path"]] = closure.DENY_BYTES
        from unittest.mock import patch

        self.enterContext(
            patch.object(
                mixed, "load_transition", return_value=({"attempt_id": closure_tests.ATTEMPT}, {})
            )
        )
        roots = closure.writer_roots({"schema": closure.MIXED_SCHEMA})
        old_roots = f.runner.source_roots
        self.enterContext(
            patch.object(
                f.runner,
                "source_roots",
                lambda **kw: (
                    old_roots()
                    + [
                        {"path": path, "present": False}
                        for path in kw.get("roots", ())[len(closure.WRITER_ROOTS) :]
                    ]
                ),
            )
        )
        self.scans = []
        old_containers = f.runner.containers
        self.enterContext(
            patch.object(
                f.runner,
                "containers",
                lambda deadline, **kw: (self.scans.append(kw), old_containers(deadline))[1],
            )
        )
        old_scan = f.runner.descriptor_scan
        self.enterContext(
            patch.object(
                f.runner,
                "descriptor_scan",
                lambda *args, **kw: (self.scans.append(kw), old_scan(*args))[1],
            )
        )
        self.expected_roots = roots

    def test_new_closure_retains_two_existing_permanent_barriers_and_scans_new_roots(self):
        owned = self.f.close()
        self.assertEqual(owned["schema"], closure.MIXED_SCHEMA)
        self.assertEqual(owned["permanent_denied_units"], sorted(self.denied))
        self.assertEqual(len(owned["dropins"]), 8)
        self.assertTrue(self.scans)
        self.assertTrue(all(row == {"roots": self.expected_roots} for row in self.scans))
        closure.reopen_native(owned, deadline=time.monotonic() + 30)
        self.assertEqual(
            set(self.f.runner.barriers),
            {entry["path"] for entry in owned["dropins"] if entry["unit"] in self.denied},
        )
        self.assertTrue(
            all(self.f.runner.actors[pid]["stopped"] for pid in closure_tests.FREEZE_PIDS[1:])
        )
        closure.fail_closed(owned)
        self.assertEqual(len(self.f.runner.barriers), 8)

    def test_missing_or_changed_permanent_barrier_denies_before_ownership_or_effect(self):
        path = next(entry["path"] for entry in closure._paths() if entry["unit"] in self.denied)
        for value in (None, b"[Service]\nExecCondition=\n"):
            with self.subTest(value=value):
                if value is None:
                    self.f.runner.barriers.pop(path, None)
                else:
                    self.f.runner.barriers[path] = value
                with self.assertRaisesRegex(io.Refusal, "permanent_denied_barrier_changed"):
                    closure.prepare_closure(self.f.baseline, self.f.directory)
                self.assertEqual(self.f.runner.saved, {})
                self.assertFalse(self.f.runner.stopped)
        self.assertFalse(
            any(
                event[0] in {"put", "signal"}
                for event in self.f.runner.events
                if isinstance(event, tuple)
            )
        )


class MixedWorkerTests(unittest.TestCase):
    def setUp(self):
        self.f = worker_tests.WorkerTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_same_typed_protocol_validates_closed_restore_and_activated_acceptance(self):
        for stage in ("closed", "activated"):
            with self.subTest(stage=stage):
                response = self.f.response(stage, mixed=True)
                self.assertIs(self.f.validate(response), response)
                self.assertEqual(
                    response["backup_context"]["candidate_manager_sha"], guard.REVISION
                )

    def test_preinstall_observation_cannot_claim_native_acceptance(self):
        response = self.f.response(mixed=True)
        response["evidence"]["native"]["native_acceptance"] = True
        self.f.rebind_document(response, "native")
        with self.assertRaisesRegex(io.Refusal, "worker_mixed_preinstall_evidence"):
            self.f.validate(response)

    def test_legacy_guard_field_cannot_attest_mixed_runtime(self):
        response = self.f.response("activated", mixed=True)
        response["inspection"].pop("mixed_guards_executed")
        response["inspection"]["original_guards_executed"] = True
        self.f.rebind(response)
        with self.assertRaisesRegex(io.Refusal, "worker_inspection_binding"):
            self.f.validate(response)

    def test_mixed_closure_cannot_change_the_two_permanent_denied_roles(self):
        response = self.f.response(mixed=True)
        response["evidence"]["closure_ownership"]["permanent_denied_units"] = []
        self.f.rebind_document(response, "closure_ownership")
        with self.assertRaisesRegex(io.Refusal, "worker_mixed_closure_binding"):
            self.f.validate(response)

    def test_writer_scan_cannot_omit_the_new_staged_roots(self):
        response = self.f.response(mixed=True)
        response["evidence"]["closure_ownership"]["source_roots"] = [
            {"path": path, "present": False} for path in closure.WRITER_ROOTS
        ]
        self.f.rebind_document(response, "closure_ownership")
        with self.assertRaisesRegex(io.Refusal, "worker_capture_roots"):
            self.f.validate(response)

    def test_worker_cannot_relabel_legacy_backup_as_the_mixed_profile(self):
        response = self.f.response("activated", mixed=True)
        profile = worker_tests.transport.PROFILE_SHA256
        response["baseline"]["backup_profile_sha256"] = profile
        response["baseline_ref"] = self.f.ref("baseline.json", response["baseline"])
        response["plan"]["baseline_ref"] = response["baseline_ref"]
        response["plan_ref"] = self.f.ref("plan.json", response["plan"])
        response["inspection"].update(
            baseline_sha256=response["baseline_ref"]["sha256"],
            plan_sha256=response["plan_ref"]["sha256"],
        )
        response["scope_profile_sha256"] = profile
        response["backup_context"]["scope_profile_sha256"] = profile
        self.f.rebind(response)
        with self.assertRaisesRegex(io.Refusal, "worker_backup_context_invalid"):
            self.f.validate(response)


if __name__ == "__main__":
    unittest.main()
