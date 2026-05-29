from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from .. import models as model_helpers
from ..models import business_timezone, normalize_text, parse_datetime

EMPLOYEE_ACTIVE_PERIODS_LIMIT = 20


def _period_sort_key(period: dict[str, str]) -> datetime:
    parsed = parse_datetime(period.get("start_at"))
    return parsed or datetime.min.replace(tzinfo=UTC)


def normalized_employee_active_periods(
    value: Any,
    *,
    created_at: str,
    updated_at: str,
    is_active: bool,
) -> list[dict[str, str]]:
    periods: list[dict[str, str]] = []
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            start_at = normalize_text(item.get("start_at"), default="", limit=40)
            if parse_datetime(start_at) is None:
                continue
            end_at = normalize_text(item.get("end_at"), default="", limit=40)
            if end_at and parse_datetime(end_at) is None:
                end_at = ""
            periods.append({"start_at": start_at, "end_at": end_at})
            if len(periods) >= EMPLOYEE_ACTIVE_PERIODS_LIMIT:
                break
    if not periods:
        fallback_end = "" if is_active else (updated_at or created_at)
        periods.append({"start_at": created_at, "end_at": fallback_end})
    periods.sort(key=_period_sort_key)
    return periods


def employee_active_periods_after_state_change(
    employee: dict[str, Any],
    *,
    next_is_active: bool,
    changed_at: str,
) -> list[dict[str, str]]:
    periods = [
        {"start_at": item["start_at"], "end_at": item.get("end_at", "")}
        for item in normalized_employee_active_periods(
            employee.get("active_periods"),
            created_at=employee.get("created_at") or changed_at,
            updated_at=employee.get("updated_at") or changed_at,
            is_active=bool(employee.get("is_active", True)),
        )
    ]
    if next_is_active:
        if not periods or periods[-1].get("end_at"):
            periods.append({"start_at": changed_at, "end_at": ""})
    else:
        if not periods:
            periods.append(
                {
                    "start_at": employee.get("created_at") or changed_at,
                    "end_at": changed_at,
                }
            )
        elif not periods[-1].get("end_at"):
            periods[-1]["end_at"] = changed_at
    return periods[-EMPLOYEE_ACTIVE_PERIODS_LIMIT:]


def employee_active_periods_for_save(
    existing: dict[str, Any], employee: dict[str, Any]
) -> list[dict[str, str]]:
    next_is_active = bool(employee.get("is_active", True))
    if bool(existing.get("is_active", True)) == next_is_active:
        return normalized_employee_active_periods(
            employee.get("active_periods", existing.get("active_periods")),
            created_at=employee.get("created_at") or existing.get("created_at") or "",
            updated_at=employee.get("updated_at") or existing.get("updated_at") or "",
            is_active=next_is_active,
        )
    return employee_active_periods_after_state_change(
        existing,
        next_is_active=next_is_active,
        changed_at=employee.get("updated_at") or model_helpers.utc_now_iso(),
    )


def employee_payroll_active_periods(
    employee: dict[str, Any],
) -> list[dict[str, datetime | None]]:
    fallback_now = model_helpers.utc_now_iso()
    periods = normalized_employee_active_periods(
        employee.get("active_periods"),
        created_at=employee.get("created_at") or fallback_now,
        updated_at=employee.get("updated_at") or employee.get("created_at") or fallback_now,
        is_active=bool(employee.get("is_active", True)),
    )
    parsed: list[dict[str, datetime | None]] = []
    for period in periods:
        start_at = parse_datetime(period.get("start_at"))
        if start_at is None:
            continue
        parsed.append(
            {
                "start_at": start_at,
                "end_at": parse_datetime(period.get("end_at")),
            }
        )
    return parsed


def employee_weekly_base_salary_accruals(
    employee: dict[str, Any],
    *,
    amount: Decimal,
    period_start: datetime,
    period_end: datetime,
    as_of: datetime,
    weekday: int,
    hour: int,
    minute: int,
) -> list[dict[str, Any]]:
    timezone = business_timezone()
    as_of_at = as_of.astimezone(timezone)
    accruals: list[dict[str, Any]] = []
    for active_period in employee_payroll_active_periods(employee):
        active_start = active_period["start_at"].astimezone(timezone)
        active_end = active_period["end_at"]
        start_at = max(period_start.astimezone(timezone), active_start)
        end_at = period_end.astimezone(timezone)
        if active_end is not None:
            end_at = min(end_at, active_end.astimezone(timezone))
        if end_at <= start_at:
            continue
        days_until_weekday = (weekday - start_at.weekday()) % 7
        candidate_date = (start_at + timedelta(days=days_until_weekday)).date()
        candidate = datetime(
            candidate_date.year,
            candidate_date.month,
            candidate_date.day,
            hour,
            minute,
            tzinfo=timezone,
        )
        if candidate < start_at:
            candidate += timedelta(days=7)
        while candidate < end_at and candidate <= as_of_at:
            accruals.append({"accrued_at": candidate, "amount": amount})
            candidate += timedelta(days=7)
    accruals.sort(key=lambda item: item["accrued_at"])
    return accruals
