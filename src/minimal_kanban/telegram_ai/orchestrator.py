from __future__ import annotations

import uuid
from typing import Any

from .audit import TelegramAIAuditService
from .auth import ROLE_UNAUTHORIZED, TelegramAuthService
from .context import CRMContextBuilder
from .crm_tools import CRMToolError, CRMToolRegistry
from .memory import TelegramAIConversationMemory, latest_card_state
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
Найди в интернете официальный сайт Toyota
Найди в интернете артикул воздушного фильтра для Prado
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
        command_text = normalized_input.command_text
        crm_context: dict[str, Any] = {}
        try:
            if identity.role == ROLE_UNAUTHORIZED:
                context.final_status = "failed"
                context.telegram_response = "Доступ запрещён."
                return context.telegram_response
            command_text = self._enrich_from_media(
                command_text,
                context,
                downloaded_attachments or [],
            )
            if context.voice_transcription_error and not command_text.strip():
                context.final_status = "completed"
                context.telegram_response = context.voice_transcription_error
                return context.telegram_response
            builtin_response = self._handle_builtin(command_text, context)
            if builtin_response is not None:
                context.final_status = "completed"
                context.telegram_response = builtin_response
                return builtin_response
            self._attach_conversation_memory(crm_context, normalized_input)
            internet_response = self._handle_internet_search(
                command_text,
                context,
                crm_context=crm_context,
            )
            if internet_response is not None:
                context.final_status = "completed"
                context.telegram_response = internet_response
                return internet_response
            crm_context.update(self._context_builder.build(command_text=command_text))
            self._attach_conversation_memory(crm_context, normalized_input)
            context.context_summary = self._context_builder.summary(crm_context)
            local_response = self._handle_local_crm_command(command_text, crm_context)
            if local_response is not None:
                context.final_status = "completed"
                context.telegram_response = local_response
                return local_response
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
        except (CRMToolError, ValueError) as exc:
            context.final_status = "failed"
            context.error = str(exc)
            context.telegram_response = build_execution_response(
                model_decision=context.model_decision,
                tool_results=context.tool_results,
                status="failed",
                error=context.error,
            )
            return context.telegram_response
        except TelegramAIModelError as exc:
            context.error = str(exc)
            fallback = self._model_failure_fallback(
                command_text=command_text,
                context=context,
                crm_context=crm_context,
                error=context.error,
            )
            context.final_status = "completed" if fallback else "failed"
            context.telegram_response = fallback or build_execution_response(
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
            conversation_state = latest_card_state(memory_rows)
            if conversation_state:
                crm_context["conversation_state"] = conversation_state

    def _enrich_from_media(
        self,
        command_text: str,
        context: RunContext,
        attachments: list[DownloadedAttachment],
    ) -> str:
        text = str(command_text or "").strip()
        for item in attachments:
            if item.attachment.kind == "voice":
                try:
                    transcript = self._model_client.transcribe_audio(
                        audio_bytes=item.content,
                        filename=item.file_name,
                        mime_type=item.mime_type,
                    )
                except TelegramAIModelError:
                    context.voice_transcription_error = (
                        "Не смог распознать голосовое сообщение сейчас. "
                        "Пришлите текстом или повторите позже."
                    )
                    continue
                context.transcribed_text = transcript
                text = f"{text}\n{transcript}".strip()
            elif item.attachment.kind == "photo":
                try:
                    facts = self._model_client.analyze_image(
                        image_bytes=item.content,
                        mime_type=item.mime_type or "image/jpeg",
                        caption=context.normalized_input.caption,
                    )
                except TelegramAIModelError as exc:
                    facts = {
                        "confidence": "low",
                        "notes": "Не удалось автоматически разобрать фото.",
                        "error": str(exc),
                    }
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
            web_search_flag = getattr(self._model_client, "web_search_enabled", None)
            if web_search_flag is None:
                web_search_status = "неизвестно"
            else:
                web_search_status = "включён" if web_search_flag else "выключен"
            return (
                "Telegram AI worker активен.\n"
                f"Роль: {context.role}.\n"
                f"Модель: {self._model_client.model}.\n"
                f"Сильная модель: {getattr(self._model_client, 'strong_model', '-')}.\n"
                f"Интернет-поиск: {web_search_status}."
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

    def _handle_internet_search(
        self,
        command_text: str,
        context: RunContext,
        *,
        crm_context: dict[str, Any] | None = None,
    ) -> str | None:
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
            crm_context=crm_context or {},
        )
        return str(response or "").strip() or "Поиск выполнен, но модель не вернула текст ответа."

    def _handle_local_crm_command(
        self, command_text: str, crm_context: dict[str, Any]
    ) -> str | None:
        text = str(command_text or "").strip()
        lowered = text.lower()
        if _is_ping_or_greeting(lowered):
            return "На связи. Голос, текст и CRM-контур доступны."
        if _asks_active_cards_count(lowered):
            count = _active_cards_count(crm_context)
            if count is not None:
                return f"Активных карточек: {count}."
        if _asks_board_summary(lowered):
            summary = _board_summary(crm_context)
            if summary:
                return summary
        return None

    def _model_failure_fallback(
        self,
        *,
        command_text: str,
        context: RunContext,
        crm_context: dict[str, Any],
        error: str,
    ) -> str:
        text = str(command_text or "").strip()
        lowered = text.lower()
        if _is_rate_limit_error(error):
            if _asks_card_search(lowered):
                search_summary = _search_results_summary(crm_context)
                if search_summary:
                    return search_summary
            if _is_ping_or_greeting(lowered):
                return "На связи. Голос распознал, Telegram отвечает. Сейчас внешний AI API ограничивает запросы, поэтому сложные команды могут выполняться нестабильно."
            if context.transcribed_text:
                return (
                    "Голос распознал:\n"
                    f"{context.transcribed_text}\n\n"
                    "Но внешний AI API сейчас ограничивает запросы. Простые CRM-команды я обработаю напрямую, сложный анализ лучше повторить чуть позже."
                )
            return (
                "Telegram-бот на связи, но внешний AI API сейчас ограничивает запросы. "
                "Простые CRM-команды можно выполнять, сложный анализ и веб-поиск могут временно не пройти."
            )
        if context.image_facts.get("error"):
            return "Фото получил, но сейчас не смог автоматически разобрать изображение. Можно отправить команду текстом к этому фото."
        return ""

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
    explicit_markers = (
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
    research_markers = (
        "официальный сайт",
        "артикул",
        "oem",
        "оригинал",
        "аналог",
        "аналоги",
        "запчаст",
        "источник",
        "ссылк",
        "цена",
        "стоимость",
        "где купить",
        "какой фильтр",
        "какой артикул",
    )
    crm_markers = (
        "карточк",
        "доска",
        "доске",
        "колонк",
        "заказ-наряд",
        "заказ наряд",
        "касс",
        "sticky",
        "стикер",
        "вложен",
        "repair order",
        "repair-order",
    )
    if any(marker in text for marker in explicit_markers):
        return True
    if any(marker in text for marker in research_markers) and not any(
        marker in text for marker in crm_markers
    ):
        return True
    return False


def _is_ping_or_greeting(lowered: str) -> bool:
    text = str(lowered or "").strip()
    if text in {"привет", "здравствуйте", "добрый день", "добрый вечер", "hello", "hi"}:
        return True
    return any(
        marker in text
        for marker in (
            "ты здесь",
            "ты на связи",
            "на связи",
            "живой",
            "работаешь",
            "проверим связь",
        )
    )


def _asks_active_cards_count(lowered: str) -> bool:
    text = str(lowered or "")
    return "сколько" in text and "актив" in text and "карточ" in text


def _asks_board_summary(lowered: str) -> bool:
    text = str(lowered or "")
    return any(marker in text for marker in ("кратко по доске", "что горит", "сводка доски"))


def _asks_card_search(lowered: str) -> bool:
    text = str(lowered or "")
    return "карточ" in text and any(marker in text for marker in ("найди", "покажи", "отыщи"))


def _active_cards_count(crm_context: dict[str, Any]) -> int | None:
    snapshot = crm_context.get("board_snapshot") if isinstance(crm_context, dict) else {}
    if not isinstance(snapshot, dict):
        return None
    for key in ("active_cards_total", "cards_total", "total_active_cards"):
        value = snapshot.get(key)
        if isinstance(value, int):
            return value
    cards = snapshot.get("cards")
    return len(cards) if isinstance(cards, list) else None


def _board_summary(crm_context: dict[str, Any]) -> str:
    active_count = _active_cards_count(crm_context)
    review = crm_context.get("board_review") if isinstance(crm_context, dict) else {}
    review = review if isinstance(review, dict) else {}
    alerts = review.get("alerts") if isinstance(review.get("alerts"), list) else []
    lines = ["Кратко по доске:"]
    if active_count is not None:
        lines.append(f"Активных карточек: {active_count}.")
    if alerts:
        lines.append(f"Сигналов внимания: {len(alerts)}.")
        for item in alerts[:5]:
            if isinstance(item, dict):
                title = str(
                    item.get("title") or item.get("message") or item.get("text") or ""
                ).strip()
            else:
                title = str(item or "").strip()
            if title:
                lines.append(f"- {title[:140]}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _search_results_summary(crm_context: dict[str, Any]) -> str:
    search = crm_context.get("search_results") if isinstance(crm_context, dict) else {}
    if not isinstance(search, dict):
        return ""
    rows = None
    for key in ("cards", "results", "items"):
        value = search.get(key)
        if isinstance(value, list):
            rows = value
            break
    if rows is None:
        return ""
    if not rows:
        return "Карточек по этому запросу не нашёл."
    lines = [f"Нашёл карточки: {len(rows)}"]
    for card in rows[:8]:
        if not isinstance(card, dict):
            continue
        title = str(card.get("title") or card.get("heading") or "").strip()
        vehicle = str(card.get("vehicle") or "").strip()
        vin = _card_vin(card)
        column = str(card.get("column_label") or card.get("column") or "").strip()
        parts = [part for part in (title, vehicle, vin, column) if part]
        if parts:
            lines.append("- " + " | ".join(parts))
    return "\n".join(lines)


def _card_vin(card: dict[str, Any]) -> str:
    profile = card.get("vehicle_profile") if isinstance(card.get("vehicle_profile"), dict) else {}
    compact = (
        card.get("vehicle_profile_compact")
        if isinstance(card.get("vehicle_profile_compact"), dict)
        else {}
    )
    vin = str(card.get("vin") or profile.get("vin") or compact.get("vin") or "").strip()
    return f"VIN: {vin}" if vin else ""


def _is_rate_limit_error(error: str) -> bool:
    text = str(error or "").lower()
    return "429" in text or "too many requests" in text or "rate limit" in text
