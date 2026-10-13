"""Exercise the real release path gate without Unix locks or a production host."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CrmUiReleasePathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse((ROOT / "scripts/deploy_crm_only.py").read_text(encoding="utf-8"))
        nodes = [
            node
            for node in cls.tree.body
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "ALLOWED_CHANGED_PATHS"
                    for target in node.targets
                )
            )
            or (
                isinstance(node, (ast.ClassDef, ast.FunctionDef))
                and node.name in {"ReleaseError", "validate_ui_changed_paths"}
            )
        ]
        cls.policy = {}
        exec(
            compile(ast.Module(body=nodes, type_ignores=[]), "release_path_gate", "exec"),
            cls.policy,
        )

    def test_reviewed_ui_helpers_and_verification_files_pass_the_real_gate(self):
        self.policy["validate_ui_changed_paths"](
            {
                "src/minimal_kanban/web_app_assets/source/inventory_edit_state.js",
                "src/minimal_kanban/web_app_assets/source/card_save_state.js",
                "src/minimal_kanban/web_app_assets/source/cashbox_money_state.js",
                "src/minimal_kanban/web_app_assets/source/employee_money_state.js",
                "src/minimal_kanban/web_app_assets/module_assets.py",
                "scripts/ci_gate_completion.ps1",
                "scripts/run_checks.ps1",
                "tests/runtime_suite_runner.py",
                "tests/test_inventory_actions_browser.py",
                "tech_debt/SWEEP.md",
            }
        )

    def test_business_storage_api_mcp_and_server_config_paths_remain_rejected(self):
        for path in (
            "src/minimal_kanban/services/card_service_finance.py",
            "src/minimal_kanban/services/card_service_inventory.py",
            "src/minimal_kanban/storage/json_store.py",
            "src/minimal_kanban/api/server.py",
            "src/minimal_kanban/mcp/server.py",
            "docker-compose.yml",
            "deploy.sh",
            ".env",
            "data/state.json",
            "src/minimal_kanban/web_app_assets/source/unreviewed.js",
        ):
            with self.subTest(path=path), self.assertRaises(self.policy["ReleaseError"]):
                self.policy["validate_ui_changed_paths"](
                    {
                        "src/minimal_kanban/web_app_assets/source/app_main_before_printing.js",
                        path,
                    }
                )

    def test_empty_and_directory_wildcard_diffs_are_rejected(self):
        for changed in (set(), {"src/minimal_kanban/web_app_assets/source/*"}, {"tests/*"}):
            with self.subTest(changed=changed), self.assertRaises(self.policy["ReleaseError"]):
                self.policy["validate_ui_changed_paths"](changed)

    def test_native_preflight_still_uses_the_gate_for_the_exact_installed_diff(self):
        release = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "CrmOnlyRelease"
        )
        preflight = next(
            node
            for node in release.body
            if isinstance(node, ast.FunctionDef) and node.name == "preflight"
        )
        calls = [node for node in ast.walk(preflight) if isinstance(node, ast.Call)]
        gate = [
            node
            for node in calls
            if isinstance(node.func, ast.Name) and node.func.id == "validate_ui_changed_paths"
        ]
        self.assertEqual(len(gate), 1)
        self.assertEqual(ast.unparse(gate[0].args[0]), "changed")
        diff = [
            node
            for node in calls
            if isinstance(node.func, ast.Attribute)
            and node.func.attr == "git"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "diff"
        ]
        self.assertEqual(len(diff), 1)
        self.assertEqual(
            [ast.unparse(arg) for arg in diff[0].args],
            ["'diff'", "'--name-only'", "old_revision", "self.sha"],
        )
        self.assertLess(diff[0].lineno, gate[0].lineno)
