from __future__ import annotations

import json
import math
import uuid
from typing import Any

from ..models import utc_now_iso


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


def _clean_display_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:limit].strip()


def _clean_display_items(value: Any, *, item_limit: int = 220, max_items: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for entry in value:
        text = _clean_display_text(entry, limit=item_limit)
        if text:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def _normalize_display_sections(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload.get("sections"), list):
        return []
    sections: list[dict[str, Any]] = []
    for entry in payload["sections"]:
        if not isinstance(entry, dict):
            continue
        section = {
            "title": _clean_display_text(entry.get("title"), limit=72),
            "body": _clean_display_text(entry.get("body"), limit=500),
            "items": _clean_display_items(entry.get("items")),
        }
        if section["title"] or section["body"] or section["items"]:
            sections.append(section)
        if len(sections) >= 6:
            break
    return sections


def _fallback_display_payload(summary: str, result: str) -> dict[str, Any]:
    return {
        "emoji": "",
        "title": _clean_display_text(summary, limit=96),
        "summary": _clean_display_text(result, limit=500),
        "tone": "success",
        "sections": [],
        "actions": [],
    }


class AgentRunnerOutputMixin:
    def _normalize_display_payload(
        self,
        decision: dict[str, Any],
        *,
        summary: str,
        result: str,
    ) -> dict[str, Any]:
        raw_display = decision.get("display")
        payload = raw_display if isinstance(raw_display, dict) else {}
        sections = _normalize_display_sections(payload)
        emoji = _clean_display_text(payload.get("emoji"), limit=6)
        title = _clean_display_text(payload.get("title"), limit=96) or _clean_display_text(
            summary, limit=96
        )
        lead = _clean_display_text(payload.get("summary"), limit=320)
        tone = _clean_display_text(payload.get("tone"), limit=16).lower()
        if tone not in {"info", "success", "warning", "error"}:
            tone = "success"
        actions = _clean_display_items(payload.get("actions"))[:4]
        normalized = {
            "emoji": emoji,
            "title": title,
            "summary": lead,
            "tone": tone,
            "sections": sections,
            "actions": actions,
        }
        if (
            normalized["title"]
            or normalized["summary"]
            or normalized["sections"]
            or normalized["actions"]
        ):
            return normalized
        return _fallback_display_payload(summary, result)

    def _preview_payload(self, payload: dict[str, Any]) -> str:
        text = json.dumps(_json_safe_value(payload), ensure_ascii=False, indent=2, allow_nan=False)
        if len(text) <= self._max_tool_result_chars:
            return text
        return f"{text[: self._max_tool_result_chars]}... [truncated]"

    def _response_data(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload

    def _response_meta(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        meta = payload.get("meta")
        return meta if isinstance(meta, dict) else {}

    def _tool_payload_error_code(self, payload: Any) -> str:
        data = self._response_data(payload)
        meta = self._response_meta(payload)
        return str(meta.get("error_code") or data.get("error_code") or "").strip().lower()

    def _is_partial_tool_payload(self, payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        data = self._response_data(payload)
        meta = self._response_meta(payload)
        return bool(meta.get("partial") or data.get("partial"))

    def _is_budget_exceeded_payload(self, payload: Any) -> bool:
        return self._tool_payload_error_code(payload) == "external_budget_exceeded"

    def _record_action(
        self,
        *,
        task_id: str,
        run_id: str,
        step: int,
        tool_name: str,
        args: dict[str, Any],
        reason: str,
        result_payload: dict[str, Any],
    ) -> None:
        started_at = utc_now_iso()
        finished_at = utc_now_iso()
        self._storage.append_action(
            {
                "id": f"agact_{uuid.uuid4().hex[:12]}",
                "task_id": task_id,
                "run_id": run_id,
                "step": step,
                "kind": "tool",
                "tool": tool_name,
                "args": args,
                "reason": reason,
                "started_at": started_at,
                "finished_at": finished_at,
                "result_preview": self._preview_payload(result_payload),
            }
        )

    def _record_log_action(
        self,
        *,
        task_id: str,
        run_id: str,
        step: int,
        level: str,
        phase: str,
        message: str,
    ) -> None:
        text = str(message or "").strip()
        if not text:
            return
        timestamp = utc_now_iso()
        self._storage.append_action(
            {
                "id": f"aglog_{uuid.uuid4().hex[:12]}",
                "task_id": task_id,
                "run_id": run_id,
                "step": step,
                "kind": "log",
                "level": str(level or "INFO").strip().upper(),
                "phase": str(phase or "").strip().lower(),
                "message": text,
                "started_at": timestamp,
                "finished_at": timestamp,
                "result_preview": text,
            }
        )

    def _task_started_message(self, metadata: dict[str, Any]) -> str:
        purpose = str(metadata.get("purpose", "") or "").strip().lower()
        if purpose == "full_card_enrichment":
            trigger = str(metadata.get("trigger", "") or "").strip().lower()
            if trigger == "adaptive_followup":
                return "Повторный проход полного заполнения карточки запущен."
            return "Полное заполнение карточки запущено."
        if purpose == "card_autofill":
            trigger = str(metadata.get("trigger", "") or "").strip().lower()
            if trigger == "adaptive_followup":
                return "Повторный проход автосопровождения запущен."
            return "Первый проход автосопровождения запущен."
        return "Задача агента запущена."

    def _task_analysis_message(self, metadata: dict[str, Any]) -> str:
        context = metadata.get("context") if isinstance(metadata.get("context"), dict) else {}
        if str(context.get("kind", "") or "").strip().lower() == "card":
            return "Начат анализ карточки."
        return "Начат анализ доски."

    def _task_completed_message(
        self, metadata: dict[str, Any], *, summary: str, applied_updates: list[str]
    ) -> str:
        purpose = str(metadata.get("purpose", "") or "").strip().lower()
        if purpose == "full_card_enrichment":
            return (
                "Карточка полностью заполнена." if applied_updates else "Изменений не обнаружено."
            )
        if purpose == "card_autofill":
            return "Карточка обновлена." if applied_updates else "Изменений не обнаружено."
        text = str(summary or "").strip()
        return text or "Задача завершена."

    def _task_failed_message(self, task: dict[str, Any], error: Exception) -> str:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        purpose = str(metadata.get("purpose", "") or "").strip().lower()
        if purpose == "full_card_enrichment":
            return "Ошибка полного заполнения карточки."
        if purpose == "card_autofill":
            return "Ошибка автосопровождения."
        message = str(error or "").strip()
        return message or "Ошибка выполнения задачи."

    def _tool_result_for_model(self, tool_name: str, payload: dict[str, Any]) -> str:
        compact = payload if isinstance(payload, dict) else {}
        data = self._response_data(compact)
        if tool_name == "review_board":
            summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
            alerts = data.get("alerts") if isinstance(data.get("alerts"), list) else []
            priorities = (
                data.get("priority_cards") if isinstance(data.get("priority_cards"), list) else []
            )
            return self._preview_payload(
                {
                    "summary": summary,
                    "alerts": alerts[:5],
                    "priority_cards": priorities[:5],
                    "text": data.get("text", "") or compact.get("text", ""),
                }
            )
        if tool_name == "get_card_context":
            card = data.get("card") if isinstance(data.get("card"), dict) else data
            vehicle_profile = (
                card.get("vehicle_profile") if isinstance(card.get("vehicle_profile"), dict) else {}
            )
            vehicle_profile_compact = (
                card.get("vehicle_profile_compact")
                if isinstance(card.get("vehicle_profile_compact"), dict)
                else vehicle_profile
            )
            repair_order = (
                card.get("repair_order") if isinstance(card.get("repair_order"), dict) else {}
            )
            return self._preview_payload(
                {
                    "card": {
                        "id": card.get("id"),
                        "vehicle": card.get("vehicle"),
                        "title": card.get("title"),
                        "description": card.get("description"),
                        "column": card.get("column"),
                        "tags": card.get("tags"),
                        "ai_autofill_prompt": card.get("ai_autofill_prompt"),
                        "ai_autofill_log": (card.get("ai_autofill_log") or [])[-8:],
                        "vin": vehicle_profile.get("vin") or repair_order.get("vin"),
                    },
                    "known_vehicle_facts": {
                        "vin": vehicle_profile_compact.get("vin") or vehicle_profile.get("vin"),
                        "make": vehicle_profile_compact.get("make_display")
                        or vehicle_profile.get("make_display"),
                        "model": vehicle_profile_compact.get("model_display")
                        or vehicle_profile.get("model_display"),
                        "year": vehicle_profile_compact.get("production_year")
                        or vehicle_profile.get("production_year"),
                        "engine": vehicle_profile_compact.get("engine_model")
                        or vehicle_profile.get("engine_model"),
                        "gearbox": vehicle_profile_compact.get("gearbox_model")
                        or vehicle_profile.get("gearbox_model"),
                        "drivetrain": vehicle_profile_compact.get("drivetrain")
                        or vehicle_profile.get("drivetrain"),
                    },
                    "vehicle_profile": vehicle_profile_compact,
                    "repair_order": {
                        "number": repair_order.get("number"),
                        "status": repair_order.get("status"),
                        "works_total": len(repair_order.get("works") or []),
                        "materials_total": len(repair_order.get("materials") or []),
                        "reason": repair_order.get("reason"),
                        "comment": repair_order.get("comment"),
                        "note": repair_order.get("note"),
                    },
                    "events_total": len(data.get("events") or []),
                }
            )
        if tool_name == "search_cards":
            cards = data.get("cards") if isinstance(data.get("cards"), list) else []
            return self._preview_payload(
                {
                    "count": len(cards),
                    "cards": [
                        {
                            "id": item.get("id"),
                            "vehicle": item.get("vehicle"),
                            "title": item.get("title"),
                            "column": item.get("column"),
                            "indicator": item.get("indicator"),
                        }
                        for item in cards[:8]
                        if isinstance(item, dict)
                    ],
                }
            )
        if tool_name in {
            "find_part_numbers",
            "search_part_numbers",
            "estimate_price_ru",
            "lookup_part_prices",
            "decode_dtc",
            "search_fault_info",
        }:
            results = data.get("results") if isinstance(data.get("results"), list) else []
            normalized_results: list[dict[str, Any]] = []
            for item in results[:6]:
                if not isinstance(item, dict):
                    continue
                normalized_results.append(
                    {
                        "title": item.get("title"),
                        "domain": item.get("domain"),
                        "url": item.get("url"),
                        "snippet": item.get("snippet"),
                        "prices": item.get("prices"),
                    }
                )
            return self._preview_payload(
                {
                    "query": data.get("part_query") or data.get("query"),
                    "vehicle_context": data.get("vehicle_context"),
                    "part_numbers": data.get("part_numbers"),
                    "price_summary": data.get("price_summary"),
                    "results": normalized_results,
                }
            )
        if tool_name == "estimate_maintenance":
            return self._preview_payload(
                {
                    "service_type": data.get("service_type"),
                    "vehicle_context": data.get("vehicle_context"),
                    "works": data.get("works"),
                    "materials": data.get("materials"),
                    "notes": data.get("notes"),
                }
            )
        if tool_name == "update_card":
            return self._preview_payload(
                {
                    "card_id": data.get("card_id") or (data.get("card") or {}).get("id"),
                    "changed": data.get("changed"),
                    "changed_fields": data.get("meta", {}).get("changed_fields")
                    if isinstance(data.get("meta"), dict)
                    else data.get("changed"),
                    "card": data.get("card") if isinstance(data.get("card"), dict) else {},
                }
            )
        return self._preview_payload(compact)

    def _autofill_tool_completion_message(self, tool_name: str, payload: dict[str, Any]) -> str:
        if tool_name == "decode_vin":
            status = self._vin_decode_status(payload)
            if status == "success":
                return "decode_vin success."
            if status == "insufficient":
                return "decode_vin insufficient."
            return "decode_vin failed."
        if tool_name == "find_part_numbers":
            part_numbers = (
                payload.get("part_numbers") if isinstance(payload.get("part_numbers"), list) else []
            )
            return (
                "Найдены кандидаты OEM/каталожных номеров."
                if part_numbers
                else "Точный OEM не найден, нужен более точный контекст."
            )
        if tool_name == "estimate_price_ru":
            return (
                "Получен ориентир по ценам РФ."
                if isinstance(payload.get("price_summary"), dict)
                else "Ценовой ориентир не найден."
            )
        if tool_name == "decode_dtc":
            return (
                "Найдена расшифровка DTC."
                if isinstance(payload.get("results"), list) and payload.get("results")
                else "По DTC найден только общий справочный контекст."
            )
        if tool_name == "estimate_maintenance":
            return "Собран предварительный состав ТО."
        if tool_name == "search_fault_info":
            return (
                "Найден внешний контекст по симптомам."
                if isinstance(payload.get("results"), list) and payload.get("results")
                else "По симптомам полезного внешнего контекста не найдено."
            )
        return ""
