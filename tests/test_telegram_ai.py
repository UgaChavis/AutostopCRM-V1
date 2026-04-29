from __future__ import annotations

# ruff: noqa: E402
import json
import logging
import socket
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from attachment_samples import PNG_1X1_BYTES

from minimal_kanban.api.server import ApiServer
from minimal_kanban.mcp.client import BoardApiClient
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore
from minimal_kanban.telegram_ai.audit import TelegramAIAuditService, redact_secrets
from minimal_kanban.telegram_ai.auth import TelegramAuthService
from minimal_kanban.telegram_ai.config import TelegramAIConfig
from minimal_kanban.telegram_ai.context import CRMContextBuilder
from minimal_kanban.telegram_ai.crm_tools import CRMToolError, CRMToolRegistry
from minimal_kanban.telegram_ai.memory import TelegramAIConversationMemory
from minimal_kanban.telegram_ai.models import DownloadedAttachment, RunContext, TelegramAttachment
from minimal_kanban.telegram_ai.normalizer import normalize_update
from minimal_kanban.telegram_ai.openai_client import TelegramAIModelError, TelegramAIOpenAIClient
from minimal_kanban.telegram_ai.orchestrator import TelegramAIOrchestrator
from minimal_kanban.telegram_ai.response import build_execution_response
from minimal_kanban.telegram_ai.worker import TelegramAIWorker


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def build_config(
    temp_dir: str, *, owner_ids: frozenset[int] = frozenset({1001})
) -> TelegramAIConfig:
    return TelegramAIConfig(
        enabled=True,
        bot_token="telegram-token",
        owner_ids=owner_ids,
        openai_api_key="openai-key",
        openai_base_url="https://api.openai.com/v1",
        model="gpt-5.4-mini",
        strong_model="gpt-5.4",
        vision_model="gpt-5.4-mini",
        transcription_model="gpt-4o-mini-transcribe",
        local_transcription_model="base",
        reasoning_effort="medium",
        strong_reasoning_effort="high",
        crm_api_base_url="http://127.0.0.1:41731",
        crm_api_bearer_token=None,
        data_dir=Path(temp_dir) / "telegram_ai",
        audit_enabled=True,
        max_batch_cards=20,
        telegram_poll_timeout_seconds=1,
        telegram_request_timeout_seconds=1.0,
        openai_request_timeout_seconds=1.0,
        autopilot_enabled=False,
        autopilot_interval_minutes=30,
        web_search_enabled=False,
        conversation_memory_limit=12,
    )


class FakeModelClient:
    model = "fake-model"

    def __init__(self) -> None:
        self.decide_calls = 0
        self.decisions: list[dict[str, object]] = []
        self.received_contexts: list[dict[str, object]] = []
        self.final_response_calls = 0
        self.final_responses: list[str] = []
        self.received_tool_results: list[list[dict[str, object]]] = []
        self.internet_search_calls = 0
        self.internet_search_responses: list[str] = []
        self.received_search_commands: list[str] = []
        self.received_search_contexts: list[dict[str, object]] = []

    def decide(self, **kwargs):
        self.decide_calls += 1
        self.received_contexts.append(kwargs.get("crm_context") or {})
        if self.decisions:
            return self.decisions.pop(0)
        return {
            "intent": "no_action",
            "confidence": "high",
            "actions": [],
            "telegram_response": "Ответ модели",
            "requires_human_confirmation": False,
        }

    def transcribe_audio(self, **kwargs) -> str:
        return "Создай карточку голосом"

    def analyze_image(self, **kwargs):
        return {"vin": "WAUZZZ8V0JA000001", "confidence": "medium"}

    def final_response(self, **kwargs) -> str:
        self.final_response_calls += 1
        self.received_tool_results.append(kwargs.get("tool_results") or [])
        if self.final_responses:
            return self.final_responses.pop(0)
        return ""

    def internet_search(self, **kwargs) -> str:
        self.internet_search_calls += 1
        self.received_search_commands.append(str(kwargs.get("command_text") or ""))
        self.received_search_contexts.append(kwargs.get("crm_context") or {})
        if self.internet_search_responses:
            return self.internet_search_responses.pop(0)
        return "Ответ интернет-поиска"


class TelegramAINormalizerTests(unittest.TestCase):
    def test_normalize_text_update(self) -> None:
        update = {
            "update_id": 10,
            "message": {
                "message_id": 20,
                "date": 123,
                "chat": {"id": 30},
                "from": {"id": 1001, "username": "owner"},
                "text": "Кратко по доске",
            },
        }

        normalized = normalize_update(update)

        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(normalized.update_id, 10)
        self.assertEqual(normalized.chat_id, 30)
        self.assertEqual(normalized.user_id, 1001)
        self.assertEqual(normalized.input_type, "text")
        self.assertEqual(normalized.command_text, "Кратко по доске")

    def test_normalize_voice_and_best_photo(self) -> None:
        voice_update = {
            "update_id": 11,
            "message": {
                "message_id": 21,
                "chat": {"id": 31},
                "from": {"id": 1001},
                "voice": {"file_id": "voice-1", "file_unique_id": "v1", "mime_type": "audio/ogg"},
            },
        }
        photo_update = {
            "update_id": 12,
            "message": {
                "message_id": 22,
                "chat": {"id": 32},
                "from": {"id": 1001},
                "caption": "по BMW",
                "photo": [
                    {"file_id": "small", "file_size": 10, "width": 10, "height": 10},
                    {"file_id": "large", "file_size": 100, "width": 100, "height": 100},
                ],
            },
        }

        voice = normalize_update(voice_update)
        photo = normalize_update(photo_update)

        self.assertEqual(voice.input_type, "voice")
        self.assertEqual(voice.attachments[0].file_id, "voice-1")
        self.assertEqual(photo.input_type, "photo")
        self.assertEqual(photo.attachments[0].file_id, "large")
        self.assertEqual(photo.command_text, "по BMW")


