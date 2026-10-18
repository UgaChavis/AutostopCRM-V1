"""Bounded continuous closure monitoring while captured bytes remain provisional.

Full observations are never relabelled as current. A completed full reference
may age past one second only while its single successor scan is in flight.
Separate identity sampling continues through those scans. A new terminal full
scan must start after capture before its evidence can be published.
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
import uuid
from pathlib import Path

from scripts import j1_forward_io as io
from scripts import j1_forward_process_scope as process_scope

SCHEMA = "autostop.j1.crm-forward-capture-monitor.v1"
SAMPLE_SCHEMA = "autostop.j1.crm-forward-closure-sample.v1"
FULL_SCHEMA = "autostop.j1.crm-forward-closure-observation.v2"
LIMITS = {
    "sample_interval_seconds": 0.2,
    "maximum_sample_age_seconds": 1.0,
    "full_refresh_seconds": 0.5,
    "maximum_full_start_gap_seconds": 1.0,
    "maximum_full_scan_seconds": 30.0,
}
JOIN_SECONDS = 35


def number(value):
    return type(value) in (float, int) and math.isfinite(value) and value >= 0


class ClosureMonitor:
    """Only the two observer threads publish observations; fences read state."""

    def __init__(self, full_observer, sample_observer, marker_check, bindings, *, deadline):
        self.full_observer = full_observer
        self.sample_observer = sample_observer
        self.marker_check = marker_check
        self.bindings = dict(bindings)
        self.deadline = deadline
        self.directory = Path(bindings["closure_ref"]["path"]).parent
        scope = io.document(io.referenced(bindings["process_scope_ref"]))
        writers, transports = process_scope.actor_sets(scope)
        self.actors = {
            name: [
                {"pid": row["pid"], "start_ticks": row["start_ticks"], "stopped": stopped}
                for row in rows
            ]
            for name, rows, stopped in (
                ("freeze_actors", writers, True),
                ("transport_actors", transports, False),
            )
        }
        self.condition = threading.Condition()
        self.stop_event = threading.Event()
        self.generation = str(uuid.uuid4())
        self.accepting = False
        self.error = None
        self.started = None
        self.full_records = []
        self.sample_count = 0
        self.first_sample = self.last_sample = None
        self.inflight = None
        self.checkpoint_after = None
        self.checkpoint_sequence = None
        self.capture_finished_after_full_sequence = None
        self.capture_started = self.capture_finished = None
        self.max_sample_gap = self.max_full_gap = 0.0
        self.max_fence_sample_age = self.max_fence_full_age = 0.0
        self.fence_count = 0
        self.last_fence_full_sequence = None
        self.full_thread = threading.Thread(target=self._full_loop, daemon=True)
        self.sample_thread = threading.Thread(target=self._sample_loop, daemon=True)

    def _need(self, condition, code):
        io.require(condition, "closure_monitor_" + code)

    def _fail(self, error):
        with self.condition:
            if self.error is None:
                self.error = error
            self.stop_event.set()
            self.condition.notify_all()

    def _budget(self, now):
        self._need(number(now) and now < self.deadline, "deadline")

    def _state(self, now, *, alive=True):
        self._budget(now)
        self._need(self.accepting and not self.stop_event.is_set(), "not_active")
        if self.error is not None:
            raise io.Refusal("closure_monitor_observation_failed") from self.error
        if alive:
            self._need(self.full_thread.is_alive() and self.sample_thread.is_alive(), "thread_dead")
        self._need(
            self.last_sample is not None and self.full_records, "initial_observation_missing"
        )
        sample_age = now - self.last_sample["started_monotonic"]
        self._need(0 <= sample_age <= LIMITS["maximum_sample_age_seconds"], "sample_overdue")
        age = now - self.full_records[-1]["completed_monotonic"]
        self._need(age >= 0, "clock_reversed")
        if self.inflight is None:
            self._need(age <= LIMITS["maximum_full_start_gap_seconds"], "full_start_overdue")
        else:
            self._need(
                self.inflight["generation"] == self.generation
                and self.inflight["started_monotonic"]
                - self.full_records[-1]["completed_monotonic"]
                <= LIMITS["maximum_full_start_gap_seconds"]
                and now <= self.inflight["deadline"],
                "full_scan_overdue",
            )
        return sample_age, age

    def _full_reference(self, reference):
        self._need(
            type(reference) is dict and set(reference) == {"path", "sha256"}, "full_reference"
        )
        path = Path(reference["path"])
        self._need(
            path.parent == self.directory
            and re.fullmatch(r"closed-observation-[0-9a-f]{32}\.json", path.name),
            "full_reference",
        )
        observed = io.document(io.referenced(reference))
        self._need(
            observed.get("schema") == FULL_SCHEMA
            and observed.get("attempt_id") == self.bindings["attempt_id"]
            and observed.get("boot_id") == self.bindings["boot_id"]
            and observed.get("process_scope_ref") == self.bindings["process_scope_ref"]
            and observed.get("ingress_drain_ref") == self.bindings["ingress_drain_ref"]
            and observed.get("full_scan_this_sample") is True
            and type(observed.get("full_scan_count")) is int
            and observed["full_scan_count"] > 0
            and all(observed.get(name) == rows for name, rows in self.actors.items()),
            "full_binding",
        )
        return dict(reference), observed["full_scan_count"]

    def _perform_full(self):
        with self.condition:
            if not self.accepting:
                return
            now = time.monotonic()
            self._budget(now)
            if self.full_records:
                gap = now - self.full_records[-1]["completed_monotonic"]
                self._need(
                    0 <= gap <= LIMITS["maximum_full_start_gap_seconds"], "full_start_overdue"
                )
                self.max_full_gap = max(self.max_full_gap, gap)
            generation = self.generation
            flight = {
                "generation": generation,
                "started_monotonic": now,
                "deadline": min(self.deadline, now + LIMITS["maximum_full_scan_seconds"]),
            }
            self._need(self.inflight is None, "multiple_full_owners")
            self.inflight = flight
            self.condition.notify_all()
        reference, scan_count = self._full_reference(self.full_observer())
        ended = time.monotonic()
        self._need(ended <= flight["deadline"], "full_scan_overdue")
        with self.condition:
            if not self.accepting or generation != self.generation:
                return
            self._need(self.error is None, "observation_failed")
            self._need(
                all(
                    reference["path"] != row["reference"]["path"]
                    and reference["sha256"] != row["reference"]["sha256"]
                    for row in self.full_records
                )
                and (
                    not self.full_records
                    or scan_count == self.full_records[-1]["full_scan_count"] + 1
                ),
                "full_observation_replayed",
            )
            self.full_records.append(
                {
                    "sequence": len(self.full_records) + 1,
                    "full_scan_count": scan_count,
                    "started_monotonic": now,
                    "completed_monotonic": ended,
                    "reference": reference,
                }
            )
            self.inflight = None
            self.condition.notify_all()

    def _perform_sample(self):
        self.marker_check()
        before = time.monotonic()
        observed = self.sample_observer()
        self.marker_check()
        ended = time.monotonic()
        keys = {
            "schema",
            "attempt_id",
            "boot_id",
            "process_id",
            "closure_sha256",
            "process_scope_ref",
            "scope_sha256",
            "ingress_drain_ref",
            "freeze_actors",
            "transport_actors",
            "sampled_at",
            "sampled_started_monotonic",
            "sampled_monotonic",
        }
        self._need(
            type(observed) is dict
            and set(observed) == keys
            and observed["schema"] == SAMPLE_SCHEMA
            and observed["attempt_id"] == self.bindings["attempt_id"]
            and observed["boot_id"] == self.bindings["boot_id"]
            and observed["process_id"] == os.getpid()
            and observed["closure_sha256"] == self.bindings["closure_ref"]["sha256"]
            and observed["process_scope_ref"] == self.bindings["process_scope_ref"]
            and observed["scope_sha256"] == self.bindings["process_scope_ref"]["sha256"]
            and observed["ingress_drain_ref"] == self.bindings["ingress_drain_ref"]
            and all(observed[name] == rows for name, rows in self.actors.items())
            and all(
                number(observed[key])
                for key in ("sampled_at", "sampled_started_monotonic", "sampled_monotonic")
            )
            and before
            <= observed["sampled_started_monotonic"]
            <= observed["sampled_monotonic"]
            <= ended
            and ended - observed["sampled_started_monotonic"]
            <= LIMITS["maximum_sample_age_seconds"],
            "sample_binding_or_duration",
        )
        reference = io.write_new(
            self.directory / ("closed-sample-" + uuid.uuid4().hex + ".json"), observed
        )
        published = time.monotonic()
        self._need(
            published - observed["sampled_started_monotonic"]
            <= LIMITS["maximum_sample_age_seconds"],
            "sample_overdue",
        )
        with self.condition:
            if not self.accepting:
                return
            published = time.monotonic()
            self._need(
                published - observed["sampled_started_monotonic"]
                <= LIMITS["maximum_sample_age_seconds"],
                "sample_overdue",
            )
            if self.last_sample is not None:
                self._need(
                    published - self.last_sample["started_monotonic"]
                    <= LIMITS["maximum_sample_age_seconds"],
                    "sample_coverage_gap",
                )
                gap = observed["sampled_started_monotonic"] - self.last_sample["started_monotonic"]
                self._need(0 <= gap <= LIMITS["maximum_sample_age_seconds"], "sample_gap")
                self.max_sample_gap = max(self.max_sample_gap, gap)
            sample = {
                "started_monotonic": observed["sampled_started_monotonic"],
                "completed_monotonic": observed["sampled_monotonic"],
                "reference": reference,
            }
            self.sample_count += 1
            if self.first_sample is None:
                self.first_sample = sample
            self.last_sample = sample
            self.condition.notify_all()

    def _full_loop(self):
        try:
            while not self.stop_event.is_set():
                with self.condition:
                    due = (
                        self.full_records[-1]["completed_monotonic"]
                        + LIMITS["full_refresh_seconds"]
                    )
                    if (
                        self.checkpoint_sequence is not None
                        and self.full_records[-1]["sequence"] < self.checkpoint_sequence
                    ):
                        due = time.monotonic()
                    self.condition.wait(timeout=max(0, due - time.monotonic()))
                    if self.stop_event.is_set():
                        return
                    if time.monotonic() < due:
                        continue
                self._perform_full()
        except BaseException as error:
            self._fail(error)

    def _sample_loop(self):
        try:
            while not self.stop_event.wait(LIMITS["sample_interval_seconds"]):
                with self.condition:
                    if self.stop_event.is_set() or not self.accepting:
                        return
                    self._state(time.monotonic())
                self._perform_sample()
                with self.condition:
                    if self.stop_event.is_set() or not self.accepting:
                        return
                    self._state(time.monotonic())
        except BaseException as error:
            self._fail(error)

    def start(self):
        with self.condition:
            self._need(self.started is None, "replay")
            self.started = time.monotonic()
            self.accepting = True
        try:
            self._perform_full()
            self._perform_sample()
            self.full_thread.start()
            self.sample_thread.start()
        except BaseException as error:
            self._fail(error)
            raise

    def fence(self):
        try:
            with self.condition:
                self._state(time.monotonic())
            self.marker_check()
            with self.condition:
                sample_age, full_age = self._state(time.monotonic())
                self.max_fence_sample_age = max(self.max_fence_sample_age, sample_age)
                self.max_fence_full_age = max(self.max_fence_full_age, full_age)
                self.fence_count += 1
                self.last_fence_full_sequence = self.full_records[-1]["sequence"]
                return {
                    "attempt_id": self.bindings["attempt_id"],
                    **self.full_records[-1]["reference"],
                }
        except BaseException as error:
            self._fail(error)
            raise

    def begin_capture(self):
        with self.condition:
            self._state(time.monotonic())
            self._need(self.capture_started is None, "capture_replay")
            self.capture_started = time.monotonic()

    def finish_capture(self):
        with self.condition:
            self._state(time.monotonic())
            self._need(
                self.capture_started is not None and self.capture_finished is None,
                "capture_boundary",
            )
            self.capture_finished = time.monotonic()
            self.checkpoint_after = self.capture_finished
            # Count a scan already in flight even when the clock gives it the
            # exact same timestamp as this boundary. It cannot be the newly
            # requested terminal scan merely because the clock has coarse ticks.
            self.capture_finished_after_full_sequence = len(self.full_records) + int(
                self.inflight is not None
            )
            self.checkpoint_sequence = self.capture_finished_after_full_sequence + 1
            self.condition.notify_all()
            while self.full_records[-1]["sequence"] < self.checkpoint_sequence:
                self._state(time.monotonic())
                self.condition.wait(timeout=0.05)
            self._state(time.monotonic())
            return {
                "schema": SCHEMA,
                "bindings": dict(self.bindings),
                "generation": self.generation,
                "process_id": os.getpid(),
                "limits": dict(LIMITS),
                "started_monotonic": self.started,
                "capture_started_monotonic": self.capture_started,
                "capture_finished_monotonic": self.capture_finished,
                "capture_finished_after_full_sequence": self.capture_finished_after_full_sequence,
                "capture_last_fence_full_sequence": self.last_fence_full_sequence,
                "observed_monotonic": time.monotonic(),
                "initial_full": dict(self.full_records[0]),
                "terminal_full": dict(self.full_records[-1]),
                "full_observations": [dict(row) for row in self.full_records],
                "first_sample": dict(self.first_sample),
                "last_sample": dict(self.last_sample),
                "sample_count": self.sample_count,
                "fence_count": self.fence_count,
                "maximum_sample_start_gap_seconds": self.max_sample_gap,
                "maximum_full_start_gap_seconds": self.max_full_gap,
                "maximum_fence_sample_age_seconds": self.max_fence_sample_age,
                "maximum_fence_full_age_seconds": self.max_fence_full_age,
            }

    def cancel(self):
        """Invalidate publications before joining; return honest reader quiescence."""
        with self.condition:
            self.accepting = False
            self.generation = str(uuid.uuid4())
            self.stop_event.set()
            self.condition.notify_all()
        until = time.monotonic() + JOIN_SECONDS
        for thread in (self.full_thread, self.sample_thread):
            if thread.ident is not None:
                thread.join(timeout=max(0, until - time.monotonic()))
        return not self.full_thread.is_alive() and not self.sample_thread.is_alive()

    def stop(self):
        try:
            with self.condition:
                self._state(time.monotonic())
        except BaseException as error:
            self._fail(error)
        self._need(self.cancel(), "readers_not_stopped")
        if self.error is not None:
            raise io.Refusal("closure_monitor_observation_failed") from self.error
