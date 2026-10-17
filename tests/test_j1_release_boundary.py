from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import check_automation_center_release as release_check
from scripts import j1_release_boundary as boundary

ROOT = Path(__file__).resolve().parents[1]


class J1ReleaseBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.cleanup)
        self.crm = self.repository("crm")
        self.manager = self.repository("manager")
        self.manager_state = boundary.checkout(self.manager)
        self.constants = patch.multiple(
            boundary,
            MANAGER_REVISION=self.manager_state["revision"],
            MANAGER_TREE=self.manager_state["tree"],
            HOST=boundary.socket.gethostname(),
        )
        self.constants.start()
        self.addCleanup(self.constants.stop)

    def cleanup(self) -> None:
        for folder, _, files in os.walk(self.root):
            for name in files:
                Path(folder, name).chmod(0o600)
        self.temporary.cleanup()

    def repository(self, name: str) -> Path:
        path = self.root / name
        path.mkdir()
        (path / "README.md").write_bytes(b"fixture\n")
        package = path / "autostop_manager"
        package.mkdir()
        # Importing this fixture is an error. Preparation must inspect bytes.
        (package / "__init__.py").write_bytes(b"raise RuntimeError('application imported')\n")
        boundary.git(path, "init", "--quiet")
        boundary.git(path, "add", ".")
        boundary.git(
            path,
            "-c",
            "user.name=AutoStop Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        )
        return path

    def preparation(self) -> tuple[Path, str, dict]:
        result = boundary.prepare(self.crm, self.manager, self.root / "prepared")
        path = Path(result["receipt"])
        return path, result["sha256"], boundary.document(path, result["sha256"])

    def test_prepare_does_not_source_environment_import_apps_or_touch_live_assets(self) -> None:
        sentinel = self.root / "environment-executed"
        (self.crm / ".git" / "info" / "exclude").write_text(".env\n", encoding="utf-8")
        (self.crm / ".env").write_text(f"touch '{sentinel}'\n", encoding="utf-8")
        live = self.root / "live"
        live.mkdir()
        for name in ("current", "unit", "env", "network", "catalog", "database", "old-archive"):
            (live / name).write_bytes(name.encode())
        before = {path.name: path.read_bytes() for path in live.iterdir()}
        path, expected, value = self.preparation()
        self.assertFalse(sentinel.exists())
        self.assertEqual(before, {path.name: path.read_bytes() for path in live.iterdir()})
        self.assertEqual(value, boundary.verify_preparation(path, expected, self.crm))
        self.assertEqual({"manager", "work-telegram", "personal-telegram"}, set(value["sources"]))
        for source in value["sources"].values():
            root = Path(source["path"])
            self.assertEqual(b"fixture\n", (root / "README.md").read_bytes())
            self.assertTrue((root / "data").is_dir())
            if os.name == "posix":
                self.assertEqual(0, stat.S_IMODE(root.stat().st_mode) & 0o022)
            else:
                self.assertTrue(os.access(root, os.R_OK))

    def test_failed_prepare_never_cleans_or_overwrites_an_existing_attempt(self) -> None:
        path, expected, _ = self.preparation()
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            boundary.prepare(self.crm, self.manager, path.parent)
        self.assertEqual(before, path.read_bytes())
        boundary.verify_preparation(path, expected, self.crm)

    def test_source_inventory_content_revision_and_receipt_tampering_are_rejected(self) -> None:
        path, expected, value = self.preparation()
        root = Path(value["sources"]["manager"]["path"])
        for name, replacement in (
            ("README.md", b"changed"),
            ("REVISION", b"unknown"),
            ("MANIFEST.files0", b"unknown"),
            ("MANIFEST.sha256", b"unknown"),
        ):
            with self.subTest(name=name):
                original = (root / name).read_bytes()
                (root / name).write_bytes(replacement)
                with self.assertRaises(boundary.BoundaryError):
                    boundary.verify_preparation(path, expected, self.crm)
                (root / name).write_bytes(original)
        extra = root / "new-startup-hook.py"
        extra.write_bytes(b"extra")
        with self.assertRaisesRegex(boundary.BoundaryError, "unknown_source_file"):
            boundary.verify_preparation(path, expected, self.crm)
        extra.unlink()
        with self.assertRaisesRegex(boundary.BoundaryError, "hash_mismatch"):
            boundary.verify_preparation(path, "0" * 64, self.crm)

    def test_source_identity_substitution_and_crm_drift_are_rejected(self) -> None:
        path, expected, value = self.preparation()
        root = Path(value["sources"]["manager"]["path"])
        moved = root.with_name("moved-manager")
        root.rename(moved)
        shutil.copytree(moved, root)
        with self.assertRaisesRegex(boundary.BoundaryError, "source_identity_changed"):
            boundary.verify_preparation(path, expected, self.crm)
        (self.crm / "README.md").write_bytes(b"dirty")
        with self.assertRaisesRegex(boundary.BoundaryError, "clean_checkout_required"):
            boundary.verify_preparation(path, expected, self.crm)

    def test_rehashed_forged_preparation_still_requires_accepted_git_blobs(self) -> None:
        path, _, value = self.preparation()
        source = value["sources"]["manager"]
        root = Path(source["path"])
        (root / "README.md").write_bytes(b"arbitrary replacement")
        manifest = root / "MANIFEST.sha256"
        names = [line.split("  ./", 1)[1] for line in manifest.read_text().splitlines()]
        raw = "".join(f"{boundary.file_hash(root / name)}  ./{name}\n" for name in names).encode()
        manifest.write_bytes(raw)
        source["manifest_sha256"] = boundary.digest(raw)
        path.chmod(0o600)
        replacement = boundary.canonical(value)
        path.write_bytes(replacement)
        with self.assertRaisesRegex(boundary.BoundaryError, "accepted_git_blob_mismatch"):
            boundary.verify_preparation(path, boundary.digest(replacement), self.crm)

    def test_unaccepted_manager_is_rejected_before_output_creation(self) -> None:
        with patch.object(boundary, "MANAGER_TREE", "0" * 40):
            with self.assertRaisesRegex(boundary.BoundaryError, "accepted_manager_required"):
                boundary.prepare(self.crm, self.manager, self.root / "denied")
        self.assertFalse((self.root / "denied").exists())

    def test_prepared_telegram_readback_checks_exact_root_and_full_revision(self) -> None:
        target = self.root / "prepared-work"
        target.mkdir()
        revision = boundary.MANAGER_REVISION
        (target / "REVISION").write_text(revision + "\n", encoding="utf-8")
        link = SimpleNamespace(is_symlink=lambda: True, resolve=lambda **_: target)
        owner = SimpleNamespace(
            lstat=lambda: SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=0)
        )
        raw = boundary.canonical(
            {
                "ok": True,
                "transport_ready": True,
                "owner_notification_configured": True,
                "inbound_enabled": False,
                "account": {"name": "private"},
            }
        )
        with patch.object(release_check.os, "read", return_value=raw):
            result = release_check.validate_telegram(
                expect_inbound=False,
                expected_revision=revision,
                release_link=link,
                owner_config=owner,
                expected_root=target,
            )
            self.assertTrue(result["release_revision_verified"])
            self.assertNotIn("private", str(result))
            (target / "REVISION").write_text("0" * 40, encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "revision_mismatch"):
                release_check.validate_telegram(
                    expect_inbound=False,
                    expected_revision=revision,
                    release_link=link,
                    owner_config=owner,
                    expected_root=target,
                )
            with self.assertRaisesRegex(RuntimeError, "link_invalid"):
                release_check.validate_telegram(
                    expect_inbound=False,
                    expected_revision=revision,
                    release_link=link,
                    owner_config=owner,
                    expected_root=self.manager,
                )

    def test_parent_traversal_and_duplicate_json_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(boundary.BoundaryError, "absolute_path_required"):
            boundary.physical(self.root / ".." / "escape")
        path = self.root / "duplicate.json"
        path.write_text('{"accepted":false,"accepted":true}', encoding="utf-8")
        with self.assertRaisesRegex(boundary.BoundaryError, "duplicate_json_key"):
            boundary.document(path)
        path.write_text('{"value":NaN}', encoding="utf-8")
        with self.assertRaisesRegex(boundary.BoundaryError, "nonfinite_json_number"):
            boundary.document(path)

    def test_rollback_denies_armed_pending_and_malformed_state_without_reading_database(
        self,
    ) -> None:
        state = self.root / "state"
        state.mkdir()
        database = self.root / "research.sqlite3"
        database.write_bytes(b"new research and budget facts")
        with patch.object(boundary, "GUARD_STATE", state), patch.object(boundary, "protected"):
            self.assertTrue(boundary.rollback_allowed())
            for name in ("armed.json", "arming-pending.json"):
                (state / name).write_bytes(b"malformed or unknown")
                self.assertFalse(boundary.rollback_allowed())
                self.assertEqual(b"new research and budget facts", database.read_bytes())
                (state / name).unlink()
            with patch.object(boundary, "protected", side_effect=PermissionError("unknown")):
                with self.assertRaises(PermissionError):
                    boundary.rollback_allowed()

    def test_missing_preclosure_refuses_before_app_or_service_operations(self) -> None:
        path, expected, _ = self.preparation()
        with (
            patch.object(boundary.sys, "platform", "linux"),
            patch.object(boundary.os, "geteuid", return_value=0, create=True),
        ):
            with (
                patch.object(boundary, "protected"),
                patch.object(boundary.subprocess, "run", wraps=subprocess.run) as run,
            ):
                with self.assertRaises(FileNotFoundError):
                    boundary.verify_closure(path, expected, self.crm)
                self.assertTrue(all(call.args[0][0] == "git" for call in run.call_args_list))
        self.assertFalse((path.parent / "handoff.json").exists())

    def test_cold_wake_and_incomplete_previous_role_set_fail_before_systemctl(self) -> None:
        path, expected, value = self.preparation()
        closed = {
            "schema": "autostop.crm.j1-closure-acceptance.v1",
            "scope": boundary.SCOPE,
            "attempt_id": value["attempt_id"],
            "preparation_sha256": expected,
            "previous_native_active": dict.fromkeys(boundary.NATIVE_UNITS, True),
            "daemon_mode": "accepted_cold",
        }
        closure = path.parent / "closure-acceptance.json"
        closure.write_bytes(boundary.canonical(closed))
        with (
            patch.object(boundary.sys, "platform", "linux"),
            patch.object(boundary.os, "geteuid", return_value=0, create=True),
            patch.object(boundary, "protected"),
        ):
            with self.assertRaisesRegex(boundary.BoundaryError, "cold_bootstrap_unsupported"):
                boundary.verify_closure(path, expected, self.crm)
            del closed["previous_native_active"]["autostop-telegram.service"]
            closure.write_bytes(boundary.canonical(closed))
            with self.assertRaisesRegex(boundary.BoundaryError, "native_desired_states_required"):
                boundary.verify_closure(path, expected, self.crm)

    def test_shell_dispatch_and_effect_order_are_explicit(self) -> None:
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        before_env = script[: script.index('if [[ -f "$ROOT_DIR/.env" ]]')]
        self.assertIn('"${1:-}" == "--prepare-j1"', before_env)
        self.assertIn("verify-closure", before_env)
        prepare = script[
            script.index("prepare_guarded_j1_units() {") : script.index("admit_j1_candidate() {")
        ]
        self.assertLess(
            prepare.index("verify-closure"),
            prepare.index('activate_manager_snapshot "$manager_release_dir"'),
        )
        self.assertNotIn("deploy_telegram_bridge.sh", script)
        self.assertNotIn('run_release "$wake_installer"', script)
        self.assertNotIn("/opt/autostop-work-telegram-runtimes/$manager_revision", script)
        self.assertNotIn("install-telegram-bridge.sh", script)
        rollback = script[script.index("rollback_release() {") : script.index("\non_exit() {")]
        self.assertLess(
            rollback.index('install -D -m 600 /dev/null "$MAINTENANCE_MARKER_HOST"'),
            rollback.index("systemctl stop"),
        )
        for action in (
            "restore-crm-changed",
            'activate_manager_snapshot "$previous_manager_dir"',
            "restore_j1_worker_state",
            'rm -f "$MAINTENANCE_MARKER_HOST"',
        ):
            self.assertNotIn(action, rollback)

    def test_file_backed_closure_checks_all_roles_dependencies_and_loaded_fences(self) -> None:
        # Files and Git are real. Only host ownership, runtime-link observations
        # and systemctl output are synthetic; this is not native acceptance.
        (self.crm / "scripts").mkdir()
        (self.crm / "scripts/j1_work_duty.py").write_bytes(
            b"# Synthetic controller, not executed.\n"
        )
        boundary.git(self.crm, "add", ".")
        boundary.git(
            self.crm,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "controller fixture",
        )
        for name in boundary.DEPENDENCY_INPUTS:
            target = self.manager / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"synthetic dependency contract\n")
        boundary.git(self.manager, "add", ".")
        boundary.git(
            self.manager,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "dependency fixture",
        )
        state = boundary.checkout(self.manager)
        with (
            ExitStack() as stack,
            patch.object(boundary, "MANAGER_REVISION", state["revision"]),
            patch.object(boundary, "MANAGER_TREE", state["tree"]),
        ):
            path, expected, value = self.preparation()
            context = {
                "scope": boundary.SCOPE,
                "attempt_id": value["attempt_id"],
                "preparation_sha256": expected,
            }

            def proof(name: str, document: dict) -> dict:
                target = self.root / f"{name}.json"
                raw = boundary.canonical(document)
                target.write_bytes(raw)
                return {"path": str(target), "sha256": boundary.digest(raw)}

            def reviewed(name: str, document: dict) -> dict:
                review = proof(
                    name + "-reviewer",
                    {
                        **context,
                        "schema": "autostop.manager.j1-independent-acceptance.v3",
                        "accepted": True,
                        "accepted_refs": list(document["files"]),
                    },
                )
                return proof(
                    name,
                    {
                        **document,
                        "reviewer_manifest": review,
                        "files": [*document["files"], review],
                    },
                )

            primary = proof("primary", {"synthetic": True})
            native = dict.fromkeys(boundary.NATIVE_UNITS, True)
            baseline = proof(
                "baseline",
                {
                    **context,
                    "schema": "autostop.crm.j1-native-baseline.v1",
                    "observed_before_closure": True,
                    "native_active": native,
                    "native_enabled": {unit: bool(index % 2) for index, unit in enumerate(native)},
                },
            )
            pause = proof("pause", {**context, "runtime_duty_enabled": False, "files": [primary]})
            links = {
                "wake_config": proof(
                    "wake",
                    {
                        "instruction_root": boundary.INSTRUCTION_ROOT,
                        "instruction_revision": boundary.INSTRUCTION_REVISION,
                    },
                ),
                "thread_identity_receipt": primary,
                "daemon_identity_receipt": proof("daemon", {"synthetic": True}),
                "writer_admission_closure_receipt": proof(
                    "writers",
                    {
                        "accepted": True,
                        "files": [primary],
                        "closed_writer_classes": sorted(
                            [
                                "native_units",
                                "detached_daemons",
                                "active_turns",
                                "direct_cli",
                                "crm_gateway",
                                "containers",
                                "scheduled_triggers",
                                "browser",
                            ]
                        ),
                    },
                ),
                "bootstrap_preservation_receipt": proof(
                    "bootstrap", {"start_or_restart_performed": False}
                ),
                "browser_hold_receipt": proof("browser", {"activation_allowed": False}),
            }
            lifecycle = reviewed(
                "lifecycle",
                {
                    **context,
                    **links,
                    "schema": "autostop.manager.j1-lifecycle-eligibility.v3",
                    "accepted": True,
                    "instruction_root": boundary.INSTRUCTION_ROOT,
                    "instruction_revision": boundary.INSTRUCTION_REVISION,
                    "history_preserved": True,
                    "daemon_mode": "accepted_warm",
                    "denied_units": sorted(boundary.UNSUPPORTED_UNITS),
                    "files": list(links.values()),
                },
            )
            runtime = {}
            for key in ("venv", "model"):
                runtime[key] = self.root / key
                runtime[key].mkdir()
            dependencies = reviewed(
                "dependencies",
                {
                    **context,
                    "schema": "autostop.crm.j1-work-dependencies.v1",
                    "manager_revision": state["revision"],
                    "source_root": value["sources"]["work-telegram"]["path"],
                    "dependency_runtime_sealed": True,
                    "model_verified": True,
                    "venv": str(runtime["venv"]),
                    "model": str(runtime["model"]),
                    "source_inputs": {
                        name: boundary.file_hash(self.manager / name)
                        for name in boundary.DEPENDENCY_INPUTS
                    },
                    "files": [primary],
                },
            )
            closed = {
                **context,
                "schema": "autostop.crm.j1-closure-acceptance.v1",
                "previous_native_active": native,
                "native_baseline": baseline,
                "daemon_mode": "accepted_warm",
                "work_inbound_before": True,
                "work_duty_paused": True,
                "work_pause_receipt": pause,
                "work_dependencies": dependencies,
                "lifecycle": lifecycle,
                "detached_daemons_and_turns_closed": True,
                "direct_cli_closed": True,
                "history_preserved": True,
                "files": [primary],
            }
            closure = path.parent / "closure-acceptance.json"
            closure.write_bytes(boundary.canonical(closed))
            systemd = self.root / "systemd"
            for unit in boundary.ALL_UNITS:
                fence = systemd / (unit + ".d") / boundary.CLOSURE_DROPIN
                fence.parent.mkdir(parents=True)
                fence.write_bytes(boundary.CLOSURE_BYTES)
            overrides = {}
            calls = []
            real_run = subprocess.run

            def observed(arguments, **kwargs):
                if arguments[0] == "git":
                    return real_run(arguments, **kwargs)
                if arguments[0] == "busctl":
                    self.assertEqual(
                        arguments[1:4], ["--system", "get-property", "org.freedesktop.systemd1"]
                    )
                    self.assertEqual(arguments[-2], "org.freedesktop.systemd1.Service")
                    self.assertIn(
                        arguments[-1],
                        ("ExecStartPre", "ExecStartPost", "ExecReload", "ExecStop", "ExecStopPost"),
                    )
                    return subprocess.CompletedProcess(arguments, 0, "a(sasbttttuii) 0\n", "")
                self.assertEqual(arguments[:2], ["systemctl", "show"])
                unit = arguments[2]
                calls.append(unit)
                warm = unit == "autostop-codex-start.service"
                fields = {
                    "Id": unit,
                    "ActiveState": "active" if warm else "inactive",
                    "SubState": "exited" if warm else "dead",
                    "NeedDaemonReload": "no",
                    "ExecCondition": "{ argv[]=/usr/bin/false ; }",
                    "DropInPaths": str(systemd / (unit + ".d") / boundary.CLOSURE_DROPIN),
                }
                # PID1 omits empty Exec vectors, including with systemctl --all.
                fields.update(overrides)
                return subprocess.CompletedProcess(
                    arguments, 0, "\n".join(f"{key}={item}" for key, item in fields.items()), ""
                )

            for name, replacement in {
                "SYSTEMD_ROOT": systemd,
                "WORK_RELEASE_ROOT": path.parent,
                "WORK_VENV_LINK": SimpleNamespace(
                    is_symlink=lambda: True, resolve=lambda **_: runtime["venv"]
                ),
                "WORK_MODEL_LINK": SimpleNamespace(
                    is_symlink=lambda: True, resolve=lambda **_: runtime["model"]
                ),
            }.items():
                stack.enter_context(patch.object(boundary, name, replacement))
            stack.enter_context(patch.object(boundary, "protected", side_effect=boundary.physical))
            stack.enter_context(patch.object(boundary.sys, "platform", "linux"))
            stack.enter_context(patch.object(boundary.os, "geteuid", return_value=0, create=True))
            stack.enter_context(patch.object(boundary.subprocess, "run", side_effect=observed))
            self.assertEqual(native, boundary.verify_closure(path, expected, self.crm))
            self.assertEqual(sorted(boundary.ALL_UNITS), calls)
            for key, change, reason in (
                ("NeedDaemonReload", "yes", "loaded_native_closure_required"),
                ("ExecStartPost", "old writer hook", "loaded_native_closure_required"),
                ("ActiveState", "activating", "native_writer_not_closed"),
                ("DropInPaths", "unexpected-last.conf", "effective_closure_fence_required"),
            ):
                with self.subTest(key=key):
                    overrides[key] = change
                    with self.assertRaisesRegex(boundary.BoundaryError, reason):
                        boundary.verify_closure(path, expected, self.crm)
                    overrides.clear()
            self.assertEqual(boundary.canonical(closed), closure.read_bytes())
            self.assertFalse((path.parent / "handoff.json").exists())
            lock = self.root / "deploy.lock"
            lock.touch()
            intent_hash = boundary.prepare_duty(
                path, expected, self.crm, self.root / "maintenance", lock
            )
            intent = boundary.document(path.parent / "work-duty-intent.json", intent_hash)
            self.assertTrue(intent["inbound_enabled"])
            self.assertEqual(expected, intent["preparation_sha256"])
            self.assertEqual(
                boundary.file_hash(self.crm / "scripts/j1_work_duty.py"),
                intent["controller_sha256"],
            )
            self.assertIn(
                boundary.duty_command(intent_hash),
                (path.parent / "work-duty-restore.conf").read_text(),
            )
            self.assertFalse((path.parent / "admitted.json").exists())

    def run_shell(self, script: str) -> subprocess.CompletedProcess[str]:
        bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
        self.assertTrue(bash)
        return subprocess.run([str(bash), "-c", script], capture_output=True, text=True, timeout=20)

    def test_failed_quiet_readback_has_no_alias_unit_or_credential_effects(self) -> None:
        source = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        prepare = source[
            source.index("\nprepare_guarded_j1_units() {") : source.index(
                "\nadmit_j1_candidate() {"
            )
        ]
        trace = self.root / "effects"
        script = f"""
set -euo pipefail
trace={shlex.quote(trace.as_posix())}
PYTHON_BIN=python3 ROOT_DIR=/fixture J1_PREPARATION=/fixture/preparation.json J1_PREPARATION_SHA256=hash
MAINTENANCE_MARKER_HOST=/fixture/maintenance DEPLOY_LOCK_PATH=/fixture/lock WORK_DUTY_CONTROLLER=/fixture/restore.py
run_release() {{
  if [[ "$*" == *verify-closure* ]]; then return 17; fi
  printf '%s\\n' "$*" >> "$trace"
}}
{prepare}
prepare_guarded_j1_units
"""
        result = self.run_shell(script)
        self.assertEqual(17, result.returncode, result.stderr)
        self.assertFalse(trace.exists())

    def test_shell_admission_precedes_candidate_start_and_preserves_prepared_runtime(self) -> None:
        source = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        prepare = source[
            source.index("\nprepare_guarded_j1_units() {") : source.index(
                "\nverify_active_crm_image() {"
            )
        ]
        worker = source[
            source.index("\nactivate_j1_worker() {") : source.index("\nrestore_j1_worker_state() {")
        ]
        trace = self.root / "effects"
        candidate = self.root / "candidate"
        candidate.mkdir()
        script = f"""
set -euo pipefail
trace={shlex.quote(trace.as_posix())}
manager_release_dir={shlex.quote(candidate.as_posix())}
MANAGER_CURRENT_LINK="$manager_release_dir"
work_telegram_prepared_dir=/fixture/work personal_telegram_prepared_dir=/fixture/personal
WORK_TELEGRAM_RELEASE_LINK=/fixture/work-current
PYTHON_BIN=python3 ROOT_DIR=/fixture J1_PREPARATION=/fixture/preparation.json J1_PREPARATION_SHA256=hash
MAINTENANCE_MARKER_HOST=/fixture/maintenance DEPLOY_LOCK_PATH=/fixture/lock WORK_DUTY_CONTROLLER=/fixture/restore.py
release_id=attempt manager_revision=89 crm_revision=candidate CRM_APP_VERSION=test
release_image=sha256:fixture backup_dir=/fixture/backup J1_UNIT_NAME=autostop-j1.service
run_release() {{ printf '%s\\n' "$*" >> "$trace"; }}
snapshot_j1_worker_state() {{ printf 'snapshot-old-unit\\n' >> "$trace"; }}
activate_manager_snapshot() {{ printf 'publish-manager-alias\\n' >> "$trace"; }}
sync_manager_crm_mcp_configuration() {{ printf 'final-credentials\\n' >> "$trace"; }}
assert_release_budget() {{ :; }}
remaining_release_budget() {{ printf '480\\n'; }}
{prepare}
{worker}
admit_j1_candidate
activate_j1_worker "$manager_release_dir"
"""
        result = self.run_shell(script)
        self.assertEqual(0, result.returncode, result.stderr)
        calls = trace.read_text(encoding="utf-8")
        self.assertLess(calls.index("verify-closure"), calls.index("publish-manager-alias"))
        self.assertLess(
            calls.index("final-credentials"), calls.index("j1_release_boundary.py admit")
        )
        self.assertLess(
            calls.index("j1_release_boundary.py admit"), calls.index("systemctl enable --now")
        )
        self.assertNotIn("runtimes/89", calls)
        self.assertNotIn("deploy_telegram_bridge.sh", calls)

    def test_late_failure_keeps_new_research_and_does_not_restore_or_start_old_code(self) -> None:
        source = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        rollback = source[source.index("\nrollback_release() {") : source.index("\non_exit() {")]
        trace = self.root / "effects"
        marker = self.root / "maintenance"
        database = self.root / "research.sqlite3"
        database.write_bytes(b"new research plus current budget")
        script = f"""
set -euo pipefail
trace={shlex.quote(trace.as_posix())}
MAINTENANCE_MARKER_HOST={shlex.quote(marker.as_posix())}
SERVICE_NAME=autostopcrm
install() {{ printf 'marker\\n' >> "$trace"; : > "$MAINTENANCE_MARKER_HOST"; }}
timeout() {{ printf '%s\\n' "$*" >> "$trace"; }}
{rollback}
set +e
rollback_release 23
"""
        result = self.run_shell(script)
        self.assertEqual(23, result.returncode, result.stderr)
        self.assertTrue(marker.exists())
        self.assertEqual(b"new research plus current budget", database.read_bytes())
        calls = trace.read_text(encoding="utf-8")
        self.assertTrue(calls.startswith("marker\n"))
        self.assertIn("systemctl stop", calls)
        self.assertIn("docker compose stop", calls)
        for forbidden in ("restore", " start ", " up ", "1eaa", " tag "):
            self.assertNotIn(forbidden, calls)

    def test_runtime_duty_rpc_does_not_modify_sealed_python_source(self) -> None:
        source = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        function = source[
            source.index("\nset_work_telegram_duty() {") : source.index(
                "\nactivate_manager_automation() {"
            )
        ]
        prepared = self.root / "runtime-source"
        package = prepared / "autostop_manager"
        package.mkdir(parents=True)
        (package / "__init__.py").write_bytes(b"# Synthetic app, never Manager.\n")
        (package / "telegram_bridge.py").write_bytes(
            b"import os, sys\n"
            b"assert os.environ['PYTHONDONTWRITEBYTECODE'] == '1'\n"
            b"assert os.environ['PYTHONSAFEPATH'] == '1'\n"
            b"assert os.environ['PYTHONNOUSERSITE'] == '1'\n"
            b"assert sys.argv[1:] == ['--account', 'work', 'duty-set', '--enabled']\n"
        )
        before = {
            str(item.relative_to(prepared)): item.read_bytes()
            for item in prepared.rglob("*")
            if item.is_file()
        }
        (self.root / "admitted.json").write_bytes(b"fixture only")
        script = f"""
set -euo pipefail
unset PYTHONPYCACHEPREFIX
J1_PREPARATION={shlex.quote((self.root / "preparation.json").as_posix())}
work_telegram_prepared_dir={shlex.quote(prepared.as_posix())}
PYTHON_BIN={shlex.quote(Path(sys.executable).as_posix())}
run_release() {{
  [[ "$1" == env ]]; shift
  local -a inputs=()
  while [[ "$1" != /opt/autostop-work-telegram-venv/bin/python ]]; do
    inputs+=("$1"); shift
  done
  shift
  env "${{inputs[@]}}" "$PYTHON_BIN" "$@"
}}
{function}
set_work_telegram_duty 1 release
"""
        result = self.run_shell(script)
        self.assertEqual(0, result.returncode, result.stderr)
        after = {
            str(item.relative_to(prepared)): item.read_bytes()
            for item in prepared.rglob("*")
            if item.is_file()
        }
        self.assertEqual(before, after)

    def test_postcommit_duty_failure_cannot_enter_rollback(self) -> None:
        source = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        committed = source[
            source.index("deployment_succeeded=1\ntrap - EXIT") : source.index(
                "\nauth_rotated=0", source.index("deployment_succeeded=1\ntrap - EXIT")
            )
        ]
        for fail_rpc in (True, False):
            trace = self.root / f"postcommit-{fail_rpc}"
            script = f"""
set -euo pipefail
trace={shlex.quote(trace.as_posix())}
work_telegram_inbound_before=1 manager_revision=89 automation_telegram_status=fixture
WORK_DUTY_CONTROLLER=/fixture/restore.py work_duty_intent_sha256=hash DEPLOY_LOCK_FD=9
trap 'printf rollback >> "$trace"' EXIT
run_release() {{ printf '%s\\n' "$*" >> "$trace"; [[ "$*" != *' restore '* ]] || return {3 if fail_rpc else 0}; }}
flock() {{ printf 'unlock\\n' >> "$trace"; }}
capture_safe_work_telegram_status() {{ printf 'readback\\n' >> "$trace"; return 3; }}
{committed}
"""
            with self.subTest(fail_rpc=fail_rpc):
                result = self.run_shell(script)
                self.assertEqual(3, result.returncode, result.stderr)
                self.assertIn("release committed; Work duty", result.stderr)
                self.assertNotIn("rollback", trace.read_text())
                self.assertIn(" commit --intent-sha256 hash\nunlock\n", trace.read_text())


