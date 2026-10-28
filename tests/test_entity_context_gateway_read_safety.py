from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from tests.services_case import CardServiceCase
from minimal_kanban.api.route_registry import build_service_routes
from minimal_kanban.mcp.client import BoardApiClient
from minimal_kanban.mcp.oauth_provider import OwnerAccessToken
from minimal_kanban.mcp.server import create_mcp_server
from minimal_kanban.repair_order import RepairOrder
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import StateFileCorruptedError
from tests.test_agent_gateway_v2 import FakeBoardApi, GATEWAY_ENV


class EntityContextGatewayReadSafetyTests(CardServiceCase, IsolatedAsyncioTestCase):
    def setUp(self):
        self.appdata = tempfile.TemporaryDirectory(prefix="synthetic-context-appdata-")
        self.addCleanup(self.appdata.cleanup)
        appdata = patch.dict("os.environ", {"APPDATA": self.appdata.name})
        appdata.start()
        self.addCleanup(appdata.stop)
        super().setUp()
        self.service = self._build_service()
        client = self.service.create_client(
            {"display_name": "SYNTHETIC_CLIENT", "vehicles": [{"vehicle": "SYNTHETIC_CAR"}]}
        )["client"]
        self.client_id = client["id"]
        self.vehicle_id = client["vehicles"][0]["id"]
        self.card_ids = []
        for index in range(6):
            card = self.service.create_card(
                {
                    "title": f"SYNTHETIC_ORDER_{index}",
                    "client_id": self.client_id,
                    "client_vehicle_id": self.vehicle_id,
                }
            )["card"]
            self.card_ids.append(card["id"])
        self.empty_card_id = self.service.create_card({"title": "SYNTHETIC_EMPTY"})["card"]["id"]
        bundle = self.store.read_bundle()
        for index, card_id in enumerate(self.card_ids):
            stored = next(item for item in bundle["cards"] if item.id == card_id)
            stored.repair_order = RepairOrder.from_dict(
                {
                    "number": str(301 + index),
                    "client": "SYNTHETIC_CLIENT",
                    "works": [{"name": "SYNTHETIC_WORK", "quantity": "1", "price": "100"}],
                }
            )
        self.store.write_bundle(**bundle)
        self.orders = Path(self.temp_dir.name) / "repair-orders"
        self.orders.mkdir(exist_ok=True)
        (self.orders / "unrelated.txt").write_text("SYNTHETIC_ORPHAN", encoding="utf-8")

        service = self.service
        self.calls = []
        calls = self.calls
        routes = build_service_routes(
            service, MagicMock(), paste_shared_files_from_clipboard=lambda _: {}
        )

        class ServiceBoardApi(FakeBoardApi):
            def relay(self, operation, payload):
                calls.append((operation, dict(payload)))
                try:
                    return {"ok": True, "data": getattr(service, operation)(payload)}
                except ServiceError as exc:
                    return {"ok": False, "error": {"code": exc.code, "message": "synthetic"}}
                except StateFileCorruptedError:
                    return {"ok": False, "error": {"code": "state_file_corrupted"}}

            def get_client(self, client_id, *, order_limit=None, strict_client_links=None):
                return self.relay(
                    "get_client",
                    {
                        "client_id": client_id,
                        "order_limit": order_limit,
                        "strict_client_links": strict_client_links,
                    },
                )

            def get_card(self, card_id):
                return self.relay("get_card", {"card_id": card_id})

            def get_card_context(
                self,
                card_id,
                *,
                event_limit,
                include_repair_order_text,
                read_only=None,
                strict_client_links=None,
            ):
                payload = {
                    "card_id": card_id,
                    "event_limit": event_limit,
                    "include_repair_order_text": include_repair_order_text,
                }
                if read_only is not None:
                    payload["read_only"] = read_only
                if strict_client_links is not None:
                    payload["strict_client_links"] = strict_client_links
                return self.relay(
                    "get_card_context",
                    payload,
                )

            def get_repair_order(self, card_id, *, create_if_missing=None):
                return BoardApiClient.get_repair_order(
                    self, card_id, create_if_missing=create_if_missing
                )

            def _request(self, path, payload):
                calls.append((path, dict(payload)))
                try:
                    return {"ok": True, "data": routes[path](payload)}
                except ServiceError as exc:
                    return {"ok": False, "error": {"code": exc.code, "message": "synthetic"}}
                except StateFileCorruptedError:
                    return {"ok": False, "error": {"code": "state_file_corrupted"}}

        env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        env.start()
        self.addCleanup(env.stop)
        manager = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        manager.start()
        self.addCleanup(manager.stop)
        self.board_api = ServiceBoardApi()
        self.server = create_mcp_server(
            self.board_api, self.logger, host="127.0.0.1", port=41838, path="/mcp"
        )

    def snapshot(self):
        result = {}
        for label, root in (
            ("state", Path(self.temp_dir.name)),
            ("appdata", Path(self.appdata.name)),
        ):
            for path in root.rglob("*"):
                stat = path.stat()
                key = (label, str(path.relative_to(root)))
                meta = (stat.st_ino, stat.st_mode, stat.st_mtime_ns, stat.st_ctime_ns)
                if path.is_file():
                    result[key] = ("file", meta, path.read_bytes())
                elif path.is_dir():
                    result[key] = ("directory", meta)
        return result

    def make_legacy(self):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        state["settings"] = "SYNTHETIC_LEGACY"
        state["columns"].append({"bad": "SYNTHETIC_LEGACY"})
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

    async def context(self, entity, entity_id, detail):
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
            return await self.server._tool_manager.get_tool("agent_entity_context").run(
                {"entity": entity, "entity_id": entity_id, "detail": detail},
                convert_result=False,
            )
        finally:
            auth_context_var.reset(context)

    async def test_summary_and_full_client_card_order_are_nonmutating_with_legacy_data(self):
        self.make_legacy()
        before = self.snapshot()
        for entity, entity_id in (
            ("client", self.client_id),
            ("card", self.card_ids[0]),
            ("repair_order", self.card_ids[0]),
            ("repair_order", self.empty_card_id),
        ):
            for detail in ("summary", "full"):
                with self.subTest(
                    entity=entity, detail=detail, empty=entity_id == self.empty_card_id
                ):
                    result = await self.context(entity, entity_id, detail)
                    payload = result.structuredContent
                    self.assertTrue(payload["ok"], payload)
                    key = "client" if entity == "client" else "card"
                    self.assertEqual(payload["data"][key]["id"], entity_id)
                    if entity == "client":
                        self.assertEqual(payload["data"]["meta"]["repair_orders_total"], 6)
                        self.assertEqual(
                            self.calls[-1][1]["order_limit"], 20 if detail == "full" else 5
                        )
                        if detail == "full":
                            self.assertEqual(len(payload["data"]["repair_orders"]), 6)
                    if entity == "card" and detail == "full":
                        self.assertFalse(self.calls[-1][1]["include_repair_order_text"])
                        self.assertIsNone(payload["data"]["repair_order_text"])
                    if entity == "repair_order":
                        self.assertFalse(self.calls[-1][1]["create_if_missing"])
                        if entity_id == self.empty_card_id:
                            self.assertFalse(payload["data"]["repair_order"]["number"])
                    self.assertTrue(before == self.snapshot(), "Read changed synthetic files")

    async def test_missing_entities_and_corrupt_state_fail_without_filesystem_effects(self):
        self.make_legacy()
        before = self.snapshot()
        for entity in ("client", "card", "repair_order"):
            for detail in ("summary", "full"):
                with self.subTest(entity=entity, detail=detail):
                    result = await self.context(entity, "missing-synthetic", detail)
                    self.assertFalse(result.structuredContent["ok"])
                    self.assertIn("not_found", result.structuredContent["warnings"])
                    self.assertTrue(
                        before == self.snapshot(), "Failed read changed synthetic files"
                    )
        self.state_file.write_bytes(b"{invalid json")
        corrupt = self.snapshot()
        for entity, entity_id in (
            ("client", self.client_id),
            ("card", self.card_ids[0]),
            ("repair_order", self.card_ids[0]),
        ):
            result = await self.context(entity, entity_id, "full")
            self.assertFalse(result.structuredContent["ok"])
            self.assertIn("state_file_corrupted", result.structuredContent["warnings"])
            self.assertTrue(corrupt == self.snapshot(), "Corrupt read changed synthetic files")

    async def test_gateway_preserves_legacy_warnings_in_summary_and_full(self):
        state = json.loads(self.state_file.read_text())
        card = next(item for item in state["cards"] if item["id"] == self.card_ids[0])
        card["deadline_timestamp"] = None
        card["repair_order"]["works"][0].pop("id", None)
        card["repair_order"]["payments"] = [{"amount": "7"}]
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        expected = {
            "deadline_calculated_from_created_at",
            "repair_order_row_identity_unverified",
            "repair_order_payment_identity_unverified",
        }
        for entity, entity_id in (("card", self.card_ids[0]), ("repair_order", self.card_ids[0])):
            for detail in ("summary", "full"):
                with self.subTest(entity=entity, detail=detail):
                    result = await self.context(entity, entity_id, detail)
                    self.assertTrue(result.structuredContent["ok"])
                    data = result.structuredContent["data"]
                    self.assertEqual(set(data["read_warnings"]), expected)
                    self.assertEqual(data["card"]["id"], self.card_ids[0])
                    self.assertNotIn("deadline_timestamp", data["card"])
                    self.assertNotIn("remaining_seconds", data["card"])
                    self.assertNotIn("status", data["card"])
                    if detail == "full":
                        self.assertNotIn("signal_timestamp", data["card"])
                        order = (
                            data["repair_order"]
                            if entity == "repair_order"
                            else data["card"]["repair_order"]
                        )
                        self.assertEqual(order["works"][0]["name"], "SYNTHETIC_WORK")
                        self.assertNotIn("id", order["works"][0])
                        self.assertNotIn("id", order["payments"][0])
                    self.assertEqual(before, self.snapshot())
        for detail in ("summary", "full"):
            result = await self.context("client", self.client_id, detail)
            self.assertTrue(result.structuredContent["ok"])
            data = result.structuredContent["data"]
            self.assertEqual(set(data["meta"]["read_warnings"]), expected)
            if detail == "full":
                for order in data["repair_orders"]:
                    for payment in order["payments"]:
                        self.assertNotIn("id", payment)
            self.assertEqual(before, self.snapshot())

    async def test_gateway_preserves_persisted_order_ids_and_deadline_without_warnings(self):
        state = json.loads(self.state_file.read_text())
        card = next(item for item in state["cards"] if item["id"] == self.card_ids[0])
        card["repair_order"]["payments"] = [{"id": "SYNTHETIC_SAVED", "amount": "7"}]
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        result = await self.context("repair_order", self.card_ids[0], "full")
        data = result.structuredContent["data"]
        self.assertTrue(result.structuredContent["ok"])
        self.assertNotIn("read_warnings", data)
        self.assertTrue(data["card"]["deadline_timestamp"])
        self.assertEqual(data["repair_order"]["payments"][0]["id"], "SYNTHETIC_SAVED")
        self.assertEqual(before, self.snapshot())

    async def test_gateway_keeps_warning_and_redacts_id_when_payload_budget_is_exhausted(self):
        self.board_api.get_client = lambda _client_id, *, order_limit, strict_client_links: {
            "ok": True,
            "data": {
                "client": {"id": self.client_id},
                "repair_orders": [
                    {"payments": [{"id": "SYNTHETIC_UNVERIFIED", "note": "x" * 4000}]}
                    for _ in range(40)
                ],
                "meta": {"read_warnings": ["repair_order_payment_identity_unverified"]},
            },
        }
        result = await self.context("client", self.client_id, "full")
        data = result.structuredContent["data"]
        self.assertTrue(result.structuredContent["ok"])
        self.assertEqual(
            data["meta"]["read_warnings"], ["repair_order_payment_identity_unverified"]
        )
        self.assertNotIn("id", data["repair_orders"][0]["payments"][0])

    async def test_gateway_marks_client_text_matches_as_unverified(self):
        state = json.loads(self.state_file.read_text())
        card = next(item for item in state["cards"] if item["id"] == self.card_ids[0])
        card["client_id"] = ""
        card["client_vehicle_id"] = ""
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        for detail in ("summary", "full"):
            result = await self.context("client", self.client_id, detail)
            self.assertTrue(result.structuredContent["ok"])
            self.assertIn(
                "related_card_client_match_unverified",
                result.structuredContent["data"]["meta"]["read_warnings"],
            )
            self.assertEqual(before, self.snapshot())
        result = await self.context("card", self.card_ids[0], "full")
        self.assertTrue(result.structuredContent["ok"])
        self.assertIn(
            "related_card_client_match_unverified",
            result.structuredContent["data"]["read_warnings"],
        )
        self.assertEqual(before, self.snapshot())

    async def test_raw_read_contract_uses_http_default_false_while_direct_service_default_creates(
        self,
    ):
        self.make_legacy()
        before = self.snapshot()
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
            with patch.dict("os.environ", {"AUTOSTOP_AGENT_GATEWAY_ENABLED": "0"}):
                raw_server = create_mcp_server(
                    self.board_api, self.logger, host="127.0.0.1", port=41838, path="/mcp"
                )
            tool = raw_server._tool_manager.get_tool("get_repair_order")
            self.assertTrue(tool.annotations.readOnlyHint)
            self.assertTrue(tool.annotations.idempotentHint)
            self.assertFalse(tool.annotations.destructiveHint)
            result = await tool.run({"card_id": self.empty_card_id}, convert_result=False)
        finally:
            auth_context_var.reset(context)
        payload = result.model_dump()
        self.assertTrue(payload["ok"])
        self.assertEqual(self.calls[-1], ("/api/get_repair_order", {"card_id": self.empty_card_id}))
        self.assertFalse(payload["data"]["meta"]["created"])
        self.assertFalse(payload["data"]["repair_order"]["number"])
        self.assertEqual(before, self.snapshot())
        created = self.service.get_repair_order({"card_id": self.empty_card_id})
        self.assertTrue(created["meta"]["created"])
        self.assertTrue(created["repair_order"]["number"])
        self.assertNotEqual(before, self.snapshot())
