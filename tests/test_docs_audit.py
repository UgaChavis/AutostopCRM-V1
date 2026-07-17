from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_manager_audit_does_not_restore_intentionally_removed_legacy_maps(self) -> None:
        module = load_docs_audit_module()

        removed = {"docs/agent/knowledge_base_index.md", "docs/agent/phone_flow.json"}

        self.assertTrue(removed.isdisjoint(module.MANAGER_CANONICAL_DOCS))
        self.assertTrue(removed.isdisjoint(module.MANAGER_GATEWAY_INSTRUCTION_DOCS))

    def test_docker_image_keeps_canonical_root_docs_for_server_audit(self) -> None:
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        rules = {line.strip() for line in dockerignore if line.strip() and not line.startswith("#")}

        self.assertIn("*.md", rules)
        self.assertIn("!AGENTS.md", rules)
        self.assertIn("!README.md", rules)
        self.assertIn("!API_GUIDE.md", rules)
        self.assertIn("!MCP_GUIDE.md", rules)
        self.assertIn("!CHATGPT_CONNECTOR_SETUP.md", rules)
        self.assertIn("!docs/OPERATIONS_RUNBOOK.md", rules)

    def test_docs_audit_detects_missing_dockerignore_canonical_markdown_keep_rule(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / ".dockerignore").write_text(
                "*.md\n!README.md\n",
                encoding="utf-8",
            )

            issues = module._check_dockerignore_keeps_canonical_markdown(temp_root)

        self.assertEqual(
            {
                "Docker image excludes canonical documentation: !AGENTS.md",
                "Docker image excludes canonical documentation: !API_GUIDE.md",
                "Docker image excludes canonical documentation: !CHATGPT_CONNECTOR_SETUP.md",
                "Docker image excludes canonical documentation: !MCP_GUIDE.md",
                "Docker image excludes canonical documentation: !docs/OPERATIONS_RUNBOOK.md",
            },
            {issue.detail for issue in issues},
        )

    def test_docs_audit_validates_local_markdown_links(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / "README.md").write_text(
                "[valid](AGENTS.md)\n[missing](docs/missing.md)\n",
                encoding="utf-8",
            )
            (temp_root / "AGENTS.md").write_text("agent rules\n", encoding="utf-8")

            issues = module._check_canonical_local_links(temp_root)

        self.assertEqual(["canonical_doc_link_missing"], [issue.code for issue in issues])
        self.assertEqual(
            "local documentation link target is missing: docs/missing.md",
            issues[0].detail,
        )

    def test_scan_forbidden_text_detects_stale_references(self) -> None:
        module = load_docs_audit_module()

        issues = module.scan_forbidden_text(
            ROOT / "sample.md",
            "Use MASTER-PLAN.md from C:\\Users\\User\\Desktop\\AutostopCRM-V1 "
            "and ssh -i ~/.ssh/codex_autostopcrm. "
            "Run AUTOSTOP_GIT_BRANCH=autostopcrm-v1 ./deploy.sh, "
            "--operator-username admin --operator-password admin, "
            "SMOKE_OPERATOR_USERNAME=${AUTOSTOP_SMOKE_OPERATOR_USERNAME:-${MINIMAL_KANBAN_DEFAULT_ADMIN_USERNAME:-admin}}, "
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

    def test_scan_forbidden_text_detects_removed_docs_and_deploy_flags(self) -> None:
        module = load_docs_audit_module()

        issues = module.scan_forbidden_text(
            ROOT / "sample.md",
            "Read docs/SERVER_MAP.md, run AUTOSTOP_VERIFY_PUBLIC_HTTPS=1, "
            "and remember that repair-order number corrections are maintenance-only.",
            root=ROOT,
        )

        self.assertEqual(
            {
                "missing_doc_reference",
                "stale_deploy_env",
                "stale_repair_order_correction_contract",
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
            (temp_root / "requirements-runtime.txt").write_text(
                "runtime manifest\n", encoding="utf-8"
            )
            (temp_root / "notes.md").write_text("unclassified\n", encoding="utf-8")
            (temp_root / "removed.md").write_text("deleted before audit\n", encoding="utf-8")
            subprocess.run(["git", "init"], cwd=temp_root, check=True, capture_output=True)
            subprocess.run(
                [
                    "git",
                    "add",
                    "README.md",
                    "requirements.txt",
                    "requirements-runtime.txt",
                    "notes.md",
                    "removed.md",
                ],
                cwd=temp_root,
                check=True,
                capture_output=True,
            )
            (temp_root / "removed.md").unlink()

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
                "read-only repair-order number audit API route is not documented: /api/repair_order_number_audit",
                "blocked repair-order number compatibility route is not documented: /api/correct_repair_order_number",
                "manual employee shift accrual route is not documented: /api/create_employee_shift_accrual",
                "toolchain bootstrap script is not documented: bootstrap_tools.ps1",
                "toolchain audit script is not documented: toolchain_doctor.ps1",
                "card log archive hydration option is not documented: include_full_details",
                "cashbox transaction pagination offset is not documented: transaction_offset",
                "immutable repair-order number rejection is not documented: repair_order_number_immutable",
                "state size diagnostics script is not documented: state_size_report.py",
                "audit compaction maintenance script is not documented: compact_audit_events.py",
                "audit archive data directory is not documented: audit-archive",
                "canonical production SSH identity is not documented: autostopcrm_server_ed25519",
                "production SSH command does not force the documented identity: IdentitiesOnly=yes",
                "deploy branch env var is not documented: AUTOSTOP_DEPLOY_BRANCH",
                "deploy/watchdog lock path env var is not documented: AUTOSTOP_DEPLOY_LOCK_PATH",
                "deploy smoke retry count env var is not documented: AUTOSTOP_SMOKE_ATTEMPTS",
                "deploy smoke retry delay env var is not documented: AUTOSTOP_SMOKE_DELAY_SECONDS",
                "production environment validator is not documented: validate_production_env.py",
                "Gateway v2 release verifier is not documented: check_agent_gateway_v2.py",
                "blocked repair-order correction contract is not documented: repair_order_number_immutable",
            },
            {issue.detail for issue in issues},
        )

    def test_quality_workflow_runs_docs_audit(self) -> None:
        module = load_docs_audit_module()

        self.assertEqual([], module._check_quality_workflow_required_gates(ROOT))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            workflow = temp_root / ".github" / "workflows" / "quality.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text("name: quality\n", encoding="utf-8")

            issues = module._check_quality_workflow_required_gates(temp_root)

        self.assertEqual(["quality_workflow_missing_gate"], [issue.code for issue in issues])
        self.assertEqual(
            "GitHub quality workflow does not run docs audit: python scripts/docs_audit.py --format text",
            issues[0].detail,
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
                "production ChatGPT/Codex OAuth setup flow is not documented in MCP guide: owner-approved OAuth 2.1",
                "production MCP connector URL is not documented: https://crm.autostopcrm.ru/mcp",
                "MCP security rule for public anonymous writes is not documented: Public anonymous writes must remain blocked",
                "exact Gateway v2 production tool count is not documented: exactly 24 tools",
                "safe exhaustive Gateway v2 release check is not documented: --exhaustive",
                "ChatGPT authenticated-client compatibility is not documented: OAuth 2.1",
            },
            {issue.detail for issue in issues},
        )

    def test_mcp_guide_lists_every_expected_gateway_tool(self) -> None:
        module = load_docs_audit_module()

        self.assertEqual([], module._check_mcp_guide_gateway_surface(ROOT))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / "MCP_GUIDE.md").write_text(
                "Production advertises exactly 2 tools: `agent_bootstrap`.\n",
                encoding="utf-8",
            )
            with patch.object(
                module,
                "load_gateway_expected_tools",
                return_value={"agent_bootstrap", "new_gateway_tool"},
            ):
                issues = module._check_mcp_guide_gateway_surface(temp_root)

        self.assertEqual(["mcp_guide_gateway_tools_missing"], [issue.code for issue in issues])
        self.assertIn("new_gateway_tool", issues[0].detail)

    def test_chatgpt_connector_setup_mentions_current_endpoint_and_safety(self) -> None:
        module = load_docs_audit_module()

        self.assertEqual([], module._check_api_guide_required_routes(ROOT))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / "CHATGPT_CONNECTOR_SETUP.md").write_text(
                "Connector setup without current endpoint.\n",
                encoding="utf-8",
            )

            issues = module._check_api_guide_required_routes(temp_root)

        self.assertEqual(
            {
                "production ChatGPT connector URL is not documented: https://crm.autostopcrm.ru/mcp",
                "ChatGPT connector bootstrap call is not documented: agent_bootstrap",
                "ChatGPT connector runtime diagnostic call is not documented: get_runtime_status",
                "ChatGPT connector write-safety rule is not documented: Public anonymous writes must remain blocked",
                "current direct ChatGPT/Codex OAuth flow is not documented: owner-approved OAuth 2.1",
                "Responses API MCP authorization field is not documented: authorization",
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
        gateway_tools = module.load_gateway_expected_tools(ROOT)

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
                                    "crm_legacy_tools_hidden_by_gateway": 1,
                                    "autostop_manager_tools_in_raw_registry": len(manager_tools),
                                    "production_visible_agent_gateway_v2": len(gateway_tools),
                                    "guarded_internal_api_write_routes_are_virtual_and_not_counted_as_tools": True,
                                },
                                "production_tools_verified": sorted(gateway_tools),
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

    def test_sibling_manager_checkout_is_not_audit_input_without_flag(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            crm_root = parent / "autostopcrm"
            manager_root = parent / "AutostopManager"
            crm_root.mkdir()
            manager_root.mkdir()

            with patch.object(module, "_check_manager_docs_and_catalogs") as manager_audit:
                module.audit(crm_root)

        manager_audit.assert_not_called()

    def test_manager_checkout_is_audited_when_explicit(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            crm_root = parent / "autostopcrm"
            manager_root = parent / "AutostopManager"
            crm_root.mkdir()
            manager_root.mkdir()

            with patch.object(
                module,
                "_check_manager_docs_and_catalogs",
                return_value=[],
            ) as manager_audit:
                module.audit(crm_root, manager_root=manager_root)

        manager_audit.assert_called_once()

    def test_user_skills_are_scanned_only_when_explicit(self) -> None:
        module = load_docs_audit_module()

        with patch.object(module, "_scan_user_skill_doc_issues", return_value=[]) as skill_audit:
            module.audit(ROOT)
            skill_audit.assert_not_called()
            module.audit(ROOT, include_skills=True)

        skill_audit.assert_called_once()

    def test_manager_instruction_scan_rejects_v1_and_direct_legacy_commands(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            manager_root = Path(temp_dir)
            agents = manager_root / "AGENTS.md"
            agents.write_text(
                "Use start_manager_run, then bootstrap_context and get_card_context.\n",
                encoding="utf-8",
            )

            issues = module._manager_gateway_instruction_issues(manager_root)

        self.assertEqual(
            {"retired_manager_lifecycle_tool", "direct_legacy_crm_instruction"},
            {issue.code for issue in issues},
        )

    def test_load_json_rejects_non_standard_constants(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "catalog.json"
            path.write_text('{"tool_count": NaN}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Unsupported JSON constant: NaN"):
                module._load_json(path)

    def test_load_json_rejects_deeply_nested_files(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "catalog.json"
            path.write_text("[" * 5000 + "0" + "]" * 5000, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "JSON is too deeply nested"):
                module._load_json(path)

    def test_read_text_rejects_oversized_files(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "huge.md"
            path.write_text("x" * 16, encoding="utf-8")

            with patch.object(module, "DOCS_AUDIT_TEXT_MAX_BYTES", 8):
                with self.assertRaisesRegex(ValueError, "docs audit file is too large"):
                    module._read_text(path)

    def test_git_inventory_timeout_returns_empty_tracked_file_list(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            with patch.object(
                module.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["git"], timeout=1),
            ) as run:
                self.assertEqual([], module._iter_git_tracked_files(temp_root))

        self.assertIs(run.call_args.kwargs["stdin"], module.subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
