# ruff: noqa: I001, E402
from __future__ import annotations

import base64
import contextlib
import gzip
import io
import json
import logging
import os
import socket
import struct
import sys
import tempfile
import http.client
import time
import unittest
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from attachment_samples import (
    JPEG_1X1_BYTES,
    PNG_1X1_BYTES,
    minimal_docx_bytes,
    minimal_pdf_bytes,
    minimal_text_bytes,
    minimal_xlsx_bytes,
)
from minimal_kanban.api.server import ApiServer
from minimal_kanban.api.server import ReusableThreadingHTTPServer
from minimal_kanban.api.server import _same_host_cors_origin
from minimal_kanban.api.server import _success_log_level
from minimal_kanban.api import server as api_server_module
from minimal_kanban.mcp.oauth_provider import (
    OAUTH_AUDIT_ACTOR_HEADER,
    OAUTH_AUDIT_ASSERTION_HEADER,
    create_oauth_audit_assertion,
)
from minimal_kanban.models import AuditEvent, utc_now
from minimal_kanban.services import snapshot_service as snapshot_service_module
from minimal_kanban.operator_activity import OperatorActivityService
from minimal_kanban.operator_auth import (
    PASSWORD_HASH_MAX_ITERATIONS,
    OperatorAuthService,
    _password_hash,
    _verify_password,
)
from minimal_kanban.services.card_service import CardService, ServiceError
from minimal_kanban.services.payroll_constants import EMPLOYEES_MAX_COUNT
from minimal_kanban.storage.json_store import JsonStore
from minimal_kanban.web_assets import (
    BOARD_WEB_APP_CSS,
    BOARD_WEB_APP_CSS_PATH,
    BOARD_WEB_APP_HTML,
    BOARD_WEB_APP_JS,
    BOARD_WEB_APP_JS_PATH,
)

TEST_API_PORT_START = 0
TEST_API_PORT_FALLBACK_LIMIT = 25
TEST_HTTP_TIMEOUT_SECONDS = 30


