from __future__ import annotations

import json
import math
import threading
import uuid
from pathlib import Path
from typing import Any

from ..models import utc_now_iso
from ..storage.file_lock import ProcessFileLock
from ..storage.limited_io import read_text_limited
from .config import (
    get_agent_actions_file,
    get_agent_data_dir,
    get_agent_lock_file,
    get_agent_memory_file,
    get_agent_prompt_file,
    get_agent_runs_file,
    get_agent_schedules_file,
    get_agent_status_file,
    get_agent_tasks_file,
)

DEFAULT_STATUS = {
    "running": False,
    "current_task_id": None,
    "current_run_id": None,
    "last_heartbeat": "",
    "last_run_started_at": "",
    "last_run_finished_at": "",
    "last_error": "",
    "last_scheduler_run_at": "",
    "last_scheduler_success_at": "",
    "last_scheduler_error": "",
    "board_control": {
        "running": False,
        "last_pass_at": "",
        "last_success_at": "",
        "last_error": "",
        "last_baseline_at": "",
        "considered_count": 0,
        "triggered_count": 0,
        "enqueued_count": 0,
        "written_count": 0,
        "error_count": 0,
        "recent_traces": [],
        "card_cache": {},
    },
}

DEFAULT_MAX_FINISHED_TASKS = 300
DEFAULT_MAX_RUNS = 1000
DEFAULT_MAX_ACTIONS = 4000
DEFAULT_COMPACT_THRESHOLD_BYTES = 262_144
MAX_FINISHED_TASKS = 10_000
MAX_RUNS = 20_000
MAX_ACTIONS = 100_000
MAX_COMPACT_THRESHOLD_BYTES = 2 * 1024 * 1024
MAX_TOOL_CALLS = 1_000_000
AGENT_JSON_FILE_MAX_BYTES = 1 * 1024 * 1024
AGENT_TEXT_FILE_MAX_BYTES = 512 * 1024
AGENT_JSONL_TAIL_MAX_BYTES = 4 * 1024 * 1024
AGENT_JSONL_LINE_MAX_BYTES = 2 * 1024 * 1024
AGENT_JSONL_PREVIEW_MAX_CHARS = 600
_JSON_SAFE_MAX_DEPTH = 12


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


