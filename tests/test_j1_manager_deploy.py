from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class J1ManagerDeployTests(unittest.TestCase):
    def test_j1_activation_is_opt_in_and_rollback_aware(self) -> None:
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        release = script[script.index("# E8 credentials are synced") :]
        rollback = script[script.index("rollback_release() {") : script.index("\non_exit() {")]

        self.assertIn('J1_ACTIVATE_ON_DEPLOY="${AUTOSTOP_J1_ACTIVATE_ON_DEPLOY:-0}"', script)
        self.assertIn("snapshot_j1_worker_state()", script)
        self.assertIn("restore_j1_worker_state()", script)
        self.assertIn("j1_worker_activation_attempted=1", release)
        self.assertLess(
            release.index("snapshot_j1_worker_state"), release.index("activate_j1_worker")
        )
        self.assertLess(
            release.index("activate_j1_worker"), release.index('rm -f "$MAINTENANCE_MARKER_HOST"')
        )
        self.assertIn('run_release "$installer" --replace-unit --activate', script)

        self.assertLess(
            rollback.index('systemctl stop "$J1_UNIT_NAME"'),
            rollback.index('activate_manager_snapshot "$previous_manager_dir"'),
        )
        self.assertLess(
            rollback.index('activate_manager_snapshot "$previous_manager_dir"'),
            rollback.index("restore_j1_worker_state"),
        )
        self.assertIn('"$j1_worker_backup_dir/previous.service"', script)
        self.assertIn("j1_worker_previous_active", script)
        self.assertIn("j1_worker_previous_enabled", script)


if __name__ == "__main__":
    unittest.main()