class TelegramAIAuthAuditTests(unittest.TestCase):
    def test_owner_authorization_and_denial(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            auth = TelegramAuthService(build_config(temp_dir, owner_ids=frozenset({42})))
            self.assertEqual(auth.resolve(user_id=42).role, "owner")
            self.assertFalse(auth.resolve(user_id=43).is_authorized)

    def test_audit_redacts_secrets(self) -> None:
        payload = redact_secrets(
            {
                "bot_token": "telegram-secret",
                "nested": {"OPENAI_API_KEY": "openai-secret"},
                "safe": "value",
            }
        )

        self.assertEqual(payload["bot_token"], "***")
        self.assertEqual(payload["nested"]["OPENAI_API_KEY"], "***")
        self.assertEqual(payload["safe"], "value")


class TelegramAIOrchestratorTests(unittest.TestCase):
    def test_unauthorized_user_does_not_call_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir, owner_ids=frozenset({1001}))
            audit = TelegramAIAuditService(config.audit_file)
            model = FakeModelClient()
            orchestrator = TelegramAIOrchestrator(
                auth=TelegramAuthService(config),
                model_client=model,
                context_builder=object(),
                tool_registry=object(),
                audit=audit,
            )
            normalized = normalize_update(
                {
                    "update_id": 1,
                    "message": {
                        "message_id": 2,
                        "chat": {"id": 3},
                        "from": {"id": 999},
                        "text": "Кратко по доске",
                    },
                }
            )

            response = orchestrator.handle(normalized)

            self.assertEqual(response, "Доступ запрещён.")
            self.assertEqual(model.decide_calls, 0)
            rows = config.audit_file.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0])["final_status"], "failed")

    def test_status_command_does_not_call_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir, owner_ids=frozenset({1001}))
            model = FakeModelClient()
            orchestrator = TelegramAIOrchestrator(
                auth=TelegramAuthService(config),
                model_client=model,
                context_builder=object(),
                tool_registry=object(),
                audit=TelegramAIAuditService(config.audit_file),
            )
            normalized = normalize_update(
                {
                    "update_id": 1,
                    "message": {
                        "message_id": 2,
                        "chat": {"id": 3},
                        "from": {"id": 1001},
                        "text": "/status",
                    },
                }
            )

            response = orchestrator.handle(normalized)

            self.assertIn("Telegram AI worker активен", response)
            self.assertEqual(model.decide_calls, 0)

    def test_internet_search_command_skips_crm_tools_and_returns_search_answer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir, owner_ids=frozenset({1001}))
            model = FakeModelClient()
            model.internet_search_responses = [
                "Нашёл в интернете: пример результата. Источник: example.com"
            ]
            orchestrator = TelegramAIOrchestrator(
                auth=TelegramAuthService(config),
                model_client=model,
                context_builder=object(),
                tool_registry=object(),
                audit=TelegramAIAuditService(config.audit_file),
            )
            normalized = normalize_update(
                {
                    "update_id": 1,
                    "message": {
                        "message_id": 2,
                        "chat": {"id": 3},
                        "from": {"id": 1001},
                        "text": "Найди в интернете лучшие воздушные фильтры для Toyota",
                    },
                }
            )

            response = orchestrator.handle(normalized)

            self.assertIn("Нашёл в интернете", response)
            self.assertEqual(model.internet_search_calls, 1)
            self.assertEqual(model.decide_calls, 0)
            self.assertIn("воздушные фильтры", model.received_search_commands[0])

    def test_research_phrase_routes_to_internet_search_without_crm_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir, owner_ids=frozenset({1001}))
            model = FakeModelClient()
            model.internet_search_responses = [
                "Нашёл в интернете: официальный сайт toyota.ru"
            ]
            orchestrator = TelegramAIOrchestrator(
                auth=TelegramAuthService(config),
                model_client=model,
                context_builder=object(),
                tool_registry=object(),
                audit=TelegramAIAuditService(config.audit_file),
            )
            normalized = normalize_update(
                {
                    "update_id": 2,
                    "message": {
                        "message_id": 3,
                        "chat": {"id": 4},
                        "from": {"id": 1001},
                        "text": "Проверь официальный сайт Toyota и дай ссылку.",
                    },
                }
            )

            response = orchestrator.handle(normalized)

            self.assertIn("toyota.ru", response)
            self.assertEqual(model.internet_search_calls, 1)
            self.assertEqual(model.decide_calls, 0)

    def test_status_command_reports_web_search_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir, owner_ids=frozenset({1001}))
            model = FakeModelClient()
            orchestrator = TelegramAIOrchestrator(
                auth=TelegramAuthService(config),
                model_client=model,
                context_builder=object(),
                tool_registry=object(),
                audit=TelegramAIAuditService(config.audit_file),
            )
            normalized = normalize_update(
                {
                    "update_id": 3,
                    "message": {
                        "message_id": 4,
                        "chat": {"id": 5},
                        "from": {"id": 1001},
                        "text": "/status",
                    },
                }
            )

            response = orchestrator.handle(normalized)

            self.assertIn("Интернет-поиск", response)
            self.assertIn("неизвестно", response)

    def test_ping_command_does_not_require_openai(self) -> None:
        class FailingModel(FakeModelClient):
            def decide(self, **kwargs):
                raise TelegramAIModelError("OpenAI request failed: 429 Too Many Requests")

        class EmptyContextBuilder:
            def build(self, *, command_text: str):
                return {"board_snapshot": {"cards": []}, "board_review": {}}

            def summary(self, context):
                return {"cards_visible": 0, "columns_visible": 0}

        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir, owner_ids=frozenset({1001}))
            model = FailingModel()
            orchestrator = TelegramAIOrchestrator(
                auth=TelegramAuthService(config),
                model_client=model,
                context_builder=EmptyContextBuilder(),
                tool_registry=object(),
                audit=TelegramAIAuditService(config.audit_file),
            )
            normalized = normalize_update(
                {
                    "update_id": 4,
                    "message": {
                        "message_id": 5,
                        "chat": {"id": 6},
                        "from": {"id": 1001},
                        "text": "ты на связи?",
                    },
                }
            )

            response = orchestrator.handle(normalized)

            self.assertIn("На связи", response)
            self.assertNotIn("429", response)

    def test_active_card_count_uses_crm_context_without_openai(self) -> None:
        class FailingModel(FakeModelClient):
            def decide(self, **kwargs):
                raise AssertionError("OpenAI should not be called for active card count.")

        class CountContextBuilder:
            def build(self, *, command_text: str):
                return {
                    "board_snapshot": {"cards": [{"id": "1"}, {"id": "2"}, {"id": "3"}]},
                    "board_review": {},
                    "search_results": {},
                }

            def summary(self, context):
                return {"cards_visible": 3, "columns_visible": 2}

        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir, owner_ids=frozenset({1001}))
            orchestrator = TelegramAIOrchestrator(
                auth=TelegramAuthService(config),
                model_client=FailingModel(),
                context_builder=CountContextBuilder(),
                tool_registry=object(),
                audit=TelegramAIAuditService(config.audit_file),
            )
            normalized = normalize_update(
                {
                    "update_id": 5,
                    "message": {
                        "message_id": 6,
                        "chat": {"id": 7},
                        "from": {"id": 1001},
                        "text": "Сколько у нас активных карточек?",
                    },
                }
            )

            response = orchestrator.handle(normalized)

            self.assertEqual(response, "Активных карточек: 3.")

    def test_follow_up_receives_conversation_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir, owner_ids=frozenset({1001}))
            audit = TelegramAIAuditService(config.audit_file)
            model = FakeModelClient()
            model.decisions = [
                {
                    "intent": "create_card",
                    "confidence": "high",
                    "actions": [
                        {
                            "tool": "create_card",
                            "arguments": {"title": "Camry"},
                            "reason": "test",
                        }
                    ],
                    "telegram_response": "Создал карточку.",
                    "requires_human_confirmation": False,
                },
                {
                    "intent": "update_card",
                    "confidence": "high",
                    "actions": [],
                    "telegram_response": "Вижу предыдущую карточку.",
                    "requires_human_confirmation": False,
                },
            ]
            logger = logging.getLogger("test.telegram_memory")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            store = JsonStore(state_file=Path(temp_dir) / "state.json", logger=logger)
            service = CardService(store, logger)
            port = reserve_port()
            server = ApiServer(service, logger, start_port=port, fallback_limit=1)
            server.start()
            try:
                client = BoardApiClient(
                    server.base_url, logger=logger, default_source="telegram_ai"
                )
                orchestrator = TelegramAIOrchestrator(
                    auth=TelegramAuthService(config),
                    model_client=model,
                    context_builder=CRMContextBuilder(client),
                    tool_registry=CRMToolRegistry(client, actor_name="TEST_TELEGRAM_AI"),
                    audit=audit,
                    memory=TelegramAIConversationMemory(config.conversation_file, limit=5),
                )
                first = normalize_update(
                    {
                        "update_id": 1,
                        "message": {
                            "message_id": 2,
                            "chat": {"id": 3},
                            "from": {"id": 1001},
                            "text": "Создай карточку Camry",
                        },
                    }
                )
                second = normalize_update(
                    {
                        "update_id": 2,
                        "message": {
                            "message_id": 3,
                            "chat": {"id": 3},
                            "from": {"id": 1001},
                            "text": "Добавь туда описание",
                        },
                    }
                )

                orchestrator.handle(first)
                orchestrator.handle(second)
            finally:
                server.stop()

            second_context = model.received_contexts[1]
            memory_rows = second_context.get("conversation_memory")
            self.assertIsInstance(memory_rows, list)
            self.assertEqual(
                memory_rows[0]["tool_results"][0]["ids"]["card_id"],
                service.get_cards()["cards"][0]["id"],
            )

    def test_search_follow_up_receives_last_card_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir, owner_ids=frozenset({1001}))
            audit = TelegramAIAuditService(config.audit_file)
            model = FakeModelClient()
            model.decisions = [
                {
                    "intent": "card_read",
                    "confidence": "high",
                    "actions": [
                        {
                            "tool": "search_cards",
                            "arguments": {"query": "Camry", "limit": 5},
                            "reason": "find the target card",
                        }
                    ],
                    "telegram_response": "Нашёл карточку Camry.",
                    "requires_human_confirmation": False,
                },
                {
                    "intent": "no_action",
                    "confidence": "high",
                    "actions": [],
                    "telegram_response": "Понял.",
                    "requires_human_confirmation": False,
                },
            ]
            logger = logging.getLogger("test.telegram_search_memory")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            store = JsonStore(state_file=Path(temp_dir) / "state.json", logger=logger)
            service = CardService(store, logger)
            created = service.create_card(
                {
                    "title": "Camry",
                    "vehicle": "Toyota Camry",
                    "description": "Найти и отработать follow-up контекст.",
                    "deadline": {"days": 1},
                }
            )
            card_id = created["card"]["id"]
            port = reserve_port()
            server = ApiServer(service, logger, start_port=port, fallback_limit=1)
            server.start()
            try:
                client = BoardApiClient(
                    server.base_url, logger=logger, default_source="telegram_ai"
                )
                orchestrator = TelegramAIOrchestrator(
                    auth=TelegramAuthService(config),
                    model_client=model,
                    context_builder=CRMContextBuilder(client),
                    tool_registry=CRMToolRegistry(client, actor_name="TEST_TELEGRAM_AI"),
                    audit=audit,
                    memory=TelegramAIConversationMemory(config.conversation_file, limit=5),
                )
                first = normalize_update(
                    {
                        "update_id": 1,
                        "message": {
                            "message_id": 2,
                            "chat": {"id": 3},
                            "from": {"id": 1001},
                            "text": "Найди карточку Camry",
                        },
                    }
                )
                second = normalize_update(
                    {
                        "update_id": 2,
                        "message": {
                            "message_id": 3,
                            "chat": {"id": 3},
                            "from": {"id": 1001},
                            "text": "Добавь туда описание",
                        },
                    }
                )

                orchestrator.handle(first)
                orchestrator.handle(second)
            finally:
                server.stop()

            second_context = model.received_contexts[1]
            state = second_context.get("conversation_state")
            self.assertIsInstance(state, dict)
            assert isinstance(state, dict)
            self.assertEqual(state["last_card"]["id"], card_id)
            self.assertEqual(state["last_card"]["title"], "Camry")
            self.assertIn("card_candidates", state)
            self.assertGreaterEqual(len(state["card_candidates"]), 1)
            self.assertEqual(
                second_context["conversation_memory"][0]["tool_results"][0]["references"][
                    "selected_card"
                ]["id"],
                card_id,
            )

    def test_card_context_follow_up_receives_last_vin_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir, owner_ids=frozenset({1001}))
            audit = TelegramAIAuditService(config.audit_file)
            model = FakeModelClient()
            logger = logging.getLogger("test.telegram_vin_memory")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            store = JsonStore(state_file=Path(temp_dir) / "state.json", logger=logger)
            service = CardService(store, logger)
            created = service.create_card(
                {
                    "title": "VIN card",
                    "vehicle": "Toyota Camry",
                    "vehicle_profile": {"vin": "WAUZZZ8V0JA000001"},
                    "deadline": {"days": 1},
                }
            )
            card_id = created["card"]["id"]
            model.decisions = [
                {
                    "intent": "card_read",
                    "confidence": "high",
                    "actions": [
                        {
                            "tool": "get_card_context",
                            "arguments": {"card_id": card_id},
                            "reason": "read the card before using its VIN",
                        }
                    ],
                    "telegram_response": "Смотрю карточку.",
                    "requires_human_confirmation": False,
                },
                {
                    "intent": "no_action",
                    "confidence": "high",
                    "actions": [],
                    "telegram_response": "VIN уже есть в контексте.",
                    "requires_human_confirmation": False,
                },
            ]
            port = reserve_port()
            server = ApiServer(service, logger, start_port=port, fallback_limit=1)
            server.start()
            try:
                client = BoardApiClient(
                    server.base_url, logger=logger, default_source="telegram_ai"
                )
                orchestrator = TelegramAIOrchestrator(
                    auth=TelegramAuthService(config),
                    model_client=model,
                    context_builder=CRMContextBuilder(client),
                    tool_registry=CRMToolRegistry(client, actor_name="TEST_TELEGRAM_AI"),
                    audit=audit,
                    memory=TelegramAIConversationMemory(config.conversation_file, limit=5),
                )
                first = normalize_update(
                    {
                        "update_id": 10,
                        "message": {
                            "message_id": 11,
                            "chat": {"id": 12},
                            "from": {"id": 1001},
                            "text": "Открой карточку и возьми VIN",
                        },
                    }
                )
                second = normalize_update(
                    {
                        "update_id": 11,
                        "message": {
                            "message_id": 12,
                            "chat": {"id": 12},
                            "from": {"id": 1001},
                            "text": "Теперь используй этот VIN для поиска",
                        },
                    }
                )

                orchestrator.handle(first)
                orchestrator.handle(second)
            finally:
                server.stop()

            second_context = model.received_contexts[1]
            state = second_context.get("conversation_state")
            self.assertIsInstance(state, dict)
            assert isinstance(state, dict)
            self.assertEqual(state["last_card"]["id"], card_id)
            self.assertEqual(state["last_vin"], "WAUZZZ8V0JA000001")
            self.assertEqual(
                second_context["conversation_memory"][0]["tool_results"][0]["references"][
                    "last_vin"
                ],
                "WAUZZZ8V0JA000001",
            )

    def test_internet_search_follow_up_uses_last_vin_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir, owner_ids=frozenset({1001}))
            audit = TelegramAIAuditService(config.audit_file)
            model = FakeModelClient()
            model.internet_search_responses = [
                "Нашёл источник по VIN.",
            ]
            logger = logging.getLogger("test.telegram_vin_internet")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            store = JsonStore(state_file=Path(temp_dir) / "state.json", logger=logger)
            service = CardService(store, logger)
            created = service.create_card(
                {
                    "title": "VIN internet card",
                    "vehicle": "Toyota Prado",
                    "vehicle_profile": {"vin": "JTEBU3FJ60K123456"},
                    "deadline": {"days": 1},
                }
            )
            card_id = created["card"]["id"]
            model.decisions = [
                {
                    "intent": "card_read",
                    "confidence": "high",
                    "actions": [
                        {
                            "tool": "get_card_context",
                            "arguments": {"card_id": card_id},
                            "reason": "read the card before web search",
                        }
                    ],
                    "telegram_response": "Смотрю карточку.",
                    "requires_human_confirmation": False,
                }
            ]
            port = reserve_port()
            server = ApiServer(service, logger, start_port=port, fallback_limit=1)
            server.start()
            try:
                client = BoardApiClient(
                    server.base_url, logger=logger, default_source="telegram_ai"
                )
                orchestrator = TelegramAIOrchestrator(
                    auth=TelegramAuthService(config),
                    model_client=model,
                    context_builder=CRMContextBuilder(client),
                    tool_registry=CRMToolRegistry(client, actor_name="TEST_TELEGRAM_AI"),
                    audit=audit,
                    memory=TelegramAIConversationMemory(config.conversation_file, limit=5),
                )
                first = normalize_update(
                    {
                        "update_id": 20,
                        "message": {
                            "message_id": 21,
                            "chat": {"id": 22},
                            "from": {"id": 1001},
                            "text": "Открой карточку и запомни VIN",
                        },
                    }
                )
                second = normalize_update(
                    {
                        "update_id": 21,
                        "message": {
                            "message_id": 22,
                            "chat": {"id": 22},
                            "from": {"id": 1001},
                            "text": "Найди в интернете по этому VIN оригинальный масляный фильтр",
                        },
                    }
                )

                orchestrator.handle(first)
                response = orchestrator.handle(second)
            finally:
                server.stop()

            self.assertIn("Нашёл источник по VIN", response)
            self.assertEqual(model.internet_search_calls, 1)
            self.assertEqual(
                model.received_search_commands[0],
                "Найди в интернете по этому VIN оригинальный масляный фильтр",
            )
            search_context = model.received_search_contexts[0]
            self.assertIsInstance(search_context, dict)
            assert isinstance(search_context, dict)
            self.assertEqual(
                search_context["conversation_state"]["last_vin"],
                "JTEBU3FJ60K123456",
            )
            self.assertEqual(
                search_context["conversation_state"]["last_card"]["id"],
                card_id,
            )

    def test_worker_sends_failure_reply_when_update_pipeline_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir, owner_ids=frozenset({1001}))
            logger = logging.getLogger("test.telegram_worker")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            worker = TelegramAIWorker(config, logger=logger)

            class FakeTelegram:
                def __init__(self) -> None:
                    self.sent_messages: list[dict[str, object]] = []

                def send_message(self, **kwargs) -> dict[str, object]:
                    self.sent_messages.append(kwargs)
                    return {"ok": True}

            class FakeOrchestrator:
                def handle(self, normalized_input, *, downloaded_attachments=None) -> str:
                    raise RuntimeError("boom")

            telegram = FakeTelegram()
            update = {
                "update_id": 1,
                "message": {
                    "message_id": 2,
                    "chat": {"id": 3},
                    "from": {"id": 1001},
                    "text": "Найди передние тормозные колодки",
                },
            }

            worker._handle_update(telegram, FakeOrchestrator(), update)

            self.assertEqual(len(telegram.sent_messages), 1)
            self.assertIn("Не выполнил", str(telegram.sent_messages[0]["text"]))
            self.assertIn("сбой обработки запроса", str(telegram.sent_messages[0]["text"]))

    def test_final_response_is_built_after_tool_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir, owner_ids=frozenset({1001}))
            audit = TelegramAIAuditService(config.audit_file)
            logger = logging.getLogger("test.telegram_final_response")
            logger.handlers.clear()
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            store = JsonStore(state_file=Path(temp_dir) / "state.json", logger=logger)
            service = CardService(store, logger)
            created = service.create_card(
                {
                    "title": "Тестовая карточка",
                    "vehicle": "Toyota Corolla",
                    "description": "Клиент просит проверить тормоза.",
                    "deadline": {"days": 1},
                }
            )
            card_id = created["card"]["id"]
            port = reserve_port()
            server = ApiServer(service, logger, start_port=port, fallback_limit=1)
            server.start()
            try:
                client = BoardApiClient(
                    server.base_url, logger=logger, default_source="telegram_ai"
                )
                model = FakeModelClient()
                model.decisions = [
                    {
                        "intent": "card_read",
                        "confidence": "high",
                        "actions": [
                            {
                                "tool": "get_card",
                                "arguments": {"card_id": card_id},
                                "reason": "read requested card",
                            }
                        ],
                        "telegram_response": "Сейчас пришлю содержание тестовой карточки.",
                        "requires_human_confirmation": False,
                    }
                ]
                model.final_responses = [
                    "Тестовая карточка: Toyota Corolla. Клиент просит проверить тормоза."
                ]
                orchestrator = TelegramAIOrchestrator(
                    auth=TelegramAuthService(config),
                    model_client=model,
                    context_builder=CRMContextBuilder(client),
                    tool_registry=CRMToolRegistry(client, actor_name="TEST_TELEGRAM_AI"),
                    audit=audit,
                    memory=TelegramAIConversationMemory(config.conversation_file, limit=5),
                )
                normalized = normalize_update(
                    {
                        "update_id": 1,
                        "message": {
                            "message_id": 2,
                            "chat": {"id": 3},
                            "from": {"id": 1001},
                            "text": "Покажи содержание тестовой карточки",
                        },
                    }
                )

                response = orchestrator.handle(normalized)
            finally:
                server.stop()

            self.assertEqual(model.final_response_calls, 1)
            self.assertEqual(model.received_tool_results[0][0]["tool"], "get_card")
            self.assertIn("Toyota Corolla", response)
            self.assertIn("Клиент просит проверить тормоза", response)
            self.assertNotIn("Сейчас пришлю", response)

    def test_voice_transcription_failure_returns_friendly_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir, owner_ids=frozenset({1001}))

            class FakeAudit:
                def __init__(self) -> None:
                    self.runs: list[object] = []

                def write_run(self, context) -> None:
                    self.runs.append(context)

            class VoiceFailModelClient(FakeModelClient):
                def transcribe_audio(self, **kwargs) -> str:
                    raise TelegramAIModelError("OpenAI transcription request failed.")

            orchestrator = TelegramAIOrchestrator(
                auth=TelegramAuthService(config),
                model_client=VoiceFailModelClient(),
                context_builder=object(),
                tool_registry=object(),
                audit=FakeAudit(),
                memory=None,
            )
            normalized = normalize_update(
                {
                    "update_id": 41,
                    "message": {
                        "message_id": 42,
                        "chat": {"id": 43},
                        "from": {"id": 1001},
                        "voice": {
                            "file_id": "voice-1",
                            "file_unique_id": "v1",
                            "mime_type": "audio/ogg",
                        },
                    },
                }
            )
            assert normalized is not None
            voice = DownloadedAttachment(
                attachment=TelegramAttachment(
                    kind="voice",
                    file_id="voice-1",
                    mime_type="audio/ogg",
                    file_name="voice.ogg",
                ),
                content=b"ogg-bytes",
            )

            response = orchestrator.handle(normalized, downloaded_attachments=[voice])

            self.assertIn("Не смог распознать голосовое сообщение", response)
            self.assertNotIn("OpenAI", response)


