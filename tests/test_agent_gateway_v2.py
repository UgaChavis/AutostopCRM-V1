from __future__ import annotations

import copy
import hashlib
import json
import logging
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.oauth_provider import ProductionOAuthAuthorizationServerProvider
from minimal_kanban.mcp.server import create_mcp_server
from minimal_kanban.mcp.store_gateway import (
    INTERNAL_ONLY_CAPABILITY_NAMES,
    STORE_MANAGEMENT_CAPABILITY_NAME,
    STORE_READ_CAPABILITY_NAMES,
    verify_store_readback,
)

GATEWAY_ENV = {
    "AUTOSTOP_DEPLOYMENT_ENV": "development",
    "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
}


class FakeBoardApi:
    base_url = "http://127.0.0.1:41731"

    def __init__(self) -> None:
        self.raw_requests: list[dict] = []
        self.card_updated_at = "2026-07-11T00:00:00+00:00"
        self.repair_order_payments: list[dict] = []
        self.cash_transactions: list[dict] = []

    def get_board_context(self) -> dict:
        return {
            "ok": True,
            "data": {
                "context": {
                    "columns_total": 2,
                    "active_cards_total": 3,
                    "archived_cards_total": 1,
                    "stickies_total": 0,
                }
            },
        }

    def health(self) -> dict:
        return {"ok": True, "data": {"status": "healthy"}}

    def get_cards(self, *, include_archived: bool = False, compact: bool = True) -> dict:
        cards = [
            {
                "id": f"card-{index}",
                "short_id": f"C-{index}",
                "vehicle": "Vehicle",
                "title": f"Task {index}",
                "column": "inbox",
                "column_label": "Inbox",
                "tags": [],
                "status": "ok",
                "indicator": "green",
                "remaining_seconds": 200_000,
                "deadline_timestamp": "2026-07-13T00:00:00+00:00",
                "client_id": "",
                "board_summary": "",
                "updated_at": "2026-07-11T00:00:00+00:00",
                "deadline_heat_glow_color": "large-ui-only-value",
            }
            for index in range(4 if include_archived else 3)
        ]
        return {"ok": True, "data": {"cards": cards}, "meta": {"compact": compact}}

    def search_cards(self, **_: object) -> dict:
        return self.get_cards()

    def list_inventory_items(self, **_: object) -> dict:
        return {"ok": True, "data": {"items": [{"id": "inventory-1", "name": "Filter"}]}}

    def get_card(self, card_id: str) -> dict:
        return {"ok": True, "data": {"card": {"id": card_id, "title": "Task"}}}

    def run_manager_operation(
        self,
        *,
        operation: str,
        payload: dict | None = None,
        mode: str = "dry_run",
        actor_name: str | None = None,
        limit: int | None = None,
    ) -> dict:
        del actor_name, limit
        return {
            "ok": True,
            "data": {
                "operation": operation,
                "payload": dict(payload or {}),
                "run": {"mode": mode},
                "verification": {"mode": mode, "checked": 3, "passed": True},
            },
        }

    def get_repair_order(self, card_id: str) -> dict:
        return {
            "ok": True,
            "data": {
                "card": {"id": card_id, "updated_at": self.card_updated_at},
                "repair_order": {
                    "number": "42",
                    "payments": [dict(item) for item in self.repair_order_payments],
                    "payment_summary": {"cash_due": "1000", "noncash_due": "1176.47"},
                },
            },
        }

    def get_cashbox(
        self,
        cashbox_id: str,
        *,
        transaction_limit: int | None = None,
        transaction_offset: int | None = None,
    ) -> dict:
        del transaction_limit, transaction_offset
        return {
            "ok": True,
            "data": {
                "cashbox": {
                    "id": cashbox_id,
                    "name": "Наличный",
                    "transactions": [dict(item) for item in self.cash_transactions],
                }
            },
        }

    def update_repair_order(
        self,
        *,
        card_id: str,
        repair_order: dict,
        expected_updated_at: str | None = None,
        actor_name: str | None = None,
    ) -> dict:
        del actor_name
        if expected_updated_at != self.card_updated_at:
            return {"ok": False, "error": {"code": "card_update_conflict"}}
        self.repair_order_payments = [dict(item) for item in repair_order.get("payments", [])]
        payment = self.repair_order_payments[-1]
        transaction_id = "cash-transaction-payment-1"
        payment["cash_transaction_id"] = transaction_id
        self.cash_transactions.append(
            {
                "id": transaction_id,
                "cashbox_id": payment["cashbox_id"],
                "amount": payment["amount"],
            }
        )
        self.card_updated_at = "2026-07-11T00:01:00+00:00"
        return {
            "ok": True,
            "data": {
                "card": {"id": card_id, "updated_at": self.card_updated_at},
                "repair_order": {"payments": [dict(item) for item in self.repair_order_payments]},
            },
        }

    def _request(
        self,
        path: str,
        payload: dict | None = None,
        *,
        method: str = "POST",
        extra_headers: dict[str, str] | None = None,
    ) -> dict:
        self.raw_requests.append(
            {
                "path": path,
                "payload": dict(payload or {}),
                "method": method,
                "extra_headers": dict(extra_headers or {}),
            }
        )
        return {
            "ok": True,
            "data": {"path": path, "accepted": True},
            "meta": {"request_id": "fake-request"},
        }


