from __future__ import annotations

import copy
import stat
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from scripts import j1_forward_authority as authority
from scripts.j1_forward_io import Refusal, canonical, sha


def loaded(command: str) -> str:
    return "{ path=/usr/bin/python3 ; argv[]=" + command + " ; ignore_errors=no ; status=0 }"


class AuthorityContractTests(unittest.TestCase):
    def observation(self) -> dict:
        return {
            "immutable": {
                "schema": authority.SCHEMA,
                "base_sha256": "a" * 64,
                "profiles": {"unit": {"stable_environment_sha256": "b" * 64}},
            },
            "units": {"unit": {"enabled": True, "active": True}},
            "processes": {
                "unit": {
                    "pid": 10,
                    "start_ticks": 20,
                    "libraries": [{"sha256": "c" * 64}],
                    "cache_mount": {"mode": "rw"},
                }
            },
            "elapsed_ms": 5,
        }

    def baseline(self) -> dict:
        with patch.object(authority, "_observe", return_value=self.observation()):
            return authority.snapshot_native()

    def test_restart_permits_new_pid_but_requires_same_native_inputs_and_libraries(self) -> None:
        baseline = self.baseline()
        observed = self.observation()
        observed["processes"]["unit"]["pid"] = 50
        observed["processes"]["unit"]["start_ticks"] = 100
        with patch.object(authority, "_observe", return_value=observed):
            result = authority.verify_preserved(baseline, running=True)
        self.assertEqual(50, result["processes"]["unit"]["pid"])
        self.assertNotIn("processes", baseline)
        self.assertNotIn("elapsed_ms", baseline)

    def test_tampered_baseline_fails_before_observing_server(self) -> None:
        baseline = self.baseline()
        baseline["profiles"]["unit"]["stable_environment_sha256"] = "d" * 64
        with patch.object(authority, "_observe") as observe:
            with self.assertRaisesRegex(Refusal, "baseline_digest"):
                authority.verify_preserved(baseline)
        observe.assert_not_called()

    def test_rehashed_stale_authority_or_enablement_is_rejected(self) -> None:
        for change in ("authority", "enablement", "library", "cache_mount", "desired_active"):
            with self.subTest(change=change):
                baseline = self.baseline()
                observed = self.observation()
                if change == "authority":
                    observed["immutable"]["base_sha256"] = "e" * 64
                elif change == "enablement":
                    observed["units"]["unit"]["enabled"] = False
                elif change == "library":
                    observed["processes"]["unit"]["libraries"] = [{"sha256": "e" * 64}]
                elif change == "cache_mount":
                    observed["processes"]["unit"]["cache_mount"] = {"mode": "ro"}
                else:
                    observed["units"]["unit"]["active"] = False
                with patch.object(authority, "_observe", return_value=observed):
                    with self.assertRaises(Refusal):
                        authority.verify_preserved(baseline, running=True)

    def test_stopped_roles_are_allowed_only_for_nonrunning_observation(self) -> None:
        observed = self.observation()
        observed["units"]["unit"]["active"] = False
        observed["processes"] = {}
        with patch.object(authority, "_observe", return_value=observed):
            result = authority.verify_preserved(self.baseline(), running=False)
        self.assertFalse(result["running"])

    def test_code_hash_is_verified_before_any_module_execution(self) -> None:
        raw = b"raise RuntimeError('unverified code executed')\n"
        with patch.object(authority, "read_protected", return_value=raw):
            with self.assertRaisesRegex(Refusal, "code_pin_changed"):
                authority._module(Path("/untrusted.py"), "a" * 64)

    def test_verified_module_is_rechecked_after_loading(self) -> None:
        raw = b"VALUE = 1\n"
        with patch.object(authority, "read_protected", side_effect=[raw, b"VALUE = 2\n"]):
            with self.assertRaisesRegex(Refusal, "changed_during_load"):
                authority._module(Path("/accepted.py"), sha(raw))

    def test_io_uncertainty_is_not_an_absent_marker(self) -> None:
        with patch.object(Path, "lstat", side_effect=PermissionError("private")):
            with self.assertRaises(PermissionError):
                authority._absent(Path("/pending"))


