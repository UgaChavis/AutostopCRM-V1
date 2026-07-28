from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from ..deployment_security import is_maintenance_mode, load_agent_gateway_security_policy
from ..models import normalize_actor_name
from ..repair_order import repair_order_payment_method_from_cashbox_name
from .agent_gateway_support import (
    AGENT_GATEWAY_FORMAT,
    AGENT_GATEWAY_TOOL_NAMES,
    DIAGNOSTIC_TOOL_NAMES,
    MAIL_CAPABILITY_NAMES,
    MANAGER_WORKFLOW_TOOL_NAMES,
    PERMANENT_AGENT_GATEWAY_TOOL_NAMES,
    STORE_VIN_PHOTO_PREVIEW_OPERATION,
    WORKFLOW_TERMINAL_STATES,
    _as_dict,
    _compact_object,
    _contains_value,
    _cursor_offset,
    _decimal_text,
    _envelope,
    _error_code,
    _find_mapping,
    _find_value,
    _is_destructive_capability,
    _items_from_data,
    _maintenance_technical_write_allowed,
    _normalize_limit,
    _policy_error,
    _positive_decimal,
    _read_annotations,
    _response_data,
    _selected_fields,
    _slim_card,
    _store_owner_prepare_binding,
    _store_owner_request_error,
    _store_owner_transport_matches_binding,
    _subset_matches,
    _tool_risk,
    _write_annotations,
)
from .agent_gateway_support import (
    _release_smoke_proof as _release_smoke_proof,
)
from .client import BoardApiClient
from .gateway_contract import (
    BOARD_WORKFLOW_OPERATIONS,
    DEFAULT_CARD_FIELDS,
    DOCUMENT_VIRTUAL_OPERATIONS,
    DOCUMENT_WORKFLOW_OPERATIONS,
    FINANCE_VIRTUAL_OPERATIONS,
    FINANCE_WORKFLOW_OPERATIONS,
    INVENTORY_WORKFLOW_OPERATIONS,
)
from .gateway_media import (
    store_vin_photo_image as _store_vin_photo_image,
)
from .gateway_media import tool_result as _tool_result
from .gateway_media import (
    tool_result_with_image as _tool_result_with_image,
)
from .gateway_media import (
    without_binary_content as _without_binary_content,
)
from .oauth_provider import (
    OAUTH_AUDIT_ACTOR_HEADER,
    OAUTH_AUDIT_ASSERTION_HEADER,
    OwnerAccessToken,
    create_oauth_audit_assertion,
)
from .raw_capability_discovery import discovery_phrase, raw_capability_discovery_score
from .raw_gateway import (
    OPTIMISTIC_WRITE_NAMES,
    RAW_API_ROUTES,
    verify_virtual_api_write_readback,
)
from .raw_gateway import (
    request_fingerprint as _request_fingerprint,
)
from .raw_gateway import (
    schema_hash as _schema_hash,
)
from .raw_gateway import (
    virtual_api_name as _virtual_api_name,
)
from .raw_gateway import (
    virtual_api_risk as _virtual_api_risk,
)
from .raw_gateway import (
    virtual_api_route as _virtual_api_route,
)
from .raw_gateway import (
    virtual_api_schema as _virtual_api_schema,
)
from .store_gateway import (
    INTERNAL_ONLY_CAPABILITY_NAMES,
    STORE_MANAGEMENT_CAPABILITY_NAME,
    STORE_MANAGEMENT_OPERATIONS,
    STORE_SEARCH_ENTITIES,
    compatible_arguments,
    internal_only_capability_warning,
    normalized_store_data,
    preflight_store_write,
    reconcile_store_receipt,
    store_action_arguments,
    store_gateway_envelope,
    store_ledger_verification,
    store_reconciliation_envelope,
    validate_store_workflow_request,
    verify_store_operation,
)
from .store_gateway import (
    workflow_state_version as _workflow_state_version,
)
from .web_gateway import (
    WEB_RESEARCH_CAPABILITY_DESCRIPTIONS,
    WEB_RESEARCH_CAPABILITY_NAMES,
    WEB_RESEARCH_CAPABILITY_SCHEMAS,
    create_web_tool_executor,
    invoke_web_research,
    web_research_argument_error,
)

_GATEWAY_ATTESTATION_RUN_RE = re.compile(r"^AST-GWAT-\d{8}T\d{6}Z$")


def _maintenance_release_smoke_headers(
    *,
    technical_allowed: bool,
    virtual_route: str | None,
    revision: str,
    proof: str,
) -> dict[str, str] | None:
    if not technical_allowed or virtual_route is None:
        return None
    return {
        "X-Autostop-Release-Smoke-Revision": str(revision).strip(),
        "X-Autostop-Release-Smoke-Proof": str(proof).strip(),
    }


