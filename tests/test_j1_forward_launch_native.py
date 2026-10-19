"""Private native-boundary fixtures; no installed unit or production path IO."""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_launch_native as native
from scripts import j1_forward_launch_permission as journal
from scripts import j1_forward_release as release


@unittest.skipUnless(os.name == "posix", "Linux root-owned launch journal and systemd admission")
class NativeLaunchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-launch-", dir=Path.home())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.crm = self.root / "crm"
        self.crm.mkdir(mode=0o700)
        self.scripts = self.crm / "scripts"
        self.scripts.mkdir(mode=0o700)
        self.data = self.crm / "data"
        self.data.mkdir(mode=0o700)
        self.attempts = self.root / "attempts"
        self.attempts.mkdir(mode=0o700)
        self.attempt = str(uuid.uuid4())
        self.directory = self.attempts / self.attempt
        self.directory.mkdir(mode=0o700)
        self.lock = self.crm / ".autostop-deploy.lock"
        journal._new(self.lock, b"")
        for module, name, value in (
            (native, "ROOT_UID", os.getuid()),
            (native, "CRM", self.crm),
            (native, "FORWARD", self.scripts / "j1_forward_release.py"),
            (native, "ATTEMPTS", self.attempts),
            (native, "LOCK", self.lock),
            (native, "MARKER", self.data / "maintenance"),
            (journal, "ROOT", self.root / "launch-permits"),
        ):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        names = [
            "release",
            "launch_native",
            "launch_permission",
            "io",
            "state",
            "host",
            "backup",
            "closure",
        ]
        self.consumers = {}
        for name in names:
            path = self.scripts / f"j1_forward_{name}.py"
            raw = b"# private fixture source; never executed\n"
            journal._new(path, raw)
            self.consumers[str(path.relative_to(self.crm))] = journal.sha(raw)
        info = self.lock.stat()
        self.baseline = {"deployment_lock_identity": [info.st_dev, info.st_ino]}
        baseline_path = self.directory / "baseline.json"
        journal._new(baseline_path, journal.canonical(self.baseline))
        self.plan = {
            "attempt_id": self.attempt,
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
            "host": socket.gethostname(),
            "baseline_ref": {
                "path": str(baseline_path),
                "sha256": journal.sha(baseline_path.read_bytes()),
            },
            "candidate": {"consumer_sha256": journal.sha(journal.canonical(self.consumers))},
            "deadline_utc": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
        }
        path = self.directory / "plan.json"
        journal._new(path, journal.canonical(self.plan))
        self.plan_ref = {"path": str(path), "sha256": journal.sha(path.read_bytes())}
        journal._new(
            self.directory / "progress.json",
            journal.canonical(
                {"phase": "prepared", "observed_at": "fixture", "plan_ref": self.plan_ref}
            ),
        )
        self.locked = False

    def issue(self):
        return native.issue(self.plan_ref, self.consumers, self.baseline)

    def accepted(self):
        self.issue()
        store, snapshot, context = native.validate(self.attempt)
        native._transition(store, snapshot["ticket"], "submit", {"owner": native.owner()})
        self.worker = {"pid": 101, "start_ticks": 50, "invocation_id": "e" * 32}
        with patch.object(native, "observe_worker", return_value=self.worker):
            native.publish_acceptance(store, store.read(), context)
        return store

    @contextlib.contextmanager
    def deployment_lock(self, expected):
        self.assertFalse(self.locked, "D must have one owner")
        self.assertEqual(expected, self.baseline["deployment_lock_identity"])
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def test_issue_binds_plan_code_native_context_and_original_deadline(self):
        result = self.issue()
        self.assertFalse(result["activation_authorized"])
        _, snapshot, context = native.validate(self.attempt)
        self.assertEqual(context["worker_argv"], native.worker_command(self.attempt))
        self.assertEqual(
            context["deployment_lock_identity"], self.baseline["deployment_lock_identity"]
        )
        deadline = datetime.fromisoformat(self.plan["deadline_utc"]).timestamp()
        self.assertEqual(snapshot["ticket"]["expires_at"], deadline)
        self.assertFalse(snapshot["state"]["execution_possible"])

    def test_code_drift_fails_pure_bootstrap_before_application_load(self):
        self.issue()
        path = self.scripts / "j1_forward_io.py"
        path.write_bytes(b"# replacement\n")
        with patch.object(release, "load_attempt") as load:
            with self.assertRaisesRegex(journal.Refusal, "consumer_changed"):
                release.apply(self.attempt)
        load.assert_not_called()

    def mixed_plan(self):
        self.plan["schema"] = "autostop.j1.crm-manager-mcp-forward-plan.v1"
        for name in native.SUPPLEMENTAL:
            path = self.crm / name
            journal._new(path, b"# immutable private successor controller fixture\n")
            self.consumers[name] = journal.sha(path.read_bytes())
        self.plan["candidate"]["consumer_sha256"] = journal.sha(journal.canonical(self.consumers))
        path = Path(self.plan_ref["path"])
        path.write_bytes(journal.canonical(self.plan))
        self.plan_ref["sha256"] = journal.sha(path.read_bytes())

    def test_mixed_bootstrap_binds_every_supplemental_successor_controller(self):
        self.mixed_plan()
        self.issue()
        native.validate(self.attempt)
        path = self.scripts / "j1_manager_successor_transition.py"
        path.write_bytes(b"# changed before worker consumption\n")
        with patch.object(release, "load_attempt") as load:
            with self.assertRaisesRegex(journal.Refusal, "consumer_changed"):
                release.apply(self.attempt)
        load.assert_not_called()

    def test_mixed_bootstrap_refuses_an_omitted_supplemental_controller(self):
        self.mixed_plan()
        self.consumers.pop("scripts/j1_manager_successor_guard_v2.py")
        with self.assertRaisesRegex(journal.Refusal, "mixed_consumers_incomplete"):
            self.issue()

    def test_missing_ticket_fails_before_git_store_or_state_lock_creation(self):
        before = sorted(path.relative_to(self.root) for path in self.root.rglob("*"))
        with patch.object(release, "load_attempt") as load:
            with self.assertRaises(FileNotFoundError):
                release.apply(self.attempt)
        load.assert_not_called()
        self.assertEqual(
            before, sorted(path.relative_to(self.root) for path in self.root.rglob("*"))
        )

    def test_context_baseline_and_original_lock_replacements_are_rejected(self):
        self.issue()
        self.lock.rename(self.lock.with_suffix(".old"))
        journal._new(self.lock, b"")
        with self.assertRaisesRegex(journal.Refusal, "lock_changed"):
            native.validate(self.attempt)

    def test_native_acceptance_is_independent_and_does_not_admit_effects(self):
        store = self.accepted()
        self.assertIsNotNone(store.read()["state"]["acceptance"])
        self.assertFalse(store.read()["state"]["execution_possible"])

    def test_entry_owns_d_before_application_load_and_releases_it_on_load_failure(self):
        store = self.accepted()

        def fail_load(_attempt):
            self.assertTrue(self.locked)
            self.assertTrue(store.read()["state"]["execution_possible"])
            raise RuntimeError("sentinel-load-failure")

        with (
            patch.object(native, "observe_worker", return_value=self.worker),
            patch.object(release, "deployment_lock", self.deployment_lock),
            patch.object(release, "load_attempt", side_effect=fail_load),
        ):
            with self.assertRaisesRegex(RuntimeError, "sentinel-load"):
                release.apply(self.attempt)
        self.assertFalse(self.locked)

    def test_revoke_between_claim_and_d_blocks_final_entry(self):
        store = self.accepted()

        @contextlib.contextmanager
        def revoke_while_acquiring_d(expected):
            native.revoke(self.attempt)
            with self.deployment_lock(expected):
                yield

        with patch.object(native, "observe_worker", return_value=self.worker):
            with self.assertRaisesRegex(journal.Refusal, "revoked"):
                with native.Admission(self.attempt, revoke_while_acquiring_d):
                    self.fail("revoked consumer entered")
        self.assertFalse(self.locked)
        self.assertFalse(store.read()["state"]["execution_possible"])

    def test_replaced_native_worker_cannot_claim_acceptance(self):
        store = self.accepted()
        with patch.object(native, "observe_worker", return_value={**self.worker, "pid": 102}):
            with self.assertRaisesRegex(journal.Refusal, "not_accepted"):
                with native.Admission(self.attempt, self.deployment_lock):
                    self.fail("replacement entered")
        self.assertIsNone(store.read()["state"]["claim"])

    def test_accepted_consumer_keeps_single_d_owner_until_opening_point(self):
        self.accepted()
        with patch.object(native, "observe_worker", return_value=self.worker):
            with native.Admission(self.attempt, self.deployment_lock) as admission:
                self.assertTrue(self.locked)
                with admission.execution(
                    self.baseline["deployment_lock_identity"], on_failure=lambda: None
                ):
                    self.assertTrue(self.locked)
                self.assertFalse(self.locked)
                # Duty restoration can take the same D after the opening point.
                with self.deployment_lock(self.baseline["deployment_lock_identity"]):
                    self.assertTrue(self.locked)

    def test_effect_failure_callback_runs_under_d_then_d_releases(self):
        self.accepted()
        calls = []
        with patch.object(native, "observe_worker", return_value=self.worker):
            with self.assertRaisesRegex(RuntimeError, "effect"):
                with native.Admission(self.attempt, self.deployment_lock) as admission:
                    with admission.execution(
                        self.baseline["deployment_lock_identity"],
                        on_failure=lambda: calls.append(self.locked),
                    ):
                        raise RuntimeError("effect")
        self.assertEqual(calls, [True])
        self.assertFalse(self.locked)

    def test_uncertain_submit_is_consumed_and_never_retried(self):
        self.issue()
        with patch.object(
            native.subprocess, "Popen", side_effect=OSError("transport unknown")
        ) as spawn:
            with self.assertRaises(OSError):
                native.submit_and_wait(self.attempt)
            with self.assertRaisesRegex(journal.Refusal, "already_reserved"):
                native.submit_and_wait(self.attempt)
        spawn.assert_called_once()
        _, snapshot, _ = native.validate(self.attempt)
        self.assertTrue(snapshot["state"]["dispatch_possible"])
        self.assertFalse(snapshot["state"]["execution_possible"])

    def test_unentered_settle_denies_under_d_and_blocks_any_late_worker(self):
        self.issue()
        result = native.settle_unentered(self.attempt, self.deployment_lock)
        self.assertTrue(result["writer_mutation_not_begun"])
        self.assertFalse(self.locked)
        store, snapshot, _ = native.validate(self.attempt)
        self.assertTrue(snapshot["state"]["admission_closed"])
        with self.assertRaisesRegex(journal.Refusal, "revoked"):
            native._transition(store, snapshot["ticket"], "submit", {"owner": native.owner()})

    def test_legacy_or_recovery_ambiguity_requires_owned_safety_settle(self):
        self.issue()
        journal._new(self.directory / "closure-owned.json", journal.canonical({"owned": "unknown"}))
        self.assertIsNone(native.settle_unentered(self.attempt, self.deployment_lock))
        store, _, _ = native.validate(self.attempt)
        self.assertFalse(store.read()["state"]["admission_closed"])

    def test_native_unit_observer_rejects_other_incarnation_before_proc_read(self):
        self.issue()
        _, snapshot, context = native.validate(self.attempt)
        wrong = {
            "Id": "other.service",
            "LoadState": "loaded",
            "ActiveState": "active",
            "SubState": "running",
            "MainPID": "123",
            "InvocationID": "e" * 32,
            "ControlGroup": "/system.slice/other.service",
        }
        with patch.object(native, "_unit", return_value=wrong):
            with self.assertRaisesRegex(journal.Refusal, "unit_identity"):
                native.observe_worker(snapshot["ticket"], context)

    def test_original_wire_argv_remains_exactly_owned_inventory(self):
        from scripts import j1_forward_closure as closure

        with patch.object(native, "FORWARD", Path(closure.FORWARD_SCRIPT)):
            self.assertEqual(
                tuple(native.launch_command(self.attempt)),
                closure._systemd_launch_command(self.attempt),
            )

    def test_archive_preserves_admitted_launch_graph_without_original_files(self):
        from scripts import j1_forward_archive as archive

        store = self.accepted()
        ticket = store.read()["ticket"]
        receipt = store.read()["state"]["acceptance"]
        for event in ("claim", "enter"):
            native._transition(store, ticket, event, {"worker": receipt})
        baseline = {"native": {}, "attempt_id": self.attempt, "source_consumers": self.consumers}
        with (
            patch.object(archive.io, "ROOT_UID", os.getuid()),
            patch.object(
                archive.io,
                "protected_directory",
                side_effect=lambda path, **_kwargs: journal._directory(Path(path), os.getuid()),
            ),
        ):
            references = archive._launch_references(self.root, baseline)
        files = {ref["path"]: Path(ref["path"]).read_bytes() for ref in references}
        shutil.rmtree(store.root)
        self.assertEqual(archive._launch_semantic(self.root, {"baseline": baseline}, files), 1)
        self.assertFalse(store.root.exists())
        event = next(name for name in files if name.endswith("00000000000000000003.json"))
        del files[event]
        with self.assertRaisesRegex(archive.io.Refusal, "history_missing"):
            archive._launch_semantic(self.root, {"baseline": baseline}, files)

    def test_archive_refuses_unentered_scope_and_damaged_acceptance(self):
        from scripts import j1_forward_archive as archive

        store = self.accepted()
        baseline = {"native": {}, "attempt_id": self.attempt, "source_consumers": self.consumers}

        def snapshot():
            with (
                patch.object(archive.io, "ROOT_UID", os.getuid()),
                patch.object(
                    archive.io,
                    "protected_directory",
                    side_effect=lambda path, **_kwargs: journal._directory(Path(path), os.getuid()),
                ),
            ):
                references = archive._launch_references(self.root, baseline)
            return {ref["path"]: Path(ref["path"]).read_bytes() for ref in references}

        with self.assertRaisesRegex(archive.io.Refusal, "not_admitted"):
            archive._launch_semantic(self.root, {"baseline": baseline}, snapshot())
        ticket = store.read()["ticket"]
        receipt = store.read()["state"]["acceptance"]
        for event in ("claim", "enter"):
            native._transition(store, ticket, event, {"worker": receipt})
        files = snapshot()
        files[receipt["evidence_ref"]["path"]] = b"{}"
        with self.assertRaisesRegex(journal.Refusal, "receipt_changed"):
            archive._launch_semantic(self.root, {"baseline": baseline}, files)


