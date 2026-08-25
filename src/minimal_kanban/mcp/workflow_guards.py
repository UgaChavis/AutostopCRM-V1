from __future__ import annotations

import base64
import hashlib
from collections.abc import Awaitable, Callable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from .agent_gateway_support import _envelope
from .gateway_contract import FINANCE_VIRTUAL_OPERATIONS, FINANCE_WORKFLOW_OPERATIONS
from .gateway_media import tool_result
from .raw_gateway import (
    request_fingerprint,
    virtual_api_argument_errors,
    virtual_api_name,
    virtual_api_route,
)

FINANCE_READ_OPERATIONS = frozenset(
    {"list_cashboxes", "get_cashbox", "get_cash_journal", "get_repair_order"}
)
LOGICAL_PAYMENT_FIELDS = frozenset(
    "allow_overpayment amount amount_minor attestation_run_id card_id cashbox_id "
    "expected_cashbox_updated_at expected_updated_at note paid_at payment_method".split()
)
FINANCE_VIRTUAL_FIELDS = {
    "/api/delete_cashbox": frozenset(
        "attestation_run_id cashbox_id expected_cashbox_updated_at expected_transaction_ids".split()
    ),
    "/api/reorder_cashboxes": frozenset(
        "before_cashbox_id before_id cashbox_id expected_cashbox_ids target_cashbox_id".split()
    ),
    "/api/create_cashbox_transfer": frozenset(
        "amount amount_minor cashbox_id expected_from_updated_at expected_to_updated_at "
        "from_cashbox_id note target_cashbox_id to_cashbox_id".split()
    ),
    "/api/create_employee_salary_transaction": frozenset(
        "amount amount_minor attestation_run_id cashboxId cashbox_id employee_id "
        "expected_cashbox_updated_at expected_employee_updated_at kind note transaction_kind".split()
    ),
    "/api/create_employee_shift_accrual": frozenset(
        "amount amount_minor attestation_run_id created_at employee_id "
        "expected_employee_updated_at note".split()
    ),
    "/api/cancel_cash_transaction": frozenset(
        "attestation_run_id cancel_reason cashbox_id expected_cashbox_updated_at "
        "expected_related_cashbox_updated_at note reason transaction_id".split()
    ),
    "/api/cancel_last_cash_transaction": frozenset(
        "attestation_run_id cashbox_id expected_cashbox_updated_at transaction_id".split()
    ),
    "/api/finance_audit/apply_safe_fixes": frozenset(
        "attestation_run_id dry_run expected_issue_ids issue_ids".split()
    ),
}


def finance_dry_run_proof(
    operation: str,
    payload: Mapping[str, Any],
    dry_run_idempotency_key: str,
) -> str:
    return request_fingerprint(
        {
            "contract": "finance_workflow_preview_v1",
            "operation": operation,
            "payload": dict(payload),
            "dry_run_idempotency_key": dry_run_idempotency_key,
        }
    )


def _logical_payment_errors(payload: Mapping[str, Any]) -> list[str]:
    errors = [f"arguments.{key}:extra_forbidden" for key in payload.keys() - LOGICAL_PAYMENT_FIELDS]
    errors.extend(
        [
            f"arguments.{field}:required"
            for field in (
                "card_id",
                "cashbox_id",
                "expected_updated_at",
                "expected_cashbox_updated_at",
                "payment_method",
            )
            if not str(payload.get(field) or "").strip()
        ]
    )
    if str(payload.get("payment_method") or "").strip().casefold() not in {
        "cash",
        "cashless",
        "card",
    }:
        errors.append("arguments.payment_method:literal_error")
    amount = payload.get("amount", payload.get("amount_minor"))
    try:
        valid_amount = (
            amount is not None and Decimal(str(amount)).is_finite() and Decimal(str(amount)) > 0
        )
    except (InvalidOperation, ValueError):
        valid_amount = False
    if not valid_amount:
        errors.append("arguments.amount:positive_required")
    return sorted(set(errors))