def register_agent_gateway_v2(
    server: FastMCP,
    board_api: BoardApiClient,
    *,
    connector_identity: Mapping[str, Any],
    agent_bearer_token: str | None = None,
) -> set[str]:
    """Register the compact Codex-first surface and hide raw tools behind discovery."""

    policy = load_agent_gateway_security_policy()
    tool_manager = getattr(server, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", None)
    if not isinstance(tools, dict):
        if policy.production:
            raise RuntimeError(
                "Production Agent Gateway requires a compatible FastMCP tool registry"
            )
        return set()
    if not policy.gateway_enabled:
        if policy.production:
            for name in list(tools):
                if name not in DIAGNOSTIC_TOOL_NAMES:
                    tool_manager.remove_tool(name)
            return set(tools)
        return set()

    raw_tools = dict(tools)
    manager_bootstrap_tool = raw_tools.get("agent_bootstrap")
    if "agent_bootstrap" in tools:
        tool_manager.remove_tool("agent_bootstrap")
    crm_runtime_status_tool = raw_tools.get("get_runtime_status")
    if "get_runtime_status" in tools:
        tool_manager.remove_tool("get_runtime_status")

    def _oauth_audit_actor() -> str:
        """Return the authenticated OAuth owner, never a caller-supplied actor."""

        access_token = get_access_token()
        if not isinstance(access_token, OwnerAccessToken):
            return ""
        subject = normalize_actor_name(access_token.subject, default="")
        if "\r" in subject or "\n" in subject:
            return ""
        return subject

    def _effective_audit_actor() -> str:
        return _oauth_audit_actor() or load_agent_gateway_security_policy().service_identity

    def _oauth_audit_headers(
        *, route: str, payload: dict[str, object], method: str = "POST"
    ) -> dict[str, str]:
        subject = _oauth_audit_actor()
        if not subject:
            return {}
        assertion = create_oauth_audit_assertion(
            subject=subject,
            method=method,
            route=route,
            payload=payload,
        )
        if not assertion:
            return {}
        return {
            OAUTH_AUDIT_ACTOR_HEADER: subject,
            OAUTH_AUDIT_ASSERTION_HEADER: assertion,
        }

    async def _invoke(
        name: str,
        arguments: dict[str, Any],
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if name in WEB_RESEARCH_CAPABILITY_NAMES:

            def run_web_research() -> dict[str, Any]:
                executor = create_web_tool_executor(
                    board_api,
                    actor_name=_effective_audit_actor(),
                )
                return invoke_web_research(executor, name, arguments)

            # Playwright's synchronous API rejects execution inside the MCP
            # asyncio loop.  A worker thread also keeps page rendering from
            # blocking every other Gateway request.
            return await asyncio.to_thread(run_web_research)
        virtual_route = _virtual_api_route(name)
        if virtual_route is not None:
            payload = dict(arguments)
            service_identity = load_agent_gateway_security_policy().service_identity
            payload["source"] = "mcp_agent_gateway_v2"
            payload["actor_name"] = _effective_audit_actor()
            request_headers = {
                "X-Autostop-Agent-Identity": service_identity,
                "X-Autostop-Agent-Token": str(agent_bearer_token or ""),
                **_oauth_audit_headers(route=virtual_route, payload=payload),
                **dict(extra_headers or {}),
            }
            try:
                return _as_dict(
                    board_api._request(
                        virtual_route,
                        payload,
                        method="POST",
                        extra_headers=request_headers,
                    )
                )
            except Exception as exc:  # pragma: no cover - transport integration failure
                return {
                    "ok": False,
                    "error": {
                        "code": "capability_failed",
                        "message": str(exc),
                        "tool": name,
                    },
                }
        tool = raw_tools.get(name)
        if tool is None:
            return {"ok": False, "error": {"code": "capability_not_found", "message": name}}
        try:
            effective_arguments = dict(arguments)
            if _tool_risk(tool) != "read":
                properties = getattr(tool, "parameters", {}).get("properties", {})
                if "actor_name" in properties:
                    effective_arguments["actor_name"] = _effective_audit_actor()
                if "actor" in properties:
                    effective_arguments["actor"] = _effective_audit_actor()
                if "source" in properties:
                    effective_arguments["source"] = "mcp_agent_gateway_v2"
            return _as_dict(await tool.run(effective_arguments, convert_result=False))
        except Exception as exc:  # pragma: no cover - exercised through integration failures
            return {
                "ok": False,
                "error": {"code": "capability_failed", "message": str(exc), "tool": name},
            }

    async def _invoke_store(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if name not in raw_tools:
            return {
                "ok": False,
                "status": "degraded",
                "error": {"code": "store_capability_unavailable", "capability": name},
                "warnings": ["store_capability_unavailable"],
            }
        return await _invoke(name, compatible_arguments(raw_tools, name, arguments))

    async def _read_store_target(entity: str, target_id: str) -> dict[str, Any]:
        return await _invoke_store(
            "store_entity_context",
            {"entity": entity, "entity_id": target_id, "detail": "full"},
        )

    async def _start_idempotent_workflow(
        *,
        workflow_id: str,
        intent: str,
        idempotency_key: str,
        payload: dict[str, Any],
        mode: str | None = None,
        dry_run: bool = False,
        correlation_id: str = "",
        scope_overrides: Mapping[str, Any] | None = None,
        refs_only: bool = False,
    ) -> tuple[int | None, dict[str, Any], bool]:
        if "start_workflow" not in raw_tools:
            return (
                None,
                {
                    "ok": False,
                    "status": "blocked",
                    "warnings": ["durable_workflow_ledger_unavailable"],
                },
                False,
            )
        workflow_scope = {
            "operation": payload.get("operation"),
            "mode": mode,
            "request_fingerprint": payload.get("request_fingerprint"),
        }
        if isinstance(scope_overrides, Mapping):
            workflow_scope.update(scope_overrides)
        start_arguments = {
            "workflow_id": workflow_id,
            "intent": intent,
            "idempotency_key": idempotency_key,
            "actor": _effective_audit_actor(),
            "scope": workflow_scope,
            "dry_run": bool(dry_run),
        }
        if not refs_only:
            start_arguments["query"] = intent
            start_arguments["metadata"] = {
                "gateway": "v2",
                "mode": mode,
                "dry_run": bool(dry_run),
            }
        if correlation_id:
            start_arguments["correlation_id"] = correlation_id
        started = await _invoke("start_workflow", start_arguments)
        run_id = started.get("run_id")
        summary = started.get("summary") if isinstance(started.get("summary"), dict) else {}
        if not isinstance(run_id, int):
            candidate = summary.get("id") or summary.get("run_id")
            run_id = candidate if isinstance(candidate, int) else None
        deduplicated = bool(summary.get("deduplicated"))
        return run_id, started, deduplicated

    async def _transition(
        run_id: int | None,
        status: str,
        *,
        expected_state_version: int | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        if run_id is None or "workflow_transition" not in raw_tools:
            return {
                "ok": False,
                "status": "blocked",
                "warnings": ["durable_workflow_transition_unavailable"],
            }
        arguments = {"run_id": run_id, "status": status, **extra}
        if expected_state_version is not None:
            arguments["expected_state_version"] = expected_state_version
        return await _invoke("workflow_transition", arguments)

    def _deduplicated_workflow_result(
        *,
        label: str,
        operation: str,
        run_id: int | None,
        started: dict[str, Any],
    ) -> CallToolResult:
        prior_status = str(started.get("status") or "planned")
        prior_completed = prior_status == "completed"
        warning = (
            "idempotency_reused_completed_result"
            if prior_completed
            else "prior_idempotent_attempt_failed"
            if prior_status in {"failed", "cancelled"}
            else "idempotent_attempt_requires_status_reconciliation"
        )
        return _tool_result(
            _envelope(
                ok=prior_completed,
                status=prior_status,
                run_id=run_id,
                summary={
                    "workflow_id": label,
                    "operation": operation,
                    "deduplicated": True,
                },
                data=_compact_object(started, item_limit=10),
                verification={
                    "idempotency_reused": True,
                    "prior_terminal_state": prior_status in WORKFLOW_TERMINAL_STATES,
                },
                warnings=[warning],
                next_actions=[]
                if prior_completed
                else [f"workflow_status(run_id={run_id}) before any retry"],
            ),
            label=label,
        )

    async def _reconcile_deduplicated_store_workflow(
        *,
        label: str,
        operation: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        correlation_id: str,
        run_id: int | None,
        started: dict[str, Any],
    ) -> CallToolResult:
        outcome = await reconcile_store_receipt(
            operation,
            payload,
            arguments=store_action_arguments(
                raw_tools,
                operation,
                payload,
                idempotency_key=idempotency_key,
                mode="apply",
                correlation_id=correlation_id,
            ),
            run_id=run_id,
            started=started,
            invoke_action=_invoke_store,
            read_target=_read_store_target,
            transition=_transition,
            state_version=_workflow_state_version,
        )
        envelope = store_reconciliation_envelope(
            outcome,
            label=label,
            operation=operation,
            run_id=run_id,
            envelope_factory=_envelope,
            compact=_compact_object,
        )
        return _tool_result(envelope, label=label)

    async def _record_repair_order_payment(
        payload: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        card_id = str(payload.get("card_id") or "").strip()
        cashbox_id = str(payload.get("cashbox_id") or "").strip()
        expected_updated_at = str(payload.get("expected_updated_at") or "").strip()
        expected_cashbox_updated_at = str(
            payload.get("expected_cashbox_updated_at") or ""
        ).strip()
        attestation_run_id = str(payload.get("attestation_run_id") or "").strip()
        payment_method = str(payload.get("payment_method") or "").strip().casefold()
        amount = _positive_decimal(payload.get("amount"))
        if amount is None and payload.get("amount_minor") is not None:
            minor = _positive_decimal(payload.get("amount_minor"))
            amount = minor / Decimal(100) if minor is not None else None
        missing = [
            name
            for name, value in (
                ("card_id", card_id),
                ("cashbox_id", cashbox_id),
                ("expected_updated_at", expected_updated_at),
                ("expected_cashbox_updated_at", expected_cashbox_updated_at),
                ("payment_method", payment_method),
                ("amount", amount),
            )
            if not value
        ]
        if missing or payment_method not in {"cash", "cashless", "card"}:
            return {
                "ok": False,
                "error": {
                    "code": "payment_preflight_failed",
                    "message": "missing_or_invalid_payment_fields",
                    "fields": missing
                    + (
                        [] if payment_method in {"cash", "cashless", "card"} else ["payment_method"]
                    ),
                },
            }

        order_read = await _invoke("get_repair_order", {"card_id": card_id})
        cashbox_read = await _invoke(
            "get_cashbox", {"cashbox_id": cashbox_id, "transaction_limit": 10}
        )
        if not bool(order_read.get("ok")) or not bool(cashbox_read.get("ok")):
            return {
                "ok": False,
                "error": {
                    "code": "payment_preflight_read_failed",
                    "order_ok": bool(order_read.get("ok")),
                    "cashbox_ok": bool(cashbox_read.get("ok")),
                },
            }
        order_data = order_read.get("data") if isinstance(order_read.get("data"), dict) else {}
        repair_order = (
            order_data.get("repair_order")
            if isinstance(order_data.get("repair_order"), dict)
            else {}
        )
        card = order_data.get("card") if isinstance(order_data.get("card"), dict) else {}
        cashbox_data = (
            cashbox_read.get("data") if isinstance(cashbox_read.get("data"), dict) else {}
        )
        cashbox = (
            cashbox_data.get("cashbox") if isinstance(cashbox_data.get("cashbox"), dict) else {}
        )
        if attestation_run_id and not (
            _GATEWAY_ATTESTATION_RUN_RE.fullmatch(attestation_run_id)
            and str(card.get("title") or "").startswith(attestation_run_id)
            and str(cashbox.get("name") or "").startswith(f"{attestation_run_id}-")
            and str(payload.get("note") or "").startswith(attestation_run_id)
        ):
            return {
                "ok": False,
                "error": {
                    "code": "payment_attestation_scope_invalid",
                    "message": "attestation_card_cashbox_and_note_must_match_the_run",
                },
            }
        resolved_cashbox_method = repair_order_payment_method_from_cashbox_name(
            cashbox.get("name"),
            default=payment_method,
        )
        if resolved_cashbox_method != payment_method:
            return {
                "ok": False,
                "error": {
                    "code": "payment_cashbox_method_mismatch",
                    "message": "select_a_cashbox_matching_the_requested_payment_method",
                    "requested_payment_method": payment_method,
                    "cashbox_payment_method": resolved_cashbox_method,
                },
            }
        current_updated_at = str(card.get("updated_at") or "").strip()
        if not current_updated_at or current_updated_at != expected_updated_at:
            return {
                "ok": False,
                "error": {
                    "code": "payment_revision_conflict",
                    "message": "reread_the_repair_order_before_retry",
                },
            }
        current_cashbox_updated_at = str(cashbox.get("updated_at") or "").strip()
        if (
            not current_cashbox_updated_at
            or current_cashbox_updated_at != expected_cashbox_updated_at
        ):
            return {
                "ok": False,
                "error": {
                    "code": "cashbox_update_conflict",
                    "message": "reread_the_cashbox_before_retry",
                },
            }
        payment_summary = (
            repair_order.get("payment_summary")
            if isinstance(repair_order.get("payment_summary"), dict)
            else {}
        )
        due_key = "noncash_due" if payment_method == "cashless" else "cash_due"
        outstanding = _positive_decimal(payment_summary.get(due_key))
        if outstanding is None:
            return {
                "ok": False,
                "error": {"code": "payment_debt_unavailable", "field": due_key},
            }
        if amount > outstanding and not bool(payload.get("allow_overpayment")):
            return {
                "ok": False,
                "error": {
                    "code": "payment_overpayment_blocked",
                    "amount": _decimal_text(amount),
                    "outstanding": _decimal_text(outstanding),
                },
            }

        existing_payments = repair_order.get("payments")
        payments = (
            [dict(item) for item in existing_payments if isinstance(item, dict)]
            if isinstance(existing_payments, list)
            else []
        )
        payment_id = f"agent-payment-{_request_fingerprint({'key': idempotency_key})[:16]}"
        payment: dict[str, Any] = {
            "id": payment_id,
            "amount": _decimal_text(amount),
            "note": str(payload.get("note") or "").strip(),
            "payment_method": payment_method,
            "cashbox_id": cashbox_id,
        }
        paid_at = str(payload.get("paid_at") or "").strip()
        if paid_at:
            payment["paid_at"] = paid_at
        payments.append(payment)
        write_result = await _invoke(
            "update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {"payments": payments},
                "expected_updated_at": expected_updated_at,
                "expected_cashbox_id": cashbox_id,
                "expected_cashbox_updated_at": expected_cashbox_updated_at,
                "attestation_run_id": attestation_run_id,
                "actor_name": _effective_audit_actor(),
            },
        )
        if not bool(write_result.get("ok")):
            return write_result

        order_readback = await _invoke("get_repair_order", {"card_id": card_id})
        recorded_payment = _find_mapping(order_readback, "id", payment_id)
        transaction_id = str((recorded_payment or {}).get("cash_transaction_id") or "").strip()
        final_cashbox_id = str((recorded_payment or {}).get("cashbox_id") or "").strip()
        final_payment_method = (
            str((recorded_payment or {}).get("payment_method") or "").strip().casefold()
        )
        cashbox_readback = await _invoke(
            "get_cashbox", {"cashbox_id": cashbox_id, "transaction_limit": 50}
        )
        checks = {
            "repair_order_reread": bool(order_readback.get("ok")),
            "cashbox_reread": bool(cashbox_readback.get("ok")),
            "payment_id_present": recorded_payment is not None,
            "amount_exact": str((recorded_payment or {}).get("amount") or "")
            == _decimal_text(amount),
            "payment_method_exact": final_payment_method == payment_method,
            "cashbox_exact": final_cashbox_id == cashbox_id,
            "cash_transaction_linked": bool(transaction_id),
            "cash_journal_entry_present": bool(transaction_id)
            and _contains_value(cashbox_readback, "id", transaction_id),
            "cashbox_revision_changed": str(
                (
                    (
                        cashbox_readback.get("data")
                        if isinstance(cashbox_readback.get("data"), dict)
                        else {}
                    ).get("cashbox")
                    or {}
                ).get("updated_at")
                or ""
            )
            != expected_cashbox_updated_at,
        }
        return {
            "ok": all(checks.values()),
            "executor_applied": True,
            "data": {
                "card_id": card_id,
                "payment_id": payment_id,
                "cash_transaction_id": transaction_id or None,
                "amount": _decimal_text(amount),
                "payment_method": payment_method,
                "cashbox_id": cashbox_id,
                "outstanding_before": _decimal_text(outstanding),
            },
            "verification": checks,
            "error": None if all(checks.values()) else {"code": "payment_readback_failed"},
        }

    async def _verify_operation(
        operation: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        risk: str,
        *,
        extra_headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if risk == "read":
            return {"required": False, "passed": bool(result.get("ok"))}
        if operation == "store_owner_api":
            mode = str(arguments.get("mode") or "").strip().casefold()
            result_status = str(result.get("status") or "").strip().casefold()
            result_meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
            if mode == "apply":
                # The generic owner transport deliberately cannot know how to
                # verify all employee operations. A successful HTTP mutation is
                # therefore only executor evidence; the caller must perform the
                # operation-specific exact reread before closing the ledger.
                readback_required = bool(result_meta.get("readback_required"))
                return {
                    "required": True,
                    # Never accept a transport self-attestation as exact
                    # business-state verification. The generic gateway lacks
                    # the operation-specific target mapping by design.
                    "passed": False,
                    "check": "store_owner_operation_specific_exact_readback",
                    "evidence": {
                        "transport_status": result_status,
                        "write_applied": bool(result_meta.get("write_applied")),
                        "readback_required": readback_required,
                        "outcome_uncertain": bool(result_meta.get("outcome_uncertain")),
                    },
                }
            if mode == "dry_run":
                return {
                    "required": True,
                    "passed": bool(result.get("ok"))
                    and result_status == "planned"
                    and result_meta.get("domain_handler_executed") is False,
                    "check": "store_owner_server_dry_run_receipt",
                    "evidence": {
                        "transport_status": result_status,
                        "domain_handler_executed": result_meta.get("domain_handler_executed"),
                    },
                }
            return {
                "required": True,
                "passed": bool(result.get("ok"))
                and result_status == "completed"
                and not bool(result_meta.get("readback_required")),
                "check": "store_owner_read_response_contract",
            }

        async def invoke_verification_readback(
            name: str, readback_arguments: dict[str, Any]
        ) -> dict[str, Any]:
            return await _invoke(
                name,
                readback_arguments,
                extra_headers=extra_headers,
            )

        virtual_verification = await verify_virtual_api_write_readback(
            operation,
            arguments,
            result,
            invoke_verification_readback,
        )
        if virtual_verification is not None:
            return virtual_verification
        if operation == "create_client":
            result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
            created = (
                result_data.get("client")
                if isinstance(result_data.get("client"), dict)
                else {}
            )
            client_id = str(created.get("id") or "")
            readback = (
                await _invoke("get_client", {"client_id": client_id})
                if client_id
                else {}
            )
            actual = _find_mapping(readback, "id", client_id) if client_id else None
            requested = (
                arguments.get("client")
                if isinstance(arguments.get("client"), dict)
                else {}
            )
            persisted_state = {
                key: created[key]
                for key in (
                    "id",
                    "client_type",
                    "last_name",
                    "first_name",
                    "middle_name",
                    "display_name",
                    "phone",
                    "phones",
                    "email",
                    "emails",
                    "comment",
                    "legal_name",
                    "short_name",
                    "vehicles",
                    "updated_at",
                )
                if key in created
            }
            passed = bool(
                result.get("ok")
                and client_id
                and readback.get("ok")
                and isinstance(actual, dict)
                and _subset_matches(requested, actual)
                and _subset_matches(persisted_state, actual)
            )
            return {
                "required": True,
                "passed": passed,
                "check": "exact_created_client_readback",
                "evidence": {
                    "client_id": client_id,
                    "requested_fields_exact": _subset_matches(requested, actual),
                    "persisted_state_exact": _subset_matches(persisted_state, actual),
                    "readback_ok": bool(readback.get("ok")),
                },
            }
        if operation == "create_card":
            result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
            created = (
                result_data.get("card")
                if isinstance(result_data.get("card"), dict)
                else {}
            )
            card_id = str(created.get("id") or "")
            readback = (
                await _invoke("get_card", {"card_id": card_id})
                if card_id
                else {}
            )
            actual = _find_mapping(readback, "id", card_id) if card_id else None
            requested = {
                key: arguments[key]
                for key in ("title", "vehicle", "description")
                if key in arguments
            }
            persisted_state = {
                key: created[key]
                for key in (
                    "id",
                    "title",
                    "vehicle",
                    "description",
                    "column",
                    "tags",
                    "deadline",
                    "deadline_timestamp",
                    "updated_at",
                )
                if key in created
            }
            passed = bool(
                result.get("ok")
                and card_id
                and readback.get("ok")
                and isinstance(actual, dict)
                and _subset_matches(requested, actual)
                and _subset_matches(persisted_state, actual)
            )
            return {
                "required": True,
                "passed": passed,
                "check": "exact_created_card_readback",
                "evidence": {
                    "card_id": card_id,
                    "requested_fields_exact": _subset_matches(requested, actual),
                    "persisted_state_exact": _subset_matches(persisted_state, actual),
                    "readback_ok": bool(readback.get("ok")),
                },
            }
        if operation == "link_card_to_client":
            result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
            result_card = (
                result_data.get("card")
                if isinstance(result_data.get("card"), dict)
                else {}
            )
            result_client = (
                result_data.get("client")
                if isinstance(result_data.get("client"), dict)
                else {}
            )
            card_id = str(arguments.get("card_id") or "")
            client_id = str(arguments.get("client_id") or "")
            card_readback = (
                await _invoke("get_card", {"card_id": card_id})
                if card_id
                else {}
            )
            client_readback = (
                await _invoke("get_client", {"client_id": client_id})
                if client_id
                else {}
            )
            actual_card = _find_mapping(card_readback, "id", card_id) if card_id else None
            actual_client = (
                _find_mapping(client_readback, "id", client_id)
                if client_id
                else None
            )
            result_vehicle_id = str(
                result_card.get("client_vehicle_id")
                or (result_data.get("meta") or {}).get("client_vehicle_id")
                or ""
            )
            requested_vehicle_id = str(arguments.get("client_vehicle_id") or "")
            if requested_vehicle_id:
                vehicle_exact = (
                    str((actual_card or {}).get("client_vehicle_id") or "")
                    == requested_vehicle_id
                )
            elif arguments.get("create_vehicle_from_card") is True:
                vehicle_exact = bool(
                    result_vehicle_id
                    and str((actual_card or {}).get("client_vehicle_id") or "")
                    == result_vehicle_id
                    and _contains_value(actual_client, "id", result_vehicle_id)
                )
            else:
                vehicle_exact = True
            card_state = {
                key: result_card[key]
                for key in (
                    "id",
                    "client_id",
                    "client_vehicle_id",
                    "updated_at",
                )
                if key in result_card
            }
            client_state = {
                key: result_client[key]
                for key in ("id", "updated_at", "vehicles")
                if key in result_client
            }
            passed = bool(
                result.get("ok")
                and card_id
                and client_id
                and card_readback.get("ok")
                and client_readback.get("ok")
                and isinstance(actual_card, dict)
                and isinstance(actual_client, dict)
                and str(actual_card.get("client_id") or "") == client_id
                and _subset_matches(card_state, actual_card)
                and _subset_matches(client_state, actual_client)
                and vehicle_exact
            )
            return {
                "required": True,
                "passed": passed,
                "check": "exact_card_client_link_readback",
                "evidence": {
                    "card_id": card_id,
                    "client_id": client_id,
                    "card_link_exact": str((actual_card or {}).get("client_id") or "")
                    == client_id,
                    "card_state_exact": _subset_matches(card_state, actual_card),
                    "client_state_exact": _subset_matches(client_state, actual_client),
                    "vehicle_link_exact": vehicle_exact,
                    "readback_ok": bool(
                        card_readback.get("ok") and client_readback.get("ok")
                    ),
                },
            }
        if operation == "record_repair_order_payment":
            checks = (
                result.get("verification") if isinstance(result.get("verification"), dict) else {}
            )
            return {
                "required": True,
                "passed": bool(result.get("ok")) and all(bool(value) for value in checks.values()),
                "check": "repair_order_payment_and_cash_journal_readback",
                "evidence": checks,
            }
        read_tool = ""
        read_arguments: dict[str, Any] = {}
        if operation in {"create_cash_transaction", "create_cashbox"}:
            cashbox_id = arguments.get("cashbox_id") or _find_value(
                result, frozenset({"cashbox_id"})
            )
            if operation == "create_cashbox":
                result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
                created_cashbox = (
                    result_data.get("cashbox")
                    if isinstance(result_data.get("cashbox"), dict)
                    else {}
                )
                cashbox_id = created_cashbox.get("id") or cashbox_id
            if cashbox_id:
                read_tool = "get_cashbox"
                read_arguments = {"cashbox_id": str(cashbox_id), "transaction_limit": 10}
        elif operation in {"update_repair_order", "set_repair_order_status"}:
            card_id = arguments.get("card_id") or _find_value(result, frozenset({"card_id"}))
            if card_id:
                read_tool = "get_repair_order"
                read_arguments = {"card_id": str(card_id)}
        elif operation in {
            "save_inventory_item",
            "replenish_inventory_item",
            "write_off_inventory_item",
            "return_inventory_movement",
        }:
            item_id = arguments.get("item_id") or _find_value(result, frozenset({"item_id"}))
            if item_id:
                read_tool = "get_inventory_item"
                read_arguments = {"item_id": str(item_id)}
        elif operation == "upload_shared_file":
            file_id = arguments.get("file_id") or _find_value(result, frozenset({"file_id", "id"}))
            if file_id:
                read_tool = "get_shared_file_info"
                read_arguments = {"file_id": str(file_id)}
        elif operation == "delete_shared_file":
            file_id = arguments.get("file_id")
            if file_id:
                readback = await _invoke("get_shared_file_info", {"file_id": str(file_id)})
                return {
                    "required": True,
                    "passed": not bool(readback.get("ok"))
                    and _error_code(readback) in {"not_found", "shared_file_not_found"},
                    "check": "get_shared_file_info_not_found",
                    "evidence": _compact_object(readback, item_limit=5),
                }
        elif operation == "delete_cashbox":
            cashbox_id = arguments.get("cashbox_id")
            if cashbox_id:
                readback = await _invoke(
                    "get_cashbox", {"cashbox_id": str(cashbox_id), "transaction_limit": 1}
                )
                return {
                    "required": True,
                    "passed": not bool(readback.get("ok"))
                    and _error_code(readback) in {"not_found", "cashbox_not_found"},
                    "check": "get_cashbox_not_found",
                    "evidence": _compact_object(readback, item_limit=5),
                }
        elif operation in {"create_document_without_card_pdf", "download_repair_order_print_pdf"}:
            has_document = bool(
                _find_value(
                    result,
                    frozenset(
                        {
                            "content_base64",
                            "pdf_base64",
                            "content_bytes",
                            "file_name",
                            "mime_type",
                        }
                    ),
                )
            )
            return {
                "required": True,
                "passed": bool(result.get("ok")) and has_document,
                "check": "document_artifact_present",
            }
        elif operation.startswith("bulk_") or operation in BOARD_WORKFLOW_OPERATIONS:
            backend_verification = _find_value(result, frozenset({"verification"}))
            if isinstance(backend_verification, dict):
                return {
                    "required": True,
                    "passed": bool(backend_verification.get("passed", result.get("ok"))),
                    "check": "backend_manager_verification",
                    "evidence": _compact_object(backend_verification),
                }
        if not read_tool:
            return {
                "required": True,
                "passed": bool(result.get("ok")),
                "check": "executor_contract_only",
                "warning": "focused_readback_not_available",
            }
        readback = await _invoke(read_tool, read_arguments)
        passed = bool(readback.get("ok"))
        if passed and operation == "create_cash_transaction":
            result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
            transaction = (
                result_data.get("transaction")
                if isinstance(result_data.get("transaction"), dict)
                else {}
            )
            transaction_id = transaction.get("id")
            passed = bool(transaction_id) and _contains_value(readback, "id", transaction_id)
        elif passed and operation == "create_cashbox":
            passed = bool(read_arguments.get("cashbox_id")) and _contains_value(
                readback, "id", read_arguments.get("cashbox_id")
            )
        elif passed and operation == "update_repair_order":
            read_data = readback.get("data") if isinstance(readback.get("data"), dict) else {}
            actual_order = read_data.get("repair_order", read_data)
            passed = _subset_matches(arguments.get("repair_order") or {}, actual_order)
        elif passed and operation == "set_repair_order_status":
            passed = _contains_value(readback, "status", arguments.get("status"))
        elif passed and operation == "upload_shared_file":
            passed = bool(read_arguments.get("file_id")) and _contains_value(
                readback, "id", read_arguments.get("file_id")
            )
        elif passed and operation in {
            "save_inventory_item",
            "replenish_inventory_item",
            "write_off_inventory_item",
            "return_inventory_movement",
        }:
            passed = bool(read_arguments.get("item_id")) and _contains_value(
                readback, "id", read_arguments.get("item_id")
            )
        return {
            "required": True,
            "passed": passed,
            "check": read_tool,
            "evidence": _compact_object(readback, item_limit=10),
        }

    async def _execute_workflow(
        *,
        workflow_id: str,
        operation: str,
        payload: dict[str, Any],
        idempotency_key: str,
        allowed: frozenset[str],
        mode: str | None = None,
        allow_large_output: bool = False,
    ) -> CallToolResult:
        if is_maintenance_mode():
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=["maintenance_mode_domain_writes_blocked"],
                ),
                label=workflow_id,
            )
        if operation not in allowed:
            return _tool_result(
                _envelope(
                    ok=False,
                    status="failed",
                    warnings=["operation_not_allowed_for_workflow"],
                    summary={"workflow_id": workflow_id, "operation": operation},
                ),
                label=workflow_id,
            )
        if not idempotency_key:
            return _tool_result(
                _envelope(ok=False, status="failed", warnings=["idempotency_key_required"]),
                label=workflow_id,
            )
        is_store_vin_photo_preview = operation == STORE_VIN_PHOTO_PREVIEW_OPERATION
        if is_store_vin_photo_preview and not allow_large_output:
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    summary={"workflow_id": workflow_id, "operation": operation},
                    warnings=["allow_large_output_required_for_store_vin_photo"],
                ),
                label=workflow_id,
            )
        store_operation = workflow_id == "inventory" and operation in STORE_MANAGEMENT_OPERATIONS
        store_preflight: dict[str, Any] = {}
        store_correlation = ""
        if store_operation:
            request_validation = validate_store_workflow_request(
                operation,
                payload,
                idempotency_key=idempotency_key,
                mode=mode,
            )
            if not request_validation["passed"]:
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        summary={
                            "workflow_id": workflow_id,
                            "operation": operation,
                            "missing_fields": request_validation.get("missing_fields", []),
                        },
                        warnings=[str(request_validation["warning"])],
                        next_actions=["agent_entity_context for the exact store target"]
                        if request_validation.get("missing_fields")
                        else [],
                    ),
                    label=workflow_id,
                )
            store_correlation = str(request_validation["correlation_id"])
        if (
            operation in {"update_repair_order", "set_repair_order_status"}
            and not str(payload.get("expected_updated_at") or "").strip()
        ):
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=["expected_updated_at_required_reread_exact_card_first"],
                    summary={"workflow_id": workflow_id, "operation": operation},
                    next_actions=["agent_entity_context for the exact repair order"],
                ),
                label=workflow_id,
            )
        if workflow_id == "finance" and operation == "create_cashbox":
            expected_cashbox_ids = payload.get("expected_cashbox_ids")
            if (
                not isinstance(expected_cashbox_ids, list)
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in expected_cashbox_ids
                )
                or len(set(expected_cashbox_ids)) != len(expected_cashbox_ids)
            ):
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=[
                            "cashbox_snapshot_required_reread_exact_list_first"
                        ],
                        summary={
                            "workflow_id": workflow_id,
                            "operation": operation,
                            "missing_fields": ["expected_cashbox_ids"],
                        },
                        next_actions=["list_cashboxes before creating a cashbox"],
                    ),
                    label=workflow_id,
                )
        if (
            workflow_id == "finance"
            and operation == "create_cash_transaction"
            and not str(payload.get("expected_updated_at") or "").strip()
        ):
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=[
                        "cashbox_expected_revision_required_reread_exact_cashbox_first"
                    ],
                    summary={
                        "workflow_id": workflow_id,
                        "operation": operation,
                        "missing_fields": ["expected_updated_at"],
                    },
                    next_actions=["agent_entity_context for the exact cashbox"],
                ),
                label=workflow_id,
            )
        if workflow_id == "finance" and operation == "create_cashbox_transfer":
            missing_revisions = [
                field
                for field in (
                    "expected_from_updated_at",
                    "expected_to_updated_at",
                )
                if not str(payload.get(field) or "").strip()
            ]
            if missing_revisions:
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=[
                            "cashbox_transfer_expected_revisions_required_reread_exact_cashboxes_first"
                        ],
                        summary={
                            "workflow_id": workflow_id,
                            "operation": operation,
                            "missing_fields": missing_revisions,
                        },
                        next_actions=[
                            "agent_entity_context for both exact cashboxes"
                        ],
                    ),
                    label=workflow_id,
                )
        if workflow_id == "finance" and operation == "record_repair_order_payment":
            missing_revisions = [
                field
                for field in (
                    "expected_updated_at",
                    "expected_cashbox_updated_at",
                )
                if not str(payload.get(field) or "").strip()
            ]
            if missing_revisions:
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=[
                            "payment_expected_revisions_required_reread_exact_targets_first"
                        ],
                        summary={
                            "workflow_id": workflow_id,
                            "operation": operation,
                            "missing_fields": missing_revisions,
                        },
                        next_actions=[
                            "agent_entity_context for the exact repair order and cashbox"
                        ],
                    ),
                    label=workflow_id,
                )
        if workflow_id == "finance" and operation == "reorder_cashboxes":
            expected_cashbox_ids = payload.get("expected_cashbox_ids")
            if (
                not isinstance(expected_cashbox_ids, list)
                or not expected_cashbox_ids
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in expected_cashbox_ids
                )
                or len(set(expected_cashbox_ids)) != len(expected_cashbox_ids)
            ):
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=[
                            "cashbox_order_snapshot_required_reread_exact_list_first"
                        ],
                        summary={
                            "workflow_id": workflow_id,
                            "operation": operation,
                            "missing_fields": ["expected_cashbox_ids"],
                        },
                        next_actions=["list_cashboxes before changing cashbox order"],
                    ),
                    label=workflow_id,
                )
        if workflow_id == "finance" and operation == "create_employee_salary_transaction":
            missing_revisions = [
                field
                for field in (
                    "expected_cashbox_updated_at",
                    "expected_employee_updated_at",
                )
                if not str(payload.get(field) or "").strip()
            ]
            if missing_revisions:
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=[
                            "salary_transaction_expected_revisions_required_reread_exact_targets_first"
                        ],
                        summary={
                            "workflow_id": workflow_id,
                            "operation": operation,
                            "missing_fields": missing_revisions,
                        },
                        next_actions=["get_cashbox and list_employees for the exact targets"],
                    ),
                    label=workflow_id,
                )
        if (
            workflow_id == "finance"
            and operation == "create_employee_shift_accrual"
            and not str(payload.get("expected_employee_updated_at") or "").strip()
        ):
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=[
                        "shift_accrual_expected_employee_revision_required_reread_exact_employee_first"
                    ],
                    summary={
                        "workflow_id": workflow_id,
                        "operation": operation,
                        "missing_fields": ["expected_employee_updated_at"],
                    },
                    next_actions=["list_employees for the exact employee"],
                ),
                label=workflow_id,
            )
        if (
            workflow_id == "finance"
            and operation in {
                "cancel_cash_transaction",
                "cancel_last_cash_transaction",
            }
            and not str(payload.get("expected_cashbox_updated_at") or "").strip()
        ):
            warning = (
                "cash_cancellation_expected_revision_required_reread_exact_cashbox_first"
                if operation == "cancel_cash_transaction"
                else "cancel_last_cash_transaction_expected_revision_required_reread_exact_cashbox_first"
            )
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=[warning],
                    summary={
                        "workflow_id": workflow_id,
                        "operation": operation,
                        "missing_fields": ["expected_cashbox_updated_at"],
                    },
                    next_actions=["get_cashbox for the exact transaction and cashbox"],
                ),
                label=workflow_id,
            )
        if workflow_id == "finance" and operation == "apply_finance_audit_safe_fixes":
            expected_issue_ids = payload.get("expected_issue_ids")
            issue_ids = payload.get("issue_ids")
            missing_fields = []
            if (
                not isinstance(expected_issue_ids, list)
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in expected_issue_ids
                )
                or len(set(expected_issue_ids)) != len(expected_issue_ids)
            ):
                missing_fields.append("expected_issue_ids")
            if (
                not isinstance(issue_ids, list)
                or not issue_ids
                or any(not isinstance(item, str) or not item.strip() for item in issue_ids)
                or len(set(issue_ids)) != len(issue_ids)
            ):
                missing_fields.append("issue_ids")
            if missing_fields:
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=[
                            "finance_audit_issue_snapshot_required_reread_exact_audit_first"
                        ],
                        summary={
                            "workflow_id": workflow_id,
                            "operation": operation,
                            "missing_fields": missing_fields,
                        },
                        next_actions=["read api:/api/finance_audit before applying safe fixes"],
                    ),
                    label=workflow_id,
                )
        if workflow_id == "finance" and operation == "delete_cashbox":
            missing_fields = [
                field
                for field in (
                    "expected_cashbox_updated_at",
                    "expected_transaction_ids",
                )
                if (
                    not str(payload.get(field) or "").strip()
                    if field == "expected_cashbox_updated_at"
                    else not isinstance(payload.get(field), list)
                )
            ]
            if missing_fields:
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=[
                            "cashbox_delete_snapshot_required_reread_exact_cashbox_first"
                        ],
                        summary={
                            "workflow_id": workflow_id,
                            "operation": operation,
                            "missing_fields": missing_fields,
                        },
                        next_actions=["get_cashbox before deleting the exact cashbox"],
                    ),
                    label=workflow_id,
                )
        if workflow_id == "inventory" and operation in {
            "save_inventory_item",
            "replenish_inventory_item",
            "write_off_inventory_item",
            "return_inventory_movement",
        }:
            missing_revisions: list[str] = []
            item_revision_required = operation != "save_inventory_item" or bool(
                str(payload.get("item_id") or "").strip()
            )
            if item_revision_required and not str(
                payload.get("expected_updated_at") or ""
            ).strip():
                missing_revisions.append("expected_updated_at")
            if operation in {"write_off_inventory_item", "return_inventory_movement"}:
                if not str(payload.get("card_id") or "").strip():
                    missing_revisions.append("card_id")
                if not str(payload.get("expected_card_updated_at") or "").strip():
                    missing_revisions.append("expected_card_updated_at")
            if missing_revisions:
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=[
                            "inventory_expected_revisions_required_reread_exact_targets_first"
                        ],
                        summary={
                            "workflow_id": workflow_id,
                            "operation": operation,
                            "missing_fields": missing_revisions,
                        },
                        next_actions=[
                            "get_inventory_item and agent_entity_context for the exact targets"
                        ],
                    ),
                    label=workflow_id,
                )
        if (
            workflow_id == "document"
            and operation == "delete_shared_file"
            and not str(payload.get("expected_updated_at") or "").strip()
        ):
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=[
                        "shared_file_expected_revision_required_reread_exact_file_first"
                    ],
                    summary={
                        "workflow_id": workflow_id,
                        "operation": operation,
                        "missing_fields": ["expected_updated_at"],
                    },
                    next_actions=["get_shared_file_info for the exact file"],
                ),
                label=workflow_id,
            )
        logical_payment = workflow_id == "finance" and operation == "record_repair_order_payment"
        if store_operation:
            target_tool = STORE_MANAGEMENT_CAPABILITY_NAME
        elif workflow_id == "board":
            target_tool = "run_manager_operation"
        elif workflow_id == "finance" and operation in FINANCE_VIRTUAL_OPERATIONS:
            target_tool = _virtual_api_name(FINANCE_VIRTUAL_OPERATIONS[operation])
        elif workflow_id == "document" and operation in DOCUMENT_VIRTUAL_OPERATIONS:
            target_tool = _virtual_api_name(DOCUMENT_VIRTUAL_OPERATIONS[operation])
        else:
            target_tool = operation
        tool = raw_tools.get(target_tool)
        virtual_route = _virtual_api_route(target_tool)
        if tool is None and virtual_route is None and not logical_payment:
            return _tool_result(
                _envelope(ok=False, status="failed", warnings=["executor_capability_missing"]),
                label=workflow_id,
            )
        risk = (
            "write"
            if logical_payment or store_operation
            else "destructive"
            if virtual_route is not None and _is_destructive_capability(target_tool, "write")
            else "write"
            if virtual_route is not None
            else _tool_risk(tool)
        )
        policy_error = _policy_error(tool_name=operation, risk=risk, arguments=payload)
        if policy_error:
            return _tool_result(
                _envelope(ok=False, status="blocked", warnings=[policy_error]), label=workflow_id
            )
        request_fingerprint = _request_fingerprint(
            {"workflow_id": workflow_id, "operation": operation, "mode": mode, "payload": payload}
        )
        run_id, started, deduplicated = await _start_idempotent_workflow(
            workflow_id=f"{workflow_id}:{operation}",
            intent=f"{workflow_id}_{operation}",
            idempotency_key=idempotency_key,
            payload={
                "operation": operation,
                "request_fingerprint": request_fingerprint,
            },
            mode=mode,
            dry_run=mode == "dry_run",
            correlation_id=store_correlation if store_operation else "",
            scope_overrides={"domain": "store", "source": "store"} if store_operation else None,
            refs_only=store_operation,
        )
        if started and not bool(started.get("ok")):
            return _tool_result(
                _envelope(
                    ok=False,
                    status="failed",
                    run_id=run_id,
                    warnings=list(started.get("warnings") or ["workflow_start_failed"]),
                    summary={"workflow_id": workflow_id, "operation": operation},
                ),
                label=workflow_id,
            )
        if deduplicated:
            if (
                store_operation
                and str(mode) == "apply"
                and str(started.get("status") or "") == "compensating"
            ):
                return await _reconcile_deduplicated_store_workflow(
                    label=workflow_id,
                    operation=operation,
                    payload=payload,
                    idempotency_key=idempotency_key,
                    correlation_id=store_correlation,
                    run_id=run_id,
                    started=started,
                )
            return _deduplicated_workflow_result(
                label=workflow_id,
                operation=operation,
                run_id=run_id,
                started=started,
            )
        if run_id is None:
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=["durable_workflow_run_id_unavailable"],
                ),
                label=workflow_id,
            )
        if store_operation:
            _context, store_preflight = await preflight_store_write(
                operation, payload, _read_store_target
            )
            if not bool(store_preflight.get("passed")):
                preflight_checks = (
                    store_preflight.get("checks")
                    if isinstance(store_preflight.get("checks"), Mapping)
                    else {}
                )
                failed = await _transition(
                    run_id,
                    "failed",
                    expected_state_version=_workflow_state_version(started),
                    message=f"failed {operation}",
                    verification={
                        "executor_ok": False,
                        "verification_passed": False,
                        "context_read_ok": bool(preflight_checks.get("context_read_ok")),
                        "target_id_exact": bool(preflight_checks.get("target_id_exact")),
                        "revision_exact": bool(preflight_checks.get("expected_updated_at_exact")),
                    },
                )
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        run_id=run_id,
                        summary={
                            "workflow_id": workflow_id,
                            "operation": operation,
                            "entity": store_preflight.get("entity"),
                            "target_id": str(payload.get("target_id") or "").strip(),
                        },
                        verification={
                            "required": True,
                            "passed": False,
                            "ledger_closed": bool(failed.get("ok")),
                            "check": "store_entity_context_preflight",
                            "evidence": _compact_object(store_preflight, item_limit=10),
                        },
                        warnings=["store_preflight_exact_target_or_revision_failed"],
                        next_actions=[
                            "reread the exact store target and rebuild the action contract"
                        ],
                    ),
                    label=workflow_id,
                )
        executing = await _transition(
            run_id,
            "executing",
            expected_state_version=_workflow_state_version(started),
            message=f"execute {operation}",
        )
        if not bool(executing.get("ok")):
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    run_id=run_id,
                    data=_compact_object(executing),
                    warnings=["workflow_enter_executing_failed"],
                ),
                label=workflow_id,
            )
        arguments = dict(payload)
        if store_operation:
            arguments = store_action_arguments(
                raw_tools,
                operation,
                payload,
                idempotency_key=idempotency_key,
                mode=str(mode),
                correlation_id=store_correlation,
            )
        elif workflow_id == "board":
            board_payload = dict(payload)
            board_payload["actor_name"] = _effective_audit_actor()
            arguments = {
                "operation": operation,
                "payload": board_payload,
                "mode": mode or str(payload.get("mode") or "dry_run"),
                "actor_name": _effective_audit_actor(),
            }
        elif workflow_id == "document" and operation in DOCUMENT_VIRTUAL_OPERATIONS:
            arguments["actor_name"] = _effective_audit_actor()
            arguments["source"] = "mcp_agent_gateway_v2"
            arguments["dry_run"] = mode == "dry_run"
        elif (
            risk != "read"
            and tool is not None
            and "actor_name" in getattr(tool, "parameters", {}).get("properties", {})
        ):
            arguments["actor_name"] = _effective_audit_actor()
        result = (
            await _record_repair_order_payment(arguments, idempotency_key=idempotency_key)
            if logical_payment
            else await _invoke(target_tool, arguments)
        )
        image_base64 = ""
        image_mime_type = ""
        if is_store_vin_photo_preview and bool(result.get("ok")):
            try:
                image_base64, image_mime_type = _store_vin_photo_image(result)
            except ValueError as exc:
                result = {
                    "ok": False,
                    "error": {"code": str(exc)},
                    "warnings": [str(exc)],
                }
        verification = (
            await verify_store_operation(
                operation,
                payload,
                result,
                mode=str(mode),
                preflight=store_preflight,
                read_target=_read_store_target,
            )
            if store_operation
            else await _verify_operation(operation, arguments, result, risk)
        )
        executor_ok = bool(result.get("ok")) or bool(
            _find_value(
                result,
                frozenset({"executor_applied", "applied", "write_applied_unverified"}),
            )
        )
        verification_passed = bool(verification.get("passed"))
        result_ok = executor_ok and verification_passed
        ledger_closed = False
        ledger_error: dict[str, Any] | None = None
        workflow_status = "failed"
        executing_version = _workflow_state_version(executing)
        if result_ok:
            verifying = await _transition(
                run_id,
                "verifying",
                expected_state_version=executing_version,
                message=f"verify {operation}",
            )
            if bool(verifying.get("ok")):
                completed = await _transition(
                    run_id,
                    "completed",
                    expected_state_version=_workflow_state_version(verifying),
                    message=f"completed {operation}",
                    verification=(
                        store_ledger_verification(verification, executor_ok=True)
                        if store_operation
                        else {"executor_ok": True, **verification}
                    ),
                    summary=f"{workflow_id}:{operation}",
                )
                ledger_closed = (
                    bool(completed.get("ok")) and str(completed.get("status")) == "completed"
                )
                workflow_status = "completed" if ledger_closed else "verifying"
                if not ledger_closed:
                    ledger_error = completed
            else:
                ledger_error = verifying
                compensation = await _transition(
                    run_id,
                    "compensating",
                    expected_state_version=executing_version,
                    message=f"ledger close reconciliation required for {operation}",
                )
                if bool(compensation.get("ok")):
                    workflow_status = "compensating"
                else:
                    ledger_error = {"verifying": verifying, "compensating": compensation}
        elif executor_ok:
            compensation = await _transition(
                run_id,
                "compensating",
                expected_state_version=executing_version,
                message=f"verification failed after executor applied {operation}",
                verification=(
                    store_ledger_verification(verification, executor_ok=True)
                    if store_operation
                    else {"executor_ok": True, **verification}
                ),
            )
            workflow_status = "compensating" if bool(compensation.get("ok")) else "executing"
            if not bool(compensation.get("ok")):
                ledger_error = compensation
        else:
            failed = await _transition(
                run_id,
                "failed",
                expected_state_version=executing_version,
                message=f"failed {operation}",
            )
            ledger_closed = bool(failed.get("ok")) and str(failed.get("status")) == "failed"
            workflow_status = "failed"
            if not ledger_closed:
                ledger_error = failed
        overall_ok = result_ok and ledger_closed
        result_data = normalized_store_data(result) if store_operation else result
        binary_document_operation = workflow_id == "document" and operation in {
            "create_document_without_card_pdf",
            "download_repair_order_print_pdf",
            "download_shared_file",
        }
        safe_result = (
            _without_binary_content(result_data)
            if is_store_vin_photo_preview
            or (binary_document_operation and not allow_large_output)
            else result_data
            if allow_large_output
            else _compact_object(result_data)
        )
        source_warnings = (
            [str(item) for item in result.get("warnings") or [] if str(item).strip()]
            if store_operation
            else []
        )
        workflow_warnings = (
            []
            if overall_ok
            else ["verification_failed_compensation_required"]
            if executor_ok and not verification_passed
            else ["workflow_ledger_close_failed"]
            if result_ok
            else ["executor_failed"]
        )
        payload = _envelope(
            ok=overall_ok,
            status=workflow_status,
            run_id=run_id,
            summary={
                "workflow_id": workflow_id,
                "operation": operation,
                "mode": mode or "apply",
                "executor": target_tool,
                "risk": risk,
            },
            data=safe_result,
            verification={
                "executor_ok": executor_ok,
                "ledger_closed": ledger_closed,
                **verification,
            },
            warnings=[*source_warnings, *workflow_warnings],
            next_actions=[]
            if overall_ok
            else [f"workflow_status(run_id={run_id}) and reconcile exact target"],
            meta={
                "mode": mode or "apply",
                "dry_run": mode == "dry_run",
                "ledger_owned_by_named_workflow": True,
                "ledger_error": _compact_object(ledger_error) if ledger_error else None,
            },
        )
        if overall_ok and is_store_vin_photo_preview:
            return _tool_result_with_image(
                payload,
                label=workflow_id,
                image_base64=image_base64,
                mime_type=image_mime_type,
            )
        return _tool_result(payload, label=workflow_id)

    async def _guarded_ledger_call(name: str, arguments: dict[str, Any]) -> CallToolResult:
        if is_maintenance_mode():
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=["maintenance_mode_workflow_ledger_write_blocked"],
                ),
                label=name,
            )
        return _tool_result(await _invoke(name, arguments), label=name)

    for guarded_ledger_tool in (
        "start_workflow",
        "workflow_transition",
        "workflow_checkpoint",
        "workflow_wait_for_external",
        "complete_external_step",
        "workflow_resume",
        "workflow_cancel",
    ):
        if guarded_ledger_tool in tools:
            tool_manager.remove_tool(guarded_ledger_tool)

    @server.tool(name="start_workflow", annotations=_write_annotations("Start Workflow"))
    async def guarded_start_workflow(
        workflow_id: str,
        intent: str,
        idempotency_key: str,
        query: str = "",
        request_id: str = "",
        correlation_id: str = "",
        actor: str = "codex-owner-agent",
        scope: dict[str, Any] | None = None,
        selected_ids: list[str] | None = None,
        dry_run: bool = False,
        source: str = "codex",
        metadata: dict[str, Any] | None = None,
    ) -> CallToolResult:
        return await _guarded_ledger_call("start_workflow", locals())

    @server.tool(name="workflow_transition", annotations=_write_annotations("Workflow Transition"))
    async def guarded_workflow_transition(
        run_id: int,
        status: str,
        message: str = "",
        verification: dict[str, Any] | None = None,
        summary: str = "",
        expected_state_version: int | None = None,
    ) -> CallToolResult:
        return await _guarded_ledger_call("workflow_transition", locals())

    @server.tool(name="workflow_checkpoint", annotations=_write_annotations("Workflow Checkpoint"))
    async def guarded_workflow_checkpoint(
        run_id: int,
        checkpoint: dict[str, Any],
        selected_ids: list[str] | None = None,
        message: str = "",
        expected_state_version: int | None = None,
    ) -> CallToolResult:
        return await _guarded_ledger_call("workflow_checkpoint", locals())

    @server.tool(
        name="workflow_wait_for_external",
        annotations=_write_annotations("Workflow External Wait"),
    )
    async def guarded_workflow_wait_for_external(
        run_id: int,
        step_id: str,
        connector: str,
        action: str,
        request_refs: dict[str, Any] | None = None,
        expected_state_version: int | None = None,
    ) -> CallToolResult:
        return await _guarded_ledger_call("workflow_wait_for_external", locals())

    @server.tool(
        name="complete_external_step",
        annotations=_write_annotations("Complete External Step"),
    )
    async def guarded_complete_external_step(
        run_id: int,
        step_id: str,
        result_refs: dict[str, Any] | None = None,
        expected_state_version: int | None = None,
    ) -> CallToolResult:
        return await _guarded_ledger_call("complete_external_step", locals())

    @server.tool(name="workflow_resume", annotations=_write_annotations("Workflow Resume"))
    async def guarded_workflow_resume(
        run_id: int, expected_state_version: int | None = None
    ) -> CallToolResult:
        return await _guarded_ledger_call("workflow_resume", locals())

    @server.tool(name="workflow_cancel", annotations=_write_annotations("Workflow Cancel"))
    async def guarded_workflow_cancel(
        run_id: int,
        reason: str = "",
        expected_state_version: int | None = None,
    ) -> CallToolResult:
        return await _guarded_ledger_call("workflow_cancel", locals())

    @server.tool(
        name="get_runtime_status",
        description=(
            "Return CRM runtime diagnostics plus the independently degraded AutoStop App "
            "adapter status without exposing credentials."
        ),
        annotations=_read_annotations("Runtime Status"),
    )
    async def get_runtime_status() -> CallToolResult:
        crm_payload = (
            _as_dict(await crm_runtime_status_tool.run({}, convert_result=False))
            if crm_runtime_status_tool is not None
            else {
                "ok": False,
                "error": {"code": "crm_runtime_status_unavailable"},
            }
        )
        store_payload = await _invoke_store("store_runtime_status", {"live": True})
        crm_ok = bool(crm_payload.get("ok"))
        store_ok = bool(store_payload.get("ok"))
        data = (
            dict(crm_payload.get("data") or {}) if isinstance(crm_payload.get("data"), dict) else {}
        )
        data["store"] = {
            "ok": store_ok,
            "status": str(store_payload.get("status") or ("ready" if store_ok else "degraded")),
            "summary": _compact_object(store_payload.get("summary") or {}, item_limit=10),
            "data": _compact_object(store_payload.get("data"), item_limit=10, key_limit=40),
            "warnings": [str(item) for item in store_payload.get("warnings") or []][:10],
        }
        warnings = [str(item) for item in crm_payload.get("warnings") or []]
        if not store_ok:
            warnings.append("store_adapter_degraded")
        payload = dict(crm_payload)
        payload.update(
            {
                "ok": crm_ok,
                "status": "ready" if crm_ok and store_ok else "degraded" if crm_ok else "failed",
                "data": data,
                "warnings": list(dict.fromkeys(warnings)),
            }
        )
        payload.setdefault("format", AGENT_GATEWAY_FORMAT)
        payload.setdefault("summary", {})
        payload.setdefault("next_actions", [])
        payload.setdefault("verification", {})
        payload.setdefault("page", {})
        payload.setdefault("meta", {})
        return _tool_result(payload, label="get_runtime_status")

    @server.tool(
        name="agent_bootstrap",
        description="Return one compact Codex startup package: manager route, CRM board digest, security policy, and unfinished workflows.",
        annotations=_read_annotations("Agent Bootstrap v2"),
    )
    async def agent_bootstrap(
        query: str = "",
        intent: str | None = None,
        sample_limit: int = 8,
    ) -> CallToolResult:
        manager_payload: dict[str, Any] = {}
        if manager_bootstrap_tool is not None:
            try:
                manager_payload = _as_dict(
                    await manager_bootstrap_tool.run(
                        {"query": query, "intent": intent, "limit": 8}, convert_result=False
                    )
                )
            except Exception as exc:  # pragma: no cover
                manager_payload = {"ok": False, "error": str(exc)}
        store_snapshot = await _invoke_store(
            "store_runtime_status",
            {"live": True, "bootstrap_snapshot": True},
        )
        context_ok, context_data, _context_meta, context_error = _response_data(
            board_api.get_board_context()
        )
        cards_ok, cards_data, _cards_meta, cards_error = _response_data(
            board_api.get_cards(include_archived=False, compact=True)
        )
        cards = _items_from_data(cards_data, "cards")
        sample = [
            _slim_card(card, DEFAULT_CARD_FIELDS)
            for card in cards[: _normalize_limit(sample_limit, default=8, maximum=20)]
        ]
        context = (
            context_data.get("context", context_data) if isinstance(context_data, dict) else {}
        )
        ok = context_ok and cards_ok
        store_ok = bool(store_snapshot.get("ok"))
        warnings = [] if ok else [str(context_error or cards_error or "bootstrap_degraded")]
        if not store_ok:
            warnings.append("store_adapter_degraded")
        payload = _envelope(
            ok=ok,
            status="ready" if ok and store_ok else "degraded",
            summary={
                "connector": dict(connector_identity),
                "board": {
                    "columns": context.get("columns_total"),
                    "active_cards": context.get("active_cards_total", len(cards)),
                    "archived_cards": context.get("archived_cards_total"),
                    "stickies": context.get("stickies_total"),
                },
                "manager": manager_payload.get("summary", manager_payload),
                "store": {
                    "ok": store_ok,
                    "status": str(
                        store_snapshot.get("status") or ("ready" if store_ok else "degraded")
                    ),
                    "snapshot": _compact_object(
                        store_snapshot.get("summary") or store_snapshot.get("data") or {},
                        item_limit=10,
                        key_limit=40,
                    ),
                },
                "security_policy": load_agent_gateway_security_policy().public_dict(),
                "card_sample": sample,
            },
            warnings=warnings,
            next_actions=["agent_board_digest or agent_search", "use named workflow before raw"],
            meta={"tool_count": len(getattr(tool_manager, "_tools", {}))},
        )
        return _tool_result(payload, label="agent_bootstrap")

    @server.tool(
        name="agent_board_digest",
        description="Return a compact CRM or AutoStop App store digest; CRM remains the default.",
        annotations=_read_annotations("Agent Board Digest"),
    )
    async def agent_board_digest(
        include_archived: bool = False,
        cursor: str | None = None,
        limit: int = 50,
        fields: list[str] | None = None,
        scope: Literal["crm", "store"] = "crm",
        since: str | None = None,
        ack_token: str | None = None,
    ) -> CallToolResult:
        effective_limit = _normalize_limit(limit, default=50, maximum=100)
        if scope == "store":
            result = await _invoke_store(
                "store_digest",
                {
                    "baseline": False,
                    "since": since,
                    "cursor": cursor,
                    "ack_token": ack_token,
                    "limit": effective_limit,
                    "stream": "store_digest",
                },
            )
            return _tool_result(
                store_gateway_envelope(
                    result,
                    summary={"scope": "store"},
                    item_limit=effective_limit,
                    envelope_factory=_envelope,
                    compact=_compact_object,
                ),
                label="agent_board_digest",
            )
        ok, data, meta, error = _response_data(
            board_api.get_cards(include_archived=include_archived, compact=True)
        )
        cards = _items_from_data(data, "cards")
        offset = _cursor_offset(cursor)
        selected = _selected_fields(fields)
        page_items = [
            _slim_card(card, selected) for card in cards[offset : offset + effective_limit]
        ]
        next_offset = offset + len(page_items)
        has_more = next_offset < len(cards)
        payload = _envelope(
            ok=ok,
            status="completed" if ok else "failed",
            summary={
                "scope": "crm",
                "total": len(cards),
                "returned": len(page_items),
                "fields": list(selected),
            },
            data={"cards": page_items},
            warnings=[] if ok else [_error_code({"error": error}) or "board_digest_failed"],
            page={
                "cursor": str(offset),
                "next_cursor": str(next_offset) if has_more else None,
                "limit": effective_limit,
                "has_more": has_more,
            },
            meta={"source_meta": _compact_object(meta)},
        )
        return _tool_result(payload, label="agent_board_digest")

    @server.tool(
        name="agent_search",
        description=(
            "Search compact CRM or AutoStop App entities; store results are paginated and "
            "redacted by the Manager adapter."
        ),
        annotations=_read_annotations("Agent Search"),
    )
    async def agent_search(
        entity: Literal[
            "card",
            "client",
            "repair_order",
            "inventory",
            "cashbox",
            "file",
            "store_part",
            "store_order",
            "store_quote_request",
            "store_supplier",
            "store_batch",
            "store_warehouse_operation",
            "store_marketplace_listing",
            "store_state",
            "store_sourcing_offer",
        ],
        query: str = "",
        include_archived: bool = False,
        limit: int = 20,
        filters: dict[str, Any] | None = None,
        cursor: str | None = None,
    ) -> CallToolResult:
        effective_limit = _normalize_limit(limit, default=20, maximum=50)
        if entity in STORE_SEARCH_ENTITIES:
            result = await _invoke_store(
                "store_search",
                {
                    "entity": entity,
                    "query": query,
                    "filters": dict(filters or {}),
                    "cursor": cursor,
                    "limit": effective_limit,
                },
            )
            return _tool_result(
                store_gateway_envelope(
                    result,
                    summary={"entity": entity, "query": query, "scope": "store"},
                    item_limit=effective_limit,
                    envelope_factory=_envelope,
                    compact=_compact_object,
                ),
                label="agent_search",
            )
        if entity == "card":
            response = board_api.search_cards(
                query=query, include_archived=include_archived, limit=effective_limit
            )
            keys = ("cards", "items", "results")
        elif entity == "client":
            response = board_api.search_clients(query=query, limit=effective_limit)
            keys = ("clients", "items", "results")
        elif entity == "repair_order":
            response = board_api.list_repair_orders(
                query=query, limit=effective_limit, compact=True, redact_private=True
            )
            keys = ("repair_orders", "items", "results")
        elif entity == "inventory":
            response = board_api.search_inventory_items(query=query, limit=effective_limit)
            keys = ("items", "inventory_items", "results")
        elif entity == "cashbox":
            response = board_api.list_cashboxes(limit=effective_limit)
            keys = ("cashboxes", "items", "results")
        else:
            response = board_api.list_shared_files()
            keys = ("files", "items", "results")
        ok, data, meta, error = _response_data(response)
        items = _items_from_data(data, *keys)[:effective_limit]
        if entity == "card":
            items = [_slim_card(item, DEFAULT_CARD_FIELDS) for item in items]
        else:
            items = _compact_object(items, item_limit=effective_limit)
        payload = _envelope(
            ok=ok,
            summary={
                "entity": entity,
                "query": query,
                "returned": len(items),
                "scope": "crm",
            },
            data={"items": items},
            warnings=[] if ok else [_error_code({"error": error}) or "search_failed"],
            page={"limit": effective_limit, "has_more": False},
            meta={"source_meta": _compact_object(meta)},
        )
        return _tool_result(payload, label="agent_search")

    @server.tool(
        name="agent_entity_context",
        description=(
            "Read focused context for one exact CRM or AutoStop App entity without storing "
            "the source payload in Gateway state."
        ),
        annotations=_read_annotations("Agent Entity Context"),
    )
    async def agent_entity_context(
        entity: Literal[
            "card",
            "client",
            "repair_order",
            "cashbox",
            "inventory",
            "file",
            "store_part",
            "store_order",
            "store_quote_request",
            "store_supplier",
            "store_batch",
            "store_warehouse_operation",
            "store_marketplace_listing",
            "store_state",
        ],
        entity_id: str,
        detail: Literal["summary", "full", "full_with_vin_photo"] = "summary",
    ) -> CallToolResult:
        if entity in STORE_SEARCH_ENTITIES:
            result = await _invoke_store(
                "store_entity_context",
                {"entity": entity, "entity_id": entity_id, "detail": detail},
            )
            compact_store = (
                (lambda value, **kwargs: _compact_object(value, max_depth=8, **kwargs))
                if entity == "store_quote_request" and detail in {"full", "full_with_vin_photo"}
                else _compact_object
            )
            return _tool_result(
                store_gateway_envelope(
                    result,
                    summary={
                        "entity": entity,
                        "entity_id": entity_id,
                        "detail": detail,
                        "scope": "store",
                    },
                    item_limit=50 if detail == "full" else 15,
                    envelope_factory=_envelope,
                    compact=compact_store,
                ),
                label="agent_entity_context",
            )
        if entity == "card":
            response = (
                board_api.get_card_context(
                    entity_id, event_limit=10, include_repair_order_text=False
                )
                if detail == "full"
                else board_api.get_card(entity_id)
            )
        elif entity == "client":
            response = board_api.get_client(entity_id, order_limit=20 if detail == "full" else 5)
        elif entity == "repair_order":
            response = board_api.get_repair_order(entity_id)
        elif entity == "cashbox":
            response = board_api.get_cashbox(
                entity_id, transaction_limit=50 if detail == "full" else 10
            )
        elif entity == "inventory":
            response = board_api.get_inventory_item(entity_id)
        else:
            response = board_api.get_shared_file_info(entity_id)
        ok, data, meta, error = _response_data(response)
        payload = _envelope(
            ok=ok,
            summary={
                "entity": entity,
                "entity_id": entity_id,
                "detail": detail,
                "scope": "crm",
            },
            data=_compact_object(
                data,
                item_limit=50 if detail == "full" else 15,
                max_depth=8 if detail == "full" else 5,
            ),
            warnings=[] if ok else [_error_code({"error": error}) or "entity_read_failed"],
            meta={"source_meta": _compact_object(meta)},
        )
        return _tool_result(payload, label="agent_entity_context")

    @server.tool(
        name="agent_board_workflow",
        description="Execute one named board manager operation with durable idempotency and automatic ledger transitions.",
        annotations=_write_annotations("Agent Board Workflow"),
    )
    async def agent_board_workflow(
        operation: str,
        payload: dict[str, Any] | None,
        idempotency_key: str,
        mode: Literal["dry_run", "apply"] = "dry_run",
    ) -> CallToolResult:
        return await _execute_workflow(
            workflow_id="board",
            operation=operation,
            payload=dict(payload or {}),
            idempotency_key=idempotency_key,
            allowed=BOARD_WORKFLOW_OPERATIONS,
            mode=mode,
        )

    @server.tool(
        name="agent_finance_workflow",
        description="Execute a finance/cashbox/repair-order operation with idempotency, policy gates, and compact verification evidence.",
        annotations=_write_annotations("Agent Finance Workflow", destructive=True),
    )
    async def agent_finance_workflow(
        operation: str, payload: dict[str, Any] | None, idempotency_key: str
    ) -> CallToolResult:
        return await _execute_workflow(
            workflow_id="finance",
            operation=operation,
            payload=dict(payload or {}),
            idempotency_key=idempotency_key,
            allowed=FINANCE_WORKFLOW_OPERATIONS,
        )

    @server.tool(
        name="agent_inventory_workflow",
        description=(
            "Execute a CRM inventory operation or an allowlisted AutoStop App management "
            "action. Existing CRM calls default to apply; store calls require explicit mode."
        ),
        annotations=_write_annotations("Agent Inventory Workflow"),
    )
    async def agent_inventory_workflow(
        operation: str,
        payload: dict[str, Any] | None,
        idempotency_key: str,
        mode: Literal["dry_run", "apply"] | None = None,
    ) -> CallToolResult:
        return await _execute_workflow(
            workflow_id="inventory",
            operation=operation,
            payload=dict(payload or {}),
            idempotency_key=idempotency_key,
            allowed=INVENTORY_WORKFLOW_OPERATIONS | STORE_MANAGEMENT_OPERATIONS,
            mode=mode,
        )

    @server.tool(
        name="agent_document_workflow",
        description=(
            "Execute a CRM print/file/dashboard-message operation or retrieve an exact Store "
            "VIN-photo preview. Dashboard-message writes support dry_run/apply; binary payloads "
            "are returned only when allow_large_output is explicit."
        ),
        annotations=_write_annotations("Agent Document Workflow"),
    )
    async def agent_document_workflow(
        operation: str,
        payload: dict[str, Any] | None,
        idempotency_key: str,
        allow_large_output: bool = False,
        mode: Literal["dry_run", "apply"] | None = None,
    ) -> CallToolResult:
        return await _execute_workflow(
            workflow_id="document",
            operation=operation,
            payload=dict(payload or {}),
            idempotency_key=idempotency_key,
            allowed=DOCUMENT_WORKFLOW_OPERATIONS,
            allow_large_output=allow_large_output,
            mode=mode,
        )

    @server.tool(
        name="discover_raw_capabilities",
        description=(
            "Search hidden raw CRM/manager capabilities by exact name or a conservative natural-language intent "
            "before using the raw escape hatch. Semantic discovery returns read-only capabilities only."
        ),
        annotations=_read_annotations("Discover Raw Capabilities"),
    )
    def discover_raw_capabilities(query: str = "", limit: int = 25) -> CallToolResult:
        effective_limit = _normalize_limit(limit, default=25, maximum=100)
        normalized_query = discovery_phrase(query)
        normalized_capability_name = normalized_query.replace(" ", "_")
        if normalized_capability_name in (
            INTERNAL_ONLY_CAPABILITY_NAMES | PERMANENT_AGENT_GATEWAY_TOOL_NAMES
        ):
            payload = _envelope(
                ok=True,
                summary={"query": query, "returned": 0},
                data={"capabilities": []},
                page={"limit": effective_limit, "has_more": False},
            )
            return _tool_result(payload, label="discover_raw_capabilities")

        candidates: list[tuple[int, str, bool, dict[str, Any]]] = []

        def collect(
            *,
            name: str,
            description: str,
            risk: str,
            schema: object,
        ) -> None:
            score, matched_terms, exact_name = raw_capability_discovery_score(
                normalized_query,
                name=name,
                description=description,
                schema=schema,
            )
            if normalized_query:
                if not exact_name and (score < 24 or risk != "read"):
                    return
            elif risk != "read":
                return
            item: dict[str, Any] = {
                "name": name,
                "description": description[:300],
                "risk": risk,
                "schema_hash": _schema_hash(schema),
            }
            if normalized_query and not exact_name and matched_terms:
                item["matched_terms"] = matched_terms
            candidates.append((score, name, exact_name, item))

        for name in sorted(WEB_RESEARCH_CAPABILITY_NAMES):
            description = WEB_RESEARCH_CAPABILITY_DESCRIPTIONS[name]
            collect(
                name=name,
                description=description,
                risk="read",
                schema=WEB_RESEARCH_CAPABILITY_SCHEMAS[name],
            )
        for name, tool in sorted(raw_tools.items()):
            if name in PERMANENT_AGENT_GATEWAY_TOOL_NAMES or name in INTERNAL_ONLY_CAPABILITY_NAMES:
                continue
            description = str(getattr(tool, "description", "") or "")
            schema = getattr(tool, "parameters", {}) or {}
            collect(name=name, description=description, risk=_tool_risk(tool), schema=schema)
        for route in sorted(RAW_API_ROUTES):
            name = _virtual_api_name(route)
            description = (
                f"Guarded internal CRM fallback for {route}; use only when no focused "
                "named workflow or MCP capability covers the exact action."
            )
            collect(
                name=name,
                description=description,
                risk=_virtual_api_risk(route, name),
                schema=_virtual_api_schema(route),
            )

        exact_candidates = [candidate for candidate in candidates if candidate[2]]
        if exact_candidates:
            candidates = exact_candidates
        candidates.sort(key=lambda item: (-item[0], item[1]))
        items = [item for _, _, _, item in candidates[:effective_limit]]
        payload = _envelope(
            ok=True,
            summary={"query": query, "returned": len(items)},
            data={"capabilities": items},
            page={"limit": effective_limit, "has_more": len(candidates) > effective_limit},
        )
        return _tool_result(payload, label="discover_raw_capabilities")

    @server.tool(
        name="get_raw_capability_schema",
        description="Return the current input schema and schema hash for one hidden raw capability.",
        annotations=_read_annotations("Raw Capability Schema"),
    )
    def get_raw_capability_schema(name: str) -> CallToolResult:
        normalized_name = str(name or "").strip()
        if normalized_name in INTERNAL_ONLY_CAPABILITY_NAMES:
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=[internal_only_capability_warning(normalized_name)],
                ),
                label="get_raw_capability_schema",
            )
        tool = raw_tools.get(normalized_name)
        virtual_route = _virtual_api_route(normalized_name)
        virtual_web = normalized_name in WEB_RESEARCH_CAPABILITY_NAMES
        if (
            tool is None and virtual_route is None and not virtual_web
        ) or normalized_name in PERMANENT_AGENT_GATEWAY_TOOL_NAMES:
            return _tool_result(
                _envelope(ok=False, status="failed", warnings=["capability_not_found"]),
                label="get_raw_capability_schema",
            )
        schema = (
            WEB_RESEARCH_CAPABILITY_SCHEMAS[normalized_name]
            if virtual_web
            else (
                _virtual_api_schema(virtual_route)
                if virtual_route is not None
                else getattr(tool, "parameters", {}) or {}
            )
        )
        risk = (
            "read"
            if virtual_web
            else (
                _virtual_api_risk(virtual_route, normalized_name)
                if virtual_route is not None
                else _tool_risk(tool)
            )
        )
        payload = _envelope(
            ok=True,
            summary={
                "name": normalized_name,
                "risk": risk,
                "schema_hash": _schema_hash(schema),
            },
            data={"input_schema": schema},
        )
        return _tool_result(payload, label="get_raw_capability_schema")

    @server.tool(
        name="call_raw_capability",
        description="Invoke one discovered hidden capability after validating its current schema hash and security policy.",
        annotations=_write_annotations("Call Raw Capability", destructive=True),
    )
    async def call_raw_capability(
        name: str,
        arguments: dict[str, Any] | None,
        schema_hash: str,
        idempotency_key: str = "",
        allow_large_output: bool = False,
        release_smoke_revision: str = "",
        release_smoke_proof: str = "",
    ) -> CallToolResult:
        policy_now = load_agent_gateway_security_policy()
        if not policy_now.raw_enabled:
            return _tool_result(
                _envelope(ok=False, status="blocked", warnings=["agent_gateway_raw_disabled"]),
                label="call_raw_capability",
            )
        normalized_name = str(name or "").strip()
        if normalized_name in INTERNAL_ONLY_CAPABILITY_NAMES:
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=[internal_only_capability_warning(normalized_name)],
                ),
                label="call_raw_capability",
            )
        tool = raw_tools.get(normalized_name)
        virtual_route = _virtual_api_route(normalized_name)
        virtual_web = normalized_name in WEB_RESEARCH_CAPABILITY_NAMES
        if (
            tool is None and virtual_route is None and not virtual_web
        ) or normalized_name in PERMANENT_AGENT_GATEWAY_TOOL_NAMES:
            return _tool_result(
                _envelope(ok=False, status="failed", warnings=["capability_not_found"]),
                label="call_raw_capability",
            )
        current_schema = (
            WEB_RESEARCH_CAPABILITY_SCHEMAS[normalized_name]
            if virtual_web
            else (
                _virtual_api_schema(virtual_route)
                if virtual_route is not None
                else getattr(tool, "parameters", {}) or {}
            )
        )
        current_hash = _schema_hash(current_schema)
        if str(schema_hash or "") != current_hash:
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    summary={"name": normalized_name, "current_schema_hash": current_hash},
                    warnings=["schema_hash_mismatch_rediscover_capability"],
                ),
                label="call_raw_capability",
            )
        if virtual_web:
            argument_error = web_research_argument_error(normalized_name, arguments or {})
            if argument_error:
                return _tool_result(
                    _envelope(ok=False, status="blocked", warnings=[argument_error]),
                    label="call_raw_capability",
                )
        owner_mode = (
            str((arguments or {}).get("mode") or "dry_run").strip().casefold()
            if normalized_name == "store_owner_api"
            else ""
        )
        owner_correlation_id = (
            str((arguments or {}).get("correlation_id") or "").strip()
            if normalized_name == "store_owner_api"
            else ""
        )
        owner_request_error = _store_owner_request_error(
            normalized_name,
            arguments or {},
            owner_mode=owner_mode,
            owner_correlation_id=owner_correlation_id,
        )
        if owner_request_error:
            return _tool_result(
                _envelope(ok=False, status="blocked", warnings=[owner_request_error]),
                label="call_raw_capability",
            )
        risk = (
            "read"
            if virtual_web
            or (
                normalized_name == "store_owner_api"
                and owner_mode in {"read", "revision", "prepare"}
            )
            else (
                _virtual_api_risk(virtual_route, normalized_name)
                if virtual_route is not None
                else _tool_risk(tool)
            )
        )
        policy_error = _policy_error(
            tool_name=normalized_name,
            risk=risk,
            arguments=arguments or {},
        )
        if policy_error:
            return _tool_result(
                _envelope(ok=False, status="blocked", warnings=[policy_error]),
                label="call_raw_capability",
            )
        maintenance_mode = risk != "read" and is_maintenance_mode()
        maintenance_technical_allowed = maintenance_mode and _maintenance_technical_write_allowed(
            capability=normalized_name,
            arguments=arguments or {},
            revision=release_smoke_revision,
            proof=release_smoke_proof,
            agent_bearer_token=agent_bearer_token,
        )
        if maintenance_mode and not maintenance_technical_allowed:
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=["maintenance_mode_raw_write_blocked"],
                ),
                label="call_raw_capability",
            )
        if normalized_name == "api:/api/reorder_cashboxes":
            expected_cashbox_ids = (arguments or {}).get("expected_cashbox_ids")
            if (
                not isinstance(expected_cashbox_ids, list)
                or not expected_cashbox_ids
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in expected_cashbox_ids
                )
                or len(set(expected_cashbox_ids)) != len(expected_cashbox_ids)
            ):
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=[
                            "cashbox_order_snapshot_required_reread_exact_list_first"
                        ],
                    ),
                    label="call_raw_capability",
                )
        if normalized_name == "api:/api/save_employee" and str(
            (arguments or {}).get("attestation_run_id") or ""
        ).strip():
            expected_employee_ids = (arguments or {}).get("expected_employee_ids")
            if (
                not isinstance(expected_employee_ids, list)
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in expected_employee_ids
                )
                or len(set(expected_employee_ids)) != len(expected_employee_ids)
            ):
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=["employee_snapshot_required_reread_exact_list_first"],
                    ),
                    label="call_raw_capability",
                )
        if normalized_name == "api:/api/create_employee_salary_transaction":
            missing_revisions = [
                field
                for field in (
                    "expected_cashbox_updated_at",
                    "expected_employee_updated_at",
                )
                if not str((arguments or {}).get(field) or "").strip()
            ]
            if missing_revisions:
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=[
                            "salary_transaction_expected_revisions_required_reread_exact_targets_first"
                        ],
                        summary={"missing_fields": missing_revisions},
                    ),
                    label="call_raw_capability",
                )
        if normalized_name == "api:/api/create_employee_shift_accrual" and not str(
            (arguments or {}).get("expected_employee_updated_at") or ""
        ).strip():
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=[
                        "shift_accrual_expected_employee_revision_required_reread_exact_employee_first"
                    ],
                    summary={"missing_fields": ["expected_employee_updated_at"]},
                ),
                label="call_raw_capability",
            )
        if normalized_name == "api:/api/cancel_cash_transaction" and not str(
            (arguments or {}).get("expected_cashbox_updated_at") or ""
        ).strip():
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=[
                        "cash_cancellation_expected_revision_required_reread_exact_cashbox_first"
                    ],
                    summary={"missing_fields": ["expected_cashbox_updated_at"]},
                ),
                    label="call_raw_capability",
                )
        if (
            normalized_name == "api:/api/delete_employee"
            and "attestation_cleanup_shift_accrual_ids" in (arguments or {})
        ):
            missing_fields = [
                field
                for field in (
                    "expected_updated_at",
                    "attestation_run_id",
                )
                if not str((arguments or {}).get(field) or "").strip()
            ]
            shift_accrual_ids = (arguments or {}).get(
                "attestation_cleanup_shift_accrual_ids"
            )
            if (
                not isinstance(shift_accrual_ids, list)
                or not shift_accrual_ids
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in shift_accrual_ids
                )
                or len(set(shift_accrual_ids)) != len(shift_accrual_ids)
            ):
                missing_fields.append("attestation_cleanup_shift_accrual_ids")
            if missing_fields:
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=[
                            "attestation_shift_cleanup_snapshot_required_reread_exact_employee_first"
                        ],
                        summary={"missing_fields": missing_fields},
                    ),
                    label="call_raw_capability",
                )
        if normalized_name == "api:/api/delete_gateway_attestation_payment_fixture":
            missing_fields = [
                field
                for field in (
                    "expected_updated_at",
                    "expected_cashbox_updated_at",
                )
                if not str((arguments or {}).get(field) or "").strip()
            ]
            expected_transaction_ids = (arguments or {}).get(
                "expected_transaction_ids"
            )
            if (
                not isinstance(expected_transaction_ids, list)
                or not expected_transaction_ids
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in expected_transaction_ids
                )
                or len(set(expected_transaction_ids))
                != len(expected_transaction_ids)
            ):
                missing_fields.append("expected_transaction_ids")
            if missing_fields:
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=[
                            "attestation_payment_cleanup_snapshot_required_reread_exact_targets_first"
                        ],
                        summary={"missing_fields": missing_fields},
                    ),
                    label="call_raw_capability",
                )
        if normalized_name == "link_card_to_client":
            missing_revisions = [
                field
                for field in (
                    "expected_card_updated_at",
                    "expected_client_updated_at",
                )
                if not str((arguments or {}).get(field) or "").strip()
            ]
            if missing_revisions:
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=[
                            "card_client_link_expected_revisions_required_reread_exact_targets_first"
                        ],
                        summary={"missing_fields": missing_revisions},
                    ),
                    label="call_raw_capability",
                )
        if (
            normalized_name in OPTIMISTIC_WRITE_NAMES
            and not str((arguments or {}).get("expected_updated_at") or "").strip()
        ):
            revision_warning = (
                "expected_updated_at_required_reread_exact_file_first"
                if normalized_name == "delete_shared_file"
                else "expected_updated_at_required_reread_exact_card_first"
            )
            return _tool_result(
                _envelope(
                    ok=False,
                    status="blocked",
                    warnings=[revision_warning],
                ),
                label="call_raw_capability",
            )
        run_id: int | None = None
        effective_arguments = dict(arguments or {})
        maintenance_headers = _maintenance_release_smoke_headers(
            technical_allowed=maintenance_technical_allowed,
            virtual_route=virtual_route,
            revision=release_smoke_revision,
            proof=release_smoke_proof,
        )
        owner_binding: dict[str, Any] = {}
        request_fingerprint = ""
        if risk != "read":
            if not str(idempotency_key or "").strip():
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=["idempotency_key_required_for_raw_write"],
                    ),
                    label="call_raw_capability",
                )
            if normalized_name == "store_owner_api":
                supplied_contract_id = str(
                    effective_arguments.get("expected_contract_id") or ""
                ).strip()
                prepare_arguments = dict(effective_arguments)
                prepare_arguments["mode"] = "prepare"
                prepare_arguments["prepare_for_mode"] = owner_mode
                prepare_arguments.pop("expected_contract_id", None)
                prepared = await _invoke(normalized_name, prepare_arguments)
                prepared_binding = _store_owner_prepare_binding(
                    effective_arguments,
                    prepared,
                    prepared_for_mode=owner_mode,
                )
                if prepared_binding is None:
                    return _tool_result(
                        _envelope(
                            ok=False,
                            status="blocked",
                            warnings=["store_owner_prepare_contract_invalid"],
                        ),
                        label="call_raw_capability",
                    )
                if supplied_contract_id and supplied_contract_id != prepared_binding["contract_id"]:
                    return _tool_result(
                        _envelope(
                            ok=False,
                            status="blocked",
                            warnings=["store_owner_expected_contract_id_mismatch"],
                        ),
                        label="call_raw_capability",
                    )
                owner_binding = prepared_binding
                effective_arguments["expected_contract_id"] = owner_binding["contract_id"]
            request_fingerprint = _request_fingerprint(
                {"capability": normalized_name, "arguments": effective_arguments}
            )
            owner_scope = (
                {
                    "domain": "store",
                    "source": "store",
                    "correlation_id": owner_correlation_id,
                    "contract_id": owner_binding["contract_id"],
                    "operation_id": owner_binding["operation_id"],
                    "request_sha256": owner_binding["request_sha256"],
                    "schema_hash": owner_binding["schema_hash"],
                    "verification_class": owner_binding["verification_class"],
                    "target_ref_sha256": owner_binding["target_ref_sha256"],
                    **(
                        {"expected_revision_sha256": owner_binding["expected_revision_sha256"]}
                        if owner_binding.get("expected_revision_sha256") is not None
                        else {}
                    ),
                }
                if normalized_name == "store_owner_api"
                else None
            )
            run_id, started, deduplicated = await _start_idempotent_workflow(
                workflow_id=f"raw:{normalized_name}",
                intent=f"raw_{normalized_name}",
                idempotency_key=idempotency_key,
                payload={
                    "operation": normalized_name,
                    "request_fingerprint": request_fingerprint,
                },
                mode=owner_mode or None,
                dry_run=owner_mode == "dry_run",
                correlation_id=owner_correlation_id,
                scope_overrides=owner_scope,
                refs_only=normalized_name == "store_owner_api",
            )
            if started and not bool(started.get("ok")):
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="failed",
                        run_id=run_id,
                        warnings=list(started.get("warnings") or ["workflow_start_failed"]),
                    ),
                    label="call_raw_capability",
                )
            if deduplicated:
                return _deduplicated_workflow_result(
                    label="call_raw_capability",
                    operation=normalized_name,
                    run_id=run_id,
                    started=started,
                )
            if run_id is None:
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        warnings=["durable_workflow_run_id_unavailable"],
                    ),
                    label="call_raw_capability",
                )
            executing = await _transition(
                run_id,
                "executing",
                expected_state_version=_workflow_state_version(started),
                message=f"raw execute {normalized_name}",
            )
            if not bool(executing.get("ok")):
                return _tool_result(
                    _envelope(
                        ok=False,
                        status="blocked",
                        run_id=run_id,
                        data=_compact_object(executing),
                        warnings=["workflow_enter_executing_failed"],
                    ),
                    label="call_raw_capability",
                )
        result = await _invoke(
            normalized_name, effective_arguments, extra_headers=maintenance_headers
        )
        owner_transport_bound = True
        ledger_state_version = _workflow_state_version(executing) if run_id is not None else None
        if normalized_name == "store_owner_api" and run_id is not None:
            owner_transport_bound = _store_owner_transport_matches_binding(result, owner_binding)
            if owner_transport_bound:
                checkpoint_payload = {
                    "phase": "transport_result",
                    "operation": normalized_name,
                    "mode": owner_mode,
                    "status": str(result.get("status") or ""),
                    "request_fingerprint": request_fingerprint,
                    "contract_id": owner_binding["contract_id"],
                    "operation_id": owner_binding["operation_id"],
                    "request_sha256": owner_binding["request_sha256"],
                    "schema_hash": owner_binding["schema_hash"],
                    "verification_class": owner_binding["verification_class"],
                    "target_ref_sha256": owner_binding["target_ref_sha256"],
                    **(
                        {"expected_revision_sha256": owner_binding["expected_revision_sha256"]}
                        if owner_binding.get("expected_revision_sha256") is not None
                        else {}
                    ),
                }
                checkpointed = await _invoke(
                    "workflow_checkpoint",
                    {
                        "run_id": run_id,
                        "checkpoint": checkpoint_payload,
                        "message": f"raw verify {normalized_name}",
                        "expected_state_version": ledger_state_version,
                    },
                )
                owner_transport_bound = bool(checkpointed.get("ok"))
                if owner_transport_bound:
                    ledger_state_version = _workflow_state_version(checkpointed)
        verification = await _verify_operation(
            normalized_name, effective_arguments, result, risk, extra_headers=maintenance_headers
        )
        if normalized_name == "store_owner_api" and run_id is not None:
            verification.update(
                {
                    "contract_id": owner_binding.get("contract_id"),
                    "operation_id": owner_binding.get("operation_id"),
                    "request_fingerprint": request_fingerprint,
                    "request_sha256": owner_binding.get("request_sha256"),
                    "schema_hash": owner_binding.get("schema_hash"),
                    "target_ref_sha256": owner_binding.get("target_ref_sha256"),
                    "verification_class": owner_binding.get("verification_class"),
                    **(
                        {"expected_revision_sha256": owner_binding.get("expected_revision_sha256")}
                        if owner_binding.get("expected_revision_sha256") is not None
                        else {}
                    ),
                }
            )
            if not owner_transport_bound:
                verification["passed"] = False
                verification["check"] = "store_owner_transport_checkpoint_binding_failed"
        result_meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        owner_executor_may_have_applied = bool(
            normalized_name == "store_owner_api"
            and (
                str(result.get("status") or "").casefold() == "compensating"
                or result_meta.get("write_applied") is True
                or result_meta.get("outcome_uncertain") is True
                or result_meta.get("readback_required") is True
            )
        )
        executor_ok = (
            bool(result.get("ok"))
            or bool(result.get("executor_applied"))
            or owner_executor_may_have_applied
        )
        verification_passed = bool(verification.get("passed"))
        ok = executor_ok and verification_passed
        ledger_closed = risk == "read"
        ledger_error: dict[str, Any] | None = None
        workflow_status = "completed" if ok and risk == "read" else "failed"
        if run_id is not None:
            executing_version = ledger_state_version
            if ok:
                verifying = await _transition(
                    run_id,
                    "verifying",
                    expected_state_version=executing_version,
                    message=f"raw verify {normalized_name}",
                )
                if bool(verifying.get("ok")):
                    completed = await _transition(
                        run_id,
                        "completed",
                        expected_state_version=_workflow_state_version(verifying),
                        message=f"raw completed {normalized_name}",
                        verification={
                            "executor_ok": True,
                            "schema_hash_verified": True,
                            **verification,
                        },
                        summary=f"raw:{normalized_name}",
                    )
                    ledger_closed = (
                        bool(completed.get("ok")) and str(completed.get("status")) == "completed"
                    )
                    workflow_status = "completed" if ledger_closed else "verifying"
                    if not ledger_closed:
                        ledger_error = completed
                else:
                    ledger_error = verifying
                    compensation = await _transition(
                        run_id,
                        "compensating",
                        expected_state_version=executing_version,
                        message=(f"raw ledger close reconciliation required for {normalized_name}"),
                    )
                    if bool(compensation.get("ok")):
                        workflow_status = "compensating"
                    else:
                        ledger_error = {"verifying": verifying, "compensating": compensation}
            elif executor_ok:
                compensation = await _transition(
                    run_id,
                    "compensating",
                    expected_state_version=executing_version,
                    message=(f"raw verification failed after executor applied {normalized_name}"),
                    verification={
                        "executor_ok": True,
                        "schema_hash_verified": True,
                        **verification,
                    },
                )
                workflow_status = "compensating" if bool(compensation.get("ok")) else "executing"
                if not bool(compensation.get("ok")):
                    ledger_error = compensation
            else:
                failed = await _transition(
                    run_id,
                    "failed",
                    expected_state_version=executing_version,
                    message=f"raw failed {normalized_name}",
                )
                ledger_closed = bool(failed.get("ok")) and str(failed.get("status")) == "failed"
                workflow_status = "failed"
                if not ledger_closed:
                    ledger_error = failed
        overall_ok = ok and ledger_closed
        data = result if allow_large_output else _compact_object(result)
        payload = _envelope(
            ok=overall_ok,
            status=workflow_status,
            run_id=run_id,
            summary={
                "name": normalized_name,
                "risk": risk,
                "schema_hash": current_hash,
            },
            data=data,
            warnings=(
                []
                if overall_ok
                else ["verification_failed_compensation_required"]
                if executor_ok and not verification_passed
                else ["workflow_ledger_close_failed"]
                if ok
                else ["raw_capability_failed"]
            ),
            verification={
                "schema_hash_verified": True,
                "executor_ok": executor_ok,
                "ledger_closed": ledger_closed,
                **verification,
            },
            next_actions=[]
            if overall_ok
            else [f"workflow_status(run_id={run_id}) and reconcile exact target"],
            meta={"ledger_error": _compact_object(ledger_error) if ledger_error else None},
        )
        return _tool_result(payload, label="call_raw_capability")

    keep = set(PERMANENT_AGENT_GATEWAY_TOOL_NAMES)
    if not policy.mail_enabled:
        keep.difference_update(MAIL_CAPABILITY_NAMES)
    for name in list(tools):
        if name not in keep:
            tool_manager.remove_tool(name)
    return set(tools)


__all__ = [
    "AGENT_GATEWAY_FORMAT",
    "AGENT_GATEWAY_TOOL_NAMES",
    "MANAGER_WORKFLOW_TOOL_NAMES",
    "PERMANENT_AGENT_GATEWAY_TOOL_NAMES",
    "WEB_RESEARCH_CAPABILITY_NAMES",
    "register_agent_gateway_v2",
]
