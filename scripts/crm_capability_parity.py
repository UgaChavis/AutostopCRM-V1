from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.route_registry import (  # noqa: E402
    build_operator_routes,
    build_route_specs,
    build_service_routes,
    policy_for_route,
)
from minimal_kanban.mcp.agent_gateway_v2 import (  # noqa: E402
    PERMANENT_AGENT_GATEWAY_TOOL_NAMES,
)
from minimal_kanban.mcp.gateway_contract import (  # noqa: E402
    BOARD_WORKFLOW_OPERATIONS,
    DOCUMENT_WORKFLOW_OPERATIONS,
    FINANCE_VIRTUAL_OPERATIONS,
    FINANCE_WORKFLOW_OPERATIONS,
    INVENTORY_WORKFLOW_OPERATIONS,
)
from minimal_kanban.mcp.raw_gateway import RAW_API_ROUTES  # noqa: E402
from minimal_kanban.mcp.tool_registry import PUBLIC_MCP_TOOL_NAMES  # noqa: E402

MANIFEST_PATH = Path(__file__).with_name("crm_capability_parity_manifest.json")
MANIFEST_FORMAT = "autostopcrm_capability_parity_v1"
API_ROUTE_PATTERN = re.compile(r"/api/[A-Za-z0-9_./-]+")
PARITY_TEST_EVIDENCE = (
    "tests/test_crm_capability_parity.py::"
    "CrmCapabilityParityTests.test_inventory_has_no_unexpected_uncovered_actions"
)
PARITY_INVENTORY_TEST_FILES = frozenset(
    {"test_crm_capability_parity.py", "test_crm_parity_inventory_quality.py"}
)
ALLOWED_INTENTIONAL_EXEMPTIONS = frozenset(
    {
        "/api/get_operator_profile",
        "/api/get_module_map_infrastructure",
        "/api/login_operator",
        "/api/logout_operator",
        "/api/update_personal_board_preferences",
    }
)
READ_OPERATION_OVERRIDES = frozenset(
    {
        "/api/attachment",
        "/api/get_module_map_infrastructure",
        "/api/health",
        "/api/repair_order_text",
        "/api/shared_file",
        "/api/get_repair_order",
    }
)
WRITE_OPERATION_OVERRIDES = frozenset(
    {
        "/api/login_operator",
        "/api/logout_operator",
        "/api/update_personal_board_preferences",
        "/api/open_card",
        "/api/set_card_ai_autofill",
        "/api/change_feed/ack",
        "/api/change_feed/bootstrap",
    }
)
WORKFLOW_ROUTE_ALIASES = {
    "/api/export_repair_order_print_pdf": (
        "agent_document_workflow",
        "download_repair_order_print_pdf",
    ),
    "/api/finance_audit/apply_safe_fixes": (
        "agent_finance_workflow",
        "apply_finance_audit_safe_fixes",
    ),
}
WORKFLOW_OPERATIONS_BY_TOOL = {
    "agent_board_workflow": BOARD_WORKFLOW_OPERATIONS,
    "agent_document_workflow": DOCUMENT_WORKFLOW_OPERATIONS,
    "agent_finance_workflow": FINANCE_WORKFLOW_OPERATIONS,
    "agent_inventory_workflow": INVENTORY_WORKFLOW_OPERATIONS,
}
EXACT_READBACK_CLASSES = {
    "create_cash_transaction": "exact_cashbox_transaction",
    "create_cashbox": "exact_cashbox",
    "delete_cashbox": "exact_absence",
    "delete_shared_file": "exact_absence",
    "record_repair_order_payment": "repair_order_and_cash_journal",
    "replenish_inventory_item": "exact_inventory_item",
    "return_inventory_movement": "exact_inventory_item",
    "save_inventory_item": "exact_inventory_item",
    "set_repair_order_status": "exact_repair_order",
    "update_repair_order": "exact_repair_order",
    "upload_shared_file": "exact_shared_file",
    "write_off_inventory_item": "exact_inventory_item",
}


