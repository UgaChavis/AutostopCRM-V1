from __future__ import annotations

import hashlib
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

    def test_crm_mcp_surface_unions_server_and_registrar_sources(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            for relative_path, tool_name in (
                ("src/minimal_kanban/mcp/server.py", "server_tool"),
                (
                    "src/minimal_kanban/mcp/connector_diagnostics.py",
                    "diagnostic_tool",
                ),
                (
                    "src/minimal_kanban/mcp/board_reads.py",
                    "board_read_tool",
                ),
                (
                    "src/minimal_kanban/mcp/board_column_writes.py",
                    "board_column_write_tool",
                ),
                (
                    "src/minimal_kanban/mcp/board_sticky_writes.py",
                    "board_sticky_write_tool",
                ),
                (
                    "src/minimal_kanban/mcp/board_card_timer_writes.py",
                    "board_card_timer_write_tool",
                ),
                (
                    "src/minimal_kanban/mcp/card_attachment_reads.py",
                    "card_attachment_read_tool",
                ),
                (
                    "src/minimal_kanban/mcp/shared_file_reads.py",
                    "shared_file_read_tool",
                ),
                (
                    "src/minimal_kanban/mcp/shared_file_writes.py",
                    "shared_file_write_tool",
                ),
            ):
                source_path = temp_root / relative_path
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_text(
                    '@server.tool(name="' + tool_name + '")\n'
                    "def registered_tool():\n"
                    "    return None\n",
                    encoding="utf-8",
                )

            with patch.object(
                module,
                "load_crm_registry_tools",
                return_value={
                    "server_tool",
                    "diagnostic_tool",
                    "board_read_tool",
                    "board_column_write_tool",
                    "board_sticky_write_tool",
                    "board_card_timer_write_tool",
                    "card_attachment_read_tool",
                    "shared_file_read_tool",
                    "shared_file_write_tool",
                },
            ):
                issues = module._check_crm_mcp_surface(temp_root)

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

    def test_docs_audit_validates_technical_debt_links(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            tasks = temp_root / "tech_debt"
            tasks.mkdir()
            (tasks / "README.md").write_text(
                "[valid](001-task.md)\n[missing](002-missing.md)\n",
                encoding="utf-8",
            )
            (tasks / "001-task.md").write_text("task\n", encoding="utf-8")

            issues = module._check_canonical_local_links(temp_root)

        self.assertEqual(["canonical_doc_link_missing"], [issue.code for issue in issues])
        self.assertEqual("tech_debt/README.md", issues[0].path)

    def test_scan_forbidden_text_detects_stale_references(self) -> None:
        module = load_docs_audit_module()

        issues = module.scan_forbidden_text(
            ROOT / "sample.md",
            "Use MASTER-PLAN.md from C:\\Users\\User\\Desktop\\AutostopCRM-V1 "
            "and ssh -i ~/.ssh/codex_autostopcrm. "
            "Run AUTOSTOP_GIT_BRANCH=autostopcrm-v1 ./deploy.sh, "
            "--operator-username admin --operator-password admin, "
            "SMOKE_OPERATOR_USERNAME=${AUTOSTOP_SMOKE_OPERATOR_USERNAME:-${MINIMAL_KANBAN_DEFAULT_ADMIN_USERNAME:-admin}}, "
            "and --site-url http://crm.autostopcrm.ru from /root/.minimal-kanban.",
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
                "stale_container_data_path",
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

    def test_technical_debt_tasks_are_active_noncanonical_docs(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            task = temp_root / "tech_debt" / "001-task.md"
            task.parent.mkdir()
            task.write_text("task\n", encoding="utf-8")
            subprocess.run(["git", "init"], cwd=temp_root, check=True, capture_output=True)
            subprocess.run(
                ["git", "add", "tech_debt/001-task.md"],
                cwd=temp_root,
                check=True,
                capture_output=True,
            )

            issues = module._check_unclassified_tracked_docs(temp_root)

        self.assertEqual([], issues)

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
                "coverage ratchet command is not documented: coverage_audit.py",
                "mandatory core browser-smoke profile is not documented: --profile core",
                "release browser-smoke profile is not documented: --profile full",
                "card log archive hydration option is not documented: include_full_details",
                "cashbox transaction pagination offset is not documented: transaction_offset",
                "immutable repair-order number rejection is not documented: repair_order_number_immutable",
                "state size diagnostics script is not documented: state_size_report.py",
                "audit compaction maintenance script is not documented: compact_audit_events.py",
                "audit archive data directory is not documented: audit-archive",
                "canonical production SSH identity is not documented: autostopcrm_server_ed25519",
                "production SSH command does not force the documented identity: IdentitiesOnly=yes",
                "fixed CRM deploy branch is not documented: CRM_DEPLOY_BRANCH",
                "deploy/watchdog lock path env var is not documented: AUTOSTOP_DEPLOY_LOCK_PATH",
                "deploy smoke retry count env var is not documented: AUTOSTOP_SMOKE_ATTEMPTS",
                "deploy smoke retry delay env var is not documented: AUTOSTOP_SMOKE_DELAY_SECONDS",
                "production environment validator is not documented: validate_production_env.py",
                "Gateway v2 release verifier is not documented: check_agent_gateway_v2.py",
                "blocked repair-order correction contract is not documented: repair_order_number_immutable",
                "desktop build entrypoint is not documented: build_app.ps1",
                "portable release assembly is not documented: prepare_release.ps1",
                "complete desktop release gate is not documented: run_quality_pass.ps1",
                "canonical local CI profile is not documented: run_checks.ps1 -Profile ci",
                "portable executable verification is not documented: post_build_verification.py",
                "payroll audit tool is not documented: payroll_audit_report.py",
                "client data-quality maintenance tool is not documented: client_data_quality_maintenance.py",
                "client duplicate maintenance tool is not documented: client_duplicates_maintenance.py",
            },
            {issue.detail for issue in issues},
        )

    def test_store_gateway_docs_are_derived_from_source_contract(self) -> None:
        module = load_docs_audit_module()

        self.assertEqual([], module._check_store_gateway_docs_contract(ROOT))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source = temp_root / "src" / "minimal_kanban" / "mcp" / "store_gateway.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                "STORE_READ_CAPABILITY_NAMES = frozenset({'store_read', 'download_store_quote_vin_photo'})\n"
                "STORE_MANAGEMENT_CAPABILITY_NAME = 'store_manage'\n"
                "STORE_SEARCH_ENTITIES = frozenset({'store_part', 'store_sourcing_offer'})\n"
                "STORE_MANAGEMENT_OPERATIONS = frozenset({'write_one', 'write_two'})\n",
                encoding="utf-8",
            )
            (temp_root / "MCP_GUIDE.md").write_text(
                "1 `INTERNAL_ONLY` tool: `store_read`. Exactly one operation: `write_one`.\n",
                encoding="utf-8",
            )
            (temp_root / "CHATGPT_CONNECTOR_SETUP.md").write_text(
                "One mounted tool. Use `write_one`; pass store_cursor to agent_bootstrap.\n",
                encoding="utf-8",
            )

            issues = module._check_store_gateway_docs_contract(temp_root)

        self.assertEqual(
            {
                "mcp_guide_store_internal_boundary_stale",
                "mcp_guide_store_search_entities_stale",
                "store_management_operations_stale",
                "store_vin_photo_workflow_missing",
                "mcp_guide_store_safety_contract_stale",
                "chatgpt_bootstrap_cursor_stale",
            },
            {issue.code for issue in issues},
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

        expected_details = {
            f"{detail}: {required_text}"
            for required_text, detail in module.QUALITY_WORKFLOW_REQUIRED_TEXT
        }
        self.assertEqual(len(expected_details), len(issues))
        self.assertEqual({"quality_workflow_missing_gate"}, {issue.code for issue in issues})
        self.assertEqual(expected_details, {issue.detail for issue in issues})

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
                "Production advertises exactly 2 tools and 46 CRM workflow operations: "
                "`agent_bootstrap`.\n",
                encoding="utf-8",
            )
            with (
                patch.object(
                    module,
                    "load_gateway_expected_tools",
                    return_value={"agent_bootstrap", "new_gateway_tool"},
                ),
                patch.object(module, "load_expected_crm_operation_count", return_value=46),
            ):
                issues = module._check_mcp_guide_gateway_surface(temp_root)

        self.assertEqual(["mcp_guide_gateway_tools_missing"], [issue.code for issue in issues])
        self.assertIn("new_gateway_tool", issues[0].detail)

    def test_mcp_guide_operation_count_is_derived_from_attestation_source(self) -> None:
        module = load_docs_audit_module()

        self.assertEqual([], module._check_mcp_guide_gateway_surface(ROOT))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            attestation = temp_root / "scripts" / "attest_agent_gateway_v2.py"
            attestation.parent.mkdir(parents=True)
            attestation.write_text("EXPECTED_CRM_OPERATION_COUNT = 47\n", encoding="utf-8")
            guide = temp_root / "MCP_GUIDE.md"
            guide.write_text(
                "Production advertises exactly 1 tools and 46 CRM workflow operations: "
                "`agent_bootstrap`.\n",
                encoding="utf-8",
            )

            with patch.object(
                module,
                "load_gateway_expected_tools",
                return_value={"agent_bootstrap"},
            ):
                issues = module._check_mcp_guide_gateway_surface(temp_root)
                guide.write_text(
                    "Production advertises exactly 1 tools and 47 CRM workflow operations: "
                    "`agent_bootstrap`.\n",
                    encoding="utf-8",
                )
                corrected_issues = module._check_mcp_guide_gateway_surface(temp_root)

        self.assertEqual(
            ["mcp_guide_gateway_operation_count_stale"],
            [issue.code for issue in issues],
        )
        self.assertIn("47 CRM workflow operations", issues[0].detail)
        self.assertEqual([], corrected_issues)

    def test_agent_connector_docs_contract_matches_compacted_baseline(self) -> None:
        module = load_docs_audit_module()

        self.assertEqual([], module._check_agent_connector_doc_contract(ROOT))
        total_lines = sum(
            len((ROOT / relative_path).read_text(encoding="utf-8").splitlines())
            for relative_path in module.AGENT_CONNECTOR_DOCS
        )

        self.assertLessEqual(total_lines, module.AGENT_CONNECTOR_DOC_MAX_TOTAL_LINES)
        self.assertLess(total_lines, 266)

    def test_agent_connector_docs_contract_enforces_cap_and_wrapped_semantics(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / "AGENTS.md").write_text("owner\ngate\n", encoding="utf-8")
            (temp_root / "CHATGPT_CONNECTOR_SETUP.md").write_text(
                "client rules\n", encoding="utf-8"
            )
            with (
                patch.object(module, "AGENTS_REQUIRED_TEXT", (("owner gate", "missing owner"),)),
                patch.object(
                    module,
                    "CHATGPT_CONNECTOR_REQUIRED_TEXT",
                    (("auth gate", "missing auth"),),
                ),
                patch.object(module, "AGENT_CONNECTOR_DOC_MAX_TOTAL_LINES", 2),
            ):
                issues = module._check_agent_connector_doc_contract(temp_root)

        self.assertEqual(
            [
                "chatgpt_connector_setup_missing_contract",
                "agent_connector_docs_line_cap_exceeded",
            ],
            [issue.code for issue in issues],
        )
        self.assertEqual("current=3; max=2; delta=+1", issues[1].detail)

    def test_chatgpt_connector_setup_mentions_current_endpoint_and_safety(self) -> None:
        module = load_docs_audit_module()

        self.assertEqual([], module._check_agent_connector_doc_contract(ROOT))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / "CHATGPT_CONNECTOR_SETUP.md").write_text(
                "Connector setup without current endpoint.\n",
                encoding="utf-8",
            )

            issues = module._check_agent_connector_doc_contract(temp_root)

        self.assertEqual(
            {
                "ChatGPT connector setup is missing a required client/auth contract: "
                f"{required_text}"
                for required_text in (
                    "https://crm.autostopcrm.ru/mcp",
                    "[MCP_GUIDE.md](MCP_GUIDE.md)",
                    "agent_bootstrap",
                    "get_runtime_status",
                    "Public anonymous writes must remain blocked",
                    "owner-approved OAuth 2.1",
                    "authorization",
                )
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

        def fingerprint(tools: list[str] | set[str]) -> str:
            names = sorted(tools)
            surface = [
                {"name": name, "inputSchema": {"type": "object", "properties": {}}}
                for name in names
            ]
            canonical = json.dumps(
                surface, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
            return hashlib.sha256(canonical).hexdigest()

        def manifest(tools: list[str] | set[str], source: str) -> dict[str, object]:
            names = sorted(tools)
            return {
                "format": "mcp_surface_manifest_v1",
                "source": source,
                "expected_tool_count": len(names),
                "expected_tool_names": names,
                "schema_fingerprint": fingerprint(tools),
                "verified_at": "2026-08-25",
            }

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
                        json.dumps(manifest(manager_tools, "test manager"), ensure_ascii=False),
                        encoding="utf-8",
                    )
                elif path.name == "crm_mcp_catalog.json":
                    path.write_text(
                        json.dumps(manifest(gateway_tools, "test gateway"), ensure_ascii=False),
                        encoding="utf-8",
                    )
                else:
                    path.write_text("ok\n", encoding="utf-8")

            with patch.object(
                module,
                "_registered_surface_fingerprints",
                return_value={
                    "manager": fingerprint(manager_tools),
                    "crm": fingerprint(gateway_tools),
                },
            ):
                issues = module._check_manager_docs_and_catalogs(ROOT, manager_root)

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
        skill_paths = (Path("autostopcrm-maintain"),)

        with patch.object(module, "_scan_user_skill_doc_issues", return_value=[]) as skill_audit:
            module.audit(ROOT)
            skill_audit.assert_not_called()
            module.audit(ROOT, include_skills=True, skill_paths=skill_paths)

        skill_audit.assert_called_once_with(ROOT.resolve(), skill_paths)

    def test_skill_paths_fail_closed_without_complete_opt_in(self) -> None:
        module = load_docs_audit_module()

        missing_paths = module.audit(ROOT, include_skills=True)
        missing_flag = module.audit(
            ROOT,
            skill_paths=(Path("autostopcrm-maintain"),),
        )

        self.assertIn("skill_paths_required", {issue.code for issue in missing_paths})
        self.assertIn(
            "skill_paths_require_include_skills",
            {issue.code for issue in missing_flag},
        )

    def test_selected_crm_skill_scan_is_scoped_deduplicated_and_reads_yaml(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            skills_root = Path(temp_dir) / "skills"
            selected = skills_root / "autostopcrm-maintain"
            foreign = skills_root / "foreign-skill"
            unselected_crm = skills_root / "autostopcrm-optimize"
            (selected / "agents").mkdir(parents=True)
            foreign.mkdir(parents=True)
            unselected_crm.mkdir(parents=True)
            (selected / "SKILL.md").write_text(
                "AUTOSTOP_DEPLOY_BRANCH=autostopcrm-v1 SECRET_FIXTURE_VALUE\n",
                encoding="utf-8",
            )
            (selected / "agents" / "openai.yaml").write_text(
                'default_prompt: "keep local GitHub and server in sync"\n',
                encoding="utf-8",
            )
            (selected / "checklist.yml").write_text(
                "note: Record the result in project memory if the finding is reusable.\n",
                encoding="utf-8",
            )
            (selected / "ignored.txt").write_text(
                "git reset --hard origin/autostopcrm-v1\n",
                encoding="utf-8",
            )
            (foreign / "SKILL.md").write_text(
                "Use MASTER-PLAN.md and AUTOSTOP_DEPLOY_BRANCH.\n",
                encoding="utf-8",
            )
            (unselected_crm / "SKILL.md").write_text(
                "Use MASTER-PLAN.md and AUTOSTOP_DEPLOY_BRANCH.\n",
                encoding="utf-8",
            )

            with patch.object(module, "_user_skills_root", return_value=skills_root):
                issues = module._scan_user_skill_doc_issues(
                    ROOT,
                    (
                        Path("autostopcrm-maintain"),
                        Path("autostopcrm-maintain"),
                    ),
                )

        codes = [issue.code for issue in issues]
        self.assertEqual(1, codes.count("stale_crm_skill_deploy_env"))
        self.assertEqual(1, codes.count("unsafe_crm_skill_server_sync"))
        self.assertEqual(1, codes.count("unsafe_crm_skill_memory_write"))
        self.assertNotIn("unsafe_crm_skill_git_reset", codes)
        self.assertNotIn("missing_doc_reference", codes)
        self.assertNotIn("SECRET_FIXTURE_VALUE", repr(issues))
        self.assertTrue(all(not Path(issue.path).is_absolute() for issue in issues))

    def test_crm_skill_scan_detects_known_unsafe_instruction_classes(self) -> None:
        module = load_docs_audit_module()

        issues = module.scan_user_skill_forbidden_text(
            ROOT / "sample.md",
            "\n".join(
                (
                    "AUTOSTOP_DEPLOY_BRANCH=autostopcrm-v1 ./deploy.sh",
                    "src/minimal_kanban/telegram_ai/",
                    "git reset --hard origin/autostopcrm-v1",
                    "- `PySide6==6.11.0`",
                    "ssh root@crm.autostopcrm.ru from /opt/autostopcrm",
                    "Keep local, GitHub, and server content in sync before declaring success.",
                    "Record the result in project memory if the finding is reusable.",
                )
            ),
            root=ROOT,
        )

        self.assertEqual(
            {
                "stale_crm_skill_deploy_env",
                "stale_crm_skill_path",
                "unsafe_crm_skill_git_reset",
                "stale_crm_skill_version_snapshot",
                "stale_crm_skill_deploy_procedure",
                "stale_crm_skill_server_access",
                "unsafe_crm_skill_server_sync",
                "unsafe_crm_skill_memory_write",
            },
            {issue.code for issue in issues},
        )

    def test_crm_skill_scan_rejects_disposable_server_mirror_instruction(self) -> None:
        module = load_docs_audit_module()

        issues = module.scan_user_skill_forbidden_text(
            ROOT / "sample.md",
            "Treat the server mirror as disposable runtime state unless the file is tracked.",
            root=ROOT,
        )

        self.assertEqual(
            ["unsafe_crm_skill_server_sync"],
            [issue.code for issue in issues],
        )

    def test_crm_skill_scan_rejects_known_access_sync_and_removed_path_variants(self) -> None:
        module = load_docs_audit_module()

        cases = (
            (
                "Use src/minimal_kanban/ui/dialogs.py.",
                "stale_crm_skill_path",
            ),
            (
                "Local key bundle path used in this workspace: PRIVATE_FIXTURE_PATH.",
                "stale_crm_skill_server_access",
            ),
            (
                "Recheck `git status` locally and on the server mirror.",
                "unsafe_crm_skill_server_sync",
            ),
            (
                "Recheck both GitHub and server state after any deploy or force-reset.",
                "unsafe_crm_skill_server_sync",
            ),
        )
        for text, expected_code in cases:
            with self.subTest(text=text):
                issues = module.scan_user_skill_forbidden_text(
                    ROOT / "sample.md",
                    text,
                    root=ROOT,
                )
                self.assertIn(expected_code, {issue.code for issue in issues})

    def test_crm_skill_scan_allows_generic_production_guardrail(self) -> None:
        module = load_docs_audit_module()

        issues = module.scan_user_skill_forbidden_text(
            ROOT / "sample.md",
            "Do not deploy, access production, synchronize a server, or write memory "
            "without a separate explicit owner command.",
            root=ROOT,
        )

        self.assertEqual([], issues)

    def test_crm_skill_path_validation_rejects_invalid_scope(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            skills_root = temp_root / "skills"
            valid = skills_root / "autostopcrm-maintain"
            foreign = skills_root / "foreign-skill"
            nested = skills_root / "group" / "autostopcrm-nested"
            not_directory = skills_root / "autostopcrm-file"
            missing_entrypoint = skills_root / "autostopcrm-empty"
            loop = skills_root / "autostopcrm-loop"
            alias = skills_root / "autostopcrm-alias"
            target = skills_root / "autostopcrm-target"
            outside = temp_root / "outside" / "autostopcrm-outside"
            for directory in (
                valid,
                foreign,
                nested,
                missing_entrypoint,
                loop,
                alias,
                target,
                outside,
            ):
                directory.mkdir(parents=True)
            (valid / "SKILL.md").write_text("valid\n", encoding="utf-8")
            for directory in (loop, alias, target):
                (directory / "SKILL.md").write_text("valid\n", encoding="utf-8")
            not_directory.write_text("not a directory\n", encoding="utf-8")

            duplicate_paths, duplicate_issues = module._resolve_user_skill_paths(
                (Path(valid.name), Path(valid.name)),
                skills_root=skills_root,
            )
            self.assertEqual([valid.resolve()], duplicate_paths)
            self.assertEqual([], duplicate_issues)

            cases = (
                (Path("autostopcrm-missing"), "skill_path_missing"),
                (Path(foreign.name), "skill_path_name_not_allowed"),
                (
                    Path("group") / "autostopcrm-nested",
                    "skill_path_not_direct_child",
                ),
                (Path(not_directory.name), "skill_path_not_directory"),
                (Path(missing_entrypoint.name), "skill_entrypoint_missing"),
                (outside, "skill_path_outside_skills_root"),
            )
            for skill_path, expected_code in cases:
                with self.subTest(skill_path=skill_path):
                    selected, issues = module._resolve_user_skill_paths(
                        (skill_path,),
                        skills_root=skills_root,
                    )
                    self.assertEqual([], selected)
                    self.assertEqual([expected_code], [issue.code for issue in issues])

            outside_issue = module._resolve_user_skill_paths(
                (outside,),
                skills_root=skills_root,
            )[1][0]
            self.assertEqual("--skill-path[1]", outside_issue.path)
            self.assertNotIn(str(outside), repr(outside_issue))

            real_resolve = module._resolve_strict

            def loop_resolver(path):
                if path in {skills_root, loop}:
                    raise RuntimeError("fixture loop")
                return real_resolve(path)

            with patch.object(module, "_resolve_strict", side_effect=loop_resolver):
                _, root_loop_issues = module._resolve_user_skill_paths(
                    (Path(valid.name),),
                    skills_root=skills_root,
                )
            self.assertEqual(
                ["skills_root_resolution_error"],
                [issue.code for issue in root_loop_issues],
            )

            def candidate_loop_resolver(path):
                if path == loop:
                    raise RuntimeError("fixture loop")
                return real_resolve(path)

            with patch.object(
                module,
                "_resolve_strict",
                side_effect=candidate_loop_resolver,
            ):
                _, candidate_loop_issues = module._resolve_user_skill_paths(
                    (Path(loop.name),),
                    skills_root=skills_root,
                )
            self.assertEqual(
                ["skill_path_resolution_error"],
                [issue.code for issue in candidate_loop_issues],
            )

            def retarget_resolver(path):
                if path == alias:
                    return real_resolve(target)
                return real_resolve(path)

            with patch.object(module, "_resolve_strict", side_effect=retarget_resolver):
                _, retarget_issues = module._resolve_user_skill_paths(
                    (Path(alias.name),),
                    skills_root=skills_root,
                )
            self.assertEqual(
                ["skill_path_retargeted"],
                [issue.code for issue in retarget_issues],
            )

    def test_crm_skill_scope_rejects_link_like_roots_and_descendants(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            skills_root = Path(temp_dir) / "skills"
            selected = skills_root / "autostopcrm-maintain"
            linked = selected / "references"
            linked.mkdir(parents=True)
            (selected / "SKILL.md").write_text("valid\n", encoding="utf-8")

            with patch.object(
                module,
                "_is_link_like",
                side_effect=lambda path: path == skills_root,
            ):
                _, root_issues = module._resolve_user_skill_paths(
                    (Path(selected.name),),
                    skills_root=skills_root,
                )

            with patch.object(
                module,
                "_is_link_like",
                side_effect=lambda path: path == selected,
            ):
                _, selected_issues = module._resolve_user_skill_paths(
                    (Path(selected.name),),
                    skills_root=skills_root,
                )

            with patch.object(
                module,
                "_is_link_like",
                side_effect=lambda path: path == selected / "SKILL.md",
            ):
                _, entrypoint_issues = module._resolve_user_skill_paths(
                    (Path(selected.name),),
                    skills_root=skills_root,
                )

            with patch.object(
                module,
                "_is_link_like",
                side_effect=lambda path: path == linked,
            ) as link_probe:
                with patch.object(
                    module,
                    "_display_path",
                    side_effect=AssertionError("rejected links must not be resolved"),
                ):
                    docs, descendant_issues = module._iter_user_skill_docs(
                        [selected],
                        skills_root=skills_root,
                    )

        self.assertEqual(
            ["skills_root_symlink_forbidden"],
            [issue.code for issue in root_issues],
        )
        self.assertEqual(
            ["skill_path_symlink_forbidden"],
            [issue.code for issue in selected_issues],
        )
        self.assertEqual(
            ["skill_entry_symlink_forbidden"],
            [issue.code for issue in entrypoint_issues],
        )
        self.assertEqual([selected / "SKILL.md"], docs)
        self.assertEqual(
            ["skill_entry_symlink_forbidden"],
            [issue.code for issue in descendant_issues],
        )
        self.assertEqual("autostopcrm-maintain/references", descendant_issues[0].path)
        self.assertTrue(link_probe.called)

    def test_crm_skill_read_failure_is_reported_without_content(self) -> None:
        module = load_docs_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            skills_root = Path(temp_dir) / "skills"
            selected = skills_root / "autostopcrm-maintain"
            selected.mkdir(parents=True)
            (selected / "SKILL.md").write_text(
                "SECRET_OVERSIZED_FIXTURE\n",
                encoding="utf-8",
            )

            with (
                patch.object(module, "_user_skills_root", return_value=skills_root),
                patch.object(module, "DOCS_AUDIT_TEXT_MAX_BYTES", 8),
            ):
                issues = module._scan_user_skill_doc_issues(
                    ROOT,
                    (Path(selected.name),),
                )

        self.assertEqual(["skill_doc_audit_error"], [issue.code for issue in issues])
        self.assertNotIn("SECRET_OVERSIZED_FIXTURE", repr(issues))

    def test_cli_accepts_repeatable_skill_paths(self) -> None:
        module = load_docs_audit_module()
        skill_paths = (
            Path("autostopcrm-maintain"),
            Path("autostopcrm-optimize"),
        )

        with (
            patch.object(module, "audit", return_value=[]) as audit_mock,
            patch.object(module, "_print_text") as print_mock,
        ):
            exit_code = module.main(
                [
                    "--include-skills",
                    "--skill-path",
                    str(skill_paths[0]),
                    "--skill-path",
                    str(skill_paths[1]),
                ]
            )

        self.assertEqual(0, exit_code)
        audit_mock.assert_called_once_with(
            module.ROOT,
            manager_root=None,
            include_skills=True,
            skill_paths=skill_paths,
            secret_bundle=None,
        )
        print_mock.assert_called_once_with([])

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
