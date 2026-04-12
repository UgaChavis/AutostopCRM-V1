from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from ..mcp.client import BoardApiClient, BoardApiTransportError, discover_board_api
from ..models import utc_now_iso
from .config import (
    get_agent_board_api_url,
    get_agent_enabled,
    get_agent_max_steps,
    get_agent_max_tool_result_chars,
    get_agent_name,
    get_agent_openai_model,
    get_agent_poll_interval_seconds,
)
from .instructions import build_default_system_prompt
from .openai_client import AgentModelError, OpenAIJsonAgentClient
from .storage import AgentStorage
from .tools import AgentToolExecutor


DEFAULT_SYSTEM_PROMPT = build_default_system_prompt()


class AgentRunner:
    def __init__(
        self,
        *,
        storage: AgentStorage,
        board_api: BoardApiClient,
        model_client: OpenAIJsonAgentClient,
        logger: logging.Logger,
        actor_name: str | None = None,
        max_steps: int | None = None,
        max_tool_result_chars: int | None = None,
    ) -> None:
        self._storage = storage
        self._board_api = board_api
        self._model_client = model_client
        self._logger = logger
        self._actor_name = actor_name or get_agent_name()
        self._max_steps = max_steps or get_agent_max_steps()
        self._max_tool_result_chars = max_tool_result_chars or get_agent_max_tool_result_chars()
        self._tools = AgentToolExecutor(board_api, actor_name=self._actor_name)

    def run_once(self) -> bool:
        task = self._storage.claim_next_task()
        if task is None:
            self._storage.heartbeat(task_id=None, run_id=None)
            return False
        run_id = f"agrun_{uuid.uuid4().hex[:12]}"
        self._storage.update_status(
            running=True,
            current_task_id=task["id"],
            current_run_id=run_id,
            last_heartbeat=utc_now_iso(),
            last_run_started_at=utc_now_iso(),
            last_error="",
        )
        tool_calls = 0
        started_at = utc_now_iso()
        try:
            summary, result, display, tool_calls = self._execute_task(task, run_id=run_id)
            completed = self._storage.complete_task(
                task_id=task["id"],
                run_id=run_id,
                summary=summary,
                result=result,
                display=display,
                tool_calls=tool_calls,
            )
            self._storage.append_run(
                {
                    "id": run_id,
                    "task_id": task["id"],
                    "status": "completed",
                    "started_at": started_at,
                    "finished_at": completed["finished_at"],
                    "source": task["source"],
                    "mode": task["mode"],
                    "task_text": task["task_text"],
                    "summary": summary,
                    "result": result,
                    "display": display,
                    "tool_calls": tool_calls,
                    "model": self._model_client.model,
                    "metadata": task.get("metadata", {}),
                }
            )
            self._storage.update_status(
                running=False,
                current_task_id=None,
                current_run_id=None,
                last_heartbeat=utc_now_iso(),
                last_run_finished_at=completed["finished_at"],
                last_error="",
            )
            self._logger.info("agent_task_completed task_id=%s run_id=%s tool_calls=%s", task["id"], run_id, tool_calls)
            return True
        except Exception as exc:
            self._record_log_action(
                task_id=task["id"],
                run_id=run_id,
                step=tool_calls + 1,
                level="WARN",
                phase="failed",
                message=self._task_failed_message(task, exc),
            )
            failed = self._storage.fail_task(
                task_id=task["id"],
                run_id=run_id,
                error=str(exc),
                tool_calls=tool_calls,
            )
            self._storage.append_run(
                {
                    "id": run_id,
                    "task_id": task["id"],
                    "status": "failed",
                    "started_at": started_at,
                    "finished_at": failed["finished_at"],
                    "source": task["source"],
                    "mode": task["mode"],
                    "task_text": task["task_text"],
                    "summary": "",
                    "result": "",
                    "error": str(exc),
                    "tool_calls": tool_calls,
                    "model": self._model_client.model,
                    "metadata": task.get("metadata", {}),
                }
            )
            self._storage.update_status(
                running=False,
                current_task_id=None,
                current_run_id=None,
                last_heartbeat=utc_now_iso(),
                last_run_finished_at=failed["finished_at"],
                last_error=str(exc),
            )
            self._logger.exception("agent_task_failed task_id=%s run_id=%s error=%s", task["id"], run_id, exc)
            return True

    def _execute_task(self, task: dict[str, Any], *, run_id: str) -> tuple[str, str, dict[str, Any], int]:
        prompt_override = self._storage.read_prompt_text().strip()
        memory_text = self._storage.read_memory_text().strip()
        system_prompt = DEFAULT_SYSTEM_PROMPT
        if prompt_override and prompt_override != DEFAULT_SYSTEM_PROMPT:
            system_prompt = f"{system_prompt}\n\nLocal instructions:\n{prompt_override}"
        if memory_text:
            system_prompt = f"{system_prompt}\n\nPersistent memory:\n{memory_text}"
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        task_type = self._classify_task(task, metadata)
        context_kind = self._context_kind(metadata)
        system_prompt = (
            f"{system_prompt}\n\nAvailable tools:\n"
            f"{self._tools.describe_for_prompt(task_type=task_type, context_kind=context_kind)}"
        )
        cleanup_task = task_type == "card_cleanup"
        cleanup_card_id = self._cleanup_card_id(metadata)
        autofill_task = str(metadata.get("purpose", "") or "").strip().lower() == "card_autofill"
        cleanup_update_applied = False
        cleanup_apply_prompt_sent = False
        applied_updates: list[str] = []
        messages: list[dict[str, str]] = [
            {
                "role": "user",
                "content": self._build_user_task_message(task, metadata, task_type=task_type),
            }
        ]
        tool_calls = 0
        self._record_log_action(
            task_id=task["id"],
            run_id=run_id,
            step=0,
            level="RUN",
            phase="start",
            message=self._task_started_message(metadata),
        )
        self._record_log_action(
            task_id=task["id"],
            run_id=run_id,
            step=0,
            level="INFO",
            phase="analysis",
            message=self._task_analysis_message(metadata),
        )
        for step in range(1, self._max_steps + 1):
            self._storage.heartbeat(task_id=task["id"], run_id=run_id)
            decision = self._model_client.next_step(system_prompt=system_prompt, messages=messages)
            decision_type = str(decision.get("type", "") or "").strip().lower()
            if decision_type == "final":
                apply_args = self._extract_card_update_apply(decision, cleanup_card_id=cleanup_card_id)
                if apply_args is not None:
                    if autofill_task:
                        apply_args = self._normalize_card_autofill_update(apply_args)
                    tool_calls += 1
                    apply_result = self._tools.execute("update_card", apply_args)
                    cleanup_update_applied = True
                    applied_updates.extend(self._summarize_applied_update(apply_args, apply_result))
                    self._record_action(
                        task_id=task["id"],
                        run_id=run_id,
                        step=step,
                        tool_name="update_card",
                        args=apply_args,
                        reason="Runner applied structured card update from final response",
                        result_payload=apply_result,
                    )
                if cleanup_task and cleanup_card_id and not cleanup_update_applied and not cleanup_apply_prompt_sent:
                    messages.append(
                        {
                            "role": "user",
                            "content": self._card_cleanup_apply_instruction(cleanup_card_id),
                        }
                    )
                    cleanup_apply_prompt_sent = True
                    continue
                summary = str(decision.get("summary", "") or "").strip() or "Task completed."
                result = str(decision.get("result", "") or "").strip() or summary
                display = self._normalize_display_payload(decision, summary=summary, result=result)
                display = self._append_applied_updates(display, applied_updates)
                self._record_log_action(
                    task_id=task["id"],
                    run_id=run_id,
                    step=step,
                    level="DONE",
                    phase="completed",
                    message=self._task_completed_message(metadata, summary=summary, applied_updates=applied_updates),
                )
                return summary, result, display, tool_calls
            if decision_type != "tool":
                raise AgentModelError("Agent model returned neither a tool call nor a final answer.")
            tool_name = str(decision.get("tool", "") or "").strip()
            args = decision.get("args")
            if not isinstance(args, dict):
                args = {}
            reason = str(decision.get("reason", "") or "").strip()
            tool_calls += 1
            if autofill_task and tool_name == "update_card":
                args = self._normalize_card_autofill_update(args)
            result_payload = self._tools.execute(tool_name, args)
            if cleanup_task and tool_name == "update_card" and str(args.get("card_id", "") or "").strip() == cleanup_card_id:
                cleanup_update_applied = True
                applied_updates.extend(self._summarize_applied_update(args, result_payload))
            self._record_action(
                task_id=task["id"],
                run_id=run_id,
                step=step,
                tool_name=tool_name,
                args=args,
                reason=reason,
                result_payload=result_payload,
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {"type": "tool", "tool": tool_name, "args": args, "reason": reason},
                        ensure_ascii=False,
                    ),
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": f"TOOL RESULT {tool_name}:\n{self._tool_result_for_model(tool_name, result_payload)}",
                }
            )
        raise AgentModelError(f"Agent exceeded max steps ({self._max_steps}) without returning a final answer.")

    def _normalize_display_payload(
        self,
        decision: dict[str, Any],
        *,
        summary: str,
        result: str,
    ) -> dict[str, Any]:
        raw_display = decision.get("display")
        payload = raw_display if isinstance(raw_display, dict) else {}

        def _clean_text(value: Any, *, limit: int = 400) -> str:
            text = str(value or "").strip()
            if not text:
                return ""
            return text[:limit].strip()

        def _clean_items(value: Any) -> list[str]:
            if not isinstance(value, list):
                return []
            items: list[str] = []
            for entry in value:
                text = _clean_text(entry, limit=220)
                if text:
                    items.append(text)
                if len(items) >= 8:
                    break
            return items

        sections: list[dict[str, Any]] = []
        if isinstance(payload.get("sections"), list):
            for entry in payload["sections"]:
                if not isinstance(entry, dict):
                    continue
                section = {
                    "title": _clean_text(entry.get("title"), limit=72),
                    "body": _clean_text(entry.get("body"), limit=500),
                    "items": _clean_items(entry.get("items")),
                }
                if section["title"] or section["body"] or section["items"]:
                    sections.append(section)
                if len(sections) >= 6:
                    break

        emoji = _clean_text(payload.get("emoji"), limit=6)
        title = _clean_text(payload.get("title"), limit=96) or _clean_text(summary, limit=96)
        lead = _clean_text(payload.get("summary"), limit=320)
        tone = _clean_text(payload.get("tone"), limit=16).lower()
        if tone not in {"info", "success", "warning", "error"}:
            tone = "success"
        actions = _clean_items(payload.get("actions"))[:4]
        normalized = {
            "emoji": emoji,
            "title": title,
            "summary": lead,
            "tone": tone,
            "sections": sections,
            "actions": actions,
        }
        if normalized["title"] or normalized["summary"] or normalized["sections"] or normalized["actions"]:
            return normalized
        return {
            "emoji": "",
            "title": _clean_text(summary, limit=96),
            "summary": _clean_text(result, limit=500),
            "tone": "success",
            "sections": [],
            "actions": [],
        }

    def _preview_payload(self, payload: dict[str, Any]) -> str:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
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

    def _task_completed_message(self, metadata: dict[str, Any], *, summary: str, applied_updates: list[str]) -> str:
        purpose = str(metadata.get("purpose", "") or "").strip().lower()
        if purpose == "card_autofill":
            return "Карточка обновлена." if applied_updates else "Изменений не обнаружено."
        text = str(summary or "").strip()
        return text or "Задача завершена."

    def _task_failed_message(self, task: dict[str, Any], error: Exception) -> str:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        purpose = str(metadata.get("purpose", "") or "").strip().lower()
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
            priorities = data.get("priority_cards") if isinstance(data.get("priority_cards"), list) else []
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
            vehicle_profile = card.get("vehicle_profile") if isinstance(card.get("vehicle_profile"), dict) else {}
            repair_order = card.get("repair_order") if isinstance(card.get("repair_order"), dict) else {}
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
                    "vehicle_profile": vehicle_profile,
                    "repair_order": {
                        "number": repair_order.get("number"),
                        "status": repair_order.get("status"),
                        "works_total": len(repair_order.get("works") or []),
                        "materials_total": len(repair_order.get("materials") or []),
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
        if tool_name in {"find_part_numbers", "search_part_numbers", "estimate_price_ru", "lookup_part_prices", "decode_dtc", "search_fault_info"}:
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
                    "changed_fields": data.get("meta", {}).get("changed_fields") if isinstance(data.get("meta"), dict) else data.get("changed"),
                    "card": data.get("card") if isinstance(data.get("card"), dict) else {},
                }
            )
        return self._preview_payload(compact)

    def _build_user_task_message(self, task: dict[str, Any], metadata: dict[str, Any], *, task_type: str) -> str:
        lines = [
            f"Task id: {task['id']}",
            f"Mode: {task.get('mode', 'manual')}",
            f"Source: {task.get('source', 'manual')}",
            f"Task type: {task_type}",
        ]
        requested_by = str(metadata.get("requested_by", "") or "").strip()
        if requested_by:
            lines.append(f"Requested by: {requested_by}")
        scheduled_name = str(metadata.get("scheduled_task_name", "") or "").strip()
        if scheduled_name:
            lines.append(f"Scheduled task: {scheduled_name}")
        context = metadata.get("context") if isinstance(metadata.get("context"), dict) else {}
        if context:
            lines.append("Context metadata:")
            lines.append(json.dumps(context, ensure_ascii=False, indent=2))
            if str(context.get("kind", "")).strip().lower() == "card":
                lines.append("This task was opened from a card. Work with this card first and inside this card first.")
        scope_prompt = self._build_scope_prompt_block(metadata)
        if scope_prompt:
            lines.append(scope_prompt)
        lines.append("Task:")
        lines.append(str(task.get("task_text", "") or "").strip())
        return "\n".join(lines)

    def _build_scope_prompt_block(self, metadata: dict[str, Any]) -> str:
        scope = metadata.get("scope") if isinstance(metadata.get("scope"), dict) else {}
        scope_type = str(scope.get("type", "") or "").strip().lower()
        if scope_type not in {"all_cards", "column", "current_card"}:
            return ""
        scope_payload: dict[str, Any] = {
            "type": scope_type,
            "column": str(scope.get("column", "") or "").strip(),
            "column_label": str(scope.get("column_label", "") or "").strip(),
            "card_id": str(scope.get("card_id", "") or "").strip(),
            "card_label": str(scope.get("card_label", "") or "").strip(),
            "cards": [],
        }
        try:
            if scope_type == "current_card" and scope_payload["card_id"]:
                context_result = self._board_api.get_card_context(
                    scope_payload["card_id"],
                    event_limit=20,
                    include_repair_order_text=True,
                )
                context_data = self._response_data(context_result)
                scope_payload["card"] = context_data.get("card") if isinstance(context_data.get("card"), dict) else {}
                scope_payload["events"] = (context_data.get("events") if isinstance(context_data.get("events"), list) else [])[:12]
                return "Execution scope:\n" + json.dumps(scope_payload, ensure_ascii=False, indent=2)
            if scope_type == "column" and scope_payload["column"]:
                result = self._board_api.search_cards(
                    query=None,
                    include_archived=False,
                    column=scope_payload["column"],
                    tag=None,
                    indicator=None,
                    status=None,
                    limit=40,
                )
                search_data = self._response_data(result)
                cards = search_data.get("cards") if isinstance(search_data.get("cards"), list) else []
            else:
                snapshot = self._board_api.get_board_snapshot(archive_limit=0)
                snapshot_data = self._response_data(snapshot)
                columns = snapshot_data.get("columns") if isinstance(snapshot_data.get("columns"), list) else []
                cards = []
                for column in columns if isinstance(columns, list) else []:
                    items = column.get("cards") if isinstance(column, dict) else []
                    if isinstance(items, list):
                        cards.extend(items)
            scope_payload["cards"] = [
                {
                    "id": item.get("id"),
                    "vehicle": item.get("vehicle"),
                    "title": item.get("title"),
                    "column": item.get("column"),
                    "tags": item.get("tags"),
                }
                for item in (cards if isinstance(cards, list) else [])[:20]
                if isinstance(item, dict)
            ]
        except Exception as exc:
            scope_payload["error"] = str(exc)
        return "Execution scope:\n" + json.dumps(scope_payload, ensure_ascii=False, indent=2)

    def _cleanup_card_id(self, metadata: dict[str, Any]) -> str:
        context = metadata.get("context") if isinstance(metadata.get("context"), dict) else {}
        if str(context.get("kind", "")).strip().lower() != "card":
            return ""
        return str(context.get("card_id", "") or "").strip()

    def _context_kind(self, metadata: dict[str, Any]) -> str:
        context = metadata.get("context") if isinstance(metadata.get("context"), dict) else {}
        return str(context.get("kind", "") or "board").strip().lower() or "board"

    def _normalize_card_autofill_update(self, args: dict[str, Any]) -> dict[str, Any]:
        card_id = str(args.get("card_id", "") or "").strip()
        if not card_id or "description" not in args:
            return args
        try:
            current_payload = self._board_api.get_card(card_id)
        except Exception:
            return args
        current_data = self._response_data(current_payload)
        current_card = current_data.get("card") if isinstance(current_data.get("card"), dict) else current_data
        current_description = str(current_card.get("description", "") if isinstance(current_card, dict) else "").strip()
        proposed_description = str(args.get("description", "") or "").strip()
        merged_description = self._merge_card_autofill_description(current_description, proposed_description)
        if merged_description == proposed_description:
            return args
        normalized_args = dict(args)
        normalized_args["description"] = merged_description
        return normalized_args

    def _merge_card_autofill_description(self, current_text: str, proposed_text: str) -> str:
        current = str(current_text or "").strip()
        proposed = str(proposed_text or "").strip()
        if not proposed:
            return current
        if not current:
            return proposed
        current_normalized = " ".join(current.split())
        proposed_normalized = " ".join(proposed.split())
        if proposed_normalized == current_normalized or proposed_normalized in current_normalized:
            return current
        if current_normalized and current_normalized in proposed:
            return proposed
        if "ИИ:" in proposed or "AI:" in proposed:
            return f"{current}\n\n{proposed}"
        return f"{current}\n\nИИ:\n{proposed}"

    def _classify_task(self, task: dict[str, Any], metadata: dict[str, Any]) -> str:
        if str(metadata.get("purpose", "") or "").strip().lower() == "card_autofill":
            return "card_cleanup"
        text = self._normalized_task_text(str(task.get("task_text", "") or ""))
        if self._is_card_cleanup_task(task, metadata):
            return "card_cleanup"
        if "vin" in text or "расшифру" in text:
            return "vin_decode"
        if "запчаст" in text or "каталож" in text or "part number" in text or "oem" in text:
            return "parts_lookup"
        if "техобслуж" in text or "maintenance" in text or "service" in text or "процени то" in text or "то на" in text:
            return "maintenance_estimate"
        if "касс" in text or "оплат" in text or "cash" in text or "payment" in text:
            return "cash_review"
        if "обзор" in text or "просроч" in text or "review board" in text:
            return "board_review"
        return "general"

    def _is_card_cleanup_task(self, task: dict[str, Any], metadata: dict[str, Any]) -> bool:
        if not self._cleanup_card_id(metadata):
            return False
        text = self._normalized_task_text(str(task.get("task_text", "") or ""))
        cleanup_markers = (
            "наведи порядок",
            "порядок в карточке",
            "структурир",
            "заполни карточ",
            "cleanup",
            "clean up",
            "tidy up",
            "structure the card",
        )
        for marker in cleanup_markers:
            if marker in text:
                return True
        return ("карточ" in text or "card" in text) and ("структур" in text or "заполни" in text or "поряд" in text)

    def _normalized_task_text(self, value: str) -> str:
        text = " ".join(str(value or "").strip().lower().split())
        if not text:
            return ""
        repaired = self._repair_mojibake_text(text)
        return repaired if self._task_text_score(repaired) > self._task_text_score(text) else text

    def _repair_mojibake_text(self, text: str) -> str:
        candidates = [text]
        for encoding in ("latin1", "cp1251", "cp866"):
            try:
                repaired = text.encode(encoding).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            candidates.append(" ".join(repaired.lower().split()))
        best = text
        best_score = self._task_text_score(text)
        for candidate in candidates[1:]:
            score = self._task_text_score(candidate)
            if score > best_score:
                best = candidate
                best_score = score
        return best

    def _task_text_score(self, text: str) -> int:
        normalized = str(text or "").lower()
        keywords = (
            "наведи",
            "поряд",
            "карточ",
            "структур",
            "заполни",
            "vin",
            "расшифр",
            "запчаст",
            "каталож",
            "касс",
            "оплат",
            "обзор",
            "просроч",
            "техобслуж",
            "maintenance",
            "service",
        )
        score = sum(8 for keyword in keywords if keyword in normalized)
        score += sum(1 for char in normalized if ("а" <= char <= "я") or char == "ё")
        score -= normalized.count("?") * 4
        score -= normalized.count("�") * 6
        return score

    def _extract_card_update_apply(self, decision: dict[str, Any], *, cleanup_card_id: str) -> dict[str, Any] | None:
        payload = decision.get("apply")
        if not isinstance(payload, dict):
            return None
        if str(payload.get("type", "") or "").strip().lower() != "update_card":
            return None
        card_id = str(payload.get("card_id", "") or "").strip() or cleanup_card_id
        if not card_id:
            return None
        update_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        normalized_payload: dict[str, Any] = {"card_id": card_id}
        for field_name in ("vehicle", "title", "description", "deadline", "tags", "vehicle_profile", "repair_order"):
            if field_name in update_payload:
                normalized_payload[field_name] = update_payload[field_name]
        return normalized_payload if len(normalized_payload) > 1 else None

    def _summarize_applied_update(self, args: dict[str, Any], result_payload: dict[str, Any]) -> list[str]:
        response_data = self._response_data(result_payload)
        changed_payload = response_data.get("changed")
        if not isinstance(changed_payload, list):
            meta = response_data.get("meta") if isinstance(response_data.get("meta"), dict) else {}
            changed_payload = meta.get("changed_fields")
        changed_fields = (
            [str(item or "").strip() for item in changed_payload if str(item or "").strip()]
            if isinstance(changed_payload, list)
            else []
        )
        if not changed_fields:
            changed_fields = [
                field_name
                for field_name in ("vehicle", "title", "description", "deadline", "tags", "vehicle_profile", "repair_order")
                if field_name in args
            ]
        labels = {
            "vehicle": "автомобиль",
            "title": "краткая суть",
            "description": "описание",
            "deadline": "сигнал",
            "tags": "метки",
            "vehicle_profile": "паспорт автомобиля",
            "repair_order": "заказ-наряд",
        }
        return [labels.get(item, item) for item in changed_fields]

    def _append_applied_updates(self, display: dict[str, Any], applied_updates: list[str]) -> dict[str, Any]:
        unique_updates: list[str] = []
        seen: set[str] = set()
        for item in applied_updates:
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            unique_updates.append(value)
        if not unique_updates:
            return display
        payload = dict(display)
        sections = list(payload.get("sections") or [])
        sections.insert(
            0,
            {
                "title": "Применено",
                "body": "",
                "items": [f"Обновлено поле: {item}" for item in unique_updates],
            },
        )
        payload["sections"] = sections[:6]
        return payload

    def _card_cleanup_apply_instruction(self, card_id: str) -> str:
        return (
            "This is a card cleanup task opened from a card.\n"
            f"Apply confident changes to card {card_id} with update_card before the final answer.\n"
            "Preserve the existing card text and only add or reorganize useful information.\n"
            "External facts found during this task may be added only when they are clearly grounded by the tool results.\n"
            "AI-added notes or follow-up questions inside the description must be labeled with 'ИИ:' or 'AI:'.\n"
            "If nothing can be safely changed, return a final answer that explicitly says no card fields were changed and why."
        )


