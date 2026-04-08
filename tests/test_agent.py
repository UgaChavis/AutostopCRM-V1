from __future__ import annotations

import json
import logging
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.control import AgentControlService
from minimal_kanban.agent.runner import AgentRunner
from minimal_kanban.agent.storage import AgentStorage
from minimal_kanban.api.server import ApiServer
from minimal_kanban.operator_auth import OperatorAuthService
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _FakeModelClient:
    def __init__(self, decisions: list[dict]) -> None:
        self._decisions = list(decisions)
        self.model = "fake-model"

    def next_step(self, *, system_prompt: str, messages: list[dict[str, str]]) -> dict:
        return self._decisions.pop(0)


class _FakeBoardApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def health(self) -> dict:
        self.calls.append(("health", {}))
        return {"ok": True}

    def review_board(self, **kwargs) -> dict:
        self.calls.append(("review_board", kwargs))
        return {"ok": True, "data": {"summary": {"active_cards": 1}}, "text": "board summary"}


class AgentStorageTests(unittest.TestCase):
    def test_queue_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            created = storage.enqueue_task(task_text="Review board", source="manual")
            self.assertEqual(created["status"], "pending")

            claimed = storage.claim_next_task()
            assert claimed is not None
            self.assertEqual(claimed["id"], created["id"])
            self.assertEqual(claimed["status"], "running")

            completed = storage.complete_task(
                task_id=created["id"],
                run_id="agrun_test",
                summary="done",
                result="ok",
                tool_calls=1,
            )
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(storage.list_tasks(limit=10)[0]["summary"], "done")


class AgentRunnerTests(unittest.TestCase):
    def test_runner_executes_tool_and_completes_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(task_text="Review the board.")
            logger = logging.getLogger("test.agent.runner")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            runner = AgentRunner(
                storage=storage,
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient(
                    [
                        {"type": "tool", "tool": "review_board", "args": {"priority_limit": 3}, "reason": "Need board state"},
                        {"type": "final", "summary": "Board reviewed", "result": "No blocking issues."},
                    ]
                ),
                logger=logger,
            )
            processed = runner.run_once()
            self.assertTrue(processed)
            task = storage.list_tasks(limit=1)[0]
            self.assertEqual(task["status"], "completed")
            self.assertEqual(task["summary"], "Board reviewed")
            self.assertEqual(len(storage.list_actions(limit=10)), 1)


class AgentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        state_file = Path(self.temp_dir.name) / "state.json"
        logger = logging.getLogger(f"test.agent.api.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(state_file=state_file, logger=logger)
        self.service = CardService(self.store, logger)
        self.operator_service = OperatorAuthService(
            self.store,
            self.service,
            users_file=Path(self.temp_dir.name) / "users.json",
            logger=logger,
        )
        self.agent_storage = AgentStorage(base_dir=Path(self.temp_dir.name) / "agent")
        self.agent_service = AgentControlService(self.agent_storage)
        self.port = reserve_port()
        self.server = ApiServer(
            self.service,
            logger,
            operator_service=self.operator_service,
            agent_service=self.agent_service,
            start_port=self.port,
            fallback_limit=1,
        )
        self.server.start()
        self.base_url = self.server.base_url

    def tearDown(self) -> None:
        self.server.stop()
        self.temp_dir.cleanup()

    def _request(self, path: str, payload: dict | None = None, *, method: str = "POST", headers: dict[str, str] | None = None) -> dict:
        body = None
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}{path}", data=body, headers=request_headers, method=method)
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_agent_routes_require_admin_and_enqueue_task(self) -> None:
        login = self._request("/api/login_operator", {"username": "admin", "password": "admin"})
        token = login["data"]["session"]["token"]
        headers = {"X-Operator-Session": token}
        queued = self._request("/api/agent_enqueue_task", {"task_text": "Review board"}, headers=headers)
        self.assertEqual(queued["data"]["task"]["status"], "pending")
        status = self._request("/api/agent_status", method="GET", headers=headers)
        self.assertEqual(status["data"]["queue"]["pending_total"], 1)