class TelegramAITranscriptionTests(unittest.TestCase):
    def test_voice_ogg_uses_local_transcription_backend_before_openai(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir)
            client = TelegramAIOpenAIClient(config)
            captured: dict[str, object] = {}

            class FakeWhisperModel:
                def __init__(self, model_name, device, compute_type, download_root):
                    captured["init"] = {
                        "model_name": model_name,
                        "device": device,
                        "compute_type": compute_type,
                        "download_root": download_root,
                    }

                def transcribe(self, audio_path, language=None, beam_size=None, vad_filter=None):
                    captured["audio_path"] = audio_path
                    captured["language"] = language
                    captured["beam_size"] = beam_size
                    captured["vad_filter"] = vad_filter
                    return [
                        types.SimpleNamespace(text="Создай "),
                        types.SimpleNamespace(text="карточку"),
                    ], {}

            fake_module = types.ModuleType("faster_whisper")
            fake_module.WhisperModel = FakeWhisperModel

            class FailIfOpenAIClient:
                def __init__(self, *args, **kwargs) -> None:
                    raise AssertionError("OpenAI transcription should not be called first.")

            with (
                patch.dict(sys.modules, {"faster_whisper": fake_module}),
                patch("minimal_kanban.telegram_ai.openai_client.httpx.Client", FailIfOpenAIClient),
            ):
                text = client.transcribe_audio(
                    audio_bytes=b"ogg-bytes",
                    filename="voice.ogg",
                    mime_type="audio/ogg",
                )

            self.assertEqual(text, "Создай карточку")
            self.assertEqual(captured["init"]["model_name"], "base")
            self.assertEqual(captured["init"]["device"], "cpu")
            self.assertEqual(captured["init"]["compute_type"], "int8")
            self.assertTrue(str(captured["audio_path"]).endswith("voice.ogg"))
            self.assertEqual(captured["language"], "ru")
            self.assertEqual(captured["beam_size"], 5)
            self.assertTrue(captured["vad_filter"])

    def test_transcription_retries_transient_429(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir)
            client = TelegramAIOpenAIClient(config)
            seen_posts: list[dict[str, object]] = []

            class FakeResponse:
                def __init__(self, *, status_code: int = 200, payload: dict[str, object] | None = None):
                    self.status_code = status_code
                    self._payload = payload or {"text": "Создай карточку"}
                    self.headers = {"retry-after": "0"}

                def raise_for_status(self) -> None:
                    if self.status_code >= 400:
                        request = httpx.Request(
                            "POST",
                            "https://api.openai.com/v1/audio/transcriptions",
                        )
                        response = httpx.Response(
                            self.status_code,
                            request=request,
                            headers=self.headers,
                        )
                        raise httpx.HTTPStatusError(
                            "boom",
                            request=request,
                            response=response,
                        )

                def json(self) -> dict[str, object]:
                    return self._payload

            class FakeHttpxClient:
                attempts = 0

                def __init__(self, *args, **kwargs) -> None:
                    return None

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb) -> None:
                    return None

                def post(self, url, headers=None, data=None, files=None):
                    FakeHttpxClient.attempts += 1
                    seen_posts.append({"url": url, "data": data, "files": files})
                    if FakeHttpxClient.attempts == 1:
                        return FakeResponse(status_code=429)
                    return FakeResponse()

            with (
                patch.object(TelegramAIOpenAIClient, "_transcribe_audio_local", return_value=""),
                patch("minimal_kanban.telegram_ai.openai_client.httpx.Client", FakeHttpxClient),
            ):
                text = client.transcribe_audio(
                    audio_bytes=b"wav-bytes",
                    filename="voice.wav",
                    mime_type="audio/wav",
                )

            self.assertEqual(text, "Создай карточку")
            self.assertEqual(len(seen_posts), 2)


