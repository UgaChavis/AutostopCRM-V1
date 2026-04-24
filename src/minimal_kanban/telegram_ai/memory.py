from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import utc_now_iso
from .audit import redact_secrets
from .models import RunContext


class TelegramAIConversationMemory:
    def __init__(self, memory_file: Path, *, limit: int = 12) -> None:
        self._memory_file = memory_file
        self._limit = max(0, int(limit))
        self._memory_file.parent.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._limit > 0

    def recent(
        self, *, chat_id: int, user_id: int | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        if not self.enabled or not self._memory_file.exists():
            return []
        max_rows = self._limit if limit is None else max(0, int(limit))
        if max_rows <= 0:
            return []
        try:
            lines = self._memory_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        for line in reversed(lines):
            if len(rows) >= max_rows:
                break
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if int(payload.get("telegram_chat_id") or 0) != int(chat_id):
                continue
            if user_id is not None and int(payload.get("telegram_user_id") or 0) != int(user_id):
                continue
            rows.append(payload)
        return list(reversed(rows))

    def append_run(self, context: RunContext) -> None:
        if not self.enabled:
            return
        payload = redact_secrets(
            {
                "created_at": utc_now_iso(),
                "run_id": context.run_id,
                "telegram_chat_id": context.normalized_input.chat_id,
                "telegram_user_id": context.normalized_input.user_id,
                "input_type": context.normalized_input.input_type,
                "command": _command_text(context),
                "telegram_response": context.telegram_response,
                "final_status": context.final_status,
                "tool_calls": _compact_tool_calls(context.tool_calls),
                "tool_results": _compact_tool_results(context.tool_results),
                "model_summary": _compact_model_decision(context.model_decision),
            }
        )
        try:
            with self._memory_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            return


def _command_text(context: RunContext) -> str:
    return (
        context.transcribed_text
        or context.normalized_input.command_text
        or context.normalized_input.caption
        or ""
    )[:1200]


def _compact_model_decision(decision: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(decision, dict):
        return {}
    return {
        "intent": decision.get("intent"),
        "confidence": decision.get("confidence"),
        "telegram_response": str(decision.get("telegram_response") or "")[:700],
    }


def _compact_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in tool_calls[:8]:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "tool": item.get("tool"),
                "arguments": _compact_value(item.get("arguments")),
                "reason": str(item.get("reason") or "")[:300],
            }
        )
    return rows


def _compact_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in tool_results[:8]:
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        rows.append(
            {
                "tool": item.get("tool"),
                "verify": item.get("verify") if isinstance(item.get("verify"), dict) else {},
                "ids": _extract_ids(data),
                "summary": _result_summary(data),
            }
        )
    return rows


def _extract_ids(data: dict[str, Any]) -> dict[str, str]:
    ids: dict[str, str] = {}
    for key in ("card", "attachment", "cashbox", "transaction", "sticky", "column", "repair_order"):
        value = data.get(key)
        if isinstance(value, dict) and value.get("id"):
            ids[f"{key}_id"] = str(value.get("id"))
    if data.get("card_id"):
        ids["card_id"] = str(data.get("card_id"))
    return ids


def _result_summary(data: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in ("card", "attachment", "cashbox", "sticky", "column"):
        value = data.get(key)
        if isinstance(value, dict):
            summary[key] = {
                field: value.get(field)
                for field in (
                    "id",
                    "title",
                    "vehicle",
                    "column",
                    "file_name",
                    "name",
                    "label",
                    "text",
                )
                if value.get(field)
            }
    if isinstance(data.get("summary"), dict):
        summary["summary"] = data.get("summary")
    return summary


def _compact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _compact_value(item) for key, item in list(value.items())[:20]}
    if isinstance(value, list):
        return [_compact_value(item) for item in value[:20]]
    if isinstance(value, str):
        return value[:600]
    return value
