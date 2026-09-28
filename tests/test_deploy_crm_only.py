"""Release ordering and fail-closed rollback without touching Docker or CRM."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if sys.platform == "win32":
    raise unittest.SkipTest("Unix release locking requires fcntl.")

from scripts.deploy_crm_only import (
    Baseline,
    CommandFailure,
    Commands,
    CrmOnlyRelease,
    ReleaseError,
    SmokeStageFailure,
)

SHA = "a" * 40
OLD_SHA = "b" * 40
OLD_IMAGE = "sha256:" + "1" * 64
CANDIDATE_IMAGE = "sha256:" + "2" * 64


class FakeRelease(CrmOnlyRelease):
    def __init__(self, root: Path, *, fail: str = "") -> None:
        super().__init__(
            source=root / "source",
            production_root=root / "production",
            sha=SHA,
            expected_telegram_release_dir="telegram-target",
        )
        self.production_root.mkdir()
        (self.production_root / "data").mkdir()
        self.backup_dir = root / "backups" / self.release_id
        self.events: list[str] = []
        self.fail = fail
        self.duty_enabled = True
        self.backup_ready = False
        self.stopped = False
        self.baseline = Baseline(
            old_revision=OLD_SHA,
            old_image_id=OLD_IMAGE,
            old_container_id="old-container",
            manager_revision="c" * 40,
            manager_link_target="manager-target",
            telegram_link_target="telegram-target",
            other_container_ids={},
            mounts={},
            manager_mcp_pid="123",
            scheduler_pid="456",
            telegram_unit_states={
                unit: ("active", "enabled")
                for unit in ("autostop-work-telegram.service", "autostop-codex-wake.service")
            },
        )
        self.active_hold_key = ""
        self.used_hold_keys: set[str] = set()
        self.telegram_link_target = "telegram-target"
        self.current_telegram_target = "telegram-target"

    def _seal_guarded_controller(self, effects_dir: Path) -> None:
        return None

    def _call_guarded_duty(self, operation: str, *, timeout: int = 45) -> str:
        if self.fail == "switch_before_disable" and operation == "--disable":
            self.current_telegram_target = "other-telegram-target"
        if self.fail == "switch_before_enable" and operation == "--enable":
            self.current_telegram_target = "other-telegram-target"
        if self.current_telegram_target != self.telegram_link_target:
            raise ReleaseError("guarded controller rejected changed Telegram target")
        return self.run("guarded-duty", operation)

    def telegram_target_unchanged(self, baseline: Baseline) -> bool:
        return self.current_telegram_target == baseline.telegram_link_target

    def preflight(self) -> Baseline:
        self.events.append("preflight")
        return self.baseline

    def build_candidate(self) -> str:
        self.events.append("build")
        if self.fail == "build":
            raise ReleaseError("injected build failure")
        return CANDIDATE_IMAGE

    def run(self, *argv: str, **kwargs: object) -> str:
        if argv[:3] == ("docker", "image", "inspect"):
            return json.dumps([{"Id": OLD_IMAGE}])
        if argv[:3] == ("docker", "image", "tag"):
            self.events.append("tag")
            return ""
        if len(argv) == 2 and argv[1] in ("--disable", "--enable"):
            self.assert_guarded_duty_path(argv[0])
            if self.fail == "rollback_duty_enable" and argv[1] == "--enable":
                raise ReleaseError("injected duty resume failure")
            self.events.append("duty_" + argv[1][2:])
            self.duty_enabled = argv[1] == "--enable"
            return ""
        raise AssertionError(f"unexpected command: {argv}")

    @staticmethod
    def assert_guarded_duty_path(path: str) -> None:
        if path != "guarded-duty":
            raise AssertionError("release bypassed sealed guarded duty controller")

    def telegram_status(self) -> dict:
        return {
            "ok": True,
            "transport_ready": True,
            "owner_notification_configured": True,
            "inbound_enabled": self.duty_enabled,
            "wake_active": self.duty_enabled,
            "state": "inbound_enabled" if self.duty_enabled else "outbound_only",
        }

    def check_telegram_ready(self) -> None:
        if not self.duty_enabled:
            raise ReleaseError("duty is disabled")

    def check_telegram_units(self, baseline: Baseline) -> None:
        self.events.append("telegram_units")

    def effect_probe(self, operation: str, *, baseline: Path) -> None:
        self.events.append(operation)
        if self.fail == "telegram_capture" and operation == "capture-telegram-effects":
            raise ReleaseError("injected capture failure")
        if operation.startswith("capture"):
            baseline.write_text("{}")
        elif not baseline.exists():
            raise ReleaseError("effect baseline missing")

    def automation_status(self) -> dict:
        return {
            "enabled": bool(self.active_hold_key),
            "reason": "release" if self.active_hold_key else None,
            "attempt_hash": hashlib.sha256(
                f"release-attempt:{self.active_hold_key}".encode()
            ).hexdigest()
            if self.active_hold_key
            else None,
        }

    def automation(self, operation: str) -> None:
        self.events.append("automation_" + operation)
        if operation == "hold":
            if self.fail == "foreign_hold":
                self.active_hold_key = "foreign:release-key"
                raise ReleaseError("another scheduler hold won the race")
            if self.active_hold_key:
                if self.active_hold_key != self.hold_key:
                    raise ReleaseError("wrong hold owner")
            else:
                if self.hold_key in self.used_hold_keys:
                    raise ReleaseError("idempotency conflict: reused hold key")
                self.used_hold_keys.add(self.hold_key)
                self.active_hold_key = self.hold_key
        elif operation == "release-hold":
            if self.active_hold_key != self.hold_key:
                raise ReleaseError("wrong release owner")
            self.active_hold_key = ""
        if (
            self.fail == "ambiguous_release"
            and operation == "release-hold"
            and self.events.count("automation_release-hold") == 1
        ):
            raise ReleaseError("release applied but response lost")

    def compose(self, *argv: str, **kwargs: object) -> str:
        if argv[0] == "stop":
            self.events.append("stop")
            if self.fail == "rollback_stop" and self.events.count("stop") == 2:
                raise ReleaseError("injected rollback stop failure")
            self.stopped = True
        elif argv[0] == "up":
            self.events.append(
                "up_candidate" if kwargs.get("image") == self.image_tag else "up_previous"
            )
            self.stopped = False
        else:
            raise AssertionError(f"unexpected compose command: {argv}")
        return ""

    def verify_stopped(self) -> None:
        if not self.stopped:
            raise ReleaseError("not stopped")

    def backup(self) -> None:
        self.events.append("backup")
        if self.fail == "backup":
            raise ReleaseError("injected backup failure")
        self.backup_ready = True

    def restore_crm_only(self) -> None:
        self.verify_stopped()
        self.events.append("restore_crm_only")
        if self.fail == "restore":
            raise ReleaseError("injected restore failure")

    def wait_health(self, image_id: str, baseline: Baseline) -> None:
        self.events.append("health_candidate" if image_id == CANDIDATE_IMAGE else "health_previous")

    def smoke(self, *, revision: str) -> None:
        self.events.append("smoke_candidate" if revision == SHA else "smoke_previous")
        if (
            self.fail in ("smoke", "rollback_stop", "restore", "rollback_duty_enable")
            and revision == SHA
        ):
            raise ReleaseError("injected candidate smoke failure")

    def check_unchanged_neighbors(self, baseline: Baseline) -> None:
        self.events.append("neighbors")

    def check_store_network(self) -> None:
        self.events.append("store_network")


class CrmOnlyReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_success_updates_only_crm_and_releases_maintenance(self) -> None:
        release = FakeRelease(self.root)
        with patch("os.geteuid", return_value=0):
            result = release.apply()
        self.assertEqual(result["crm_revision"], SHA)
        self.assertEqual(release.events.count("up_candidate"), 1)
        self.assertNotIn("up_previous", release.events)
        self.assertLess(release.events.index("backup"), release.events.index("up_candidate"))
        self.assertLess(
            release.events.index("smoke_candidate"), release.events.index("automation_release-hold")
        )
        self.assertFalse(release.marker.exists())
        self.assertTrue(release.duty_enabled)

    def test_apply_requires_approved_telegram_target(self) -> None:
        release = FakeRelease(self.root)
        release.expected_telegram_release_dir = None
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "target is required"):
                release.apply()
        self.assertEqual(release.events, [])

    def test_preseal_status_executes_verified_bytes_not_mutable_source(self) -> None:
        release = CrmOnlyRelease(
            source=self.root / "source",
            production_root=self.root / "production",
            sha=SHA,
        )
        release.telegram_link_target = "synthetic-target"
        mutable = self.root / "mutable.sh"
        mutable.write_text("#!/bin/sh\nexit 99\n")
        safe = b"#!/bin/sh\nprintf '{\"ok\":true}\\n'\n"

        def verified_source():
            release.guarded_controller_bytes = safe
            return mutable

        with patch.object(release, "_guarded_controller_source", side_effect=verified_source):
            self.assertEqual(release.telegram_status(), {"ok": True})

    def test_changed_telegram_target_before_disable_keeps_hold_and_marker(self) -> None:
        release = FakeRelease(self.root, fail="switch_before_disable")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "hold retained"):
                release.apply()
        self.assertTrue(release.marker.exists())
        self.assertTrue(release.duty_enabled)
        self.assertNotIn("duty_disable", release.events)

    def test_changed_telegram_target_before_enable_does_not_switch_other_release(self) -> None:
        release = FakeRelease(self.root, fail="switch_before_enable")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "post-open check failed"):
                release.apply()
        self.assertFalse(release.duty_enabled)
        self.assertEqual(release.events.count("duty_enable"), 0)

    def test_candidate_failure_restores_only_crm_before_opening(self) -> None:
        release = FakeRelease(self.root, fail="smoke")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "prior CRM restored"):
                release.apply()
        self.assertLess(
            release.events.index("stop", release.events.index("up_candidate")),
            release.events.index("restore_crm_only"),
        )
        self.assertIn("up_previous", release.events)
        self.assertFalse(release.marker.exists())
        self.assertTrue(release.duty_enabled)

    def test_backup_failure_restarts_previous_image_without_restore(self) -> None:
        release = FakeRelease(self.root, fail="backup")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "prior CRM restored"):
                release.apply()
        self.assertIn("up_previous", release.events)
        self.assertNotIn("restore_crm_only", release.events)
        self.assertFalse(release.marker.exists())

    def test_failed_rollback_stop_keeps_marker_and_duty_paused(self) -> None:
        release = FakeRelease(self.root, fail="rollback_stop")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "rollback incomplete"):
                release.apply()
        self.assertTrue(release.marker.exists())
        self.assertFalse(release.duty_enabled)
        self.assertNotIn("restore_crm_only", release.events)

    def test_failed_restore_keeps_marker_and_duty_paused(self) -> None:
        release = FakeRelease(self.root, fail="restore")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "rollback incomplete"):
                release.apply()
        self.assertTrue(release.marker.exists())
        self.assertFalse(release.duty_enabled)

    def test_build_failure_precedes_maintenance_marker(self) -> None:
        release = FakeRelease(self.root, fail="build")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "build failure"):
                release.apply()
        self.assertEqual(release.events, ["preflight", "build"])
        self.assertFalse(release.marker.exists())

    def test_ambiguous_hold_release_reacquires_before_restore(self) -> None:
        release = FakeRelease(self.root, fail="ambiguous_release")
        original_smoke = release.smoke

        def smoke(*, revision: str) -> None:
            original_smoke(revision=revision)

        release.smoke = smoke
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "prior CRM restored"):
                release.apply()
        release_index = release.events.index("automation_release-hold")
        rehold_index = release.events.index("automation_hold", release_index)
        restore_index = release.events.index("restore_crm_only")
        self.assertLess(rehold_index, restore_index)
        self.assertFalse(release.marker.exists())

    def test_marker_removal_failure_reacquires_before_restore(self) -> None:
        release = FakeRelease(self.root)
        original_unlink = Path.unlink
        failed = False

        def one_failed_unlink(path: Path, *args: object, **kwargs: object) -> None:
            nonlocal failed
            if path == release.marker and not failed:
                failed = True
                raise OSError("injected marker unlink failure")
            original_unlink(path, *args, **kwargs)

        with patch("os.geteuid", return_value=0), patch.object(Path, "unlink", one_failed_unlink):
            with self.assertRaisesRegex(ReleaseError, "prior CRM restored"):
                release.apply()
        release_index = release.events.index("automation_release-hold")
        rehold_index = release.events.index("automation_hold", release_index)
        self.assertLess(rehold_index, release.events.index("restore_crm_only"))
        self.assertFalse(release.marker.exists())

    def test_gateway_smoke_is_read_only_and_has_no_incompatible_flags(self) -> None:
        release = FakeRelease(self.root)
        commands: list[tuple[str, ...]] = []

        def capture(*argv: str, **kwargs: object) -> str:
            commands.append(tuple(argv))
            return ""

        with (
            patch.object(release, "run", side_effect=capture),
            patch.object(release, "compose", side_effect=capture),
        ):
            CrmOnlyRelease.smoke(release, revision=SHA)
        gateway = next(argv for argv in commands if "scripts/check_agent_gateway_v2.py" in argv)
        self.assertIn("--require-store", gateway)
        self.assertNotIn("--exhaustive", gateway)
        self.assertNotIn("--maintenance-safe", gateway)
        self.assertNotIn("--release-revision", gateway)
        connector = next(argv for argv in commands if "scripts/check_live_connector.py" in argv)
        self.assertIn("AUTOSTOP_SMOKE_OPERATOR_USERNAME=", connector)
        self.assertIn("AUTOSTOP_SMOKE_OPERATOR_PASSWORD=", connector)
        self.assertNotIn("--expect-admin", connector)
        self.assertFalse(any("scripts/check_mcp_oauth.py" in argv for argv in commands))
        public_guard = next(
            argv for argv in commands if "https://crm.autostopcrm.ru/api/get_card" in " ".join(argv)
        )
        self.assertIn("GET", " ".join(public_guard))

    def test_smoke_failure_names_gate_without_command_output(self) -> None:
        release = FakeRelease(self.root)

        def fail_gateway(*argv: str, **kwargs: object) -> str:
            if "scripts/check_agent_gateway_v2.py" in argv:
                raise CommandFailure(2, ("store_runtime_ready",))
            return ""

        with (
            patch.object(release, "run", return_value=""),
            patch.object(release, "compose", side_effect=fail_gateway),
        ):
            with self.assertRaisesRegex(SmokeStageFailure, "agent-gateway") as caught:
                CrmOnlyRelease.smoke(release, revision=SHA)
        self.assertIn("checks=store_runtime_ready", str(caught.exception))

    def test_gateway_failure_only_exposes_allowlisted_check_names(self) -> None:
        payload = json.dumps(
            {
                "checks": {
                    "store_runtime_ready": False,
                    "private_customer_value": False,
                    "bootstrap_ok": True,
                },
                "diagnostic_chain": "private customer data",
            }
        ).encode()
        result = SimpleNamespace(returncode=2, stdout=payload, stderr=b"private secret")
        with patch("subprocess.run", return_value=result):
            with self.assertRaises(CommandFailure) as caught:
                Commands().run(["docker", "compose", "scripts/check_agent_gateway_v2.py"])
        self.assertEqual(caught.exception.failed_checks, ("store_runtime_ready",))
        self.assertNotIn("private", str(caught.exception))

    def test_rollback_reports_candidate_smoke_gate(self) -> None:
        release = FakeRelease(self.root)

        def smoke(*, revision: str) -> None:
            if revision == SHA:
                raise SmokeStageFailure("agent-gateway")

        release.smoke = smoke
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(
                ReleaseError, "prior CRM restored: smoke stage agent-gateway"
            ):
                release.apply()
        self.assertFalse(release.marker.exists())

    def test_telegram_capture_failure_recovers_without_baseline(self) -> None:
        release = FakeRelease(self.root, fail="telegram_capture")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "prior CRM restored"):
                release.apply()
        self.assertNotIn("stop", release.events)
        self.assertNotIn("verify-telegram-effects", release.events)
        self.assertTrue(release.duty_enabled)
        self.assertFalse(release.marker.exists())

    def test_foreign_hold_is_not_released_after_race(self) -> None:
        release = FakeRelease(self.root, fail="foreign_hold")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "prior CRM restored"):
                release.apply()
        self.assertEqual(release.active_hold_key, "foreign:release-key")
        self.assertNotIn("automation_release-hold", release.events)
        self.assertFalse(release.marker.exists())

    def test_rollback_resume_failure_reports_opened_previous_crm(self) -> None:
        release = FakeRelease(self.root, fail="rollback_duty_enable")
        with patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(ReleaseError, "previous CRM restored and reopened"):
                release.apply()
        self.assertFalse(release.marker.exists())
        self.assertFalse(release.active_hold_key)
        self.assertFalse(release.duty_enabled)


if __name__ == "__main__":
    unittest.main()