class ClosureAndLoadedUnitTests(unittest.TestCase):
    def observe_systemd(self, rows: list[tuple[str, str]]) -> dict[str, str]:
        response = SimpleNamespace(
            returncode=0, stdout="".join(f"{key}={value}\n" for key, value in rows).encode()
        )
        with patch.object(authority.subprocess, "run", return_value=response):
            return authority._systemd("autostop-work-telegram.service", time.monotonic() + 10)

    def test_repeated_systemd_conditions_preserve_the_guard_then_deny(self) -> None:
        unit, fields, context = self.fixture(closed=True)
        original, deny = fields["ExecCondition"].split(" " + loaded("/usr/bin/false"))
        self.assertEqual("", deny)
        rows = [(key, value) for key, value in fields.items() if key != "ExecCondition"]
        rows += [("ExecCondition", original), ("ExecCondition", loaded("/usr/bin/false"))]
        observed = self.observe_systemd(rows)
        self.assertEqual(fields, observed)
        authority._loaded(unit, observed, context, True)
        for commands in (
            [loaded("/usr/bin/false"), original],
            [original, loaded("/usr/bin/false"), loaded("/usr/bin/false")],
        ):
            with self.subTest(commands=commands):
                observed = self.observe_systemd(
                    rows[:-2] + [("ExecCondition", command) for command in commands]
                )
                with self.assertRaisesRegex(Refusal, "loaded_guard_changed"):
                    authority._loaded(unit, observed, context, True)

    def test_repeated_systemd_commands_cannot_hide_start_or_hook_changes(self) -> None:
        unit, fields, context = self.fixture(closed=True)
        for key in (
            "ExecStart",
            "ExecStartPre",
            "ExecStartPost",
            "ExecStop",
            "ExecStopPost",
            "ExecReload",
        ):
            with self.subTest(key=key):
                rows = [(name, value) for name, value in fields.items() if name != key]
                first = fields.get(key) or loaded("/usr/bin/true")
                rows += [(key, first), (key, loaded("/usr/bin/false"))]
                observed = self.observe_systemd(rows)
                self.assertEqual(first + " " + loaded("/usr/bin/false"), observed[key])
                with self.assertRaises(Refusal):
                    authority._loaded(unit, observed, context, True)

    def test_duplicate_scalar_systemd_properties_remain_invalid(self) -> None:
        _, fields, _ = self.fixture(closed=True)
        for key in ("Id", "LoadState", "NeedDaemonReload", "MainPID", "UnitFileState", "Other"):
            with self.subTest(key=key):
                original = {**fields, key: fields.get(key, "enabled")}
                with self.assertRaisesRegex(Refusal, "systemd_shape"):
                    self.observe_systemd([*original.items(), (key, original[key])])

    def test_empty_repeated_command_or_malformed_systemd_row_is_invalid(self) -> None:
        _, fields, _ = self.fixture(closed=True)
        for commands in (("", fields["ExecCondition"]), (fields["ExecCondition"], "")):
            with self.subTest(commands=commands):
                rows = [(key, value) for key, value in fields.items() if key != "ExecCondition"]
                rows += [("ExecCondition", command) for command in commands]
                with self.assertRaisesRegex(Refusal, "systemd_shape"):
                    self.observe_systemd(rows)
        response = SimpleNamespace(returncode=0, stdout=b"Id=unit\ninvalid-row\n")
        with patch.object(authority.subprocess, "run", return_value=response):
            with self.assertRaisesRegex(Refusal, "systemd_shape"):
                authority._systemd("autostop-work-telegram.service", time.monotonic() + 10)

    def test_unparsed_repeated_command_cannot_be_hidden_by_valid_commands(self) -> None:
        _, fields, _ = self.fixture(closed=True)
        for key in ("ExecCondition", "ExecStart"):
            for invalid in ("unparsed-row", " ", "\t"):
                for invalid_first in (False, True):
                    with self.subTest(key=key, invalid=invalid, invalid_first=invalid_first):
                        values = [fields[key], invalid]
                        if invalid_first:
                            values.reverse()
                        rows = [(name, value) for name, value in fields.items() if name != key]
                        rows += [(key, value) for value in values]
                        with self.assertRaisesRegex(Refusal, "loaded_command_shape"):
                            self.observe_systemd(rows)

    def test_systemd_preserves_each_condition_in_order(self) -> None:
        guard = loaded("original guard")
        deny = loaded("/usr/bin/false")
        raw = (
            "Id=autostop-j1.service\nLoadState=loaded\nNeedDaemonReload=no\n"
            f"ExecCondition={guard}\nExecCondition={deny}\n"
        ).encode()
        with patch.object(
            authority.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=raw)
        ):
            fields = authority._systemd("autostop-j1.service", time.monotonic() + 10)
        self.assertEqual(
            ["original guard", "/usr/bin/false"], authority._commands(fields, "ExecCondition")
        )

    def test_systemd_refuses_duplicate_scalars_and_malformed_lifecycle_hooks(self) -> None:
        prefix = b"Id=autostop-j1.service\nLoadState=loaded\nNeedDaemonReload=no\n"
        for key in ("Id", "LoadState", "NeedDaemonReload", "MainPID", "ExecStop"):
            raw = prefix + f"{key}=one\n{key}=two\n".encode()
            with (
                self.subTest(key=key),
                patch.object(
                    authority.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=0, stdout=raw),
                ),
                self.assertRaises(Refusal),
            ):
                authority._systemd("autostop-j1.service", time.monotonic() + 10)

    def test_denied_bootstrap_and_browser_keep_exact_dropin_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = []
            for unit in set(authority.ALL_UNITS) - set(authority.NATIVE_UNITS):
                folder = root / (unit + ".d")
                folder.mkdir()
                (folder / "99-j1-candidate-guard.conf").write_bytes(b"guard\n")
                hook = folder / "98-hooks.conf"
                hook.write_bytes(b"held\n")
                records.append({"path": str(hook), "sha256": sha(hook.read_bytes())})
            state = {
                "guard": SimpleNamespace(
                    directory=lambda path: None, unit_dropin=lambda unit, digest: "guard\n"
                ),
                "capsule": {"policy": {"sha256": "a" * 64}},
                "policy": {"bindings": {"one": {"unit_files": records}}},
            }
            with (
                patch.object(authority, "SYSTEMD", root),
                patch.object(
                    authority, "read_protected", side_effect=lambda path: path.read_bytes()
                ),
            ):
                authority._unsupported_units(state, False)
                for folder in root.iterdir():
                    (folder / authority.DENY_NAME).write_bytes(authority.DENY_BYTES)
                authority._unsupported_units(state, True)
                with self.assertRaisesRegex(Refusal, "denied_role_dropin_changed"):
                    authority._unsupported_units(state, False)
                surprise = root / "autostop-j1-browser.service.d" / "zzz-extra.conf"
                surprise.write_bytes(b"[Service]\nUser=another\n")
                with self.assertRaisesRegex(Refusal, "denied_role_dropin_changed"):
                    authority._unsupported_units(state, True)

    def closure(self) -> dict:
        digest = sha(authority.DENY_BYTES)
        return {
            "bytes_sha256": digest,
            "dropins": [
                {
                    "unit": unit,
                    "path": str(authority.SYSTEMD / (unit + ".d") / authority.DENY_NAME),
                    "sha256": digest,
                }
                for unit in authority.ALL_UNITS
            ],
        }

    def test_owned_closure_is_exactly_eight_fixed_paths_and_bytes(self) -> None:
        with patch.object(authority, "referenced", return_value=authority.DENY_BYTES):
            self.assertEqual(
                set(authority.ALL_UNITS), set(authority._closure_record(self.closure()))
            )
        for mutation in ("path", "unit", "missing", "hash", "unknown"):
            with self.subTest(mutation=mutation):
                record = self.closure()
                if mutation == "missing":
                    record["dropins"].pop()
                elif mutation == "unknown":
                    record["command"] = "true"
                else:
                    record["dropins"][0]["sha256" if mutation == "hash" else mutation] = "other"
                with self.assertRaisesRegex(Refusal, "closure_shape"):
                    authority._closure_record(record)
        with patch.object(
            authority, "referenced", return_value=b"[Service]\nExecCondition=/usr/bin/true\n"
        ):
            with self.assertRaisesRegex(Refusal, "closure_changed"):
                authority._closure_record(self.closure())

    def fixture(self, *, closed: bool = False) -> tuple[str, dict, dict]:
        unit = "autostop-work-telegram.service"
        policy_hash, intent_hash = "a" * 64, "b" * 64
        context = {"Id": unit, "LoadState": "loaded", "NeedDaemonReload": "no"}
        capsule = {
            "policy": {"sha256": policy_hash},
            "original_duty_intent": {"sha256": intent_hash},
            "profiles": {unit: {"unit_context": context}},
        }
        prefix = f"/usr/bin/python3 -I -S {authority.GUARD}"
        fields = {
            **context,
            "ActiveState": "inactive" if closed else "active",
            "SubState": "dead" if closed else "running",
            "MainPID": "0" if closed else "5",
            "ExecCondition": loaded(f"{prefix} check --policy-sha256 {policy_hash} --unit {unit}"),
            "ExecStart": loaded(f"{prefix} launch --policy-sha256 {policy_hash} --unit {unit}"),
            "ExecStartPost": ""
            if closed
            else loaded(
                f"/usr/bin/python3 -I -S {authority.DUTY} restore --intent-sha256 {intent_hash}"
            ),
        }
        if closed:
            fields["ExecCondition"] += " " + loaded("/usr/bin/false")
        return unit, fields, {"capsule": capsule}

    def test_closure_keeps_original_guard_before_deny_and_removes_all_hooks(self) -> None:
        unit, fields, context = self.fixture(closed=True)
        authority._loaded(unit, fields, context, True)
        for key, value in (
            ("ExecCondition", loaded("/usr/bin/false")),
            ("ExecStartPost", loaded("/bin/true")),
            ("ExecStop", loaded("/bin/true")),
            ("MainPID", "22"),
            ("NeedDaemonReload", "yes"),
        ):
            with self.subTest(key=key):
                changed = {**fields, key: value}
                with self.assertRaises(Refusal):
                    authority._loaded(unit, changed, context, True)

    def test_repeated_condition_rows_still_require_exact_original_guard_then_owned_deny(self):
        unit, fields, context = self.fixture(closed=True)
        guard, deny = authority._commands(fields, "ExecCondition")
        prefix = "".join(
            key + "=" + value + "\n" for key, value in fields.items() if key != "ExecCondition"
        )
        for conditions in (
            (guard, deny),
            (deny, guard),
            (deny,),
            (guard, "/usr/bin/true"),
            (guard, deny, "/usr/bin/true"),
            (guard, guard, deny),
        ):
            raw = (
                prefix + "".join("ExecCondition=" + loaded(row) + "\n" for row in conditions)
            ).encode()
            with (
                self.subTest(conditions=conditions),
                patch.object(
                    authority.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=0, stdout=raw),
                ),
            ):
                observed = authority._systemd(unit, time.monotonic() + 10)
                if conditions == (guard, deny):
                    authority._loaded(unit, observed, context, True)
                else:
                    with self.assertRaisesRegex(Refusal, "native_loaded_guard_changed"):
                        authority._loaded(unit, observed, context, True)

    def test_work_restore_is_the_only_allowed_post_hook(self) -> None:
        unit, fields, context = self.fixture()
        authority._loaded(unit, fields, context, False)
        fields["ExecStartPost"] += " " + loaded("/bin/true")
        with self.assertRaisesRegex(Refusal, "duty_hook_changed"):
            authority._loaded(unit, fields, context, False)

    def test_static_closure_allows_no_unowned_dropin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unit = "autostop-j1.service"
            folder = root / (unit + ".d")
            folder.mkdir()
            own = folder / "99-j1-candidate-guard.conf"
            own.write_bytes(b"guard\n")
            (folder / authority.DENY_NAME).write_bytes(authority.DENY_BYTES)
            original = folder / "98-hooks.conf"
            original.write_bytes(b"old hooks\n")
            binding = {
                "guard_dropin": str(own),
                "unit_files": [{"path": str(original), "sha256": sha(original.read_bytes())}],
                "dropin_directories": [
                    {"path": str(folder), "files": {original.name: sha(original.read_bytes())}}
                ],
            }
            guard = SimpleNamespace(
                directory=lambda path: None, unit_dropin=lambda unit, digest: "guard\n"
            )
            with (
                patch.object(authority, "SYSTEMD", root),
                patch.object(
                    authority, "read_protected", side_effect=lambda path: Path(path).read_bytes()
                ),
            ):
                authority._units_with_closure(guard, binding, unit, "a" * 64)
                (folder / "zzz-surprise.conf").write_bytes(b"[Service]\nExecStart=/bin/true\n")
                with self.assertRaisesRegex(Refusal, "unknown_dropin"):
                    authority._units_with_closure(guard, binding, unit, "a" * 64)