class TelegramAIResponsesPayloadTests(unittest.TestCase):
    def test_decide_payload_omits_temperature_for_responses_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir)
            client = TelegramAIOpenAIClient(config)
            captured: dict[str, object] = {}

            def fake_post_with_retry(
                path: str,
                payload: dict[str, object],
                **kwargs: object,
            ) -> dict[str, object]:
                captured["path"] = path
                captured["payload"] = payload
                return {
                    "output_text": json.dumps(
                        {
                            "intent": "no_action",
                            "confidence": "high",
                            "actions": [],
                            "telegram_response": "ok",
                            "requires_human_confirmation": False,
                        },
                        ensure_ascii=False,
                    )
                }

            with patch.object(
                TelegramAIOpenAIClient, "_post_with_retry", side_effect=fake_post_with_retry
            ):
                result = client.decide(
                    command_text="/status",
                    role="owner",
                    crm_context={},
                    tool_catalog=[],
                )

            self.assertEqual(captured["path"], "/responses")
            payload = captured["payload"]
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertNotIn("temperature", payload)
            self.assertIn("json", json.dumps(payload["input"], ensure_ascii=False).lower())
            self.assertEqual(payload["reasoning"], {"effort": "medium"})
            self.assertEqual(result["intent"], "no_action")

    def test_post_with_retry_retries_transient_429(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir)
            client = TelegramAIOpenAIClient(config)
            attempts = {"count": 0}

            class FakeClient:
                def __init__(self, *args, **kwargs) -> None:
                    return None

                def __enter__(self) -> FakeClient:
                    return self

                def __exit__(self, exc_type, exc, tb) -> bool:
                    return False

                def post(self, url: str, headers=None, json=None) -> httpx.Response:
                    attempts["count"] += 1
                    request = httpx.Request("POST", url)
                    if attempts["count"] == 1:
                        return httpx.Response(429, request=request, headers={"Retry-After": "0"})
                    return httpx.Response(200, request=request, json={"output_text": "ok"})

            with patch("minimal_kanban.telegram_ai.openai_client.httpx.Client", FakeClient):
                payload = client._post_with_retry("/responses", {"model": "gpt-5.4-mini"}, max_attempts=2)

            self.assertEqual(payload["output_text"], "ok")
            self.assertEqual(attempts["count"], 2)

    def test_simple_decide_uses_base_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir)
            client = TelegramAIOpenAIClient(config)
            captured: dict[str, object] = {}

            def fake_post_with_retry(
                path: str,
                payload: dict[str, object],
                **kwargs: object,
            ) -> dict[str, object]:
                captured["payload"] = payload
                return {
                    "output_text": json.dumps(
                        {
                            "intent": "no_action",
                            "confidence": "high",
                            "actions": [],
                            "telegram_response": "ok",
                            "requires_human_confirmation": False,
                        },
                        ensure_ascii=False,
                    )
                }

            with patch.object(
                TelegramAIOpenAIClient, "_post_with_retry", side_effect=fake_post_with_retry
            ):
                client.decide(
                    command_text="Кратко по доске",
                    role="owner",
                    crm_context={},
                    tool_catalog=[],
                )

            payload = captured["payload"]
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["model"], "gpt-5.4-mini")
            self.assertEqual(payload["reasoning"], {"effort": "medium"})

    def test_complex_decide_uses_strong_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir)
            client = TelegramAIOpenAIClient(config)
            captured: dict[str, object] = {}

            def fake_post_with_retry(
                path: str,
                payload: dict[str, object],
                **kwargs: object,
            ) -> dict[str, object]:
                captured["payload"] = payload
                return {
                    "output_text": json.dumps(
                        {
                            "intent": "multi_action",
                            "confidence": "high",
                            "actions": [],
                            "telegram_response": "ok",
                            "requires_human_confirmation": False,
                        },
                        ensure_ascii=False,
                    )
                }

            with patch.object(
                TelegramAIOpenAIClient, "_post_with_retry", side_effect=fake_post_with_retry
            ):
                client.decide(
                    command_text=(
                        "Сначала зайди в карточку, возьми VIN, потом найди запчасти, "
                        "сравни OEM и аналоги, проверь источники и сформируй ответ."
                    ),
                    role="owner",
                    crm_context={},
                    tool_catalog=[],
                )

            payload = captured["payload"]
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["model"], "gpt-5.4")
            self.assertEqual(payload["reasoning"], {"effort": "high"})

    def test_internet_search_payload_uses_web_search_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir)
            config = TelegramAIConfig(
                **{
                    **config.__dict__,
                    "web_search_enabled": True,
                }
            )
            client = TelegramAIOpenAIClient(config)
            captured: dict[str, object] = {}

            def fake_post_with_retry(
                path: str,
                payload: dict[str, object],
                **kwargs: object,
            ) -> dict[str, object]:
                captured["path"] = path
                captured["payload"] = payload
                return {"output_text": "Найдено: source.example"}

            with patch.object(
                TelegramAIOpenAIClient, "_post_with_retry", side_effect=fake_post_with_retry
            ):
                result = client.internet_search(
                    command_text="Найди в интернете новости Toyota",
                    role="owner",
                )

            self.assertEqual(result, "Найдено: source.example")
            self.assertEqual(captured["path"], "/responses")
            payload = captured["payload"]
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["tools"][0]["type"], "web_search_preview")
            self.assertIn("🔎 Коротко", str(payload["instructions"]))
            self.assertNotIn("📎 Источники", str(payload["instructions"]))
            self.assertIn("Do not include sources", str(payload["instructions"]))

    def test_internet_search_response_strips_links_for_telegram(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = TelegramAIConfig(
                **{
                    **build_config(temp_dir).__dict__,
                    "web_search_enabled": True,
                }
            )
            client = TelegramAIOpenAIClient(config)

            def fake_post_with_retry(
                path: str,
                payload: dict[str, object],
                **kwargs: object,
            ) -> dict[str, object]:
                return {
                    "output_text": (
                        "🔎 Коротко: VIN подходит по каталогу "
                        "[VINCheck](https://vincheck.me/toyota/corolla?utm_source=openai)\n\n"
                        "✅ Найдено: 17801-0D020 "
                        "(https://www.toyotapartsandservice.com/file.pdf)\n\n"
                        "📎 Источники: Toyota Parts & Service; VINCheck"
                    )
                }

            with patch.object(
                TelegramAIOpenAIClient, "_post_with_retry", side_effect=fake_post_with_retry
            ):
                result = client.internet_search(
                    command_text="Найди в интернете по этому VIN оригинальный фильтр",
                    role="owner",
                    crm_context={
                        "conversation_state": {
                            "last_vin": "JTEBU3FJ60K123456",
                            "last_card": {"id": "card-77", "vehicle": "Toyota Corolla"},
                        }
                    },
                )

            self.assertNotIn("http", result)
            self.assertNotIn("utm_", result)
            self.assertNotIn("](", result)
            self.assertNotIn("()", result)
            self.assertNotIn("**", result)
            self.assertNotIn("Источники", result)
            self.assertNotIn("📎", result)
            self.assertNotIn("VINCheck", result)
            self.assertNotIn("Toyota Parts & Service", result)
            self.assertNotIn("VINCheck", result)
            self.assertNotIn("Toyota Parts & Service", result)

    def test_simple_internet_search_tool_result_is_human_readable(self) -> None:
        response = build_execution_response(
            model_decision={"telegram_response": "Сделано."},
            tool_results=[
                {
                    "tool": "internet_search",
                    "verify": {"passed": True},
                    "result": {
                        "data": {
                            "answer": (
                                "🔎 Коротко: **подходит по VIN**. (https://example.com)\n\n"
                                "✅ Найдено: **04465-02070**\n\n"
                                "📎 Источники: Toyota Parts & Service; VINCheck"
                            )
                        }
                    },
                }
            ],
            status="completed",
        )

        self.assertIn("Коротко:", response)
        self.assertIn("04465-02070", response)
        self.assertNotIn("Сделано", response)
        self.assertNotIn("internet_search", response)
        self.assertNotIn("http", response)
        self.assertNotIn("**", response)
        self.assertNotIn("Источники", response)
        self.assertNotIn("📎", response)
        self.assertNotIn("VINCheck", response)
        self.assertNotIn("Toyota Parts & Service", response)

    def test_internet_search_payload_includes_follow_up_vin_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = TelegramAIConfig(
                **{
                    **build_config(temp_dir).__dict__,
                    "web_search_enabled": True,
                }
            )
            client = TelegramAIOpenAIClient(config)
            captured: dict[str, object] = {}

            def fake_post_with_retry(
                path: str,
                payload: dict[str, object],
                **kwargs: object,
            ) -> dict[str, object]:
                captured["payload"] = payload
                return {"output_text": "Найдено: source.example"}

            with patch.object(
                TelegramAIOpenAIClient, "_post_with_retry", side_effect=fake_post_with_retry
            ):
                client.internet_search(
                    command_text="Найди в интернете по этому VIN оригинальный фильтр",
                    role="owner",
                    crm_context={
                        "conversation_state": {
                            "last_vin": "JTEBU3FJ60K123456",
                            "last_card": {
                                "id": "card-77",
                                "title": "VIN Test",
                                "vehicle": "Toyota Prado",
                            },
                        }
                    },
                )

            payload = captured["payload"]
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            input_messages = payload["input"]
            self.assertIsInstance(input_messages, list)
            assert isinstance(input_messages, list)
            user_content = input_messages[0]["content"]
            self.assertIsInstance(user_content, str)
            assert isinstance(user_content, str)
            user_payload = json.loads(user_content)
            self.assertEqual(user_payload["resolved_vin"], "JTEBU3FJ60K123456")
            self.assertEqual(user_payload["resolved_card"]["id"], "card-77")

    def test_complex_internet_search_uses_strong_model_with_deep_reasoning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = TelegramAIConfig(
                **{
                    **build_config(temp_dir).__dict__,
                    "web_search_enabled": True,
                }
            )
            client = TelegramAIOpenAIClient(config)
            captured: dict[str, object] = {}

            def fake_post_with_retry(
                path: str,
                payload: dict[str, object],
                **kwargs: object,
            ) -> dict[str, object]:
                captured["payload"] = payload
                return {"output_text": "Найдено"}

            with patch.object(
                TelegramAIOpenAIClient, "_post_with_retry", side_effect=fake_post_with_retry
            ):
                client.internet_search(
                    command_text=(
                        "Найди в интернете запчасти по VIN, сравни оригинал и аналоги, "
                        "дай ссылки на источники."
                    ),
                    role="owner",
                )

            payload = captured["payload"]
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["model"], "gpt-5.4")
            self.assertEqual(payload["reasoning"], {"effort": "medium"})
            self.assertEqual(payload["tools"][0]["type"], "web_search_preview")
            self.assertEqual(payload["tools"][0]["search_context_size"], "low")

    def test_vin_parts_search_uses_strong_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = TelegramAIConfig(
                **{
                    **build_config(temp_dir).__dict__,
                    "web_search_enabled": True,
                }
            )
            client = TelegramAIOpenAIClient(config)
            captured: dict[str, object] = {}

            def fake_post_with_retry(
                path: str,
                payload: dict[str, object],
                **kwargs: object,
            ) -> dict[str, object]:
                captured["payload"] = payload
                return {"output_text": "Найдено"}

            with patch.object(
                TelegramAIOpenAIClient, "_post_with_retry", side_effect=fake_post_with_retry
            ):
                client.internet_search(
                    command_text="Найди по этому VIN передние тормозные колодки",
                    role="owner",
                    crm_context={
                        "conversation_state": {
                            "last_vin": "JTD...",
                        }
                    },
                )

            payload = captured["payload"]
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["model"], "gpt-5.4")
            self.assertEqual(payload["reasoning"], {"effort": "medium"})

    def test_complex_internet_search_falls_back_to_base_model_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = TelegramAIConfig(
                **{
                    **build_config(temp_dir).__dict__,
                    "web_search_enabled": True,
                }
            )
            client = TelegramAIOpenAIClient(config)
            seen_models: list[str] = []
            seen_kwargs: list[dict[str, object]] = []

            def fake_post_with_retry(
                path: str,
                payload: dict[str, object],
                **kwargs: object,
            ) -> dict[str, object]:
                seen_models.append(str(payload.get("model") or ""))
                seen_kwargs.append(kwargs)
                if len(seen_models) == 1:
                    raise TelegramAIModelError("OpenAI request failed: timeout")
                return {"output_text": "Найдено на базовой модели"}

            with patch.object(
                TelegramAIOpenAIClient, "_post_with_retry", side_effect=fake_post_with_retry
            ):
                result = client.internet_search(
                    command_text=(
                        "Найди в интернете запчасти по VIN, сравни оригинал и аналоги, "
                        "дай ссылки на источники."
                    ),
                    role="owner",
                )

            self.assertEqual(result, "Найдено на базовой модели")
            self.assertEqual(seen_models, ["gpt-5.4", "gpt-5.4-mini"])
            self.assertEqual(seen_kwargs[0]["max_attempts"], 3)
            self.assertEqual(seen_kwargs[1]["max_attempts"], 3)


