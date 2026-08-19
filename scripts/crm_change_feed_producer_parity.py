from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.crm_capability_parity import build_inventory  # noqa: E402

MANIFEST_PATH = Path(__file__).with_name("crm_change_feed_producer_manifest.json")
MANIFEST_FORMAT = "autostopcrm_change_feed_producer_parity_v1"
ROUTE_CONTRACT_TEST = Path("tests/test_change_feed_route_contracts.py")

PRODUCER_KINDS = frozenset(
    {
        "state_projection",
        "shared_files_projection",
        "operator_users_projection",
        "print_module_projection",
        "composite_projection",
        "mixed_audit_infrastructure",
        "privacy_exemption",
        "infrastructure_exemption",
        "non_mutating_exemption",
    }
)
EXEMPTION_KINDS = frozenset(
    {"privacy_exemption", "infrastructure_exemption", "non_mutating_exemption"}
)
ALLOWED_EXEMPTIONS = {
    "privacy_exemption": frozenset(
        {
            "/api/login_operator",
            "/api/logout_operator",
            "/api/mark_cashbox_notifications_seen",
            "/api/update_personal_board_preferences",
        }
    ),
    "infrastructure_exemption": frozenset(
        {
            "/api/agent_enqueue_task",
            "/api/change_feed/ack",
            "/api/change_feed/bootstrap",
            "/api/delete_agent_scheduled_task",
            "/api/pause_agent_scheduled_task",
            "/api/resume_agent_scheduled_task",
            "/api/run_agent_scheduled_task",
            "/api/save_agent_scheduled_task",
        }
    ),
    "non_mutating_exemption": frozenset(
        {
            "/api/copy_shared_file",
            "/api/correct_repair_order_number",
            "/api/export_repair_order_print_pdf",
            "/api/preview_repair_order_print_documents",
        }
    ),
}
REQUIRED_ENTITY_DOMAINS = frozenset(
    {
        "attachment",
        "board_settings",
        "card",
        "cash_transaction",
        "cashbox",
        "client",
        "client_vehicle",
        "column",
        "employee",
        "employee_repair_order_accrual",
        "employee_shift_accrual",
        "inspection_sheet_form",
        "inventory_item",
        "inventory_movement",
        "operator_user",
        "print_settings",
        "print_template",
        "repair_order",
        "repair_order_cycle",
        "repair_order_material",
        "repair_order_payment",
        "repair_order_payroll_posting",
        "repair_order_work",
        "shared_file",
        "sticky",
        "vehicle_profile",
    }
)
REQUIRED_CHANGE_TYPES = frozenset({"create", "update", "move", "archive", "restore", "delete"})
ALLOWED_ROUTE_CONTRACT_EXEMPTIONS = frozenset(
    {
        "/api/agent_enqueue_task",
        "/api/autofill_inspection_sheet_form",
        "/api/autofill_repair_order",
        "/api/copy_shared_file",
        "/api/correct_repair_order_number",
        "/api/delete_agent_scheduled_task",
        "/api/delete_gateway_attestation_payment_fixture",
        "/api/finance_audit/apply_safe_fixes",
        "/api/mark_cashbox_notifications_seen",
        "/api/pause_agent_scheduled_task",
        "/api/preview_repair_order_print_documents",
        "/api/resume_agent_scheduled_task",
        "/api/rollback_manager_run",
        "/api/run_agent_scheduled_task",
        "/api/run_full_card_enrichment",
        "/api/run_manager_operation",
        "/api/save_agent_scheduled_task",
    }
)


def _issue(code: str, route: str, detail: str) -> dict[str, str]:
    return {"code": code, "route": route, "detail": detail}


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Producer manifest must contain one JSON object.")
    return payload


def _source_functions(root: Path) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    calls: dict[str, set[str]] = defaultdict(set)
    locations: dict[str, list[str]] = defaultdict(list)
    for path in sorted((root / "src" / "minimal_kanban").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            locations[node.name].append(f"{relative}:{node.lineno}")
            for child in ast.walk(node):
                if isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Load):
                    calls[node.name].add(child.attr)
                if not isinstance(child, ast.Call):
                    continue
                if isinstance(child.func, ast.Attribute):
                    calls[node.name].add(child.func.attr)
                elif isinstance(child.func, ast.Name):
                    calls[node.name].add(child.func.id)
    return dict(calls), dict(locations)


def _reachable_sinks(start: str, calls: dict[str, set[str]], sinks: set[str]) -> set[str]:
    reached: set[str] = set()
    seen: set[str] = set()
    queue = deque([start])
    while queue:
        name = queue.popleft()
        if name in seen:
            continue
        seen.add(name)
        if name in sinks:
            reached.add(name)
        for target in calls.get(name, set()):
            if target not in seen:
                queue.append(target)
    return reached


