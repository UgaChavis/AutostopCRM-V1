"""Admission predicates that must reject plausible but insufficient evidence."""

from __future__ import annotations

import copy
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import j1_forward_io as io
from scripts import j1_forward_release as release


class JsonEvidenceTests(unittest.TestCase):
    def test_duplicate_keys_and_nonfinite_numbers_refused(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e999}'):
            with self.subTest(raw=raw), self.assertRaises(io.Refusal):
                io.document(raw)


class GuardExecutionTests(unittest.TestCase):
    def host(self, *, guard_status=0, other_status=0, entered=200):
        units = {"native" + str(n) + ".service": {"active": True} for n in range(6)}
        guard = (
            "ExecCondition={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -I -S "
            "/usr/local/libexec/autostop-j1-candidate-guard/guard.py check --unit native ; "
            "code=exited ; status=" + str(guard_status) + " }\n"
        )
        other = (
            "ExecStartPre={ path=/usr/bin/true ; code=exited ; status=" + str(other_status) + " }\n"
        )
        raw = (
            "InvocationID="
            + "1" * 32
            + "\nMainPID=123\nActiveEnterTimestampMonotonic="
            + str(entered)
            + "\n"
            + guard
            + other
        ).encode()
        return SimpleNamespace(native={"desired_units": units}, run=lambda *a, **k: raw)

    def test_guard_itself_must_succeed(self):
        with self.assertRaisesRegex(io.Refusal, "original_guard_success"):
            release.guard_starts(self.host(guard_status=1, other_status=0), 100)

    def test_valid_systemd_record_with_trailing_whitespace(self):
        result = release.guard_starts(self.host(), 100)
        self.assertEqual(len(result), 6)

    def test_prior_invocation_cannot_attest_new_start(self):
        with self.assertRaisesRegex(io.Refusal, "fresh_native_invocation"):
            release.guard_starts(self.host(entered=99), 100)


class QualityAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.source = {"revision": "a" * 40, "tree": "b" * 40}
        self.run = {
            "id": 42,
            "head_sha": self.source["revision"],
            "head_branch": "autostopcrm-v1",
            "event": "push",
            "path": ".github/workflows/quality.yml",
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://github.com/UgaChavis/AutostopCRM-V1/actions/runs/42",
        }
        self.jobs = {
            "total_count": 5,
            "jobs": [
                {
                    "name": name,
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": self.source["revision"],
                }
                for name in (
                    "docker-runtime-assets",
                    "static",
                    "unit",
                    "browser-performance",
                    "quality",
                )
            ],
        }

    def call(self):
        data = [copy.deepcopy(self.run), copy.deepcopy(self.jobs)]

        class Response:
            status = 200

            def __init__(self, request):
                self.url = request.full_url
                self.data = data.pop(0)

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self, _limit):
                return io.canonical(self.data)

        opener = SimpleNamespace(open=lambda request, **_: Response(request))
        with patch.object(release.urllib.request, "build_opener", return_value=opener):
            return release.verify_ci(42, self.source)

    def test_exact_merged_quality_success_is_accepted(self):
        self.assertEqual(self.call()["revision"], self.source["revision"])

    def test_success_on_feature_or_different_commit_is_refused(self):
        for key, wrong in (
            ("head_sha", "c" * 40),
            ("head_branch", "codex/feature"),
            ("event", "pull_request"),
        ):
            original = self.run[key]
            self.run[key] = wrong
            with self.subTest(key=key), self.assertRaises(io.Refusal):
                self.call()
            self.run[key] = original

    def test_skipped_or_missing_gate_is_refused(self):
        self.jobs["jobs"][0]["conclusion"] = "skipped"
        with self.assertRaises(io.Refusal):
            self.call()
        self.jobs["jobs"][0]["conclusion"] = "success"
        self.jobs["total_count"] = 4
        with self.assertRaises(io.Refusal):
            self.call()


class MonitorTests(unittest.TestCase):
    def test_background_closure_failure_prevents_later_acceptance(self):
        def refused():
            raise io.Refusal("actor_was_thawed")

        monitor = release.ClosureMonitor(refused)
        monitor.start()
        end = time.monotonic() + 2
        while monitor.error is None and time.monotonic() < end:
            time.sleep(0.01)
        with self.assertRaisesRegex(io.Refusal, "continuous_closure_observation_failed"):
            monitor.stop()


if __name__ == "__main__":
    unittest.main()