def finance_payload_errors(
    payload: Mapping[str, Any],
    *,
    tool: Any = None,
    virtual_route: str | None = None,
    logical_payment: bool = False,
) -> list[str]:
    if logical_payment:
        return _logical_payment_errors(payload)
    if virtual_route is not None:
        errors = virtual_api_argument_errors(virtual_route, payload)
        allowed = FINANCE_VIRTUAL_FIELDS.get(virtual_route, frozenset())
        errors.extend(f"arguments.{key}:extra_forbidden" for key in payload.keys() - allowed)
        return sorted(set(errors))[:20]
    argument_model = getattr(getattr(tool, "fn_metadata", None), "arg_model", None)
    if argument_model is None:
        return ["arguments:schema_unavailable"]
    allowed = set(getattr(argument_model, "model_fields", {}))
    extra_errors = [f"arguments.{key}:extra_forbidden" for key in payload.keys() - allowed]
    try:
        argument_model.model_validate(dict(payload))
    except ValidationError as exc:
        extra_errors.extend(
            [
                f"arguments.{'.'.join(str(part) for part in item['loc'])}:{item['type']}"
                for item in exc.errors()
            ]
        )
    return sorted(set(extra_errors))[:20]


def finance_request_error(
    operation: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
    mode: str | None,
    dry_run_proof: str | None,
    dry_run_idempotency_key: str | None,
    *,
    raw_tools: Mapping[str, Any],
) -> tuple[str, list[str]] | None:
    if operation not in FINANCE_WORKFLOW_OPERATIONS:
        return None
    proof = str(dry_run_proof or "").strip()
    dry_run_key = str(dry_run_idempotency_key or "").strip()
    if mode is None:
        return (
            ("finance_preview_fields_require_explicit_mode", []) if proof or dry_run_key else None
        )
    if operation in FINANCE_READ_OPERATIONS:
        return "finance_read_operation_write_mode_not_allowed", []
    logical_payment = operation == "record_repair_order_payment"
    target_tool = (
        virtual_api_name(FINANCE_VIRTUAL_OPERATIONS[operation])
        if operation in FINANCE_VIRTUAL_OPERATIONS
        else operation
    )
    validation_errors = finance_payload_errors(
        payload,
        tool=raw_tools.get(target_tool),
        virtual_route=virtual_api_route(target_tool),
        logical_payment=logical_payment,
    )
    if validation_errors:
        return "finance_payload_schema_validation_failed", validation_errors
    if mode == "dry_run":
        return ("finance_preview_does_not_accept_proof", []) if proof or dry_run_key else None
    if not proof or not dry_run_key:
        return "finance_dry_run_proof_required", []
    if dry_run_key == idempotency_key:
        return "apply_requires_new_idempotency_key", []
    if proof != finance_dry_run_proof(operation, payload, dry_run_key):
        return "finance_dry_run_proof_mismatch", []
    return None


def _valid_id_list(value: Any, *, require_items: bool = False) -> bool:
    return bool(
        isinstance(value, list)
        and (value or not require_items)
        and all(isinstance(item, str) and item.strip() for item in value)
        and len(set(value)) == len(value)
    )


def finance_contract_error(
    operation: str, payload: Mapping[str, Any]
) -> tuple[str, list[str], list[str]] | None:
    card_operations = {"update_repair_order", "set_repair_order_status", "reopen_repair_order"}
    revision_rules = {
        "create_cash_transaction": (
            "expected_updated_at",
            "cashbox_expected_revision_required_reread_exact_cashbox_first",
        ),
        "create_cashbox_transfer": (
            "expected_from_updated_at expected_to_updated_at",
            "cashbox_transfer_expected_revisions_required_reread_exact_cashboxes_first",
        ),
        "record_repair_order_payment": (
            "expected_updated_at expected_cashbox_updated_at",
            "payment_expected_revisions_required_reread_exact_targets_first",
        ),
        "create_employee_salary_transaction": (
            "expected_cashbox_updated_at expected_employee_updated_at",
            "salary_transaction_expected_revisions_required_reread_exact_targets_first",
        ),
        "create_employee_shift_accrual": (
            "expected_employee_updated_at",
            "shift_accrual_expected_employee_revision_required_reread_exact_employee_first",
        ),
        "cancel_cash_transaction": (
            "expected_cashbox_updated_at",
            "cash_cancellation_expected_revision_required_reread_exact_cashbox_first",
        ),
        "cancel_last_cash_transaction": (
            "expected_cashbox_updated_at",
            "cancel_last_cash_transaction_expected_revision_required_reread_exact_cashbox_first",
        ),
    }
    rule = (
        ("expected_updated_at", "expected_updated_at_required_reread_exact_card_first")
        if operation in card_operations
        else revision_rules.get(operation)
    )
    if rule:
        missing = [field for field in rule[0].split() if not str(payload.get(field) or "").strip()]
        if missing:
            return rule[1], missing, ["reread the exact finance targets"]
    if operation in {"create_cashbox", "reorder_cashboxes"} and not _valid_id_list(
        payload.get("expected_cashbox_ids"), require_items=operation == "reorder_cashboxes"
    ):
        warning = (
            "cashbox_snapshot_required_reread_exact_list_first"
            if operation == "create_cashbox"
            else "cashbox_order_snapshot_required_reread_exact_list_first"
        )
        return warning, ["expected_cashbox_ids"], ["list_cashboxes before changing cashboxes"]
    if operation == "apply_finance_audit_safe_fixes":
        missing = [
            field
            for field, require_items in (("expected_issue_ids", False), ("issue_ids", True))
            if not _valid_id_list(payload.get(field), require_items=require_items)
        ]
        if missing:
            return (
                "finance_audit_issue_snapshot_required_reread_exact_audit_first",
                missing,
                ["read api:/api/finance_audit before applying safe fixes"],
            )
    if operation == "delete_cashbox":
        missing = []
        if not str(payload.get("expected_cashbox_updated_at") or "").strip():
            missing.append("expected_cashbox_updated_at")
        if not _valid_id_list(payload.get("expected_transaction_ids")):
            missing.append("expected_transaction_ids")
        if missing:
            return (
                "cashbox_delete_snapshot_required_reread_exact_cashbox_first",
                missing,
                ["get_cashbox before deleting the exact cashbox"],
            )
    return None


