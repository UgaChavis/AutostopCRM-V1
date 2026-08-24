from __future__ import annotations

import configparser
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "coverage_audit.py"
MANIFEST_PATH = ROOT / "scripts" / "coverage_baseline.json"


def load_coverage_audit_module():
    spec = importlib.util.spec_from_file_location("coverage_audit", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("coverage_audit.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def summary(
    *, covered_lines: int, statements: int, covered_branches: int, branches: int
) -> dict[str, int]:
    return {
        "covered_lines": covered_lines,
        "num_statements": statements,
        "covered_branches": covered_branches,
        "num_branches": branches,
    }


def report(*, files: dict[str, dict[str, int]], totals: dict[str, int]) -> dict[str, object]:
    return {
        "meta": {"branch_coverage": True, "format": 3, "version": "7.15.3"},
        "files": {path: {"summary": metrics} for path, metrics in files.items()},
        "totals": totals,
    }


def manifest(*, minimum_percent: float = 75.0) -> dict[str, object]:
    baseline = summary(
        covered_lines=8,
        statements=10,
        covered_branches=1,
        branches=2,
    )
    return {
        "schema_version": 1,
        "measurements": {
            "runtime": {"default_json": "runtime.json"},
            "release_scripts": {"default_json": "release.json"},
        },
        "floors": [
            {
                "id": "runtime_global",
                "measurement": "runtime",
                "scope": "global",
                "paths": [],
                "minimum_percent": minimum_percent,
                "baseline": baseline,
            },
            {
                "id": "release_backup_restore",
                "measurement": "release_scripts",
                "scope": "files",
                "paths": ["scripts/agent_release_backup.py"],
                "minimum_percent": minimum_percent,
                "baseline": baseline,
            },
        ],
    }


class CoverageAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_coverage_audit_module()

    def test_dev_dependency_and_repo_config_enable_branch_parallel_measurement(self) -> None:
        requirements = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
        config = configparser.ConfigParser()
        config.read(ROOT / ".coveragerc", encoding="utf-8")

        self.assertIn("coverage==7.15.3", requirements.splitlines())
        self.assertTrue(config.getboolean("run", "branch"))
        self.assertTrue(config.getboolean("run", "relative_files"))
        self.assertTrue(config.getboolean("run", "parallel"))
        self.assertIn("src/minimal_kanban", config.get("run", "source"))

    def test_repository_manifest_has_exact_critical_surfaces_and_bounded_floors(self) -> None:
        value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        floors = {floor["id"]: floor for floor in value["floors"]}

        self.assertEqual(
            {
                "runtime_global",
                "operator_auth",
                "api_server",
                "deployment_security",
                "mcp_oauth_provider",
                "gateway_ledger_raw",
                "json_store_change_feed",
                "finance",
                "payroll",
                "repair_order",
                "attachments",
                "printing",
                "release_backup_restore",
            },
            set(floors),
        )
        self.assertEqual(["src/minimal_kanban/operator_auth.py"], floors["operator_auth"]["paths"])
        self.assertEqual(["src/minimal_kanban/api/server.py"], floors["api_server"]["paths"])
        self.assertEqual(
            ["scripts/agent_release_backup.py"],
            floors["release_backup_restore"]["paths"],
        )
        for floor in floors.values():
            metrics = self.module.CoverageMetrics.from_summary(floor["baseline"], label=floor["id"])
            self.assertGreater(floor["minimum_percent"], 0.0)
            self.assertLess(floor["minimum_percent"], 100.0)
            self.assertGreaterEqual(metrics.percent, floor["minimum_percent"])
            self.assertLessEqual(metrics.percent - floor["minimum_percent"], 0.5)

    def test_matching_runtime_and_release_reports_pass(self) -> None:
        baseline = summary(covered_lines=8, statements=10, covered_branches=1, branches=2)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            runtime_path = temp_root / "runtime.json"
            release_path = temp_root / "release.json"
            manifest_path.write_text(json.dumps(manifest()), encoding="utf-8")
            runtime_path.write_text(
                json.dumps(report(files={"src\\module.py": baseline}, totals=baseline)),
                encoding="utf-8",
            )
            release_path.write_text(
                json.dumps(
                    report(
                        files={"scripts\\agent_release_backup.py": baseline},
                        totals=baseline,
                    )
                ),
                encoding="utf-8",
            )

            result = self.module.audit_coverage(
                manifest_path,
                report_overrides={
                    "runtime": runtime_path,
                    "release_scripts": release_path,
                },
            )

        self.assertTrue(result.ok, result.issues)
        self.assertEqual(2, len(result.results))
        self.assertTrue(all(floor.passed for floor in result.results))

    def test_current_coverage_below_floor_fails(self) -> None:
        baseline = summary(covered_lines=8, statements=10, covered_branches=1, branches=2)
        reduced = summary(covered_lines=7, statements=10, covered_branches=1, branches=2)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            runtime_path = temp_root / "runtime.json"
            release_path = temp_root / "release.json"
            manifest_path.write_text(json.dumps(manifest()), encoding="utf-8")
            runtime_path.write_text(
                json.dumps(report(files={"src/module.py": reduced}, totals=reduced)),
                encoding="utf-8",
            )
            release_path.write_text(
                json.dumps(
                    report(
                        files={"scripts/agent_release_backup.py": baseline},
                        totals=baseline,
                    )
                ),
                encoding="utf-8",
            )

            result = self.module.audit_coverage(
                manifest_path,
                report_overrides={
                    "runtime": runtime_path,
                    "release_scripts": release_path,
                },
            )

        self.assertFalse(result.ok)
        self.assertIn("coverage_below_floor", {issue.code for issue in result.issues})

    def test_manifest_rejects_floor_more_than_half_point_below_baseline(self) -> None:
        baseline = summary(covered_lines=8, statements=10, covered_branches=1, branches=2)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            runtime_path = temp_root / "runtime.json"
            release_path = temp_root / "release.json"
            manifest_path.write_text(json.dumps(manifest(minimum_percent=74.0)), encoding="utf-8")
            runtime_path.write_text(
                json.dumps(report(files={"src/module.py": baseline}, totals=baseline)),
                encoding="utf-8",
            )
            release_path.write_text(
                json.dumps(
                    report(
                        files={"scripts/agent_release_backup.py": baseline},
                        totals=baseline,
                    )
                ),
                encoding="utf-8",
            )

            result = self.module.audit_coverage(
                manifest_path,
                report_overrides={
                    "runtime": runtime_path,
                    "release_scripts": release_path,
                },
            )

        self.assertFalse(result.ok)
        self.assertEqual({"floor_invalid"}, {issue.code for issue in result.issues})

    def test_non_branch_report_is_rejected(self) -> None:
        baseline = summary(covered_lines=8, statements=10, covered_branches=1, branches=2)
        bad_report = report(files={"src/module.py": baseline}, totals=baseline)
        bad_report["meta"]["branch_coverage"] = False
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            runtime_path = temp_root / "runtime.json"
            release_path = temp_root / "release.json"
            manifest_path.write_text(json.dumps(manifest()), encoding="utf-8")
            runtime_path.write_text(json.dumps(bad_report), encoding="utf-8")
            release_path.write_text(
                json.dumps(
                    report(
                        files={"scripts/agent_release_backup.py": baseline},
                        totals=baseline,
                    )
                ),
                encoding="utf-8",
            )

            result = self.module.audit_coverage(
                manifest_path,
                report_overrides={
                    "runtime": runtime_path,
                    "release_scripts": release_path,
                },
            )

        self.assertFalse(result.ok)
        self.assertIn("coverage_report_invalid", {issue.code for issue in result.issues})

    def test_missing_critical_file_is_not_silently_ignored(self) -> None:
        baseline = summary(covered_lines=8, statements=10, covered_branches=1, branches=2)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            runtime_path = temp_root / "runtime.json"
            release_path = temp_root / "release.json"
            manifest_path.write_text(json.dumps(manifest()), encoding="utf-8")
            runtime_path.write_text(
                json.dumps(report(files={"src/module.py": baseline}, totals=baseline)),
                encoding="utf-8",
            )
            release_path.write_text(json.dumps(report(files={}, totals=baseline)), encoding="utf-8")

            result = self.module.audit_coverage(
                manifest_path,
                report_overrides={
                    "runtime": runtime_path,
                    "release_scripts": release_path,
                },
            )

        self.assertFalse(result.ok)
        self.assertIn("floor_invalid", {issue.code for issue in result.issues})
        self.assertTrue(any("no measured file" in issue.detail for issue in result.issues))

    def test_cli_emits_machine_readable_result(self) -> None:
        baseline = summary(covered_lines=8, statements=10, covered_branches=1, branches=2)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            runtime_path = temp_root / "runtime.json"
            release_path = temp_root / "release.json"
            manifest_path.write_text(json.dumps(manifest()), encoding="utf-8")
            runtime_path.write_text(
                json.dumps(report(files={"src/module.py": baseline}, totals=baseline)),
                encoding="utf-8",
            )
            release_path.write_text(
                json.dumps(
                    report(
                        files={"scripts/agent_release_backup.py": baseline},
                        totals=baseline,
                    )
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--manifest",
                    str(manifest_path),
                    "--coverage-json",
                    f"runtime={runtime_path}",
                    "--coverage-json",
                    f"release_scripts={release_path}",
                    "--format",
                    "json",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(0, completed.returncode, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(2, payload["summary"]["floors"])


if __name__ == "__main__":
    unittest.main()
