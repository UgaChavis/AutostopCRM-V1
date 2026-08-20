from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GIT_COMMAND_TIMEOUT_SECONDS = 15
CODE_HEALTH_SOURCE_MAX_BYTES = 2 * 1024 * 1024

MAX_PY_MODULE_LINES = 2500
MAX_TEST_MODULE_LINES = 3000
MAX_CLASS_LINES = 2500
MAX_FUNCTION_LINES = 450

SKIP_DIRS = {
    ".git",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "build.staging",
    "dist",
    "dist.staging",
    "release",
    "release.staging",
}

ALLOWED_LARGE_MODULES = {
    "scripts/attest_agent_gateway_v2.py": "Gateway attestation scenario suite split target",
    "src/minimal_kanban/mcp/agent_gateway_v2.py": "Gateway workflow executor split target",
    "src/minimal_kanban/mcp/raw_gateway.py": "raw readback verifier split target",
    "src/minimal_kanban/services/card_service.py": "domain facade split target",
    "src/minimal_kanban/services/card_service_finance.py": "finance domain split target",
    "src/minimal_kanban/services/card_service_payroll.py": "payroll domain split target",
    "src/minimal_kanban/services/snapshot_service.py": "snapshot serialization split target",
    "src/minimal_kanban/agent/runner.py": "agent orchestration split target",
    "src/minimal_kanban/mcp/server.py": "MCP registry split target",
    "src/minimal_kanban/printing/service.py": "print rendering workflow split target",
    "tests/test_service.py": "legacy broad service coverage pending domain split",
    "tests/test_api.py": "legacy broad API coverage pending route split",
    "tests/test_mcp.py": "legacy broad MCP coverage pending contract split",
    "tests/test_agent_gateway_v2.py": "Gateway contract coverage pending family split",
    "tests/test_web_assets.py": "web asset contract coverage pending chunk split",
}

ALLOWED_LARGE_CLASSES = {
    "src/minimal_kanban/services/card_service.py:CardService": "domain facade split target",
    "src/minimal_kanban/services/card_service_payroll.py:CardServicePayrollMixin": "payroll domain split target",
    "src/minimal_kanban/services/card_service_finance.py:CardServiceFinanceMixin": "finance domain split target",
    "src/minimal_kanban/services/snapshot_service.py:SnapshotService": "snapshot serialization split target",
    "src/minimal_kanban/agent/runner.py:AgentRunner": "agent orchestration split target",
    "tests/test_api.py:ApiServerTests": "legacy broad API coverage pending route split",
    "tests/test_agent_gateway_v2.py:AgentGatewayV2Tests": "Gateway contract coverage pending family split",
    "tests/test_service.py:CardServiceTests": "legacy broad service coverage pending domain split",
    "tests/test_web_assets.py:WebAssetsTests": "web asset contract coverage pending chunk split",
}

ALLOWED_LARGE_FUNCTIONS = {
    "scripts/attest_agent_gateway_v2.py:_finance_apply_audit_safe_fixes_case": "finance attestation scenario split target",
    "scripts/browser_smoke.py:_desktop_scenarios": "browser smoke scenario split target",
    "src/minimal_kanban/api/server.py:_make_handler": "API handler split target",
    "src/minimal_kanban/demo_seed.py:_demo_specs": "demo seed data split target",
    "src/minimal_kanban/mcp/agent_gateway_v2.py:register_agent_gateway_v2": "Gateway v2 registry split target",
    "src/minimal_kanban/mcp/agent_gateway_v2.py:_execute_workflow": "Gateway workflow executor split target",
    "src/minimal_kanban/mcp/agent_gateway_v2.py:call_raw_capability": "raw capability executor split target",
    "src/minimal_kanban/mcp/raw_gateway.py:verify_virtual_api_write_readback": "raw readback verifier split target",
    "src/minimal_kanban/mcp/server.py:create_mcp_server": "MCP registry split target",
    "src/minimal_kanban/printing/defaults.py:builtin_template_records": "print template data split target",
    "tests/test_mcp.py:test_mcp_tools_reach_backend": "MCP contract test split target",
}

