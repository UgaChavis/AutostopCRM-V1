from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.types import CallToolRequest, CallToolRequestParams

from tests.services_case import CardServiceCase
from minimal_kanban.mcp.oauth_provider import OwnerAccessToken
from minimal_kanban.mcp.server import create_mcp_server
from minimal_kanban.repair_order import RepairOrder
from minimal_kanban.services.errors import ServiceError
from tests.test_agent_gateway_v2 import FakeBoardApi, GATEWAY_ENV


class RepairOrderGatewayFixture(CardServiceCase, IsolatedAsyncioTestCase):
    def setUp(self):
        self.appdata_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.appdata_dir.cleanup)
        appdata = patch.dict("os.environ", {"APPDATA": self.appdata_dir.name})
        appdata.start()
        self.addCleanup(appdata.stop)
        super().setUp()
        self.service = self._build_service()
        card = self.service.create_card(
            {
                "title": "PRIVATE_TITLE_SENTINEL",
                "vehicle": "PRIVATE_VEHICLE_SENTINEL",
                "description": "PRIVATE_DESCRIPTION_SENTINEL",
            }
        )["card"]
        self.card_id = card["id"]
        bundle = self.store.read_bundle()
        stored = next(item for item in bundle["cards"] if item.id == self.card_id)
        stored.repair_order = RepairOrder.from_dict(
            {
                "number": "",
                "client": "PRIVATE_CLIENT_SENTINEL",
                "phone": "+79991112233",
                "vin": "TESTVIN1234567890",
                "license_plate": "A123BC24",
                "vehicle": "PRIVATE_ORDER_VEHICLE_SENTINEL",
                "reason": "PRIVATE_REASON_SENTINEL",
                "comment": "PRIVATE_COMMENT_SENTINEL",
                "note": "PRIVATE_NOTE_SENTINEL",
                "works": [{"name": "PRIVATE_WORK_SENTINEL", "quantity": "1", "price": "10"}],
            }
        )
        self.store.write_bundle(**bundle)
        self.orders = Path(self.temp_dir.name) / "repair-orders"
        self.orders.mkdir(exist_ok=True)
        (self.orders / "orphan__C-ORPHAN.txt").write_text("orphan", encoding="utf-8")
        self.calls = []
        service, calls = self.service, self.calls

        class ServiceBoardApi(FakeBoardApi):
            def list_repair_orders(self, **kwargs):
                payload = {key: value for key, value in kwargs.items() if value is not None}
                calls.append(payload)
                try:
                    result = service.list_repair_orders(payload)
                except ServiceError as exc:
                    return {"ok": False, "error": {"code": exc.code, "message": "synthetic error"}}
                except OSError:
                    return {"ok": False, "error": {"code": "synthetic_backend_failure"}}
                return {"ok": True, "data": result}

        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.manager = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        self.manager.start()
        self.addCleanup(self.manager.stop)
        self.board_api = ServiceBoardApi()
        self.server = create_mcp_server(
            self.board_api,
            self.logger,
            host="127.0.0.1",
            port=41838,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )

    def snapshot(self):
        snapshots = {}
        for name, root in (
            ("service", Path(self.temp_dir.name)),
            ("appdata", Path(self.appdata_dir.name)),
        ):
            for path in root.rglob("*"):
                relative = (name, str(path.relative_to(root)))
                if path.is_file():
                    stat = path.stat()
                    snapshots[relative] = (
                        stat.st_ino,
                        stat.st_mode,
                        stat.st_mtime_ns,
                        stat.st_ctime_ns,
                        path.read_bytes(),
                    )
                elif path.is_dir():
                    snapshots[relative] = "directory"
        return snapshots

    def set_existing_number(self):
        bundle = self.store.read_bundle()
        card = next(item for item in bundle["cards"] if item.id == self.card_id)
        card.repair_order.number = "302"
        self.store.write_bundle(**bundle)

    async def call(self, tool, payload):
        token = OwnerAccessToken(
            token="synthetic",
            client_id="test-client",
            subject="CODEX",
            family_id="test-family",
            scopes=["kanban:read", "kanban:write"],
            resource="https://crm.example/mcp",
        )
        context = auth_context_var.set(AuthenticatedUser(token))
        try:
            return await self.server._tool_manager.get_tool(tool).run(payload, convert_result=False)
        finally:
            auth_context_var.reset(context)

    async def wire_call(self, server, name, arguments):
        request = CallToolRequest(params=CallToolRequestParams(name=name, arguments=arguments))
        token = OwnerAccessToken(
            token="synthetic",
            client_id="test-client",
            subject="CODEX",
            family_id="test-family",
            scopes=["kanban:read", "kanban:write"],
            resource="https://crm.example/mcp",
        )
        context = auth_context_var.set(AuthenticatedUser(token))
        try:
            result = await server._mcp_server.request_handlers[CallToolRequest](request)
            return result.root
        finally:
            auth_context_var.reset(context)

    def assert_no_private_values(self, result):
        text = json.dumps(result, ensure_ascii=False)
        for value in ("PRIVATE_", "+79991112233", "TESTVIN1234567890", "A123BC24"):
            self.assertNotIn(value, text)

    async def call_list(self, arguments):
        schema = await self.call("get_raw_capability_schema", {"name": "list_repair_orders"})
        self.assertTrue(schema.structuredContent["ok"], schema.structuredContent)
        return await self.call(
            "call_raw_capability",
            {
                "name": "list_repair_orders",
                "arguments": arguments,
                "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                "allow_large_output": True,
            },
        )