def workflow_error_result(
    label: str,
    warning: str,
    *,
    status: str = "blocked",
    summary: dict[str, Any] | None = None,
    next_actions: list[str] | None = None,
) -> Any:
    return tool_result(
        _envelope(
            ok=False,
            status=status,
            summary=summary,
            warnings=[warning],
            next_actions=next_actions,
        ),
        label=label,
    )


def finance_preview_result(
    operation: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "data": {"validated": True},
        "dry_run_proof": finance_dry_run_proof(operation, payload, idempotency_key),
        "dry_run_idempotency_key": idempotency_key,
        "meta": {"domain_handler_executed": False},
    }


def _state_version(result: Mapping[str, Any]) -> int | None:
    summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
    value = summary.get("state_version", result.get("state_version"))
    return value if isinstance(value, int) else None


async def bind_finance_preview(
    operation: str,
    payload: Mapping[str, Any],
    apply_idempotency_key: str,
    dry_run_idempotency_key: str,
    proof: str,
    *,
    start_workflow: Callable[..., Awaitable[tuple[int | None, dict[str, Any], bool]]],
    transition: Callable[..., Awaitable[dict[str, Any]]],
) -> str | None:
    preview_fingerprint = request_fingerprint(
        {"workflow_id": "finance", "operation": operation, "mode": "dry_run", "payload": payload}
    )
    preview_run_id, preview, deduplicated = await start_workflow(
        workflow_id=f"finance:{operation}",
        intent=f"finance_{operation}",
        idempotency_key=dry_run_idempotency_key,
        payload={"operation": operation, "request_fingerprint": preview_fingerprint},
        mode="dry_run",
        dry_run=True,
    )
    if not (preview.get("ok") and deduplicated and preview.get("status") == "completed"):
        if preview_run_id is not None and preview.get("ok") and not deduplicated:
            await transition(
                preview_run_id,
                "failed",
                expected_state_version=_state_version(preview),
                message=f"failed forged preview claim for {operation}",
            )
        return "finance_dry_run_not_completed"

    apply_key_hash = request_fingerprint({"apply_idempotency_key": apply_idempotency_key})
    binding_fingerprint = request_fingerprint(
        {
            "operation": operation,
            "preview_run_id": preview_run_id,
            "proof": proof,
            "apply_key_sha256": apply_key_hash,
        }
    )
    binding_run_id, binding, binding_replay = await start_workflow(
        workflow_id="finance:proof",
        intent="finance_proof_binding",
        idempotency_key=f"finance-proof-{proof}",
        payload={"operation": "bind_preview", "request_fingerprint": binding_fingerprint},
        mode="apply",
    )
    if not binding.get("ok"):
        return "finance_dry_run_proof_already_consumed"
    if binding_replay and binding.get("status") == "completed":
        return None
    if binding_run_id is None or binding.get("status") != "planned":
        return "finance_dry_run_proof_binding_failed"
    executing = await transition(
        binding_run_id,
        "executing",
        expected_state_version=_state_version(binding),
        message="execute finance proof binding",
    )
    if not executing.get("ok"):
        return "finance_dry_run_proof_binding_failed"
    completed = await transition(
        binding_run_id,
        "completed",
        expected_state_version=_state_version(executing),
        message="completed finance proof binding",
        verification={
            "apply_key_sha256": apply_key_hash,
            "passed": True,
            "preview_run_ref": f"run:{preview_run_id}",
            "proof_sha256": request_fingerprint({"proof": proof}),
        },
        summary="finance:proof",
    )
    return (
        None
        if completed.get("ok") and completed.get("status") == "completed"
        else "finance_dry_run_proof_binding_failed"
    )


