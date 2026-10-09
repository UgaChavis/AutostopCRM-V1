"""The full installed catalog must survive protected HTTP serialization exactly."""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.api.server import ApiServer, _json_response  # noqa: E402
from minimal_kanban.operator_auth import OperatorAuthService  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.manager_tool_catalog import content_hash, load_bundle  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402

CATALOG_ROUTE = "/api/manager_structure/tool_catalog"


class ManagerToolCatalogWireTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        logger = logging.getLogger(f"test.catalog-wire.{self._testMethodName}")
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        store = JsonStore(state_file=root / "state.json", logger=logger)
        service = CardService(
            store, logger, attachments_dir=root / "attachments", repair_orders_dir=root / "orders"
        )
        operators = OperatorAuthService(
            store, service, users_file=root / "users.json", logger=logger
        )
        server = ApiServer(service, logger, operator_service=operators, start_port=0)
        server.start()
        self.addCleanup(server.stop)
        self.base_url = server.base_url
        status, result = self.request(
            "/api/login_operator", "POST", {"username": "admin", "password": "admin"}
        )
        self.assertEqual(status, 200)
        self.admin = {"X-Operator-Session": result["data"]["session"]["token"]}

    def request(self, route, method="GET", data=None, headers=None):
        encoded = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(
            self.base_url + route,
            data=encoded,
            headers={"Content-Type": "application/json", **(headers or {})},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as response:
            with response:
                return response.code, json.loads(response.read())

    def assert_full_catalog(self, actual):
        expected = load_bundle()
        self.assertEqual(len(expected["modules"]), 15)
        self.assertEqual(len(expected["tools"]), 120)
        self.assertEqual(len(expected["native_schemas"]), 68)
        self.assertEqual(actual, expected)
        self.assertEqual(content_hash(actual), expected["content_hash"])

    def test_full_pinned_bundle_roundtrips_get_and_post_without_schema_or_card_truncation(self):
        for method in ("GET", "POST"):
            with self.subTest(method=method):
                status, response = self.request(
                    CATALOG_ROUTE, method, {} if method == "POST" else None, self.admin
                )
                self.assertEqual(status, 200)
                self.assertTrue(response["ok"])
                self.assert_full_catalog(response["data"])

    def test_existing_service_identity_receives_full_catalog_with_view_only_graph(self):
        environment = {
            "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
            "AUTOSTOP_AGENT_SERVICE_IDENTITY": "catalog-test-reader",
            "MINIMAL_KANBAN_MCP_BEARER_TOKEN": "synthetic-local-service-token",
        }
        headers = {
            "X-Autostop-Agent-Identity": "catalog-test-reader",
            "X-Autostop-Agent-Token": "synthetic-local-service-token",
        }
        with patch.dict("os.environ", environment):
            status, response = self.request(
                CATALOG_ROUTE + "?source=mcp_agent_gateway_v2", headers=headers
            )
            self.assertEqual(status, 200)
            self.assert_full_catalog(response["data"])
            status, graph = self.request(
                "/api/manager_structure?source=mcp_agent_gateway_v2", headers=headers
            )
        self.assertEqual(status, 200)
        self.assertFalse(graph["data"]["can_edit"])
        self.assertFalse(graph["data"]["can_edit_tool_status"])

    def test_anonymous_reads_stay_denied_and_operator_viewer_keeps_read_only_access(self):
        status, _ = self.request(
            "/api/save_operator_user",
            "POST",
            {"username": "worker", "password": "synthetic-password"},
            self.admin,
        )
        self.assertEqual(status, 200)
        status, login = self.request(
            "/api/login_operator", "POST", {"username": "worker", "password": "synthetic-password"}
        )
        self.assertEqual(status, 200)
        worker = {"X-Operator-Session": login["data"]["session"]["token"]}
        for method in ("GET", "POST"):
            with self.subTest(method=method):
                status, response = self.request(
                    CATALOG_ROUTE, method, {} if method == "POST" else None
                )
                self.assertEqual(status, 401)
                self.assertFalse(response["ok"])
                status, response = self.request(
                    CATALOG_ROUTE, method, {} if method == "POST" else None, worker
                )
                self.assertEqual(status, 200)
                self.assert_full_catalog(response["data"])
        status, response = self.request(
            "/api/manager_structure/apply",
            "POST",
            {
                "operation": "set_tool_status",
                "tool_status": {"operation_id": "manager.decode_wmi_vpic", "state": "working"},
            },
            worker,
        )
        self.assertEqual(status, 403)
        self.assertFalse(response["ok"])

    def test_other_json_responses_keep_the_bounded_depth_guard(self):
        cyclic = {"value": 1.25}
        cyclic["self"] = cyclic
        actual = json.loads(_json_response(ok=True, data=cyclic, request_id="synthetic"))["data"]
        self.assertEqual(actual["value"], 1.25)
        for _ in range(7):
            actual = actual["self"]
        self.assertIsInstance(actual, str)