class J1NativeProbeContextTests(unittest.TestCase):
    """File-backed /proc observations, without Manager imports or SQLite opens."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.process = self.root / "proc" / "321"
        self.process.mkdir(parents=True)
        runtime = self.root / "runtime"
        runtime.mkdir()
        self.interpreter = runtime / "python3.12"
        self.interpreter.write_bytes(b"synthetic interpreter, never executed")
        self.libraries = [runtime / "_sqlite3.fixture.so", runtime / "libsqlite3.so.0.8.6"]
        for library in self.libraries:
            library.write_bytes(b"synthetic accepted runtime payload")
        source = self.root / "manager"
        source.mkdir()
        self.environment = {
            "PYTHONPATH": str(source),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONSAFEPATH": "1",
            "PYTHONNOUSERSITE": "1",
            "AUTOSTOP_MANAGER_ENV_FILE": "/dev/null",
            "AUTOSTOP_J1_CACHE_DIR": "/var/cache/autostop-j1",
            "LD_LIBRARY_PATH": str(runtime),
        }
        self.binding = {
            "interpreter": str(self.interpreter),
            "interpreter_realpath": str(self.interpreter),
            "interpreter_identity": [
                self.interpreter.stat().st_dev,
                self.interpreter.stat().st_ino,
            ],
            "interpreter_sha256": boundary.file_hash(self.interpreter),
            "cwd": str(source),
            "source_root": str(source),
            "selector": "/opt/manager/current",
            "runtime_files": [
                {"path": str(path), "sha256": boundary.file_hash(path)} for path in self.libraries
            ],
        }
        self.write_environment()
        (self.process / "stat").write_bytes(
            b"321 (worker) " + b" ".join([b"R", *([b"0"] * 18), b"1234", b"0"])
        )
        (self.process / "cgroup").write_bytes(b"0::/system.slice/autostop-j1.service\n")
        (self.process / "cmdline").write_bytes(
            b"\0".join(
                item.encode()
                for item in (
                    str(self.interpreter),
                    "-P",
                    "-m",
                    "autostop_manager.j1_research",
                    "worker",
                )
            )
            + b"\0"
        )
        (self.process / "maps").write_text(
            "\n".join(f"000-fff r-xp 00000000 00:00 1 {path}" for path in self.libraries)
        )
        self.fields = {
            "MainPID": "321",
            "ActiveState": "active",
            "SubState": "running",
            "ControlGroup": "/system.slice/autostop-j1.service",
        }
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(boundary, "PROC_ROOT", self.root / "proc"))
        stack.enter_context(patch.object(boundary, "protected", side_effect=boundary.physical))
        self.links = {
            self.process / "exe": str(self.interpreter),
            self.process / "cwd": str(source),
        }
        stack.enter_context(
            patch.object(
                boundary.os,
                "readlink",
                side_effect=lambda path: self.links[Path(path)],
            )
        )
        self.command = stack.enter_context(
            patch.object(boundary.subprocess, "run", side_effect=self.show)
        )
        self.guard = self.root / "guard" / "guard.py"
        self.guard.parent.mkdir()
        self.guard.write_bytes(b"synthetic guard, never executed")
        self.guard_python = runtime / "guard-python"
        self.guard_python.write_bytes(b"synthetic system interpreter, never executed")
        stack.enter_context(
            patch.multiple(
                boundary,
                GUARD_ROOT=self.guard.parent,
                GUARD_SHA256=boundary.file_hash(self.guard),
                GUARD_PYTHON=self.guard_python,
            )
        )
        self.clock = 0.0
        self.sleeps = []
        self.on_sleep = lambda: None
        stack.enter_context(
            patch.object(boundary.time, "monotonic", side_effect=lambda: self.clock)
        )
        stack.enter_context(patch.object(boundary.time, "sleep", side_effect=self.advance))

    def advance(self, seconds):
        self.sleeps.append(seconds)
        self.clock += seconds
        self.on_sleep()

    def guard_starting(self):
        self.environment["PYTHONPATH"] = self.binding["selector"]
        self.write_environment()
        self.links[self.process / "exe"] = str(self.guard_python)
        arguments = (
            str(self.guard_python),
            "-I",
            "-S",
            str(self.guard),
            "launch",
            "--policy-sha256",
            "a" * 64,
            "--unit",
            "autostop-j1.service",
        )
        (self.process / "cmdline").write_bytes(
            b"\0".join(item.encode() for item in arguments) + b"\0"
        )
        (self.process / "maps").write_bytes(b"")

    def worker_starting(self, *, sqlite_loaded):
        self.environment["PYTHONPATH"] = self.binding["source_root"]
        self.write_environment()
        self.links[self.process / "exe"] = str(self.interpreter)
        (self.process / "cmdline").write_bytes(
            b"\0".join(boundary.native_worker_argv(self.binding)) + b"\0"
        )
        (self.process / "maps").write_text(
            "\n".join(f"000-fff r-xp 00000000 00:00 1 {path}" for path in self.libraries)
            if sqlite_loaded
            else ""
        )

    def write_environment(self) -> None:
        (self.process / "environ").write_bytes(
            b"\0".join(f"{key}={value}".encode() for key, value in self.environment.items()) + b"\0"
        )
        self.binding["stable_environment_sha256"] = boundary.digest(
            boundary.canonical({**self.environment, "PYTHONPATH": self.binding["selector"]})
        )

    def show(self, arguments, **kwargs):
        self.assertGreater(kwargs["timeout"], 0)
        self.assertLessEqual(kwargs["timeout"], 10)
        self.assertEqual(
            [
                "systemctl",
                "show",
                "autostop-j1.service",
                "--property=MainPID,ActiveState,SubState,ControlGroup",
                "--no-pager",
            ],
            arguments,
        )
        return subprocess.CompletedProcess(
            arguments, 0, "\n".join(f"{key}={value}" for key, value in self.fields.items()), ""
        )

    def test_context_preserves_accepted_loader_and_source_without_application_launch(self):
        pid, start, environment, libraries = boundary.native_probe_context(self.binding)
        self.assertEqual(("321", "1234"), (pid, start))
        self.assertEqual(self.environment, environment)
        self.assertEqual(
            sorted(self.binding["runtime_files"], key=lambda item: item["path"]), libraries
        )
        self.assertEqual(1, self.command.call_count)

    def test_missing_native_sqlite_loader_never_falls_back_to_system_engine(self):
        self.environment.pop("LD_LIBRARY_PATH")
        self.write_environment()
        with self.assertRaisesRegex(boundary.BoundaryError, "native_sqlite_loader_path_required"):
            boundary.native_probe_context(self.binding)
        self.assertEqual(1, self.command.call_count)

    def test_database_filename_cannot_replace_native_cache_directory(self):
        self.environment["AUTOSTOP_J1_CACHE_DIR"] = "/var/cache/autostop-j1/research.sqlite3"
        self.write_environment()
        with self.assertRaisesRegex(boundary.BoundaryError, "native_j1_environment_changed"):
            boundary.native_probe_context(self.binding)
        self.assertEqual(1, self.command.call_count)

    def test_readiness_waits_for_exact_guard_then_worker_sqlite_import(self):
        self.guard_starting()
        self.on_sleep = lambda: self.worker_starting(sqlite_loaded=len(self.sleeps) >= 2)
        self.assertEqual(("321", "1234"), boundary.wait_native_probe_ready(self.binding, "a" * 64))
        self.assertEqual([0.1, 0.1], self.sleeps)
        self.assertEqual(5, self.command.call_count)
        # The ordinary validator remains strict and is used before SQL launch.
        self.assertEqual(("321", "1234"), boundary.native_probe_context(self.binding)[:2])

    def test_readiness_handles_only_forward_exec_between_proc_reads(self):
        self.guard_starting()
        transitioned = False

        def readlink(path):
            nonlocal transitioned
            if not transitioned and Path(path) == self.process / "exe":
                self.worker_starting(sqlite_loaded=False)
                transitioned = True
            return self.links[Path(path)]

        # The guard argv was read first; execve occurs before reading /proc/exe.
        with patch.object(boundary.os, "readlink", side_effect=readlink):
            self.on_sleep = lambda: self.worker_starting(sqlite_loaded=True)
            self.assertEqual(
                ("321", "1234"), boundary.wait_native_probe_ready(self.binding, "a" * 64)
            )
        self.assertEqual([0.1], self.sleeps)

    def test_readiness_timeout_is_fixed_and_never_starts_an_application(self):
        for stage in ("guard", "worker"):
            with self.subTest(stage=stage):
                self.clock = 0.0
                self.sleeps.clear()
                if stage == "guard":
                    self.guard_starting()
                else:
                    self.worker_starting(sqlite_loaded=False)
                with self.assertRaisesRegex(boundary.BoundaryError, "native_j1_readiness_timeout"):
                    boundary.wait_native_probe_ready(self.binding, "a" * 64)
                self.assertAlmostEqual(20.0, self.clock)
                self.assertTrue(all(0 < duration <= 0.1 for duration in self.sleeps))

    def test_readiness_refuses_foreign_argv_and_wrong_policy_without_wait(self):
        self.guard_starting()
        expected = (self.process / "cmdline").read_bytes()
        for value in (
            expected.replace(b"launch", b"check"),
            expected.replace(b"a" * 64, b"b" * 64),
            expected.replace(b"autostop-j1.service", b"other.service"),
            b"/usr/bin/python3\0-c\0pass\0",
        ):
            with self.subTest(argv=value):
                (self.process / "cmdline").write_bytes(value)
                self.command.reset_mock()
                with self.assertRaisesRegex(boundary.BoundaryError, "native_j1_entrypoint_changed"):
                    boundary.wait_native_probe_ready(self.binding, "a" * 64)
                self.assertEqual(1, self.command.call_count)
                self.assertEqual([], self.sleeps)

    def test_readiness_refuses_changed_guard_bytes_before_wait(self):
        self.guard_starting()
        self.guard.write_bytes(b"substituted guard")
        with self.assertRaisesRegex(boundary.BoundaryError, "accepted_guard_package_required"):
            boundary.wait_native_probe_ready(self.binding, "a" * 64)
        self.assertEqual(1, self.command.call_count)
        self.assertEqual([], self.sleeps)

    def test_readiness_refuses_replacement_pid_or_reused_pid(self):
        for replacement in ("pid", "start"):
            with self.subTest(replacement=replacement):
                self.fields["MainPID"] = "321"
                (self.process / "stat").write_bytes(
                    b"321 (guard) " + b" ".join([b"R", *([b"0"] * 18), b"1234", b"0"])
                )
                self.guard_starting()
                self.sleeps.clear()
                self.command.reset_mock()

                def replace():
                    if replacement == "pid":
                        shutil.copytree(self.process, self.process.parent / "322")
                        self.fields["MainPID"] = "322"
                    else:
                        path = self.process / "stat"
                        path.write_bytes(path.read_bytes().replace(b"1234", b"1235"))

                self.on_sleep = replace
                with self.assertRaisesRegex(
                    boundary.BoundaryError, "native_j1_process_changed_during_start"
                ):
                    boundary.wait_native_probe_ready(self.binding, "a" * 64)
                self.assertEqual([0.1], self.sleeps)
                self.assertEqual(2, self.command.call_count)

    def test_readiness_refuses_inactive_or_wrong_guard_runtime_and_environment(self):
        self.guard_starting()
        self.fields["ActiveState"] = "inactive"
        with self.assertRaisesRegex(boundary.BoundaryError, "admitted_j1_process_required"):
            boundary.wait_native_probe_ready(self.binding, "a" * 64)
        self.fields["ActiveState"] = "active"
        self.links[self.process / "exe"] = str(self.interpreter)
        with self.assertRaisesRegex(boundary.BoundaryError, "native_j1_guard_runtime_changed"):
            boundary.wait_native_probe_ready(self.binding, "a" * 64)
        self.links[self.process / "exe"] = str(self.guard_python)
        (self.process / "environ").write_bytes(b"PYTHONPATH=/unknown\0")
        with self.assertRaisesRegex(boundary.BoundaryError, "native_j1_guard_environment_changed"):
            boundary.wait_native_probe_ready(self.binding, "a" * 64)
        self.assertEqual([], self.sleeps)

    def test_only_accepted_partial_sqlite_maps_are_transient_after_full_worker_validation(self):
        self.worker_starting(sqlite_loaded=False)
        with self.assertRaisesRegex(boundary.BoundaryError, "native_sqlite_mapping_required"):
            boundary.native_probe_context(self.binding)
        (self.process / "maps").write_text(f"000-fff r-xp 00000000 00:00 1 {self.libraries[0]}")
        with self.assertRaisesRegex(boundary.BoundaryError, "native_sqlite_mapping_required"):
            boundary.native_probe_context(self.binding)
        self.on_sleep = lambda: self.worker_starting(sqlite_loaded=True)
        self.assertEqual(("321", "1234"), boundary.wait_native_probe_ready(self.binding, "a" * 64))
        self.assertEqual([0.1], self.sleeps)
        self.sleeps.clear()
        for path in (self.root / "_sqlite3.unknown.so", str(self.libraries[0]) + " (deleted)"):
            (self.process / "maps").write_text(f"000-fff r-xp 00000000 00:00 1 {path}")
            with self.assertRaisesRegex(
                boundary.BoundaryError, "native_sqlite_not_in_accepted_runtime"
            ):
                boundary.wait_native_probe_ready(self.binding, "a" * 64)
        (self.process / "maps").write_bytes(b"")
        self.libraries[0].write_bytes(b"changed runtime while importing")
        with self.assertRaisesRegex(boundary.BoundaryError, "native_runtime_file_changed"):
            boundary.wait_native_probe_ready(self.binding, "a" * 64)
        self.assertEqual([], self.sleeps)

    def test_partial_sqlite_library_with_wrong_loader_path_refuses_without_wait(self):
        self.worker_starting(sqlite_loaded=False)
        (self.process / "maps").write_text(f"000-fff r-xp 00000000 00:00 1 {self.libraries[1]}")
        self.environment.pop("LD_LIBRARY_PATH")
        self.write_environment()
        with self.assertRaisesRegex(boundary.BoundaryError, "native_sqlite_loader_path_required"):
            boundary.wait_native_probe_ready(self.binding, "a" * 64)
        self.assertEqual([], self.sleeps)

    def test_worker_cannot_return_to_guard_while_waiting_for_sqlite(self):
        self.worker_starting(sqlite_loaded=False)
        self.on_sleep = self.guard_starting
        with self.assertRaisesRegex(boundary.BoundaryError, "native_j1_entrypoint_changed"):
            boundary.wait_native_probe_ready(self.binding, "a" * 64)
        self.assertEqual([0.1], self.sleeps)

    def test_readiness_does_not_retry_unknown_process_read_errors(self):
        (self.process / "cmdline").unlink()
        with self.assertRaises(FileNotFoundError):
            boundary.wait_native_probe_ready(self.binding, "a" * 64)
        self.assertEqual(1, self.command.call_count)
        self.assertEqual([], self.sleeps)

    def test_changed_or_unaccepted_sqlite_library_refuses_before_application_launch(self):
        self.binding["runtime_files"].pop()
        with self.assertRaisesRegex(
            boundary.BoundaryError, "native_sqlite_not_in_accepted_runtime"
        ):
            boundary.native_probe_context(self.binding)
        self.binding["runtime_files"] = [
            {"path": str(path), "sha256": boundary.file_hash(path)} for path in self.libraries
        ]
        self.libraries[-1].write_bytes(b"old or substituted SQLite")
        with self.assertRaisesRegex(boundary.BoundaryError, "native_runtime_file_changed"):
            boundary.native_probe_context(self.binding)

    def test_wrong_process_cgroup_or_environment_refuses_before_application_launch(self):
        self.fields["SubState"] = "dead"
        with self.assertRaisesRegex(boundary.BoundaryError, "admitted_j1_process_required"):
            boundary.native_probe_context(self.binding)
        self.fields["SubState"] = "running"
        (self.process / "cgroup").write_bytes(b"0::/system.slice/other.service\n")
        with self.assertRaisesRegex(boundary.BoundaryError, "native_j1_cgroup_required"):
            boundary.native_probe_context(self.binding)
        (self.process / "cgroup").write_bytes(b"0::/system.slice/autostop-j1.service\n")
        self.environment["AUTOSTOP_MANAGER_ENV_FILE"] = "/opt/AutostopManager/.env"
        self.write_environment()
        with self.assertRaisesRegex(boundary.BoundaryError, "native_j1_environment_changed"):
            boundary.native_probe_context(self.binding)

    def test_probe_reuses_only_hash_verified_pure_guard_validators(self):
        guard_root = self.root / "pure-guard"
        guard_root.mkdir()
        raw = b"""import sys
