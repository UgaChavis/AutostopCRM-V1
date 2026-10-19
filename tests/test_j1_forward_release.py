"""Release orchestration faults with real evidence files and simulated services.

The signature primitive and native process actions are replaced. Envelope
freshness, binding, payload validation, hash references and marker files remain
real; tests assert externally meaningful ordering and closed writer state.
"""

from __future__ import annotations

import base64
import copy
import stat
import sys
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import j1_forward_io as io
from scripts import j1_forward_release as r

REAL_DEPLOYMENT_LOCK = r.deployment_lock


class FakeStore:
    def __init__(self, fixture):
        self.f = fixture
        self.record = None
        self.pending_plan = None
        self.head_path = fixture.root / "head.json"
        self.commit_ref = None

    def pending(self):
        return self.pending_plan

    def begin(self, plan):
        self.f.event("state.begin")
        self.pending_plan = plan

    def commit(self, plan, proofs):
        self.f.event("commit.begin")
        self.f.fault("commit")
        self.f.assertTrue(self.f.marker.exists())
        self.f.assertTrue(self.f.locked)
        self.f.assertEqual(
            set(proofs),
            {"closure", "backup", "offhost_restore", "native_acceptance", "runtime_acceptance"},
        )
        ref = io.write_new(
            self.f.directory / "synthetic-commit.json", {"plan": plan, "proofs": proofs}
        )
        self.record = {"plan": plan}
        self.commit_ref = ref
        io.write_new(self.head_path, {"commit": ref, "plan": plan})
        self.f.event("commit.durable")
        return ref

    def head(self):
        self.f.event("head.readback")
        return {
            "record": {"plan": {}} if self.f.failure == "head" else self.record,
            "sha256": self.commit_ref["sha256"] if self.commit_ref else None,
            "sequence": self.record["plan"]["sequence"] if self.record else 0,
        }

    def confirm_finished(self, plan):
        io.require(
            self.pending_plan is None and self.record == {"plan": plan}, "fixture_terminal_head"
        )
        return self.commit_ref

    def finish(self, _plan):
        self.f.event("state.finish")
        self.pending_plan = None


class FakeHost:
    def __init__(self, fixture):
        self.f = fixture
        self.native = fixture.baseline["native"]
        self.deadline = None
        self.enabled = True
        self.current = copy.deepcopy(fixture.baseline["previous_container"])

    def container(self):
        if self.f.failure == "prevalidation":
            return {**self.current, "image": "changed-before-effects"}
        return self.current

    def begin_drain(self, _attempt, _baseline):
        self.f.event("drain.begin")
        self.enabled = False
        self.f.fault("drain")
        return {"drained": True}

    def observe_idle_appserver(self):
        self.f.assertFalse(self.f.auxiliary_live)
        return self.f.event("ingress.idle")

    def replace_crm(self, candidate):
        self.f.assertFalse(self.f.barriers)
        self.f.event("candidate.replace")
        self.current = {
            **self.current,
            "revision": candidate["revision"],
            "image": candidate["image"],
        }

    def start_native(self):
        self.f.assertFalse(self.f.barriers)
        self.f.event("native.start")
        self.f.writers = dict.fromkeys(self.f.writers, True)
        self.f.fault("native_start")

    def job_status(self):
        return {
            "ok": True,
            "status": {
                "enabled": self.enabled,
                "execution_paused": not self.enabled,
                **dict.fromkeys(
                    (
                        "pending_events",
                        "active_jobs",
                        "needs_review_jobs",
                        "needs_review_events",
                        "gap_count",
                    ),
                    0,
                ),
            },
        }

    def manager(self, operation, *_args):
        self.f.event("manager." + operation)
        if operation == "hold":
            self.enabled = False
        return {"ok": True}

    def validate_manager(self, _value, _attempt, *, held):
        self.f.assertEqual(self.enabled, not held)

    def restore_duty(self):
        self.f.event("duty.restore")
        self.f.assertFalse(self.f.locked)
        self.f.assertFalse(self.f.marker.exists())
        self.f.assertIsNotNone(self.f.store.record)
        self.enabled = True

    def probes(self, *_args, held):
        self.f.event("runtime.final" if not held else "runtime.held")
        self.f.fault("final_probe")
        return {"ok": True}