def _fake_digest(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _fake_comment_hash(value: object) -> str:
    normalized = str(value or "").strip()
    canonical = "none:" if not normalized else f"comment:{normalized}"
    return hashlib.sha256(canonical.encode()).hexdigest()


def _fake_store_error(code: str, *, correlation_id: str = "") -> dict:
    return {
        "ok": False,
        "format": "store_agent_v1",
        "status": "conflict",
        "summary": {"error_code": code},
        "items": [],
        "changes": [],
        "page": {"has_more": False, "next_cursor": None, "limit": 0},
        "warnings": [code],
        "meta": {
            "correlation_id": correlation_id,
            "external_effect_state": "NOT_APPLICABLE",
        },
    }


def _fake_store_unavailable() -> dict:
    return {
        "ok": False,
        "format": "store_agent_v1",
        "status": "degraded",
        "summary": {},
        "items": [],
        "page": {},
        "warnings": ["store_unavailable"],
        "meta": {},
    }


def _fake_store_management_action(state: dict, arguments: dict) -> dict:
    domain = arguments["domain"]
    action = arguments["action"]
    target_id = arguments["target_id"]
    planned_changes = arguments["planned_changes"]
    owner_intent = arguments["owner_intent"]
    expected_updated_at = arguments["expected_updated_at"]
    idempotency_key = arguments["idempotency_key"]
    correlation_id = arguments["correlation_id"]
    mode = arguments["mode"]
    item = state["entities"].get((domain, target_id))
    if not state["store_available"] or item is None:
        return _fake_store_unavailable()

    principal = str(state["store_principal"])
    request_hash = _fake_digest({"operation": action, **arguments})
    receipt_key = (principal, idempotency_key)
    existing_receipt = state["store_receipts"].get(receipt_key)
    if existing_receipt is not None:
        if existing_receipt["request_hash"] != request_hash:
            return _fake_store_error("AGENT_IDEMPOTENCY_CONFLICT", correlation_id=correlation_id)
        replay = copy.deepcopy(existing_receipt["response"])
        replay["meta"]["idempotency_replay"] = True
        replay["meta"]["external_effect_state"] = existing_receipt["external_effect_state"]
        return replay

    plan_hash = _fake_digest(
        {
            "operation": action,
            "target_id": target_id,
            "expected_updated_at": expected_updated_at,
            "correlation_id": correlation_id,
            "planned_changes": planned_changes,
        }
    )
    if mode == "apply":
        proof = next(
            (
                candidate
                for candidate in reversed(state["store_dry_run_proofs"])
                if candidate["principal"] == principal
                and candidate["correlation_id"] == correlation_id
                and candidate["plan_hash"] == plan_hash
                and state["store_clock"] - candidate["created_at"] <= 1800
            ),
            None,
        )
        if proof is None:
            return _fake_store_error("AGENT_DRY_RUN_REQUIRED", correlation_id=correlation_id)
    if str(item.get("updated_at") or "") != expected_updated_at:
        return _fake_store_error("AGENT_REVISION_CONFLICT", correlation_id=correlation_id)

    before = dict(item)
    prospective = dict(item)
    effects: list[dict] = []
    if action == "assign_quote_request":
        changes = [
            {
                "field": "assigned_user_id",
                "before": item.get("assigned_user_id"),
                "after": planned_changes.get("assignee_id"),
            }
        ]
        prospective["assigned_user_id"] = planned_changes.get("assignee_id")
    elif action == "set_quote_request_status":
        changes = [
            {
                "field": "status",
                "before": item.get("status"),
                "after": planned_changes.get("status"),
            }
        ]
        prospective["status"] = planned_changes.get("status")
    elif action == "update_quote_request_comment":
        normalized_comment = str(planned_changes.get("internal_comment") or "").strip() or None
        changes = [
            {
                "field": "has_internal_comment",
                "before": bool(before.get("has_internal_comment")),
                "after": bool(normalized_comment),
            },
            {
                "field": "internal_comment_sha256",
                "before": before.get("internal_comment_sha256"),
                "after": _fake_comment_hash(normalized_comment),
            },
        ]
        prospective["has_internal_comment"] = bool(normalized_comment)
        prospective["internal_comment_sha256"] = _fake_comment_hash(normalized_comment)
    elif action == "set_batch_storage_location":
        changes = [
            {
                "field": "storage_location",
                "before": item.get("storage_location"),
                "after": planned_changes.get("storage_location"),
            }
        ]
        prospective["storage_location"] = planned_changes.get("storage_location")
    elif action == "mark_order_ready":
        changes = [
            {"field": "status", "before": item.get("status"), "after": "READY"},
            {
                "field": "ready_at",
                "before": item.get("ready_at"),
                "after": "generated_on_apply",
            },
        ]
        prospective["status"] = "READY"
        prospective["ready_at"] = "generated_on_apply"
        effects = [
            {"effect": "set_order_status", "status": "READY"},
            {"effect": "set_ready_at"},
            {"effect": "sync_shipment_draft", "applies": False, "local_items": 0},
            {"effect": "create_internal_order_ready_notification", "applies": True},
            {
                "effect": "attempt_external_customer_notifier_after_commit",
                "applies": True,
                "best_effort": True,
                "configured": False,
                "customer_linked": True,
                "cached_chat_available": False,
                "deliverability": "FAILED",
            },
        ]
    else:
        return _fake_store_error("AGENT_OPERATION_NOT_FOUND", correlation_id=correlation_id)

    external_effect_state = "NOT_APPLICABLE"
    if mode == "apply" and action == "mark_order_ready":
        external_effect_state = str(state.get("external_effect_state") or "SENT")
    if mode == "apply" and not state.get("skip_apply"):
        item.update(prospective)
        if action == "update_quote_request_comment":
            state["comment_values"][target_id] = normalized_comment
        state["store_apply_sequence"] += 1
        item["updated_at"] = f"2026-07-16T10:{state['store_apply_sequence']:02d}:00+00:00"
        prospective = dict(item)
    elif mode == "dry_run":
        prospective["updated_at"] = item["updated_at"]

    response = {
        "ok": True,
        "format": "store_agent_v1",
        "status": "dry_run" if mode == "dry_run" else "applied",
        "summary": {
            "operation": action,
            "mode": mode,
            "target_id": target_id,
            "changed": any(change["before"] != change["after"] for change in changes),
            "result": prospective,
        },
        "items": [],
        "changes": changes,
        "page": {"has_more": False, "next_cursor": None, "limit": 0},
        "verification": {"passed": not state.get("manager_verification_failed", False)},
        "warnings": [
            *(["external_notifier_is_best_effort"] if action == "mark_order_ready" else []),
            *(["dry_run_proof_expires_in_30_minutes"] if mode == "dry_run" else []),
        ],
        "meta": {
            "correlation_id": correlation_id,
            "idempotency_key": idempotency_key,
            "idempotency_replay": False,
            "owner_intent_present": bool(owner_intent.strip()),
            "owner_intent_sha256": hashlib.sha256(
                f"intent:{owner_intent.strip()}".encode()
            ).hexdigest(),
            "effects": effects,
            "external_effect_state": external_effect_state,
            "dry_run_proof_ttl_seconds": 1800,
        },
    }
    state["store_receipts"][receipt_key] = {
        "request_hash": request_hash,
        "response": copy.deepcopy(response),
        "external_effect_state": external_effect_state,
    }
    if mode == "dry_run":
        state["store_dry_run_proofs"].append(
            {
                "principal": principal,
                "correlation_id": correlation_id,
                "plan_hash": plan_hash,
                "created_at": state["store_clock"],
                "idempotency_key": idempotency_key,
            }
        )

    if mode == "apply" and (
        state.get("manager_compensating") or state.pop("manager_compensating_once", False)
    ):
        return {
            "ok": False,
            "format": "store_agent_v1",
            "status": "compensating",
            "summary": {"write_applied_unverified": True},
            "items": [],
            "changes": changes,
            "page": {},
            "verification": {"passed": False},
            "warnings": ["store_apply_readback_failed"],
            "meta": {
                "correlation_id": correlation_id,
                "external_effect_state": external_effect_state,
            },
        }
    return response


def _prepare_fake_store_state(state: dict) -> None:
    state.setdefault("store_available", True)
    state.setdefault("calls", [])
    state.setdefault("store_clock", 0)
    state.setdefault("store_principal", "store-manager-principal")
    state.setdefault("store_receipts", {})
    state.setdefault("store_dry_run_proofs", [])
    state.setdefault("store_apply_sequence", 0)
    state.setdefault("store_post_count", 0)
    state.setdefault("workflow_runs", {})
    state.setdefault("workflow_keys", {})
    state.setdefault("next_run_id", 501)
    state.setdefault("comment_values", {"quote-1": ""})
    state.setdefault(
        "entities",
        {
            ("store_quote_request", "quote-1"): {
                "id": "quote-1",
                "entity_type": "store_quote_request",
                "entity_id": "quote-1",
                "request_number": "QR-1",
                "updated_at": "2026-07-16T10:00:00+00:00",
                "status": "NEW",
                "assigned_user_id": None,
                "has_internal_comment": False,
                "internal_comment_sha256": hashlib.sha256(b"none:").hexdigest(),
            },
            ("store_batch", "batch-1"): {
                "id": "batch-1",
                "entity_type": "store_batch",
                "entity_id": "batch-1",
                "part_id": "part-1",
                "updated_at": "2026-07-16T10:00:00+00:00",
                "storage_location": "A-1",
            },
            ("store_order", "order-1"): {
                "id": "order-1",
                "entity_type": "store_order",
                "entity_id": "order-1",
                "order_number": "SO-1",
                "updated_at": "2026-07-16T10:00:00+00:00",
                "status": "IN_PROGRESS",
                "ready_at": None,
            },
        },
    )


def _fake_store_bootstrap_snapshot() -> dict:
    return {
        "ok": True,
        "format": "store_agent_v1",
        "status": "ok",
        "summary": {
            "store_api_ready": True,
            "product_count": 42,
            "active_order_count": 3,
            "open_quote_request_count": 2,
            "inventory": {
                "position_count": 40,
                "physical_qty": 100,
                "reserved_qty": 7,
                "available_qty": 93,
            },
            "marketplaces": {
                "active_accounts": 2,
                "export_errors": {
                    "counts": {"last_24_hours": 1, "last_7_days": 2, "all_time": 3},
                    "latest": [],
                },
            },
            "contract_version": "store_agent_v1",
            "bootstrap_snapshot_version": 1,
        },
        "items": [],
        "changes": [],
        "page": {"has_more": False, "next_cursor": None},
        "warnings": [],
        "meta": {},
    }


def register_fake_store_manager_tools(server, _logger, state: dict) -> None:
    _prepare_fake_store_state(state)

    @server.tool(name="store_runtime_status", description="INTERNAL_ONLY store runtime")
    def store_runtime_status(
        live: bool = False,
        bootstrap_snapshot: bool = False,
    ) -> dict:
        state["calls"].append(
            (
                "store_runtime_status",
                {"live": live, "bootstrap_snapshot": bootstrap_snapshot},
            )
        )
        if not state["store_available"]:
            return _fake_store_unavailable()
        if bootstrap_snapshot:
            return _fake_store_bootstrap_snapshot()
        return {
            "ok": True,
            "format": "store_agent_v1",
            "status": "ready",
            "summary": {"adapter": "ready"},
            "items": [],
            "page": {},
            "warnings": [],
            "meta": {},
        }

    @server.tool(name="store_digest", description="INTERNAL_ONLY store digest")
    def store_digest(
        baseline: bool = False,
        since: str | None = None,
        cursor: str | None = None,
        ack_token: str | None = None,
        limit: int = 25,
        stream: str = "store_digest",
    ) -> dict:
        state["calls"].append(
            (
                "store_digest",
                {
                    "baseline": baseline,
                    "since": since,
                    "cursor": cursor,
                    "ack_token": ack_token,
                    "limit": limit,
                    "stream": stream,
                },
            )
        )
        if not state["store_available"]:
            return _fake_store_unavailable()
        if state.get("store_digest_ack_mode"):
            suffix = "bootstrap" if stream == "store_bootstrap" else "digest"
            first_cursor = f"manager-{suffix}-page-1"
            final_cursor = f"manager-{suffix}-final-ack"
            if cursor is None and ack_token is None:
                items = [{"id": f"{suffix}-order-1", "status": "IN_PROGRESS"}]
                page = {
                    "next_cursor": first_cursor,
                    "has_more": True,
                    "ack_required": True,
                    "ack_token": f"{suffix}-ack-1",
                }
            elif cursor == first_cursor and ack_token == f"{suffix}-ack-1":
                items = [{"id": f"{suffix}-order-2", "status": "READY"}]
                page = {
                    "next_cursor": final_cursor,
                    "has_more": True,
                    "ack_required": True,
                    "ack_token": f"{suffix}-ack-2",
                }
            elif cursor == final_cursor and ack_token == f"{suffix}-ack-2":
                items = []
                page = {
                    "next_cursor": None,
                    "has_more": False,
                    "ack_required": False,
                    "ack_token": None,
                }
            else:
                return {
                    **_fake_store_unavailable(),
                    "status": "conflict",
                    "warnings": ["store_digest_ack_stale_or_foreign"],
                }
            return {
                "ok": True,
                "format": "store_agent_v1",
                "status": "completed",
                "summary": {"new_orders": len(items), "stream": stream},
                "items": items,
                "page": page,
                "warnings": [],
                "meta": {},
            }
        return {
            "ok": True,
            "format": "store_agent_v1",
            "status": "completed",
            "summary": {"new_orders": 1, "stream": stream},
            "items": [{"id": "order-1", "status": "IN_PROGRESS"}],
            "page": {"cursor": cursor, "next_cursor": "opaque-2", "has_more": False},
            "warnings": [],
            "meta": {},
        }

    @server.tool(name="store_search", description="INTERNAL_ONLY store search")
    def store_search(
        entity: str,
        query: str = "",
        filters: dict | None = None,
        cursor: str | None = None,
        limit: int = 25,
    ) -> dict:
        arguments = {
            "entity": entity,
            "query": query,
            "filters": dict(filters or {}),
            "cursor": cursor,
            "limit": limit,
        }
        state["calls"].append(("store_search", arguments))
        if not state["store_available"]:
            return _fake_store_unavailable()
        items = [
            dict(value)
            for (candidate_entity, _), value in state["entities"].items()
            if candidate_entity == entity
        ][:limit]
        return {
            "ok": True,
            "format": "store_agent_v1",
            "status": "completed",
            "summary": {"entity": entity, "returned": len(items)},
            "items": items,
            "page": {"cursor": cursor, "next_cursor": None, "has_more": False},
            "warnings": [],
            "meta": {},
        }

    @server.tool(name="store_entity_context", description="INTERNAL_ONLY store context")
    def store_entity_context(entity: str, entity_id: str, detail: str = "summary") -> dict:
        state["calls"].append(
            (
                "store_entity_context",
                {"entity": entity, "entity_id": entity_id, "detail": detail},
            )
        )
        if not state["store_available"]:
            return _fake_store_unavailable()
        item = state["entities"].get((entity, entity_id))
        return {
            "ok": item is not None,
            "format": "store_agent_v1",
            "status": "completed" if item is not None else "failed",
            "summary": {"entity": entity, "entity_id": entity_id},
            "items": [dict(item)] if item is not None else [],
            "page": {},
            "warnings": [] if item is not None else ["store_entity_not_found"],
            "meta": {},
        }

    @server.tool(
        name="get_store_analytics_report",
        description="Read-only storefront analytics report",
        annotations=ToolAnnotations(
            title="Store Analytics Report",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def get_store_analytics_report(period: str = "today") -> dict:
        state["calls"].append(("get_store_analytics_report", {"period": period}))
        return {"ok": True, "data": {"period": period, "orders_count": 3}}

    @server.tool(name="store_management_action", description="INTERNAL_ONLY store write")
    def store_management_action(
        domain: str,
        action: str,
        target_id: str,
        planned_changes: dict,
        owner_intent: str,
        expected_updated_at: str,
        idempotency_key: str,
        correlation_id: str,
        mode: str = "dry_run",
    ) -> dict:
        arguments = {
            "domain": domain,
            "action": action,
            "target_id": target_id,
            "planned_changes": dict(planned_changes),
            "owner_intent": owner_intent,
            "expected_updated_at": expected_updated_at,
            "idempotency_key": idempotency_key,
            "correlation_id": correlation_id,
            "mode": mode,
        }
        state["calls"].append(("store_management_action", arguments))
        state["store_post_count"] += 1
        return _fake_store_management_action(state, arguments)

    @server.tool(name="start_workflow")
    def start_workflow(
        workflow_id: str,
        intent: str,
        idempotency_key: str,
        query: str = "",
        actor: str = "",
        correlation_id: str = "",
        scope: dict | None = None,
        metadata: dict | None = None,
        dry_run: bool = False,
    ) -> dict:
        state["calls"].append(
            (
                "start_workflow",
                {
                    "workflow_id": workflow_id,
                    "intent": intent,
                    "idempotency_key": idempotency_key,
                    "query": query,
                    "actor": actor,
                    "correlation_id": correlation_id,
                    "scope": scope,
                    "metadata": metadata,
                    "dry_run": dry_run,
                },
            )
        )
        existing_run_id = state["workflow_keys"].get(idempotency_key)
        if existing_run_id is not None:
            run = state["workflow_runs"][existing_run_id]
            if (
                run["workflow_id"] != workflow_id
                or run["intent"] != intent
                or run["scope"] != dict(scope or {})
                or run["dry_run"] != dry_run
            ):
                return {
                    "ok": False,
                    "status": run["status"],
                    "run_id": existing_run_id,
                    "summary": {
                        "id": existing_run_id,
                        "deduplicated": False,
                        "state_version": run["state_version"],
                    },
                    "warnings": ["idempotency_key_conflict"],
                }
            return {
                "ok": True,
                "run_id": existing_run_id,
                "status": run["status"],
                "summary": {
                    "id": existing_run_id,
                    "deduplicated": True,
                    "state_version": run["state_version"],
                },
            }
        run_id = state["next_run_id"]
        state["next_run_id"] += 1
        run = {
            "workflow_id": workflow_id,
            "intent": intent,
            "scope": dict(scope or {}),
            "dry_run": dry_run,
            "status": "planned",
            "state_version": 1,
        }
        state["workflow_runs"][run_id] = run
        state["workflow_keys"][idempotency_key] = run_id
        return {
            "ok": True,
            "run_id": run_id,
            "status": run["status"],
            "summary": {
                "id": run_id,
                "deduplicated": False,
                "state_version": run["state_version"],
            },
        }

    @server.tool(name="workflow_transition")
    def workflow_transition(
        run_id: int,
        status: str,
        message: str = "",
        verification: dict | None = None,
        summary: str = "",
        expected_state_version: int | None = None,
    ) -> dict:
        state["calls"].append(
            (
                "workflow_transition",
                {
                    "run_id": run_id,
                    "status": status,
                    "message": message,
                    "verification": verification,
                    "summary": summary,
                    "expected_state_version": expected_state_version,
                },
            )
        )
        run = state["workflow_runs"].get(run_id)
        if run is None:
            return {"ok": False, "status": "failed", "warnings": ["run_not_found"]}
        if expected_state_version is not None and expected_state_version != run["state_version"]:
            return {
                "ok": False,
                "run_id": run_id,
                "status": run["status"],
                "summary": {"state_version": run["state_version"]},
                "warnings": ["state_version_conflict"],
            }
        run["status"] = status
        run["state_version"] += 1
        return {
            "ok": True,
            "run_id": run_id,
            "status": status,
            "summary": {"state_version": run["state_version"]},
        }

    @server.tool(name="list_agent_workflows")
    def list_agent_workflows(query: str = "", intent: str | None = None, limit: int = 50) -> dict:
        del query, intent, limit
        return {"ok": True, "summary": {"items": []}}

    @server.tool(name="prepare_action_contract")
    def prepare_action_contract(
        domain: str,
        action: str,
        target_id: str = "",
        planned_changes: dict | None = None,
        owner_intent: str = "",
        expected_revision: str | None = None,
        idempotency_key: str = "",
        run_id: int | None = None,
        actor: str = "codex-owner-agent",
        dry_run: bool = True,
    ) -> dict:
        del (
            domain,
            action,
            target_id,
            planned_changes,
            owner_intent,
            expected_revision,
            idempotency_key,
            run_id,
            actor,
            dry_run,
        )
        return {"ok": True, "summary": {}}

    @server.tool(name="workflow_status")
    def workflow_status(
        run_id: int, include_events: bool = False, include_external_steps: bool = True
    ) -> dict:
        del include_events, include_external_steps
        run = state["workflow_runs"].get(run_id)
        return {
            "ok": run is not None,
            "run_id": run_id,
            "status": run["status"] if run is not None else "failed",
            "summary": {"state_version": run["state_version"]} if run is not None else {},
        }

    @server.tool(name="workflow_checkpoint")
    def workflow_checkpoint(
        run_id: int,
        checkpoint: dict,
        selected_ids: list[str] | None = None,
        message: str = "",
        expected_state_version: int | None = None,
    ) -> dict:
        del checkpoint, selected_ids, message, expected_state_version
        run = state["workflow_runs"].get(run_id)
        return {
            "ok": run is not None,
            "run_id": run_id,
            "status": run["status"] if run is not None else "failed",
            "summary": {},
        }

    @server.tool(name="workflow_wait_for_external")
    def workflow_wait_for_external(
        run_id: int,
        step_id: str,
        connector: str,
        action: str,
        request_refs: dict | None = None,
        expected_state_version: int | None = None,
    ) -> dict:
        del step_id, connector, action, request_refs, expected_state_version
        return {"ok": True, "run_id": run_id, "status": "external_wait", "summary": {}}

    @server.tool(name="complete_external_step")
    def complete_external_step(
        run_id: int,
        step_id: str,
        result_refs: dict | None = None,
        expected_state_version: int | None = None,
    ) -> dict:
        del step_id, result_refs, expected_state_version
        return {"ok": True, "run_id": run_id, "status": "external_wait", "summary": {}}

    @server.tool(name="workflow_resume")
    def workflow_resume(run_id: int, expected_state_version: int | None = None) -> dict:
        del expected_state_version
        return {"ok": True, "run_id": run_id, "status": "executing", "summary": {}}

    @server.tool(name="workflow_cancel")
    def workflow_cancel(
        run_id: int, reason: str = "", expected_state_version: int | None = None
    ) -> dict:
        del reason, expected_state_version
        return {"ok": True, "run_id": run_id, "status": "cancelled", "summary": {}}


class AgentGatewayV2Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(self._testMethodName)
        self.logger.addHandler(logging.NullHandler())
        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.manager_patch = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        self.env.start()
        self.manager_register = self.manager_patch.start()
        self.board_api = FakeBoardApi()
        self.server = create_mcp_server(
            self.board_api,
            self.logger,
            host="127.0.0.1",
            port=41831,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

    def tearDown(self) -> None:
        self.manager_patch.stop()
        self.env.stop()

    async def _call(self, name: str, arguments: dict | None = None):
        tool = self.server._tool_manager.get_tool(name)
        self.assertIsNotNone(tool)
        return await tool.run(arguments or {}, convert_result=False)

    def _create_store_server(self, state: dict | None = None):
        store_state = state if state is not None else {}
        self.manager_register.side_effect = lambda server, logger: (
            register_fake_store_manager_tools(server, logger, store_state)
        )
        server = create_mcp_server(
            FakeBoardApi(),
            self.logger,
            host="127.0.0.1",
            port=41839,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )
        return server, store_state

    async def _store_write(
        self,
        server,
        *,
        operation: str,
        payload: dict,
        idempotency_key: str,
        mode: str,
    ):
        return await server._tool_manager.get_tool("agent_inventory_workflow").run(
            {
                "operation": operation,
                "payload": payload,
                "idempotency_key": idempotency_key,
                "mode": mode,
            },
            convert_result=False,
        )

    async def _store_dry_apply(
        self,
        server,
        *,
        operation: str,
        payload: dict,
        key_prefix: str,
    ):
        dry_run = await self._store_write(
            server,
            operation=operation,
            payload={**payload, "owner_intent": f"Preview {key_prefix}"},
            idempotency_key=f"{key_prefix}-dry-run",
            mode="dry_run",
        )
        apply = await self._store_write(
            server,
            operation=operation,
            payload={**payload, "owner_intent": f"Apply {key_prefix}"},
            idempotency_key=f"{key_prefix}-apply",
            mode="apply",
        )
        return dry_run, apply

    async def test_v2_replaces_full_surface_with_compact_tools(self) -> None:
        names = {tool.name for tool in self.server._tool_manager.list_tools()}
        self.assertLessEqual(len(names), 25)
        self.assertIn("agent_bootstrap", names)
        self.assertIn("agent_board_digest", names)
        self.assertIn("call_raw_capability", names)
        self.assertIn("ping_connector", names)
        self.assertNotIn("get_cards", names)
        self.assertNotIn("create_cash_transaction", names)

    async def test_store_enabled_surface_remains_exactly_24_tools(self) -> None:
        server, _state = self._create_store_server()

        names = {tool.name for tool in server._tool_manager.list_tools()}

        self.assertEqual(24, len(names))
        self.assertEqual(4, len(STORE_READ_CAPABILITY_NAMES))
        self.assertEqual(
            {*STORE_READ_CAPABILITY_NAMES, STORE_MANAGEMENT_CAPABILITY_NAME},
            set(INTERNAL_ONLY_CAPABILITY_NAMES),
        )
        self.assertNotIn("store_digest", names)
        self.assertNotIn("store_management_action", names)

    async def test_store_analytics_remains_raw_discoverable_and_read_only(self) -> None:
        server, _state = self._create_store_server()
        names = {tool.name for tool in server._tool_manager.list_tools()}
        discovered = await server._tool_manager.get_tool("discover_raw_capabilities").run(
            {"query": "get_store_analytics_report"}, convert_result=False
        )

        self.assertEqual(24, len(names))
        self.assertNotIn("get_store_analytics_report", names)
        self.assertEqual(
            [
                {
                    "name": "get_store_analytics_report",
                    "description": "Read-only storefront analytics report",
                    "risk": "read",
                    "schema_hash": discovered.structuredContent["data"]["capabilities"][0][
                        "schema_hash"
                    ],
                }
            ],
            discovered.structuredContent["data"]["capabilities"],
        )

    async def test_existing_crm_tool_schemas_remain_backward_compatible(self) -> None:
        bootstrap_schema = self.server._tool_manager.get_tool("agent_bootstrap").parameters
        board_schema = self.server._tool_manager.get_tool("agent_board_digest").parameters
        search_schema = self.server._tool_manager.get_tool("agent_search").parameters
        context_schema = self.server._tool_manager.get_tool("agent_entity_context").parameters
        inventory_schema = self.server._tool_manager.get_tool("agent_inventory_workflow").parameters

        self.assertEqual("crm", board_schema["properties"]["scope"]["default"])
        self.assertTrue(
            {"include_archived", "cursor", "limit", "fields"} <= set(board_schema["properties"])
        )
        self.assertIn("ack_token", board_schema["properties"])
        self.assertNotIn("store_cursor", bootstrap_schema["properties"])
        self.assertNotIn("store_ack_token", bootstrap_schema["properties"])
        self.assertNotIn("ack_token", board_schema.get("required", []))
        self.assertTrue(
            {"card", "client", "repair_order", "inventory", "cashbox", "file"}
            <= set(search_schema["properties"]["entity"]["enum"])
        )
        self.assertTrue(
            {"card", "client", "repair_order", "cashbox", "inventory", "file"}
            <= set(context_schema["properties"]["entity"]["enum"])
        )
        self.assertEqual({"summary", "full"}, set(context_schema["properties"]["detail"]["enum"]))
        self.assertEqual(
            {"operation", "payload", "idempotency_key"},
            set(inventory_schema["required"]),
        )
        self.assertIsNone(inventory_schema["properties"]["mode"]["default"])

    async def test_store_bootstrap_snapshot_is_one_store_call_and_digest_keeps_owner_stream(
        self,
    ) -> None:
        server, state = self._create_store_server()

        bootstrap = await server._tool_manager.get_tool("agent_bootstrap").run(
            {}, convert_result=False
        )
        digest = await server._tool_manager.get_tool("agent_board_digest").run(
            {"scope": "store", "cursor": "opaque-1", "limit": 3},
            convert_result=False,
        )

        self.assertTrue(bootstrap.structuredContent["ok"])
        self.assertTrue(bootstrap.structuredContent["summary"]["store"]["ok"])
        self.assertEqual(
            42,
            bootstrap.structuredContent["summary"]["store"]["snapshot"]["product_count"],
        )
        self.assertEqual("order-1", digest.structuredContent["data"]["items"][0]["id"])
        self.assertEqual("opaque-2", digest.structuredContent["page"]["next_cursor"])
        bootstrap_calls = [
            arguments
            for name, arguments in state["calls"]
            if name == "store_runtime_status" and arguments["bootstrap_snapshot"]
        ]
        digest_calls = [arguments for name, arguments in state["calls"] if name == "store_digest"]
        self.assertEqual(
            [{"live": True, "bootstrap_snapshot": True}],
            bootstrap_calls,
        )
        self.assertEqual(1, len(digest_calls))
        self.assertEqual("store_digest", digest_calls[0]["stream"])

    async def test_store_digest_ack_traverses_first_next_and_final_without_new_tool(self) -> None:
        server, state = self._create_store_server({"store_digest_ack_mode": True})
        tool = server._tool_manager.get_tool("agent_board_digest")

        first = await tool.run({"scope": "store", "limit": 1}, convert_result=False)
        first_page = first.structuredContent["page"]
        second = await tool.run(
            {
                "scope": "store",
                "cursor": first_page["next_cursor"],
                "ack_token": first_page["ack_token"],
                "limit": 1,
            },
            convert_result=False,
        )
        second_page = second.structuredContent["page"]
        final = await tool.run(
            {
                "scope": "store",
                "cursor": second_page["next_cursor"],
                "ack_token": second_page["ack_token"],
                "limit": 1,
            },
            convert_result=False,
        )

        self.assertEqual("digest-order-1", first.structuredContent["data"]["items"][0]["id"])
        self.assertEqual("digest-order-2", second.structuredContent["data"]["items"][0]["id"])
        self.assertFalse(final.structuredContent["page"]["has_more"])
        self.assertIsNone(final.structuredContent["page"]["next_cursor"])
        calls = [arguments for name, arguments in state["calls"] if name == "store_digest"]
        self.assertEqual("digest-ack-1", calls[1]["ack_token"])
        self.assertEqual("digest-ack-2", calls[2]["ack_token"])

    async def test_store_bootstrap_snapshot_never_uses_digest_ack_or_pagination(self) -> None:
        server, state = self._create_store_server({"store_digest_ack_mode": True})
        tool = server._tool_manager.get_tool("agent_bootstrap")

        result = await tool.run({}, convert_result=False)

        snapshot = result.structuredContent["summary"]["store"]["snapshot"]
        self.assertTrue(snapshot["store_api_ready"])
        self.assertNotIn("page", snapshot)
        calls = [arguments for name, arguments in state["calls"] if name == "store_digest"]
        self.assertEqual([], calls)
        snapshot_calls = [
            arguments
            for name, arguments in state["calls"]
            if name == "store_runtime_status" and arguments["bootstrap_snapshot"]
        ]
        self.assertEqual([{"live": True, "bootstrap_snapshot": True}], snapshot_calls)

    async def test_store_search_and_exact_context_use_existing_public_tools(self) -> None:
        server, state = self._create_store_server()

        search = await server._tool_manager.get_tool("agent_search").run(
            {
                "entity": "store_order",
                "query": "order-1",
                "filters": {"status": "IN_PROGRESS"},
                "cursor": "opaque-search",
                "limit": 5,
            },
            convert_result=False,
        )
        context = await server._tool_manager.get_tool("agent_entity_context").run(
            {"entity": "store_order", "entity_id": "order-1", "detail": "full"},
            convert_result=False,
        )

        self.assertEqual("order-1", search.structuredContent["data"]["items"][0]["id"])
        self.assertEqual("order-1", context.structuredContent["data"]["items"][0]["id"])
        search_call = next(
            arguments for name, arguments in state["calls"] if name == "store_search"
        )
        self.assertEqual({"status": "IN_PROGRESS"}, search_call["filters"])
        self.assertEqual("opaque-search", search_call["cursor"])

    async def test_store_outage_degrades_store_without_breaking_crm(self) -> None:
        server, state = self._create_store_server({"store_available": False})

        bootstrap = await server._tool_manager.get_tool("agent_bootstrap").run(
            {}, convert_result=False
        )
        runtime = await server._tool_manager.get_tool("get_runtime_status").run(
            {}, convert_result=False
        )
        crm_digest = await server._tool_manager.get_tool("agent_board_digest").run(
            {"scope": "crm", "limit": 1}, convert_result=False
        )

        self.assertTrue(bootstrap.structuredContent["ok"])
        self.assertEqual("degraded", bootstrap.structuredContent["status"])
        self.assertIn("store_adapter_degraded", bootstrap.structuredContent["warnings"])
        self.assertTrue(runtime.structuredContent["ok"])
        self.assertEqual("degraded", runtime.structuredContent["status"])
        self.assertFalse(runtime.structuredContent["data"]["store"]["ok"])
        self.assertTrue(crm_digest.structuredContent["ok"])
        runtime_calls = [
            arguments for name, arguments in state["calls"] if name == "store_runtime_status"
        ]
        self.assertTrue(runtime_calls[-1]["live"])

    async def test_all_store_hidden_tools_are_excluded_from_raw_escape(self) -> None:
        server, _state = self._create_store_server()
        discover = server._tool_manager.get_tool("discover_raw_capabilities")
        schema = server._tool_manager.get_tool("get_raw_capability_schema")
        raw_call = server._tool_manager.get_tool("call_raw_capability")

        for name in (
            "store_runtime_status",
            "store_digest",
            "store_search",
            "store_entity_context",
            "store_management_action",
        ):
            with self.subTest(name=name):
                discovered = await discover.run({"query": name}, convert_result=False)
                self.assertEqual([], discovered.structuredContent["data"]["capabilities"])
                blocked_schema = await schema.run({"name": name}, convert_result=False)
                blocked_call = await raw_call.run(
                    {"name": name, "arguments": {}, "schema_hash": "irrelevant"},
                    convert_result=False,
                )
                expected = (
                    "named_workflow_required"
                    if name == "store_management_action"
                    else "named_operation_required"
                )
                self.assertIn(expected, blocked_schema.structuredContent["warnings"])
                self.assertIn(expected, blocked_call.structuredContent["warnings"])

                disguised = f" \t{name}\n"
                disguised_schema = await schema.run({"name": disguised}, convert_result=False)
                disguised_call = await raw_call.run(
                    {
                        "name": disguised,
                        "arguments": {},
                        "schema_hash": "irrelevant",
                    },
                    convert_result=False,
                )
                self.assertIn(expected, disguised_schema.structuredContent["warnings"])
                self.assertIn(expected, disguised_call.structuredContent["warnings"])

    async def test_store_write_requires_explicit_mode_and_owner_intent_before_ledger(
        self,
    ) -> None:
        server, state = self._create_store_server()
        tool = server._tool_manager.get_tool("agent_inventory_workflow")
        base_payload = {
            "target_id": "order-1",
            "expected_updated_at": "2026-07-16T10:00:00+00:00",
            "owner_intent": "Mark this exact store order ready",
            "planned_changes": {"status": "READY"},
        }

        missing_mode = await tool.run(
            {
                "operation": "mark_order_ready",
                "payload": base_payload,
                "idempotency_key": "store-order-ready-1",
            },
            convert_result=False,
        )
        missing_owner_intent = await tool.run(
            {
                "operation": "mark_order_ready",
                "payload": {**base_payload, "owner_intent": ""},
                "idempotency_key": "store-order-ready-2",
                "mode": "dry_run",
            },
            convert_result=False,
        )

        self.assertIn(
            "store_mode_required_explicit_dry_run_or_apply",
            missing_mode.structuredContent["warnings"],
        )
        self.assertIn(
            "store_write_exact_target_revision_owner_intent_and_idempotency_required",
            missing_owner_intent.structuredContent["warnings"],
        )
        self.assertFalse(any(name == "start_workflow" for name, _ in state["calls"]))

    async def test_store_write_revision_conflict_closes_ledger_before_executor(self) -> None:
        server, state = self._create_store_server()

        result = await server._tool_manager.get_tool("agent_inventory_workflow").run(
            {
                "operation": "mark_order_ready",
                "payload": {
                    "target_id": "order-1",
                    "expected_updated_at": "stale-revision",
                    "owner_intent": "Mark this exact store order ready",
                    "planned_changes": {"status": "READY"},
                },
                "idempotency_key": "store-order-ready-conflict",
                "mode": "apply",
            },
            convert_result=False,
        )

        self.assertFalse(result.structuredContent["ok"])
        self.assertIn(
            "store_preflight_exact_target_or_revision_failed",
            result.structuredContent["warnings"],
        )
        start_call = next(
            arguments for name, arguments in state["calls"] if name == "start_workflow"
        )
        self.assertEqual("store", start_call["scope"]["domain"])
        self.assertEqual("store", start_call["scope"]["source"])
        self.assertFalse(
            any(name == "store_management_action" for name, _arguments in state["calls"])
        )
        self.assertEqual(
            ["failed"],
            [
                arguments["status"]
                for name, arguments in state["calls"]
                if name == "workflow_transition"
            ],
        )
        failed_transition = next(
            arguments for name, arguments in state["calls"] if name == "workflow_transition"
        )
        self.assertEqual("failed mark_order_ready", failed_transition["message"])
        self.assertTrue(
            all(isinstance(value, bool) for value in failed_transition["verification"].values())
        )

    async def test_store_all_real_shaped_actions_share_plan_correlation_and_verify(self) -> None:
        pii_comment = (
            "Иван Петров, +7 999 111-22-33, ivan@example.com, XTA210990Y1234567, token-secret"
        )
        cases = (
            (
                "assign_quote_request",
                "quote-1",
                {"assignee_id": "user-1"},
                ["assigned_user_id"],
                ("assigned_user_id", "user-1"),
            ),
            (
                "set_quote_request_status",
                "quote-1",
                {"status": "IN_PROGRESS"},
                ["status"],
                ("status", "IN_PROGRESS"),
            ),
            (
                "update_quote_request_comment",
                "quote-1",
                {"internal_comment": pii_comment},
                ["has_internal_comment", "internal_comment_sha256"],
                (
                    "internal_comment_sha256",
                    hashlib.sha256(f"comment:{pii_comment}".encode()).hexdigest(),
                ),
            ),
            (
                "set_batch_storage_location",
                "batch-1",
                {"storage_location": "B-22"},
                ["storage_location"],
                ("storage_location", "B-22"),
            ),
            (
                "mark_order_ready",
                "order-1",
                {"status": "READY"},
                ["status", "ready_at"],
                ("status", "READY"),
            ),
        )

        for index, (operation, target_id, changes, change_fields, result_pair) in enumerate(cases):
            with self.subTest(operation=operation):
                server, state = self._create_store_server()
                dry_run, apply = await self._store_dry_apply(
                    server,
                    operation=operation,
                    payload={
                        "target_id": target_id,
                        "expected_updated_at": "2026-07-16T10:00:00+00:00",
                        "planned_changes": changes,
                    },
                    key_prefix=f"store-real-{index}",
                )

                self.assertTrue(dry_run.structuredContent["ok"])
                self.assertTrue(apply.structuredContent["ok"])
                self.assertEqual("completed", apply.structuredContent["status"])
                self.assertEqual(
                    change_fields,
                    [item["field"] for item in apply.structuredContent["data"]["changes"]],
                )
                self.assertEqual(
                    result_pair[1],
                    apply.structuredContent["data"]["result"][result_pair[0]],
                )
                management_calls = [
                    arguments
                    for name, arguments in state["calls"]
                    if name == "store_management_action"
                ]
                self.assertEqual(2, len(management_calls))
                self.assertNotEqual(
                    management_calls[0]["idempotency_key"],
                    management_calls[1]["idempotency_key"],
                )
                self.assertEqual(
                    management_calls[0]["correlation_id"],
                    management_calls[1]["correlation_id"],
                )
                self.assertTrue(management_calls[0]["correlation_id"].startswith("store-action-"))
                ledger_scopes = [
                    arguments["scope"]
                    for name, arguments in state["calls"]
                    if name == "start_workflow"
                ]
                self.assertEqual(["store", "store"], [scope["domain"] for scope in ledger_scopes])
                self.assertEqual(["store", "store"], [scope["source"] for scope in ledger_scopes])
                ledger_starts = [
                    arguments for name, arguments in state["calls"] if name == "start_workflow"
                ]
                for start in ledger_starts:
                    self.assertEqual("", start["query"])
                    self.assertIsNone(start["metadata"])
                    self.assertEqual(
                        {"operation", "mode", "request_fingerprint", "domain", "source"},
                        set(start["scope"]),
                    )
                ledger_transitions = [
                    arguments for name, arguments in state["calls"] if name == "workflow_transition"
                ]
                self.assertEqual(
                    ["executing", "verifying", "completed"] * 2,
                    [transition["status"] for transition in ledger_transitions],
                )
                for transition in ledger_transitions:
                    verification = transition["verification"]
                    if verification is not None:
                        self.assertTrue(verification)
                        self.assertTrue(
                            all(isinstance(value, bool) for value in verification.values())
                        )
                        self.assertFalse(
                            any(isinstance(value, (dict, list)) for value in verification.values())
                        )
                if operation == "update_quote_request_comment":
                    public_payload = json.dumps(
                        apply.structuredContent, ensure_ascii=False, sort_keys=True
                    )
                    self.assertNotIn(pii_comment, public_payload)
                    self.assertNotIn(
                        "internal_comment", state["entities"][("store_quote_request", target_id)]
                    )

    async def test_store_ready_dry_run_discloses_possible_customer_notification(self) -> None:
        server, _state = self._create_store_server()

        result = await server._tool_manager.get_tool("agent_inventory_workflow").run(
            {
                "operation": "mark_order_ready",
                "payload": {
                    "target_id": "order-1",
                    "expected_updated_at": "2026-07-16T10:00:00+00:00",
                    "owner_intent": "Preview marking this exact store order ready",
                    "planned_changes": {"status": "READY"},
                },
                "idempotency_key": "store-order-ready-dry-run",
                "mode": "dry_run",
            },
            convert_result=False,
        )

        self.assertTrue(result.structuredContent["ok"])
        self.assertIn(
            "external_notifier_is_best_effort",
            result.structuredContent["warnings"],
        )
        self.assertIn(
            "attempt_external_customer_notifier_after_commit",
            {item["effect"] for item in result.structuredContent["data"]["effects"]},
        )
        self.assertEqual(
            "NOT_APPLICABLE",
            result.structuredContent["data"]["external_effect_state"],
        )
        self.assertFalse(result.structuredContent["data"]["idempotency_replay"])
        self.assertTrue(result.structuredContent["data"]["correlation_id"])
        self.assertEqual(1800, result.structuredContent["data"]["dry_run_proof_ttl_seconds"])
        notifier = next(
            effect
            for effect in result.structuredContent["data"]["effects"]
            if effect["effect"] == "attempt_external_customer_notifier_after_commit"
        )
        self.assertEqual(
            {
                "configured": False,
                "customer_linked": True,
                "cached_chat_available": False,
                "deliverability": "FAILED",
            },
            {
                key: notifier[key]
                for key in (
                    "configured",
                    "customer_linked",
                    "cached_chat_available",
                    "deliverability",
                )
            },
        )
        self.assertNotIn("owner_intent", result.structuredContent["data"])
        self.assertNotIn("idempotency_key", result.structuredContent["data"])

    async def test_store_comment_clear_is_canonical_and_verified_in_both_modes(self) -> None:
        server, state = self._create_store_server()
        dry_run, apply = await self._store_dry_apply(
            server,
            operation="update_quote_request_comment",
            payload={
                "target_id": "quote-1",
                "expected_updated_at": "2026-07-16T10:00:00+00:00",
                "planned_changes": {"internal_comment": "   "},
            },
            key_prefix="store-comment-clear",
        )

        self.assertTrue(dry_run.structuredContent["ok"])
        self.assertTrue(apply.structuredContent["ok"])
        self.assertTrue(dry_run.structuredContent["verification"]["passed"])
        self.assertTrue(apply.structuredContent["verification"]["passed"])
        management_calls = [
            arguments for name, arguments in state["calls"] if name == "store_management_action"
        ]
        self.assertEqual(
            [{"internal_comment": None}, {"internal_comment": None}],
            [call["planned_changes"] for call in management_calls],
        )
        quote = state["entities"][("store_quote_request", "quote-1")]
        self.assertFalse(quote["has_internal_comment"])
        self.assertEqual(hashlib.sha256(b"none:").hexdigest(), quote["internal_comment_sha256"])

    async def test_store_direct_apply_is_blocked_without_fresh_matching_dry_run(self) -> None:
        server, state = self._create_store_server()

        result = await self._store_write(
            server,
            operation="mark_order_ready",
            payload={
                "target_id": "order-1",
                "expected_updated_at": "2026-07-16T10:00:00+00:00",
                "owner_intent": "Apply without proof",
                "planned_changes": {"status": "READY"},
            },
            idempotency_key="store-direct-apply",
            mode="apply",
        )

        self.assertFalse(result.structuredContent["ok"])
        self.assertIn("AGENT_DRY_RUN_REQUIRED", result.structuredContent["warnings"])
        self.assertEqual(1, state["store_post_count"])
        self.assertEqual("IN_PROGRESS", state["entities"][("store_order", "order-1")]["status"])

    async def test_store_proof_requires_same_principal_plan_distinct_key_and_ttl(self) -> None:
        base_payload = {
            "target_id": "batch-1",
            "expected_updated_at": "2026-07-16T10:00:00+00:00",
            "planned_changes": {"storage_location": "B-22"},
            "correlation_id": "store-proof-correlation",
        }

        for mismatch in ("principal", "plan", "same_key", "expired"):
            with self.subTest(mismatch=mismatch):
                server, state = self._create_store_server()
                dry_key = f"store-proof-{mismatch}-dry"
                dry_run = await self._store_write(
                    server,
                    operation="set_batch_storage_location",
                    payload={**base_payload, "owner_intent": "Preview exact plan"},
                    idempotency_key=dry_key,
                    mode="dry_run",
                )
                self.assertTrue(dry_run.structuredContent["ok"])
                apply_payload = {**base_payload, "owner_intent": "Apply exact plan"}
                apply_key = f"store-proof-{mismatch}-apply"
                if mismatch == "principal":
                    state["store_principal"] = "different-principal"
                elif mismatch == "plan":
                    apply_payload["planned_changes"] = {"storage_location": "C-33"}
                elif mismatch == "same_key":
                    apply_key = dry_key
                    apply_payload["owner_intent"] = "Preview exact plan"
                elif mismatch == "expired":
                    state["store_clock"] = 1801

                apply = await self._store_write(
                    server,
                    operation="set_batch_storage_location",
                    payload=apply_payload,
                    idempotency_key=apply_key,
                    mode="apply",
                )

                self.assertFalse(apply.structuredContent["ok"])
                expected = (
                    "idempotency_key_conflict"
                    if mismatch == "same_key"
                    else "AGENT_DRY_RUN_REQUIRED"
                )
                self.assertIn(expected, apply.structuredContent["warnings"])

    async def test_store_explicit_correlation_is_preserved_and_invalid_value_blocked(self) -> None:
        server, state = self._create_store_server()
        explicit = "store-explicit-correlation-123"
        dry_run, apply = await self._store_dry_apply(
            server,
            operation="set_batch_storage_location",
            payload={
                "target_id": "batch-1",
                "expected_updated_at": "2026-07-16T10:00:00+00:00",
                "planned_changes": {"storage_location": "B-22"},
                "correlation_id": explicit,
            },
            key_prefix="store-explicit",
        )
        invalid = await self._store_write(
            server,
            operation="set_batch_storage_location",
            payload={
                "target_id": "batch-1",
                "expected_updated_at": "2026-07-16T10:01:00+00:00",
                "owner_intent": "Invalid correlation must not execute",
                "planned_changes": {"storage_location": "C-33"},
                "correlation_id": "bad correlation with spaces",
            },
            idempotency_key="store-invalid-correlation",
            mode="dry_run",
        )

        self.assertTrue(dry_run.structuredContent["ok"])
        self.assertTrue(apply.structuredContent["ok"])
        self.assertEqual(
            [explicit, explicit],
            [
                arguments["correlation_id"]
                for name, arguments in state["calls"]
                if name == "store_management_action"
            ],
        )
        self.assertFalse(invalid.structuredContent["ok"])
        self.assertIn("store_correlation_id_invalid", invalid.structuredContent["warnings"])
        self.assertEqual(
            2,
            sum(1 for name, _arguments in state["calls"] if name == "store_management_action"),
        )

    async def test_store_correlation_contract_accepts_160_and_rejects_161_characters(
        self,
    ) -> None:
        server, state = self._create_store_server()
        maximum = "s" + ("x" * 159)
        accepted = await self._store_write(
            server,
            operation="set_batch_storage_location",
            payload={
                "target_id": "batch-1",
                "expected_updated_at": "2026-07-16T10:00:00+00:00",
                "owner_intent": "Validate maximum correlation length",
                "planned_changes": {"storage_location": "B-22"},
                "correlation_id": maximum,
            },
            idempotency_key="store-max-correlation",
            mode="dry_run",
        )
        rejected = await self._store_write(
            server,
            operation="set_batch_storage_location",
            payload={
                "target_id": "batch-1",
                "expected_updated_at": "2026-07-16T10:00:00+00:00",
                "owner_intent": "Reject overlong correlation",
                "planned_changes": {"storage_location": "B-22"},
                "correlation_id": maximum + "x",
            },
            idempotency_key="store-overlong-correlation",
            mode="dry_run",
        )

        self.assertTrue(accepted.structuredContent["ok"])
        self.assertEqual(maximum, accepted.structuredContent["data"]["correlation_id"])
        self.assertFalse(rejected.structuredContent["ok"])
        self.assertIn("store_correlation_id_invalid", rejected.structuredContent["warnings"])
        self.assertEqual(
            [maximum],
            [
                arguments["correlation_id"]
                for name, arguments in state["calls"]
                if name == "store_management_action"
            ],
        )

    def test_store_readback_requires_exact_correlation_and_advanced_apply_revision(
        self,
    ) -> None:
        payload = {
            "target_id": "batch-1",
            "expected_updated_at": "2026-07-16T10:00:00+00:00",
            "planned_changes": {"storage_location": "B-22"},
            "correlation_id": "expected-correlation-123",
        }
        result = {
            "ok": True,
            "changes": [{"field": "storage_location"}],
            "verification": {"passed": True},
            "meta": {"correlation_id": "different-correlation-123"},
        }
        readback = {
            "ok": True,
            "data": {
                "item": {
                    "id": "batch-1",
                    "updated_at": "2026-07-16T10:00:00+00:00",
                    "storage_location": "B-22",
                }
            },
        }
        verification = verify_store_readback(
            "set_batch_storage_location",
            payload,
            result,
            mode="apply",
            preflight={"actual_updated_at": "2026-07-16T10:00:00+00:00"},
            readback=readback,
        )

        self.assertFalse(verification["passed"])
        checks = verification["evidence"]["checks"]
        self.assertFalse(checks["correlation_id_exact"])
        self.assertFalse(checks["revision_advanced"])

        result["meta"]["correlation_id"] = payload["correlation_id"]
        readback["data"]["item"]["updated_at"] = "2026-07-16T10:01:00+00:00"
        accepted = verify_store_readback(
            "set_batch_storage_location",
            payload,
            result,
            mode="apply",
            preflight={"actual_updated_at": "2026-07-16T10:00:00+00:00"},
            readback=readback,
        )
        self.assertTrue(accepted["passed"])

    def test_store_quote_note_and_draft_readback_are_exact(self) -> None:
        result = {
            "ok": True,
            "verification": {"passed": True},
            "meta": {"correlation_id": "quote-correlation-123"},
        }
        common = {
            "target_id": "quote-1",
            "expected_updated_at": "2026-07-16T10:00:00+00:00",
            "correlation_id": "quote-correlation-123",
        }
        note = verify_store_readback(
            "add_quote_request_note",
            {**common, "planned_changes": {"text": "Уточнить сторону"}},
            {**result, "changes": [{"field": "notes_count"}]},
            mode="apply",
            preflight={"actual_updated_at": "2026-07-16T10:00:00+00:00"},
            readback={
                "ok": True,
                "data": {
                    "item": {
                        "id": "quote-1",
                        "updated_at": "2026-07-16T10:01:00+00:00",
                        "notes": [{"origin": "AUTOSTOP_MANAGER", "text": "Уточнить сторону"}],
                    }
                },
            },
        )
        self.assertTrue(note["passed"])

        drafts = verify_store_readback(
            "replace_quote_offer_drafts",
            {
                **common,
                "planned_changes": {
                    "items": [
                        {
                            "item_id": "item-1",
                            "drafts": [{"candidate_key": "rossko:abc"}],
                        }
                    ]
                },
            },
            {**result, "changes": [{"field": "agent_draft_count"}]},
            mode="apply",
            preflight={"actual_updated_at": "2026-07-16T10:00:00+00:00"},
            readback={
                "ok": True,
                "data": {
                    "item": {
                        "id": "quote-1",
                        "updated_at": "2026-07-16T10:01:00+00:00",
                        "items": [
                            {
                                "item_id": "item-1",
                                "offers": [
                                    {
                                        "origin": "AUTOSTOP_MANAGER",
                                        "publication_status": "DRAFT",
                                        "candidate_key": "rossko:abc",
                                    }
                                ],
                            }
                        ],
                    }
                },
            },
        )
        self.assertTrue(drafts["passed"])

    async def test_store_ready_external_effect_requires_terminal_success_state(self) -> None:
        for external_state, expected_status in (
            ("SENT", "completed"),
            ("NOT_APPLICABLE", "completed"),
            ("CLAIMED", "compensating"),
            ("FAILED", "compensating"),
        ):
            with self.subTest(external_state=external_state):
                server, _state = self._create_store_server(
                    {"external_effect_state": external_state}
                )
                _dry_run, apply = await self._store_dry_apply(
                    server,
                    operation="mark_order_ready",
                    payload={
                        "target_id": "order-1",
                        "expected_updated_at": "2026-07-16T10:00:00+00:00",
                        "planned_changes": {"status": "READY"},
                    },
                    key_prefix=f"store-notifier-{external_state.lower()}",
                )

                self.assertEqual(expected_status, apply.structuredContent["status"])
                checks = apply.structuredContent["verification"]["evidence"]["checks"]
                self.assertTrue(checks["status_ready"])
                self.assertEqual(
                    external_state in {"SENT", "NOT_APPLICABLE"},
                    checks["external_effect_terminal"],
                )

    async def test_store_compensating_exact_retry_replays_receipt_and_closes(self) -> None:
        server, state = self._create_store_server({"manager_compensating_once": True})
        payload = {
            "target_id": "order-1",
            "expected_updated_at": "2026-07-16T10:00:00+00:00",
            "planned_changes": {"status": "READY"},
        }
        _dry_run, first_apply = await self._store_dry_apply(
            server,
            operation="mark_order_ready",
            payload=payload,
            key_prefix="store-reconcile",
        )

        self.assertEqual("compensating", first_apply.structuredContent["status"])
        self.assertEqual(2, state["store_post_count"])
        replay = await self._store_write(
            server,
            operation="mark_order_ready",
            payload={**payload, "owner_intent": "Apply store-reconcile"},
            idempotency_key="store-reconcile-apply",
            mode="apply",
        )

        self.assertTrue(replay.structuredContent["ok"])
        self.assertEqual("completed", replay.structuredContent["status"])
        self.assertTrue(replay.structuredContent["data"]["idempotency_replay"])
        self.assertEqual(3, state["store_post_count"])
        self.assertIn("store_receipt_replayed_and_reconciled", replay.structuredContent["warnings"])
        final_transition = [
            arguments
            for name, arguments in state["calls"]
            if name == "workflow_transition" and arguments["status"] == "completed"
        ][-1]
        self.assertEqual("completed mark_order_ready", final_transition["message"])
        self.assertEqual("store:mark_order_ready", final_transition["summary"])
        self.assertTrue(final_transition["verification"]["idempotency_replay"])
        self.assertTrue(
            all(isinstance(value, bool) for value in final_transition["verification"].values())
        )

    async def test_store_applied_but_unverified_enters_compensating(self) -> None:
        server, state = self._create_store_server({"skip_apply": True})

        _dry_run, result = await self._store_dry_apply(
            server,
            operation="mark_order_ready",
            payload={
                "target_id": "order-1",
                "expected_updated_at": "2026-07-16T10:00:00+00:00",
                "planned_changes": {"status": "READY"},
            },
            key_prefix="store-order-ready-unverified",
        )

        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual("compensating", result.structuredContent["status"])
        self.assertTrue(result.structuredContent["verification"]["executor_ok"])
        self.assertFalse(result.structuredContent["verification"]["passed"])
        self.assertIn(
            "verification_failed_compensation_required",
            result.structuredContent["warnings"],
        )

    async def test_manager_write_applied_unverified_signal_enters_compensating(self) -> None:
        server, _state = self._create_store_server({"manager_compensating": True})

        _dry_run, result = await self._store_dry_apply(
            server,
            operation="mark_order_ready",
            payload={
                "target_id": "order-1",
                "expected_updated_at": "2026-07-16T10:00:00+00:00",
                "planned_changes": {"status": "READY"},
            },
            key_prefix="store-order-ready-manager-unverified",
        )

        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual("compensating", result.structuredContent["status"])
        self.assertTrue(result.structuredContent["verification"]["executor_ok"])
        self.assertIn("store_apply_readback_failed", result.structuredContent["warnings"])

    async def test_legacy_crm_inventory_call_without_mode_keeps_apply_behavior(self) -> None:
        server, state = self._create_store_server()

        result = await server._tool_manager.get_tool("agent_inventory_workflow").run(
            {
                "operation": "list_inventory_items",
                "payload": {},
                "idempotency_key": "legacy-inventory-read",
            },
            convert_result=False,
        )

        self.assertTrue(result.structuredContent["ok"])
        self.assertEqual("apply", result.structuredContent["summary"]["mode"])
        self.assertFalse(any(name == "store_management_action" for name, _ in state["calls"]))

    async def test_board_digest_is_paginated_and_omits_ui_fields(self) -> None:
        result = await self._call("agent_board_digest", {"limit": 2})
        payload = result.structuredContent
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["data"]["cards"]), 2)
        self.assertTrue(payload["page"]["has_more"])
        self.assertEqual(payload["page"]["next_cursor"], "2")
        self.assertNotIn("deadline_heat_glow_color", payload["data"]["cards"][0])
        self.assertLessEqual(len(result.content[0].text), 1000)

    async def test_raw_capability_requires_live_schema_hash(self) -> None:
        discovered = await self._call("discover_raw_capabilities", {"query": "get_cards"})
        items = discovered.structuredContent["data"]["capabilities"]
        capability = next(item for item in items if item["name"] == "get_cards")

        rejected = await self._call(
            "call_raw_capability",
            {"name": "get_cards", "arguments": {}, "schema_hash": "stale"},
        )
        self.assertFalse(rejected.structuredContent["ok"])
        self.assertIn(
            "schema_hash_mismatch_rediscover_capability",
            rejected.structuredContent["warnings"],
        )

        accepted = await self._call(
            "call_raw_capability",
            {
                "name": "get_cards",
                "arguments": {"include_archived": False, "compact": True},
                "schema_hash": capability["schema_hash"],
            },
        )
        self.assertTrue(accepted.structuredContent["ok"])
        self.assertTrue(accepted.structuredContent["verification"]["schema_hash_verified"])

    async def test_web_research_capabilities_are_discoverable_without_expanding_surface(
        self,
    ) -> None:
        server, _state = self._create_store_server()
        names = {tool.name for tool in server._tool_manager.list_tools()}
        discovered = await server._tool_manager.get_tool("discover_raw_capabilities").run(
            {"query": "search_web_multi"}, convert_result=False
        )
        capability = discovered.structuredContent["data"]["capabilities"][0]
        schema = await server._tool_manager.get_tool("get_raw_capability_schema").run(
            {"name": "search_web_multi"}, convert_result=False
        )

        self.assertEqual(24, len(names))
        self.assertEqual("search_web_multi", capability["name"])
        self.assertEqual("read", capability["risk"])
        self.assertEqual(["query"], schema.structuredContent["data"]["input_schema"]["required"])
        self.assertFalse(schema.structuredContent["data"]["input_schema"]["additionalProperties"])

    async def test_web_search_raw_call_uses_schema_hash_and_read_only_envelope(self) -> None:
        schema = await self._call("get_raw_capability_schema", {"name": "search_web_multi"})
        schema_hash = schema.structuredContent["summary"]["schema_hash"]
        mocked_result = {
            "query": "AutoStop",
            "results": [{"title": "AutoStop", "url": "https://example.com"}],
            "provider_order": ["searxng"],
            "providers": [{"provider": "searxng", "status": "success"}],
            "fallback_used": False,
        }

        with patch(
            "minimal_kanban.mcp.web_gateway.AgentToolExecutor.execute",
            return_value=mocked_result,
        ) as execute:
            result = await self._call(
                "call_raw_capability",
                {
                    "name": "search_web_multi",
                    "arguments": {"query": "AutoStop", "limit": 3},
                    "schema_hash": schema_hash,
                },
            )

        self.assertTrue(result.structuredContent["ok"])
        self.assertEqual("read", result.structuredContent["summary"]["risk"])
        self.assertTrue(result.structuredContent["verification"]["schema_hash_verified"])
        execute.assert_called_once_with("search_web_multi", {"query": "AutoStop", "limit": 3})

    async def test_web_raw_call_runs_outside_the_mcp_asyncio_thread(self) -> None:
        schema = await self._call("get_raw_capability_schema", {"name": "fetch_page_browser"})
        caller_thread = threading.get_ident()
        worker_threads: list[int] = []

        def execute(_executor, _name, _arguments):
            worker_threads.append(threading.get_ident())
            return {"ok": True, "status_code": 200, "excerpt": "Example"}

        with patch(
            "minimal_kanban.mcp.web_gateway.AgentToolExecutor.execute",
            autospec=True,
            side_effect=execute,
        ):
            result = await self._call(
                "call_raw_capability",
                {
                    "name": "fetch_page_browser",
                    "arguments": {"url": "https://example.com", "wait_ms": 0},
                    "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                },
            )

        self.assertTrue(result.structuredContent["ok"])
        self.assertEqual(1, len(worker_threads))
        self.assertNotEqual(caller_thread, worker_threads[0])

    async def test_web_excerpt_raw_call_rejects_private_url(self) -> None:
        schema = await self._call("get_raw_capability_schema", {"name": "fetch_page_excerpt"})
        result = await self._call(
            "call_raw_capability",
            {
                "name": "fetch_page_excerpt",
                "arguments": {"url": "http://127.0.0.1/private"},
                "schema_hash": schema.structuredContent["summary"]["schema_hash"],
            },
        )

        self.assertFalse(result.structuredContent["ok"])
        self.assertIn("raw_capability_failed", result.structuredContent["warnings"])

    async def test_web_raw_call_rejects_schema_drift_and_redacts_runtime_errors(self) -> None:
        schema = await self._call("get_raw_capability_schema", {"name": "search_web_multi"})
        schema_hash = schema.structuredContent["summary"]["schema_hash"]
        rejected = await self._call(
            "call_raw_capability",
            {
                "name": "search_web_multi",
                "arguments": {"query": "AutoStop", "unexpected": "field"},
                "schema_hash": schema_hash,
            },
        )
        with patch(
            "minimal_kanban.mcp.web_gateway.AgentToolExecutor.execute",
            side_effect=RuntimeError("secret-token-value"),
        ):
            failed = await self._call(
                "call_raw_capability",
                {
                    "name": "search_web_multi",
                    "arguments": {"query": "AutoStop"},
                    "schema_hash": schema_hash,
                },
            )

        self.assertIn(
            "web_arguments_contain_unknown_fields", rejected.structuredContent["warnings"]
        )
        self.assertFalse(failed.structuredContent["ok"])
        self.assertNotIn("secret-token-value", json.dumps(failed.structuredContent))

    async def test_permanent_v2_tools_are_not_duplicated_through_raw_discovery(self) -> None:
        discovered = await self._call("discover_raw_capabilities", {"query": "ping_connector"})
        self.assertEqual([], discovered.structuredContent["data"]["capabilities"])

        schema = await self._call("get_raw_capability_schema", {"name": "ping_connector"})
        self.assertFalse(schema.structuredContent["ok"])
        self.assertIn("capability_not_found", schema.structuredContent["warnings"])

    async def test_raw_write_requires_idempotency_key(self) -> None:
        discovered = await self._call("discover_raw_capabilities", {"query": "create_sticky"})
        capability = next(
            item
            for item in discovered.structuredContent["data"]["capabilities"]
            if item["name"] == "create_sticky"
        )
        rejected = await self._call(
            "call_raw_capability",
            {
                "name": "create_sticky",
                "arguments": {
                    "text": "test",
                    "x": 0,
                    "y": 0,
                    "deadline": {"total_seconds": 60},
                },
                "schema_hash": capability["schema_hash"],
            },
        )
        self.assertFalse(rejected.structuredContent["ok"])
        self.assertIn(
            "idempotency_key_required_for_raw_write",
            rejected.structuredContent["warnings"],
        )

    async def test_raw_write_fails_closed_when_durable_manager_ledger_is_missing(self) -> None:
        discovered = await self._call(
            "discover_raw_capabilities", {"query": "api:/api/create_cashbox_transfer"}
        )
        capability = next(
            item
            for item in discovered.structuredContent["data"]["capabilities"]
            if item["name"] == "api:/api/create_cashbox_transfer"
        )
        rejected = await self._call(
            "call_raw_capability",
            {
                "name": capability["name"],
                "arguments": {
                    "from_cashbox_id": "cash-1",
                    "to_cashbox_id": "cash-2",
                    "amount": "1000",
                },
                "schema_hash": capability["schema_hash"],
                "idempotency_key": "transfer-cash-1-cash-2-v1",
            },
        )
        self.assertFalse(rejected.structuredContent["ok"])
        self.assertEqual(self.board_api.raw_requests, [])
        self.assertIn("durable_workflow_ledger_unavailable", rejected.structuredContent["warnings"])

    async def test_finance_update_requires_optimistic_revision_before_ledger(self) -> None:
        rejected = await self._call(
            "agent_finance_workflow",
            {
                "operation": "update_repair_order",
                "payload": {"card_id": "card-1", "repair_order": {"comment": "x"}},
                "idempotency_key": "repair-order-card-1-v1",
            },
        )
        self.assertFalse(rejected.structuredContent["ok"])
        self.assertIn(
            "expected_updated_at_required_reread_exact_card_first",
            rejected.structuredContent["warnings"],
        )

    async def test_virtual_raw_capability_covers_hidden_internal_crm_writes(self) -> None:
        discovered = await self._call(
            "discover_raw_capabilities", {"query": "create_employee_salary_transaction"}
        )
        capability = next(
            item
            for item in discovered.structuredContent["data"]["capabilities"]
            if item["name"] == "api:/api/create_employee_salary_transaction"
        )
        schema = await self._call("get_raw_capability_schema", {"name": capability["name"]})
        self.assertTrue(schema.structuredContent["ok"])
        self.assertEqual(schema.structuredContent["summary"]["risk"], "write")
        self.assertTrue(schema.structuredContent["data"]["input_schema"]["additionalProperties"])

    async def test_virtual_raw_schema_hash_is_bound_to_exact_route(self) -> None:
        first = await self._call(
            "get_raw_capability_schema",
            {"name": "api:/api/create_cashbox_transfer"},
        )
        second = await self._call(
            "get_raw_capability_schema",
            {"name": "api:/api/update_repair_order"},
        )

        self.assertNotEqual(
            first.structuredContent["summary"]["schema_hash"],
            second.structuredContent["summary"]["schema_hash"],
        )
        self.assertEqual(
            second.structuredContent["data"]["input_schema"]["title"],
            "/api/update_repair_order",
        )

    async def test_virtual_operator_admin_read_is_available_without_write_ledger(self) -> None:
        schema = await self._call(
            "get_raw_capability_schema",
            {"name": "api:/api/list_operator_users"},
        )
        self.assertEqual(schema.structuredContent["summary"]["risk"], "read")

        result = await self._call(
            "call_raw_capability",
            {
                "name": "api:/api/list_operator_users",
                "arguments": {},
                "schema_hash": schema.structuredContent["summary"]["schema_hash"],
            },
        )

        self.assertTrue(result.structuredContent["ok"])
        self.assertEqual(self.board_api.raw_requests[-1]["path"], "/api/list_operator_users")

    async def test_finance_switch_blocks_virtual_repair_order_and_payroll_bypasses(self) -> None:
        with patch.dict(
            "os.environ",
            {**GATEWAY_ENV, "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "0"},
            clear=False,
        ):
            server = create_mcp_server(
                FakeBoardApi(),
                self.logger,
                host="127.0.0.1",
                port=41835,
                path="/mcp",
                public_endpoint_url="https://crm.example/mcp",
            )

        async def assert_blocked(name: str, arguments: dict) -> None:
            schema = await server._tool_manager.get_tool("get_raw_capability_schema").run(
                {"name": name}, convert_result=False
            )
            with patch.dict(
                "os.environ",
                {**GATEWAY_ENV, "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "0"},
                clear=False,
            ):
                result = await server._tool_manager.get_tool("call_raw_capability").run(
                    {
                        "name": name,
                        "arguments": arguments,
                        "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                        "idempotency_key": f"blocked-{name}",
                    },
                    convert_result=False,
                )
            self.assertFalse(result.structuredContent["ok"])
            self.assertIn("agent_gateway_finance_disabled", result.structuredContent["warnings"])

        await assert_blocked(
            "api:/api/update_repair_order",
            {"card_id": "card-1", "repair_order": {"payments": []}},
        )
        await assert_blocked(
            "api:/api/save_employee",
            {"employee": {"id": "employee-1", "salary": "1000"}},
        )

    async def test_production_master_switch_fails_closed_to_diagnostics(self) -> None:
        production_env = {
            **GATEWAY_ENV,
            "AUTOSTOP_DEPLOYMENT_ENV": "production",
            "AUTOSTOP_AGENT_GATEWAY_ENABLED": "0",
            "AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED": "0",
            "AUTOSTOP_AGENT_SERVICE_IDENTITY": "codex-owner-agent",
        }
        with patch.dict("os.environ", production_env, clear=False):
            server = create_mcp_server(
                FakeBoardApi(),
                self.logger,
                host="127.0.0.1",
                port=41836,
                path="/mcp",
                bearer_token="b" * 48,
                public_endpoint_url="https://crm.example/mcp",
            )

        names = {tool.name for tool in server._tool_manager.list_tools()}
        self.assertEqual(
            names,
            {"ping_connector", "get_connector_identity", "get_runtime_status"},
        )
        self.assertNotIn("create_cash_transaction", names)
        self.assertNotIn("update_repair_order", names)

    async def test_raw_write_uses_ledger_and_deduplicates_only_completed_result(self) -> None:
        state = {"started": False, "status": "planned", "scope": None}

        def register_fake_ledger(server, _logger) -> None:
            @server.tool(name="start_workflow")
            def start_workflow(
                workflow_id: str,
                intent: str,
                idempotency_key: str,
                query: str = "",
                actor: str = "",
                scope: dict | None = None,
                metadata: dict | None = None,
                dry_run: bool = False,
            ) -> dict:
                del workflow_id, intent, idempotency_key, query, actor, metadata, dry_run
                if state["started"] and scope != state["scope"]:
                    return {
                        "ok": False,
                        "status": state["status"],
                        "warnings": ["idempotency_key_conflict"],
                    }
                deduplicated = state["started"]
                state["started"] = True
                state["scope"] = scope
                return {
                    "ok": True,
                    "format": "agent_envelope_v2",
                    "run_id": 77,
                    "status": state["status"],
                    "summary": {
                        "id": 77,
                        "status": state["status"],
                        "deduplicated": deduplicated,
                    },
                }

            @server.tool(name="workflow_transition")
            def workflow_transition(
                run_id: int,
                status: str,
                message: str = "",
                verification: dict | None = None,
                summary: str = "",
                expected_state_version: int | None = None,
            ) -> dict:
                del run_id, message, verification, summary, expected_state_version
                state["status"] = status
                return {
                    "ok": True,
                    "format": "agent_envelope_v2",
                    "run_id": 77,
                    "status": status,
                    "summary": {"id": 77, "status": status},
                }

        self.manager_register.side_effect = register_fake_ledger
        board_api = FakeBoardApi()
        server = create_mcp_server(
            board_api,
            self.logger,
            host="127.0.0.1",
            port=41833,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

        async def call(name: str, arguments: dict):
            return await server._tool_manager.get_tool(name).run(arguments, convert_result=False)

        discovered = await call(
            "discover_raw_capabilities", {"query": "api:/api/create_cashbox_transfer"}
        )
        capability = next(
            item
            for item in discovered.structuredContent["data"]["capabilities"]
            if item["name"] == "api:/api/create_cashbox_transfer"
        )
        arguments = {
            "name": capability["name"],
            "arguments": {
                "from_cashbox_id": "cash-1",
                "to_cashbox_id": "cash-2",
                "amount": "1000",
                "actor_name": "spoofed-human",
                "source": "ui",
            },
            "schema_hash": capability["schema_hash"],
            "idempotency_key": "transfer-cash-1-cash-2-v1",
        }
        first = await call("call_raw_capability", arguments)
        duplicate = await call("call_raw_capability", arguments)

        self.assertTrue(first.structuredContent["ok"])
        self.assertTrue(first.structuredContent["verification"]["ledger_closed"])
        self.assertEqual(len(board_api.raw_requests), 1)
        self.assertEqual(board_api.raw_requests[0]["payload"]["actor_name"], "codex-owner-agent")
        self.assertEqual(board_api.raw_requests[0]["payload"]["source"], "mcp_agent_gateway_v2")
        self.assertTrue(duplicate.structuredContent["ok"])
        self.assertIn(
            "idempotency_reused_completed_result", duplicate.structuredContent["warnings"]
        )
        self.assertEqual(len(board_api.raw_requests), 1)

    async def test_named_board_dry_run_is_recorded_and_reported_without_parent_ledger(self) -> None:
        state = {"status": "planned", "start_arguments": None}

        def register_fake_ledger(server, _logger) -> None:
            @server.tool(name="start_workflow")
            def start_workflow(
                workflow_id: str,
                intent: str,
                idempotency_key: str,
                query: str = "",
                actor: str = "",
                scope: dict | None = None,
                metadata: dict | None = None,
                dry_run: bool = False,
            ) -> dict:
                state["start_arguments"] = {
                    "workflow_id": workflow_id,
                    "intent": intent,
                    "idempotency_key": idempotency_key,
                    "query": query,
                    "actor": actor,
                    "scope": scope,
                    "metadata": metadata,
                    "dry_run": dry_run,
                }
                return {
                    "ok": True,
                    "run_id": 91,
                    "status": state["status"],
                    "summary": {"id": 91, "deduplicated": False},
                }

            @server.tool(name="workflow_transition")
            def workflow_transition(
                run_id: int,
                status: str,
                message: str = "",
                verification: dict | None = None,
                summary: str = "",
                expected_state_version: int | None = None,
            ) -> dict:
                del run_id, message, verification, summary, expected_state_version
                state["status"] = status
                return {"ok": True, "run_id": 91, "status": status, "summary": {}}

        self.manager_register.side_effect = register_fake_ledger
        server = create_mcp_server(
            FakeBoardApi(),
            self.logger,
            host="127.0.0.1",
            port=41838,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

        result = await server._tool_manager.get_tool("agent_board_workflow").run(
            {
                "operation": "bulk_set_deadline_if_below",
                "payload": {
                    "include_archived": False,
                    "min_total_seconds": 172800,
                    "target_total_seconds": 173700,
                },
                "idempotency_key": "timer-floor-dry-run-v1",
                "mode": "dry_run",
            },
            convert_result=False,
        )

        self.assertTrue(result.structuredContent["ok"])
        self.assertEqual(result.structuredContent["run_id"], 91)
        self.assertEqual(result.structuredContent["summary"]["mode"], "dry_run")
        self.assertTrue(result.structuredContent["meta"]["dry_run"])
        self.assertTrue(result.structuredContent["meta"]["ledger_owned_by_named_workflow"])
        self.assertTrue(state["start_arguments"]["dry_run"])
        self.assertEqual(state["start_arguments"]["scope"]["mode"], "dry_run")
        self.assertEqual(state["start_arguments"]["metadata"]["mode"], "dry_run")
        self.assertTrue(state["start_arguments"]["metadata"]["dry_run"])

    async def test_named_repair_order_payment_reconciles_order_and_cash_journal(self) -> None:
        state = {"started": False, "status": "planned"}

        def register_fake_ledger(server, _logger) -> None:
            @server.tool(name="start_workflow")
            def start_workflow(
                workflow_id: str,
                intent: str,
                idempotency_key: str,
                query: str = "",
                actor: str = "",
                scope: dict | None = None,
                metadata: dict | None = None,
                dry_run: bool = False,
            ) -> dict:
                del workflow_id, intent, idempotency_key, query, actor, scope, metadata, dry_run
                state["started"] = True
                return {
                    "ok": True,
                    "run_id": 88,
                    "status": state["status"],
                    "summary": {"id": 88, "deduplicated": False},
                }

            @server.tool(name="workflow_transition")
            def workflow_transition(
                run_id: int,
                status: str,
                message: str = "",
                verification: dict | None = None,
                summary: str = "",
                expected_state_version: int | None = None,
            ) -> dict:
                del run_id, message, verification, summary, expected_state_version
                state["status"] = status
                return {"ok": True, "run_id": 88, "status": status, "summary": {}}

        self.manager_register.side_effect = register_fake_ledger
        board_api = FakeBoardApi()
        server = create_mcp_server(
            board_api,
            self.logger,
            host="127.0.0.1",
            port=41834,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )
        tool = server._tool_manager.get_tool("agent_finance_workflow")
        result = await tool.run(
            {
                "operation": "record_repair_order_payment",
                "payload": {
                    "card_id": "card-1",
                    "cashbox_id": "cashbox-main",
                    "amount": "1000",
                    "payment_method": "cash",
                    "expected_updated_at": "2026-07-11T00:00:00+00:00",
                    "note": "Полная оплата заказ-наряда 42",
                },
                "idempotency_key": "payment-card-1-full-v1",
            },
            convert_result=False,
        )

        self.assertTrue(result.structuredContent["ok"])
        self.assertTrue(result.structuredContent["verification"]["ledger_closed"])
        evidence = result.structuredContent["verification"]["evidence"]
        self.assertTrue(evidence["cash_journal_entry_present"])
        self.assertEqual(len(board_api.repair_order_payments), 1)
        self.assertEqual(len(board_api.cash_transactions), 1)

    async def test_payment_readback_failure_enters_compensating_after_write(self) -> None:
        state = {"status": "planned"}

        def register_fake_ledger(server, _logger) -> None:
            @server.tool(name="start_workflow")
            def start_workflow(
                workflow_id: str,
                intent: str,
                idempotency_key: str,
                query: str = "",
                actor: str = "",
                scope: dict | None = None,
                metadata: dict | None = None,
                dry_run: bool = False,
            ) -> dict:
                del workflow_id, intent, idempotency_key, query, actor, scope, metadata, dry_run
                return {
                    "ok": True,
                    "run_id": 89,
                    "status": state["status"],
                    "summary": {"id": 89, "deduplicated": False},
                }

            @server.tool(name="workflow_transition")
            def workflow_transition(
                run_id: int,
                status: str,
                message: str = "",
                verification: dict | None = None,
                summary: str = "",
                expected_state_version: int | None = None,
            ) -> dict:
                del run_id, message, verification, summary, expected_state_version
                state["status"] = status
                return {"ok": True, "run_id": 89, "status": status, "summary": {}}

        class MismatchedPaymentBoardApi(FakeBoardApi):
            def update_repair_order(self, **kwargs) -> dict:
                result = super().update_repair_order(**kwargs)
                self.repair_order_payments[-1]["payment_method"] = "cashless"
                return result

        self.manager_register.side_effect = register_fake_ledger
        board_api = MismatchedPaymentBoardApi()
        server = create_mcp_server(
            board_api,
            self.logger,
            host="127.0.0.1",
            port=41837,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )
        result = await server._tool_manager.get_tool("agent_finance_workflow").run(
            {
                "operation": "record_repair_order_payment",
                "payload": {
                    "card_id": "card-1",
                    "cashbox_id": "cashbox-main",
                    "amount": "1000",
                    "payment_method": "cash",
                    "expected_updated_at": "2026-07-11T00:00:00+00:00",
                },
                "idempotency_key": "payment-card-1-mismatch-v1",
            },
            convert_result=False,
        )

        self.assertFalse(result.structuredContent["ok"])
        self.assertEqual(result.structuredContent["status"], "compensating")
        self.assertTrue(result.structuredContent["verification"]["executor_ok"])
        self.assertFalse(
            result.structuredContent["verification"]["evidence"]["payment_method_exact"]
        )
        self.assertIn(
            "verification_failed_compensation_required",
            result.structuredContent["warnings"],
        )
        self.assertEqual(len(board_api.cash_transactions), 1)

    async def test_production_uses_owner_approved_oauth_with_internal_bearer_compatibility(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                "os.environ",
                {
                    **GATEWAY_ENV,
                    "AUTOSTOP_DEPLOYMENT_ENV": "production",
                    "AUTOSTOP_MCP_OAUTH_ENABLED": "1",
                    "AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED": "0",
                    "AUTOSTOP_MCP_OAUTH_STATE_KEY": (
                        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
                    ),
                    "AUTOSTOP_AGENT_SERVICE_IDENTITY": "codex-owner-agent",
                },
                clear=False,
            ):
                server = create_mcp_server(
                    FakeBoardApi(),
                    self.logger,
                    host="127.0.0.1",
                    port=41832,
                    path="/mcp",
                    bearer_token="a" * 48,
                    public_endpoint_url="https://crm.example/mcp",
                    oauth_state_file=Path(temp_dir) / "oauth-state.json",
                )
        provider = server._auth_server_provider
        self.assertIsInstance(provider, ProductionOAuthAuthorizationServerProvider)
        self.assertIsNotNone(server._token_verifier)
        self.assertIsNotNone(await provider.load_access_token("a" * 48))
        self.assertIsNone(await provider.load_access_token("wrong"))


if __name__ == "__main__":
    unittest.main()
