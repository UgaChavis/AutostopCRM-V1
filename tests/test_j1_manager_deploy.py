from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class J1ManagerDeployTests(unittest.TestCase):
    def test_j1_activation_preserves_previous_role_and_uses_guarded_unit(self) -> None:
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        release = script[script.index("maintenance_started=1") :]
        start = script.index("activate_j1_worker() {")
        activation = script[start : script.index("\n}\n", start)]
        admission_start = script.index("admit_j1_candidate() {")
        admission = script[admission_start : script.index("\n}\n", admission_start)]

        self.assertIn('J1_ACTIVATE_ON_DEPLOY="${AUTOSTOP_J1_ACTIVATE_ON_DEPLOY:-0}"', script)
        self.assertIn('"$J1_ACTIVATE_ON_DEPLOY" != "$j1_previously_active"', script)
        self.assertIn("J1_ACTIVATE_ON_DEPLOY=1", script)
        self.assertIn("j1_worker_activation_attempted=1", release)
        self.assertLess(release.index("admit_j1_candidate"), release.index("activate_j1_worker"))
        self.assertLess(
            release.index("activate_j1_worker"), release.index('rm -f "$MAINTENANCE_MARKER_HOST"')
        )
        self.assertIn('systemctl enable --now "$J1_UNIT_NAME"', activation)
        self.assertIn('systemctl is-active --quiet "$J1_UNIT_NAME"', activation)
        self.assertIn("scripts/j1_release_boundary.py native-probe", activation)
        self.assertNotIn("/usr/bin/python3 -m autostop_manager.j1_research probe", activation)
        self.assertNotIn("--replace-unit --activate", activation)
        self.assertLess(
            admission.index("j1_candidate_access_possible=1"),
            admission.index("prepare_guarded_j1_units"),
        )
        self.assertLess(
            admission.index("prepare_guarded_j1_units"),
            admission.index("scripts/j1_release_boundary.py admit"),
        )

    def test_j1_browser_remains_denied_and_failed_release_cannot_restore_old_writer(self) -> None:
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        rollback = script[script.index("rollback_release() {") : script.index("\non_exit() {")]
        browser_flag = script.index('if [[ "$J1_BROWSER_ACTIVATE_ON_DEPLOY" != "0" ]]; then')
        browser_gate = script[browser_flag : script.index("\nfi", browser_flag)]
        self.assertIn("exit 2", browser_gate)
        self.assertLess(browser_flag, script.index("maintenance_started=1"))
        self.assertIn("unsupported J1 browser profile", browser_gate)
        for unit in (
            "autostop-j1.service",
            "autostop-manager-mcp.service",
            "autostop-manager-scheduler.service",
            "autostop-work-telegram.service",
            "autostop-telegram.service",
            "autostop-codex-wake.service",
            "autostop-j1-browser.service",
        ):
            self.assertIn(unit, rollback)
        for forbidden in (
            "restore_j1_worker_state",
            "restore_j1_browser_state",
            "activate_manager_snapshot",
            "restore-crm-changed",
            "restore-manager-changed",
            "docker compose up",
            'rm -f "$MAINTENANCE_MARKER_HOST"',
        ):
            self.assertNotIn(forbidden, rollback)
        self.assertIn('return "$original_status"', rollback)


if __name__ == "__main__":
    unittest.main()
