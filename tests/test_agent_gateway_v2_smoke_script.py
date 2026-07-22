from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "check_agent_gateway_v2.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("check_agent_gateway_v2", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("check_agent_gateway_v2.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def tool_result(payload: dict, *, is_error: bool = False):
    return SimpleNamespace(structuredContent=payload, isError=is_error)


class ScriptSession:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return self.handler(name, arguments)


class FailingScriptSession:
    async def call_tool(self, _name: str, _arguments: dict):
        raise ConnectionError("sensitive transport detail")


def raw_write_result(executor: dict, *, check: str = "executor_contract_only"):
    return tool_result(
        {
            "ok": True,
            "status": "completed",
            "data": executor,
            "verification": {
                "schema_hash_verified": True,
                "executor_ok": True,
                "passed": True,
                "ledger_closed": True,
                "check": check,
            },
        }
    )


class AgentGatewayV2SmokeScriptTests(unittest.TestCase):
    def test_expected_surface_is_exactly_24_tools(self) -> None:
        module = load_script_module()

        self.assertEqual(24, len(module.EXPECTED_TOOL_NAMES))
        self.assertFalse(module.EXPECTED_TOOL_NAMES & module.FORBIDDEN_LEGACY_TOOL_NAMES)

    def test_exhaustive_flag_is_explicit(self) -> None:
        module = load_script_module()

        standard = module.build_parser().parse_args([])
        exhaustive = module.build_parser().parse_args(["--exhaustive"])

        self.assertFalse(standard.exhaustive)
        self.assertTrue(exhaustive.exhaustive)

    def test_release_revision_and_attempt_are_required_for_maintenance_safe(self) -> None:
        module = load_script_module()
        ordinary = module.build_parser().parse_args(["--exhaustive"])
        missing_revision = module.build_parser().parse_args(["--exhaustive", "--maintenance-safe"])
        missing_attempt = module.build_parser().parse_args(
            ["--exhaustive", "--maintenance-safe", "--release-revision", "a" * 40]
        )

        with patch.dict(module.os.environ, {}, clear=True):
            ordinary_result = asyncio.run(module.check_gateway(ordinary))
            missing_revision_result = asyncio.run(module.check_gateway(missing_revision))
            missing_attempt_result = asyncio.run(module.check_gateway(missing_attempt))

        self.assertIn("token environment variable is missing", ordinary_result["error"])
        self.assertEqual(
            missing_revision_result["error"],
            "--maintenance-safe requires --release-revision",
        )
        self.assertEqual(
            missing_attempt_result["error"],
            "release attempt id must be an opaque 8-160 character identifier",
        )

    def test_release_smoke_identity_is_deterministic_and_attempt_bound(self) -> None:
        module = load_script_module()
        first = module._release_smoke_id("a" * 40, "attempt-0001")

        self.assertEqual(first, module._release_smoke_id("a" * 40, "attempt-0001"))
        self.assertNotEqual(first, module._release_smoke_id("a" * 40, "attempt-0002"))
        self.assertNotEqual(first, module._release_smoke_id("b" * 40, "attempt-0001"))
        self.assertEqual(32, len(first))
        with self.assertRaises(ValueError):
            module._release_smoke_id("not-a-revision", "attempt-0001")
        with self.assertRaises(ValueError):
            module._release_smoke_id("a" * 40, "short")

    def test_store_readiness_gate_is_explicit(self) -> None:
        module = load_script_module()

        standard = module.build_parser().parse_args([])
        required = module.build_parser().parse_args(["--require-store"])

        self.assertFalse(standard.require_store)
        self.assertTrue(required.require_store)

    def test_store_readiness_gate_does_not_advance_digest_cursor(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn('"entity": "store_state"', source)
        self.assertIn('"entity": "store_sourcing_offer"', source)
        self.assertIn('"store_quote_adapter_configured"', source)
        self.assertIn('"store_quote_full_read_enabled"', source)
        self.assertIn('"store_quote_draft_write_enabled"', source)
        self.assertIn('"store_supplier_lookup_enabled"', source)
        self.assertNotIn('{"scope": "store"', source)

    def test_exception_group_diagnostics_expose_only_wrapped_safe_context(self) -> None:
        module = load_script_module()
        failure = ExceptionGroup(
            "transport group may be sensitive",
            [
                ConnectionError("secret transport response"),
                RuntimeError(
                    "MCP tool call failed: "
                    "call_raw_capability[api:/api/change_feed/bootstrap] (ConnectionError)"
                ),
            ],
        )

        diagnostics = module._failure_diagnostics(failure)

        self.assertEqual("ExceptionGroup", diagnostics["failure_type"])
        self.assertEqual(
            ["ConnectionError", "RuntimeError"],
            diagnostics["failure_leaf_types"],
        )
        self.assertIn("api:/api/change_feed/bootstrap", diagnostics["failure_detail"])
        serialized = str(diagnostics)
        self.assertNotIn("secret transport response", serialized)
        self.assertNotIn("transport group may be sensitive", serialized)

        unsafe = module._failure_diagnostics(
            ExceptionGroup("outer secret", [RuntimeError("SDK secret-bearing detail")])
        )
        self.assertNotIn("failure_detail", unsafe)

    def test_state_version_requires_integer_summary_value(self) -> None:
        module = load_script_module()

        self.assertEqual(7, module._state_version({"summary": {"state_version": 7}}))
        with self.assertRaisesRegex(RuntimeError, "state_version"):
            module._state_version({"summary": {}})

    def test_exhaustive_inventory_contract_includes_required_card_target(self) -> None:
        module = load_script_module()

        arguments = module._safe_inventory_contract_arguments("case-id")

        self.assertEqual("write_off", arguments["planned_changes"]["movement_type"])
        self.assertEqual("synthetic-card-target", arguments["planned_changes"]["card_id"])
        self.assertEqual("gateway-v2-contract-case-id", arguments["idempotency_key"])
        self.assertTrue(arguments["dry_run"])

    def test_store_exhaustive_probes_are_conditioned_on_both_flags(self) -> None:
        source = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn("require_store=args.require_store", source)
        self.assertIn("if require_store:", source)
        self.assertIn("_run_store_owner_probes(", source)
        self.assertIn("_run_change_feed_probes(", source)


class AgentGatewayV2SmokeProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_failure_reports_only_fixed_capability_context(self) -> None:
        module = load_script_module()

        with self.assertRaisesRegex(
            RuntimeError,
            r"MCP tool call failed: call_raw_capability\[api:/api/change_feed/bootstrap\] "
            r"\(ConnectionError\)",
        ) as caught:
            await module._call(
                FailingScriptSession(),
                {},
                "call_raw_capability",
                {"name": "api:/api/change_feed/bootstrap", "secret": "must-not-leak"},
            )

        self.assertNotIn("sensitive transport detail", str(caught.exception))
        self.assertNotIn("must-not-leak", str(caught.exception))

    async def test_repeated_public_tool_failure_cannot_be_masked(self) -> None:
        module = load_script_module()
        results = iter([tool_result({"ok": False}), tool_result({"ok": True, "data": {}})])
        session = ScriptSession(lambda _name, _arguments: next(results))
        calls: dict[str, bool] = {}

        await module._call(session, calls, "call_raw_capability", {"case": 1})
        await module._call(session, calls, "call_raw_capability", {"case": 2})

        self.assertFalse(calls["call_raw_capability"])

    async def test_web_checks_include_read_only_automotive_probes(self) -> None:
        module = load_script_module()
        discovery_items = {
            "search_web_multi": {"name": "search_web_multi", "risk": "read"},
            "fetch_page_excerpt": {"name": "fetch_page_excerpt", "risk": "read"},
            "fetch_page_browser": {"name": "fetch_page_browser", "risk": "read"},
            "как выставить ГРМ на Mercedes": {
                "name": "recommend_automotive_sources",
                "risk": "read",
                "matched_terms": ["грм"],
            },
            "официальный отзыв автомобиля": {
                "name": "lookup_public_automotive_evidence",
                "risk": "read",
                "matched_terms": ["официальный отзыв"],
            },
        }
        raw_calls: list[dict] = []

        def handler(name: str, arguments: dict):
            if name == "discover_raw_capabilities":
                return tool_result(
                    {
                        "ok": True,
                        "data": {"capabilities": [discovery_items[arguments["query"]]]},
                    }
                )
            if name == "get_raw_capability_schema":
                return tool_result(
                    {
                        "ok": True,
                        "summary": {"schema_hash": "schema-hash", "risk": "read"},
                    }
                )
            self.assertEqual("call_raw_capability", name)
            raw_calls.append(arguments)
            return tool_result({"ok": True, "data": {}})

        checks = await module._run_web_checks(ScriptSession(handler), {})

        self.assertTrue(all(checks.values()))
        self.assertTrue(checks["automotive_timing_read_only"])
        self.assertTrue(checks["public_automotive_evidence_read_only"])
        automotive_call = next(
            item for item in raw_calls if item["name"] == "lookup_public_automotive_evidence"
        )
        self.assertEqual(
            {
                "make": "Mercedes-Benz",
                "system": "automatic transmission",
                "topics": ["fluids"],
                "limit": 1,
            },
            automotive_call["arguments"],
        )
        self.assertNotIn("vin", automotive_call["arguments"])
        self.assertFalse(automotive_call["allow_large_output"])

    def test_change_feed_page_rejects_gap_after_acked_sequence(self) -> None:
        module = load_script_module()
        page = {
            "format": "crm_change_feed_page_v1",
            "generation": "generation-gap",
            "consumer_id": module.CHANGE_FEED_SMOKE_CONSUMER_ID,
            "high_water": 6,
            "delivery_high_water": 6,
            "acked_sequence": 4,
            "from_sequence": 6,
            "through_sequence": 6,
            "events": [
                {
                    "sequence": 6,
                    "event_id": "event-6",
                    "occurred_at": "2026-07-21T00:00:00+00:00",
                    "action": "card_updated",
                    "entity_type": "card",
                    "entity_id": "card:opaque",
                    "change_type": "update",
                    "tombstone": False,
                }
            ],
            "replay_cursor": "replay-gap",
            "next_cursor": None,
            "ack": "ack-gap",
            "caught_up": True,
        }

        with self.assertRaisesRegex(RuntimeError, "sequence window"):
            module._validated_change_feed_page(
                page,
                consumer_id=module.CHANGE_FEED_SMOKE_CONSUMER_ID,
            )

    async def test_store_owner_probe_reads_revision_and_never_applies(self) -> None:
        module = load_script_module()
        operation_id = "create_category_api_v1_categories_post"
        schema_risks = {
            module.STORE_OWNER_CAPABILITIES_NAME: "read",
            module.STORE_OWNER_API_NAME: "write",
        }

        def handler(name: str, arguments: dict):
            if name == "discover_raw_capabilities":
                capability_name = arguments["query"]
                return tool_result(
                    {
                        "ok": True,
                        "data": {
                            "capabilities": [
                                {"name": capability_name, "risk": schema_risks[capability_name]}
                            ]
                        },
                    }
                )
            if name == "get_raw_capability_schema":
                capability_name = arguments["name"]
                return tool_result(
                    {
                        "ok": True,
                        "summary": {
                            "schema_hash": f"hash-{capability_name}",
                            "risk": schema_risks[capability_name],
                        },
                        "data": {"input_schema": {"type": "object"}},
                    }
                )
            self.assertEqual("call_raw_capability", name)
            raw_name = arguments["name"]
            raw_arguments = arguments["arguments"]
            if raw_name == module.STORE_OWNER_CAPABILITIES_NAME:
                return tool_result(
                    {
                        "ok": True,
                        "status": "completed",
                        "data": {
                            "ok": True,
                            "items": [
                                {
                                    "operation_id": operation_id,
                                    "method": "POST",
                                    "path": "/api/v1/categories",
                                    "risk": "write",
                                    "request_content_types": ["application/json"],
                                    "request_required": True,
                                    "path_parameters": [],
                                    "schema_hash": "store-schema-hash",
                                }
                            ],
                        },
                        "verification": {
                            "schema_hash_verified": True,
                            "executor_ok": True,
                            "passed": True,
                            "ledger_closed": True,
                        },
                    }
                )
            self.assertEqual(module.STORE_OWNER_API_NAME, raw_name)
            if raw_arguments["mode"] == "revision":
                return raw_write_result(
                    {
                        "ok": True,
                        "status": "completed",
                        "summary": {
                            "operation_id": operation_id,
                            "method": "POST",
                            "path": "/api/v1/categories",
                            "route_key": "POST /api/v1/categories",
                            "current_revision": None,
                            "revision_kind": "revision_exempt",
                            "expected_revision_required": False,
                            "contract_version": "store-owner-preflight-v1",
                        },
                    },
                    check="store_owner_read_response_contract",
                )
            self.assertEqual("dry_run", raw_arguments["mode"])
            return raw_write_result(
                {
                    "ok": True,
                    "status": "planned",
                    "summary": {
                        "operation_id": operation_id,
                        "method": "POST",
                        "path": "/api/v1/categories",
                        "current_revision": None,
                        "revision_kind": "revision_exempt",
                        "contract_version": "store-owner-preflight-v1",
                        "dry_run_proof": "a" * 64,
                    },
                    "meta": {
                        "request_dispatched": True,
                        "outcome_uncertain": False,
                        "domain_handler_executed": False,
                    },
                },
                check="store_owner_server_dry_run_receipt",
            )

        session = ScriptSession(handler)
        calls: dict[str, bool] = {}
        result = await module._run_store_owner_probes(
            session,
            calls,
            smoke_id="1" * 32,
        )

        self.assertTrue(result["ok"])
        owner_calls = [
            arguments["arguments"]
            for name, arguments in session.calls
            if name == "call_raw_capability" and arguments["name"] == module.STORE_OWNER_API_NAME
        ]
        self.assertEqual(["revision", "dry_run"], [item["mode"] for item in owner_calls])
        self.assertNotIn("apply", {item["mode"] for item in owner_calls})
        self.assertEqual("collection:/api/v1/categories", owner_calls[1]["target_id"])
        self.assertIsNone(owner_calls[1]["expected_revision"])

    async def test_store_owner_probe_fails_closed_if_domain_handler_executes(self) -> None:
        module = load_script_module()
        operation_id = "create_category_api_v1_categories_post"

        def handler(name: str, arguments: dict):
            if name == "discover_raw_capabilities":
                capability_name = arguments["query"]
                risk = (
                    "read" if capability_name == module.STORE_OWNER_CAPABILITIES_NAME else "write"
                )
                return tool_result(
                    {
                        "ok": True,
                        "data": {"capabilities": [{"name": capability_name, "risk": risk}]},
                    }
                )
            if name == "get_raw_capability_schema":
                capability_name = arguments["name"]
                risk = (
                    "read" if capability_name == module.STORE_OWNER_CAPABILITIES_NAME else "write"
                )
                return tool_result(
                    {
                        "ok": True,
                        "summary": {"schema_hash": "hash", "risk": risk},
                        "data": {"input_schema": {"type": "object"}},
                    }
                )
            raw_name = arguments["name"]
            raw_arguments = arguments["arguments"]
            if raw_name == module.STORE_OWNER_CAPABILITIES_NAME:
                return tool_result(
                    {
                        "ok": True,
                        "data": {
                            "ok": True,
                            "items": [
                                {
                                    "operation_id": operation_id,
                                    "method": "POST",
                                    "path": "/api/v1/categories",
                                    "risk": "write",
                                    "request_content_types": ["application/json"],
                                    "request_required": True,
                                    "path_parameters": [],
                                    "schema_hash": "schema",
                                }
                            ],
                        },
                    }
                )
            if raw_arguments["mode"] == "revision":
                return raw_write_result(
                    {
                        "ok": True,
                        "status": "completed",
                        "summary": {
                            "operation_id": operation_id,
                            "method": "POST",
                            "path": "/api/v1/categories",
                            "route_key": "POST /api/v1/categories",
                            "current_revision": None,
                            "revision_kind": "revision_exempt",
                            "expected_revision_required": False,
                            "contract_version": "store-owner-preflight-v1",
                        },
                    },
                    check="store_owner_read_response_contract",
                )
            return raw_write_result(
                {
                    "ok": True,
                    "status": "planned",
                    "summary": {
                        "operation_id": operation_id,
                        "method": "POST",
                        "path": "/api/v1/categories",
                        "current_revision": None,
                        "revision_kind": "revision_exempt",
                        "contract_version": "store-owner-preflight-v1",
                        "dry_run_proof": "b" * 64,
                    },
                    "meta": {
                        "request_dispatched": True,
                        "outcome_uncertain": False,
                        "domain_handler_executed": True,
                    },
                },
                check="store_owner_server_dry_run_receipt",
            )

        with self.assertRaisesRegex(RuntimeError, "dry-run proof"):
            await module._run_store_owner_probes(
                ScriptSession(handler),
                {},
                smoke_id="2" * 32,
            )

    async def test_change_feed_probe_replays_exact_page_and_acks(self) -> None:
        module = load_script_module()
        risks = {
            module.CHANGE_FEED_BOOTSTRAP_NAME: "write",
            module.CHANGE_FEED_READ_NAME: "read",
            module.CHANGE_FEED_ACK_NAME: "write",
        }
        read_count = 0

        def handler(name: str, arguments: dict):
            nonlocal read_count
            if name == "discover_raw_capabilities":
                capability_name = arguments["query"]
                return tool_result(
                    {
                        "ok": True,
                        "data": {
                            "capabilities": [
                                {"name": capability_name, "risk": risks[capability_name]}
                            ]
                        },
                    }
                )
            if name == "get_raw_capability_schema":
                capability_name = arguments["name"]
                return tool_result(
                    {
                        "ok": True,
                        "summary": {
                            "schema_hash": f"hash-{capability_name}",
                            "risk": risks[capability_name],
                        },
                        "data": {"input_schema": {"type": "object"}},
                    }
                )
            raw_name = arguments["name"]
            consumer_id = arguments["arguments"]["consumer_id"]
            if raw_name == module.CHANGE_FEED_BOOTSTRAP_NAME:
                return raw_write_result(
                    {
                        "ok": True,
                        "data": {
                            "format": "crm_change_feed_bootstrap_v1",
                            "generation": "generation-1",
                            "consumer_id": consumer_id,
                            "high_water": 1,
                            "acked_sequence": 0,
                            "pending_high_water": None,
                            "has_unacked": True,
                        },
                    },
                    check="exact_change_feed_bootstrap_checkpoint",
                )
            if raw_name == module.CHANGE_FEED_READ_NAME:
                read_count += 1
                event = {
                    "sequence": 1,
                    "event_id": "event-1",
                    "occurred_at": "2026-07-21T00:00:00+00:00",
                    "action": "card_updated",
                    "entity_type": "card",
                    "entity_id": "card:opaque",
                    "change_type": "update",
                    "tombstone": False,
                    "correlation_ref": "corr:opaque",
                    "idempotency_ref": "idem:opaque",
                    "producer": "audit_event",
                }
                return tool_result(
                    {
                        "ok": True,
                        "data": {
                            "ok": True,
                            "data": {
                                "format": "crm_change_feed_page_v1",
                                "generation": "generation-1",
                                "consumer_id": consumer_id,
                                "high_water": 1,
                                "delivery_high_water": 1,
                                "acked_sequence": 0,
                                "from_sequence": 1,
                                "through_sequence": 1,
                                "events": [event],
                                "replay_cursor": "replay-token",
                                "next_cursor": None,
                                "ack": "ack-token",
                                "caught_up": True,
                            },
                        },
                    }
                )
            self.assertEqual(module.CHANGE_FEED_ACK_NAME, raw_name)
            return raw_write_result(
                {
                    "ok": True,
                    "data": {
                        "format": "crm_change_feed_ack_v1",
                        "generation": "generation-1",
                        "consumer_id": consumer_id,
                        "high_water": 1,
                        "acked_sequence": 1,
                        "changed": True,
                        "delivery_complete": True,
                    },
                },
                check="exact_change_feed_ack_checkpoint",
            )

        session = ScriptSession(handler)
        result = await module._run_change_feed_probes(
            session,
            {},
            smoke_id="3" * 32,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(2, read_count)
        self.assertEqual(1, result["event_count"])
        self.assertNotIn("events", result)
        read_arguments = [
            arguments["arguments"]
            for name, arguments in session.calls
            if name == "call_raw_capability" and arguments["name"] == module.CHANGE_FEED_READ_NAME
        ]
        self.assertNotIn("cursor", read_arguments[0])
        self.assertEqual("replay-token", read_arguments[1]["cursor"])

    async def test_change_feed_probe_fails_closed_on_replay_drift(self) -> None:
        module = load_script_module()
        risks = {
            module.CHANGE_FEED_BOOTSTRAP_NAME: "write",
            module.CHANGE_FEED_READ_NAME: "read",
            module.CHANGE_FEED_ACK_NAME: "write",
        }
        read_count = 0

        def handler(name: str, arguments: dict):
            nonlocal read_count
            if name == "discover_raw_capabilities":
                capability_name = arguments["query"]
                return tool_result(
                    {
                        "ok": True,
                        "data": {
                            "capabilities": [
                                {"name": capability_name, "risk": risks[capability_name]}
                            ]
                        },
                    }
                )
            if name == "get_raw_capability_schema":
                capability_name = arguments["name"]
                return tool_result(
                    {
                        "ok": True,
                        "summary": {"schema_hash": "hash", "risk": risks[capability_name]},
                        "data": {"input_schema": {"type": "object"}},
                    }
                )
            raw_name = arguments["name"]
            consumer_id = arguments["arguments"]["consumer_id"]
            if raw_name == module.CHANGE_FEED_BOOTSTRAP_NAME:
                return raw_write_result(
                    {
                        "ok": True,
                        "data": {
                            "format": "crm_change_feed_bootstrap_v1",
                            "generation": "generation-1",
                            "consumer_id": consumer_id,
                            "high_water": 1,
                            "acked_sequence": 0,
                            "pending_high_water": None,
                            "has_unacked": True,
                        },
                    },
                    check="exact_change_feed_bootstrap_checkpoint",
                )
            if raw_name == module.CHANGE_FEED_READ_NAME:
                read_count += 1
                event_id = f"event-{read_count}"
                event = {
                    "sequence": 1,
                    "event_id": event_id,
                    "occurred_at": "2026-07-21T00:00:00+00:00",
                    "action": "card_updated",
                    "entity_type": "card",
                    "entity_id": "card:opaque",
                    "change_type": "update",
                    "tombstone": False,
                }
                return tool_result(
                    {
                        "ok": True,
                        "data": {
                            "ok": True,
                            "data": {
                                "format": "crm_change_feed_page_v1",
                                "generation": "generation-1",
                                "consumer_id": consumer_id,
                                "high_water": 1,
                                "delivery_high_water": 1,
                                "acked_sequence": 0,
                                "from_sequence": 1,
                                "through_sequence": 1,
                                "events": [event],
                                "replay_cursor": "replay",
                                "next_cursor": None,
                                "ack": "ack",
                                "caught_up": True,
                            },
                        },
                    }
                )
            self.assertEqual(module.CHANGE_FEED_ACK_NAME, raw_name)
            return raw_write_result(
                {
                    "ok": True,
                    "data": {
                        "format": "crm_change_feed_ack_v1",
                        "generation": "generation-1",
                        "consumer_id": consumer_id,
                        "acked_sequence": 1,
                        "changed": True,
                        "delivery_complete": True,
                    },
                },
                check="exact_change_feed_ack_checkpoint",
            )

        session = ScriptSession(handler)
        with self.assertRaisesRegex(RuntimeError, "replay"):
            await module._run_change_feed_probes(
                session,
                {},
                smoke_id="4" * 32,
            )
        self.assertTrue(
            any(
                name == "call_raw_capability" and arguments["name"] == module.CHANGE_FEED_ACK_NAME
                for name, arguments in session.calls
            )
        )

    async def test_change_feed_probe_accepts_clean_caught_up_empty_page(self) -> None:
        module = load_script_module()
        risks = {
            module.CHANGE_FEED_BOOTSTRAP_NAME: "write",
            module.CHANGE_FEED_READ_NAME: "read",
            module.CHANGE_FEED_ACK_NAME: "write",
        }

        def handler(name: str, arguments: dict):
            if name == "discover_raw_capabilities":
                capability_name = arguments["query"]
                return tool_result(
                    {
                        "ok": True,
                        "data": {
                            "capabilities": [
                                {"name": capability_name, "risk": risks[capability_name]}
                            ]
                        },
                    }
                )
            if name == "get_raw_capability_schema":
                capability_name = arguments["name"]
                return tool_result(
                    {
                        "ok": True,
                        "summary": {"schema_hash": "hash", "risk": risks[capability_name]},
                        "data": {"input_schema": {"type": "object"}},
                    }
                )
            raw_name = arguments["name"]
            consumer_id = arguments["arguments"]["consumer_id"]
            if raw_name == module.CHANGE_FEED_BOOTSTRAP_NAME:
                return raw_write_result(
                    {
                        "ok": True,
                        "data": {
                            "format": "crm_change_feed_bootstrap_v1",
                            "generation": "generation-empty",
                            "consumer_id": consumer_id,
                            "high_water": 7,
                            "acked_sequence": 7,
                            "pending_high_water": None,
                            "has_unacked": False,
                        },
                    },
                    check="exact_change_feed_bootstrap_checkpoint",
                )
            if raw_name == module.CHANGE_FEED_READ_NAME:
                return tool_result(
                    {
                        "ok": True,
                        "data": {
                            "ok": True,
                            "data": {
                                "format": "crm_change_feed_page_v1",
                                "generation": "generation-empty",
                                "consumer_id": consumer_id,
                                "high_water": 7,
                                "delivery_high_water": 7,
                                "acked_sequence": 7,
                                "from_sequence": None,
                                "through_sequence": None,
                                "events": [],
                                "replay_cursor": None,
                                "next_cursor": None,
                                "ack": None,
                                "caught_up": True,
                            },
                        },
                    }
                )
            raise AssertionError("ACK must not run for a clean empty page")

        session = ScriptSession(handler)
        result = await module._run_change_feed_probes(
            session,
            {},
            smoke_id="7" * 32,
        )

        self.assertEqual("caught_up_empty", result["status"])
        self.assertEqual(module.CHANGE_FEED_SMOKE_CONSUMER_ID, result["consumer_id"])
        self.assertFalse(result["replay_required"])
        self.assertFalse(result["ack_required"])
        self.assertFalse(
            any(
                name == "call_raw_capability" and arguments["name"] == module.CHANGE_FEED_ACK_NAME
                for name, arguments in session.calls
            )
        )

        checks = module._change_feed_probe_checks(result)
        self.assertTrue(all(checks.values()))

    def test_change_feed_probe_gate_rejects_inconsistent_empty_status(self) -> None:
        module = load_script_module()

        checks = module._change_feed_probe_checks(
            {
                "ok": True,
                "status": "caught_up_empty",
                "bootstrap_ledger_closed": True,
                "replay_required": True,
                "replay_exact": True,
                "ack_required": True,
                "ack_ledger_closed": True,
                "pii_free_projection": True,
            }
        )

        self.assertFalse(checks["change_feed_bootstrap_and_ack_ledgers_ok"])
        self.assertFalse(checks["change_feed_replay_exact"])

    def test_change_feed_probe_gate_accepts_bounded_partial_and_deduplicated_ack(self) -> None:
        module = load_script_module()
        base = {
            "ok": True,
            "bootstrap_ledger_closed": True,
            "replay_required": True,
            "replay_exact": True,
            "ack_required": True,
            "ack_ledger_closed": True,
            "pii_free_projection": True,
        }

        for status in ("replayed_and_acked_partial", "replayed_and_acked_deduplicated"):
            self.assertTrue(
                all(module._change_feed_probe_checks({**base, "status": status}).values())
            )


if __name__ == "__main__":
    unittest.main()
