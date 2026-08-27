from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.json_safety import reject_deeply_nested_json  # noqa: E402

GIT_COMMAND_TIMEOUT_SECONDS = 15
DOCS_AUDIT_TEXT_MAX_BYTES = 2 * 1024 * 1024

CRM_CANONICAL_DOCS = (
    "AGENTS.md",
    "API_GUIDE.md",
    "AUTOSTOPCRM_FULL_INSTRUCTION.txt",
    "CHATGPT_CONNECTOR_SETUP.md",
    "MCP_GUIDE.md",
    "README.md",
    "docs/OPERATIONS_RUNBOOK.md",
)

CRM_DOCUMENTATION_MANIFESTS = (
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-runtime.txt",
)

CRM_MCP_RAW_TOOL_SOURCE_PATHS = (
    "src/minimal_kanban/mcp/server.py",
    "src/minimal_kanban/mcp/connector_diagnostics.py",
    "src/minimal_kanban/mcp/board_reads.py",
    "src/minimal_kanban/mcp/board_column_writes.py",
    "src/minimal_kanban/mcp/board_sticky_writes.py",
    "src/minimal_kanban/mcp/board_card_timer_writes.py",
)

DOCUMENTATION_SUFFIXES = (".md", ".txt", ".rst", ".adoc")

ACTIVE_DOC_GLOBS = ("tech_debt/*.md",)

MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\((?P<target><[^>]+>|[^)\s]+)")

SCRIPT_INSTRUCTION_SUFFIXES = (
    ".ps1",
    ".sh",
    ".py",
    ".conf.example",
)

SCRIPT_INSTRUCTION_SKIP_FILES = {
    "scripts/docs_audit.py",
}

SCRIPT_INSTRUCTION_FILES = {
    ".dockerignore",
    ".github/workflows/quality.yml",
    ".pre-commit-config.yaml",
    "Dockerfile",
    "docker-compose.yml",
}

MANAGER_CANONICAL_DOCS = (
    "AGENTS.md",
    "README.md",
    "docs/agent/board_cleanup_autopilot_playbook.md",
    "docs/agent/command_routes.json",
    "docs/agent/crm_manager_data_playbook.md",
    "docs/agent/crm_mcp_catalog.json",
    "docs/agent/deployment_runbook.md",
    "docs/agent/knowledge_map.json",
    "docs/agent/knowledge_shelves.md",
    "docs/agent/manager_mcp_catalog.json",
    "docs/agent/manager_rules.json",
    "docs/agent/service_management_sources.json",
)

MANAGER_GATEWAY_INSTRUCTION_DOCS = (
    "AGENTS.md",
    "README.md",
    "docs/agent/board_cleanup_autopilot_playbook.md",
    "docs/agent/command_routes.json",
    "docs/agent/crm_manager_data_playbook.md",
    "docs/agent/deployment_runbook.md",
    "docs/agent/manager_rules.json",
    "docs/agent/service_management_sources.json",
)

MANAGER_GATEWAY_FORBIDDEN_TEXT_PATTERNS = (
    (
        "retired_manager_lifecycle_tool",
        re.compile(
            r"\b(?:start_manager_run|record_manager_run_event|finish_manager_run|list_manager_runs)\b"
        ),
        "retired v1 manager lifecycle tool; use Gateway v2 workflow tools",
    ),
    (
        "retired_manager_lifecycle_cli",
        re.compile(r"\b(?:run-start|run-event|run-finish|run-list)\b"),
        "retired v1 manager lifecycle CLI command",
    ),
    (
        "direct_legacy_crm_instruction",
        re.compile(r"\b(?:bootstrap_context|get_card_context)\b"),
        "direct legacy CRM instruction; use Gateway v2 focused tools",
    ),
)

RETIRED_DOC_GLOBS = (
    "AI_REMODEL_*",
    "GPT_AGENT_*",
    "docs/superpowers/plans/*.md",
    "docs/superpowers/specs/*.md",
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
    "release",
    "release.staging",
}