class RepairOrderGatewayReadSafetyTests(RepairOrderGatewayFixture):
    async def test_raw_full_and_compact_redacted_reads_are_private_and_non_mutating(self):
        before = self.snapshot()
        for compact in (False, True):
            for _ in range(2):
                with self.subTest(compact=compact):
                    result = await self.call_list(
                        {"status": "all", "compact": compact, "redact_private": True}
                    )
                    data = result.structuredContent
                    self.assertTrue(data["ok"], data)
                    items = data["data"]["data"]["repair_orders"]
                    self.assertEqual(len(items), 1)
                    self.assertEqual(items[0]["card_id"], self.card_id)
                    self.assertEqual(items[0]["number"], "")
                    self.assert_no_private_values(data)
                    self.assertEqual(before, self.snapshot())

    async def test_gateway_search_reads_real_service_with_redaction_and_no_effects(self):
        before = self.snapshot()
        for _ in range(2):
            result = await self.call(
                "agent_search",
                {"entity": "repair_order", "include_archived": True, "limit": 10},
            )
            data = result.structuredContent
            self.assertTrue(data["ok"], data)
            items = data["data"]["items"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["card_id"], self.card_id)
            self.assertEqual(items[0]["number"], "")
            self.assertTrue(self.calls[-1]["compact"])
            self.assertTrue(self.calls[-1]["redact_private"])
            self.assert_no_private_values(data)
            self.assertEqual(before, self.snapshot())

    async def test_full_and_compact_redaction_hide_private_values_with_existing_number(self):
        self.set_existing_number()
        before = self.snapshot()
        for compact in (False, True):
            with self.subTest(compact=compact):
                result = await self.call_list(
                    {"status": "all", "compact": compact, "redact_private": True}
                )
                data = result.structuredContent
                self.assertTrue(data["ok"], data)
                self.assertEqual(data["data"]["data"]["repair_orders"][0]["number"], "302")
                self.assert_no_private_values(data)
                self.assertEqual(before, self.snapshot())

    async def test_private_search_query_is_not_echoed_in_redacted_responses(self):
        self.set_existing_number()
        before = self.snapshot()
        sentinel = "PRIVATE_CLIENT_SENTINEL"
        for compact in (False, True):
            with self.subTest(route="raw", compact=compact):
                result = await self.call_list(
                    {
                        "status": "all",
                        "query": sentinel,
                        "compact": compact,
                        "redact_private": True,
                    }
                )
                self.assertTrue(result.structuredContent["ok"], result.structuredContent)
                self.assertEqual(
                    result.structuredContent["data"]["data"]["repair_orders"][0]["number"],
                    "302",
                )
                self.assert_no_private_values(result.structuredContent)
                self.assertEqual(before, self.snapshot())
        for _ in range(2):
            result = await self.call(
                "agent_search",
                {"entity": "repair_order", "query": sentinel, "include_archived": True},
            )
            self.assertTrue(result.structuredContent["ok"], result.structuredContent)
            self.assertEqual(result.structuredContent["data"]["items"][0]["number"], "302")
            self.assert_no_private_values(result.structuredContent)
            self.assertEqual(before, self.snapshot())

    async def test_exact_number_filter_matches_only_requested_order_without_mutation(self):
        self.set_existing_number()
        before = self.snapshot()
        for number, expected in (("302", [self.card_id]), ("9999", [])):
            with self.subTest(number=number):
                raw = await self.call_list(
                    {"status": "all", "number": number, "redact_private": True}
                )
                self.assertTrue(raw.structuredContent["ok"], raw.structuredContent)
                raw_items = raw.structuredContent["data"]["data"]["repair_orders"]
                self.assertEqual([item["card_id"] for item in raw_items], expected)
                search = await self.call(
                    "agent_search",
                    {"entity": "repair_order", "filters": {"number": number, "status": "all"}},
                )
                self.assertTrue(search.structuredContent["ok"], search.structuredContent)
                self.assertEqual(
                    [item["card_id"] for item in search.structuredContent["data"]["items"]],
                    expected,
                )
                self.assertEqual(before, self.snapshot())

    async def test_full_unredacted_response_keeps_legacy_data_shape_without_write(self):
        before = self.snapshot()
        result = await self.call_list({"status": "all", "compact": False, "redact_private": False})
        self.assertTrue(result.structuredContent["ok"], result.structuredContent)
        item = result.structuredContent["data"]["data"]["repair_orders"][0]
        self.assertEqual(item["client"], "PRIVATE_CLIENT_SENTINEL")
        self.assertEqual(item["phone"], "+79991112233")
        self.assertEqual(item["vin"], "TESTVIN1234567890")
        self.assertIn("file_name", item)
        self.assertEqual(before, self.snapshot())

    async def test_invalid_search_and_backend_failure_do_not_mutate(self):
        before = self.snapshot()
        invalid = await self.call(
            "agent_search", {"entity": "repair_order", "filters": {"bad_filter": "x"}}
        )
        self.assertFalse(invalid.structuredContent["ok"])
        self.assertIn("unsupported_search_filters", invalid.structuredContent["warnings"])
        self.assertEqual([], self.calls)
        self.assertEqual(before, self.snapshot())
        with patch.object(
            self.service, "list_repair_orders", side_effect=OSError("synthetic read failure")
        ):
            failed = await self.call("agent_search", {"entity": "repair_order"})
        self.assertFalse(failed.structuredContent["ok"])
        self.assertEqual(before, self.snapshot())

    async def test_invalid_status_does_not_echo_private_values_for_redacted_raw_read(self):
        before = self.snapshot()
        sentinel = "PRIVATE_STATUS_SENTINEL"
        for redact in (True, False):
            with self.subTest(redact_private=redact):
                result = await self.call_list({"status": sentinel, "redact_private": redact})
                self.assertFalse(result.structuredContent["ok"])
                if redact:
                    self.assert_not_in_response(sentinel, result)
                else:
                    self.assertIn(
                        sentinel, json.dumps(result.structuredContent, ensure_ascii=False)
                    )
                self.assertEqual(before, self.snapshot())

    def assert_not_in_response(self, sentinel, result):
        self.assertNotIn(sentinel, json.dumps(result.structuredContent, ensure_ascii=False))

    async def test_coerced_redaction_flags_cannot_leak_invalid_argument(self):
        before = self.snapshot()
        sentinel = "PRIVATE_COERCED_STATUS"
        for flag in ("true", 1):
            with self.subTest(redact_private=flag):
                result = await self.call_list(
                    {"status": "all", "query": sentinel, "redact_private": flag}
                )
                self.assertFalse(result.structuredContent["ok"])
                self.assert_not_in_response(sentinel, result)
                self.assertEqual([], self.calls)
                self.assertEqual(before, self.snapshot())

    async def test_unsupported_private_filter_name_is_not_echoed_by_gateway(self):
        before = self.snapshot()
        sentinel = "PRIVATE_FILTER_SENTINEL"
        result = await self.call(
            "agent_search", {"entity": "repair_order", "filters": {sentinel: "x"}}
        )
        self.assertFalse(result.structuredContent["ok"])
        self.assertIn("unsupported_search_filters", result.structuredContent["warnings"])
        self.assert_not_in_response(sentinel, result)
        self.assertEqual(before, self.snapshot())

    async def test_backend_errors_do_not_leak_in_redacted_raw_or_gateway(self):
        before = self.snapshot()
        sentinel = "PRIVATE_BACKEND_SENTINEL"
        for code in ("synthetic_backend_failure", sentinel):
            failure = {
                "ok": False,
                "error": {"code": code, "message": sentinel, "details": {"message": sentinel}},
            }
            with patch.object(self.board_api, "list_repair_orders", return_value=failure):
                for route in ("raw", "gateway", "unredacted"):
                    with self.subTest(error_code=code, route=route):
                        if route == "gateway":
                            result = await self.call("agent_search", {"entity": "repair_order"})
                        else:
                            result = await self.call_list(
                                {"status": "all", "redact_private": route == "raw"}
                            )
                        self.assertFalse(result.structuredContent["ok"])
                        if route == "unredacted":
                            self.assertIn(
                                sentinel, json.dumps(result.structuredContent, ensure_ascii=False)
                            )
                        else:
                            self.assert_not_in_response(sentinel, result)
        self.assertEqual(before, self.snapshot())

    async def test_successful_backend_metadata_does_not_leak_in_redacted_responses(self):
        before = self.snapshot()
        sentinel = "PRIVATE_BACKEND_META_SENTINEL"
        response = {
            "ok": True,
            "data": {
                "repair_orders": [{"card_id": "synthetic", "number": "302", "status": "open"}],
                "meta": {"query": sentinel, "directory": sentinel},
            },
            "meta": {"applied_params": {"query": sentinel}},
        }
        with patch.object(self.board_api, "list_repair_orders", return_value=response):
            for route in ("raw", "gateway", "unredacted"):
                with self.subTest(route=route):
                    if route == "gateway":
                        result = await self.call("agent_search", {"entity": "repair_order"})
                    else:
                        result = await self.call_list(
                            {"status": "all", "redact_private": route == "raw"}
                        )
                    self.assertTrue(result.structuredContent["ok"], result.structuredContent)
                    if route == "unredacted":
                        self.assertIn(
                            sentinel, json.dumps(result.structuredContent, ensure_ascii=False)
                        )
                    else:
                        self.assert_not_in_response(sentinel, result)
                    self.assertEqual(before, self.snapshot())

    async def test_old_backend_private_rows_are_projected_at_gateway_boundary(self):
        before = self.snapshot()
        sentinel = "PRIVATE_LEGACY_BACKEND_SENTINEL"
        old_backend = {
            "ok": True,
            "data": {
                "repair_orders": [
                    {
                        "card_id": self.card_id,
                        "number": "302",
                        "status": "open",
                        "status_label": sentinel,
                        "short_id": sentinel,
                        "date": sentinel,
                        "created_at": sentinel,
                        "opened_at": sentinel,
                        "closed_at": sentinel,
                        "updated_at": sentinel,
                        "heading": sentinel,
                        "vehicle": sentinel,
                        "file_name": sentinel,
                        "file_path": sentinel,
                        "client": sentinel,
                        "phone": sentinel,
                        "vin": sentinel,
                        "license_plate": sentinel,
                        "reason": sentinel,
                        "summary": sentinel,
                        "payment_method": sentinel,
                        "payment_method_label": sentinel,
                        "payment_status": sentinel,
                        "payment_status_label": sentinel,
                        "prepayment": sentinel,
                        "paid_total": sentinel,
                        "grand_total": sentinel,
                        "due_total": sentinel,
                        "client_present": sentinel,
                        "phone_present": sentinel,
                        "vin_present": sentinel,
                        "license_plate_present": sentinel,
                        "payments": [{"note": sentinel}],
                        "tags": [{"label": sentinel}],
                        "unexpected_field": {"nested": sentinel},
                    }
                ]
            },
        }
        with patch.object(self.board_api, "list_repair_orders", return_value=old_backend):
            for compact in (False, True):
                with self.subTest(route="raw", compact=compact):
                    result = await self.call_list(
                        {"status": "all", "compact": compact, "redact_private": True}
                    )
                    self.assertTrue(result.structuredContent["ok"], result.structuredContent)
                    row = result.structuredContent["data"]["data"]["repair_orders"][0]
                    self.assertEqual(row["number"], "302")
                    self.assert_not_in_response(sentinel, result)
                    self.assertEqual(before, self.snapshot())
            with self.subTest(route="gateway"):
                result = await self.call("agent_search", {"entity": "repair_order"})
                self.assertTrue(result.structuredContent["ok"], result.structuredContent)
                self.assertEqual(result.structuredContent["data"]["items"][0]["number"], "302")
                self.assert_not_in_response(sentinel, result)
                self.assertEqual(before, self.snapshot())
            with self.subTest(route="unredacted"):
                result = await self.call_list({"status": "all", "redact_private": False})
                self.assertTrue(result.structuredContent["ok"], result.structuredContent)
                self.assertIn(sentinel, json.dumps(result.structuredContent, ensure_ascii=False))
                self.assertEqual(before, self.snapshot())

    async def test_inconsistent_success_with_private_error_fails_closed_in_both_modes(self):
        before = self.snapshot()
        sentinel = "PRIVATE_INCONSISTENT_ERROR_SENTINEL"
        for backend_data in ({"repair_orders": []}, None):
            with self.subTest(data_is_none=backend_data is None):
                response = {
                    "ok": True,
                    "data": backend_data,
                    "error": {"code": "validation_error", "message": sentinel},
                }
                with patch.object(self.board_api, "list_repair_orders", return_value=response):
                    for redact in (True, False):
                        with self.subTest(redact_private=redact):
                            result = await self.call_list(
                                {"status": "all", "redact_private": redact}
                            )
                            self.assertFalse(result.structuredContent["ok"])
                            self.assert_not_in_response(sentinel, result)
                            self.assertIsNone(result.structuredContent["data"]["data"])
                self.assertEqual(before, self.snapshot())

    async def test_gateway_fails_closed_for_inconsistent_or_missing_backend_data(self):
        before = self.snapshot()
        sentinel = "PRIVATE_GATEWAY_MALFORMED_SENTINEL"
        for backend_data, backend_error in (
            ({"repair_orders": []}, {"code": "validation_error", "message": sentinel}),
            (None, None),
        ):
            with self.subTest(data_is_none=backend_data is None):
                response = {"ok": True, "data": backend_data, "error": backend_error}
                with patch.object(self.board_api, "list_repair_orders", return_value=response):
                    result = await self.call("agent_search", {"entity": "repair_order"})
                self.assertFalse(result.structuredContent["ok"])
                self.assert_not_in_response(sentinel, result)
                self.assertEqual(before, self.snapshot())

    async def test_wire_validation_redacts_errors_for_direct_and_hidden_repair_order_calls(self):
        before = self.snapshot()
        sentinel = "PRIVATE_WIRE_SENTINEL"
        with patch.dict("os.environ", {"AUTOSTOP_AGENT_GATEWAY_ENABLED": "0"}):
            direct = create_mcp_server(
                self.board_api,
                self.logger,
                host="127.0.0.1",
                port=41838,
                path="/mcp",
                public_endpoint_url="https://crm.example/mcp",
            )
        direct_schema = direct._tool_manager.get_tool("list_repair_orders").parameters
        properties = direct_schema["properties"]
        self.assertEqual(properties["status"]["enum"], ["open", "ready", "closed", "all"])

        def enum_values(schema):
            return {
                value
                for option in schema.get("anyOf", [schema])
                for value in option.get("enum", [])
            }

        self.assertEqual(enum_values(properties["sort_by"]), {"number", "opened_at", "closed_at"})
        self.assertEqual(enum_values(properties["sort_dir"]), {"asc", "desc"})
        raw_schema = await self.call("get_raw_capability_schema", {"name": "list_repair_orders"})
        self.assertEqual(raw_schema.structuredContent["data"]["input_schema"], direct_schema)

        for field in ("status", "sort_by", "sort_dir", "compact"):
            for redact in (True, False):
                with self.subTest(field=field, redact_private=redact):
                    result = await self.wire_call(
                        direct, "list_repair_orders", {field: sentinel, "redact_private": redact}
                    )
                    self.assertTrue(result.isError)
                    serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
                    if redact:
                        self.assertNotIn(sentinel, serialized)
                    else:
                        self.assertIn(sentinel, serialized)
                    self.assertEqual(before, self.snapshot())

        for redact in (True, False):
            with self.subTest(route="hidden", redact_private=redact):
                result = await self.wire_call(
                    self.server,
                    "call_raw_capability",
                    {
                        "name": "list_repair_orders",
                        "arguments": {"redact_private": redact},
                        "schema_hash": {"bad_value": sentinel},
                    },
                )
                self.assertTrue(result.isError)
                serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
                if redact:
                    self.assertNotIn(sentinel, serialized)
                else:
                    self.assertIn(sentinel, serialized)
                self.assertEqual(before, self.snapshot())

        for entity in ("repair_order", "card"):
            with self.subTest(route="search", entity=entity):
                result = await self.wire_call(
                    self.server, "agent_search", {"entity": entity, "limit": sentinel}
                )
                self.assertTrue(result.isError)
                serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
                if entity == "repair_order":
                    self.assertNotIn(sentinel, serialized)
                else:
                    self.assertIn(sentinel, serialized)
                self.assertEqual(before, self.snapshot())

    async def test_wire_hidden_raw_rejects_invalid_nested_status_without_leaking(self):
        schema = await self.call("get_raw_capability_schema", {"name": "list_repair_orders"})
        schema_hash = schema.structuredContent["summary"]["schema_hash"]
        before = self.snapshot()
        sentinel = "PRIVATE_WIRE_NESTED_STATUS_SENTINEL"
        for redact in (True, False):
            with self.subTest(redact_private=redact):
                result = await self.wire_call(
                    self.server,
                    "call_raw_capability",
                    {
                        "name": "list_repair_orders",
                        "schema_hash": schema_hash,
                        "arguments": {"status": sentinel, "redact_private": redact},
                    },
                )
                self.assertTrue(result.isError)
                serialized = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
                if redact:
                    self.assertNotIn(sentinel, serialized)
                else:
                    self.assertIn(sentinel, serialized)
                self.assertEqual([], self.calls)
                self.assertEqual(before, self.snapshot())
