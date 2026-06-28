from __future__ import annotations

import json
import math
import time
from typing import Any

import httpx

from .config import (
    get_agent_openai_api_key,
    get_agent_openai_base_url,
    get_agent_openai_model,
    get_agent_request_timeout_seconds,
)

AGENT_MODEL_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
AGENT_MODEL_TIMEOUT_MAX_SECONDS = 120.0


class AgentModelError(RuntimeError):
    pass


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _null_json_constant(value: str) -> None:
    _ = value
    return None


def _json_safe_value(value: Any, *, depth: int = 8) -> Any:
    if depth <= 0:
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item, depth=depth - 1)
            for key, item in value.items()
            if key is not None
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    return str(value)


def _json_dumps(payload: Any) -> str:
    return json.dumps(_json_safe_value(payload), ensure_ascii=False, allow_nan=False)


def _normalize_timeout_seconds(value: Any, *, default: float) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(parsed):
        return default
    return min(max(parsed, 1.0), AGENT_MODEL_TIMEOUT_MAX_SECONDS)


class OpenAIJsonAgentClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._api_key = api_key or get_agent_openai_api_key()
        self._model = model or get_agent_openai_model()
        self._base_url = (base_url or get_agent_openai_base_url()).rstrip("/")
        self._timeout_seconds = _normalize_timeout_seconds(
            timeout_seconds,
            default=get_agent_request_timeout_seconds(),
        )
        if not self._api_key:
            raise AgentModelError("OPENAI_API_KEY is not configured for the server agent.")

    @property
    def model(self) -> str:
        return self._model

    def complete_json(
        self, *, instructions: str, messages: list[dict[str, str]], temperature: float = 0.1
    ) -> dict[str, Any]:
        input_messages = []
        for message in messages:
            message_text = str(message.get("content") or "")
            input_messages.append(
                {
                    "role": str(message.get("role") or "user"),
                    "content": f"JSON mode. {message_text}",
                }
            )
        payload = {
            "model": self._model,
            "temperature": temperature,
            "instructions": instructions.strip(),
            "text": {"format": {"type": "json_object"}},
            "input": input_messages,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        response = self._post_with_retry(headers=headers, payload=payload)
        try:
            payload = json.loads(
                self._response_text(response),
                parse_constant=_reject_json_constant,
            )
            if not isinstance(payload, dict):
                raise ValueError("non-object response payload")
            message = self._extract_output_text(payload)
        except (KeyError, IndexError, TypeError, ValueError, RecursionError) as exc:
            raise AgentModelError("Agent model returned an unexpected payload.") from exc
        return self._parse_json_payload(message)

    def next_step(self, *, system_prompt: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        instructions = f"{system_prompt.strip()}\n\nReturn only one JSON object that matches the requested schema."
        return self.complete_json(instructions=instructions, messages=messages, temperature=0.1)

    def _extract_error_message(self, response: httpx.Response) -> str:
        status = response.status_code
        try:
            body = self._response_text(response, label="Agent model error response")
            payload = json.loads(body, parse_constant=_null_json_constant)
        except AgentModelError:
            return f"HTTP {status}: response body is too large"
        except (ValueError, RecursionError):
            body = self._safe_response_text(response).strip()
            return f"HTTP {status}: {body or 'Unknown API error'}"
        if not isinstance(payload, dict):
            return f"HTTP {status}: {_json_dumps(payload)}"
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "").strip()
            message = str(error.get("message") or "").strip()
            if code and message:
                return f"HTTP {status} ({code}): {message}"
            if message:
                return f"HTTP {status}: {message}"
            if code:
                return f"HTTP {status} ({code})"
        return f"HTTP {status}: {_json_dumps(payload)}"

    def _extract_output_text(self, payload: dict[str, Any]) -> str:
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

    def _parse_json_payload(self, content: Any) -> dict[str, Any]:
        if isinstance(content, list):
            text = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
        else:
            text = str(content or "")
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            payload = json.loads(text, parse_constant=_reject_json_constant)
        except (ValueError, RecursionError):
            start = text.find("{")
            if start >= 0:
                try:
                    decoder = json.JSONDecoder(parse_constant=_reject_json_constant)
                    payload, _ = decoder.raw_decode(text[start:])
                except (ValueError, RecursionError):
                    raise AgentModelError("Agent model did not return valid JSON.")
            else:
                raise AgentModelError("Agent model did not return valid JSON.")
        if not isinstance(payload, dict):
            raise AgentModelError("Agent model returned a non-object JSON payload.")
        return payload

    def _response_text(
        self, response: httpx.Response, *, label: str = "Agent model response"
    ) -> str:
        content = response.content
        if len(content) > AGENT_MODEL_RESPONSE_MAX_BYTES:
            raise AgentModelError(f"{label} is too large.")
        return self._decode_response_content(response, content)

    def _safe_response_text(self, response: httpx.Response) -> str:
        content = response.content[:AGENT_MODEL_RESPONSE_MAX_BYTES]
        return self._decode_response_content(response, content)

    def _decode_response_content(self, response: httpx.Response, content: bytes) -> str:
        encoding = response.encoding or "utf-8"
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            return content.decode("utf-8", errors="replace")

    def _read_response_content(self, response: httpx.Response) -> bytes:
        chunks = bytearray()
        for chunk in response.iter_bytes(chunk_size=AGENT_MODEL_RESPONSE_MAX_BYTES + 1):
            chunks.extend(chunk)
            if len(chunks) > AGENT_MODEL_RESPONSE_MAX_BYTES:
                raise AgentModelError("Agent model response is too large.")
        return bytes(chunks)

    def _post_with_retry(
        self, *, headers: dict[str, str], payload: dict[str, Any]
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                with httpx.Client(timeout=self._timeout_seconds) as client:
                    with client.stream(
                        "POST",
                        f"{self._base_url}/responses",
                        headers=headers,
                        json=payload,
                        follow_redirects=False,
                    ) as streamed_response:
                        content = self._read_response_content(streamed_response)
                        response = httpx.Response(
                            streamed_response.status_code,
                            headers=streamed_response.headers,
                            content=content,
                            request=streamed_response.request,
                        )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if not self._should_retry_status(exc.response.status_code) or attempt >= 3:
                    message = self._extract_error_message(exc.response)
                    raise AgentModelError(f"Agent model request failed: {message}") from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= 3:
                    raise AgentModelError(f"Agent model request failed: {exc}") from exc
            time.sleep(0.6 * attempt)
        raise AgentModelError(f"Agent model request failed: {last_error}")

    def _should_retry_status(self, status_code: int) -> bool:
        return status_code in {408, 409, 429, 500, 502, 503, 504}
