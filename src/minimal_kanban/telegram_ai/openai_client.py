from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from .config import TelegramAIConfig


class TelegramAIModelError(RuntimeError):
    pass


class TelegramAIOpenAIClient:
    def __init__(self, config: TelegramAIConfig) -> None:
        if not config.openai_api_key:
            raise TelegramAIModelError("OPENAI_API_KEY is not configured.")
        self._api_key = config.openai_api_key
        self._base_url = config.openai_base_url.rstrip("/")
        self._model = config.model
        self._strong_model = config.strong_model or config.model
        self._vision_model = config.vision_model
        self._transcription_model = config.transcription_model
        self._reasoning_effort = config.reasoning_effort
        self._strong_reasoning_effort = config.strong_reasoning_effort or config.reasoning_effort
        self._timeout_seconds = config.openai_request_timeout_seconds
        self._web_search_enabled = config.web_search_enabled
        self._local_transcription_model = config.local_transcription_model
        self._local_transcription_download_root = config.data_dir / "models" / "faster_whisper"

    @property
    def model(self) -> str:
        return self._model

    @property
    def web_search_enabled(self) -> bool:
        return self._web_search_enabled

    @property
    def strong_model(self) -> str:
        return self._strong_model

    def internet_search(
        self,
        *,
        command_text: str,
        role: str,
        crm_context: dict[str, Any] | None = None,
    ) -> str:
        if not self._web_search_enabled:
            raise TelegramAIModelError("OpenAI web search is disabled.")
        instructions = """
You are AutoStop CRM Telegram AI Board Manager.
Language: Russian.
Use web search now and answer directly.
Do not create CRM actions or promise to send later.
If last_vin exists in crm_context, use it and do not ask for the VIN again.
Write a short Telegram-ready answer with emojis and clear paragraphs.
Use this structure when it fits:
🔎 Коротко: one direct conclusion.
✅ Найдено: exact part numbers, names, or facts.
🧩 Почему подходит: short compatibility reasoning, VIN/vehicle facts, limits.
Do not include sources, source lists, links, raw URLs, markdown links, or markdown tables.
If the exact part number is not confirmed, say that clearly and list what data is missing.
""".strip()
        search_context = _compact_search_context(crm_context or {})
        user_payload = {
            "command_text": command_text,
            "role": role,
            "mode": "internet_search",
        }
        for key in ("resolved_vin", "resolved_card"):
            value = search_context.get(key)
            if value not in (None, "", [], {}):
                user_payload[key] = value
        primary_model = self._model_for_internet_search(command_text, user_payload)
        fallback_model = self._model if primary_model != self._model else None
        models = [primary_model, *([fallback_model] if fallback_model else [primary_model])]
        last_error: TelegramAIModelError | None = None
        for model in models:
            try:
                return _sanitize_telegram_search_answer(
                    self._responses_text(
                        model=model,
                        instructions=instructions,
                        input_messages=[
                            {
                                "role": "user",
                                "content": json.dumps(
                                    user_payload,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                ),
                            }
                        ],
                        web_search=True,
                        reasoning_effort=self._reasoning_effort,
                        request_timeout_seconds=max(
                            self._timeout_seconds,
                            (
                                120.0
                                if model == self._strong_model and model != self._model
                                else 60.0
                            ),
                        ),
                        max_attempts=3,
                    )
                )
            except TelegramAIModelError as exc:
                last_error = exc
        raise last_error or TelegramAIModelError("OpenAI web search request failed.")

    def decide(
        self,
        *,
        command_text: str,
        role: str,
        crm_context: dict[str, Any],
        tool_catalog: list[dict[str, Any]],
        image_facts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        instructions = _decision_instructions(role=role, tool_catalog=tool_catalog)
        user_payload = {
            "command_text": command_text,
            "role": role,
            "crm_context": crm_context,
            "image_facts": image_facts or {},
        }
        model = self._model_for_command(command_text)
        return self._responses_json(
            model=model,
            instructions=instructions,
            input_messages=[
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
                }
            ],
            web_search=False,
            reasoning_effort=self._reasoning_for_model(model),
        )

    def final_response(
        self,
        *,
        command_text: str,
        role: str,
        model_decision: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> str:
        instructions = """
You are AutoStop CRM Telegram AI Board Manager.
Language: Russian.
You receive completed CRM tool results and must write the final Telegram answer now.
Do not promise to send anything later. Do not say "сейчас пришлю", "потом пришлю",
"вернусь с результатом", or similar future-follow-up phrases.
Do not create new actions and do not ask the user to wait.
Return only JSON with this shape:
{"telegram_response":"short final answer with the actual result from tool_results"}
If tool_results contain card text, repair order text, card list, counts, or write results, include the useful result directly.
Write for a human, not for a developer. Do not mention internal tool names, ids, status values, tags, deadline timestamps, column ids, verification marks, or raw JSON fields unless the user explicitly asked for technical details.
For a card read, prefer a short readable summary: card title, vehicle, VIN if present, current column label if meaningful, and a compact description.
Keep the answer compact, but complete enough that no second message is needed.
""".strip()
        user_payload = {
            "command_text": command_text,
            "role": role,
            "model_decision": model_decision,
            "tool_results": _compact_for_final_response(tool_results),
        }
        model = self._model_for_command(command_text)
        payload = self._responses_json(
            model=model,
            instructions=instructions,
            input_messages=[
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
                }
            ],
            web_search=False,
            reasoning_effort=self._reasoning_for_model(model),
        )
        return str(payload.get("telegram_response") or "").strip()

    def analyze_image(
        self, *, image_bytes: bytes, mime_type: str, caption: str = ""
    ) -> dict[str, Any]:
        data_url = f"data:{mime_type or 'image/jpeg'};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        instructions = """