class FakeMonitor:
    """Orchestration seam; monitor concurrency has its own adversarial tests."""

    def __init__(self, fixture, full, sample, marker, bindings, **_kwargs):
        self.f, self.full, self.sample, self.marker = fixture, full, sample, marker
        self.bindings = bindings
        fixture.monitor = self

    def start(self):
        self.f.event("monitor.start")
        self.initial = self.full()
        self.sample()

    def begin_capture(self):
        self.f.event("monitor.capture.begin")

    def fence(self):
        self.marker()
        return {"attempt_id": self.f.plan["attempt_id"], **self.initial}

    def finish_capture(self):
        self.f.event("monitor.capture.terminal")
        self.f.fault("terminal_scan")
        return {"terminal_full": {"reference": self.full()}}

    def stop(self):
        self.f.event("monitor.stop")
        self.f.fault("monitor_stop")

    def cancel(self):
        self.f.event("monitor.cancel")
        return self.f.failure != "monitor_nonjoin"


class ForwardReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="j1-release-synthetic-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.directory = self.root / "attempt"
        self.directory.mkdir()
        self.marker = self.root / "maintenance.json"
        parent = self.root.stat()
        self.maintenance_parent = {
            "path": str(self.root),
            "dev": parent.st_dev,
            "ino": parent.st_ino,
        }
        self.trace, self.failure, self.locked = [], None, False
        self.writers = dict.fromkeys(
            ("j1", "manager", "scheduler", "work", "personal", "wake"), True
        )
        self.barriers = False
        self.auxiliary_live = True
        self.proof_change = None
        self.inspection_change = None
        self.withhold_offhost = False
        self.manifest_sha = "c" * 64
        now = datetime.now(UTC)
        self.plan = {
            "schema": r.PLAN_SCHEMA,
            "scope": r.SCOPE,
            "host": "synthetic.example",
            "attempt_id": "00000000-0000-4000-8000-000000000001",
            "sequence": 1,
            "predecessor_sha256": "1" * 64,
            "base_sha256": "1" * 64,
            "candidate": {
                "revision": "2" * 40,
                "tree": "3" * 40,
                "image": "sha256:" + "4" * 64,
                "consumer_sha256": "5" * 64,
            },
            "created_at": (now - timedelta(seconds=1)).isoformat(),
            "deadline_utc": (now + timedelta(seconds=500)).isoformat(),
            "candidate_may_have_written": True,
            "old_writer_allowed": False,
        }
        key = self.root / "reviewer.pem"
        key.write_bytes(
            b"-----BEGIN PUBLIC KEY-----\n"
            + base64.b64encode(r.signatures.ED25519_SPKI_PREFIX + b"x" * 32)
            + b"\n-----END PUBLIC KEY-----\n"
        )
        self.baseline = {
            "schema": r.SCHEMA,
            "attempt_id": self.plan["attempt_id"],
            "native": {"base_sha256": self.plan["base_sha256"], "desired_inbound": True},
            "host": {},
            "previous_container": {
                "image": "old-image",
                "revision": "0" * 40,
                "health": "healthy",
                "running": True,
            },
            "backup_profile_sha256": r.backup.profile_sha256(),
            "deployment_lock_identity": [12, 34],
            "maintenance_parent": self.maintenance_parent,
            "review_key_ref": {"path": str(key), "sha256": io.sha(key.read_bytes())},
        }
        self.install(io, "protected_directory", self.physical_directory)
        self.install(io, "read_protected", self.read_physical)
        self.install(io, "fsync_directory", self.sync)
        self.install(r, "MARKER", self.marker)
        self.install(r.marker, "prepare", self.prepare_marker)
        self.install(r.marker, "ensure", self.ensure_marker)
        self.install(r.marker, "require_owned", self.require_owned_marker)
        self.install(r.marker, "remove_owned", self.remove_owned_marker)
        self.install(io, "confirm_durable", self.confirm_durable)
        self.install(r, "CACHE_INVENTORY", self.root / "unused-cache.json")
        self.install(r.signatures, "REVIEW_KEY_PATH", str(key))
        self.install(r.signatures, "_verify_signature", self.verify_signature)
        self.plan["baseline_ref"] = io.write_new(self.directory / "baseline.json", self.baseline)
        self.plan_ref = io.write_new(self.directory / "plan.json", self.plan)
        self.store, self.host = FakeStore(self), FakeHost(self)
        self.ownership = {
            "dropins": ["synthetic-owned-dropin"],
            "bytes_sha256": "d" * 64,
            "boot_id": "00000000-0000-4000-8000-000000000002",
            "process_scope_ref": io.write_new(self.directory / "scope.json", {"fixture": True}),
        }
        self.install(
            r, "ClosureMonitor", lambda *args, **kwargs: FakeMonitor(self, *args, **kwargs)
        )
        self.install(r, "Store", lambda *_args: self.store)
        self.install(
            r,
            "load_attempt",
            lambda _attempt: (self.directory, self.plan_ref, self.plan, self.baseline, self.host),
        )
        self.install(r, "deployment_lock", self.deployment_lock)
        self.install(r, "cleanup_signals", nullcontext)
        self.install(r.authority, "verify_preserved", self.verify_authority)
        self.install(r.closure, "close_writers", self.close_writers)
        self.install(r.closure, "prepare_closure", self.prepare_closure)
        self.install(r.closure, "observe_closed", self.observe_closed)
        self.install(r.closure, "sample_closed", lambda *_args: self.event("monitor.sample"))
        self.install(r.closure, "observe_activation", self.observe_activation)
        self.install(r.closure, "reopen_native", self.reopen_native)
        self.install(r.closure, "release_auxiliary", self.release_auxiliary)
        self.install(r.closure, "fail_closed", self.fail_closed)
        self.install(r.backup, "capture", self.capture)
        self.install(r.transport, "bundle_delta", self.bundle_delta)
        self.install(r, "guard_starts", lambda *_args: self.event("guards.fresh"))
        self.install(r, "check_container_preserved", self.check_candidate)
        original_progress = r.progress

        def progress(directory, phase, **kwargs):
            result = original_progress(directory, phase, **kwargs)
            self.event("phase." + phase)
            if phase == "awaiting-offhost-restore" and not self.withhold_offhost:
                self.publish_proof("offhost_restore")
            elif phase == "awaiting-native-runtime-acceptance":
                self.publish_proof("native_acceptance")
                self.publish_proof("runtime_acceptance")
            return result

        self.install(r, "progress", progress)
        original_write = io.write_new

        def write(path, value):
            result = original_write(path, value)
            if path == self.marker:
                self.event("marker.create")
            return result

        self.install(io, "write_new", write)
        original_marker = r.require_marker

        def require_marker(*args):
            original_marker(*args)
            self.event("marker.readback")

        self.install(r, "require_marker", require_marker)
        original_unlink = Path.unlink

        def unlink(path, *args, **kwargs):
            if path == self.marker:
                self.event("marker.remove")
                self.fault("marker_unlink")
            return original_unlink(path, *args, **kwargs)

        self.install(Path, "unlink", unlink)
        self.trace.clear()

    def install(self, owner, name, value):
        mocked = patch.object(owner, name, value)
        mocked.start()
        self.addCleanup(mocked.stop)

    @staticmethod
    def physical_directory(path, **_kwargs):
        path = Path(path)
        io.require(path.is_absolute() and ".." not in path.parts, "fixture_physical_path")
        for entry in (path, *path.parents):
            info = entry.lstat()
            io.require(
                stat.S_ISDIR(info.st_mode)
                and not entry.is_symlink()
                and not getattr(info, "st_file_attributes", 0) & 0x400,
                "fixture_physical_directory",
            )
        return path

    @classmethod
    def read_physical(cls, path, limit=2 * 1024**2):
        path = Path(path)
        cls.physical_directory(path.parent)
        before = path.lstat()
        io.require(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and not path.is_symlink()
            and before.st_size <= limit,
            "fixture_physical_file",
        )
        raw = path.read_bytes()
        after = path.lstat()
        io.require(
            len(raw) == before.st_size
            and (before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_ino, after.st_size, after.st_mtime_ns),
            "fixture_file_changed",
        )
        return raw

    def event(self, name):
        self.trace.append((name, self.locked))
        return {"ok": True}

    def prepare_marker(self):
        self.event("marker.parent.prepare")
        io.require(not self.marker.exists(), "fixture_marker_exists")
        self.fault("marker_parent")
        return dict(self.maintenance_parent)

    def require_owned_marker(self, pin, payload, *, expected_identity=None):
        io.require(pin == self.maintenance_parent, "fixture_marker_parent_changed")
        raw = self.read_physical(self.marker)
        io.require(raw == io.canonical(payload), "owned_maintenance_marker_changed")
        info = self.marker.stat()
        identity = {
            "path": str(self.marker),
            "dev": info.st_dev,
            "ino": info.st_ino,
            "mtime_ns": info.st_mtime_ns,
            "sha256": io.sha(raw),
        }
        if expected_identity is not None:
            io.require(identity == expected_identity, "marker_owned_identity_changed")
        return identity

    def ensure_marker(self, pin, payload, *, authorize):
        self.assertTrue(self.locked)
        authorize()
        if not self.marker.exists():
            authorize()
            io.write_new(self.marker, payload)
        return self.require_owned_marker(pin, payload)

    def remove_owned_marker(self, pin, payload, *, expected_identity, authorize):
        self.assertTrue(self.locked)
        authorize()
        self.require_owned_marker(pin, payload, expected_identity=expected_identity)
        authorize()
        self.marker.unlink()
        io.fsync_directory(self.marker.parent)

    def confirm_durable(self, ref):
        self.assertTrue(self.locked)
        self.assertEqual(ref, io.reference(ref["path"]))
        self.event("marker.authority.durable")

    def fault(self, name):
        if self.failure == name:
            raise RuntimeError("synthetic_" + name)

    def sync(self, path):
        if (
            path == self.marker.parent
            and not self.marker.exists()
            and "marker.remove" in self.events()
        ):
            self.event("marker.removal_fsynced")
            self.fault("marker_fsync")

    @contextmanager
    def deployment_lock(self, expected=None, *, on_failure=None):
        self.assertFalse(self.locked)
        self.assertEqual(expected, self.baseline["deployment_lock_identity"])
        self.locked = True
        self.event("lock.acquire")
        try:
            yield
        except BaseException:
            if on_failure:
                on_failure()
            raise
        finally:
            self.event("lock.release")
            self.locked = False

    def verify_authority(self, *_args, **kwargs):
        self.event("authority.running" if kwargs.get("running") else "authority.closed")
        return {"preserved": True}

    def close_writers(self, _native, directory, **_kwargs):
        self.auxiliary_live = False
        self.event("ingress.freeze")
        _kwargs["idle_observer"]()
        self.event("writers.close")
        self.writers = dict.fromkeys(self.writers, False)
        self.barriers = True
        io.write_new(directory / "ingress-drain.json", {"fixture": True})
        if not (directory / "closure-owned.json").exists():
            io.write_new(directory / "closure-owned.json", self.ownership)
        return self.ownership

    def prepare_closure(self, _native, directory):
        self.event("ownership.prepare")
        io.write_new(directory / "closure-owned.json", self.ownership)
        return self.ownership

    def observe_closed(self, _ownership, **_kwargs):
        self.assertFalse(any(self.writers.values()))
        self.assertTrue(self.marker.exists())
        self.assertTrue(_kwargs["force_full"])
        return {"full_scan_this_sample": True, "closed": True}

    def reopen_native(self, _ownership, *, deadline):
        self.assertEqual(deadline, self.host.deadline)
        self.event("writers.reopen")
        self.barriers = False
        self.assertFalse(self.auxiliary_live)

    def observe_activation(self, _ownership):
        self.assertFalse(self.barriers)
        self.assertTrue(all(self.writers.values()))
        self.assertFalse(self.auxiliary_live)
        return self.event("activation.cohort")

    def release_auxiliary(self, _ownership, commit_ref):
        self.assertTrue(self.locked)
        self.assertTrue(self.marker.exists())
        self.assertIsNotNone(self.store.record)
        self.assertTrue(Path(commit_ref["path"]).is_file())
        self.event("auxiliary.release")
        self.fault("auxiliary_release")
        self.auxiliary_live = True

    def fail_closed(self, ownership):
        self.assertIsNotNone(ownership)
        self.event("writers.fail_closed")
        self.writers = dict.fromkeys(self.writers, False)
        self.auxiliary_live = False
        self.barriers = True
        return {"closed": True}

    def capture(self, _output, _context, observer, **_kwargs):
        self.event("backup.capture")
        observer()
        self.fault("backup_capture")
        return {"manifest_sha256": self.manifest_sha}

    def bundle_delta(self, *_args, **_kwargs):
        self.event("transport.bundle")
        return {"archive": {"path": str(self.directory / "delta.tar.gz"), "sha256": "e" * 64}}

    def check_candidate(self, _previous, current, candidate):
        self.assertEqual(current["image"], candidate["image"])
        self.assertEqual(current["revision"], candidate["revision"])

    def verify_signature(self, body, signature, _parent, **_kwargs):
        value = io.document(body)
        self.event("signature." + value["kind"])
        io.require(signature == b"s" * 64, "synthetic_signature_invalid")

    def publish_proof(self, kind):
        closed = kind == "offhost_restore"
        inspection = {
            "schema": r.INSPECTION,
            "stage": "closed" if closed else "activated",
            "attempt_id": self.plan["attempt_id"],
            "plan_sha256": self.plan_ref["sha256"],
            "baseline_sha256": self.plan["baseline_ref"]["sha256"],
            "candidate": self.plan["candidate"],
            "native_preserved": True,
            "writer_closure_confirmed": closed,
            "original_guards_executed": not closed,
            "runtime_checks_passed": not closed,
        }
        if self.inspection_change:
            self.inspection_change(kind, inspection)
        inspection_path = self.directory / ("inspection-" + inspection["stage"] + ".json")
        inspection_ref = (
            io.reference(inspection_path)
            if inspection_path.exists()
            else io.write_new(inspection_path, inspection)
        )
        payload = {
            "phase": inspection["stage"],
            "plan_sha256": self.plan_ref["sha256"],
            "baseline_sha256": self.plan["baseline_ref"]["sha256"],
        }
        if closed:
            payload.update(
                actual_restore=True,
                manifest_sha256=self.manifest_sha,
                profile_sha256=self.baseline["backup_profile_sha256"],
                closed_inspection_sha256=inspection_ref["sha256"],
            )
        else:
            payload.update(
                inspection_sha256=inspection_ref["sha256"],
                native_preserved=True,
                original_guards_executed=True,
                runtime_checks_passed=True,
                candidate_image=self.plan["candidate"]["image"],
                candidate_revision=self.plan["candidate"]["revision"],
            )
        envelope = {
            "schema": r.signatures.SCHEMA,
            "kind": kind,
            "attempt_id": self.plan["attempt_id"],
            "plan_sha256": self.plan_ref["sha256"],
            "key_sha256": self.baseline["review_key_ref"]["sha256"],
            "observed_at": datetime.now(UTC).isoformat(),
            "payload": payload,
            "signature_base64": base64.b64encode(b"s" * 64).decode(),
        }
        if self.proof_change:
            self.proof_change(kind, envelope)
        io.write_new(self.directory / (kind + ".json"), envelope)

    def events(self):
        return [name for name, _held in self.trace]

    def assert_before(self, earlier, later):
        self.assertLess(self.events().index(earlier), self.events().index(later), self.trace)

    def assert_failed_closed(self, *, before_first_unlock=True):
        self.assertTrue(self.marker.exists())
        self.assertFalse(any(self.writers.values()), self.trace)
        self.assertTrue(self.barriers)
        self.assertFalse(self.host.enabled)
        cleanups = [
            (name, held)
            for name, held in self.trace
            if name in ("writers.close", "writers.fail_closed", "manager.hold")
        ]
        self.assertTrue(cleanups)
        self.assertTrue(all(held for _, held in cleanups), self.trace)
        self.assertNotIn("state.finish", self.events())
        if before_first_unlock:
            self.assertEqual(self.events().count("lock.acquire"), 1, self.trace)
            self.assert_before("writers.fail_closed", "lock.release")
            last_marker_check = max(
                index for index, value in enumerate(self.events()) if value == "marker.readback"
            )
            self.assertLess(last_marker_check, self.events().index("lock.release"), self.trace)

    def test_success_commits_before_marker_and_old_lock_release(self):
        result = r.apply(self.plan["attempt_id"])
        self.assertTrue(result["ok"])
        for earlier, later in (
            ("ingress.freeze", "ingress.idle"),
            ("ingress.idle", "writers.close"),
            ("monitor.start", "monitor.capture.begin"),
            ("monitor.capture.begin", "backup.capture"),
            ("backup.capture", "monitor.capture.terminal"),
            ("monitor.capture.terminal", "transport.bundle"),
            ("signature.offhost_restore", "monitor.stop"),
            ("monitor.stop", "writers.reopen"),
            ("backup.capture", "signature.offhost_restore"),
            ("signature.offhost_restore", "writers.reopen"),
            ("writers.reopen", "candidate.replace"),
            ("writers.reopen", "native.start"),
            ("signature.native_acceptance", "commit.begin"),
            ("signature.runtime_acceptance", "commit.begin"),
            ("commit.durable", "head.readback"),
            ("head.readback", "auxiliary.release"),
            ("auxiliary.release", "marker.remove"),
            ("marker.removal_fsynced", "lock.release"),
            ("lock.release", "duty.restore"),
            ("duty.restore", "state.finish"),
        ):
            self.assert_before(earlier, later)
        self.assertFalse(self.marker.exists())
        self.assertTrue(self.host.enabled)
        self.assertTrue(self.auxiliary_live)
        captured = io.document(io.read_protected(self.directory / "backup-evidence.json"))
        monitored = io.document(io.referenced(captured["capture_monitor_ref"]))
        self.assertEqual(captured["terminal_full_ref"], monitored["terminal_full"]["reference"])
        self.assertEqual(self.monitor.bindings["plan_ref"], self.plan_ref)
        self.assertEqual(
            self.monitor.bindings["closure_ref"],
            io.reference(self.directory / "closure-owned.json"),
        )

    def test_terminal_full_failure_cannot_publish_backup_or_start_transport(self):
        self.failure = "terminal_scan"
        with self.assertRaisesRegex(RuntimeError, "synthetic_terminal_scan"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertFalse((self.directory / "backup-evidence.json").exists())
        self.assertNotIn("transport.bundle", self.events())
        self.assert_before("monitor.cancel", "writers.fail_closed")

    def test_monitor_stop_failure_joins_before_failure_fencing(self):
        self.failure = "monitor_stop"
        with self.assertRaisesRegex(RuntimeError, "synthetic_monitor_stop"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assert_before("monitor.cancel", "writers.fail_closed")
        self.assertNotIn("writers.reopen", self.events())

    def test_nonjoining_reader_ends_only_consumer_for_fixed_cold_settlement(self):
        self.failure = "monitor_nonjoin"
        self.withhold_offhost = True
        with (
            patch.object(r.time, "sleep", side_effect=TimeoutError("synthetic timeout")),
            patch.object(r.os, "_exit", side_effect=SystemExit(2)) as terminate,
            self.assertRaises(SystemExit),
        ):
            r.apply(self.plan["attempt_id"])
        terminate.assert_called_once_with(2)
        self.assertNotIn("writers.fail_closed", self.events())
        self.assertNotIn("writers.reopen", self.events())
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.store.pending(), self.plan)
        state = io.document(io.read_protected(self.directory / "progress.json"))
        self.assertEqual(state["phase"], "failure-closure-incomplete")
        self.assertFalse(state["readers_stopped"])

    def test_auxiliary_release_failure_refences_before_lock_opens(self):
        self.failure = "auxiliary_release"
        with self.assertRaisesRegex(RuntimeError, "synthetic_auxiliary_release"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertIsNotNone(self.store.record)
        self.assertFalse(self.auxiliary_live)
        self.assertNotIn("marker.remove", self.events())

    def test_premutation_validation_failure_has_no_effects(self):
        self.failure = "prevalidation"
        with self.assertRaisesRegex(io.Refusal, "baseline_container_changed"):
            r.apply(self.plan["attempt_id"])
        self.assertFalse(self.marker.exists())
        self.assertIsNone(self.store.pending_plan)
        self.assertTrue(all(self.writers.values()))
        for event in (
            "drain.begin",
            "writers.close",
            "writers.fail_closed",
            "manager.hold",
            "commit.begin",
            "candidate.replace",
        ):
            self.assertNotIn(event, self.events())

    def test_invalid_marker_parent_fails_before_pending_or_closure_ownership(self):
        self.failure = "marker_parent"
        with self.assertRaisesRegex(RuntimeError, "synthetic_marker_parent"):
            r.apply(self.plan["attempt_id"])
        self.assertIsNone(self.store.pending_plan)
        self.assertFalse(self.marker.exists())
        for event in ("state.begin", "ownership.prepare", "writers.fail_closed", "manager.hold"):
            self.assertNotIn(event, self.events())

    def test_changed_or_missing_marker_parent_pin_has_no_writer_effects(self):
        for pin in ({"different": True}, None):
            with self.subTest(pin=pin):
                self.baseline["maintenance_parent"] = pin
                with self.assertRaisesRegex(io.Refusal, "maintenance_parent"):
                    r.apply(self.plan["attempt_id"])
                self.assertIsNone(self.store.pending_plan)
                self.assertFalse(self.marker.exists())
                self.assertNotIn("ownership.prepare", self.events())
                self.assertNotIn("writers.fail_closed", self.events())

    def test_marker_creation_requires_exact_pending_authority(self):
        self.store.pending_plan = {**self.plan, "sequence": 2}
        with self.deployment_lock(self.baseline["deployment_lock_identity"]):
            with self.assertRaisesRegex(io.Refusal, "marker_pending_changed"):
                r.ensure_marker(self.directory, self.plan_ref, self.plan, self.baseline)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.directory / "marker-owned.json").exists())

    def test_marker_removal_requires_committed_head_even_with_matching_pending(self):
        self.store.pending_plan = self.plan
        with self.deployment_lock(self.baseline["deployment_lock_identity"]):
            r.ensure_marker(self.directory, self.plan_ref, self.plan, self.baseline)
            with self.assertRaisesRegex(io.Refusal, "marker_committed_head_required"):
                r.remove_marker(self.plan_ref, self.plan, self.baseline, {"sha256": "f" * 64})
        self.assertTrue(self.marker.exists())
        self.assertNotIn("marker.remove", self.events())

    def test_matching_marker_bytes_cannot_replace_recorded_file_identity(self):
        self.store.pending_plan = self.plan
        with self.deployment_lock(self.baseline["deployment_lock_identity"]):
            r.ensure_marker(self.directory, self.plan_ref, self.plan, self.baseline)
            replacement = self.root / "replacement.json"
            replacement.write_bytes(self.marker.read_bytes())
            replacement.replace(self.marker)
            with self.assertRaisesRegex(io.Refusal, "marker_owned_identity_changed"):
                r.require_marker(self.plan_ref, self.plan, self.baseline)
            with self.assertRaisesRegex(io.Refusal, "marker_owned_identity_changed"):
                r.ensure_marker(self.directory, self.plan_ref, self.plan, self.baseline)

    def test_partial_begin_drain_failure_closes_every_owned_writer(self):
        self.failure = "drain"
        with self.assertRaisesRegex(RuntimeError, "synthetic_drain"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertNotIn("candidate.replace", self.events())

    def test_commit_failure_keeps_marker_and_cleanup_inside_old_lock(self):
        self.failure = "commit"
        with self.assertRaisesRegex(RuntimeError, "synthetic_commit"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertNotIn("marker.remove", self.events())
        self.assertIsNone(self.store.record)

    def test_failed_durable_head_readback_cannot_open_marker(self):
        self.failure = "head"
        with self.assertRaisesRegex(io.Refusal, "durable_head_readback_failed"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertNotIn("marker.remove", self.events())

    def test_marker_removal_sync_failure_recloses_before_lock_release(self):
        self.failure = "marker_fsync"
        with self.assertRaisesRegex(RuntimeError, "synthetic_marker_fsync"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertIsNotNone(self.store.record)
        self.assertNotIn("duty.restore", self.events())

    def test_failure_after_unlock_reacquires_old_lock_before_cleanup(self):
        self.failure = "final_probe"
        with self.assertRaisesRegex(RuntimeError, "synthetic_final_probe"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed(before_first_unlock=False)
        self.assertGreaterEqual(self.events().count("lock.acquire"), 2)

    def test_offhost_proof_requires_actual_restore(self):
        self.proof_change = lambda kind, value: (
            value["payload"].update(actual_restore=False) if kind == "offhost_restore" else None
        )
        with self.assertRaisesRegex(io.Refusal, "offhost_restore_required"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertNotIn("writers.reopen", self.events())

    def test_signature_rejection_blocks_reactivation(self):
        self.proof_change = lambda kind, value: (
            value.update(signature_base64=base64.b64encode(b"x" * 64).decode())
            if kind == "offhost_restore"
            else None
        )
        with self.assertRaisesRegex(io.Refusal, "synthetic_signature_invalid"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertNotIn("candidate.replace", self.events())

    def test_stale_signed_observation_blocks_reactivation(self):
        self.proof_change = lambda kind, value: (
            value.update(observed_at="2020-01-01T00:00:00Z") if kind == "offhost_restore" else None
        )
        with self.assertRaisesRegex(io.Refusal, "proof_observation_outside_plan"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertNotIn("signature.offhost_restore", self.events())
        self.assertNotIn("candidate.replace", self.events())

    def test_mismatched_offhost_manifest_blocks_reactivation(self):
        self.proof_change = lambda kind, value: (
            value["payload"].update(manifest_sha256="9" * 64) if kind == "offhost_restore" else None
        )
        with self.assertRaisesRegex(io.Refusal, "offhost_restore_required"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertNotIn("writers.reopen", self.events())

    def test_failed_runtime_proof_never_commits_or_opens_marker(self):
        self.proof_change = lambda kind, value: (
            value["payload"].update(runtime_checks_passed=False)
            if kind == "runtime_acceptance"
            else None
        )
        with self.assertRaisesRegex(io.Refusal, "runtime_acceptance_required"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertNotIn("commit.begin", self.events())
        self.assertNotIn("marker.remove", self.events())

    def test_missing_offhost_proof_cannot_reactivate_on_wait_failure(self):
        self.withhold_offhost = True
        with patch.object(
            r.time, "sleep", side_effect=TimeoutError("synthetic review channel timeout")
        ):
            with self.assertRaises(TimeoutError):
                r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertNotIn("writers.reopen", self.events())
        self.assertNotIn("commit.begin", self.events())

    def test_signature_from_different_attempt_cannot_reactivate(self):
        self.proof_change = lambda kind, value: (
            value.update(attempt_id="00000000-0000-4000-8000-000000000002")
            if kind == "offhost_restore"
            else None
        )
        with self.assertRaisesRegex(io.Refusal, "proof_binding_mismatch"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertNotIn("candidate.replace", self.events())

    def test_partial_native_start_failure_never_commits(self):
        self.failure = "native_start"
        with self.assertRaisesRegex(RuntimeError, "synthetic_native_start"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertNotIn("commit.begin", self.events())
        self.assertNotIn("marker.remove", self.events())

    def test_failed_native_proof_never_commits(self):
        self.proof_change = lambda kind, value: (
            value["payload"].update(original_guards_executed=False)
            if kind == "native_acceptance"
            else None
        )
        with self.assertRaisesRegex(io.Refusal, "native_acceptance_required"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertNotIn("commit.begin", self.events())

    def test_cleanup_failure_is_recorded_as_incomplete(self):
        self.failure = "commit"
        with patch.object(
            r.closure, "fail_closed", side_effect=RuntimeError("synthetic stop failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic_commit"):
                r.apply(self.plan["attempt_id"])
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.host.enabled)
        failure = io.document(io.read_protected(self.directory / "failure.json"))
        self.assertIn("owned_closure_restore_failed", failure["errors"])
        state = io.document(io.read_protected(self.directory / "progress.json"))
        self.assertEqual(state["phase"], "failure-closure-incomplete")
        self.assertNotIn("state.finish", self.events())

    def test_inspection_binding_mismatch_rejected(self):
        self.inspection_change = lambda kind, value: (
            value.update(plan_sha256="9" * 64) if kind == "offhost_restore" else None
        )
        with self.assertRaisesRegex(io.Refusal, "inspection_binding_invalid"):
            r.apply(self.plan["attempt_id"])
        self.assert_failed_closed()
        self.assertNotIn("candidate.replace", self.events())

    def test_real_lock_context_calls_failure_before_descriptor_close(self):
        calls = []
        info = SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1, st_dev=12, st_ino=34
        )
        fake_path = SimpleNamespace(parent=self.root, lstat=lambda: info, stat=lambda: info)

        def flock(descriptor, flags):
            self.assertEqual((descriptor, flags), (77, 2 | 4))
            calls.append("locked")

        fcntl = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=flock)

        def cleanup():
            self.assertNotIn("closed", calls)
            calls.append("cleanup")

        with (
            patch.object(r, "LOCK", fake_path),
            patch.dict(sys.modules, {"fcntl": fcntl}),
            patch.object(r.os, "open", return_value=77),
            patch.object(r.os, "fstat", return_value=info),
            patch.object(r.os, "close", side_effect=lambda _fd: calls.append("closed")),
            patch.object(r.os, "O_NOFOLLOW", 0, create=True),
            patch.object(r.os, "O_CLOEXEC", 0, create=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic body failure"):
                with REAL_DEPLOYMENT_LOCK([12, 34], on_failure=cleanup):
                    raise RuntimeError("synthetic body failure")
        self.assertEqual(calls, ["locked", "cleanup", "closed"])


if __name__ == "__main__":
    unittest.main()
