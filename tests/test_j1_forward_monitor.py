"""Adversarial closure-monitor tests with private synthetic evidence only."""

from __future__ import annotations

import copy
import os
import stat
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from scripts import j1_forward_io as io
from scripts import j1_forward_monitor as m


class ControlledThread:
    """Deterministic join/liveness boundary; never launches native work."""

    ident = 1

    def __init__(self, *, joins=True, on_join=None):
        self.alive, self.joins, self.on_join = True, joins, on_join

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        if self.on_join:
            self.on_join()
        if self.joins:
            self.alive = False


class ForwardMonitorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="j1-monitor-synthetic-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.monitors, self.releases, self.threads = [], [], []
        self.install(io, "protected_directory", self.physical_directory)
        self.install(io, "read_protected", self.read_physical)
        self.install(io, "fsync_directory", lambda _path: None)
        self.install(
            m.process_scope,
            "actor_sets",
            lambda _scope: (
                [{"pid": 10001, "start_ticks": 11}],
                [{"pid": 10002, "start_ticks": 12}],
            ),
        )
        self.bindings = {
            "attempt_id": "00000000-0000-4000-8000-000000000001",
            "boot_id": "00000000-0000-4000-8000-000000000002",
            "closure_ref": io.write_new(self.root / "closure.json", {"synthetic": "closure"}),
            "process_scope_ref": io.write_new(self.root / "scope.json", {"synthetic": "scope"}),
            "ingress_drain_ref": io.write_new(self.root / "drain.json", {"synthetic": "drain"}),
        }
        self.addCleanup(self.close_monitors)

    def install(self, owner, name, value):
        mocked = patch.object(owner, name, value)
        mocked.start()
        self.addCleanup(mocked.stop)

    def physical_directory(self, path, **_kwargs):
        path = Path(path)
        io.require(path.is_absolute() and path.is_relative_to(self.root), "synthetic_directory")
        for entry in [path, *path.parents]:
            info = entry.lstat()
            io.require(
                stat.S_ISDIR(info.st_mode)
                and not entry.is_symlink()
                and not getattr(info, "st_file_attributes", 0) & 0x400,
                "synthetic_link",
            )
        return path

    def read_physical(self, path, limit=2 * 1024**2):
        path = Path(path)
        self.physical_directory(path.parent)
        before = path.lstat()
        io.require(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and not path.is_symlink()
            and before.st_size <= limit,
            "synthetic_file",
        )
        raw = path.read_bytes()
        after = path.lstat()
        io.require(
            len(raw) == before.st_size
            and (before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_ino, after.st_size, after.st_mtime_ns),
            "synthetic_file_changed",
        )
        return raw

    def close_monitors(self):
        for event in self.releases:
            event.set()
        threads = set(self.threads)
        for monitor in self.monitors:
            threads.update(
                thread
                for thread in (monitor.full_thread, monitor.sample_thread)
                if isinstance(thread, threading.Thread)
            )
            with patch.object(m, "JOIN_SECONDS", 2):
                monitor.cancel()
        for thread in threads:
            if thread.ident is not None:
                thread.join(2)
        self.assertFalse(
            [thread.name for thread in threads if thread.is_alive()], "synthetic thread survived"
        )

    def thread(self, **kwargs):
        thread = threading.Thread(**kwargs)
        self.threads.append(thread)
        return thread

    def release_event(self):
        event = threading.Event()
        self.releases.append(event)
        return event

    def full(self, monitor, count, **changes):
        document = {
            "schema": m.FULL_SCHEMA,
            "attempt_id": self.bindings["attempt_id"],
            "boot_id": self.bindings["boot_id"],
            "process_scope_ref": self.bindings["process_scope_ref"],
            "ingress_drain_ref": self.bindings["ingress_drain_ref"],
            "full_scan_this_sample": True,
            "full_scan_count": count,
            **copy.deepcopy(monitor.actors),
            **changes,
        }
        return io.write_new(
            self.root / ("closed-observation-" + uuid.uuid4().hex + ".json"), document
        )

    def sample(self, monitor, *, started=None, ended=None, **changes):
        started = m.time.monotonic() if started is None else started
        ended = m.time.monotonic() if ended is None else ended
        return {
            "schema": m.SAMPLE_SCHEMA,
            "attempt_id": self.bindings["attempt_id"],
            "boot_id": self.bindings["boot_id"],
            "process_id": os.getpid(),
            "closure_sha256": self.bindings["closure_ref"]["sha256"],
            "process_scope_ref": self.bindings["process_scope_ref"],
            "scope_sha256": self.bindings["process_scope_ref"]["sha256"],
            "ingress_drain_ref": self.bindings["ingress_drain_ref"],
            **copy.deepcopy(monitor.actors),
            "sampled_at": 1.0,
            "sampled_started_monotonic": started,
            "sampled_monotonic": ended,
            **changes,
        }

    def make(self, *, full_hook=None, sample_hook=None, deadline=None):
        counts = {"full": 0, "sample": 0, "marker": 0}
        holder = []

        def full_observer():
            counts["full"] += 1
            return (
                full_hook(holder[0], counts["full"])
                if full_hook
                else self.full(holder[0], counts["full"])
            )

        def sample_observer():
            counts["sample"] += 1
            return (
                sample_hook(holder[0], counts["sample"]) if sample_hook else self.sample(holder[0])
            )

        def marker():
            counts["marker"] += 1

        monitor = m.ClosureMonitor(
            full_observer,
            sample_observer,
            marker,
            self.bindings,
            deadline=deadline or m.time.monotonic() + 10,
        )
        holder.append(monitor)
        self.monitors.append(monitor)
        return monitor, counts

    @contextmanager
    def manual(self):
        with patch.object(m.time, "monotonic", return_value=100.0) as clock:
            monitor, counts = self.make(deadline=200.0)
            monitor.accepting, monitor.started = True, 100.0
            monitor.full_thread, monitor.sample_thread = ControlledThread(), ControlledThread()
            monitor._perform_full()
            monitor._perform_sample()
            yield monitor, clock, counts

    def assert_sticky(self, monitor):
        first = monitor.error
        self.assertIsNotNone(first)
        monitor._fail(RuntimeError("later failure"))
        self.assertIs(monitor.error, first)
        with self.assertRaises(io.Refusal):
            monitor.fence()

    @staticmethod
    def full_runner(monitor, errors):
        try:
            monitor._perform_full()
        except BaseException as error:
            errors.append(error)

    def test_real_two_threads_save_actual_refs_and_terminal_checkpoint(self):
        sampled = threading.Event()

        def sample(monitor, count):
            if count >= 2:
                sampled.set()
            return self.sample(monitor)

        with patch.dict(m.LIMITS, sample_interval_seconds=0.01, full_refresh_seconds=0.02):
            monitor, counts = self.make(sample_hook=sample)
            monitor.start()
            monitor.begin_capture()
            self.assertTrue(sampled.wait(2))
            witness = monitor.fence()
            receipt = monitor.finish_capture()
            monitor.stop()
        self.assertGreaterEqual(counts["sample"], 2)
        self.assertGreaterEqual(counts["full"], 2)
        self.assertGreaterEqual(
            receipt["terminal_full"]["started_monotonic"], receipt["capture_finished_monotonic"]
        )
        self.assertEqual(witness["attempt_id"], self.bindings["attempt_id"])
        references = [row["reference"] for row in receipt["full_observations"]]
        self.assertEqual(len({ref["sha256"] for ref in references}), len(references))
        for row in receipt["full_observations"]:
            saved = io.document(io.referenced(row["reference"]))
            self.assertTrue(saved["full_scan_this_sample"])
            self.assertEqual(saved["full_scan_count"], row["full_scan_count"])
        saved = io.document(io.referenced(receipt["last_sample"]["reference"]))
        self.assertEqual(
            saved["sampled_started_monotonic"], receipt["last_sample"]["started_monotonic"]
        )
        self.assertFalse(monitor.full_thread.is_alive())
        self.assertFalse(monitor.sample_thread.is_alive())

    def test_sample_age_uses_start_not_recent_completion(self):
        with self.manual() as (monitor, clock, _):
            monitor.last_sample["completed_monotonic"] = 100.99
            clock.return_value = 101.01
            with self.assertRaisesRegex(io.Refusal, "sample_overdue"):
                monitor.fence()
            self.assert_sticky(monitor)

    def test_background_observer_failure_is_sticky_and_stop_refuses_acceptance(self):
        error = RuntimeError("synthetic background closure failure")

        def full(monitor, count):
            if count == 2:
                raise error
            return self.full(monitor, count)

        with patch.dict(m.LIMITS, full_refresh_seconds=0.01):
            monitor, _ = self.make(full_hook=full)
            monitor.start()
            with monitor.condition:
                self.assertTrue(
                    monitor.condition.wait_for(lambda: monitor.error is not None, timeout=2)
                )
            self.assertIs(monitor.error, error)
            self.assert_sticky(monitor)
            with self.assertRaisesRegex(io.Refusal, "observation_failed"):
                monitor.stop()
            self.assertFalse(monitor.full_thread.is_alive())
            self.assertFalse(monitor.sample_thread.is_alive())

    def test_sample_coverage_gap_cannot_be_healed_by_new_long_sample(self):
        with self.manual() as (monitor, clock, _):
            clock.return_value = 100.24

            def delayed():
                clock.return_value = 101.19
                return self.sample(monitor, started=100.24, ended=101.19)

            monitor.sample_observer = delayed
            with patch.object(monitor.stop_event, "wait", return_value=False):
                monitor._sample_loop()
            self.assertIn("sample_coverage_gap", str(monitor.error))
            self.assertEqual(monitor.sample_count, 1)
            self.assert_sticky(monitor)

    def test_missed_sample_is_sticky_even_after_fresh_state_arrives(self):
        with self.manual() as (monitor, clock, _):
            clock.return_value = 101.1
            with patch.object(monitor.stop_event, "wait", return_value=False):
                monitor._sample_loop()
            monitor.last_sample["started_monotonic"] = 101.1
            monitor.full_records[-1]["completed_monotonic"] = 101.1
            self.assert_sticky(monitor)

    def test_missed_full_start_gap_is_sticky_without_observer_retry(self):
        with self.manual() as (monitor, clock, counts):
            clock.return_value = 101.01
            monitor.last_sample["started_monotonic"] = 101.01
            monitor._full_loop()
            self.assertIn("full_start_overdue", str(monitor.error))
            self.assertEqual(counts["full"], 1)
            self.assert_sticky(monitor)

    def test_old_full_reference_without_inflight_successor_is_rejected(self):
        with self.manual() as (monitor, clock, _):
            clock.return_value = 101.01
            monitor.last_sample["started_monotonic"] = 101.01
            with self.assertRaisesRegex(io.Refusal, "full_start_overdue"):
                monitor.fence()
            self.assert_sticky(monitor)

    def test_actual_inflight_full_allows_old_reference_with_fresh_samples(self):
        entered, release = threading.Event(), self.release_event()
        errors = []
        with self.manual() as (monitor, clock, _):
            old = dict(monitor.full_records[-1]["reference"])

            def blocked():
                entered.set()
                self.assertTrue(release.wait(2))
                return self.full(monitor, 2)

            monitor.full_observer = blocked
            clock.return_value = 100.4
            worker = self.thread(target=self.full_runner, args=(monitor, errors))
            monitor.full_thread = worker
            worker.start()
            self.assertTrue(entered.wait(2))
            for now in (100.8, 101.2, 101.6, 102.0):
                clock.return_value = now
                monitor._perform_sample()
            self.assertEqual(monitor.fence(), {"attempt_id": self.bindings["attempt_id"], **old})
            self.assertGreater(monitor.max_fence_full_age, 1)
            release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(monitor.full_records), 2)

    def test_inflight_deadline_cannot_be_extended_by_a_fresh_sample(self):
        with self.manual() as (monitor, clock, _):
            monitor.inflight = {
                "generation": monitor.generation,
                "started_monotonic": 100.5,
                "deadline": 130.5,
            }
            clock.return_value = 130.501
            monitor.last_sample["started_monotonic"] = 130.501
            with self.assertRaisesRegex(io.Refusal, "full_scan_overdue"):
                monitor.fence()
            self.assert_sticky(monitor)

    def test_full_observation_over_30_seconds_is_never_published(self):
        with self.manual() as (monitor, clock, _):
            clock.return_value = 100.5

            def slow():
                clock.return_value = 130.501
                return self.full(monitor, 2)

            monitor.full_observer = slow
            monitor._full_loop()
            self.assertIn("full_scan_overdue", str(monitor.error))
            self.assertEqual(len(monitor.full_records), 1)
            self.assert_sticky(monitor)

    def test_terminal_scan_must_start_after_capture_not_only_finish_after(self):
        old_started, terminal_started = threading.Event(), threading.Event()
        release_old, release_terminal = self.release_event(), self.release_event()
        result, errors, finished = [], [], threading.Event()

        def full(monitor, count):
            if count == 2:
                old_started.set()
                self.assertTrue(release_old.wait(3))
            if count == 3:
                terminal_started.set()
                self.assertTrue(release_terminal.wait(3))
            return self.full(monitor, count)

        with (
            patch.dict(m.LIMITS, sample_interval_seconds=0.01, full_refresh_seconds=0.02),
            patch.object(m.time, "monotonic", time.perf_counter),
        ):
            monitor, _ = self.make(full_hook=full)
            monitor.start()
            monitor.begin_capture()
            self.assertTrue(old_started.wait(2))

            def finish():
                try:
                    result.append(monitor.finish_capture())
                except BaseException as error:
                    errors.append(error)
                finally:
                    finished.set()

            waiter = self.thread(target=finish)
            waiter.start()
            with monitor.condition:
                self.assertTrue(
                    monitor.condition.wait_for(
                        lambda: monitor.capture_finished is not None, timeout=2
                    )
                )
            self.assertFalse(finished.is_set())
            release_old.set()
            self.assertTrue(terminal_started.wait(2))
            self.assertFalse(finished.is_set(), [str(error) for error in errors])
            release_terminal.set()
            waiter.join(2)
            self.assertFalse(waiter.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(result[0]["terminal_full"]["full_scan_count"], 3)
            self.assertGreaterEqual(
                result[0]["terminal_full"]["started_monotonic"],
                result[0]["capture_finished_monotonic"],
            )
            monitor.stop()

    def test_equal_clock_tick_still_requires_a_new_terminal_scan(self):
        with self.manual() as (monitor, _, counts):
            monitor.begin_capture()
            with patch.object(
                monitor.condition, "wait", side_effect=lambda **_kwargs: monitor._perform_full()
            ):
                receipt = monitor.finish_capture()
            self.assertEqual(counts["full"], 2)
            self.assertEqual(receipt["initial_full"]["full_scan_count"], 1)
            self.assertEqual(receipt["terminal_full"]["full_scan_count"], 2)

    def test_equal_clock_inflight_scan_is_excluded_from_terminal_and_last_fence(self):
        old_started, terminal_started = threading.Event(), threading.Event()
        release_old, release_terminal = self.release_event(), self.release_event()
        errors, result, finished = [], [], threading.Event()
        with self.manual() as (monitor, _, counts):
            monitor.begin_capture()
            witness = monitor.fence()

            def full():
                counts["full"] += 1
                if counts["full"] == 2:
                    old_started.set()
                    self.assertTrue(release_old.wait(2))
                if counts["full"] == 3:
                    terminal_started.set()
                    self.assertTrue(release_terminal.wait(2))
                return self.full(monitor, counts["full"])

            def run_full():
                try:
                    monitor._perform_full()
                    monitor._full_loop()
                except BaseException as error:
                    errors.append(error)

            def finish():
                try:
                    result.append(monitor.finish_capture())
                except BaseException as error:
                    errors.append(error)
                finally:
                    finished.set()

            monitor.full_observer = full
            monitor.full_thread = self.thread(target=run_full)
            monitor.full_thread.start()
            self.assertTrue(old_started.wait(2))
            waiter = self.thread(target=finish)
            waiter.start()
            with monitor.condition:
                self.assertTrue(
                    monitor.condition.wait_for(
                        lambda: monitor.capture_finished is not None, timeout=2
                    )
                )
            release_old.set()
            self.assertTrue(terminal_started.wait(2))
            self.assertFalse(finished.is_set(), [str(error) for error in errors])
            release_terminal.set()
            waiter.join(2)
            self.assertFalse(waiter.is_alive())
            self.assertTrue(monitor.cancel())
            self.assertEqual(errors, [])
            receipt = result[0]
            self.assertEqual(receipt["capture_finished_after_full_sequence"], 2)
            self.assertEqual(receipt["capture_last_fence_full_sequence"], 1)
            self.assertEqual(receipt["terminal_full"]["sequence"], 3)
            self.assertEqual(witness["sha256"], receipt["initial_full"]["reference"]["sha256"])
            self.assertTrue(
                all(
                    row["started_monotonic"] == receipt["capture_finished_monotonic"]
                    for row in receipt["full_observations"]
                )
            )

    def test_repeated_full_ref_same_hash_and_skipped_counts_are_rejected(self):
        for variant in ("same_reference", "same_hash", "skipped_count", "zero_count"):
            with self.subTest(variant=variant), self.manual() as (monitor, clock, _):
                old = monitor.full_records[-1]["reference"]
                if variant == "same_reference":
                    monitor.full_observer = lambda: old
                else:
                    count = {"same_hash": 1, "skipped_count": 3, "zero_count": 0}[variant]
                    monitor.full_observer = lambda: self.full(monitor, count)
                clock.return_value = 100.5
                with self.assertRaisesRegex(io.Refusal, "full_observation_replayed|full_binding"):
                    monitor._perform_full()
                self.assertEqual(len(monitor.full_records), 1)

    def test_full_reference_hash_and_bindings_are_checked_from_saved_bytes(self):
        with self.manual() as (monitor, _, _):
            ref = self.full(monitor, 2)
            with self.assertRaisesRegex(io.Refusal, "reference_hash_mismatch"):
                monitor._full_reference({**ref, "sha256": "f" * 64})
            with self.assertRaisesRegex(io.Refusal, "full_binding"):
                monitor._full_reference(self.full(monitor, 2, boot_id="other"))
            with self.assertRaisesRegex(io.Refusal, "full_reference"):
                monitor._full_reference(self.bindings["closure_ref"])

    def test_sample_binding_failure_never_replaces_saved_sample(self):
        with self.manual() as (monitor, _, _):
            initial = monitor.last_sample
            monitor.sample_observer = lambda: self.sample(monitor, process_id=os.getpid() + 1)
            with self.assertRaisesRegex(io.Refusal, "sample_binding_or_duration"):
                monitor._perform_sample()
            self.assertIs(monitor.last_sample, initial)

    def test_marker_io_delay_rechecks_freshness_and_latches_failure(self):
        with self.manual() as (monitor, clock, _):
            monitor.marker_check = lambda: setattr(clock, "return_value", 101.01)
            with self.assertRaisesRegex(io.Refusal, "sample_overdue"):
                monitor.fence()
            self.assert_sticky(monitor)

    def test_sample_publication_io_delay_cannot_renew_stale_observation(self):
        with self.manual() as (monitor, clock, _):
            original = io.write_new

            def delayed(path, value):
                ref = original(path, value)
                clock.return_value = 101.01
                return ref

            with (
                patch.object(io, "write_new", side_effect=delayed),
                self.assertRaisesRegex(io.Refusal, "sample_overdue"),
            ):
                monitor._perform_sample()
            self.assertEqual(monitor.sample_count, 1)

    def test_condition_wait_after_publication_rechecks_freshness(self):
        with self.manual() as (monitor, clock, _):
            original = monitor.condition

            class DelayedCondition:
                def __enter__(self):
                    original.__enter__()
                    clock.return_value = 101.01

                def __exit__(self, *args):
                    return original.__exit__(*args)

                def notify_all(self):
                    original.notify_all()

            monitor.condition = DelayedCondition()
            try:
                with self.assertRaisesRegex(io.Refusal, "sample_overdue"):
                    monitor._perform_sample()
                self.assertEqual(monitor.sample_count, 1)
            finally:
                monitor.condition = original

    def test_late_observer_error_during_join_is_not_reported_as_clean_stop(self):
        with self.manual() as (monitor, _, _):
            late = RuntimeError("synthetic late observer failure")
            monitor.full_thread.on_join = lambda: monitor._fail(late)
            with self.assertRaisesRegex(io.Refusal, "observation_failed"):
                monitor.stop()
            self.assertIs(monitor.error, late)
            self.assertFalse(monitor.full_thread.is_alive())
            self.assertFalse(monitor.sample_thread.is_alive())

    def test_stop_attempts_join_even_when_pre_stop_state_is_overdue(self):
        with self.manual() as (monitor, clock, _):
            clock.return_value = 101.01
            with self.assertRaisesRegex(io.Refusal, "observation_failed"):
                monitor.stop()
            self.assertFalse(monitor.full_thread.is_alive())
            self.assertFalse(monitor.sample_thread.is_alive())

    def test_cancel_invalidates_generation_and_honestly_reports_nonjoin(self):
        with self.manual() as (monitor, _, _):
            generation = monitor.generation
            monitor.full_thread = ControlledThread(joins=False)
            self.assertFalse(monitor.cancel())
            self.assertNotEqual(generation, monitor.generation)
            self.assertFalse(monitor.accepting)
            self.assertTrue(monitor.stop_event.is_set())
            with self.assertRaisesRegex(io.Refusal, "not_active"):
                monitor.fence()

    def test_stop_rejects_nonjoined_reader(self):
        with self.manual() as (monitor, _, _):
            monitor.sample_thread = ControlledThread(joins=False)
            with self.assertRaisesRegex(io.Refusal, "readers_not_stopped"):
                monitor.stop()

    def test_late_full_result_after_cancel_is_not_published(self):
        entered, release = threading.Event(), self.release_event()
        errors = []
        with self.manual() as (monitor, clock, _):

            def blocked():
                entered.set()
                self.assertTrue(release.wait(2))
                return self.full(monitor, 2)

            monitor.full_observer = blocked
            clock.return_value = 100.5
            worker = self.thread(target=self.full_runner, args=(monitor, errors))
            monitor.full_thread = worker
            worker.start()
            self.assertTrue(entered.wait(2))
            with patch.object(m, "JOIN_SECONDS", 0.01):
                self.assertFalse(monitor.cancel())
            release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(monitor.full_records), 1)
            self.assertIsNone(monitor.error)
            self.assertTrue(monitor.cancel())

    def test_intentional_cancel_during_active_sample_does_not_invent_error(self):
        entered, release = threading.Event(), self.release_event()

        def sample(monitor, count):
            if count == 2:
                entered.set()
                self.assertTrue(release.wait(2))
            return self.sample(monitor)

        with patch.dict(m.LIMITS, sample_interval_seconds=0.01):
            monitor, _ = self.make(sample_hook=sample)
            monitor.start()
            self.assertTrue(entered.wait(2))
            with patch.object(m, "JOIN_SECONDS", 0.01):
                self.assertFalse(monitor.cancel())
            release.set()
            self.assertTrue(monitor.cancel())
            self.assertIsNone(monitor.error)
            self.assertEqual(monitor.sample_count, 1)


if __name__ == "__main__":
    unittest.main()