class TelegramAIInternetSearchToolTests(unittest.TestCase):
    def test_registry_exposes_and_executes_internet_search(self) -> None:
        registry = CRMToolRegistry(
            object(),
            internet_searcher=lambda **kwargs: "Нашёл в интернете: example.com",
        )

        catalog = registry.catalog_for_model()

        self.assertIn("internet_search", {item["name"] for item in catalog})

        result = registry.execute(
            {"tool": "internet_search", "arguments": {"query": "Toyota Corolla"}},
            role="owner",
        )

        self.assertEqual(result["tool"], "internet_search")
        self.assertEqual(result["result"]["data"]["answer"], "Нашёл в интернете: example.com")
        self.assertTrue(result["verify"]["passed"])


class TelegramAIConversationMemoryTests(unittest.TestCase):
    def test_memory_stores_compact_run_and_filters_by_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            normalized = normalize_update(
                {
                    "update_id": 1,
                    "message": {
                        "message_id": 2,
                        "chat": {"id": 30},
                        "from": {"id": 1001},
                        "text": "Создай карточку Camry",
                    },
                }
            )
            context = RunContext(
                run_id="tgai_test",
                role="owner",
                normalized_input=normalized,
                model_decision={"intent": "create_card", "telegram_response": "Создал."},
                tool_calls=[
                    {
                        "tool": "create_card",
                        "arguments": {"title": "Camry", "description": "x" * 1000},
                    }
                ],
                tool_results=[
                    {
                        "tool": "create_card",
                        "verify": {"passed": True},
                        "result": {"data": {"card": {"id": "card-1", "title": "Camry"}}},
                    }
                ],
                final_status="completed",
                telegram_response="Сделано.",
            )
            memory = TelegramAIConversationMemory(
                Path(temp_dir) / "conversation.jsonl",
                limit=5,
            )

            memory.append_run(context)

            rows = memory.recent(chat_id=30, user_id=1001)
            self.assertEqual(rows[0]["tool_results"][0]["ids"]["card_id"], "card-1")
            self.assertEqual(memory.recent(chat_id=31, user_id=1001), [])


