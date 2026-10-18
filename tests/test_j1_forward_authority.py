from __future__ import annotations

import stat
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
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