class NativeProcessTests(unittest.TestCase):
    def fixture(self) -> tuple[dict, dict, dict]:
        unit = "autostop-j1.service"
        binding = {
            "interpreter": "/venv/bin/python",
            "interpreter_realpath": "/usr/bin/python3.12",
            "cwd": "/source",
            "source_root": "/source",
            "selector": "/selector",
            "module": "autostop_manager.j1_research",
            "arguments": ["worker"],
            "credentials": [0, 0, [0]],
            "access": "writer",
        }
        fields = {
            "MainPID": "42",
            "ActiveState": "active",
            "SubState": "running",
            "ControlGroup": "/system.slice/" + unit,
        }
        proc = {
            "cgroup": ("0::/system.slice/" + unit + "\n").encode(),
            "cmdline": b"/venv/bin/python\0-P\0-m\0autostop_manager.j1_research\0worker\0",
            "status": b"Uid:\t0\t0\t0\t0\nGid:\t0\t0\t0\t0\nGroups:\t0\n",
            "environ": b"PYTHONPATH=/source\0PRIVATE_TOKEN=never-emit-this\0",
        }
        return binding, fields, proc

    def run_fixture(
        self, binding: dict, fields: dict, proc: dict, *, after: dict | None = None
    ) -> tuple[dict, Mock]:
        guard = SimpleNamespace(verify_environment=Mock())
        with (
            patch.object(authority, "_start", return_value="123"),
            patch.object(authority, "_proc", side_effect=lambda pid, name: proc[name]),
            patch.object(
                authority.os,
                "readlink",
                side_effect=lambda path: (
                    binding["interpreter_realpath"] if path.name == "exe" else binding["cwd"]
                ),
            ),
            patch.object(
                authority, "_libraries", return_value=[{"path": "/libsqlite", "sha256": "a" * 64}]
            ),
            patch.object(authority, "_cache_mount", return_value={"mode": "rw"}),
            patch.object(authority, "_systemd", return_value=after or fields),
        ):
            result = authority._running(
                "autostop-j1.service", binding, fields, guard, time.monotonic() + 10
            )
        return result, guard.verify_environment

    def test_actual_process_env_is_checked_without_returning_secret(self) -> None:
        binding, fields, proc = self.fixture()
        result, verify = self.run_fixture(binding, fields, proc)
        verify.assert_called_once_with(
            {"PYTHONPATH": "/selector", "PRIVATE_TOKEN": "never-emit-this"}, binding
        )
        self.assertNotIn("never-emit-this", canonical(result).decode())
        self.assertNotIn("environment", result)

    def test_foreign_cgroup_argv_credentials_environment_or_pid_reuse_is_denied(self) -> None:
        for name, value in (
            ("cgroup", b"0::/user.slice/foreign\n"),
            ("cmdline", b"python\0foreign.py\0"),
            ("status", b"Uid:\t1\t1\t1\t1\nGid:\t0\t0\t0\t0\nGroups:\t0\n"),
            ("environ", b"PYTHONPATH=/source\0PYTHONPATH=/other\0"),
            ("environ", b"PYTHONPATH=/other\0"),
        ):
            with self.subTest(name=name, value=value):
                binding, fields, proc = self.fixture()
                proc[name] = value
                with self.assertRaises(Refusal):
                    self.run_fixture(binding, fields, proc)
        binding, fields, proc = self.fixture()
        with self.assertRaisesRegex(Refusal, "changed_during_observation"):
            self.run_fixture(binding, fields, proc, after={**fields, "MainPID": "99"})

    def test_cache_mount_requires_single_exact_readonly_mapping(self) -> None:
        good = b"266 30 253:1 /var/cache/autostop-j1 /var/cache/autostop-j1 ro,relatime - ext4 /dev/vda1 rw\n"
        with patch.object(authority, "_proc", return_value=good):
            self.assertEqual("ro", authority._cache_mount("1", "ro")["mode"])
        for raw in (good.replace(b"ro,", b"rw,"), good + good, b""):
            with patch.object(authority, "_proc", return_value=raw), self.assertRaises(Refusal):
                authority._cache_mount("1", "ro")

    def test_native_cache_requires_the_accepted_readonly_then_readwrite_stack(self) -> None:
        lower = b"266 30 253:1 /var/cache/autostop-j1 /var/cache/autostop-j1 ro,relatime - ext4 /dev/vda1 rw\n"
        upper = b"267 266 253:1 /var/cache/autostop-j1 /var/cache/autostop-j1 rw,relatime - ext4 /dev/vda1 rw\n"
        with (
            patch.object(authority, "_proc", return_value=upper + lower),
            patch.object(authority, "_identity", return_value=[1, 2, 0, 0, 384]),
        ):
            result = authority._cache_mount("42", "native")
            self.assertEqual(["ro", "rw"], [item["mode"] for item in result["layers"]])
            self.assertNotIn("id", result["layers"][0])
        for raw in (
            lower,
            lower + upper.replace(b"267 266", b"267 30"),
            lower + upper.replace(b"253:1", b"253:2"),
            lower + upper + upper,
        ):
            with (
                patch.object(authority, "_proc", return_value=raw),
                patch.object(authority, "_identity", return_value=[1, 2, 0, 0, 384]),
                self.assertRaises(Refusal),
            ):
                authority._cache_mount("42", "native")
        with (
            patch.object(authority, "_proc", return_value=lower + upper),
            patch.object(authority, "_identity", side_effect=[[1, 2], [1, 3]]),
            self.assertRaisesRegex(Refusal, "cache_identity_changed"),
        ):
            authority._cache_mount("42", "native")

    def test_start_time_handles_spaces_and_parentheses_in_process_name(self) -> None:
        fields = ["S"] + ["0"] * 18 + ["123"] + ["0"] * 5
        with patch.object(
            authority, "_proc", return_value=("42 (worker (warm)) " + " ".join(fields)).encode()
        ):
            self.assertEqual("123", authority._start("42"))

    def test_observation_deadline_refuses_without_subprocess(self) -> None:
        with patch.object(authority.subprocess, "run") as run:
            with self.assertRaisesRegex(Refusal, "deadline"):
                authority._systemd("autostop-j1.service", time.monotonic() - 1)
        run.assert_not_called()


