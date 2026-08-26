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
SCRIPT_PATH = ROOT / "scripts" / "code_health_audit.py"


def load_code_health_audit_module():
    spec = importlib.util.spec_from_file_location("code_health_audit", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("code_health_audit.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CodeHealthAuditTests(unittest.TestCase):
    def test_current_tree_has_no_unapproved_size_budget_issues(self) -> None:
        module = load_code_health_audit_module()

        self.assertEqual([], module.audit(ROOT))

    def test_every_current_repository_file_has_an_explicit_role(self) -> None:
        module = load_code_health_audit_module()

        inventory = module.repository_inventory(ROOT, include_untracked=True)

        self.assertGreater(len(inventory), 270)
        self.assertTrue(all(entry.role in module.TRACKED_FILE_ROLES for entry in inventory))
        self.assertEqual(len(inventory), len({entry.path for entry in inventory}))
        self.assertFalse(any("generated" in entry.flags for entry in inventory))

    def test_one_off_migrations_stay_visible_as_review_candidates(self) -> None:
        module = load_code_health_audit_module()

        for path in module.ONE_OFF_MIGRATIONS:
            entry = module.classify_repository_file(path)
            self.assertEqual("ops_tool", entry.role)
            self.assertEqual(
                {"migration_one_off", "delete_candidate", "review_required"},
                set(entry.flags),
            )

    def test_technical_debt_markdown_has_a_bounded_noncanonical_role(self) -> None:
        module = load_code_health_audit_module()

        entry = module.classify_repository_file("tech_debt/001-example.md")

        self.assertEqual("technical_debt_task", entry.role)
        self.assertEqual((), entry.flags)
        self.assertNotIn("tech_debt/001-example.md", module.CANONICAL_DOCS)

    def test_unclassified_and_generated_tracked_files_are_reported(self) -> None:
        module = load_code_health_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=temp_root, check=True, capture_output=True)
            mystery = temp_root / "mystery.bin"
            generated = temp_root / "output" / "bundle.bin"
            generated.parent.mkdir()
            mystery.write_bytes(b"unknown")
            generated.write_bytes(b"generated")
            subprocess.run(
                ["git", "add", "mystery.bin", "output/bundle.bin"],
                cwd=temp_root,
                check=True,
                capture_output=True,
            )

            issues = module.audit(temp_root)

        self.assertEqual(
            {"unclassified_tracked_file", "tracked_generated_artifact"},
            {issue.code for issue in issues},
        )

    def test_unapproved_large_module_is_reported(self) -> None:
        module = load_code_health_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            path = temp_root / "src" / "minimal_kanban" / "oversized.py"
            path.parent.mkdir(parents=True)
            path.write_text("x = 1\n" * (module.MAX_PY_MODULE_LINES + 1), encoding="utf-8")

            issues = module.audit(temp_root)

        self.assertEqual(["large_module"], [issue.code for issue in issues])
        self.assertEqual("src/minimal_kanban/oversized.py", issues[0].path)

    def test_allowed_large_module_is_documented_exception(self) -> None:
        module = load_code_health_audit_module()

        module_budget = module.ALLOWED_LARGE_MODULES["src/minimal_kanban/services/card_service.py"]
        class_budget = module.ALLOWED_LARGE_CLASSES[
            "src/minimal_kanban/services/card_service.py:CardService"
        ]
        function_budget = module.ALLOWED_LARGE_FUNCTIONS[
            "src/minimal_kanban/mcp/agent_gateway_v2.py:register_agent_gateway_v2"
        ]

        self.assertIsInstance(module_budget, module.RatchetBudget)
        self.assertEqual(module_budget.baseline, module_budget.max_allowed)
        self.assertEqual("012", module_budget.owner_task)
        self.assertTrue(module_budget.reason)
        self.assertEqual(class_budget.baseline, class_budget.max_allowed)
        self.assertEqual(function_budget.baseline, function_budget.max_allowed)

    def test_current_ratchets_are_typed_owned_and_deterministically_measured(self) -> None:
        module = load_code_health_audit_module()

        report = module.build_report(ROOT)
        ratchets = report["ratchets"]

        self.assertEqual(34, report["summary"]["size_exemptions_configured"])
        self.assertEqual(2, report["summary"]["complexity_ratchets_configured"])
        self.assertEqual(36, len(ratchets))
        self.assertEqual(
            sorted(ratchets, key=lambda entry: (entry["metric"], entry["target"])),
            ratchets,
        )
        self.assertTrue(all(entry["reason"] for entry in ratchets))
        self.assertTrue(all(entry["owner_task"].isdigit() for entry in ratchets))
        self.assertTrue(all(entry["present"] for entry in ratchets))
        self.assertTrue(all(entry["delta"] <= 0 for entry in ratchets))
        self.assertFalse(
            {
                issue["code"]
                for issue in report["issues"]
                if issue["code"].endswith("owner_task")
                or issue["code"].startswith("invalid_ratchet")
                or issue["code"] == "missing_ratchet_reason"
            }
        )

    def test_module_size_ratchet_blocks_growth_and_allows_shrink(self) -> None:
        module = load_code_health_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            task_root = temp_root / "tech_debt"
            task_root.mkdir()
            (task_root / "001-owner.md").write_text("# Owner\n", encoding="utf-8")
            path = temp_root / "sample.py"
            budget = module.RatchetBudget("fixture module", 3, 3, "001")
            with patch.multiple(
                module,
                ALLOWED_LARGE_MODULES={"sample.py": budget},
                ALLOWED_LARGE_CLASSES={},
                ALLOWED_LARGE_FUNCTIONS={},
                COMPLEXITY_RATCHETS={},
                EXPECTED_SIZE_EXEMPTION_COUNT=1,
            ):
                path.write_text("x = 1\n" * 3, encoding="utf-8")
                self.assertEqual([], module.audit(temp_root))

                path.write_text("x = 1\n" * 4, encoding="utf-8")
                growth_issues = module.audit(temp_root)

                path.write_text("x = 1\n" * 2, encoding="utf-8")
                shrink_report = module.build_report(temp_root)

        self.assertEqual(["size_ratchet_exceeded"], [issue.code for issue in growth_issues])
        self.assertIn("current=4; max=3; delta=+1", growth_issues[0].detail)
        self.assertTrue(shrink_report["ok"])
        self.assertEqual(-1, shrink_report["ratchets"][0]["delta"])

    def test_class_and_function_size_ratchets_are_independent(self) -> None:
        module = load_code_health_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            task_root = temp_root / "tech_debt"
            task_root.mkdir()
            (task_root / "001-owner.md").write_text("# Owner\n", encoding="utf-8")
            path = temp_root / "sample.py"
            class_budget = module.RatchetBudget("fixture class", 3, 3, "001")
            function_budget = module.RatchetBudget("fixture function", 2, 2, "001")
            with patch.multiple(
                module,
                ALLOWED_LARGE_MODULES={},
                ALLOWED_LARGE_CLASSES={"sample.py:Example": class_budget},
                ALLOWED_LARGE_FUNCTIONS={"sample.py:Example.run": function_budget},
                COMPLEXITY_RATCHETS={},
                EXPECTED_SIZE_EXEMPTION_COUNT=2,
            ):
                path.write_text(
                    "class Example:\n    def run(self):\n        return 1\n",
                    encoding="utf-8",
                )
                self.assertEqual([], module.audit(temp_root))

                path.write_text(
                    "class Example:\n    def run(self):\n        value = 1\n        return value\n",
                    encoding="utf-8",
                )
                issues = module.audit(temp_root)

        self.assertEqual(2, len(issues))
        self.assertTrue(all(issue.code == "size_ratchet_exceeded" for issue in issues))
        self.assertTrue(any("class_lines" in issue.detail for issue in issues))
        self.assertTrue(any("function_lines" in issue.detail for issue in issues))

    def test_complexity_ratchet_blocks_growth_and_allows_shrink(self) -> None:
        module = load_code_health_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            task_root = temp_root / "tech_debt"
            task_root.mkdir()
            (task_root / "001-owner.md").write_text("# Owner\n", encoding="utf-8")
            path = temp_root / "sample.py"
            budget = module.RatchetBudget("fixture complexity", 2, 2, "001")
            with patch.multiple(
                module,
                ALLOWED_LARGE_MODULES={},
                ALLOWED_LARGE_CLASSES={},
                ALLOWED_LARGE_FUNCTIONS={},
                COMPLEXITY_RATCHETS={"sample.py:target": budget},
                EXPECTED_SIZE_EXEMPTION_COUNT=0,
            ):
                path.write_text(
                    "def target(value):\n    if value:\n        return 1\n    return 0\n",
                    encoding="utf-8",
                )
                self.assertEqual([], module.audit(temp_root))

                path.write_text(
                    "def target(value):\n"
                    "    if value > 1:\n"
                    "        return 2\n"
                    "    if value:\n"
                    "        return 1\n"
                    "    return 0\n",
                    encoding="utf-8",
                )
                growth_issues = module.audit(temp_root)

                path.write_text("def target(value):\n    return value\n", encoding="utf-8")
                shrink_report = module.build_report(temp_root)

        self.assertEqual(["complexity_ratchet_exceeded"], [issue.code for issue in growth_issues])
        self.assertIn("current=3; max=2; delta=+1", growth_issues[0].detail)
        self.assertTrue(shrink_report["ok"])
        self.assertEqual(-1, shrink_report["ratchets"][0]["delta"])

    def test_missing_ratchet_target_fails_closed(self) -> None:
        module = load_code_health_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            task_root = temp_root / "tech_debt"
            task_root.mkdir()
            (task_root / "001-owner.md").write_text("# Owner\n", encoding="utf-8")
            budget = module.RatchetBudget("fixture target", 1, 1, "001")
            with patch.multiple(
                module,
                ALLOWED_LARGE_MODULES={},
                ALLOWED_LARGE_CLASSES={},
                ALLOWED_LARGE_FUNCTIONS={"missing.py:target": budget},
                COMPLEXITY_RATCHETS={},
                EXPECTED_SIZE_EXEMPTION_COUNT=1,
            ):
                report = module.build_report(temp_root)

        self.assertFalse(report["ok"])
        self.assertEqual(
            ["missing_ratchet_target"],
            [issue["code"] for issue in report["issues"]],
        )
        self.assertFalse(report["ratchets"][0]["present"])

    def test_invalid_missing_and_duplicate_owner_configuration_is_reported(self) -> None:
        module = load_code_health_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            task_root = temp_root / "tech_debt"
            task_root.mkdir()
            (task_root / "001-owner.md").write_text("# Owner\n", encoding="utf-8")
            (task_root / "002-first.md").write_text("# First\n", encoding="utf-8")
            (task_root / "002-second.md").write_text("# Second\n", encoding="utf-8")
            registry = {
                "missing-reason.py": module.RatchetBudget("", 1, 1, "001"),
                "invalid-owner.py": module.RatchetBudget("reason", 1, 1, "bad"),
                "duplicate-owner.py": module.RatchetBudget("reason", 1, 1, "002"),
                "missing-owner.py": module.RatchetBudget("reason", 1, 1, "003"),
            }
            with patch.multiple(
                module,
                ALLOWED_LARGE_MODULES=registry,
                ALLOWED_LARGE_CLASSES={},
                ALLOWED_LARGE_FUNCTIONS={},
                COMPLEXITY_RATCHETS={},
                EXPECTED_SIZE_EXEMPTION_COUNT=4,
            ):
                issues = module._ratchet_configuration_issues(temp_root)

        self.assertEqual(
            {
                "missing_ratchet_reason",
                "invalid_owner_task",
                "missing_owner_task",
                "duplicate_owner_task",
            },
            {issue.code for issue in issues},
        )

    def test_json_and_text_reports_expose_current_max_and_delta_stably(self) -> None:
        module = load_code_health_audit_module()

        first = module.build_report(ROOT)
        second = module.build_report(ROOT)
        first_json = json.dumps(first, ensure_ascii=False, allow_nan=False)
        second_json = json.dumps(second, ensure_ascii=False, allow_nan=False)
        first_text = module.render_text(first)

        self.assertEqual(first_json, second_json)
        self.assertEqual(first_text, module.render_text(second))
        self.assertIn("current=", first_text)
        self.assertIn(" max=", first_text)
        self.assertIn(" delta=", first_text)
        self.assertTrue(
            all({"current", "max_allowed", "delta"} <= set(entry) for entry in first["ratchets"])
        )

    def test_untracked_files_are_opt_in_for_server_local_safety(self) -> None:
        module = load_code_health_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=temp_root, check=True, capture_output=True)
            tracked = temp_root / "tracked.py"
            tracked.write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.py"], cwd=temp_root, check=True)
            untracked = temp_root / "oversized_local.py"
            untracked.write_text("x = 1\n" * (module.MAX_PY_MODULE_LINES + 1), encoding="utf-8")

            default_issues = module.audit(temp_root)
            opt_in_issues = module.audit(temp_root, include_untracked=True)

        self.assertEqual([], default_issues)
        self.assertEqual(["large_module"], [issue.code for issue in opt_in_issues])

    def test_deleted_tracked_files_are_ignored_in_dirty_tree(self) -> None:
        module = load_code_health_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=temp_root, check=True, capture_output=True)
            tracked = temp_root / "removed.py"
            tracked.write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "removed.py"], cwd=temp_root, check=True)
            tracked.unlink()

            issues = module.audit(temp_root)

        self.assertEqual([], issues)

    def test_git_inventory_timeout_falls_back_to_filesystem_scan(self) -> None:
        module = load_code_health_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            path = temp_root / "src" / "minimal_kanban" / "oversized.py"
            path.parent.mkdir(parents=True)
            path.write_text("x = 1\n" * (module.MAX_PY_MODULE_LINES + 1), encoding="utf-8")

            with patch.object(
                module.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["git"], timeout=1),
            ) as run:
                issues = module.audit(temp_root)

        self.assertEqual(["large_module"], [issue.code for issue in issues])
        self.assertIs(run.call_args.kwargs["stdin"], module.subprocess.DEVNULL)

    def test_oversized_python_source_is_reported_without_parsing(self) -> None:
        module = load_code_health_audit_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            path = temp_root / "huge.py"
            path.write_text("x" * 16, encoding="utf-8")

            with patch.object(module, "CODE_HEALTH_SOURCE_MAX_BYTES", 8):
                issues = module.audit(temp_root)

        self.assertEqual(["source_read_error"], [issue.code for issue in issues])
        self.assertIn("too large", issues[0].detail)


if __name__ == "__main__":
    unittest.main()