class AgentStorage:
    def __init__(
        self,
        base_dir: Path | None = None,
        *,
        max_finished_tasks: int = DEFAULT_MAX_FINISHED_TASKS,
        max_runs: int = DEFAULT_MAX_RUNS,
        max_actions: int = DEFAULT_MAX_ACTIONS,
        compact_threshold_bytes: int = DEFAULT_COMPACT_THRESHOLD_BYTES,
    ) -> None:
        self._base_dir = base_dir or get_agent_data_dir()
        self._lock = threading.RLock()
        self._max_finished_tasks = self._normalize_int(
            max_finished_tasks,
            default=DEFAULT_MAX_FINISHED_TASKS,
            minimum=1,
            maximum=MAX_FINISHED_TASKS,
        )
        self._max_runs = self._normalize_int(
            max_runs,
            default=DEFAULT_MAX_RUNS,
            minimum=1,
            maximum=MAX_RUNS,
        )
        self._max_actions = self._normalize_int(
            max_actions,
            default=DEFAULT_MAX_ACTIONS,
            minimum=1,
            maximum=MAX_ACTIONS,
        )
        self._compact_threshold_bytes = self._normalize_int(
            compact_threshold_bytes,
            default=DEFAULT_COMPACT_THRESHOLD_BYTES,
            minimum=0,
            maximum=MAX_COMPACT_THRESHOLD_BYTES,
        )
        self._prompt_file = self._base_dir / get_agent_prompt_file().name
        self._memory_file = self._base_dir / get_agent_memory_file().name
        self._tasks_file = self._base_dir / get_agent_tasks_file().name
        self._schedules_file = self._base_dir / get_agent_schedules_file().name
        self._status_file = self._base_dir / get_agent_status_file().name
        self._runs_file = self._base_dir / get_agent_runs_file().name
        self._actions_file = self._base_dir / get_agent_actions_file().name
        self._lock_file = self._base_dir / get_agent_lock_file().name
        self._process_lock = ProcessFileLock(self._lock_file)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_json_file(self._tasks_file, [])
        self._ensure_json_file(self._schedules_file, [])
        self._ensure_json_file(self._status_file, DEFAULT_STATUS)
        self._ensure_text_file(self._prompt_file, "")
        self._ensure_text_file(self._memory_file, "")
        self._ensure_text_file(self._runs_file, "")
        self._ensure_text_file(self._actions_file, "")

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def read_prompt_text(self) -> str:
        return self._read_text_or_reset(self._prompt_file, max_bytes=AGENT_TEXT_FILE_MAX_BYTES)

    def write_prompt_text(self, text: str) -> None:
        self._write_text_file(
            self._prompt_file,
            text,
            max_bytes=AGENT_TEXT_FILE_MAX_BYTES,
            label="agent text file",
        )

    def read_memory_text(self) -> str:
        return self._read_text_or_reset(self._memory_file, max_bytes=AGENT_TEXT_FILE_MAX_BYTES)

    def write_memory_text(self, text: str) -> None:
        self._write_text_file(
            self._memory_file,
            text,
            max_bytes=AGENT_TEXT_FILE_MAX_BYTES,
            label="agent text file",
        )

    def read_status(self) -> dict[str, Any]:
        with self._lock, self._process_lock.acquire():
            payload = self._read_json(self._status_file, DEFAULT_STATUS)
            normalized = self._normalize_status_payload(
                payload if isinstance(payload, dict) else {}
            )
            return normalized

    def update_status(self, **updates: Any) -> dict[str, Any]:
        with self._lock, self._process_lock.acquire():
            payload = self._read_json(self._status_file, DEFAULT_STATUS)
            current = self._normalize_status_payload(payload if isinstance(payload, dict) else {})
            board_control_updates = updates.get("board_control")
            if isinstance(board_control_updates, dict):
                merged_board_control = dict(
                    current.get("board_control")
                    if isinstance(current.get("board_control"), dict)
                    else {}
                )
                merged_board_control.update(board_control_updates)
                updates = dict(updates)
                updates["board_control"] = merged_board_control
            current.update(updates)
            self._write_json(self._status_file, current)
            return current

    def heartbeat(self, *, task_id: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        return self.update_status(
            running=bool(task_id),
            current_task_id=task_id,
            current_run_id=run_id,
            last_heartbeat=utc_now_iso(),
        )

    def enqueue_task(
        self,
        *,
        task_text: str,
        source: str = "manual",
        mode: str = "manual",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = {
            "id": f"agtask_{uuid.uuid4().hex[:12]}",
            "created_at": utc_now_iso(),
            "started_at": "",
            "finished_at": "",
            "status": "pending",
            "source": str(source or "manual"),
            "mode": str(mode or "manual"),
            "task_text": str(task_text or "").strip(),
            "metadata": metadata or {},
            "run_id": "",
            "summary": "",
            "result": "",
            "display": {},
            "error": "",
            "tool_calls": 0,
        }
        with self._lock, self._process_lock.acquire():
            tasks = self._read_tasks_locked()
            tasks.append(task)
            tasks = self._compact_tasks_locked(tasks)
            self._write_json(self._tasks_file, tasks)
        return task

    def list_tasks(
        self, *, limit: int = 50, statuses: set[str] | None = None
    ) -> list[dict[str, Any]]:
        with self._lock, self._process_lock.acquire():
            tasks = self._read_tasks_locked()
        if statuses:
            tasks = [task for task in tasks if str(task.get("status", "")).strip() in statuses]
        tasks.sort(
            key=lambda item: (str(item.get("created_at", "")), str(item.get("id", ""))),
            reverse=True,
        )
        return tasks[:limit]

    def list_schedules(self) -> list[dict[str, Any]]:
        with self._lock, self._process_lock.acquire():
            items = self._read_schedules_locked()
        items.sort(
            key=lambda item: (str(item.get("created_at", "")), str(item.get("id", ""))),
            reverse=True,
        )
        return items

    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        normalized_id = str(schedule_id or "").strip()
        if not normalized_id:
            return None
        with self._lock, self._process_lock.acquire():
            schedules = self._read_schedules_locked()
            for item in schedules:
                if str(item.get("id", "")).strip() == normalized_id:
                    return dict(item)
        return None

    def upsert_schedule(self, payload: dict[str, Any]) -> dict[str, Any]:
        schedule_id = str(payload.get("id", "") or "").strip()
        with self._lock, self._process_lock.acquire():
            schedules = self._read_schedules_locked()
            updated = dict(payload)
            if schedule_id:
                for index, item in enumerate(schedules):
                    if str(item.get("id", "")).strip() != schedule_id:
                        continue
                    schedules[index] = updated
                    self._write_json(self._schedules_file, schedules)
                    return updated
            schedules.append(updated)
            self._write_json(self._schedules_file, schedules)
            return updated

    def update_schedule(self, schedule_id: str, **updates: Any) -> dict[str, Any]:
        normalized_id = str(schedule_id or "").strip()
        if not normalized_id:
            raise KeyError("Unknown schedule task: ")
        with self._lock, self._process_lock.acquire():
            schedules = self._read_schedules_locked()
            for index, item in enumerate(schedules):
                if str(item.get("id", "")).strip() != normalized_id:
                    continue
                updated = dict(item)
                updated.update(updates)
                schedules[index] = updated
                self._write_json(self._schedules_file, schedules)
                return updated
        raise KeyError(f"Unknown schedule task: {normalized_id}")

    def delete_schedule(self, schedule_id: str) -> bool:
        normalized_id = str(schedule_id or "").strip()
        if not normalized_id:
            return False
        with self._lock, self._process_lock.acquire():
            schedules = self._read_schedules_locked()
            kept = [item for item in schedules if str(item.get("id", "")).strip() != normalized_id]
            if len(kept) == len(schedules):
                return False
            self._write_json(self._schedules_file, kept)
            return True

    def has_active_task_for_schedule(self, schedule_id: str) -> bool:
        normalized_id = str(schedule_id or "").strip()
        if not normalized_id:
            return False
        with self._lock, self._process_lock.acquire():
            tasks = self._read_tasks_locked()
        for task in tasks:
            if str(task.get("status", "")).strip() not in {"pending", "running"}:
                continue
            metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
            if str(metadata.get("scheduled_task_id", "")).strip() == normalized_id:
                return True
        return False

    def has_active_task_for_card(self, card_id: str, *, purpose: str | None = None) -> bool:
        normalized_id = str(card_id or "").strip()
        normalized_purpose = str(purpose or "").strip().lower()
        if not normalized_id:
            return False
        with self._lock, self._process_lock.acquire():
            tasks = self._read_tasks_locked()
        for task in tasks:
            if str(task.get("status", "")).strip() not in {"pending", "running"}:
                continue
            metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
            context = metadata.get("context") if isinstance(metadata.get("context"), dict) else {}
            metadata_card_id = str(context.get("card_id") or metadata.get("card_id") or "").strip()
            if metadata_card_id != normalized_id:
                continue
            if not normalized_purpose:
                return True
            task_purpose = str(metadata.get("purpose", "") or "").strip().lower()
            if task_purpose == normalized_purpose:
                return True
        return False

    def has_active_task_for_schedule_card(self, schedule_id: str, card_id: str) -> bool:
        normalized_schedule_id = str(schedule_id or "").strip()
        normalized_card_id = str(card_id or "").strip()
        if not normalized_schedule_id or not normalized_card_id:
            return False
        with self._lock, self._process_lock.acquire():
            tasks = self._read_tasks_locked()
        for task in tasks:
            if str(task.get("status", "")).strip() not in {"pending", "running"}:
                continue
            metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
            if str(metadata.get("scheduled_task_id", "")).strip() != normalized_schedule_id:
                continue
            context = metadata.get("context") if isinstance(metadata.get("context"), dict) else {}
            if str(context.get("card_id", "")).strip() == normalized_card_id:
                return True
        return False

    def claim_next_task(self) -> dict[str, Any] | None:
        with self._lock, self._process_lock.acquire():
            tasks = self._read_tasks_locked()
            pending = [
                (index, task)
                for index, task in enumerate(tasks)
                if str(task.get("status", "")).strip() == "pending"
            ]
            if not pending:
                return None
            pending.sort(
                key=lambda item: (str(item[1].get("created_at", "")), str(item[1].get("id", "")))
            )
            index, task = pending[0]
            task = dict(task)
            task["status"] = "running"
            task["started_at"] = utc_now_iso()
            tasks[index] = task
            tasks = self._compact_tasks_locked(tasks)
            self._write_json(self._tasks_file, tasks)
            return task

    def complete_task(
        self,
        *,
        task_id: str,
        run_id: str,
        summary: str,
        result: str,
        display: dict[str, Any] | None,
        tool_calls: int,
    ) -> dict[str, Any]:
        return self._finish_task(
            task_id=task_id,
            run_id=run_id,
            status="completed",
            summary=summary,
            result=result,
            display=display,
            error="",
            tool_calls=tool_calls,
        )

    def fail_task(
        self,
        *,
        task_id: str,
        run_id: str,
        error: str,
        tool_calls: int,
    ) -> dict[str, Any]:
        return self._finish_task(
            task_id=task_id,
            run_id=run_id,
            status="failed",
            summary="",
            result="",
            display={},
            error=error,
            tool_calls=tool_calls,
        )

    def append_run(self, payload: dict[str, Any]) -> None:
        self._append_jsonl(self._runs_file, payload, retention=self._max_runs)

    def append_action(self, payload: dict[str, Any]) -> None:
        self._append_jsonl(self._actions_file, payload, retention=self._max_actions)

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._read_jsonl(self._runs_file, limit=limit)

    def list_actions(
        self,
        *,
        limit: int = 200,
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        items = self._read_jsonl(self._actions_file, limit=max(limit * 4, limit))
        if run_id:
            items = [item for item in items if item.get("run_id") == run_id]
        if task_id:
            items = [item for item in items if item.get("task_id") == task_id]
        return items[:limit]

    def _finish_task(
        self,
        *,
        task_id: str,
        run_id: str,
        status: str,
        summary: str,
        result: str,
        display: dict[str, Any] | None,
        error: str,
        tool_calls: int,
    ) -> dict[str, Any]:
        with self._lock, self._process_lock.acquire():
            tasks = self._read_tasks_locked()
            updated: dict[str, Any] | None = None
            for index, task in enumerate(tasks):
                if task.get("id") != task_id:
                    continue
                updated = dict(task)
                updated["status"] = status
                updated["finished_at"] = utc_now_iso()
                updated["run_id"] = run_id
                updated["summary"] = summary
                updated["result"] = result
                updated["display"] = display if isinstance(display, dict) else {}
                updated["error"] = error
                updated["tool_calls"] = self._normalize_int(
                    tool_calls,
                    default=0,
                    minimum=0,
                    maximum=MAX_TOOL_CALLS,
                )
                tasks[index] = updated
                break
            if updated is None:
                raise KeyError(f"Unknown agent task: {task_id}")
            tasks = self._compact_tasks_locked(tasks)
            self._write_json(self._tasks_file, tasks)
            return updated

    def _read_tasks_locked(self) -> list[dict[str, Any]]:
        payload = self._read_json(self._tasks_file, [])
        return (
            [item for item in payload if isinstance(item, dict)]
            if isinstance(payload, list)
            else []
        )

    def _read_schedules_locked(self) -> list[dict[str, Any]]:
        payload = self._read_json(self._schedules_file, [])
        return (
            [item for item in payload if isinstance(item, dict)]
            if isinstance(payload, list)
            else []
        )

    def _compact_tasks_locked(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        active_tasks = [
            task for task in tasks if str(task.get("status", "")).strip() in {"pending", "running"}
        ]
        finished_tasks = [
            task
            for task in tasks
            if str(task.get("status", "")).strip() not in {"pending", "running"}
        ]
        if len(finished_tasks) > self._max_finished_tasks:
            finished_keep_ids = {id(task) for task in finished_tasks[-self._max_finished_tasks :]}
            finished_tasks = [task for task in finished_tasks if id(task) in finished_keep_ids]
        if len(active_tasks) == len(tasks) and len(finished_tasks) == 0:
            return tasks
        retained_ids = {id(task) for task in active_tasks} | {id(task) for task in finished_tasks}
        return [task for task in tasks if id(task) in retained_ids]

    def _append_jsonl(self, path: Path, payload: dict[str, Any], *, retention: int) -> None:
        line = self._jsonl_line(payload)
        with self._lock, self._process_lock.acquire():
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.write("\n")
            if (
                self._compact_threshold_bytes
                and path.stat().st_size > self._compact_threshold_bytes
            ):
                self._compact_jsonl_locked(path, retention=retention)

    def _compact_jsonl_locked(self, path: Path, *, retention: int) -> None:
        if not path.exists():
            return
        try:
            lines = self._read_jsonl_tail_lines(path)
        except UnicodeDecodeError:
            self._replace_with_corrupted_backup(path)
            self._write_text_file(path, "")
            return
        if len(lines) <= retention:
            return
        kept_lines = [line for line in lines if line.strip()][-retention:]
        self._write_text_file(path, "\n".join(kept_lines) + ("\n" if kept_lines else ""))

    def _jsonl_line(self, payload: dict[str, Any]) -> str:
        safe_payload = _json_safe_value(payload)
        line = json.dumps(safe_payload, ensure_ascii=False, allow_nan=False)
        if len(line.encode("utf-8")) <= AGENT_JSONL_LINE_MAX_BYTES:
            return line
        fallback = self._oversized_jsonl_fallback(payload, original_bytes=len(line.encode("utf-8")))
        compact_line = json.dumps(fallback, ensure_ascii=False, allow_nan=False)
        if len(compact_line.encode("utf-8")) <= AGENT_JSONL_LINE_MAX_BYTES:
            return compact_line
        return json.dumps(
            {
                "id": str(payload.get("id") or ""),
                "task_id": str(payload.get("task_id") or ""),
                "run_id": str(payload.get("run_id") or ""),
                "truncated": True,
                "truncation_reason": "agent JSONL record exceeded line size limit",
                "original_bytes": len(line.encode("utf-8")),
            },
            ensure_ascii=False,
            allow_nan=False,
        )

    def _oversized_jsonl_fallback(
        self, payload: dict[str, Any], *, original_bytes: int
    ) -> dict[str, Any]:
        fallback: dict[str, Any] = {}
        for key in (
            "id",
            "task_id",
            "run_id",
            "status",
            "kind",
            "tool",
            "step",
            "level",
            "phase",
            "source",
            "mode",
            "model",
            "tool_calls",
            "started_at",
            "finished_at",
        ):
            if key in payload:
                fallback[key] = _json_safe_value(payload.get(key))
        for key in ("task_text", "summary", "result", "error", "message", "reason"):
            preview = self._preview_jsonl_value(payload.get(key))
            if preview:
                fallback[key] = preview
        for key in ("args", "metadata", "display", "orchestration", "result_preview"):
            preview = self._preview_jsonl_value(payload.get(key))
            if preview:
                fallback[f"{key}_preview"] = preview
        fallback["truncated"] = True
        fallback["truncation_reason"] = "agent JSONL record exceeded line size limit"
        fallback["original_bytes"] = max(0, int(original_bytes))
        return fallback

    def _preview_jsonl_value(self, value: Any) -> str:
        if value in (None, "", [], {}):
            return ""
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(_json_safe_value(value), ensure_ascii=False, allow_nan=False)
        if len(text) <= AGENT_JSONL_PREVIEW_MAX_CHARS:
            return text
        return f"{text[:AGENT_JSONL_PREVIEW_MAX_CHARS]}... [truncated]"

    def _read_jsonl(self, path: Path, *, limit: int) -> list[dict[str, Any]]:
        normalized_limit = self._normalize_int(
            limit,
            default=0,
            minimum=0,
            maximum=MAX_ACTIONS,
        )
        if normalized_limit <= 0:
            return []
        with self._lock, self._process_lock.acquire():
            if not path.exists():
                return []
            try:
                lines = self._read_jsonl_tail_lines(path)
            except UnicodeDecodeError:
                self._replace_with_corrupted_backup(path)
                self._write_text_file(path, "")
                return []
        items: list[dict[str, Any]] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                payload = json.loads(line, parse_constant=_reject_json_constant)
            except (ValueError, RecursionError):
                continue
            if isinstance(payload, dict):
                items.append(payload)
            if len(items) >= normalized_limit:
                break
        return items

    def _ensure_json_file(self, path: Path, default_payload: Any) -> None:
        if path.exists():
            return
        self._write_json(path, default_payload)

    def _ensure_text_file(self, path: Path, default_text: str) -> None:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_text_file(path, default_text)

    def _read_json(self, path: Path, default_payload: Any) -> Any:
        if not path.exists():
            return default_payload
        try:
            payload = json.loads(
                self._read_json_text(path),
                parse_constant=_reject_json_constant,
            )
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError):
            self._replace_with_corrupted_backup(path)
            self._write_json(path, default_payload)
            return default_payload
        if isinstance(default_payload, (dict, list)) and not isinstance(
            payload, type(default_payload)
        ):
            self._replace_with_corrupted_backup(path)
            self._write_json(path, default_payload)
            return default_payload
        return payload

    def _read_json_text(self, path: Path) -> str:
        return read_text_limited(
            path,
            max_bytes=AGENT_JSON_FILE_MAX_BYTES,
            label="agent JSON state file",
        )

    def _read_text_or_reset(self, path: Path, *, max_bytes: int) -> str:
        with self._lock, self._process_lock.acquire():
            if not path.exists():
                return ""
            try:
                return self._read_text_bounded(path, max_bytes=max_bytes)
            except (OSError, UnicodeDecodeError, ValueError):
                if path.exists():
                    self._replace_with_corrupted_backup(path)
                self._write_text_file(path, "", max_bytes=max_bytes, label="agent text file")
                return ""

    def _read_text_bounded(self, path: Path, *, max_bytes: int) -> str:
        return read_text_limited(path, max_bytes=max_bytes, label="agent text file")

    def _read_jsonl_tail_lines(self, path: Path) -> list[str]:
        size = path.stat().st_size
        if size <= AGENT_JSONL_TAIL_MAX_BYTES:
            return read_text_limited(
                path,
                max_bytes=AGENT_JSONL_TAIL_MAX_BYTES,
                label="agent JSONL file",
            ).splitlines()
        with path.open("rb") as handle:
            handle.seek(size - AGENT_JSONL_TAIL_MAX_BYTES)
            discarded = handle.readline(AGENT_JSONL_TAIL_MAX_BYTES)
            if not discarded.endswith(b"\n"):
                return []
            raw = handle.read(AGENT_JSONL_TAIL_MAX_BYTES + 1)
        return raw[:AGENT_JSONL_TAIL_MAX_BYTES].decode("utf-8").splitlines()

    def _write_json(self, path: Path, payload: Any) -> None:
        text = json.dumps(
            _json_safe_value(payload),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        if len(text.encode("utf-8")) > AGENT_JSON_FILE_MAX_BYTES:
            raise ValueError("agent JSON state file is too large")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(text, encoding="utf-8")
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)

    def _write_text_file(
        self,
        path: Path,
        text: str,
        *,
        max_bytes: int | None = None,
        label: str = "agent text file",
    ) -> None:
        if max_bytes is not None and len(text.encode("utf-8")) > max_bytes:
            raise ValueError(f"{label} is too large")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(text, encoding="utf-8")
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)

    def _replace_with_corrupted_backup(self, path: Path) -> Path:
        backup = self._corrupted_backup_path(path)
        path.replace(backup)
        return backup

    def _corrupted_backup_path(self, path: Path) -> Path:
        backup = path.with_name(f"{path.stem}.corrupted{path.suffix}")
        if not backup.exists():
            return backup
        for index in range(2, 1000):
            candidate = path.with_name(f"{path.stem}.corrupted-{index}{path.suffix}")
            if not candidate.exists():
                return candidate
        return path.with_name(f"{path.stem}.corrupted-{uuid.uuid4().hex}{path.suffix}")

    def _normalize_int(
        self,
        value: Any,
        *,
        default: int,
        minimum: int,
        maximum: int | None = None,
    ) -> int:
        if isinstance(value, bool):
            numeric = float(default)
        else:
            try:
                numeric = float(value)
            except (OverflowError, TypeError, ValueError):
                numeric = float(default)
        if not math.isfinite(numeric) or not numeric.is_integer():
            numeric = float(default)
        if numeric < minimum:
            return minimum
        if maximum is not None and numeric > maximum:
            return maximum
        return int(numeric)

    def _normalize_status_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(DEFAULT_STATUS)
        normalized.update(payload if isinstance(payload, dict) else {})
        board_control = payload.get("board_control") if isinstance(payload, dict) else {}
        normalized_board_control = dict(DEFAULT_STATUS["board_control"])
        if isinstance(board_control, dict):
            normalized_board_control.update(board_control)
        normalized["board_control"] = normalized_board_control
        return normalized


def _json_safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > _JSON_SAFE_MAX_DEPTH:
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item, depth=depth + 1) for item in value]
    return str(value)
