import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeployScriptTests(unittest.TestCase):
    def test_deploy_script_syncs_active_v1_branch_by_default(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")

        self.assertIn('DEPLOY_BRANCH="${AUTOSTOP_DEPLOY_BRANCH:-autostopcrm-v1}"', script)
        self.assertIn('DEPLOY_REMOTE="${AUTOSTOP_DEPLOY_REMOTE:-origin}"', script)
        self.assertIn('git fetch "$DEPLOY_REMOTE" "$DEPLOY_BRANCH"', script)
        self.assertIn("git reset --hard FETCH_HEAD", script)

    def test_deploy_loads_server_local_env_before_smoke_credentials(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")

        self.assertIn('if [[ -f "$ROOT_DIR/.env" ]]; then', script)
        self.assertLess(
            script.index('if [[ -f "$ROOT_DIR/.env" ]]; then'),
            script.index('SMOKE_OPERATOR_USERNAME="${AUTOSTOP_SMOKE_OPERATOR_USERNAME:?'),
        )
        self.assertIn(
            'SMOKE_OPERATOR_USERNAME="${AUTOSTOP_SMOKE_OPERATOR_USERNAME:?set smoke username}"',
            script,
        )
        self.assertIn(
            'SMOKE_OPERATOR_PASSWORD="${AUTOSTOP_SMOKE_OPERATOR_PASSWORD:?set smoke password}"',
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

    def test_deploy_installs_production_watchdog_timer_by_default(self) -> None:
        script = (PROJECT_ROOT / "deploy.sh").read_text(encoding="utf-8")
        installer = (PROJECT_ROOT / "scripts" / "install_production_watchdog.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('INSTALL_WATCHDOG="${AUTOSTOP_INSTALL_WATCHDOG:-1}"', script)
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

    def test_dockerignore_excludes_server_local_vpn_artifacts(self) -> None:
        dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

        self.assertIn("amnezia*", dockerignore)
        self.assertIn("audit_autostopvpn.ps1", dockerignore)
        self.assertIn("remove_autostopvpn.ps1", dockerignore)

    def test_github_actions_quality_workflow_runs_release_gates(self) -> None:
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )
        requirements_dev = (PROJECT_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")

        self.assertIn("ruff format --check .", workflow)
        self.assertIn("ruff check .", workflow)
        self.assertIn("python scripts/docs_audit.py --format text", workflow)
        self.assertIn("python -m unittest discover -s tests -v", workflow)
        self.assertIn("python scripts/code_health_audit.py --format text", workflow)
        self.assertIn("python scripts/audit_localization.py", workflow)
        self.assertIn("python scripts/check_web_assets_js.py", workflow)
        self.assertIn("python scripts/perf_probe.py", workflow)
        self.assertIn("--local-temp-server", workflow)
        self.assertIn("perf-probe-local.json", workflow)
        self.assertIn("python scripts/finance_audit_report.py", workflow)
        self.assertIn("browser_smoke", workflow)
        self.assertIn("python -m playwright install chromium", workflow)
        self.assertIn("python scripts/browser_smoke.py", workflow)
        self.assertIn("playwright", requirements_dev)

    def test_runbook_and_readme_document_quality_gates(self) -> None:
        runbook = (PROJECT_ROOT / "docs" / "OPERATIONS_RUNBOOK.md").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("Release Checklist", runbook)
        self.assertIn("Finance Audit-First", runbook)
        self.assertIn("Performance Smoke", runbook)
        self.assertIn("scripts\\finance_audit_report.py", runbook)
        self.assertIn("scripts\\perf_probe.py", runbook)
        self.assertIn("AUTOSTOP_SMOKE_OPERATOR_USERNAME", runbook)
        self.assertIn("AUTOSTOP_SMOKE_OPERATOR_PASSWORD", runbook)
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
