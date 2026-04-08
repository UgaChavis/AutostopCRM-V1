from __future__ import annotations

import json
from typing import Any

import httpx

from .config import (
    get_agent_openai_api_key,
    get_agent_openai_base_url,
    get_agent_openai_model,
    get_agent_request_timeout_seconds,
)


class AgentModelError(RuntimeError):
    pass


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
        self._timeout_seconds = timeout_seconds or get_agent_request_timeout_seconds()
        if not self._api_key:
            raise AgentModelError("OPENAI_API_KEY is not configured for the server agent.")

    @property
    def model(self) -> str:
        return self._model

    def next_step(self, *, system_prompt: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload = {
            "model": self._model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system_prompt}, *messages],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.post(f"{self._base_url}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AgentModelError(f"Agent model request failed: {exc}") from exc
        try:
            payload = response.json()
            message = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise AgentModelError("Agent model returned an unexpected payload.") from exc
        return self._parse_json_payload(message)

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
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                payload = json.loads(text[start : end + 1])
            else:
                raise AgentModelError("Agent model did not return valid JSON.")
        if not isinstance(payload, dict):
            raise AgentModelError("Agent model returned a non-object JSON payload.")
        return payload
