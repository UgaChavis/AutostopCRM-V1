from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from minimal_kanban.mcp.store_gateway import (
    STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
    _valid_store_quote_telegram_text,
)


def _fake_digest(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def register_fake_store_quote_conductor(server: Any, state: dict[str, Any]) -> None:
    """Register the focused fake used by the typed quote-conductor contract cases."""

    state.setdefault("conductor_receipts", {})

    @server.tool(name="store_quote_conductor", description="INTERNAL_ONLY Store quote conductor")
    def store_quote_conductor(
        operation: str,
        quote_request_id: str = "",
        run_id: int | None = None,
        expected_state_version: int | None = None,
        expected_revision: str = "",
        idempotency_key: str = "",
        correlation_id: str = "",
        entries: list[dict] | None = None,
        coverage: list[dict] | None = None,
        customer_response: str = "",
        evidence: dict | None = None,
        step_id: str = "",
        reply_classification: str = "",
        consent_context_hash: str = "",
        published_snapshot_hash: str = "",
        telegram_context_hash: str = "",
        telegram_inbound_receipt: str = "",
        telegram_message: str = "",
        telegram_message_kind: str = "",
        mode: str = "apply",
    ) -> dict:
        arguments = {
            "operation": operation,
            "quote_request_id": quote_request_id,
            "run_id": run_id,
            "expected_state_version": expected_state_version,
            "expected_revision": expected_revision,
            "idempotency_key": idempotency_key,
            "correlation_id": correlation_id,
            "entries": list(entries or []),
            "coverage": list(coverage or []),
            "customer_response": customer_response,
            "evidence": dict(evidence or {}),
            "step_id": step_id,
            "reply_classification": reply_classification,
            "consent_context_hash": consent_context_hash,
            "published_snapshot_hash": published_snapshot_hash,
            "telegram_context_hash": telegram_context_hash,
            "telegram_inbound_receipt": telegram_inbound_receipt,
            "telegram_message": telegram_message,
            "telegram_message_kind": telegram_message_kind,
            "mode": mode,
        }
        state["calls"].append(("store_quote_conductor", arguments))
        request_hash = _fake_digest(arguments)
        existing = state["conductor_receipts"].get(idempotency_key)
        if existing is not None:
            if existing["request_hash"] != request_hash:
                return {
                    "ok": False,
                    "status": "conflict",
                    "summary": {"error_code": "idempotency_key_conflict"},
                    "warnings": ["idempotency_key_conflict"],
                }
            replay = copy.deepcopy(existing["response"])
            replay["summary"]["deduplicated"] = True
            replay["meta"]["idempotency_replay"] = True
            return replay
        response = {
            "ok": True,
            "status": "planned" if mode == "dry_run" else "completed",
            "run_id": run_id or 901,
            "summary": {
                "phase": operation,
                "state_version": (expected_state_version or 0) + 1,
                "entries_count": len(entries or []),
                "coverage_count": len(coverage or []),
                "target_ref_sha256": "a" * 64,
            },
            # This deliberately contains raw-looking values: the public Gateway
            # test proves the bridge strips them before returning its envelope.
            "data": {
                "entries": list(entries or []),
                "evidence": dict(evidence or {}),
                "customer_response": customer_response,
                "published_snapshot_hash": published_snapshot_hash,
                "telegram_context_hash": telegram_context_hash,
                "telegram_message": telegram_message,
                "telegram_message_kind": telegram_message_kind,
                "message_sha256": "c" * 64,
            },
            "verification": {"manager_contract_verified": True},
            "warnings": ["store_quote_conductor_fake"],
            "meta": {"idempotency_replay": False},
        }
        state["conductor_receipts"][idempotency_key] = {
            "request_hash": request_hash,
            "response": copy.deepcopy(response),
        }
        return response


class StoreQuoteConductorCasesMixin:
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
            "download_store_quote_vin_photo",
            "store_management_action",
            STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
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
                    if name in {"store_management_action", STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME}
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

    async def test_generic_store_owner_api_is_not_a_raw_escape_hatch(self) -> None:
        server, _state = self._create_store_server()
        discover = await server._tool_manager.get_tool("discover_raw_capabilities").run(
            {"query": "store_owner_api"}, convert_result=False
        )
        self.assertEqual([], discover.structuredContent["data"]["capabilities"])

        schema = await server._tool_manager.get_tool("get_raw_capability_schema").run(
            {"name": "store_owner_api"}, convert_result=False
        )
        self.assertFalse(schema.structuredContent["ok"])
        self.assertIn("named_workflow_required", schema.structuredContent["warnings"])

        call = await server._tool_manager.get_tool("call_raw_capability").run(
            {
                "name": "store_owner_api",
                "arguments": {"operation_id": "replace_estimate_draft", "mode": "dry_run"},
                "schema_hash": "0" * 64,
            },
            convert_result=False,
        )
        self.assertFalse(call.structuredContent["ok"])
        self.assertIn("named_workflow_required", call.structuredContent["warnings"])

    async def test_store_quote_conductor_is_narrow_inventory_operation_with_refs_only_result(
        self,
    ) -> None:
        server, state = self._create_store_server()
        inventory = server._tool_manager.get_tool("agent_inventory_workflow")
        public_names = {tool.name for tool in server._tool_manager.list_tools()}
        schema = inventory.parameters
        self.assertEqual(24, len(public_names))
        self.assertNotIn(STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME, public_names)
        self.assertIn(
            STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
            schema["properties"]["operation"]["enum"],
        )

        payload = {
            "operation": "draft",
            "quote_request_id": "quote-1",
            "run_id": 901,
            "expected_state_version": 4,
            "expected_revision": "2026-07-16T10:00:00+00:00",
            "correlation_id": "quote-conductor-draft-0001",
            "entries": [
                {
                    "name": "PRIVATE_PART_NAME",
                    "cost": "PRIVATE_CUSTOMER_PRICE",
                    "customer": "PRIVATE_CLIENT_REFERENCE",
                }
            ],
            "coverage": [{"request_item": "PRIVATE_SOURCE_ITEM"}],
            "evidence": {"source": "PRIVATE_SOURCE_REFERENCE"},
        }
        dry_run = await inventory.run(
            {
                "operation": STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
                "payload": payload,
                "idempotency_key": "quote-conductor-draft-dry-0001",
                "mode": "dry_run",
            },
            convert_result=False,
        )
        applied = await inventory.run(
            {
                "operation": STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
                "payload": payload,
                "idempotency_key": "quote-conductor-draft-apply-0001",
                "mode": "apply",
            },
            convert_result=False,
        )
        replay = await inventory.run(
            {
                "operation": STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
                "payload": payload,
                "idempotency_key": "quote-conductor-draft-apply-0001",
                "mode": "apply",
            },
            convert_result=False,
        )

        self.assertTrue(dry_run.structuredContent["ok"])
        self.assertTrue(applied.structuredContent["ok"])
        self.assertTrue(replay.structuredContent["ok"])
        self.assertEqual("draft", applied.structuredContent["summary"]["conductor_operation"])
        self.assertEqual("apply", applied.structuredContent["summary"]["mode"])
        self.assertTrue(replay.structuredContent["summary"]["deduplicated"])
        self.assertTrue(applied.structuredContent["meta"]["refs_only"])
        self.assertEqual("write", applied.structuredContent["summary"]["risk"])
        self.assertTrue(applied.structuredContent["meta"]["external_store_write"])
        public_payload = json.dumps(
            [dry_run.structuredContent, applied.structuredContent, replay.structuredContent],
            ensure_ascii=False,
        )
        for private_value in (
            "PRIVATE_PART_NAME",
            "PRIVATE_CUSTOMER_PRICE",
            "PRIVATE_CLIENT_REFERENCE",
            "PRIVATE_SOURCE_ITEM",
            "PRIVATE_SOURCE_REFERENCE",
        ):
            self.assertNotIn(private_value, public_payload)
        conductor_calls = [
            arguments
            for name, arguments in state["calls"]
            if name == STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME
        ]
        self.assertEqual(3, len(conductor_calls))
        self.assertEqual("draft", conductor_calls[0]["operation"])
        self.assertEqual("dry_run", conductor_calls[0]["mode"])
        self.assertEqual("apply", conductor_calls[1]["mode"])
        self.assertFalse(any(name == "store_management_action" for name, _ in state["calls"]))
        self.assertFalse(any(name == "start_workflow" for name, _ in state["calls"]))

    async def test_conductor_draft_and_reopen_require_full_write_guards(self) -> None:
        server, state = self._create_store_server()
        inventory = server._tool_manager.get_tool("agent_inventory_workflow")
        payloads = {
            "draft": {
                "operation": "draft",
                "quote_request_id": "quote-1",
                "entries": [{"name": "PART"}],
                "coverage": [{"request_item": "PART"}],
            },
            "reopen": {
                "operation": "reopen",
                "quote_request_id": "quote-1",
            },
        }
        required = {
            "run_id",
            "expected_state_version",
            "expected_revision",
            "correlation_id",
            "idempotency_key",
        }
        for operation, payload in payloads.items():
            with self.subTest(operation=operation):
                blocked = await inventory.run(
                    {
                        "operation": STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
                        "payload": payload,
                        "mode": "apply",
                    },
                    convert_result=False,
                )
                self.assertFalse(blocked.structuredContent["ok"])
                self.assertIn(
                    "store_quote_conductor_required_fields_missing_or_invalid",
                    blocked.structuredContent["warnings"],
                )
                self.assertTrue(
                    required.issubset(set(blocked.structuredContent["summary"]["missing_fields"]))
                )
        self.assertFalse(
            any(name == STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME for name, _ in state["calls"])
        )
        self.assertFalse(any(name == "start_workflow" for name, _ in state["calls"]))

    async def test_conductor_reopen_is_a_guarded_store_write(self) -> None:
        server, state = self._create_store_server()
        inventory = server._tool_manager.get_tool("agent_inventory_workflow")
        reopened = await inventory.run(
            {
                "operation": STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
                "payload": {
                    "operation": "reopen",
                    "quote_request_id": "quote-1",
                    "run_id": 901,
                    "expected_state_version": 4,
                    "expected_revision": "2026-07-16T10:00:00+00:00",
                    "correlation_id": "quote-conductor-reopen-0001",
                },
                "idempotency_key": "quote-conductor-reopen-apply-0001",
                "mode": "apply",
            },
            convert_result=False,
        )
        self.assertTrue(reopened.structuredContent["ok"])
        self.assertEqual("write", reopened.structuredContent["summary"]["risk"])
        self.assertTrue(reopened.structuredContent["meta"]["external_store_write"])
        calls = [
            arguments
            for name, arguments in state["calls"]
            if name == STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME
        ]
        self.assertEqual(1, len(calls))
        self.assertEqual("reopen", calls[0]["operation"])
        self.assertEqual("apply", calls[0]["mode"])
        self.assertEqual(901, calls[0]["run_id"])
        self.assertEqual(4, calls[0]["expected_state_version"])
        self.assertEqual("2026-07-16T10:00:00+00:00", calls[0]["expected_revision"])
        self.assertEqual("quote-conductor-reopen-0001", calls[0]["correlation_id"])

    async def test_legacy_dialogue_operations_are_unavailable_and_do_not_call_conductor(
        self,
    ) -> None:
        server, state = self._create_store_server()
        inventory = server._tool_manager.get_tool("agent_inventory_workflow")
        for operation in ("clarification", "wait", "reply"):
            with self.subTest(operation=operation):
                result = await inventory.run(
                    {
                        "operation": STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME,
                        "payload": {
                            "operation": operation,
                            "quote_request_id": "quote-1",
                            "run_id": 901,
                            "expected_state_version": 5,
                            "telegram_message": "PRIVATE_TELEGRAM_TEXT without style rules",
                        },
                        "idempotency_key": f"quote-conductor-{operation}-0001",
                    },
                    convert_result=False,
                )
                self.assertFalse(result.structuredContent["ok"])
                self.assertEqual("unavailable", result.structuredContent["status"])
                self.assertIn(
                    "store_quote_conductor_dialogue_workflow_required",
                    result.structuredContent["warnings"],
                )

        self.assertEqual(
            [],
            [
                arguments
                for name, arguments in state["calls"]
                if name == STORE_QUOTE_CONDUCTOR_CAPABILITY_NAME
            ],
        )

    def test_telegram_text_validation_is_technical_only(self) -> None:
        self.assertTrue(_valid_store_quote_telegram_text("Two sentences. No forced question."))
        self.assertFalse(_valid_store_quote_telegram_text("line one\nline two"))
        self.assertFalse(_valid_store_quote_telegram_text(""))