class RecoveryWarmObserverTests(unittest.TestCase):
    """Exercise real recovery and warm observers at the read-only proc boundary."""

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.historical = []
        self.cmdlines = {}
        for pid in (538285, 538314, 538487):
            self.cmdlines[str(pid)] = f"actor-{pid}\0accepted\0".encode()
            self.historical.append(
                {
                    "pid": pid,
                    "start_ticks": pid + 10,
                    "argv_sha256": sha(self.cmdlines[str(pid)]),
                    "cgroup": "0::/user.slice/accepted",
                    "exe": f"/bin/actor-{pid}",
                    "mount_namespace": "mnt:[10]",
                    "exe_sha256": "a" * 64,
                    "exe_identity": [7, pid],
                    "exe_file_identity": [7, pid, 0, 0, 0o755],
                    "exe_metadata": [7, pid, stat.S_IFREG | 0o755, 0, 0, 42, 1, 2],
                    "uid": 0,
                }
            )
        self.accepted = {
            "warm": {"boot_id": "original-boot", "actors": copy.deepcopy(self.historical)}
        }
        self.original = {"schema": authority.SCHEMA, "warm_actors": copy.deepcopy(self.historical)}
        self.origin = {"path": "/root-private/origin.json", "sha256": "d" * 64}
        module = ModuleType("scripts.j1_forward_recovery")
        self.validate = Mock(return_value={"baseline": {"native": self.original}})
        module.validate_origin = self.validate
        self.stack.enter_context(patch.dict(sys.modules, {module.__name__: module}))
        self.stack.enter_context(patch.object(Path, "read_text", return_value="original-boot\n"))
        self.absent = self.stack.enter_context(
            patch.object(authority, "_absent", return_value=True)
        )
        self.starts = self.stack.enter_context(
            patch.object(authority, "_start", side_effect=lambda pid: str(int(pid) + 10))
        )
        self.process = self.stack.enter_context(
            patch.object(authority, "_proc", side_effect=self.proc)
        )
        self.stack.enter_context(patch.object(authority.os, "readlink", side_effect=self.readlink))
        self.executable = self.stack.enter_context(
            patch.object(authority, "_warm_executable", side_effect=self.executable_record)
        )
        self.mapped = self.stack.enter_context(patch.object(authority, "_mapped_executable"))
        self.mount = self.stack.enter_context(patch.object(authority, "_cache_mount"))

    def proc(self, pid, name):
        if name == "cmdline":
            return self.cmdlines[pid]
        if name == "cgroup":
            return b"0::/user.slice/accepted\n"
        if name == "status":
            return b"Uid:\t0\t0\t0\t0\n"
        raise AssertionError(name)

    def readlink(self, path):
        if path.name == "exe":
            return f"/bin/actor-{path.parent.name}"
        if path.name == "mnt":
            return "mnt:[10]"
        raise AssertionError(path)

    def executable_record(self, path, deadline):
        row = next(item for item in self.historical if item["exe"] == path.as_posix())
        return {
            "sha256": row["exe_sha256"],
            "identity": row["exe_file_identity"],
            "metadata": row["exe_metadata"],
        }

    def observe(self):
        return authority._recovery_warm(self.accepted, self.origin, time.monotonic() + 20)

    def test_recovery_keeps_historical_three_and_observes_only_exact_two(self):
        before = copy.deepcopy(self.accepted)
        historical, observed = self.observe()
        self.validate.assert_called_once_with(self.origin)
        self.assertEqual(historical, self.historical)
        self.assertEqual([row["pid"] for row in observed], [538285, 538487])
        self.assertEqual(
            self.accepted, before, "recovery must not mutate the shared accepted policy"
        )
        self.assertEqual(
            {call.args[0] for call in self.starts.call_args_list}, {"538285", "538487"}
        )
        self.assertEqual(
            {call.args[0] for call in self.mapped.call_args_list}, {"538285", "538487"}
        )
        self.assertEqual(self.mount.call_count, 2)
        self.assertTrue(all(call.args[1] == "ro" for call in self.mount.call_args_list))
        self.assertTrue(
            all(call.args[0] == authority.PROC / "538314" for call in self.absent.call_args_list)
        )

    def test_missing_or_rejected_origin_stops_before_process_observation(self):
        for error in (
            FileNotFoundError("missing original source"),
            Refusal("origin_reference_changed"),
        ):
            with self.subTest(error=type(error).__name__):
                self.validate.side_effect = error
                with self.assertRaises(type(error)):
                    self.observe()
        self.absent.assert_not_called()
        self.starts.assert_not_called()
        self.executable.assert_not_called()

    def test_historical_core_identity_must_match_immutable_acceptance(self):
        for key, value in (
            ("pid", 999),
            ("start_ticks", 999),
            ("argv_sha256", "c" * 64),
            ("cgroup", "foreign"),
            ("exe", "/foreign"),
            ("mount_namespace", "mnt:[99]"),
        ):
            with self.subTest(key=key):
                self.original["warm_actors"] = copy.deepcopy(self.historical)
                self.original["warm_actors"][0][key] = value
                with self.assertRaisesRegex(Refusal, "recovery_historical_warm_changed"):
                    self.observe()
        self.starts.assert_not_called()

    def test_departed_transport_reappearance_or_uncertain_absence_refuses(self):
        self.absent.return_value = False
        with self.assertRaisesRegex(Refusal, "recovery_departed_transport_reappeared"):
            self.observe()
        self.absent.side_effect = PermissionError("cannot inspect proc")
        with self.assertRaises(PermissionError):
            self.observe()
        self.starts.assert_not_called()

    def test_departed_transport_reappearance_during_live_hashing_refuses(self):
        self.absent.side_effect = [True, False]
        with self.assertRaisesRegex(Refusal, "recovery_departed_transport_reappeared"):
            self.observe()
        self.assertEqual(self.mapped.call_count, 2)

    def test_either_remaining_writer_missing_refuses_without_historical_fallback(self):
        for dead in ("538285", "538487"):
            with self.subTest(dead=dead):

                def start(pid):
                    if pid == dead:
                        raise FileNotFoundError("writer disappeared")
                    return str(int(pid) + 10)

                self.starts.side_effect = start
                with self.assertRaises(FileNotFoundError):
                    self.observe()

    def test_original_boot_and_live_birth_checks_remain_mandatory(self):
        with patch.object(Path, "read_text", return_value="different-boot\n"):
            with self.assertRaisesRegex(Refusal, "native_warm_boot_changed"):
                self.observe()
        self.starts.assert_not_called()
        self.starts.side_effect = lambda pid: str(int(pid) + 11)
        with self.assertRaisesRegex(Refusal, "native_warm_actor_changed"):
            self.observe()

    def test_live_birth_reuse_during_hashing_is_rejected(self):
        calls = 0

        def start(pid):
            nonlocal calls
            calls += 1
            return str(int(pid) + (11 if calls == 2 else 10))

        self.starts.side_effect = start
        with self.assertRaisesRegex(Refusal, "native_warm_actor_replaced"):
            self.observe()

    def test_all_observed_hash_and_metadata_fields_equal_historical_rows(self):
        for key, value in (
            ("exe_sha256", "b" * 64),
            ("exe_identity", [7, 999]),
            ("exe_file_identity", [7, 999, 0, 0, 0o755]),
            ("exe_metadata", [7, 999, stat.S_IFREG | 0o755, 0, 0, 42, 1, 2]),
        ):
            with self.subTest(key=key):
                self.original["warm_actors"] = copy.deepcopy(self.historical)
                self.original["warm_actors"][0][key] = value
                with self.assertRaisesRegex(Refusal, "recovery_live_warm_changed"):
                    self.observe()

    def test_mapped_executable_hash_refusal_is_not_suppressed(self):
        self.mapped.side_effect = Refusal("native_warm_mapped_executable_changed")
        with self.assertRaisesRegex(Refusal, "native_warm_mapped_executable_changed"):
            self.observe()

    def test_live_writer_credentials_and_readonly_cache_are_still_checked(self):
        def changed_proc(pid, name):
            if name == "status":
                return b"Uid:\t10001\t10001\t10001\t10001\n"
            return self.proc(pid, name)

        self.process.side_effect = changed_proc
        with self.assertRaisesRegex(Refusal, "native_warm_actor_credentials_changed"):
            self.observe()
        self.process.side_effect = self.proc
        self.mount.side_effect = Refusal("native_cache_mount_mode")
        with self.assertRaisesRegex(Refusal, "native_cache_mount_mode"):
            self.observe()

    def test_observed_live_set_cannot_be_missing_extended_or_reordered(self):
        expected = [row for row in self.historical if row["pid"] != 538314]
        for rows in (expected[:1], expected[::-1], self.historical):
            with self.subTest(pids=[row["pid"] for row in rows]):
                with patch.object(authority, "_warm_actors", return_value=rows):
                    with self.assertRaisesRegex(Refusal, "recovery_warm_live_set"):
                        self.observe()

    def test_normal_v1_observer_still_requires_departed_original_transport_alive(self):
        def start(pid):
            if pid == "538314":
                raise FileNotFoundError("departed original transport")
            return str(int(pid) + 10)

        self.starts.side_effect = start
        with self.assertRaises(FileNotFoundError):
            authority._warm_actors(self.accepted, time.monotonic() + 20)
        self.validate.assert_not_called()
        self.absent.assert_not_called()
        self.assertIn("538314", [call.args[0] for call in self.starts.call_args_list])


class RecoverySnapshotContractTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.ordinary = AuthorityContractTests().observation()
        self.historical = [
            {"pid": pid, "start_ticks": pid + 10} for pid in (538285, 538314, 538487)
        ]
        self.ordinary["immutable"]["warm_actors"] = copy.deepcopy(self.historical)
        with patch.object(authority, "_observe", return_value=self.ordinary):
            self.original = authority.snapshot_native()
        self.origin = {"path": "/protected/recovery-origin.json", "sha256": "e" * 64}
        self.owned = {"dropins": ["owned eight"], "bytes_sha256": "f" * 64}
        module = ModuleType("scripts.j1_forward_recovery")
        self.validate = Mock(return_value={"baseline": {"native": self.original}})
        module.validate_origin = self.validate
        self.stack.enter_context(patch.dict(sys.modules, {module.__name__: module}))

    def observation(self):
        result = copy.deepcopy(self.ordinary)
        result["immutable"].update(
            {
                "schema": authority.RECOVERY_SCHEMA,
                "warm_identity_semantics": authority.WARM_RECOVERY_SEMANTICS,
                "historical_warm_actors": copy.deepcopy(self.historical),
                "observed_warm_actors": [row for row in self.historical if row["pid"] != 538314],
                "recovery_origin": copy.deepcopy(self.origin),
            }
        )
        return result

    def snapshot(self):
        with patch.object(authority, "_observe", return_value=self.observation()) as observe:
            result = authority.snapshot_recovery(self.original, self.origin, self.owned)
        observe.assert_called_once_with(
            running=True,
            owned_closure=self.owned,
            full_runtime=True,
            recovery_origin=self.origin,
            preclose=True,
        )
        return result

    def test_snapshot_v2_explicitly_distinguishes_historical_and_observed_identity(self):
        before = copy.deepcopy(self.original)
        result = self.snapshot()
        self.assertEqual(result["schema"], authority.RECOVERY_SCHEMA)
        self.assertEqual(result["warm_actors"], result["historical_warm_actors"])
        self.assertEqual([row["pid"] for row in result["observed_warm_actors"]], [538285, 538487])
        self.assertEqual(result["warm_identity_semantics"], authority.WARM_RECOVERY_SEMANTICS)
        self.assertEqual(
            result["sha256"],
            sha(canonical({key: value for key, value in result.items() if key != "sha256"})),
        )
        self.assertEqual(self.original, before)
        self.assertNotEqual(result["sha256"], self.original["sha256"])

    def test_snapshot_rejects_unbound_original_before_fresh_server_observation(self):
        wrong = copy.deepcopy(self.original)
        wrong["base_sha256"] = "0" * 64
        with patch.object(authority, "_observe") as observe:
            with self.assertRaisesRegex(Refusal, "recovery_native_origin"):
                authority.snapshot_recovery(wrong, self.origin, self.owned)
        observe.assert_not_called()

    def test_snapshot_origin_failure_is_not_a_normal_snapshot_fallback(self):
        self.validate.side_effect = Refusal("origin_circular_or_missing")
        with patch.object(authority, "_observe") as observe:
            with self.assertRaisesRegex(Refusal, "origin_circular_or_missing"):
                authority.snapshot_recovery(self.original, self.origin, self.owned)
        observe.assert_not_called()

    def test_recovery_snapshot_cannot_change_any_preserved_native_input(self):
        for field in (
            "base_sha256",
            "profiles",
            "warm_actors",
            "desired_units",
            "libraries",
            "cache_mounts",
        ):
            with self.subTest(field=field):
                observed = self.observation()
                if field == "desired_units":
                    observed["units"]["unit"]["active"] = False
                elif field == "libraries":
                    observed["processes"]["unit"]["libraries"] = []
                elif field == "cache_mounts":
                    observed["processes"]["unit"]["cache_mount"] = {"mode": "ro"}
                else:
                    observed["immutable"][field] = [] if field == "warm_actors" else "changed"
                with patch.object(authority, "_observe", return_value=observed):
                    with self.assertRaisesRegex(Refusal, "recovery_native_inputs_changed"):
                        authority.snapshot_recovery(self.original, self.origin, self.owned)

    def test_verify_v2_dispatches_origin_and_returns_explicit_two_live_rows(self):
        baseline = self.snapshot()
        with patch.object(authority, "_observe", return_value=self.observation()) as observe:
            result = authority.verify_preserved(baseline, running=True)
        observe.assert_called_once_with(
            running=True, owned_closure=None, full_runtime=False, recovery_origin=self.origin
        )
        self.assertEqual(result["historical_warm_actors"], self.historical)
        self.assertEqual([row["pid"] for row in result["observed_warm_actors"]], [538285, 538487])
        self.assertEqual(result["schema"], authority.RECOVERY_SCHEMA)

    def test_v2_cannot_omit_semantics_or_origin_even_with_new_digest(self):
        for field in ("warm_identity_semantics", "recovery_origin"):
            baseline = self.snapshot()
            del baseline[field]
            baseline["sha256"] = sha(
                canonical({key: value for key, value in baseline.items() if key != "sha256"})
            )
            with patch.object(authority, "_observe") as observe:
                with self.assertRaisesRegex(Refusal, "recovery_native_semantics"):
                    authority.verify_preserved(baseline)
            observe.assert_not_called()

    def test_v1_schema_cannot_enable_recovery_exception_with_extra_origin(self):
        baseline = copy.deepcopy(self.original)
        baseline["recovery_origin"] = self.origin
        baseline["sha256"] = sha(
            canonical({key: value for key, value in baseline.items() if key != "sha256"})
        )
        with patch.object(authority, "_observe", return_value=self.ordinary) as observe:
            with self.assertRaisesRegex(Refusal, "native_immutable_inputs_changed"):
                authority.verify_preserved(baseline)
        observe.assert_called_once_with(running=False, owned_closure=None, full_runtime=False)

    def test_public_verify_rejects_v1_preclose_before_reading_native_inputs(self):
        with patch.object(authority, "_observe") as observe:
            with self.assertRaisesRegex(Refusal, "native_preclose_scope"):
                authority.verify_preserved(
                    self.original, running=True, owned_closure=self.owned, preclose=True
                )
        observe.assert_not_called()

    def test_public_verify_forwards_preclose_only_for_explicit_v2(self):
        baseline = self.snapshot()
        with patch.object(authority, "_observe", return_value=self.observation()) as observe:
            authority.verify_preserved(
                baseline, running=True, owned_closure=self.owned, preclose=True
            )
        observe.assert_called_once_with(
            running=True,
            owned_closure=self.owned,
            full_runtime=False,
            recovery_origin=self.origin,
            preclose=True,
        )

    def test_preclose_exception_requires_running_owned_and_explicit_recovery_context(self):
        state = {"guard": Mock(), "wrapper": Mock(), "policy": {}}
        cases = (
            (True, True, None, False, "native_running_and_closed_conflict"),
            (True, True, None, True, "native_running_and_closed_conflict"),
            (True, False, self.origin, True, "native_preclose_scope"),
            (False, True, self.origin, True, "native_preclose_scope"),
            (True, True, self.origin, False, "native_running_and_closed_conflict"),
        )
        for running, owned, origin, preclose, error in cases:
            with self.subTest(running=running, owned=owned, origin=origin, preclose=preclose):
                with (
                    patch.object(authority, "_load_authority", return_value=state),
                    patch.object(
                        authority, "_closure_record", return_value={"owned": True} if owned else {}
                    ),
                    patch.object(authority, "_unsupported_units") as units,
                ):
                    with self.assertRaisesRegex(Refusal, error):
                        authority._observe(
                            running=running,
                            owned_closure=self.owned if owned else None,
                            full_runtime=False,
                            recovery_origin=origin,
                            preclose=preclose,
                        )
                units.assert_not_called()

    def test_preclose_only_relaxes_active_state_for_six_original_native_roles(self):
        fixture = ClosureAndLoadedUnitTests()
        unit, fields, state = fixture.fixture(closed=True)
        fields.update(ActiveState="active", SubState="running", MainPID="42")
        authority._loaded(unit, fields, state, True, preclose=True)
        with self.assertRaisesRegex(Refusal, "native_closed_role_still_active"):
            authority._loaded(unit, fields, state, True)
        for bad in (
            {"ExecCondition": loaded("/usr/bin/false")},
            {"ExecStop": loaded("/bin/true")},
            {"NeedDaemonReload": "yes"},
        ):
            with self.subTest(bad=bad), self.assertRaises(Refusal):
                authority._loaded(unit, {**fields, **bad}, state, True, preclose=True)
        for denied in set(authority.ALL_UNITS) - set(authority.NATIVE_UNITS):
            denied_fields = {key: value.replace(unit, denied) for key, value in fields.items()}
            with (
                self.subTest(denied=denied),
                self.assertRaisesRegex(Refusal, "native_closed_role_still_active"),
            ):
                authority._loaded(denied, denied_fields, state, True, preclose=True)


