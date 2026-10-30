from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.services_case import CardServiceCase
from minimal_kanban.agent.control import AgentControlService
from minimal_kanban.agent.runner import AgentRunner
from minimal_kanban.agent.tools import AgentToolExecutor
from minimal_kanban.api.route_registry import build_service_routes
from minimal_kanban.mcp.client import BoardApiClient
from minimal_kanban.services.errors import ServiceError


class AgentContextReadonlyTests(CardServiceCase):
    def setUp(self):
        self.appdata = tempfile.TemporaryDirectory(prefix="synthetic-agent-context-")
        self.addCleanup(self.appdata.cleanup)
        env = patch.dict("os.environ", {"APPDATA": self.appdata.name})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()
        self.service = self._build_service()
        self.card_id = self.service.create_card({"title": "Synthetic agent order"})["card"]["id"]
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        state["cards"][0]["repair_order"].update(
            {"number": "", "client": "Synthetic client", "reason": "Synthetic work"}
        )
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        self.routes = build_service_routes(
            self.service, MagicMock(), paste_shared_files_from_clipboard=lambda _: {}
        )
        self.calls = []
        self.board_api = BoardApiClient("http://127.0.0.1:1", bearer_token="synthetic")
        request = patch.object(self.board_api, "_request", side_effect=self.request)
        request.start()
        self.addCleanup(request.stop)
        self.runner = object.__new__(AgentRunner)
        self.runner._board_api = self.board_api
        self.runner._tools = AgentToolExecutor(self.board_api)

    def request(self, route, payload):
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
                result[label, str(path.relative_to(root))] = (
                    stat.st_ino,
                    stat.st_mode,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                    path.read_bytes() if path.is_file() else None,
                )
        return result

    def test_autofill_context_primary_read_does_not_materialize_text(self):
        before = self.snapshot()
        name, result = self.runner._load_card_autofill_context(
            card_id=self.card_id, context_args={"card_id": self.card_id}
        )
        self.assertEqual(name, "get_card_context")
        self.assertIn("Synthetic client", result["data"]["repair_order_text"]["text"])
        self.assertEqual(result["data"]["repair_order_text"]["file_path"], "")
        self.assertTrue(self.calls[0][1]["read_only"])
        self.assertEqual(before, self.snapshot())

    def test_autofill_context_fallback_preserves_order_and_artifacts(self):
        before = self.snapshot()
        with patch.object(self.runner._tools, "execute", side_effect=RuntimeError("synthetic")):
            name, result = self.runner._load_card_autofill_context(
                card_id=self.card_id, context_args={"card_id": self.card_id}
            )
        self.assertEqual(name, "get_card")
        self.assertIn("Synthetic client", result["data"]["repair_order_text"]["text"])
        self.assertEqual(result["data"]["repair_order_text"]["file_path"], "")
        self.assertIn(
            ("/api/get_repair_order", {"card_id": self.card_id, "create_if_missing": False}),
            self.calls,
        )
        self.assertIn(
            ("/api/get_repair_order_text", {"card_id": self.card_id, "read_only": True}),
            self.calls,
        )
        self.assertEqual(before, self.snapshot())

    def test_verification_read_preserves_order_and_artifacts(self):
        before = self.snapshot()
        result = self.runner._read_verification_state(self.card_id)
        self.assertIn("Synthetic client", result["repair_order_text"]["text"])
        self.assertTrue(self.calls[0][1]["read_only"])
        self.assertEqual(before, self.snapshot())

    def test_scope_prompt_read_preserves_order_and_artifacts(self):
        before = self.snapshot()
        prompt = self.runner._build_scope_prompt_block(
            {"scope": {"type": "current_card", "card_id": self.card_id}}
        )
        self.assertIn("Synthetic client", prompt)
        self.assertTrue(self.calls[0][1]["read_only"])
        self.assertEqual(before, self.snapshot())

    def test_board_control_context_scan_preserves_order_and_artifacts(self):
        storage = MagicMock()
        storage.has_active_task_for_card.return_value = False
        control = AgentControlService(storage)
        control._board_service = self.service
        moment = datetime(2026, 10, 30, 10, tzinfo=UTC)
        preflight = {
            "settings": {"cooldown_minutes": 60},
            "runtime": {"last_baseline_at": "2026-10-29T10:00:00+00:00"},
            "now": moment,
            "now_text": moment.isoformat(),
        }
        snapshot = {
            "columns": [{"cards": [{"id": self.card_id, "updated_at": moment.isoformat()}]}]
        }
        before = self.snapshot()
        with (
            patch.object(control, "_board_control_preflight", return_value=preflight),
            patch.object(self.service, "get_board_snapshot", return_value=snapshot),
            patch.object(
                self.service, "get_card_context", wraps=self.service.get_card_context
            ) as read,
            patch.object(
                control,
                "_evaluate_board_control_triggers",
                return_value={"eligible": False, "skip_reason": "synthetic", "triggers": []},
            ) as evaluate,
        ):
            result = control._trigger_board_control(force=True)
        self.assertEqual(result, {"launched": [], "failed": []})
        self.assertTrue(read.call_args.args[0]["read_only"])
        self.assertEqual(evaluate.call_args.kwargs["card"]["id"], self.card_id)
        self.assertEqual(before, self.snapshot())
