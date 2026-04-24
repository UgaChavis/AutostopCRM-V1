from __future__ import annotations

# ruff: noqa: E402
import json
import logging
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
from minimal_kanban.telegram_ai.models import DownloadedAttachment, TelegramAttachment
from minimal_kanban.telegram_ai.normalizer import normalize_update
from minimal_kanban.telegram_ai.openai_client import TelegramAIOpenAIClient
from minimal_kanban.telegram_ai.orchestrator import TelegramAIOrchestrator
from minimal_kanban.telegram_ai.response import build_execution_response


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
        vision_model="gpt-5.4-mini",
        transcription_model="gpt-4o-mini-transcribe",
        reasoning_effort="medium",
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
    )


class FakeModelClient:
    model = "fake-model"

    def __init__(self) -> None:
        self.decide_calls = 0

    def decide(self, **kwargs):
        self.decide_calls += 1
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


class TelegramAITranscriptionTests(unittest.TestCase):
    def test_voice_ogg_is_converted_before_transcription_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir)
            client = TelegramAIOpenAIClient(config)
            captured: dict[str, object] = {}

            class FakeResponse:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, object]:
                    return {"text": "Создай карточку"}

            class FakeHttpxClient:
                def __init__(self, *args, **kwargs) -> None:
                    return None

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb) -> None:
                    return None

                def post(self, url, headers=None, data=None, files=None):
                    captured["url"] = url
                    captured["headers"] = headers
                    captured["data"] = data
                    captured["files"] = files
                    return FakeResponse()

            def fake_run(cmd, check, capture_output, text):
                Path(cmd[-1]).write_bytes(b"mp3-bytes")
                return None

            with (
                patch(
                    "minimal_kanban.telegram_ai.openai_client.shutil.which", return_value="ffmpeg"
                ),
                patch(
                    "minimal_kanban.telegram_ai.openai_client.subprocess.run", side_effect=fake_run
                ),
                patch("minimal_kanban.telegram_ai.openai_client.httpx.Client", FakeHttpxClient),
            ):
                text = client.transcribe_audio(
                    audio_bytes=b"ogg-bytes",
                    filename="voice.ogg",
                    mime_type="audio/ogg",
                )

            self.assertEqual(text, "Создай карточку")
            file_name, file_bytes, file_mime = captured["files"]["file"]
            self.assertTrue(str(file_name).endswith(".mp3"))
            self.assertEqual(file_bytes, b"mp3-bytes")
            self.assertEqual(file_mime, "audio/mpeg")
            self.assertEqual(captured["data"]["model"], "gpt-4o-mini-transcribe")


class TelegramAIResponsesPayloadTests(unittest.TestCase):
    def test_decide_payload_omits_temperature_for_responses_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = build_config(temp_dir)
            client = TelegramAIOpenAIClient(config)
            captured: dict[str, object] = {}

            def fake_post_with_retry(path: str, payload: dict[str, object]) -> dict[str, object]:
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


class TelegramAIResponseTests(unittest.TestCase):
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
