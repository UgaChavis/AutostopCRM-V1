from __future__ import annotations

import json
import logging
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.control import AgentControlService
from minimal_kanban.agent.automotive_tools import AutomotiveLookupService
from minimal_kanban.agent.instructions import build_default_system_prompt
from minimal_kanban.agent.runner import AgentRunner
from minimal_kanban.agent.storage import AgentStorage
from minimal_kanban.api.server import ApiServer
from minimal_kanban.models import utc_now_iso
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
        self.cards = {
            "card-1": {
                "id": "card-1",
                "title": "Черновик",
                "description": "Исходный текст карточки.\nЦена детали 5000.\nАртикул ABC-123.",
            }
        }

    def health(self) -> dict:
        self.calls.append(("health", {}))
        return {"ok": True}

    def review_board(self, **kwargs) -> dict:
        self.calls.append(("review_board", kwargs))
        return {"ok": True, "data": {"summary": {"active_cards": 1}}, "text": "board summary"}

    def get_card(self, card_id: str) -> dict:
        self.calls.append(("get_card", {"card_id": card_id}))
        return {"card": dict(self.cards.get(card_id, {"id": card_id, "description": ""}))}

    def update_card(self, **kwargs) -> dict:
        self.calls.append(("update_card", kwargs))
        card_id = str(kwargs.get("card_id") or "").strip()
        if card_id:
            card = dict(self.cards.get(card_id, {"id": card_id}))
            card.update({key: value for key, value in kwargs.items() if key in {"title", "description", "vehicle", "tags", "vehicle_profile"}})
            self.cards[card_id] = card
        return {
            "ok": True,
            "card_id": kwargs.get("card_id"),
            "changed": ["title", "description"],
            "meta": {"changed": True, "changed_fields": ["title", "description"]},
        }


class _FakeWrappedBoardApi(_FakeBoardApi):
    def review_board(self, **kwargs) -> dict:
        raw = super().review_board(**kwargs)
        return {"ok": True, "data": raw.get("data", {})}

    def get_card(self, card_id: str) -> dict:
        raw = super().get_card(card_id)
        return {"ok": True, "data": raw}

    def get_card_context(self, card_id: str, **kwargs) -> dict:
        self.calls.append(("get_card_context", {"card_id": card_id, **kwargs}))
        card = dict(self.cards.get(card_id, {"id": card_id, "description": ""}))
        vehicle_profile = {"vin": "WBAPF71060A798127"}
        repair_order = {"number": "", "status": "open", "works": [], "materials": [], "vin": ""}
        return {
            "ok": True,
            "data": {
                "card": {
                    **card,
                    "vehicle": "BMW 320I",
                    "title": card.get("title", ""),
                    "column": "inbox",
                    "tags": [],
                    "ai_autofill_prompt": "Добавь короткую ИИ-заметку.",
                    "ai_autofill_log": [{"level": "RUN", "message": "Автосопровождение включено."}],
                    "vehicle_profile": vehicle_profile,
                    "repair_order": repair_order,
                },
                "events": [{"action": "card_created"}],
            },
        }

    def search_cards(self, **kwargs) -> dict:
        self.calls.append(("search_cards", kwargs))
        return {
            "ok": True,
            "data": {
                "cards": [
                    {
                        "id": "card-1",
                        "vehicle": "BMW 320I",
                        "title": "Черновик",
                        "column": "inbox",
                        "tags": [],
                    }
                ]
            },
        }

    def update_card(self, **kwargs) -> dict:
        raw = super().update_card(**kwargs)
        return {"ok": True, "data": raw}


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

    def test_storage_prunes_old_finished_tasks_but_keeps_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir), max_finished_tasks=2)
            first = storage.enqueue_task(task_text="one")
            second = storage.enqueue_task(task_text="two")
            third = storage.enqueue_task(task_text="three")
            pending = storage.enqueue_task(task_text="pending")

            for task in [first, second, third]:
                claimed = storage.claim_next_task()
                assert claimed is not None
                storage.complete_task(
                    task_id=claimed["id"],
                    run_id=f"run_{claimed['id']}",
                    summary=claimed["task_text"],
                    result="ok",
                    display={},
                    tool_calls=1,
                )

            tasks = storage.list_tasks(limit=10)
            task_ids = {item["id"] for item in tasks}
            self.assertNotIn(first["id"], task_ids)
            self.assertIn(second["id"], task_ids)
            self.assertIn(third["id"], task_ids)
            self.assertIn(pending["id"], task_ids)

    def test_storage_compacts_runs_and_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(
                base_dir=Path(temp_dir),
                max_runs=2,
                max_actions=3,
                compact_threshold_bytes=1,
            )
            for index in range(4):
                storage.append_run({"id": f"run-{index}"})
            for index in range(6):
                storage.append_action({"id": f"action-{index}"})

            self.assertEqual([item["id"] for item in storage.list_runs(limit=10)], ["run-3", "run-2"])
            self.assertEqual(
                [item["id"] for item in storage.list_actions(limit=10)],
                ["action-5", "action-4", "action-3"],
            )


