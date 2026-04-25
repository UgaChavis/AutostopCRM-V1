from __future__ import annotations

import uuid
from typing import Any

from .audit import TelegramAIAuditService
from .auth import ROLE_UNAUTHORIZED, TelegramAuthService
from .context import CRMContextBuilder
from .crm_tools import CRMToolError, CRMToolRegistry
from .memory import TelegramAIConversationMemory
from .models import DownloadedAttachment, NormalizedTelegramInput, RunContext
from .openai_client import TelegramAIModelError, TelegramAIOpenAIClient
from .response import build_execution_response, build_recent_actions_response

HELP_TEXT = """AutoStop Telegram AI готов.
Команды:
/status - статус связи
/help - помощь
Кратко по доске
Создай карточку: BMW X5, диагностика пневмы, сегодня до 18:00
Найди просроченные карточки
Что ты сделал сегодня?
"""


class TelegramAIOrchestrator:
    def __init__(
        self,
        *,
        auth: TelegramAuthService,
        model_client: TelegramAIOpenAIClient,
        context_builder: CRMContextBuilder,
        tool_registry: CRMToolRegistry,
        audit: TelegramAIAuditService,
        memory: TelegramAIConversationMemory | None = None,
    ) -> None:
        self._auth = auth
        self._model_client = model_client
        self._context_builder = context_builder
        self._tool_registry = tool_registry
        self._audit = audit
        self._memory = memory

    def handle(
        self,
        normalized_input: NormalizedTelegramInput,
        *,
        downloaded_attachments: list[DownloadedAttachment] | None = None,
    ) -> str:
        identity = self._auth.resolve(
            user_id=normalized_input.user_id,
            username=normalized_input.username,
        )
        context = RunContext(
            run_id=f"tgai_{uuid.uuid4().hex[:12]}",
            role=identity.role,
            normalized_input=normalized_input,
        )
        try:
            if identity.role == ROLE_UNAUTHORIZED:
                context.final_status = "failed"
                context.telegram_response = "Доступ запрещён."
                return context.telegram_response
            command_text = normalized_input.command_text
            command_text = self._enrich_from_media(
                command_text,
                context,
                downloaded_attachments or [],
            )
            builtin_response = self._handle_builtin(command_text, context)
            if builtin_response is not None:
                context.final_status = "completed"
                context.telegram_response = builtin_response
                return builtin_response
            internet_response = self._handle_internet_search(command_text, context)
            if internet_response is not None:
                context.final_status = "completed"
                context.telegram_response = internet_response
                return internet_response
            crm_context = self._context_builder.build(command_text=command_text)
            self._attach_conversation_memory(crm_context, normalized_input)
            context.context_summary = self._context_builder.summary(crm_context)
            decision = self._model_client.decide(
                command_text=command_text,
                role=identity.role,
                crm_context=crm_context,
                tool_catalog=self._tool_registry.catalog_for_model(),
                image_facts=context.image_facts,
            )
            context.model_decision = decision
            actions = _normalized_actions(decision)
            context.planned_actions = actions
            self._tool_registry.set_run_media(downloaded_attachments or [])
            for action in actions:
                context.tool_calls.append(
                    {
                        "tool": action.get("tool"),
                        "arguments": action.get("arguments")
                        if isinstance(action.get("arguments"), dict)
                        else {},
                        "reason": action.get("reason") or "",
                    }
                )
                result = self._tool_registry.execute(action, role=identity.role)
                context.tool_results.append(result)
            context.final_status = "completed"
            context.verify_result = {
                "passed": all(
                    (item.get("verify") if isinstance(item.get("verify"), dict) else {}).get(
                        "passed", True
                    )
                    for item in context.tool_results
                )
            }
            final_decision = self._with_final_tool_response(
                decision=decision,
                command_text=command_text,
                role=identity.role,
                tool_results=context.tool_results,
            )
            context.telegram_response = build_execution_response(
                model_decision=final_decision,
                tool_results=context.tool_results,
                status=context.final_status,
            )
            return context.telegram_response
        except (CRMToolError, TelegramAIModelError, ValueError) as exc:
            context.final_status = "failed"
            context.error = str(exc)
            context.telegram_response = build_execution_response(
                model_decision=context.model_decision,
                tool_results=context.tool_results,
                status="failed",
                error=context.error,
            )
            return context.telegram_response
        finally:
            if hasattr(self._tool_registry, "clear_run_media"):
                self._tool_registry.clear_run_media()
            if self._memory is not None:
                self._memory.append_run(context)
            self._audit.write_run(context)

    def _attach_conversation_memory(
        self, crm_context: dict[str, Any], normalized_input: NormalizedTelegramInput
    ) -> None:
        if self._memory is None:
            return
        memory_rows = self._memory.recent(
            chat_id=normalized_input.chat_id,
            user_id=normalized_input.user_id,
        )
        if memory_rows:
            crm_context["conversation_memory"] = memory_rows

    def _enrich_from_media(
        self,
        command_text: str,
        context: RunContext,
        attachments: list[DownloadedAttachment],
    ) -> str:
        text = str(command_text or "").strip()
        for item in attachments:
            if item.attachment.kind == "voice":
                transcript = self._model_client.transcribe_audio(
                    audio_bytes=item.content,
                    filename=item.file_name,
                    mime_type=item.mime_type,
                )
                context.transcribed_text = transcript
                text = f"{text}\n{transcript}".strip()
            elif item.attachment.kind == "photo":
                facts = self._model_client.analyze_image(
                    image_bytes=item.content,
                    mime_type=item.mime_type or "image/jpeg",
                    caption=context.normalized_input.caption,
                )
                context.image_facts = facts
                context.image_facts.setdefault("telegram_media", [])
                if isinstance(context.image_facts["telegram_media"], list):
                    context.image_facts["telegram_media"].append(
                        {
                            "media_index": len(context.image_facts["telegram_media"]),
                            "file_name": item.file_name,
                            "mime_type": item.mime_type or "image/jpeg",
                            "size_bytes": len(item.content),
                            "width": item.attachment.width,
                            "height": item.attachment.height,
                            "attach_tool": "attach_telegram_photo_to_card",
                        }
                    )
                text = text or context.normalized_input.caption or "Обработай фото для CRM."
        return text

    def _handle_builtin(self, command_text: str, context: RunContext) -> str | None:
        text = str(command_text or "").strip()
        lowered = text.lower()
        if lowered in {"/start", "/help", "help", "помощь"}:
            return HELP_TEXT
        if lowered == "/status":
            return (
                "Telegram AI worker активен.\n"
                f"Роль: {context.role}.\n"
                f"Модель: {self._model_client.model}."
            )
        if (
            "что ты сделал" in lowered
            or "последние действия ai" in lowered
            or "последние действия ии" in lowered
        ):
            return build_recent_actions_response(self._audit.recent(limit=10))
        if "откати последнее" in lowered or lowered.startswith("откати"):
            return self._rollback_last_action(context)
        return None

    def _handle_internet_search(self, command_text: str, context: RunContext) -> str | None:
        if not _looks_like_internet_search(command_text):
            return None
        context.model_decision = {
            "intent": "internet_search",
            "actions": [],
            "telegram_response": "",
        }
        response = self._model_client.internet_search(
            command_text=command_text,
            role=context.role,
        )
        return str(response or "").strip() or "Поиск выполнен, но модель не вернула текст ответа."

    def _with_final_tool_response(
        self,
        *,
        decision: dict[str, Any],
        command_text: str,
        role: str,
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not tool_results or not hasattr(self._model_client, "final_response"):
            return decision
        try:
            final_text = self._model_client.final_response(
                command_text=command_text,
                role=role,
                model_decision=decision,
                tool_results=tool_results,
            )
        except TelegramAIModelError:
            return decision
        if not str(final_text or "").strip():
            return decision
        updated = dict(decision)
        updated["telegram_response"] = str(final_text).strip()
        return updated

    def _rollback_last_action(self, context: RunContext) -> str:
        for row in self._audit.recent(limit=20):
            tool_results = (
                row.get("tool_results") if isinstance(row.get("tool_results"), list) else []
            )
            for tool_result in reversed(tool_results):
                if not isinstance(tool_result, dict):
                    continue
                rollback_result = self._tool_registry.rollback_tool_result(
                    tool_result,
                    role=context.role,
                )
                context.tool_results.append(rollback_result)
                context.verify_result = {"passed": True, "rollback": rollback_result.get("tool")}
                return f"Откатил последнее обратимое действие: {rollback_result.get('tool')}."
        return "Не нашёл обратимых AI-действий для отката."


def _normalized_actions(decision: dict[str, Any]) -> list[dict[str, Any]]:
    actions = decision.get("actions") if isinstance(decision.get("actions"), list) else []
    normalized: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        tool = str(action.get("tool") or "").strip()
        if not tool:
            continue
        arguments = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
        normalized.append(
            {
                "tool": tool,
                "arguments": arguments,
                "reason": str(action.get("reason") or ""),
            }
        )
    return normalized


def _looks_like_internet_search(command_text: str) -> bool:
    text = str(command_text or "").strip().lower()
    if not text:
        return False
    markers = (
        "найди в интернете",
        "поищи в интернете",
        "поиск в интернете",
        "выйди в интернет",
        "зайди в интернет",
        "загугли",
        "погугли",
        "посмотри в интернете",
        "проверь в интернете",
        "найди актуальную",
        "актуальная информация",
        "найди информацию в сети",
        "поищи в сети",
        "web search",
        "internet search",
    )
    return any(marker in text for marker in markers)
