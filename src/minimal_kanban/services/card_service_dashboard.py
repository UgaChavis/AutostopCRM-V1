from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any

from .. import models as model_helpers
from ..models import Card, business_timezone, parse_business_datetime
from ..repair_order import REPAIR_ORDER_STATUS_CLOSED

DASHBOARD_VISIBLE_FIELD = "dashboard_visible"
DISPLAY_DASHBOARD_TIMEZONE = "Asia/Krasnoyarsk"
DISPLAY_DASHBOARD_WEEKS = 4

_ADMINISTRATIVE_POSITION_PATTERN = re.compile(
    r"(?:^|[^\w])(?:администратор\w*|административ\w*|administrator\w*|admin)(?:$|[^\w])",
    re.IGNORECASE,
)


def is_administrative_position(value: object) -> bool:
    return bool(_ADMINISTRATIVE_POSITION_PATTERN.search(str(value or "").strip().casefold()))


class CardServiceDashboardMixin:
    def get_display_dashboard(self, payload: dict | None = None) -> dict[str, Any]:
        del payload
        with self._lock:
            bundle = self._store.read_bundle()
            employees = self._employees_from_settings(bundle["settings"])
            employees_by_id = {item["id"]: item for item in employees}
            shift_accruals = self._employee_shift_accruals_from_settings(
                bundle["settings"], employees_by_id=employees_by_id
            )
            repair_order_accruals = self._employee_repair_order_accruals_from_settings(
                bundle["settings"], employees_by_id=employees_by_id
            )
            now = model_helpers.utc_now()
            local_now = now.astimezone(business_timezone())
            salary_period = self._display_dashboard_salary_period(now=now)
            period_start = salary_period["starts_at"].astimezone(UTC)
            period_end = salary_period["accrued_through"].astimezone(UTC)
            visible_employees: list[dict[str, str]] = []
            for employee in employees:
                if not employee.get("is_active") or not employee.get(DASHBOARD_VISIBLE_FIELD):
                    continue
                payroll = self._build_employee_salary_reconciliation(
                    bundle["cards"],
                    [],
                    [],
                    employee,
                    shift_accruals=shift_accruals,
                    repair_order_accruals=repair_order_accruals,
                    period_start=period_start,
                    period_end=period_end,
                )
                visible_employees.append(
                    {
                        "name": employee["name"],
                        "position": employee.get("position", ""),
                        "salary": self._format_display_dashboard_rubles(
                            payroll["totals"].get("accrued_total") or "0"
                        ),
                    }
                )
            visible_employees.sort(
                key=lambda item: (
                    -self._parse_payroll_decimal(item["salary"]),
                    item["name"].casefold(),
                    item["position"].casefold(),
                )
            )
            weeks = self._display_dashboard_week_buckets(bundle["cards"], now=now)
            completed_amounts = [Decimal(item["amount"]) for item in weeks[:3]]
            completed_average = (
                sum(completed_amounts, Decimal("0")) / Decimal(len(completed_amounts))
                if completed_amounts
                else Decimal("0")
            )
            return {
                "schema_version": "display_dashboard.v2",
                "generated_at": local_now.isoformat(),
                "timezone": DISPLAY_DASHBOARD_TIMEZONE,
                "salary_period": {
                    "date_from": salary_period["starts_at"].date().isoformat(),
                    "date_to": (salary_period["ends_at"] - timedelta(days=1)).date().isoformat(),
                    "starts_at": salary_period["starts_at"].isoformat(),
                    "ends_at": salary_period["ends_at"].isoformat(),
                    "label": salary_period["label"],
                    "is_open": salary_period["is_open"],
                },
                "employees": visible_employees,
                "weeks": weeks,
                "completed_week_average": self._format_display_dashboard_rubles(completed_average),
            }

    def _display_dashboard_salary_period(self, *, now: datetime | None = None) -> dict[str, Any]:
        timezone = business_timezone()
        local_now = (now or model_helpers.utc_now()).astimezone(timezone)
        monday = local_now.date() - timedelta(days=local_now.weekday())
        starts_at = datetime(
            monday.year,
            monday.month,
            monday.day,
            tzinfo=timezone,
        )
        ends_at = starts_at + timedelta(days=7)
        accrued_through = min(local_now, ends_at)
        return {
            "starts_at": starts_at,
            "ends_at": ends_at,
            "accrued_through": accrued_through,
            "label": f"{starts_at:%d.%m}–{(ends_at - timedelta(days=1)):%d.%m}",
            "is_open": starts_at <= local_now < ends_at,
        }

    def _display_dashboard_week_buckets(
        self, cards: list[Card], *, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        timezone = business_timezone()
        local_now = (now or model_helpers.utc_now()).astimezone(timezone)
        current_week_start = datetime(
            local_now.year,
            local_now.month,
            local_now.day,
            tzinfo=timezone,
        ) - timedelta(days=local_now.weekday())
        starts = [
            current_week_start - timedelta(weeks=offset)
            for offset in range(DISPLAY_DASHBOARD_WEEKS - 1, -1, -1)
        ]
        totals = [Decimal("0") for _ in starts]
        counts = [0 for _ in starts]

        for card in cards:
            order = card.repair_order
            if order.status != REPAIR_ORDER_STATUS_CLOSED:
                continue
            closed_at = parse_business_datetime(order.closed_at)
            if closed_at is None:
                continue
            closed_at = closed_at.astimezone(timezone)
            if closed_at > local_now:
                continue
            for index, start_at in enumerate(starts):
                end_at = start_at + timedelta(days=7)
                if start_at <= closed_at < end_at:
                    totals[index] += Decimal(order.grand_total_amount())
                    counts[index] += 1
                    break

        weeks: list[dict[str, Any]] = []
        for index, start_at in enumerate(starts):
            is_current = index == len(starts) - 1
            date_to = local_now.date() if is_current else (start_at + timedelta(days=6)).date()
            weeks.append(
                {
                    "date_from": start_at.date().isoformat(),
                    "date_to": date_to.isoformat(),
                    "label": f"{start_at:%d.%m}–{date_to:%d.%m}",
                    "amount": self._format_display_dashboard_rubles(totals[index]),
                    "orders_count": counts[index],
                    "is_current": is_current,
                }
            )
        return weeks

    @staticmethod
    def _format_display_dashboard_rubles(amount: object) -> str:
        return str(Decimal(str(amount)).to_integral_value(rounding=ROUND_CEILING))
