"""Real Store stop gates and recovery; OS, services and HTTP remain synthetic."""

from __future__ import annotations

import copy
import os
import signal
import socket
import subprocess
import tempfile
import time
import unittest
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import j1_forward_closure as closure
from scripts import j1_forward_io as io
from scripts import j1_forward_release as release
from scripts import j1_forward_store as store_drain
from tests import test_j1_forward_closure as fixtures
from tests import test_j1_forward_release as release_fixtures


class StoreRunner(fixtures.FakeRunner):
    # Keep the production gate, protected baseline loader, consumer hashes and
    # immutable receipt writes. Only Linux observations and commands are fake.
    stop = closure.LinuxRunner.stop
    _store_stop = closure.LinuxRunner._store_stop
    _store_stop_receipt = staticmethod(closure.LinuxRunner._store_stop_receipt)
    _control_baseline = closure.LinuxRunner._control_baseline
    control_bindings = closure.LinuxRunner.control_bindings

    def __init__(self, case):
        super().__init__()
        self.case = case

    def command(self, arguments, deadline):
        self.case.commands.append(arguments)
        self.case.assertGreater(deadline, time.monotonic())
        self.case.assertTrue(self.case.drained_observed, "Stop before actual Store completion")
        receipts = self.case.receipts()
        self.case.assertTrue(receipts, "Stop before durable Store receipt")
        self.case.assertTrue(any(row["result"]["stop_permitted"] for row in receipts))
        self.events.append("stop")
        self.stopped = True
        return b""


class StoreStopIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="j1-store-stop-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.attempt = fixtures.ATTEMPT
        self.directory = self.root / self.attempt
        self.directory.mkdir()
        self.marker = self.root / "maintenance.json"
        self.marker_parent = {"path": str(self.root)}
        self.crm = self.root / "crm"
        (self.crm / "scripts").mkdir(parents=True)
        self.commands, self.requests, self.observations = [], [], []
        self.held = self.posted = self.drained_observed = False
        self.polls, self.now = 0, 1000.0
        self.mode = "complete"
        self.pending = None
        self.instance = "d" * 32
        self.identity = {
            "pid": 42,
            "start_ticks": "123",
            "boot_id": "00000000-0000-4000-8000-000000000002",
            "revision": "b" * 40,
            "invocation_id": "e" * 32,
            "source_root": "/sealed/manager",
        }
        self.install(time, "monotonic", lambda: self.now)
        self.install(time, "sleep", self.advance)
        for owner, name in (
            (subprocess, "Popen"),
            (socket, "socket"),
            (socket, "create_connection"),
            (socket, "getaddrinfo"),
            (os, "kill"),
        ):
            self.install(owner, name, Mock(side_effect=AssertionError("External effect forbidden")))
        self.install(
            signal,
            "pidfd_send_signal",
            Mock(side_effect=AssertionError("Real signal forbidden")),
            create=True,
        )
        self.install(
            io, "protected_directory", release_fixtures.ForwardReleaseTests.physical_directory
        )
        self.install(io, "read_protected", release_fixtures.ForwardReleaseTests.read_physical)
        self.install(io, "fsync_directory", Mock())
        self.install(closure, "CRM", self.crm)
        self.install(closure, "ATTEMPTS", self.root)
        self.runner = StoreRunner(self)
        self.install(closure, "RUNNER", self.runner)
        closure._CACHE.clear()
        self.addCleanup(closure._CACHE.clear)
        consumers = {}
        for name in ("release", "authority", "backup", "closure", "host", "io", "state", "store"):
            relative = "scripts/j1_forward_" + name + ".py"
            (self.crm / relative).write_bytes(("# synthetic " + name + "\n").encode())
            consumers[relative] = io.reference(self.crm / relative)["sha256"]
        self.native = {
            "base_sha256": "a" * 64,
            "manager_revision": self.identity["revision"],
            "warm_actors": [fixtures.actor(pid) for pid in sorted(closure.ACCEPTED_PIDS)],
        }
        self.native["sha256"] = io.sha(io.canonical(self.native))
        self.baseline = {
            "attempt_id": self.attempt,
            "native": self.native,
            "host": {},
            "deployment_lock_identity": [123, 456],
            "source_consumers": consumers,
            "maintenance_parent": self.marker_parent,
            "previous_container": {"synthetic": "old-container"},
        }
        self.plan = {
            "attempt_id": self.attempt,
            "base_sha256": "a" * 64,
            "candidate": {"consumer_sha256": io.sha(io.canonical(consumers))},
            "created_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            "deadline_utc": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "baseline_ref": io.write_new(self.directory / "baseline.json", self.baseline),
        }
        self.plan_ref = io.write_new(self.directory / "plan.json", self.plan)
        self.owned = closure.prepare_closure(self.native, self.directory)
        io.write_new(self.directory / "closure-owned.json", self.owned)
        self.store = Mock()
        self.store.pending.side_effect = lambda: self.pending
        self.store.begin.side_effect = self.begin
        self.host = Mock()
        self.host.container.return_value = self.baseline["previous_container"]
        self.host.begin_drain.return_value = {"ok": True}
        self.install(store_drain, "_identity", self.current_identity)
        self.install(store_drain, "_request", self.request)
        self.install(release, "MARKER", self.marker)
        self.install(release.marker, "prepare", lambda: dict(self.marker_parent))
        self.install(release.marker, "ensure", self.ensure_marker)
        self.install(release.marker, "require_owned", self.require_owned_marker)
        self.install(
            release.marker,
            "remove_owned",
            Mock(side_effect=AssertionError("Marker removal forbidden before complete closure")),
        )
        self.install(release, "Store", Mock(return_value=self.store))
        self.install(release, "deployment_lock", self.lock)
        # The Store stop tests retain real closure/drain behavior; their launch
        # boundary is synthetic and must not inspect the host's root journal.
        self.install(release.launch_native, "Admission", release_fixtures.SyntheticAdmission)
        self.install(release.launch_native, "settle_unentered", Mock(return_value=None))
        self.install(release, "cleanup_signals", nullcontext)
        self.install(
            release,
            "load_attempt",
            Mock(return_value=(self.directory, self.plan_ref, self.plan, self.baseline, self.host)),
        )
        self.install(release.authority, "verify_preserved", Mock())
        self.backup = self.install(
            release.backup,
            "capture",
            Mock(side_effect=AssertionError("Backup must not start before closure")),
        )

    def install(self, owner, name, replacement, **kwargs):
        patched = patch.object(owner, name, replacement, **kwargs)
        patched.start()
        self.addCleanup(patched.stop)
        return replacement

    def advance(self, seconds):
        self.now += max(1.0, seconds)

    def require_owned_marker(self, pin, payload, *, expected_identity=None):
        self.assertEqual(pin, self.marker_parent)
        raw = self.marker.read_bytes()
        self.assertEqual(raw, io.canonical(payload))
        info = self.marker.stat()
        identity = {
            "path": str(self.marker),
            "dev": info.st_dev,
            "ino": info.st_ino,
            "mtime_ns": info.st_mtime_ns,
            "sha256": io.sha(raw),
        }
        if expected_identity is not None:
            self.assertEqual(identity, expected_identity)
        return identity

    def ensure_marker(self, pin, payload, *, authorize):
        self.assertTrue(self.held)
        authorize()
        if not self.marker.exists():
            authorize()
            io.write_new(self.marker, payload)
        return self.require_owned_marker(pin, payload)

    def begin(self, plan):
        self.assertEqual(plan, self.plan)
        self.pending = plan

    @contextmanager
    def lock(self, identity, *, on_failure=None):
        self.assertEqual(identity, self.baseline["deployment_lock_identity"])
        self.assertFalse(self.held)
        self.held = True
        try:
            yield
        except BaseException:
            if on_failure:
                on_failure()
            raise
        finally:
            self.held = False

    def current_identity(self, native, command, deadline):
        self.assertEqual(native, self.native)
        self.assertEqual(command, self.runner.command)
        store_drain._remaining(deadline)
        return copy.deepcopy(self.identity)

    def request(self, method, instance, identity, deadline):
        self.assertEqual(identity, self.identity)
        self.assertGreater(deadline, self.now)
        self.requests.append(method)
        if method == "POST":
            self.assertEqual(instance, self.instance)
            self.assertFalse(self.posted, "Drain POST must not be repeated during recovery")
            self.posted = True
        elif self.posted:
            self.assertIn(instance, (None, self.instance))
            self.polls += 1
        accepting = not self.posted
        drained = self.posted and self.mode == "complete" and self.polls >= 2
        if drained:
            self.drained_observed = True
        pending = {"read": 0, "stateful": int(accepting), "transport": int(not drained)}
        self.observations.append(pending)
        snapshot = {
            "format": "store_worker_lifecycle_v1",
            "instance_id": self.instance,
            "pid": self.identity["pid"],
            "accepting": accepting,
            "status": "accepting" if accepting else "drained" if drained else "draining",
            "drained": drained,
            "pending": pending,
        }
        if self.mode == "contradictory" and self.posted:
            snapshot.update(drained=True, status="drained")
        return (202 if method == "POST" else 200), io.canonical(snapshot)

    def receipts(self):
        return [
            io.document(io.read_protected(path))
            for path in self.directory.glob("store-drain-*.json")
        ]

    def close(self):
        return closure.close_writers(
            self.native,
            self.directory,
            deadline=self.now + 5,
            idle_observer=self.runner.idle_observer,
        )

    def assert_no_stop_or_activation(self):
        self.assertEqual(self.commands, [])
        self.assertFalse(self.runner.stopped)
        self.backup.assert_not_called()
        self.host.replace_crm.assert_not_called()
        self.host.start_native.assert_not_called()
        self.host.restore_duty.assert_not_called()
        self.store.commit.assert_not_called()
        self.store.finish.assert_not_called()

    def test_close_persists_exact_store_proof_before_both_stop_batches(self):
        self.assertEqual(self.close(), self.owned)
        self.assertEqual(self.requests, ["GET", "POST", "GET", "GET"])
        self.assertIn({"read": 0, "stateful": 0, "transport": 1}, self.observations)
        self.assertEqual(self.commands[0], ["/usr/bin/systemctl", "stop", *closure.UNITS])
        self.assertEqual(self.commands[1][-2:], ["stop", "autostopcrm"])
        (receipt,) = self.receipts()
        self.assertEqual(receipt["attempt_id"], self.attempt)
        self.assertEqual(receipt["baseline_ref"], self.plan["baseline_ref"])
        self.assertEqual(receipt["native_baseline_sha256"], self.owned["baseline_sha256"])
        self.assertEqual(receipt["process_scope_ref"], self.owned["process_scope_ref"])
        self.assertEqual(receipt["result"]["identity"], self.identity)
        self.assertEqual(receipt["result"]["instance_id"], self.instance)
        self.assertEqual(
            receipt["result"]["pending"], dict.fromkeys(("read", "stateful", "transport"), 0)
        )
        self.assertTrue(receipt["result"]["store_workers_drained"])
        self.assertEqual(
            closure.observe_closed(self.owned, force_full=True)["ingress_drain_ref"],
            self.runner.load_drain(self.owned)[1],
        )

    def test_timeout_and_retrying_failure_cleanup_never_stop_pending_transport(self):
        self.mode = "pending"
        with self.assertRaisesRegex(io.Refusal, "store_drain_deadline"):
            self.close()
        failed = closure.fail_closed(self.owned)
        self.assertIn("writer_stop_failed", failed["errors"])
        self.assert_no_stop_or_activation()
        self.assertEqual(self.requests.count("POST"), 1)
        self.assertTrue(self.receipts())
        self.assertTrue(all(row["result"]["stop_permitted"] is False for row in self.receipts()))
        with self.assertRaises(io.Refusal):
            closure.observe_closed(self.owned, force_full=True)
        with self.assertRaises(io.Refusal):
            closure.reopen_native(self.owned)

    def test_false_drained_body_never_authorizes_normal_or_failure_stop(self):
        self.mode = "contradictory"
        with self.assertRaisesRegex(io.Refusal, "store_status_inconsistent"):
            self.close()
        self.assert_no_stop_or_activation()
        self.assertTrue(all(row["result"]["stop_permitted"] is False for row in self.receipts()))

    def test_apply_timeout_then_cold_settle_retains_pending_and_marker(self):
        self.mode = "pending"
        # apply owns preparation; retain its real implementation rather than
        # injecting a ready-made ownership object or replacing failure cleanup.
        self.runner.saved.clear()
        self.runner.scopes.clear()
        with self.assertRaisesRegex(io.Refusal, "store_drain_deadline"):
            release.apply(self.attempt)
        self.assert_no_stop_or_activation()
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.pending, self.plan)
        self.assertEqual(release.settle(self.attempt), {"ok": False, "failure_closed": False})
        self.assert_no_stop_or_activation()
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.pending, self.plan)
        self.assertEqual(self.requests.count("POST"), 1)
        failure = io.document(io.read_protected(self.directory / "failure.json"))
        self.assertIn("writer_stop_failed", failure["closure"]["errors"])
        progress = io.document(io.read_protected(self.directory / "progress.json"))
        self.assertEqual(progress["phase"], "failure-closure-incomplete")

    def test_changed_protected_baseline_refuses_gate_before_http_or_commands(self):
        baseline = copy.deepcopy(self.baseline)
        baseline["native"]["manager_revision"] = "f" * 40
        (self.directory / "baseline.json").write_bytes(io.canonical(baseline))
        with self.assertRaisesRegex(io.Refusal, "reference_hash_mismatch"):
            self.runner.stop(self.now + 5, ownership=self.owned)
        self.assertEqual(self.requests, [])
        self.assert_no_stop_or_activation()

    def test_receipt_write_failure_prevents_both_stop_batches(self):
        original_write = io.write_new

        def write(path, value):
            if Path(path).name.startswith("store-drain-"):
                raise OSError("synthetic receipt durability failure")
            return original_write(path, value)

        with patch.object(io, "write_new", side_effect=write), self.assertRaises(OSError):
            self.runner.stop(self.now + 5, ownership=self.owned)
        self.assertTrue(self.drained_observed)
        self.assert_no_stop_or_activation()

    def test_process_change_during_receipt_write_cannot_authorize_stop(self):
        original_write = io.write_new

        def write(path, value):
            reference = original_write(path, value)
            if Path(path).name.startswith("store-drain-") and value["result"]["stop_permitted"]:
                self.identity["start_ticks"] = "124"
            return reference

        with (
            patch.object(io, "write_new", side_effect=write),
            self.assertRaisesRegex(io.Refusal, "store_process_incarnation_changed"),
        ):
            self.runner.stop(self.now + 5, ownership=self.owned)
        self.assert_no_stop_or_activation()
        self.assertEqual(len(self.receipts()), 2)
        self.assertEqual(
            {row["result"]["stop_permitted"] for row in self.receipts()}, {False, True}
        )

    def test_new_process_requires_its_own_handshake_despite_prior_success_receipt(self):
        self.runner.stop(self.now + 5, ownership=self.owned)
        first_receipt = self.receipts()[0]
        self.identity.update(pid=43, start_ticks="124", invocation_id="f" * 32)
        self.instance = "c" * 32
        self.commands.clear()
        self.requests.clear()
        self.posted = self.drained_observed = False
        self.polls = 0
        self.runner.stop(self.now + 5, ownership=self.owned)
        self.assertEqual(self.requests, ["GET", "POST", "GET", "GET"])
        self.assertEqual(len(self.commands), 2)
        self.assertEqual(len(self.receipts()), 2)
        self.assertIn(first_receipt, self.receipts())
        current = next(row for row in self.receipts() if row["result"]["identity"]["pid"] == 43)
        self.assertEqual(current["result"]["identity"], self.identity)
        self.assertEqual(current["result"]["instance_id"], self.instance)


if __name__ == "__main__":
    unittest.main()
