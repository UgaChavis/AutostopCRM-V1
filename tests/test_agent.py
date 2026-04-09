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
from minimal_kanban.agent.automotive_tools import AutomotiveLookupService
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
        self.calls: list[dict[str, object]] = []

    def next_step(self, *, system_prompt: str, messages: list[dict[str, str]]) -> dict:
        self.calls.append({"system_prompt": system_prompt, "messages": list(messages)})
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
                display={"title": "done", "summary": "ok", "tone": "success", "sections": [], "actions": []},
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
            self.assertEqual(task["display"]["title"], "Board reviewed")
            self.assertEqual(len(storage.list_actions(limit=10)), 1)

    def test_runner_persists_structured_display_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(task_text="List overdue cards.")
            logger = logging.getLogger("test.agent.runner.display")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            runner = AgentRunner(
                storage=storage,
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient(
                    [
                        {
                            "type": "final",
                            "summary": "Overdue cards found",
                            "result": "Fallback text.",
                            "display": {
                                "emoji": "⚠️",
                                "title": "Просрочки",
                                "summary": "Найдены карточки с просрочкой.",
                                "tone": "warning",
                                "sections": [{"title": "Приоритет", "items": ["BMW X5", "Renault Duster"]}],
                                "actions": ["Покажи детали просрочек"],
                            },
                        }
                    ]
                ),
                logger=logger,
            )
            processed = runner.run_once()
            self.assertTrue(processed)
            task = storage.list_tasks(limit=1)[0]
            self.assertEqual(task["display"]["emoji"], "⚠️")
            self.assertEqual(task["display"]["tone"], "warning")
            self.assertEqual(task["display"]["sections"][0]["title"], "Приоритет")

    def test_runner_includes_card_context_in_model_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Расшифруй VIN",
                metadata={
                    "requested_by": "operator",
                    "context": {
                        "kind": "card",
                        "card_id": "card-1",
                        "title": "Диагностика",
                        "vin": "MMCJJKL10NH019836",
                    },
                },
            )
            logger = logging.getLogger("test.agent.runner.context")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            model = _FakeModelClient([{"type": "final", "summary": "done", "result": "ok"}])
            runner = AgentRunner(
                storage=storage,
                board_api=_FakeBoardApi(),
                model_client=model,
                logger=logger,
            )
            processed = runner.run_once()
            self.assertTrue(processed)
            call = model.calls[0]
            self.assertIn("decode_vin", str(call["system_prompt"]))
            self.assertIn("This task was opened from a card.", call["messages"][0]["content"])
            self.assertIn('"card_id": "card-1"', call["messages"][0]["content"])

    def test_tool_executor_exposes_automotive_internet_tools(self) -> None:
        executor = AgentRunner(
            storage=AgentStorage(base_dir=Path(tempfile.mkdtemp())),
            board_api=_FakeBoardApi(),
            model_client=_FakeModelClient([{"type": "final", "summary": "done", "result": "ok"}]),
            logger=logging.getLogger("test.agent.runner.tools"),
        )._tools
        tool_names = {item.name for item in executor.definitions}
        self.assertIn("decode_vin", tool_names)
        self.assertIn("search_part_numbers", tool_names)
        self.assertIn("lookup_part_prices", tool_names)
        self.assertIn("estimate_maintenance", tool_names)


class AutomotiveLookupServiceTests(unittest.TestCase):
    class _FakeSearch:
        def __init__(self, *, search_results: list | None = None, excerpts: dict[str, dict] | None = None) -> None:
            self._search_results = list(search_results or [])
            self._excerpts = dict(excerpts or {})

        def search(self, query: str, *, limit: int = 5, allowed_domains: list[str] | None = None) -> list:
            return self._search_results[:limit]

        def fetch_page_excerpt(self, url: str, *, max_chars: int = 2500) -> dict:
            return self._excerpts.get(url, {"url": url, "domain": "", "excerpt": ""})

    class _FakeResult:
        def __init__(self, *, title: str, url: str, snippet: str, domain: str) -> None:
            self.title = title
            self.url = url
            self.snippet = snippet
            self.domain = domain

        def to_dict(self) -> dict[str, str]:
            return {
                "title": self.title,
                "url": self.url,
                "snippet": self.snippet,
                "domain": self.domain,
            }

    def test_estimate_maintenance_returns_readable_russian_plan(self) -> None:
        service = AutomotiveLookupService()
        result = service.estimate_maintenance(
            vehicle_context={"make": "Mitsubishi", "model": "L200", "year": "2022", "vin": "MMCJJKL10NH019836"},
            service_type="ТО и тормоза",
        )
        self.assertEqual(result["service_type"], "ТО и тормоза")
        self.assertIn("Замена моторного масла", {item["name"] for item in result["works"]})
        self.assertIn("Осмотр тормозной системы", {item["name"] for item in result["works"]})
        self.assertIn("Тормозная жидкость", {item["name"] for item in result["materials"]})
        self.assertTrue(any("VIN доступен" in note for note in result["notes"]))

    def test_lookup_part_prices_extracts_price_from_snippet_before_fetch(self) -> None:
        service = AutomotiveLookupService()
        service._search = self._FakeSearch(
            search_results=[
                self._FakeResult(
                    title="Амортизатор KYB 12 500 ₽",
                    url="https://example.test/part",
                    snippet="В наличии, цена 12 500 ₽",
                    domain="example.test",
                )
            ]
        )
        result = service.lookup_part_prices(
            vehicle_context={"make": "Mitsubishi", "model": "L200", "year": "2022"},
            part_number_or_query="KYB 344123",
        )
        self.assertEqual(result["query"], "KYB 344123")
        self.assertEqual(result["results"][0]["prices"][0]["amount"], "12 500")
        self.assertEqual(result["results"][0]["prices"][0]["currency"], "₽")

    def test_search_part_numbers_uses_vin_query_when_available(self) -> None:
        calls: list[str] = []

        class _RecordingSearch(self._FakeSearch):
            def search(self, query: str, *, limit: int = 5, allowed_domains: list[str] | None = None) -> list:
                calls.append(query)
                return []

        service = AutomotiveLookupService()
        service._search = _RecordingSearch()
        result = service.search_part_numbers(
            vehicle_context={"make": "Mitsubishi", "model": "L200", "year": "2022", "vin": "MMCJJKL10NH019836"},
            part_query="рычаг передний нижний",
            limit=3,
        )
        self.assertEqual(result["vehicle_context"]["vehicle"], "Mitsubishi L200 2022")
        self.assertTrue(calls)
        self.assertIn("MMCJJKL10NH019836", calls[0])


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

    def test_agent_routes_allow_operator_session_and_enqueue_task(self) -> None:
        admin_login = self._request("/api/login_operator", {"username": "admin", "password": "admin"})
        admin_token = admin_login["data"]["session"]["token"]
        admin_headers = {"X-Operator-Session": admin_token}
        self._request(
            "/api/save_operator_user",
            {"username": "worker", "password": "worker-pass"},
            headers=admin_headers,
        )
        login = self._request("/api/login_operator", {"username": "worker", "password": "worker-pass"})
        token = login["data"]["session"]["token"]
        headers = {"X-Operator-Session": token}
        queued = self._request("/api/agent_enqueue_task", {"task_text": "Review board"}, headers=headers)
        self.assertEqual(queued["data"]["task"]["status"], "pending")
        status = self._request("/api/agent_status", method="GET", headers=headers)
        self.assertEqual(status["data"]["queue"]["pending_total"], 1)
