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

    def test_j1_browser_activation_is_opt_in_preflighted_and_rollback_aware(self) -> None:
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        release = script[script.index("# E8 credentials are synced") :]
        rollback = script[script.index("rollback_release() {") : script.index("\non_exit() {")]
        optional = script[
            script.index("activate_j1_browser_optional() {") : script.index(
                "\nrollback_release() {"
            )
        ]
        preflight = script[
            script.index("preflight_j1_browser_resources() {") : script.index(
                "\nactivate_j1_browser() {"
            )
        ]

        self.assertIn(
            'J1_BROWSER_ACTIVATE_ON_DEPLOY="${AUTOSTOP_J1_BROWSER_ACTIVATE_ON_DEPLOY:-0}"', script
        )
        self.assertIn("J1_BROWSER_MIN_MEM_AVAILABLE_KIB=2097152", script)
        self.assertIn("J1_BROWSER_MIN_SWAP_FREE_KIB=1048576", script)
        self.assertIn("preflight_j1_browser_resources()", script)
        self.assertIn("run_release sleep 60", script)
        self.assertIn('"pswpin"', script)
        self.assertIn('"pswpout"', script)
        self.assertIn("no longer has at least 2 GiB MemAvailable", preflight)
        self.assertIn("no longer has at least 1 GiB SwapFree", preflight)
        self.assertIn("snapshot_j1_browser_state()", script)
        self.assertIn("restore_j1_browser_state()", script)
        self.assertIn("rollback_j1_browser_only()", script)
        self.assertIn("j1_browser_marker_is_sealed()", script)
        self.assertIn('"0:0:600"', script)
        self.assertIn('run_release "$installer" --replace-unit --activate', script)
        self.assertIn("j1_browser_activation_attempted=1", optional)
        self.assertLess(
            optional.index("preflight_j1_browser_resources"),
            optional.index("snapshot_j1_browser_state"),
        )
        self.assertLess(
            optional.index("snapshot_j1_browser_state"),
            optional.index('activate_j1_browser "$target_dir"'),
        )
        self.assertIn("static J1 remains available", optional)
        self.assertIn("rolling back browser stack only", optional)
        self.assertIn('activate_j1_browser_optional "$manager_release_dir"', release)
        self.assertLess(
            release.index("activate_j1_browser_optional"),
            release.index('rm -f "$MAINTENANCE_MARKER_HOST"'),
        )
        self.assertLess(
            rollback.index('systemctl stop "$J1_BROWSER_UNIT_NAME"'),
            rollback.index('activate_manager_snapshot "$previous_manager_dir"'),
        )
        self.assertLess(
            rollback.index('activate_manager_snapshot "$previous_manager_dir"'),
            rollback.index("restore_j1_browser_state"),
        )
        self.assertIn('"$j1_browser_backup_dir/previous.marker"', script)


if __name__ == "__main__":
    unittest.main()
