from __future__ import annotations

import threading
import time
import uuid
from datetime import timedelta
from typing import Any

from ..models import parse_datetime, utc_now, utc_now_iso
from .config import (
    get_agent_board_api_url,
    get_agent_enabled,
    get_agent_name,
    get_agent_openai_api_key,
    get_agent_openai_model,
    get_agent_poll_interval_seconds,
)
from .storage import AgentStorage


class AgentControlService:
    def __init__(
        self,
        storage: AgentStorage,
        *,
        scheduler_interval_seconds: float = 20.0,
        start_scheduler: bool = False,
    ) -> None:
        self._storage = storage
        self._board_service: Any | None = None
        self._scheduler_interval_seconds = max(5.0, float(scheduler_interval_seconds))
        self._scheduler_stop = threading.Event()
        self._scheduler_thread: threading.Thread | None = None
        self._worker_stop = threading.Event()
        self._worker_thread: threading.Thread | None = None
        if start_scheduler:
            self.start_scheduler()

    def start_scheduler(self) -> None:
        if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
            return
        self._scheduler_stop.clear()
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, name="minimal-kanban-agent-scheduler", daemon=True)
        self._scheduler_thread.start()

    def close(self) -> None:
        self._scheduler_stop.set()
        if self._scheduler_thread is not None:
            self._scheduler_thread.join(timeout=2)
            self._scheduler_thread = None
        self._worker_stop.set()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=2)
            self._worker_thread = None

    def bind_board_service(self, board_service: Any | None) -> None:
        self._board_service = board_service

    def start_worker(self, *, logger: Any, board_api_url: str | None = None) -> bool:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return True
        if not get_agent_enabled() or not get_agent_openai_api_key():
            return False
        self._worker_stop.clear()
        resolved_board_api_url = str(board_api_url or get_agent_board_api_url() or "").strip().rstrip("/")
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            args=(logger, resolved_board_api_url),
            name="minimal-kanban-agent-worker",
            daemon=True,
        )
        self._worker_thread.start()
        return True

    def has_active_task_for_card(self, card_id: str, *, purpose: str | None = None) -> bool:
        return self._storage.has_active_task_for_card(card_id, purpose=purpose)

    def latest_task_for_card(self, card_id: str, *, purpose: str | None = None) -> dict[str, Any] | None:
        normalized_card_id = str(card_id or "").strip()
        normalized_purpose = str(purpose or "").strip().lower()
        if not normalized_card_id:
            return None
        for task in self._storage.list_tasks(limit=200):
            metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
            context = metadata.get("context") if isinstance(metadata.get("context"), dict) else {}
            metadata_card_id = str(context.get("card_id") or metadata.get("card_id") or "").strip()
            if metadata_card_id != normalized_card_id:
                continue
            if normalized_purpose and str(metadata.get("purpose", "") or "").strip().lower() != normalized_purpose:
                continue
            return task
        return None

    def agent_status(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        self.trigger_scheduled_tasks()
        pending_total = len(self._storage.list_tasks(limit=1000, statuses={"pending"}))
        running_total = len(self._storage.list_tasks(limit=1000, statuses={"running"}))
        status = self._storage.read_status()
        schedules = self._storage.list_schedules()
        active_total = sum(1 for item in schedules if bool(item.get("active")))
        configured = bool(get_agent_openai_api_key())
        heartbeat_at = parse_datetime(str(status.get("last_heartbeat", "") or "").strip())
        heartbeat_fresh = False
        if heartbeat_at is not None:
            heartbeat_age_seconds = max(0.0, (utc_now() - heartbeat_at).total_seconds())
            heartbeat_fresh = heartbeat_age_seconds <= max(30.0, float(get_agent_poll_interval_seconds()) * 10.0)
        available = get_agent_enabled() and (configured or heartbeat_fresh)
        return {
            "agent": {
                "name": get_agent_name(),
                "enabled": get_agent_enabled(),
                "available": available,
                "configured": configured,
                "model": get_agent_openai_model(),
                "board_api_url": get_agent_board_api_url() or "",
            },
            "worker": {
                "embedded": True,
                "running": bool(self._worker_thread and self._worker_thread.is_alive()),
                "heartbeat_fresh": heartbeat_fresh,
            },
            "status": status,
            "queue": {
                "pending_total": pending_total,
                "running_total": running_total,
            },
            "scheduled": {
                "total": len(schedules),
                "active_total": active_total,
                "paused_total": max(0, len(schedules) - active_total),
            },
            "recent_runs": self._storage.list_runs(limit=min(max(int(payload.get("run_limit", 10) or 10), 1), 50)),
        }

    def agent_enqueue_task(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        task_text = str(payload.get("task_text", "") or "").strip()
        if not task_text:
            raise ValueError("task_text is required")
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        session = payload.get("_operator_session") if isinstance(payload.get("_operator_session"), dict) else {}
        if session:
            metadata = dict(metadata)
            metadata.setdefault("requested_by", str(session.get("username", "") or "").strip())
        task = self._storage.enqueue_task(
            task_text=task_text,
            source=str(payload.get("source", "ui_agent") or "ui_agent"),
            mode=str(payload.get("mode", "manual") or "manual"),
            metadata=metadata or None,
        )
        return {"task": task}

    def agent_runs(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        limit = min(max(int(payload.get("limit", 50) or 50), 1), 200)
        return {"runs": self._storage.list_runs(limit=limit), "meta": {"limit": limit}}

    def agent_actions(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        limit = min(max(int(payload.get("limit", 100) or 100), 1), 500)
        run_id = str(payload.get("run_id", "") or "").strip() or None
        task_id = str(payload.get("task_id", "") or "").strip() or None
        return {
            "actions": self._storage.list_actions(limit=limit, run_id=run_id, task_id=task_id),
            "meta": {"limit": limit, "run_id": run_id, "task_id": task_id},
        }

    def agent_tasks(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        limit = min(max(int(payload.get("limit", 50) or 50), 1), 200)
        statuses_raw = str(payload.get("status", "") or "").strip()
        statuses = {item.strip() for item in statuses_raw.split(",") if item.strip()} if statuses_raw else None
        return {
            "tasks": self._storage.list_tasks(limit=limit, statuses=statuses),
            "meta": {"limit": limit, "statuses": sorted(statuses) if statuses else []},
        }

    def agent_scheduled_tasks(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.trigger_scheduled_tasks()
        tasks = [self._serialize_schedule(item) for item in self._storage.list_schedules()]
        return {"tasks": tasks, "meta": {"total": len(tasks)}}

    def save_agent_scheduled_task(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        existing = self._storage.get_schedule(str(payload.get("task_id", "") or payload.get("id", "")).strip())
        task = self._normalize_schedule_payload(payload, existing=existing)
        stored = self._storage.upsert_schedule(task)
        if stored.get("active"):
            self.trigger_scheduled_tasks()
        return {"task": self._serialize_schedule(stored)}

    def delete_agent_scheduled_task(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        task_id = str(payload.get("task_id", "") or "").strip()
        if not task_id:
            raise ValueError("task_id is required")
        return {"deleted": self._storage.delete_schedule(task_id), "task_id": task_id}

    def pause_agent_scheduled_task(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        task = self._set_schedule_active(payload, active=False)
        return {"task": self._serialize_schedule(task)}

    def resume_agent_scheduled_task(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        task = self._set_schedule_active(payload, active=True)
        self.trigger_scheduled_tasks()
        return {"task": self._serialize_schedule(task)}

    def run_agent_scheduled_task(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        task_id = str(payload.get("task_id", "") or "").strip()
        if not task_id:
            raise ValueError("task_id is required")
        scheduled = self._storage.get_schedule(task_id)
        if scheduled is None:
            raise KeyError(f"Unknown schedule task: {task_id}")
        if self._storage.has_active_task_for_schedule(task_id):
            return {"task": None, "scheduled_task": self._serialize_schedule(scheduled), "meta": {"already_running": True}}
        task = self._enqueue_scheduled_task(scheduled, source="ui_agent_task_run")
        return {"task": task, "scheduled_task": self._serialize_schedule(self._storage.get_schedule(task_id) or scheduled), "meta": {"already_running": False}}

    def handle_card_created(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        card_id = str(payload.get("card_id", "") or "").strip()
        column = str(payload.get("column", "") or "").strip()
        if not card_id:
            return {"launched": [], "meta": {"matched": 0}}
        launched: list[str] = []
        matched_total = 0
        for scheduled in self._storage.list_schedules():
            if not bool(scheduled.get("active")):
                continue
            if str(scheduled.get("schedule_type", "") or "").strip().lower() != "on_create":
                continue
            if not self._schedule_matches_card(scheduled, card_id=card_id, column=column):
                continue
            matched_total += 1
            schedule_id = str(scheduled.get("id", "") or "").strip()
            if self._storage.has_active_task_for_schedule_card(schedule_id, card_id):
                continue
            task = self._enqueue_scheduled_task(
                scheduled,
                source="agent_on_create",
                context={
                    "kind": "card",
                    "card_id": card_id,
                },
                metadata_extra={
                    "purpose": "scheduled_on_create",
                    "trigger": "card_created",
                },
            )
            if task:
                launched.append(str(task.get("id", "") or "").strip())
        return {"launched": launched, "meta": {"matched": matched_total}}

    def enqueue_card_autofill_task(
        self,
        payload: dict[str, Any] | None = None,
        *,
        source: str = "ui_card_autofill",
        trigger: str = "manual",
    ) -> dict[str, Any] | None:
        payload = payload or {}
        card_id = str(payload.get("card_id", "") or "").strip()
        if not card_id:
            raise ValueError("card_id is required")
        if self._storage.has_active_task_for_card(card_id, purpose="card_autofill"):
            return None
        task_text = str(payload.get("task_text", "") or "").strip() or self._build_card_autofill_prompt(payload)
        metadata = {
            "requested_by": str(payload.get("requested_by", "") or "autofill").strip() or "autofill",
            "purpose": "card_autofill",
            "trigger": str(trigger or "manual").strip() or "manual",
            "context": {
                "kind": "card",
                "card_id": card_id,
            },
            "scope": {
                "type": "current_card",
                "card_id": card_id,
                "card_label": str(payload.get("card_heading", "") or payload.get("title", "") or "").strip(),
            },
            "card_autofill": {
                "card_id": card_id,
                "card_heading": str(payload.get("card_heading", "") or payload.get("title", "") or "").strip(),
                "vehicle": str(payload.get("vehicle", "") or "").strip(),
            },
        }
        return self._storage.enqueue_task(
            task_text=task_text,
            source=source,
            mode="card_autofill",
            metadata=metadata,
        )

    def trigger_scheduled_tasks(self) -> dict[str, Any]:
        now_text = utc_now_iso()
        launched: list[str] = []
        failed: list[dict[str, str]] = []
        for scheduled in self._storage.list_schedules():
            if not bool(scheduled.get("active")):
                continue
            if str(scheduled.get("schedule_type", "once") or "once").strip().lower() == "on_create":
                continue
            next_run_at = str(scheduled.get("next_run_at", "") or "").strip()
            if next_run_at and next_run_at > now_text:
                continue
            task_id = str(scheduled.get("id", "") or "").strip()
            if not task_id or self._storage.has_active_task_for_schedule(task_id):
                continue
            try:
                self._enqueue_scheduled_task(scheduled, source="agent_scheduler")
                launched.append(task_id)
            except Exception as exc:
                failed.append({"task_id": task_id, "error": str(exc)})
                self._storage.update_schedule(
                    task_id,
                    last_error=str(exc),
                    updated_at=utc_now_iso(),
                    next_run_at=self._next_run_at(scheduled, from_now=True),
                )
        if self._board_service is not None:
            try:
                payload = self._board_service.trigger_due_ai_followups()
                launched.extend([str(item) for item in payload.get("launched", []) if str(item or "").strip()])
                for item in payload.get("failed", []):
                    if isinstance(item, dict):
                        failed.append({"task_id": str(item.get("card_id", "") or "").strip(), "error": str(item.get("error", "") or "").strip()})
            except Exception as exc:
                failed.append({"task_id": "card_autofill", "error": str(exc)})
        return {"launched": launched, "failed": failed}

    def _scheduler_loop(self) -> None:
        while not self._scheduler_stop.wait(self._scheduler_interval_seconds):
            try:
                self.trigger_scheduled_tasks()
            except Exception:
                continue

    def _worker_loop(self, logger: Any, board_api_url: str) -> None:
        from ..mcp.client import BoardApiClient
        from .openai_client import OpenAIJsonAgentClient
        from .runner import AgentRunner, DEFAULT_SYSTEM_PROMPT

        idle_sleep = max(0.2, float(get_agent_poll_interval_seconds()))
        if not self._storage.read_prompt_text().strip():
            self._storage.write_prompt_text(DEFAULT_SYSTEM_PROMPT)
        if not self._storage.read_memory_text().strip():
            self._storage.write_memory_text(
                "CRM URL: https://crm.autostopcrm.ru\n"
                "MCP URL: https://crm.autostopcrm.ru/mcp\n"
                "Default admin: admin/admin\n"
                "Use cashbox names exactly as they exist.\n"
                "If payment goes to cashbox 'Безналичный', the repair order adds 15% taxes and fees from that payment amount.\n"
                "Cashboxes 'Наличный' and 'Карта Мария' do not add taxes and fees.\n"
            )
        runner = None
        while not self._worker_stop.is_set():
            try:
                if not get_agent_enabled():
                    self._storage.update_status(
                        running=False,
                        current_task_id=None,
                        current_run_id=None,
                        last_heartbeat=utc_now_iso(),
                        last_error="",
                    )
                    self._worker_stop.wait(idle_sleep)
                    continue
                if runner is None:
                    resolved_board_api_url = str(board_api_url or get_agent_board_api_url() or "").strip().rstrip("/")
                    if not resolved_board_api_url:
                        raise RuntimeError("Board API URL is not configured for embedded agent runtime.")
                    board_api = BoardApiClient(resolved_board_api_url, logger=logger, default_source="agent")
                    health = board_api.health()
                    if not health.get("ok"):
                        raise RuntimeError("Board API health check failed for embedded agent runtime.")
                    runner = AgentRunner(
                        storage=self._storage,
                        board_api=board_api,
                        model_client=OpenAIJsonAgentClient(),
                        logger=logger,
                    )
                processed = runner.run_once()
            except Exception as exc:
                self._storage.update_status(
                    running=False,
                    current_task_id=None,
                    current_run_id=None,
                    last_heartbeat=utc_now_iso(),
                    last_error=str(exc),
                )
                logger.exception("embedded_agent_worker_failed error=%s", exc)
                runner = None
                self._worker_stop.wait(idle_sleep)
                continue
            self._worker_stop.wait(0.2 if processed else idle_sleep)

    def _set_schedule_active(self, payload: dict[str, Any] | None, *, active: bool) -> dict[str, Any]:
        payload = payload or {}
        task_id = str(payload.get("task_id", "") or "").strip()
        if not task_id:
            raise ValueError("task_id is required")
        scheduled = self._storage.get_schedule(task_id)
        if scheduled is None:
            raise KeyError(f"Unknown schedule task: {task_id}")
        updated = self._storage.update_schedule(
            task_id,
            active=active,
            updated_at=utc_now_iso(),
            next_run_at=self._next_run_at({**scheduled, "active": active}, from_now=True) if active else "",
            last_error="",
        )
        return updated

    def _normalize_schedule_payload(self, payload: dict[str, Any], *, existing: dict[str, Any] | None) -> dict[str, Any]:
        now_text = utc_now_iso()
        raw_id = str(payload.get("task_id", "") or payload.get("id", "") or (existing.get("id", "") if existing else "")).strip()
        task_id = raw_id or f"agsch_{uuid.uuid4().hex[:12]}"
        name = str(payload.get("name", existing.get("name", "") if existing else "") or "").strip()[:80]
        prompt = str(payload.get("prompt", existing.get("prompt", "") if existing else "") or "").strip()[:8000]
        if not name:
            raise ValueError("name is required")
        if not prompt:
            raise ValueError("prompt is required")
        scope_type = str(payload.get("scope_type", existing.get("scope_type", "all_cards") if existing else "all_cards") or "all_cards").strip().lower()
        if scope_type not in {"all_cards", "column", "current_card"}:
            scope_type = "all_cards"
        scope_column = str(
            payload.get("scope_column")
            or payload.get("column_id")
            or payload.get("column")
            or (existing.get("scope_column", "") if existing else "")
            or ""
        ).strip()
        scope_column_label = str(payload.get("scope_column_label", existing.get("scope_column_label", "") if existing else "") or "").strip()
        scope_card_id = str(payload.get("scope_card_id", existing.get("scope_card_id", "") if existing else "") or "").strip()
        scope_card_label = str(payload.get("scope_card_label", existing.get("scope_card_label", "") if existing else "") or "").strip()
        if scope_type == "column" and not scope_column:
            raise ValueError("scope column is required")
        if scope_type == "current_card" and not scope_card_id:
            raise ValueError("scope card id is required")
        schedule_type = str(payload.get("schedule_type", existing.get("schedule_type", "once") if existing else "once") or "once").strip().lower()
        if schedule_type not in {"once", "interval", "on_create"}:
            schedule_type = "once"
        interval_value = self._normalize_interval_value(payload.get("interval_value", existing.get("interval_value", 1) if existing else 1))
        interval_unit = str(payload.get("interval_unit", existing.get("interval_unit", "minute") if existing else "minute") or "minute").strip().lower()
        if interval_unit not in {"minute", "hour"}:
            interval_unit = "minute"
        active = self._as_bool(payload.get("active", existing.get("active", True) if existing else True))
        fields_changed = not existing or any(
            existing.get(field) != value
            for field, value in (
                ("name", name),
                ("prompt", prompt),
                ("scope_type", scope_type),
                ("scope_column", scope_column),
                ("scope_column_label", scope_column_label),
                ("scope_card_id", scope_card_id),
                ("scope_card_label", scope_card_label),
                ("schedule_type", schedule_type),
                ("interval_value", interval_value),
                ("interval_unit", interval_unit),
                ("active", active),
            )
        )
        next_run_at = str(existing.get("next_run_at", "") if existing else "").strip()
        if active:
            if fields_changed or not next_run_at:
                next_run_at = self._next_run_at(
                    {
                        "schedule_type": schedule_type,
                        "interval_value": interval_value,
                        "interval_unit": interval_unit,
                    },
                    from_now=True,
                )
        else:
            next_run_at = ""
        return {
            "id": task_id,
            "created_at": str(existing.get("created_at", now_text) if existing else now_text),
            "updated_at": now_text,
            "name": name,
            "prompt": prompt,
            "scope_type": scope_type,
            "scope_column": scope_column,
            "scope_column_label": scope_column_label,
            "scope_card_id": scope_card_id,
            "scope_card_label": scope_card_label,
            "schedule_type": schedule_type,
            "interval_value": interval_value,
            "interval_unit": interval_unit,
            "active": active,
            "next_run_at": next_run_at,
            "last_enqueued_at": str(existing.get("last_enqueued_at", "") if existing else ""),
            "last_task_id": str(existing.get("last_task_id", "") if existing else ""),
            "last_error": str(existing.get("last_error", "") if existing else ""),
        }

    def _normalize_interval_value(self, value: Any) -> int:
        try:
            return max(1, int(value or 1))
        except (TypeError, ValueError):
            return 1

    def _as_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}

    def _next_run_at(self, scheduled: dict[str, Any], *, from_now: bool) -> str:
        if not bool(scheduled.get("active", True)) and not from_now:
            return ""
        schedule_type = str(scheduled.get("schedule_type", "once") or "once").strip().lower()
        if schedule_type == "on_create":
            return ""
        if schedule_type != "interval":
            return utc_now_iso() if (from_now or not str(scheduled.get("last_enqueued_at", "")).strip()) else ""
        base = utc_now() if from_now else (parse_datetime(str(scheduled.get("last_enqueued_at", "") or "").strip()) or utc_now())
        step = self._normalize_interval_value(scheduled.get("interval_value"))
        unit = str(scheduled.get("interval_unit", "minute") or "minute").strip().lower()
        delta = timedelta(hours=step) if unit == "hour" else timedelta(minutes=step)
        return (base + delta).isoformat()

    def _enqueue_scheduled_task(
        self,
        scheduled: dict[str, Any],
        *,
        source: str,
        context: dict[str, Any] | None = None,
        metadata_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task_id = str(scheduled.get("id", "") or "").strip()
        if not task_id:
            raise ValueError("Scheduled task id is required")
        scope_type = str(scheduled.get("scope_type", "all_cards") or "all_cards").strip().lower()
        scope_card_id = str(scheduled.get("scope_card_id", "") or "").strip()
        normalized_context = context if isinstance(context, dict) else {}
        if scope_type == "current_card" and not normalized_context:
            normalized_context = {"kind": "card", "card_id": scope_card_id}
        metadata = {
            "requested_by": "scheduler",
            "scheduled_task_id": task_id,
            "scheduled_task_name": str(scheduled.get("name", "") or "").strip(),
            "schedule": {
                "type": str(scheduled.get("schedule_type", "once") or "once"),
                "interval_value": self._normalize_interval_value(scheduled.get("interval_value")),
                "interval_unit": str(scheduled.get("interval_unit", "minute") or "minute"),
            },
            "scope": {
                "type": scope_type,
                "column": str(scheduled.get("scope_column", "") or "").strip(),
                "column_label": str(scheduled.get("scope_column_label", "") or "").strip(),
                "card_id": scope_card_id,
                "card_label": str(scheduled.get("scope_card_label", "") or "").strip(),
            },
        }
        if normalized_context:
            metadata["context"] = dict(normalized_context)
        if isinstance(metadata_extra, dict):
            metadata.update(metadata_extra)
        task = self._storage.enqueue_task(
            task_text=str(scheduled.get("prompt", "") or "").strip(),
            source=source,
            mode="scheduled",
            metadata=metadata,
        )
        updates = {
            "updated_at": utc_now_iso(),
            "last_enqueued_at": task["created_at"],
            "last_task_id": task["id"],
            "last_error": "",
            "next_run_at": self._next_run_at(scheduled, from_now=False),
        }
        if str(scheduled.get("schedule_type", "once") or "once").strip().lower() not in {"interval", "on_create"}:
            updates["active"] = False
            updates["next_run_at"] = ""
        self._storage.update_schedule(task_id, **updates)
        return task

    def _serialize_schedule(self, scheduled: dict[str, Any]) -> dict[str, Any]:
        task_id = str(scheduled.get("id", "") or "").strip()
        scope_type = str(scheduled.get("scope_type", "all_cards") or "all_cards").strip().lower()
        interval_value = self._normalize_interval_value(scheduled.get("interval_value"))
        interval_unit = str(scheduled.get("interval_unit", "minute") or "minute").strip().lower()
        schedule_type = str(scheduled.get("schedule_type", "once") or "once").strip().lower()
        if schedule_type == "interval":
            period = f"{interval_value}{'h' if interval_unit == 'hour' else 'm'}"
        elif schedule_type == "on_create":
            period = "on_create"
        else:
            period = "once"
        scope_card_id = str(scheduled.get("scope_card_id", "") or "").strip()
        scope_card_label = str(scheduled.get("scope_card_label", "") or "").strip()
        return {
            "id": task_id,
            "name": str(scheduled.get("name", "") or "").strip(),
            "prompt": str(scheduled.get("prompt", "") or "").strip(),
            "status": "active" if bool(scheduled.get("active")) else "paused",
            "active": bool(scheduled.get("active")),
            "period": period,
            "schedule_type": schedule_type,
            "interval_value": interval_value,
            "interval_unit": interval_unit,
            "scope_type": scope_type,
            "scope_column": str(scheduled.get("scope_column", "") or "").strip(),
            "scope_column_label": str(scheduled.get("scope_column_label", "") or "").strip(),
            "scope_card_id": scope_card_id,
            "scope_card_label": scope_card_label,
            "scope_label": (
                str(scheduled.get("scope_column_label", "") or scheduled.get("scope_column", "") or "").strip()
                if scope_type == "column"
                else "Все карточки"
            ),
            "next_run_at": str(scheduled.get("next_run_at", "") or "").strip(),
            "last_enqueued_at": str(scheduled.get("last_enqueued_at", "") or "").strip(),
            "last_task_id": str(scheduled.get("last_task_id", "") or "").strip(),
            "last_error": str(scheduled.get("last_error", "") or "").strip(),
            "busy": self._storage.has_active_task_for_schedule(task_id),
        }

    def _schedule_matches_card(self, scheduled: dict[str, Any], *, card_id: str, column: str) -> bool:
        scope_type = str(scheduled.get("scope_type", "all_cards") or "all_cards").strip().lower()
        if scope_type == "column":
            return str(scheduled.get("scope_column", "") or "").strip() == str(column or "").strip()
        if scope_type == "current_card":
            return str(scheduled.get("scope_card_id", "") or "").strip() == str(card_id or "").strip()
        return True

    def _build_card_autofill_prompt(self, payload: dict[str, Any]) -> str:
        heading = str(payload.get("card_heading", "") or payload.get("title", "") or "").strip()
        vehicle = str(payload.get("vehicle", "") or "").strip()
        mini_prompt = str(payload.get("prompt", "") or payload.get("ai_autofill_prompt", "") or "").strip()
        ai_log_tail = payload.get("ai_log_tail") if isinstance(payload.get("ai_log_tail"), list) else []
        lines = [
            "Выполни автосопровождение карточки автосервиса.",
            "Работай только с этой карточкой и не добавляй шум, если полезных изменений нет.",
            "Сначала прочитай get_card_context.",
            "Если есть VIN, используй decode_vin и кратко дополни карточку.",
            "Если есть детали, используй find_part_numbers и estimate_price_ru.",
            "Если есть DTC-коды, используй decode_dtc.",
            "Если есть симптомы без кодов, при необходимости используй search_fault_info.",
            "Обновляй карточку коротко, структурированно и без перезаписи полезного существующего текста.",
        ]
        if heading:
            lines.append(f"Карточка: {heading}.")
        if vehicle:
            lines.append(f"Автомобиль: {vehicle}.")
        lines.extend(
            [
                "Preserve all existing facts, numbers, prices, part numbers, notes, and customer statements.",
                "Do not delete useful content just to make the text shorter.",
                "Only supplement, structure, or carefully rephrase the card.",
                "Do not repeat the current description verbatim. Return only the net-new AI note or one clean deduplicated rewrite.",
                "Write the card update in Russian unless the whole card is clearly in another language.",
                "Label AI-added notes, comments, or next questions with 'ИИ:' or 'AI:'.",
                "Treat current vehicle_profile and repair_order fields as known facts. Do not say model, year, engine, gearbox, or drivetrain are missing if they are already filled in the card.",
                "If VIN decoding gives only generic facts, append only the new confirmed facts and avoid repeating what the card already shows.",
                "When you update the card, use update_card or apply.update_card.",
            ]
        )
        if mini_prompt:
            lines.append(f"User mini-prompt: {mini_prompt}")
        if ai_log_tail:
            lines.append("Use the recent autofill feed as continuation context:")
            for entry in ai_log_tail[-8:]:
                if not isinstance(entry, dict):
                    continue
                level = str(entry.get("level", "INFO") or "INFO").strip().upper()[:8]
                message = str(entry.get("message", "") or "").strip()
                if message:
                    lines.append(f"- {level}: {message}")
        return "\n".join(lines)