CANONICAL_DOCS = frozenset(
    {
        "AGENTS.md",
        "API_GUIDE.md",
        "AUTOSTOPCRM_FULL_INSTRUCTION.txt",
        "CHATGPT_CONNECTOR_SETUP.md",
        "MCP_GUIDE.md",
        "README.md",
        "docs/OPERATIONS_RUNBOOK.md",
    }
)
DEPENDENCY_MANIFESTS = frozenset(
    {"requirements.txt", "requirements-dev.txt", "requirements-runtime.txt"}
)
CONFIG_DEPLOY_FILES = frozenset(
    {
        ".dockerignore",
        ".gitattributes",
        ".gitignore",
        ".pre-commit-config.yaml",
        ".python-version",
        "Dockerfile",
        "deploy.sh",
        "docker-compose.yml",
        "ruff.toml",
    }
)
ONE_OFF_MIGRATIONS = frozenset(
    {
        "scripts/apply_payroll_policy_2026_07_13.py",
        "scripts/normalize_cashboxes_after_safe_fix.py",
    }
)
GENERATED_PATH_PREFIXES = (
    "build/",
    "dist/",
    "output/",
    "release/",
)
TRACKED_FILE_ROLES = frozenset(
    {
        "canonical_doc",
        "manifest",
        "runtime_code",
        "runtime_asset",
        "ops_tool",
        "test",
        "config_deploy",
    }
)


@dataclass(frozen=True)
class CodeHealthIssue:
    code: str
    path: str
    detail: str


@dataclass(frozen=True)
class TrackedFileClassification:
    path: str
    role: str
    flags: tuple[str, ...] = ()


