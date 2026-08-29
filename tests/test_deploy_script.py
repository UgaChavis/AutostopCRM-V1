import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _posix_bash_available() -> bool:
    bash = shutil.which("bash")
    if os.name != "posix" or not bash:
        return False
    try:
        return (
            subprocess.run(
                [bash, "-c", "exit 0"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


class DeployScriptTests(unittest.TestCase):
    def test_deploy_script_verifies_active_v1_branch_without_destructive_reset(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")
        preflight = (PROJECT_ROOT / "scripts" / "release_git_preflight.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('CRM_DEPLOY_BRANCH="autostopcrm-v1"', script)
        self.assertIn('CRM_DEPLOY_REMOTE="origin"', script)
        self.assertIn('MANAGER_DEPLOY_BRANCH="AutostopManager"', script)
        self.assertIn('MANAGER_DEPLOY_REMOTE="${AUTOSTOP_MANAGER_DEPLOY_REMOTE:-origin}"', script)
        self.assertIn("release_git_verify_fetched_checkout \\", script)
        self.assertIn("symbolic-ref --quiet --short HEAD", preflight)
        self.assertIn("status --porcelain=v1 --untracked-files=all", preflight)
        self.assertIn("fetch --quiet --no-tags \\", preflight)
        self.assertIn("GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=Never", preflight)
        self.assertIn("GIT_SSH_COMMAND='ssh -oBatchMode=yes -oConnectTimeout=15'", preflight)
        self.assertIn('timeout --signal=TERM --kill-after=5 "${fetch_timeout}s"', preflight)
        self.assertIn('"$remote" "refs/heads/$remote_branch"', preflight)
        self.assertNotIn("AUTOSTOP_SKIP_GIT_SYNC", script)
        self.assertNotIn("AUTOSTOP_ALLOW_DIRTY_RELEASE", script)
        self.assertNotIn("AUTOSTOP_DEPLOY_BRANCH", script)
        self.assertNotIn("AUTOSTOP_DEPLOY_REMOTE", script)
        self.assertNotIn("git reset --hard", script)

    def test_deploy_loads_server_local_env_before_smoke_credentials(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")

        self.assertIn('if [[ -f "$ROOT_DIR/.env" ]]; then', script)
        self.assertLess(
            script.index('if [[ -f "$ROOT_DIR/.env" ]]; then'),
            script.index(': "${AUTOSTOP_SMOKE_OPERATOR_USERNAME:?'),
        )
        self.assertIn(
            ': "${AUTOSTOP_SMOKE_OPERATOR_USERNAME:?set smoke username}"',
            script,
        )
        self.assertIn(
            ': "${AUTOSTOP_SMOKE_OPERATOR_PASSWORD:?set smoke password}"',
            script,
        )
        self.assertNotIn("MINIMAL_KANBAN_DEFAULT_ADMIN_USERNAME:-admin", script)
        self.assertNotIn("MINIMAL_KANBAN_DEFAULT_ADMIN_PASSWORD:-admin", script)

    def test_compose_declares_only_primary_crm_service(self) -> None:
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        retired_service = "autostopcrm-" + "tele" + "gram-ai"
        retired_entrypoint = "main_" + "tele" + "gram_ai.py"
        retired_env = "tele" + "gram-ai.env"

        self.assertIn("autostopcrm:", compose)
        self.assertNotIn(retired_service, compose)
        self.assertNotIn(retired_entrypoint, compose)
        self.assertNotIn(retired_env, compose)

    def test_compose_uses_separate_precreated_internal_store_network_without_database(
        self,
    ) -> None:
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("autostop-store-agent:", compose)
        self.assertIn("name: autostop-store-agent", compose)
        self.assertIn("external: true", compose)
        self.assertIn(
            'AUTOSTOP_STORE_API_URL: "${AUTOSTOP_STORE_API_URL:-http://autostop-app:8000}"', compose
        )
        self.assertIn('AUTOSTOP_STORE_READ_TOKEN: "${AUTOSTOP_STORE_READ_TOKEN:-}"', compose)
        self.assertIn('AUTOSTOP_STORE_QUOTE_TOKEN: "${AUTOSTOP_STORE_QUOTE_TOKEN:-}"', compose)
        self.assertIn('AUTOSTOP_STORE_MANAGE_TOKEN: "${AUTOSTOP_STORE_MANAGE_TOKEN:-}"', compose)
        self.assertIn('AUTOSTOP_STORE_OWNER_TOKEN: "${AUTOSTOP_STORE_OWNER_TOKEN:-}"', compose)
        self.assertNotIn("autostop-db:", compose)

    def test_deploy_does_not_install_production_watchdog_timer_by_default(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")
        installer = (PROJECT_ROOT / "scripts" / "install_production_watchdog.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('INSTALL_WATCHDOG="${AUTOSTOP_INSTALL_WATCHDOG:-0}"', script)
        self.assertIn("install_production_watchdog.sh", script)
        self.assertIn("autostopcrm-watchdog.service", installer)
        self.assertIn("autostopcrm-watchdog.timer", installer)
        self.assertIn("OnUnitActiveSec=", installer)
        self.assertIn("production_watchdog.py", installer)

    def test_deploy_holds_lock_for_production_watchdog(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")

        self.assertIn(
            'DEPLOY_LOCK_PATH="${AUTOSTOP_DEPLOY_LOCK_PATH:-$ROOT_DIR/.autostop-deploy.lock}"',
            script,
        )
        self.assertIn('exec {DEPLOY_LOCK_FD}>"$DEPLOY_LOCK_PATH"', script)
        self.assertIn('flock -n "$DEPLOY_LOCK_FD"', script)
        self.assertNotIn("eval ", script)

    def test_deploy_prebuilds_then_replaces_only_crm_with_bounded_rollback(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")

        self.assertIn(
            'MAINTENANCE_BUDGET_SECONDS="${AUTOSTOP_MAINTENANCE_BUDGET_SECONDS:-600}"', script
        )
        self.assertIn('git -C "$ROOT_DIR" archive --format=tar "$crm_revision"', script)
        self.assertIn('--label "org.opencontainers.image.revision=$crm_revision"', script)
        self.assertIn('--tag "$release_image_tag" -', script)
        self.assertIn("^sha256:[0-9a-f]{64}$", script)
        self.assertIn('release_image_revision="$(', script)
        self.assertIn('if [[ "$release_image_revision" != "$crm_revision" ]]', script)
        self.assertIn(
            'if [[ "$BUILD_RELEASE_IMAGE" != "1" ]]',
            script,
        )
        self.assertIn('MIN_FREE_DISK_BYTES="${AUTOSTOP_MIN_FREE_DISK_BYTES:-2147483648}"', script)
        self.assertIn(
            'BUILD_DISK_RESERVE_BYTES="${AUTOSTOP_BUILD_DISK_RESERVE_BYTES:-1073741824}"',
            script,
        )
        self.assertIn('df --output=avail -B1 "$target_path"', script)
        self.assertIn('require_disk_headroom "pre-build" "$prebuild_required_bytes"', script)
        self.assertIn('require_disk_headroom "post-build" "$MIN_FREE_DISK_BYTES"', script)
        self.assertIn('docker tag "$release_image" "$STABLE_IMAGE"', script)
        self.assertIn('docker tag "$rollback_image" "$STABLE_IMAGE"', script)
        self.assertIn("--no-deps --no-build --force-recreate", script)
        self.assertIn("scripts/agent_release_backup.py create", script)
        self.assertIn(
            '"$CRM_DATA_DIR/printing/completion_act_forms.json"',
            script,
        )
        self.assertIn(
            'du -sb "$CRM_DATA_DIR/printing/completion_act_forms"',
            script,
        )
        self.assertIn("restore-crm-changed --backup-dir", script)
        self.assertIn("restore-manager-changed --backup-dir", script)
        self.assertIn("rollback_release", script)
        self.assertIn("umask 077", script)
        self.assertIn(
            'ROLLBACK_RESERVE_SECONDS="${AUTOSTOP_ROLLBACK_RESERVE_SECONDS:-120}"', script
        )
        self.assertEqual(
            script.count(
                'timeout --signal=TERM --kill-after=5 "${command_budget}s" "$@" </dev/null'
            ),
            2,
        )
        self.assertIn("run_release docker compose stop", script)
        self.assertIn("run_maintenance env AUTOSTOP_RELEASE_IMAGE", script)
        self.assertEqual(script.count('mkdir -p "$staging_dir/data"'), 1)
        self.assertEqual(script.count('chmod -R a+rX "$staging_dir"'), 1)
        self.assertIn('chmod 0755 "$MANAGER_RELEASE_ROOT"', script)
        self.assertIn('mkdir -p "$MANAGER_RELEASE_ROOT" "$BACKUP_ROOT"', script)
        self.assertIn(
            '"manager-release" "$manager_release_required_bytes" "$MANAGER_RELEASE_ROOT"',
            script,
        )
        self.assertIn(
            '"pre-maintenance-backup" "$premaintenance_required_bytes" "$BACKUP_ROOT"',
            script,
        )
        self.assertIn('disk_available_bytes "$target_path"', script)
        self.assertIn('git -C "$source_dir" archive HEAD | tar -x -C "$staging_dir"', script)
        self.assertEqual(
            script.count('snapshot_manager_commit "$MANAGER_SOURCE_DIR"'),
            1,
        )
        self.assertIn("manager_mount_source=", script)
        self.assertIn("running CRM Manager mount source is unavailable", script)
        self.assertNotIn("rsync", script)
        self.assertLess(
            script.index('require_disk_headroom "pre-build"'),
            script.index('snapshot_manager_commit "$MANAGER_SOURCE_DIR"'),
        )
        self.assertLess(
            script.index('require_disk_headroom "pre-build"'),
            script.index('--tag "$release_image_tag" -'),
        )
        self.assertLess(
            script.index('require_disk_headroom "post-build"'),
            script.index('snapshot --backup-dir "$auth_backup_dir"'),
        )
        self.assertNotIn("docker compose up -d --build --remove-orphans", script)

    def test_deploy_verifies_both_checkouts_before_snapshot_build_or_rotation(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")

        crm_preflight = script.index('crm_revision="$(')
        manager_preflight = script.index('manager_revision="$(')
        oauth_ensure = script.index("scripts/configure_mcp_oauth.py ensure")
        manager_snapshot = script.index(
            'snapshot_manager_commit "$MANAGER_SOURCE_DIR" "$manager_release_dir"'
        )
        image_build = script.index('--tag "$release_image_tag" -')
        self.assertEqual(
            2,
            script.count(
                '"AutoStop CRM" "$ROOT_DIR" "$CRM_DEPLOY_BRANCH" "$crm_revision" >/dev/null'
            ),
        )
        auth_snapshot = script.index('snapshot --backup-dir "$auth_backup_dir"')
        auth_rotation = script.index("rotate --generate")

        self.assertLess(crm_preflight, manager_preflight)
        for release_action in (
            oauth_ensure,
            manager_snapshot,
            image_build,
            auth_snapshot,
            auth_rotation,
        ):
            self.assertLess(manager_preflight, release_action)

    def test_deploy_requires_scoped_store_identity_and_safe_network_membership(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")

        self.assertIn(
            ': "${AUTOSTOP_STORE_READ_TOKEN:?provision store read service token}"', script
        )
        self.assertIn(
            ': "${AUTOSTOP_STORE_QUOTE_TOKEN:?provision store quote service token}"', script
        )
        self.assertIn(
            ': "${AUTOSTOP_STORE_MANAGE_TOKEN:?provision store manage service token}"', script
        )
        self.assertIn(
            ': "${AUTOSTOP_STORE_OWNER_TOKEN:?provision store owner service token}"', script
        )
        self.assertIn("validate_store_network 0", script)
        self.assertIn("validate_store_network 1 run_release", script)
        self.assertIn('inspect_command=("$runner" docker network inspect)', script)
        self.assertIn("docker network inspect)", script)
        self.assertIn("--format '{{.Internal}}'", script)
        self.assertIn('grep -Fxq "$STORE_APP_CONTAINER"', script)
        self.assertIn('grep -Fxq "$STORE_DB_CONTAINER"', script)
        self.assertIn("unexpected container is attached", script)
        self.assertIn("--require-production --require-store", script)
        self.assertEqual(3, script.count("--require-store"))
        self.assertEqual(1, script.count("--require-web"))

    def test_deploy_retries_internal_store_gateway_until_ready(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")

        self.assertIn("wait_for_internal_store_gateway()", script)
        self.assertIn("attempt <= SMOKE_ATTEMPTS", script)
        self.assertIn("Store Gateway is not ready yet; retrying", script)
        self.assertIn("wait_for_internal_store_gateway\n", script)
        readiness = script[
            script.index("wait_for_internal_store_gateway()") : script.index(
                "reload_deploy_environment()"
            )
        ]
        self.assertIn("assert_release_budget || return 1", readiness)
        self.assertIn("run_release docker compose exec", readiness)
        self.assertIn('run_release sleep "$SMOKE_DELAY_SECONDS"', readiness)
        self.assertNotIn("run_maintenance", readiness)
        self.assertLess(
            script.index("wait_for_internal_store_gateway\n"),
            script.index('run_release rm -f "$MAINTENANCE_MARKER_HOST"'),
        )

    def test_deploy_rotates_auth_at_cutover_and_restores_it_on_rollback(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")

        build_index = script.index('docker tag "$previous_image_id" "$rollback_image"')
        snapshot_index = script.index('snapshot --backup-dir "$auth_backup_dir"')
        recovery_armed_index = script.index("auth_rotated=1", snapshot_index)
        rotate_index = script.index("rotate --generate")
        maintenance_index = script.index("maintenance_started=1", rotate_index)
        self.assertLess(build_index, snapshot_index)
        self.assertLess(snapshot_index, recovery_armed_index)
        self.assertLess(recovery_armed_index, rotate_index)
        self.assertLess(rotate_index, maintenance_index)
        self.assertIn('restore --backup-dir "$auth_backup_dir"', script)
        self.assertIn("  check\n", script)
        self.assertNotIn("CODEX_CONFIG_PATH", script)
        self.assertNotIn("CODEX_RUNTIME_ENV_PATH", script)
        self.assertNotIn("--runtime-env", script)

    def test_auth_snapshot_remains_recoverable_until_release_commit(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")
        snapshot = script.index('snapshot --backup-dir "$auth_backup_dir"')
        recovery_armed = script.index("auth_rotated=1", snapshot)
        rotate = script.index("rotate --generate")
        marker_removal = script.index('run_release rm -f "$MAINTENANCE_MARKER_HOST"')
        marked_success = script.index("deployment_succeeded=1", marker_removal)
        trap_removed = script.index("trap - EXIT", marked_success)
        recovery_disarmed = script.index("auth_rotated=0", trap_removed)
        backup_cleanup = script.index("remove_auth_backup_if_safe", recovery_disarmed)

        self.assertLess(snapshot, recovery_armed)
        self.assertLess(recovery_armed, rotate)
        self.assertLess(rotate, marker_removal)
        self.assertLess(marker_removal, marked_success)
        self.assertLess(marked_success, trap_removed)
        self.assertLess(trap_removed, recovery_disarmed)
        self.assertLess(recovery_disarmed, backup_cleanup)
        rotation_failure = script[rotate : script.index("reload_deploy_environment", rotate)]
        self.assertNotIn('rm -rf "$auth_backup_dir"', rotation_failure)

    def test_failed_auth_restore_never_deletes_the_recovery_snapshot(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")
        restore_start = script.index("restore_auth_configuration()")
        restore_end = script.index("\nremove_auth_backup_if_safe()", restore_start)
        restore = script[restore_start:restore_end]
        cleanup_start = restore_end + 1
        cleanup_end = script.index("\nrollback_release()", cleanup_start)
        cleanup = script[cleanup_start:cleanup_end]
        rollback_start = cleanup_end + 1
        rollback_end = script.index("\non_exit() {", rollback_start)
        rollback = script[rollback_start:rollback_end]
        on_exit_start = rollback_end + 1
        on_exit_end = script.index("trap on_exit EXIT", on_exit_start)
        on_exit = script[on_exit_start:on_exit_end]

        self.assertIn("if (( status == 0 )); then", restore)
        self.assertIn("if reload_deploy_environment; then", restore)
        self.assertIn("auth_rotated=0", restore)
        self.assertIn("if (( status != 0 )); then", restore)
        self.assertNotIn('rm -rf "$auth_backup_dir"', restore)
        self.assertIn("if (( auth_rotated != 0 )); then", cleanup)
        self.assertIn('rm -rf "$auth_backup_dir"', cleanup)
        self.assertNotIn('rm -rf "$auth_backup_dir"', rollback)
        self.assertIn("if restore_auth_configuration; then", rollback)
        self.assertIn("if (( auth_rotated == 0 )); then", rollback)
        self.assertNotIn('rm -rf "$auth_backup_dir"', on_exit)
        self.assertIn("if restore_auth_configuration; then", on_exit)

    def test_deploy_makes_public_auth_smoke_mandatory_for_reads_and_writes(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")
        connector = (PROJECT_ROOT / "scripts" / "check_live_connector.py").read_text(
            encoding="utf-8"
        )
        gateway_check = (PROJECT_ROOT / "scripts" / "check_agent_gateway_v2.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("VERIFY_PUBLIC_HTTPS", script)
        marker_removal = script.index('run_release rm -f "$MAINTENANCE_MARKER_HOST"')
        exhaustive_index = script.index("  --exhaustive")
        public_block = script[script.index('  --site-url "$PUBLIC_SITE_URL"') :]
        self.assertLess(exhaustive_index, marker_removal)
        self.assertNotIn("  --exhaustive", public_block)
        self.assertNotIn("--skip-public-write-protection", public_block)
        self.assertIn("check_public_read_protection", connector)
        self.assertIn("check_public_write_protection", connector)
        self.assertNotIn("skip-anonymous-check", gateway_check)
        self.assertIn("response.status_code in {401, 403}", gateway_check)

    def test_deploy_keeps_maintenance_for_all_checks_and_reopens_as_final_action(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")

        internal_connector_index = script.index("  --skip-public-site")
        internal_gateway_index = script.index("  --mcp-url http://127.0.0.1:41831/mcp")
        marker_removal = script.index('run_release rm -f "$MAINTENANCE_MARKER_HOST"')
        exhaustive_index = script.index("  --exhaustive")
        public_connector_index = script.index('  --site-url "$PUBLIC_SITE_URL"')
        public_gateway_index = script.index('  --mcp-url "$PUBLIC_MCP_URL"', public_connector_index)
        deployment_succeeded = script.index("deployment_succeeded=1", marker_removal)

        self.assertLess(internal_connector_index, marker_removal)
        self.assertLess(internal_gateway_index, marker_removal)
        self.assertLess(exhaustive_index, marker_removal)
        self.assertIn("  --maintenance-safe", script[:marker_removal])
        self.assertIn('  --release-revision "$crm_revision"', script[:marker_removal])
        self.assertIn('  --release-attempt-id "$release_id"', script[:marker_removal])
        self.assertLess(public_connector_index, marker_removal)
        self.assertLess(public_gateway_index, marker_removal)
        self.assertLess(marker_removal, deployment_succeeded)
        self.assertEqual(script.count('run_release rm -f "$MAINTENANCE_MARKER_HOST"'), 1)
        self.assertLess(public_connector_index, marker_removal)

    def test_rollback_rearms_marker_and_restores_crm_before_manager_fuser_gate(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")
        rollback_start = script.index("rollback_release()")
        rollback = script[rollback_start : script.index("\non_exit() {", rollback_start)]

        marker = rollback.index('install -D -m 600 /dev/null "$MAINTENANCE_MARKER_HOST"')
        stop = rollback.index("docker compose stop")
        crm_restore = rollback.index("restore-crm-changed --backup-dir")
        fuser = rollback.index('fuser "$MANAGER_DB"')
        manager_restore = rollback.index("restore-manager-changed --backup-dir")
        self.assertLess(marker, stop)
        self.assertLess(crm_restore, fuser)
        self.assertLess(fuser, manager_restore)

    def test_incomplete_rollback_never_reopens_public_writes(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")
        rollback_start = script.index("rollback_release()")
        rollback = script[rollback_start : script.index("\non_exit() {", rollback_start)]

        marker_removal = rollback.index('run_maintenance rm -f "$MAINTENANCE_MARKER_HOST"')
        marker_rearm = rollback.index('install -D -m 600 /dev/null "$MAINTENANCE_MARKER_HOST"')
        stop = rollback.index("docker compose stop")
        stable_tag = rollback.index('docker tag "$rollback_image" "$STABLE_IMAGE"')
        crm_restore = rollback.index("restore-crm-changed --backup-dir")
        health = rollback.index('wait_for_health "$rollback_image"')
        self.assertIn(
            "timeout --signal=TERM --kill-after=5 30s \\\n"
            '    docker tag "$rollback_image" "$STABLE_IMAGE" </dev/null',
            rollback,
        )
        self.assertLess(marker_rearm, stable_tag)
        self.assertLess(stable_tag, stop)
        self.assertLess(stable_tag, crm_restore)
        self.assertLess(stable_tag, health)
        self.assertIn(
            "if (( rollback_ok == 1 )); then\n"
            '      run_maintenance rm -f "$MAINTENANCE_MARKER_HOST" '
            "|| rollback_ok=0\n"
            "    fi",
            rollback,
        )
        self.assertLess(stable_tag, marker_removal)
        self.assertIn("ROLLBACK INCOMPLETE: maintenance marker remains", rollback)

    @unittest.skipUnless(_posix_bash_available(), "a working POSIX bash is required")
    def test_failed_rollback_stop_never_touches_protected_state(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")
        rollback_start = script.index("rollback_release()")
        rollback = script[rollback_start : script.index("\non_exit() {", rollback_start)]

        stop_guard = rollback.index(
            'if ! run_maintenance env AUTOSTOP_RELEASE_IMAGE="$release_image"'
        )
        abort = rollback.index('return "$original_status"', stop_guard)
        crm_restore = rollback.index("restore-crm-changed --backup-dir")
        manager_restore = rollback.index("restore-manager-changed --backup-dir")
        self.assertLess(stop_guard, abort)
        self.assertLess(abort, crm_restore)
        self.assertLess(abort, manager_restore)
        self.assertIn("protected data remains untouched and maintenance stays active", rollback)

        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "calls.log"
            marker_path = Path(temp_dir) / "maintenance"
            harness = f"""
set -u
MAINTENANCE_MARKER_HOST={marker_path!s}
release_image=sha256:{"1" * 64}
rollback_image=autostopcrm-rollback:test
STABLE_IMAGE=autostopcrm:stable
SERVICE_NAME=autostopcrm
backup_dir=/does/not-matter
MANAGER_DB=/does/not-matter.sqlite3
PYTHON_BIN=python3
previous_manager_dir=/does/not-matter-manager
rollback_active=0
install() {{ : > "$MAINTENANCE_MARKER_HOST"; }}
run_maintenance() {{
  printf '%s\n' "$*" >> {log_path!s}
  if [[ "$*" == *"docker compose stop"* ]]; then return 1; fi
  return 0
}}
restore_auth_configuration() {{ printf '%s\n' restore-auth >> {log_path!s}; }}
activate_manager_snapshot() {{ printf '%s\n' activate-manager >> {log_path!s}; }}
wait_for_health() {{ printf '%s\n' wait-health >> {log_path!s}; return 0; }}
{rollback}
if rollback_release 17; then
  status=0
else
  status=$?
fi
printf 'status=%s\n' "$status"
"""
            completed = subprocess.run(
                ["bash", "-c", harness],
                check=True,
                capture_output=True,
                text=True,
            )
            calls = log_path.read_text(encoding="utf-8")

        self.assertIn("status=17", completed.stdout)
        self.assertIn("docker compose stop", calls)
        for forbidden_call in (
            "agent_release_backup.py",
            "restore-auth",
            "activate-manager",
            "wait-health",
            "docker compose up",
        ):
            self.assertNotIn(forbidden_call, calls)

    def test_premaintenance_attempt_artifacts_have_early_exact_cleanup(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")

        early_trap = script.index("trap premaintenance_on_exit EXIT")
        manager_snapshot = script.index(
            'snapshot_manager_commit "$MANAGER_SOURCE_DIR" "$manager_release_dir"'
        )
        image_build = script.index('--tag "$release_image_tag" -')
        rollback_tag = script.index('docker tag "$previous_image_id" "$rollback_image"')
        full_trap = script.index("trap on_exit EXIT")
        self.assertLess(early_trap, manager_snapshot)
        self.assertLess(early_trap, image_build)
        self.assertLess(early_trap, rollback_tag)
        self.assertLess(manager_snapshot, full_trap)
        self.assertLess(image_build, full_trap)
        self.assertLess(rollback_tag, full_trap)
        self.assertLess(
            script.index("manager_attempt_cleanup_authorized=1"),
            script.index('mkdir "$staging_dir"'),
        )
        self.assertLess(script.index("release_image_tag_cleanup_authorized=1"), image_build)
        self.assertLess(script.index("rollback_image_cleanup_authorized=1"), rollback_tag)
        cleanup = script[
            script.index("cleanup_owned_premaintenance_artifacts()") : script.index(
                "premaintenance_on_exit()"
            )
        ]
        self.assertIn("cleanup-attempt", cleanup)
        self.assertIn('--owned-manager-path "$owned_manager_path"', cleanup)
        self.assertIn('--protected-manager-path "$previous_manager_dir"', cleanup)
        self.assertIn('--protected-image-tag "$STABLE_IMAGE"', cleanup)
        self.assertIn('--restore-image-tag "$release_image_tag"', cleanup)
        on_exit_start = script.index("\non_exit() {")
        on_exit = script[on_exit_start : script.index("trap on_exit EXIT", on_exit_start)]
        self.assertIn("maintenance_started == 0", on_exit)
        self.assertIn("cleanup_owned_premaintenance_artifacts", on_exit)

    def test_watchdog_precedes_final_reopen_and_retention_is_post_success_only(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")

        watchdog = script.index(
            'run_release bash "$ROOT_DIR/scripts/install_production_watchdog.sh"'
        )
        marker_removal = script.index('run_release rm -f "$MAINTENANCE_MARKER_HOST"')
        marked_success = script.index("deployment_succeeded=1", marker_removal)
        trap_removed = script.index("trap - EXIT", marked_success)
        retention = script.index("scripts/agent_release_retention.py prune", trap_removed)
        self.assertLess(watchdog, marker_removal)
        self.assertLess(marker_removal, marked_success)
        self.assertLess(marked_success, trap_removed)
        self.assertLess(trap_removed, retention)
        self.assertIn("post-success release retention failed", script[retention:])
        for protected in (
            '"$backup_dir"',
            '"$manager_release_dir"',
            '"$previous_manager_dir"',
            '"$release_image_tag"',
            '"$rollback_image"',
            '"$STABLE_IMAGE"',
        ):
            self.assertIn(protected, script[retention:])

    def test_production_compose_is_fail_closed_and_exposes_kill_switches(self) -> None:
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn('AUTOSTOP_DEPLOYMENT_ENV: "production"', compose)
        self.assertIn("AUTOSTOP_MCP_OAUTH_ENABLED", compose)
        self.assertIn("AUTOSTOP_MCP_OAUTH_STATE_KEY", compose)
        self.assertIn('AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED: "0"', compose)
        self.assertNotIn('AUTOSTOP_DEPLOYMENT_ENV: "${', compose)
        self.assertIn("MINIMAL_KANBAN_MCP_BEARER_TOKEN", compose)
        self.assertIn("AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED", compose)
        self.assertIn("AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED", compose)
        self.assertIn("AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED", compose)
        self.assertNotIn("AUTOSTOP_AGENT_GATEWAY_ENABLED:-1", compose)
        self.assertNotIn("AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED:-1", compose)
        self.assertIn("${AUTOSTOP_AGENT_GATEWAY_ENABLED:?set explicitly to 0 or 1}", compose)
        self.assertIn(
            "${AUTOSTOP_CRAWL4AI_API_TOKEN:?provision a dedicated Crawl4AI API token}",
            compose,
        )
        self.assertIn(
            "${AUTOSTOP_CRAWL4AI_SECRET_KEY:?provision a dedicated Crawl4AI secret key}",
            compose,
        )
        self.assertNotIn("autostop-local-crawl4ai-token-change-me", compose)
        self.assertNotIn("autostop-local-crawl4ai-secret-change-me", compose)
        deploy_script = (PROJECT_ROOT / "deploy.sh").read_text()
        self.assertIn("validate_gateway_switches", deploy_script)
        self.assertIn("validate_crawl4ai_credentials", deploy_script)
        self.assertIn(
            "AUTOSTOP_CRAWL4AI_API_TOKEN and AUTOSTOP_CRAWL4AI_SECRET_KEY must be distinct.",
            deploy_script,
        )
        self.assertIn("/opt/autostop-manager-releases/current", compose)
        self.assertIn("/opt/AutostopManager}:ro", compose)
        self.assertIn("/opt/AutostopManager}/data", compose)
        self.assertIn("scripts/container_entrypoint.py", dockerfile)

    def test_production_containers_are_bounded_and_non_privileged(self) -> None:
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        deploy_script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")
        desktop_requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
        runtime_requirements = (PROJECT_ROOT / "requirements-runtime.txt").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("searxng/searxng:latest", compose)
        self.assertNotIn("unclecode/crawl4ai:latest", compose)
        self.assertEqual(compose.count("no-new-privileges:true"), 3)
        self.assertEqual(compose.count("driver: local"), 3)
        self.assertGreaterEqual(compose.count("cap_drop:"), 3)
        self.assertIn('user: "977:977"', compose)
        self.assertIn('user: "appuser"', compose)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("/home/autostop/.minimal-kanban", compose)
        self.assertIn("defusedxml==0.7.1", desktop_requirements)
        self.assertIn("defusedxml==0.7.1", runtime_requirements)
        self.assertIn('RUNTIME_UID="${AUTOSTOP_RUNTIME_UID:-10001}"', deploy_script)
        self.assertIn(
            'SEARXNG_RUNTIME_UID="${AUTOSTOP_SEARXNG_RUNTIME_UID:-977}"',
            deploy_script,
        )
        self.assertIn(
            'run_release chown -R "$RUNTIME_UID:$RUNTIME_GID" "$CRM_DATA_DIR" "$(dirname "$MANAGER_DB")"',
            deploy_script,
        )
        self.assertIn(
            'run_release chown -R "$SEARXNG_RUNTIME_UID:$SEARXNG_RUNTIME_GID" "$searxng_dir"',
            deploy_script,
        )
        self.assertIn(
            'export AUTOSTOP_MAINTENANCE_MARKER="/home/autostop/.minimal-kanban/.agent-gateway-maintenance"',
            deploy_script,
        )

    def test_deploy_provisions_and_verifies_owner_approved_oauth(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")

        self.assertIn("scripts/configure_mcp_oauth.py ensure", script)
        self.assertIn("scripts/check_mcp_oauth.py", script)
        self.assertIn('export AUTOSTOP_MCP_OAUTH_ENABLED="1"', script)
        self.assertIn('export AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED="0"', script)
        self.assertLess(
            script.index("scripts/configure_mcp_oauth.py ensure"),
            script.index("docker compose config --quiet"),
        )

    def test_deploy_lock_file_is_ignored(self) -> None:
        gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn(".autostop-deploy.lock", gitignore)

    def test_dockerfile_installs_qt_webengine_pdf_dependencies(self) -> None:
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

        for package in (
            "fonts-dejavu-core",
            "libgbm1",
            "libnspr4",
            "libnss3",
            "libasound2t64",
            "libgssapi-krb5-2",
            "libx11-xcb1",
            "libxcomposite1",
            "libxdamage1",
            "libxfixes3",
            "libxrender1",
            "libxshmfence1",
            "libxcb-cursor0",
            "libxcb-icccm4",
            "libxcb-image0",
            "libxcb-keysyms1",
            "libxtst6",
            "libxkbfile1",
            "libxrandr2",
        ):
            with self.subTest(package=package):
                self.assertIn(package, dockerfile)

    def test_dockerfile_installs_runtime_requirements_only(self) -> None:
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        runtime_requirements = (PROJECT_ROOT / "requirements-runtime.txt").read_text(
            encoding="utf-8"
        )

        self.assertIn("requirements-runtime.txt", dockerfile)
        self.assertNotIn("pyinstaller", runtime_requirements.lower())

    def test_dockerfile_installs_agent_browser_runtime(self) -> None:
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        runtime_requirements = (PROJECT_ROOT / "requirements-runtime.txt").read_text(
            encoding="utf-8"
        )

        self.assertIn("playwright", runtime_requirements)
        self.assertIn("python -m playwright install --with-deps chromium", dockerfile)

    def test_dockerignore_excludes_server_local_vpn_artifacts(self) -> None:
        dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

        self.assertIn("amnezia*", dockerignore)
        self.assertIn("audit_autostopvpn.ps1", dockerignore)
        self.assertIn("remove_autostopvpn.ps1", dockerignore)

    def test_docker_runtime_png_assets_are_exactly_allowlisted_and_verified(self) -> None:
        dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        rules = [
            line.strip()
            for line in dockerignore.splitlines()
            if line.strip() and not line.startswith("#")
        ]
        png_allowlist = [
            "!src/minimal_kanban/static/favicon.png",
            "!src/minimal_kanban/printing/assets/autostop_brand_logo.png",
        ]

        recursive_exclusion = rules.index("**/*.png")
        self.assertNotIn("*.png", rules)
        self.assertEqual(
            png_allowlist,
            [rule for rule in rules if rule.startswith("!") and rule.endswith(".png")],
        )
        for rule in png_allowlist:
            with self.subTest(rule=rule):
                self.assertGreater(rules.index(rule), recursive_exclusion)

        copy_index = dockerfile.index("COPY . .")
        user_index = dockerfile.index("USER 10001:10001")
        for asset in (
            "/app/src/minimal_kanban/static/favicon.png",
            "/app/src/minimal_kanban/printing/assets/autostop_brand_logo.png",
        ):
            with self.subTest(asset=asset):
                assertion_index = dockerfile.index(f"test -s {asset}")
                self.assertGreater(assertion_index, copy_index)
                self.assertLess(assertion_index, user_index)

    def test_github_actions_builds_and_checks_unpublishable_runtime_candidate(self) -> None:
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("\n  docker-runtime-assets:\n", workflow)
        self.assertIn("mkdir -p output/docker-context-probe", workflow)
        self.assertIn(
            "printf 'excluded-by-dockerignore\\n' > output/docker-context-probe/ignored.png",
            workflow,
        )
        self.assertIn('docker build --tag "autostopcrm-ci:${GITHUB_SHA}" .', workflow)
        self.assertIn("docker run --rm", workflow)
        self.assertIn("test -s /app/src/minimal_kanban/static/favicon.png", workflow)
        self.assertIn(
            "test -s /app/src/minimal_kanban/printing/assets/autostop_brand_logo.png",
            workflow,
        )
        self.assertIn("test ! -e /app/output/docker-context-probe/ignored.png", workflow)
        self.assertNotIn("docker login", workflow)
        self.assertNotIn("docker push", workflow)

    def test_github_actions_quality_workflow_runs_release_gates(self) -> None:
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )
        requirements_dev = (PROJECT_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")

        self.assertIn("defaults:\n      run:\n        shell: bash", workflow)
        self.assertIn("python -m pip install -r requirements-runtime.txt", workflow)
        self.assertIn("Validate production Compose configuration", workflow)
        self.assertIn("docker compose config --quiet", workflow)
        self.assertIn("AUTOSTOP_CRAWL4AI_API_TOKEN", workflow)
        self.assertIn("AUTOSTOP_CRAWL4AI_SECRET_KEY", workflow)
        # GitHub's explicit bash invocation adds `-o pipefail`; without it the
        # exit status of tee could turn a failed Python quality check green.
        for artifact in (
            "perf-probe-local.json",
            "perf-stage1.json",
            "browser-smoke-core.json",
            "browser-smoke-full.json",
            "perf-probe.json",
            "finance-audit.json",
        ):
            with self.subTest(artifact=artifact):
                self.assertIn(f"| tee {artifact}", workflow)
        self.assertIn("ruff format --check .", workflow)
        self.assertIn("ruff check .", workflow)
        self.assertIn("python scripts/docs_audit.py --format text", workflow)
        self.assertIn("coverage run -m unittest discover -s tests -v", workflow)
        self.assertEqual(1, workflow.count("unittest discover -s tests -v"))
        self.assertIn("python scripts/coverage_audit.py --format text", workflow)
        self.assertIn("coverage-runtime.xml", workflow)
        self.assertIn("htmlcov/", workflow)
        self.assertIn("python scripts/code_health_audit.py --format text", workflow)
        self.assertIn("python scripts/audit_localization.py", workflow)
        self.assertIn("python scripts/check_web_assets_js.py", workflow)
        self.assertIn("python scripts/perf_probe.py", workflow)
        self.assertIn("--local-temp-server", workflow)
        self.assertIn("perf-probe-local.json", workflow)
        self.assertIn("python scripts/finance_audit_report.py", workflow)
        self.assertIn("browser_smoke", workflow)
        self.assertIn("poppler-utils", workflow)
        self.assertIn("python -m playwright install --with-deps chromium", workflow)
        self.assertIn(
            "python scripts/browser_smoke.py --profile core --attempts 1",
            workflow,
        )
        self.assertIn(
            "python scripts/browser_smoke.py --profile full --attempts 1",
            workflow,
        )
        self.assertIn("playwright", requirements_dev)
        self.assertIn("coverage==", requirements_dev)

    def test_runbook_and_readme_document_quality_gates(self) -> None:
        runbook = (PROJECT_ROOT / "docs" / "OPERATIONS_RUNBOOK.md").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("Release Checklist", runbook)
        self.assertIn("Finance Audit-First", runbook)
        self.assertIn("Performance Smoke", runbook)
        self.assertIn("scripts\\finance_audit_report.py", runbook)
        self.assertIn("scripts\\perf_probe.py", runbook)
        self.assertIn("--token-env MINIMAL_KANBAN_MCP_BEARER_TOKEN", runbook)
        self.assertNotIn("scripts\\perf_probe.py --base-url https://crm.autostopcrm.ru", runbook)
        self.assertIn("AUTOSTOP_SMOKE_OPERATOR_USERNAME", runbook)
        self.assertIn("AUTOSTOP_SMOKE_OPERATOR_PASSWORD", runbook)
        self.assertIn("AUTOSTOP_CRAWL4AI_API_TOKEN", runbook)
        self.assertIn("AUTOSTOP_CRAWL4AI_SECRET_KEY", runbook)
        self.assertIn("Crawl4AI credential is absent or they", runbook)
        self.assertNotIn("--operator-username admin --operator-password admin", runbook)
        self.assertIn("docs/OPERATIONS_RUNBOOK.md", readme)

    def test_prepare_release_generates_current_start_guide(self) -> None:
        script = (PROJECT_ROOT / "scripts" / "prepare_release.ps1").read_text(encoding="utf-8")

        self.assertIn("This release build runs without installation.", script)
        self.assertIn("Log in as an operator when the browser workspace opens.", script)
        self.assertIn("Do not start Python, Node.js, npm, or Docker manually", script)
        self.assertIn("MCP starts automatically when enabled in settings", script)
        self.assertIn("docs/OPERATIONS_RUNBOOK.md", script)
        self.assertNotIn("No terminal, npm, python, node, or manual commands are required.", script)


if __name__ == "__main__":
    unittest.main()