class RetainedOriginObserverIntegrationTests(unittest.TestCase):
    """Real archived files/hashes and validator; only live process observation is synthetic."""

    def setUp(self):
        from tests.test_j1_forward_recovery import OriginTests

        self.files = OriginTests()
        self.files.setUp()
        self.addCleanup(self.files.doCleanups)
        self.historical = copy.deepcopy(self.files.baseline["native"]["warm_actors"])
        self.accepted = {"warm": {"actors": copy.deepcopy(self.historical)}}
        self.live = [row for row in self.historical if row["pid"] != 538314]

    def observe(self):
        with (
            patch.object(authority, "_absent", return_value=True),
            patch.object(authority, "_warm_actors", return_value=copy.deepcopy(self.live)) as warm,
        ):
            result = authority._recovery_warm(
                self.accepted, self.files.origin, time.monotonic() + 20
            )
        self.assertEqual(
            [row["pid"] for row in warm.call_args.args[0]["warm"]["actors"]], [538285, 538487]
        )
        return result

    def test_actual_origin_validator_allows_archived_provenance_after_pending_consumed(self):
        result = self.observe()
        self.files.files.store.pending_path.unlink()
        with (
            patch.object(
                authority, "snapshot_native", side_effect=AssertionError("recursive observation")
            ),
            patch.object(
                authority, "verify_preserved", side_effect=AssertionError("recursive observation")
            ),
        ):
            self.assertEqual(self.observe(), result)
        self.assertEqual(result, (self.historical, self.live))

    def test_archive_and_consumer_tamper_fail_before_any_live_observation(self):
        paths = (
            self.files.retained / "pending.json",
            self.files.retained / "consumers/scripts/j1_forward_release.py",
        )
        for path in paths:
            original = path.read_bytes()
            try:
                path.write_bytes(original + b"\n")
                with (
                    self.subTest(path=path.name),
                    patch.object(authority, "_warm_actors") as warm,
                    patch.object(authority, "_absent") as absent,
                ):
                    with self.assertRaises(Refusal):
                        authority._recovery_warm(
                            self.accepted, self.files.origin, time.monotonic() + 20
                        )
                warm.assert_not_called()
                absent.assert_not_called()
            finally:
                path.write_bytes(original)

    def test_real_validator_refuses_missing_original_source_before_observation(self):
        (self.files.retained / "consumers/scripts/j1_forward_release.py").unlink()
        with patch.object(authority, "_warm_actors") as warm:
            with self.assertRaises(FileNotFoundError):
                authority._recovery_warm(self.accepted, self.files.origin, time.monotonic() + 20)
        warm.assert_not_called()

    def test_rehashed_bundle_cannot_repoint_consumer_to_unrelated_matching_file(self):
        from scripts import j1_forward_io as io

        reference = self.files.origin["origin_bundle_ref"]
        bundle = io.document(io.referenced(reference))
        name = "scripts/j1_forward_release.py"
        foreign = self.files.old / "matching-but-unretained.py"
        foreign.write_bytes(self.files.raw)
        bundle["files"][name] = io.reference(foreign)
        Path(reference["path"]).write_bytes(canonical(bundle))
        changed = {**self.files.origin, "origin_bundle_ref": io.reference(reference["path"])}
        with patch.object(authority, "_warm_actors") as warm:
            with self.assertRaisesRegex(Refusal, "recovery_origin_consumer_binding"):
                authority._recovery_warm(self.accepted, changed, time.monotonic() + 20)
        warm.assert_not_called()