def _verify_evidence(
    evidence: object,
    *,
    root: Path,
    route: str,
    issues: list[dict[str, str]],
) -> list[str]:
    verified: list[str] = []
    if not isinstance(evidence, list) or not evidence:
        issues.append(_issue("producer_test_evidence_missing", route, "No producer test evidence."))
        return verified
    for item in evidence:
        if not isinstance(item, dict):
            issues.append(
                _issue("producer_test_evidence_invalid", route, "Evidence must be an object.")
            )
            continue
        source = str(item.get("source") or "")
        pattern = str(item.get("pattern") or "")
        path = root / source
        if not source.startswith("tests/") or not path.is_file() or not pattern:
            issues.append(
                _issue(
                    "producer_test_evidence_invalid",
                    route,
                    "Evidence needs an existing tests/ source and exact pattern.",
                )
            )
            continue
        text = path.read_text(encoding="utf-8")
        offset = text.find(pattern)
        if offset < 0:
            issues.append(
                _issue(
                    "producer_test_evidence_missing",
                    route,
                    f"Pattern absent from {source}: {pattern}",
                )
            )
            continue
        line = text.count("\n", 0, offset) + 1
        verified.append(f"{source}:{line}")
    return verified


def _canonical_route_contracts(
    root: Path,
) -> tuple[dict[str, str], dict[str, str], list[dict[str, str]]]:
    """Read executable temp-state route contracts and narrow boundary decisions."""

    path = root / ROUTE_CONTRACT_TEST
    issues: list[dict[str, str]] = []
    if not path.is_file():
        return (
            {},
            {},
            [
                _issue(
                    "canonical_route_contract_test_missing",
                    "manifest",
                    ROUTE_CONTRACT_TEST.as_posix(),
                )
            ],
        )
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    invoked: dict[str, str] = {}
    exemptions: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr != "_invoke" or not node.args:
                continue
            route_arg = node.args[0]
            if isinstance(route_arg, ast.Constant) and isinstance(route_arg.value, str):
                route = route_arg.value
                if route.startswith("/api/"):
                    invoked[route] = f"{ROUTE_CONTRACT_TEST.as_posix()}:{node.lineno}"
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "REASONED_ROUTE_CONTRACT_EXEMPTIONS"
            for target in targets
        ):
            continue
        value = node.value
        if not isinstance(value, ast.Dict):
            issues.append(
                _issue(
                    "canonical_route_contract_exemptions_invalid",
                    "manifest",
                    "REASONED_ROUTE_CONTRACT_EXEMPTIONS must be a literal dictionary.",
                )
            )
            continue
        for key_node, value_node in zip(value.keys, value.values, strict=True):
            if not (
                isinstance(key_node, ast.Constant)
                and isinstance(key_node.value, str)
                and isinstance(value_node, ast.Constant)
                and isinstance(value_node.value, str)
            ):
                issues.append(
                    _issue(
                        "canonical_route_contract_exemption_invalid",
                        "manifest",
                        f"Non-literal exemption at {ROUTE_CONTRACT_TEST}:{node.lineno}",
                    )
                )
                continue
            exemptions[key_node.value] = value_node.value.strip()
    overlap = sorted(set(invoked) & set(exemptions))
    for route in overlap:
        issues.append(
            _issue(
                "canonical_route_contract_duplicate",
                route,
                "Route is both executable and exempted.",
            )
        )
    for route, rationale in sorted(exemptions.items()):
        if route not in ALLOWED_ROUTE_CONTRACT_EXEMPTIONS:
            issues.append(
                _issue(
                    "canonical_route_contract_exemption_not_allowlisted",
                    route,
                    "Route is not in the reviewed boundary allowlist.",
                )
            )
        if len(rationale) < 32:
            issues.append(
                _issue(
                    "canonical_route_contract_exemption_reason_invalid",
                    route,
                    "Boundary rationale must contain at least 32 characters.",
                )
            )
    for route in sorted(ALLOWED_ROUTE_CONTRACT_EXEMPTIONS - set(exemptions)):
        issues.append(
            _issue(
                "canonical_route_contract_exemption_missing",
                route,
                "Reviewed boundary route is absent from the executable contract matrix.",
            )
        )
    return invoked, exemptions, issues


