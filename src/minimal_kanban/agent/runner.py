from __future__ import annotations

import json
import logging
import re
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
_AUTOFILL_VIN_PATTERN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_AUTOFILL_DTC_PATTERN = re.compile(r"\b[PBCU][0-9]{4}\b", re.IGNORECASE)
_AUTOFILL_MILEAGE_PATTERN = re.compile(r"(?:пробег|mileage|одометр)\s*[:\-]?\s*([\d\s]{2,12})", re.IGNORECASE)
_AUTOFILL_MAINTENANCE_PATTERN = re.compile(
    r"\b(?:то|техобслуживание|техническое обслуживание|service|oil service|замена масла)\b",
    re.IGNORECASE,
)
_AUTOFILL_WAIT_HINTS = ("ожид", "в пути", "клиент дума", "согласован", "заказали", "ждем", "ждём")
_AUTOFILL_PART_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("радиатор", ("радиатор", "radiator")),
    ("рычаг подвески", ("рычаг", "control arm")),
    ("стойка амортизатора", ("стойк", "амортиз", "shock", "strut")),
    ("ступичный подшипник", ("ступиц", "ступич", "bearing", "hub")),
    ("тормозные колодки", ("колодк", "pads")),
    ("тормозной диск", ("тормозн", "brake disc", "rotor")),
    ("термостат", ("термостат", "thermostat")),
    ("помпа", ("помп", "water pump")),
    ("ремень", ("ремень", "belt")),
    ("цепь грм", ("цеп", "timing chain")),
    ("масло", ("масло", "oil")),
    ("фильтр", ("фильтр", "filter")),
    ("свечи зажигания", ("свеч", "spark")),
    ("аккумулятор", ("аккумулятор", "battery")),
)


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
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        autofill_task = str(metadata.get("purpose", "") or "").strip().lower() == "card_autofill"
        self._tools.reset_task_budget()
        if autofill_task:
            return self._execute_card_autofill_task(task, run_id=run_id, metadata=metadata)
        prompt_override = self._storage.read_prompt_text().strip()
        memory_text = self._storage.read_memory_text().strip()
        system_prompt = DEFAULT_SYSTEM_PROMPT
        if prompt_override and prompt_override != DEFAULT_SYSTEM_PROMPT:
            system_prompt = f"{system_prompt}\n\nLocal instructions:\n{prompt_override}"
        if memory_text:
            system_prompt = f"{system_prompt}\n\nPersistent memory:\n{memory_text}"
        task_type = self._classify_task(task, metadata)
        context_kind = self._context_kind(metadata)
        system_prompt = (
            f"{system_prompt}\n\nAvailable tools:\n"
            f"{self._tools.describe_for_prompt(task_type=task_type, context_kind=context_kind)}"
        )
        cleanup_task = task_type == "card_cleanup"
        cleanup_card_id = self._cleanup_card_id(metadata)
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

    def _execute_card_autofill_task(
        self,
        task: dict[str, Any],
        *,
        run_id: str,
        metadata: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any], int]:
        card_id = self._cleanup_card_id(metadata) or str(metadata.get("card_id", "") or "").strip()
        if not card_id:
            raise AgentModelError("card_autofill task requires metadata.context.card_id.")
        tool_calls = 0
        applied_updates: list[str] = []
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
        context_args = {"card_id": card_id, "event_limit": 20, "include_repair_order_text": True}
        context_tool_name, context_payload = self._load_card_autofill_context(card_id=card_id, context_args=context_args)
        tool_calls += 1
        self._record_action(
            task_id=task["id"],
            run_id=run_id,
            step=tool_calls,
            tool_name=context_tool_name,
            args=context_args if context_tool_name == "get_card_context" else {"card_id": card_id},
            reason="Read current card context for deterministic autofill orchestration",
            result_payload=context_payload,
        )
        context_data = self._response_data(context_payload)
        facts = self._analyze_card_autofill_context(context_data, task_text=str(task.get("task_text", "") or ""))
        self._record_log_action(
            task_id=task["id"],
            run_id=run_id,
            step=tool_calls,
            level="INFO",
            phase="analysis",
            message=self._build_card_autofill_plan_message(facts),
        )
        orchestration_results: dict[str, Any] = {}
        if self._autofill_vin_should_run(facts):
            vin_payload = self._run_autofill_tool(
                task_id=task["id"],
                run_id=run_id,
                step=tool_calls + 1,
                tool_name="decode_vin",
                args={"vin": facts["vin"]},
                reason="Decode VIN and fill missing vehicle facts",
            )
            if vin_payload is not None:
                tool_calls += 1
                orchestration_results["decode_vin"] = self._response_data(vin_payload) or vin_payload
                facts["vehicle_context"] = self._merge_vehicle_context(
                    facts["vehicle_context"],
                    orchestration_results["decode_vin"],
                )
        if facts["dtc_codes"]:
            dtc_payload = self._run_autofill_tool(
                task_id=task["id"],
                run_id=run_id,
                step=tool_calls + 1,
                tool_name="decode_dtc",
                args={
                    "code": facts["dtc_codes"][0],
                    "vehicle_context": facts["vehicle_context"],
                },
                reason="Decode the first detected DTC code",
            )
            if dtc_payload is not None:
                tool_calls += 1
                orchestration_results["decode_dtc"] = self._response_data(dtc_payload) or dtc_payload
        if facts["maintenance_needed"]:
            maintenance_payload = self._run_autofill_tool(
                task_id=task["id"],
                run_id=run_id,
                step=tool_calls + 1,
                tool_name="estimate_maintenance",
                args={
                    "service_type": facts["maintenance_query"],
                    "vehicle_context": facts["vehicle_context"],
                },
                reason="Build a compact maintenance plan for this card",
            )
            if maintenance_payload is not None:
                tool_calls += 1
                orchestration_results["estimate_maintenance"] = self._response_data(maintenance_payload) or maintenance_payload
        if facts["part_queries"]:
            part_query = facts["part_queries"][0]
            part_payload = self._run_autofill_tool(
                task_id=task["id"],
                run_id=run_id,
                step=tool_calls + 1,
                tool_name="find_part_numbers",
                args={
                    "query": part_query,
                    "vehicle": facts["vehicle_context"],
                    "limit": 5,
                },
                reason="Find OEM and catalog part numbers for the main detected part",
            )
            if part_payload is not None:
                tool_calls += 1
                orchestration_results["find_part_numbers"] = self._response_data(part_payload) or part_payload
                best_part_number = self._pick_best_part_number(orchestration_results["find_part_numbers"])
                if best_part_number:
                    price_payload = self._run_autofill_tool(
                        task_id=task["id"],
                        run_id=run_id,
                        step=tool_calls + 1,
                        tool_name="estimate_price_ru",
                        args={
                            "part_number": best_part_number,
                            "vehicle": facts["vehicle_context"],
                            "limit": 5,
                        },
                        reason="Estimate Russian-market price for the top matched part number",
                    )
                    if price_payload is not None:
                        tool_calls += 1
                        orchestration_results["estimate_price_ru"] = self._response_data(price_payload) or price_payload
        if facts["symptom_query"] and not facts["waiting_state"]:
            fault_payload = self._run_autofill_tool(
                task_id=task["id"],
                run_id=run_id,
                step=tool_calls + 1,
                tool_name="search_fault_info",
                args={
                    "query": facts["symptom_query"],
                    "vehicle": facts["vehicle_context"],
                    "limit": 5,
                },
                reason="Search symptom context and typical causes for the current complaint",
            )
            if fault_payload is not None:
                tool_calls += 1
                orchestration_results["search_fault_info"] = self._response_data(fault_payload) or fault_payload
        update_args, display_sections = self._compose_card_autofill_update(
            card_id=card_id,
            facts=facts,
            orchestration_results=orchestration_results,
        )
        if update_args is not None:
            update_args = self._normalize_card_autofill_update(update_args)
            update_result = self._tools.execute("update_card", update_args)
            tool_calls += 1
            applied_updates.extend(self._summarize_applied_update(update_args, update_result))
            self._record_action(
                task_id=task["id"],
                run_id=run_id,
                step=tool_calls,
                tool_name="update_card",
                args=update_args,
                reason="Apply deterministic autofill enrichment to the current card",
                result_payload=update_result,
            )
        summary = self._autofill_result_summary(applied_updates, orchestration_results)
        display = {
            "emoji": "",
            "title": "Автосопровождение",
            "summary": summary,
            "tone": "success" if applied_updates else "info",
            "sections": display_sections[:5],
            "actions": [],
        }
        self._record_log_action(
            task_id=task["id"],
            run_id=run_id,
            step=max(tool_calls, 1),
            level="DONE",
            phase="completed",
            message=self._task_completed_message(metadata, summary=summary, applied_updates=applied_updates),
        )
        return summary, summary, display, tool_calls

    def _load_card_autofill_context(
        self,
        *,
        card_id: str,
        context_args: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        try:
            return "get_card_context", self._tools.execute("get_card_context", context_args)
        except Exception:
            card_payload = self._board_api.get_card(card_id)
            card_data = self._response_data(card_payload)
            card = card_data.get("card") if isinstance(card_data.get("card"), dict) else card_data
            context: dict[str, Any] = {
                "card": dict(card) if isinstance(card, dict) else {"id": card_id},
                "events": [],
            }
            if hasattr(self._board_api, "get_repair_order"):
                try:
                    repair_order_payload = self._board_api.get_repair_order(card_id)
                    repair_order_data = self._response_data(repair_order_payload)
                    repair_order = (
                        repair_order_data.get("repair_order")
                        if isinstance(repair_order_data.get("repair_order"), dict)
                        else repair_order_data
                    )
                    if isinstance(repair_order, dict):
                        context["card"]["repair_order"] = repair_order
                except Exception:
                    pass
            if hasattr(self._board_api, "get_repair_order_text"):
                try:
                    repair_order_text_payload = self._board_api.get_repair_order_text(card_id)
                    repair_order_text_data = self._response_data(repair_order_text_payload)
                    if isinstance(repair_order_text_data, dict):
                        context["repair_order_text"] = repair_order_text_data
                except Exception:
                    pass
            return "get_card", {"ok": True, "data": context}

    def _run_autofill_tool(
        self,
        *,
        task_id: str,
        run_id: str,
        step: int,
        tool_name: str,
        args: dict[str, Any],
        reason: str,
    ) -> dict[str, Any] | None:
        try:
            payload = self._tools.execute(tool_name, args)
        except Exception as exc:
            self._record_log_action(
                task_id=task_id,
                run_id=run_id,
                step=step,
                level="WARN",
                phase="tool",
                message=f"{tool_name}: {str(exc or '').strip() or 'ошибка внешнего шага.'}",
            )
            return None
        self._record_action(
            task_id=task_id,
            run_id=run_id,
            step=step,
            tool_name=tool_name,
            args=args,
            reason=reason,
            result_payload=payload,
        )
        completion_message = self._autofill_tool_completion_message(tool_name, self._response_data(payload) or payload)
        if completion_message:
            self._record_log_action(
                task_id=task_id,
                run_id=run_id,
                step=step,
                level="INFO",
                phase="tool",
                message=completion_message,
            )
        return payload

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
            vehicle_profile_compact = (
                card.get("vehicle_profile_compact")
                if isinstance(card.get("vehicle_profile_compact"), dict)
                else vehicle_profile
            )
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
                    "known_vehicle_facts": {
                        "vin": vehicle_profile_compact.get("vin") or vehicle_profile.get("vin"),
                        "make": vehicle_profile_compact.get("make_display") or vehicle_profile.get("make_display"),
                        "model": vehicle_profile_compact.get("model_display") or vehicle_profile.get("model_display"),
                        "year": vehicle_profile_compact.get("production_year") or vehicle_profile.get("production_year"),
                        "engine": vehicle_profile_compact.get("engine_model") or vehicle_profile.get("engine_model"),
                        "gearbox": vehicle_profile_compact.get("gearbox_model") or vehicle_profile.get("gearbox_model"),
                        "drivetrain": vehicle_profile_compact.get("drivetrain") or vehicle_profile.get("drivetrain"),
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
                    "changed_fields": data.get("meta", {}).get("changed_fields") if isinstance(data.get("meta"), dict) else data.get("changed"),
                    "card": data.get("card") if isinstance(data.get("card"), dict) else {},
                }
            )
        return self._preview_payload(compact)

    def _autofill_tool_completion_message(self, tool_name: str, payload: dict[str, Any]) -> str:
        if tool_name == "decode_vin":
            return "VIN расшифрован." if any(str(payload.get(key, "") or "").strip() for key in ("make", "model", "model_year", "engine_model")) else "VIN подтверждён, но новых фактов почти нет."
        if tool_name == "find_part_numbers":
            part_numbers = payload.get("part_numbers") if isinstance(payload.get("part_numbers"), list) else []
            return "Найдены кандидаты OEM/каталожных номеров." if part_numbers else "Точный OEM не найден, нужен более точный контекст."
        if tool_name == "estimate_price_ru":
            return "Получен ориентир по ценам РФ." if isinstance(payload.get("price_summary"), dict) else "Ценовой ориентир не найден."
        if tool_name == "decode_dtc":
            return "Найдена расшифровка DTC." if isinstance(payload.get("results"), list) and payload.get("results") else "По DTC найден только общий справочный контекст."
        if tool_name == "estimate_maintenance":
            return "Собран предварительный состав ТО."
        if tool_name == "search_fault_info":
            return "Найден внешний контекст по симптомам." if isinstance(payload.get("results"), list) and payload.get("results") else "По симптомам полезного внешнего контекста не найдено."
        return ""

    def _analyze_card_autofill_context(self, context_data: dict[str, Any], *, task_text: str = "") -> dict[str, Any]:
        card = context_data.get("card") if isinstance(context_data.get("card"), dict) else {}
        repair_order = card.get("repair_order") if isinstance(card.get("repair_order"), dict) else {}
        repair_order_text = context_data.get("repair_order_text") if isinstance(context_data.get("repair_order_text"), dict) else {}
        vehicle_profile = card.get("vehicle_profile") if isinstance(card.get("vehicle_profile"), dict) else {}
        ai_prompt = str(card.get("ai_autofill_prompt", "") or "").strip()
        known_vehicle_facts = {
            "make": str(vehicle_profile.get("make_display", "") or "").strip(),
            "model": str(vehicle_profile.get("model_display", "") or "").strip(),
            "year": str(vehicle_profile.get("production_year", "") or "").strip(),
            "engine": str(vehicle_profile.get("engine_model", "") or "").strip(),
            "gearbox": str(vehicle_profile.get("gearbox_model", "") or "").strip(),
            "drivetrain": str(vehicle_profile.get("drivetrain", "") or "").strip(),
            "vin": str(vehicle_profile.get("vin", "") or repair_order.get("vin", "") or "").strip().upper(),
        }
        source_text = "\n".join(
            part
            for part in (
                str(card.get("title", "") or "").strip(),
                str(card.get("vehicle", "") or "").strip(),
                str(card.get("description", "") or "").strip(),
                str(repair_order.get("reason", "") or "").strip(),
                str(repair_order.get("comment", "") or "").strip(),
                str(repair_order.get("note", "") or "").strip(),
                str(repair_order_text.get("text", "") or "").strip(),
            )
            if part
        )
        analysis_text = "\n".join(part for part in (source_text, ai_prompt, str(task_text or "").strip()) if part)
        haystack = analysis_text.casefold()
        vin_match = _AUTOFILL_VIN_PATTERN.search(analysis_text.upper())
        vin = known_vehicle_facts["vin"] or (vin_match.group(0) if vin_match else "")
        mileage = self._extract_autofill_mileage(card=card, repair_order=repair_order, source_text=analysis_text)
        dtc_codes = list(dict.fromkeys(match.upper() for match in _AUTOFILL_DTC_PATTERN.findall(analysis_text)))[:2]
        part_queries = self._extract_autofill_part_queries(analysis_text)
        maintenance_needed = bool(_AUTOFILL_MAINTENANCE_PATTERN.search(analysis_text)) or ("пробег" in haystack and "масл" in haystack)
        maintenance_query = f"ТО на пробеге {mileage}" if mileage else "ТО"
        if "торм" in haystack:
            maintenance_query = "ТО и тормоза"
        symptom_query = self._extract_autofill_symptom_query(source_text)
        return {
            "card": card,
            "repair_order": repair_order,
            "vehicle_profile": vehicle_profile,
            "source_text": source_text,
            "analysis_text": analysis_text,
            "ai_prompt": ai_prompt,
            "vin": vin,
            "mileage": mileage,
            "dtc_codes": dtc_codes,
            "part_queries": part_queries,
            "maintenance_needed": maintenance_needed,
            "maintenance_query": maintenance_query,
            "symptom_query": symptom_query,
            "waiting_state": any(token in haystack for token in _AUTOFILL_WAIT_HINTS),
            "force_vin_decode": bool(vin) and any(token in haystack for token in ("vin", "расшифр", "комплектац", "подтверд")),
            "missing_vehicle_fields": self._profile_missing_fields(vehicle_profile),
            "known_vehicle_facts": known_vehicle_facts,
            "vehicle_context": self._extract_autofill_vehicle_context(card=card, repair_order=repair_order, vehicle_profile=vehicle_profile, vin=vin),
        }

    def _build_card_autofill_plan_message(self, facts: dict[str, Any]) -> str:
        steps: list[str] = []
        if self._autofill_vin_should_run(facts):
            steps.append("VIN")
        if facts["dtc_codes"]:
            steps.append("DTC")
        if facts["maintenance_needed"]:
            steps.append("ТО")
        if facts["part_queries"]:
            steps.append("ЗАПЧАСТИ")
        if facts["symptom_query"] and not facts["waiting_state"]:
            steps.append("СИМПТОМЫ")
        if not steps:
            return "План: контекст прочитан, явных внешних сценариев не найдено."
        return "План: " + " -> ".join(steps)

    def _extract_autofill_vehicle_context(
        self,
        *,
        card: dict[str, Any],
        repair_order: dict[str, Any],
        vehicle_profile: dict[str, Any],
        vin: str,
    ) -> dict[str, Any]:
        return {
            "vehicle": str(card.get("vehicle", "") or repair_order.get("vehicle", "") or "").strip(),
            "make": str(vehicle_profile.get("make_display", "") or "").strip(),
            "model": str(vehicle_profile.get("model_display", "") or "").strip(),
            "year": str(vehicle_profile.get("production_year", "") or "").strip(),
            "engine": str(vehicle_profile.get("engine_model", "") or "").strip(),
            "gearbox": str(vehicle_profile.get("gearbox_model", "") or "").strip(),
            "drivetrain": str(vehicle_profile.get("drivetrain", "") or "").strip(),
            "vin": str(vin or "").strip(),
        }

    def _extract_autofill_mileage(self, *, card: dict[str, Any], repair_order: dict[str, Any], source_text: str) -> str:
        profile = card.get("vehicle_profile") if isinstance(card.get("vehicle_profile"), dict) else {}
        direct = str(profile.get("mileage", "") or repair_order.get("mileage", "") or "").strip()
        if direct:
            return direct
        match = _AUTOFILL_MILEAGE_PATTERN.search(source_text)
        return " ".join(match.group(1).split()) if match else ""

    def _extract_autofill_part_queries(self, source_text: str) -> list[str]:
        haystack = source_text.casefold()
        matches: list[str] = []
        for label, hints in _AUTOFILL_PART_HINTS:
            if any(token in haystack for token in hints):
                matches.append(label)
            if len(matches) >= 2:
                break
        return matches

    def _extract_autofill_symptom_query(self, source_text: str) -> str:
        lines: list[str] = []
        for raw_line in str(source_text or "").splitlines():
            line = " ".join(str(raw_line or "").strip().split())
            if not line:
                continue
            lower = line.casefold()
            if lower.startswith("vin") or lower.startswith("ии:") or lower.startswith("ai:"):
                continue
            if "артикул" in lower:
                continue
            if "цена" in lower and any(char.isdigit() for char in line):
                continue
            lines.append(line)
            if len(lines) >= 3:
                break
        return "; ".join(lines)[:280]

    def _profile_missing_fields(self, vehicle_profile: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        for field_name in ("make_display", "model_display", "production_year", "engine_model", "gearbox_model", "drivetrain"):
            if not str(vehicle_profile.get(field_name, "") or "").strip():
                missing.append(field_name)
        return missing

    def _autofill_vin_should_run(self, facts: dict[str, Any]) -> bool:
        return bool(facts["vin"]) and (bool(facts["missing_vehicle_fields"]) or bool(facts.get("force_vin_decode")))

    def _merge_vehicle_context(self, current: dict[str, Any], decoded: dict[str, Any]) -> dict[str, Any]:
        merged = dict(current)
        if not merged.get("make") and decoded.get("make"):
            merged["make"] = str(decoded.get("make", "") or "").strip()
        if not merged.get("model") and decoded.get("model"):
            merged["model"] = str(decoded.get("model", "") or "").strip()
        if not merged.get("year") and decoded.get("model_year"):
            merged["year"] = str(decoded.get("model_year", "") or "").strip()
        if not merged.get("engine") and decoded.get("engine_model"):
            merged["engine"] = str(decoded.get("engine_model", "") or "").strip()
        if not merged.get("gearbox") and decoded.get("transmission"):
            merged["gearbox"] = str(decoded.get("transmission", "") or "").strip()
        if not merged.get("drivetrain") and decoded.get("drive_type"):
            merged["drivetrain"] = str(decoded.get("drive_type", "") or "").strip()
        if not merged.get("vin") and decoded.get("vin"):
            merged["vin"] = str(decoded.get("vin", "") or "").strip()
        if not merged.get("vehicle"):
            merged["vehicle"] = " ".join(part for part in (merged.get("make", ""), merged.get("model", ""), merged.get("year", "")) if part).strip()
        return merged

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

    def _compose_card_autofill_update(
        self,
        *,
        card_id: str,
        facts: dict[str, Any],
        orchestration_results: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        card = facts["card"]
        current_description = str(card.get("description", "") or "").strip()
        vehicle_patch = self._autofill_vehicle_patch(facts=facts, decoded_vin=orchestration_results.get("decode_vin"))
        ai_lines: list[str] = []
        decoded_vin = orchestration_results.get("decode_vin")
        if isinstance(decoded_vin, dict):
            vin_bits: list[str] = []
            if decoded_vin.get("make"):
                vin_bits.append(str(decoded_vin.get("make", "") or "").strip())
            if decoded_vin.get("model"):
                vin_bits.append(str(decoded_vin.get("model", "") or "").strip())
            if decoded_vin.get("model_year"):
                vin_bits.append(str(decoded_vin.get("model_year", "") or "").strip())
            if decoded_vin.get("engine_model") and "engine_model" in vehicle_patch:
                vin_bits.append(f"двигатель: {decoded_vin.get('engine_model')}")
            if decoded_vin.get("transmission") and "gearbox_model" in vehicle_patch:
                vin_bits.append(f"КПП: {decoded_vin.get('transmission')}")
            if decoded_vin.get("drive_type") and "drivetrain" in vehicle_patch:
                vin_bits.append(f"привод: {decoded_vin.get('drive_type')}")
            if decoded_vin.get("plant_country"):
                vin_bits.append(f"сборка: {decoded_vin.get('plant_country')}")
            if vin_bits:
                ai_lines.append("По VIN подтверждено: " + ", ".join(vin_bits) + ".")
        part_lookup = orchestration_results.get("find_part_numbers")
        if isinstance(part_lookup, dict) and facts["part_queries"]:
            part_numbers = self._summarize_part_numbers(part_lookup)
            if part_numbers:
                part_line = f"{facts['part_queries'][0].capitalize()}: OEM/каталожные номера {part_numbers}."
                price_lookup = orchestration_results.get("estimate_price_ru")
                if isinstance(price_lookup, dict):
                    price_line = self._summarize_price_summary(price_lookup)
                    if price_line:
                        part_line += f" {price_line}"
                ai_lines.append(part_line)
            else:
                missing_bits = self._humanize_missing_vehicle_fields(facts["missing_vehicle_fields"])
                if missing_bits:
                    ai_lines.append(f"Следующему исполнителю: для точного подбора {facts['part_queries'][0]} уточнить {missing_bits}.")
        maintenance = orchestration_results.get("estimate_maintenance")
        if isinstance(maintenance, dict):
            works = maintenance.get("works") if isinstance(maintenance.get("works"), list) else []
            materials = maintenance.get("materials") if isinstance(maintenance.get("materials"), list) else []
            works_preview = ", ".join(
                str(item.get("name", "") or "").strip()
                for item in works[:3]
                if isinstance(item, dict) and str(item.get("name", "") or "").strip()
            )
            materials_preview = ", ".join(
                str(item.get("name", "") or "").strip()
                for item in materials[:4]
                if isinstance(item, dict) and str(item.get("name", "") or "").strip()
            )
            line = f"{str(maintenance.get('service_type', 'ТО') or 'ТО').strip()}:"
            if works_preview:
                line += f" работы — {works_preview}."
            if materials_preview:
                line += f" Расходники — {materials_preview}."
            ai_lines.append(line)
        dtc_result = orchestration_results.get("decode_dtc")
        if isinstance(dtc_result, dict) and facts["dtc_codes"]:
            snippet = self._first_search_snippet(dtc_result)
            if snippet:
                ai_lines.append(f"DTC {facts['dtc_codes'][0]}: {snippet}")
        fault_result = orchestration_results.get("search_fault_info")
        if isinstance(fault_result, dict):
            snippet = self._first_search_snippet(fault_result)
            if snippet:
                ai_lines.append(f"По симптомам: {snippet}")
        filtered_ai_lines = [line for line in ai_lines if self._line_has_new_information(current_description, line)]
        if not filtered_ai_lines and not vehicle_patch:
            return None, []
        update_args: dict[str, Any] = {"card_id": card_id}
        if filtered_ai_lines:
            update_args["description"] = "ИИ:\n- " + "\n- ".join(filtered_ai_lines)
        if vehicle_patch:
            update_args["vehicle_profile"] = vehicle_patch
        display_sections: list[dict[str, Any]] = []
        if vehicle_patch:
            display_sections.append(
                {
                    "title": "Профиль авто",
                    "body": "",
                    "items": [f"{key}: {value}" for key, value in vehicle_patch.items() if key in {"make_display", "model_display", "production_year", "engine_model", "gearbox_model", "drivetrain", "vin"}],
                }
            )
        if filtered_ai_lines:
            display_sections.append({"title": "Добавлено в карточку", "body": "", "items": filtered_ai_lines[:6]})
        return update_args, display_sections

    def _autofill_vehicle_patch(self, *, facts: dict[str, Any], decoded_vin: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(decoded_vin, dict):
            return {}
        patch: dict[str, Any] = {}
        existing = facts["vehicle_profile"]
        field_sources: dict[str, str] = {}
        autofilled_fields: list[str] = []

        def _set_if_missing(field_name: str, value: Any) -> None:
            text = str(value or "").strip()
            if not text or str(existing.get(field_name, "") or "").strip():
                return
            patch[field_name] = text
            autofilled_fields.append(field_name)
            field_sources[field_name] = "official_vin_decode_nhtsa"

        _set_if_missing("vin", decoded_vin.get("vin") or facts["vin"])
        _set_if_missing("make_display", decoded_vin.get("make"))
        _set_if_missing("model_display", decoded_vin.get("model"))
        if not str(existing.get("production_year", "") or "").strip():
            try:
                year_value = int(str(decoded_vin.get("model_year", "") or "").strip())
            except (TypeError, ValueError):
                year_value = None
            if year_value:
                patch["production_year"] = year_value
                autofilled_fields.append("production_year")
                field_sources["production_year"] = "official_vin_decode_nhtsa"
        _set_if_missing("engine_model", decoded_vin.get("engine_model"))
        _set_if_missing("gearbox_model", decoded_vin.get("transmission"))
        _set_if_missing("drivetrain", decoded_vin.get("drive_type"))
        if not patch:
            return {}
        patch["source_summary"] = "official VIN decode"
        patch["source_confidence"] = 0.78
        patch["autofilled_fields"] = autofilled_fields
        patch["field_sources"] = field_sources
        source_refs = [str(decoded_vin.get("source_url", "") or "").strip()]
        patch["source_links_or_refs"] = [item for item in source_refs if item]
        patch["data_completion_state"] = "mostly_autofilled" if len(autofilled_fields) >= 3 else "partially_autofilled"
        return patch

    def _humanize_missing_vehicle_fields(self, fields: list[str]) -> str:
        mapping = {
            "model_display": "модель",
            "production_year": "год",
            "engine_model": "двигатель",
            "gearbox_model": "КПП",
            "drivetrain": "привод",
            "make_display": "марку",
        }
        values = [mapping[field_name] for field_name in fields[:3] if field_name in mapping]
        return ", ".join(values)

    def _pick_best_part_number(self, payload: dict[str, Any]) -> str:
        candidates = payload.get("part_numbers") if isinstance(payload.get("part_numbers"), list) else []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value", "") or "").strip()
            if value:
                return value
        return ""

    def _summarize_part_numbers(self, payload: dict[str, Any]) -> str:
        candidates = payload.get("part_numbers") if isinstance(payload.get("part_numbers"), list) else []
        values = [
            str(item.get("value", "") or "").strip()
            for item in candidates[:3]
            if isinstance(item, dict) and str(item.get("value", "") or "").strip()
        ]
        return ", ".join(values)

    def _summarize_price_summary(self, payload: dict[str, Any]) -> str:
        price_summary = payload.get("price_summary") if isinstance(payload.get("price_summary"), dict) else {}
        if not price_summary:
            return ""
        offers_total = int(price_summary.get("offers_total", 0) or 0)
        min_rub = int(price_summary.get("min_rub", 0) or 0)
        max_rub = int(price_summary.get("max_rub", 0) or 0)
        if min_rub <= 0 and max_rub <= 0:
            return ""
        if min_rub and max_rub and min_rub != max_rub:
            return f"Ориентир по РФ: {min_rub:,}-{max_rub:,} ₽ ({offers_total} предложений).".replace(",", " ")
        value = max_rub or min_rub
        return f"Ориентир по РФ: около {value:,} ₽ ({offers_total} предложений).".replace(",", " ")

    def _first_search_snippet(self, payload: dict[str, Any]) -> str:
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        for item in results:
            if not isinstance(item, dict):
                continue
            text = str(item.get("snippet", "") or item.get("title", "") or "").strip()
            if text:
                return text[:220]
        return ""

    def _line_has_new_information(self, current_description: str, line: str) -> bool:
        normalized_current = " ".join(str(current_description or "").split()).casefold()
        normalized_line = " ".join(str(line or "").replace("ИИ:", "").replace("AI:", "").split()).casefold()
        return bool(normalized_line) and normalized_line not in normalized_current

    def _autofill_result_summary(self, applied_updates: list[str], orchestration_results: dict[str, Any]) -> str:
        if applied_updates:
            parts: list[str] = []
            if "decode_vin" in orchestration_results:
                parts.append("VIN")
            if "find_part_numbers" in orchestration_results:
                parts.append("запчасти")
            if "estimate_maintenance" in orchestration_results:
                parts.append("ТО")
            if "decode_dtc" in orchestration_results:
                parts.append("DTC")
            if "search_fault_info" in orchestration_results:
                parts.append("симптомы")
            if parts:
                return "Карточка дополнена: " + ", ".join(parts) + "."
            return "Карточка дополнена по автосопровождению."
        return "Изменений не обнаружено."

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
            return self._dedupe_card_autofill_paragraphs(proposed)
        current_normalized = " ".join(current.split())
        proposed_normalized = " ".join(proposed.split())
        if proposed_normalized == current_normalized or proposed_normalized in current_normalized:
            return current
        if current_normalized and current_normalized in proposed_normalized:
            return self._dedupe_card_autofill_paragraphs(proposed)
        if "ИИ:" in proposed or "AI:" in proposed:
            return self._dedupe_card_autofill_paragraphs(f"{current}\n\n{proposed}")
        normalized_ai_block = "\n".join(
            line.strip()
            for line in proposed.splitlines()
            if line.strip()
        )
        return self._dedupe_card_autofill_paragraphs(f"{current}\n\nИИ:\n{normalized_ai_block}")

    def _dedupe_card_autofill_paragraphs(self, text: str) -> str:
        paragraphs = [part.strip() for part in str(text or "").split("\n\n") if str(part or "").strip()]
        if not paragraphs:
            return ""
        deduped: list[str] = []
        seen: set[str] = set()
        for paragraph in paragraphs:
            normalized = " ".join(paragraph.split()).casefold()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(paragraph)
        return "\n\n".join(deduped)

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
