from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..config import (
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    get_api_bearer_token,
    get_app_data_dir,
)

DEFAULT_TELEGRAM_POLL_TIMEOUT_SECONDS = 25
DEFAULT_TELEGRAM_REQUEST_TIMEOUT_SECONDS = 35
DEFAULT_MAX_BATCH_CARDS = 20
DEFAULT_CONVERSATION_MEMORY_LIMIT = 12


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw_value = (os.environ.get(name) or "").strip().lower()
    if not raw_value:
        return default
    return raw_value in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw_value = (os.environ.get(name) or "").strip()
    if not raw_value:
        return default
    try:
        return max(minimum, int(raw_value))
    except ValueError:
        return default


def _env_float(name: str, default: float, *, minimum: float = 0.1) -> float:
    raw_value = (os.environ.get(name) or "").strip()
    if not raw_value:
        return default
    try:
        return max(minimum, float(raw_value))
    except ValueError:
        return default


def _env_text(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _parse_int_set(raw_value: str) -> frozenset[int]:
    values: set[int] = set()
    for chunk in str(raw_value or "").replace("\n", ",").split(","):
        text = chunk.strip()
        if not text:
            continue
        try:
            values.add(int(text))
        except ValueError:
            continue
    return frozenset(values)


def _default_crm_api_base_url() -> str:
    return (
        _env_text("AUTOSTOP_CRM_API_BASE_URL")
        or _env_text("MINIMAL_KANBAN_AGENT_BOARD_API_URL")
        or _env_text("MINIMAL_KANBAN_API_BASE_URL")
        or "http://127.0.0.1:41731"
    ).rstrip("/")


@dataclass(frozen=True)
class TelegramAIConfig:
    enabled: bool
    bot_token: str | None
    owner_ids: frozenset[int]
    openai_api_key: str | None
    openai_base_url: str
    model: str
    vision_model: str
    transcription_model: str
    reasoning_effort: str
    crm_api_base_url: str
    crm_api_bearer_token: str | None
    data_dir: Path
    audit_enabled: bool
    max_batch_cards: int
    telegram_poll_timeout_seconds: int
    telegram_request_timeout_seconds: float
    openai_request_timeout_seconds: float
    autopilot_enabled: bool
    autopilot_interval_minutes: int
    web_search_enabled: bool
    conversation_memory_limit: int

    @property
    def audit_file(self) -> Path:
        return self.data_dir / "audit.jsonl"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def downloads_dir(self) -> Path:
        return self.data_dir / "downloads"

    @property
    def conversation_file(self) -> Path:
        return self.data_dir / "conversation.jsonl"

    @property
    def is_ready(self) -> bool:
        return bool(self.enabled and self.bot_token and self.owner_ids and self.openai_api_key)


def load_config() -> TelegramAIConfig:
    model = _env_text("AUTOSTOP_AI_MODEL") or _env_text("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
    data_dir = get_app_data_dir() / "telegram_ai"
    return TelegramAIConfig(
        enabled=_env_flag("AUTOSTOP_TELEGRAM_AI_ENABLED", default=False),
        bot_token=_env_text("AUTOSTOP_TELEGRAM_BOT_TOKEN") or None,
        owner_ids=_parse_int_set(_env_text("AUTOSTOP_TELEGRAM_OWNER_IDS")),
        openai_api_key=_env_text("OPENAI_API_KEY") or _env_text("AUTOSTOP_OPENAI_API_KEY") or None,
        openai_base_url=(_env_text("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL).rstrip("/"),
        model=model,
        vision_model=_env_text("AUTOSTOP_AI_VISION_MODEL") or model,
        transcription_model=_env_text("AUTOSTOP_AI_TRANSCRIPTION_MODEL")
        or "gpt-4o-mini-transcribe",
        reasoning_effort=_env_text("AUTOSTOP_AI_REASONING_EFFORT") or "medium",
        crm_api_base_url=_default_crm_api_base_url(),
        crm_api_bearer_token=_env_text("AUTOSTOP_CRM_API_BEARER_TOKEN") or get_api_bearer_token(),
        data_dir=data_dir,
        audit_enabled=_env_flag("AUTOSTOP_AI_AUDIT_ENABLED", default=True),
        max_batch_cards=_env_int("AUTOSTOP_AI_MAX_BATCH_CARDS", DEFAULT_MAX_BATCH_CARDS, minimum=1),
        telegram_poll_timeout_seconds=_env_int(
            "AUTOSTOP_TELEGRAM_POLL_TIMEOUT_SECONDS",
            DEFAULT_TELEGRAM_POLL_TIMEOUT_SECONDS,
            minimum=1,
        ),
        telegram_request_timeout_seconds=_env_float(
            "AUTOSTOP_TELEGRAM_REQUEST_TIMEOUT_SECONDS",
            DEFAULT_TELEGRAM_REQUEST_TIMEOUT_SECONDS,
            minimum=1.0,
        ),
        openai_request_timeout_seconds=_env_float(
            "AUTOSTOP_AI_REQUEST_TIMEOUT_SECONDS",
            DEFAULT_REQUEST_TIMEOUT_SECONDS,
            minimum=1.0,
        ),
        autopilot_enabled=_env_flag("AUTOSTOP_AI_AUTOPILOT_ENABLED", default=False),
        autopilot_interval_minutes=_env_int(
            "AUTOSTOP_AI_AUTOPILOT_INTERVAL_MINUTES", 30, minimum=1
        ),
        web_search_enabled=_env_flag("AUTOSTOP_AI_WEB_SEARCH_ENABLED", default=True),
        conversation_memory_limit=_env_int(
            "AUTOSTOP_AI_CONVERSATION_MEMORY_LIMIT",
            DEFAULT_CONVERSATION_MEMORY_LIMIT,
            minimum=0,
        ),
    )


def redact_secret(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}...{text[-4:]}"