class _FakeService:
    def __getattr__(self, name: str):
        def handler(payload: dict[str, Any] | None = None) -> dict[str, Any]:
            return {"handler": name, "payload": payload}

        handler.__name__ = name
        return handler


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("CRM capability parity manifest must contain one JSON object.")
    return value


def _normalized_route(value: str) -> str | None:
    route = str(value or "").split("?", 1)[0].rstrip("/")
    if route == "/api" or not route.startswith("/api/"):
        return None
    return route


def _normalized_http_route(value: str) -> str | None:
    route = str(value or "").split("?", 1)[0]
    if not route.startswith("/") or route.startswith("//"):
        return None
    if route != "/":
        route = route.rstrip("/")
    return route or "/"


def _route_locations(path: Path) -> dict[str, list[str]]:
    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(ROOT).as_posix()
    locations: dict[str, list[str]] = defaultdict(list)
    for match in API_ROUTE_PATTERN.finditer(text):
        route = _normalized_route(match.group(0))
        if route is None:
            continue
        line = text.count("\n", 0, match.start()) + 1
        location = f"{relative}:{line}"
        if location not in locations[route]:
            locations[route].append(location)
    return dict(locations)


def _http_route_locations(path: Path) -> dict[str, list[str]]:
    """Find API literals plus exact non-API paths compared against ``route``."""

    locations = _route_locations(path)
    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(ROOT).as_posix()
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return locations
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
            continue
        if node.left.id != "route" or len(node.ops) != 1 or len(node.comparators) != 1:
            continue
        comparator = node.comparators[0]
        values: list[str] = []
        if isinstance(node.ops[0], ast.Eq) and isinstance(comparator, ast.Constant):
            if isinstance(comparator.value, str):
                values.append(comparator.value)
        elif isinstance(node.ops[0], ast.In) and isinstance(
            comparator, (ast.Set, ast.Tuple, ast.List)
        ):
            values.extend(
                item.value
                for item in comparator.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
        for value in values:
            route = _normalized_http_route(value)
            if route is None:
                continue
            location = f"{relative}:{node.lineno}"
            bucket = locations.setdefault(route, [])
            if location not in bucket:
                bucket.append(location)
    return locations


def _merge_locations(target: dict[str, list[str]], additions: dict[str, list[str]]) -> None:
    for route, locations in additions.items():
        bucket = target.setdefault(route, [])
        for location in locations:
            if location not in bucket:
                bucket.append(location)


def discover_ui_routes(
    manifest: dict[str, Any], root: Path = ROOT
) -> tuple[dict[str, list[str]], list[dict[str, str]]]:
    locations: dict[str, list[str]] = {}
    issues: list[dict[str, str]] = []
    for source_pattern in manifest.get("ui_sources") or []:
        matches = sorted(root.glob(str(source_pattern)))
        if not matches:
            issues.append(
                {
                    "code": "ui_source_missing",
                    "action": str(source_pattern),
                    "detail": "Manifest UI source pattern matched no files.",
                }
            )
        for path in matches:
            _merge_locations(locations, _route_locations(path))

    for route, evidence in (manifest.get("dynamic_ui_routes") or {}).items():
        source = root / str(evidence.get("source") or "")
        pattern = str(evidence.get("evidence_pattern") or "")
        if not source.is_file() or not pattern:
            issues.append(
                {
                    "code": "dynamic_ui_evidence_invalid",
                    "action": route,
                    "detail": "Dynamic UI route needs an existing source and evidence pattern.",
                }
            )
            continue
        text = source.read_text(encoding="utf-8")
        offset = text.find(pattern)
        if offset < 0:
            issues.append(
                {
                    "code": "dynamic_ui_evidence_missing",
                    "action": route,
                    "detail": f"Pattern not found in {evidence['source']}.",
                }
            )
            continue
        line = text.count("\n", 0, offset) + 1
        locations.setdefault(route, []).append(f"{evidence['source']}:{line} (dynamic)")
    return locations, issues


def discover_backend_routes() -> dict[str, dict[str, str]]:
    service = _FakeService()
    shared_files = _FakeService()
    service_routes = build_service_routes(
        service,
        shared_files,
        paste_shared_files_from_clipboard=shared_files.paste_shared_files_from_clipboard,
    )
    operator_routes = build_operator_routes(_FakeService())
    overlap = set(service_routes) & set(operator_routes)
    if overlap:
        raise ValueError(f"Service/operator route overlap: {sorted(overlap)}")
    discovered: dict[str, dict[str, str]] = {}
    for registry, routes in (("service", service_routes), ("operator", operator_routes)):
        specs = build_route_specs(routes, registry=registry)
        for route, spec in specs.items():
            discovered[route] = {
                "registry": registry,
                "handler": spec.handler_name,
                "methods": ",".join(sorted(spec.methods)),
                "mutation_kind": spec.mutation_kind,
                "auth_kind": spec.auth_kind,
                "maintenance_behavior": spec.maintenance_behavior,
                "response_kind": spec.response_kind,
                "feed_expectation": spec.feed_expectation,
                "readback_class": spec.readback_class,
            }
    return discovered


def discover_http_routes(
    manifest: dict[str, Any], root: Path = ROOT
) -> tuple[dict[str, list[str]], list[dict[str, str]]]:
    locations: dict[str, list[str]] = {}
    issues: list[dict[str, str]] = []
    for source_pattern in manifest.get("http_route_sources") or []:
        matches = sorted(root.glob(str(source_pattern)))
        if not matches:
            issues.append(
                {
                    "code": "http_route_source_missing",
                    "action": str(source_pattern),
                    "detail": "Manifest HTTP route source pattern matched no files.",
                }
            )
        for path in matches:
            _merge_locations(locations, _http_route_locations(path))
    return locations, issues


def discover_test_evidence(
    root: Path = ROOT, *, additional_routes: set[str] | frozenset[str] = frozenset()
) -> dict[str, list[str]]:
    locations: dict[str, list[str]] = {}
    for path in sorted((root / "tests").glob("test_*.py")):
        if path.name in PARITY_INVENTORY_TEST_FILES:
            continue
        _merge_locations(locations, _route_locations(path))
        if additional_routes:
            text = path.read_text(encoding="utf-8")
            relative = path.relative_to(root).as_posix()
            for route in sorted(additional_routes):
                offset = text.find(route)
                if offset < 0:
                    continue
                location = f"{relative}:{text.count(chr(10), 0, offset) + 1}"
                bucket = locations.setdefault(route, [])
                if location not in bucket:
                    bucket.append(location)
    return locations


def _workflow_for_route(route: str) -> tuple[str, str] | None:
    if route in WORKFLOW_ROUTE_ALIASES:
        return WORKFLOW_ROUTE_ALIASES[route]
    operation = route.removeprefix("/api/")
    if operation in BOARD_WORKFLOW_OPERATIONS:
        return "agent_board_workflow", operation
    if operation in FINANCE_WORKFLOW_OPERATIONS:
        return "agent_finance_workflow", operation
    if operation in INVENTORY_WORKFLOW_OPERATIONS:
        return "agent_inventory_workflow", operation
    if operation in DOCUMENT_WORKFLOW_OPERATIONS:
        return "agent_document_workflow", operation
    virtual_route = FINANCE_VIRTUAL_OPERATIONS.get(operation)
    if virtual_route == route:
        return "agent_finance_workflow", operation
    return None


def _is_write_action(route: str) -> bool:
    if route in READ_OPERATION_OVERRIDES:
        return False
    if route in WRITE_OPERATION_OVERRIDES:
        return True
    if not route.startswith("/api/"):
        return False
    try:
        return policy_for_route(route).is_write
    except ValueError:
        # Discovery-only routes without reviewed policy remain high-risk until
        # either the registry rejects them or parity reports the missing seam.
        return True


def _selected_reachability(
    route: str,
    *,
    manifest: dict[str, Any],
) -> dict[str, str] | None:
    special = (manifest.get("special_http_coverage") or {}).get(route)
    if isinstance(special, dict):
        selected = {
            "kind": str(special.get("gateway_kind") or ""),
            "gateway_tool": str(special.get("gateway_tool") or ""),
        }
        if special.get("operation"):
            selected["operation"] = str(special["operation"])
        if special.get("mcp_tool"):
            selected["mcp_tool"] = str(special["mcp_tool"])
        return selected
    workflow = _workflow_for_route(route)
    if workflow is not None:
        return {"kind": "named_workflow", "gateway_tool": workflow[0], "operation": workflow[1]}
    operation = route.removeprefix("/api/")
    if operation in PUBLIC_MCP_TOOL_NAMES:
        return {
            "kind": "guarded_raw_mcp",
            "gateway_tool": "call_raw_capability",
            "mcp_tool": operation,
        }
    if route in RAW_API_ROUTES:
        return {
            "kind": "guarded_virtual_api",
            "gateway_tool": "call_raw_capability",
            "operation": f"api:{route}",
        }
    return None


def _reachability(route: str, manifest: dict[str, Any]) -> dict[str, Any]:
    special = (manifest.get("special_http_coverage") or {}).get(route) or {}
    operation = route.removeprefix("/api/")
    special_mcp_tool = str(special.get("mcp_tool") or "")
    mcp_tool = operation if operation in PUBLIC_MCP_TOOL_NAMES else special_mcp_tool or None
    workflow = _workflow_for_route(route)
    if special.get("gateway_kind") == "named_workflow":
        workflow = (
            str(special.get("gateway_tool") or ""),
            str(special.get("operation") or special_mcp_tool),
        )
    return {
        "named_workflow": (
            {"gateway_tool": workflow[0], "operation": workflow[1]} if workflow else None
        ),
        "mcp_tool": mcp_tool,
        "guarded_raw_mcp": bool(mcp_tool and mcp_tool in PUBLIC_MCP_TOOL_NAMES),
        "virtual_capability": f"api:{route}" if route in RAW_API_ROUTES else None,
        "selected": _selected_reachability(route, manifest=manifest),
    }


def _verify_special_backend(
    route: str,
    special: dict[str, Any],
    *,
    backend_registered: bool,
    root: Path,
) -> tuple[bool, list[dict[str, str]]]:
    if backend_registered:
        return True, []
    source_value = str(special.get("backend_source") or "")
    pattern = str(special.get("backend_evidence_pattern") or "")
    source = root / source_value
    if not source.is_file() or not pattern:
        return False, [
            _issue(
                "special_backend_evidence_invalid",
                route,
                "Non-registry HTTP coverage needs an existing source and exact pattern.",
            )
        ]
    if pattern not in source.read_text(encoding="utf-8"):
        return False, [
            _issue(
                "special_backend_evidence_missing",
                route,
                f"Special backend handler pattern is absent from {source_value}.",
            )
        ]
    return True, []


def _verify_special_gateway(
    route: str, special: dict[str, Any]
) -> tuple[bool, list[dict[str, str]]]:
    kind = str(special.get("gateway_kind") or "")
    gateway_tool = str(special.get("gateway_tool") or "")
    mcp_tool = str(special.get("mcp_tool") or "")
    operation = str(special.get("operation") or mcp_tool)
    if kind == "permanent_gateway_tool":
        if gateway_tool == operation and gateway_tool in PERMANENT_AGENT_GATEWAY_TOOL_NAMES:
            return True, []
        return False, [
            _issue(
                "special_permanent_gateway_tool_missing",
                route,
                f"Declared permanent Gateway tool is not registered: {gateway_tool or '<empty>'}.",
            )
        ]
    if kind == "guarded_virtual_api" and gateway_tool == "call_raw_capability":
        expected_operation = f"api:{route}"
        if route in RAW_API_ROUTES and operation == expected_operation:
            return True, []
        return False, [
            _issue(
                "special_virtual_api_missing",
                route,
                f"Declared virtual route must be registered as {expected_operation}.",
            )
        ]
    if kind == "guarded_virtual_api_alias" and gateway_tool == "call_raw_capability":
        target_route = operation.removeprefix("api:")
        if operation.startswith("api:") and target_route in RAW_API_ROUTES:
            return True, []
        return False, [
            _issue(
                "special_virtual_api_alias_missing",
                route,
                f"Declared alias target is not a registered virtual API route: {operation}.",
            )
        ]
    if not mcp_tool or mcp_tool not in PUBLIC_MCP_TOOL_NAMES:
        return False, [
            _issue(
                "special_mcp_tool_missing",
                route,
                f"Declared MCP tool is not registered: {mcp_tool or '<empty>'}.",
            )
        ]
    if kind == "guarded_raw_mcp" and gateway_tool == "call_raw_capability":
        return True, []
    allowed = WORKFLOW_OPERATIONS_BY_TOOL.get(gateway_tool)
    if kind == "named_workflow" and allowed is not None and operation in allowed:
        return True, []
    return False, [
        _issue(
            "special_gateway_path_invalid",
            route,
            f"Declared path {gateway_tool}:{operation} is not a registered workflow route.",
        )
    ]


def _readback_class(
    route: str,
    *,
    status: str,
    reachability: dict[str, Any],
    manifest: dict[str, Any],
) -> str:
    special = (manifest.get("special_http_coverage") or {}).get(route) or {}
    if special.get("readback_class"):
        return str(special["readback_class"])
    if status == "intentional_exemption":
        return "human_session_boundary"
    if status == "gap":
        return "unavailable"
    if not _is_write_action(route):
        return "read_operation"
    selected = reachability.get("selected") or {}
    operation = str(
        selected.get("operation") or selected.get("mcp_tool") or route.removeprefix("/api/")
    )
    if operation.startswith("api:"):
        return "executor_contract_only"
    if operation in EXACT_READBACK_CLASSES:
        return EXACT_READBACK_CLASSES[operation]
    if operation in BOARD_WORKFLOW_OPERATIONS or operation.startswith("bulk_"):
        return "backend_manager_verification"
    if operation in {"create_document_without_card_pdf", "download_repair_order_print_pdf"}:
        return "document_artifact"
    return "executor_contract_only"


def _issue(code: str, action: str, detail: str) -> dict[str, str]:
    return {"code": code, "action": action, "detail": detail}


def build_inventory(
    *,
    root: Path = ROOT,
    manifest_path: Path = MANIFEST_PATH,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    ui_locations, issues = discover_ui_routes(manifest, root)
    http_locations, http_issues = discover_http_routes(manifest, root)
    issues.extend(http_issues)
    backend_routes = discover_backend_routes()
    special_coverage = manifest.get("special_http_coverage") or {}
    presentation_routes = manifest.get("presentation_http_routes") or {}
    test_evidence = discover_test_evidence(
        root,
        additional_routes=frozenset(
            route for route in special_coverage if _normalized_route(route) is None
        ),
    )
    baseline_gaps = manifest.get("baseline_gaps") or {}
    exemptions = manifest.get("intentional_exemptions") or {}

    if manifest.get("format") != MANIFEST_FORMAT:
        issues.append(_issue("manifest_format_invalid", "manifest", str(manifest.get("format"))))
    if set(exemptions) != set(ALLOWED_INTENTIONAL_EXEMPTIONS):
        issues.append(
            _issue(
                "intentional_exemptions_invalid",
                "manifest",
                "Only reviewed human-session routes may be intentional parity exemptions.",
            )
        )
    if len(baseline_gaps) != 14:
        issues.append(
            _issue(
                "baseline_gap_count_invalid",
                "manifest",
                f"Expected the reviewed 14-gap baseline, found {len(baseline_gaps)}.",
            )
        )
    if not set(exemptions) <= set(baseline_gaps):
        issues.append(
            _issue(
                "exemption_not_in_baseline",
                "manifest",
                "Every exemption must refer to one reviewed baseline gap.",
            )
        )

    for route, decision in presentation_routes.items():
        if route.startswith("/api/") or route not in http_locations:
            issues.append(
                _issue(
                    "presentation_http_route_invalid",
                    route,
                    "Presentation-only route must be a discovered non-API HTTP route.",
                )
            )
        business_route = str((decision or {}).get("business_data_route") or "")
        if business_route and business_route not in backend_routes:
            issues.append(
                _issue(
                    "presentation_business_route_missing",
                    route,
                    f"Presentation route points to an unregistered data route: {business_route}.",
                )
            )

    action_routes = (
        set(ui_locations) | set(http_locations) | set(backend_routes) | set(special_coverage)
    ) - set(presentation_routes)
    for route in sorted(set(baseline_gaps) - action_routes):
        issues.append(
            _issue(
                "baseline_action_missing",
                route,
                "Baseline gap no longer exists in UI/backend discovery; update the manifest.",
            )
        )

    matrix: list[dict[str, Any]] = []
    for route in sorted(action_routes):
        backend = backend_routes.get(route)
        special = special_coverage.get(route) if isinstance(special_coverage, dict) else None
        special_backend_verified = False
        special_gateway_verified = False
        if isinstance(special, dict):
            special_backend_verified, special_backend_issues = _verify_special_backend(
                route,
                special,
                backend_registered=backend is not None,
                root=root,
            )
            special_gateway_verified, special_gateway_issues = _verify_special_gateway(
                route, special
            )
            issues.extend(special_backend_issues)
            issues.extend(special_gateway_issues)
        backend_present = backend is not None or special_backend_verified
        reachability = _reachability(route, manifest)
        gateway_reachable = bool(reachability.get("selected")) and (
            not isinstance(special, dict) or special_gateway_verified
        )
        actual_coverage = backend_present and gateway_reachable
        if route in exemptions:
            status = "intentional_exemption"
            if actual_coverage:
                issues.append(
                    _issue(
                        "human_session_exemption_became_reachable",
                        route,
                        "Human login/logout must remain outside service-principal reachability.",
                    )
                )
        elif actual_coverage:
            status = "covered"
        else:
            status = "gap"
            if route not in baseline_gaps:
                issues.append(
                    _issue(
                        "unexpected_uncovered_action",
                        route,
                        "New UI/backend action has no verified Gateway coverage or exemption.",
                    )
                )

        evidence = list(test_evidence.get(route) or [])
        if not test_evidence.get(route):
            issues.append(
                _issue(
                    "route_test_evidence_missing",
                    route,
                    "No focused test source contains this exact API action.",
                )
            )
        matrix.append(
            {
                "action": route.removeprefix("/api/"),
                "route": route,
                "surfaces": {
                    "ui": route in ui_locations,
                    "ui_sources": ui_locations.get(route, []),
                    "http": route in http_locations,
                    "http_sources": http_locations.get(route, []),
                    "backend_registered": backend is not None,
                    "backend_registry": backend.get("registry") if backend else None,
                    "backend_handler": backend.get("handler") if backend else None,
                    "backend_methods": backend.get("methods") if backend else None,
                    "backend_mutation_kind": backend.get("mutation_kind") if backend else None,
                    "backend_auth_kind": backend.get("auth_kind") if backend else None,
                    "backend_maintenance_behavior": (
                        backend.get("maintenance_behavior") if backend else None
                    ),
                    "backend_response_kind": backend.get("response_kind") if backend else None,
                    "backend_feed_expectation": (
                        backend.get("feed_expectation") if backend else None
                    ),
                    "backend_policy_readback_class": (
                        backend.get("readback_class") if backend else None
                    ),
                    "special_http": special.get("backend_kind")
                    if isinstance(special, dict)
                    else None,
                    "special_http_verified": special_backend_verified,
                },
                "risk": "write" if _is_write_action(route) else "read",
                "reachability": reachability,
                "readback_class": _readback_class(
                    route,
                    status=status,
                    reachability=reachability,
                    manifest=manifest,
                ),
                "test_evidence": evidence,
                "inventory_test_evidence": [PARITY_TEST_EVIDENCE],
                "status": status,
                "baseline_gap": route in baseline_gaps,
                "baseline_gap_resolved": route in baseline_gaps and status == "covered",
                "decision": exemptions.get(route) or baseline_gaps.get(route),
            }
        )

    status_counts = Counter(row["status"] for row in matrix)
    reachability_counts = Counter(
        str((row["reachability"].get("selected") or {}).get("kind") or "none") for row in matrix
    )
    readback_counts = Counter(str(row["readback_class"]) for row in matrix)
    summary = {
        "actions": len(matrix),
        "ui_actions": len(ui_locations),
        "registered_backend_routes": len(backend_routes),
        "service_routes": sum(
            1 for item in backend_routes.values() if item.get("registry") == "service"
        ),
        "operator_routes": sum(
            1 for item in backend_routes.values() if item.get("registry") == "operator"
        ),
        "covered": status_counts["covered"],
        "gaps": status_counts["gap"],
        "intentional_exemptions": status_counts["intentional_exemption"],
        "baseline_gaps": len(baseline_gaps),
        "baseline_gaps_resolved": sum(row["baseline_gap_resolved"] for row in matrix),
        "reachability": dict(sorted(reachability_counts.items())),
        "readback_classes": dict(sorted(readback_counts.items())),
        "parity_complete": status_counts["gap"] == 0,
        "inventory_valid": not issues,
    }
    return {
        "ok": not issues,
        "format": MANIFEST_FORMAT,
        "manifest": manifest_path.relative_to(root).as_posix(),
        "summary": summary,
        "issues": issues,
        "gaps": [row["route"] for row in matrix if row["status"] == "gap"],
        "intentional_exemptions": [
            row["route"] for row in matrix if row["status"] == "intentional_exemption"
        ],
        "matrix": matrix,
    }


def _print_text(payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    print(
        "CRM capability parity inventory: "
        f"actions={summary['actions']} ui={summary['ui_actions']} "
        f"backend={summary['registered_backend_routes']} covered={summary['covered']} "
        f"gaps={summary['gaps']} exemptions={summary['intentional_exemptions']}"
    )
    print(f"Reachability: {json.dumps(summary['reachability'], sort_keys=True)}")
    print(f"Readback: {json.dumps(summary['readback_classes'], sort_keys=True)}")
    if payload["gaps"]:
        print("Current gaps:")
        for route in payload["gaps"]:
            print(f"- {route}")
    if payload["intentional_exemptions"]:
        print("Intentional human-session exemptions:")
        for route in payload["intentional_exemptions"]:
            print(f"- {route}")
    if payload["issues"]:
        print("Inventory validation issues:")
        for issue in payload["issues"]:
            print(f"- {issue['code']}: {issue['action']}: {issue['detail']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and validate the machine-verifiable AutoStop CRM capability matrix."
    )
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--format", choices={"text", "json"}, default="text")
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Also fail while any reviewed parity gap remains open.",
    )
    args = parser.parse_args(argv)
    manifest_path = args.manifest.resolve()
    payload = build_inventory(manifest_path=manifest_path)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        _print_text(payload)
    if not payload["ok"]:
        return 1
    if args.require_complete and payload["summary"]["gaps"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