class LaunchPortableValidationTests(unittest.TestCase):
    def test_read_snapshot_serializes_with_publication_then_releases_short_lock(self):
        held, calls = False, []

        class Store:
            @contextlib.contextmanager
            def locked(inner):
                nonlocal held
                calls.append("try")
                if calls.count("try") == 1:
                    raise BlockingIOError()
                held = True
                try:
                    yield
                finally:
                    held = False

            def read(inner):
                self.assertTrue(held)
                return {"published": True}

            def recover_publication(inner):
                raise AssertionError("pure reader cannot recover or publish")

        with patch.object(native.time, "sleep") as sleeper:
            self.assertEqual(native.coherent_snapshot(Store()), {"published": True})
        sleeper.assert_called_once()
        self.assertFalse(held)

    def test_busy_snapshot_has_a_finite_refusal_and_never_reads_partial_state(self):
        class Store:
            @contextlib.contextmanager
            def locked(inner):
                raise BlockingIOError()
                yield

            def read(inner):
                raise AssertionError("busy publication is not a readable snapshot")

        with (
            patch.object(native.time, "monotonic", side_effect=[0.0, 0.1, 0.3]),
            patch.object(native.time, "sleep"),
        ):
            with self.assertRaisesRegex(journal.Refusal, "read_busy"):
                native.coherent_snapshot(Store(), wait_seconds=0.2)

    def test_duplicate_and_nonfinite_json_are_rejected_on_every_platform(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(journal.Refusal):
                journal.document(raw)

    def test_noncanonical_uuid_and_nonhex_digest_are_rejected(self):
        for value in ("missing", str(uuid.uuid4()).upper(), 3):
            with self.subTest(value=value), self.assertRaises(journal.Refusal):
                journal._uuid(value)
        with self.assertRaises(journal.Refusal):
            journal._hash("g" * 64)


if __name__ == "__main__":
    unittest.main()
