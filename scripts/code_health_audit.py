from __future__ import annotations

import argparse
import ast
import json
import re
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
EXPECTED_SIZE_EXEMPTION_COUNT = 34


@dataclass(frozen=True)
class RatchetBudget:
    reason: str
    baseline: int
    max_allowed: int
    owner_task: str


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
    "scripts/attest_agent_gateway_v2.py": RatchetBudget(
        "Gateway attestation scenario suite split target", 9498, 9498, "207"
    ),
    "src/minimal_kanban/mcp/agent_gateway_v2.py": RatchetBudget(
        "Gateway workflow executor split target", 3547, 3547, "009"
    ),
    "src/minimal_kanban/mcp/raw_gateway.py": RatchetBudget(
        "raw readback verifier split target", 1465, 1465, "009"
    ),
    "src/minimal_kanban/services/card_service.py": RatchetBudget(
        "domain facade split target", 11627, 11627, "012"
    ),
    "src/minimal_kanban/services/card_service_finance.py": RatchetBudget(
        "finance domain split target", 3048, 3048, "019"
    ),
    "src/minimal_kanban/services/card_service_payroll.py": RatchetBudget(
        "payroll domain split target", 4805, 4805, "013"
    ),
    "src/minimal_kanban/services/snapshot_service.py": RatchetBudget(
        "snapshot serialization split target", 2879, 2879, "018"
    ),
    "src/minimal_kanban/agent/runner.py": RatchetBudget(
        "agent orchestration split target after the runtime keep decision", 5093, 5093, "206"
    ),
    "src/minimal_kanban/mcp/server.py": RatchetBudget(
        "MCP registry split target", 3551, 3551, "008"
    ),
    "src/minimal_kanban/printing/service.py": RatchetBudget(
        "print rendering workflow split target", 4229, 4229, "014"
    ),
    "src/minimal_kanban/printing/web_module.py": RatchetBudget(
        "embedded print UI asset split target", 3367, 3367, "021"
    ),
    "tests/test_service.py": RatchetBudget(
        "legacy broad service coverage pending domain split", 13514, 13514, "003"
    ),
    "tests/test_api.py": RatchetBudget(
        "legacy broad API coverage pending route split", 7692, 7692, "003"
    ),
    "tests/test_agent_gateway_v2.py": RatchetBudget(
        "Gateway contract coverage pending family split", 4447, 4447, "003"
    ),
    "tests/test_web_assets.py": RatchetBudget(
        "web asset contract coverage pending chunk split", 5963, 5963, "003"
    ),
}

ALLOWED_LARGE_CLASSES = {
    "src/minimal_kanban/printing/service.py:PrintModuleService": RatchetBudget(
        "print rendering and draft-store split target", 2839, 2839, "014"
    ),
    "src/minimal_kanban/services/card_service.py:CardService": RatchetBudget(
        "domain facade split target", 11122, 11122, "012"
    ),
    "src/minimal_kanban/services/card_service_payroll.py:CardServicePayrollMixin": RatchetBudget(
        "payroll domain split target", 4608, 4608, "013"
    ),
    "src/minimal_kanban/services/card_service_finance.py:CardServiceFinanceMixin": RatchetBudget(
        "finance domain split target", 3002, 3002, "019"
    ),
    "src/minimal_kanban/services/snapshot_service.py:SnapshotService": RatchetBudget(
        "snapshot serialization split target", 2574, 2574, "018"
    ),
    "src/minimal_kanban/agent/runner.py:AgentRunner": RatchetBudget(
        "agent orchestration split target after the runtime keep decision", 4865, 4865, "206"
    ),
    "tests/test_api.py:ApiServerTests": RatchetBudget(
        "legacy broad API coverage pending route split", 7268, 7268, "003"
    ),
    "tests/test_agent_gateway_v2.py:AgentGatewayV2Tests": RatchetBudget(
        "Gateway contract coverage pending family split", 3031, 3031, "003"
    ),
    "tests/test_service.py:CardServiceTests": RatchetBudget(
        "legacy broad service coverage pending domain split", 13296, 13296, "003"
    ),
    "tests/test_web_assets.py:WebAssetsTests": RatchetBudget(
        "web asset contract coverage pending chunk split", 5905, 5905, "003"
    ),
}