class TelegramAIResponseTests(unittest.TestCase):
    def test_card_read_result_replaces_future_promise_in_reply(self) -> None:
        response = build_execution_response(
            model_decision={"telegram_response": "Сейчас пришлю содержание тестовой карточки."},
            tool_results=[
                {
                    "tool": "get_card_context",
                    "verify": {"passed": True},
                    "result": {
                        "data": {
                            "card": {
                                "id": "card-1",
                                "title": "Тестовая карточка",
                                "vehicle": "Toyota Corolla",
                                "column": "priemka",
                                "description": "Клиент просит проверить тормоза.",
                            }
                        }
                    },
                }
            ],
            status="completed",
        )

        self.assertIn("Тестовая карточка", response)
        self.assertIn("Клиент просит проверить тормоза", response)
        self.assertNotIn("Сейчас пришлю", response)

    def test_card_read_result_is_human_readable_without_internal_fields(self) -> None:
        response = build_execution_response(
            model_decision={"telegram_response": "Сделано."},
            tool_results=[
                {
                    "tool": "get_card",
                    "verify": {"passed": True},
                    "result": {
                        "data": {
                            "card": {
                                "id": "C-244DACF8",
                                "title": "тестовая карточка",
                                "vehicle": "Toyota Corolla",
                                "column": "column_6",
                                "column_label": "В работе",
                                "description": "Toyota Corolla 2016. Тестовая карточка.",
                                "vehicle_profile": {
                                    "vin": "JTDBR32E302123456",
                                    "make_display": "Toyota",
                                    "model_display": "Corolla",
                                    "production_year": "2016",
                                    "drivetrain": "FWD",
                                },
                                "status": "open",
                                "tags": [{"label": "TEST"}],
                                "deadline": {"target": "2026-04-25T14:17:24"},
                            }
                        }
                    },
                }
            ],
            status="completed",
        )

        self.assertIn("Карточка: тестовая карточка", response)
        self.assertIn("Авто: Toyota Corolla", response)
        self.assertIn("VIN: JTDBR32E302123456", response)
        self.assertIn("Колонка: В работе", response)
        self.assertIn("Описание:", response)
        self.assertNotIn("Сделано", response)
        self.assertNotIn("get_card", response)
        self.assertNotIn("проверено", response)
        self.assertNotIn("column_6", response)
        self.assertNotIn("Статус", response)
        self.assertNotIn("open", response)
        self.assertNotIn("tags", response)
        self.assertNotIn("deadline", response)

    def test_image_analysis_result_is_surfaced_in_telegram_reply(self) -> None:
        response = build_execution_response(
            model_decision={"telegram_response": "Проверил фото."},
            tool_results=[
                {
                    "tool": "analyze_card_image_attachment",
                    "verify": {"passed": True},
                    "result": {
                        "data": {
                            "image_facts": {
                                "vin": "WAUZZZ8V0JA000001",
                                "license_plate": "А123ВС",
                                "confidence": "high",
                            }
                        }
                    },
                }
            ],
            status="completed",
        )

        self.assertIn("Фото:", response)
        self.assertIn("vin: WAUZZZ8V0JA000001", response)
        self.assertIn("license_plate: А123ВС", response)


class TelegramAICRMToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        logger = logging.getLogger(f"test.telegram_ai.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(state_file=Path(self.temp_dir.name) / "state.json", logger=logger)
        self.service = CardService(self.store, logger)
        self.port = reserve_port()
        self.server = ApiServer(self.service, logger, start_port=self.port, fallback_limit=1)
        self.server.start()
        self.client = BoardApiClient(
            self.server.base_url, logger=logger, default_source="telegram_ai"
        )

    def tearDown(self) -> None:
        self.server.stop()
        self.temp_dir.cleanup()

    def test_registry_create_update_move_with_verification(self) -> None:
        registry = CRMToolRegistry(self.client, actor_name="TEST_TELEGRAM_AI")

        created = registry.execute(
            {
                "tool": "create_card",
                "arguments": {
                    "title": "Telegram AI card",
                    "description": "Создано тестом",
                    "vehicle": "Toyota Camry",
                },
            },
            role="owner",
        )
        card_id = created["result"]["data"]["card"]["id"]
        self.assertTrue(created["verify"]["passed"])

        updated = registry.execute(
            {
                "tool": "update_card",
                "arguments": {"card_id": card_id, "description": "Обновлено Telegram AI"},
            },
            role="owner",
        )
        self.assertTrue(updated["verify"]["passed"])

        moved = registry.execute(
            {"tool": "move_card", "arguments": {"card_id": card_id, "column": "in_progress"}},
            role="owner",
        )
        self.assertTrue(moved["verify"]["passed"])

        fetched = self.client.get_card(card_id)
        self.assertEqual(fetched["data"]["card"]["column"], "in_progress")
        self.assertEqual(fetched["data"]["card"]["description"], "Обновлено Telegram AI")

        rollback = registry.rollback_tool_result(moved, role="owner")
        self.assertEqual(rollback["tool"], "rollback_move_card")
        rolled_back = self.client.get_card(card_id)
        self.assertNotEqual(rolled_back["data"]["card"]["column"], "in_progress")

    def test_registry_attaches_current_telegram_photo_to_card(self) -> None:
        registry = CRMToolRegistry(self.client, actor_name="TEST_TELEGRAM_AI")
        created = registry.execute(
            {"tool": "create_card", "arguments": {"title": "Photo target"}},
            role="owner",
        )
        card_id = created["result"]["data"]["card"]["id"]
        registry.set_run_media(
            [
                DownloadedAttachment(
                    attachment=TelegramAttachment(
                        kind="photo",
                        file_id="tg-photo",
                        file_unique_id="unique-photo",
                        mime_type="image/png",
                        file_name="client-photo.png",
                    ),
                    content=PNG_1X1_BYTES,
                    file_path="photos/client-photo.png",
                )
            ]
        )

        attached = registry.execute(
            {
                "tool": "attach_telegram_photo_to_card",
                "arguments": {"card_id": card_id, "media_index": 0},
            },
            role="owner",
        )

        self.assertTrue(attached["verify"]["passed"])
        attachment_id = attached["result"]["data"]["attachment"]["id"]
        listed = self.client.list_card_attachments(card_id)
        self.assertEqual(listed["data"]["attachments"][0]["id"], attachment_id)
        self.assertEqual(listed["data"]["attachments"][0]["content_kind"], "image")

    def test_registry_analyzes_existing_card_image_attachment(self) -> None:
        captured: dict[str, object] = {}

        def fake_analyzer(**kwargs):
            captured.update(kwargs)
            return {"vin": "WAUZZZ8V0JA000001", "confidence": "high"}

        registry = CRMToolRegistry(
            self.client,
            actor_name="TEST_TELEGRAM_AI",
            image_analyzer=fake_analyzer,
        )
        created = registry.execute(
            {"tool": "create_card", "arguments": {"title": "Image read target"}},
            role="owner",
        )
        card_id = created["result"]["data"]["card"]["id"]
        upload = self.client.add_card_attachment(
            card_id=card_id,
            file_name="existing.png",
            mime_type="image/png",
            content=PNG_1X1_BYTES,
            actor_name="TEST_TELEGRAM_AI",
        )
        attachment_id = upload["data"]["attachment"]["id"]

        analyzed = registry.execute(
            {
                "tool": "analyze_card_image_attachment",
                "arguments": {"card_id": card_id, "attachment_id": attachment_id},
            },
            role="owner",
        )

        self.assertEqual(captured["image_bytes"], PNG_1X1_BYTES)
        self.assertEqual(captured["mime_type"], "image/png")
        self.assertEqual(analyzed["result"]["data"]["image_facts"]["confidence"], "high")

    def test_registry_exposes_extended_board_sticky_cashbox_and_archive_tools(self) -> None:
        registry = CRMToolRegistry(self.client, actor_name="TEST_TELEGRAM_AI")
        names = {definition.name for definition in registry.definitions}
        self.assertIn("review_board", names)
        self.assertIn("bulk_move_cards", names)
        self.assertIn("mark_card_ready", names)
        self.assertIn("search_clients", names)
        self.assertIn("link_card_to_client", names)
        self.assertIn("upsert_client_vehicle", names)
        self.assertIn("create_sticky", names)
        self.assertIn("create_cash_transaction", names)

        first = registry.execute(
            {"tool": "create_card", "arguments": {"title": "Bulk one"}},
            role="owner",
        )["result"]["data"]["card"]["id"]
        second = registry.execute(
            {"tool": "create_card", "arguments": {"title": "Bulk two"}},
            role="owner",
        )["result"]["data"]["card"]["id"]
        bulk = registry.execute(
            {
                "tool": "bulk_move_cards",
                "arguments": {"card_ids": [first, second], "column": "in_progress"},
            },
            role="owner",
        )
        self.assertTrue(bulk["verify"]["passed"])

        archived = registry.execute(
            {"tool": "archive_card", "arguments": {"card_id": first}},
            role="owner",
        )
        self.assertTrue(archived["verify"]["passed"])
        restored = registry.execute(
            {"tool": "restore_card", "arguments": {"card_id": first, "column": "in_progress"}},
            role="owner",
        )
        self.assertTrue(restored["verify"]["passed"])

        column = registry.execute(
            {"tool": "create_column", "arguments": {"label": "Telegram AI test column"}},
            role="owner",
        )
        self.assertTrue(column["verify"]["passed"])
        column_id = column["result"]["data"]["column"]["id"]
        renamed = registry.execute(
            {
                "tool": "rename_column",
                "arguments": {"column_id": column_id, "label": "Telegram AI renamed"},
            },
            role="owner",
        )
        self.assertTrue(renamed["verify"]["passed"])

        sticky = registry.execute(
            {
                "tool": "create_sticky",
                "arguments": {
                    "text": "Telegram sticky",
                    "x": 10,
                    "y": 20,
                    "deadline": {"hours": 1},
                },
            },
            role="owner",
        )
        self.assertTrue(sticky["verify"]["passed"])
        sticky_id = sticky["result"]["data"]["sticky"]["id"]
        moved_sticky = registry.execute(
            {"tool": "move_sticky", "arguments": {"sticky_id": sticky_id, "x": 40, "y": 50}},
            role="owner",
        )
        self.assertTrue(moved_sticky["verify"]["passed"])

        cashbox = registry.execute(
            {"tool": "create_cashbox", "arguments": {"name": "Telegram AI cashbox"}},
            role="owner",
        )
        self.assertTrue(cashbox["verify"]["passed"])
        cashbox_id = cashbox["result"]["data"]["cashbox"]["id"]
        transaction = registry.execute(
            {
                "tool": "create_cash_transaction",
                "arguments": {
                    "cashbox_id": cashbox_id,
                    "direction": "income",
                    "amount_minor": 1000,
                    "note": "Telegram AI test",
                },
            },
            role="owner",
        )
        self.assertTrue(transaction["verify"]["passed"])

        for tool in (
            "get_board_context",
            "review_board",
            "get_cards",
            "list_columns",
            "list_archived_cards",
            "list_repair_orders",
            "list_cashboxes",
        ):
            result = registry.execute({"tool": tool, "arguments": {}}, role="owner")
            self.assertIn("result", result)

    def test_registry_rejects_unknown_and_non_owner_write(self) -> None:
        registry = CRMToolRegistry(self.client, actor_name="TEST_TELEGRAM_AI")

        with self.assertRaises(CRMToolError):
            registry.execute({"tool": "unknown_tool", "arguments": {}}, role="owner")
        with self.assertRaises(CRMToolError):
            registry.execute(
                {"tool": "create_card", "arguments": {"title": "Denied"}},
                role="viewer",
            )

    def test_context_builder_reads_board_without_writes(self) -> None:
        self.client.create_card(title="Camry context", description="Проверить ходовую")
        context = CRMContextBuilder(self.client).build(command_text="Покажи Camry")

        self.assertIn("board_snapshot", context)
        self.assertEqual(context["search_hint"], "Camry")
        self.assertIn("search_results", context)


if __name__ == "__main__":
    unittest.main()
