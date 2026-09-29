"""The scheduler revision repair preserves config bytes and owned holds."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if sys.platform == "win32":
    raise unittest.SkipTest("Unix release locking requires fcntl.")

from scripts import reconcile_automation_crm_revision as revision_module
from scripts.deploy_crm_only import CrmOnlyRelease, ReleaseError
from scripts.reconcile_automation_crm_revision import (
    AutomationRevisionReconciler,
    atomic_replace_exact,
    secure_snapshot,
    updated_identity,
)

OLD = "a" * 40
NEW = "b" * 40
MANAGER = "c" * 40


def control_bytes() -> bytes:
    return (
        b"# preserve this comment\n"
        + b"AUTOSTOP_MANAGER_REVISION="
        + MANAGER.encode()
        + b"\nAUTOSTOP_AUTOMATION_CRM_REVISION="
        + OLD.encode()
        + b"\nSECRET_KEEP_BYTES='literal value'\n"
    )


class FakeRelease:
    def __init__(self, root: Path, config: Path) -> None:
        self.sha = NEW
        self.release_id = "synthetic-reconcile"
        self.hold_key = "synthetic-reconcile:hold"
        self.lock_path = root / "deploy.lock"
        self.config = config
        self.held = False
        self.duty_enabled = True
        self.scheduler_stopped = False
        self.telegram_changed = False
        self.events: list[str] = []
        self.fail_release_response = False
        self.foreign_during_enable = False
        self.ambiguous_probe = False

    def automation(self, operation: str) -> None:
        self.events.append(operation)
        self.held = operation == "hold"
        if operation == "release-hold" and self.fail_release_response:
            raise ReleaseError("response lost after release")

    def service_pid(self, name: str) -> str:
        if self.scheduler_stopped:
            raise ReleaseError("MainPID=0")
        return "123"

    def run(self, *argv, **kwargs):
        if len(argv) == 4 and argv[1] in {"--disable", "--enable"}:
            assert argv[2:] == (
                "--expected-release-dir",
                str(self.config.parent / "synthetic-telegram-release"),
            )
            self.duty_enabled = argv[1] == "--enable"
            self.events.append("duty_" + argv[1][2:])
            if argv[1] == "--enable" and self.foreign_during_enable:
                self.config.write_bytes(b"foreign during enable")
            return ""
        if argv[:2] == ("systemctl", "stop"):
            self.scheduler_stopped = True
            self.events.append("stop_scheduler")
            return ""
        if argv[:2] == ("systemctl", "restart"):
            self.scheduler_stopped = False
            self.events.append("systemctl_restart")
            return ""
        raise AssertionError(f"unexpected command: {argv}")

    def telegram_status(self):
        return {"inbound_enabled": self.duty_enabled}

    def check_telegram_ready(self):
        if not self.duty_enabled:
            raise ReleaseError("duty paused")

    def unit_state(self, unit):
        if unit == "autostop-manager-scheduler.service":
            return ("inactive" if self.scheduler_stopped else "active", "enabled")
        return ("active", "enabled")

    def effect_probe(self, operation, *, baseline):
        self.events.append(operation)
        if operation.startswith("capture"):
            baseline.write_text("{}")
        elif not baseline.exists():
            raise ReleaseError("missing effect baseline")

    def reacquire_hold_for_rollback(self):
        if self.scheduler_stopped or not self.held:
            raise ReleaseError("cannot reacquire")


class FakeReconciler(AutomationRevisionReconciler):
    def __init__(self, root: Path, *, failure: str = "") -> None:
        config = root / "automation-control.env"
        config.write_bytes(control_bytes())
        config.chmod(0o600)
        telegram_release = root / "synthetic-telegram-release"
        telegram_release.mkdir()
        telegram_link = root / "current-telegram"
        telegram_link.symlink_to(telegram_release)
        self.fake = FakeRelease(root, config)
        super().__init__(
            self.fake,
            expected_reported_sha=OLD,
            control_env=config,
            expected_telegram_release_dir=str(telegram_release),
        )
        self.backup_dir = root / "backup"
        self.failure = failure
        self.restart_count = 0
        self.fake.foreign_during_enable = failure == "foreign_during_enable"
        self.fake.ambiguous_probe = failure == "ambiguous_release_probe"
        self.telegram_link = telegram_link
        self.telegram_link_target = str(telegram_release)

    def preflight(self):
        return (
            secure_snapshot(self.control_env),
            {
                "crm_revision": OLD,
                "manager_revision": MANAGER,
                "crm_version": "0.4.0",
                "timers": [
                    {
                        "timer_id": timer_id,
                        "reconcile_state": "in_sync",
                        "period_minutes": 15,
                        "actual_period_minutes": 15,
                        "revision": 1,
                    }
                    for timer_id in revision_module.TIMER_UNITS
                ],
                "dependencies": {"crm_change_feed": "ready"},
                "outbox": {"by_status": {}, "total_count": 0},
                "runs": {"by_status": {}, "total_count": 0},
                "cursors": [],
            },
            "123",
        )

    def _backup(self, snapshot, baseline):
        self.fake.events.append("backup_after_hold")
        self.backup_dir.mkdir()
        (self.backup_dir / "automation-control.env").write_bytes(snapshot.contents)

    def _capture_unit_definitions(self):
        return {unit: "synthetic unit" for unit in revision_module.SNAPSHOT_UNITS}

    def _require_owned_hold(self):
        if not self.fake.held or self.fake.scheduler_stopped:
            raise ReleaseError("hold lost")

    def _offline_owned_hold(self):
        return self.fake.held

    def _process_identity(self, pid, key):
        if key == revision_module.MANAGER_KEY:
            return MANAGER.encode()
        return NEW.encode() if NEW.encode() in self.control_env.read_bytes() else OLD.encode()

    def _verify_feed_auth(self):
        self.fake.events.append("feed_auth")

    def _verify_neighbors(self, manager_sha):
        if manager_sha != MANAGER:
            raise ReleaseError("wrong Manager SHA")

    def _require_telegram_link(self):
        if self.fake.telegram_changed:
            raise ReleaseError("work Telegram release link changed")

    def _verify_sealed_controller(self):
        return None

    def _pinned_telegram_status(self):
        return {"inbound_enabled": self.fake.duty_enabled}

    def _check_pinned_telegram_ready(self):
        if not self.fake.duty_enabled:
            raise ReleaseError("duty paused")

    def _restart_and_verify(self, *, expected_sha, old_pid, baseline):
        self.restart_count += 1
        self.fake.events.append("restart_" + expected_sha[0])
        if self.failure == "restart" and self.restart_count == 1:
            self.fake.scheduler_stopped = True
            raise ReleaseError("injected restart failure")
        if self.failure == "foreign_write" and self.restart_count == 1:
            self.control_env.write_bytes(b"foreign change")
            raise ReleaseError("injected restart failure after foreign write")
        if self.failure == "lost_hold" and self.restart_count == 1:
            self.fake.held = False
            raise ReleaseError("injected hold loss")
        if self.fake.scheduler_stopped:
            if not self._offline_owned_hold():
                raise ReleaseError("offline hold lost")
        else:
            self._require_owned_hold()
        self.fake.scheduler_stopped = False
        expected = b"AUTOSTOP_AUTOMATION_CRM_REVISION=" + expected_sha.encode()
        if expected not in self.control_env.read_bytes():
            raise ReleaseError("wrong config during restart")
        if self.failure == "foreign_after_restart" and self.restart_count == 1:
            self.control_env.write_bytes(b"foreign config")
        if self.failure == "telegram_link_drift" and self.restart_count == 1:
            self.fake.telegram_changed = True
        return "456"

    def scheduler_packet(self):
        released = "release-hold" in self.fake.events
        if released and self.fake.ambiguous_probe:
            self.fake.ambiguous_probe = False
            raise ReleaseError("first post-release probe unavailable")
        return {
            "ready": not self.fake.held
            and not (
                released and self.failure in {"post_open_not_ready", "lost_release_bad_ready"}
            ),
            "hold": {"enabled": self.fake.held},
            "crm_revision": (
                NEW
                if b"AUTOSTOP_AUTOMATION_CRM_REVISION=" + NEW.encode()
                in self.control_env.read_bytes()
                else OLD
            ),
            "manager_revision": MANAGER,
            "crm_version": "0.4.0",
            "timers": [
                {
                    "timer_id": timer_id,
                    "reconcile_state": "in_sync",
                    "period_minutes": 15,
                    "actual_period_minutes": 15,
                    "revision": 1,
                }
                for timer_id in revision_module.TIMER_UNITS
            ],
            "dependencies": {"crm_change_feed": "ready"},
            "outbox": {"by_status": {}, "total_count": 0},
            "runs": {"by_status": {}, "total_count": 0},
            "cursors": [],
        }


class ReconcileAutomationCrmRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        uid_patch = patch.object(revision_module, "REQUIRED_UID", os.getuid())
        gid_patch = patch.object(revision_module, "REQUIRED_GID", os.getgid())
        uid_patch.start()
        gid_patch.start()
        self.addCleanup(uid_patch.stop)
        self.addCleanup(gid_patch.stop)

    def test_exact_key_change_preserves_every_other_byte(self) -> None:
        before = control_bytes()
        after = updated_identity(before, old_sha=OLD, new_sha=NEW, manager_sha=MANAGER)
        self.assertEqual(
            after,
            before.replace(
                b"AUTOSTOP_AUTOMATION_CRM_REVISION=" + OLD.encode(),
                b"AUTOSTOP_AUTOMATION_CRM_REVISION=" + NEW.encode(),
            ),
        )

    def test_refuses_missing_duplicate_stale_or_reformatted_identity(self) -> None:
        before = control_bytes()
        for contents in (
            before.replace(b"AUTOSTOP_AUTOMATION_CRM_REVISION=", b"MISSING="),
            before + b"AUTOSTOP_AUTOMATION_CRM_REVISION=" + OLD.encode() + b"\n",
            before.replace(OLD.encode(), ("d" * 40).encode()),
            before.replace(
                b"AUTOSTOP_AUTOMATION_CRM_REVISION=", b"AUTOSTOP_AUTOMATION_CRM_REVISION= "
            ),
        ):
            with self.subTest(contents=contents[-60:]), self.assertRaises(ReleaseError):
                updated_identity(contents, old_sha=OLD, new_sha=NEW, manager_sha=MANAGER)

    def test_refuses_symlink_wrong_mode_and_concurrent_write(self) -> None:
        path = self.root / "config"
        path.write_bytes(control_bytes())
        path.chmod(0o600)
        snapshot = secure_snapshot(path)
        path.write_bytes(control_bytes() + b"foreign=1\n")
        with self.assertRaisesRegex(ReleaseError, "concurrently"):
            atomic_replace_exact(path, snapshot, b"candidate")
        path.chmod(0o644)
        with self.assertRaisesRegex(ReleaseError, "unsafe"):
            secure_snapshot(path)
        path.chmod(0o600)
        link = self.root / "link"
        link.symlink_to(path)
        with self.assertRaisesRegex(ReleaseError, "unsafe"):
            secure_snapshot(link)

    def test_success_restarts_under_hold_then_releases(self) -> None:
        reconcile = FakeReconciler(self.root)
        with patch("os.geteuid", return_value=0):
            result = reconcile.apply()
        self.assertEqual(result["crm_revision"], NEW)
        self.assertEqual(
            reconcile.fake.events,
            [
                "hold",
                "backup_after_hold",
                "duty_disable",
                "capture-telegram-effects",
                "restart_b",
                "verify-telegram-effects",
                "release-hold",
                "duty_enable",
            ],
        )
        self.assertFalse(reconcile.fake.held)
        self.assertTrue(reconcile.fake.duty_enabled)
        self.assertIn(NEW.encode(), reconcile.control_env.read_bytes())
        self.assertEqual(
            (reconcile.backup_dir / "automation-control.env").read_bytes(), control_bytes()
        )

    def test_apply_requires_preflight_telegram_release_target(self) -> None:
        reconcile = FakeReconciler(self.root)
        reconcile.expected_telegram_release_dir = None
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "target is required"):
                reconcile.apply()
        self.assertEqual(reconcile.fake.events, [])

    def test_changed_telegram_release_aborts_before_hold(self) -> None:
        reconcile = FakeReconciler(self.root)
        replacement = self.root / "new-telegram-release"
        replacement.mkdir()
        reconcile.telegram_link.unlink()
        reconcile.telegram_link.symlink_to(replacement)
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "differs from approved target"):
                reconcile.apply()
        self.assertEqual(reconcile.fake.events, [])

    def test_restart_failure_restores_config_under_hold(self) -> None:
        reconcile = FakeReconciler(self.root, failure="restart")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "prior config restored"):
                reconcile.apply()
        self.assertEqual(
            reconcile.fake.events,
            [
                "hold",
                "backup_after_hold",
                "duty_disable",
                "capture-telegram-effects",
                "restart_b",
                "stop_scheduler",
                "restart_a",
                "verify-telegram-effects",
                "release-hold",
                "duty_enable",
            ],
        )
        self.assertEqual(reconcile.control_env.read_bytes(), control_bytes())

    def test_failed_directory_sync_after_replace_restores_actual_candidate(self) -> None:
        reconcile = FakeReconciler(self.root)
        original_fsync = os.fsync
        calls = 0

        def fail_after_replace(descriptor: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected directory sync failure")
            original_fsync(descriptor)

        with (
            patch("os.geteuid", return_value=0),
            patch.object(revision_module.os, "fsync", side_effect=fail_after_replace),
        ):
            with self.assertRaisesRegex(ReleaseError, "prior config restored"):
                reconcile.apply()
        self.assertEqual(reconcile.control_env.read_bytes(), control_bytes())
        self.assertEqual(
            reconcile.fake.events,
            [
                "hold",
                "backup_after_hold",
                "duty_disable",
                "capture-telegram-effects",
                "restart_a",
                "verify-telegram-effects",
                "release-hold",
                "duty_enable",
            ],
        )

    def test_foreign_write_blocks_rollback_without_clobber(self) -> None:
        reconcile = FakeReconciler(self.root, failure="foreign_write")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "rollback incomplete"):
                reconcile.apply()
        self.assertEqual(reconcile.control_env.read_bytes(), b"foreign change")
        self.assertTrue(reconcile.fake.held)

    def test_foreign_write_after_restart_blocks_hold_release(self) -> None:
        reconcile = FakeReconciler(self.root, failure="foreign_after_restart")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "rollback incomplete"):
                reconcile.apply()
        self.assertEqual(reconcile.control_env.read_bytes(), b"foreign config")
        self.assertTrue(reconcile.fake.held)
        self.assertFalse(reconcile.fake.duty_enabled)

    def test_lost_hold_stops_automatic_rollback(self) -> None:
        reconcile = FakeReconciler(self.root, failure="lost_hold")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "rollback incomplete"):
                reconcile.apply()
        self.assertFalse(reconcile.fake.held)
        self.assertTrue(reconcile.fake.scheduler_stopped)
        self.assertFalse(reconcile.fake.duty_enabled)
        self.assertIn(NEW.encode(), reconcile.control_env.read_bytes())

    def test_lost_release_response_is_reconciled_by_readback(self) -> None:
        reconcile = FakeReconciler(self.root)
        reconcile.fake.fail_release_response = True
        with patch("os.geteuid", return_value=0):
            result = reconcile.apply()
        self.assertEqual(result["release_outcome_reconciled"], "true")
        self.assertEqual(
            reconcile.fake.events,
            [
                "hold",
                "backup_after_hold",
                "duty_disable",
                "capture-telegram-effects",
                "restart_b",
                "verify-telegram-effects",
                "release-hold",
                "duty_enable",
            ],
        )

    def test_post_open_bad_readiness_stops_scheduler_without_config_rollback(self) -> None:
        reconcile = FakeReconciler(self.root, failure="post_open_not_ready")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "post-open check failed"):
                reconcile.apply()
        self.assertTrue(reconcile.fake.scheduler_stopped)
        self.assertFalse(reconcile.fake.duty_enabled)
        self.assertIn(NEW.encode(), reconcile.control_env.read_bytes())

    def test_lost_release_response_with_bad_readiness_stops_scheduler(self) -> None:
        reconcile = FakeReconciler(self.root, failure="lost_release_bad_ready")
        reconcile.fake.fail_release_response = True
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "post-open check failed"):
                reconcile.apply()
        self.assertTrue(reconcile.fake.scheduler_stopped)
        self.assertFalse(reconcile.fake.duty_enabled)
        self.assertIn(NEW.encode(), reconcile.control_env.read_bytes())

    def test_ambiguous_release_probe_stops_scheduler_after_readback_opens(self) -> None:
        reconcile = FakeReconciler(self.root, failure="ambiguous_release_probe")
        reconcile.fake.fail_release_response = True
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "scheduler stopped and duty paused"):
                reconcile.apply()
        self.assertTrue(reconcile.fake.scheduler_stopped)
        self.assertFalse(reconcile.fake.duty_enabled)
        self.assertIn(NEW.encode(), reconcile.control_env.read_bytes())

    def test_telegram_link_drift_does_not_enable_new_duty_controller(self) -> None:
        reconcile = FakeReconciler(self.root, failure="telegram_link_drift")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "rollback incomplete"):
                reconcile.apply()
        self.assertNotIn("duty_enable", reconcile.fake.events)
        self.assertFalse(reconcile.fake.duty_enabled)

    def test_foreign_write_during_duty_enable_stops_scheduler_without_clobber(self) -> None:
        reconcile = FakeReconciler(self.root, failure="foreign_during_enable")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "post-open check failed"):
                reconcile.apply()
        self.assertEqual(reconcile.control_env.read_bytes(), b"foreign during enable")
        self.assertTrue(reconcile.fake.scheduler_stopped)
        self.assertFalse(reconcile.fake.duty_enabled)
        self.assertEqual(
            reconcile.fake.events[-3:], ["duty_enable", "stop_scheduler", "duty_disable"]
        )

    def test_restart_readback_rejects_period_and_effect_drift(self) -> None:
        reconcile = FakeReconciler(self.root)
        reconcile.fake.held = True
        reconcile.unit_definitions = reconcile._capture_unit_definitions()
        baseline = reconcile.preflight()[1]
        for field, changed in (
            (
                "timers",
                [{**baseline["timers"][0], "actual_period_minutes": 30}, *baseline["timers"][1:]],
            ),
            ("runs", {"by_status": {}, "total_count": 1}),
            ("outbox", {"by_status": {"pending": 1}, "total_count": 1}),
            ("cursors", [{"synthetic": True}]),
        ):
            with self.subTest(field=field):
                expected = {**baseline, field: changed}
                with self.assertRaisesRegex(ReleaseError, "readback differs"):
                    AutomationRevisionReconciler._restart_and_verify(
                        reconcile, expected_sha=OLD, old_pid="old", baseline=expected
                    )

    def test_guarded_controller_accepts_only_published_clean_blob(self) -> None:
        remote = self.root / "remote.git"
        repo = self.root / "manager"

        def git(*argv: str, input_bytes: bytes | None = None) -> str:
            return (
                subprocess.run(["git", *argv], check=True, capture_output=True, input=input_bytes)
                .stdout.decode()
                .strip()
            )

        git("init", "--bare", str(remote))
        git("init", "-b", "AutostopManager", str(repo))
        script = repo / "scripts/set-work-telegram-duty.sh"
        script.parent.mkdir()
        script.write_text("#!/bin/sh\nexit 0\n")
        git("-C", str(repo), "add", "scripts/set-work-telegram-duty.sh")
        git(
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "synthetic guarded controller",
        )
        sha = git("-C", str(repo), "rev-parse", "HEAD")
        git("-C", str(repo), "remote", "add", "origin", str(remote))
        git("-C", str(repo), "push", "origin", "HEAD:AutostopManager")
        reconcile = FakeReconciler(self.root)
        reconcile.guarded_controller_repo = repo
        reconcile.guarded_controller_sha = sha
        original_run = reconcile.fake.run

        def git_or_fake(*argv, **kwargs):
            if argv[0] == "git":
                return git(*argv[1:], input_bytes=kwargs.get("input_bytes"))
            return original_run(*argv, **kwargs)

        reconcile.fake.run = git_or_fake
        self.assertEqual(reconcile._guarded_controller_source(), script)
        self.assertEqual(
            reconcile.guarded_controller_hash, hashlib.sha256(script.read_bytes()).hexdigest()
        )
        script.write_text("#!/bin/sh\nexit 1\n")
        with self.assertRaisesRegex(ReleaseError, "dirty"):
            reconcile._guarded_controller_source()

    def test_sealed_controller_rejects_content_change(self) -> None:
        reconcile = FakeReconciler(self.root)
        script = self.root / "guarded.sh"
        script.write_bytes(b"#!/bin/sh\nexit 0\n")
        script.chmod(0o500)
        reconcile.pinned_telegram_duty_path = script
        reconcile.guarded_controller_hash = hashlib.sha256(script.read_bytes()).hexdigest()
        AutomationRevisionReconciler._verify_sealed_controller(reconcile)
        script.chmod(0o700)
        script.write_bytes(b"#!/bin/sh\nexit 1\n")
        script.chmod(0o500)
        with self.assertRaisesRegex(ReleaseError, "artifact changed"):
            AutomationRevisionReconciler._verify_sealed_controller(reconcile)

    def test_preseal_status_executes_verified_bytes_not_mutable_source(self) -> None:
        repo = self.root / "manager"
        unsafe = repo / "scripts/set-work-telegram-duty.sh"
        unsafe.parent.mkdir(parents=True)
        unsafe.write_text("#!/bin/sh\nexit 99\n")
        release = CrmOnlyRelease(
            source=self.root / "crm-source", production_root=self.root / "production", sha=NEW
        )
        reconcile = AutomationRevisionReconciler(
            release,
            expected_reported_sha=OLD,
            guarded_controller_repo=repo,
            guarded_controller_sha=MANAGER,
        )
        reconcile.pinned_telegram_duty_path = unsafe
        reconcile.telegram_link_target = "synthetic-target"
        safe = b"#!/bin/sh\nprintf '{\"ok\":true}\\n'\n"

        def verified_source():
            reconcile.guarded_controller_bytes = safe
            return unsafe

        with patch.object(reconcile, "_guarded_controller_source", side_effect=verified_source):
            self.assertEqual(reconcile._pinned_telegram_status(), {"ok": True})


if __name__ == "__main__":
    unittest.main()
