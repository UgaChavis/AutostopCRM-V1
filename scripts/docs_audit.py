from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

CRM_CANONICAL_DOCS = (
    "API_GUIDE.md",
    "AUTOSTOPCRM_FULL_INSTRUCTION.txt",
    "CHATGPT_CONNECTOR_SETUP.md",
    "MCP_GUIDE.md",
    "README.md",
    "docs/OPERATIONS_RUNBOOK.md",
)

MANAGER_CANONICAL_DOCS = (
    "README.md",
    "docs/agent/autostop_manager_skill.md",
    "docs/agent/command_routes.json",
    "docs/agent/crm_mcp_catalog.json",
    "docs/agent/knowledge_base_index.md",
    "docs/agent/knowledge_map.json",
    "docs/agent/knowledge_shelves.md",
    "docs/agent/manager_mcp_catalog.json",
)

RETIRED_DOC_GLOBS = (
    "AI_REMODEL_*",
    "GPT_AGENT_*",
    "mcp-tools-example.json",
    "openai-tools-example.json",
    "main_agent.py",
)

SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}

FORBIDDEN_TEXT_PATTERNS = (
    (
        "missing_doc_reference",
        re.compile(r"\bMASTER-PLAN\.md\b"),
        "reference to removed MASTER-PLAN.md",
    ),
    (
        "missing_doc_reference",
        re.compile(r"\bdocs/PROJECT_MEMORY\.md\b|\bdocs\\PROJECT_MEMORY\.md\b"),
        "reference to removed docs/PROJECT_MEMORY.md",
    ),
    (
        "stale_workspace_path",
        re.compile(r"C:\\Users\\User\\Desktop\\AutostopCRM-V1"),
        "old local checkout path",
    ),
    (
        "stale_sandbox_path",
        re.compile(r"C:\\Users\\User\\Desktop\\AutostopCRM-data-snapshots"),
        "old user-specific production-data sandbox path",
    ),
    (
        "stale_ssh_identity",
        re.compile(r"\bcodex_autostopcrm\b"),
        "old SSH identity name; use autostopcrm_server_ed25519 from the local key bundle",
    ),
    (
        "stale_mcp_count",
        re.compile(r"optional_autostop_manager_tools[\"`\s:=\-]+19\b"),
        "old optional AutostopManager MCP tool count",
    ),
    (
        "stale_mcp_count",
        re.compile(r"production_tools_with_manager_mounted[\"`\s:=\-]+90\b"),
        "old mounted MCP tool count",
    ),
)

API_GUIDE_REQUIRED_ROUTE_TEXT = (
    (
        "/api/finance_audit",
        "read-only finance audit API route is not documented",
    ),
    (
        "/api/finance_audit/apply_safe_fixes",
        "finance audit maintenance route is not documented",
    ),
    (
        "/api/correct_repair_order_number",
        "repair-order number maintenance route is not documented",
    ),
)

API_GUIDE_REQUIRED_TEXT = (
    (
        "include_full_details",
        "card log archive hydration option is not documented",
    ),
    (
        "transaction_offset",
        "cashbox transaction pagination offset is not documented",
    ),
)

RUNBOOK_REQUIRED_TEXT = (
    (
        "state_size_report.py",
        "state size diagnostics script is not documented",
    ),
    (
        "compact_audit_events.py",
        "audit compaction maintenance script is not documented",
    ),
    (
        "audit-archive",
        "audit archive data directory is not documented",
    ),
    (
        "autostopcrm_server_ed25519",
        "canonical production SSH identity is not documented",
    ),
    (
        "IdentitiesOnly=yes",
        "production SSH command does not force the documented identity",
    ),
)


@dataclass(frozen=True)
class Issue:
    code: str
    path: str
    detail: str


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def scan_forbidden_text(path: Path, text: str, *, root: Path = ROOT) -> list[Issue]:
    issues: list[Issue] = []
    for code, pattern, detail in FORBIDDEN_TEXT_PATTERNS:
        if pattern.search(text):
            issues.append(Issue(code, _display_path(path, root), detail))
    return issues


def _contains_route_text(text: str, route: str) -> bool:
    return (
        re.search(
            rf"(?<![A-Za-z0-9_/\-]){re.escape(route)}(?![A-Za-z0-9_/\-])",
            text,
        )
        is not None
    )


