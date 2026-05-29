from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "docs_audit.py"


def load_docs_audit_module():
    spec = importlib.util.spec_from_file_location("docs_audit", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("docs_audit.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DocsAuditTests(unittest.TestCase):
    def test_docs_audit_passes_current_tree(self) -> None:
        module = load_docs_audit_module()

        issues = module.audit(ROOT)

        self.assertEqual([], issues)

    def test_docker_image_keeps_canonical_root_docs_for_server_audit(self) -> None:
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        rules = {line.strip() for line in dockerignore if line.strip() and not line.startswith("#")}

        self.assertIn("*.md", rules)
        self.assertIn("!README.md", rules)
        self.assertIn("!API_GUIDE.md", rules)
        self.assertIn("!MCP_GUIDE.md", rules)
        self.assertNotIn("!CHATGPT_CONNECTOR_SETUP.md", rules)

    def test_scan_forbidden_text_detects_stale_references(self) -> None:
        module = load_docs_audit_module()

        issues = module.scan_forbidden_text(
            ROOT / "sample.md",
            "Use MASTER-PLAN.md from C:\\Users\\User\\Desktop\\AutostopCRM-V1 "
            "and ssh -i ~/.ssh/codex_autostopcrm. "
            "Run AUTOSTOP_GIT_BRANCH=autostopcrm-v1 ./deploy.sh, "
            "--operator-username admin --operator-password admin, "
            "and --site-url http://crm.autostopcrm.ru.",
            root=ROOT,
        )

        self.assertEqual(
            {
                "missing_doc_reference",
                "stale_workspace_path",
                "stale_ssh_identity",
                "stale_deploy_env",
                "stale_smoke_credentials",
                "stale_public_http",
            },
            {issue.code for issue in issues},
        )

    def test_scan_crm_only_forbidden_text_detects_old_manager_vault_path(self) -> None:
        module = load_docs_audit_module()

        issues = module.scan_crm_only_forbidden_text(
            ROOT / "sample.md",
            "Use C:\\Users\\User\\Мой диск\\Obsidian CRM\\AutostopCRM "
            "or C:\\Users\\User\\Desktop\\Obsidian CRM\\AutostopCRM for manager knowledge.",
            root=ROOT,
        )

        self.assertEqual(["stale_workspace_path"], [issue.code for issue in issues])
        self.assertIn("manager knowledge vault", issues[0].detail)

    def test_script_instruction_scan_detects_stale_instruction_text(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            script = temp_root / "scripts" / "run_dev.ps1"
            script.parent.mkdir(parents=True)
            script.write_text(
                "Use AUTOSTOP_GIT_BRANCH=autostopcrm-v1 before deploy.\n",
                encoding="utf-8",
            )

            issues = module._check_script_instruction_text(temp_root)

        self.assertEqual(["stale_deploy_env"], [issue.code for issue in issues])
        self.assertEqual("scripts/run_dev.ps1", issues[0].path)

    def test_tracked_documentation_requires_explicit_classification(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / "README.md").write_text("canonical\n", encoding="utf-8")
            (temp_root / "requirements.txt").write_text("manifest\n", encoding="utf-8")
            (temp_root / "notes.md").write_text("unclassified\n", encoding="utf-8")
            subprocess.run(["git", "init"], cwd=temp_root, check=True, capture_output=True)
            subprocess.run(
                ["git", "add", "README.md", "requirements.txt", "notes.md"],
                cwd=temp_root,
                check=True,
                capture_output=True,
            )

            issues = module._check_unclassified_tracked_docs(temp_root)

        self.assertEqual(["unclassified_tracked_doc"], [issue.code for issue in issues])
        self.assertEqual("notes.md", issues[0].path)

    def test_superpowers_planning_docs_are_retired_artifacts(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            plan = temp_root / "docs" / "superpowers" / "plans" / "old-plan.md"
            spec = temp_root / "docs" / "superpowers" / "specs" / "old-spec.md"
            plan.parent.mkdir(parents=True)
            spec.parent.mkdir(parents=True)
            plan.write_text("checked-off implementation plan\n", encoding="utf-8")
            spec.write_text("approved design copied from implementation\n", encoding="utf-8")

            issues = module._iter_retired_candidates(temp_root)

        self.assertEqual(
            {
                "docs/superpowers/plans/old-plan.md",
                "docs/superpowers/specs/old-spec.md",
            },
            {path.relative_to(temp_root).as_posix() for path in issues},
        )

    def test_api_guide_mentions_safety_critical_internal_routes(self) -> None:
        module = load_docs_audit_module()

        self.assertEqual([], module._check_api_guide_required_routes(ROOT))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / "API_GUIDE.md").write_text(
                "Only /api/finance_audit/apply_safe_fixes is mentioned.\n",
                encoding="utf-8",
            )
            (temp_root / "docs").mkdir()
            (temp_root / "docs" / "OPERATIONS_RUNBOOK.md").write_text(
                "No state maintenance docs.\n",
                encoding="utf-8",
            )

            issues = module._check_api_guide_required_routes(temp_root)

        self.assertEqual(
            {
                "read-only finance audit API route is not documented: /api/finance_audit",
                "repair-order number maintenance route is not documented: /api/correct_repair_order_number",
                "manual employee shift accrual route is not documented: /api/create_employee_shift_accrual",
                "card log archive hydration option is not documented: include_full_details",
                "cashbox transaction pagination offset is not documented: transaction_offset",
                "state size diagnostics script is not documented: state_size_report.py",
                "audit compaction maintenance script is not documented: compact_audit_events.py",
                "audit archive data directory is not documented: audit-archive",
                "canonical production SSH identity is not documented: autostopcrm_server_ed25519",
                "production SSH command does not force the documented identity: IdentitiesOnly=yes",
                "deploy branch env var is not documented: AUTOSTOP_DEPLOY_BRANCH",
                "deploy/watchdog lock path env var is not documented: AUTOSTOP_DEPLOY_LOCK_PATH",
                "deploy smoke retry count env var is not documented: AUTOSTOP_SMOKE_ATTEMPTS",
                "deploy smoke retry delay env var is not documented: AUTOSTOP_SMOKE_DELAY_SECONDS",
                "manual shift salary accrual browser-smoke scenario is not documented: employee_shift_accrual_manual_salary",
            },
            {issue.detail for issue in issues},
        )

    def test_mcp_guide_mentions_transport_security_allowlist_overrides(self) -> None:
        module = load_docs_audit_module()

        self.assertEqual([], module._check_api_guide_required_routes(ROOT))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / "MCP_GUIDE.md").write_text(
                "MCP runtime docs without allowlist overrides.\n",
                encoding="utf-8",
            )

            issues = module._check_api_guide_required_routes(temp_root)

        self.assertEqual(
            {
                "MCP allowed-host transport security override is not documented: MINIMAL_KANBAN_MCP_ALLOWED_HOSTS",
                "MCP allowed-origin transport security override is not documented: MINIMAL_KANBAN_MCP_ALLOWED_ORIGINS",
                "ChatGPT connector setup flow is not documented in MCP guide: ChatGPT Apps & Connectors",
                "production MCP connector URL is not documented: https://crm.autostopcrm.ru/mcp",
                "MCP security rule for public anonymous writes is not documented: Public anonymous writes must remain blocked",
            },
            {issue.detail for issue in issues},
        )

    def test_secret_bundle_scan_reports_stale_instructions_without_secret_values(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            bundle = Path(temp_dir)
            (bundle / "ACCESS.txt").write_text(
                "ssh -i ~/.ssh/codex_autostopcrm root@crm.autostopcrm.ru\n"
                "do not print actual token values\n",
                encoding="utf-8",
            )

            issues = module.audit(ROOT, include_skills=False, secret_bundle=bundle)

        self.assertIn("stale_ssh_identity", {issue.code for issue in issues})

    def test_manager_mcp_count_is_dynamic(self) -> None:
        module = load_docs_audit_module()
        manager_tools = [f"manager_tool_{index}" for index in range(32)] + [
            "estimate_repair_work_cost"
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            manager_root = Path(temp_dir)
            source_dir = manager_root / "autostop_manager"
            docs_dir = manager_root / "docs" / "agent"
            source_dir.mkdir(parents=True)
            docs_dir.mkdir(parents=True)

            tool_source = [
                "class DummyServer:",
                "    def tool(self, **kwargs): pass",
                "server = DummyServer()",
            ]
            for tool_name in manager_tools:
                tool_source.extend(
                    [
                        f"@server.tool(name={tool_name!r})",
                        f"def {tool_name}():",
                        "    return {}",
                        "",
                    ]
                )
            (source_dir / "mcp_tools.py").write_text("\n".join(tool_source), encoding="utf-8")

            for relative_path in module.MANAGER_CANONICAL_DOCS:
                path = manager_root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.name == "manager_mcp_catalog.json":
                    path.write_text(
                        json.dumps(
                            {
                                "tool_count": len(manager_tools),
                                "all_tools": manager_tools,
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                elif path.name == "crm_mcp_catalog.json":
                    path.write_text(
                        json.dumps(
                            {
                                "tool_counts": {
                                    "crm_base_tools": 1,
                                    "optional_autostop_manager_tools": len(manager_tools),
                                    "production_tools_with_manager_mounted": len(manager_tools) + 1,
                                },
                                "live_tools_verified": ["crm_tool", *manager_tools],
                                "tool_families": {
                                    "optional_manager_memory_and_routing": manager_tools
                                },
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                else:
                    path.write_text("ok\n", encoding="utf-8")

            issues = module._check_manager_docs_and_catalogs(
                ROOT,
                manager_root,
                {"crm_tool"},
            )

        self.assertEqual([], issues)


if __name__ == "__main__":
    unittest.main()
