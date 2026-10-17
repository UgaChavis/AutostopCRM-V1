from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class E8ManagerDeployTests(unittest.TestCase):
    def _run_automation_activation(
        self, *, adopt_exit_code: int
    ) -> subprocess.CompletedProcess[str]:
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        start = script.index("activate_manager_automation() {")
        activation = script[start : script.index("\n}\n", start) + 2]
        socket_check = '[[ -S "$AUTOMATION_SOCKET" ]]'
        self.assertEqual(activation.count(socket_check), 2)
        # Supply readiness without an actual daemon or platform-specific Unix socket.
        # The release commands and their error propagation remain the real Bash body.
        activation = activation.replace(socket_check, '[[ "$AUTOMATION_SOCKET" == fixture-ready ]]')
        bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
        self.assertIsNotNone(bash, "Bash is required for the deployment regression")
        assert bash is not None
        self.assertTrue(Path(bash).is_file(), f"Bash is unavailable: {bash}")
        harness = r"""
set -euo pipefail
manager_release_dir=/fixture/manager
automation_release_attempt_key=fixture-attempt
AUTOMATION_SERVICE_NAME=fixture-automation.service
AUTOMATION_SOCKET=fixture-ready
run_automation_release_module() {
  [[ "$#" == 4 && "$1" == /fixture/manager && "$2" == release && "$4" == fixture-attempt ]] || return 90
  case "$3" in
    hold|seed) printf '%s\n' "$3" ;;
    adopt-current) printf '%s\n' adopt; return "$ADOPT_EXIT_CODE" ;;
    *) return 91 ;;
  esac
}
run_release() {
  case "$*" in
    'systemctl enable --now fixture-automation.service') printf '%s\n' start ;;
    'systemctl is-active --quiet fixture-automation.service') return 0 ;;
    *) return 92 ;;
  esac
}
"""
        return subprocess.run(
            [
                bash,
                "-c",
                harness
                + f"\nADOPT_EXIT_CODE={adopt_exit_code}\n"
                + activation
                + "\nactivate_manager_automation\n",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_automation_adoption_failure_prevents_start_and_seed(self) -> None:
        result = self._run_automation_activation(adopt_exit_code=17)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["hold", "adopt"])

    def test_automation_adopts_current_before_start_and_seed(self) -> None:
        result = self._run_automation_activation(adopt_exit_code=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["hold", "adopt", "start", "seed"])

    def test_catalog_sync_uses_sealed_manager_before_maintenance(self) -> None:
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")

        snapshot = script.index('adopt_prepared_manager_commit "$MANAGER_SOURCE_DIR"')
        knowledge = script.index("run_isolated_manager_knowledge_preflight", snapshot)
        sync = script.index('catalog_sync_script="$manager_release_dir/scripts/', knowledge)
        disk_check = script.index('require_disk_headroom "post-catalog"', sync)
        maintenance = script.index("maintenance_started=1", disk_check)

        self.assertLess(snapshot, knowledge)
        self.assertLess(knowledge, sync)
        self.assertLess(sync, disk_check)
        self.assertLess(disk_check, maintenance)
        self.assertIn('[[ ! -f "$catalog_sync_script" || -L "$catalog_sync_script" ]]', script)
        self.assertIn('PYTHONPATH="$manager_release_dir"', script[sync:disk_check])
        self.assertIn(
            '--cache-root "$(dirname "$MANAGER_DB")/offline_parts_catalogs"',
            script[sync:disk_check],
        )

    def test_deploy_binds_final_private_credentials_before_native_activation(self) -> None:
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        admission_start = script.index("admit_j1_candidate() {")
        admission = script[admission_start : script.index("\n}\n", admission_start)]
        release = script[script.index("maintenance_started=1") :]

        self.assertIn('MANAGER_CRM_MCP_ENV="/opt/AutostopManager/.crm-mcp.env"', script)
        self.assertIn('snapshot --backup-dir "$manager_crm_mcp_backup_dir"', script)
        self.assertIn('"$MANAGER_MCP_ACTIVATE_ON_DEPLOY" != "${j1_closed[1]}"', script)
        sync = admission.index("sync_manager_crm_mcp_configuration")
        gate = admission.index("scripts/j1_release_boundary.py admit")
        self.assertLess(sync, gate)
        self.assertNotIn("sync_manager_crm_mcp_configuration", release)
        self.assertLess(
            release.index("admit_j1_candidate"), release.index("activate_manager_automation")
        )
        self.assertEqual(release.count("activate_manager_automation"), 1)
        self.assertIn("probe_manager_crm_feed_auth", release)
        self.assertIn("require-dependencies-ready", release)
        self.assertLess(
            release.index("scripts/check_mcp_oauth.py"),
            release.index('rm -f "$MAINTENANCE_MARKER_HOST"'),
        )

    def test_native_activation_uses_guarded_unit_and_preserves_failed_release(self) -> None:
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        start = script.index("activate_manager_native_mcp() {")
        activation = script[start : script.index("\n}\n", start)]
        release = script[script.index("maintenance_started=1") :]
        rollback = script[script.index("rollback_release() {") : script.index("\non_exit() {")]

        self.assertIn('if [[ "$MANAGER_MCP_ACTIVATE_ON_DEPLOY" == "1" ]]; then', release)
        self.assertIn("manager_mcp_activation_attempted=1", release)
        self.assertIn("systemctl enable --now autostop-manager-mcp.service", activation)
        self.assertIn("systemctl is-active --quiet autostop-manager-mcp.service", activation)
        self.assertIn("mcp-probe", activation)
        self.assertNotIn('run_release "$installer" --replace-unit --activate', activation)
        self.assertLess(
            release.index("admit_j1_candidate"),
            release.index('activate_manager_native_mcp "$manager_release_dir" release'),
        )
        for forbidden in (
            "restore_manager_crm_mcp_configuration",
            "activate_manager_snapshot",
            "restore-manager-changed",
        ):
            self.assertNotIn(forbidden, rollback)
        self.assertIn("autostop-manager-mcp.service", rollback)
        self.assertIn("docker compose stop", rollback)


if __name__ == "__main__":
    unittest.main()
