from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch

from tests.services_case import CardServiceCase
from minimal_kanban.api.route_registry import build_service_routes
from minimal_kanban.mcp.client import BoardApiClient
from minimal_kanban.mcp.server import create_mcp_server
from minimal_kanban.services.errors import ServiceError
from tests.test_mcp_registration_contracts import _RAW_REGISTRATION_ENV


class McpRepairOrderReadonlyTests(CardServiceCase, IsolatedAsyncioTestCase):
    def setUp(self):
        self.appdata = tempfile.TemporaryDirectory(prefix="synthetic-mcp-order-appdata-")
        self.addCleanup(self.appdata.cleanup)
        env = patch.dict("os.environ", {**_RAW_REGISTRATION_ENV, "APPDATA": self.appdata.name})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()
        self.service = self._build_service()
        self.card_id = self.service.create_card({"title": "Synthetic read order"})["card"]["id"]
        self.other_card_id = self.service.create_card({"title": "Synthetic other order"})["card"][
            "id"
        ]
        orders = Path(self.temp_dir.name) / "repair-orders"
        orders.mkdir(exist_ok=True)
        (orders / "unrelated.txt").write_text("Synthetic unrelated file", encoding="utf-8")
        self.calls = []
        self.routes = build_service_routes(
            self.service, MagicMock(), paste_shared_files_from_clipboard=lambda _: {}
        )
        board_api = BoardApiClient("http://127.0.0.1:1", bearer_token="synthetic")
        self.board_api = board_api
        request = patch.object(board_api, "_request", side_effect=self._request)
        request.start()
        self.addCleanup(request.stop)
        manager = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        manager.start()
        self.addCleanup(manager.stop)
        self.server = create_mcp_server(
            board_api,
            self.logger,
            host="127.0.0.1",
            port=41731,
            path="/mcp",
            bearer_token="synthetic",
        )

    def _request(self, route, payload):
        self.calls.append((route, dict(payload)))
        try:
            return {"ok": True, "data": self.routes[route](payload)}
        except ServiceError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": "synthetic"}}

    def snapshot(self):
        result = {}
        for label, root in (
            ("state", Path(self.temp_dir.name)),
            ("appdata", Path(self.appdata.name)),
        ):
            for path in root.rglob("*"):
                stat = path.stat()
                key = (label, str(path.relative_to(root)))
                metadata = (stat.st_ino, stat.st_mode, stat.st_mtime_ns, stat.st_ctime_ns)
                result[key] = (metadata, path.read_bytes() if path.is_file() else None)
        return result

    def make_legacy_orders(self):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        for card in state["cards"]:
            card["repair_order"].update(
                {"number": "", "client": "Synthetic client", "reason": "Synthetic service"}
            )
        state["columns"].append({"bad": "Synthetic legacy column"})
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

    async def call(self, name, card_id):
        tool = self.server._tool_manager.get_tool(name)
        self.assertTrue(tool.annotations.readOnlyHint)
        return await tool.run({"card_id": card_id}, convert_result=False)

    async def test_empty_order_read_does_not_create_order_or_files(self):
        before = self.snapshot()
        for _ in range(2):
            result = await self.call("get_repair_order", self.card_id)
            self.assertTrue(result.ok)
            self.assertFalse(result.data["meta"]["created"])
            self.assertFalse(result.data["meta"]["has_any_data"])
            self.assertEqual(result.data["repair_order"]["number"], "")
            self.assertEqual(
                self.calls[-1],
                ("/api/get_repair_order", {"card_id": self.card_id, "create_if_missing": False}),
            )
            self.assertEqual(before, self.snapshot())

    async def test_existing_order_read_does_not_repair_any_legacy_order(self):
        self.make_legacy_orders()
        before = self.snapshot()
        result = await self.call("get_repair_order", self.card_id)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["repair_order"]["client"], "Synthetic client")
        self.assertEqual(result.data["repair_order"]["number"], "")
        self.assertFalse(result.data["meta"]["created"])
        self.assertEqual(before, self.snapshot())

    async def test_text_read_renders_without_numbering_or_materializing(self):
        self.make_legacy_orders()
        before = self.snapshot()
        result = await self.call("get_repair_order_text", self.card_id)
        self.assertTrue(result.ok)
        self.assertIn("Synthetic client", result.data["text"])
        self.assertIn(self.card_id, result.data["text"])
        self.assertTrue(result.data["file_name"].endswith(".txt"))
        self.assertEqual(result.data["file_path"], "")
        self.assertEqual(result.data["repair_order"]["number"], "")
        self.assertEqual(
            self.calls[-1],
            ("/api/get_repair_order_text", {"card_id": self.card_id, "read_only": True}),
        )
        self.assertEqual(before, self.snapshot())

    async def test_missing_order_reads_do_not_repair_other_orders(self):
        self.make_legacy_orders()
        for name in ("get_repair_order", "get_repair_order_text"):
            with self.subTest(tool=name):
                before = self.snapshot()
                result = await self.call(name, "missing-synthetic")
                self.assertFalse(result.ok)
                self.assertEqual(before, self.snapshot())

    async def test_text_read_reports_synthesized_row_identity_without_repairing_it(self):
        self.make_legacy_orders()
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        for card in state["cards"]:
            if card["id"] == self.card_id:
                card["repair_order"]["works"] = [
                    {"name": "Synthetic work", "quantity": "1", "price": "100", "total": "100"}
                ]
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()

        result = await self.call("get_repair_order_text", self.card_id)

        self.assertTrue(result.ok)
        self.assertEqual(result.data["repair_order"]["works"][0]["id"], "row-1")
        self.assertIn("repair_order_row_identity_unverified", result.data["read_warnings"])
        self.assertEqual(before, self.snapshot())

        legacy_result = self.board_api.get_repair_order_text(self.card_id)
        self.assertTrue(legacy_result["ok"])
        self.assertNotIn("read_warnings", legacy_result["data"])

    def test_legacy_text_client_still_materializes_by_default_and_explicit_false(self):
        self.make_legacy_orders()
        for options in ({}, {"read_only": False}):
            with self.subTest(options=options):
                result = self.board_api.get_repair_order_text(self.card_id, **options)
                self.assertTrue(result["ok"])
                data = result["data"]
                self.assertTrue(data["repair_order"]["number"])
                path = Path(data["file_path"])
                self.assertTrue(path.is_file())
                self.assertEqual(path.name, data["file_name"])
                self.assertEqual(path.read_text(encoding="utf-8"), data["text"])
                self.assertEqual(
                    self.calls[-1],
                    ("/api/get_repair_order_text", {"card_id": self.card_id, **options}),
                )
