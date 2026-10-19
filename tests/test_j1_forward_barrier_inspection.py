"""Installed-barrier diagnostics with synthetic files; no real service operations."""

from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import j1_forward_closure as closure
from tests import test_j1_forward_closure as fixtures


def command(path, argv=None):
    return (
        f"{{ path={path} ; argv[]={argv or path} ; ignore_errors=no ; "
        "start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }"
    )


@unittest.skipUnless(os.name == "posix", "Linux installed-barrier files")
class BarrierInspectionTests(unittest.TestCase):
    def install(self, target, name, value):
        replacement = patch.object(target, name, value)
        replacement.start()
        self.addCleanup(replacement.stop)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.runner = fixtures.FakeRunner()
        self.install(closure, "RUNNER", self.runner)
        self.install(closure, "SYSTEMD", self.root / "systemd")
        self.install(closure.io, "ROOT_UID", os.getuid())
        # Temporary test directories have writable ancestors; file checks stay real.
        self.install(closure.io, "protected_directory", lambda path, **_: Path(path))
        self.install(closure.os, "geteuid", lambda: 0)
        self.install(closure.socket, "gethostname", lambda: closure.authority.HOST)
        directory = self.root / fixtures.ATTEMPT
        directory.mkdir()
        baseline = {
            "warm_actors": [fixtures.actor(pid) for pid in sorted(closure.ACCEPTED_PIDS)],
            "sha256": "a" * 64,
        }
        self.owned = closure.prepare_closure(baseline, directory)
        scope = self.runner.scopes[self.owned["process_scope_ref"]["path"]]
        (directory / "process-scope.json").write_bytes(closure.io.canonical(scope))
        (directory / "closure-owned.json").write_bytes(closure.io.canonical(self.owned))
        for entry in self.owned["dropins"]:
            path = Path(entry["path"])
            path.parent.mkdir(parents=True)
            path.write_bytes(closure.DENY_BYTES)
            self.runner.barriers[str(path)] = closure.DENY_BYTES
        self.barrier = Path(self.owned["dropins"][0]["path"])
        guard = self.root / "guard.py"
        guard.write_bytes(b"raise AssertionError('must never execute')\n")
        policy = self.root / "policy.json"
        policy.write_bytes(b"{}\n")
        self.policy_hash = closure.io.sha(policy.read_bytes())
        capsule = self.root / "capsule.json"
        capsule.write_bytes(closure.io.canonical({"policy": closure.io.reference(policy)}))
        self.install(closure.authority, "GUARD", guard)
        self.install(closure.authority, "CAPSULE", capsule)
        self.install(
            closure.authority,
            "PINS",
            {path: closure.io.sha(path.read_bytes()) for path in (guard, capsule)},
        )
        self.units = {}
        for index, unit in enumerate(closure.UNITS):
            active = unit in closure.authority.NATIVE_UNITS
            argv = (
                f"/usr/bin/python3 -I -S {guard} check --policy-sha256 "
                f"{self.policy_hash} --unit {unit}"
            )
            self.units[unit] = {
                "LoadState": "loaded",
                "NeedDaemonReload": "no",
                "ExecCondition": command("/usr/bin/python3", argv)
                + " "
                + command("/usr/bin/false"),
                "ActiveState": "active" if active else "inactive",
                "SubState": "running" if active else "dead",
                "MainPID": str(100 + index) if active else "0",
                "Job": "",
                **dict.fromkeys(closure.HOOKS, ""),
            }
        self.runner.units = Mock(side_effect=lambda _: copy.deepcopy(self.units))
        self.runner.read_barrier = closure.LinuxRunner().read_barrier
        for name in (
            "save",
            "save_scope",
            "put_barrier",
            "remove_barrier",
            "reload",
            "stop",
            "signal_actor",
            "processes",
            "verify_scope",
            "activation_scope",
        ):
            setattr(self.runner, name, Mock(side_effect=AssertionError("mutation or native proof")))
        for name in ("_load_authority", "_module", "snapshot_native", "verify_preserved"):
            self.install(
                closure.authority, name, Mock(side_effect=AssertionError("native observer"))
            )

    def inspect(self):
        return closure.inspect_barriers(self.owned)

    def test_six_active_and_two_denied_units_are_diagnostic_without_effects(self):
        before = closure._barrier_files(self.owned)
        result = self.inspect()
        self.assertTrue(result["barriers_verified"])
        for key in (
            "writer_closure_confirmed",
            "ownership_adopted",
            "native_acceptance",
            "activation_authorized",
        ):
            self.assertIs(result[key], False)
        self.assertEqual(sum(row["ActiveState"] == "active" for row in result["units"].values()), 6)
        self.assertEqual(closure._barrier_files(self.owned), before)
        self.assertEqual(self.runner.units.call_count, 2)
        with self.assertRaisesRegex(closure.io.Refusal, "unit_not_stopped"):
            closure._unit_policy(self.units)

    def test_missing_modified_and_unsafely_writable_barrier_are_refused(self):
        for mode in ("missing", "bytes", "writable"):
            with self.subTest(mode=mode):
                self.barrier.unlink()
                if mode != "missing":
                    self.barrier.write_bytes(b"unsafe\n" if mode == "bytes" else closure.DENY_BYTES)
                    self.barrier.chmod(0o666 if mode == "writable" else 0o644)
                with self.assertRaises((closure.io.Refusal, FileNotFoundError)):
                    self.inspect()
                if mode == "missing":
                    self.barrier.write_bytes(closure.DENY_BYTES)
        self.runner.units.assert_not_called()

    def test_same_bytes_replacement_between_samples_is_refused(self):
        def replace(_):
            replacement = self.barrier.with_name("replacement")
            replacement.write_bytes(closure.DENY_BYTES)
            replacement.replace(self.barrier)
            return copy.deepcopy(self.units)

        self.runner.units.side_effect = replace
        with self.assertRaisesRegex(closure.io.Refusal, "barrier_files_changed"):
            self.inspect()

    def test_changed_scope_record_or_loaded_units_is_refused(self):
        original = self.runner.units.side_effect
        for change in ("scope", "ownership", "unit"):
            with self.subTest(change=change):
                if change == "unit":
                    modified = copy.deepcopy(self.units)
                    modified[closure.UNITS[0]]["MainPID"] = "321"
                    self.runner.units.side_effect = [copy.deepcopy(self.units), modified]
                else:

                    def mutate(_, change=change):
                        if change == "scope":
                            self.runner.scopes[self.owned["process_scope_ref"]["path"]][
                                "boot_id"
                            ] = "changed"
                        else:
                            self.runner.saved[self.owned["attempt_dir"]]["boot_id"] = "changed"
                        return copy.deepcopy(self.units)

                    self.runner.units.side_effect = mutate
                with self.assertRaises(closure.io.Refusal):
                    self.inspect()
                self.runner.saved[self.owned["attempt_dir"]] = copy.deepcopy(self.owned)
                self.runner.scopes[self.owned["process_scope_ref"]["path"]]["boot_id"] = self.owned[
                    "boot_id"
                ]
                self.runner.units.side_effect = original

    def test_wrong_guard_path_ignored_errors_order_extra_and_malformed_conditions_refused(self):
        unit = closure.UNITS[0]
        original = self.units[unit]["ExecCondition"]
        guard, deny = original.split(" } ")
        guard += " }"
        for bad in (
            original.replace("path=/usr/bin/python3", "path=/other/python", 1),
            original.replace("ignore_errors=no", "ignore_errors=yes", 1),
            deny + " " + guard,
            original + " " + command("/usr/bin/false"),
            original + " garbage",
            original.replace("pid=0", "malformed", 1),
            original.replace("pid=0", "pid=0 ; pid=0", 1),
        ):
            with self.subTest(bad=bad), self.assertRaises(closure.io.Refusal):
                self.units[unit]["ExecCondition"] = bad
                self.inspect()

    def test_reloaded_hook_queued_job_and_active_denied_role_are_refused(self):
        original = copy.deepcopy(self.units)
        for unit, key, value in (
            (closure.UNITS[0], "NeedDaemonReload", "yes"),
            (closure.UNITS[0], "ExecStop", command("/unsafe/hook")),
            (closure.UNITS[0], "Job", "42/start"),
            ("autostop-codex-start.service", "ActiveState", "active"),
        ):
            with self.subTest(key=key), self.assertRaises(closure.io.Refusal):
                self.units = copy.deepcopy(original)
                self.units[unit][key] = value
                self.inspect()

    def test_incomplete_observation_and_wrong_host_are_refused(self):
        with patch.object(closure.time, "monotonic", side_effect=[0, 31]):
            with self.assertRaisesRegex(closure.io.Refusal, "closure_deadline"):
                self.inspect()
        with patch.object(closure.socket, "gethostname", return_value="another-host"):
            with self.assertRaisesRegex(closure.io.Refusal, "barrier_host_required"):
                self.inspect()
