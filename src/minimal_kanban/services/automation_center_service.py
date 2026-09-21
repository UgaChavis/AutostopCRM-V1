from __future__ import annotations

import json
import re
import socket
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..config import get_automation_control_socket
from .errors import ServiceError

AUTOMATION_CONTROL_PROTOCOL = "autostop.manager.automation-control.v1"
AUTOMATION_CONTROL_MAX_REQUEST_BYTES = 64 * 1024
AUTOMATION_CONTROL_MAX_RESPONSE_BYTES = 1024 * 1024
AUTOMATION_CONTROL_TIMEOUT_SECONDS = 10.0

_CONTROL_OPERATIONS = frozenset(
    {
        "preview",
        "create_from_template",
        "set_enabled",
        "set_schedule",
        "run_now",
        "test_notification",
        "archive",
    }
)
_COMMAND_ID_PATTERN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}\Z")
_SAFE_CONTROL_KEYS = frozenset(
    {
        "command_id",
        "idempotency_key",
        "expected_revision",
        "job_id",
        "timer_id",
        "template_id",
        "enabled",
        "schedule",
        "name",
        "confirm_token",
        "target_operation",
        "target_payload",
    }
)
_SAFE_JOB_KEYS = frozenset(
    {
        "job_id",
        "id",
        "name",
        "template_id",
        "kind",
        "desired_state",
        "actual_state",
        "revision",
        "applied_revision",
        "reconcile_state",
        "schedule",
        "last_run_at",
        "next_run_at",
        "last_attempt_at",
        "heartbeat_at",
        "lag_seconds",
        "status_color",
        "error_code",
        "applying",
        "archived",
    }
)
_SAFE_TIMER_KEYS = frozenset(
    {
        "unit",
        "id",
        "timer_id",
        "name",
        "label",
        "control_mode",
        "desired_state",
        "actual_state",
        "enabled",
        "active",
        "schedule",
        "period_minutes",
        "next_run_at",
        "last_run_at",
        "status_color",
        "error_code",
        "mutable",
        "locked",
        "lock_reason",
        "detail",
        "revision",
        "reconcile_state",
        "actual_period_minutes",
    }
)
_SAFE_READINESS_KEYS = frozenset({"id", "label", "state", "detail", "error_code"})
_SAFE_TEMPLATE_KEYS = frozenset(
    {
        "id",
        "template_id",
        "name",
        "description",
        "singleton",
        "default_every_minutes",
        "min_every_minutes",
        "max_every_minutes",
        "default_timezone",
        "default_active_window",
        "capabilities",
    }
)