ALLOWED_LARGE_FUNCTIONS = {
    "scripts/attest_agent_gateway_v2.py:_finance_apply_audit_safe_fixes_case": RatchetBudget(
        "finance attestation scenario split target", 457, 457, "207"
    ),
    "src/minimal_kanban/demo_seed.py:_demo_specs": RatchetBudget(
        "bounded data-only demo seed factory", 957, 957, "001"
    ),
    "src/minimal_kanban/mcp/agent_gateway_v2.py:register_agent_gateway_v2": RatchetBudget(
        "Gateway v2 registry split target", 3288, 3288, "008"
    ),
    "src/minimal_kanban/mcp/agent_gateway_v2.py:register_agent_gateway_v2._execute_workflow": RatchetBudget(
        "Gateway workflow executor split target", 868, 868, "009"
    ),
    "src/minimal_kanban/mcp/agent_gateway_v2.py:register_agent_gateway_v2.call_raw_capability": RatchetBudget(
        "raw capability executor split target", 707, 707, "009"
    ),
    "src/minimal_kanban/mcp/raw_gateway.py:verify_virtual_api_write_readback": RatchetBudget(
        "raw readback verifier split target", 966, 966, "009"
    ),
    "src/minimal_kanban/mcp/server.py:create_mcp_server": RatchetBudget(
        "MCP registry split target", 3104, 3104, "008"
    ),
    "src/minimal_kanban/printing/defaults.py:builtin_template_records": RatchetBudget(
        "bounded data-only built-in print template factory", 1164, 1164, "001"
    ),
    "tests/test_mcp.py:McpServerBackendTests.test_mcp_tools_reach_backend": RatchetBudget(
        "MCP backend test split target", 1169, 1169, "003"
    ),
}