assert sys.dont_write_bytecode
class Deny(Exception): pass
calls = []
def verify_source(binding, index):
    assert index == {"fixture": True}
    calls.append("source")
def verify_runtime(binding, scope):
    assert scope == "production_quiet_window"
    calls.append("runtime")
def verify_environment(environment, binding):
    assert environment["PYTHONPATH"] == binding["selector"]
    calls.append("environment")
def verify_units(binding, unit, policy_hash):
    assert calls == ["source", "runtime", "environment"]
    assert unit == "autostop-j1.service" and policy_hash == "a" * 64
def authorize(*args): raise AssertionError("not a real service context")
def check(*args): raise AssertionError("not a real service context")
def launch(*args): raise AssertionError("probe must not launch a writer")
"""
        guard_path = guard_root / "guard.py"
        guard_path.write_bytes(raw)
        index = boundary.canonical({"fixture": True})
        (guard_root / "source-index.json").write_bytes(index)
        previous = sys.dont_write_bytecode
        with patch.multiple(
            boundary,
            GUARD_ROOT=guard_root,
            GUARD_SHA256=boundary.digest(raw),
            INDEX_SHA256=boundary.digest(index),
        ):
            boundary.verify_native_probe_runtime(self.binding, self.environment, "a" * 64)
            self.assertEqual(previous, sys.dont_write_bytecode)
            self.assertFalse((guard_root / "__pycache__").exists())
            guard_path.write_bytes(b"raise AssertionError('untrusted guard code')")
            with self.assertRaisesRegex(boundary.BoundaryError, "accepted_guard_package_required"):
                boundary.verify_native_probe_runtime(self.binding, self.environment, "a" * 64)


if __name__ == "__main__":
    unittest.main()