def _check_api_guide_required_routes(root: Path) -> list[Issue]:
    path = root / "API_GUIDE.md"
    if not path.exists():
        return []

    text = _read_text(path)
    issues: list[Issue] = []
    for route, detail in API_GUIDE_REQUIRED_ROUTE_TEXT:
        if not _contains_route_text(text, route):
            issues.append(
                Issue(
                    "api_guide_missing_route",
                    _display_path(path, root),
                    f"{detail}: {route}",
                )
            )
    for required_text, detail in API_GUIDE_REQUIRED_TEXT:
        if required_text not in text:
            issues.append(
                Issue(
                    "api_guide_missing_contract",
                    _display_path(path, root),
                    f"{detail}: {required_text}",
                )
            )
    runbook = root / "docs" / "OPERATIONS_RUNBOOK.md"
    if runbook.exists():
        runbook_text = _read_text(runbook)
        for required_text, detail in RUNBOOK_REQUIRED_TEXT:
            if required_text not in runbook_text:
                issues.append(
                    Issue(
                        "runbook_missing_maintenance_contract",
                        _display_path(runbook, root),
                        f"{detail}: {required_text}",
                    )
                )
    return issues


def _iter_retired_candidates(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for pattern in RETIRED_DOC_GLOBS:
        candidates.extend(
            path
            for path in root.rglob(pattern)
            if path.is_file() and not any(part in SKIP_DIRS for part in path.parts)
        )
    return sorted(candidates)


def _iter_user_skill_docs() -> list[Path]:
    skills_root = Path.home() / ".codex" / "skills"
    if not skills_root.exists():
        return []

    docs: list[Path] = []
    for path in skills_root.rglob("*.md"):
        try:
            relative_parts = path.relative_to(skills_root).parts
        except ValueError:
            continue
        if not relative_parts or relative_parts[0] == ".system":
            continue
        docs.append(path)
    return sorted(docs)


def _literal_assignment(tree: ast.AST, name: str) -> Any:
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name and node.value is not None:
                return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise ValueError(f"{name} assignment not found")


def load_crm_registry_tools(root: Path) -> set[str]:
    registry_path = root / "src" / "minimal_kanban" / "mcp" / "tool_registry.py"
    tree = ast.parse(_read_text(registry_path), filename=str(registry_path))
    groups = _literal_assignment(tree, "MCP_TOOL_GROUPS")
    return {str(tool_name) for tools in groups.values() for tool_name in tools}


def extract_decorated_tool_names(path: Path) -> set[str]:
    tree = ast.parse(_read_text(path), filename=str(path))
    tool_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute) or func.attr != "tool":
                continue
            for keyword in decorator.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    if isinstance(keyword.value.value, str):
                        tool_names.add(keyword.value.value)
    return tool_names


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _check_required(paths: tuple[str, ...], root: Path, label: str) -> list[Issue]:
    issues: list[Issue] = []
    for relative_path in paths:
        path = root / relative_path
        if not path.exists():
            issues.append(
                Issue(
                    "missing_canonical_doc",
                    _display_path(path, root),
                    f"missing canonical {label} doc",
                )
            )
    return issues


def _check_crm_mcp_surface(root: Path) -> list[Issue]:
    issues: list[Issue] = []
    registry_tools = load_crm_registry_tools(root)
    server_tools = extract_decorated_tool_names(
        root / "src" / "minimal_kanban" / "mcp" / "server.py"
    )

    if len(registry_tools) != 71:
        issues.append(
            Issue(
                "crm_mcp_count_mismatch",
                "src/minimal_kanban/mcp/tool_registry.py",
                f"registry has {len(registry_tools)} tools, expected 71",
            )
        )
    if registry_tools != server_tools:
        missing = sorted(registry_tools - server_tools)
        unexpected = sorted(server_tools - registry_tools)
        issues.append(
            Issue(
                "crm_mcp_registry_mismatch",
                "src/minimal_kanban/mcp/server.py",
                f"missing={missing}; unexpected={unexpected}",
            )
        )
    return issues


def _default_manager_root(root: Path) -> Path | None:
    candidate = root.parent.parent / "AutostopManager"
    return candidate if candidate.exists() else None


