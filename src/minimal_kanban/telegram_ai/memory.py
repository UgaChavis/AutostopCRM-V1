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
        tool_name = str(item.get("tool") or "")
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        rows.append(
            {
                "tool": tool_name,
                "verify": item.get("verify") if isinstance(item.get("verify"), dict) else {},
                "ids": _extract_ids(data),
                "summary": _result_summary(data),
                "references": _extract_references(tool_name, data),
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
    cards = _first_list(data, ("cards", "results", "items", "overdue_cards"))
    if cards:
        summary["cards"] = [_card_summary(card) for card in cards[:5] if isinstance(card, dict)]
    return summary


def _first_list(data: dict[str, Any], keys: tuple[str, ...]) -> list[Any] | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return None


def _extract_references(tool_name: str, data: dict[str, Any]) -> dict[str, Any]:
    references: dict[str, Any] = {}
    card = data.get("card") if isinstance(data.get("card"), dict) else None
    if isinstance(card, dict):
        selected = _card_reference(card, include_match=False)
        if selected:
            references["selected_card"] = selected
    cards = _first_list(data, ("cards", "results", "items", "overdue_cards"))
    if cards:
        card_refs = [
            _card_reference(card, include_match=tool_name == "search_cards")
            for card in cards[:5]
            if isinstance(card, dict)
        ]
        card_refs = [item for item in card_refs if item]
        if card_refs:
            references["card_candidates"] = card_refs
            if len(card_refs) == 1:
                references["selected_card"] = card_refs[0]
    return references


def _card_reference(card: dict[str, Any], *, include_match: bool) -> dict[str, Any]:
    if not card:
        return {}
    reference = _card_summary(card)
    if include_match:
        match = card.get("match") if isinstance(card.get("match"), dict) else {}
        if match:
            compact_match: dict[str, Any] = {}
            for key in ("score", "query", "tag"):
                value = match.get(key)
                if value not in (None, "", [], {}):
                    compact_match[key] = value
            fields = match.get("fields")
            if isinstance(fields, list) and fields:
                compact_match["fields"] = [str(field) for field in fields[:8]]
            if compact_match:
                reference["match"] = compact_match
    return reference


def _card_summary(card: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in ("id", "title", "vehicle", "column", "status"):
        value = card.get(key)
        if value not in (None, "", [], {}):
            summary[key] = value
    if "archived" in card:
        summary["archived"] = bool(card.get("archived"))
    return summary


def latest_card_state(rows: list[dict[str, Any]]) -> dict[str, Any]:
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        tool_results = row.get("tool_results") if isinstance(row.get("tool_results"), list) else []
        for tool_result in reversed(tool_results):
            if not isinstance(tool_result, dict):
                continue
            references = (
                tool_result.get("references")
                if isinstance(tool_result.get("references"), dict)
                else {}
            )
            if not references:
                continue
            selected_card = (
                references.get("selected_card")
                if isinstance(references.get("selected_card"), dict)
                else None
            )
            card_candidates = (
                references.get("card_candidates")
                if isinstance(references.get("card_candidates"), list)
                else []
            )
            if selected_card:
                state: dict[str, Any] = {
                    "last_card": selected_card,
                    "source_run_id": row.get("run_id"),
                    "source_tool": tool_result.get("tool"),
                }
                if card_candidates:
                    state["card_candidates"] = card_candidates[:5]
                return state
            if card_candidates:
                return {
                    "card_candidates": card_candidates[:5],
                    "source_run_id": row.get("run_id"),
                    "source_tool": tool_result.get("tool"),
                }
    return {}


def _compact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _compact_value(item) for key, item in list(value.items())[:20]}
    if isinstance(value, list):
        return [_compact_value(item) for item in value[:20]]
    if isinstance(value, str):
        return value[:600]
    return value
