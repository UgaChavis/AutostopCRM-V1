from __future__ import annotations

import importlib.util
import json
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
        self.assertIn("!CHATGPT_CONNECTOR_SETUP.md", rules)

    def test_scan_forbidden_text_detects_stale_references(self) -> None:
        module = load_docs_audit_module()

        issues = module.scan_forbidden_text(
            ROOT / "sample.md",
            "Use MASTER-PLAN.md from C:\\Users\\User\\Desktop\\AutostopCRM-V1",
            root=ROOT,
        )

        self.assertEqual(
            {"missing_doc_reference", "stale_workspace_path"},
            {issue.code for issue in issues},
        )

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

            tool_source = ["class DummyServer:", "    def tool(self, **kwargs): pass", "server = DummyServer()"]
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
                                    "production_tools_with_manager_mounted": len(manager_tools)
                                    + 1,
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