You extract operational facts from auto-service photos for AutoStop CRM.
Return only JSON with keys:
vin, license_plate, make, model, mileage, client_name, phone, symptoms, requested_works,
parts, dates, visible_codes, confidence, notes.
Use empty strings or empty arrays when a fact is not visible. Do not invent facts.
""".strip()
        return self._responses_json(
            model=self._vision_model,
            instructions=instructions,
            input_messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Caption: {caption or '-'}\nExtract CRM facts from this image.",
                        },
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ],
            web_search=False,
        )

    def transcribe_audio(self, *, audio_bytes: bytes, filename: str, mime_type: str = "") -> str:
        try:
            local_text = self._transcribe_audio_local(
                audio_bytes=audio_bytes,
                filename=filename,
                mime_type=mime_type,
            )
        except TelegramAIModelError:
            local_text = ""
        if local_text:
            return local_text
        return self._transcribe_audio_openai(
            audio_bytes=audio_bytes,
            filename=filename,
            mime_type=mime_type,
        )

    def _transcribe_audio_local(
        self, *, audio_bytes: bytes, filename: str, mime_type: str = ""
    ) -> str:
        try:
            model = _get_local_whisper_model(
                model_name=self._local_transcription_model,
                download_root=self._local_transcription_download_root,
            )
        except TelegramAIModelError:
            return ""
        upload_name = (filename or "telegram-voice.ogg").strip() or "telegram-voice.ogg"
        with tempfile.TemporaryDirectory(prefix="telegram_ai_local_stt_") as temp_dir:
            temp_dir_path = Path(temp_dir)
            input_path = temp_dir_path / Path(upload_name).name
            input_path.write_bytes(audio_bytes)
            try:
                segments, _info = model.transcribe(
                    str(input_path),
                    language="ru",
                    beam_size=5,
                    vad_filter=True,
                )
            except Exception as exc:
                raise TelegramAIModelError("Local transcription request failed.") from exc
            text = " ".join(
                segment.text.strip()
                for segment in segments
                if getattr(segment, "text", "").strip()
            ).strip()
        return text

    def _transcribe_audio_openai(
        self, *, audio_bytes: bytes, filename: str, mime_type: str = ""
    ) -> str:
        upload_name, upload_mime_type, upload_bytes = self._prepare_transcription_audio(
            audio_bytes=audio_bytes,
            filename=filename,
            mime_type=mime_type,
        )
        headers = {"Authorization": f"Bearer {self._api_key}"}
        files = {
            "file": (
                upload_name,
                upload_bytes,
                upload_mime_type,
            )
        }
        data = {"model": self._transcription_model, "response_format": "json"}
        last_error: Exception | None = None
        timeout = max(self._timeout_seconds, 90.0)
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(
                        f"{self._base_url}/audio/transcriptions",
                        headers=headers,
                        data=data,
                        files=files,
                    )
                response.raise_for_status()
                payload = response.json()
                break
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code not in {408, 409, 429, 500, 502, 503, 504}:
                    raise TelegramAIModelError(_openai_error_message(exc.response)) from exc
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
            if attempt < attempts:
                time.sleep(_retry_delay_seconds(last_error, attempt))
        else:
            raise TelegramAIModelError(f"OpenAI transcription request failed: {last_error}") from last_error
        text = str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""
        if not text:
            raise TelegramAIModelError("OpenAI transcription returned empty text.")
        return text

    def _prepare_transcription_audio(
        self, *, audio_bytes: bytes, filename: str, mime_type: str
    ) -> tuple[str, str, bytes]:
        upload_name = (filename or "telegram-voice.ogg").strip() or "telegram-voice.ogg"
        upload_mime_type = (mime_type or "").strip() or _guess_audio_mime_type(upload_name)
        if _is_supported_audio_upload(upload_name, upload_mime_type):
            return upload_name, upload_mime_type, audio_bytes

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise TelegramAIModelError(
                "OpenAI transcription request failed: unsupported Telegram voice format and ffmpeg is unavailable."
            )

        with tempfile.TemporaryDirectory(prefix="telegram_ai_audio_") as temp_dir:
            temp_dir_path = Path(temp_dir)
            input_suffix = Path(upload_name).suffix or ".ogg"
            input_path = temp_dir_path / f"voice{input_suffix}"
            output_path = temp_dir_path / "voice.mp3"
            input_path.write_bytes(audio_bytes)
            try:
                subprocess.run(
                    [
                        ffmpeg,
                        "-y",
                        "-i",
                        str(input_path),
                        "-vn",
                        "-acodec",
                        "libmp3lame",
                        "-q:a",
                        "4",
                        str(output_path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                stderr = str(exc.stderr or "").strip()
                detail = f": {stderr}" if stderr else ""
                raise TelegramAIModelError(
                    f"OpenAI transcription request failed: voice conversion failed{detail}"
                ) from exc
            if not output_path.exists():
                raise TelegramAIModelError(
                    "OpenAI transcription request failed: voice conversion did not produce output."
                )
            return output_path.name, "audio/mpeg", output_path.read_bytes()

    def _responses_json(
        self,
        *,
        model: str,
        instructions: str,
        input_messages: list[dict[str, Any]],
        web_search: bool = False,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        if web_search:
            raise TelegramAIModelError("JSON responses cannot be combined with web search.")
        payload: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": _ensure_json_keyword_in_input(input_messages),
            "text": {"format": {"type": "json_object"}},
            "reasoning": {"effort": reasoning_effort or self._reasoning_effort},
            "store": False,
        }
        response_payload = self._post_with_retry("/responses", payload)
        output_text = _extract_output_text(response_payload)
        return _parse_json(output_text)

    def _responses_text(
        self,
        *,
        model: str,
        instructions: str,
        input_messages: list[dict[str, Any]],
        web_search: bool = False,
        reasoning_effort: str | None = None,
        request_timeout_seconds: float | None = None,
        max_attempts: int = 3,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input_messages,
            "reasoning": {"effort": reasoning_effort or self._reasoning_effort},
            "store": False,
        }
        if web_search and self._web_search_enabled:
            payload["tools"] = [
                {
                    "type": "web_search_preview",
                    "search_context_size": "low",
                }
            ]
        response_payload = self._post_with_retry(
            "/responses",
            payload,
            timeout_seconds=request_timeout_seconds,
            max_attempts=max_attempts,
        )
        return _extract_output_text(response_payload)

    def _model_for_command(self, command_text: str) -> str:
        if _is_complex_command(command_text) and self._strong_model:
            return self._strong_model
        return self._model

    def _model_for_internet_search(self, command_text: str, payload: dict[str, Any]) -> str:
        if self._strong_model and _is_complex_internet_search(command_text, payload):
            return self._strong_model
        return self._model

    def _reasoning_for_model(self, model: str) -> str:
        if model == self._strong_model and model != self._model:
            return self._strong_reasoning_effort
        return self._reasoning_effort

    def _post_with_retry(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        attempts = max(1, int(max_attempts or 1))
        timeout = timeout_seconds or self._timeout_seconds
        for attempt in range(1, attempts + 1):
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(f"{self._base_url}{path}", headers=headers, json=payload)
                response.raise_for_status()
                parsed = response.json()
                if not isinstance(parsed, dict):
                    raise TelegramAIModelError("OpenAI returned a non-object payload.")
                return parsed
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code not in {408, 409, 429, 500, 502, 503, 504}:
                    raise TelegramAIModelError(_openai_error_message(exc.response)) from exc
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
            if attempt < attempts:
                time.sleep(_retry_delay_seconds(last_error, attempt))
        raise TelegramAIModelError(f"OpenAI request failed: {last_error}") from last_error


def _retry_delay_seconds(error: Exception | None, attempt: int) -> float:
    retry_after = _retry_after_seconds(error)
    if retry_after is not None:
        return retry_after
    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code == 429:
        return min(30.0, 5.0 * attempt * attempt)
    return 0.6 * attempt


def _retry_after_seconds(error: Exception | None) -> float | None:
    if not isinstance(error, httpx.HTTPStatusError):
        return None
    header_value = str(error.response.headers.get("retry-after") or "").strip()
    if not header_value:
        return None
    try:
        return max(0.0, float(header_value))
    except ValueError:
        return None


def _decision_instructions(*, role: str, tool_catalog: list[dict[str, Any]]) -> str:
    return (
        "You are AutoStop CRM Telegram AI Board Manager.\n"
        "Language: Russian.\n"
        "Mode: owner/full_control when role is owner. No confirmation is required for owner.\n"
        "You manage only CRM operational data through explicit tools. Never request shell, git, secrets, or raw storage access.\n"
        "For owner commands, act directly and use the most relevant context instead of asking for repeated details.\n"
        "Ask a clarifying question only when there is no usable target in the current command, conversation memory, or CRM search context.\n"
        "When image_facts.telegram_media is present and the user asks to save or attach the photo to a card, use attach_telegram_photo_to_card with the matching media_index.\n"
        "When the user asks to inspect an image already stored in a card, use list_card_attachments/get_card_attachment/read_card_attachment or analyze_card_image_attachment.\n"
        "Use board/report tools for summaries, card tools for operational card work, sticky/column tools for board layout, cashbox tools for cash operations, and repair-order tools only for repair-order data.\n"
        "Use internet_search when the user wants external web research, parts, prices, official sites, sources, or other non-CRM lookup. Do not answer that internet search is unavailable when the tool catalog includes internet_search.\n"
        "Use crm_context.conversation_memory to resolve references such as 'this card', 'there', 'the previous one', and follow-up commands. Prefer ids from recent verified tool results when the user refers to previous work.\n"
        "If crm_context.conversation_state.last_card exists, treat it as the active card for follow-up commands unless the user explicitly names a different card. If crm_context.conversation_state.last_vin exists, treat it as the active VIN for follow-up commands unless the user explicitly gives a different VIN. If crm_context.conversation_state.card_candidates exists and there is no single last_card, use the candidates to avoid asking the user to restate the search from scratch.\n"
        "Return only one JSON object with this shape:\n"
        "{"
        '"intent":"board_report|card_read|create_card|update_card|move_card|archive_card|restore_card|attachment_work|sticky_work|column_work|cashbox_work|repair_order_update|internet_search|multi_action|no_action",'
        '"confidence":"high|medium|low",'
        '"actions":[{"tool":"tool_name","arguments":{},"reason":"short reason"}],'
        '"telegram_response":"short Russian response to send after execution or question when no action",'
        '"requires_human_confirmation":false'
        "}\n"
        "Allowed tools with schemas:\n"
        f"{json.dumps(tool_catalog, ensure_ascii=False, sort_keys=True)}\n"
        f"Current role: {role}."
    )


def _is_complex_command(command_text: str) -> bool:
    text = str(command_text or "").strip().lower()
    if not text:
        return False
    score = 0
    if len(text) >= 220:
        score += 2
    elif len(text) >= 140:
        score += 1
    strong_markers = (
        "сначала",
        "потом",
        "после этого",
        "затем",
        "несколько шагов",
        "пошагово",
        "проанализируй",
        "сравни",
        "подбери",
        "найди запчаст",
        "аналоги",
        "оригинал",
        "oem",
        "vin",
        "заказ-наряд",
        "по всем карточкам",
        "все карточки",
        "интернет",
        "источники",
        "ссылки",
        "с учетом",
        "сформируй",
        "заполни",
        "обнови",
        "прикрепи",
    )
    score += sum(1 for marker in strong_markers if marker in text)
    separators = text.count(",") + text.count(";") + text.count(" и ") + text.count(" а также ")
    if separators >= 4:
        score += 1
    return score >= 2


def _is_complex_internet_search(command_text: str, payload: dict[str, Any]) -> bool:
    text = str(command_text or "").strip().lower()
    if not text:
        return False
    if payload.get("resolved_vin"):
        return True
    score = 0
    strong_markers = (
        "oem",
        "оригинал",
        "оригинальный",
        "аналог",
        "аналоги",
        "запчаст",
        "артикул",
        "каталог",
        "совместим",
        "подходит",
        "сравни",
        "источники",
        "ссылки",
        "масляный фильтр",
        "воздушный фильтр",
        "тормоз",
    )
    score += sum(1 for marker in strong_markers if marker in text)
    if len(text) >= 120:
        score += 1
    if text.count(",") + text.count(";") + text.count(" и ") >= 3:
        score += 1
    return score >= 2


def _compact_for_final_response(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in tool_results[:12]:
        if not isinstance(item, dict):
            continue
        compact.append(_compact_value(item, max_depth=6))
    return compact


def _compact_search_context(crm_context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(crm_context, dict):
        return {}
    compact: dict[str, Any] = {}
    conversation_state = (
        crm_context.get("conversation_state")
        if isinstance(crm_context.get("conversation_state"), dict)
        else {}
    )
    if conversation_state:
        state: dict[str, Any] = {}
        last_card = conversation_state.get("last_card")
        if isinstance(last_card, dict):
            state["last_card"] = {
                key: last_card.get(key)
                for key in ("id", "title", "vehicle", "column", "status", "vin")
                if last_card.get(key) not in (None, "", [], {})
            }
        last_vin = conversation_state.get("last_vin")
        if last_vin not in (None, "", [], {}):
            state["last_vin"] = str(last_vin)
        card_candidates = conversation_state.get("card_candidates")
        if isinstance(card_candidates, list) and card_candidates:
            state["card_candidates"] = [
                {
                    key: item.get(key)
                    for key in ("id", "title", "vehicle", "column", "status", "vin")
                    if isinstance(item, dict) and item.get(key) not in (None, "", [], {})
                }
                for item in card_candidates[:5]
                if isinstance(item, dict)
            ]
        if state:
            compact["conversation_state"] = state
            last_vin = state.get("last_vin")
            if last_vin not in (None, "", [], {}):
                compact["resolved_vin"] = str(last_vin)
            last_card = state.get("last_card")
            if isinstance(last_card, dict):
                compact["resolved_card"] = {
                    key: last_card.get(key)
                    for key in ("id", "title", "vehicle", "column", "status", "vin")
                    if last_card.get(key) not in (None, "", [], {})
                }
    return compact


def _sanitize_telegram_search_answer(text: str) -> str:
    cleaned = str(text or "").replace("\r", "").strip()
    if not cleaned:
        return ""
    cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
    cleaned = re.sub(r"\[([^\]\n]{1,120})\]\((?:https?://|www\.)[^\s)]+\)", "", cleaned)
    cleaned = re.sub(r"\((?:https?://|www\.)[^\s)]+\)", "", cleaned)
    cleaned = re.sub(r"(?:https?://|www\.)[^\s)\]]+", "", cleaned)
    cleaned = re.sub(r"\butm_[A-Za-z0-9_=-]+", "", cleaned)
    cleaned = re.sub(r"[ \t]+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    lines = []
    source_header = re.compile(r"^(?:[📎•\-\s]*)?(?:источники?|sources?)\b", re.IGNORECASE)
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue
        if source_header.match(line):
            break
        line = re.sub(r"\s+\)$", "", line).strip()
        line = re.sub(r"\(\s*\)", "", line).strip()
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _compact_value(value: Any, *, max_depth: int) -> Any:
    if max_depth <= 0:
        return _truncate_text(value, limit=600)
    if isinstance(value, dict):
        return {
            str(key): _compact_value(item, max_depth=max_depth - 1)
            for key, item in list(value.items())[:80]
            if str(key).lower()
            not in {"content_base64", "base64", "data_url", "token", "api_key", "password"}
        }
    if isinstance(value, list):
        return [_compact_value(item, max_depth=max_depth - 1) for item in value[:30]]
    if isinstance(value, str):
        return _truncate_text(value, limit=2500)
    return value


def _truncate_text(value: Any, *, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "... [truncated]"


def _ensure_json_keyword_in_input(input_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized = json.dumps(input_messages, ensure_ascii=False).lower()
    if "json" in serialized:
        return input_messages
    return [
        *input_messages,
        {
            "role": "user",
            "content": "Return JSON only.",
        },
    ]


def _extract_output_text(payload: dict[str, Any]) -> str:
    text = str(payload.get("output_text") or "").strip()
    if text:
        return text
    chunks: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") in {"output_text", "text"}:
                chunk = content.get("text")
                if chunk:
                    chunks.append(str(chunk))
    return "".join(chunks).strip()


def _parse_json(text: str) -> dict[str, Any]:
    content = str(text or "").strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:].strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        if start < 0:
            raise TelegramAIModelError("OpenAI did not return JSON.")
        payload, _ = json.JSONDecoder().raw_decode(content[start:])
    if not isinstance(payload, dict):
        raise TelegramAIModelError("OpenAI JSON response is not an object.")
    return payload


def _openai_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"OpenAI HTTP {response.status_code}"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message") or "").strip()
        code = str(error.get("code") or "").strip()
        if message and code:
            return f"OpenAI HTTP {response.status_code} ({code}): {message}"
        if message:
            return f"OpenAI HTTP {response.status_code}: {message}"
    return f"OpenAI HTTP {response.status_code}"


def _is_supported_audio_upload(filename: str, mime_type: str) -> bool:
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}:
        return True
    return (mime_type or "").lower() in {
        "audio/mpeg",
        "audio/mp4",
        "audio/m4a",
        "audio/x-m4a",
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
        "video/mp4",
    }


def _guess_audio_mime_type(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".mp4":
        return "video/mp4"
    if suffix in {".mpeg", ".mpga"}:
        return "audio/mpeg"
    if suffix == ".m4a":
        return "audio/m4a"
    if suffix == ".wav":
        return "audio/wav"
    if suffix == ".webm":
        return "audio/webm"
    if suffix in {".ogg", ".oga", ".opus"}:
        return "audio/ogg"
    return "application/octet-stream"


_LOCAL_WHISPER_MODELS: dict[tuple[str, str], Any] = {}


def _get_local_whisper_model(*, model_name: str, download_root: Path) -> Any:
    cache_key = (model_name, str(download_root))
    cached = _LOCAL_WHISPER_MODELS.get(cache_key)
    if cached is not None:
        return cached
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TelegramAIModelError("Local transcription backend is unavailable.") from exc
    download_root.mkdir(parents=True, exist_ok=True)
    try:
        model = WhisperModel(
            model_name,
            device="cpu",
            compute_type="int8",
            download_root=str(download_root),
        )
    except Exception:
        try:
            model = WhisperModel(
                model_name,
                device="cpu",
                compute_type="float32",
                download_root=str(download_root),
            )
        except Exception as fallback_exc:
            raise TelegramAIModelError("Local transcription model failed to load.") from fallback_exc
    _LOCAL_WHISPER_MODELS[cache_key] = model
    return model
