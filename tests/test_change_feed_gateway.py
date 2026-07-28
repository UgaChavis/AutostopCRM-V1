from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from minimal_kanban.mcp.raw_gateway import (  # noqa: E402
    CHANGE_FEED_ACK_ROUTE,
    CHANGE_FEED_BOOTSTRAP_ROUTE,
    CHANGE_FEED_READ_ROUTE,
    RAW_API_READ_ROUTES,
    RAW_API_WRITE_ROUTES,
    schema_hash,
    verify_virtual_api_write_readback,
    virtual_api_name,
    virtual_api_risk,
    virtual_api_route,
    virtual_api_schema,
)
from minimal_kanban.mcp.server import create_mcp_server  # noqa: E402
from scripts import crm_capability_parity  # noqa: E402

GATEWAY_ENV = {
    "AUTOSTOP_DEPLOYMENT_ENV": "development",
    "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
}


class ChangeFeedBoardApi:
    base_url = "http://127.0.0.1:41731"

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.acked_sequence = 0

    def health(self) -> dict:
        return {"ok": True, "data": {"status": "healthy"}}

    def get_board_context(self) -> dict:
        return {"ok": True, "data": {"context": {}}}

    def get_cards(self, **_: object) -> dict:
        return {"ok": True, "data": {"cards": []}}

    def _request(
        self,
        path: str,
        payload: dict | None = None,
        *,
        method: str = "POST",
        extra_headers: dict[str, str] | None = None,
    ) -> dict:
        request_payload = dict(payload or {})
        self.requests.append(
            {
                "path": path,
                "payload": request_payload,
                "method": method,
                "extra_headers": dict(extra_headers or {}),
            }
        )
        if path == CHANGE_FEED_READ_ROUTE:
            events = [
                {
                    "sequence": index,
                    "event_id": f"event-{index}",
                    "occurred_at": "2026-07-21T00:00:00+00:00",
                    "action": "card_updated",
                    "entity_type": "card",
                    "entity_id": f"card-{index}",
                    "change_type": "upsert",
                    "tombstone": False,
                }
                for index in range(1, 26)
            ]
            return {
                "ok": True,
                "data": {
                    "format": "crm_change_feed_page_v1",
                    "generation": "generation-1",
                    "consumer_id": request_payload.get("consumer_id"),
                    "high_water": 25,
                    "delivery_high_water": 25,
                    "acked_sequence": 0,
                    "from_sequence": 1,
                    "through_sequence": 25,
                    "events": events,
                    "next_cursor": None,
                    "ack": "opaque-ack",
                    "caught_up": True,
                },
            }
        if path == CHANGE_FEED_ACK_ROUTE:
            self.acked_sequence = 1
            return {
                "ok": True,
                "data": {
                    "format": "crm_change_feed_ack_v1",
                    "generation": "generation-1",
                    "consumer_id": request_payload.get("consumer_id"),
                    "high_water": 1,
                    "acked_sequence": self.acked_sequence,
                    "changed": True,
                    "delivery_complete": True,
                },
            }
        if path == CHANGE_FEED_BOOTSTRAP_ROUTE:
            return {
                "ok": True,
                "data": {
                    "format": "crm_change_feed_bootstrap_v1",
                    "generation": "generation-1",
                    "consumer_id": request_payload.get("consumer_id"),
                    "high_water": 1,
                    "acked_sequence": self.acked_sequence,
                    "pending_high_water": None,
                    "has_unacked": self.acked_sequence < 1,
                },
            }
        return {"ok": True, "data": {"accepted": True, "path": path}}


class ChangeFeedRawGatewayContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(f"test.change_feed.gateway.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.manager_patch = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        self.env.start()
        self.manager_register = self.manager_patch.start()
        self.board_api = ChangeFeedBoardApi()
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

    async def call(self, name: str, arguments: dict):
        return await self.server._tool_manager.get_tool(name).run(arguments, convert_result=False)

    def test_routes_have_exact_schemas_and_risk_classes(self) -> None:
        self.assertIn(CHANGE_FEED_READ_ROUTE, RAW_API_READ_ROUTES)
        self.assertNotIn(CHANGE_FEED_READ_ROUTE, RAW_API_WRITE_ROUTES)
        self.assertNotIn(
            "/api/get_operator_profile",
            RAW_API_READ_ROUTES,
            "personal operator profiles must not be advertised to the service gateway",
        )
        for route in (CHANGE_FEED_BOOTSTRAP_ROUTE, CHANGE_FEED_ACK_ROUTE):
            self.assertIn(route, RAW_API_WRITE_ROUTES)
            self.assertNotIn(route, RAW_API_READ_ROUTES)
        expected_required = {
            CHANGE_FEED_BOOTSTRAP_ROUTE: ["consumer_id"],
            CHANGE_FEED_READ_ROUTE: ["consumer_id"],
            CHANGE_FEED_ACK_ROUTE: ["consumer_id", "ack"],
        }
        for route, required in expected_required.items():
            with self.subTest(route=route):
                name = virtual_api_name(route)
                schema = virtual_api_schema(route)
                self.assertEqual(route, virtual_api_route(name))
                self.assertEqual(required, schema["required"])
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(
                    "read" if route == CHANGE_FEED_READ_ROUTE else "write",
                    virtual_api_risk(route, name),
                )
                self.assertEqual(16, len(schema_hash(schema)))
        read_schema = virtual_api_schema(CHANGE_FEED_READ_ROUTE)
        self.assertEqual(25, read_schema["properties"]["limit"]["maximum"])

    async def test_all_routes_are_discoverable_without_adding_a_public_tool(self) -> None:
        public_names = {tool.name for tool in self.server._tool_manager.list_tools()}
        self.assertTrue(
            {"discover_raw_capabilities", "get_raw_capability_schema", "call_raw_capability"}
            <= public_names
        )
        self.assertFalse(any("change_feed" in name for name in public_names))
        for route in (
            CHANGE_FEED_BOOTSTRAP_ROUTE,
            CHANGE_FEED_READ_ROUTE,
            CHANGE_FEED_ACK_ROUTE,
        ):
            name = virtual_api_name(route)
            with self.subTest(route=route):
                discovered = await self.call("discover_raw_capabilities", {"query": name})
                capabilities = discovered.structuredContent["data"]["capabilities"]
                capability = next(item for item in capabilities if item["name"] == name)
                schema = await self.call("get_raw_capability_schema", {"name": name})
                self.assertEqual(
                    "read" if route == CHANGE_FEED_READ_ROUTE else "write",
                    capability["risk"],
                )
                self.assertEqual(
                    virtual_api_schema(route),
                    schema.structuredContent["data"]["input_schema"],
                )

    async def test_read_page_preserves_all_25_bounded_events_through_raw_gateway(self) -> None:
        name = virtual_api_name(CHANGE_FEED_READ_ROUTE)
        schema = virtual_api_schema(CHANGE_FEED_READ_ROUTE)
        result = await self.call(
            "call_raw_capability",
            {
                "name": name,
                "arguments": {"consumer_id": "owner", "limit": 25},
                "schema_hash": schema_hash(schema),
            },
        )

        self.assertTrue(result.structuredContent["ok"])
        delivered = result.structuredContent["data"]["data"]["events"]
        self.assertEqual(25, len(delivered))
        self.assertEqual(list(range(1, 26)), [event["sequence"] for event in delivered])
        self.assertEqual(CHANGE_FEED_READ_ROUTE, self.board_api.requests[0]["path"])
        self.assertEqual("mcp_agent_gateway_v2", self.board_api.requests[0]["payload"]["source"])

    async def test_checkpoint_writes_require_idempotency_before_executor(self) -> None:
        for route, arguments in (
            (CHANGE_FEED_BOOTSTRAP_ROUTE, {"consumer_id": "owner"}),
            (CHANGE_FEED_ACK_ROUTE, {"consumer_id": "owner", "ack": "opaque-ack"}),
        ):
            with self.subTest(route=route):
                name = virtual_api_name(route)
                result = await self.call(
                    "call_raw_capability",
                    {
                        "name": name,
                        "arguments": arguments,
                        "schema_hash": schema_hash(virtual_api_schema(route)),
                    },
                )
                self.assertFalse(result.structuredContent["ok"])
                self.assertIn(
                    "idempotency_key_required_for_raw_write",
                    result.structuredContent["warnings"],
                )
        self.assertEqual([], self.board_api.requests)

    async def test_checkpoint_writes_execute_with_ledger_and_exact_readback(self) -> None:
        state = {"version": 0}

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
                state["version"] = 1
                return {
                    "ok": True,
                    "run_id": 77,
                    "status": "planned",
                    "summary": {"id": 77, "deduplicated": False, "state_version": 1},
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
                state["version"] += 1
                return {
                    "ok": True,
                    "run_id": 77,
                    "status": status,
                    "summary": {"id": 77, "status": status, "state_version": state["version"]},
                }

        self.manager_register.side_effect = register_fake_ledger
        board_api = ChangeFeedBoardApi()
        server = create_mcp_server(
            board_api,
            self.logger,
            host="127.0.0.1",
            port=41832,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

        async def call(name: str, arguments: dict):
            return await server._tool_manager.get_tool(name).run(arguments, convert_result=False)

        for route, arguments, idempotency_key in (
            (
                CHANGE_FEED_BOOTSTRAP_ROUTE,
                {"consumer_id": "owner"},
                "change-feed-bootstrap-owner-v1",
            ),
            (
                CHANGE_FEED_ACK_ROUTE,
                {"consumer_id": "owner", "ack": "opaque-ack"},
                "change-feed-ack-owner-1-v1",
            ),
        ):
            with self.subTest(route=route):
                name = virtual_api_name(route)
                result = await call(
                    "call_raw_capability",
                    {
                        "name": name,
                        "arguments": arguments,
                        "schema_hash": schema_hash(virtual_api_schema(route)),
                        "idempotency_key": idempotency_key,
                    },
                )
                self.assertTrue(result.structuredContent["ok"])
                self.assertTrue(result.structuredContent["verification"]["passed"])
                self.assertTrue(result.structuredContent["verification"]["ledger_closed"])
        executed_routes = [request["path"] for request in board_api.requests]
        self.assertEqual(
            [
                CHANGE_FEED_BOOTSTRAP_ROUTE,
                CHANGE_FEED_BOOTSTRAP_ROUTE,
                CHANGE_FEED_ACK_ROUTE,
                CHANGE_FEED_BOOTSTRAP_ROUTE,
            ],
            executed_routes,
        )

    async def test_bootstrap_and_ack_have_exact_checkpoint_readback(self) -> None:
        async def invoke(_name: str, _arguments: dict) -> dict:
            acked_sequence = 9 if _arguments.get("consumer_id") == "owner-ack" else 0
            return {
                "ok": True,
                "data": {
                    "consumer_id": _arguments["consumer_id"],
                    "generation": "generation-1",
                    "acked_sequence": acked_sequence,
                },
            }

        bootstrap_result = await verify_virtual_api_write_readback(
            virtual_api_name(CHANGE_FEED_BOOTSTRAP_ROUTE),
            {"consumer_id": "owner"},
            {
                "ok": True,
                "data": {
                    "consumer_id": "owner",
                    "generation": "generation-1",
                    "acked_sequence": 0,
                },
            },
            invoke,
        )
        ack_result = await verify_virtual_api_write_readback(
            virtual_api_name(CHANGE_FEED_ACK_ROUTE),
            {"consumer_id": "owner-ack", "ack": "opaque"},
            {
                "ok": True,
                "data": {
                    "consumer_id": "owner-ack",
                    "generation": "generation-1",
                    "acked_sequence": 9,
                },
            },
            invoke,
        )

        self.assertTrue((bootstrap_result or {})["passed"])
        self.assertEqual(
            "exact_change_feed_bootstrap_checkpoint", (bootstrap_result or {})["check"]
        )
        self.assertTrue((ack_result or {})["passed"])
        self.assertEqual("exact_change_feed_ack_checkpoint", (ack_result or {})["check"])

    async def test_cashbox_transfer_has_exact_pair_readback(self) -> None:
        source_transaction = {
            "id": "transaction-out",
            "cashbox_id": "cashbox-source",
            "direction": "expense",
            "amount_minor": 100,
            "transfer_group_id": "transfer-1",
            "related_transaction_id": "transaction-in",
        }
        target_transaction = {
            "id": "transaction-in",
            "cashbox_id": "cashbox-target",
            "direction": "income",
            "amount_minor": 100,
            "transfer_group_id": "transfer-1",
            "related_transaction_id": "transaction-out",
        }

        async def invoke(_name: str, arguments: dict) -> dict:
            transaction = (
                source_transaction
                if arguments["cashbox_id"] == "cashbox-source"
                else target_transaction
            )
            return {
                "ok": True,
                "data": {
                    "cashbox": {"id": arguments["cashbox_id"]},
                    "transactions": [transaction],
                },
            }

        verification = await verify_virtual_api_write_readback(
            "create_cashbox_transfer",
            {
                "from_cashbox_id": "cashbox-source",
                "to_cashbox_id": "cashbox-target",
            },
            {
                "ok": True,
                "data": {
                    "source_transaction": source_transaction,
                    "target_transaction": target_transaction,
                },
            },
            invoke,
        )

        self.assertTrue((verification or {})["passed"])
        self.assertEqual(
            "exact_cashbox_transfer_pair_readback",
            (verification or {})["check"],
        )

    async def test_cashbox_reorder_has_exact_order_readback(self) -> None:
        expected_order = ["cashbox-3", "cashbox-1", "cashbox-2"]

        async def invoke(name: str, arguments: dict) -> dict:
            self.assertEqual(name, "list_cashboxes")
            self.assertGreaterEqual(arguments["limit"], 3)
            return {
                "ok": True,
                "data": {
                    "cashboxes": [
                        {"id": cashbox_id, "order": index}
                        for index, cashbox_id in enumerate(expected_order)
                    ]
                },
            }

        verification = await verify_virtual_api_write_readback(
            "reorder_cashboxes",
            {
                "cashbox_id": "cashbox-3",
                "before_cashbox_id": "cashbox-1",
                "expected_cashbox_ids": ["cashbox-1", "cashbox-2", "cashbox-3"],
            },
            {
                "ok": True,
                "data": {
                    "cashboxes": [
                        {"id": cashbox_id, "order": index}
                        for index, cashbox_id in enumerate(expected_order)
                    ]
                },
            },
            invoke,
        )

        self.assertTrue((verification or {})["passed"])
        self.assertEqual(
            "exact_cashbox_order_readback",
            (verification or {})["check"],
        )

    async def test_salary_transaction_has_exact_cashbox_employee_and_ledger_readback(
        self,
    ) -> None:
        transaction = {
            "id": "salary-transaction-1",
            "cashbox_id": "cashbox-1",
            "employee_id": "employee-1",
            "direction": "expense",
            "transaction_kind": "salary_payout",
            "amount_minor": 100,
        }

        async def invoke(name: str, arguments: dict) -> dict:
            if name == "get_cashbox":
                self.assertEqual(arguments["cashbox_id"], "cashbox-1")
                return {
                    "ok": True,
                    "data": {
                        "cashbox": {
                            "id": "cashbox-1",
                            "updated_at": "2026-07-28T20:00:01+00:00",
                        },
                        "transactions": [transaction],
                    },
                }
            if name == "api:/api/list_employees":
                return {
                    "ok": True,
                    "data": {
                        "employees": [
                            {
                                "id": "employee-1",
                                "name": "Synthetic",
                                "updated_at": "2026-07-28T20:00:00+00:00",
                            }
                        ]
                    },
                }
            self.assertEqual(name, "api:/api/get_employee_salary_ledger")
            return {
                "ok": True,
                "data": {
                    "journal_rows": [
                        {
                            "transaction_id": "salary-transaction-1",
                            "amount_minor": 100,
                        }
                    ]
                },
            }

        verification = await verify_virtual_api_write_readback(
            "create_employee_salary_transaction",
            {
                "employee_id": "employee-1",
                "cashbox_id": "cashbox-1",
                "amount_minor": 100,
                "expected_employee_updated_at": "2026-07-28T20:00:00+00:00",
                "expected_cashbox_updated_at": "2026-07-28T20:00:00+00:00",
            },
            {"ok": True, "data": {"transaction": transaction}},
            invoke,
        )

        self.assertTrue((verification or {})["passed"])
        self.assertEqual(
            "exact_salary_cashbox_employee_and_ledger_readback",
            (verification or {})["check"],
        )

    async def test_save_employee_has_exact_list_readback(self) -> None:
        employee = {
            "id": "employee-1",
            "name": "AST-GWAT-20260728T165722Z-employee",
            "updated_at": "2026-07-28T20:00:00+00:00",
        }

        async def invoke(name: str, arguments: dict) -> dict:
            self.assertEqual(name, "api:/api/list_employees")
            self.assertEqual(arguments, {})
            return {"ok": True, "data": {"employees": [employee]}}

        verification = await verify_virtual_api_write_readback(
            "api:/api/save_employee",
            {"name": employee["name"]},
            {"ok": True, "data": {"employee": employee}},
            invoke,
        )

        self.assertTrue((verification or {})["passed"])
        self.assertEqual("exact_employee_list_readback", (verification or {})["check"])

    async def test_shift_accrual_has_exact_employee_and_ledger_readback(self) -> None:
        accrual = {
            "id": "shift-accrual-1",
            "employee_id": "employee-1",
            "amount_minor": 100,
        }

        async def invoke(name: str, _arguments: dict) -> dict:
            if name == "api:/api/list_employees":
                return {
                    "ok": True,
                    "data": {
                        "employees": [
                            {
                                "id": "employee-1",
                                "name": "Synthetic",
                                "updated_at": "2026-07-28T20:00:00+00:00",
                            }
                        ]
                    },
                }
            self.assertEqual(name, "api:/api/get_employee_salary_ledger")
            return {
                "ok": True,
                "data": {
                    "journal_rows": [
                        {
                            "kind": "shift_accrual",
                            "accrual_id": "shift-accrual-1",
                            "amount_minor": 100,
                        }
                    ]
                },
            }

        verification = await verify_virtual_api_write_readback(
            "create_employee_shift_accrual",
            {
                "employee_id": "employee-1",
                "amount_minor": 100,
                "expected_employee_updated_at": "2026-07-28T20:00:00+00:00",
            },
            {"ok": True, "data": {"accrual": accrual}},
            invoke,
        )

        self.assertTrue((verification or {})["passed"])
        self.assertEqual(
            "exact_shift_accrual_employee_and_ledger_readback",
            (verification or {})["check"],
        )

    async def test_cash_cancellation_has_exact_reversal_readback(self) -> None:
        cancelled = {
            "id": "transaction-1",
            "cashbox_id": "cashbox-1",
            "direction": "expense",
            "amount_minor": 100,
            "transaction_kind": "cashbox_cancelled",
        }
        cancellation = {
            "id": "cancellation-1",
            "cashbox_id": "cashbox-1",
            "direction": "income",
            "amount_minor": 100,
            "transaction_kind": "cashbox_cancellation",
            "related_transaction_id": "transaction-1",
        }

        async def invoke(name: str, arguments: dict) -> dict:
            self.assertEqual(name, "get_cashbox")
            self.assertEqual(arguments["cashbox_id"], "cashbox-1")
            return {
                "ok": True,
                "data": {
                    "cashbox": {
                        "id": "cashbox-1",
                        "updated_at": "2026-07-28T20:00:01+00:00",
                    },
                    "transactions": [cancelled, cancellation],
                },
            }

        verification = await verify_virtual_api_write_readback(
            "cancel_cash_transaction",
            {
                "cashbox_id": "cashbox-1",
                "transaction_id": "transaction-1",
                "expected_cashbox_updated_at": "2026-07-28T20:00:00+00:00",
            },
            {
                "ok": True,
                "data": {
                    "cancelled_transaction": cancelled,
                    "cancellation_transaction": cancellation,
                    "meta": {"repair_order_card_id": None},
                },
            },
            invoke,
        )

        self.assertTrue((verification or {})["passed"])
        self.assertEqual(
            "exact_cash_cancellation_and_optional_payment_readback",
            (verification or {})["check"],
        )

    async def test_cancel_last_cash_transaction_requires_exact_absence_readback(self) -> None:
        cancelled = {
            "id": "transaction-1",
            "cashbox_id": "cashbox-1",
            "direction": "income",
            "amount_minor": 100,
        }

        async def invoke(name: str, arguments: dict) -> dict:
            self.assertEqual(name, "get_cashbox")
            self.assertEqual(arguments["cashbox_id"], "cashbox-1")
            return {
                "ok": True,
                "data": {
                    "cashbox": {
                        "id": "cashbox-1",
                        "updated_at": "2026-07-28T20:00:01+00:00",
                    },
                    "transactions": [],
                },
            }

        verification = await verify_virtual_api_write_readback(
            "cancel_last_cash_transaction",
            {
                "cashbox_id": "cashbox-1",
                "transaction_id": "transaction-1",
                "expected_cashbox_updated_at": "2026-07-28T20:00:00+00:00",
            },
            {
                "ok": True,
                "data": {
                    "cancelled_transaction": cancelled,
                    "meta": {"repair_order_card_id": None},
                },
            },
            invoke,
        )

        self.assertTrue((verification or {})["passed"])
        self.assertEqual(
            "exact_cancelled_last_transaction_absence_readback",
            (verification or {})["check"],
        )

    def test_parity_manifest_has_exact_guarded_coverage_and_zero_new_gaps(self) -> None:
        inventory = crm_capability_parity.build_inventory()
        rows = {row["route"]: row for row in inventory["matrix"]}
        expected = {
            CHANGE_FEED_BOOTSTRAP_ROUTE: ("write", "exact_change_feed_bootstrap_checkpoint"),
            CHANGE_FEED_READ_ROUTE: ("read", "replay_safe_ordered_change_feed_page"),
            CHANGE_FEED_ACK_ROUTE: ("write", "exact_change_feed_ack_checkpoint"),
        }
        for route, (risk, readback_class) in expected.items():
            with self.subTest(route=route):
                self.assertEqual("covered", rows[route]["status"])
                self.assertEqual(risk, rows[route]["risk"])
                self.assertEqual(readback_class, rows[route]["readback_class"])
                self.assertEqual(
                    {
                        "kind": "guarded_virtual_api",
                        "gateway_tool": "call_raw_capability",
                        "operation": f"api:{route}",
                    },
                    rows[route]["reachability"]["selected"],
                )
        change_feed_gaps = [
            route for route in inventory["gaps"] if route.startswith("/api/change_feed/")
        ]
        self.assertEqual([], change_feed_gaps)


if __name__ == "__main__":
    unittest.main()
