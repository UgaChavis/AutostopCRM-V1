import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _resolved_requirements(name: str) -> str:
    lines = []
    for line in (PROJECT_ROOT / name).read_text(encoding="utf-8").splitlines():
        if line.startswith("-r "):
            lines.append(_resolved_requirements(line[3:].strip()))
        else:
            lines.append(line)
    return "\n".join(lines)


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
    def test_retired_deploy_entrypoint_exits_before_reading_config_or_running_commands(
        self,
    ) -> None:
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("a working POSIX bash is required")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            effect = root / "unexpected-effect"
            (root / ".env").write_text(f"touch {effect}\n", encoding="utf-8")
            result = subprocess.run(
                [bash, str(PROJECT_ROOT / "deploy.sh"), "--forward-j1", "--apply"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("retired", result.stderr)
            self.assertIn("OPERATIONS_RUNBOOK.md#deploy", result.stderr)
            self.assertFalse(effect.exists())

    def test_dependency_pins_are_shared_without_build_tools_in_runtime(self) -> None:
        manifests = sorted(PROJECT_ROOT.glob("requirements*.txt"))
        pins = [
            line.split("==", 1)[0].casefold()
            for manifest in manifests
            for line in manifest.read_text(encoding="utf-8").splitlines()
            if "==" in line
        ]
        self.assertEqual(len(pins), len(set(pins)))
        runtime = _resolved_requirements("requirements-runtime.txt")
        desktop = _resolved_requirements("requirements.txt")
        development = _resolved_requirements("requirements-dev.txt")
        self.assertIn("playwright==", runtime)
        self.assertNotIn("pyinstaller==", runtime)
        self.assertNotIn("coverage==", runtime)
        self.assertIn("pyinstaller==", desktop)
        self.assertIn("coverage==", development)
        self.assertIn(
            "COPY requirements-common.txt requirements-runtime.txt ./",
            (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8"),
        )

    def test_setup_dev_installs_the_full_dependency_graph_once(self) -> None:
        script = (PROJECT_ROOT / "scripts" / "setup_dev.ps1").read_text(encoding="utf-8")
        bootstrap = (PROJECT_ROOT / "scripts" / "python_bootstrap.ps1").read_text(encoding="utf-8")

        self.assertIn("$installRequirementsPath = if (Test-Path $devRequirementsPath)", script)
        self.assertIn("$devRequirementsPath\n} else {\n    $requirementsPath", script)
        self.assertEqual(1, script.count("-m pip install -r"))
        self.assertIn("-m pip install -r $installRequirementsPath", script)
        self.assertIn("function Get-ProjectVirtualEnvironmentPythonPath", bootstrap)
        self.assertIn('"Scripts\\python.exe"', bootstrap)
        self.assertIn('"bin/python"', bootstrap)
        for script_name in (
            "build_app.ps1",
            "doctor.ps1",
            "run_checks.ps1",
            "run_dev.ps1",
            "run_mcp_server.ps1",
            "setup_dev.ps1",
        ):
            script_text = (PROJECT_ROOT / "scripts" / script_name).read_text(encoding="utf-8")
            self.assertIn(
                "Get-ProjectVirtualEnvironmentPythonPath -VenvPath $venvPath",
                script_text,
                script_name,
            )

    def test_ci_parallel_checks_have_a_fail_closed_aggregate(self) -> None:
        workflow = (PROJECT_ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8")
        self.assertIn("suite: [static, unit, browser-performance]", workflow)
        self.assertIn("fail-fast: false", workflow)
        self.assertIn("cache: pip", workflow)
        self.assertIn("path: ~/.cache/ms-playwright", workflow)
        aggregate = workflow[workflow.index("\n  quality:\n") :]
        self.assertIn("if: ${{ always() }}", aggregate)
        self.assertIn("needs: [checks, docker-runtime-assets]", aggregate)
        self.assertIn('test "$CHECKS_RESULT" = success', aggregate)
        self.assertIn('test "$DOCKER_RESULT" = success', aggregate)

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

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose CLI is required")
    def test_production_compose_resolves_without_legacy_crawler_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("", encoding="utf-8")
            environment = {"PATH": os.defpath, "HOME": directory}
            # Docker Desktop installs Compose next to its CLI, and normally
            # discovers it through the user's config. Keep that config private
            # while providing the installed plugin directory to this temp run.
            docker = Path(shutil.which("docker") or "docker")
            plugin_candidates = [
                docker.parent / "cli-plugins",
                docker.parent.parent / "cli-plugins",
            ]
            if os.environ.get("CRM_REHEARSAL_COMPOSE_DIR"):
                plugin_candidates.insert(0, Path(os.environ["CRM_REHEARSAL_COMPOSE_DIR"]))
            environment["DOCKER_CONFIG"] = directory
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "cliPluginsExtraDirs": [
                            str(path) for path in plugin_candidates if path.is_dir()
                        ]
                    }
                ),
                encoding="utf-8",
            )
            environment["AUTOSTOP_MCP_OAUTH_STATE_KEY"] = "synthetic-oauth-state-key"
            for suffix in (
                "ENABLED",
                "WRITES_ENABLED",
                "FINANCE_ENABLED",
                "MAIL_ENABLED",
                "DESTRUCTIVE_ENABLED",
                "RAW_ENABLED",
            ):
                environment[f"AUTOSTOP_AGENT_GATEWAY_{suffix}"] = "0"
            completed = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--project-directory",
                    directory,
                    "--file",
                    str(PROJECT_ROOT / "docker-compose.yml"),
                    "config",
                    "--format",
                    "json",
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=20,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        services = json.loads(completed.stdout)["services"]
        self.assertEqual(set(services), {"autostopcrm", "searxng"})
        self.assertEqual(
            services["autostopcrm"]["depends_on"],
            {"searxng": {"condition": "service_healthy", "required": True}},
        )
        self.assertFalse(any("CRAWL4AI" in key for key in services["autostopcrm"]["environment"]))

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
        self.assertIn("/opt/autostop-manager-releases/current", compose)
        self.assertIn("/opt/AutostopManager}:ro", compose)
        self.assertIn("/opt/AutostopManager}/data", compose)
        self.assertIn("scripts/container_entrypoint.py", dockerfile)

    def test_production_containers_are_bounded_and_non_privileged(self) -> None:
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        desktop_requirements = _resolved_requirements("requirements.txt")
        runtime_requirements = _resolved_requirements("requirements-runtime.txt")

        self.assertNotIn("searxng/searxng:latest", compose)
        self.assertNotIn("unclecode/crawl4ai:latest", compose)
        self.assertEqual(compose.count("no-new-privileges:true"), 2)
        self.assertEqual(compose.count("driver: local"), 2)
        self.assertGreaterEqual(compose.count("cap_drop:"), 2)
        self.assertIn('user: "977:977"', compose)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("/home/autostop/.minimal-kanban", compose)
        self.assertIn("defusedxml==0.7.1", desktop_requirements)
        self.assertIn("defusedxml==0.7.1", runtime_requirements)

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
        for asset in ("/app/src/minimal_kanban/static/favicon.png",):
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
        self.assertIn("uses: docker/build-push-action@v7", workflow)
        self.assertIn("context: .", workflow)
        self.assertIn("load: true", workflow)
        self.assertIn("push: false", workflow)
        self.assertIn("tags: autostopcrm-ci:${{ github.sha }}", workflow)
        self.assertIn("cache-to: type=gha,scope=crm-runtime,mode=max", workflow)
        self.assertIn("docker run --rm", workflow)
        self.assertIn("test -s /app/src/minimal_kanban/static/favicon.png", workflow)
        self.assertIn("test ! -e /app/output/docker-context-probe/ignored.png", workflow)
        self.assertNotIn("docker login", workflow)
        self.assertNotIn("docker push", workflow)

    def test_github_actions_quality_workflow_runs_release_gates(self) -> None:
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )
        requirements_dev = _resolved_requirements("requirements-dev.txt")

        self.assertIn("defaults:\n      run:\n        shell: bash", workflow)
        self.assertIn("python -m pip install -r requirements-dev.txt", workflow)
        self.assertIn("Validate production Compose configuration", workflow)
        self.assertIn("docker compose config --quiet", workflow)
        self.assertNotIn("AUTOSTOP_CRAWL4AI_API_TOKEN", workflow)
        self.assertNotIn("AUTOSTOP_CRAWL4AI_SECRET_KEY", workflow)
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
        self.assertIn("python -m tests.runtime_suite_runner", workflow)
        self.assertEqual(1, workflow.count("python -m tests.runtime_suite_runner"))
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

    def test_local_ci_profile_matches_mandatory_hosted_quality_gates(self) -> None:
        profile = (PROJECT_ROOT / "scripts" / "run_checks.ps1").read_text(encoding="utf-8")
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "quality.yml").read_text(
            encoding="utf-8"
        )
        ci_profile = profile[profile.index("$managedEnvironmentNames = @(") :]
        workflow_flat = re.sub(r"\\\s*\n", " ", workflow)
        workflow_flat = re.sub(r"\s+", " ", workflow_flat)

        def workflow_step_block(step_name: str) -> str:
            pattern = re.compile(
                rf"^      - name: {re.escape(step_name)}\n.*?"
                r"(?=^      - name: |\Z)",
                re.DOTALL | re.MULTILINE,
            )
            matches = pattern.findall(workflow)
            self.assertEqual(1, len(matches), step_name)
            return matches[0]

        def step_args(step_name: str) -> tuple[str, ...]:
            pattern = re.compile(
                rf'Invoke-Python\s+-StepName\s+"{re.escape(step_name)}"\s+'
                r"-Arguments\s+@\((?P<args>.*?)\)",
                re.DOTALL,
            )
            matches = list(pattern.finditer(ci_profile))
            self.assertEqual(1, len(matches), step_name)
            return tuple(re.findall(r'"([^"]*)"', matches[0].group("args")))

        self.assertIn('[ValidateSet("changed", "ci")]', profile)
        self.assertIn('$Profile = "changed"', profile)
        self.assertEqual(2, profile.count(") + $targets)"))
        expected_steps = (
            (
                "Full Ruff format",
                ("-m", "ruff", "format", "--check", "."),
                "ruff format --check .",
            ),
            ("Full Ruff lint", ("-m", "ruff", "check", "."), "ruff check ."),
            (
                "Documentation audit",
                ("scripts/docs_audit.py", "--format", "text"),
                "python scripts/docs_audit.py --format text",
            ),
            (
                "Erase stale runtime coverage",
                ("-m", "coverage", "erase"),
                "coverage erase",
            ),
            (
                "Isolated runtime branch coverage",
                ("-m", "tests.runtime_suite_runner"),
                "python -m tests.runtime_suite_runner",
            ),
            (
                "Combine runtime coverage",
                ("-m", "coverage", "combine"),
                "coverage combine",
            ),
            (
                "Report runtime coverage",
                ("-m", "coverage", "report", "--show-missing"),
                "coverage report --show-missing",
            ),
            (
                "Write runtime coverage JSON",
                ("-m", "coverage", "json", "-o", "coverage-runtime.json"),
                "coverage json -o coverage-runtime.json",
            ),
            (
                "Write runtime coverage XML",
                ("-m", "coverage", "xml", "-o", "coverage-runtime.xml"),
                "coverage xml -o coverage-runtime.xml",
            ),
            (
                "Write runtime coverage HTML",
                ("-m", "coverage", "html", "-d", "htmlcov"),
                "coverage html -d htmlcov",
            ),
            (
                "Erase stale release coverage",
                ("-m", "coverage", "erase"),
                "coverage erase",
            ),
            (
                "Release branch coverage",
                (
                    "-m",
                    "coverage",
                    "run",
                    "--source=scripts",
                    "-m",
                    "unittest",
                    "tests.test_agent_release_backup",
                    "tests.test_agent_release_retention",
                    "-v",
                ),
                "coverage run --source=scripts -m unittest tests.test_agent_release_backup "
                "tests.test_agent_release_retention -v",
            ),
            (
                "Combine release coverage",
                ("-m", "coverage", "combine"),
                "coverage combine",
            ),
            (
                "Report release coverage",
                ("-m", "coverage", "report", "--show-missing"),
                "coverage report --show-missing",
            ),
            (
                "Write release coverage JSON",
                ("-m", "coverage", "json", "-o", "coverage-release.json"),
                "coverage json -o coverage-release.json",
            ),
            (
                "Coverage ratchet",
                ("scripts/coverage_audit.py", "--format", "text"),
                "python scripts/coverage_audit.py --format text",
            ),
            (
                "Repository code health",
                ("scripts/code_health_audit.py", "--format", "text"),
                "python scripts/code_health_audit.py --format text",
            ),
            (
                "Localization audit",
                ("scripts/audit_localization.py",),
                "python scripts/audit_localization.py",
            ),
            (
                "Generated browser JavaScript",
                ("scripts/check_web_assets_js.py",),
                "python scripts/check_web_assets_js.py",
            ),
            (
                "CRM capability parity",
                ("scripts/crm_capability_parity.py", "--require-complete"),
                "python scripts/crm_capability_parity.py --require-complete",
            ),
            (
                "Change-feed producer parity",
                ("scripts/crm_change_feed_producer_parity.py", "--require-complete"),
                "python scripts/crm_change_feed_producer_parity.py --require-complete",
            ),
            (
                "Mandatory core browser smoke",
                ("scripts/browser_smoke.py", "--profile", "core", "--attempts", "1"),
                "python scripts/browser_smoke.py --profile core --attempts 1",
            ),
            (
                "Compile release probe scripts",
                (
                    "-m",
                    "py_compile",
                    "scripts/perf_probe.py",
                    "scripts/perf_mcp.py",
                    "scripts/perf_workflows.py",
                    "scripts/finance_audit_report.py",
                    "scripts/browser_smoke.py",
                ),
                "python -m py_compile scripts/perf_probe.py scripts/perf_mcp.py "
                "scripts/perf_workflows.py scripts/finance_audit_report.py "
                "scripts/browser_smoke.py",
            ),
            (
                "Local temp performance probe",
                (
                    "scripts/perf_probe.py",
                    "--local-temp-server",
                    "--iterations",
                    "1",
                    "--max-snapshot-gzip-ms",
                    "1200",
                    "--max-snapshot-gzip-bytes",
                    "120000",
                    "--max-revision-ms",
                    "800",
                    "--max-get-card-ms",
                    "800",
                ),
                "python scripts/perf_probe.py --local-temp-server --iterations 1 "
                "--max-snapshot-gzip-ms 1200 --max-snapshot-gzip-bytes 120000 "
                "--max-revision-ms 800 --max-get-card-ms 800",
            ),
            (
                "Stage 1 production-scale performance gates",
                (
                    "scripts/perf_workflows.py",
                    "--synthetic-state-profile",
                    "current-production",
                    "--stage1-only",
                    "--skip-browser",
                    "--warmup-iterations",
                    "2",
                    "--iterations",
                    "20",
                    "--max-backend-write-ms",
                    "600",
                    "--max-storage-write-ms",
                    "550",
                    "--max-revision-server-ms",
                    "20",
                    "--max-get-card-direct-ms",
                    "20",
                    "--max-list-cashboxes-ms",
                    "50",
                    "--max-feed-read-ms",
                    "50",
                    "--max-feed-replay-ms",
                    "20",
                ),
                "python scripts/perf_workflows.py --synthetic-state-profile current-production "
                "--stage1-only --skip-browser --warmup-iterations 2 --iterations 20 "
                "--max-backend-write-ms 600 --max-storage-write-ms 550 "
                "--max-revision-server-ms 20 --max-get-card-direct-ms 20 "
                "--max-list-cashboxes-ms 50 --max-feed-read-ms 50 --max-feed-replay-ms 20",
            ),
        )
        local_positions: list[int] = []
        hosted_cursor = 0
        for step_name, expected_args, hosted_command in expected_steps:
            with self.subTest(step_name=step_name):
                self.assertEqual(expected_args, step_args(step_name))
                local_positions.append(ci_profile.index(f'StepName "{step_name}"'))
                hosted_index = workflow_flat.find(hosted_command, hosted_cursor)
                self.assertGreaterEqual(hosted_index, 0, hosted_command)
                hosted_cursor = hosted_index + len(hosted_command)

        self.assertEqual(sorted(local_positions), local_positions)
        self.assertEqual(1, ci_profile.count('"scripts/finance_audit_report.py"'))
        for environment_name in (
            "PYTHONPYCACHEPREFIX",
            "COVERAGE_FILE",
            "QT_QPA_PLATFORM",
            "QTWEBENGINE_DISABLE_SANDBOX",
            "AUTOSTOP_BROWSER_SMOKE_SCREENSHOT_DIR",
        ):
            with self.subTest(environment_name=environment_name):
                self.assertIn(f'"{environment_name}"', ci_profile)
        self.assertIn("$env:PYTHONPYCACHEPREFIX = $ciPycachePath", ci_profile)
        self.assertIn('$env:COVERAGE_FILE = Join-Path $ciPycachePath ".coverage"', ci_profile)
        self.assertIn("finally", ci_profile)
        self.assertIn("SetEnvironmentVariable", ci_profile)
        self.assertIn("StartsWith($tempRootPrefix", ci_profile)
        self.assertIn("Remove-Item -LiteralPath $ciPycachePath -Recurse -Force", ci_profile)
        for forbidden in (
            "https://crm.autostopcrm.ru",
            "--base-url",
            "--mcp-url",
            "check_live_connector.py",
            "deploy.sh",
            "docker compose",
            ".env",
            "Start-Job",
            "Start-ThreadJob",
            "Start-Process",
            "ForEach-Object -Parallel",
            '"full"',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, ci_profile)

        self.assertIn(": > .env", workflow)
        self.assertIn("trap 'rm -f .env' EXIT", workflow)
        self.assertIn("docker compose config --quiet", workflow)

        dispatch_inputs = workflow[workflow.index("    inputs:") : workflow.index("\njobs:")]
        for input_name in ("browser_smoke", "live_probes"):
            with self.subTest(input_name=input_name):
                pattern = re.compile(
                    rf"^      {re.escape(input_name)}:\n.*?"
                    r"(?=^      [a-z][a-z0-9_-]*:|\Z)",
                    re.DOTALL | re.MULTILINE,
                )
                matches = pattern.findall(dispatch_inputs)
                self.assertEqual(1, len(matches))
                self.assertIn('default: "false"', matches[0])

        full_browser_block = workflow_step_block("Optional full browser smoke")
        self.assertIn(
            "if: ${{ matrix.suite == 'browser-performance' && github.event_name == 'workflow_dispatch' && inputs.browser_smoke == 'true' }}",
            full_browser_block,
        )
        self.assertIn(
            "python scripts/browser_smoke.py --profile full --attempts 1",
            full_browser_block,
        )

        live_probe_block = workflow_step_block("Optional read-only live probes")
        self.assertIn(
            "if: ${{ matrix.suite == 'browser-performance' && github.event_name == 'workflow_dispatch' && inputs.live_probes == 'true' }}",
            live_probe_block,
        )
        self.assertIn("--base-url https://crm.autostopcrm.ru", live_probe_block)
        self.assertIn("python scripts/finance_audit_report.py", live_probe_block)
        for hosted_only in (
            "Ubuntu/Python 3.12",
            "production Compose configuration",
            "docker-runtime-assets",
        ):
            with self.subTest(hosted_only=hosted_only):
                self.assertIn(hosted_only, profile)

    def test_runbook_and_readme_document_quality_gates(self) -> None:
        runbook = (PROJECT_ROOT / "docs" / "OPERATIONS_RUNBOOK.md").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        agents = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("Release Checklist", runbook)
        self.assertIn("Finance Audit-First", runbook)
        self.assertIn("Performance Smoke", runbook)
        self.assertIn("scripts\\finance_audit_report.py", runbook)
        self.assertIn("scripts\\perf_probe.py", runbook)
        self.assertIn("--token-env MINIMAL_KANBAN_MCP_BEARER_TOKEN", runbook)
        self.assertNotIn("scripts\\perf_probe.py --base-url https://crm.autostopcrm.ru", runbook)
        self.assertIn("AUTOSTOP_SMOKE_OPERATOR_USERNAME", runbook)
        self.assertIn("AUTOSTOP_SMOKE_OPERATOR_PASSWORD", runbook)
        self.assertIn("one consistent backup of necessary data and settings", runbook)
        self.assertIn("one local isolated", runbook)
        self.assertIn("restore check", runbook)
        self.assertIn("Never automatically replay uncertain operations", runbook)
        self.assertIn("restore an old database over newer working data", runbook)
        self.assertIn("preserving an intermediate", runbook)
        self.assertNotIn("--operator-username admin --operator-password admin", runbook)
        self.assertIn("run_checks.ps1 -Profile ci", runbook)
        self.assertIn("run_checks.ps1 -Profile ci", readme)
        self.assertIn("run_checks.ps1 -Profile ci", agents)
        self.assertIn("Ubuntu/Python 3.12", runbook)
        self.assertIn("docker-runtime-assets", runbook)
        self.assertIn("never runs live probes", runbook)
        self.assertIn("`.env`", runbook)
        self.assertIn("accesses production", runbook)
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
