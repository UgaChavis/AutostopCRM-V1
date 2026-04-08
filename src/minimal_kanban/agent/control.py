from __future__ import annotations

from typing import Any

from .config import get_agent_board_api_url, get_agent_enabled, get_agent_name, get_agent_openai_model
from .storage import AgentStorage


class AgentControlService:
    def __init__(self, storage: AgentStorage) -> None:
        self._storage = storage

    def agent_status(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        pending_total = len(self._storage.list_tasks(limit=1000, statuses={"pending"}))
        running_total = len(self._storage.list_tasks(limit=1000, statuses={"running"}))
        status = self._storage.read_status()
        return {
            "agent": {
                "name": get_agent_name(),
                "enabled": get_agent_enabled(),
                "model": get_agent_openai_model(),
                "board_api_url": get_agent_board_api_url() or "",
            },
            "status": status,
            "queue": {
                "pending_total": pending_total,
                "running_total": running_total,
            },
            "recent_runs": self._storage.list_runs(limit=min(max(int(payload.get("run_limit", 10) or 10), 1), 50)),
        }

    def agent_enqueue_task(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        task_text = str(payload.get("task_text", "") or "").strip()
        if not task_text:
            raise ValueError("task_text is required")
        task = self._storage.enqueue_task(
            task_text=task_text,
            source=str(payload.get("source", "manual") or "manual"),
            mode=str(payload.get("mode", "manual") or "manual"),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
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