def _find_mapping(value: Any, key: str, expected: str) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        if str(value.get(key) or "") == expected:
            return value
        for nested in value.values():
            found = _find_mapping(nested, key, expected)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_mapping(nested, key, expected)
            if found is not None:
                return found
    return None


def _snapshot(
    result: Mapping[str, Any], key: str, total_key: str = ""
) -> tuple[list[str] | None, bool]:
    data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    rows = data.get(key)
    if not isinstance(rows, list) or any(
        not isinstance(row, Mapping) or not str(row.get("id") or "").strip() for row in rows
    ):
        return None, False
    ids = [str(row["id"]) for row in rows]
    meta = data.get("meta") if isinstance(data.get("meta"), Mapping) else {}
    if not total_key:
        has_more = meta.get("has_more")
        return ids, has_more is None or has_more is False
    total = meta.get(total_key)
    return ids, bool(
        isinstance(total, int)
        and not isinstance(total, bool)
        and total == len(ids)
        and meta.get("has_more") is False
    )


async def finance_revision_preflight(
    operation: str,
    payload: Mapping[str, Any],
    invoke: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    expected_cashboxes = payload.get("expected_cashbox_ids")
    if isinstance(expected_cashboxes, list):
        result = await invoke("list_cashboxes", {"limit": 1000})
        actual, complete = _snapshot(result, "cashboxes", "total")
        checks["cashbox_snapshot_read_ok"] = bool(result.get("ok"))
        checks["cashbox_snapshot_complete"] = complete
        checks["cashbox_order_exact"] = actual == expected_cashboxes

    card_id = str(payload.get("card_id") or "").strip()
    expected_card = str(payload.get("expected_updated_at") or "").strip()
    if card_id and expected_card:
        result = await invoke("get_repair_order", {"card_id": card_id})
        card = _find_mapping(result, "id", card_id)
        checks["repair_order_read_ok"] = bool(result.get("ok"))
        checks["card_revision_exact"] = str((card or {}).get("updated_at") or "") == expected_card

    cashbox_specs = (
        ("cashbox_id", "expected_cashbox_updated_at"),
        ("from_cashbox_id", "expected_from_updated_at"),
        ("to_cashbox_id", "expected_to_updated_at"),
    )
    if operation == "create_cash_transaction":
        cashbox_specs = (("cashbox_id", "expected_updated_at"),)
    for id_field, revision_field in cashbox_specs:
        cashbox_id = str(payload.get(id_field) or "").strip()
        expected = str(payload.get(revision_field) or "").strip()
        if not cashbox_id or not expected:
            continue
        limit = 5000 if operation == "delete_cashbox" else 1
        result = await invoke("get_cashbox", {"cashbox_id": cashbox_id, "transaction_limit": limit})
        cashbox = _find_mapping(result, "id", cashbox_id)
        checks[f"{id_field}_read_ok"] = bool(result.get("ok"))
        checks[f"{id_field}_revision_exact"] = (
            str((cashbox or {}).get("updated_at") or "") == expected
        )
        if operation == "delete_cashbox":
            actual, complete = _snapshot(result, "transactions", "transactions_total")
            expected_transactions = payload.get("expected_transaction_ids")
            checks["transaction_snapshot_complete"] = complete
            checks["transaction_snapshot_exact"] = actual == expected_transactions

    employee_id = str(payload.get("employee_id") or "").strip()
    expected_employee = str(payload.get("expected_employee_updated_at") or "").strip()
    if employee_id and expected_employee:
        result = await invoke("api:/api/list_employees", {})
        employee = _find_mapping(result, "id", employee_id)
        checks["employee_read_ok"] = bool(result.get("ok"))
        checks["employee_revision_exact"] = (
            str((employee or {}).get("updated_at") or "") == expected_employee
        )

    expected_issues = payload.get("expected_issue_ids")
    if isinstance(expected_issues, list):
        result = await invoke("api:/api/finance_audit", {})
        actual, complete = _snapshot(result, "issues")
        selected = payload.get("issue_ids")
        checks["finance_audit_read_ok"] = bool(result.get("ok"))
        checks["finance_audit_snapshot_complete"] = complete
        checks["finance_audit_snapshot_exact"] = actual == expected_issues
        checks["finance_audit_selection_current"] = bool(
            isinstance(selected, list) and actual is not None and set(selected).issubset(actual)
        )
    return {"passed": all(checks.values()), "checks": checks or {"revision_not_applicable": True}}


def _reference_hashes(value: Any, *, key: str = "") -> list[str]:
    hashes: list[str] = []
    if isinstance(value, Mapping):
        for nested_key, nested in value.items():
            hashes.extend(_reference_hashes(nested, key=str(nested_key)))
    elif isinstance(value, list):
        if key.casefold().endswith("_ids"):
            hashes.extend(request_fingerprint({key: item}) for item in value)
        else:
            for nested in value:
                hashes.extend(_reference_hashes(nested, key=key))
    elif key.casefold().endswith("_id") and value not in {None, ""}:
        hashes.append(request_fingerprint({key: value}))
    return sorted(set(hashes))


def finance_ledger_verification(
    operation: str,
    payload: Mapping[str, Any],
    verification: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = verification.get("evidence")
    return {
        "check_sha256": request_fingerprint({"check": verification.get("check")}),
        "executor_ok": True,
        "passed": bool(verification.get("passed")),
        "payload_sha256": request_fingerprint({"operation": operation, "payload": payload}),
        "readback_present": evidence is not None,
        "readback_sha256": request_fingerprint(evidence) if evidence is not None else "",
        "revision_guarded": any(str(key).startswith("expected_") for key in payload),
        "target_ref_hashes": _reference_hashes(payload),
    }


def _money_decimal(value: Any) -> Decimal | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def invoice_document_guard(result: Mapping[str, Any]) -> dict[str, Any]:
    data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    meta = data.get("meta") if isinstance(data.get("meta"), Mapping) else {}
    documents = meta.get("documents") if isinstance(meta.get("documents"), list) else []
    source = next(
        (
            item.get("document_guard")
            for item in documents
            if isinstance(item, Mapping)
            and item.get("id") == "invoice"
            and isinstance(item.get("document_guard"), Mapping)
        ),
        None,
    )
    if source is None:
        return {}
    encoded = data.get("content_base64") or data.get("pdf_base64")
    content = b""
    try:
        content = base64.b64decode(str(encoded or ""), validate=True)
        attachment_sha256 = hashlib.sha256(content).hexdigest() if content else ""
    except (ValueError, TypeError):
        attachment_sha256 = ""
    guard = {
        key: source.get(key)
        for key in (
            "money_basis",
            "rendered_total",
            "repair_order_total",
            "tax_status",
            "financial_mismatch",
            "tax_mismatch",
            "financial_or_tax_mismatch",
            "mismatch_with_current_repair_order",
        )
    }
    guard["attachment_sha256"] = attachment_sha256
    rendered_total = _money_decimal(guard["rendered_total"])
    repair_order_total = _money_decimal(guard["repair_order_total"])
    typed = bool(
        isinstance(guard["money_basis"], str)
        and guard["money_basis"].strip()
        and rendered_total is not None
        and repair_order_total is not None
        and isinstance(guard["tax_status"], str)
        and guard["tax_status"].strip()
        and all(
            isinstance(guard[key], bool)
            for key in (
                "financial_mismatch",
                "tax_mismatch",
                "financial_or_tax_mismatch",
                "mismatch_with_current_repair_order",
            )
        )
        and guard["financial_mismatch"] == (rendered_total != repair_order_total)
        and guard["financial_or_tax_mismatch"]
        == (guard["financial_mismatch"] or guard["tax_mismatch"])
        and guard["mismatch_with_current_repair_order"] == guard["financial_or_tax_mismatch"]
    )
    guard["qa_passed"] = bool(
        result.get("ok")
        and attachment_sha256
        and str(data.get("mime_type") or "").casefold() == "application/pdf"
        and content.startswith(b"%PDF-")
        and typed
    )
    return guard
