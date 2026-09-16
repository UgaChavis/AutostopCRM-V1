from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class E8ManagerDeployTests(unittest.TestCase):
    def test_deploy_syncs_only_private_loopback_manager_config_after_crm_checks(self) -> None:
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")

        self.assertIn('MANAGER_CRM_MCP_ENV="/opt/AutostopManager/.crm-mcp.env"', script)
        self.assertIn(
            'MANAGER_MCP_ACTIVATE_ON_DEPLOY="${AUTOSTOP_MANAGER_MCP_ACTIVATE_ON_DEPLOY:-0}"', script
        )
        self.assertIn("sync_manager_crm_mcp_configuration()", script)
        self.assertIn("restore_manager_crm_mcp_configuration()", script)
        self.assertIn("activate_manager_native_mcp()", script)
        self.assertIn('snapshot --backup-dir "$manager_crm_mcp_backup_dir"', script)
        self.assertIn('restore --backup-dir "$manager_crm_mcp_backup_dir"', script)

        public_oauth = script.index(
            'run_release docker compose exec -T "$SERVICE_NAME" python scripts/check_mcp_oauth.py'
        )
        sync = script.index("sync_manager_crm_mcp_configuration", public_oauth)
        marker_removal = script.index('run_release rm -f "$MAINTENANCE_MARKER_HOST"')
        self.assertLess(public_oauth, sync)
        self.assertLess(sync, marker_removal)

    def test_opt_in_native_activation_is_rollback_aware(self) -> None:
        script = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        rollback_start = script.index("rollback_release()")
        rollback = script[rollback_start : script.index("\non_exit() {", rollback_start)]

        self.assertIn('if [[ "$MANAGER_MCP_ACTIVATE_ON_DEPLOY" == "1" ]]; then', script)
        self.assertIn("manager_mcp_activation_attempted=1", script)
        self.assertIn('activate_manager_native_mcp "$manager_release_dir" release', script)
        self.assertIn('activate_manager_native_mcp "$previous_manager_dir" maintenance', rollback)
        self.assertLess(
            rollback.index("restore_manager_crm_mcp_configuration"),
            rollback.index('activate_manager_snapshot "$previous_manager_dir"'),
        )
        self.assertIn('"$installer" --replace-unit --activate', script)


if __name__ == "__main__":
    unittest.main()
