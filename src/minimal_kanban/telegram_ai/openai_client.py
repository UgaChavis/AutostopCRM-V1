from __future__ import annotations

import base64
import json
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
        self._vision_model = config.vision_model
        self._transcription_model = config.transcription_model
        self._reasoning_effort = config.reasoning_effort
        self._timeout_seconds = config.openai_request_timeout_seconds
        self._web_search_enabled = config.web_search_enabled

    @property
    def model(self) -> str:
        return self._model

    @property
    def web_search_enabled(self) -> bool:
        return self._web_search_enabled

    def internet_search(self, *, command_text: str, role: str) -> str:
        if not self._web_search_enabled:
            raise TelegramAIModelError("OpenAI web search is disabled.")
        instructions = """
You are AutoStop CRM Telegram AI Board Manager.
Language: Russian.
The user explicitly asks for internet research. Use web search and answer now.
Do not create CRM actions. Do not promise to send later.
Keep the answer practical and concise. Include source names or URLs when available.
""".strip()
        user_payload = {
            "command_text": command_text,
            "role": role,
            "mode": "internet_search",
        }
        return self._responses_text(
            model=self._model,
            instructions=instructions,
            input_messages=[
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
                }
            ],
            web_search=True,
        )

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
        return self._responses_json(
            model=self._model,
            instructions=instructions,
            input_messages=[
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
                }
            ],
            web_search=False,
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
Keep the answer compact, but complete enough that no second message is needed.
""".strip()
        user_payload = {
            "command_text": command_text,
            "role": role,
            "model_decision": model_decision,
            "tool_results": _compact_for_final_response(tool_results),
        }
        payload = self._responses_json(
            model=self._model,
            instructions=instructions,
            input_messages=[
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
                }
            ],
            web_search=False,
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
        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.post(
                    f"{self._base_url}/audio/transcriptions",
                    headers=headers,
                    data=data,
                    files=files,
                )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramAIModelError("OpenAI transcription request failed.") from exc
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
    ) -> dict[str, Any]:
        if web_search:
            raise TelegramAIModelError("JSON responses cannot be combined with web search.")
        payload: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": _ensure_json_keyword_in_input(input_messages),
            "text": {"format": {"type": "json_object"}},
            "reasoning": {"effort": self._reasoning_effort},
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
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input_messages,
            "reasoning": {"effort": self._reasoning_effort},
            "store": False,
        }
        if web_search and self._web_search_enabled:
            payload["tools"] = [
                {
                    "type": "web_search_preview",
                    "search_context_size": "medium",
                }
            ]
        response_payload = self._post_with_retry("/responses", payload)
        return _extract_output_text(response_payload)

    def _post_with_retry(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                with httpx.Client(timeout=self._timeout_seconds) as client:
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
            time.sleep(0.6 * attempt)
        raise TelegramAIModelError(f"OpenAI request failed: {last_error}") from last_error


def _decision_instructions(*, role: str, tool_catalog: list[dict[str, Any]]) -> str:
    return (
        "You are AutoStop CRM Telegram AI Board Manager.\n"
        "Language: Russian.\n"
        "Mode: owner/full_control when role is owner. No confirmation is required for owner.\n"
        "You manage only CRM operational data through explicit tools. Never request shell, git, secrets, or raw storage access.\n"
        "If a target is ambiguous and a wrong write could damage CRM data, ask a short clarifying question and return no actions.\n"
        "For normal owner commands, act directly.\n"
        "When image_facts.telegram_media is present and the user asks to save or attach the photo to a card, use attach_telegram_photo_to_card with the matching media_index.\n"
        "When the user asks to inspect an image already stored in a card, use list_card_attachments/get_card_attachment/read_card_attachment or analyze_card_image_attachment.\n"
        "Use board/report tools for summaries, card tools for operational card work, sticky/column tools for board layout, cashbox tools for cash operations, and repair-order tools only for repair-order data.\n"
        "Use crm_context.conversation_memory to resolve references such as 'this card', 'there', 'the previous one', and follow-up commands. Prefer ids from recent verified tool results when the user refers to previous work.\n"
        "Return only one JSON object with this shape:\n"
        "{"
        '"intent":"board_report|card_read|create_card|update_card|move_card|archive_card|restore_card|attachment_work|sticky_work|column_work|cashbox_work|repair_order_update|multi_action|no_action",'
        '"confidence":"high|medium|low",'
        '"actions":[{"tool":"tool_name","arguments":{},"reason":"short reason"}],'
        '"telegram_response":"short Russian response to send after execution or question when no action",'
        '"requires_human_confirmation":false'
        "}\n"
        "Allowed tools with schemas:\n"
        f"{json.dumps(tool_catalog, ensure_ascii=False, sort_keys=True)}\n"
        f"Current role: {role}."
    )


def _compact_for_final_response(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in tool_results[:12]:
        if not isinstance(item, dict):
            continue
        compact.append(_compact_value(item, max_depth=6))
    return compact


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
