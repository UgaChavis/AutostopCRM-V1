"""Focused API checks for the shared Telegram-agent behavior diagram."""

from __future__ import annotations

import base64
import copy
import json
import logging
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.api.server import ApiServer  # noqa: E402
from minimal_kanban.mcp.oauth_provider import (  # noqa: E402
    OAUTH_AUDIT_ACTOR_HEADER,
    OAUTH_AUDIT_ASSERTION_HEADER,
    create_oauth_audit_assertion,
)
from minimal_kanban.operator_auth import OperatorAuthService  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class TelegramAgentBehaviorApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="telegram-behavior-test-")
        self.addCleanup(self.temp_dir.cleanup)
        environment = patch.dict(
            os.environ,
            {
                "MINIMAL_KANBAN_DEFAULT_ADMIN_USERNAME": "UGA",
                "MINIMAL_KANBAN_DEFAULT_ADMIN_PASSWORD": "Local-Test-Owner-Password-2026",
                "AUTOSTOP_TELEGRAM_BEHAVIOR_OWNER_LOGIN": "UGA",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.base = Path(self.temp_dir.name)
        self.logger = logging.getLogger(f"test.telegram_behavior.{self._testMethodName}")
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(state_file=self.base / "state.json", logger=self.logger)
        service = CardService(
            self.store,
            self.logger,
            attachments_dir=self.base / "attachments",
            repair_orders_dir=self.base / "repair-orders",
        )
        self.operators = OperatorAuthService(
            self.store,
            service,
            users_file=self.base / "users.json",
            logger=self.logger,
        )
        self.server = ApiServer(
            service,
            self.logger,
            operator_service=self.operators,
            host="127.0.0.1",
            start_port=0,
            bearer_token="local-telegram-behavior-test-token",
        )
        self.server.start()
        self.addCleanup(self.server.stop)
        self.owner_headers = self._login("UGA", "Local-Test-Owner-Password-2026")

    def _request(
        self,
        path: str,
        *,
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
        method: str = "GET",
    ) -> tuple[int, dict]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            self.server.base_url + path,
            data=body,
            method=method,
            headers={
                "Authorization": "Bearer local-telegram-behavior-test-token",
                "Content-Type": "application/json",
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read().decode("utf-8"))
            finally:
                error.close()

    def _login(self, username: str, password: str) -> dict[str, str]:
        status, result = self._request(
            "/api/login_operator",
            payload={"username": username, "password": password},
            method="POST",
        )
        self.assertEqual(status, 200)
        return {"X-Operator-Session": result["data"]["session"]["token"]}

    def _graph(self) -> tuple[dict, int]:
        status, result = self._request(
            "/api/get_telegram_agent_behavior",
            headers=self.owner_headers,
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["data"]["can_edit"])
        return result["data"]["graph"], result["data"]["revision"]

    def _save(self, graph: dict, revision: int, *, headers: dict[str, str] | None = None):
        return self._request(
            "/api/save_telegram_agent_behavior",
            payload={"graph": graph, "expected_revision": revision},
            headers=headers if headers is not None else self.owner_headers,
            method="POST",
        )

    @staticmethod
    def _agent_environment() -> dict[str, str]:
        return {
            "AUTOSTOP_DEPLOYMENT_ENV": "development",
            "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
            "AUTOSTOP_AGENT_SERVICE_IDENTITY": "codex-owner-agent",
            "AUTOSTOP_MCP_OAUTH_STATE_KEY": base64.urlsafe_b64encode(b"0" * 32).decode("ascii"),
            "MINIMAL_KANBAN_MCP_BEARER_TOKEN": "local-telegram-agent-service-token",
        }

    def _patch_agent(
        self,
        operations: list[dict],
        revision: int,
        key: str,
        *,
        actor: str | None = "UGA",
        signed: bool = True,
    ) -> tuple[int, dict]:
        route = "/api/patch_telegram_agent_behavior"
        payload = {
            "expected_revision": revision,
            "idempotency_key": key,
            "operations": operations,
            "source": "mcp_agent_gateway_v2",
        }
        with patch.dict(os.environ, self._agent_environment()):
            headers = {
                "X-Autostop-Agent-Identity": "codex-owner-agent",
                "X-Autostop-Agent-Token": "local-telegram-agent-service-token",
            }
            if actor is not None:
                headers[OAUTH_AUDIT_ACTOR_HEADER] = actor
                if signed:
                    headers[OAUTH_AUDIT_ASSERTION_HEADER] = create_oauth_audit_assertion(
                        subject=actor,
                        method="POST",
                        route=route,
                        payload=payload,
                    )
            return self._request(route, payload=payload, headers=headers, method="POST")

    def _create_other_admin(self) -> dict[str, str]:
        owner_session = self.operators.resolve_session(self.owner_headers["X-Operator-Session"])
        self.operators.save_user(
            {
                "_operator_session": owner_session,
                "username": "OTHER",
                "password": "Local-Test-Other-Password-2026",
                "role": "admin",
            }
        )
        return self._login("OTHER", "Local-Test-Other-Password-2026")

    def test_seed_is_normalized_to_editable_graph(self) -> None:
        graph, revision = self._graph()
        self.assertEqual(revision, 0)
        self.assertEqual(graph["schema_version"], "autostopcrm.telegram-agent-behavior.v2")
        self.assertEqual({node["id"] for node in graph["elements"]}, {f"B{i}" for i in range(1, 7)})
        self.assertEqual(
            {edge["id"] for edge in graph["relations"]}, {f"L{i}" for i in range(1, 6)}
        )
        self.assertTrue(all("path" not in edge for edge in graph["relations"]))

    def test_save_then_read_preserves_editable_modules_and_connections(self) -> None:
        graph, revision = self._graph()
        graph = copy.deepcopy(graph)
        graph["elements"][0]["description"] = "Новая инструкция владельца."
        new_node = copy.deepcopy(graph["elements"][5])
        new_node.update({"id": "B7", "title": "Следующий этап", "x": 1200, "y": 600})
        graph["elements"].append(new_node)
        new_edge = copy.deepcopy(graph["relations"][0])
        new_edge.update({"id": "L6", "from": "B6", "to": "B7", "label": "следующий шаг"})
        graph["relations"].append(new_edge)

        status, saved = self._save(graph, revision)
        self.assertEqual(status, 200)
        self.assertEqual(saved["data"]["revision"], revision + 1)
        reloaded, reloaded_revision = self._graph()
        self.assertEqual(reloaded_revision, revision + 1)
        self.assertEqual(reloaded, graph)
        fresh_store = JsonStore(state_file=self.base / "state.json", logger=self.logger)
        fresh_service = CardService(
            fresh_store,
            self.logger,
            attachments_dir=self.base / "attachments",
            repair_orders_dir=self.base / "repair-orders",
        )
        fresh_read = fresh_service.get_telegram_agent_behavior(
            {
                "_operator_session": self.operators.resolve_session(
                    self.owner_headers["X-Operator-Session"]
                )
            }
        )
        self.assertEqual(fresh_read["revision"], revision + 1)
        self.assertEqual(fresh_read["graph"], graph)
        self.assertEqual(fresh_read["history"][0]["revision"], revision + 1)
        self.assertEqual(fresh_read["history"][0]["actor"], "UGA")
        self.assertEqual(fresh_read["history"][0]["source"], "owner")

    def test_agent_patch_add_update_delete_and_history_preserve_seed(self) -> None:
        original, revision = self._graph()
        module = copy.deepcopy(original["elements"][4])
        module.update(
            {
                "id": "M1",
                "title": "Новая ветка",
                "description": "Текст владельца",
                "x": 1830,
                "y": 620,
            }
        )
        relation = copy.deepcopy(original["relations"][3])
        relation.update({"id": "R1", "from": "B5", "to": "M1", "label": "переход"})
        operations = [
            {"op": "resize_canvas", "width": 2200, "height": 1000},
            {"op": "add_element", "element": module},
            {"op": "add_relation", "relation": relation},
            {
                "op": "update_element",
                "id": "B1",
                "changes": {"title": "Новый запрос", "icon": "telegram"},
            },
            {"op": "update_relation", "id": "L1", "changes": {"label": "пробуждение"}},
        ]
        status, response = self._patch_agent(operations, revision, "behavior-add-and-edit-1")
        self.assertEqual(status, 200, response)
        data = response["data"]
        self.assertEqual(data["revision"], revision + 1)
        self.assertEqual(data["applied_revision"], revision + 1)
        self.assertFalse(data["idempotent_replay"])
        self.assertNotIn("cards", data)
        current, current_revision = self._graph()
        self.assertEqual(current_revision, revision + 1)
        self.assertEqual(
            {item["id"] for item in current["elements"]}, {f"B{i}" for i in range(1, 7)} | {"M1"}
        )
        self.assertEqual(
            {item["id"] for item in current["relations"]}, {f"L{i}" for i in range(1, 6)} | {"R1"}
        )
        self.assertEqual(current["canvas"], {"width": 2200, "height": 1000})
        self.assertEqual(
            next(item for item in current["elements"] if item["id"] == "B1")["icon"], "telegram"
        )
        self.assertEqual(
            next(item for item in current["relations"] if item["id"] == "L1")["label"],
            "пробуждение",
        )
        self.assertEqual(
            {item["id"] for item in original["elements"] if item["id"] != "B1"},
            {item["id"] for item in current["elements"] if item["id"] not in {"B1", "M1"}},
        )

        status, deleted = self._patch_agent(
            [{"op": "delete_element", "id": "M1"}, {"op": "delete_relation", "id": "L3"}],
            current_revision,
            "behavior-delete-module-and-link-2",
        )
        self.assertEqual(status, 200, deleted)
        after, after_revision = self._graph()
        self.assertEqual(after_revision, revision + 2)
        self.assertEqual({item["id"] for item in after["elements"]}, {f"B{i}" for i in range(1, 7)})
        self.assertEqual({item["id"] for item in after["relations"]}, {"L1", "L2", "L4", "L5"})
        history = deleted["data"]["history"]
        self.assertEqual([item["revision"] for item in history[:2]], [revision + 2, revision + 1])
        self.assertTrue(
            all(
                item["actor"] == "Агент (UGA)" and item["source"] == "agent" for item in history[:2]
            )
        )
        self.assertTrue(all(item["summary"] for item in history[:2]))

    def test_agent_patch_requires_signed_current_oauth_owner(self) -> None:
        graph, revision = self._graph()
        other_headers = self._create_other_admin()
        operations = [
            {"op": "update_element", "id": "B1", "changes": {"title": "Разрешённая правка"}}
        ]

        status, _ = self._patch_agent(operations, revision, "without-oauth-owner", actor=None)
        self.assertEqual(status, 403)
        status, _ = self._patch_agent(operations, revision, "unsigned-owner", signed=False)
        self.assertEqual(status, 401)
        status, _ = self._patch_agent(operations, revision, "other-admin-oauth", actor="OTHER")
        self.assertEqual(status, 403)
        status, _ = self._request(
            "/api/patch_telegram_agent_behavior",
            payload={
                "expected_revision": revision,
                "idempotency_key": "human-owner",
                "operations": operations,
            },
            headers=self.owner_headers,
            method="POST",
        )
        self.assertEqual(status, 403)
        status, _ = self._request(
            "/api/patch_telegram_agent_behavior",
            payload={
                "expected_revision": revision,
                "idempotency_key": "other-admin",
                "operations": operations,
            },
            headers=other_headers,
            method="POST",
        )
        self.assertEqual(status, 403)
        self.assertEqual(self._graph(), (graph, revision))

        status, result = self._patch_agent(operations, revision, "signed-oauth-owner")
        self.assertEqual(status, 200, result)
        self.assertEqual(result["data"]["revision"], revision + 1)
        self.assertEqual(result["data"]["history"][0]["source"], "agent")

        other_session = self.operators.resolve_session(other_headers["X-Operator-Session"])
        self.operators.save_user(
            {"_operator_session": other_session, "username": "UGA", "role": "operator"}
        )
        status, _ = self._patch_agent(operations, revision + 1, "former-owner-oauth-signature")
        self.assertEqual(status, 401)
        self.assertEqual(
            self._request("/api/get_telegram_agent_behavior", headers=other_headers)[1]["data"][
                "revision"
            ],
            revision + 1,
        )

    def test_agent_patch_replay_conflict_and_invalid_batch_are_atomic(self) -> None:
        original, revision = self._graph()
        operations = [
            {"op": "update_element", "id": "B2", "changes": {"description": "Сохранено агентом"}}
        ]
        status, first = self._patch_agent(operations, revision, "agent-retry-one")
        self.assertEqual(status, 200, first)
        status, replay = self._patch_agent(operations, revision, "agent-retry-one")
        self.assertEqual(status, 200, replay)
        self.assertTrue(replay["data"]["idempotent_replay"])
        self.assertEqual(replay["data"]["applied_revision"], first["data"]["applied_revision"])
        self.assertEqual(self._graph()[1], revision + 1)

        status, stale = self._patch_agent(operations, revision, "agent-stale-version")
        self.assertEqual(status, 409, stale)
        self.assertEqual(stale["error"]["code"], "revision_conflict")

        invalid = [
            {"op": "update_element", "id": "B1", "changes": {"title": "Несохранённый текст"}},
            {
                "op": "add_relation",
                "relation": {
                    "id": "R2",
                    "from": "B1",
                    "to": "MISSING",
                    "label": "",
                    "description": "",
                },
            },
        ]
        status, rejected = self._patch_agent(invalid, revision + 1, "agent-invalid-batch")
        self.assertEqual(status, 400, rejected)
        self.assertEqual(rejected["error"]["code"], "validation_error")
        status, rejected = self._patch_agent(
            [{"op": "update_element", "id": "B1", "changes": {"icon": "unknown-icon"}}],
            revision + 1,
            "agent-invalid-icon",
        )
        self.assertEqual(status, 400, rejected)
        current, current_revision = self._graph()
        self.assertEqual(current_revision, revision + 1)
        self.assertEqual(
            next(item for item in current["elements"] if item["id"] == "B1"),
            original["elements"][0],
        )
        status, read = self._request("/api/get_telegram_agent_behavior", headers=self.owner_headers)
        self.assertEqual(status, 200)
        self.assertEqual(len(read["data"]["history"]), 1)

    def test_owner_configuration_missing_fails_closed(self) -> None:
        graph, revision = self._graph()
        with patch.dict(os.environ):
            os.environ.pop("AUTOSTOP_TELEGRAM_BEHAVIOR_OWNER_LOGIN", None)
            status, visible = self._request(
                "/api/get_telegram_agent_behavior", headers=self.owner_headers
            )
            self.assertEqual(status, 200)
            self.assertFalse(visible["data"]["can_edit"])
            status, blocked = self._save(graph, revision)
        self.assertEqual(status, 403)
        self.assertEqual(blocked["error"]["code"], "forbidden")
        self.assertEqual(self._graph()[1], revision)

    def test_only_owner_session_can_save_even_if_other_admin_or_service(self) -> None:
        graph, revision = self._graph()
        status, unauthenticated = self._save(graph, revision, headers={})
        self.assertEqual(status, 401)
        self.assertEqual(unauthenticated["error"]["code"], "unauthorized")

        owner_session = self.operators.resolve_session(self.owner_headers["X-Operator-Session"])
        self.operators.save_user(
            {
                "_operator_session": owner_session,
                "username": "OTHER",
                "password": "Local-Test-Other-Password-2026",
                "role": "admin",
            }
        )
        other_headers = self._login("OTHER", "Local-Test-Other-Password-2026")
        status, other_get = self._request("/api/get_telegram_agent_behavior", headers=other_headers)
        self.assertEqual(status, 200)
        self.assertFalse(other_get["data"]["can_edit"])
        status, blocked = self._save(graph, revision, headers=other_headers)
        self.assertEqual(status, 403)
        self.assertEqual(blocked["error"]["code"], "forbidden")

        # A trusted technical caller must not inherit the human owner's edit right.
        trusted_token = "local-telegram-agent-service-token"
        with patch.dict(
            os.environ,
            {
                "AUTOSTOP_DEPLOYMENT_ENV": "development",
                "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
                "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
                "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
                "AUTOSTOP_AGENT_SERVICE_IDENTITY": "UGA",
                "MINIMAL_KANBAN_MCP_BEARER_TOKEN": trusted_token,
            },
        ):
            trusted_headers = {
                "X-Autostop-Agent-Identity": "UGA",
                "X-Autostop-Agent-Token": trusted_token,
            }
            read_status, technical_read = self._request(
                "/api/get_telegram_agent_behavior?source=mcp_agent_gateway_v2",
                headers=trusted_headers,
            )
            status, blocked = self._request(
                "/api/save_telegram_agent_behavior",
                payload={
                    "graph": graph,
                    "expected_revision": revision,
                    "source": "mcp_agent_gateway_v2",
                },
                headers=trusted_headers,
                method="POST",
            )
        self.assertEqual(read_status, 200)
        self.assertEqual(technical_read["data"]["graph"], graph)
        self.assertFalse(technical_read["data"]["can_edit"])
        self.assertEqual(status, 403)
        self.assertEqual(blocked["error"]["code"], "forbidden")
        self.assertEqual(self._graph()[1], revision)

    def test_stale_revision_does_not_overwrite_saved_graph(self) -> None:
        graph, revision = self._graph()
        first = copy.deepcopy(graph)
        first["elements"][0]["title"] = "Первый вариант"
        status, saved = self._save(first, revision)
        self.assertEqual(status, 200)
        self.assertEqual(saved["data"]["revision"], revision + 1)

        stale = copy.deepcopy(graph)
        stale["elements"][0]["title"] = "Устаревший вариант"
        status, rejected = self._save(stale, revision)
        self.assertEqual(status, 409)
        self.assertEqual(rejected["error"]["code"], "revision_conflict")
        current, current_revision = self._graph()
        self.assertEqual(current_revision, revision + 1)
        self.assertEqual(current["elements"][0]["title"], "Первый вариант")

    def test_invalid_links_duplicate_ids_sizes_and_text_are_rejected(self) -> None:
        original, revision = self._graph()

        def dangling(graph: dict) -> None:
            graph["relations"][0]["to"] = "missing"

        def duplicate(graph: dict) -> None:
            graph["elements"].append(copy.deepcopy(graph["elements"][0]))

        def invalid_size(graph: dict) -> None:
            graph["elements"][0]["width"] = 0

        def excessive_text(graph: dict) -> None:
            graph["elements"][0]["description"] = "А" * 10001

        for corrupt in (dangling, duplicate, invalid_size, excessive_text):
            with self.subTest(problem=corrupt.__name__):
                graph = copy.deepcopy(original)
                corrupt(graph)
                status, rejected = self._save(graph, revision)
                self.assertEqual(status, 400)
                self.assertEqual(rejected["error"]["code"], "validation_error")
                self.assertEqual(self._graph()[1], revision)

    def test_browser_draw_edit_move_resize_connect_save_reload_and_delete(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright is not installed")

        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                self.skipTest("Playwright Chromium is not installed")
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                context = browser.new_context(
                    viewport={"width": 1600, "height": 1000},
                    extra_http_headers={
                        "Authorization": "Bearer local-telegram-behavior-test-token"
                    },
                )
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                token = self.owner_headers["X-Operator-Session"]
                page.add_init_script(
                    "localStorage.setItem('kanban-operator-session', " + json.dumps(token) + ")"
                )
                page.goto(self.server.base_url + "/telegram-agent-behavior")
                page.locator('[data-kind="node"]').first.wait_for()
                self.assertEqual(page.locator('[data-kind="node"]').count(), 6)

                def wire_endpoints(edge_id: str) -> dict[str, dict[str, float]]:
                    return page.locator(
                        f'[data-kind="edge"][data-id="{edge_id}"] path.wire'
                    ).evaluate(
                        """path => {
                            const first = path.getPointAtLength(0);
                            const last = path.getPointAtLength(path.getTotalLength());
                            return {start: {x: first.x, y: first.y}, end: {x: last.x, y: last.y}};
                        }"""
                    )

                # SVG endpoints must meet the actual cards, including their y coordinates.
                initial_wire = wire_endpoints("L1")
                self.assertAlmostEqual(initial_wire["start"]["x"], 325, delta=1)
                self.assertAlmostEqual(initial_wire["start"]["y"], 365, delta=1)
                self.assertAlmostEqual(initial_wire["end"]["x"], 405, delta=1)
                self.assertAlmostEqual(initial_wire["end"]["y"], 365, delta=1)
                page.locator("#toolRect").click()

                def svg_point(x: int, y: int) -> dict[str, float]:
                    return page.locator("#map").evaluate(
                        """(svg, point) => {
                            const local = svg.createSVGPoint();
                            local.x = point.x; local.y = point.y;
                            const screen = local.matrixTransform(svg.getScreenCTM());
                            return {x: screen.x, y: screen.y};
                        }""",
                        {"x": x, "y": y},
                    )

                start = svg_point(1500, 80)
                end = svg_point(1770, 210)
                page.mouse.move(start["x"], start["y"])
                page.mouse.down()
                page.mouse.move(end["x"], end["y"], steps=5)
                page.mouse.up()
                page.locator("#detailTitleInput").fill("Следующий этап")
                page.locator("#detailDescriptionInput").fill("Описание новой ветки владельца.")
                page.locator("#applyDetail").click()
                new_node = page.locator('[data-kind="node"][data-id="M1"]')
                new_node.wait_for()
                self.assertEqual(page.locator('[data-kind="node"]').count(), 7)

                page.locator("#toolSelect").click()
                original_transform = new_node.get_attribute("transform")
                start = svg_point(1610, 145)
                end = svg_point(1660, 170)
                page.mouse.move(start["x"], start["y"])
                page.mouse.down()
                page.mouse.move(end["x"], end["y"], steps=5)
                page.mouse.up()
                self.assertNotEqual(new_node.get_attribute("transform"), original_transform)

                old_width = int(new_node.locator(".node-card").get_attribute("width"))
                handle = new_node.locator(".resize-handle").bounding_box()
                assert handle is not None
                page.mouse.move(
                    handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
                )
                page.mouse.down()
                page.mouse.move(
                    handle["x"] + handle["width"] / 2 + 25,
                    handle["y"] + handle["height"] / 2 + 15,
                    steps=5,
                )
                page.mouse.up()
                self.assertGreater(
                    int(new_node.locator(".node-card").get_attribute("width")), old_width
                )

                page.locator("#toolConnect").click()
                new_node.locator(".node-card").click()
                page.locator('[data-kind="node"][data-id="B5"] .node-card').click()
                page.locator("#detailTitleInput").fill("Ветка продолжения")
                page.locator("#detailDescriptionInput").fill("Переход к следующему этапу.")
                page.locator("#applyDetail").click()
                self.assertEqual(page.locator('[data-kind="edge"]').count(), 6)
                page.locator("#saveGraph").click()
                page.get_by_text("Схема сохранена. Версия 1.").wait_for()
                self.assertIn("UGA", page.locator("#historyList").inner_text())

                page.reload()
                new_node.wait_for()
                self.assertIn("Следующий этап", new_node.text_content())
                new_edge = page.locator('[data-kind="edge"][data-id="R1"]')
                new_edge.wait_for()
                self.assertIn("Ветка продолжения", new_edge.text_content())
                saved, revision = self._graph()
                self.assertEqual(revision, 1)
                self.assertEqual(
                    next(item for item in saved["elements"] if item["id"] == "M1")["title"],
                    "Следующий этап",
                )
                self.assertEqual(
                    next(item for item in saved["relations"] if item["id"] == "R1")["to"], "B5"
                )

                new_edge.locator(".edge-label").click()
                page.locator("#deleteDetail").click()
                self.assertEqual(new_edge.count(), 0)
                new_node.locator(".node-card").click()
                page.locator("#deleteDetail").click()
                page.locator("#saveGraph").click()
                page.get_by_text("Схема сохранена. Версия 2.").wait_for()
                page.reload()
                page.locator('[data-kind="node"]').first.wait_for()
                self.assertEqual(new_node.count(), 0)
                self.assertEqual(new_edge.count(), 0)
                self.assertEqual(self._graph()[1], 2)

                map_box = page.locator("#map").bounding_box()
                assert map_box is not None
                page.mouse.move(
                    map_box["x"] + map_box["width"] / 2,
                    map_box["y"] + map_box["height"] / 2,
                )
                page.mouse.wheel(0, 500)
                page.wait_for_function(
                    "Number(document.querySelector('#map').getAttribute('viewBox').split(' ')[2]) > 1900"
                )
                small_move_node = page.locator('[data-kind="node"][data-id="B1"]')
                before_small_move = small_move_node.get_attribute("transform")
                card_box = small_move_node.locator(".node-card").bounding_box()
                assert card_box is not None
                center_x = card_box["x"] + card_box["width"] / 2
                center_y = card_box["y"] + card_box["height"] / 2
                page.mouse.move(center_x, center_y)
                page.mouse.down()
                page.mouse.move(center_x + 3, center_y + 2)
                page.mouse.up()
                self.assertNotEqual(small_move_node.get_attribute("transform"), before_small_move)
                self.assertTrue(page.locator("#saveGraph").is_enabled())
                page.locator("#saveGraph").click()
                page.get_by_text("Схема сохранена. Версия 3.").wait_for()
                small_move_graph, small_move_revision = self._graph()
                self.assertEqual(small_move_revision, 3)
                moved_b1 = next(item for item in small_move_graph["elements"] if item["id"] == "B1")
                self.assertGreater(moved_b1["x"], 70)
                self.assertGreater(moved_b1["y"], 300)
                moved_wire = wire_endpoints("L1")
                self.assertAlmostEqual(
                    moved_wire["start"]["x"], moved_b1["x"] + moved_b1["width"], delta=1
                )
                self.assertAlmostEqual(
                    moved_wire["start"]["y"], moved_b1["y"] + moved_b1["height"] / 2, delta=1
                )
                page.reload()
                small_move_node.wait_for()
                self.assertEqual(
                    small_move_node.get_attribute("transform"),
                    f"translate({moved_b1['x']} {moved_b1['y']})",
                )
                self.assertEqual(errors, [])
            finally:
                browser.close()

    def test_browser_refreshes_agent_patch_and_keeps_conflicting_owner_draft(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("Playwright is not installed")

        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).exists():
                self.skipTest("Playwright Chromium is not installed")
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                context = browser.new_context(
                    viewport={"width": 1600, "height": 1000},
                    extra_http_headers={
                        "Authorization": "Bearer local-telegram-behavior-test-token"
                    },
                )
                page = context.new_page()
                token = self.owner_headers["X-Operator-Session"]
                page.add_init_script(
                    "localStorage.setItem('kanban-operator-session', " + json.dumps(token) + ")"
                )
                page.goto(self.server.base_url + "/telegram-agent-behavior")
                first_node = page.locator('[data-kind="node"][data-id="B1"]')
                first_node.wait_for()

                status, patched = self._patch_agent(
                    [
                        {
                            "op": "update_element",
                            "id": "B1",
                            "changes": {"title": "Новый вход агента"},
                        }
                    ],
                    0,
                    "browser-agent-update-one",
                )
                self.assertEqual(status, 200, patched)
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                page.get_by_text("Схема обновлена до версии 1.").wait_for()
                self.assertIn("Новый вход агента", first_node.text_content())
                self.assertIn("Агент (UGA)", page.locator("#historyList").inner_text())

                first_node.locator(".node-card").click()
                page.locator("#detailTitleInput").fill("Мой локальный черновик")
                page.locator("#applyDetail").click()
                self.assertIn("Мой локальный черновик", first_node.text_content())
                self.assertTrue(page.locator("#saveGraph").is_enabled())

                status, patched = self._patch_agent(
                    [
                        {
                            "op": "update_element",
                            "id": "B2",
                            "changes": {"title": "Вторая правка агента"},
                        }
                    ],
                    1,
                    "browser-agent-update-two",
                )
                self.assertEqual(status, 200, patched)
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                page.locator("#conflictPanel").wait_for(state="visible")
                self.assertIn("Мой локальный черновик", first_node.text_content())
                self.assertFalse(page.locator("#saveGraph").is_enabled())
                self.assertIn("B1", page.locator("#conflictDiff").inner_text())
                self.assertIn("B2", page.locator("#conflictDiff").inner_text())
                self.assertEqual(self._graph()[1], 2)
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
