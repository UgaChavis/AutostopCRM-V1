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

from minimal_kanban.mcp.server import create_mcp_server
from tests.test_agent_gateway_v2 import GATEWAY_ENV, FakeBoardApi


class DashboardBoardApi(FakeBoardApi):
    def __init__(self) -> None:
        super().__init__()
        self.display_dashboard_message = {
            "schema_version": "display_dashboard_message.v1",
            "body_html": "",
            "image_file_ids": [],
            "updated_at": "",
            "updated_by": "",
            "revision": "dashboard-revision-1",
        }

    def _request(
        self,
        path: str,
        payload: dict | None = None,
        *,
        method: str = "POST",
        extra_headers: dict[str, str] | None = None,
    ) -> dict:
        request_payload = dict(payload or {})
        if path == "/api/get_display_dashboard":
            self.raw_requests.append(
                {
                    "path": path,
                    "payload": request_payload,
                    "method": method,
                    "extra_headers": dict(extra_headers or {}),
                }
            )
            return {
                "ok": True,
                "data": {
                    "schema_version": "display_dashboard.v3",
                    "message_board": dict(self.display_dashboard_message),
                    "weeks": [],
                },
            }
        if path == "/api/update_board_settings":
            self.raw_requests.append(
                {
                    "path": path,
                    "payload": request_payload,
                    "method": method,
                    "extra_headers": dict(extra_headers or {}),
                }
            )
            if (
                request_payload.get("expected_revision")
                != self.display_dashboard_message["revision"]
            ):
                return {"ok": False, "error": {"code": "revision_conflict"}}
            requested_message = request_payload.get("display_dashboard_message") or {}
            proposed = {
                "schema_version": "display_dashboard_message.v1",
                "body_html": str(requested_message.get("body_html") or ""),
                "image_file_ids": list(requested_message.get("image_file_ids") or []),
                "updated_at": "2026-07-26T12:00:00+07:00",
                "updated_by": str(request_payload.get("actor_name") or ""),
                "revision": "dashboard-revision-2",
            }
            if request_payload.get("dry_run") is not True:
                self.display_dashboard_message = proposed
            return {
                "ok": True,
                "data": {
                    "settings": {"display_dashboard_message": proposed},
                    "meta": {"dry_run": request_payload.get("dry_run") is True},
                },
            }
        return super()._request(
            path,
            payload,
            method=method,
            extra_headers=extra_headers,
        )


class DisplayDashboardGatewayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(self._testMethodName)
        self.logger.addHandler(logging.NullHandler())
        self.env = patch.dict("os.environ", GATEWAY_ENV, clear=False)
        self.manager_patch = patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools")
        self.env.start()
        self.manager_register = self.manager_patch.start()

    def tearDown(self) -> None:
        self.manager_patch.stop()
        self.env.stop()

    async def test_named_document_dashboard_message_dry_run_apply_and_readback(self) -> None:
        ledger_state = {"next_run_id": 100, "statuses": {}}

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
                ledger_state["next_run_id"] += 1
                run_id = ledger_state["next_run_id"]
                ledger_state["statuses"][run_id] = "planned"
                return {
                    "ok": True,
                    "run_id": run_id,
                    "status": "planned",
                    "summary": {"id": run_id, "deduplicated": False},
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
                del message, verification, summary, expected_state_version
                ledger_state["statuses"][run_id] = status
                return {"ok": True, "run_id": run_id, "status": status, "summary": {}}

        self.manager_register.side_effect = register_fake_ledger
        board_api = DashboardBoardApi()
        server = create_mcp_server(
            board_api,
            self.logger,
            host="127.0.0.1",
            port=41840,
            path="/mcp",
            public_endpoint_url="https://crm.example/mcp",
        )
        tool = server._tool_manager.get_tool("agent_document_workflow")
        payload = {
            "expected_revision": "dashboard-revision-1",
            "display_dashboard_message": {
                "body_html": "<p>Проверить подъёмник</p>",
                "image_file_ids": [],
            },
        }

        dry_run = await tool.run(
            {
                "operation": "update_display_dashboard_message",
                "payload": payload,
                "idempotency_key": "dashboard-message-dry-v1",
                "mode": "dry_run",
            },
            convert_result=False,
        )
        applied = await tool.run(
            {
                "operation": "update_display_dashboard_message",
                "payload": payload,
                "idempotency_key": "dashboard-message-apply-v1",
                "mode": "apply",
            },
            convert_result=False,
        )

        self.assertTrue(dry_run.structuredContent["ok"])
        self.assertTrue(dry_run.structuredContent["verification"]["passed"])
        self.assertEqual(
            dry_run.structuredContent["verification"]["check"],
            "display_dashboard_message_dry_run_without_write",
        )
        self.assertTrue(applied.structuredContent["ok"])
        self.assertTrue(applied.structuredContent["verification"]["passed"])
        self.assertEqual(
            applied.structuredContent["verification"]["check"],
            "exact_display_dashboard_message_readback",
        )
        self.assertEqual(
            board_api.display_dashboard_message["body_html"],
            "<p>Проверить подъёмник</p>",
        )