def _repository_files(root: Path, *, include_untracked: bool = False) -> list[Path]:
    git_args = ["git", "ls-files", "--cached"]
    if include_untracked:
        git_args.extend(["--others", "--exclude-standard"])
    try:
        result = subprocess.run(
            git_args,
            cwd=root,
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return sorted(
            path
            for path in root.rglob("*")
            if path.is_file()
            and not any(part in SKIP_DIRS for part in path.relative_to(root).parts)
        )
    return sorted(
        root / line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and (root / line.strip()).is_file()
    )


def classify_repository_file(path: str) -> TrackedFileClassification:
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    flags: list[str] = []
    if normalized in ONE_OFF_MIGRATIONS:
        flags.extend(["migration_one_off", "delete_candidate", "review_required"])

    if normalized in CANONICAL_DOCS:
        role = "canonical_doc"
    elif normalized in DEPENDENCY_MANIFESTS:
        role = "manifest"
    elif normalized.startswith("tests/"):
        role = "test"
    elif normalized.startswith("scripts/"):
        role = (
            "config_deploy"
            if normalized.endswith((".conf.example", ".http-bootstrap.conf.example"))
            else "ops_tool"
        )
    elif normalized.startswith(".github/") or normalized in CONFIG_DEPLOY_FILES:
        role = "config_deploy"
    elif normalized in {"main.py", "main_mcp.py"} or (
        normalized.startswith("src/") and normalized.endswith(".py")
    ):
        role = "runtime_code"
    elif normalized.startswith("src/"):
        role = "runtime_asset"
    elif normalized.endswith(".py"):
        role = "runtime_code"
    else:
        role = ""

    if normalized.startswith(GENERATED_PATH_PREFIXES) or normalized.endswith((".pyc", ".pyo")):
        flags.extend(["generated", "review_required"])
    return TrackedFileClassification(normalized, role, tuple(flags))


def repository_inventory(
    root: Path = ROOT, *, include_untracked: bool = False
) -> list[TrackedFileClassification]:
    return [
        classify_repository_file(_relative(path, root))
        for path in _repository_files(root, include_untracked=include_untracked)
    ]


def _tracked_python_files(root: Path, *, include_untracked: bool = False) -> list[Path]:
    return [
        path
        for path in _repository_files(root, include_untracked=include_untracked)
        if path.suffix == ".py"
    ]


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _read_python_source(path: Path) -> str:
    with path.open("rb") as handle:
        raw = handle.read(CODE_HEALTH_SOURCE_MAX_BYTES + 1)
    if len(raw) > CODE_HEALTH_SOURCE_MAX_BYTES:
        raise ValueError(f"Python source file is too large: {path}")
    return raw.decode("utf-8")


def _node_length(node: ast.AST) -> int:
    end_lineno = getattr(node, "end_lineno", getattr(node, "lineno", 0))
    return max(int(end_lineno) - int(getattr(node, "lineno", 0)) + 1, 0)


def audit(root: Path = ROOT, *, include_untracked: bool = False) -> list[CodeHealthIssue]:
    issues: list[CodeHealthIssue] = []
    for entry in repository_inventory(root, include_untracked=include_untracked):
        if not entry.role:
            issues.append(
                CodeHealthIssue(
                    "unclassified_tracked_file",
                    entry.path,
                    "tracked file has no explicit repository role",
                )
            )
        if "generated" in entry.flags:
            issues.append(
                CodeHealthIssue(
                    "tracked_generated_artifact",
                    entry.path,
                    "generated build/cache artifact must not be versioned",
                )
            )
    for path in _tracked_python_files(root, include_untracked=include_untracked):
        relative_path = _relative(path, root)
        try:
            source = _read_python_source(path)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            issues.append(
                CodeHealthIssue(
                    "source_read_error",
                    relative_path,
                    str(exc),
                )
            )
            continue
        line_count = len(source.splitlines())
        module_budget = (
            MAX_TEST_MODULE_LINES if relative_path.startswith("tests/") else MAX_PY_MODULE_LINES
        )
        if line_count > module_budget and relative_path not in ALLOWED_LARGE_MODULES:
            issues.append(
                CodeHealthIssue(
                    "large_module",
                    relative_path,
                    f"{line_count} lines exceeds budget {module_budget}",
                )
            )

        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            issues.append(
                CodeHealthIssue(
                    "syntax_error",
                    relative_path,
                    f"Python parse failed: {exc.msg} at line {exc.lineno}",
                )
            )
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                key = f"{relative_path}:{node.name}"
                length = _node_length(node)
                if length > MAX_CLASS_LINES and key not in ALLOWED_LARGE_CLASSES:
                    issues.append(
                        CodeHealthIssue(
                            "large_class",
                            relative_path,
                            f"{node.name} has {length} lines; budget {MAX_CLASS_LINES}",
                        )
                    )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                key = f"{relative_path}:{node.name}"
                length = _node_length(node)
                if length > MAX_FUNCTION_LINES and key not in ALLOWED_LARGE_FUNCTIONS:
                    issues.append(
                        CodeHealthIssue(
                            "large_function",
                            relative_path,
                            f"{node.name} has {length} lines; budget {MAX_FUNCTION_LINES}",
                        )
                    )
    return issues


def _summary(root: Path, *, include_untracked: bool) -> dict[str, Any]:
    files = _tracked_python_files(root, include_untracked=include_untracked)
    inventory = repository_inventory(root, include_untracked=include_untracked)
    role_counts = {
        role: sum(entry.role == role for entry in inventory) for role in sorted(TRACKED_FILE_ROLES)
    }
    return {
        "python_files": len(files),
        "repository_files": len(inventory),
        "repository_roles": role_counts,
        "include_untracked": include_untracked,
        "budgets": {
            "py_module_lines": MAX_PY_MODULE_LINES,
            "test_module_lines": MAX_TEST_MODULE_LINES,
            "class_lines": MAX_CLASS_LINES,
            "function_lines": MAX_FUNCTION_LINES,
        },
        "allowed_large_modules": ALLOWED_LARGE_MODULES,
        "allowed_large_classes": ALLOWED_LARGE_CLASSES,
        "allowed_large_functions": ALLOWED_LARGE_FUNCTIONS,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit AutoStop CRM code-health budgets.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--format", choices={"text", "json"}, default="text")
    parser.add_argument(
        "--include-untracked",
        action="store_true",
        help="Also audit untracked Python files in the working tree.",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    issues = audit(root, include_untracked=args.include_untracked)
    payload = {
        "ok": not issues,
        "summary": _summary(root, include_untracked=args.include_untracked),
        "inventory": [
            asdict(entry)
            for entry in repository_inventory(root, include_untracked=args.include_untracked)
        ],
        "issues": [asdict(issue) for issue in issues],
    }
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    elif issues:
        print(f"Code health audit found {len(issues)} issue(s):")
        for issue in issues:
            print(f"- {issue.code}: {issue.path}: {issue.detail}")
    else:
        summary = payload["summary"]
        print(
            "Code health audit passed: "
            f"{summary['repository_files']} repository files classified; "
            "no unapproved size-budget issues."
        )
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