class AutomationControlError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class AutomationControlClient:
    """Bounded client for the Manager-owned automation control socket."""

    def __init__(
        self,
        socket_path: Path | None = None,
        *,
        timeout_seconds: float = AUTOMATION_CONTROL_TIMEOUT_SECONDS,
    ) -> None:
        self.socket_path = Path(socket_path or get_automation_control_socket())
        self.timeout_seconds = max(0.1, min(float(timeout_seconds), 10.0))

    def request(
        self,
        operation: str,
        payload: Mapping[str, Any] | None,
        *,
        actor: Mapping[str, Any],
    ) -> dict[str, Any]:
        request_id = str(uuid4())
        safe_payload = dict(payload or {})
        command_id = str(safe_payload.pop("command_id", "") or "").strip()
        # Command metadata belongs to the protocol envelope.  Passing it through
        # to an operation payload breaks Manager's strict per-operation schema
        # (notably create_from_template) and weakens the single idempotency key
        # contract shared by both repositories.
        safe_payload.pop("idempotency_key", None)
        expected_revision = safe_payload.pop("expected_revision", None)
        envelope: dict[str, Any] = {
            "protocol": AUTOMATION_CONTROL_PROTOCOL,
            "request_id": request_id,
            "operation": operation,
            "actor": {
                "kind": "crm_operator",
                "id": str(actor.get("id") or "operator")[:128],
                "is_admin": bool(actor.get("is_admin")),
            },
            "payload": safe_payload,
        }
        if command_id:
            envelope["idempotency_key"] = command_id[:128]
        if expected_revision is not None:
            envelope["expected_revision"] = expected_revision
        encoded = (
            json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        )
        if len(encoded) > AUTOMATION_CONTROL_MAX_REQUEST_BYTES:
            raise AutomationControlError(
                "automation_request_too_large",
                "Команда Центра автоматизаций превышает допустимый размер.",
                status_code=400,
            )
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(str(self.socket_path))
                connection.sendall(encoded)
                response = bytearray()
                while len(response) <= AUTOMATION_CONTROL_MAX_RESPONSE_BYTES:
                    chunk = connection.recv(min(65536, AUTOMATION_CONTROL_MAX_RESPONSE_BYTES + 1))
                    if not chunk:
                        break
                    response.extend(chunk)
                    if b"\n" in chunk:
                        break
        except (OSError, TimeoutError) as exc:
            raise AutomationControlError(
                "automation_control_unavailable",
                "Контроллер автоматизаций временно недоступен.",
                status_code=503,
            ) from exc
        if len(response) > AUTOMATION_CONTROL_MAX_RESPONSE_BYTES:
            raise AutomationControlError(
                "automation_control_protocol_error",
                "Контроллер автоматизаций вернул слишком большой ответ.",
            )
        raw_line = bytes(response).split(b"\n", 1)[0]
        try:
            decoded = json.loads(raw_line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AutomationControlError(
                "automation_control_protocol_error",
                "Контроллер автоматизаций вернул некорректный ответ.",
            ) from exc
        if (
            not isinstance(decoded, dict)
            or decoded.get("protocol") != AUTOMATION_CONTROL_PROTOCOL
            or decoded.get("request_id") != request_id
            or not isinstance(decoded.get("ok"), bool)
        ):
            raise AutomationControlError(
                "automation_control_protocol_error",
                "Ответ контроллера автоматизаций не прошёл проверку протокола.",
            )
        if not decoded["ok"]:
            error = decoded.get("error") if isinstance(decoded.get("error"), dict) else {}
            code = str(error.get("code") or "automation_control_failed")[:96]
            status_code = (
                409 if code in {"revision_conflict", "automation_revision_conflict"} else 422
            )
            if code in {"forbidden", "blocked", "not_authorized"}:
                status_code = 403
            raise AutomationControlError(
                "automation_revision_conflict"
                if code in {"revision_conflict", "automation_revision_conflict"}
                else code,
                str(error.get("message") or "Команда контроллера автоматизаций не выполнена.")[
                    :320
                ],
                status_code=status_code,
            )
        data = decoded.get("data")
        if not isinstance(data, dict):
            raise AutomationControlError(
                "automation_control_protocol_error",
                "Контроллер автоматизаций не вернул объект данных.",
            )
        return data


def _safe_mapping(value: object, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {key: value[key] for key in allowed if key in value}


def _safe_rows(value: object, allowed: frozenset[str], *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [_safe_mapping(row, allowed) for row in value[:limit] if isinstance(row, Mapping)]


class AutomationCenterService:
    """CRM adapter: derives actors from trusted sessions and never owns scheduler state."""

    def __init__(self, client: AutomationControlClient | None = None) -> None:
        self._client = client or AutomationControlClient()

    @staticmethod
    def _request(payload: dict[str, Any] | None) -> dict[str, Any]:
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise ServiceError(
                "validation_error", "Параметры должны быть JSON-объектом.", status_code=400
            )
        return payload

    @staticmethod
    def _session(payload: Mapping[str, Any]) -> dict[str, Any]:
        session = payload.get("_operator_session")
        if not isinstance(session, Mapping):
            raise ServiceError("unauthorized", "Нужен вход оператора.", status_code=401)
        return dict(session)

    @staticmethod
    def _actor(session: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": str(session.get("username") or session.get("employee_id") or "operator"),
            "is_admin": bool(session.get("is_admin")),
        }

    @staticmethod
    def _translate(exc: AutomationControlError) -> ServiceError:
        return ServiceError(exc.code, exc.message, status_code=exc.status_code)

    @staticmethod
    def _status_projection(data: Mapping[str, Any], *, can_manage: bool) -> dict[str, Any]:
        controller = _safe_mapping(
            data.get("controller") if isinstance(data.get("controller"), Mapping) else data,
            frozenset(
                {
                    "state",
                    "actual_state",
                    "heartbeat_at",
                    "stale",
                    "status_color",
                    "hold",
                    "schema_version",
                    "error_code",
                    "overall_state",
                    "enabled_jobs",
                    "error_count",
                    "applying_count",
                }
            ),
        )
        hold_source = data.get("global_hold")
        hold = _safe_mapping(
            hold_source,
            frozenset({"enabled", "revision", "updated_at", "error_code"}),
        )
        controller["hold"] = hold
        readiness_source = data.get("readiness")
        if isinstance(readiness_source, Mapping):
            checks = readiness_source.get("checks")
            if isinstance(checks, Mapping):
                readiness_source = [
                    {
                        "id": str(check_id),
                        "label": str(check_id).replace("_", " "),
                        "state": str(check_state),
                        "detail": "",
                    }
                    for check_id, check_state in checks.items()
                ]
            else:
                readiness_source = checks
        timer_rows: list[dict[str, Any]] = []
        raw_timers = data.get("system_timers") or data.get("timers")
        if isinstance(raw_timers, list):
            for raw_timer in raw_timers[:32]:
                if not isinstance(raw_timer, Mapping):
                    continue
                timer = _safe_mapping(raw_timer, _SAFE_TIMER_KEYS)
                timer["id"] = str(raw_timer.get("timer_id") or raw_timer.get("id") or "")
                timer["unit"] = str(raw_timer.get("unit_name") or raw_timer.get("unit") or "")
                timer["name"] = str(
                    raw_timer.get("name") or raw_timer.get("unit_name") or timer["id"]
                )
                timer["mutable"] = str(raw_timer.get("control_mode") or "") == "managed"
                adopted = raw_timer.get("adopted_state")
                if isinstance(adopted, Mapping):
                    timer["enabled"] = str(adopted.get("unit_file_state") or "") in {
                        "enabled",
                        "enabled-runtime",
                    }
                    timer["active"] = str(adopted.get("active_state") or "") == "active"
                    timer["next_run_at"] = adopted.get("next_elapse")
                timer["desired_state"] = str(
                    raw_timer.get("desired_state") or ("on" if timer.get("enabled") else "off")
                )
                if not timer.get("actual_state"):
                    timer["actual_state"] = (
                        "error"
                        if raw_timer.get("inspection_ok") is False
                        else "on"
                        if timer.get("active")
                        else "off"
                    )
                if not isinstance(timer.get("schedule"), Mapping):
                    timer["schedule"] = {
                        "kind": "interval",
                        "every_minutes": timer.get("period_minutes"),
                    }
                timer["locked"] = bool(raw_timer.get("locked")) or not timer["mutable"]
                timer_rows.append(timer)
        template_rows: list[dict[str, Any]] = []
        raw_templates = data.get("templates")
        if isinstance(raw_templates, list):
            for raw_template in raw_templates[:32]:
                if not isinstance(raw_template, Mapping):
                    continue
                template = _safe_mapping(raw_template, _SAFE_TEMPLATE_KEYS)
                default_schedule = raw_template.get("default_schedule")
                limits = raw_template.get("limits")
                if isinstance(default_schedule, Mapping):
                    template["default_every_minutes"] = default_schedule.get("every_minutes")
                    template["default_timezone"] = default_schedule.get("timezone")
                    template["default_active_window"] = default_schedule.get(
                        "active_window", "24/7"
                    )
                if isinstance(limits, Mapping):
                    template["min_every_minutes"] = limits.get("minimum_every_minutes")
                    template["max_every_minutes"] = limits.get("maximum_every_minutes")
                template_rows.append(template)
        jobs = _safe_rows(data.get("jobs"), _SAFE_JOB_KEYS, limit=64)
        readiness = _safe_rows(readiness_source, _SAFE_READINESS_KEYS, limit=64)
        error_states = {"error", "failed", "blocked", "unhealthy"}
        applying_states = {"applying", "pending", "starting", "stopping", "backoff", "queued"}
        ready_states = {"ready", "ok", "healthy", "pass", "available", "configured"}
        settled_actual_states = {
            "ready",
            "idle",
            "scheduled",
            "healthy",
            "ok",
            "pass",
            "available",
            "running",
            "active",
            "success",
            "on",
            "enabled",
            "off",
            "disabled",
            "stopped",
            "paused",
            "inactive",
        }
        enabled_jobs = sum(
            str(job.get("desired_state") or "").casefold() in {"on", "enabled", "true"}
            for job in jobs
        ) + sum(
            str(timer.get("desired_state") or "").casefold() in {"on", "enabled", "true"}
            for timer in timer_rows
        )
        error_count = (
            sum(str(job.get("actual_state") or "").casefold() in error_states for job in jobs)
            + sum(
                str(timer.get("actual_state") or "").casefold() in error_states
                or (
                    bool(timer.get("error_code"))
                    and str(timer.get("error_code")) != "system_timer_drift"
                )
                for timer in timer_rows
            )
            + sum(str(check.get("state") or "").casefold() in error_states for check in readiness)
        )
        applying_count = (
            sum(
                str(job.get("actual_state") or "").casefold() in applying_states
                or str(job.get("reconcile_state") or "").casefold() in {"pending", "drift"}
                or (
                    type(job.get("revision")) is int
                    and type(job.get("applied_revision")) is int
                    and job["revision"] != job["applied_revision"]
                )
                or str(job.get("actual_state") or "unknown").casefold()
                not in settled_actual_states | applying_states | error_states
                for job in jobs
            )
            + sum(
                str(timer.get("actual_state") or "").casefold() in applying_states
                or str(timer.get("reconcile_state") or "").casefold() == "drift"
                or str(timer.get("actual_state") or "unknown").casefold()
                not in settled_actual_states | applying_states | error_states
                for timer in timer_rows
            )
            + sum(
                str(check.get("state") or "unknown").casefold() not in ready_states | error_states
                for check in readiness
            )
        )
        controller_state = str(controller.get("state") or "unknown").casefold()
        if controller_state in error_states or error_count:
            overall_state = "error"
        elif hold.get("enabled") is True or controller_state == "held" or applying_count:
            overall_state = "applying"
        elif controller_state in {"stale", "offline", "unavailable", "unknown"}:
            overall_state = "stale"
        else:
            overall_state = "on" if enabled_jobs else "off"
        controller.update(
            overall_state=overall_state,
            enabled_jobs=enabled_jobs,
            error_count=error_count,
            applying_count=applying_count,
        )
        if not can_manage:
            jobs = []
            timer_rows = []
            readiness = []
            template_rows = []
        return {
            "format": "crm_automation_center_status_v1",
            "generated_at": str(data.get("generated_at") or datetime.now(UTC).isoformat()),
            "can_manage": can_manage,
            "controller": controller,
            "jobs": jobs,
            "system_timers": timer_rows,
            "readiness": readiness,
            "templates": template_rows,
        }

    def status(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = self._request(payload)
        session = self._session(request)
        can_manage = bool(session.get("is_admin"))
        try:
            data = self._client.request(
                "status",
                {"view": "admin" if can_manage else "operator"},
                actor=self._actor(session),
            )
        except AutomationControlError as exc:
            raise self._translate(exc) from exc
        return self._status_projection(data, can_manage=can_manage)

    def control(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = self._request(payload)
        session = self._session(request)
        if not session.get("is_admin"):
            raise ServiceError("forbidden", "Нужны права администратора.", status_code=403)
        operation = str(request.get("operation") or "").strip()
        if operation not in _CONTROL_OPERATIONS:
            raise ServiceError(
                "automation_operation_not_allowed",
                "Эта операция не разрешена Центром автоматизаций.",
                status_code=400,
            )
        command_id = str(request.get("command_id") or "").strip()
        if not _COMMAND_ID_PATTERN.fullmatch(command_id):
            raise ServiceError(
                "validation_error",
                "command_id должен быть непустым техническим идентификатором.",
                status_code=400,
                details={"field": "command_id"},
            )
        safe_payload = {key: request[key] for key in _SAFE_CONTROL_KEYS if key in request}
        safe_payload["command_id"] = command_id
        try:
            result = self._client.request(
                operation,
                safe_payload,
                actor=self._actor(session),
            )
        except AutomationControlError as exc:
            raise self._translate(exc) from exc
        return {
            "format": "crm_automation_center_control_v1",
            "operation": operation,
            "command_id": command_id,
            "result": result,
        }