COMPLEXITY_RATCHETS = {
    "src/minimal_kanban/mcp/agent_gateway_v2.py:register_agent_gateway_v2._execute_workflow": RatchetBudget(
        "Gateway workflow branch complexity split target", 72, 72, "009"
    ),
    "src/minimal_kanban/services/card_service.py:CardService.update_card": RatchetBudget(
        "card update branch complexity split target", 29, 29, "012"
    ),
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
        ".coveragerc",
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
        "technical_debt_task",
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


@dataclass(frozen=True)
class RatchetMeasurement:
    metric: str
    target: str
    current: int
    baseline: int
    max_allowed: int
    delta: int
    owner_task: str
    reason: str
    present: bool


@dataclass(frozen=True)
class _AuditResult:
    issues: list[CodeHealthIssue]
    ratchets: list[RatchetMeasurement]


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
    elif normalized.startswith("tech_debt/") and normalized.endswith(".md"):
        role = "technical_debt_task"
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


def _size_exemption_registries() -> tuple[tuple[str, dict[str, RatchetBudget]], ...]:
    return (
        ("module_lines", ALLOWED_LARGE_MODULES),
        ("class_lines", ALLOWED_LARGE_CLASSES),
        ("function_lines", ALLOWED_LARGE_FUNCTIONS),
    )


def _all_ratchet_registries() -> tuple[tuple[str, dict[str, RatchetBudget]], ...]:
    return (*_size_exemption_registries(), ("branch_complexity", COMPLEXITY_RATCHETS))


def _ratchet_configuration_issues(root: Path) -> list[CodeHealthIssue]:
    issues: list[CodeHealthIssue] = []
    size_count = sum(len(registry) for _, registry in _size_exemption_registries())
    if size_count != EXPECTED_SIZE_EXEMPTION_COUNT:
        issues.append(
            CodeHealthIssue(
                "invalid_size_exemption_count",
                "scripts/code_health_audit.py",
                f"configured={size_count}; expected={EXPECTED_SIZE_EXEMPTION_COUNT}",
            )
        )

    owner_tasks: set[str] = set()
    for metric, registry in _all_ratchet_registries():
        for target, budget in sorted(registry.items()):
            if not isinstance(budget, RatchetBudget):
                issues.append(
                    CodeHealthIssue(
                        "invalid_ratchet_budget",
                        target,
                        f"{metric} entry must be RatchetBudget",
                    )
                )
                continue
            if not isinstance(budget.reason, str) or not budget.reason.strip():
                issues.append(
                    CodeHealthIssue(
                        "missing_ratchet_reason",
                        target,
                        f"{metric} entry has no reason",
                    )
                )
            if isinstance(budget.baseline, bool) or not isinstance(budget.baseline, int):
                issues.append(
                    CodeHealthIssue(
                        "invalid_ratchet_baseline",
                        target,
                        f"{metric} baseline must be an integer",
                    )
                )
            elif budget.baseline <= 0:
                issues.append(
                    CodeHealthIssue(
                        "invalid_ratchet_baseline",
                        target,
                        f"{metric} baseline must be positive",
                    )
                )
            if isinstance(budget.max_allowed, bool) or not isinstance(budget.max_allowed, int):
                issues.append(
                    CodeHealthIssue(
                        "invalid_ratchet_max",
                        target,
                        f"{metric} max_allowed must be an integer",
                    )
                )
            elif budget.max_allowed <= 0:
                issues.append(
                    CodeHealthIssue(
                        "invalid_ratchet_max",
                        target,
                        f"{metric} max_allowed must be positive",
                    )
                )
            elif (
                isinstance(budget.baseline, int)
                and not isinstance(budget.baseline, bool)
                and budget.max_allowed > budget.baseline
            ):
                issues.append(
                    CodeHealthIssue(
                        "ratchet_headroom",
                        target,
                        f"{metric} max_allowed exceeds baseline",
                    )
                )
            if not isinstance(budget.owner_task, str) or not re.fullmatch(
                r"\d{3}", budget.owner_task
            ):
                issues.append(
                    CodeHealthIssue(
                        "invalid_owner_task",
                        target,
                        f"{metric} owner_task must be a three-digit tech-debt id",
                    )
                )
            else:
                owner_tasks.add(budget.owner_task)

    tech_debt_root = root / "tech_debt"
    if tech_debt_root.is_dir():
        task_paths: dict[str, list[Path]] = {}
        for path in sorted(tech_debt_root.glob("*.md")):
            match = re.match(r"^(\d{3})-", path.name)
            if match:
                task_paths.setdefault(match.group(1), []).append(path)
        for owner_task in sorted(owner_tasks):
            matches = task_paths.get(owner_task, [])
            if not matches:
                issues.append(
                    CodeHealthIssue(
                        "missing_owner_task",
                        f"tech_debt/{owner_task}-*.md",
                        "ratchet owner task does not exist",
                    )
                )
            elif len(matches) > 1:
                relative_matches = ", ".join(_relative(path, root) for path in matches)
                issues.append(
                    CodeHealthIssue(
                        "duplicate_owner_task",
                        f"tech_debt/{owner_task}-*.md",
                        f"owner id is ambiguous: {relative_matches}",
                    )
                )
    return issues


class _QualifiedNodeVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self._scope: list[str] = []
        self.classes: list[tuple[str, ast.ClassDef]] = []
        self.functions: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified_name = ".".join((*self._scope, node.name))
        self.classes.append((qualified_name, node))
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified_name = ".".join((*self._scope, node.name))
        self.functions.append((qualified_name, node))
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


def _branch_complexity(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Return a small AST-only McCabe-style decision count.

    Boolean expressions and conditional expressions are deliberately excluded,
    matching the repository's current C901 signal for the three guarded targets.
    Nested definitions add one path and their decision nodes remain part of the
    enclosing factory's score.
    """

    score = 1
    for child in ast.walk(node):
        if child is node:
            continue
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            score += 1
        elif isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)):
            score += 1
        elif isinstance(child, ast.Match):
            score += max(len(child.cases) - 1, 0)
    return score


def _record_ratchet_value(
    values: dict[tuple[str, str], int],
    counts: dict[tuple[str, str], int],
    *,
    metric: str,
    target: str,
    current: int,
) -> None:
    key = (metric, target)
    values[key] = max(current, values.get(key, 0))
    counts[key] = counts.get(key, 0) + 1


def _ratchet_measurements(
    values: dict[tuple[str, str], int], counts: dict[tuple[str, str], int]
) -> list[RatchetMeasurement]:
    measurements: list[RatchetMeasurement] = []
    for metric, registry in _all_ratchet_registries():
        for target, budget in sorted(registry.items()):
            key = (metric, target)
            present = counts.get(key, 0) > 0
            current = values.get(key, 0)
            measurements.append(
                RatchetMeasurement(
                    metric=metric,
                    target=target,
                    current=current,
                    baseline=budget.baseline,
                    max_allowed=budget.max_allowed,
                    delta=current - budget.max_allowed,
                    owner_task=budget.owner_task,
                    reason=budget.reason,
                    present=present,
                )
            )
    return sorted(measurements, key=lambda entry: (entry.metric, entry.target))


def _run_audit(root: Path, *, include_untracked: bool) -> _AuditResult:
    issues: list[CodeHealthIssue] = []
    values: dict[tuple[str, str], int] = {}
    counts: dict[tuple[str, str], int] = {}
    issues.extend(_ratchet_configuration_issues(root))
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
        allowed_module = ALLOWED_LARGE_MODULES.get(relative_path)
        if allowed_module is not None:
            _record_ratchet_value(
                values,
                counts,
                metric="module_lines",
                target=relative_path,
                current=line_count,
            )
            if line_count > allowed_module.max_allowed:
                issues.append(
                    CodeHealthIssue(
                        "size_ratchet_exceeded",
                        relative_path,
                        "module_lines "
                        f"current={line_count}; max={allowed_module.max_allowed}; "
                        f"delta={line_count - allowed_module.max_allowed:+d}",
                    )
                )
        elif line_count > module_budget:
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

        visitor = _QualifiedNodeVisitor()
        visitor.visit(tree)
        for qualified_name, node in visitor.classes:
            target = f"{relative_path}:{qualified_name}"
            length = _node_length(node)
            allowed_class = ALLOWED_LARGE_CLASSES.get(target)
            if allowed_class is not None:
                _record_ratchet_value(
                    values,
                    counts,
                    metric="class_lines",
                    target=target,
                    current=length,
                )
                if length > allowed_class.max_allowed:
                    issues.append(
                        CodeHealthIssue(
                            "size_ratchet_exceeded",
                            relative_path,
                            "class_lines "
                            f"{qualified_name} current={length}; "
                            f"max={allowed_class.max_allowed}; "
                            f"delta={length - allowed_class.max_allowed:+d}",
                        )
                    )
            elif length > MAX_CLASS_LINES:
                issues.append(
                    CodeHealthIssue(
                        "large_class",
                        relative_path,
                        f"{qualified_name} has {length} lines; budget {MAX_CLASS_LINES}",
                    )
                )

        for qualified_name, node in visitor.functions:
            target = f"{relative_path}:{qualified_name}"
            length = _node_length(node)
            allowed_function = ALLOWED_LARGE_FUNCTIONS.get(target)
            if allowed_function is not None:
                _record_ratchet_value(
                    values,
                    counts,
                    metric="function_lines",
                    target=target,
                    current=length,
                )
                if length > allowed_function.max_allowed:
                    issues.append(
                        CodeHealthIssue(
                            "size_ratchet_exceeded",
                            relative_path,
                            "function_lines "
                            f"{qualified_name} current={length}; "
                            f"max={allowed_function.max_allowed}; "
                            f"delta={length - allowed_function.max_allowed:+d}",
                        )
                    )
            elif length > MAX_FUNCTION_LINES:
                issues.append(
                    CodeHealthIssue(
                        "large_function",
                        relative_path,
                        f"{qualified_name} has {length} lines; budget {MAX_FUNCTION_LINES}",
                    )
                )

            complexity_budget = COMPLEXITY_RATCHETS.get(target)
            if complexity_budget is not None:
                complexity = _branch_complexity(node)
                _record_ratchet_value(
                    values,
                    counts,
                    metric="branch_complexity",
                    target=target,
                    current=complexity,
                )
                if complexity > complexity_budget.max_allowed:
                    issues.append(
                        CodeHealthIssue(
                            "complexity_ratchet_exceeded",
                            relative_path,
                            "branch_complexity "
                            f"{qualified_name} current={complexity}; "
                            f"max={complexity_budget.max_allowed}; "
                            f"delta={complexity - complexity_budget.max_allowed:+d}",
                        )
                    )

    for (metric, target), count in sorted(counts.items()):
        if count > 1:
            issues.append(
                CodeHealthIssue(
                    "ambiguous_ratchet_target",
                    target,
                    f"{metric} target resolved {count} times",
                )
            )
    ratchets = _ratchet_measurements(values, counts)
    enforce_target_presence = (root / "tech_debt").is_dir()
    for measurement in ratchets:
        if enforce_target_presence and not measurement.present:
            issues.append(
                CodeHealthIssue(
                    "missing_ratchet_target",
                    measurement.target,
                    f"{measurement.metric} target is absent from the audited tree",
                )
            )
    return _AuditResult(issues, ratchets)


def audit(root: Path = ROOT, *, include_untracked: bool = False) -> list[CodeHealthIssue]:
    return _run_audit(root, include_untracked=include_untracked).issues


def ratchet_measurements(
    root: Path = ROOT, *, include_untracked: bool = False
) -> list[RatchetMeasurement]:
    return _run_audit(root, include_untracked=include_untracked).ratchets


def _budget_payload(registry: dict[str, RatchetBudget]) -> dict[str, dict[str, Any]]:
    return {target: asdict(registry[target]) for target in sorted(registry)}


def _summary(
    root: Path,
    *,
    include_untracked: bool,
    ratchets: list[RatchetMeasurement],
) -> dict[str, Any]:
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
        "size_exemptions_configured": sum(
            len(registry) for _, registry in _size_exemption_registries()
        ),
        "size_exemptions_present": sum(
            entry.present and entry.metric != "branch_complexity" for entry in ratchets
        ),
        "complexity_ratchets_configured": len(COMPLEXITY_RATCHETS),
        "complexity_ratchets_present": sum(
            entry.present and entry.metric == "branch_complexity" for entry in ratchets
        ),
        "allowed_large_modules": _budget_payload(ALLOWED_LARGE_MODULES),
        "allowed_large_classes": _budget_payload(ALLOWED_LARGE_CLASSES),
        "allowed_large_functions": _budget_payload(ALLOWED_LARGE_FUNCTIONS),
        "complexity_ratchets": _budget_payload(COMPLEXITY_RATCHETS),
    }


def build_report(root: Path = ROOT, *, include_untracked: bool = False) -> dict[str, Any]:
    result = _run_audit(root, include_untracked=include_untracked)
    return {
        "ok": not result.issues,
        "summary": _summary(
            root,
            include_untracked=include_untracked,
            ratchets=result.ratchets,
        ),
        "inventory": [
            asdict(entry)
            for entry in repository_inventory(root, include_untracked=include_untracked)
        ],
        "ratchets": [asdict(entry) for entry in result.ratchets],
        "issues": [asdict(issue) for issue in result.issues],
    }


def render_text(payload: dict[str, Any]) -> str:
    issues = payload["issues"]
    summary = payload["summary"]
    if issues:
        lines = [f"Code health audit found {len(issues)} issue(s):"]
        lines.extend(f"- {issue['code']}: {issue['path']}: {issue['detail']}" for issue in issues)
    else:
        lines = [
            "Code health audit passed: "
            f"{summary['repository_files']} repository files classified; "
            "no unapproved size/complexity-budget issues."
        ]
    lines.append(
        "Maintainability ratchets: "
        f"size={summary['size_exemptions_present']}/"
        f"{summary['size_exemptions_configured']}; "
        f"complexity={summary['complexity_ratchets_present']}/"
        f"{summary['complexity_ratchets_configured']}."
    )
    lines.extend(
        "- "
        f"{entry['metric']}: {entry['target']}: "
        f"current={entry['current']} max={entry['max_allowed']} "
        f"delta={entry['delta']:+d} owner={entry['owner_task']}"
        for entry in payload["ratchets"]
    )
    return "\n".join(lines)


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
    payload = build_report(root, include_untracked=args.include_untracked)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(render_text(payload))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
