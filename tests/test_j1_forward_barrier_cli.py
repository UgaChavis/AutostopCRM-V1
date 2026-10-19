"""Barrier diagnostics stay outside release admission and have no live effects."""

from __future__ import annotations

import copy
import io as stream_io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import j1_forward_io as io
from scripts import j1_forward_release as release
from scripts import j1_forward_worker as worker
from tests import test_j1_forward_worker as worker_fixtures

DIAGNOSTIC_SCHEMA = "autostop.j1.crm-forward-barrier-inspection.v1"


class BarrierInspectionCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.attempt = "33220918-5be9-48c1-9a6e-122045f3a4ac"
        self.directory = self.root / self.attempt
        self.directory.mkdir()
        self.owned_path = self.directory / "closure-owned.json"
        self.ownership = {"attempt_id": self.attempt, "attempt_dir": str(self.directory)}
        self.observation = {
            "schema": DIAGNOSTIC_SCHEMA,
            "attempt_id": self.attempt,
            "barriers_verified": True,
            "writer_closure_confirmed": False,
            "ownership_adopted": False,
            "native_acceptance": False,
            "activation_authorized": False,
        }
        self.owned_path.write_bytes(io.canonical(self.ownership))
        (self.directory / "plan.json").write_bytes(
            io.canonical({"deadline_utc": "2000-01-01T00:00:00Z"})
        )
        (self.directory / "progress.json").write_bytes(b"historical failed attempt\n")
        (self.directory / "pending.json").write_bytes(b"historical pending\n")
        (self.root / "maintenance-marker").write_bytes(b"historical marker\n")
        self.install(release, "ATTEMPTS", self.root)
        self.install(release.io, "protected_directory", lambda path, **_: Path(path))
        self.reader = self.install(release.io, "read_protected", side_effect=self.read_owned)
        self.observer = self.install(
            release.closure, "inspect_barriers", return_value=copy.deepcopy(self.observation)
        )
        self.forbidden = []
        for owner, names in (
            (
                release,
                (
                    "load_attempt",
                    "remaining",
                    "require_marker",
                    "save",
                    "progress",
                    "deployment_lock",
                    "Host",
                    "Store",
                    "launch",
                    "apply",
                    "settle",
                ),
            ),
            (release.io, ("write_new", "atomic_replace")),
            (
                release.authority,
                ("_load_authority", "snapshot_native", "verify_preserved"),
            ),
            (
                release.closure,
                (
                    "prepare_closure",
                    "close_writers",
                    "fail_closed",
                    "reopen_native",
                    "release_auxiliary",
                    "observe_closed",
                    "observe_activation",
                ),
            ),
            (release.subprocess, ("run", "Popen")),
        ):
            for name in names:
                self.forbidden.append(
                    self.install(owner, name, side_effect=AssertionError("forbidden: " + name))
                )

    def install(self, owner, name, value=None, **kwargs):
        replacement = (
            patch.object(owner, name, value, **kwargs)
            if value is not None
            else (patch.object(owner, name, **kwargs))
        )
        result = replacement.start()
        self.addCleanup(replacement.stop)
        return result

    def read_owned(self, path, *_args, **_kwargs):
        self.assertEqual(self.owned_path, Path(path))
        return self.owned_path.read_bytes()

    def snapshot_files(self):
        return {
            path.relative_to(self.root): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def cli(self, *, root=True):
        stdout = SimpleNamespace(buffer=stream_io.BytesIO())
        stderr = SimpleNamespace(buffer=stream_io.BytesIO())
        with (
            patch.object(
                release, "os", SimpleNamespace(name="posix", geteuid=lambda: 0 if root else 9)
            ),
            patch.object(
                release.sys,
                "argv",
                [
                    "j1_forward_release.py",
                    "inspect",
                    "--attempt",
                    self.attempt,
                    "--stage",
                    "barriers",
                ],
            ),
            patch.object(release.sys, "stdout", stdout),
            patch.object(release.sys, "stderr", stderr),
        ):
            code = release.main()
        return code, stdout.buffer.getvalue(), stderr.buffer.getvalue()

    def assert_no_effects(self):
        for method in self.forbidden:
            method.assert_not_called()

    def test_barriers_bypass_expired_plan_marker_and_release_effects(self):
        before = self.snapshot_files()
        result = release.inspect(self.attempt, "barriers")
        self.assertEqual({"ok": True, "stage": "barriers", "observation": self.observation}, result)
        self.observer.assert_called_once_with(self.ownership)
        self.reader.assert_called_once_with(self.owned_path)
        self.assertEqual(before, self.snapshot_files())
        self.assert_no_effects()

    def test_cli_barriers_emits_only_diagnostic_json(self):
        before = self.snapshot_files()
        code, stdout, stderr = self.cli()
        self.assertEqual(0, code)
        self.assertEqual(b"", stderr)
        self.assertEqual(
            {"ok": True, "stage": "barriers", "observation": self.observation},
            io.document(stdout),
        )
        self.assertEqual(before, self.snapshot_files())
        self.assert_no_effects()

    def test_historical_record_must_match_exact_attempt_and_directory(self):
        for change in (
            {"attempt_id": "6adbb798-d86a-4500-9d85-4bbb2f30d2e0"},
            {"attempt_dir": str(self.root / "another-attempt")},
            {"attempt_id": None},
            {"attempt_dir": None},
        ):
            with self.subTest(change=change):
                self.owned_path.write_bytes(io.canonical({**self.ownership, **change}))
                with self.assertRaisesRegex(io.Refusal, "barrier_attempt_binding"):
                    release.inspect(self.attempt, "barriers")
                self.observer.assert_not_called()
        self.assert_no_effects()

    def test_wrong_json_shape_is_bounded_and_never_reaches_observer(self):
        secret = "private-fixture-do-not-print"
        for value in (None, [], [secret], secret, 7, True):
            with self.subTest(value=value):
                self.owned_path.write_bytes(io.canonical(value))
                with self.assertRaisesRegex(io.Refusal, "barrier_attempt_binding"):
                    release.inspect(self.attempt, "barriers")
                code, stdout, stderr = self.cli()
                self.assertEqual(2, code)
                self.assertEqual(b"", stdout)
                self.assertEqual(
                    {"ok": False, "error": "barrier_attempt_binding"}, io.document(stderr)
                )
                self.assertNotIn(secret.encode(), stderr)
                self.observer.assert_not_called()
        self.assert_no_effects()

    def test_invalid_json_has_safe_error_without_observation(self):
        self.owned_path.write_bytes(b"private-fixture-invalid-json")
        code, stdout, stderr = self.cli()
        self.assertEqual(2, code)
        self.assertEqual(b"", stdout)
        self.assertEqual({"ok": False, "error": "invalid_json"}, io.document(stderr))
        self.observer.assert_not_called()
        self.assert_no_effects()

    def test_unknown_stage_and_nonroot_cli_refuse_before_reading_record(self):
        with self.assertRaisesRegex(io.Refusal, "inspection_stage_invalid"):
            release.inspect(self.attempt, "unknown")
        code, stdout, stderr = self.cli(root=False)
        self.assertEqual(2, code)
        self.assertEqual(b"", stdout)
        self.assertEqual({"ok": False, "error": "linux_native_root_required"}, io.document(stderr))
        self.reader.assert_not_called()
        self.observer.assert_not_called()
        self.assert_no_effects()

    def test_existing_release_stages_still_use_original_load_attempt(self):
        with patch.object(release, "load_attempt", side_effect=io.Refusal("original_release_gate")):
            for stage in ("closed", "activated"):
                with (
                    self.subTest(stage=stage),
                    self.assertRaisesRegex(io.Refusal, "original_release_gate"),
                ):
                    release.inspect(self.attempt, stage)
        self.reader.assert_not_called()
        self.observer.assert_not_called()
        self.assert_no_effects()

    def test_worker_rejects_diagnostic_response_for_every_stage(self):
        result = release.inspect(self.attempt, "barriers")
        for stage in ("closed", "activated", "barriers"):
            with (
                self.subTest(stage=stage),
                self.assertRaisesRegex(io.Refusal, "worker_inspect_response_invalid"),
            ):
                worker.validate_response(result, attempt=self.attempt, stage=stage)
        self.assert_no_effects()

    def test_worker_rejects_diagnostic_schema_even_with_valid_release_bindings(self):
        fixture = worker_fixtures.WorkerTests(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for stage in ("closed", "activated"):
            with self.subTest(stage=stage):
                response = fixture.response(stage)
                self.assertIs(response, fixture.validate(response))
                response["inspection"]["schema"] = DIAGNOSTIC_SCHEMA
                fixture.rebind(response)
                with self.assertRaisesRegex(io.Refusal, "worker_inspection_binding"):
                    fixture.validate(response)
        self.assert_no_effects()


if __name__ == "__main__":
    unittest.main()
