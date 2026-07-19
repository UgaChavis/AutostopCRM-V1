from __future__ import annotations

import re
from datetime import datetime, timedelta
from decimal import Decimal
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
            month = local_now.strftime("%Y-%m")
            payroll = self._build_payroll_report(
                bundle["cards"],
                employees,
                shift_accruals=shift_accruals,
                repair_order_accruals=repair_order_accruals,
                month=month,
            )
            salary_by_employee = {
                str(item.get("employee_id") or ""): str(item.get("total_salary") or "0")
                for item in payroll["summary"]
            }
            visible_employees = [
                {
                    "name": employee["name"],
                    "position": employee.get("position", ""),
                    "salary": salary_by_employee.get(employee["id"], "0"),
                }
                for employee in employees
                if employee.get("is_active") and employee.get(DASHBOARD_VISIBLE_FIELD)
            ]
            visible_employees.sort(
                key=lambda item: (item["name"].casefold(), item["position"].casefold())
            )
            weeks = self._display_dashboard_week_buckets(bundle["cards"], now=now)
            completed_amounts = [Decimal(item["amount"]) for item in weeks[:3]]
            completed_average = (
                sum(completed_amounts, Decimal("0")) / Decimal(len(completed_amounts))
                if completed_amounts
                else Decimal("0")
            )
            return {
                "schema_version": "display_dashboard.v1",
                "generated_at": local_now.isoformat(),
                "timezone": DISPLAY_DASHBOARD_TIMEZONE,
                "salary_month": month,
                "employees": visible_employees,
                "weeks": weeks,
                "completed_week_average": self._format_payroll_decimal(completed_average),
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
                    "amount": self._format_payroll_decimal(totals[index]),
                    "orders_count": counts[index],
                    "is_current": is_current,
                }
            )
        return weeks