class RuntimeInventoryTests(unittest.TestCase):
    def test_warm_pid_incarnations_require_the_original_boot(self) -> None:
        with (
            patch.object(Path, "read_text", return_value="other-boot\n"),
            patch.object(authority, "_start") as start,
            self.assertRaisesRegex(Refusal, "warm_boot_changed"),
        ):
            authority._warm_actors({"warm": {"boot_id": "accepted-boot"}}, time.monotonic() + 10)
        start.assert_not_called()

    def test_warm_archive_uid_exception_requires_the_exact_private_path(self) -> None:
        def metadata(path: Path):
            private = path == Path("/root")
            payload = path in {authority.WARM_CODEX, authority.WARM_CODEX.parent}
            return SimpleNamespace(
                st_mode=(stat.S_IFREG if path == authority.WARM_CODEX else stat.S_IFDIR)
                | (0o700 if private else 0o755),
                st_uid=1001 if payload else 0,
                st_gid=0,
                st_dev=1,
                st_ino=2,
                st_size=10,
                st_mtime_ns=3,
                st_ctime_ns=4,
            )

        with (
            patch.object(Path, "lstat", metadata),
            patch.object(authority, "_file_digest", return_value="a" * 64) as digest,
        ):
            authority._warm_executable(authority.WARM_CODEX, 100)
            digest.assert_called_once_with(authority.WARM_CODEX, 100, owner_uid=1001)
        for mutation in ("root_private", "ancestor_owner", "symlink", "bin_mode"):

            def changed(path: Path):
                info = metadata(path)
                if mutation == "root_private" and path == Path("/root"):
                    info.st_mode = stat.S_IFDIR | 0o755
                elif mutation == "ancestor_owner" and path == Path("/root/.codex"):
                    info.st_uid = 1001
                elif mutation == "symlink" and path == authority.WARM_CODEX.parent:
                    info.st_mode = stat.S_IFLNK | 0o755
                elif mutation == "bin_mode" and path == authority.WARM_CODEX.parent:
                    info.st_mode = stat.S_IFDIR | 0o750
                return info

            with (
                self.subTest(mutation=mutation),
                patch.object(Path, "lstat", changed),
                patch.object(authority, "_file_digest") as digest,
                self.assertRaises(Refusal),
            ):
                authority._warm_executable(authority.WARM_CODEX, 100)
            digest.assert_not_called()
        with (
            patch.object(Path, "lstat", metadata),
            patch.object(authority, "_file_digest", return_value="a" * 64) as digest,
        ):
            authority._warm_executable(Path("/different/codex"), 100)
            digest.assert_called_once_with(Path("/different/codex"), 100, owner_uid=0)

    def test_mapped_executable_must_match_both_metadata_and_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "42").mkdir()
            (root / "42/exe").write_bytes(b"accepted executable")
            expected = {"metadata": [1], "sha256": sha(b"accepted executable")}
            with (
                patch.object(authority, "PROC", root),
                patch.object(authority, "_metadata", return_value=[1]),
            ):
                authority._mapped_executable("42", expected, time.monotonic() + 10)
                with self.assertRaisesRegex(Refusal, "mapped_executable_changed"):
                    authority._mapped_executable(
                        "42", {**expected, "sha256": "a" * 64}, time.monotonic() + 10
                    )
            with (
                patch.object(authority, "PROC", root),
                patch.object(authority, "_metadata", return_value=[2]),
                self.assertRaisesRegex(Refusal, "mapped_executable_changed"),
            ):
                authority._mapped_executable("42", expected, time.monotonic() + 10)

    def test_full_inventory_hashes_bytes_and_later_metadata_drift_rechecks_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "module.py"
            source.write_bytes(b"accepted bytes\n")
            source.chmod(0o444)
            record = {
                "relative": source.name,
                "kind": "file",
                "metadata": authority._metadata(source.stat()),
                "sha256": sha(source.read_bytes()),
            }
            inventory = {
                "schema": "autostop.public-runtime-tree-inventory.v1",
                "root": str(root),
                "records": [record],
            }
            ref = {
                "path": "/autostop-runtime-inventories-test/manifest.sha256",
                "sha256": sha(canonical(inventory)),
            }
            state = {
                "guard": SimpleNamespace(directory=lambda path: None),
                "policy": {
                    "bindings": {"one": {"runtime_files": [ref]}, "two": {"runtime_files": [ref]}}
                },
            }
            with (
                patch.object(authority, "read_protected", return_value=canonical(inventory)),
                patch.object(authority.j1_forward_io, "ROOT_UID", source.stat().st_uid),
                # Inventory policy is platform independent; exact Linux
                # descriptor metadata checks are exercised separately.
                patch.object(
                    authority,
                    "_file_digest",
                    side_effect=lambda path, deadline: sha(path.read_bytes()),
                ),
            ):
                result = authority._runtime_inventory(state, True, time.monotonic() + 10)
                self.assertEqual(
                    1, len(result), "Shared runtimes should be checked once per observation"
                )
                self.assertEqual(
                    result, authority._runtime_inventory(state, False, time.monotonic() + 10)
                )
                source.chmod(0o644)
                source.write_bytes(b"tampered bytes\n")
                source.chmod(0o444)
                with patch.object(
                    authority, "_file_digest", wraps=authority._file_digest
                ) as digest:
                    with self.assertRaisesRegex(Refusal, "runtime_content_changed"):
                        authority._runtime_inventory(state, False, time.monotonic() + 10)
                    digest.assert_called_once()
            source.chmod(0o644)

    def test_runtime_inventory_rejects_unlisted_file_and_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "module.py"
            source.write_bytes(b"accepted\n")
            source.chmod(0o444)
            inventory = {
                "schema": "autostop.public-runtime-tree-inventory.v1",
                "root": str(root),
                "records": [
                    {
                        "relative": source.name,
                        "kind": "file",
                        "metadata": authority._metadata(source.stat()),
                        "sha256": sha(source.read_bytes()),
                    }
                ],
            }
            state = {
                "guard": SimpleNamespace(directory=lambda path: None),
                "policy": {
                    "bindings": {
                        "unit": {
                            "runtime_files": [
                                {
                                    "path": "/autostop-runtime-inventories-test/inventory",
                                    "sha256": sha(canonical(inventory)),
                                }
                            ]
                        }
                    }
                },
            }
            extra = root / "surprise.pth"
            extra.write_bytes(b"import unwanted\n")
            with (
                patch.object(authority, "read_protected", return_value=canonical(inventory)),
                patch.object(authority.j1_forward_io, "ROOT_UID", source.stat().st_uid),
            ):
                with self.assertRaisesRegex(Refusal, "runtime_unexpected_file"):
                    authority._runtime_inventory(state, False, time.monotonic() + 10)
                extra.unlink()
                source.chmod(0o644)
                source.unlink()
                with self.assertRaisesRegex(Refusal, "runtime_missing_file"):
                    authority._runtime_inventory(state, False, time.monotonic() + 10)

    def test_library_mapping_requires_accepted_inode_and_both_sqlite_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            extension, library = root / "_sqlite3.test.so", root / "libsqlite3.so.0"
            extension.write_bytes(b"extension")
            library.write_bytes(b"library")
            mapping = b"".join(
                (f"100-200 r-xp 0 00:00 {path.stat().st_ino} {path}\n").encode()
                for path in (extension, library)
            )
            binding = {"module": "autostop_manager.automation_daemon", "runtime_files": []}
            with (
                patch.object(authority, "_proc", return_value=mapping),
                patch.object(authority.os, "major", return_value=0, create=True),
                patch.object(authority.os, "minor", return_value=0, create=True),
                patch.object(
                    authority,
                    "read_protected",
                    side_effect=lambda path, **kwargs: path.read_bytes(),
                ),
            ):
                self.assertEqual(2, len(authority._libraries("42", binding, {})))
                with patch.object(
                    authority,
                    "_proc",
                    return_value=mapping.replace(str(library.stat().st_ino).encode(), b"0"),
                ):
                    with self.assertRaisesRegex(Refusal, "mapping_identity_changed"):
                        authority._libraries("42", binding, {})
                with patch.object(
                    authority, "_proc", return_value=mapping.splitlines(keepends=True)[0]
                ):
                    with self.assertRaisesRegex(Refusal, "sqlite_not_ready"):
                        authority._libraries("42", binding, {})

    def test_j1_library_must_be_explicitly_accepted_and_use_accepted_loader(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            library = Path(temporary) / "libsqlite3.so.0"
            library.write_bytes(b"library")
            mapping = (f"100-200 r-xp 0 00:00 {library.stat().st_ino} {library}\n").encode()
            binding = {"module": "autostop_manager.j1_research", "runtime_files": []}
            with (
                patch.object(authority, "_proc", return_value=mapping),
                patch.object(authority.os, "major", return_value=0, create=True),
                patch.object(authority.os, "minor", return_value=0, create=True),
                patch.object(
                    authority,
                    "read_protected",
                    side_effect=lambda path, **kwargs: path.read_bytes(),
                ),
            ):
                with self.assertRaisesRegex(Refusal, "sqlite_loader_changed"):
                    authority._libraries("42", binding, {})
                # Loader validation uses POSIX separators on the real Linux target.
                with patch.object(
                    authority.Path,
                    "parent",
                    new_callable=lambda: property(lambda self: Path("/accepted")),
                ):
                    with self.assertRaisesRegex(Refusal, "sqlite_not_accepted"):
                        authority._libraries(
                            "42", binding, {"LD_LIBRARY_PATH": str(Path("/accepted"))}
                        )


if __name__ == "__main__":
    unittest.main()
