from __future__ import annotations

import importlib.util
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

        self.assertIn(
            "src/minimal_kanban/services/card_service.py",
            module.ALLOWED_LARGE_MODULES,
        )
        self.assertIn(
            "src/minimal_kanban/services/card_service.py:CardService",
            module.ALLOWED_LARGE_CLASSES,
        )
        self.assertIn(
            "src/minimal_kanban/mcp/agent_gateway_v2.py:register_agent_gateway_v2",
            module.ALLOWED_LARGE_FUNCTIONS,
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
