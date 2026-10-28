from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser

from tests.services_case import CardServiceCase
from minimal_kanban.api.route_registry import build_service_routes
from minimal_kanban.api.server import RequestContextFactory
from minimal_kanban.mcp.client import BoardApiClient
from minimal_kanban.mcp.oauth_provider import OwnerAccessToken
from minimal_kanban.mcp.server import create_mcp_server
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import StateFileCorruptedError
from tests.test_agent_gateway_v2 import FakeBoardApi, GATEWAY_ENV


class ContextLinkAndTextSafetyTests(CardServiceCase, IsolatedAsyncioTestCase):
    def setUp(self):
        appdata_dir = tempfile.TemporaryDirectory(prefix="synthetic-context-links-")
        self.addCleanup(appdata_dir.cleanup)
        appdata = patch.dict("os.environ", {"APPDATA": appdata_dir.name})
        appdata.start()
        self.addCleanup(appdata.stop)
        self.appdata_dir = Path(appdata_dir.name)
        super().setUp()
        self.service = self._build_service()
        self.client_a = self.service.create_client(
            {"display_name": "SYNTHETIC_A", "vehicles": [{"vehicle": "SYNTHETIC_A_CAR"}]}
        )["client"]
        self.client_b = self.service.create_client(
            {"display_name": "SYNTHETIC_B", "vehicles": [{"vehicle": "SYNTHETIC_B_CAR"}]}
        )["client"]
        self.cards = []
        for title, client, vehicle in (
            ("SYNTHETIC_BOUND_A", self.client_a, "SYNTHETIC_A_CAR"),
            ("SYNTHETIC_BOUND_B", self.client_b, "SYNTHETIC_B_CAR"),
            ("SYNTHETIC_UNBOUND", None, "SYNTHETIC_INFERRED_CAR"),
        ):
            card_payload = {"title": title, "vehicle": vehicle}
            if client:
                card_payload.update(
                    {"client_id": client["id"], "client_vehicle_id": client["vehicles"][0]["id"]}
                )
            card = self.service.create_card(card_payload)["card"]
            self.service.update_card(
                {
                    "card_id": card["id"],
                    "repair_order": {
                        "client": "SYNTHETIC_A",
                        "works": [{"name": "SYNTHETIC_WORK", "quantity": "1", "price": "10"}],
                    },
                }
            )
            self.cards.append(card["id"])

        self.calls = []
        calls = self.calls
        routes = build_service_routes(
            self.service, MagicMock(), paste_shared_files_from_clipboard=lambda _: {}
        )

        class LocalBoard(FakeBoardApi):
            _request_readonly_query = BoardApiClient._request_readonly_query
            get_client = BoardApiClient.get_client
            get_card = BoardApiClient.get_card
            get_card_context = BoardApiClient.get_card_context
            get_repair_order = BoardApiClient.get_repair_order

            def _request(self, path, payload=None, *, method="POST"):
                parsed = urlsplit(path)
                effective_payload = (
                    RequestContextFactory.query_payload(parsed.query)
                    if method == "GET"
                    else dict(payload or {})
                )
                if self.operator_session is not None:
                    effective_payload["_operator_session"] = self.operator_session
                calls.append((parsed.path, effective_payload))
                try:
                    return {"ok": True, "data": routes[parsed.path](effective_payload)}
                except ServiceError as exc:
                    return {"ok": False, "error": {"code": exc.code, "message": "synthetic"}}
                except StateFileCorruptedError:
                    return {"ok": False, "error": {"code": "state_file_corrupted"}}

        self.board_api = LocalBoard()
        self.board_api.operator_session = None
        env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        env.start()
        self.addCleanup(env.stop)
        manager = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        manager.start()
        self.addCleanup(manager.stop)
        self.server = create_mcp_server(
            self.board_api, self.logger, host="127.0.0.1", port=41839, path="/mcp"
        )
        with patch.dict("os.environ", {"AUTOSTOP_AGENT_GATEWAY_ENABLED": "0"}):
            self.raw_server = create_mcp_server(
                self.board_api, self.logger, host="127.0.0.1", port=41839, path="/mcp"
            )

    def snapshot(self):
        result = {}
        for label, root in (("state", Path(self.temp_dir.name)), ("appdata", self.appdata_dir)):
            for path in root.rglob("*"):
                stat = path.stat()
                key = (label, str(path.relative_to(root)))
                meta = (stat.st_ino, stat.st_mode, stat.st_mtime_ns, stat.st_ctime_ns)
                result[key] = (meta, path.read_bytes() if path.is_file() else None)
        return result

    async def call(self, name, arguments, *, raw=False):
        token = OwnerAccessToken(
            token="synthetic",
            client_id="test-client",
            subject="CODEX",
            family_id="test-family",
            scopes=["kanban:read", "kanban:write"],
            resource="https://crm.example/mcp",
        )
        ctx = auth_context_var.set(AuthenticatedUser(token))
        try:
            server = self.raw_server if raw else self.server
            tool = server._tool_manager.get_tool(name)
            result = await tool.run(arguments, convert_result=False)
            return result.model_dump() if raw else result.structuredContent
        finally:
            auth_context_var.reset(ctx)

    async def call_hidden(self, name, arguments):
        schema = await self.call("get_raw_capability_schema", {"name": name})
        self.assertTrue(schema["ok"])
        return await self.call(
            "call_raw_capability",
            {
                "name": name,
                "arguments": arguments,
                "schema_hash": schema["summary"]["schema_hash"],
                "allow_large_output": True,
            },
        )

    async def test_raw_and_gateway_client_use_only_exact_links_with_matching_totals(self):
        before = self.snapshot()
        ui = self.service.get_client({"client_id": self.client_a["id"]})
        self.assertEqual(ui["meta"]["repair_orders_total"], 3)
        for raw, name, arguments in (
            (True, "get_client", {"client_id": self.client_a["id"]}),
            (
                False,
                "agent_entity_context",
                {"entity": "client", "entity_id": self.client_a["id"], "detail": "full"},
            ),
        ):
            with self.subTest(raw=raw):
                result = await self.call(name, arguments, raw=raw)
                self.assertTrue(result["ok"], result)
                data = result["data"]
                self.assertEqual(
                    [item["card_id"] for item in data["repair_orders"]], [self.cards[0]]
                )
                self.assertEqual(data["meta"]["repair_orders_total"], 1)
                serialized = json.dumps(data)
                self.assertNotIn("SYNTHETIC_BOUND_B", serialized)
                self.assertNotIn("SYNTHETIC_B_CAR", serialized)
                self.assertNotIn("SYNTHETIC_INFERRED_CAR", serialized)
                self.assertTrue(before == self.snapshot(), "Client read changed synthetic files")
        hidden = await self.call_hidden("get_client", {"client_id": self.client_a["id"]})
        self.assertTrue(hidden["ok"])
        hidden_data = hidden["data"]["data"]
        self.assertEqual(
            [item["card_id"] for item in hidden_data["repair_orders"]], [self.cards[0]]
        )
        self.assertEqual(hidden_data["meta"]["repair_orders_total"], 1)
        self.assertTrue(before == self.snapshot(), "Hidden client read changed synthetic files")
        for raw, name, arguments in (
            (True, "get_card_context", {"card_id": self.cards[0]}),
            (
                False,
                "agent_entity_context",
                {"entity": "card", "entity_id": self.cards[0], "detail": "full"},
            ),
        ):
            result = await self.call(name, arguments, raw=raw)
            self.assertTrue(result["ok"])
            client = result["data"]["client"]
            self.assertEqual(client["stats"]["repair_orders_total"], 1)
            self.assertNotIn("SYNTHETIC_B_CAR", json.dumps(client))
            self.assertNotIn("SYNTHETIC_INFERRED_CAR", json.dumps(client))
            self.assertTrue(before == self.snapshot(), "Card client read changed synthetic files")

    async def test_cross_client_vehicle_fails_closed_for_raw_and_gateway_reads(self):
        state = json.loads(self.state_file.read_text())
        card = next(item for item in state["cards"] if item["id"] == self.cards[0])
        card["client_vehicle_id"] = self.client_b["vehicles"][0]["id"]
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        for name, arguments in (
            ("get_card", {"card_id": self.cards[0]}),
            ("get_card_context", {"card_id": self.cards[0]}),
            ("get_card_context", {"card_id": self.cards[0], "include_repair_order_text": False}),
            ("get_repair_order", {"card_id": self.cards[0]}),
        ):
            with self.subTest(raw=name, args=arguments):
                result = await self.call(name, arguments, raw=True)
                self.assertFalse(result["ok"], "Invalid vehicle link was accepted")
                self.assertEqual(result["error"]["code"], "state_file_corrupted")
                self.assertTrue(before == self.snapshot(), "Card read changed synthetic files")
        for entity in ("card", "repair_order"):
            for detail in ("summary", "full"):
                with self.subTest(entity=entity, detail=detail):
                    result = await self.call(
                        "agent_entity_context",
                        {"entity": entity, "entity_id": self.cards[0], "detail": detail},
                    )
                    self.assertFalse(result["ok"], "Invalid vehicle link was accepted")
                    self.assertIn("state_file_corrupted", result["warnings"])
                    self.assertTrue(
                        before == self.snapshot(), "Gateway read changed synthetic files"
                    )

    async def test_strict_client_limit_counts_only_exact_links_before_pagination(self):
        card = self.service.create_card(
            {
                "title": "SYNTHETIC_BOUND_A_SECOND",
                "client_id": self.client_a["id"],
                "client_vehicle_id": self.client_a["vehicles"][0]["id"],
            }
        )["card"]
        self.service.update_card(
            {
                "card_id": card["id"],
                "repair_order": {"client": "SYNTHETIC_A", "works": [{"name": "SYNTHETIC"}]},
            }
        )
        before = self.snapshot()
        result = await self.call(
            "get_client", {"client_id": self.client_a["id"], "order_limit": 1}, raw=True
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["meta"]["repair_orders_total"], 2)
        self.assertEqual(result["data"]["meta"]["repair_orders_returned"], 1)
        self.assertEqual(result["data"]["client"]["stats"]["repair_orders_total"], 2)
        self.assertEqual(len(result["data"]["repair_orders"]), 1)
        self.assertIn(result["data"]["repair_orders"][0]["card_id"], (self.cards[0], card["id"]))
        self.assertTrue(before == self.snapshot(), "Limited client read changed synthetic files")

    async def test_unresolved_or_duplicate_vehicle_links_fail_closed(self):
        original = json.loads(self.state_file.read_text())
        for scenario in ("missing_owner", "missing_client", "duplicate_id", "normalized_away"):
            with self.subTest(scenario=scenario):
                state = json.loads(json.dumps(original))
                card = next(item for item in state["cards"] if item["id"] == self.cards[0])
                client = next(
                    item for item in state["clients"] if item["id"] == self.client_a["id"]
                )
                if scenario == "missing_owner":
                    card["client_id"] = ""
                elif scenario == "missing_client":
                    state["clients"] = [
                        item for item in state["clients"] if item["id"] != client["id"]
                    ]
                elif scenario == "duplicate_id":
                    client["vehicles"].append(dict(client["vehicles"][0]))
                else:
                    duplicate = dict(client["vehicles"][0])
                    duplicate["id"] = "SYNTHETIC_NORMALIZED_AWAY"
                    client["vehicles"].append(duplicate)
                    card["client_vehicle_id"] = duplicate["id"]
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                result = await self.call("get_card", {"card_id": self.cards[0]}, raw=True)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "state_file_corrupted")
                self.assertTrue(
                    before == self.snapshot(), "Invalid link read changed synthetic files"
                )

    async def test_orphan_and_duplicate_client_links_fail_without_vehicle(self):
        original = json.loads(self.state_file.read_text())
        for scenario in ("orphan", "duplicate", "invalid_record", "normalized_card_id"):
            with self.subTest(scenario=scenario):
                state = json.loads(json.dumps(original))
                card = next(item for item in state["cards"] if item["id"] == self.cards[0])
                card["client_vehicle_id"] = ""
                if scenario == "orphan":
                    card["client_id"] = "SYNTHETIC_UNKNOWN_CLIENT"
                elif scenario == "normalized_card_id":
                    card["client_id"] += " "
                else:
                    client = next(
                        item for item in state["clients"] if item["id"] == card["client_id"]
                    )
                    if scenario == "duplicate":
                        state["clients"].append(dict(client))
                    else:
                        client["phones"] = 7
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                for name in ("get_card", "get_card_context", "get_repair_order"):
                    result = await self.call(name, {"card_id": self.cards[0]}, raw=True)
                    self.assertFalse(result["ok"], f"Unverified client accepted by {name}")
                    self.assertEqual(result["error"]["code"], "state_file_corrupted")
                    self.assertTrue(before == self.snapshot(), "Raw read changed synthetic files")
                    hidden = await self.call_hidden(name, {"card_id": self.cards[0]})
                    self.assertFalse(hidden["ok"], f"Hidden {name} accepted an unverified client")
                    self.assertEqual(hidden["data"]["error"]["code"], "state_file_corrupted")
                    self.assertTrue(before == self.snapshot(), "Hidden raw read changed files")
                for entity in ("card", "repair_order"):
                    for detail in ("summary", "full"):
                        result = await self.call(
                            "agent_entity_context",
                            {"entity": entity, "entity_id": self.cards[0], "detail": detail},
                        )
                        self.assertFalse(result["ok"], "Gateway accepted an unverified client")
                        self.assertIn("state_file_corrupted", result["warnings"])
                        self.assertTrue(before == self.snapshot(), "Gateway read changed files")

    async def test_valid_empty_and_legacy_client_links_without_vehicle_remain_readable(self):
        original = json.loads(self.state_file.read_text())
        for scenario in ("valid", "empty", "null", "missing", "legacy"):
            with self.subTest(scenario=scenario):
                state = json.loads(json.dumps(original))
                card = next(item for item in state["cards"] if item["id"] == self.cards[0])
                card["client_vehicle_id"] = ""
                if scenario == "empty":
                    card["client_id"] = ""
                elif scenario == "null":
                    card["client_id"] = None
                elif scenario == "missing":
                    del card["client_id"]
                elif scenario == "legacy":
                    client = next(
                        item for item in state["clients"] if item["id"] == card["client_id"]
                    )
                    card["client_id"] = client["id"] = "synthetic-legacy-client"
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                for name in ("get_card", "get_card_context", "get_repair_order"):
                    result = await self.call(name, {"card_id": self.cards[0]}, raw=True)
                    self.assertTrue(result["ok"], f"{name} rejected {scenario}")
                    self.assertEqual(
                        result["data"]["card"]["client_id"], card.get("client_id") or ""
                    )
                    self.assertTrue(before == self.snapshot(), "Raw read changed synthetic files")
                for entity in ("card", "repair_order"):
                    for detail in ("summary", "full"):
                        result = await self.call(
                            "agent_entity_context",
                            {"entity": entity, "entity_id": self.cards[0], "detail": detail},
                        )
                        self.assertTrue(result["ok"], f"Gateway rejected {scenario}")
                        self.assertTrue(before == self.snapshot(), "Gateway read changed files")

    async def test_raw_card_context_renders_text_in_memory_without_any_file_changes(self):
        raw_tool = self.raw_server._tool_manager.get_tool("get_card_context")
        self.assertTrue(raw_tool.annotations.readOnlyHint)
        state = json.loads(self.state_file.read_text())
        state["columns"].append({"bad": "synthetic legacy"})
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        for include_text in (None, True, False):
            arguments = {"card_id": self.cards[0]}
            if include_text is not None:
                arguments["include_repair_order_text"] = include_text
            with self.subTest(include_text=include_text):
                result = await self.call("get_card_context", arguments, raw=True)
                self.assertTrue(result["ok"], result)
                text_payload = result["data"]["repair_order_text"]
                if include_text is False:
                    self.assertIsNone(text_payload)
                else:
                    self.assertIn("SYNTHETIC_WORK", text_payload["text"])
                    self.assertEqual(text_payload["file_path"], "")
                self.assertTrue(before == self.snapshot(), "Text read changed synthetic files")
        hidden = await self.call_hidden("get_card_context", {"card_id": self.cards[0]})
        self.assertTrue(hidden["ok"])
        self.assertIn("SYNTHETIC_WORK", hidden["data"]["data"]["repair_order_text"]["text"])
        self.assertEqual(hidden["data"]["data"]["repair_order_text"]["file_path"], "")
        self.assertTrue(before == self.snapshot(), "Hidden text read changed synthetic files")

    async def test_in_memory_text_preserves_operator_visibility(self):
        state = json.loads(self.state_file.read_text())
        card = next(item for item in state["cards"] if item["id"] == self.cards[0])
        card["repair_order"]["works"][0]["work_salary_note"] = "SYNTHETIC_PRIVATE_SALARY"
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        for permissions, visible in ((["employees_cashboxes_access"], True), ([], False)):
            with self.subTest(permissions=permissions):
                self.board_api.operator_session = {"permissions": permissions}
                result = await self.call("get_card_context", {"card_id": self.cards[0]}, raw=True)
                self.assertTrue(result["ok"])
                rendered = result["data"]["repair_order_text"]["text"]
                self.assertEqual("SYNTHETIC_PRIVATE_SALARY" in rendered, visible)
                self.assertIn("SYNTHETIC_WORK", rendered)
                self.assertTrue(before == self.snapshot(), "Text read changed synthetic files")

    async def test_legacy_ui_text_context_keeps_existing_materialization_behavior(self):
        card = next(card for card in self.store.read_bundle()["cards"] if card.id == self.cards[0])
        path = self.service._repair_order_text_path(card)
        path.unlink(missing_ok=True)
        self.assertFalse(path.exists())
        result = self.service.get_card_context({"card_id": self.cards[0]})
        self.assertEqual(result["repair_order_text"]["file_path"], str(path))
        self.assertTrue(path.is_file())
        self.assertIn("SYNTHETIC_WORK", path.read_text(encoding="utf-8"))