def build_producer_inventory(
    *,
    root: Path = ROOT,
    manifest_path: Path = MANIFEST_PATH,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    capability = build_inventory(root=root)
    issues: list[dict[str, str]] = []
    if manifest.get("format") != MANIFEST_FORMAT:
        issues.append(
            _issue("manifest_format_invalid", "manifest", str(manifest.get("format") or ""))
        )
    if not capability["summary"].get("parity_complete") or capability.get("issues"):
        issues.append(
            _issue(
                "capability_parity_incomplete",
                "manifest",
                "UI/backend/Gateway capability parity must be complete first.",
            )
        )

    write_rows = {row["route"]: row for row in capability["matrix"] if row["risk"] == "write"}
    groups = manifest.get("producer_groups")
    if not isinstance(groups, list):
        groups = []
        issues.append(
            _issue("producer_groups_invalid", "manifest", "producer_groups must be a list.")
        )

    route_groups: dict[str, dict[str, Any]] = {}
    for group in groups:
        if not isinstance(group, dict):
            issues.append(
                _issue("producer_group_invalid", "manifest", "Every group must be an object.")
            )
            continue
        name = str(group.get("name") or "")
        kind = str(group.get("kind") or "")
        routes = group.get("routes")
        if not name or kind not in PRODUCER_KINDS or not isinstance(routes, list) or not routes:
            issues.append(
                _issue(
                    "producer_group_invalid",
                    name or "manifest",
                    "Group needs a name, allowed kind and non-empty routes.",
                )
            )
            continue
        for raw_route in routes:
            route = str(raw_route)
            if route in route_groups:
                issues.append(
                    _issue("producer_route_duplicate", route, "Route appears in multiple groups.")
                )
                continue
            route_groups[route] = group

    for route in sorted(set(write_rows) - set(route_groups)):
        issues.append(
            _issue("producer_route_uncovered", route, "Write route has no producer decision.")
        )
    for route in sorted(set(route_groups) - set(write_rows)):
        issues.append(
            _issue("producer_route_stale", route, "Manifest route is not a discovered write route.")
        )

    declared_entities = set(manifest.get("entity_domains") or [])
    missing_entities = sorted(REQUIRED_ENTITY_DOMAINS - declared_entities)
    extra_entities = sorted(declared_entities - REQUIRED_ENTITY_DOMAINS)
    if missing_entities or extra_entities:
        issues.append(
            _issue(
                "entity_domain_matrix_invalid",
                "manifest",
                f"missing={missing_entities} extra={extra_entities}",
            )
        )
    declared_changes = set(manifest.get("change_types") or [])
    if declared_changes != set(REQUIRED_CHANGE_TYPES):
        issues.append(
            _issue(
                "change_type_matrix_invalid",
                "manifest",
                f"expected={sorted(REQUIRED_CHANGE_TYPES)} actual={sorted(declared_changes)}",
            )
        )

    calls, function_locations = _source_functions(root)
    canonical_routes, contract_exemptions, contract_issues = _canonical_route_contracts(root)
    issues.extend(contract_issues)
    matrix: list[dict[str, Any]] = []
    for route, capability_row in sorted(write_rows.items()):
        group = route_groups.get(route)
        if group is None:
            matrix.append(
                {
                    "route": route,
                    "handler": capability_row["surfaces"].get("backend_handler"),
                    "status": "gap",
                }
            )
            continue
        kind = str(group["kind"])
        handler = str(capability_row["surfaces"].get("backend_handler") or "")
        route_issues_before = len(issues)
        handler_sources = list(function_locations.get(handler) or [])
        if not handler and kind not in EXEMPTION_KINDS:
            issues.append(
                _issue("producer_handler_missing", route, "Covered producer route has no handler.")
            )
        elif handler and not handler_sources:
            issues.append(
                _issue(
                    "producer_handler_source_missing", route, f"Handler source not found: {handler}"
                )
            )

        sinks = {str(value) for value in group.get("sinks") or [] if str(value)}
        reached = _reachable_sinks(handler, calls, sinks) if handler and sinks else set()
        if kind not in EXEMPTION_KINDS and sinks and not reached:
            issues.append(
                _issue(
                    "producer_sink_unreachable",
                    route,
                    f"Handler {handler} does not reach one of {sorted(sinks)}.",
                )
            )

        if kind in EXEMPTION_KINDS:
            allowed = ALLOWED_EXEMPTIONS.get(kind, frozenset())
            if route not in allowed:
                issues.append(
                    _issue(
                        "producer_exemption_not_allowlisted",
                        route,
                        f"{kind} is not allowed for this route.",
                    )
                )
            rationale = str(group.get("rationale") or "").strip()
            route_evidence = group.get("route_evidence")
            evidence = (
                route_evidence.get(route)
                if isinstance(route_evidence, dict) and isinstance(route_evidence.get(route), dict)
                else {}
            )
            source = root / str(evidence.get("source") or "")
            pattern = str(evidence.get("pattern") or "")
            if len(rationale) < 24 or not source.is_file() or not pattern:
                issues.append(
                    _issue(
                        "producer_exemption_evidence_invalid",
                        route,
                        "Exemption needs rationale, existing source and exact evidence pattern.",
                    )
                )
            elif pattern not in source.read_text(encoding="utf-8"):
                issues.append(
                    _issue(
                        "producer_exemption_evidence_missing",
                        route,
                        f"Pattern absent from {source.relative_to(root)}.",
                    )
                )

        producer_test_evidence = _verify_evidence(
            group.get("test_evidence"), root=root, route=route, issues=issues
        )
        route_handler_tests = list(capability_row.get("test_evidence") or [])
        if not route_handler_tests:
            issues.append(
                _issue(
                    "route_handler_test_evidence_missing",
                    route,
                    "Capability matrix has no route/handler test evidence.",
                )
            )
        canonical_contract: dict[str, str] | None = None
        if capability_row.get("readback_class") == "executor_contract_only":
            if route in canonical_routes:
                canonical_contract = {
                    "class": "temp_state_feed_readback",
                    "evidence": canonical_routes[route],
                }
            elif route in contract_exemptions:
                canonical_contract = {
                    "class": "reasoned_boundary",
                    "evidence": f"{ROUTE_CONTRACT_TEST.as_posix()}:REASONED_ROUTE_CONTRACT_EXEMPTIONS",
                    "reason": contract_exemptions[route],
                }
            else:
                issues.append(
                    _issue(
                        "canonical_route_contract_missing",
                        route,
                        "executor_contract_only route has no temp-state feed readback or reviewed boundary.",
                    )
                )
        status = "covered" if len(issues) == route_issues_before else "gap"
        matrix.append(
            {
                "route": route,
                "action": capability_row["action"],
                "handler": handler or None,
                "handler_sources": handler_sources,
                "gateway": capability_row["reachability"].get("selected"),
                "readback_class": capability_row.get("readback_class"),
                "producer_group": str(group["name"]),
                "producer_kind": kind,
                "producer": str(group.get("producer") or ""),
                "reachable_sinks": sorted(reached),
                "route_handler_test_evidence": route_handler_tests,
                "producer_test_evidence": producer_test_evidence,
                "canonical_route_contract": canonical_contract,
                "status": status,
            }
        )

    status_counts = Counter(str(row["status"]) for row in matrix)
    kind_counts = Counter(str(row.get("producer_kind") or "unmapped") for row in matrix)
    executor_contract_only = sorted(
        row["route"] for row in matrix if row.get("readback_class") == "executor_contract_only"
    )
    executor_contract_resolved = sum(
        1
        for row in matrix
        if row.get("readback_class") == "executor_contract_only"
        and row.get("canonical_route_contract")
    )
    summary = {
        "write_actions": len(write_rows),
        "covered": status_counts["covered"],
        "gaps": status_counts["gap"],
        "producer_kinds": dict(sorted(kind_counts.items())),
        "entity_domains": len(declared_entities),
        "change_types": sorted(declared_changes),
        "executor_contract_only": len(executor_contract_only),
        "executor_contract_resolved": executor_contract_resolved,
        "canonical_route_feed_readback": sum(
            1
            for row in matrix
            if (row.get("canonical_route_contract") or {}).get("class")
            == "temp_state_feed_readback"
        ),
        "reasoned_route_contract_exemptions": sum(
            1
            for row in matrix
            if (row.get("canonical_route_contract") or {}).get("class") == "reasoned_boundary"
        ),
        "canonical_contract_complete": executor_contract_resolved == len(executor_contract_only),
        "producer_complete": not issues and status_counts["gap"] == 0,
    }
    return {
        "format": MANIFEST_FORMAT,
        "summary": summary,
        "matrix": matrix,
        "executor_contract_only_routes": executor_contract_only,
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify CRM write route to change-feed producer parity."
    )
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--format", choices=("json", "summary"), default="summary")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    result = build_producer_inventory(manifest_path=args.manifest)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        summary = result["summary"]
        print(
            "CRM change-feed producer parity: "
            f"write_actions={summary['write_actions']} covered={summary['covered']} "
            f"gaps={summary['gaps']} entity_domains={summary['entity_domains']} "
            f"producer_complete={str(summary['producer_complete']).lower()}"
        )
        for issue in result["issues"]:
            print(f"- {issue['code']} {issue['route']}: {issue['detail']}")
    if args.require_complete and not result["summary"]["producer_complete"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