def _check_manager_docs_and_catalogs(
    root: Path,
    manager_root: Path,
    crm_tools: set[str],
) -> list[Issue]:
    issues: list[Issue] = _check_required(
        MANAGER_CANONICAL_DOCS,
        manager_root,
        "AutostopManager",
    )

    manager_tools_path = manager_root / "autostop_manager" / "mcp_tools.py"
    manager_catalog_path = manager_root / "docs" / "agent" / "manager_mcp_catalog.json"
    crm_catalog_path = manager_root / "docs" / "agent" / "crm_mcp_catalog.json"

    if not manager_tools_path.exists():
        return issues + [
            Issue("missing_manager_source", str(manager_tools_path), "manager MCP source missing")
        ]

    manager_tools = extract_decorated_tool_names(manager_tools_path)

    if manager_catalog_path.exists():
        manager_catalog = _load_json(manager_catalog_path)
        catalog_tools = set(manager_catalog.get("all_tools", []))
        catalog_count = manager_catalog.get("tool_count")
        if catalog_count != len(manager_tools):
            issues.append(
                Issue(
                    "manager_catalog_count_mismatch",
                    str(manager_catalog_path),
                    f"catalog has {catalog_count}, source has {len(manager_tools)}",
                )
            )
        if catalog_tools != manager_tools:
            issues.append(
                Issue(
                    "manager_catalog_tools_mismatch",
                    str(manager_catalog_path),
                    _tool_set_delta(catalog_tools, manager_tools),
                )
            )

    if crm_catalog_path.exists():
        crm_catalog = _load_json(crm_catalog_path)
        tool_counts = crm_catalog.get("tool_counts", {})
        expected_live_tools = crm_tools | manager_tools
        expected_counts = {
            "crm_base_tools": len(crm_tools),
            "optional_autostop_manager_tools": len(manager_tools),
            "production_tools_with_manager_mounted": len(expected_live_tools),
        }
        if tool_counts != expected_counts:
            issues.append(
                Issue(
                    "crm_catalog_count_mismatch",
                    str(crm_catalog_path),
                    f"catalog has {tool_counts}, expected {expected_counts}",
                )
            )

        live_tools = set(crm_catalog.get("live_tools_verified", []))
        if live_tools != expected_live_tools:
            issues.append(
                Issue(
                    "crm_catalog_live_tools_mismatch",
                    str(crm_catalog_path),
                    _tool_set_delta(live_tools, expected_live_tools),
                )
            )

        optional_tools = set(
            crm_catalog.get("tool_families", {}).get(
                "optional_manager_memory_and_routing",
                [],
            )
        )
        if optional_tools != manager_tools:
            issues.append(
                Issue(
                    "crm_catalog_optional_tools_mismatch",
                    str(crm_catalog_path),
                    _tool_set_delta(optional_tools, manager_tools),
                )
            )

    for relative_path in MANAGER_CANONICAL_DOCS:
        path = manager_root / relative_path
        if path.exists():
            issues.extend(scan_forbidden_text(path, _read_text(path), root=manager_root))

    return issues


def _tool_set_delta(actual: set[str], expected: set[str]) -> str:
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    return f"missing={missing}; unexpected={unexpected}"


def audit(
    root: Path = ROOT,
    *,
    manager_root: Path | None = None,
    include_skills: bool = True,
) -> list[Issue]:
    root = root.resolve()
    issues: list[Issue] = []

    issues.extend(_check_required(CRM_CANONICAL_DOCS, root, "CRM"))
    for relative_path in CRM_CANONICAL_DOCS:
        path = root / relative_path
        if path.exists():
            issues.extend(scan_forbidden_text(path, _read_text(path), root=root))

    issues.extend(_check_api_guide_required_routes(root))

    for retired_path in _iter_retired_candidates(root):
        issues.append(
            Issue(
                "retired_doc_candidate_present",
                _display_path(retired_path, root),
                "retired cleanup or agent artifact is still present",
            )
        )

    try:
        crm_tools = load_crm_registry_tools(root)
        issues.extend(_check_crm_mcp_surface(root))
    except (OSError, SyntaxError, ValueError) as exc:
        crm_tools = set()
        issues.append(Issue("crm_mcp_audit_error", str(root), str(exc)))

    resolved_manager_root = manager_root or _default_manager_root(root)
    if resolved_manager_root is not None:
        if resolved_manager_root.exists():
            issues.extend(
                _check_manager_docs_and_catalogs(
                    root,
                    resolved_manager_root.resolve(),
                    crm_tools,
                )
            )
        else:
            issues.append(
                Issue(
                    "missing_manager_root",
                    str(resolved_manager_root),
                    "explicit AutostopManager root does not exist",
                )
            )

    if include_skills:
        for path in _iter_user_skill_docs():
            issues.extend(scan_forbidden_text(path, _read_text(path), root=root))

    return issues


def _print_text(issues: list[Issue]) -> None:
    if not issues:
        print("Docs audit passed: no issues found.")
        return
    print(f"Docs audit found {len(issues)} issue(s):")
    for issue in issues:
        print(f"- {issue.code}: {issue.path}: {issue.detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only documentation and MCP catalog drift audit."
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--manager-root", type=Path, default=None)
    args = parser.parse_args(argv)

    issues = audit(ROOT, manager_root=args.manager_root)
    if args.format == "json":
        print(json.dumps([asdict(issue) for issue in issues], ensure_ascii=False, indent=2))
    else:
        _print_text(issues)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
