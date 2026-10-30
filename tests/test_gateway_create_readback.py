from __future__ import annotations

import copy
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.mcp.client import BoardApiClient
from minimal_kanban.mcp.server import create_mcp_server
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore
from tests.test_agent_gateway_v2 import GATEWAY_ENV, register_fake_store_manager_tools


class GatewayCreateReadbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        env = patch.dict("os.environ", {**GATEWAY_ENV, "APPDATA": folder.name}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        network = patch("socket.socket.connect", side_effect=AssertionError("network prohibited"))
        network.start()
        self.addCleanup(network.stop)
        logger = logging.getLogger(self.id())
        self.service = CardService(
            JsonStore(state_file=base / "state.json", logger=logger),
            logger,
            attachments_dir=base / "attachments",
            repair_orders_dir=base / "orders",
        )
        self.state: dict = {}
        self.requests: list[str] = []
        self.tamper_route = ""
        self.tamper_client_field = "first_name"
        self.readback_error = False
        self.omit_created_client_field = ""
        board = BoardApiClient("https://crm.example", bearer_token="synthetic")
        request = patch.object(board, "_request", side_effect=self.request)
        request.start()
        self.addCleanup(request.stop)
        with patch(
            "minimal_kanban.mcp.server._try_register_autostop_manager_tools",
            side_effect=lambda server, logger: register_fake_store_manager_tools(
                server, logger, self.state
            ),
        ):
            self.server = create_mcp_server(
                board,
                logger,
                host="127.0.0.1",
                port=41844,
                path="/mcp",
                public_endpoint_url="https://crm.example/mcp",
            )

    def request(self, route: str, payload: dict | None = None, **_: object) -> dict:
        parsed = urlsplit(route)
        self.assertIn(
            parsed.path,
            {"/api/create_card", "/api/get_card", "/api/create_client", "/api/get_client"},
        )
        self.requests.append(parsed.path)
        if parsed.path == "/api/get_client" and self.readback_error:
            return {"ok": False, "error": {"code": "synthetic_readback_unreachable"}}
        arguments = dict(payload or parse_qsl(parsed.query))
        # Mirror the HTTP query parser before invoking the real domain service.
        if "strict_client_links" in arguments:
            arguments["strict_client_links"] = arguments["strict_client_links"] == "true"
        if "order_limit" in arguments:
            arguments["order_limit"] = int(arguments["order_limit"])
        operation = parsed.path.rsplit("/", 1)[1]
        data = copy.deepcopy(getattr(self.service, operation)(arguments))
        if parsed.path == self.tamper_route:
            if "card" in data:
                data["card"]["title"] = "Different persisted card"
            else:
                data["client"][self.tamper_client_field] = "Different persisted client"
        if parsed.path == "/api/create_client" and self.omit_created_client_field:
            data["client"].pop(self.omit_created_client_field)
        return {"ok": True, "data": data}

    async def call(self, name: str, arguments: dict, key: str) -> dict:
        schema = await self.server._tool_manager.get_tool("get_raw_capability_schema").run(
            {"name": name}, convert_result=False
        )
        result = await self.server._tool_manager.get_tool("call_raw_capability").run(
            {
                "name": name,
                "arguments": arguments,
                "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                "idempotency_key": key,
            },
            convert_result=False,
        )
        return result.structuredContent

    async def test_card_whitespace_and_line_endings_match_real_service_for_both_names(self):
        arguments = {
            "title": "  Synthetic card  ",
            "vehicle": "  Synthetic   vehicle  ",
            "description": "Line one\r\nLine two\rLine three",
        }
        for name in ("create_card", "api:/api/create_card"):
            with self.subTest(name=name):
                result = await self.call(name, arguments, name)
                self.assertTrue(result["ok"], result)
                self.assertEqual("completed", result["status"])
                evidence = result["verification"]["evidence"]
                self.assertTrue(evidence["requested_fields_exact"])
                self.assertTrue(evidence["persisted_state_exact"])
                replay = await self.call(name, arguments, name)
                self.assertTrue(replay["ok"])
                self.assertTrue(replay["summary"]["deduplicated"])
        self.assertEqual(2, self.requests.count("/api/create_card"))

    async def test_empty_card_vehicle_uses_profile_display(self):
        result = await self.call(
            "create_card",
            {
                "title": "Synthetic",
                "vehicle": " ",
                "vehicle_profile": {"brand": "Volvo", "model": "XC90"},
            },
            "profile-display",
        )
        self.assertTrue(result["ok"], result)

    async def test_client_normalization_uses_domain_model_including_nested_vehicle(self):
        result = await self.call(
            "create_client",
            {
                "client": {
                    "first_name": "  Synthetic  ",
                    "last_name": "  Client  ",
                    "email": " User@Example.COM ",
                    "emails": [" User@Example.COM ", " other@example.com "],
                    "phones": [" +7 (999) 123-45-67 ", " +7 (999) 123-45-67 "],
                    "vehicles": [
                        {"brand": "  Volvo ", "model": " XC90 ", "vin": "yv1synthetic123456"},
                        {"id": " synthetic-vehicle ", "brand": " Volvo ", "model": " V70 "},
                    ],
                }
            },
            "client-normalization",
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["verification"]["evidence"]["requested_fields_exact"])
        self.assertTrue(result["verification"]["evidence"]["persisted_state_exact"])

    async def test_card_and_client_still_reject_mutation_response_or_readback_mismatch(self):
        cases = (
            ("create_card", {"title": " Synthetic "}, "card"),
            (
                "create_client",
                {"client": {"first_name": " Synthetic ", "last_name": "Client"}},
                "client",
            ),
        )
        for name, arguments, entity in cases:
            for phase in ("create", "get"):
                with self.subTest(name=name, phase=phase):
                    self.tamper_route = f"/api/{phase}_{entity}"
                    result = await self.call(name, arguments, f"mismatch-{name}-{phase}")
                    self.assertFalse(result["ok"], result)
                    self.assertEqual("compensating", result["status"])
                    self.assertFalse(result["verification"]["evidence"]["persisted_state_exact"])
                    self.assertTrue(result["verification"]["evidence"]["readback_ok"])

    async def test_invalid_client_payload_keeps_structured_executor_rejection(self):
        result = await self.call(
            "create_client",
            {"client": {"first_name": "Synthetic", "phones": 123}},
            "invalid-client",
        )
        self.assertFalse(result["ok"])
        self.assertEqual("failed", result["status"])
        self.assertEqual([], self.requests)

    async def test_consistent_but_wrong_persisted_values_still_fail_requested_field_check(self):
        cases = (
            ("create_card", {"title": " Synthetic "}, "title"),
            ("create_client", {"client": {"first_name": " Synthetic "}}, "first_name"),
        )
        for name, arguments, field in cases:
            with self.subTest(name=name):
                original = getattr(self.service, name)
                with patch.object(
                    self.service,
                    name,
                    side_effect=lambda payload: original({**payload, field: "Wrong value"}),
                ):
                    result = await self.call(name, arguments, f"wrong-{name}")
                self.assertFalse(result["ok"])
                self.assertEqual("compensating", result["status"])
                evidence = result["verification"]["evidence"]
                self.assertFalse(evidence["requested_fields_exact"])
                self.assertTrue(evidence["persisted_state_exact"])

    async def test_client_api_alias_reads_flat_and_nested_legacy_payloads(self):
        payloads = (
            {"first_name": " Synthetic ", "last_name": " Alias "},
            {
                "client_id": " synthetic-company ",
                "client": {
                    "type": "company",
                    "legal_name": " Synthetic Company ",
                    "account": " synthetic-account ",
                    "bank": " synthetic-bank ",
                    "note": " synthetic-note ",
                    "vehicles": [{"brand": " Volvo ", "model": " XC90 "}],
                },
            },
        )
        for index, arguments in enumerate(payloads):
            with self.subTest(index=index):
                result = await self.call("api:/api/create_client", arguments, f"alias-{index}")
                self.assertTrue(result["ok"], result)
                self.assertEqual("completed", result["status"])
                verification = result["verification"]
                self.assertEqual("exact_created_client_state_readback", verification["check"])
                self.assertTrue(verification["evidence"]["complete_persisted_state"])
                self.assertTrue(verification["evidence"]["persisted_state_exact"])
                if index:
                    self.assertEqual("synthetic-company", verification["evidence"]["client_id"])
                self.assertEqual(["/api/create_client", "/api/get_client"], self.requests[-2:])

    async def test_client_api_alias_duplicate_reads_existing_profile(self):
        arguments = {
            "first_name": "Synthetic",
            "last_name": "Duplicate",
            "phone": "+7 (999) 123-45-67",
        }
        first = await self.call("api:/api/create_client", arguments, "first-client")
        second = await self.call("api:/api/create_client", arguments, "duplicate-client")
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual(
            first["verification"]["evidence"]["client_id"],
            second["verification"]["evidence"]["client_id"],
        )
        self.assertTrue(second["data"]["data"]["meta"]["duplicate"])
        self.assertEqual(2, self.requests.count("/api/get_client"))

    async def test_client_api_alias_read_error_never_confirms_persistence(self):
        self.readback_error = True
        result = await self.call(
            "api:/api/create_client", {"first_name": "Synthetic"}, "alias-read-error"
        )
        self.assertFalse(result["ok"])
        self.assertEqual("compensating", result["status"])
        self.assertFalse(result["verification"]["evidence"]["readback_ok"])
        self.assertFalse(result["verification"]["passed"])

    async def test_client_api_alias_rejects_bank_and_vehicle_readback_mismatches(self):
        self.tamper_route = "/api/get_client"
        for field in ("checking_account", "vehicles", "id"):
            with self.subTest(field=field):
                self.tamper_client_field = field
                result = await self.call(
                    "api:/api/create_client",
                    {
                        "first_name": "Synthetic",
                        "checking_account": "synthetic-account",
                        "vehicles": [{"brand": "Volvo", "model": "XC90"}],
                    },
                    f"alias-mismatch-{field}",
                )
                self.assertFalse(result["ok"], result)
                self.assertEqual("compensating", result["status"])
                evidence = result["verification"]["evidence"]
                self.assertTrue(evidence["readback_ok"])
                self.assertFalse(evidence["persisted_state_exact"])

    async def test_client_api_alias_incomplete_mutation_response_never_confirms_persistence(self):
        self.omit_created_client_field = "checking_account"
        result = await self.call(
            "api:/api/create_client", {"first_name": "Synthetic"}, "alias-incomplete"
        )
        self.assertFalse(result["ok"])
        self.assertEqual("compensating", result["status"])
        evidence = result["verification"]["evidence"]
        self.assertTrue(evidence["readback_ok"])
        self.assertFalse(evidence["complete_persisted_state"])


if __name__ == "__main__":
    unittest.main()