def is_transient_request_error(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", None)
    transient_types = (TimeoutError, ConnectionAbortedError, ConnectionResetError)
    return isinstance(exc, transient_types) or isinstance(reason, transient_types)


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ApiServerTests(unittest.TestCase):
    def test_threaded_api_server_shutdown_does_not_wait_forever_on_client_threads(self) -> None:
        self.assertTrue(ReusableThreadingHTTPServer.daemon_threads)
        self.assertFalse(ReusableThreadingHTTPServer.block_on_close)
        self.assertGreaterEqual(ReusableThreadingHTTPServer.request_queue_size, 64)

    def test_api_server_supports_os_assigned_port(self) -> None:
        logger = logging.getLogger(f"test.api.os_port.{self._testMethodName}")
        server = ApiServer(self.service, logger, start_port=0, fallback_limit=25)
        try:
            server.start()
            self.assertGreater(server.port, 0)
            self.assertIn(f":{server.port}", server.base_url)
            with urllib.request.urlopen(server.base_url + "/api/health", timeout=5) as response:
                self.assertEqual(response.status, 200)
        finally:
            server.stop()

    def test_api_responses_close_local_http_connections(self) -> None:
        self.assertEqual(self.request("/api/health", method="GET")[0], 200)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Connection"), "close")
            response.read()
        finally:
            connection.close()

    def test_maintenance_marker_blocks_writes_but_keeps_reads_available(self) -> None:
        marker = Path(self.temp_dir.name) / ".agent-gateway-maintenance"
        marker.touch()
        with patch.dict(os.environ, {"AUTOSTOP_MAINTENANCE_MARKER": str(marker)}):
            status, blocked = self.request(
                "/api/create_card",
                {"title": "Must not be created", "deadline": {"hours": 2}},
            )
            health_status, health = self.request("/api/health", method="GET")
            read_status, cards = self.request("/api/get_cards", {})

        self.assertEqual(status, 503)
        self.assertEqual(blocked["error"]["code"], "maintenance_mode")
        self.assertEqual(health_status, 200)
        self.assertTrue(health["data"]["maintenance_mode"])
        self.assertEqual(read_status, 200)
        self.assertEqual(cards["data"]["cards"], [])

    def test_ai_chat_knowledge_and_autofill_ui_routes_use_card_service(self) -> None:
        knowledge_status, knowledge = self.request(
            "/api/get_ai_chat_knowledge",
            {
                "prompt": "Кратко объясни контекст карточки",
                "context": {"kind": "compact_context", "card_label": "C-TEST"},
                "prompt_profile": {"kind": "ai_chat"},
            },
        )
        get_status, get_knowledge = self.request(
            "/api/get_ai_chat_knowledge?prompt=diagnostics",
            method="GET",
        )
        create_status, created = self.request(
            "/api/create_card",
            {"title": "AI route contract", "deadline": {"hours": 1}},
        )
        card = created["data"]["card"]
        stale_status, stale = self.request(
            "/api/set_card_ai_autofill",
            {
                "card_id": card["id"],
                "enabled": True,
                "expected_updated_at": "2000-01-01T00:00:00+00:00",
            },
        )
        update_status, updated = self.request(
            "/api/set_card_ai_autofill",
            {
                "card_id": card["id"],
                "enabled": True,
                "expected_updated_at": card["updated_at"],
            },
        )

        self.assertEqual(knowledge_status, 200)
        self.assertEqual(knowledge["data"]["kind"], "ai_chat_knowledge")
        self.assertEqual(knowledge["data"]["prompt_profile_kind"], "ai_chat")
        self.assertEqual(get_status, 200)
        self.assertEqual(get_knowledge["data"]["prompt"], "diagnostics")
        self.assertEqual(create_status, 200)
        self.assertEqual(stale_status, 409)
        self.assertEqual(stale["error"]["code"], "card_update_conflict")
        self.assertEqual(update_status, 200)
        self.assertEqual(updated["data"]["card"]["id"], card["id"])
        self.assertTrue(updated["data"]["meta"]["retired"])

    def test_copy_shared_file_obeys_write_maintenance_and_operator_gates(self) -> None:
        upload_status, uploaded = self.request(
            "/api/upload_shared_file",
            {
                "file_name": "copy-gate.txt",
                "content_base64": base64.b64encode(b"copy gate").decode("ascii"),
            },
        )
        self.assertEqual(upload_status, 200)
        file_id = uploaded["data"]["file"]["id"]

        marker = Path(self.temp_dir.name) / ".agent-gateway-maintenance"
        marker.touch()
        with patch.dict(os.environ, {"AUTOSTOP_MAINTENANCE_MARKER": str(marker)}):
            maintenance_status, maintenance_blocked = self.request(
                "/api/copy_shared_file", {"file_id": file_id}
            )

        proxied_headers = {"X-Forwarded-For": "203.0.113.10"}
        unauthorized_status, unauthorized = self.request(
            "/api/copy_shared_file",
            {"file_id": file_id},
            headers=proxied_headers,
        )
        login_status, logged_in = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(login_status, 200)
        authorized_status, authorized = self.request(
            "/api/copy_shared_file",
            {"file_id": file_id},
            headers={
                **proxied_headers,
                "X-Operator-Session": logged_in["data"]["session"]["token"],
            },
        )

        self.assertEqual(maintenance_status, 503)
        self.assertEqual(maintenance_blocked["error"]["code"], "maintenance_mode")
        self.assertEqual(unauthorized_status, 401)
        self.assertEqual(unauthorized["error"]["code"], "unauthorized")
        self.assertEqual(authorized_status, 200)
        self.assertEqual(authorized["data"]["clipboard"]["source_id"], file_id)

    def test_api_cors_allows_same_host_origin_only(self) -> None:
        status, headers, _ = self.raw_request("/api/health", headers={"Origin": self.base_url})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), self.base_url)

        status, headers, _ = self.raw_request(
            "/api/health", headers={"Origin": "https://evil.example"}
        )
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_api_cors_helper_normalizes_trailing_dot_and_rejects_bad_ports(self) -> None:
        self.assertEqual(
            _same_host_cors_origin("http://localhost.:41731", "localhost:41731"),
            "http://localhost.:41731",
        )
        self.assertEqual(
            _same_host_cors_origin("http://localhost:41731", "localhost.:41731"),
            "http://localhost:41731",
        )
        self.assertEqual(
            _same_host_cors_origin("http://evil.example.:41731", "example:41731"),
            "",
        )
        self.assertEqual(
            _same_host_cors_origin("http://localhost:bad", "localhost:41731"),
            "",
        )

    def test_api_cors_rejects_cross_origin_preflight(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(
                "OPTIONS",
                "/api/create_card",
                headers={
                    "Origin": "https://evil.example",
                    "Access-Control-Request-Method": "POST",
                },
            )
            response = connection.getresponse()
            response.read()
        finally:
            connection.close()

        self.assertEqual(response.status, 403)
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))

    def test_snapshot_success_route_uses_debug_log_level(self) -> None:
        self.assertEqual(_success_log_level("/api/get_board_snapshot"), logging.DEBUG)
        self.assertEqual(_success_log_level("/api/get_board_revision"), logging.DEBUG)
        self.assertEqual(_success_log_level("/api/health"), logging.DEBUG)
        self.assertEqual(_success_log_level("/api/create_card"), logging.INFO)

    def test_inventory_routes_save_search_write_off_and_return_fractional_item(self) -> None:
        status, created_card = self.request(
            "/api/create_card",
            {
                "vehicle": "BMW X5",
                "title": "Склад API",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created_card["data"]["card"]["id"]

        status, saved = self.request(
            "/api/save_inventory_item",
            {
                "name": "Масло 5W-30",
                "catalog_number": "OIL-API",
                "unit": "л",
                "quantity": "5.5",
                "cost_price": "500",
                "sale_price": "800",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        item = saved["data"]["item"]
        self.assertEqual(item["quantity"], "5.5")

        status, listed = self.request("/api/list_inventory_items?limit=200", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(listed["data"]["items"][0]["id"], item["id"])

        status, searched = self.request(
            "/api/search_inventory_items",
            {"query": "oil-api", "limit": 10},
        )
        self.assertEqual(status, 200)
        self.assertEqual(searched["data"]["items"][0]["id"], item["id"])

        status, written_off = self.request(
            "/api/write_off_inventory_item",
            {
                "item_id": item["id"],
                "card_id": card_id,
                "quantity": "1.25",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(written_off["data"]["item"]["quantity"], "4.25")
        self.assertEqual(written_off["data"]["repair_order"]["materials"][0]["price"], "800")

        status, movements = self.request("/api/list_inventory_movements?limit=20", method="GET")
        self.assertEqual(status, 200)
        self.assertIn(
            written_off["data"]["movement"]["id"],
            {movement["id"] for movement in movements["data"]["movements"]},
        )

        status, rejected = self.request(
            "/api/write_off_inventory_item",
            {
                "item_id": item["id"],
                "card_id": card_id,
                "quantity": "99",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(rejected["error"]["code"], "validation_error")

        status, returned = self.request(
            "/api/return_inventory_movement",
            {
                "movement_id": written_off["data"]["movement"]["id"],
                "card_id": card_id,
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(returned["data"]["item"]["quantity"], "5.5")

    def test_api_base_url_normalizes_wildcard_and_formats_ipv6_hosts(self) -> None:
        logger = logging.getLogger(f"test.api.base_url.{self._testMethodName}")
        wildcard = ApiServer(Mock(), logger, host="0.0.0.0", start_port=41731, fallback_limit=1)
        ipv6 = ApiServer(Mock(), logger, host="::1", start_port=41731, fallback_limit=1)
        ipv6_wildcard = ApiServer(Mock(), logger, host="[::]", start_port=41731, fallback_limit=1)
        self.assertEqual(wildcard.base_url, "http://127.0.0.1:41731")
        self.assertEqual(ipv6.base_url, "http://[::1]:41731")
        self.assertEqual(ipv6_wildcard.base_url, "http://127.0.0.1:41731")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        state_file = Path(self.temp_dir.name) / "state.json"
        logger = logging.getLogger(f"test.api.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(state_file=state_file, logger=logger)
        self.service = CardService(
            self.store,
            logger,
            attachments_dir=Path(self.temp_dir.name) / "attachments",
            repair_orders_dir=Path(self.temp_dir.name) / "repair-orders",
        )
        self.users_file = Path(self.temp_dir.name) / "users.json"
        self.operator_service = OperatorAuthService(
            self.store,
            self.service,
            users_file=self.users_file,
            activity_service=OperatorActivityService(
                activity_dir=Path(self.temp_dir.name) / "operator-activity",
                logger=logger,
            ),
            logger=logger,
        )
        self.port = TEST_API_PORT_START
        self.server = ApiServer(
            self.service,
            logger,
            operator_service=self.operator_service,
            start_port=self.port,
            fallback_limit=TEST_API_PORT_FALLBACK_LIMIT,
        )
        self.server.start()
        self.port = self.server.port
        self.base_url = self.server.base_url

    def tearDown(self) -> None:
        self.server.stop()
        self.temp_dir.cleanup()

    def request(
        self,
        path: str,
        payload: dict | list | None = None,
        *,
        method: str = "POST",
        headers: dict[str, str] | None = None,
        timeout: float = TEST_HTTP_TIMEOUT_SECONDS,
    ) -> tuple[int, dict]:
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        merged_headers = {"Content-Type": "application/json"}
        if headers:
            merged_headers.update(headers)
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=merged_headers,
            method=method,
        )
        attempts = 3
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                try:
                    return exc.code, json.loads(exc.read().decode("utf-8"))
                finally:
                    exc.close()
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionAbortedError,
                ConnectionResetError,
            ) as exc:
                if attempt + 1 < attempts and is_transient_request_error(exc):
                    time.sleep(0.05)
                    continue
                raise
        raise AssertionError("unreachable request retry state")

    def raw_request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict | list | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = TEST_HTTP_TIMEOUT_SECONDS,
    ) -> tuple[int, dict[str, str], bytes]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        merged_headers = {"Content-Type": "application/json"}
        if headers:
            merged_headers.update(headers)
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=merged_headers,
            method=method,
        )
        attempts = 2 if method.upper() == "GET" else 1
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return response.status, dict(response.headers.items()), response.read()
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionAbortedError,
                ConnectionResetError,
            ) as exc:
                if attempt + 1 < attempts and is_transient_request_error(exc):
                    time.sleep(0.05)
                    continue
                raise
        raise AssertionError("unreachable raw request retry state")

    def test_health_and_create_card(self) -> None:
        status, health = self.request("/api/health", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        self.assertEqual(health["data"]["base_url"], self.base_url)
        self.assertFalse(health["data"]["auth_required"])

        status, created = self.request(
            "/api/create_card",
            {"title": "API карточка", "deadline": {"days": 1, "hours": 2}},
        )
        self.assertEqual(status, 200)
        self.assertTrue(created["ok"])
        self.assertEqual(created["data"]["card"]["title"], "API карточка")
        self.assertEqual(created["data"]["card"]["status"], "ok")
        self.assertEqual(created["data"]["card"]["indicator"], "green")
        self.assertIn("remaining_seconds", created["data"]["card"])
        self.assertIn("deadline_timestamp", created["data"]["card"])

    def test_json_responses_include_nosniff_header(self) -> None:
        status, headers, _ = self.raw_request("/api/health")

        self.assertEqual(status, 200)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")

    def test_client_routes_accept_documented_nested_payloads(self) -> None:
        status, created = self.request(
            "/api/create_client",
            {
                "client": {
                    "client_type": "ip",
                    "display_name": "ИП Петров Петр",
                    "inn": "540000000001",
                    "phone": "+7 913 555-66-77",
                }
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(created["ok"])
        client_id = created["data"]["client"]["id"]
        self.assertEqual(created["data"]["client"]["client_type"], "ip")
        self.assertEqual(created["data"]["client"]["inn"], "540000000001")

        status, updated = self.request(
            "/api/update_client",
            {
                "client_id": client_id,
                "patch": {
                    "bank_name": "Тест Банк",
                    "contact_person": "Петров Петр",
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(updated["ok"])
        self.assertEqual(updated["data"]["client"]["bank_name"], "Тест Банк")
        self.assertEqual(updated["data"]["client"]["contact_person"], "Петров Петр")

        status, deleted = self.request(
            "/api/delete_client",
            {"client_id": client_id},
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["ok"])
        self.assertTrue(deleted["data"]["meta"]["deleted"])

    def test_client_routes_accept_three_phones_and_search_each(self) -> None:
        status, created = self.request(
            "/api/create_client",
            {
                "display_name": "API клиент телефоны",
                "phones": [
                    "+7 900 100-00-01",
                    "+7 900 100-00-02",
                    "+7 900 100-00-03",
                    "+7 900 100-00-04",
                ],
            },
        )
        self.assertEqual(status, 200)
        client = created["data"]["client"]
        self.assertEqual(client["phone"], "+7 900 100-00-01")
        self.assertEqual(
            client["phones"],
            ["+7 900 100-00-01", "+7 900 100-00-02", "+7 900 100-00-03"],
        )

        status, found = self.request("/api/search_clients", {"query": "79001000003"})
        self.assertEqual(status, 200)
        self.assertEqual(found["data"]["clients"][0]["id"], client["id"])

    def test_client_vehicle_routes_link_specific_vehicle(self) -> None:
        status, created_client = self.request(
            "/api/create_client",
            {
                "display_name": "API клиент с автопарком",
                "phone": "+7 913 888-99-00",
                "vehicles": [
                    {
                        "vehicle": "Toyota Prado 2017",
                        "brand": "Toyota",
                        "model": "Prado",
                        "vin": "JTEBU3FJX05027767",
                        "license_plate": "Р888РО124",
                        "year": "2017",
                    }
                ],
            },
        )
        self.assertEqual(status, 200)
        client = created_client["data"]["client"]
        vehicle_id = client["vehicles"][0]["id"]

        status, created_card = self.request(
            "/api/create_card",
            {
                "title": "API выбор машины",
                "vehicle_profile": {"customer_phone": "+7 913 888-99-00"},
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created_card["data"]["card"]["id"]

        status, linked = self.request(
            "/api/link_card_to_client",
            {
                "card_id": card_id,
                "client_id": client["id"],
                "client_vehicle_id": vehicle_id,
                "sync_vehicle_fields": True,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(linked["data"]["card"]["client_vehicle_id"], vehicle_id)
        self.assertEqual(linked["data"]["card"]["vehicle_profile"]["vin"], "JTEBU3FJX05027767")

        status, found = self.request("/api/search_clients", {"query": "Р888РО124", "limit": 5})
        self.assertEqual(status, 200)
        self.assertEqual(found["data"]["clients"][0]["vehicles_preview"][0]["id"], vehicle_id)

        status, upserted = self.request(
            "/api/upsert_client_vehicle",
            {
                "client_id": client["id"],
                "client_vehicle_id": vehicle_id,
                "vehicle": {"vin": "JTEBU3FJX05999999", "license_plate": "Р999РО124"},
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(upserted["data"]["meta"]["changed"])
        self.assertEqual(upserted["data"]["vehicle"]["vin"], "JTEBU3FJX05999999")
        self.assertIn(card_id, upserted["data"]["meta"]["synced_card_ids"])

        status, synced_card = self.request("/api/get_card", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertEqual(
            synced_card["data"]["card"]["vehicle_profile"]["registration_plate"],
            "р999ро124",
        )

        status, deleted_vehicle = self.request(
            "/api/delete_client_vehicle",
            {
                "client_id": client["id"],
                "client_vehicle_id": vehicle_id,
                "unlink_cards": True,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted_vehicle["data"]["meta"]["deleted"])
        self.assertEqual(deleted_vehicle["data"]["meta"]["linked_cards_unlinked"], 1)

        status, unlinked_card = self.request("/api/get_card", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertEqual(unlinked_card["data"]["card"]["client_vehicle_id"], "")

    def test_get_repair_order_creates_it_lazily_on_first_open(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "KIA RIO",
                "title": "Ленивый заказ-наряд",
                "description": "Первый вход",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "registration_plate": "А123АА124",
                    "vin": "KNADN512BD6123456",
                    "mileage": 120000,
                },
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, listed_before = self.request("/api/list_repair_orders", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(listed_before["data"]["meta"]["total"], 0)

        status, fetched = self.request(
            "/api/get_repair_order",
            {"card_id": card_id, "create_if_missing": True},
        )
        self.assertEqual(status, 200)
        self.assertTrue(fetched["data"]["meta"]["has_any_data"])
        self.assertTrue(fetched["data"]["meta"]["created"])
        self.assertEqual(fetched["data"]["repair_order"]["reason"], "Ленивый заказ-наряд")
        self.assertEqual(fetched["data"]["repair_order"]["license_plate"], "а123аа124")
        self.assertEqual(fetched["data"]["repair_order"]["vin"], "KNADN512BD6123456")
        self.assertEqual(fetched["data"]["repair_order"]["mileage"], "120000")
        self.assertEqual(fetched["data"]["card"]["repair_order"]["number"], "1")

        status, listed_after = self.request("/api/list_repair_orders", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(listed_after["data"]["meta"]["total"], 1)
        self.assertTrue(
            any(item["card_id"] == card_id for item in listed_after["data"]["repair_orders"])
        )

    def test_get_repair_order_does_not_mutate_without_explicit_create_flag(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "KIA RIO",
                "title": "Read-only заказ-наряд",
                "description": "Открытие без создания",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, fetched = self.request("/api/get_repair_order", {"card_id": card_id})

        self.assertEqual(status, 200)
        self.assertFalse(fetched["data"]["meta"]["has_any_data"])
        self.assertFalse(fetched["data"]["meta"]["created"])
        self.assertEqual(fetched["data"]["repair_order"]["number"], "")

        status, listed = self.request("/api/list_repair_orders", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(listed["data"]["meta"]["total"], 0)

    def test_cleanup_card_content_route_runs_local_cleanup(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "title": "Течь антифриза",
                "description": "Клиент: Иван Иванов\nТелефон: 89001112233\nVIN: WAUZZZ8V0JA000001\nПроверить радиатор",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, cleaned = self.request("/api/cleanup_card_content", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertTrue(cleaned["ok"])
        self.assertTrue(cleaned["data"]["meta"]["changed"])
        self.assertEqual(cleaned["data"]["meta"]["cleanup_mode"], "local_card_cleanup")
        self.assertTrue(cleaned["data"]["meta"]["verify"]["passed"])
        self.assertIn("СУТЬ", cleaned["data"]["card"]["description"])
        self.assertEqual(cleaned["data"]["card"]["vehicle_profile"]["customer_name"], "Иван Иванов")

    def test_agent_routes_and_full_enrichment_launch_when_agent_is_attached(self) -> None:
        agent_status_payload = {
            "agent": {
                "name": "AUTOSTOP SERVER AGENT",
                "enabled": True,
                "available": True,
                "ready": True,
                "availability_reason": "worker_running",
                "configured": True,
                "model": "gpt-test",
                "board_api_url": self.base_url,
            },
            "ai_remodel": {},
            "board_control": {},
            "worker": {
                "embedded": True,
                "running": True,
                "heartbeat_fresh": True,
            },
            "scheduler": {
                "last_run_at": "",
                "last_success_at": "",
                "last_error": "",
            },
            "status": {
                "running": True,
                "current_task_id": None,
                "current_run_id": None,
                "last_heartbeat": utc_now().isoformat(),
                "last_run_started_at": "",
                "last_run_finished_at": "",
                "last_error": "",
                "last_scheduler_run_at": "",
                "last_scheduler_success_at": "",
                "last_scheduler_error": "",
                "board_control": {},
            },
            "queue": {"pending_total": 0, "running_total": 0},
            "scheduled": {"total": 0, "active_total": 0, "paused_total": 0},
            "recent_runs": [],
        }
        agent_control = Mock()
        agent_control.agent_status.return_value = agent_status_payload
        agent_control.agent_tasks.return_value = {
            "tasks": [],
            "meta": {"limit": 20, "statuses": []},
        }
        agent_control.agent_actions.return_value = {
            "actions": [],
            "meta": {"limit": 80, "run_id": None, "task_id": None},
        }
        agent_control.agent_scheduled_tasks.return_value = {"tasks": [], "meta": {"total": 0}}
        agent_control.enqueue_card_autofill_task.return_value = {
            "id": "task-123",
            "created_at": utc_now().isoformat(),
            "status": "pending",
        }
        self.service.attach_agent_control(agent_control)

        status, agent_status = self.request("/api/agent_status", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(agent_status["ok"])
        self.assertTrue(agent_status["data"]["agent"]["enabled"])
        self.assertEqual(agent_status["data"]["agent"]["model"], "gpt-test")

        status, agent_tasks = self.request("/api/agent_tasks?limit=20", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(agent_tasks["ok"])
        self.assertEqual(agent_tasks["data"]["meta"]["limit"], 20)

        status, agent_scheduled = self.request("/api/agent_scheduled_tasks", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(agent_scheduled["ok"])

        status, created = self.request(
            "/api/create_card",
            {
                "title": "AI карточка",
                "description": "VIN: WAUZZZ8V0JA000001\nПроверить радиатор",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, launched = self.request(
            "/api/run_full_card_enrichment",
            {"card_id": card_id, "actor_name": "AI", "context_packet": {"kind": "compact_context"}},
        )
        self.assertEqual(status, 200)
        self.assertTrue(launched["ok"])
        self.assertTrue(launched["data"]["meta"]["launched"])
        self.assertEqual(launched["data"]["meta"]["task_id"], "task-123")
        self.assertEqual(launched["data"]["meta"]["scenario_id"], "full_card_enrichment")
        agent_control.enqueue_card_autofill_task.assert_called()
        payload = agent_control.enqueue_card_autofill_task.call_args.args[0]
        prompt_text = str(
            payload.get("task_text", payload.get("prompt", payload.get("ai_autofill_prompt", "")))
        )
        self.assertIn("полное заполнение", prompt_text.lower())
        self.assertIn("update_card", prompt_text)
        self.assertIn("update_repair_order", prompt_text)
        self.assertIn("replace_repair_order_works", prompt_text)
        self.assertIn("replace_repair_order_materials", prompt_text)
        self.assertEqual(payload["scenario_id"], "full_card_enrichment")
        self.assertEqual(
            agent_control.enqueue_card_autofill_task.call_args.kwargs["purpose"],
            "full_card_enrichment",
        )
        self.assertEqual(
            agent_control.enqueue_card_autofill_task.call_args.kwargs["source"],
            "ui_full_card_enrichment",
        )
        self.assertEqual(payload["vehicle"], created["data"]["card"]["vehicle"])

    def test_head_root_and_health_are_supported(self) -> None:
        parsed = urlsplit(self.base_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        try:
            connection.request("HEAD", "/")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")
            connection.close()

            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
            connection.request("HEAD", "/api/health")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")
            connection.close()

            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
            connection.request("HEAD", "/favicon.ico")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertGreater(int(response.getheader("Content-Length", "0")), 0)
            self.assertEqual(response.getheader("Content-Type"), "image/x-icon")
            self.assertEqual(response.read(), b"")

            connection.close()

            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
            connection.request("HEAD", "/favicon.png")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertGreater(int(response.getheader("Content-Length", "0")), 0)
            self.assertEqual(response.getheader("Content-Type"), "image/png")
            self.assertEqual(response.read(), b"")
        finally:
            connection.close()

    def test_favicon_routes_serve_brand_assets(self) -> None:
        parsed = urlsplit(self.base_url)
        cases = [
            ("/favicon.ico", "image/x-icon", b"\x00\x00\x01\x00"),
            ("/favicon.png", "image/png", b"\x89PNG\r\n\x1a\n"),
        ]
        for path, content_type, expected_prefix in cases:
            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
            try:
                connection.request("GET", path)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("Content-Type"), content_type)
                self.assertGreater(int(response.getheader("Content-Length", "0")), 0)
                body = response.read()
                self.assertGreater(len(body), 0)
                self.assertTrue(body.startswith(expected_prefix))
            finally:
                connection.close()

    def test_board_assets_are_fingerprinted_immutable_and_precompressed(self) -> None:
        cases = (
            (BOARD_WEB_APP_CSS_PATH, "text/css; charset=utf-8", BOARD_WEB_APP_CSS),
            (
                BOARD_WEB_APP_JS_PATH,
                "application/javascript; charset=utf-8",
                BOARD_WEB_APP_JS,
            ),
        )
        for path, content_type, text in cases:
            expected = text.encode("utf-8")
            with self.subTest(path=path, encoding="identity"):
                status, headers, body = self.raw_request(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("Content-Type"), content_type)
                self.assertEqual(
                    headers.get("Cache-Control"),
                    "public, max-age=31536000, immutable",
                )
                self.assertEqual(headers.get("Vary"), "Accept-Encoding")
                self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
                self.assertNotIn("Content-Encoding", headers)
                self.assertEqual(body, expected)

            with self.subTest(path=path, encoding="gzip"):
                status, headers, body = self.raw_request(
                    path,
                    headers={"Accept-Encoding": "gzip"},
                )
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("Content-Encoding"), "gzip")
                self.assertEqual(headers.get("Vary"), "Accept-Encoding")
                self.assertEqual(gzip.decompress(body), expected)

            with self.subTest(path=path, method="HEAD"):
                status, headers, body = self.raw_request(
                    path,
                    method="HEAD",
                    headers={"Accept-Encoding": "gzip"},
                )
                self.assertEqual(status, 200)
                self.assertEqual(body, b"")
                self.assertEqual(headers.get("Content-Encoding"), "gzip")
                self.assertEqual(
                    int(headers.get("Content-Length", "0")),
                    len(gzip.compress(expected, mtime=0)),
                )

    def test_unknown_board_asset_hash_is_not_served(self) -> None:
        known_assets = dict(api_server_module._BOARD_ASSETS)
        for index in range(1000):
            self.assertIsNone(api_server_module._board_asset_bytes(f"/assets/unknown-{index}"))
        self.assertEqual(api_server_module._BOARD_ASSETS, known_assets)

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.raw_request(f"{BOARD_WEB_APP_JS_PATH}.stale")

        self.assertEqual(raised.exception.code, 404)

    def test_board_client_disconnect_is_handled_without_server_error(self) -> None:
        parsed = urlsplit(self.base_url)
        server = self.server._server
        self.assertIsNotNone(server)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            reset_on_close = struct.pack("ii", 1, 0)
            for _ in range(8):
                with socket.create_connection((parsed.hostname, parsed.port), timeout=5) as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, reset_on_close)
                    sock.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            time.sleep(0.5)
        self.assertNotIn("Exception occurred during processing", stderr.getvalue())
        self.assertNotIn("BrokenPipeError", stderr.getvalue())
        self.assertNotIn("ConnectionResetError", stderr.getvalue())

    def test_board_html_uses_cached_gzip_when_client_accepts_it(self) -> None:
        status, headers, body = self.raw_request("/", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertEqual(headers.get("Vary"), "Accept-Encoding")
        decoded = gzip.decompress(body).decode("utf-8")
        self.assertEqual(decoded, BOARD_WEB_APP_HTML)
        self.assertLess(len(body), len(decoded.encode("utf-8")) // 4)

    def test_html_gzip_negotiation_honors_quality_values_and_exact_tokens(self) -> None:
        rejected_encodings = (
            "gzip;q=0",
            "br, gzip ; q=0.000",
            "gzip;q=0, *;q=1",
            "notgzip",
        )
        accepted_encodings = (
            "br;q=1, gzip;q=0.25",
            "GZip ; q=1",
            "*;q=0.5",
        )

        for route in ("/", "/dashboard"):
            for accept_encoding in rejected_encodings:
                with self.subTest(route=route, accept_encoding=accept_encoding):
                    status, headers, body = self.raw_request(
                        route,
                        headers={"Accept-Encoding": accept_encoding},
                    )

                    self.assertEqual(status, 200)
                    self.assertNotIn("Content-Encoding", headers)
                    self.assertIn("<!doctype html>", body.decode("utf-8").lower())

            for accept_encoding in accepted_encodings:
                with self.subTest(route=route, accept_encoding=accept_encoding):
                    status, headers, body = self.raw_request(
                        route,
                        headers={"Accept-Encoding": accept_encoding},
                    )

                    self.assertEqual(status, 200)
                    self.assertEqual(headers.get("Content-Encoding"), "gzip")
                    self.assertIn("<!doctype html>", gzip.decompress(body).decode("utf-8").lower())

    def test_review_board_route_returns_summary(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Review route", "description": "For review_board", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)

        status, review = self.request("/api/review_board", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(review["ok"])
        self.assertIn("summary", review["data"])
        self.assertIn("by_column", review["data"])
        self.assertIn("alerts", review["data"])
        self.assertIn("priority_cards", review["data"])
        self.assertIn("recent_events", review["data"])
        self.assertGreaterEqual(review["data"]["summary"]["active_cards"], 1)
        self.assertIn("[BOARD REVIEW]", review["data"]["text"])

    def test_password_verifier_rejects_excessive_iterations_without_hashing(self) -> None:
        excessive_hash = f"pbkdf2_sha256${PASSWORD_HASH_MAX_ITERATIONS + 1}$salt$deadbeef"
        huge_hash = f"pbkdf2_sha256${'9' * 128}$salt$deadbeef"

        with patch("minimal_kanban.operator_auth.hashlib.pbkdf2_hmac") as pbkdf2_hmac:
            self.assertFalse(_verify_password("admin", excessive_hash))
            self.assertFalse(_verify_password("admin", huge_hash))

        pbkdf2_hmac.assert_not_called()

    def test_operator_login_profile_and_admin_user_management(self) -> None:
        status, logged_in = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(status, 200)
        token = logged_in["data"]["session"]["token"]
        headers = {"X-Operator-Session": token}

        status, profile = self.request("/api/get_operator_profile", method="GET", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(profile["data"]["user"]["username"], "ADMIN")
        self.assertTrue(profile["data"]["user"]["is_admin"])
        self.assertTrue(profile["data"]["security"]["using_default_admin_credentials"])
        self.assertIn(
            "MINIMAL_KANBAN_DEFAULT_ADMIN_PASSWORD", profile["data"]["security"]["warning"]
        )

        status, saved = self.request(
            "/api/save_operator_user",
            {"username": "mekh", "password": "1234", "role": "admin"},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(saved["data"]["meta"]["created"])
        self.assertEqual(saved["data"]["user"]["username"], "MEKH")
        self.assertEqual(saved["data"]["user"]["role"], "admin")

        status, promoted = self.request(
            "/api/save_operator_user",
            {"username": "mekh", "role": "operator"},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(promoted["data"]["user"]["role"], "operator")

        status, listed = self.request("/api/list_operator_users", method="GET", headers=headers)
        self.assertEqual(status, 200)
        self.assertTrue(any(item["username"] == "MEKH" for item in listed["data"]["users"]))

        status, deleted = self.request(
            "/api/delete_operator_user",
            {"username": "mekh"},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["data"]["deleted"])

    def test_personal_board_preferences_are_private_to_the_human_operator_session(self) -> None:
        preferences = {
            "extra_column": {
                "is_open": True,
                "filter": {"tag_label": "срочно", "tag_color": "yellow"},
            }
        }
        unauthenticated_status, unauthenticated = self.request(
            "/api/update_personal_board_preferences",
            {"board_preferences": preferences},
        )
        self.assertEqual(unauthenticated_status, 401)
        self.assertEqual(unauthenticated["error"]["code"], "unauthorized")

        admin_status, admin_login = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(admin_status, 200)
        admin_headers = {"X-Operator-Session": admin_login["data"]["session"]["token"]}
        default_profile_status, default_profile = self.request(
            "/api/get_operator_profile", method="GET", headers=admin_headers
        )
        self.assertEqual(default_profile_status, 200)
        self.assertEqual(
            {
                "extra_column": {
                    "is_open": False,
                    "is_detached": False,
                    "position": {"x": 0, "y": 0},
                    "filter": {"tag_label": "НАДО ЧТО ТО СДЕЛАТЬ", "tag_color": "red"},
                }
            },
            default_profile["data"]["board_preferences"],
        )

        created_status, _ = self.request(
            "/api/save_operator_user",
            {"username": "personal", "password": "personal-password-71A9"},
            headers=admin_headers,
        )
        self.assertEqual(created_status, 200)
        personal_status, personal_login = self.request(
            "/api/login_operator",
            {"username": "personal", "password": "personal-password-71A9"},
        )
        self.assertEqual(personal_status, 200)
        personal_headers = {"X-Operator-Session": personal_login["data"]["session"]["token"]}
        before_update_list_status, before_update_list = self.request(
            "/api/list_operator_users", method="GET", headers=admin_headers
        )
        self.assertEqual(before_update_list_status, 200)
        personal_updated_at_before = next(
            user["updated_at"]
            for user in before_update_list["data"]["users"]
            if user["username"] == "PERSONAL"
        )

        marker = Path(self.temp_dir.name) / ".agent-gateway-maintenance"
        marker.touch()
        with patch.dict(os.environ, {"AUTOSTOP_MAINTENANCE_MARKER": str(marker)}):
            saved_status, saved = self.request(
                "/api/update_personal_board_preferences",
                {"board_preferences": preferences},
                headers=personal_headers,
            )
        self.assertEqual(saved_status, 200)
        self.assertTrue(saved["data"]["meta"]["changed"])
        expected_preferences = {
            "extra_column": {
                "is_open": True,
                "is_detached": False,
                "position": {"x": 0, "y": 0},
                "filter": {"tag_label": "СРОЧНО", "tag_color": "yellow"},
            }
        }
        self.assertEqual(expected_preferences, saved["data"]["board_preferences"])

        profile_status, profile = self.request(
            "/api/get_operator_profile", method="GET", headers=personal_headers
        )
        self.assertEqual(profile_status, 200)
        self.assertEqual(expected_preferences, profile["data"]["board_preferences"])

        admin_profile_status, admin_profile = self.request(
            "/api/get_operator_profile", method="GET", headers=admin_headers
        )
        self.assertEqual(admin_profile_status, 200)
        self.assertFalse(admin_profile["data"]["board_preferences"]["extra_column"]["is_open"])
        list_status, listed = self.request(
            "/api/list_operator_users", method="GET", headers=admin_headers
        )
        self.assertEqual(list_status, 200)
        personal_user = next(
            user for user in listed["data"]["users"] if user["username"] == "PERSONAL"
        )
        self.assertNotIn("board_preferences", personal_user)
        self.assertEqual(personal_updated_at_before, personal_user["updated_at"])

        invalid_status, invalid = self.request(
            "/api/update_personal_board_preferences",
            {
                "board_preferences": {
                    "extra_column": {
                        "is_open": True,
                        "filter": {"tag_label": "СРОЧНО", "tag_color": "blue"},
                    }
                }
            },
            headers=personal_headers,
        )
        self.assertEqual(invalid_status, 400)
        self.assertEqual(invalid["error"]["code"], "validation_error")

        gateway_token = "agent-service-token-with-strong-test-entropy-0123456789"
        gateway_env = {
            "AUTOSTOP_DEPLOYMENT_ENV": "development",
            "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
            "AUTOSTOP_AGENT_SERVICE_IDENTITY": "personal",
            "MINIMAL_KANBAN_MCP_BEARER_TOKEN": gateway_token,
        }
        with patch.dict(os.environ, gateway_env, clear=False):
            agent_profile_status, agent_profile_denied = self.request(
                "/api/get_operator_profile",
                {"source": "mcp_agent_gateway_v2"},
                headers={
                    "X-Autostop-Agent-Identity": "personal",
                    "X-Autostop-Agent-Token": gateway_token,
                },
            )
            agent_status, agent_denied = self.request(
                "/api/update_personal_board_preferences",
                {"board_preferences": preferences, "source": "mcp_agent_gateway_v2"},
                headers={
                    "X-Autostop-Agent-Identity": "personal",
                    "X-Autostop-Agent-Token": gateway_token,
                },
            )
        self.assertEqual(agent_profile_status, 403)
        self.assertEqual(agent_profile_denied["error"]["code"], "forbidden")
        self.assertEqual(agent_profile_denied["error"]["details"]["auth_type"], "operator_session")
        self.assertEqual(agent_status, 403)
        self.assertEqual(agent_denied["error"]["code"], "forbidden")

    def test_local_agent_service_identity_can_use_admin_route_without_human_session(self) -> None:
        token = "agent-service-token-with-strong-test-entropy-0123456789"
        gateway_env = {
            "AUTOSTOP_DEPLOYMENT_ENV": "development",
            "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
            "AUTOSTOP_AGENT_SERVICE_IDENTITY": "codex-owner-agent",
            "MINIMAL_KANBAN_MCP_BEARER_TOKEN": token,
        }
        payload = {
            "username": "agentmade",
            "password": "1234",
            "source": "mcp_agent_gateway_v2",
        }
        headers = {
            "X-Autostop-Agent-Identity": "codex-owner-agent",
            "X-Autostop-Agent-Token": token,
        }
        with patch.dict("os.environ", gateway_env, clear=False):
            wrong_status, _ = self.request(
                "/api/save_operator_user",
                payload,
                headers={**headers, "X-Autostop-Agent-Token": "wrong"},
            )
            proxied_status, _ = self.request(
                "/api/save_operator_user",
                payload,
                headers={**headers, "X-Forwarded-For": "203.0.113.10"},
            )
            status, saved = self.request(
                "/api/save_operator_user",
                payload,
                headers=headers,
            )

        self.assertEqual(wrong_status, 401)
        self.assertEqual(proxied_status, 401)
        self.assertEqual(status, 200)
        self.assertEqual(saved["data"]["user"]["username"], "AGENTMADE")

    def test_local_agent_oauth_audit_actor_is_signed_and_must_remain_admin(self) -> None:
        login_status, admin_login = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(login_status, 200)
        admin_headers = {"X-Operator-Session": admin_login["data"]["session"]["token"]}
        save_status, saved = self.request(
            "/api/save_operator_user",
            {"username": "codex", "password": "test-password", "role": "admin"},
            headers=admin_headers,
        )
        self.assertEqual(save_status, 200)
        self.assertEqual(saved["data"]["user"]["username"], "CODEX")

        token = "agent-service-token-with-strong-test-entropy-0123456789"
        gateway_env = {
            "AUTOSTOP_DEPLOYMENT_ENV": "development",
            "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
            "AUTOSTOP_AGENT_SERVICE_IDENTITY": "codex-owner-agent",
            "AUTOSTOP_MCP_OAUTH_STATE_KEY": ("MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="),
            "MINIMAL_KANBAN_MCP_BEARER_TOKEN": token,
        }
        payload = {
            "title": "OAuth audit actor",
            "deadline": {"hours": 1},
            "source": "mcp_agent_gateway_v2",
            "actor_name": "CODEX",
        }
        with patch.dict("os.environ", gateway_env, clear=False):
            assertion = create_oauth_audit_assertion(
                subject="CODEX",
                method="POST",
                route="/api/create_card",
                payload=payload,
            )
            headers = {
                "X-Autostop-Agent-Identity": "codex-owner-agent",
                "X-Autostop-Agent-Token": token,
                OAUTH_AUDIT_ACTOR_HEADER: "CODEX",
                OAUTH_AUDIT_ASSERTION_HEADER: assertion,
            }
            created_status, created = self.request("/api/create_card", payload, headers=headers)
            invalid_status, invalid = self.request(
                "/api/create_card",
                payload,
                headers={**headers, OAUTH_AUDIT_ASSERTION_HEADER: "invalid"},
            )
            demote_status, _ = self.request(
                "/api/save_operator_user",
                {"username": "CODEX", "role": "operator"},
                headers=admin_headers,
            )
            demoted_status, demoted = self.request("/api/create_card", payload, headers=headers)

        self.assertEqual(created_status, 200)
        card_id = created["data"]["card"]["id"]
        log_status, log = self.request(f"/api/get_card_log?card_id={card_id}&limit=1", method="GET")
        self.assertEqual(log_status, 200)
        self.assertEqual(log["data"]["events"][0]["actor_name"], "CODEX")
        self.assertEqual(invalid_status, 401)
        self.assertEqual(invalid["error"]["code"], "unauthorized")
        self.assertEqual(demote_status, 200)
        self.assertEqual(demoted_status, 401)
        self.assertEqual(demoted["error"]["code"], "unauthorized")

    def test_local_agent_service_identity_can_open_card_and_read_dashboard_with_audit(self) -> None:
        create_status, created = self.request(
            "/api/create_card",
            {"title": "Agent parity open", "deadline": {"hours": 1}},
        )
        self.assertEqual(create_status, 200)
        card_id = created["data"]["card"]["id"]
        token = "agent-service-token-with-strong-test-entropy-0123456789"
        gateway_env = {
            "AUTOSTOP_DEPLOYMENT_ENV": "development",
            "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
            "AUTOSTOP_AGENT_SERVICE_IDENTITY": "codex-owner-agent",
            "MINIMAL_KANBAN_MCP_BEARER_TOKEN": token,
        }
        headers = {
            "X-Autostop-Agent-Identity": "codex-owner-agent",
            "X-Autostop-Agent-Token": token,
        }
        with patch.dict("os.environ", gateway_env, clear=False):
            open_status, opened = self.request(
                "/api/open_card",
                {
                    "card_id": card_id,
                    "return_card": False,
                    "mark_seen": False,
                    "source": "mcp_agent_gateway_v2",
                },
                headers=headers,
            )
            dashboard_status, dashboard = self.request(
                "/api/get_display_dashboard",
                {"source": "mcp_agent_gateway_v2"},
                headers=headers,
            )
            activity_status, activity = self.request(
                "/api/list_operator_activity",
                {
                    "action": "card_opened",
                    "source": "mcp_agent_gateway_v2",
                    "query": card_id,
                    "limit": 10,
                },
                headers=headers,
            )

        self.assertEqual(open_status, 200)
        self.assertEqual(opened["data"]["card_id"], card_id)
        self.assertEqual(dashboard_status, 200)
        self.assertTrue(dashboard["data"]["generated_at"])
        self.assertEqual(activity_status, 200)
        row = activity["data"]["activities"][0]
        self.assertEqual(row["object_id"], card_id)
        self.assertEqual(row["action"], "card_opened")
        self.assertEqual(row["source"], "mcp_agent_gateway_v2")
        self.assertEqual(row["username"], "CODEX-OWNER-AGENT")

    def test_local_agent_read_identity_stays_available_when_writes_are_disabled(self) -> None:
        create_status, created = self.request(
            "/api/create_card",
            {"title": "Read-only agent parity", "deadline": {"hours": 1}},
        )
        self.assertEqual(create_status, 200)
        token = "agent-service-token-with-strong-test-entropy-0123456789"
        gateway_env = {
            "AUTOSTOP_DEPLOYMENT_ENV": "development",
            "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "0",
            "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
            "AUTOSTOP_AGENT_SERVICE_IDENTITY": "codex-owner-agent",
            "MINIMAL_KANBAN_MCP_BEARER_TOKEN": token,
        }
        headers = {
            "X-Autostop-Agent-Identity": "codex-owner-agent",
            "X-Autostop-Agent-Token": token,
        }
        with patch.dict("os.environ", gateway_env, clear=False):
            dashboard_status, _ = self.request(
                "/api/get_display_dashboard",
                {"source": "mcp_agent_gateway_v2"},
                headers=headers,
            )
            open_status, blocked = self.request(
                "/api/open_card",
                {
                    "card_id": created["data"]["card"]["id"],
                    "source": "mcp_agent_gateway_v2",
                },
                headers=headers,
            )

        self.assertEqual(dashboard_status, 200)
        self.assertEqual(open_status, 401)
        self.assertEqual(blocked["error"]["code"], "unauthorized")

    def test_operator_password_update_revokes_existing_sessions(self) -> None:
        status, admin_login = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(status, 200)
        admin_headers = {"X-Operator-Session": admin_login["data"]["session"]["token"]}

        status, saved = self.request(
            "/api/save_operator_user",
            {"username": "parts", "password": "1234"},
            headers=admin_headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(saved["data"]["meta"]["created"])

        status, parts_login = self.request(
            "/api/login_operator",
            {"username": "parts", "password": "1234"},
        )
        self.assertEqual(status, 200)
        parts_headers = {"X-Operator-Session": parts_login["data"]["session"]["token"]}
        status, profile = self.request(
            "/api/get_operator_profile",
            method="GET",
            headers=parts_headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(profile["data"]["user"]["username"], "PARTS")

        status, updated = self.request(
            "/api/save_operator_user",
            {"username": "parts", "password": "5678"},
            headers=admin_headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(updated["data"]["meta"]["updated"])

        status, revoked = self.request(
            "/api/get_operator_profile",
            method="GET",
            headers=parts_headers,
        )
        self.assertEqual(status, 401)
        self.assertEqual(revoked["error"]["details"]["auth_type"], "operator_session")

        status, old_password = self.request(
            "/api/login_operator",
            {"username": "parts", "password": "1234"},
        )
        self.assertEqual(status, 401)
        status, new_password = self.request(
            "/api/login_operator",
            {"username": "parts", "password": "5678"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(new_password["data"]["user"]["username"], "PARTS")

    def test_operator_auth_backs_up_non_object_users_file_before_bootstrap(self) -> None:
        self.users_file.write_text("[]", encoding="utf-8")

        state = self.operator_service._read_normalized_state()

        backup = self.users_file.with_suffix(".corrupted.json")
        self.assertEqual(backup.read_text(encoding="utf-8"), "[]")
        self.assertNotEqual(self.users_file.read_text(encoding="utf-8"), "[]")
        self.assertTrue(any(user["role"] == "admin" for user in state["users"]))

    def test_operator_auth_backs_up_nonstandard_json_constants_before_bootstrap(self) -> None:
        self.users_file.write_text(
            '{"users":[{"username":"ADMIN","stats":{"cards_opened":NaN}}]}',
            encoding="utf-8",
        )

        state = self.operator_service._read_normalized_state()

        backup = self.users_file.with_suffix(".corrupted.json")
        self.assertIn("NaN", backup.read_text(encoding="utf-8"))
        self.assertNotIn("NaN", self.users_file.read_text(encoding="utf-8"))
        self.assertTrue(any(user["role"] == "admin" for user in state["users"]))

    def test_operator_auth_backs_up_partially_corrupted_users_before_normalizing(self) -> None:
        payload = json.loads(self.users_file.read_text(encoding="utf-8"))
        payload["users"].append({"username": "DAMAGED", "role": "operator"})
        original = json.dumps(payload, ensure_ascii=False)
        self.users_file.write_text(original, encoding="utf-8")

        state = self.operator_service._read_normalized_state()

        backup = self.users_file.with_suffix(".corrupted.json")
        self.assertEqual(backup.read_text(encoding="utf-8"), original)
        self.assertNotIn("DAMAGED", self.users_file.read_text(encoding="utf-8"))
        self.assertNotIn("DAMAGED", [user["username"] for user in state["users"]])
        self.assertTrue(any(user["role"] == "admin" for user in state["users"]))

    def test_operator_auth_clamps_oversized_open_count_stat(self) -> None:
        users = self.operator_service._normalize_users(
            [
                {
                    "username": "admin",
                    "password_hash": _password_hash("admin123"),
                    "role": "admin",
                    "stats": {"cards_opened": 1e308},
                }
            ]
        )

        self.assertEqual(users[0]["stats"]["cards_opened"], 1_000_000_000)

    def test_operator_auth_backs_up_oversized_users_file_before_bootstrap(self) -> None:
        self.users_file.write_text(
            json.dumps({"users": [], "padding": "x" * 1024}),
            encoding="utf-8",
        )

        with patch("minimal_kanban.operator_auth.OPERATOR_AUTH_STATE_MAX_BYTES", 640):
            state = self.operator_service._read_normalized_state()

        backup = self.users_file.with_suffix(".corrupted.json")
        self.assertIn("padding", backup.read_text(encoding="utf-8"))
        self.assertNotIn("padding", self.users_file.read_text(encoding="utf-8"))
        self.assertTrue(any(user["role"] == "admin" for user in state["users"]))

    def test_operator_auth_rejects_oversized_state_write_without_clobbering_users_file(
        self,
    ) -> None:
        original = self.users_file.read_text(encoding="utf-8")
        oversized_state = self.operator_service._read_normalized_state()
        oversized_state["users"][0]["action_history"] = [
            {"timestamp": utc_now().isoformat(), "action": "card_opened", "object_id": "x" * 512}
        ]

        with patch("minimal_kanban.operator_auth.OPERATOR_AUTH_STATE_MAX_BYTES", 128):
            with self.assertRaisesRegex(ValueError, "operator users file is too large"):
                self.operator_service._write_state(oversized_state)

        self.assertEqual(self.users_file.read_text(encoding="utf-8"), original)
        self.assertEqual(list(self.users_file.parent.glob(f".{self.users_file.name}.*.tmp")), [])

    def test_operator_auth_backs_up_deeply_nested_users_file_before_bootstrap(self) -> None:
        deep_json = "[" * 5000 + "]" * 5000
        self.users_file.write_text(deep_json, encoding="utf-8")

        state = self.operator_service._read_normalized_state()

        backup = self.users_file.with_suffix(".corrupted.json")
        self.assertEqual(backup.read_text(encoding="utf-8"), deep_json)
        self.assertTrue(any(user["role"] == "admin" for user in state["users"]))

    def test_operator_auth_blocks_insecure_default_admin_bootstrap_on_default_users_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as auth_dir:
            users_file = Path(auth_dir) / "users.json"
            logger = logging.getLogger(f"test.api.{self._testMethodName}.operator_auth")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            with (
                patch("minimal_kanban.operator_auth.get_app_data_dir", return_value=Path(auth_dir)),
                patch("minimal_kanban.operator_auth.get_users_file", return_value=users_file),
                patch.dict(
                    os.environ,
                    {
                        "MINIMAL_KANBAN_DEFAULT_ADMIN_USERNAME": "admin",
                        "MINIMAL_KANBAN_DEFAULT_ADMIN_PASSWORD": "admin",
                    },
                    clear=False,
                ),
            ):
                operator_service = OperatorAuthService(
                    self.store,
                    self.service,
                    activity_service=OperatorActivityService(
                        activity_dir=Path(auth_dir) / "operator-activity",
                        logger=logger,
                    ),
                    logger=logger,
                )

                with self.assertRaises(ServiceError) as denied:
                    operator_service.login({"username": "admin", "password": "admin"})

            self.assertEqual(denied.exception.status_code, 401)
            self.assertEqual(denied.exception.code, "unauthorized")
            self.assertTrue(users_file.exists())

    def test_strong_configured_default_admin_password_is_not_flagged_as_insecure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as auth_dir:
            users_file = Path(auth_dir) / "users.json"
            logger = logging.getLogger(f"test.api.{self._testMethodName}.operator_auth")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            strong_password = "Admin-2026-Strong-Local-Only"
            with (
                patch("minimal_kanban.operator_auth.get_app_data_dir", return_value=Path(auth_dir)),
                patch("minimal_kanban.operator_auth.get_users_file", return_value=users_file),
                patch.dict(
                    os.environ,
                    {
                        "MINIMAL_KANBAN_DEFAULT_ADMIN_USERNAME": "admin",
                        "MINIMAL_KANBAN_DEFAULT_ADMIN_PASSWORD": strong_password,
                    },
                    clear=False,
                ),
            ):
                operator_service = OperatorAuthService(
                    self.store,
                    self.service,
                    activity_service=OperatorActivityService(
                        activity_dir=Path(auth_dir) / "operator-activity",
                        logger=logger,
                    ),
                    logger=logger,
                )

                logged_in = operator_service.login(
                    {"username": "admin", "password": strong_password}
                )

                self.assertEqual(logged_in["user"]["username"], "ADMIN")
                self.assertFalse(logged_in["security"]["using_default_admin_credentials"])
                self.assertEqual(logged_in["security"]["warning"], "")

    def test_operator_user_employee_binding_controls_material_default_executor(self) -> None:
        status, logged_in = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(status, 200)
        headers = {"X-Operator-Session": logged_in["data"]["session"]["token"]}
        employee = self.service.save_employee({"name": "Иван Снабженец", "position": "Снабженец"})[
            "employee"
        ]

        status, saved = self.request(
            "/api/save_operator_user",
            {"username": "parts", "password": "1234"},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved["data"]["user"]["employee_id"], "")

        status, bound = self.request(
            "/api/set_operator_user_employee",
            {"username": "parts", "employee_id": employee["id"]},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(bound["data"]["meta"]["bound"])
        self.assertEqual(bound["data"]["user"]["employee_id"], employee["id"])

        status, listed = self.request("/api/list_operator_users", method="GET", headers=headers)
        self.assertEqual(status, 200)
        parts_user = next(item for item in listed["data"]["users"] if item["username"] == "PARTS")
        self.assertEqual(parts_user["employee_id"], employee["id"])

        status, parts_login = self.request(
            "/api/login_operator",
            {"username": "parts", "password": "1234"},
        )
        self.assertEqual(status, 200)
        parts_headers = {"X-Operator-Session": parts_login["data"]["session"]["token"]}
        status, profile = self.request(
            "/api/get_operator_profile", method="GET", headers=parts_headers
        )
        self.assertEqual(status, 200)
        self.assertEqual(profile["data"]["user"]["employee_id"], employee["id"])

        status, unbound = self.request(
            "/api/set_operator_user_employee",
            {"username": "parts", "employee_id": ""},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertFalse(unbound["data"]["meta"]["bound"])
        self.assertEqual(unbound["data"]["user"]["employee_id"], "")

    def test_operator_user_employee_binding_rejects_duplicate_missing_and_inactive(self) -> None:
        logged_in = self.operator_service.login({"username": "admin", "password": "admin"})
        session = logged_in["session"]
        employee = self.service.save_employee({"name": "Мария Снабженец", "position": "Снабженец"})[
            "employee"
        ]
        disabled_employee = self.service.save_employee(
            {"name": "Сергей Архив", "position": "Мастер"}
        )["employee"]
        self.service.toggle_employee({"employee_id": disabled_employee["id"]})
        self.operator_service.save_user(
            {"_operator_session": session, "username": "katya", "password": "1234"}
        )
        self.operator_service.save_user(
            {"_operator_session": session, "username": "maria", "password": "1234"}
        )
        self.operator_service.set_user_employee(
            {"_operator_session": session, "username": "katya", "employee_id": employee["id"]}
        )

        with self.assertRaises(ServiceError) as duplicate:
            self.operator_service.set_user_employee(
                {
                    "_operator_session": session,
                    "username": "maria",
                    "employee_id": employee["id"],
                }
            )
        self.assertEqual(duplicate.exception.status_code, 409)
        self.assertEqual(duplicate.exception.details["username"], "KATYA")

        with self.assertRaises(ServiceError) as missing:
            self.operator_service.set_user_employee(
                {"_operator_session": session, "username": "maria", "employee_id": "missing"}
            )
        self.assertEqual(missing.exception.status_code, 404)

        with self.assertRaises(ServiceError) as inactive:
            self.operator_service.set_user_employee(
                {
                    "_operator_session": session,
                    "username": "maria",
                    "employee_id": disabled_employee["id"],
                }
            )
        self.assertEqual(inactive.exception.status_code, 409)

    def test_ui_write_routes_require_operator_session(self) -> None:
        status, blocked = self.request(
            "/api/create_column",
            {"label": "Protected", "source": "ui"},
        )
        self.assertEqual(status, 401)
        self.assertEqual(blocked["error"]["details"]["auth_type"], "operator_session")

        status, logged_in = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(status, 200)
        token = logged_in["data"]["session"]["token"]

        status, created = self.request(
            "/api/create_column",
            {"label": "Protected", "source": "ui"},
            headers={"X-Operator-Session": token},
        )
        self.assertEqual(status, 200)
        self.assertEqual(created["data"]["column"]["label"], "Protected")

    def test_proxied_write_routes_require_operator_session(self) -> None:
        proxy_headers = {"X-Forwarded-For": "203.0.113.10"}

        status, blocked = self.request(
            "/api/create_sticky",
            {"text": "Proxy write", "x": 1, "y": 1, "deadline": {"hours": 1}},
            headers=proxy_headers,
        )
        self.assertEqual(status, 401)
        self.assertEqual(blocked["error"]["details"]["auth_type"], "operator_session")

        status, blocked_transfer = self.request(
            "/api/create_cashbox_transfer",
            {
                "from_cashbox_id": "CB1",
                "to_cashbox_id": "CB2",
                "amount": "100",
                "actor_name": "AUDIT",
            },
            headers=proxy_headers,
        )
        self.assertEqual(status, 401)
        self.assertEqual(blocked_transfer["error"]["details"]["auth_type"], "operator_session")

        status, blocked_employee_delete = self.request(
            "/api/delete_employee",
            {"employee_id": "employee-1", "actor_name": "AUDIT"},
            headers=proxy_headers,
        )
        self.assertEqual(status, 401)
        self.assertEqual(
            blocked_employee_delete["error"]["details"]["auth_type"], "operator_session"
        )

    def test_proxied_read_routes_require_operator_session(self) -> None:
        proxy_headers = {"X-Forwarded-For": "203.0.113.10"}

        status, blocked = self.request(
            "/api/get_cards",
            method="GET",
            headers=proxy_headers,
        )
        self.assertEqual(status, 401)
        self.assertEqual(blocked["error"]["details"]["auth_type"], "operator_session")

        status, blocked_post = self.request(
            "/api/get_cards",
            {},
            headers=proxy_headers,
        )
        self.assertEqual(status, 401)
        self.assertEqual(blocked_post["error"]["details"]["auth_type"], "operator_session")

        status, logged_in = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
            headers=proxy_headers,
        )
        self.assertEqual(status, 200)
        token = logged_in["data"]["session"]["token"]

        status, allowed = self.request(
            "/api/get_cards",
            method="GET",
            headers={**proxy_headers, "X-Operator-Session": token},
        )
        self.assertEqual(status, 200)
        self.assertTrue(allowed["ok"])

        status, allowed_post = self.request(
            "/api/get_cards",
            {},
            headers={**proxy_headers, "X-Operator-Session": token},
        )
        self.assertEqual(status, 200)
        self.assertTrue(allowed_post["ok"])

        status, local_allowed = self.request("/api/get_cards", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(local_allowed["ok"])

        status, card = self.request(
            "/api/create_card",
            {"title": "Proxy order", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        status, blocked_repair_order_open = self.request(
            "/api/get_repair_order",
            {"card_id": card["data"]["card"]["id"], "create_if_missing": True},
            headers=proxy_headers,
        )
        self.assertEqual(status, 401)
        self.assertEqual(
            blocked_repair_order_open["error"]["details"]["auth_type"], "operator_session"
        )

        status, logged_in = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(status, 200)
        token = logged_in["data"]["session"]["token"]

        status, created = self.request(
            "/api/create_sticky",
            {"text": "Proxy write", "x": 1, "y": 1, "deadline": {"hours": 1}},
            headers={**proxy_headers, "X-Operator-Session": token},
        )
        self.assertEqual(status, 200)
        self.assertEqual(created["data"]["sticky"]["text"], "Proxy write")

    def test_proxied_data_routes_fail_closed_without_operator_service(self) -> None:
        self.server.stop()
        logger = logging.getLogger(f"test.api.no_operator_service.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.server = ApiServer(
            self.service,
            logger,
            start_port=0,
            fallback_limit=TEST_API_PORT_FALLBACK_LIMIT,
            bearer_token="",
        )
        self.server.start()
        self.port = self.server.port
        self.base_url = self.server.base_url

        local_status, local_payload = self.request("/api/get_cards", method="GET")
        self.assertEqual(local_status, 200)
        self.assertTrue(local_payload["ok"])

        proxy_headers = {"X-Forwarded-For": "203.0.113.10"}
        cases = (
            ("GET", "/api/get_cards", None),
            ("POST", "/api/get_cards", {}),
            ("GET", "/api/shared_file?file_id=missing", None),
        )
        for method, path, payload in cases:
            with self.subTest(method=method, path=path):
                status, blocked = self.request(
                    path,
                    payload,
                    method=method,
                    headers=proxy_headers,
                )
                self.assertEqual(status, 503)
                self.assertEqual(blocked["error"]["code"], "operator_auth_unavailable")
                self.assertEqual(blocked["error"]["details"]["auth_type"], "operator_session")

    def test_proxied_post_auth_preflight_precedes_json_and_size_checks(self) -> None:
        proxy_headers = {
            "Content-Type": "application/json",
            "X-Forwarded-For": "203.0.113.10",
        }
        cases = (
            ("malformed", b"{not-json", contextlib.nullcontext()),
            (
                "oversized",
                b'{"padding":"' + (b"x" * 4096) + b'"}',
                patch("minimal_kanban.api.server.MAX_JSON_BODY_BYTES", 64),
            ),
        )

        for label, body, size_limit in cases:
            with (
                self.subTest(label=label),
                size_limit,
                patch(
                    "minimal_kanban.api.server.json.loads",
                    side_effect=AssertionError("anonymous proxied body reached JSON parser"),
                ) as json_loads,
            ):
                status = None
                response_body = None
                for attempt in range(3):
                    connection = http.client.HTTPConnection(
                        "127.0.0.1", self.port, timeout=TEST_HTTP_TIMEOUT_SECONDS
                    )
                    try:
                        connection.request(
                            "POST",
                            "/api/get_cards",
                            body=body,
                            headers={**proxy_headers, "Content-Length": str(len(body))},
                        )
                        response = connection.getresponse()
                        status = response.status
                        response_body = response.read()
                        break
                    except (
                        TimeoutError,
                        ConnectionAbortedError,
                        ConnectionResetError,
                    ) as exc:
                        if attempt + 1 >= 3 or not is_transient_request_error(exc):
                            raise
                        time.sleep(0.05)
                    finally:
                        connection.close()

                json_loads.assert_not_called()

            self.assertIsNotNone(status)
            self.assertIsNotNone(response_body)
            blocked = json.loads(response_body.decode("utf-8"))
            self.assertEqual(status, 401)
            self.assertEqual(blocked["error"]["code"], "unauthorized")
            self.assertEqual(blocked["error"]["details"]["auth_type"], "operator_session")

    def test_proxied_binary_routes_require_operator_session(self) -> None:
        proxy_headers = {"X-Forwarded-For": "203.0.113.10"}
        status, created = self.request(
            "/api/create_card",
            {"title": "Protected attachment", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        private_content = b"private attachment"
        status, uploaded = self.request(
            "/api/add_card_attachment",
            {
                "card_id": card_id,
                "file_name": "private.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(private_content).decode("ascii"),
            },
        )
        self.assertEqual(status, 200)
        attachment_id = uploaded["data"]["attachment"]["id"]
        path = f"/api/attachment?card_id={card_id}&attachment_id={attachment_id}"

        protected_paths = (
            path,
            "/api/shared_file?file_id=missing",
            "/api/repair_order_text?card_id=missing",
            "/employee_salary_reconciliation_print?employee_id=missing",
        )
        for protected_path in protected_paths:
            with self.subTest(protected_path=protected_path):
                blocked_request = urllib.request.Request(
                    f"{self.base_url}{protected_path}",
                    headers=proxy_headers,
                    method="GET",
                )
                with self.assertRaises(urllib.error.HTTPError) as blocked:
                    urllib.request.urlopen(blocked_request, timeout=5)
                try:
                    self.assertEqual(blocked.exception.code, 401)
                    payload = json.loads(blocked.exception.read().decode("utf-8"))
                finally:
                    blocked.exception.close()
                self.assertEqual(payload["error"]["details"]["auth_type"], "operator_session")

        status, logged_in = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
            headers=proxy_headers,
        )
        self.assertEqual(status, 200)
        token = logged_in["data"]["session"]["token"]
        allowed_request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={**proxy_headers, "X-Operator-Session": token},
            method="GET",
        )
        with urllib.request.urlopen(allowed_request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), private_content)

    def test_update_board_settings_route_is_not_exposed_via_get(self) -> None:
        status, response = self.request("/api/update_board_settings?board_scale=1.25", method="GET")
        self.assertEqual(status, 404)
        self.assertEqual(response["error"]["code"], "not_found")

    def test_post_request_rejects_oversized_json_body_before_dispatch(self) -> None:
        with patch("minimal_kanban.api.server.MAX_JSON_BODY_BYTES", 32, create=True):
            status, response = self.request(
                "/api/get_board_revision",
                {"padding": "x" * 80},
            )

        self.assertEqual(status, 413)
        self.assertEqual(response["error"]["code"], "request_too_large")
        self.assertEqual(response["error"]["details"]["max_size_bytes"], 32)

    def test_get_request_rejects_oversized_query_before_dispatch(self) -> None:
        with patch("minimal_kanban.api.server.MAX_QUERY_STRING_BYTES", 8):
            status, response = self.request(
                "/api/get_board_revision?padding=xxxxxxxx",
                method="GET",
            )

        self.assertEqual(status, 414)
        self.assertEqual(response["error"]["code"], "request_too_large")
        self.assertEqual(response["error"]["details"]["max_size_bytes"], 8)

    def test_get_request_rejects_too_many_query_fields_before_dispatch(self) -> None:
        with patch("minimal_kanban.api.server.MAX_QUERY_FIELDS", 1):
            status, response = self.request(
                "/api/get_board_revision?a=1&b=2",
                method="GET",
            )

        self.assertEqual(status, 414)
        self.assertEqual(response["error"]["code"], "request_too_large")
        self.assertEqual(response["error"]["details"]["max_fields"], 1)

    def test_post_request_rejects_negative_content_length(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.putrequest("POST", "/api/get_board_revision")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", "-1")
            connection.endheaders()
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "validation_error")

    def test_post_request_rejects_non_utf8_json_body(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(
                "POST",
                "/api/get_board_revision",
                body=b"\xff",
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_json")

    def test_post_request_rejects_deeply_nested_json_body_without_disconnect(self) -> None:
        raw_body = ("[" * 5000 + "]" * 5000).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(
                "POST",
                "/api/get_board_revision",
                body=raw_body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_json")

    def test_post_request_rejects_non_standard_json_numbers(self) -> None:
        status, response = self.request("/api/get_board_revision", {"bad": float("nan")})

        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "invalid_json")

    def test_open_card_updates_operator_opened_counter(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Tracked open", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, logged_in = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(status, 200)
        token = logged_in["data"]["session"]["token"]
        headers = {"X-Operator-Session": token}

        status, opened = self.request(
            "/api/open_card",
            {"card_id": card_id},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(opened["data"]["card"]["id"], card_id)

        status, profile = self.request("/api/get_operator_profile", method="GET", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(profile["data"]["stats"]["cards_opened"], 1)

        with patch.object(
            self.service, "mark_card_seen", wraps=self.service.mark_card_seen
        ) as mark_seen:
            status, lightweight_opened = self.request(
                "/api/open_card",
                {"card_id": card_id, "return_card": False, "mark_seen": False},
                headers=headers,
            )

        self.assertEqual(status, 200)
        self.assertEqual(lightweight_opened["data"]["card_id"], card_id)
        self.assertTrue(lightweight_opened["data"]["opened"])
        self.assertFalse(lightweight_opened["data"]["meta"]["return_card"])
        self.assertFalse(lightweight_opened["data"]["meta"]["mark_seen"])
        self.assertNotIn("card", lightweight_opened["data"])
        self.assertEqual(mark_seen.call_count, 0)

        status, profile = self.request("/api/get_operator_profile", method="GET", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(profile["data"]["stats"]["cards_opened"], 2)

    def test_open_card_requires_operator_session(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Tracked open", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, blocked = self.request("/api/open_card", {"card_id": card_id})
        self.assertEqual(status, 401)
        self.assertEqual(blocked["error"]["code"], "unauthorized")
        self.assertEqual(blocked["error"]["details"]["auth_type"], "operator_session")

    def test_operator_activity_route_scopes_non_admin_to_own_rows(self) -> None:
        status, admin_login = self.request(
            "/api/login_operator", {"username": "admin", "password": "admin"}
        )
        self.assertEqual(status, 200)
        admin_headers = {"X-Operator-Session": admin_login["data"]["session"]["token"]}

        status, _ = self.request(
            "/api/save_operator_user",
            {"username": "worker", "password": "1234"},
            headers=admin_headers,
        )
        self.assertEqual(status, 200)

        status, worker_login = self.request(
            "/api/login_operator", {"username": "worker", "password": "1234"}
        )
        self.assertEqual(status, 200)
        worker_headers = {"X-Operator-Session": worker_login["data"]["session"]["token"]}

        status, admin_rows = self.request(
            "/api/list_operator_activity?limit=50", method="GET", headers=admin_headers
        )
        self.assertEqual(status, 200)
        admin_usernames = {row["username"] for row in admin_rows["data"]["activities"]}
        self.assertIn("ADMIN", admin_usernames)
        self.assertIn("WORKER", admin_usernames)

        status, worker_rows = self.request(
            "/api/list_operator_activity?limit=50", method="GET", headers=worker_headers
        )
        self.assertEqual(status, 200)
        self.assertTrue(worker_rows["data"]["activities"])
        self.assertEqual(
            {row["username"] for row in worker_rows["data"]["activities"]},
            {"WORKER"},
        )

        status, forbidden = self.request(
            "/api/list_operator_activity?username=ADMIN", method="GET", headers=worker_headers
        )
        self.assertEqual(status, 403)
        self.assertEqual(forbidden["error"]["code"], "forbidden")

    def test_operator_activity_details_scope_and_missing_archive_are_safe(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Details scope card", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, admin_login = self.request(
            "/api/login_operator", {"username": "admin", "password": "admin"}
        )
        self.assertEqual(status, 200)
        admin_headers = {"X-Operator-Session": admin_login["data"]["session"]["token"]}

        status, _ = self.request(
            "/api/save_operator_user",
            {"username": "worker", "password": "1234"},
            headers=admin_headers,
        )
        self.assertEqual(status, 200)
        status, worker_login = self.request(
            "/api/login_operator", {"username": "worker", "password": "1234"}
        )
        self.assertEqual(status, 200)
        worker_headers = {"X-Operator-Session": worker_login["data"]["session"]["token"]}

        status, opened = self.request(
            "/api/open_card",
            {"card_id": card_id, "return_card": False, "mark_seen": False},
            headers=admin_headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(opened["data"]["opened"])

        status, activity = self.request(
            "/api/list_operator_activity?action=card_opened&username=ADMIN",
            method="GET",
            headers=admin_headers,
        )
        self.assertEqual(status, 200)
        activity_id = activity["data"]["activities"][0]["id"]

        status, forbidden = self.request(
            f"/api/get_operator_activity_details?activity_id={activity_id}",
            method="GET",
            headers=worker_headers,
        )
        self.assertEqual(status, 403)
        self.assertEqual(forbidden["error"]["code"], "forbidden")

        status, details = self.request(
            f"/api/get_operator_activity_details?activity_id={activity_id}",
            method="GET",
            headers=admin_headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(details["data"]["details"]["card_id"], card_id)

        activity_dir = self.operator_service._activity_service.activity_dir
        for detail_file in (activity_dir / "details").glob("*.jsonl"):
            detail_file.unlink()

        status, missing_details = self.request(
            f"/api/get_operator_activity_details?activity_id={activity_id}",
            method="GET",
            headers=admin_headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(missing_details["data"]["details"], {})

    def test_open_card_records_operator_activity_row(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Tracked activity open", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        card = created["data"]["card"]

        status, logged_in = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(status, 200)
        headers = {"X-Operator-Session": logged_in["data"]["session"]["token"]}

        status, opened = self.request(
            "/api/open_card",
            {"card_id": card["id"], "return_card": False, "mark_seen": False},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(opened["data"]["opened"])

        status, activity = self.request(
            "/api/list_operator_activity?action=card_opened", method="GET", headers=headers
        )
        self.assertEqual(status, 200)
        rows = activity["data"]["activities"]
        self.assertEqual(rows[0]["username"], "ADMIN")
        self.assertEqual(rows[0]["object_id"], card["id"])
        self.assertEqual(rows[0]["object_label"], card["title"])
        self.assertEqual(rows[0]["summary"], "Просмотр без изменения данных")

    def test_admin_user_report_uses_last_15_days_window(self) -> None:
        status, logged_in = self.request(
            "/api/login_operator", {"username": "admin", "password": "admin"}
        )
        self.assertEqual(status, 200)
        token = logged_in["data"]["session"]["token"]
        headers = {"X-Operator-Session": token}

        status, saved = self.request(
            "/api/save_operator_user",
            {"username": "worker", "password": "1234"},
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved["data"]["user"]["role"], "operator")

        users_state = self.operator_service._read_normalized_state()
        worker = self.operator_service._find_user(users_state["users"], "WORKER")
        self.assertIsNotNone(worker)
        worker["action_history"] = [
            {
                "timestamp": (utc_now() - timedelta(days=2)).isoformat(),
                "action": "card_opened",
                "message": "Открыл карточку.",
                "card_id": "recent-card",
            },
            {
                "timestamp": (utc_now() - timedelta(days=20)).isoformat(),
                "action": "card_opened",
                "message": "Старое открытие.",
                "card_id": "old-card",
            },
        ]
        self.operator_service._write_state(users_state)

        bundle = self.store.read_bundle()
        bundle["events"].append(
            AuditEvent(
                id="recent-move",
                timestamp=(utc_now() - timedelta(days=1)).isoformat(),
                actor_name="WORKER",
                source="ui",
                action="card_moved",
                message="Переместил карточку.",
                card_id="recent-card",
                details={},
            )
        )
        bundle["events"].append(
            AuditEvent(
                id="old-archive",
                timestamp=(utc_now() - timedelta(days=25)).isoformat(),
                actor_name="WORKER",
                source="ui",
                action="card_archived",
                message="Старое архивирование.",
                card_id="old-card",
                details={},
            )
        )
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        status, listed = self.request("/api/list_operator_users", method="GET", headers=headers)
        self.assertEqual(status, 200)
        worker_row = next(item for item in listed["data"]["users"] if item["username"] == "WORKER")
        self.assertEqual(worker_row["stats"]["cards_opened"], 1)
        self.assertEqual(worker_row["stats"]["card_moves"], 1)
        self.assertEqual(worker_row["stats"]["cards_archived"], 0)

        status, report = self.request(
            "/api/get_operator_user_report?username=worker", method="GET", headers=headers
        )
        self.assertEqual(status, 200)
        self.assertEqual(report["data"]["meta"]["window_days"], 15)
        text = report["data"]["text"]
        self.assertIn("последние 15 дней", text)
        self.assertIn("Переместил карточку.", text)
        self.assertIn("Открыл карточку.", text)
        self.assertNotIn("Старое архивирование.", text)
        self.assertNotIn("Старое открытие.", text)

    def test_snapshot_marks_card_as_updated_for_viewer_after_other_operator_edit(self) -> None:
        status, admin_login = self.request(
            "/api/login_operator", {"username": "admin", "password": "admin"}
        )
        self.assertEqual(status, 200)
        admin_token = admin_login["data"]["session"]["token"]
        admin_headers = {"X-Operator-Session": admin_token}

        status, _ = self.request(
            "/api/save_operator_user",
            {"username": "worker", "password": "1234"},
            headers=admin_headers,
        )
        self.assertEqual(status, 200)

        status, worker_login = self.request(
            "/api/login_operator", {"username": "worker", "password": "1234"}
        )
        self.assertEqual(status, 200)
        worker_headers = {"X-Operator-Session": worker_login["data"]["session"]["token"]}

        status, created = self.request(
            "/api/create_card",
            {"title": "Updated badge", "description": "Initial", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, opened = self.request("/api/open_card", {"card_id": card_id}, headers=admin_headers)
        self.assertEqual(status, 200)
        self.assertFalse(opened["data"]["card"]["has_unseen_update"])

        status, updated = self.request(
            "/api/update_card",
            {"card_id": card_id, "description": "Worker updated card"},
            headers=worker_headers,
        )
        self.assertEqual(status, 200)
        self.assertFalse(updated["data"]["card"]["has_unseen_update"])

        status, snapshot = self.request(
            "/api/get_board_snapshot", method="GET", headers=admin_headers
        )
        self.assertEqual(status, 200)
        admin_card = next(card for card in snapshot["data"]["cards"] if card["id"] == card_id)
        self.assertTrue(admin_card["has_unseen_update"])
        self.assertFalse(admin_card["is_unread"])

        status, marked = self.request(
            "/api/mark_card_seen", {"card_id": card_id}, headers=admin_headers
        )
        self.assertEqual(status, 200)
        self.assertFalse(marked["data"]["card"]["has_unseen_update"])

    def test_snapshot_does_not_mark_timer_only_update_as_unseen_for_viewer(self) -> None:
        status, admin_login = self.request(
            "/api/login_operator", {"username": "admin", "password": "admin"}
        )
        self.assertEqual(status, 200)
        admin_headers = {"X-Operator-Session": admin_login["data"]["session"]["token"]}

        status, _ = self.request(
            "/api/save_operator_user",
            {"username": "worker", "password": "1234"},
            headers=admin_headers,
        )
        self.assertEqual(status, 200)

        status, worker_login = self.request(
            "/api/login_operator", {"username": "worker", "password": "1234"}
        )
        self.assertEqual(status, 200)
        worker_headers = {"X-Operator-Session": worker_login["data"]["session"]["token"]}

        status, created = self.request(
            "/api/create_card",
            {
                "title": "Timer-only API",
                "description": "Initial",
                "deadline": {"hours": 1},
                "source": "ui",
            },
            headers=admin_headers,
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, opened = self.request(
            "/api/open_card", {"card_id": card_id}, headers=worker_headers
        )
        self.assertEqual(status, 200)
        self.assertFalse(opened["data"]["card"]["is_unread"])
        before_updated_at = opened["data"]["card"]["updated_at"]

        status, before_revision = self.request(
            "/api/get_board_revision?compact=1&include_archive=0",
            method="GET",
            headers=worker_headers,
        )
        self.assertEqual(status, 200)

        status, updated = self.request(
            "/api/update_card",
            {"card_id": card_id, "deadline": {"hours": 4}, "source": "ui"},
            headers=admin_headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["meta"]["changed_fields"], ["deadline"])
        self.assertNotEqual(updated["data"]["card"]["updated_at"], before_updated_at)

        status, after_revision = self.request(
            "/api/get_board_revision?compact=1&include_archive=0",
            method="GET",
            headers=worker_headers,
        )
        self.assertEqual(status, 200)
        self.assertNotEqual(after_revision["data"]["revision"], before_revision["data"]["revision"])

        status, snapshot = self.request(
            "/api/get_board_snapshot?compact=1&include_archive=0",
            method="GET",
            headers=worker_headers,
        )
        self.assertEqual(status, 200)
        worker_card = next(card for card in snapshot["data"]["cards"] if card["id"] == card_id)
        self.assertFalse(worker_card["is_unread"])
        self.assertFalse(worker_card["has_unseen_update"])

    def test_ui_created_card_is_unread_for_other_operator_sessions(self) -> None:
        status, admin_login = self.request(
            "/api/login_operator", {"username": "admin", "password": "admin"}
        )
        self.assertEqual(status, 200)
        admin_headers = {"X-Operator-Session": admin_login["data"]["session"]["token"]}

        status, _ = self.request(
            "/api/save_operator_user",
            {"username": "worker", "password": "1234"},
            headers=admin_headers,
        )
        self.assertEqual(status, 200)

        status, worker_login = self.request(
            "/api/login_operator", {"username": "worker", "password": "1234"}
        )
        self.assertEqual(status, 200)
        worker_headers = {"X-Operator-Session": worker_login["data"]["session"]["token"]}

        status, created = self.request(
            "/api/create_card",
            {
                "title": "UI unread",
                "description": "Created by admin",
                "deadline": {"hours": 1},
                "source": "ui",
            },
            headers=admin_headers,
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        self.assertFalse(created["data"]["card"]["is_unread"])

        status, admin_snapshot = self.request(
            "/api/get_board_snapshot", method="GET", headers=admin_headers
        )
        self.assertEqual(status, 200)
        admin_card = next(card for card in admin_snapshot["data"]["cards"] if card["id"] == card_id)
        self.assertFalse(admin_card["is_unread"])

        status, worker_snapshot = self.request(
            "/api/get_board_snapshot", method="GET", headers=worker_headers
        )
        self.assertEqual(status, 200)
        worker_card = next(
            card for card in worker_snapshot["data"]["cards"] if card["id"] == card_id
        )
        self.assertTrue(worker_card["is_unread"])

        status, marked = self.request(
            "/api/mark_card_seen", {"card_id": card_id}, headers=worker_headers
        )
        self.assertEqual(status, 200)
        self.assertTrue(marked["data"]["meta"]["changed"])
        self.assertFalse(marked["data"]["card"]["is_unread"])

        status, worker_snapshot = self.request(
            "/api/get_board_snapshot", method="GET", headers=worker_headers
        )
        self.assertEqual(status, 200)
        worker_card = next(
            card for card in worker_snapshot["data"]["cards"] if card["id"] == card_id
        )
        self.assertFalse(worker_card["is_unread"])

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "description": "Worker saved after seeing",
                "source": "ui",
            },
            headers=worker_headers,
        )
        self.assertEqual(status, 200)
        self.assertFalse(updated["data"]["card"]["is_unread"])
        self.assertFalse(updated["data"]["card"]["has_unseen_update"])

        columns = worker_snapshot["data"]["columns"]
        current_column = updated["data"]["card"]["column"]
        target_column = next(column["id"] for column in columns if column["id"] != current_column)
        status, moved = self.request(
            "/api/move_card",
            {"card_id": card_id, "column": target_column, "source": "ui"},
            headers=worker_headers,
        )
        self.assertEqual(status, 200)
        self.assertFalse(moved["data"]["card"]["is_unread"])
        self.assertFalse(moved["data"]["card"]["has_unseen_update"])
        moved_affected = next(
            card for card in moved["data"]["affected_cards"] if card["id"] == card_id
        )
        self.assertFalse(moved_affected["is_unread"])
        self.assertFalse(moved_affected["has_unseen_update"])

        status, admin_updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "description": "Admin changed after worker saw it",
                "source": "ui",
            },
            headers=admin_headers,
        )
        self.assertEqual(status, 200)
        self.assertFalse(admin_updated["data"]["card"]["has_unseen_update"])

        status, worker_snapshot = self.request(
            "/api/get_board_snapshot", method="GET", headers=worker_headers
        )
        self.assertEqual(status, 200)
        worker_card = next(
            card for card in worker_snapshot["data"]["cards"] if card["id"] == card_id
        )
        self.assertFalse(worker_card["is_unread"])
        self.assertTrue(worker_card["has_unseen_update"])

    def test_operator_user_listing_reads_board_bundle_once(self) -> None:
        logged_in = self.operator_service.login({"username": "admin", "password": "admin"})
        session = logged_in["session"]
        self.operator_service.save_user(
            {
                "_operator_session": session,
                "username": "mekh",
                "password": "1234",
            }
        )
        bundle = self.store.read_bundle()
        self.store.read_bundle = Mock(return_value=bundle)
        self.operator_service._build_event_activity_index = Mock(
            wraps=self.operator_service._build_event_activity_index
        )

        listed = self.operator_service.list_users({"_operator_session": session})

        self.assertEqual(listed["meta"]["total"], 2)
        self.assertEqual(self.store.read_bundle.call_count, 1)
        self.assertEqual(self.operator_service._build_event_activity_index.call_count, 1)

    def test_default_admin_accepts_admin_password_and_migrates_legacy_hash(self) -> None:
        state = self.operator_service._read_normalized_state()
        admin_user = next(user for user in state["users"] if user["username"] == "ADMIN")
        admin_user["password_hash"] = _password_hash("admin123")
        self.operator_service._write_state(state)

        logged_in = self.operator_service.login({"username": "admin", "password": "admin"})

        self.assertEqual(logged_in["user"]["username"], "ADMIN")
        migrated_state = self.operator_service._read_normalized_state()
        migrated_admin = next(
            user for user in migrated_state["users"] if user["username"] == "ADMIN"
        )
        self.assertNotEqual(migrated_admin["password_hash"], admin_user["password_hash"])

    def test_create_column_move_card_and_update_deadline(self) -> None:
        status, created_column = self.request("/api/create_column", {"label": "Блокеры"})
        self.assertEqual(status, 200)
        self.assertTrue(created_column["ok"])
        column_id = created_column["data"]["column"]["id"]

        status, columns = self.request("/api/list_columns", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(any(column["id"] == column_id for column in columns["data"]["columns"]))

        status, created_card = self.request(
            "/api/create_card",
            {
                "title": "Карточка в новом столбце",
                "column": column_id,
                "deadline": {"days": 0, "hours": 6},
            },
        )
        self.assertEqual(status, 200)
        card_id = created_card["data"]["card"]["id"]
        self.assertEqual(created_card["data"]["card"]["column"], column_id)

        status, updated = self.request(
            "/api/set_card_deadline",
            {"card_id": card_id, "deadline": {"days": 0, "hours": 0, "minutes": 1}},
        )
        self.assertEqual(status, 200)
        self.assertLessEqual(updated["data"]["card"]["remaining_seconds"], 60)

        status, yellow = self.request(
            "/api/set_card_indicator", {"card_id": card_id, "indicator": "yellow"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(yellow["data"]["card"]["indicator"], "yellow")
        self.assertEqual(yellow["data"]["card"]["status"], "warning")

        status, red = self.request(
            "/api/set_card_indicator", {"card_id": card_id, "indicator": "red"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(red["data"]["card"]["status"], "expired")

        status, summary = self.request(
            "/api/set_card_board_summary",
            {
                "card_id": card_id,
                "summary": "Что сейчас: проверить карточку.\nСтадия: в работе.\nСледующее действие: согласовать.",
                "actor_name": "AI",
                "source": "mcp",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(summary["data"]["card"]["board_summary_source"], "mcp")
        self.assertFalse(summary["data"]["card"]["board_summary_stale"])
        self.assertIn("Что сейчас", summary["data"]["card"]["board_summary"])

        status, overdue = self.request("/api/list_overdue_cards", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(any(card["id"] == card_id for card in overdue["data"]["cards"]))

    def test_card_timer_starts_inactive_and_uses_explicit_start_stop_routes(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Карточка без таймера", "actor_name": "ALICE", "source": "api"},
        )
        self.assertEqual(status, 200)
        card = created["data"]["card"]
        self.assertEqual(card["timer_state"], "inactive")
        self.assertFalse(card["timer_active"])
        self.assertEqual(card["remaining_seconds"], 0)

        status, started = self.request(
            "/api/start_card_timer",
            {
                "card_id": card["id"],
                "deadline": {"hours": 2},
                "actor_name": "ALICE",
                "source": "api",
                "expected_updated_at": card["updated_at"],
            },
        )
        self.assertEqual(status, 200)
        running = started["data"]["card"]
        self.assertEqual(running["timer_state"], "running")
        self.assertTrue(running["timer_active"])
        self.assertGreater(running["remaining_seconds"], 0)

        status, stopped = self.request(
            "/api/stop_card_timer",
            {
                "card_id": card["id"],
                "actor_name": "ALICE",
                "source": "api",
                "expected_updated_at": running["updated_at"],
            },
        )
        self.assertEqual(status, 200)
        inactive = stopped["data"]["card"]
        self.assertEqual(inactive["timer_state"], "inactive")
        self.assertFalse(inactive["timer_active"])
        self.assertEqual(inactive["remaining_seconds"], 0)

    def test_create_column_accepts_name_alias(self) -> None:
        status, created_column = self.request("/api/create_column", {"name": "Этап по имени"})
        self.assertEqual(status, 200)
        self.assertTrue(created_column["ok"])
        self.assertEqual(created_column["data"]["column"]["label"], "Этап по имени")

    def test_move_card_route_can_reorder_within_column(self) -> None:
        status, first = self.request(
            "/api/create_card", {"title": "First", "column": "inbox", "deadline": {"hours": 2}}
        )
        self.assertEqual(status, 200)
        status, second = self.request(
            "/api/create_card", {"title": "Second", "column": "inbox", "deadline": {"hours": 2}}
        )
        self.assertEqual(status, 200)
        status, third = self.request(
            "/api/create_card", {"title": "Third", "column": "inbox", "deadline": {"hours": 2}}
        )
        self.assertEqual(status, 200)

        status, moved = self.request(
            "/api/move_card",
            {
                "card_id": first["data"]["card"]["id"],
                "column": "inbox",
                "before_card_id": second["data"]["card"]["id"],
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(moved["ok"])
        self.assertEqual(moved["data"]["card"]["position"], 1)
        self.assertEqual(moved["data"]["affected_column_ids"], ["inbox"])
        self.assertEqual(
            [card["id"] for card in moved["data"]["affected_cards"][:3]],
            [
                third["data"]["card"]["id"],
                first["data"]["card"]["id"],
                second["data"]["card"]["id"],
            ],
        )
        self.assertTrue(all("repair_order" not in card for card in moved["data"]["affected_cards"]))
        self.assertTrue(moved["data"]["meta"]["changed"])

        status, snapshot = self.request("/api/get_board_snapshot", method="GET")
        self.assertEqual(status, 200)
        inbox_cards = sorted(
            [card for card in snapshot["data"]["cards"] if card["column"] == "inbox"],
            key=lambda item: item["position"],
        )
        self.assertEqual(
            [card["id"] for card in inbox_cards[:3]],
            [
                third["data"]["card"]["id"],
                first["data"]["card"]["id"],
                second["data"]["card"]["id"],
            ],
        )

    def test_move_card_route_supports_ui_delta_without_changing_legacy_default(self) -> None:
        _, source = self.request(
            "/api/create_card",
            {"title": "Delta API source", "column": "inbox", "deadline": {"hours": 1}},
        )
        _, target = self.request(
            "/api/create_card",
            {
                "title": "Delta API target",
                "column": "in_progress",
                "deadline": {"hours": 1},
            },
        )
        status, moved = self.request(
            "/api/move_card",
            {
                "card_id": source["data"]["card"]["id"],
                "column": "in_progress",
                "before_card_id": target["data"]["card"]["id"],
                "response_mode": "delta",
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(moved["data"]["meta"]["response_mode"], "delta")
        self.assertNotIn("affected_cards", moved["data"])
        self.assertEqual(
            moved["data"]["affected_columns"][1]["ordered_card_ids"][:2],
            [source["data"]["card"]["id"], target["data"]["card"]["id"]],
        )

    def test_cashbox_routes_create_list_transaction_get_and_delete(self) -> None:
        status, created = self.request(
            "/api/create_cashbox", {"name": "Касса 1", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(created["ok"])
        cashbox = created["data"]["cashbox"]

        status, listed = self.request("/api/list_cashboxes?limit=20", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(listed["data"]["meta"]["total"], 1)
        self.assertEqual(listed["data"]["cashboxes"][0]["id"], cashbox["id"])

        status, another_created = self.request(
            "/api/create_cashbox", {"name": "Касса 2", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        another_cashbox = another_created["data"]["cashbox"]

        status, first_page = self.request("/api/list_cashboxes?limit=1", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(first_page["data"]["meta"]["limit"], 1)
        self.assertEqual(first_page["data"]["meta"]["returned"], 1)
        self.assertTrue(first_page["data"]["meta"]["has_more"])

        status, reordered = self.request(
            "/api/reorder_cashboxes",
            {
                "cashbox_id": another_cashbox["id"],
                "before_cashbox_id": cashbox["id"],
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(reordered["data"]["meta"]["changed"])
        self.assertEqual(
            [item["id"] for item in reordered["data"]["cashboxes"][:2]],
            [another_cashbox["id"], cashbox["id"]],
        )

        status, transaction = self.request(
            "/api/create_cash_transaction",
            {
                "cashbox_id": cashbox["short_id"],
                "direction": "income",
                "amount": "2500",
                "note": "Оплата клиента",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(transaction["data"]["transaction"]["amount_minor"], 250000)

        destination_cashbox = another_cashbox

        status, transferred = self.request(
            "/api/create_cashbox_transfer",
            {
                "from_cashbox_id": cashbox["id"],
                "to_cashbox_id": destination_cashbox["short_id"],
                "amount": "500",
                "note": "Перевод на запас",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(transferred["data"]["source_transaction"]["direction"], "expense")
        self.assertEqual(transferred["data"]["target_transaction"]["direction"], "income")
        self.assertEqual(
            transferred["data"]["source_transaction"]["related_transaction_id"],
            transferred["data"]["target_transaction"]["id"],
        )
        self.assertTrue(transferred["data"]["source_transaction"]["transfer_group_id"])

        status, details = self.request(
            f"/api/get_cashbox?cashbox_id={cashbox['id']}&transaction_limit=10",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertEqual(details["data"]["cashbox"]["statistics"]["transactions_total"], 2)
        self.assertEqual(details["data"]["cashbox"]["statistics"]["balance_minor"], 200000)
        self.assertEqual(details["data"]["meta"]["transaction_offset"], 0)
        self.assertFalse(details["data"]["meta"]["has_more"])
        status, paged_details = self.request(
            f"/api/get_cashbox?cashbox_id={cashbox['id']}&transaction_limit=1&transaction_offset=1",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertEqual(paged_details["data"]["meta"]["transaction_offset"], 1)
        self.assertEqual(paged_details["data"]["meta"]["transactions_returned"], 1)
        self.assertFalse(paged_details["data"]["meta"]["has_more"])
        self.assertIn("Перемещение в Касса 2", details["data"]["transactions"][0]["note"])
        self.assertIn("business_date", details["data"]["transactions"][0])
        self.assertIn("business_time", details["data"]["transactions"][0])
        self.assertIn("business_datetime_display", details["data"]["transactions"][0])
        self.assertIn("link_status", details["data"]["transactions"][0])

        status, destination_details = self.request(
            f"/api/get_cashbox?cashbox_id={destination_cashbox['id']}&transaction_limit=10",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            destination_details["data"]["cashbox"]["statistics"]["transactions_total"], 1
        )
        self.assertEqual(
            destination_details["data"]["cashbox"]["statistics"]["balance_minor"], 50000
        )
        self.assertIn(
            "Перемещение из Касса 1", destination_details["data"]["transactions"][0]["note"]
        )

        status, deleted = self.request(
            "/api/delete_cashbox", {"cashbox_id": cashbox["id"], "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 400)
        self.assertFalse(deleted["ok"])
        self.assertIn("есть движения", deleted["error"]["message"])

        status, empty_created = self.request(
            "/api/create_cashbox", {"name": "Касса 3", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        empty_cashbox = empty_created["data"]["cashbox"]

        status, deleted = self.request(
            "/api/delete_cashbox", {"cashbox_id": empty_cashbox["id"], "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["data"]["meta"]["deleted"])

        status, journal = self.request("/api/get_cash_journal?months=3&limit=100", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(journal["ok"])
        self.assertEqual(journal["data"]["meta"]["schema_version"], "cash_journal.v2")
        self.assertIn("Кассовый журнал", journal["data"]["markdown"])
        self.assertEqual(journal["data"]["text"], journal["data"]["markdown"])
        self.assertIn("days", journal["data"])
        self.assertIn("weeks", journal["data"])
        self.assertIn("months", journal["data"])
        self.assertIn("totals", journal["data"])
        self.assertGreaterEqual(journal["data"]["meta"]["returned"], 1)
        self.assertIn("business_date", journal["data"]["entries"][0])
        self.assertIn("related_transaction_id", journal["data"]["entries"][0])

        status, compact_journal = self.request(
            "/api/get_cash_journal?months=3&limit=100&include_markdown=0&compact_groups=1",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertTrue(compact_journal["ok"])
        self.assertNotIn("markdown", compact_journal["data"])
        self.assertNotIn("text", compact_journal["data"])
        self.assertEqual(compact_journal["data"]["meta"]["format"], "json")
        self.assertFalse(compact_journal["data"]["meta"]["include_markdown"])
        self.assertTrue(compact_journal["data"]["meta"]["compact_groups"])
        self.assertIn("days", compact_journal["data"])
        self.assertIn("totals", compact_journal["data"])
        self.assertNotIn("entries", compact_journal["data"]["days"][0])

        status, audit = self.request("/api/finance_audit", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(audit["ok"])
        self.assertEqual(audit["data"]["meta"]["schema_version"], "finance_audit.v1")
        self.assertIn("issues", audit["data"])
        self.assertIn("counts_by_code", audit["data"]["summary"])

        status, number_audit = self.request("/api/repair_order_number_audit", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(number_audit["ok"])
        self.assertEqual(
            number_audit["data"]["meta"]["schema_version"],
            "repair_order_number_audit.v1",
        )
        self.assertTrue(number_audit["data"]["meta"]["read_only"])
        self.assertTrue(number_audit["data"]["meta"]["dry_run"])
        self.assertIn("issues", number_audit["data"])
        self.assertIn("counts_by_code", number_audit["data"]["summary"])

    def test_cashbox_notification_receipt_route_is_scoped_to_current_actor(self) -> None:
        status, created = self.request(
            "/api/create_cashbox", {"name": "Касса уведомлений", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        cashbox = created["data"]["cashbox"]

        status, baseline = self.request(
            "/api/mark_cashbox_notifications_seen", {"actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(baseline["data"]["notification"]["initialized"])

        status, movement = self.request(
            "/api/create_cash_transaction",
            {
                "cashbox_id": cashbox["id"],
                "direction": "expense",
                "amount": "700",
                "note": "Списание другого оператора",
                "actor_name": "БУХГАЛТЕР",
            },
        )
        self.assertEqual(status, 200)
        status, listed = self.request("/api/list_cashboxes?limit=20&actor_name=ADMIN", method="GET")
        self.assertEqual(status, 200)
        notice = listed["data"]["notification"]
        self.assertTrue(notice["has_unread"])
        self.assertEqual(notice["tone"], "expense")
        self.assertEqual(
            notice["unread_transactions"][0]["transaction_id"],
            movement["data"]["transaction"]["id"],
        )

        status, marked = self.request(
            "/api/mark_cashbox_notifications_seen",
            {
                "actor_name": "ADMIN",
                "through_transaction_id": notice["through_transaction_id"],
            },
        )
        self.assertEqual(status, 200)
        self.assertFalse(marked["data"]["notification"]["has_unread"])

        status, other_actor = self.request(
            "/api/list_cashboxes?limit=20&actor_name=DIRECTOR", method="GET"
        )
        self.assertEqual(status, 200)
        self.assertFalse(other_actor["data"]["notification"]["initialized"])

    def test_cancel_last_cash_transaction_route_removes_latest_manual_movement(self) -> None:
        status, created = self.request(
            "/api/create_cashbox", {"name": "Касса API", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        cashbox = created["data"]["cashbox"]
        status, first = self.request(
            "/api/create_cash_transaction",
            {
                "cashbox_id": cashbox["id"],
                "direction": "income",
                "amount": "1000",
                "note": "Старт",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        status, last = self.request(
            "/api/create_cash_transaction",
            {
                "cashbox_id": cashbox["id"],
                "direction": "expense",
                "amount": "300",
                "note": "Расход по кассе",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)

        status, cancelled = self.request(
            "/api/cancel_last_cash_transaction",
            {
                "cashbox_id": cashbox["id"],
                "transaction_id": last["data"]["transaction"]["id"],
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(cancelled["data"]["meta"]["cancelled"])
        self.assertEqual(
            cancelled["data"]["cancelled_transaction"]["id"], last["data"]["transaction"]["id"]
        )

        status, details = self.request(
            f"/api/get_cashbox?cashbox_id={cashbox['id']}&transaction_limit=10", method="GET"
        )
        self.assertEqual(status, 200)
        self.assertEqual(details["data"]["cashbox"]["statistics"]["transactions_total"], 1)
        self.assertEqual(
            details["data"]["transactions"][0]["id"], first["data"]["transaction"]["id"]
        )

    def test_create_cash_transaction_requires_note_for_expense(self) -> None:
        status, created = self.request(
            "/api/create_cashbox", {"name": "Касса API", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        cashbox = created["data"]["cashbox"]

        status, blocked = self.request(
            "/api/create_cash_transaction",
            {
                "cashbox_id": cashbox["id"],
                "direction": "expense",
                "amount": "300",
                "note": "Расход",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 400)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"]["code"], "validation_error")
        self.assertEqual(blocked["error"]["details"]["field"], "note")
        self.assertEqual(blocked["error"]["details"]["min_length"], 10)

        status, allowed = self.request(
            "/api/create_cash_transaction",
            {
                "cashbox_id": cashbox["id"],
                "direction": "expense",
                "amount": "300",
                "note": "Расход по кассе",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(allowed["data"]["transaction"]["note"], "Расход по кассе")

    def test_employee_salary_ledger_and_cash_routes_work_together(self) -> None:
        status, employee_saved = self.request(
            "/api/save_employee",
            {
                "name": "Сергей Электрик",
                "position": "Электрик",
                "salary_mode": "salary_plus_percent",
                "base_salary": "40000",
                "work_percent": "25",
            },
        )
        self.assertEqual(status, 200)
        employee = employee_saved["data"]["employee"]

        status, cashbox_created = self.request(
            "/api/create_cashbox", {"name": "Наличный", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        cashbox = cashbox_created["data"]["cashbox"]
        status, supplier_cashbox_created = self.request(
            "/api/create_cashbox", {"name": "Касса снабженца", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        supplier_cashbox = supplier_cashbox_created["data"]["cashbox"]

        status, card_created = self.request(
            "/api/create_card",
            {"vehicle": "Toyota Camry", "title": "Зарплатный наряд", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = card_created["data"]["card"]["id"]

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "201",
                    "status": "open",
                    "vehicle": "Toyota Camry",
                    "license_plate": "Т201ТС124",
                    "works": [
                        {
                            "name": "Замена генератора",
                            "quantity": "1",
                            "price": "8000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["card"]["repair_order"]["works"][0]["salary_amount"], "")

        status, paid = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "201",
                    "status": "open",
                    "vehicle": "Toyota Camry",
                    "license_plate": "Т201ТС124",
                    "payments": [
                        {
                            "amount": "8000",
                            "paid_at": "16.04.2026 12:00",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [
                        {
                            "name": "Замена генератора",
                            "quantity": "1",
                            "price": "8000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(paid["data"]["card"]["repair_order"]["payments"][0]["amount"], "8000")

        status, closed = self.request(
            "/api/set_repair_order_status", {"card_id": card_id, "status": "closed"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(closed["data"]["repair_order"]["works"][0]["salary_amount"], "2000")

        status, ledger = self.request(
            f"/api/get_employee_salary_ledger?employee_id={employee['id']}&months=6",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertEqual(ledger["data"]["balance_total"], "2000")
        self.assertTrue(any(row["kind"] == "accrual" for row in ledger["data"]["journal_rows"]))

        status, shift_accrual = self.request(
            "/api/create_employee_shift_accrual",
            {
                "employee_id": employee["id"],
                "amount": "3000",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(shift_accrual["data"]["accrual"]["employee_id"], employee["id"])
        self.assertEqual(shift_accrual["data"]["accrual"]["amount"], "3000")
        self.assertEqual(
            shift_accrual["data"]["accrual"]["note"],
            "Выплата за смены за текущую неделю",
        )

        status, payout = self.request(
            "/api/create_employee_salary_transaction",
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_payout",
                "amount": "2500",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payout["data"]["transaction"]["employee_id"], employee["id"])
        self.assertEqual(payout["data"]["transaction"]["transaction_kind"], "salary_payout")
        self.assertEqual(payout["data"]["transaction"]["cashbox_id"], cashbox["id"])

        status, advance = self.request(
            "/api/create_employee_salary_transaction",
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_advance",
                "amount": "500",
                "cashbox_id": supplier_cashbox["id"],
                "note": "Аванс: Командировка на выезд",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(advance["data"]["transaction"]["transaction_kind"], "salary_advance")
        self.assertEqual(advance["data"]["transaction"]["cashbox_id"], supplier_cashbox["id"])
        self.assertEqual(advance["data"]["transaction"]["note"], "Аванс: Командировка на выезд")

        status, ledger_after = self.request(
            f"/api/get_employee_salary_ledger?employee_id={employee['id']}&months=6",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertEqual(ledger_after["data"]["balance_total"], "2000")
        self.assertEqual(ledger_after["data"]["accrued_total"], "5000")
        self.assertEqual(ledger_after["data"]["payout_total"], "2500")
        self.assertEqual(ledger_after["data"]["advance_total"], "500")
        self.assertTrue(
            any(row["kind"] == "shift_accrual" for row in ledger_after["data"]["journal_rows"])
        )
        self.assertTrue(
            any(row["kind"] == "salary_payout" for row in ledger_after["data"]["journal_rows"])
        )
        self.assertTrue(
            any(row["kind"] == "salary_advance" for row in ledger_after["data"]["journal_rows"])
        )
        advance_row = next(
            row for row in ledger_after["data"]["journal_rows"] if row["kind"] == "salary_advance"
        )
        self.assertEqual(advance_row["note"], "Аванс: Командировка на выезд")

        closed_at = closed["data"]["repair_order"]["closed_at"]
        report_month = f"{closed_at[6:10]}-{closed_at[3:5]}"
        status, report = self.request(
            f"/api/get_employee_salary_report?employee_id={employee['id']}&month={report_month}",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertEqual(report["data"]["period"]["month"], report_month)
        self.assertEqual(report["data"]["meta"]["schema_version"], "employee_salary_report.v3")
        self.assertEqual(report["data"]["totals"]["repair_order_count"], 1)
        self.assertEqual(report["data"]["totals"]["work_count"], 1)
        self.assertEqual(report["data"]["totals"]["work_total"], "8000")
        self.assertEqual(report["data"]["totals"]["shift_accrual_count"], 1)
        self.assertEqual(report["data"]["totals"]["shift_accrual_total"], "3000")
        self.assertEqual(report["data"]["totals"]["accrued_total"], "5000")
        self.assertIn("ОТЧЕТ ПО НАЧИСЛЕНИЯМ", report["data"]["text"])
        self.assertIn("ЗН 201 | Toyota Camry | госномер: т201тс124", report["data"]["text"])
        self.assertIn("Замена генератора", report["data"]["text"])
        self.assertIn("Выплата за смены за текущую неделю", report["data"]["text"])
        self.assertNotIn("Выплата зарплаты", report["data"]["text"])
        self.assertNotIn("Аванс", report["data"]["text"])
        self.assertIn("employee-accrual-report-", report["data"]["file_name"])
        self.assertTrue(report["data"]["file_name"].endswith(".md"))
        self.assertIn("period", report["data"])
        self.assertIn("days", report["data"])
        self.assertIn("totals", report["data"])
        self.assertNotIn("weeks", report["data"])
        self.assertNotIn("months", report["data"])

        status, cashbox_details = self.request(
            f"/api/get_cashbox?cashbox_id={cashbox['id']}&transaction_limit=10", method="GET"
        )
        self.assertEqual(status, 200)
        self.assertGreaterEqual(
            cashbox_details["data"]["cashbox"]["statistics"]["transactions_total"], 2
        )

    def test_employee_shift_accrual_rejects_missing_or_inactive_employee(self) -> None:
        status, missing = self.request(
            "/api/create_employee_shift_accrual",
            {"employee_id": "missing", "amount": "1000"},
        )
        self.assertEqual(status, 404)
        self.assertEqual(missing["error"]["code"], "not_found")

        status, employee_saved = self.request(
            "/api/save_employee",
            {
                "name": "Неактивный Сотрудник",
                "position": "Мастер",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "10",
            },
        )
        self.assertEqual(status, 200)
        employee = employee_saved["data"]["employee"]

        status, toggled = self.request(
            "/api/toggle_employee",
            {"employee_id": employee["id"], "actor_name": "ADMIN"},
        )
        self.assertEqual(status, 200)
        self.assertFalse(toggled["data"]["employee"]["is_active"])

        status, inactive = self.request(
            "/api/create_employee_shift_accrual",
            {"employee_id": employee["id"], "amount": "1000"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(inactive["error"]["code"], "validation_error")

    def test_employee_salary_reconciliation_route_returns_print_payload(self) -> None:
        status, employee_saved = self.request(
            "/api/save_employee",
            {
                "name": "Иван Мастер",
                "position": "Мастер",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "25",
                "material_percent": "0",
            },
        )
        self.assertEqual(status, 200)
        employee = employee_saved["data"]["employee"]

        status, cashbox_created = self.request(
            "/api/create_cashbox", {"name": "Наличный", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        cashbox = cashbox_created["data"]["cashbox"]

        status, card_created = self.request(
            "/api/create_card",
            {"vehicle": "Toyota Camry", "title": "Акт сверки", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = card_created["data"]["card"]["id"]

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "501",
                    "status": "open",
                    "vehicle": "Toyota Camry",
                    "license_plate": "Т501ТС124",
                    "payments": [
                        {
                            "amount": "8000",
                            "paid_at": "16.04.2026 12:00",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [
                        {
                            "name": "Замена генератора",
                            "quantity": "1",
                            "price": "8000",
                            "executor_id": employee["id"],
                        }
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["card"]["repair_order"]["number"], "501")

        status, closed = self.request(
            "/api/set_repair_order_status", {"card_id": card_id, "status": "closed"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(closed["data"]["repair_order"]["works"][0]["salary_amount"], "2000")

        status, payout = self.request(
            "/api/create_employee_salary_transaction",
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_payout",
                "amount": "500",
                "cashbox_id": cashbox["id"],
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payout["data"]["transaction"]["amount_minor"], 50000)

        status, advance = self.request(
            "/api/create_employee_salary_transaction",
            {
                "employee_id": employee["id"],
                "transaction_kind": "salary_advance",
                "amount": "250",
                "cashbox_id": cashbox["id"],
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(advance["data"]["transaction"]["amount_minor"], 25000)

        status, shift_accrual = self.request(
            "/api/create_employee_shift_accrual",
            {
                "employee_id": employee["id"],
                "amount": "1200",
                "actor_name": "ADMIN",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(shift_accrual["data"]["accrual"]["amount_minor"], 120000)

        status, report = self.request(
            f"/api/get_employee_salary_reconciliation?employee_id={employee['id']}",
            method="GET",
        )

        self.assertEqual(status, 200)
        data = report["data"]
        self.assertEqual(data["meta"]["schema_version"], "employee_salary_reconciliation.v1")
        self.assertEqual(data["period"]["days"], 30)
        self.assertEqual(data["employee"]["id"], employee["id"])
        self.assertEqual(data["totals"]["accrued_total"], "3200")
        self.assertEqual(data["totals"]["payout_total"], "500")
        self.assertEqual(data["totals"]["advance_total"], "250")
        self.assertEqual(data["totals"]["amount_due_total"], "2450")
        self.assertTrue(any(row["kind"] == "work_accrual" for row in data["rows"]))
        self.assertTrue(any(row["kind"] == "shift_accrual" for row in data["rows"]))
        self.assertTrue(any(row["kind"] == "salary_payout" for row in data["rows"]))
        self.assertTrue(any(row["kind"] == "salary_advance" for row in data["rows"]))
        work_row = next(row for row in data["rows"] if row["kind"] == "work_accrual")
        self.assertEqual(work_row["repair_order_number"], "501")
        self.assertEqual(work_row["license_plate"], "т501тс124")
        self.assertEqual(work_row["accrued"], "2000")

        status, days_report = self.request(
            f"/api/get_employee_salary_reconciliation?employee_id={employee['id']}&days=7",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertEqual(days_report["data"]["period"]["days"], 7)
        self.assertEqual(days_report["data"]["period"]["mode"], "last_days")
        self.assertEqual(days_report["data"]["meta"]["period_days"], 7)
        self.assertEqual(days_report["data"]["meta"]["period_mode"], "last_days")

        status, headers, body = self.raw_request(
            f"/employee_salary_reconciliation_print?employee_id={employee['id']}&days=7"
        )
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        html = body.decode("utf-8")
        self.assertIn("Акт сверки зарплаты", html)
        self.assertIn("Иван Мастер", html)
        self.assertIn("Toyota Camry", html)
        self.assertIn("т501тс124", html)
        self.assertIn(days_report["data"]["period"]["label"], html)

        status, headers, empty_body = self.raw_request(
            f"/employee_salary_reconciliation_print?employee_id={employee['id']}"
            "&date_from=2020-01-01&date_to=2020-01-02"
        )
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        empty_html = empty_body.decode("utf-8")
        self.assertIn("01.01.2020 - 02.01.2020", empty_html)
        self.assertIn("За период 01.01.2020 - 02.01.2020 движений нет.", empty_html)
        self.assertNotIn("За последние 30 дней движений нет.", empty_html)
        self.assertIn("Замена генератора", html)
        self.assertIn("Выплата за смены за текущую неделю", html)
        self.assertIn("ПЕЧАТЬ", html)
        self.assertIn("@media print", html)
        self.assertIn("Бухгалтер", html)
        self.assertIn("Сотрудник", html)
        self.assertRegex(html, r"Дата: \d{2}\.\d{2}\.\d{4}")
        self.assertNotRegex(html, r"Дата: \d{4}-\d{2}-\d{2}T")
        self.assertNotIn("+00:00", html)

    def test_repair_order_work_salary_override_round_trips_through_api(self) -> None:
        status, employee_saved = self.request(
            "/api/save_employee",
            {
                "name": "Иван Мастер",
                "position": "Мастер",
                "salary_mode": "percent_only",
                "base_salary": "0",
                "work_percent": "25",
            },
        )
        self.assertEqual(status, 200)
        employee = employee_saved["data"]["employee"]

        status, card_created = self.request(
            "/api/create_card",
            {"vehicle": "Mercedes GLA", "title": "Построчная зарплата", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = card_created["data"]["card"]["id"]

        status, updated = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "502",
                    "status": "open",
                    "vehicle": "Mercedes GLA",
                    "license_plate": "К502КК124",
                    "payments": [
                        {
                            "amount": "20000",
                            "paid_at": "16.04.2026 12:00",
                            "payment_method": "cash",
                        }
                    ],
                    "works": [
                        {
                            "name": "Ремонт модуля SCM",
                            "quantity": "1",
                            "price": "20000",
                            "executor_id": employee["id"],
                            "work_salary_override_enabled": "true",
                            "work_salary_guarantee": "5000",
                            "work_salary_percent_override": "45",
                            "work_salary_cost_price": "3000",
                        }
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        work = updated["data"]["repair_order"]["works"][0]
        self.assertEqual(work["work_salary_override_enabled"], "true")
        self.assertEqual(work["work_salary_guarantee"], "5000")
        self.assertEqual(work["work_salary_percent_override"], "45")
        self.assertEqual(work["work_salary_cost_price"], "3000")

        status, closed = self.request(
            "/api/set_repair_order_status", {"card_id": card_id, "status": "closed"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(closed["data"]["repair_order"]["works"][0]["salary_amount"], "10400")

        status, report = self.request(
            f"/api/get_employee_salary_reconciliation?employee_id={employee['id']}",
            method="GET",
        )
        self.assertEqual(status, 200)
        work_row = next(row for row in report["data"]["rows"] if row["kind"] == "work_accrual")
        self.assertEqual(work_row["accrued"], "10400")
        self.assertIn("Выплата исполнителю 5 000,00 ₽ + 45%", work_row["scheme"])
        self.assertIn("Себестоимость работы 3 000,00 ₽", work_row["calculation_base"])

    def test_snapshot_compact_query_returns_board_friendly_cards(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"vehicle": "LEXUS IS F", "title": "Compact API snapshot", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, snapshot = self.request(
            "/api/get_board_snapshot?archive_limit=30&compact=1", method="GET"
        )
        self.assertEqual(status, 200)
        self.assertTrue(snapshot["data"]["meta"]["compact_cards"])
        compact_card = next(card for card in snapshot["data"]["cards"] if card["id"] == card_id)
        self.assertNotIn("repair_order", compact_card)
        self.assertNotIn("vehicle_profile", compact_card)
        self.assertNotIn("attachments", compact_card)
        self.assertTrue(snapshot["data"]["meta"]["revision"])

    def test_compact_snapshot_cache_refreshes_per_request_metadata(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Prepared snapshot metadata", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        self.assertTrue(created["ok"])
        generated_at = (
            "2026-07-22T01:02:03+00:00",
            "2026-07-22T01:02:04+00:00",
        )
        response_timestamps = (
            datetime(2026, 7, 22, 1, 2, 3, tzinfo=UTC),
            datetime(2026, 7, 22, 1, 2, 4, tzinfo=UTC),
        )

        with (
            patch.object(snapshot_service_module.time, "monotonic", return_value=100.0),
            patch.object(
                snapshot_service_module,
                "build_prepared_snapshot_data",
                wraps=snapshot_service_module.build_prepared_snapshot_data,
            ) as build_prepared,
            patch.object(api_server_module, "utc_now_iso", side_effect=generated_at),
            patch.object(api_server_module, "datetime") as api_datetime,
        ):
            api_datetime.now.side_effect = response_timestamps
            first_status, first = self.request(
                "/api/get_board_snapshot?compact=1&include_archive=0",
                method="GET",
            )
            second_status, second = self.request(
                "/api/get_board_snapshot?compact=1&include_archive=0",
                method="GET",
            )

        self.assertEqual((first_status, second_status), (200, 200))
        self.assertEqual(build_prepared.call_count, 1)
        self.assertEqual(first["data"]["meta"]["generated_at"], generated_at[0])
        self.assertEqual(second["data"]["meta"]["generated_at"], generated_at[1])
        self.assertEqual(first["meta"]["timestamp"], response_timestamps[0].isoformat())
        self.assertEqual(second["meta"]["timestamp"], response_timestamps[1].isoformat())
        self.assertNotEqual(first["meta"]["request_id"], second["meta"]["request_id"])
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertIsNone(first["error"])
        self.assertIsNone(second["error"])
        first_static = dict(first["data"])
        first_static["meta"] = {
            key: value for key, value in first["data"]["meta"].items() if key != "generated_at"
        }
        second_static = dict(second["data"])
        second_static["meta"] = {
            key: value for key, value in second["data"]["meta"].items() if key != "generated_at"
        }
        self.assertEqual(first_static, second_static)

    def test_prepared_snapshot_matches_http_normalization_depth(self) -> None:
        generated_at = "2026-07-22T01:02:03+00:00"
        response_timestamp = datetime(2026, 7, 22, 1, 2, 4, tzinfo=UTC)
        request_id = "fixed-request-id"
        snapshot = {
            "columns": [],
            "cards": [],
            "archive": [],
            "stickies": [],
            "settings": {
                "deep": {
                    "level_1": {
                        "level_2": {
                            "level_3": {"level_4": {"level_5": {"level_6": "settings-leaf"}}}
                        }
                    }
                }
            },
            "meta": {
                "revision": "test-revision",
                "deep": {
                    "level_1": {
                        "level_2": {"level_3": {"level_4": {"level_5": {"level_6": "meta-leaf"}}}}
                    }
                },
                "generated_at": generated_at,
            },
        }
        prepared = snapshot_service_module.build_prepared_snapshot_data(
            snapshot,
            json_dumps=snapshot_service_module._json_dumps,
        )

        with patch.object(api_server_module, "datetime") as api_datetime:
            api_datetime.now.return_value = response_timestamp
            ordinary = api_server_module._json_response(
                ok=True,
                data=snapshot,
                error=None,
                request_id=request_id,
            )
            preencoded = api_server_module._json_response_from_preencoded_data(
                data=prepared.render(generated_at=generated_at),
                request_id=request_id,
            )

        self.assertEqual(json.loads(preencoded), json.loads(ordinary))

    def test_board_revision_route_matches_snapshot_without_card_payload(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"vehicle": "FORD", "title": "Revision API", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        self.assertTrue(created["ok"])

        status, snapshot = self.request(
            "/api/get_board_snapshot?compact=1&include_archive=0", method="GET"
        )
        self.assertEqual(status, 200)
        status, revision = self.request(
            "/api/get_board_revision?compact=1&include_archive=0", method="GET"
        )

        self.assertEqual(status, 200)
        self.assertEqual(revision["data"]["revision"], snapshot["data"]["meta"]["revision"])
        self.assertEqual(revision["data"]["meta"]["revision"], snapshot["data"]["meta"]["revision"])
        self.assertEqual(revision["data"]["counts"]["cards"], len(snapshot["data"]["cards"]))
        self.assertNotIn("cards", revision["data"])
        self.assertNotIn("archive", revision["data"])

    def test_json_api_gzips_large_payloads_when_client_accepts_gzip(self) -> None:
        for index in range(6):
            status, created = self.request(
                "/api/create_card",
                {
                    "vehicle": f"TEST {index}",
                    "title": f"Gzip snapshot {index}",
                    "description": "Длинное описание " * 80,
                    "deadline": {"hours": 2},
                },
            )
            self.assertEqual(status, 200)
            self.assertTrue(created["ok"])

        status, headers, body = self.raw_request(
            "/api/get_board_snapshot?compact=1&include_archive=0",
            headers={"Accept-Encoding": "gzip"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertEqual(headers.get("Vary"), "Accept-Encoding")
        self.assertIn("Server-Timing", headers)
        timing_names = {
            item.split(";", 1)[0].strip() for item in headers["Server-Timing"].split(",")
        }
        self.assertEqual(
            timing_names,
            {
                "app",
                "total",
                "lock",
                "service_lock",
                "store_lock",
                "file_lock",
                "normalize",
                "serialize",
                "write",
                "storage",
            },
        )
        payload = json.loads(gzip.decompress(body).decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["data"]["meta"]["revision"])
        self.assertGreater(len(payload["data"]["cards"]), 0)

    def test_json_gzip_negotiation_honors_quality_values_and_exact_tokens(self) -> None:
        cases = (
            ("gzip;q=0", False),
            ("br, gzip ; q=0.000", False),
            ("gzip;q=0, *;q=1", False),
            ("notgzip", False),
            ("gzip;q=invalid", False),
            ("gzip;q=1.1", False),
            ("br;q=1, gzip;q=0.25", True),
            ("GZip ; q=1", True),
            ("*;q=0.5", True),
        )

        with patch("minimal_kanban.api.server.JSON_GZIP_MIN_BYTES", 0):
            for accept_encoding, expected_gzip in cases:
                with self.subTest(accept_encoding=accept_encoding):
                    status, headers, body = self.raw_request(
                        "/api/get_cards",
                        headers={"Accept-Encoding": accept_encoding},
                    )

                    self.assertEqual(status, 200)
                    self.assertEqual(headers.get("Content-Encoding") == "gzip", expected_gzip)
                    decoded = gzip.decompress(body) if expected_gzip else body
                    self.assertTrue(json.loads(decoded)["ok"])

    def test_write_response_exposes_storage_phase_timings(self) -> None:
        status, headers, body = self.raw_request(
            "/api/create_card",
            method="POST",
            payload={"title": "Timing contract", "deadline": {"hours": 2}},
        )

        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        metrics = {
            item.split(";", 1)[0].strip(): float(item.rsplit("=", 1)[1])
            for item in headers["Server-Timing"].split(",")
        }
        self.assertGreater(metrics["storage"], 0)
        self.assertGreater(metrics["serialize"], 0)
        self.assertGreater(metrics["write"], 0)
        self.assertGreaterEqual(metrics["total"], metrics["storage"])

    def test_snapshot_without_archive_allows_zero_archive_limit(self) -> None:
        status, snapshot = self.request(
            "/api/get_board_snapshot?include_archive=0&archive_limit=0&compact=1",
            method="GET",
        )

        self.assertEqual(status, 200)
        self.assertFalse(snapshot["data"]["meta"]["include_archive"])
        self.assertEqual(snapshot["data"]["meta"]["archive_limit"], 0)
        self.assertEqual(snapshot["data"]["archive"], [])

    def test_get_cards_compact_query_omits_heavy_fields(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "BMW",
                "title": "Compact API cards",
                "description": "VIN test",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, cards_payload = self.request("/api/get_cards?compact=1", method="GET")
        self.assertEqual(status, 200)
        compact_card = next(
            card for card in cards_payload["data"]["cards"] if card["id"] == card_id
        )
        self.assertNotIn("repair_order", compact_card)
        self.assertNotIn("vehicle_profile", compact_card)
        self.assertNotIn("attachments", compact_card)
        self.assertNotIn("ai_autofill_log", compact_card)

    def test_get_cards_compact_query_redacts_phone_and_vin_from_description_preview(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "title": "Compact redact",
                "description": "Клиент: +7 (923) 123-45-67\nVIN: X4XKCN81140CY67957\nПроверить запись.",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, cards_payload = self.request("/api/get_cards?compact=1", method="GET")
        self.assertEqual(status, 200)
        compact_card = next(
            card for card in cards_payload["data"]["cards"] if card["id"] == card_id
        )
        self.assertNotIn("+7 (923) 123-45-67", compact_card["description"])
        self.assertNotIn("X4XKCN81140CY67957", compact_card["description"])
        self.assertIn("[PHONE]", compact_card["description"])
        self.assertIn("[VIN]", compact_card["description"])
        self.assertEqual(compact_card["description"], compact_card["description_preview"])

    def test_snapshot_can_skip_archive_payload_for_board_refresh(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "MITSUBISHI L200",
                "title": "Archive API snapshot",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        status, archived = self.request(
            "/api/archive_card", {"card_id": card_id, "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(archived["data"]["card"]["archived"])

        status, snapshot = self.request(
            "/api/get_board_snapshot?compact=1&include_archive=0", method="GET"
        )
        self.assertEqual(status, 200)
        self.assertEqual(snapshot["data"]["archive"], [])
        self.assertEqual(snapshot["data"]["meta"]["archived_cards_total"], 1)

    def test_repair_order_routes_list_and_open_text_file(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"vehicle": "KIA RIO", "title": "API заказ-наряд", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "comment": "Проверить и выдать текстовый файл",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1000", "total": "1000"}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["card"]["repair_order"]["number"], "1")

        status, listed = self.request("/api/list_repair_orders", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(listed["data"]["repair_orders"][0]["card_id"], card_id)
        self.assertEqual(listed["data"]["repair_orders"][0]["paid_total"], "0")
        self.assertEqual(listed["data"]["repair_orders"][0]["payment_status"], "unpaid")
        self.assertTrue(listed["data"]["repair_orders"][0]["file_name"].endswith(".txt"))

        request = urllib.request.Request(
            f"{self.base_url}/api/repair_order_text?card_id={card_id}",
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "text/plain")
            self.assertIn("1", body)
            self.assertIn("1000", body)
            self.assertIn("+7 900 123-45-67", body)
            self.assertIn("JSON:", body)

    def test_update_card_route_derives_repair_order_taxes_from_selected_cashbox(self) -> None:
        status, cashless_cashbox = self.request(
            "/api/create_cashbox", {"name": "Безналичный", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        status, maria_cashbox = self.request(
            "/api/create_cashbox", {"name": "Карта Мария", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)

        status, created = self.request(
            "/api/create_card",
            {"vehicle": "AUDI A4", "title": "API оплата", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        cashless_card_id = created["data"]["card"]["id"]

        status, updated_cashless = self.request(
            "/api/update_card",
            {
                "card_id": cashless_card_id,
                "repair_order": {
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
                    "payments": [
                        {
                            "amount": "500",
                            "paid_at": "06.04.2026 10:00",
                            "note": "Аванс",
                            "payment_method": "cash",
                            "cashbox_id": cashless_cashbox["data"]["cashbox"]["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        cashless_order = updated_cashless["data"]["card"]["repair_order"]
        self.assertEqual(cashless_order["payment_method"], "cashless")
        self.assertEqual(cashless_order["taxes_total"], "75")
        self.assertEqual(cashless_order["grand_total"], "1075")
        self.assertEqual(cashless_order["due_total"], "575")

        status, cash_cashbox = self.request(
            "/api/create_cashbox", {"name": "Наличный", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        status, mixed_paid = self.request(
            "/api/update_card",
            {
                "card_id": cashless_card_id,
                "repair_order": {
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
                    "payments": [
                        {
                            "amount": "500",
                            "paid_at": "06.04.2026 10:00",
                            "note": "Аванс",
                            "payment_method": "cash",
                            "cashbox_id": cashless_cashbox["data"]["cashbox"]["id"],
                            "actor_name": "ADMIN",
                        },
                        {
                            "amount": "500",
                            "paid_at": "06.04.2026 10:10",
                            "note": "Доплата",
                            "payment_method": "cash",
                            "cashbox_id": cash_cashbox["data"]["cashbox"]["id"],
                            "actor_name": "ADMIN",
                        },
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        mixed_order = mixed_paid["data"]["card"]["repair_order"]
        self.assertEqual(mixed_order["taxes_total"], "75")
        self.assertEqual(mixed_order["grand_total"], "1075")
        self.assertEqual(mixed_order["paid_total"], "1000")
        self.assertEqual(mixed_order["due_total"], "75")

        status, created = self.request(
            "/api/create_card",
            {"vehicle": "BMW X5", "title": "API карта", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        maria_card_id = created["data"]["card"]["id"]

        status, updated_maria = self.request(
            "/api/update_card",
            {
                "card_id": maria_card_id,
                "repair_order": {
                    "works": [{"name": "Осмотр", "quantity": "1", "price": "1000"}],
                    "payments": [
                        {
                            "amount": "500",
                            "paid_at": "06.04.2026 10:05",
                            "note": "Оплата",
                            "payment_method": "cashless",
                            "cashbox_id": maria_cashbox["data"]["cashbox"]["id"],
                            "actor_name": "ADMIN",
                        }
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        maria_order = updated_maria["data"]["card"]["repair_order"]
        self.assertEqual(maria_order["payment_method"], "card")
        self.assertEqual(maria_order["taxes_total"], "0")
        self.assertEqual(maria_order["grand_total"], "1000")
        self.assertEqual(maria_order["due_total"], "500")

    def test_create_card_accepts_colored_tags(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "title": "Цветная карточка",
                "description": "Проверка API",
                "tags": [
                    {"label": "СРОЧНО", "color": "red"},
                    {"label": "ЖДЁМ", "color": "yellow"},
                ],
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(created["data"]["card"]["tags"], ["СРОЧНО", "ЖДЁМ"])
        self.assertEqual(created["data"]["card"]["tag_items"][0]["color"], "red")
        self.assertEqual(created["data"]["card"]["tag_items"][1]["color"], "yellow")

    def test_create_card_rejects_more_than_three_tags(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "title": "Слишком много меток",
                "description": "Проверка ограничения",
                "tags": ["СРОЧНО", "ЖДЁМ", "СОГЛАСОВАТЬ", "ЗАКАЗАТЬ"],
                "deadline": {"hours": 2},
            },
        )

        self.assertEqual(status, 400)
        self.assertFalse(created["ok"])
        self.assertEqual(created["error"]["code"], "validation_error")

    def test_delete_column_route_removes_empty_column_and_rejects_non_empty(self) -> None:
        status, created_column = self.request("/api/create_column", {"label": "DELETE ME"})
        self.assertEqual(status, 200)
        column_id = created_column["data"]["column"]["id"]

        status, deleted = self.request("/api/delete_column", {"column_id": column_id})
        self.assertEqual(status, 200)
        self.assertTrue(deleted["ok"])
        self.assertEqual(deleted["data"]["deleted_column"]["id"], column_id)
        self.assertTrue(all(column["id"] != column_id for column in deleted["data"]["columns"]))

        status, created_again = self.request("/api/create_column", {"label": "NOT EMPTY"})
        self.assertEqual(status, 200)
        blocked_column_id = created_again["data"]["column"]["id"]
        status, _ = self.request(
            "/api/create_card",
            {"title": "Busy column", "column": blocked_column_id, "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        status, blocked = self.request("/api/delete_column", {"column_id": blocked_column_id})
        self.assertEqual(status, 409)
        self.assertEqual(blocked["error"]["code"], "column_not_empty")

    def test_delete_column_route_rejects_system_ready_column(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Archived done card", "column": "done", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, archived = self.request("/api/archive_card", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertTrue(archived["data"]["card"]["archived"])

        status, deleted = self.request("/api/delete_column", {"column_id": "done"})
        self.assertEqual(status, 409)
        self.assertFalse(deleted["ok"])
        self.assertEqual(deleted["error"]["code"], "system_column_locked")

        status, card = self.request("/api/get_card", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertTrue(card["data"]["card"]["archived"])
        self.assertEqual(card["data"]["card"]["column"], "done")

    def test_archive_card_route_rejects_open_repair_order(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "KIA RIO",
                "title": "Open order",
                "description": "Проверить подвеску",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "18",
                    "status": "open",
                    "client": "Иван Иванов",
                    "vehicle": "KIA RIO",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "2000"}],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["card"]["repair_order"]["status"], "open")

        status, blocked = self.request("/api/archive_card", {"card_id": card_id})
        self.assertEqual(status, 409)
        self.assertEqual(blocked["error"]["code"], "repair_order_open_archive_blocked")
        self.assertIn("открыт заказ-наряд", blocked["error"]["message"])

    def test_archive_card_route_allows_open_empty_repair_order_without_money(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "KIA RIO",
                "title": "Empty order",
                "description": "Открыли, но не расписывали",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "20",
                    "date": "06.04.2026 10:00",
                    "status": "open",
                    "opened_at": "06.04.2026 10:00",
                    "client": "Иван Иванов",
                    "vehicle": "KIA RIO",
                    "reason": "Empty order",
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(updated["data"]["card"]["repair_order"]["is_empty_for_archive"])

        status, archived = self.request("/api/archive_card", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertTrue(archived["data"]["card"]["archived"])

    def test_employee_routes_and_payroll_report(self) -> None:
        status, saved_employee = self.request(
            "/api/save_employee",
            {
                "name": "Иван Мастер",
                "position": "Механик",
                "salary_mode": "salary_plus_percent",
                "base_salary": "50000",
                "work_percent": "30",
            },
        )
        self.assertEqual(status, 200)
        employee_id = saved_employee["data"]["employee"]["id"]

        status, employees = self.request("/api/list_employees", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(any(item["id"] == employee_id for item in employees["data"]["employees"]))

        status, created = self.request(
            "/api/create_card",
            {"vehicle": "Mitsubishi L200", "title": "Payroll API", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, _ = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "repair_order": {
                    "number": "31",
                    "status": "open",
                    "client": "Клиент",
                    "vehicle": "Mitsubishi L200",
                    "payments": [
                        {"amount": "10000", "paid_at": "05.04.2026 10:00", "payment_method": "cash"}
                    ],
                    "works": [
                        {
                            "name": "Диагностика",
                            "quantity": "1",
                            "price": "5000",
                            "executor_id": employee_id,
                        },
                        {
                            "name": "Замена масла",
                            "quantity": "1",
                            "price": "5000",
                            "executor_id": employee_id,
                        },
                    ],
                },
            },
        )
        self.assertEqual(status, 200)

        status, closed = self.request(
            "/api/set_repair_order_status", {"card_id": card_id, "status": "closed"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(closed["data"]["repair_order"]["works"][0]["salary_amount"], "1500")

        status, report = self.request("/api/get_payroll_report", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(
            any(item["employee_id"] == employee_id for item in report["data"]["summary"])
        )
        detail_rows = [
            item for item in report["data"]["detail_rows"] if item["employee_id"] == employee_id
        ]
        self.assertEqual(len(detail_rows), 1)
        self.assertEqual(detail_rows[0]["works_count"], 2)
        self.assertEqual(detail_rows[0]["work_total"], "10000")
        self.assertEqual(detail_rows[0]["salary_amount"], "3000")

        status, reopened = self.request(
            "/api/set_repair_order_status", {"card_id": card_id, "status": "open"}
        )
        self.assertEqual(status, 200)
        reopened_row = reopened["data"]["repair_order"]["works"][0]
        self.assertEqual(reopened_row["salary_amount"], "")
        self.assertEqual(reopened_row["salary_accrued_at"], "")

    def test_employee_routes_create_multiple_and_delete(self) -> None:
        status, first = self.request("/api/save_employee", {"name": "Иван", "position": "Мастер"})
        self.assertEqual(status, 200)
        self.assertTrue(first["data"]["created"])

        status, second = self.request(
            "/api/save_employee", {"name": "Пётр", "position": "Приёмщик"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(second["data"]["created"])
        self.assertNotEqual(first["data"]["employee"]["id"], second["data"]["employee"]["id"])

        status, listed = self.request("/api/list_employees", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed["data"]["employees"]), 2)

        status, deleted = self.request(
            "/api/delete_employee", {"employee_id": second["data"]["employee"]["id"]}
        )
        self.assertEqual(status, 200)
        self.assertTrue(deleted["data"]["deleted"])

        status, listed_after = self.request("/api/list_employees", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed_after["data"]["employees"]), 1)
        self.assertEqual(
            listed_after["data"]["employees"][0]["id"], first["data"]["employee"]["id"]
        )

    def test_delete_employee_route_rejects_payroll_referenced_employee(self) -> None:
        status, saved = self.request(
            "/api/save_employee", {"name": "Олег Мастер", "position": "Механик"}
        )
        self.assertEqual(status, 200)
        employee_id = saved["data"]["employee"]["id"]
        status, cashbox = self.request("/api/create_cashbox", {"name": "Зарплатная касса"})
        self.assertEqual(status, 200)
        status, payout = self.request(
            "/api/create_employee_salary_transaction",
            {
                "employee_id": employee_id,
                "transaction_kind": "salary_payout",
                "amount": "1000",
                "cashbox_id": cashbox["data"]["cashbox"]["id"],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payout["data"]["transaction"]["employee_id"], employee_id)

        status, deleted = self.request("/api/delete_employee", {"employee_id": employee_id})

        self.assertEqual(status, 400)
        self.assertEqual(deleted["error"]["code"], "validation_error")
        self.assertEqual(deleted["error"]["details"]["usage"]["salary_transactions"], 1)

    def test_employee_routes_toggle_active_state(self) -> None:
        status, saved = self.request("/api/save_employee", {"name": "Иван", "position": "Мастер"})
        self.assertEqual(status, 200)
        employee_id = saved["data"]["employee"]["id"]

        status, toggled_off = self.request("/api/toggle_employee", {"employee_id": employee_id})
        self.assertEqual(status, 200)
        self.assertFalse(toggled_off["data"]["employee"]["is_active"])

        status, toggled_on = self.request("/api/toggle_employee", {"employee_id": employee_id})
        self.assertEqual(status, 200)
        self.assertTrue(toggled_on["data"]["employee"]["is_active"])

    def test_employee_routes_support_max_count_and_reject_overflow(self) -> None:
        checkpoints = {1, 2, 3, 10, 15, EMPLOYEES_MAX_COUNT}
        seen_ids: set[str] = set()
        for index in range(EMPLOYEES_MAX_COUNT):
            status, saved = self.request(
                "/api/save_employee",
                {
                    "name": f"Сотрудник {index + 1}",
                    "position": f"Пост {index + 1}",
                    "salary_mode": "salary_plus_percent",
                    "base_salary": str((index + 1) * 1000),
                    "work_percent": str(index + 3),
                },
            )
            self.assertEqual(status, 200)
            seen_ids.add(saved["data"]["employee"]["id"])
            if (index + 1) in checkpoints:
                status, listed = self.request("/api/list_employees", method="GET")
                self.assertEqual(status, 200)
                self.assertEqual(len(listed["data"]["employees"]), index + 1)
                self.assertEqual(
                    len({item["id"] for item in listed["data"]["employees"]}), index + 1
                )

        status, listed = self.request("/api/list_employees", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed["data"]["employees"]), EMPLOYEES_MAX_COUNT)
        self.assertEqual({item["id"] for item in listed["data"]["employees"]}, seen_ids)

        status, overflow = self.request(
            "/api/save_employee", {"name": f"Сотрудник {EMPLOYEES_MAX_COUNT + 1}"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(overflow["error"]["code"], "validation_error")
        self.assertIn(str(EMPLOYEES_MAX_COUNT), overflow["error"]["message"])

    def test_save_employee_create_mode_ignores_stale_employee_id(self) -> None:
        status, first = self.request("/api/save_employee", {"name": "Иван", "position": "Мастер"})
        self.assertEqual(status, 200)

        status, second = self.request(
            "/api/save_employee",
            {
                "employee_id": first["data"]["employee"]["id"],
                "create_mode": True,
                "name": "Пётр",
                "position": "Приёмщик",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(second["data"]["created"])
        self.assertNotEqual(first["data"]["employee"]["id"], second["data"]["employee"]["id"])

        status, listed = self.request("/api/list_employees", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed["data"]["employees"]), 2)
        self.assertCountEqual(
            [item["name"] for item in listed["data"]["employees"]], ["Иван", "Пётр"]
        )

    def test_save_employee_update_preserves_payroll_settings_from_blank_form_payload(
        self,
    ) -> None:
        status, saved = self.request(
            "/api/save_employee",
            {
                "name": "Сергей",
                "position": "Мастер",
                "salary_mode": "salary_plus_percent",
                "base_salary": "30000",
                "work_percent": "45",
                "material_percent": "10",
            },
        )
        self.assertEqual(status, 200)

        status, updated = self.request(
            "/api/save_employee",
            {
                "employee_id": saved["data"]["employee"]["id"],
                "name": "Сергей Гелингер",
                "position": "Старший мастер",
                "salary_mode": "percent_only",
                "base_salary": "",
                "work_percent": "",
                "material_percent": "",
            },
        )

        self.assertEqual(status, 200)
        employee = updated["data"]["employee"]
        self.assertEqual(employee["name"], "Сергей Гелингер")
        self.assertEqual(employee["salary_mode"], "salary_plus_percent")
        self.assertEqual(employee["base_salary"], "30000")
        self.assertEqual(employee["work_percent"], "45")
        self.assertEqual(employee["material_percent"], "10")

    def test_save_employee_requires_name(self) -> None:
        status, response = self.request(
            "/api/save_employee",
            {
                "name": "",
                "position": "Механик",
                "salary_mode": "salary_plus_percent",
                "base_salary": "50000",
                "work_percent": "30",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "validation_error")
        self.assertEqual(response["error"]["details"]["field"], "name")

    def test_rename_column_route_updates_label_and_preserves_id(self) -> None:
        status, created_column = self.request("/api/create_column", {"label": "OLD LABEL"})
        self.assertEqual(status, 200)
        column_id = created_column["data"]["column"]["id"]
        status, sibling_column = self.request("/api/create_column", {"label": "SIBLING LABEL"})
        self.assertEqual(status, 200)

        status, renamed = self.request(
            "/api/rename_column", {"column_id": column_id, "label": "NEW LABEL"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(renamed["ok"])
        self.assertEqual(renamed["data"]["column"]["id"], column_id)
        self.assertEqual(renamed["data"]["column"]["label"], "NEW LABEL")
        self.assertTrue(renamed["data"]["meta"]["changed"])

        status, duplicate = self.request(
            "/api/rename_column",
            {"column_id": column_id, "label": sibling_column["data"]["column"]["label"]},
        )
        self.assertEqual(status, 400)
        self.assertEqual(duplicate["error"]["code"], "validation_error")

    def test_move_column_route_reorders_columns(self) -> None:
        status, first = self.request("/api/create_column", {"label": "FIRST"})
        self.assertEqual(status, 200)
        status, second = self.request("/api/create_column", {"label": "SECOND"})
        self.assertEqual(status, 200)
        status, third = self.request("/api/create_column", {"label": "THIRD"})
        self.assertEqual(status, 200)

        status, moved = self.request(
            "/api/move_column",
            {
                "column_id": third["data"]["column"]["id"],
                "before_column_id": first["data"]["column"]["id"],
            },
        )
        self.assertEqual(status, 200)
        ordered_ids = [item["id"] for item in moved["data"]["columns"]]
        self.assertEqual(
            ordered_ids[-3:],
            [
                third["data"]["column"]["id"],
                first["data"]["column"]["id"],
                second["data"]["column"]["id"],
            ],
        )
        self.assertTrue(moved["data"]["meta"]["changed"])

    def test_bulk_move_cards_route_moves_cards_and_reports_partial_failures(self) -> None:
        status, created_column = self.request("/api/create_column", {"label": "MCP TEST COLUMN"})
        self.assertEqual(status, 200)
        target_column = created_column["data"]["column"]["id"]

        status, first = self.request(
            "/api/create_card", {"title": "Bulk one", "column": "inbox", "deadline": {"hours": 2}}
        )
        self.assertEqual(status, 200)
        status, second = self.request(
            "/api/create_card",
            {"title": "Bulk two", "column": "in_progress", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        status, archived = self.request(
            "/api/create_card",
            {"title": "Bulk archived", "column": "done", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        archived_id = archived["data"]["card"]["id"]
        status, _ = self.request("/api/archive_card", {"card_id": archived_id})
        self.assertEqual(status, 200)

        status, moved = self.request(
            "/api/bulk_move_cards",
            {
                "card_ids": [
                    first["data"]["card"]["id"],
                    second["data"]["card"]["id"],
                    archived_id,
                    "missing-card",
                ],
                "column": target_column,
                "actor_name": "MCP TEST",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(moved["ok"])
        self.assertEqual(moved["data"]["meta"]["moved"], 2)
        self.assertEqual(moved["data"]["meta"]["errors"], 2)
        self.assertTrue(any(item["code"] == "archived_card" for item in moved["data"]["errors"]))
        self.assertTrue(any(item["code"] == "not_found" for item in moved["data"]["errors"]))
        self.assertTrue(
            all(card["column"] == target_column for card in moved["data"]["moved_cards"])
        )

        status, first_after = self.request(
            f"/api/get_card?card_id={first['data']['card']['id']}", method="GET"
        )
        self.assertEqual(status, 200)
        self.assertEqual(first_after["data"]["card"]["column"], target_column)

    def test_vehicle_profile_can_be_created_and_updated_via_api(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "title": "API vehicle profile",
                "deadline": {"hours": 5},
                "vehicle_profile": {
                    "make_display": "Suzuki",
                    "model_display": "Swift",
                    "production_year": 2014,
                    "vin": "JSAZC72S001234567",
                    "engine_code": "K12B",
                    "registration_plate": "A123BC77",
                    "pts_series": "77AA",
                    "pts_number": "123456",
                },
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        self.assertEqual(created["data"]["card"]["vehicle"], "Suzuki Swift 2014")
        self.assertEqual(created["data"]["card"]["vehicle_profile"]["vin"], "JSAZC72S001234567")
        self.assertEqual(
            created["data"]["card"]["vehicle_profile"]["registration_plate"], "a123bc77"
        )
        self.assertEqual(
            created["data"]["card"]["vehicle_profile_compact"]["vin"], "JSAZC72S001234567"
        )

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "vehicle_profile": {
                    "engine_code": "K12C",
                    "gearbox_model": "A6GF1",
                    "manual_fields": ["engine_code"],
                    "pts_series": "77AA",
                    "pts_number": "765432",
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["card"]["vehicle_profile"]["engine_code"], "K12C")
        self.assertEqual(updated["data"]["card"]["vehicle_profile"]["gearbox_model"], "A6GF1")
        self.assertEqual(updated["data"]["card"]["vehicle_profile"]["pts_series"], "77AA")
        self.assertEqual(updated["data"]["card"]["vehicle_profile"]["pts_number"], "765432")
        self.assertEqual(
            updated["data"]["card"]["vehicle_profile_compact"]["gearbox_model"], "A6GF1"
        )

    def test_vehicle_profile_ui_alias_fields_are_saved_via_api(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "title": "API vehicle aliases",
                "deadline": {"hours": 5},
            },
        )
        self.assertEqual(status, 200)

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": created["data"]["card"]["id"],
                "vehicle_profile": {
                    "display_name": "Toyota Camry",
                    "license_plate": "А111АА124",
                    "manual_fields": ["display_name", "license_plate"],
                    "field_sources": {
                        "display_name": "manual_ui",
                        "license_plate": "manual_ui",
                    },
                },
            },
        )

        self.assertEqual(status, 200)
        profile = updated["data"]["card"]["vehicle_profile"]
        self.assertEqual(profile["display_name"], "Toyota Camry")
        self.assertEqual(profile["make_display"], "Toyota")
        self.assertEqual(profile["model_display"], "Camry")
        self.assertEqual(profile["registration_plate"], "а111аа124")
        self.assertIn("registration_plate", profile["manual_fields"])

    def test_cards_can_be_marked_seen_via_api(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "title": "Unread from MCP",
                "description": "Новая карточка от GPT",
                "deadline": {"hours": 2},
                "source": "mcp",
            },
        )
        self.assertEqual(status, 200)
        card = created["data"]["card"]
        self.assertFalse(card["is_unread"])
        card_id = card["id"]
        updated_at = card["updated_at"]

        status, marked = self.request("/api/mark_card_seen", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertTrue(marked["data"]["meta"]["changed"])
        self.assertFalse(marked["data"]["card"]["is_unread"])
        self.assertEqual(marked["data"]["card"]["updated_at"], updated_at)

    def test_update_card_accepts_repair_order_payload(self) -> None:
        status, cashbox_created = self.request(
            "/api/create_cashbox", {"name": "Безналичный", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        cashbox = cashbox_created["data"]["cashbox"]
        status, created = self.request(
            "/api/create_card",
            {
                "title": "Repair order API",
                "description": "Клиент ожидает звонка",
                "deadline": {"hours": 3},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "payment_method": "cashless",
                    "payments": [
                        {
                            "amount": "500",
                            "paid_at": "20.04.2026 09:00",
                            "note": "Ранний платёж",
                            "payment_method": "cashless",
                            "actor_name": "ADMIN",
                            "cashbox_id": cashbox["id"],
                        },
                        {
                            "amount": "700",
                            "paid_at": "05.05.2026 10:00",
                            "note": "Поздний платёж",
                            "payment_method": "cashless",
                            "actor_name": "ADMIN",
                            "cashbox_id": cashbox["id"],
                        },
                    ],
                    "client_information": "Краткая история ремонта для клиента",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1500", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["card"]["repair_order"]["number"], "1")
        self.assertEqual(updated["data"]["card"]["repair_order"]["client"], "Иван Иванов")
        self.assertEqual(
            updated["data"]["card"]["repair_order"]["client_information"],
            "Краткая история ремонта для клиента",
        )
        self.assertEqual(updated["data"]["card"]["repair_order"]["works"][0]["name"], "Диагностика")
        self.assertEqual(updated["data"]["card"]["repair_order"]["works"][0]["total"], "1500")
        self.assertEqual(updated["data"]["card"]["repair_order"]["payment_method"], "cashless")
        self.assertTrue(updated["data"]["card"]["repair_order"]["payment_method_label"])
        self.assertEqual(updated["data"]["card"]["repair_order"]["prepayment"], "1200")
        self.assertEqual(updated["data"]["card"]["repair_order"]["prepayment_display"], "1200")
        self.assertEqual(updated["data"]["card"]["repair_order"]["paid_total"], "1200")
        self.assertEqual(updated["data"]["card"]["repair_order"]["payment_status"], "unpaid")
        self.assertEqual(
            updated["data"]["card"]["repair_order"]["payment_status_label"], "Не оплачен"
        )
        self.assertEqual(len(updated["data"]["card"]["repair_order"]["payments"]), 2)
        self.assertEqual(
            updated["data"]["card"]["repair_order"]["payments"][0]["actor_name"], "ADMIN"
        )
        self.assertEqual(
            updated["data"]["card"]["repair_order"]["payments"][0]["cashbox_name"], cashbox["name"]
        )
        self.assertTrue(
            updated["data"]["card"]["repair_order"]["payments"][0]["cash_transaction_id"]
        )
        self.assertEqual(updated["data"]["card"]["repair_order"]["works_total"], "1500")
        self.assertEqual(updated["data"]["card"]["repair_order"]["materials_total"], "0")
        self.assertEqual(updated["data"]["card"]["repair_order"]["subtotal_total"], "1500")
        self.assertEqual(updated["data"]["card"]["repair_order"]["taxes_total"], "180")
        self.assertEqual(updated["data"]["card"]["repair_order"]["grand_total"], "1680")
        self.assertEqual(updated["data"]["card"]["repair_order"]["due_total"], "480")

        status, cashbox_details = self.request(
            f"/api/get_cashbox?cashbox_id={cashbox['id']}&transaction_limit=10",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertEqual(cashbox_details["data"]["cashbox"]["statistics"]["transactions_total"], 2)
        self.assertTrue(
            str(cashbox_details["data"]["cashbox"]["statistics"]["last_transaction_at"]).startswith(
                "2026-05-05T10:00:00"
            )
        )
        self.assertEqual(cashbox_details["data"]["transactions"][0]["note"], "Поздний платёж")
        self.assertEqual(cashbox_details["data"]["transactions"][1]["note"], "Ранний платёж")

    def test_repair_order_context_and_patch_routes(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "BMW 320i",
                "title": "Ошибка двигателя",
                "description": "Госномер В003НК124",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, patched = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "client_information": "Согласовать дальнейшую диагностику",
                    "license_plate": "В003НК124",
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(patched["data"]["repair_order"]["client"], "Иван Иванов")
        self.assertEqual(
            patched["data"]["repair_order"]["comment"], "Согласовать дальнейшую диагностику"
        )

        status, materials = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "materials": [
                        {
                            "name": "Радиатор",
                            "catalog_number": "1300A123",
                            "quantity": "1",
                            "price": "12000",
                            "total": "",
                        }
                    ]
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            materials["data"]["repair_order"]["materials"][0]["catalog_number"], "1300A123"
        )

        status, works = self.request(
            "/api/replace_repair_order_works",
            {
                "card_id": card_id,
                "rows": [{"name": "Диагностика", "quantity": "1", "price": "2000", "total": ""}],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(works["data"]["repair_order"]["works_total"], "2000")

        status, order = self.request(
            "/api/get_repair_order", {"card_id": card_id, "create_if_missing": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(order["data"]["repair_order"]["license_plate"], "в003нк124")
        self.assertEqual(
            order["data"]["repair_order"]["materials"][0]["catalog_number"], "1300A123"
        )

        status, context = self.request(
            "/api/get_card_context",
            {"card_id": card_id, "event_limit": 10, "include_repair_order_text": True},
        )
        self.assertEqual(status, 200)
        self.assertEqual(context["data"]["card"]["id"], card_id)
        self.assertTrue(context["data"]["meta"]["has_repair_order"])
        self.assertIn("ЗАКАЗ-НАРЯД", context["data"]["repair_order_text"]["text"])

        status, text_payload = self.request("/api/get_repair_order_text", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertEqual(text_payload["data"]["card_id"], card_id)
        self.assertIn(
            "Стоимость заказ-наряда за наличный расчет: 14000",
            text_payload["data"]["text"],
        )
        self.assertIn(
            "Стоимость заказ-наряда по безналичному расчету: 16470.59",
            text_payload["data"]["text"],
        )
        self.assertIn(
            "Доплата по безналичному расчету: 16470.59",
            text_payload["data"]["text"],
        )
        self.assertIn(
            "Доплата по наличному расчету: 14000",
            text_payload["data"]["text"],
        )
        self.assertNotIn("Итого по заказ-наряду", text_payload["data"]["text"])
        self.assertNotIn("К доплате:", text_payload["data"]["text"])

    def test_repair_order_number_correction_route_is_immutable(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Toyota Mark II",
                "title": "Immutable number API",
                "deadline": {"hours": 1},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, order = self.request(
            "/api/get_repair_order", {"card_id": card_id, "create_if_missing": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(order["data"]["repair_order"]["number"], "1")

        status, logged_in = self.request(
            "/api/login_operator",
            {"username": "admin", "password": "admin"},
        )
        self.assertEqual(status, 200)
        headers = {"X-Operator-Session": logged_in["data"]["session"]["token"]}

        status, blocked = self.request(
            "/api/correct_repair_order_number",
            {"card_id": card_id, "number": "99"},
            headers=headers,
        )
        self.assertEqual(status, 409)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"]["code"], "repair_order_number_immutable")

        status, reread = self.request("/api/get_repair_order", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertEqual(reread["data"]["repair_order"]["number"], "1")

    def test_repair_order_print_module_routes_preview_export_and_template_crud(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Toyota Camry XV70",
                "title": "Print module API",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, updated_order = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "vehicle": "Toyota Camry XV70",
                    "vin": "JTNB11HK103456789",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "2500", "total": ""}
                    ],
                    "materials": [{"name": "ATF", "quantity": "6", "price": "950", "total": ""}],
                },
            },
        )
        self.assertEqual(status, 200)
        order_number = updated_order["data"]["repair_order"]["number"]

        status, workspace = self.request(
            "/api/get_repair_order_print_workspace", {"card_id": card_id}
        )
        self.assertEqual(status, 200)
        self.assertEqual(workspace["data"]["documents"][0]["id"], "repair_order")
        self.assertIn("repair_order", workspace["data"]["templates"])

        status, preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "card_id": card_id,
                "selected_document_ids": ["repair_order"],
                "active_document_id": "repair_order",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(preview["data"]["documents"][0]["id"], "repair_order")
        self.assertIn("Заказ-наряд", preview["data"]["documents"][0]["pages"][0]["html"])

        status, draft_preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Черновик из формы",
                    "vehicle": "Toyota Camry XV70",
                    "works": [{"name": "Проверка перед печатью", "quantity": "1", "price": "500"}],
                },
                "selected_document_ids": ["repair_order"],
                "active_document_id": "repair_order",
            },
        )
        self.assertEqual(status, 200)
        draft_html = draft_preview["data"]["documents"][0]["pages"][0]["html"]
        self.assertIn(f"№ {order_number}", draft_html)
        self.assertIn("Черновик из формы", draft_html)

        status, saved_template = self.request(
            "/api/save_print_template",
            {
                "document_type": "repair_order",
                "name": "API template",
                "content": '<div class="document-page"><h1>{{client.name_display}}</h1></div>',
            },
        )
        self.assertEqual(status, 200)
        template_id = saved_template["data"]["template"]["id"]
        self.assertTrue(template_id.startswith("custom:repair_order:"))

        status, defaulted = self.request(
            "/api/set_default_print_template",
            {"document_type": "repair_order", "template_id": template_id},
        )
        self.assertEqual(status, 200)
        self.assertTrue(any(item["is_default"] for item in defaulted["data"]["templates"]))

        with patch(
            "minimal_kanban.printing.service.render_html_to_pdf_bytes",
            return_value=b"%PDF-1.4 route-test",
        ):
            status, exported = self.request(
                "/api/export_repair_order_print_pdf",
                {
                    "card_id": card_id,
                    "selected_document_ids": ["repair_order", "invoice"],
                },
            )
        self.assertEqual(status, 200)
        self.assertTrue(
            base64.b64decode(exported["data"]["content_base64"]).startswith(b"%PDF-1.4")
        )
        self.assertEqual(exported["data"]["mime_type"], "application/pdf")
        self.assertEqual(exported["data"]["meta"]["documents"][0]["id"], "repair_order")

        with patch("minimal_kanban.printing.service.print_html") as print_backend:
            status, printed = self.request(
                "/api/print_repair_order_documents",
                {
                    "card_id": card_id,
                    "selected_document_ids": ["repair_order"],
                    "printer_name": "Office Printer",
                    "print_settings": {"default_printer": "Office Printer", "copies": 2},
                },
            )
        self.assertEqual(status, 200)
        self.assertEqual(printed["data"]["printer_name"], "Office Printer")
        self.assertEqual(printed["data"]["copies"], 2)
        print_backend.assert_called_once()

    def test_repair_order_print_module_accepts_manual_document_without_card(self) -> None:
        manual_document = {
            "document_number": "MAN-100",
            "document_date": "15.06.2026",
            "client": {
                "client_type": "ooo",
                "display_name": "ООО Документ Без Карточки",
                "legal_name": "ООО Документ Без Карточки",
                "inn": "2468000000",
                "kpp": "246801001",
                "checking_account": "40702810900000000001",
                "bank_name": "Тест Банк",
                "bik": "044525225",
                "correspondent_account": "30101810400000000225",
                "legal_address": "660000, г. Красноярск, ул. Тестовая, 1",
            },
            "vehicle": {
                "name": "Nissan X-Trail T32",
                "vin": "Z8NTANT32ES123456",
                "license_plate": "К123КК124",
                "mileage": "92000",
            },
            "works": [{"name": "Замена масла", "quantity": "1", "price": "1500"}],
            "materials": [{"name": "Масло моторное", "quantity": "5", "price": "700"}],
            "payments": [{"amount": "2000", "paid_at": "15.06.2026", "payment_method": "cash"}],
            "reason": "Ручное оформление",
            "comment": "Данные введены оператором вручную",
            "note": "Проверить подписи",
        }

        status, workspace = self.request(
            "/api/get_repair_order_print_workspace",
            {"document_without_card": True, "manual_document": manual_document},
        )
        self.assertEqual(status, 200)
        self.assertTrue(workspace["data"]["meta"]["document_without_card"])
        self.assertEqual(workspace["data"]["card_id"], "manual-document")
        self.assertEqual(
            [item["id"] for item in workspace["data"]["documents"]],
            [
                "repair_order",
                "vehicle_acceptance_act",
                "invoice",
                "invoice_factura",
                "upd",
                "inspection_sheet",
                "completion_act",
                "parts_sale",
            ],
        )

        status, preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "document_without_card": True,
                "manual_document": manual_document,
                "selected_document_ids": ["invoice", "completion_act", "repair_order"],
                "active_document_id": "completion_act",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(preview["data"]["active_document_id"], "completion_act")
        invoice_html = preview["data"]["documents"][0]["pages"][0]["html"]
        repair_order_html = preview["data"]["documents"][2]["pages"][0]["html"]
        self.assertIn("ООО Документ Без Карточки", invoice_html)
        self.assertIn("40702810900000000001", invoice_html)
        self.assertIn("5 882,35", invoice_html)
        self.assertIn("Nissan X-Trail T32", repair_order_html)

        status, upd_preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "document_without_card": True,
                "manual_document": {
                    **manual_document,
                    "tax_label": "НДС (5%)",
                    "regulated": {
                        "basis": "Договор № MANUAL-100",
                        "transport_details": "Передача на территории сервиса",
                    },
                },
                "selected_document_ids": ["upd"],
                "active_document_id": "upd",
            },
        )
        self.assertEqual(status, 200)
        upd_document = upd_preview["data"]["documents"][0]
        self.assertEqual(upd_document["id"], "upd")
        self.assertEqual(upd_document["page_count"], 2)
        upd_html = "".join(page["html"] for page in upd_document["pages"])
        self.assertIn("Универсальный передаточный документ", upd_html)
        self.assertIn("ООО Документ Без Карточки", upd_html)
        self.assertIn("Договор № MANUAL-100", upd_html)
        self.assertIn("Передача на территории сервиса", upd_html)

        with patch(
            "minimal_kanban.printing.service.render_html_to_pdf_bytes",
            return_value=b"%PDF-1.4 manual-document-route",
        ):
            status, exported = self.request(
                "/api/export_repair_order_print_pdf",
                {
                    "document_without_card": True,
                    "manual_document": manual_document,
                    "selected_document_ids": ["invoice"],
                },
            )
        self.assertEqual(status, 200)
        self.assertTrue(base64.b64decode(exported["data"]["content_base64"]).startswith(b"%PDF"))
        self.assertEqual(exported["data"]["meta"]["source"], "manual_document")
        self.assertEqual(
            exported["data"]["meta"]["documents"][0]["template_id"], "builtin:invoice:standard"
        )

    def test_repair_order_print_module_accepts_request_text_without_card(self) -> None:
        request_text = (
            "Счет № TEXT-77 от 15.06.2026\n"
            "Клиент: ООО Текстовый Документ\n"
            "ИНН: 2468003333\n"
            "КПП: 246801001\n"
            "Банк: Тест Банк\n"
            "БИК: 044525225\n"
            "Р/с: 40702810900000000001\n"
            "К/с: 30101810400000000225\n"
            "Автомобиль: Toyota RAV4\n"
            "VIN: JTMBFREV10D123456\n"
            "Работы:\n"
            "Диагностика 1 x 2500\n"
            "Материалы:\n"
            "Фильтр салона 1 x 900\n"
            "Оплаты:\n"
            "1000 | 15.06.2026 | cash | Аванс"
        )

        status, preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "document_without_card": True,
                "request_text": request_text,
                "selected_document_ids": ["invoice", "completion_act"],
                "active_document_id": "invoice",
            },
        )
        self.assertEqual(status, 200)
        invoice_html = preview["data"]["documents"][0]["pages"][0]["html"]
        self.assertIn("ООО Текстовый Документ", invoice_html)
        self.assertIn("2468003333", invoice_html)
        self.assertIn("40702810900000000001", invoice_html)
        self.assertIn("4 000,00", invoice_html)
        self.assertIn("В том числе НДС", invoice_html)

        with patch(
            "minimal_kanban.printing.service.render_html_to_pdf_bytes",
            return_value=b"%PDF-1.4 request-text-manual-document-route",
        ):
            status, exported = self.request(
                "/api/export_repair_order_print_pdf",
                {
                    "document_without_card": True,
                    "request_text": request_text,
                    "selected_document_ids": ["invoice"],
                },
            )
        self.assertEqual(status, 200)
        self.assertTrue(base64.b64decode(exported["data"]["content_base64"]).startswith(b"%PDF"))
        self.assertEqual(exported["data"]["meta"]["source"], "manual_document")
        self.assertEqual(
            exported["data"]["meta"]["documents"][0]["template_id"], "builtin:invoice:standard"
        )

    def test_repair_order_print_module_manual_invoice_supports_no_vat(self) -> None:
        status, preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "document_without_card": True,
                "manual_document": {
                    "document_number": "NO-VAT-API",
                    "document_date": "15.06.2026",
                    "tax_label": "Без НДС",
                    "client": {"display_name": "ООО Без НДС", "inn": "2468000000"},
                    "vehicle": {"name": "Toyota Camry", "vin": "JTNB11HK203123456"},
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
                },
                "selected_document_ids": ["invoice"],
                "active_document_id": "invoice",
            },
        )

        self.assertEqual(status, 200)
        invoice_html = preview["data"]["documents"][0]["pages"][0]["html"]
        self.assertIn("<td>Налоговый режим</td><td>Без НДС</td>", invoice_html)
        self.assertNotIn("В том числе НДС (5%)", invoice_html)
        self.assertIn("1 176,47", invoice_html)

    def test_invoice_preview_includes_linked_client_requisites(self) -> None:
        status, client_created = self.request(
            "/api/create_client",
            {
                "client": {
                    "client_type": "ooo",
                    "display_name": "ООО Контрагент",
                    "legal_name": "ООО Контрагент",
                    "short_name": "Контрагент",
                    "phone": "+7 900 000-00-01",
                    "email": "info@example.com",
                    "inn": "2468000000",
                    "kpp": "246801001",
                    "ogrn": "1234567890123",
                    "checking_account": "40702810900000000001",
                    "bank_name": "Тест Банк",
                    "bik": "044525225",
                    "correspondent_account": "30101810400000000225",
                    "legal_address": "660000, г. Красноярск, ул. Тестовая, 1",
                    "contact_person": "Иванов Иван",
                    "contact_position": "Директор",
                }
            },
        )
        self.assertEqual(status, 200)
        client_id = client_created["data"]["client"]["id"]

        status, card_created = self.request(
            "/api/create_card",
            {"vehicle": "Toyota Camry", "title": "Invoice client link", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = card_created["data"]["card"]["id"]

        status, linked = self.request(
            "/api/link_card_to_client",
            {"card_id": card_id, "client_id": client_id},
        )
        self.assertEqual(status, 200)
        self.assertTrue(linked["data"]["meta"]["changed"])

        status, preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "card_id": card_id,
                "selected_document_ids": ["invoice"],
                "active_document_id": "invoice",
            },
        )
        self.assertEqual(status, 200)
        html = preview["data"]["documents"][0]["pages"][0]["html"]
        self.assertIn("Реквизиты покупателя", html)
        self.assertIn("ООО Контрагент", html)
        self.assertIn("2468000000", html)
        self.assertIn("40702810900000000001", html)
        self.assertIn("Тест Банк", html)
        self.assertIn("Иванов Иван", html)
        self.assertNotIn("Реквизиты клиента не указаны", html)

    def test_invoice_preview_autodetects_client_requisites_from_repair_order(self) -> None:
        status, client_created = self.request(
            "/api/create_client",
            {
                "client": {
                    "client_type": "ooo",
                    "display_name": "ООО Контрагент",
                    "legal_name": "ООО Контрагент",
                    "short_name": "Контрагент",
                    "phone": "+7 900 000-00-01",
                    "email": "info@example.com",
                    "inn": "2468000000",
                    "kpp": "246801001",
                    "ogrn": "1234567890123",
                    "checking_account": "40702810900000000001",
                    "bank_name": "Тест Банк",
                    "bik": "044525225",
                    "correspondent_account": "30101810400000000225",
                    "legal_address": "660000, г. Красноярск, ул. Тестовая, 1",
                    "contact_person": "Иванов Иван",
                    "contact_position": "Директор",
                }
            },
        )
        self.assertEqual(status, 200)

        status, card_created = self.request(
            "/api/create_card",
            {"vehicle": "Toyota Camry", "title": "Invoice autodetect", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = card_created["data"]["card"]["id"]

        status, updated = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "ООО Контрагент",
                    "phone": "+7 900 000-00-01",
                    "vehicle": "Toyota Camry",
                    "works": [
                        {
                            "name": "Диагностика",
                            "quantity": "1",
                            "price": "1500",
                            "total": "",
                        }
                    ],
                    "materials": [
                        {"name": "Расходник", "quantity": "1", "price": "500", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["card"]["repair_order"]["client"], "ООО Контрагент")

        status, preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "card_id": card_id,
                "selected_document_ids": ["invoice"],
                "active_document_id": "invoice",
            },
        )
        self.assertEqual(status, 200)
        html = preview["data"]["documents"][0]["pages"][0]["html"]
        self.assertIn("Реквизиты покупателя", html)
        self.assertIn("ООО Контрагент", html)
        self.assertIn("2468000000", html)
        self.assertIn("40702810900000000001", html)
        self.assertIn("Тест Банк", html)
        self.assertNotIn("Реквизиты клиента не указаны", html)

    def test_inspection_sheet_form_routes_save_preview_and_autofill(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Mazda CX-3",
                "title": "Fill inspection sheet",
                "description": "Suspension noise, inspection required.",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, _ = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Nina Yarulina",
                    "vehicle": "Mazda CX-3",
                    "vin": "DK5FW106086",
                    "license_plate": "A123AA124",
                    "reason": "Suspension noise",
                    "comment": "Client asked for chassis inspection",
                    "note": "Stabilizer link play found",
                    "works": [
                        {
                            "name": "Suspension diagnosis",
                            "quantity": "1",
                            "price": "1800",
                            "total": "",
                        }
                    ],
                    "materials": [
                        {"name": "Stabilizer link", "quantity": "2", "price": "900", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)

        status, loaded = self.request("/api/get_inspection_sheet_form", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertIn("complaint_summary", loaded["data"]["form"])
        self.assertIn("planned_work_rows", loaded["data"]["form"])
        self.assertIn("planned_material_rows", loaded["data"]["form"])

        status, saved = self.request(
            "/api/save_inspection_sheet_form",
            {
                "card_id": card_id,
                "form_data": {
                    "client": "Nina Yarulina",
                    "vehicle": "Mazda CX-3",
                    "vin_or_plate": "DK5FW106086 ? A123AA124",
                    "complaint_summary": "Suspension noise",
                    "findings": "Stabilizer link play",
                    "recommendations": "Replace stabilizer links",
                    "planned_works": "Replace stabilizer links",
                    "planned_materials": "Stabilizer link",
                    "planned_work_rows": [
                        {"name": "Replace stabilizer links", "quantity": "1"},
                        {"name": "Check bushings", "quantity": ""},
                    ],
                    "planned_material_rows": [
                        {"name": "Stabilizer link", "quantity": "2"},
                    ],
                    "master_comment": "Approve estimate",
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved["data"]["form"]["vehicle"], "Mazda CX-3")
        self.assertEqual(saved["data"]["form"]["planned_work_rows"][1]["name"], "Check bushings")

        status, preview = self.request(
            "/api/preview_repair_order_print_documents",
            {
                "card_id": card_id,
                "selected_document_ids": ["inspection_sheet"],
                "active_document_id": "inspection_sheet",
            },
        )
        self.assertEqual(status, 200)
        html = preview["data"]["documents"][0]["pages"][0]["html"]
        self.assertIn("Mazda CX-3", html)
        self.assertIn("Stabilizer link play", html)
        self.assertIn("Replace stabilizer links", html)
        self.assertIn("Check bushings", html)
        self.assertIn("Stabilizer link", html)
        self.assertIn('<td class="doc-table__narrow">—</td>', html)
        self.assertNotIn("вЂ”", html)

        with patch("minimal_kanban.services.card_service.OpenAIJsonAgentClient") as client_cls:
            client = client_cls.return_value
            client.model = "gpt-5.4-mini"
            client.complete_json.return_value = {
                "client": "Nina Yarulina",
                "vehicle": "Mazda CX-3",
                "vin_or_plate": "DK5FW106086 ? A123AA124",
                "complaint_summary": "Suspension noise and vibration",
                "findings": ["Stabilizer link play", "Bushing wear"],
                "recommendations": ["Replace links", "Check bushings"],
                "planned_works": ["Replace stabilizer links", "Check bushings"],
                "planned_materials": ["Stabilizer link", "Stabilizer bushing"],
                "planned_work_rows": [
                    {"name": "Replace stabilizer links", "quantity": "1"},
                    {"name": "Check bushings", "quantity": "1"},
                ],
                "planned_material_rows": [
                    {"name": "Stabilizer link", "quantity": "2"},
                    {"name": "Stabilizer bushing", "quantity": "2"},
                ],
                "master_comment": "Prepare estimate",
                "confidence_notes": ["Part of the data came from the card description"],
            }
            status, autofilled = self.request(
                "/api/autofill_inspection_sheet_form", {"card_id": card_id}
            )
        self.assertEqual(status, 200)
        self.assertEqual(autofilled["data"]["form"]["master_comment"], "Prepare estimate")
        self.assertEqual(autofilled["data"]["autofill"]["model"], "gpt-5.4-mini")
        self.assertEqual(
            autofilled["data"]["form"]["planned_material_rows"][1]["name"], "Stabilizer bushing"
        )
        self.assertIn(
            "Part of the data came from the card description",
            autofilled["data"]["autofill"]["confidence_notes"][0],
        )

    def test_repair_order_print_pdf_export_works_from_http_thread(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Lexus IS F",
                "title": "Threaded PDF export",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, _ = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "vehicle": "Lexus IS F",
                    "vin": "USE205004751",
                    "works": [
                        {"name": "Замена масла", "quantity": "1", "price": "2500", "total": ""}
                    ],
                    "materials": [
                        {"name": "Масло 5W-30", "quantity": "6", "price": "950", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)

        status, exported = self.request(
            "/api/export_repair_order_print_pdf",
            {
                "card_id": card_id,
                "selected_document_ids": ["repair_order"],
                "active_document_id": "repair_order",
            },
            timeout=30,
        )
        self.assertEqual(status, 200)
        content = base64.b64decode(exported["data"]["content_base64"])
        self.assertTrue(content.startswith(b"%PDF"))
        self.assertEqual(exported["data"]["mime_type"], "application/pdf")
        self.assertEqual(exported["data"]["meta"]["documents"][0]["id"], "repair_order")

    def test_save_print_module_settings_route_persists_workspace_settings(self) -> None:
        status, saved = self.request(
            "/api/save_print_module_settings",
            {
                "print_settings": {
                    "default_printer": "",
                    "copies": 2,
                    "paper_size": "A5",
                    "orientation": "landscape",
                    "service_profile": {
                        "company_name": "AutoStop CRM",
                        "phone": "+7 900 123-45-67",
                    },
                }
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved["data"]["settings"]["copies"], 2)
        self.assertEqual(saved["data"]["settings"]["paper_size"], "A5")
        self.assertEqual(saved["data"]["settings"]["orientation"], "landscape")
        self.assertEqual(
            saved["data"]["settings"]["service_profile"]["company_name"], "AutoStop CRM"
        )

        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Toyota Camry",
                "title": "Workspace settings reuse",
                "deadline": {"hours": 2},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, workspace = self.request(
            "/api/get_repair_order_print_workspace", {"card_id": card_id}
        )
        self.assertEqual(status, 200)
        self.assertEqual(workspace["data"]["settings"]["copies"], 2)
        self.assertEqual(workspace["data"]["settings"]["paper_size"], "A5")
        self.assertEqual(workspace["data"]["settings"]["orientation"], "landscape")
        self.assertEqual(
            workspace["data"]["settings"]["service_profile"]["company_name"], "AutoStop CRM"
        )

    def test_autofill_repair_order_route_uses_card_and_vehicle_profile(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Volkswagen Tiguan II",
                "title": "ТО DSG/АКПП",
                "description": "Госномер А123АА124. Выполнить обслуживание и замену расходников.",
                "deadline": {"hours": 5},
                "vehicle_profile": {
                    "customer_name": "Петров Пётр",
                    "customer_phone": "+7 999 000-11-22",
                },
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, autofilled = self.request("/api/autofill_repair_order", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertTrue(autofilled["ok"])
        self.assertEqual(autofilled["data"]["repair_order"]["number"], "1")
        self.assertEqual(autofilled["data"]["repair_order"]["client"], "Петров Пётр")
        self.assertEqual(autofilled["data"]["repair_order"]["phone"], "+7 999 000-11-22")
        self.assertEqual(autofilled["data"]["repair_order"]["license_plate"], "а123аа124")
        self.assertEqual(autofilled["data"]["repair_order"]["works"], [])
        self.assertEqual(autofilled["data"]["repair_order"]["client_information"], "")
        self.assertIn("autofill_report", autofilled["data"]["meta"])

    def test_autofill_repair_order_route_returns_structured_rows_and_history_prices(self) -> None:
        vin = "WVWZZZ1KZBP123456"
        for index in range(2):
            status, created = self.request(
                "/api/create_card",
                {
                    "vehicle": "Volkswagen Tiguan II",
                    "title": f"История DSG {index}",
                    "description": "Ранее выполненные работы",
                    "deadline": {"hours": 4},
                    "vehicle_profile": {"vin": vin},
                },
            )
            self.assertEqual(status, 200)
            history_id = created["data"]["card"]["id"]
            status, _ = self.request(
                "/api/update_repair_order",
                {
                    "card_id": history_id,
                    "repair_order": {
                        "works": [
                            {
                                "name": "Диагностика DSG",
                                "quantity": "1",
                                "price": "2500",
                                "total": "",
                            }
                        ],
                        "materials": [
                            {"name": "ATF", "quantity": "6", "price": "950", "total": ""}
                        ],
                    },
                },
            )
            self.assertEqual(status, 200)

        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Volkswagen Tiguan II",
                "title": "Жалоба DSG",
                "description": "VIN WVWZZZ1KZBP123456\nЖалоба: пинки DSG.\nРаботы: Диагностика DSG\nМатериалы: ATF 6 л",
                "deadline": {"hours": 4},
                "vehicle_profile": {"vin": vin},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, autofilled = self.request("/api/autofill_repair_order", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertEqual(autofilled["data"]["repair_order"]["works"], [])
        self.assertEqual(autofilled["data"]["repair_order"]["materials"], [])
        self.assertEqual(autofilled["data"]["repair_order"]["client_information"], "")
        self.assertIn("filled_fields", autofilled["data"]["meta"]["autofill_report"])

    def test_repair_order_status_route_moves_order_between_active_list_and_archive(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Volkswagen Tiguan",
                "title": "Repair order API status",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, patched = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "payments": [
                        {"amount": "1500", "paid_at": "06.04.2026 10:00", "payment_method": "cash"}
                    ],
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1500", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(patched["data"]["repair_order"]["status"], "open")

        status, active = self.request("/api/list_repair_orders", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(active["data"]["meta"]["status"], "open")
        self.assertTrue(any(item["card_id"] == card_id for item in active["data"]["repair_orders"]))

        status, closed = self.request(
            "/api/set_repair_order_status", {"card_id": card_id, "status": "closed"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(closed["data"]["repair_order"]["status"], "closed")
        self.assertTrue(closed["data"]["repair_order"]["closed_at"])

        status, active_after = self.request("/api/list_repair_orders", method="GET")
        self.assertEqual(status, 200)
        self.assertFalse(
            any(item["card_id"] == card_id for item in active_after["data"]["repair_orders"])
        )

        status, archived = self.request("/api/list_repair_orders", {"status": "closed"})
        self.assertEqual(status, 200)
        self.assertEqual(archived["data"]["meta"]["status"], "closed")
        self.assertTrue(
            any(item["card_id"] == card_id for item in archived["data"]["repair_orders"])
        )

    def test_mark_card_ready_route_moves_order_to_ready_list(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Volkswagen Polo",
                "title": "Ready route",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        status, patched = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1500", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(patched["data"]["repair_order"]["status"], "open")

        status, marked = self.request("/api/mark_card_ready", {"card_id": card_id})

        self.assertEqual(status, 200)
        self.assertEqual(marked["data"]["card"]["column_label"], "Готовые автомобили")
        self.assertEqual(marked["data"]["card"]["repair_order"]["status"], "ready")
        self.assertIn("ГОТОВ", marked["data"]["card"]["tags"])

        status, ready = self.request("/api/list_repair_orders", {"status": "ready"})
        self.assertEqual(status, 200)
        self.assertEqual(ready["data"]["meta"]["status"], "ready")
        self.assertEqual([item["card_id"] for item in ready["data"]["repair_orders"]], [card_id])

        status, active = self.request("/api/list_repair_orders", method="GET")
        self.assertEqual(status, 200)
        self.assertFalse(
            any(item["card_id"] == card_id for item in active["data"]["repair_orders"])
        )

    def test_manager_bulk_deadline_defaults_to_dry_run_and_apply_requires_actor(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Таймер менеджера", "deadline": {"minutes": 15}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, dry_run = self.request(
            "/api/bulk_set_deadline_if_below",
            {"card_ids": [card_id], "min_total_seconds": 172800, "target_total_seconds": 172800},
        )
        self.assertEqual(status, 200)
        self.assertEqual(dry_run["data"]["eligible"], 1)
        self.assertEqual(dry_run["data"]["changed"], 0)
        self.assertEqual(dry_run["data"]["run"]["mode"], "dry_run")

        status, rejected = self.request(
            "/api/bulk_set_deadline_if_below",
            {
                "mode": "apply",
                "card_ids": [card_id],
                "min_total_seconds": 172800,
                "target_total_seconds": 172800,
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(rejected["error"]["code"], "validation_error")

        status, applied = self.request(
            "/api/bulk_set_deadline_if_below",
            {
                "mode": "apply",
                "actor_name": "CODEX MCP QA",
                "card_ids": [card_id],
                "min_total_seconds": 172800,
                "target_total_seconds": 172800,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(applied["data"]["changed"], 1)
        self.assertTrue(applied["data"]["verification"]["passed"])

        status, reread = self.request("/api/get_card", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(reread["data"]["card"]["remaining_seconds"], 172798)

    def test_manager_ready_unpaid_followups_and_compact_repair_orders(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"vehicle": "Skoda Octavia", "title": "Готов без оплаты", "deadline": {"hours": 4}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        status, _patched = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Проверочный",
                    "phone": "+7 999 111 22 33",
                    "vin": "WVWZZZ1JZXW000001",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "2000", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        status, _marked = self.request("/api/mark_card_ready", {"card_id": card_id})
        self.assertEqual(status, 200)

        status, ready_unpaid = self.request("/api/list_ready_unpaid_cards", {"limit": 10})
        self.assertEqual(status, 200)
        self.assertIn(card_id, [item["id"] for item in ready_unpaid["data"]["cards"]])

        status, dry_run = self.request("/api/apply_ready_unpaid_followups", {"limit": 10})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(dry_run["data"]["eligible"], 1)
        self.assertEqual(dry_run["data"]["changed"], 0)

        status, applied = self.request(
            "/api/apply_ready_unpaid_followups",
            {"mode": "apply", "actor_name": "CODEX MCP QA", "limit": 10},
        )
        self.assertEqual(status, 200)
        self.assertGreaterEqual(applied["data"]["changed"], 1)

        status, reread = self.request("/api/get_card", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertIn("ЖДЕТ ОПЛАТЫ", reread["data"]["card"]["tags"])

        status, compact = self.request(
            "/api/list_repair_orders",
            {"status": "ready", "compact": True, "redact_private": True},
        )
        self.assertEqual(status, 200)
        item = next(item for item in compact["data"]["repair_orders"] if item["card_id"] == card_id)
        self.assertTrue(item["client_present"])
        self.assertTrue(item["phone_present"])
        self.assertTrue(item["vin_present"])
        self.assertNotIn("phone", item)
        self.assertNotIn("vin", item)

    def test_manager_missing_data_and_update_card_compact_conflict(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Неполные данные", "deadline": {"hours": 4}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        updated_at = created["data"]["card"]["updated_at"]

        status, missing = self.request(
            "/api/list_cards_missing_manager_data",
            {"kinds": ["vin", "client_link"], "limit": 10},
        )
        self.assertEqual(status, 200)
        self.assertIn(card_id, [item["id"] for item in missing["data"]["cards"]])

        status, updated = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "title": "Неполные данные обновлены",
                "tags": ["СТАТУС", {"label": "ЖДЕМ", "color": "yellow"}],
                "expected_updated_at": updated_at,
                "response_mode": "compact",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["meta"]["response_mode"], "compact")
        self.assertIn("СТАТУС", updated["data"]["card"]["tags"])
        self.assertIn("ЖДЕМ", updated["data"]["card"]["tags"])

        status, conflict = self.request(
            "/api/update_card",
            {
                "card_id": card_id,
                "title": "Старое ожидание",
                "expected_updated_at": updated_at,
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(conflict["error"]["code"], "card_update_conflict")

    def test_repair_order_status_route_rejects_unpaid_close(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Toyota Camry",
                "title": "Неоплаченный наряд",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, patched = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1500", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(patched["data"]["repair_order"]["payment_status"], "unpaid")

        status, response = self.request(
            "/api/set_repair_order_status", {"card_id": card_id, "status": "closed"}
        )
        self.assertEqual(status, 409)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "repair_order_payment_required")
        self.assertIn("выполнить оплату", response["error"]["message"].lower())

    def test_repair_order_update_route_rejects_unpaid_closed_status(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Toyota Camry",
                "title": "Обход закрытия",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, response = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "status": "closed",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1500", "total": ""}
                    ],
                },
            },
        )

        self.assertEqual(status, 409)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "repair_order_payment_required")

    def test_repair_order_update_route_rejects_closed_order_financial_underpayment(
        self,
    ) -> None:
        status, cashbox_response = self.request(
            "/api/create_cashbox", {"name": "Касса закрытых нарядов", "actor_name": "ADMIN"}
        )
        self.assertEqual(status, 200)
        cashbox = cashbox_response["data"]["cashbox"]
        status, created = self.request(
            "/api/create_card",
            {
                "vehicle": "Toyota Camry",
                "title": "Закрытый наряд нельзя сделать неоплаченным",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, updated = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1500", "total": ""}
                    ],
                    "payments": [
                        {
                            "amount": "1500",
                            "paid_at": "06.04.2026 10:00",
                            "payment_method": "cash",
                            "cashbox_id": cashbox["id"],
                        }
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["data"]["repair_order"]["payment_status"], "paid")
        status, closed = self.request(
            "/api/set_repair_order_status", {"card_id": card_id, "status": "closed"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(closed["data"]["repair_order"]["status"], "closed")

        status, response = self.request(
            "/api/update_repair_order",
            {
                "card_id": card_id,
                "repair_order": {
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "2000", "total": ""}
                    ],
                },
            },
        )

        self.assertEqual(status, 409)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "repair_order_payment_required")
        self.assertEqual(response["error"]["details"]["due_total"], "500")

    def test_repair_order_list_route_supports_query_sort_and_tags(self) -> None:
        status, first = self.request(
            "/api/create_card",
            {
                "vehicle": "Audi A6",
                "title": "Диагностика DSG",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        first_id = first["data"]["card"]["id"]

        status, second = self.request(
            "/api/create_card",
            {
                "vehicle": "BMW X5",
                "title": "Замена масла",
                "deadline": {"hours": 4},
            },
        )
        self.assertEqual(status, 200)
        second_id = second["data"]["card"]["id"]

        status, patched_first = self.request(
            "/api/update_repair_order",
            {
                "card_id": first_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "comment": "Проверить DSG и согласовать диагностику",
                    "tags": [
                        {"label": "Срочно", "color": "yellow"},
                        {"label": "DSG", "color": "green"},
                    ],
                    "works": [
                        {"name": "Диагностика DSG", "quantity": "1", "price": "2500", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            patched_first["data"]["repair_order"]["tags"],
            [
                {"label": "СРОЧНО", "color": "yellow"},
                {"label": "DSG", "color": "green"},
            ],
        )

        status, _ = self.request(
            "/api/update_repair_order",
            {
                "card_id": second_id,
                "repair_order": {
                    "client": "Петр Петров",
                    "phone": "+7 901 000-11-22",
                    "comment": "Стандартное ТО",
                    "works": [
                        {"name": "Замена масла", "quantity": "1", "price": "1500", "total": ""}
                    ],
                },
            },
        )
        self.assertEqual(status, 200)

        status, listed = self.request(
            "/api/list_repair_orders",
            {
                "status": "all",
                "query": "срочно иван dsg",
                "sort_by": "number",
                "sort_dir": "asc",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(listed["data"]["meta"]["query"], "срочно иван dsg")
        self.assertEqual(listed["data"]["meta"]["sort_by"], "number")
        self.assertEqual(listed["data"]["meta"]["sort_dir"], "asc")
        self.assertEqual([item["card_id"] for item in listed["data"]["repair_orders"]], [first_id])
        self.assertEqual(
            listed["data"]["repair_orders"][0]["tags"],
            [
                {"label": "СРОЧНО", "color": "yellow"},
                {"label": "DSG", "color": "green"},
            ],
        )

    def test_rejects_invalid_json_and_payload_type(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/create_card",
            data=b"{broken",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as invalid_json:
            urllib.request.urlopen(request, timeout=5)
        try:
            payload = json.loads(invalid_json.exception.read().decode("utf-8"))
        finally:
            invalid_json.exception.close()
        self.assertEqual(payload["error"]["code"], "invalid_json")

        status, wrong_type = self.request("/api/create_card", payload=["not", "object"])  # type: ignore[arg-type]
        self.assertEqual(status, 400)
        self.assertEqual(wrong_type["error"]["code"], "validation_error")

    def test_validation_error_is_returned_for_wrong_input(self) -> None:
        status, response = self.request("/api/get_cards", {"include_archived": "false"})
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "validation_error")

        status, response = self.request("/api/create_column", {"label": "   "})
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "validation_error")

        status, response = self.request(
            "/api/create_card", {"title": "x", "deadline": {"days": 0, "hours": 0}}
        )
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "validation_error")

        status, response = self.request(
            "/api/create_card", {"title": "x", "deadline": {"days": 0, "hours": 24}}
        )
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "validation_error")

        status, created = self.request(
            "/api/create_card", {"title": "Карточка", "deadline": {"hours": 1}}
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, response = self.request(
            "/api/set_card_indicator", {"card_id": card_id, "indicator": "blue"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "validation_error")

    def test_snapshot_log_restore_and_search_routes(self) -> None:
        status, created_column = self.request("/api/create_column", {"label": "Электрики"})
        self.assertEqual(status, 200)
        column_id = created_column["data"]["column"]["id"]

        status, created_sticky = self.request(
            "/api/create_sticky",
            {
                "text": "Проверить втулки стабилизатора",
                "x": 120,
                "y": 90,
                "deadline": {"hours": 4},
                "actor_name": "ИНСПЕКТОР",
                "source": "api",
            },
        )
        self.assertEqual(status, 200)
        sticky_id = created_sticky["data"]["sticky"]["id"]
        self.assertTrue(created_sticky["data"]["sticky"]["short_id"].startswith("S-"))

        status, created_card = self.request(
            "/api/create_card",
            {
                "vehicle": "KIA RIO",
                "title": "ПЛАВАЕТ ХОЛОСТОЙ ХОД",
                "description": "Проверить дроссель и датчик холостого хода",
                "column": column_id,
                "tags": ["СРОЧНО", "ДИАГНОСТИКА"],
                "deadline": {"hours": 8},
                "actor_name": "ИНСПЕКТОР",
                "source": "api",
            },
        )
        self.assertEqual(status, 200)
        card_id = created_card["data"]["card"]["id"]
        card_short_id = created_card["data"]["card"]["short_id"]
        self.assertEqual(created_card["data"]["card"]["heading"], "KIA RIO / ПЛАВАЕТ ХОЛОСТОЙ ХОД")
        self.assertEqual(created_card["data"]["card"]["column_label"], "Электрики")

        status, snapshot = self.request("/api/get_board_snapshot", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(any(card["id"] == card_id for card in snapshot["data"]["cards"]))
        self.assertTrue(any(sticky["id"] == sticky_id for sticky in snapshot["data"]["stickies"]))
        self.assertGreater(snapshot["data"]["meta"]["stickies_total"], 0)
        self.assertIn("cards_returned", snapshot["data"]["meta"])
        self.assertIn("archive_returned", snapshot["data"]["meta"])

        status, log = self.request(f"/api/get_card_log?card_id={card_id}&limit=1", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(log["data"]["events"][0]["actor_name"], "ИНСПЕКТОР")
        self.assertEqual(log["data"]["meta"]["schema_version"], "card_journal.v2")
        self.assertEqual(log["data"]["meta"]["limit"], 1)
        self.assertEqual(log["data"]["meta"]["events_returned"], 1)
        self.assertEqual(log["data"]["meta"]["event_order"], "newest_first")
        self.assertIn("has_more", log["data"]["meta"])
        self.assertIn("entries", log["data"])
        self.assertIn("days", log["data"])
        self.assertIn("weeks", log["data"])
        self.assertIn("months", log["data"])
        self.assertIn("timeline", log["data"])
        self.assertIn("markdown", log["data"])
        self.assertIn("text", log["data"])
        self.assertIn("icon", log["data"]["entries"][0])
        self.assertIn("action_label", log["data"]["entries"][0])
        self.assertIn("source_label", log["data"]["entries"][0])
        self.assertIn("changes", log["data"]["entries"][0])
        self.assertIn("journal_blocks", log["data"]["entries"][0])
        self.assertEqual(log["data"]["text"], log["data"]["markdown"])
        self.assertTrue(log["data"]["text"].startswith("# 🧾 Журнал карточки"))

        status, compact_log = self.request(
            f"/api/get_card_log?card_id={card_id}&compact=1&limit=1", method="GET"
        )
        self.assertEqual(status, 200)
        self.assertTrue(compact_log["data"]["meta"]["compact"])
        self.assertEqual(compact_log["data"]["meta"]["format"], "json_compact")
        self.assertEqual(compact_log["data"]["meta"]["limit"], 1)
        self.assertEqual(compact_log["data"]["meta"]["events_returned"], 1)
        self.assertIn("entries", compact_log["data"])
        self.assertIn("days", compact_log["data"])
        self.assertNotIn("events", compact_log["data"])
        self.assertNotIn("markdown", compact_log["data"])
        self.assertNotIn("text", compact_log["data"])

        status, archived = self.request("/api/archive_card", {"card_id": card_id})
        self.assertEqual(status, 200)
        self.assertTrue(archived["data"]["card"]["archived"])

        status, archive_list = self.request("/api/list_archived_cards", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(any(card["id"] == card_id for card in archive_list["data"]["cards"]))
        self.assertGreaterEqual(archive_list["data"]["meta"]["total"], 1)
        self.assertGreaterEqual(archive_list["data"]["meta"]["returned"], 1)

        status, restored = self.request(
            "/api/restore_card", {"card_id": card_id, "column": column_id}
        )
        self.assertEqual(status, 200)
        self.assertFalse(restored["data"]["card"]["archived"])

        status, searched = self.request(
            "/api/search_cards",
            {"query": "rio дроссель", "column": column_id, "tag": "срочно", "limit": 5},
        )
        self.assertEqual(status, 200)
        self.assertEqual(searched["data"]["meta"]["total_matches"], 1)
        self.assertFalse(searched["data"]["meta"]["has_more"])
        self.assertEqual(searched["data"]["cards"][0]["id"], card_id)

        status, searched_by_short_id = self.request(
            "/api/search_cards", {"query": card_short_id, "limit": 5}
        )
        self.assertEqual(status, 200)
        self.assertEqual(searched_by_short_id["data"]["cards"][0]["id"], card_id)

        status, wall = self.request(
            "/api/get_gpt_wall", {"include_archived": True, "event_limit": 50}
        )
        self.assertEqual(status, 200)
        self.assertIn(card_short_id, wall["data"]["text"])
        self.assertIn("sections", wall["data"])
        self.assertIn("board_content", wall["data"]["sections"])
        self.assertIn("event_log", wall["data"]["sections"])
        self.assertTrue(wall["data"]["text"].startswith("# AutoStop CRM Board Content"))
        self.assertEqual(wall["data"]["meta"]["text_format"], "markdown")
        self.assertEqual(
            wall["data"]["sections"]["board_content"]["meta"]["text_format"], "markdown"
        )
        self.assertEqual(wall["data"]["sections"]["event_log"]["meta"]["text_format"], "markdown")
        self.assertTrue(any(card["id"] == card_id for card in wall["data"]["cards"]))
        wall_card = next(card for card in wall["data"]["cards"] if card["id"] == card_id)
        self.assertIn("vehicle_profile_compact", wall_card)
        self.assertTrue(any(event["card_id"] == card_id for event in wall["data"]["events"]))
        self.assertIn(card_short_id, wall["data"]["sections"]["board_content"]["text"])
        self.assertTrue(
            any(
                event["card_id"] == card_id
                for event in wall["data"]["sections"]["event_log"]["events"]
            )
        )

        status, board_content = self.request(
            "/api/get_board_content", {"include_archived": True, "view_mode": "agent"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(board_content["data"]["text"].startswith("# AutoStop CRM Board Content"))
        self.assertEqual(board_content["data"]["meta"]["section_kind"], "board_content")
        self.assertEqual(board_content["data"]["meta"]["response_mode"], "agent_context")
        self.assertEqual(board_content["data"]["meta"]["view_mode"], "agent")
        self.assertTrue(board_content["data"]["meta"]["cards_compact"])
        self.assertIn(card_short_id, board_content["data"]["text"])

        status, board_events = self.request(
            "/api/get_board_events", {"include_archived": True, "event_limit": 50}
        )
        self.assertEqual(status, 200)
        self.assertTrue(board_events["data"]["text"].startswith("# AutoStop CRM Event Log"))
        self.assertEqual(board_events["data"]["meta"]["section_kind"], "event_log")
        self.assertEqual(board_events["data"]["meta"]["response_mode"], "audit")
        self.assertEqual(board_events["data"]["meta"]["event_limit"], 50)
        self.assertTrue(
            any(event["card_id"] == card_id for event in board_events["data"]["events"])
        )

        status, board_content_get = self.request(
            "/api/get_board_content?include_archived=true&view_mode=agent",
            method="GET",
        )
        self.assertEqual(status, 200)
        self.assertIn(card_short_id, board_content_get["data"]["text"])

        status, board_settings = self.request(
            "/api/update_board_settings", {"board_scale": 1.25, "actor_name": "ИНСПЕКТОР"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(board_settings["data"]["settings"]["board_scale"], 1.25)

        status, updated_snapshot = self.request("/api/get_board_snapshot", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(updated_snapshot["data"]["settings"]["board_scale"], 1.25)


class ApiServerAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        state_file = Path(self.temp_dir.name) / "state.json"
        logger = logging.getLogger(f"test.api.auth.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.logger = logger
        self.store = JsonStore(state_file=state_file, logger=logger)
        self.service = CardService(
            self.store,
            logger,
            attachments_dir=Path(self.temp_dir.name) / "attachments",
            repair_orders_dir=Path(self.temp_dir.name) / "repair-orders",
        )
        self.port = TEST_API_PORT_START
        self.server = ApiServer(
            self.service,
            logger,
            start_port=self.port,
            fallback_limit=TEST_API_PORT_FALLBACK_LIMIT,
            bearer_token="secret-token",
        )
        self.server.start()
        self.port = self.server.port
        self.base_url = self.server.base_url

    def tearDown(self) -> None:
        self.server.stop()
        self.temp_dir.cleanup()

    def request(
        self,
        path: str,
        payload: dict | None = None,
        *,
        method: str = "POST",
        token: str | None = None,
    ):
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        attempts = 2 if method.upper() == "GET" else 1
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                try:
                    return exc.code, json.loads(exc.read().decode("utf-8"))
                finally:
                    exc.close()
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionAbortedError,
                ConnectionResetError,
            ) as exc:
                if attempt + 1 < attempts and is_transient_request_error(exc):
                    time.sleep(0.05)
                    continue
                raise
        raise AssertionError("unreachable request retry state")

    def test_mutating_routes_require_bearer_token(self) -> None:
        status, health = self.request("/api/health", method="GET")
        self.assertEqual(status, 200)
        self.assertTrue(health["data"]["auth_required"])

        status, unauthorized = self.request(
            "/api/create_card",
            {"title": "Закрыто", "deadline": {"hours": 1}},
        )
        self.assertEqual(status, 401)
        self.assertEqual(unauthorized["error"]["code"], "unauthorized")

        status, authorized = self.request(
            "/api/create_card",
            {"title": "Открыто", "deadline": {"hours": 1}},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        self.assertTrue(authorized["ok"])

    def test_query_access_token_supports_browser_share_flow(self) -> None:
        status, created = self.request(
            "/api/create_card?access_token=secret-token",
            {"title": "РџРѕ СЃСЃС‹Р»РєРµ", "deadline": {"hours": 2}},
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]

        status, attachment = self.request(
            "/api/add_card_attachment?access_token=secret-token",
            {
                "card_id": card_id,
                "file_name": "report.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(b"hello").decode("ascii"),
            },
        )
        self.assertEqual(status, 200)
        attachment_id = attachment["data"]["attachment"]["id"]

        status, snapshot = self.request(
            "/api/get_board_snapshot?archive_limit=10&access_token=secret-token", method="GET"
        )
        self.assertEqual(status, 200)
        self.assertTrue(any(card["id"] == card_id for card in snapshot["data"]["cards"]))

        request = urllib.request.Request(
            f"{self.base_url}/api/attachment?card_id={card_id}&attachment_id={attachment_id}&access_token=secret-token",
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"hello")

    def test_error_response_redacts_query_access_token_from_path_details(self) -> None:
        status, response = self.request(
            "/api/missing_route?access_token=secret-token&padding=visible",
            method="GET",
        )

        self.assertEqual(status, 404)
        self.assertEqual(response["error"]["details"]["path"], "/api/missing_route?<redacted>")
        self.assertNotIn("secret-token", json.dumps(response, ensure_ascii=False))

    def test_query_access_token_keeps_numeric_token_as_string(self) -> None:
        server = ApiServer(
            self.service,
            self.logger,
            start_port=0,
            fallback_limit=TEST_API_PORT_FALLBACK_LIMIT,
            bearer_token="1",
        )
        try:
            server.start()
            request = urllib.request.Request(
                f"{server.base_url}/api/get_board_snapshot?access_token=1",
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                status = response.status
                payload = json.loads(response.read().decode("utf-8"))
        finally:
            server.stop()

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_query_access_token_rejects_oversized_post_query_before_auth(self) -> None:
        with patch("minimal_kanban.api.server.MAX_QUERY_STRING_BYTES", 24):
            status, response = self.request(
                "/api/create_card?access_token=secret-token&padding=xxxxxxxx",
                {"title": "Too much query", "deadline": {"hours": 1}},
            )

        self.assertEqual(status, 414)
        self.assertEqual(response["error"]["code"], "request_too_large")

    def test_attachment_api_roundtrip_preserves_headers_for_required_formats(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Attachment headers", "deadline": {"hours": 2}},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        samples = [
            ("фото клиента.png", "image/png", PNG_1X1_BYTES),
            ("фото клиента.jpg", "image/jpeg", JPEG_1X1_BYTES),
            (
                "отчёт клиента.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                minimal_docx_bytes(),
            ),
            (
                "смета клиента.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                minimal_xlsx_bytes(),
            ),
            ("заметки клиента.txt", "text/plain", minimal_text_bytes()),
            ("договор.final.pdf", "application/pdf", minimal_pdf_bytes()),
        ]

        for file_name, mime_type, payload in samples:
            with self.subTest(file_name=file_name):
                status, upload = self.request(
                    "/api/add_card_attachment",
                    {
                        "card_id": card_id,
                        "file_name": file_name,
                        "mime_type": mime_type,
                        "content_base64": base64.b64encode(payload).decode("ascii"),
                    },
                    token="secret-token",
                )
                self.assertEqual(status, 200)
                attachment_id = upload["data"]["attachment"]["id"]

                request = urllib.request.Request(
                    f"{self.base_url}/api/attachment?card_id={card_id}&attachment_id={attachment_id}",
                    headers={"Authorization": "Bearer secret-token"},
                    method="GET",
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    header = response.headers["Content-Disposition"]
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), payload)
                    self.assertEqual(response.headers.get_content_type(), mime_type)
                    self.assertIn('filename="', header)
                    self.assertIn("filename*=", header)
                    self.assertIn(quote(file_name, safe=""), header)
                    self.assertIn("X-Content-Type-Options", response.headers)

    def test_attachment_read_routes_return_bounded_agent_payload(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Attachment agent read", "deadline": {"hours": 2}},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        status, upload = self.request(
            "/api/add_card_attachment",
            {
                "card_id": card_id,
                "file_name": "agent-note.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(minimal_text_bytes()).decode("ascii"),
            },
            token="secret-token",
        )
        self.assertEqual(status, 200)
        attachment_id = upload["data"]["attachment"]["id"]

        status, listed = self.request(
            "/api/list_card_attachments",
            {"card_id": card_id},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(listed["data"]["attachments"][0]["content_kind"], "text")

        status, metadata = self.request(
            "/api/get_card_attachment",
            {"card_id": card_id, "attachment_id": attachment_id},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(metadata["data"]["attachment"]["id"], attachment_id)
        self.assertIn("sha256", metadata["data"]["attachment"])

        status, read = self.request(
            "/api/read_card_attachment",
            {"card_id": card_id, "attachment_id": attachment_id, "max_chars": 12},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(read["data"]["content"]["text"], "Привет, влож")
        self.assertTrue(read["data"]["content"]["text_truncated"])

    def test_attachment_api_rejects_disallowed_and_mismatched_files(self) -> None:
        status, created = self.request(
            "/api/create_card",
            {"title": "Attachment validation", "deadline": {"hours": 2}},
            token="secret-token",
        )
        self.assertEqual(status, 200)
        card_id = created["data"]["card"]["id"]
        cases = [
            ("malware.exe", "application/x-msdownload", b"MZ\x90\x00"),
            ("script.js", "application/javascript", b"alert(1);"),
            ("report.exe.pdf", "application/pdf", minimal_pdf_bytes()),
            ("report.pdf", "application/pdf", b"MZ\x00\x02\x03\x00"),
        ]

        for file_name, mime_type, payload in cases:
            with self.subTest(file_name=file_name):
                status, response = self.request(
                    "/api/add_card_attachment",
                    {
                        "card_id": card_id,
                        "file_name": file_name,
                        "mime_type": mime_type,
                        "content_base64": base64.b64encode(payload).decode("ascii"),
                    },
                    token="secret-token",
                )
                self.assertEqual(status, 400)
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], "validation_error")

    def test_board_context_route_describes_single_board_scope(self) -> None:
        status, created_column = self.request(
            "/api/create_column", {"label": "КЛИЕНТСКИЙ ЗАЛ"}, token="secret-token"
        )
        self.assertEqual(status, 200)
        column_id = created_column["data"]["column"]["id"]

        status, _ = self.request(
            "/api/create_card",
            {"title": "Перезвонить владельцу", "column": column_id, "deadline": {"hours": 4}},
            token="secret-token",
        )
        self.assertEqual(status, 200)

        status, context = self.request("/api/get_board_context", method="GET", token="secret-token")
        self.assertEqual(status, 200)
        self.assertTrue(context["ok"])
        self.assertEqual(context["data"]["context"]["board_name"], "Current AutoStop CRM Board")
        self.assertEqual(context["data"]["context"]["board_key"], "autostopcrm/current-board")
        self.assertEqual(context["data"]["context"]["board_scope"], "single_local_board_instance")
        self.assertIn("Do not use it for Trello, YouGile", context["data"]["context"]["scope_rule"])
        self.assertNotIn("minimal-kanban", json.dumps(context, ensure_ascii=False))
        self.assertTrue(
            any(column["id"] == column_id for column in context["data"]["context"]["columns"])
        )
        self.assertIn("[BOARD CONTEXT]", context["data"]["text"])


if __name__ == "__main__":
    unittest.main()