def build_board_api_client(*, logger: logging.Logger) -> BoardApiClient:
    board_api_url = get_agent_board_api_url() or discover_board_api(timeout_seconds=1.0)
    if not board_api_url:
        raise RuntimeError("Unable to discover a reachable local board API for the server agent.")
    try:
        client = BoardApiClient(board_api_url, logger=logger, default_source="agent")
        health = client.health()
    except BoardApiTransportError as exc:
        raise RuntimeError(f"Board API is not reachable for the server agent: {exc}") from exc
    if not health.get("ok"):
        raise RuntimeError("Board API health check failed for the server agent.")
    return client


def run_agent_loop(*, logger: logging.Logger) -> int:
    if not get_agent_enabled():
        logger.info("agent_runtime_disabled")
        return 0
    storage = AgentStorage()
    idle_sleep = get_agent_poll_interval_seconds()
    if not storage.read_prompt_text().strip():
        storage.write_prompt_text(DEFAULT_SYSTEM_PROMPT)
    if not storage.read_memory_text().strip():
        storage.write_memory_text(
            "CRM URL: https://crm.autostopcrm.ru\n"
            "MCP URL: https://crm.autostopcrm.ru/mcp\n"
            "Default admin: admin/admin\n"
            "Use cashbox names exactly as they exist.\n"
            "If payment goes to cashbox 'Безналичный', the repair order adds 15% taxes and fees from that payment amount.\n"
            "Cashboxes 'Наличный' and 'Карта Мария' do not add taxes and fees.\n"
        )
    board_api = None
    while board_api is None:
        try:
            board_api = build_board_api_client(logger=logger)
        except Exception as exc:
            storage.update_status(
                running=False,
                current_task_id=None,
                current_run_id=None,
                last_heartbeat=utc_now_iso(),
                last_error=str(exc),
            )
            logger.warning("agent_waiting_for_board_api error=%s", exc)
            time.sleep(idle_sleep)
    model_client = OpenAIJsonAgentClient()
    runner = AgentRunner(storage=storage, board_api=board_api, model_client=model_client, logger=logger)
    logger.info("agent_runtime_started model=%s board_api_url=%s", get_agent_openai_model(), board_api.base_url)
    while True:
        try:
            processed = runner.run_once()
        except KeyboardInterrupt:
            break
        except Exception as exc:
            storage.update_status(
                running=False,
                current_task_id=None,
                current_run_id=None,
                last_heartbeat=utc_now_iso(),
                last_error=str(exc),
            )
            logger.exception("agent_runtime_loop_failed error=%s", exc)
            time.sleep(idle_sleep)
            continue
        time.sleep(idle_sleep if not processed else 0.2)
    return 0