SECRET_BUNDLE_DOC_SUFFIXES = {".md", ".txt"}

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
        "missing_doc_reference",
        re.compile(r"\bdocs[/\\]SERVER_MAP\.md\b"),
        "reference to removed docs/SERVER_MAP.md",
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
        "stale_deploy_env",
        re.compile(r"\bAUTOSTOP_GIT_BRANCH\b"),
        "old deploy env var; the target branch is fixed by CRM_DEPLOY_BRANCH in deploy.sh",
    ),
    (
        "stale_deploy_env",
        re.compile(r"\bAUTOSTOP_VERIFY_PUBLIC_HTTPS\b"),
        "removed deploy env var; public HTTPS smoke is mandatory",
    ),
    (
        "stale_repair_order_correction_contract",
        re.compile(
            r"(?:maintenance-only\s*:\s*`?/api/correct_repair_order_number|"
            r"repair-order number corrections?\s+(?:are|is)\s+maintenance(?:-only)?|"
            r"repair-order number correction\s+is\s+a\s+maintenance\s+flow)",
            re.IGNORECASE,
        ),
        "repair-order number correction is blocked, not a maintenance writer",
    ),
    (
        "stale_smoke_credentials",
        re.compile(r"--operator-username\s+admin\s+--operator-password\s+admin"),
        "default admin smoke credentials are documented; use smoke env variables",
    ),
    (
        "stale_smoke_credentials",
        re.compile(r"MINIMAL_KANBAN_DEFAULT_ADMIN_(?:USERNAME|PASSWORD):-admin"),
        "deploy smoke falls back to default admin credentials; require AUTOSTOP_SMOKE_OPERATOR_*",
    ),
    (
        "stale_smoke_credentials",
        re.compile(r"--operator-(?:username|password)\"?,\s*default=\"admin"),
        "operator smoke CLI flag defaults to admin; use smoke env variables or explicit options",
    ),
    (
        "stale_public_http",
        re.compile(r"--site-url\s+http://crm\.autostopcrm\.ru\b"),
        "public CRM smoke must use https://crm.autostopcrm.ru with --expect-https",
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

CRM_ONLY_FORBIDDEN_TEXT_PATTERNS = (
    (
        "stale_workspace_path",
        re.compile(
            r"C:[\\/]+Users[\\/]+User[\\/]+(?:Мой диск|Desktop)[\\/]+Obsidian CRM[\\/]+AutostopCRM"
        ),
        "old user-specific manager knowledge vault path in CRM docs",
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
        "/api/repair_order_number_audit",
        "read-only repair-order number audit API route is not documented",
    ),
    (
        "/api/correct_repair_order_number",
        "blocked repair-order number compatibility route is not documented",
    ),
    (
        "/api/create_employee_shift_accrual",
        "manual employee shift accrual route is not documented",
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
    (
        "repair_order_number_immutable",
        "immutable repair-order number rejection is not documented",
    ),
)

MCP_GUIDE_REQUIRED_TEXT = (
    (
        "MINIMAL_KANBAN_MCP_ALLOWED_HOSTS",
        "MCP allowed-host transport security override is not documented",
    ),
    (
        "MINIMAL_KANBAN_MCP_ALLOWED_ORIGINS",
        "MCP allowed-origin transport security override is not documented",
    ),
    (
        "owner-approved OAuth 2.1",
        "production ChatGPT/Codex OAuth setup flow is not documented in MCP guide",
    ),
    (
        "https://crm.autostopcrm.ru/mcp",
        "production MCP connector URL is not documented",
    ),
    (
        "Public anonymous writes must remain blocked",
        "MCP security rule for public anonymous writes is not documented",
    ),
    (
        "exactly 24 tools",
        "exact Gateway v2 production tool count is not documented",
    ),
    (
        "--exhaustive",
        "safe exhaustive Gateway v2 release check is not documented",
    ),
    (
        "OAuth 2.1",
        "ChatGPT authenticated-client compatibility is not documented",
    ),
)

CHATGPT_CONNECTOR_REQUIRED_TEXT = (
    (
        "https://crm.autostopcrm.ru/mcp",
        "production ChatGPT connector URL is not documented",
    ),
    (
        "agent_bootstrap",
        "ChatGPT connector bootstrap call is not documented",
    ),
    (
        "get_runtime_status",
        "ChatGPT connector runtime diagnostic call is not documented",
    ),
    (
        "Public anonymous writes must remain blocked",
        "ChatGPT connector write-safety rule is not documented",
    ),
    (
        "owner-approved OAuth 2.1",
        "current direct ChatGPT/Codex OAuth flow is not documented",
    ),
    (
        "authorization",
        "Responses API MCP authorization field is not documented",
    ),
)

RUNBOOK_REQUIRED_TEXT = (
    (
        "bootstrap_tools.ps1",
        "toolchain bootstrap script is not documented",
    ),
    (
        "toolchain_doctor.ps1",
        "toolchain audit script is not documented",
    ),
    (
        "coverage_audit.py",
        "coverage ratchet command is not documented",
    ),
    (
        "--profile core",
        "mandatory core browser-smoke profile is not documented",
    ),
    (
        "--profile full",
        "release browser-smoke profile is not documented",
    ),
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
    (
        "CRM_DEPLOY_BRANCH",
        "fixed CRM deploy branch is not documented",
    ),
    (
        "AUTOSTOP_DEPLOY_LOCK_PATH",
        "deploy/watchdog lock path env var is not documented",
    ),
    (
        "AUTOSTOP_SMOKE_ATTEMPTS",
        "deploy smoke retry count env var is not documented",
    ),
    (
        "AUTOSTOP_SMOKE_DELAY_SECONDS",
        "deploy smoke retry delay env var is not documented",
    ),
    (
        "validate_production_env.py",
        "production environment validator is not documented",
    ),
    (
        "check_agent_gateway_v2.py",
        "Gateway v2 release verifier is not documented",
    ),
    (
        "repair_order_number_immutable",
        "blocked repair-order correction contract is not documented",
    ),
    (
        "build_app.ps1",
        "desktop build entrypoint is not documented",
    ),
    (
        "prepare_release.ps1",
        "portable release assembly is not documented",
    ),
    (
        "run_quality_pass.ps1",
        "complete desktop release gate is not documented",
    ),
    (
        "post_build_verification.py",
        "portable executable verification is not documented",
    ),
    (
        "payroll_audit_report.py",
        "payroll audit tool is not documented",
    ),
    (
        "client_data_quality_maintenance.py",
        "client data-quality maintenance tool is not documented",
    ),
    (
        "client_duplicates_maintenance.py",
        "client duplicate maintenance tool is not documented",
    ),
)

QUALITY_WORKFLOW_REQUIRED_TEXT = (
    (
        "python scripts/docs_audit.py --format text",
        "GitHub quality workflow does not run docs audit",
    ),
    (
        "coverage run -m unittest discover -s tests -v",
        "GitHub quality workflow does not collect full-suite branch coverage",
    ),
    (
        "python scripts/coverage_audit.py --format text",
        "GitHub quality workflow does not enforce the coverage ratchet",
    ),
    (
        "python scripts/browser_smoke.py --profile core --attempts 1",
        "GitHub quality workflow does not run the mandatory core browser smoke",
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
    with path.open("rb") as handle:
        raw = handle.read(DOCS_AUDIT_TEXT_MAX_BYTES + 1)
    if len(raw) > DOCS_AUDIT_TEXT_MAX_BYTES:
        raise ValueError(f"docs audit file is too large: {path}")
    return raw.decode("utf-8", errors="replace")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _iter_git_tracked_files(root: Path) -> list[Path]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    return [root / line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _is_skipped_path(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def _matches_relative_glob(path: Path, root: Path, patterns: tuple[str, ...]) -> bool:
    relative_path = _display_path(path, root)
    return any(fnmatch.fnmatch(relative_path, pattern) for pattern in patterns)


def _check_unclassified_tracked_docs(root: Path) -> list[Issue]:
    allowed = set(CRM_CANONICAL_DOCS) | set(CRM_DOCUMENTATION_MANIFESTS)
    issues: list[Issue] = []

    for path in _iter_git_tracked_files(root):
        if not path.exists() or _is_skipped_path(path):
            continue
        relative_path = _display_path(path, root)
        if path.suffix.lower() not in DOCUMENTATION_SUFFIXES:
            continue
        if relative_path in allowed:
            continue
        if _matches_relative_glob(path, root, ACTIVE_DOC_GLOBS):
            continue
        if _matches_relative_glob(path, root, RETIRED_DOC_GLOBS):
            continue
        issues.append(
            Issue(
                "unclassified_tracked_doc",
                relative_path,
                "tracked documentation file is not classified in docs_audit",
            )
        )
    return issues


def _check_dockerignore_keeps_canonical_markdown(root: Path) -> list[Issue]:
    path = root / ".dockerignore"
    if not path.exists():
        return []
    rules = {
        line.strip()
        for line in _read_text(path).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if "*.md" not in rules:
        return []
    issues: list[Issue] = []
    for relative_path in CRM_CANONICAL_DOCS:
        if not relative_path.endswith(".md"):
            continue
        keep_rule = f"!{relative_path}"
        if keep_rule not in rules:
            issues.append(
                Issue(
                    "dockerignore_missing_canonical_doc",
                    _display_path(path, root),
                    f"Docker image excludes canonical documentation: {keep_rule}",
                )
            )
    return issues


def _check_canonical_local_links(root: Path) -> list[Issue]:
    root = root.resolve()
    issues: list[Issue] = []
    relative_paths = list(CRM_CANONICAL_DOCS)
    for pattern in ACTIVE_DOC_GLOBS:
        relative_paths.extend(
            _display_path(path, root) for path in sorted(root.glob(pattern)) if path.is_file()
        )
    for relative_path in dict.fromkeys(relative_paths):
        path = root / relative_path
        if path.suffix.lower() != ".md" or not path.exists():
            continue
        for match in MARKDOWN_LINK_PATTERN.finditer(_read_text(path)):
            target = match.group("target").strip().strip("<>")
            if (
                not target
                or target.startswith(("#", "//"))
                or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target)
            ):
                continue
            target_path = target.split("#", 1)[0].split("?", 1)[0]
            if not target_path:
                continue
            resolved = (path.parent / target_path).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                issues.append(
                    Issue(
                        "canonical_doc_link_outside_root",
                        _display_path(path, root),
                        f"local documentation link leaves repository: {target}",
                    )
                )
                continue
            if not resolved.exists():
                issues.append(
                    Issue(
                        "canonical_doc_link_missing",
                        _display_path(path, root),
                        f"local documentation link target is missing: {target}",
                    )
                )
    return issues


def _iter_script_instruction_files(root: Path) -> list[Path]:
    tracked_files = _iter_git_tracked_files(root)
    if not tracked_files:
        candidates = [root / "deploy.sh"]
        scripts_dir = root / "scripts"
        if scripts_dir.exists():
            candidates.extend(scripts_dir.rglob("*"))
        tracked_files = candidates

    instruction_files: list[Path] = []
    for path in tracked_files:
        if not path.is_file() or _is_skipped_path(path):
            continue
        relative_path = _display_path(path, root)
        if relative_path in SCRIPT_INSTRUCTION_SKIP_FILES:
            continue
        if (
            relative_path in SCRIPT_INSTRUCTION_FILES
            or relative_path == "deploy.sh"
            or (
                relative_path.startswith("scripts/")
                and any(relative_path.endswith(suffix) for suffix in SCRIPT_INSTRUCTION_SUFFIXES)
            )
        ):
            instruction_files.append(path)
    return sorted(instruction_files)


def _check_script_instruction_text(root: Path) -> list[Issue]:
    issues: list[Issue] = []
    for path in _iter_script_instruction_files(root):
        text = _read_text(path)
        issues.extend(scan_forbidden_text(path, text, root=root))
        issues.extend(scan_crm_only_forbidden_text(path, text, root=root))
    return issues


def scan_forbidden_text(path: Path, text: str, *, root: Path = ROOT) -> list[Issue]:
    issues: list[Issue] = []
    for code, pattern, detail in FORBIDDEN_TEXT_PATTERNS:
        if pattern.search(text):
            issues.append(Issue(code, _display_path(path, root), detail))
    return issues


def scan_crm_only_forbidden_text(path: Path, text: str, *, root: Path = ROOT) -> list[Issue]:
    issues: list[Issue] = []
    for code, pattern, detail in CRM_ONLY_FORBIDDEN_TEXT_PATTERNS:
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


def _missing_route_issues(
    path: Path,
    required_routes: tuple[tuple[str, str], ...],
    *,
    issue_code: str,
    root: Path,
) -> list[Issue]:
    if not path.exists():
        return []
    text = _read_text(path)
    issues: list[Issue] = []
    for route, detail in required_routes:
        if not _contains_route_text(text, route):
            issues.append(
                Issue(
                    issue_code,
                    _display_path(path, root),
                    f"{detail}: {route}",
                )
            )
    return issues


def _missing_text_issues(
    path: Path,
    required_texts: tuple[tuple[str, str], ...],
    *,
    issue_code: str,
    root: Path,
) -> list[Issue]:
    if not path.exists():
        return []
    text = _read_text(path)
    issues: list[Issue] = []
    for required_text, detail in required_texts:
        if required_text not in text:
            issues.append(
                Issue(
                    issue_code,
                    _display_path(path, root),
                    f"{detail}: {required_text}",
                )
            )
    return issues


def _scan_existing_canonical_docs_for_forbidden_text(root: Path) -> list[Issue]:
    issues: list[Issue] = []
    for relative_path in CRM_CANONICAL_DOCS:
        path = root / relative_path
        if not path.exists():
            continue
        text = _read_text(path)
        issues.extend(scan_forbidden_text(path, text, root=root))
        issues.extend(scan_crm_only_forbidden_text(path, text, root=root))
    return issues


def _scan_retired_candidate_issues(root: Path) -> list[Issue]:
    return [
        Issue(
            "retired_doc_candidate_present",
            _display_path(retired_path, root),
            "retired cleanup or agent artifact is still present",
        )
        for retired_path in _iter_retired_candidates(root)
    ]


def _scan_user_skill_doc_issues(root: Path) -> list[Issue]:
    issues: list[Issue] = []
    for path in _iter_user_skill_docs():
        issues.extend(scan_forbidden_text(path, _read_text(path), root=root))
    return issues


def _scan_secret_bundle_issues(secret_bundle: Path, *, root: Path) -> list[Issue]:
    issues: list[Issue] = []
    for path in _iter_secret_bundle_docs(secret_bundle):
        issues.extend(scan_forbidden_text(path, _read_text(path), root=root))
    return issues


def _tool_manifest_issues(
    path: Path,
    expected_tools: set[str],
    expected_fingerprint: str,
    *,
    prefix: str,
) -> list[Issue]:
    if not path.exists():
        return []
    manifest = _load_json(path)
    names = manifest.get("expected_tool_names")
    catalog_names = (
        names if isinstance(names, list) and all(isinstance(x, str) for x in names) else []
    )
    expected_names = sorted(expected_tools)
    issues: list[Issue] = []
    checks = (
        ("format", manifest.get("format"), "mcp_surface_manifest_v1"),
        ("count", manifest.get("expected_tool_count"), len(expected_names)),
        ("tools", catalog_names, expected_names),
        ("fingerprint", manifest.get("schema_fingerprint"), expected_fingerprint),
    )
    for suffix, actual, expected in checks:
        if actual != expected:
            detail = (
                _tool_set_delta(set(catalog_names), expected_tools)
                if suffix == "tools"
                else f"catalog has {actual!r}, expected {expected!r}"
            )
            issues.append(Issue(f"{prefix}_catalog_{suffix}_mismatch", str(path), detail))
    if not isinstance(manifest.get("source"), str) or not manifest["source"].strip():
        issues.append(Issue(f"{prefix}_catalog_source_missing", str(path), "source is required"))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(manifest.get("verified_at", ""))):
        issues.append(
            Issue(
                f"{prefix}_catalog_verified_at_invalid", str(path), "verified_at must be YYYY-MM-DD"
            )
        )
    return issues


def _manager_catalog_issues(
    manager_catalog_path: Path,
    manager_tools: set[str],
    gateway_tools: set[str],
    fingerprints: dict[str, str],
) -> list[Issue]:
    return _tool_manifest_issues(
        manager_catalog_path,
        manager_tools,
        fingerprints["manager"],
        prefix="manager",
    ) + _tool_manifest_issues(
        manager_catalog_path.parent / "crm_mcp_catalog.json",
        gateway_tools,
        fingerprints["crm"],
        prefix="crm",
    )


def _registered_surface_fingerprints(root: Path, manager_root: Path) -> dict[str, str]:
    probe = r"""
import hashlib, json, logging, sys
crm_root, manager_root = sys.argv[1:3]
sys.path[:0] = [f"{crm_root}/src", manager_root]
from autostop_manager.mcp_server import build_server
from minimal_kanban.mcp.client import BoardApiClient
from minimal_kanban.mcp.server import create_mcp_server
def digest(server):
    tools = sorted(server._tool_manager.list_tools(), key=lambda item: item.name)
    surface = [{"name": item.name, "inputSchema": item.parameters} for item in tools]
    value = json.dumps(surface, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()
manager = build_server()
crm = create_mcp_server(
    BoardApiClient("http://127.0.0.1:9"), logging.getLogger("schema-probe"),
    host="127.0.0.1", port=41831, path="/mcp", bearer_token="schema-probe",
    public_endpoint_url="https://crm.example/mcp",
)
print(json.dumps({"manager": digest(manager), "crm": digest(crm)}))
"""
    environment = os.environ.copy()
    environment.update(
        {
            "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
            "AUTOSTOP_MCP_OAUTH_ENABLED": "0",
            "MINIMAL_KANBAN_MCP_BEARER_TOKEN": "schema-probe",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe, str(root), str(manager_root)],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=GIT_COMMAND_TIMEOUT_SECONDS,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict) or any(
        re.fullmatch(r"[0-9a-f]{64}", str(payload.get(key) or "")) is None
        for key in ("manager", "crm")
    ):
        raise ValueError("registered MCP schema fingerprint probe returned invalid data")
    return {key: str(payload[key]) for key in ("manager", "crm")}


def _manager_gateway_instruction_issues(manager_root: Path) -> list[Issue]:
    issues: list[Issue] = []
    for relative_path in MANAGER_GATEWAY_INSTRUCTION_DOCS:
        path = manager_root / relative_path
        if not path.exists():
            continue
        text = _read_text(path)
        for code, pattern, detail in MANAGER_GATEWAY_FORBIDDEN_TEXT_PATTERNS:
            if pattern.search(text):
                issues.append(Issue(code, _display_path(path, manager_root), detail))
    return issues


def _manager_doc_forbidden_issues(manager_root: Path) -> list[Issue]:
    issues: list[Issue] = []
    for relative_path in MANAGER_CANONICAL_DOCS:
        path = manager_root / relative_path
        if path.exists():
            issues.extend(scan_forbidden_text(path, _read_text(path), root=manager_root))
    return issues


def _check_api_guide_required_routes(root: Path) -> list[Issue]:
    return [
        *_missing_route_issues(
            root / "API_GUIDE.md",
            API_GUIDE_REQUIRED_ROUTE_TEXT,
            issue_code="api_guide_missing_route",
            root=root,
        ),
        *_missing_text_issues(
            root / "API_GUIDE.md",
            API_GUIDE_REQUIRED_TEXT,
            issue_code="api_guide_missing_contract",
            root=root,
        ),
        *_missing_text_issues(
            root / "MCP_GUIDE.md",
            MCP_GUIDE_REQUIRED_TEXT,
            issue_code="mcp_guide_missing_contract",
            root=root,
        ),
        *_missing_text_issues(
            root / "CHATGPT_CONNECTOR_SETUP.md",
            CHATGPT_CONNECTOR_REQUIRED_TEXT,
            issue_code="chatgpt_connector_setup_missing_contract",
            root=root,
        ),
        *_missing_text_issues(
            root / "docs" / "OPERATIONS_RUNBOOK.md",
            RUNBOOK_REQUIRED_TEXT,
            issue_code="runbook_missing_maintenance_contract",
            root=root,
        ),
    ]


def _check_quality_workflow_required_gates(root: Path) -> list[Issue]:
    path = root / ".github" / "workflows" / "quality.yml"
    if not path.exists():
        return [
            Issue(
                "missing_quality_workflow",
                _display_path(path, root),
                "GitHub quality workflow is missing",
            )
        ]

    text = _read_text(path)
    issues: list[Issue] = []
    for required_text, detail in QUALITY_WORKFLOW_REQUIRED_TEXT:
        if required_text not in text:
            issues.append(
                Issue(
                    "quality_workflow_missing_gate",
                    _display_path(path, root),
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


def _iter_secret_bundle_docs(secret_bundle: Path) -> list[Path]:
    if not secret_bundle.exists():
        return []
    return sorted(
        path
        for path in secret_bundle.rglob("*")
        if path.is_file() and path.suffix.lower() in SECRET_BUNDLE_DOC_SUFFIXES
    )


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


def _literal_string_collection_assignment(tree: ast.AST, name: str) -> set[str]:
    value: ast.AST | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                value = node.value
                break
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            value = node.value
            break
    if value is None:
        raise ValueError(f"{name} assignment not found")
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in {"frozenset", "set", "tuple", "list"}
        and len(value.args) == 1
    ):
        value = value.args[0]
    items = ast.literal_eval(value)
    if not isinstance(items, (set, frozenset, tuple, list)):
        raise ValueError(f"{name} must be a literal collection")
    return {str(item) for item in items}


def load_store_gateway_contract(root: Path) -> dict[str, set[str] | str]:
    path = root / "src" / "minimal_kanban" / "mcp" / "store_gateway.py"
    tree = ast.parse(_read_text(path), filename=str(path))
    return {
        "read_capabilities": _literal_string_collection_assignment(
            tree, "STORE_READ_CAPABILITY_NAMES"
        ),
        "management_capability": str(_literal_assignment(tree, "STORE_MANAGEMENT_CAPABILITY_NAME")),
        "search_entities": _literal_string_collection_assignment(tree, "STORE_SEARCH_ENTITIES"),
        "management_operations": _literal_string_collection_assignment(
            tree, "STORE_MANAGEMENT_OPERATIONS"
        ),
    }


def _check_store_gateway_docs_contract(root: Path) -> list[Issue]:
    contract = load_store_gateway_contract(root)
    mcp_path = root / "MCP_GUIDE.md"
    chatgpt_path = root / "CHATGPT_CONNECTOR_SETUP.md"
    if not mcp_path.exists() or not chatgpt_path.exists():
        return []
    mcp_text = _read_text(mcp_path)
    chatgpt_text = _read_text(chatgpt_path)
    compact_mcp = " ".join(mcp_text.split())
    compact_chatgpt = " ".join(chatgpt_text.split())
    reads = set(contract["read_capabilities"])
    management = str(contract["management_capability"])
    internal_capabilities = reads | {management}
    search_entities = set(contract["search_entities"])
    operations = set(contract["management_operations"])
    issues: list[Issue] = []

    def missing_items(*, path: Path, text: str, items: set[str], code: str, label: str) -> None:
        missing = sorted(item for item in items if f"`{item}`" not in text)
        if missing:
            issues.append(Issue(code, _display_path(path, root), f"missing {label}: {missing}"))

    missing_items(
        path=mcp_path,
        text=mcp_text,
        items=internal_capabilities,
        code="mcp_guide_store_internal_capabilities_stale",
        label="Store internal capabilities",
    )
    missing_items(
        path=mcp_path,
        text=mcp_text,
        items=search_entities,
        code="mcp_guide_store_search_entities_stale",
        label="Store search entities",
    )
    for path, text in ((mcp_path, mcp_text), (chatgpt_path, chatgpt_text)):
        missing_items(
            path=path,
            text=text,
            items=operations,
            code="store_management_operations_stale",
            label="Store management operations",
        )
        if "download_store_quote_vin_photo" not in text:
            issues.append(
                Issue(
                    "store_vin_photo_workflow_missing",
                    _display_path(path, root),
                    "public Store VIN photo workflow is not documented",
                )
            )

    expected_internal_mcp = f"{len(internal_capabilities)} `INTERNAL_ONLY`"
    if expected_internal_mcp not in compact_mcp:
        issues.append(
            Issue(
                "mcp_guide_store_internal_count_stale",
                _display_path(mcp_path, root),
                f"expected current internal Store count text: {expected_internal_mcp}",
            )
        )
    expected_internal_chatgpt = f"{len(internal_capabilities)} mounted `store_*` adapter tools"
    if expected_internal_chatgpt not in compact_chatgpt:
        issues.append(
            Issue(
                "chatgpt_store_internal_count_stale",
                _display_path(chatgpt_path, root),
                f"expected current internal Store count text: {expected_internal_chatgpt}",
            )
        )
    if f"exactly {len(operations)} operations" not in compact_mcp:
        issues.append(
            Issue(
                "mcp_guide_store_operation_count_stale",
                _display_path(mcp_path, root),
                f"expected current Store operation count: {len(operations)}",
            )
        )
    if f"{len(operations)} allowlisted Store actions" not in compact_chatgpt:
        issues.append(
            Issue(
                "chatgpt_store_operation_count_stale",
                _display_path(chatgpt_path, root),
                f"expected current Store operation count: {len(operations)}",
            )
        )
    for stale_argument in ("store_cursor", "store_ack_token"):
        if stale_argument in chatgpt_text:
            issues.append(
                Issue(
                    "chatgpt_bootstrap_cursor_stale",
                    _display_path(chatgpt_path, root),
                    f"agent_bootstrap does not accept {stale_argument}",
                )
            )
    return issues


def _check_short_server_instruction_commands(root: Path) -> list[Issue]:
    path = root / "AUTOSTOPCRM_FULL_INSTRUCTION.txt"
    if not path.exists():
        return []
    text = " ".join(_read_text(path).split())
    required = (
        (
            "scripts/validate_production_env.py --require-production --require-store",
            "short server instruction omits the mandatory Store environment gate",
        ),
        (
            "scripts/check_agent_gateway_v2.py --mcp-url https://crm.autostopcrm.ru/mcp --exhaustive --require-store --require-web",
            "short server instruction omits mandatory Store/Web Gateway gates",
        ),
    )
    return [
        Issue("server_instruction_release_gate_stale", _display_path(path, root), detail)
        for command, detail in required
        if command not in text
    ]


def load_crm_registry_tools(root: Path) -> set[str]:
    registry_path = root / "src" / "minimal_kanban" / "mcp" / "tool_registry.py"
    tree = ast.parse(_read_text(registry_path), filename=str(registry_path))
    groups = _literal_assignment(tree, "MCP_TOOL_GROUPS")
    return {str(tool_name) for tools in groups.values() for tool_name in tools}


def load_gateway_expected_tools(root: Path) -> set[str]:
    del root
    from minimal_kanban.mcp.agent_gateway_support import PERMANENT_AGENT_GATEWAY_TOOL_NAMES

    return set(PERMANENT_AGENT_GATEWAY_TOOL_NAMES)


def _check_mcp_guide_gateway_surface(root: Path) -> list[Issue]:
    path = root / "MCP_GUIDE.md"
    if not path.exists():
        return []
    text = _read_text(path)
    expected_tools = load_gateway_expected_tools(root)
    missing_tools = sorted(
        tool_name for tool_name in expected_tools if f"`{tool_name}`" not in text
    )
    issues: list[Issue] = []
    if missing_tools:
        issues.append(
            Issue(
                "mcp_guide_gateway_tools_missing",
                _display_path(path, root),
                f"missing visible Gateway v2 tools: {missing_tools}",
            )
        )
    expected_count_text = f"exactly {len(expected_tools)} tools"
    if expected_count_text not in text:
        issues.append(
            Issue(
                "mcp_guide_gateway_count_stale",
                _display_path(path, root),
                f"expected current visible-tool count text: {expected_count_text}",
            )
        )
    return issues


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
    try:
        data = json.loads(_read_text(path), parse_constant=_reject_json_constant)
    except RecursionError as exc:
        raise ValueError(f"{path} JSON is too deeply nested") from exc
    reject_deeply_nested_json(data, message=f"{path} JSON is too deeply nested")
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
    implementation_tools: set[str] = set()
    for relative_path in CRM_MCP_RAW_TOOL_SOURCE_PATHS:
        source_path = root / relative_path
        if not source_path.exists():
            issues.append(
                Issue(
                    "missing_crm_mcp_source",
                    relative_path,
                    "raw MCP tool implementation source missing",
                )
            )
            continue
        implementation_tools.update(extract_decorated_tool_names(source_path))

    if registry_tools != implementation_tools:
        missing = sorted(registry_tools - implementation_tools)
        unexpected = sorted(implementation_tools - registry_tools)
        issues.append(
            Issue(
                "crm_mcp_registry_mismatch",
                "src/minimal_kanban/mcp",
                f"missing={missing}; unexpected={unexpected}",
            )
        )
    return issues


def _check_manager_docs_and_catalogs(
    root: Path,
    manager_root: Path,
) -> list[Issue]:
    issues: list[Issue] = _check_required(
        MANAGER_CANONICAL_DOCS,
        manager_root,
        "AutostopManager",
    )

    manager_tools_path = manager_root / "autostop_manager" / "mcp_tools.py"

    if not manager_tools_path.exists():
        return issues + [
            Issue("missing_manager_source", str(manager_tools_path), "manager MCP source missing")
        ]

    manager_tools = extract_decorated_tool_names(manager_tools_path)
    gateway_tools = load_gateway_expected_tools(root)
    try:
        fingerprints = _registered_surface_fingerprints(root, manager_root)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        fingerprints = {"manager": "", "crm": ""}
        issues.append(
            Issue(
                "mcp_catalog_schema_probe_failed",
                str(manager_root),
                type(exc).__name__,
            )
        )
    issues.extend(
        _manager_catalog_issues(
            manager_root / "docs" / "agent" / "manager_mcp_catalog.json",
            manager_tools,
            gateway_tools,
            fingerprints,
        )
    )
    issues.extend(_manager_doc_forbidden_issues(manager_root))
    issues.extend(_manager_gateway_instruction_issues(manager_root))
    return issues


def _tool_set_delta(actual: set[str], expected: set[str]) -> str:
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    return f"missing={missing}; unexpected={unexpected}"


def audit(
    root: Path = ROOT,
    *,
    manager_root: Path | None = None,
    include_skills: bool = False,
    secret_bundle: Path | None = None,
) -> list[Issue]:
    root = root.resolve()
    issues: list[Issue] = []

    issues.extend(_check_required(CRM_CANONICAL_DOCS, root, "CRM"))
    issues.extend(_check_unclassified_tracked_docs(root))
    issues.extend(_check_dockerignore_keeps_canonical_markdown(root))
    issues.extend(_check_canonical_local_links(root))
    issues.extend(_scan_existing_canonical_docs_for_forbidden_text(root))

    issues.extend(_check_script_instruction_text(root))
    issues.extend(_check_api_guide_required_routes(root))
    issues.extend(_check_short_server_instruction_commands(root))
    issues.extend(_check_quality_workflow_required_gates(root))
    issues.extend(_scan_retired_candidate_issues(root))

    try:
        issues.extend(_check_crm_mcp_surface(root))
        issues.extend(_check_mcp_guide_gateway_surface(root))
        issues.extend(_check_store_gateway_docs_contract(root))
    except (OSError, SyntaxError, ValueError) as exc:
        issues.append(Issue("crm_mcp_audit_error", str(root), str(exc)))

    if manager_root is not None:
        if manager_root.exists():
            issues.extend(
                _check_manager_docs_and_catalogs(
                    root,
                    manager_root.resolve(),
                )
            )
        else:
            issues.append(
                Issue(
                    "missing_manager_root",
                    str(manager_root),
                    "explicit AutostopManager root does not exist",
                )
            )

    if include_skills:
        issues.extend(_scan_user_skill_doc_issues(root))

    if secret_bundle is not None:
        secret_bundle = secret_bundle.resolve()
        if not secret_bundle.exists():
            issues.append(
                Issue(
                    "missing_secret_bundle",
                    str(secret_bundle),
                    "explicit secret/access bundle does not exist",
                )
            )
        else:
            issues.extend(_scan_secret_bundle_issues(secret_bundle, root=secret_bundle))

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
    parser.add_argument(
        "--manager-root",
        type=Path,
        default=None,
        help="Optional AutostopManager checkout to audit explicitly.",
    )
    parser.add_argument(
        "--include-skills",
        action="store_true",
        help="Also scan local user skill documentation for stale instructions.",
    )
    parser.add_argument(
        "--secret-bundle",
        type=Path,
        default=None,
        help="Optional local secret/access docs bundle to scan for stale instructions without printing secrets.",
    )
    args = parser.parse_args(argv)

    issues = audit(
        ROOT,
        manager_root=args.manager_root,
        include_skills=args.include_skills,
        secret_bundle=args.secret_bundle,
    )
    if args.format == "json":
        print(
            json.dumps(
                [asdict(issue) for issue in issues],
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
        )
    else:
        _print_text(issues)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
