from __future__ import annotations

import base64
import hashlib
import json
import logging
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.server import create_mcp_server
from minimal_kanban.mcp.workflow_guards import finance_dry_run_proof, invoice_document_guard
from minimal_kanban.printing.document_guard import invoice_guard
from tests.test_agent_gateway_v2 import (
    GATEWAY_ENV,
    FakeBoardApi,
    register_fake_store_manager_tools,
)


class FinancePreviewBoardApi(FakeBoardApi):
    def __init__(self) -> None:
        super().__init__()
        self.update_calls = 0
        self.repair_order_reads = 0
        self.cashboxes = [
            {"id": "cashbox-a", "order": 0, "updated_at": self.cashbox_updated_at},
            {"id": "cashbox-b", "order": 1, "updated_at": self.cashbox_updated_at},
        ]
        self.cash_transactions = [
            {"id": "transaction-new", "cashbox_id": "cashbox-a"},
            {"id": "transaction-old", "cashbox_id": "cashbox-a"},
        ]
        self.finance_issue_ids = ["issue-a", "issue-b"]
        self.cashbox_snapshot_complete = True
        self.transaction_snapshot_complete = True
        self.order = {"client": "Before"}

    def get_repair_order(self, card_id: str, *, create_if_missing: bool | None = None) -> dict:
        del create_if_missing
        self.repair_order_reads += 1
        return {
            "ok": True,
            "data": {
                "card": {"id": card_id, "updated_at": self.card_updated_at},
                "repair_order": dict(self.order),
            },
        }

    def update_repair_order(
        self,
        *,
        card_id: str,
        repair_order: dict,
        expected_updated_at: str | None = None,
        **_: object,
    ) -> dict:
        self.update_calls += 1
        if expected_updated_at != self.card_updated_at:
            return {"ok": False, "error": {"code": "card_update_conflict"}}
        self.order.update(repair_order)
        self.card_updated_at = "2026-07-11T00:01:00+00:00"
        return {
            "ok": True,
            "data": {
                "card": {"id": card_id, "updated_at": self.card_updated_at},
                "repair_order": dict(self.order),
            },
        }

    def list_cashboxes(self, *, limit: int | None = None) -> dict:
        rows = self.cashboxes[: int(limit or 1000)]
        extra = 0 if self.cashbox_snapshot_complete else 1
        return {
            "ok": True,
            "data": {
                "cashboxes": [dict(row) for row in rows],
                "meta": {"total": len(rows) + extra, "has_more": bool(extra)},
            },
        }

    def get_cashbox(
        self,
        cashbox_id: str,
        *,
        transaction_limit: int | None = None,
        transaction_offset: int | None = None,
    ) -> dict:
        rows = [row for row in self.cash_transactions if row["cashbox_id"] == cashbox_id]
        offset = int(transaction_offset or 0)
        returned = rows[offset : offset + int(transaction_limit or 300)]
        extra = 0 if self.transaction_snapshot_complete else 1
        return {
            "ok": True,
            "data": {
                "cashbox": {"id": cashbox_id, "updated_at": self.cashbox_updated_at},
                "transactions": [dict(row) for row in returned],
                "meta": {"transactions_total": len(returned) + extra, "has_more": bool(extra)},
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
        result = super()._request(path, payload, method=method, extra_headers=extra_headers)
        if path == "/api/finance_audit":
            return {
                "ok": True,
                "data": {"issues": [{"id": issue_id} for issue_id in self.finance_issue_ids]},
            }
        return result

    def download_repair_order_print_pdf(self, **_: object) -> dict:
        pdf = b"%PDF-invoice-guard"
        return {
            "ok": True,
            "data": {
                "file_name": "invoice.pdf",
                "mime_type": "application/pdf",
                "content_base64": base64.b64encode(pdf).decode("ascii"),
                "meta": {
                    "documents": [
                        {
                            "id": "invoice",
                            "document_guard": {
                                "money_basis": "cashless",
                                "rendered_total": "1176.47",
                                "repair_order_total": "1176.47",
                                "tax_status": "НДС (5%)",
                                "financial_mismatch": False,
                                "tax_mismatch": False,
                                "financial_or_tax_mismatch": False,
                                "mismatch_with_current_repair_order": False,
                            },
                        }
                    ]
                },
            },
        }


class AgentGatewayFinancePreviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(self._testMethodName)
        self.logger.addHandler(logging.NullHandler())
        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.manager_patch = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        self.env.start()
        self.manager_register = self.manager_patch.start()
        self.manager_state: dict = {}
        self.manager_register.side_effect = lambda server, logger: (
            register_fake_store_manager_tools(server, logger, self.manager_state)
        )
        self.board_api = FinancePreviewBoardApi()
        self.server = create_mcp_server(
            self.board_api,
            self.logger,
            host="127.0.0.1",
            port=41852,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

    def tearDown(self) -> None:
        self.manager_patch.stop()
        self.env.stop()

    async def _finance(self, arguments: dict):
        return await self.server._tool_manager.get_tool("agent_finance_workflow").run(
            arguments, convert_result=False
        )

    async def _workflow(self, operation: str, key: str, payload: dict, **fields: object):
        return await self._finance(
            {"operation": operation, "payload": payload, "idempotency_key": key, **fields}
        )

    @staticmethod
    def _payload(expected_updated_at: str = "2026-07-11T00:00:00+00:00") -> dict:
        return {
            "card_id": "card-1",
            "repair_order": {"client": "Horizon"},
            "expected_updated_at": expected_updated_at,
        }

    async def test_finance_schema_and_gateway_surface_remain_compatible(self) -> None:
        names = {tool.name for tool in self.server._tool_manager.list_tools()}
        schema = self.server._tool_manager.get_tool("agent_finance_workflow").parameters

        self.assertEqual(24, len(names))
        self.assertEqual({"operation", "payload", "idempotency_key"}, set(schema["required"]))
        self.assertTrue(
            {"mode", "dry_run_proof", "dry_run_idempotency_key"} <= set(schema["properties"])
        )
        self.assertIsNone(schema["properties"]["mode"]["default"])

    async def test_dry_run_closes_ledger_without_calling_business_executor(self) -> None:
        preview = await self._workflow(
            "update_repair_order", "finance-preview-1", self._payload(), mode="dry_run"
        )

        self.assertTrue(preview.structuredContent["ok"])
        self.assertEqual("completed", preview.structuredContent["status"])
        self.assertEqual(0, self.board_api.update_calls)
        self.assertEqual(1, self.board_api.repair_order_reads)
        self.assertEqual(64, len(preview.structuredContent["data"]["dry_run_proof"]))
        self.assertEqual(
            "finance-preview-1",
            preview.structuredContent["data"]["dry_run_idempotency_key"],
        )
        self.assertEqual(
            "finance_preview_non_mutating", preview.structuredContent["verification"]["check"]
        )
        self.assertIsNone(preview.structuredContent["summary"]["executor"])
        replay = await self._workflow(
            "update_repair_order", "finance-preview-1", self._payload(), mode="dry_run"
        )
        self.assertEqual(
            preview.structuredContent["data"]["dry_run_proof"],
            replay.structuredContent["data"]["dry_run_proof"],
        )
        self.assertEqual(
            "finance-preview-1", replay.structuredContent["data"]["dry_run_idempotency_key"]
        )

    async def test_apply_requires_matching_proof_and_distinct_key_then_deduplicates(self) -> None:
        payload = self._payload()
        forged = await self._workflow(
            "update_repair_order",
            "forged-apply",
            payload,
            mode="apply",
            dry_run_proof=finance_dry_run_proof(
                "update_repair_order", payload, "preview-never-ran"
            ),
            dry_run_idempotency_key="preview-never-ran",
        )
        self.assertIn("finance_dry_run_not_completed", forged.structuredContent["warnings"])
        preview = await self._workflow(
            "update_repair_order", "finance-preview-2", payload, mode="dry_run"
        )
        proof = preview.structuredContent["data"]["dry_run_proof"]
        base_apply = {
            "operation": "update_repair_order",
            "payload": payload,
            "mode": "apply",
            "dry_run_proof": proof,
            "dry_run_idempotency_key": "finance-preview-2",
        }

        mismatch = await self._finance(
            {**base_apply, "idempotency_key": "finance-apply-mismatch", "dry_run_proof": "f" * 64}
        )
        same_key = await self._finance({**base_apply, "idempotency_key": "finance-preview-2"})
        applied = await self._finance({**base_apply, "idempotency_key": "finance-apply-2"})
        replay = await self._finance({**base_apply, "idempotency_key": "finance-apply-2"})

        self.assertIn("finance_dry_run_proof_mismatch", mismatch.structuredContent["warnings"])
        self.assertIn("apply_requires_new_idempotency_key", same_key.structuredContent["warnings"])
        self.assertTrue(applied.structuredContent["ok"])
        self.assertTrue(replay.structuredContent["ok"])
        self.assertTrue(replay.structuredContent["summary"]["deduplicated"])
        self.assertEqual(1, self.board_api.update_calls)
        completed = [
            arguments
            for name, arguments in self.manager_state["calls"]
            if name == "workflow_transition"
            and arguments["status"] == "completed"
            and arguments["summary"] == "finance:update_repair_order"
        ]
        ledger_verification = completed[-1]["verification"]
        persisted = json.dumps(ledger_verification, ensure_ascii=False)
        self.assertNotIn("card-1", persisted)
        self.assertNotIn("Horizon", persisted)
        self.assertNotIn("2026-07-11", persisted)
        self.assertTrue(ledger_verification["target_ref_hashes"])
        self.assertTrue(ledger_verification["revision_guarded"])

        reused = await self._finance({**base_apply, "idempotency_key": "finance-apply-other"})
        self.assertIn(
            "finance_dry_run_proof_already_consumed", reused.structuredContent["warnings"]
        )
        self.assertEqual(1, self.board_api.update_calls)

    async def test_strict_preview_rejects_extras_and_stale_revision(self) -> None:
        unexpected_update = await self._workflow(
            "update_repair_order",
            "strict-update-extra",
            {**self._payload(), "unexpected": "ignored before"},
            mode="dry_run",
        )
        unexpected_payment = await self._workflow(
            "record_repair_order_payment",
            "strict-payment-extra",
            {
                "card_id": "card-1",
                "cashbox_id": "cashbox-1",
                "expected_updated_at": self.board_api.card_updated_at,
                "expected_cashbox_updated_at": "2026-07-11T00:00:00+00:00",
                "payment_method": "cash",
                "amount": "1.00",
                "unexpected": True,
            },
            mode="dry_run",
        )
        stale = await self._workflow(
            "update_repair_order",
            "strict-stale-revision",
            self._payload("2025-01-01T00:00:00+00:00"),
            mode="dry_run",
        )

        self.assertIn(
            "finance_payload_schema_validation_failed",
            unexpected_update.structuredContent["warnings"],
        )
        self.assertIn(
            "finance_payload_schema_validation_failed",
            unexpected_payment.structuredContent["warnings"],
        )
        self.assertIn("finance_revision_preflight_failed", stale.structuredContent["warnings"])
        self.assertEqual(0, self.board_api.update_calls)

    async def test_snapshot_preflight_requires_exact_complete_live_lists(self) -> None:
        cashboxes = ["cashbox-a", "cashbox-b"]
        create = {"name": "Reserve", "expected_cashbox_ids": cashboxes}
        reorder = {
            "cashbox_id": "cashbox-b",
            "before_cashbox_id": "cashbox-a",
            "expected_cashbox_ids": cashboxes,
        }
        delete = {
            "cashbox_id": "cashbox-a",
            "expected_cashbox_updated_at": self.board_api.cashbox_updated_at,
            "expected_transaction_ids": ["transaction-new", "transaction-old"],
        }
        audit = {
            "dry_run": False,
            "expected_issue_ids": ["issue-a", "issue-b"],
            "issue_ids": ["issue-a"],
        }
        cases = [
            ("create_cashbox", create, "expected_cashbox_ids", ["definitely-not-current"]),
            ("reorder_cashboxes", reorder, "expected_cashbox_ids", ["wrong-a", "wrong-b"]),
            ("delete_cashbox", delete, "expected_transaction_ids", ["wrong-transaction"]),
            ("apply_finance_audit_safe_fixes", audit, "expected_issue_ids", ["wrong-issue"]),
            ("apply_finance_audit_safe_fixes", audit, "issue_ids", ["unknown-selection"]),
        ]
        for index, (operation, payload, forged_field, forged_value) in enumerate(cases):
            with self.subTest(operation=operation, field=forged_field):
                valid = await self._workflow(
                    operation, f"snapshot-valid-{index}", payload, mode="dry_run"
                )
                forged = await self._workflow(
                    operation,
                    f"snapshot-forged-{index}",
                    {**payload, forged_field: forged_value},
                    mode="dry_run",
                )
                self.assertEqual(64, len(valid.structuredContent["data"]["dry_run_proof"]))
                self.assertIn(
                    "finance_revision_preflight_failed", forged.structuredContent["warnings"]
                )
                self.assertNotIn("dry_run_proof", str(forged.structuredContent))

        for attribute, operation, payload in (
            ("cashbox_snapshot_complete", "create_cashbox", cases[0][1]),
            ("transaction_snapshot_complete", "delete_cashbox", cases[2][1]),
        ):
            setattr(self.board_api, attribute, False)
            result = await self._workflow(
                operation, f"snapshot-truncated-{operation}", payload, mode="dry_run"
            )
            setattr(self.board_api, attribute, True)
            self.assertIn("finance_revision_preflight_failed", result.structuredContent["warnings"])
        write_paths = {
            "/api/create_cashbox",
            "/api/reorder_cashboxes",
            "/api/delete_cashbox",
            "/api/finance_audit/apply_safe_fixes",
        }
        self.assertTrue(
            write_paths.isdisjoint(item["path"] for item in self.board_api.raw_requests)
        )

    async def test_legacy_write_still_applies_and_explicit_mode_is_rejected_for_reads(self) -> None:
        read_blocked = await self._workflow(
            "get_repair_order", "finance-read-preview", {"card_id": "card-1"}, mode="dry_run"
        )
        legacy = await self._workflow(
            "update_repair_order", "finance-legacy-apply", self._payload()
        )

        self.assertIn(
            "finance_read_operation_write_mode_not_allowed",
            read_blocked.structuredContent["warnings"],
        )
        self.assertTrue(legacy.structuredContent["ok"])
        self.assertEqual(1, self.board_api.update_calls)

    async def test_invoice_workflow_returns_transient_guard_and_hides_compact_binary(self) -> None:
        result = await self.server._tool_manager.get_tool("agent_document_workflow").run(
            {
                "operation": "download_repair_order_print_pdf",
                "payload": {"card_id": "card-1", "selected_document_ids": ["invoice"]},
                "idempotency_key": "invoice-document-guard-1",
            },
            convert_result=False,
        )

        guard = result.structuredContent["data"]["document_guard"]
        self.assertTrue(result.structuredContent["ok"])
        self.assertTrue(guard["qa_passed"])
        self.assertEqual("cashless", guard["money_basis"])
        self.assertEqual("1176.47", guard["rendered_total"])
        self.assertFalse(guard["mismatch_with_current_repair_order"])
        self.assertFalse(guard["financial_or_tax_mismatch"])
        self.assertEqual(
            hashlib.sha256(b"%PDF-invoice-guard").hexdigest(), guard["attachment_sha256"]
        )
        self.assertNotIn("content_base64", str(result.structuredContent))

    def test_invoice_guard_qa_rejects_non_pdf_and_incomplete_typed_guard(self) -> None:
        source = {
            "money_basis": "cashless",
            "rendered_total": "1.00",
            "repair_order_total": "1.00",
            "tax_status": "Без НДС",
            "financial_mismatch": False,
            "tax_mismatch": False,
            "financial_or_tax_mismatch": False,
            "mismatch_with_current_repair_order": False,
        }

        def result(content: bytes, mime_type: str, guard: dict) -> dict:
            return {
                "ok": True,
                "data": {
                    "content_base64": base64.b64encode(content).decode("ascii"),
                    "mime_type": mime_type,
                    "meta": {"documents": [{"id": "invoice", "document_guard": guard}]},
                },
            }

        self.assertTrue(
            invoice_document_guard(result(b"%PDF-valid", "application/pdf", source))["qa_passed"]
        )
        cases = (
            (b"", "application/pdf", source),
            (b"arbitrary", "application/pdf", source),
            (b"%PDF-valid", "text/plain", source),
            (b"%PDF-valid", "application/pdf", {**source, "tax_mismatch": "false"}),
            (b"%PDF-valid", "application/pdf", {**source, "repair_order_total": None}),
            (b"%PDF-valid", "application/pdf", {**source, "repair_order_total": "NaN"}),
            (b"%PDF-valid", "application/pdf", {**source, "rendered_total": "Infinity"}),
            (b"%PDF-valid", "application/pdf", {**source, "rendered_total": "-0.01"}),
            (b"%PDF-valid", "application/pdf", {**source, "rendered_total": "not-money"}),
            (b"%PDF-valid", "application/pdf", {**source, "repair_order_total": "2.00"}),
        )
        for content, mime_type, guard in cases:
            with self.subTest(content=content, mime_type=mime_type):
                self.assertFalse(
                    invoice_document_guard(result(content, mime_type, guard))["qa_passed"]
                )

    def test_print_guard_uses_rendered_invoice_and_current_order_totals(self) -> None:
        context = {
            "invoice": {
                "amount_due": Decimal("1176.47"),
                "tax_label": "НДС (5%)",
            },
            "repair_order": {"tax_label": "НДС (5%)"},
            "totals": {"noncash_due": Decimal("1176.47")},
        }
        guard = invoice_guard("invoice", context)

        self.assertEqual("1176.47", guard["rendered_total"])
        self.assertFalse(guard["mismatch_with_current_repair_order"])
        self.assertFalse(guard["financial_or_tax_mismatch"])