class AgentControlStatusTests(unittest.TestCase):
    def test_agent_status_uses_fresh_shared_heartbeat_for_external_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(
            "os.environ",
            {
                "MINIMAL_KANBAN_AGENT_ENABLED": "1",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.update_status(
                running=False,
                current_task_id=None,
                current_run_id=None,
                last_heartbeat=utc_now_iso(),
                last_error="",
            )
            service = AgentControlService(storage)
            payload = service.agent_status()
            self.assertTrue(payload["agent"]["enabled"])
            self.assertFalse(payload["agent"]["configured"])
            self.assertTrue(payload["agent"]["available"])
            self.assertTrue(payload["worker"]["heartbeat_fresh"])


class AgentRunnerTests(unittest.TestCase):
    def test_default_prompt_includes_card_cleanup_rules(self) -> None:
        prompt = build_default_system_prompt()
        self.assertIn("tidy up, clean up, or structure a card", prompt)
        self.assertIn("preserve all facts from the card", prompt)
        self.assertIn("apply confident changes with update_card", prompt)
        self.assertIn("card_autofill tasks", prompt)
        self.assertIn("Do not repeat the current description verbatim", prompt)
        self.assertIn("Treat existing vehicle_profile and repair_order fields as grounded known facts", prompt)
        self.assertIn('must be labeled with "ИИ:" or "AI:"', prompt)

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
            actions = storage.list_actions(limit=10)
            self.assertEqual(len(actions), 4)
            self.assertEqual(actions[0]["kind"], "log")
            self.assertEqual(actions[1]["kind"], "tool")
            self.assertEqual(actions[-1]["message"], "Задача агента запущена.")

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

    def test_runner_records_card_autofill_log_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Проверь карточку и дополни только при необходимости.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "Диагностика"},
                },
            )
            logger = logging.getLogger("test.agent.runner.autofill")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            runner = AgentRunner(
                storage=storage,
                board_api=_FakeBoardApi(),
                model_client=_FakeModelClient(
                    [
                        {"type": "final", "summary": "Готово", "result": "Изменения не требуются."},
                        {"type": "final", "summary": "done", "result": "No safe card changes were needed."},
                    ]
                ),
                logger=logger,
            )
            processed = runner.run_once()
            self.assertTrue(processed)
            actions = storage.list_actions(limit=10)
            log_messages = [item.get("message", "") for item in actions if item.get("kind") == "log"]
            self.assertIn("Первый проход автосопровождения запущен.", log_messages)
            self.assertIn("Начат анализ карточки.", log_messages)
            self.assertIn("Изменений не обнаружено.", log_messages)

    def test_runner_merges_card_autofill_description_in_additive_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Дополняй карточку краткими ИИ-комментариями по делу.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "Черновик"},
                },
            )
            logger = logging.getLogger("test.agent.runner.autofill.apply")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeBoardApi()
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=_FakeModelClient(
                    [
                        {
                            "type": "final",
                            "summary": "Карточка дополнена",
                            "result": "Добавлены ИИ-комментарии.",
                            "apply": {
                                "type": "update_card",
                                "card_id": "card-1",
                                "payload": {
                                    "description": "Проверить VIN и уточнить комплектацию.",
                                },
                            },
                        }
                    ]
                ),
                logger=logger,
            )
            self.assertTrue(runner.run_once())
            update_call = next(call for call in board_api.calls if call[0] == "update_card")
            self.assertIn("Исходный текст карточки.", update_call[1]["description"])
            self.assertIn("Цена детали 5000.", update_call[1]["description"])
            self.assertIn("Артикул ABC-123.", update_call[1]["description"])
            self.assertIn("ИИ:", update_call[1]["description"])
            self.assertIn("Проверить VIN и уточнить комплектацию.", update_call[1]["description"])

    def test_runner_card_autofill_dedupes_repeated_description_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Добавь короткую ИИ-заметку без дублирования существующего текста.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "Черновик"},
                },
            )
            logger = logging.getLogger("test.agent.runner.autofill.dedupe")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeBoardApi()
            current = board_api.cards["card-1"]["description"]
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=_FakeModelClient(
                    [
                        {
                            "type": "final",
                            "summary": "Карточка дополнена",
                            "result": "Добавлена ИИ-заметка.",
                            "apply": {
                                "type": "update_card",
                                "card_id": "card-1",
                                "payload": {
                                    "description": current + "\n\n" + current + "\n\nAI: Подтвержден VIN и нужна проверка радиатора.",
                                },
                            },
                        }
                    ]
                ),
                logger=logger,
            )
            self.assertTrue(runner.run_once())
            update_call = next(call for call in board_api.calls if call[0] == "update_card")
            merged = update_call[1]["description"]
            for line in [item.strip() for item in current.splitlines() if item.strip()]:
                self.assertEqual(merged.count(line), 1)
            self.assertIn("AI: Подтвержден VIN и нужна проверка радиатора.", merged)

    def test_runner_unwraps_wrapped_card_context_for_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Проверь карточку и при необходимости дополни её.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "Черновик"},
                },
            )
            logger = logging.getLogger("test.agent.runner.wrapped.context")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            model = _FakeModelClient(
                [
                    {
                        "type": "tool",
                        "tool": "get_card_context",
                        "args": {"card_id": "card-1", "event_limit": 10, "include_repair_order_text": True},
                        "reason": "Read current card context before autofill",
                    },
                    {"type": "final", "summary": "done", "result": "No safe changes were needed."},
                ]
            )
            runner = AgentRunner(
                storage=storage,
                board_api=_FakeWrappedBoardApi(),
                model_client=model,
                logger=logger,
            )
            self.assertTrue(runner.run_once())
            second_call = model.calls[1]
            tool_result_message = second_call["messages"][-1]["content"]
            self.assertIn("Исходный текст карточки.", tool_result_message)
            self.assertIn("ABC-123", tool_result_message)
            self.assertIn("WBAPF71060A798127", tool_result_message)

    def test_runner_merges_card_autofill_description_with_wrapped_get_card_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Дополняй карточку только короткой ИИ-заметкой.",
                metadata={
                    "purpose": "card_autofill",
                    "trigger": "manual_activate",
                    "context": {"kind": "card", "card_id": "card-1", "title": "Черновик"},
                },
            )
            logger = logging.getLogger("test.agent.runner.wrapped.apply")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeWrappedBoardApi()
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=_FakeModelClient(
                    [
                        {
                            "type": "final",
                            "summary": "Карточка дополнена",
                            "result": "Добавлены ИИ-комментарии.",
                            "apply": {
                                "type": "update_card",
                                "card_id": "card-1",
                                "payload": {
                                    "description": "Подтвердить VIN и подготовить краткий список проверок.",
                                },
                            },
                        }
                    ]
                ),
                logger=logger,
            )
            self.assertTrue(runner.run_once())
            update_call = next(call for call in board_api.calls if call[0] == "update_card")
            self.assertIn("Исходный текст карточки.", update_call[1]["description"])
            self.assertIn("Цена детали 5000.", update_call[1]["description"])
            self.assertIn("Артикул ABC-123.", update_call[1]["description"])
            self.assertIn("ИИ:", update_call[1]["description"])
            self.assertIn("Подтвердить VIN и подготовить краткий список проверок.", update_call[1]["description"])

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

    def test_runner_requires_update_card_before_finalizing_card_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Наведи порядок в этой карточке и структурируй данные.",
                metadata={
                    "context": {
                        "kind": "card",
                        "card_id": "card-1",
                        "title": "Черновик",
                    }
                },
            )
            logger = logging.getLogger("test.agent.runner.cleanup")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeBoardApi()
            model = _FakeModelClient(
                [
                    {"type": "final", "summary": "Готово", "result": "Предлагаю обновления."},
                    {
                        "type": "tool",
                        "tool": "update_card",
                        "args": {
                            "card_id": "card-1",
                            "title": "Mitsubishi L200 / Диагностика подвески",
                            "description": "Структурированное описание без потери данных.",
                            "tags": ["диагностика", "подвеска"],
                        },
                        "reason": "Apply cleanup changes to the current card",
                    },
                    {"type": "final", "summary": "Карточка обновлена", "result": "Изменения применены."},
                ]
            )
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=model,
                logger=logger,
            )
            processed = runner.run_once()
            self.assertTrue(processed)
            task = storage.list_tasks(limit=1)[0]
            self.assertEqual(task["status"], "completed")
            self.assertEqual(task["summary"], "Карточка обновлена")
            update_call = next(call for call in board_api.calls if call[0] == "update_card")
            self.assertEqual(update_call[1]["card_id"], "card-1")
            self.assertEqual(update_call[1]["title"], "Mitsubishi L200 / Диагностика подвески")
            self.assertEqual(update_call[1]["description"], "Структурированное описание без потери данных.")
            self.assertEqual(update_call[1]["tags"], ["диагностика", "подвеска"])
            self.assertEqual(len(model.calls), 3)
            self.assertIn("Apply confident changes to card card-1 with update_card before the final answer.", model.calls[1]["messages"][-1]["content"])

    def test_runner_applies_structured_card_update_from_final_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            storage.enqueue_task(
                task_text="Наведи порядок в этой карточке.",
                metadata={"context": {"kind": "card", "card_id": "card-1", "title": "Черновик"}},
            )
            logger = logging.getLogger("test.agent.runner.apply")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            board_api = _FakeBoardApi()
            model = _FakeModelClient(
                [
                    {
                        "type": "final",
                        "summary": "Карточка приведена в порядок",
                        "result": "Изменения применены.",
                        "display": {"title": "Карточка обновлена", "summary": "Нормализованы поля."},
                        "apply": {
                            "type": "update_card",
                            "card_id": "card-1",
                            "payload": {
                                "title": "Mitsubishi L200 / диагностика",
                                "description": "Структурированное описание.",
                                "tags": ["диагностика"],
                            },
                        },
                    }
                ]
            )
            runner = AgentRunner(
                storage=storage,
                board_api=board_api,
                model_client=model,
                logger=logger,
            )
            self.assertTrue(runner.run_once())
            task = storage.list_tasks(limit=1)[0]
            self.assertEqual(task["status"], "completed")
            update_call = next(call for call in board_api.calls if call[0] == "update_card")
            self.assertEqual(update_call[1]["card_id"], "card-1")
            self.assertEqual(update_call[1]["title"], "Mitsubishi L200 / диагностика")
            applied_section = task["display"]["sections"][0]
            self.assertEqual(applied_section["title"], "Применено")
            self.assertTrue(any("краткая суть" in item for item in applied_section["items"]))

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
        self.service.attach_agent_control(self.agent_service)
        self.agent_service.bind_board_service(self.service)
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

    def test_agent_schedule_routes_create_pause_resume_and_run_tasks(self) -> None:
        login = self._request("/api/login_operator", {"username": "admin", "password": "admin"})
        headers = {"X-Operator-Session": login["data"]["session"]["token"]}

        saved = self._request(
            "/api/save_agent_scheduled_task",
            {
                "name": "Проверка оплат",
                "prompt": "Проверь неоплаченные заказ-наряды и обнови карточки через MCP.",
                "scope_type": "all_cards",
                "schedule_type": "interval",
                "interval_value": 1,
                "interval_unit": "hour",
                "active": True,
            },
            headers=headers,
        )
        task_id = saved["data"]["task"]["id"]
        self.assertEqual(saved["data"]["task"]["period"], "1h")
        self.assertEqual(saved["data"]["task"]["status"], "active")

        listed = self._request("/api/agent_scheduled_tasks", method="GET", headers=headers)
        self.assertTrue(any(item["id"] == task_id for item in listed["data"]["tasks"]))

        paused = self._request("/api/pause_agent_scheduled_task", {"task_id": task_id}, headers=headers)
        self.assertEqual(paused["data"]["task"]["status"], "paused")

        resumed = self._request("/api/resume_agent_scheduled_task", {"task_id": task_id}, headers=headers)
        self.assertEqual(resumed["data"]["task"]["status"], "active")

        queued = self._request("/api/run_agent_scheduled_task", {"task_id": task_id}, headers=headers)
        self.assertEqual(queued["data"]["task"]["status"], "pending")
        self.assertEqual(queued["data"]["scheduled_task"]["id"], task_id)

    def test_agent_on_create_schedule_enqueues_single_task_for_matching_card(self) -> None:
        login = self._request("/api/login_operator", {"username": "admin", "password": "admin"})
        headers = {"X-Operator-Session": login["data"]["session"]["token"]}
        default_column = self.service.list_columns()["columns"][0]["id"]
        saved = self._request(
            "/api/save_agent_scheduled_task",
            {
                "name": "On create inbox",
                "prompt": "Inspect new inbox cards and enrich them through MCP.",
                "scope_type": "column",
                "scope_column": default_column,
                "schedule_type": "on_create",
                "active": True,
            },
            headers=headers,
        )
        task_id = saved["data"]["task"]["id"]
        self.assertEqual(saved["data"]["task"]["period"], "on_create")

        created = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "Fresh card", "column": default_column, "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        queued = self.agent_storage.list_tasks(limit=10)
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["metadata"]["scheduled_task_id"], task_id)
        self.assertEqual(queued[0]["metadata"]["purpose"], "scheduled_on_create")
        self.assertEqual(queued[0]["metadata"]["context"]["card_id"], card_id)

        duplicate = self.agent_service.handle_card_created({"card_id": card_id, "column": default_column})
        self.assertEqual(duplicate["launched"], [])
        self.assertEqual(len(self.agent_storage.list_tasks(limit=10)), 1)

    def test_set_card_ai_autofill_route_enqueues_current_card_task(self) -> None:
        created = self.service.create_card({"vehicle": "Toyota Corolla", "title": "AI follow-up", "deadline": {"hours": 2}})
        card_id = created["card"]["id"]

        enabled = self._request(
            "/api/set_card_ai_autofill",
            {
                "card_id": card_id,
                "enabled": True,
                "actor_name": "AI",
            },
        )
        self.assertTrue(enabled["data"]["meta"]["enabled"])
        self.assertTrue(enabled["data"]["meta"]["launched"])
        self.assertTrue(enabled["data"]["card"]["ai_autofill_active"])
        self.assertEqual(enabled["data"]["card"]["ai_run_count"], 1)

        queued = self.agent_storage.list_tasks(limit=10)
        self.assertEqual(len(queued), 1)
        task = queued[0]
        self.assertEqual(task["mode"], "card_autofill")
        self.assertEqual(task["metadata"]["scope"]["type"], "current_card")
        self.assertEqual(task["metadata"]["scope"]["card_id"], card_id)
        self.assertEqual(task["metadata"]["purpose"], "card_autofill")
