from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from .. import models as model_helpers
from ..models import (
    Card,
    CashBox,
    CashTransaction,
    business_timezone,
    format_money_minor,
    normalize_actor_name,
    normalize_bool,
    normalize_file_name,
    normalize_money_minor,
    normalize_source,
    normalize_text,
    parse_business_datetime,
    parse_datetime,
)
from ..repair_order import REPAIR_ORDER_STATUS_CLOSED, RepairOrder, RepairOrderRow
from ..vehicle_profile import normalize_license_plate
from .payroll_constants import (
    EMPLOYEE_SHIFT_ACCRUAL_NOTE,
    PAYROLL_ALLOWED_MODES,
    PAYROLL_MODE_PERCENT_ONLY,
    PAYROLL_MODE_SALARY_ONLY,
    PAYROLL_MODE_SALARY_PLUS_PERCENT,
)

EMPLOYEES_SETTING_KEY = "employees"
EMPLOYEE_SHIFT_ACCRUALS_SETTING_KEY = "employee_shift_accruals"
EMPLOYEES_MAX_COUNT = 15
DEFAULT_MATERIAL_PERCENT = "10"
PAYROLL_WEEKLY_BASE_SALARY_WEEKDAY = 4
PAYROLL_WEEKLY_BASE_SALARY_HOUR = 20
PAYROLL_WEEKLY_BASE_SALARY_MINUTE = 0


class CardServicePayrollMixin:
    def get_payroll_report(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            employees = self._employees_from_settings(bundle["settings"])
            employees_by_id = {item["id"]: item for item in employees}
            shift_accruals = self._employee_shift_accruals_from_settings(
                bundle["settings"], employees_by_id=employees_by_id
            )
            month = self._validated_payroll_month(payload.get("month"))
            employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
            report = self._build_payroll_report(
                bundle["cards"],
                employees,
                shift_accruals=shift_accruals,
                month=month,
                employee_id=employee_id or None,
            )
            return {
                "month": month,
                "summary": report["summary"],
                "detail_rows": report["detail_rows"],
            }

    def _build_employee_salary_ledger(
        self,
        cards: list[Card],
        cashboxes: list[CashBox],
        cash_transactions: list[CashTransaction],
        employee: dict[str, Any],
        *,
        shift_accruals: list[dict[str, Any]] | None = None,
        months: int = 6,
        period_only_totals: bool = False,
    ) -> dict[str, Any]:
        period_start = model_helpers.utc_now() - timedelta(days=30 * months)
        employee_id = employee["id"]
        cashboxes_by_id = {cashbox.id: cashbox for cashbox in cashboxes}
        journal_rows: list[dict[str, Any]] = []
        accrual_total = Decimal("0")
        payout_total = Decimal("0")
        advance_total = Decimal("0")
        now = model_helpers.utc_now()
        weekly_base_start = parse_datetime(employee.get("created_at")) or period_start
        for accrual in self._employee_weekly_base_salary_accruals(
            employee,
            period_start=weekly_base_start,
            period_end=now + timedelta(seconds=1),
            as_of=now,
        ):
            amount = accrual["amount"]
            accrued_at = accrual["accrued_at"]
            is_recent = accrued_at.astimezone(UTC) >= period_start
            if period_only_totals:
                if not is_recent:
                    continue
                accrual_total += amount
            else:
                accrual_total += amount
                if not is_recent:
                    continue
            journal_rows.append(
                {
                    "kind": "base_salary_accrual",
                    "kind_label": "ОКЛАД",
                    "created_at": accrued_at.strftime("%d.%m.%Y %H:%M"),
                    "closed_at": "",
                    "repair_order_number": "",
                    "card_id": "",
                    "vehicle": "",
                    "work_name": "Недельный оклад",
                    "amount_minor": int(
                        (amount * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP)
                    ),
                    "amount_display": self._format_payroll_decimal(amount),
                    "source_label": "пятница 20:00",
                }
            )

        for shift_accrual in shift_accruals or []:
            if shift_accrual.get("employee_id") != employee_id:
                continue
            amount = Decimal(normalize_money_minor(shift_accrual.get("amount_minor"))) / Decimal(
                "100"
            )
            if amount <= Decimal("0"):
                continue
            created_at = parse_business_datetime(shift_accrual.get("created_at"))
            if created_at is None:
                continue
            is_recent = created_at.astimezone(UTC) >= period_start
            if period_only_totals:
                if not is_recent:
                    continue
                accrual_total += amount
            else:
                accrual_total += amount
                if not is_recent:
                    continue
            journal_rows.append(
                {
                    "kind": "shift_accrual",
                    "kind_label": "СМЕНЫ",
                    "created_at": created_at.astimezone(business_timezone()).strftime(
                        "%d.%m.%Y %H:%M"
                    ),
                    "closed_at": "",
                    "repair_order_number": "",
                    "card_id": "",
                    "vehicle": "",
                    "work_name": shift_accrual.get("note") or EMPLOYEE_SHIFT_ACCRUAL_NOTE,
                    "accrual_id": shift_accrual.get("id") or "",
                    "amount_minor": int(normalize_money_minor(shift_accrual.get("amount_minor"))),
                    "amount_display": format_money_minor(
                        normalize_money_minor(shift_accrual.get("amount_minor"))
                    ),
                    "source_label": "ручное начисление",
                    "note": shift_accrual.get("note") or EMPLOYEE_SHIFT_ACCRUAL_NOTE,
                }
            )

        for card in cards:
            order = card.repair_order
            if order.status != REPAIR_ORDER_STATUS_CLOSED:
                continue
            closed_at = self._parse_repair_order_datetime(order.closed_at)
            if closed_at is None:
                continue
            is_recent = closed_at >= period_start
            for source_row in order.works:
                row = RepairOrderRow.from_dict(
                    source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
                )
                if row.executor_id != employee_id:
                    continue
                amount = self._parse_payroll_decimal(row.salary_amount)
                if period_only_totals:
                    if not is_recent:
                        continue
                    accrual_total += amount
                else:
                    accrual_total += amount
                    if not is_recent:
                        continue
                journal_rows.append(
                    {
                        "kind": "accrual",
                        "kind_label": "НАЧИСЛЕНИЕ",
                        "created_at": order.closed_at,
                        "closed_at": order.closed_at,
                        "repair_order_number": order.number,
                        "card_id": card.id,
                        "vehicle": order.vehicle or card.vehicle,
                        "work_name": row.name,
                        "amount_minor": int(
                            (amount * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP)
                        ),
                        "amount_display": self._format_payroll_decimal(amount),
                        "source_label": "заказ-наряд",
                        "scheme": self._work_salary_scheme(row),
                    }
                )
            for source_row in order.materials:
                row = RepairOrderRow.from_dict(
                    source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
                )
                if (
                    self._material_salary_employee_id(row) != employee_id
                    or not row.material_salary_accrued_at
                ):
                    continue
                amount = self._parse_payroll_decimal(row.material_salary_amount)
                if period_only_totals:
                    if not is_recent:
                        continue
                    accrual_total += amount
                else:
                    accrual_total += amount
                    if not is_recent:
                        continue
                journal_rows.append(
                    {
                        "kind": "material_accrual",
                        "kind_label": "НАЧИСЛЕНИЕ МАТЕРИАЛ",
                        "created_at": order.closed_at,
                        "closed_at": order.closed_at,
                        "repair_order_number": order.number,
                        "card_id": card.id,
                        "vehicle": order.vehicle or card.vehicle,
                        "work_name": row.name,
                        "amount_minor": int(
                            (amount * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP)
                        ),
                        "amount_display": self._format_payroll_decimal(amount),
                        "source_label": "материал",
                        "material_profit": row.material_profit,
                        "material_percent": row.material_percent_snapshot,
                    }
                )

        for transaction in cash_transactions:
            if transaction.employee_id != employee_id:
                continue
            kind = normalize_text(transaction.transaction_kind, default="", limit=32).casefold()
            if kind not in {"salary_payout", "salary_advance"}:
                continue
            amount = Decimal(transaction.amount_minor) / Decimal("100")
            created_at = parse_datetime(transaction.created_at)
            if period_only_totals and created_at is not None and created_at < period_start:
                continue
            if kind == "salary_payout":
                payout_total += amount
                kind_label = "ВЫПЛАТА"
            else:
                advance_total += amount
                kind_label = "АВАНС"
            if created_at is not None and created_at < period_start:
                continue
            cashbox_name = (
                cashboxes_by_id.get(transaction.cashbox_id).name
                if cashboxes_by_id.get(transaction.cashbox_id)
                else "касса"
            )
            journal_rows.append(
                {
                    "kind": kind,
                    "kind_label": kind_label,
                    "created_at": transaction.created_at,
                    "closed_at": "",
                    "repair_order_number": "",
                    "card_id": "",
                    "vehicle": "",
                    "work_name": "",
                    "transaction_id": transaction.id,
                    "amount_minor": int(transaction.amount_minor),
                    "amount_display": format_money_minor(transaction.amount_minor),
                    "source_label": cashbox_name,
                    "cashbox_id": transaction.cashbox_id,
                    "note": transaction.note,
                }
            )

        journal_rows.sort(
            key=lambda item: (
                self._repair_order_sortable_datetime(item["created_at"]),
                item["kind_label"],
                item.get("repair_order_number") or "",
                item.get("work_name") or "",
            ),
            reverse=True,
        )
        balance_total = accrual_total - payout_total - advance_total
        return {
            "employee_id": employee_id,
            "employee_name": employee["name"],
            "position": employee["position"],
            "period_months": months,
            "period_start": period_start.isoformat(),
            "balance_total": self._format_payroll_decimal(balance_total),
            "balance_display": self._format_payroll_decimal(balance_total),
            "accrued_total": self._format_payroll_decimal(accrual_total),
            "accrued_total_display": self._format_payroll_decimal(accrual_total),
            "payout_total": self._format_payroll_decimal(payout_total),
            "payout_total_display": self._format_payroll_decimal(payout_total),
            "advance_total": self._format_payroll_decimal(advance_total),
            "advance_total_display": self._format_payroll_decimal(advance_total),
            "journal_rows": journal_rows,
            "journal_total": len(journal_rows),
        }

    def get_employee_salary_ledger(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            employees = self._employees_from_settings(bundle["settings"])
            employees_by_id = {item["id"]: item for item in employees}
            shift_accruals = self._employee_shift_accruals_from_settings(
                bundle["settings"], employees_by_id=employees_by_id
            )
            employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
            if not employee_id:
                self._fail(
                    "validation_error",
                    "Нужно передать employee_id.",
                    details={"field": "employee_id"},
                )
            months = self._validated_limit(payload.get("months"), default=6, maximum=12)
            employee = next((item for item in employees if item["id"] == employee_id), None)
            if employee is None:
                self._fail(
                    "not_found",
                    "Сотрудник не найден.",
                    status_code=404,
                    details={"employee_id": employee_id},
                )
            ledger = self._build_employee_salary_ledger(
                bundle["cards"],
                bundle["cashboxes"],
                bundle["cash_transactions"],
                employee,
                shift_accruals=shift_accruals,
                months=months,
            )
            return ledger

    def _employee_salary_reconciliation_amount_fields(
        self,
        *,
        accrued: Decimal = Decimal("0"),
        payment: Decimal = Decimal("0"),
        show_accrued: bool = False,
        show_payment: bool = False,
    ) -> dict[str, object]:
        accrued_money = self._employee_salary_report_money(accrued)
        payment_money = self._employee_salary_report_money(payment)
        return {
            "accrued": accrued_money["raw"],
            "accrued_minor": accrued_money["minor"],
            "accrued_display": accrued_money["display"] if show_accrued else "",
            "payment": payment_money["raw"],
            "payment_minor": payment_money["minor"],
            "payment_display": payment_money["display"] if show_payment else "",
        }

    def _employee_salary_reconciliation_datetime_payload(self, moment: datetime) -> dict[str, str]:
        business_moment = moment.astimezone(business_timezone())
        return {
            "date": business_moment.strftime("%d.%m.%Y %H:%M"),
            "date_iso": business_moment.isoformat(),
        }

    def _build_employee_salary_reconciliation(
        self,
        cards: list[Card],
        cashboxes: list[CashBox],
        cash_transactions: list[CashTransaction],
        employee: dict[str, Any],
        *,
        shift_accruals: list[dict[str, Any]] | None = None,
        period_start: datetime,
        period_end: datetime,
    ) -> dict[str, Any]:
        employee_id = employee["id"]
        cashboxes_by_id = {cashbox.id: cashbox for cashbox in cashboxes}
        rows: list[dict[str, Any]] = []
        accrued_total = Decimal("0")
        payout_total = Decimal("0")
        advance_total = Decimal("0")

        def money_base(label: str, value: Decimal) -> str:
            return f"{label} {self._employee_salary_report_money(value)['display']}"

        def add_row(sort_at: datetime, payload: dict[str, Any]) -> None:
            date_payload = self._employee_salary_reconciliation_datetime_payload(sort_at)
            rows.append({**payload, **date_payload, "_sort_at": sort_at.astimezone(UTC)})

        for accrual in self._employee_weekly_base_salary_accruals(
            employee,
            period_start=period_start,
            period_end=period_end + timedelta(seconds=1),
            as_of=period_end,
        ):
            accrued_at = accrual["accrued_at"]
            if accrued_at.astimezone(UTC) < period_start or accrued_at.astimezone(UTC) > period_end:
                continue
            amount = accrual["amount"]
            accrued_total += amount
            add_row(
                accrued_at,
                {
                    "kind": "base_salary_accrual",
                    "kind_label": "ОКЛАД",
                    "repair_order_number": "",
                    "card_id": "",
                    "vehicle": "",
                    "license_plate": "",
                    "item": "Недельный оклад",
                    "calculation_base": money_base("Оклад", amount),
                    "scheme": "Недельный оклад",
                    "note": "пятница 20:00",
                    **self._employee_salary_reconciliation_amount_fields(
                        accrued=amount, show_accrued=True
                    ),
                },
            )

        for shift_accrual in shift_accruals or []:
            if shift_accrual.get("employee_id") != employee_id:
                continue
            created_at = parse_business_datetime(shift_accrual.get("created_at"))
            if created_at is None:
                continue
            created_at_utc = created_at.astimezone(UTC)
            if created_at_utc < period_start or created_at_utc > period_end:
                continue
            amount = Decimal(normalize_money_minor(shift_accrual.get("amount_minor"))) / Decimal(
                "100"
            )
            if amount <= Decimal("0"):
                continue
            accrued_total += amount
            note = shift_accrual.get("note") or EMPLOYEE_SHIFT_ACCRUAL_NOTE
            add_row(
                created_at,
                {
                    "kind": "shift_accrual",
                    "kind_label": "СМЕНЫ",
                    "repair_order_number": "",
                    "card_id": "",
                    "vehicle": "",
                    "license_plate": "",
                    "item": note,
                    "calculation_base": "Ручное начисление",
                    "scheme": "Смены за неделю",
                    "accrual_id": shift_accrual.get("id") or "",
                    "note": note,
                    **self._employee_salary_reconciliation_amount_fields(
                        accrued=amount, show_accrued=True
                    ),
                },
            )

        for card in cards:
            order = card.repair_order
            if order.status != REPAIR_ORDER_STATUS_CLOSED:
                continue
            closed_at = self._parse_repair_order_datetime(order.closed_at)
            if closed_at is None:
                continue
            closed_at_utc = closed_at.astimezone(UTC)
            if closed_at_utc < period_start or closed_at_utc > period_end:
                continue
            vehicle = order.vehicle or card.vehicle_display() or "-"
            license_plate = (
                normalize_license_plate(order.license_plate, limit=40)
                or normalize_license_plate(card.vehicle_profile.registration_plate, limit=40)
                or ""
            )
            repair_order_number = order.number or "-"
            for source_row in order.works:
                row = RepairOrderRow.from_dict(
                    source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
                )
                if row.executor_id != employee_id:
                    continue
                row_total = row.total_value()
                amount = self._parse_payroll_decimal(row.salary_amount)
                accrued_total += amount
                scheme = self._work_salary_scheme(row)
                add_row(
                    closed_at,
                    {
                        "kind": "work_accrual",
                        "kind_label": "РАБОТА",
                        "repair_order_number": repair_order_number,
                        "card_id": card.id,
                        "vehicle": vehicle,
                        "license_plate": license_plate,
                        "item": row.name or "Работа без названия",
                        "calculation_base": money_base("Работа", row_total),
                        "scheme": scheme,
                        "note": "",
                        **self._employee_salary_reconciliation_amount_fields(
                            accrued=amount, show_accrued=True
                        ),
                    },
                )
            for source_row in order.materials:
                row = RepairOrderRow.from_dict(
                    source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
                )
                if (
                    self._material_salary_employee_id(row) != employee_id
                    or not row.material_salary_accrued_at
                ):
                    continue
                profit = self._parse_payroll_decimal(row.material_profit)
                amount = self._parse_payroll_decimal(row.material_salary_amount)
                accrued_total += amount
                percent = normalize_text(row.material_percent_snapshot, default="", limit=40)
                scheme = f"Материалы {percent}%" if percent else "Материалы"
                add_row(
                    closed_at,
                    {
                        "kind": "material_accrual",
                        "kind_label": "МАТЕРИАЛ",
                        "repair_order_number": repair_order_number,
                        "card_id": card.id,
                        "vehicle": vehicle,
                        "license_plate": license_plate,
                        "item": row.name or "Материал без названия",
                        "calculation_base": money_base("Прибыль материалов", profit),
                        "scheme": scheme,
                        "note": "",
                        **self._employee_salary_reconciliation_amount_fields(
                            accrued=amount, show_accrued=True
                        ),
                    },
                )

        for transaction in cash_transactions:
            if transaction.employee_id != employee_id:
                continue
            kind = normalize_text(transaction.transaction_kind, default="", limit=32).casefold()
            if kind not in {"salary_payout", "salary_advance"}:
                continue
            created_at = parse_datetime(transaction.created_at)
            if created_at is None:
                continue
            created_at_utc = created_at.astimezone(UTC)
            if created_at_utc < period_start or created_at_utc > period_end:
                continue
            amount = Decimal(transaction.amount_minor) / Decimal("100")
            if kind == "salary_payout":
                payout_total += amount
                kind_label = "ВЫПЛАТА"
            else:
                advance_total += amount
                kind_label = "АВАНС"
            cashbox = cashboxes_by_id.get(transaction.cashbox_id)
            add_row(
                created_at,
                {
                    "kind": kind,
                    "kind_label": kind_label,
                    "repair_order_number": "",
                    "card_id": "",
                    "vehicle": "",
                    "license_plate": "",
                    "item": "",
                    "calculation_base": "",
                    "scheme": "",
                    "transaction_id": transaction.id,
                    "cashbox_id": transaction.cashbox_id,
                    "cashbox_name": cashbox.name if cashbox else "",
                    "note": transaction.note,
                    **self._employee_salary_reconciliation_amount_fields(
                        payment=amount, show_payment=True
                    ),
                },
            )

        rows.sort(
            key=lambda item: (
                item["_sort_at"],
                item["kind_label"],
                item.get("repair_order_number") or "",
                item.get("item") or "",
            )
        )
        for index, row in enumerate(rows, start=1):
            row["number"] = index
            row.pop("_sort_at", None)

        amount_due_total = accrued_total - payout_total - advance_total
        totals: dict[str, object] = {}
        for key, value in (
            ("accrued_total", accrued_total),
            ("payout_total", payout_total),
            ("advance_total", advance_total),
            ("amount_due_total", amount_due_total),
        ):
            money = self._employee_salary_report_money(value)
            totals[key] = money["raw"]
            totals[f"{key}_minor"] = money["minor"]
            totals[f"{key}_display"] = money["display"]
        return {"rows": rows, "totals": totals}

    def get_employee_salary_reconciliation(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            employees = self._employees_from_settings(bundle["settings"])
            employees_by_id = {item["id"]: item for item in employees}
            shift_accruals = self._employee_shift_accruals_from_settings(
                bundle["settings"], employees_by_id=employees_by_id
            )
            employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
            if not employee_id:
                self._fail(
                    "validation_error",
                    "Нужно передать employee_id.",
                    details={"field": "employee_id"},
                )
            employee = next((item for item in employees if item["id"] == employee_id), None)
            if employee is None:
                self._fail(
                    "not_found",
                    "Сотрудник не найден.",
                    status_code=404,
                    details={"employee_id": employee_id},
                )
            period_days = 30
            period_end = model_helpers.utc_now().astimezone(UTC)
            period_start = period_end - timedelta(days=period_days)
            report = self._build_employee_salary_reconciliation(
                bundle["cards"],
                bundle["cashboxes"],
                bundle["cash_transactions"],
                employee,
                shift_accruals=shift_accruals,
                period_start=period_start,
                period_end=period_end,
            )
            business_start = period_start.astimezone(business_timezone())
            business_end = period_end.astimezone(business_timezone())
            period = {
                "date_from": business_start.date().isoformat(),
                "date_to": business_end.date().isoformat(),
                "label": f"{business_start.strftime('%d.%m.%Y')} - {business_end.strftime('%d.%m.%Y')}",
                "days": period_days,
                "generated_at": period_end.isoformat(),
            }
            return {
                "employee": {
                    "id": employee["id"],
                    "name": employee["name"],
                    "position": employee.get("position", ""),
                    "salary_mode": employee.get("salary_mode", ""),
                    "base_salary": employee.get("base_salary", ""),
                    "work_percent": employee.get("work_percent", ""),
                    "material_percent": employee.get("material_percent", ""),
                },
                "period": period,
                "rows": report["rows"],
                "totals": report["totals"],
                "meta": {
                    "schema_version": "employee_salary_reconciliation.v1",
                    "period_days": period_days,
                    "row_count": len(report["rows"]),
                },
            }

    def _employee_salary_report_decimal_minor(self, value: Decimal) -> int:
        return int((value * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP))

    def _employee_salary_report_money(self, value: Decimal) -> dict[str, object]:
        amount = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        amount_minor = self._employee_salary_report_decimal_minor(amount)
        return {
            "raw": self._format_payroll_decimal(amount),
            "minor": amount_minor,
            "display": format_money_minor(amount_minor),
        }

    def _employee_salary_report_period(self, month: str) -> dict[str, str]:
        normalized_month = self._validated_payroll_month(month)
        period_start = datetime.strptime(normalized_month, "%Y-%m")
        if period_start.month == 12:
            next_month = datetime(period_start.year + 1, 1, 1)
        else:
            next_month = datetime(period_start.year, period_start.month + 1, 1)
        period_end = next_month - timedelta(days=1)
        return {
            "month": normalized_month,
            "label": self._cash_journal_month_label(normalized_month),
            "date_from": period_start.date().isoformat(),
            "date_to": period_end.date().isoformat(),
        }

    def _employee_salary_report_totals_payload(
        self,
        *,
        repair_order_count: int,
        work_count: int,
        work_total: Decimal,
        base_salary_count: int = 0,
        base_salary_total: Decimal = Decimal("0"),
        work_accrued_total: Decimal | None = None,
        material_count: int = 0,
        material_total: Decimal = Decimal("0"),
        material_cost_total: Decimal = Decimal("0"),
        material_profit_total: Decimal = Decimal("0"),
        material_accrued_total: Decimal = Decimal("0"),
        shift_accrual_count: int = 0,
        shift_accrual_total: Decimal = Decimal("0"),
        accrued_total: Decimal | None = None,
    ) -> dict[str, object]:
        resolved_work_accrued_total = (
            accrued_total
            if work_accrued_total is None and accrued_total is not None
            else work_accrued_total
        )
        if resolved_work_accrued_total is None:
            resolved_work_accrued_total = Decimal("0")
        resolved_accrued_total = (
            accrued_total
            if accrued_total is not None
            else (
                base_salary_total
                + shift_accrual_total
                + resolved_work_accrued_total
                + material_accrued_total
            )
        )
        base_salary_money = self._employee_salary_report_money(base_salary_total)
        shift_accrual_money = self._employee_salary_report_money(shift_accrual_total)
        work_money = self._employee_salary_report_money(work_total)
        work_accrued_money = self._employee_salary_report_money(resolved_work_accrued_total)
        material_money = self._employee_salary_report_money(material_total)
        material_cost_money = self._employee_salary_report_money(material_cost_total)
        material_profit_money = self._employee_salary_report_money(material_profit_total)
        material_accrued_money = self._employee_salary_report_money(material_accrued_total)
        accrued_money = self._employee_salary_report_money(resolved_accrued_total)
        return {
            "repair_order_count": repair_order_count,
            "base_salary_count": base_salary_count,
            "base_salary_total": base_salary_money["raw"],
            "base_salary_total_minor": base_salary_money["minor"],
            "base_salary_total_display": base_salary_money["display"],
            "shift_accrual_count": shift_accrual_count,
            "shift_accrual_total": shift_accrual_money["raw"],
            "shift_accrual_total_minor": shift_accrual_money["minor"],
            "shift_accrual_total_display": shift_accrual_money["display"],
            "work_count": work_count,
            "work_total": work_money["raw"],
            "work_total_minor": work_money["minor"],
            "work_total_display": work_money["display"],
            "work_accrued_total": work_accrued_money["raw"],
            "work_accrued_total_minor": work_accrued_money["minor"],
            "work_accrued_total_display": work_accrued_money["display"],
            "material_count": material_count,
            "material_total": material_money["raw"],
            "material_total_minor": material_money["minor"],
            "material_total_display": material_money["display"],
            "material_cost_total": material_cost_money["raw"],
            "material_cost_total_minor": material_cost_money["minor"],
            "material_cost_total_display": material_cost_money["display"],
            "material_profit_total": material_profit_money["raw"],
            "material_profit_total_minor": material_profit_money["minor"],
            "material_profit_total_display": material_profit_money["display"],
            "material_accrued_total": material_accrued_money["raw"],
            "material_accrued_total_minor": material_accrued_money["minor"],
            "material_accrued_total_display": material_accrued_money["display"],
            "accrued_total": accrued_money["raw"],
            "accrued_total_minor": accrued_money["minor"],
            "accrued_total_display": accrued_money["display"],
        }

    def _employee_salary_report_text(
        self,
        *,
        employee: dict[str, Any],
        period: dict[str, str],
        totals: dict[str, object],
        days: list[dict[str, Any]],
    ) -> str:
        lines = [
            "ОТЧЕТ ПО НАЧИСЛЕНИЯМ",
            "",
            f"Сотрудник: {employee.get('name') or 'Сотрудник'}",
            f"Период: {period['label']}",
            "",
        ]
        if not days:
            lines.append("За выбранный период начислений по закрытым заказ-нарядам нет.")
            return "\n".join(lines).strip()

        lines.extend(
            [
                "ИТОГО",
                f"Заказ-нарядов:        {totals['repair_order_count']}",
                f"Окладов:              {totals['base_salary_count']}",
                f"Начислено окладом:    {totals['base_salary_total_display']}",
                f"Выплат за смены:      {totals['shift_accrual_count']}",
                f"Начислено сменами:    {totals['shift_accrual_total_display']}",
                f"Работ:                {totals['work_count']}",
                f"Стоимость работ:      {totals['work_total_display']}",
                f"Материалы:            {totals['material_count']}",
                f"Прибыль материалов:   {totals['material_profit_total_display']}",
                f"Начислено с работ:    {totals['work_accrued_total_display']}",
                f"Начислено с мат.:     {totals['material_accrued_total_display']}",
                f"Начислено:            {totals['accrued_total_display']}",
            ]
        )
        for day in days:
            lines.extend(["", str(day["label"]), ""])
            for salary in day.get("base_salary_accruals", []):
                lines.append(
                    "Оклад | "
                    + f"{salary['created_at']} | "
                    + f"начислено: {salary['amount_display']}"
                )
            for shift in day.get("shift_accruals", []):
                lines.append(
                    "Смены | "
                    + f"{shift['created_at']} | "
                    + f"{shift['note']} | "
                    + f"начислено: {shift['amount_display']}"
                )
            for order in day["repair_orders"]:
                lines.append(
                    "ЗН "
                    + f"{order['repair_order_number']} | {order['vehicle']} | "
                    + f"госномер: {order['license_plate']}"
                )
                lines.append(
                    f"Работ: {order['work_count']} | "
                    + f"Стоимость работ: {order['work_total_display']} | "
                    + f"Материалов: {order['material_count']} | "
                    + f"Прибыль материалов: {order['material_profit_total_display']} | "
                    + f"Начислено: {order['accrued_total_display']}"
                )
                for work in order["works"]:
                    lines.append(f"  - {work['name']}")
                    if work["quantity"] or work["price"]:
                        lines.append(
                            f"    Кол-во: {work['quantity'] or '-'} | "
                            + f"Цена: {work['price_display'] or '-'}"
                        )
                    lines.append(f"    Стоимость: {work['total_display']}")
                    if work.get("scheme"):
                        lines.append(f"    Схема: {work['scheme']}")
                    lines.append(f"    Начислено: {work['accrued_display']}")
                for material in order["materials"]:
                    lines.append(f"  - Материал: {material['name']}")
                    if material["quantity"] or material["price"]:
                        lines.append(
                            f"    Кол-во: {material['quantity'] or '-'} | "
                            + f"Цена: {material['price_display'] or '-'} | "
                            + f"Закупка: {material['cost_price_display'] or '-'}"
                        )
                    lines.append(f"    Продажа: {material['total_display']}")
                    lines.append(f"    Закупка всего: {material['cost_total_display']}")
                    lines.append(f"    Прибыль: {material['profit_display']}")
                    lines.append(f"    Начислено: {material['accrued_display']}")
                lines.append("")
            day_totals = day["totals"]
            lines.append(
                "Итого за день: "
                + f"заказ-нарядов {day_totals['repair_order_count']}, "
                + f"окладов {day_totals['base_salary_count']}, "
                + f"смен {day_totals['shift_accrual_count']}, "
                + f"работ {day_totals['work_count']}, "
                + f"материалов {day_totals['material_count']}, "
                + f"стоимость {day_totals['work_total_display']}, "
                + f"прибыль материалов {day_totals['material_profit_total_display']}, "
                + f"начислено {day_totals['accrued_total_display']}"
            )
        return "\n".join(lines).strip()

    def _build_employee_salary_report(
        self,
        cards: list[Card],
        employee: dict[str, Any],
        *,
        month: str,
        shift_accruals: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        employee_id = employee["id"]
        period = self._employee_salary_report_period(month)
        period_start, period_end = self._payroll_month_bounds(period["month"])
        month_key = period["month"].replace("-", "")
        grouped_days: dict[str, dict[str, Any]] = {}

        for accrual in self._employee_weekly_base_salary_accruals(
            employee,
            period_start=period_start,
            period_end=period_end,
        ):
            amount = accrual["amount"]
            accrued_at = accrual["accrued_at"]
            amount_money = self._employee_salary_report_money(amount)
            day_key = accrued_at.date().isoformat()
            day_payload = grouped_days.setdefault(
                day_key,
                {
                    "date": day_key,
                    "label": accrued_at.strftime("%d.%m.%Y"),
                    "base_salary_accruals": [],
                    "shift_accruals": [],
                    "repair_orders": [],
                    "_base_salary_total": Decimal("0"),
                    "_shift_accrual_total": Decimal("0"),
                    "_work_total": Decimal("0"),
                    "_work_accrued_total": Decimal("0"),
                    "_material_total": Decimal("0"),
                    "_material_cost_total": Decimal("0"),
                    "_material_profit_total": Decimal("0"),
                    "_material_accrued_total": Decimal("0"),
                },
            )
            day_payload["base_salary_accruals"].append(
                {
                    "created_at": accrued_at.strftime("%d.%m.%Y %H:%M"),
                    "created_at_iso": accrued_at.isoformat(),
                    "amount": amount_money["raw"],
                    "amount_minor": amount_money["minor"],
                    "amount_display": amount_money["display"],
                }
            )
            day_payload["_base_salary_total"] += amount

        for shift_accrual in shift_accruals or []:
            if shift_accrual.get("employee_id") != employee_id:
                continue
            created_at = parse_business_datetime(shift_accrual.get("created_at"))
            if created_at is None:
                continue
            created_at = created_at.astimezone(business_timezone())
            if created_at < period_start or created_at >= period_end:
                continue
            amount = Decimal(normalize_money_minor(shift_accrual.get("amount_minor"))) / Decimal(
                "100"
            )
            if amount <= Decimal("0"):
                continue
            amount_money = self._employee_salary_report_money(amount)
            day_key = created_at.date().isoformat()
            day_payload = grouped_days.setdefault(
                day_key,
                {
                    "date": day_key,
                    "label": created_at.strftime("%d.%m.%Y"),
                    "base_salary_accruals": [],
                    "shift_accruals": [],
                    "repair_orders": [],
                    "_base_salary_total": Decimal("0"),
                    "_shift_accrual_total": Decimal("0"),
                    "_work_total": Decimal("0"),
                    "_work_accrued_total": Decimal("0"),
                    "_material_total": Decimal("0"),
                    "_material_cost_total": Decimal("0"),
                    "_material_profit_total": Decimal("0"),
                    "_material_accrued_total": Decimal("0"),
                },
            )
            note = shift_accrual.get("note") or EMPLOYEE_SHIFT_ACCRUAL_NOTE
            day_payload["shift_accruals"].append(
                {
                    "id": shift_accrual.get("id") or "",
                    "created_at": created_at.strftime("%d.%m.%Y %H:%M"),
                    "created_at_iso": created_at.isoformat(),
                    "note": note,
                    "amount": amount_money["raw"],
                    "amount_minor": amount_money["minor"],
                    "amount_display": amount_money["display"],
                }
            )
            day_payload["_shift_accrual_total"] += amount

        for card in cards:
            order = card.repair_order
            if order.status != REPAIR_ORDER_STATUS_CLOSED:
                continue
            closed_sort_key = self._repair_order_closed_sort_value(card)
            if not closed_sort_key.startswith(month_key):
                continue
            closed_at = self._parse_repair_order_business_datetime(order.closed_at)
            if closed_at is None:
                continue
            works: list[dict[str, Any]] = []
            materials: list[dict[str, Any]] = []
            work_total = Decimal("0")
            work_accrued_total = Decimal("0")
            material_total = Decimal("0")
            material_cost_total = Decimal("0")
            material_profit_total = Decimal("0")
            material_accrued_total = Decimal("0")
            for source_row in order.works:
                row = RepairOrderRow.from_dict(
                    source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
                )
                if row.executor_id != employee_id:
                    continue
                row_total = row.total_value()
                row_accrued = self._parse_payroll_decimal(row.salary_amount)
                row_price_text = normalize_text(row.price, default="", limit=40)
                row_price = self._parse_payroll_decimal(row_price_text)
                work_money = self._employee_salary_report_money(row_total)
                accrued_money = self._employee_salary_report_money(row_accrued)
                price_money = self._employee_salary_report_money(row_price)
                works.append(
                    {
                        "name": row.name or "Работа без названия",
                        "quantity": row.quantity,
                        "price": self._format_payroll_decimal(row_price) if row_price_text else "",
                        "price_display": price_money["display"] if row_price_text else "",
                        "total": work_money["raw"],
                        "total_minor": work_money["minor"],
                        "total_display": work_money["display"],
                        "accrued": accrued_money["raw"],
                        "accrued_minor": accrued_money["minor"],
                        "accrued_display": accrued_money["display"],
                        "scheme": self._work_salary_scheme(row),
                    }
                )
                work_total += row_total
                work_accrued_total += row_accrued
            for source_row in order.materials:
                row = RepairOrderRow.from_dict(
                    source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
                )
                if (
                    self._material_salary_employee_id(row) != employee_id
                    or not row.material_salary_accrued_at
                ):
                    continue
                row_total = self._material_sale_total(row)
                row_cost_total = self._material_cost_total(row) or Decimal("0")
                row_profit = self._parse_payroll_decimal(row.material_profit)
                row_accrued = self._parse_payroll_decimal(row.material_salary_amount)
                row_price_text = normalize_text(
                    row.material_price_snapshot or row.price, default="", limit=40
                )
                row_cost_text = normalize_text(
                    row.material_cost_price_snapshot or row.cost_price, default="", limit=40
                )
                row_price = self._parse_payroll_decimal(row_price_text)
                row_cost = self._parse_payroll_decimal(row_cost_text)
                total_money = self._employee_salary_report_money(row_total)
                cost_total_money = self._employee_salary_report_money(row_cost_total)
                profit_money = self._employee_salary_report_money(row_profit)
                accrued_money = self._employee_salary_report_money(row_accrued)
                price_money = self._employee_salary_report_money(row_price)
                cost_money = self._employee_salary_report_money(row_cost)
                materials.append(
                    {
                        "name": row.name or "Материал без названия",
                        "quantity": row.quantity,
                        "price": self._format_payroll_decimal(row_price) if row_price_text else "",
                        "price_display": price_money["display"] if row_price_text else "",
                        "cost_price": self._format_payroll_decimal(row_cost)
                        if row_cost_text
                        else "",
                        "cost_price_display": cost_money["display"] if row_cost_text else "",
                        "total": total_money["raw"],
                        "total_minor": total_money["minor"],
                        "total_display": total_money["display"],
                        "cost_total": cost_total_money["raw"],
                        "cost_total_minor": cost_total_money["minor"],
                        "cost_total_display": cost_total_money["display"],
                        "profit": profit_money["raw"],
                        "profit_minor": profit_money["minor"],
                        "profit_display": profit_money["display"],
                        "material_percent": row.material_percent_snapshot,
                        "accrued": accrued_money["raw"],
                        "accrued_minor": accrued_money["minor"],
                        "accrued_display": accrued_money["display"],
                    }
                )
                material_total += row_total
                material_cost_total += row_cost_total
                material_profit_total += row_profit
                material_accrued_total += row_accrued
            if not works and not materials:
                continue

            plate = (
                normalize_license_plate(order.license_plate, limit=40)
                or normalize_license_plate(card.vehicle_profile.registration_plate, limit=40)
                or "-"
            )
            vehicle = order.vehicle or card.vehicle_display() or "-"
            order_totals = self._employee_salary_report_totals_payload(
                repair_order_count=1,
                work_count=len(works),
                work_total=work_total,
                work_accrued_total=work_accrued_total,
                material_count=len(materials),
                material_total=material_total,
                material_cost_total=material_cost_total,
                material_profit_total=material_profit_total,
                material_accrued_total=material_accrued_total,
            )
            order_payload = {
                "card_id": card.id,
                "repair_order_number": order.number or "-",
                "closed_at": closed_at.strftime("%d.%m.%Y %H:%M"),
                "closed_at_iso": closed_at.isoformat(),
                "vehicle": vehicle,
                "license_plate": plate,
                "work_count": len(works),
                "work_total": order_totals["work_total"],
                "work_total_minor": order_totals["work_total_minor"],
                "work_total_display": order_totals["work_total_display"],
                "work_accrued_total": order_totals["work_accrued_total"],
                "work_accrued_total_minor": order_totals["work_accrued_total_minor"],
                "work_accrued_total_display": order_totals["work_accrued_total_display"],
                "material_count": len(materials),
                "material_total": order_totals["material_total"],
                "material_total_minor": order_totals["material_total_minor"],
                "material_total_display": order_totals["material_total_display"],
                "material_cost_total": order_totals["material_cost_total"],
                "material_cost_total_minor": order_totals["material_cost_total_minor"],
                "material_cost_total_display": order_totals["material_cost_total_display"],
                "material_profit_total": order_totals["material_profit_total"],
                "material_profit_total_minor": order_totals["material_profit_total_minor"],
                "material_profit_total_display": order_totals["material_profit_total_display"],
                "material_accrued_total": order_totals["material_accrued_total"],
                "material_accrued_total_minor": order_totals["material_accrued_total_minor"],
                "material_accrued_total_display": order_totals["material_accrued_total_display"],
                "accrued_total": order_totals["accrued_total"],
                "accrued_total_minor": order_totals["accrued_total_minor"],
                "accrued_total_display": order_totals["accrued_total_display"],
                "works": works,
                "materials": materials,
            }
            day_key = closed_at.date().isoformat()
            day_payload = grouped_days.setdefault(
                day_key,
                {
                    "date": day_key,
                    "label": closed_at.strftime("%d.%m.%Y"),
                    "base_salary_accruals": [],
                    "shift_accruals": [],
                    "repair_orders": [],
                    "_base_salary_total": Decimal("0"),
                    "_shift_accrual_total": Decimal("0"),
                    "_work_total": Decimal("0"),
                    "_work_accrued_total": Decimal("0"),
                    "_material_total": Decimal("0"),
                    "_material_cost_total": Decimal("0"),
                    "_material_profit_total": Decimal("0"),
                    "_material_accrued_total": Decimal("0"),
                },
            )
            day_payload["repair_orders"].append(order_payload)
            day_payload["_work_total"] += work_total
            day_payload["_work_accrued_total"] += work_accrued_total
            day_payload["_material_total"] += material_total
            day_payload["_material_cost_total"] += material_cost_total
            day_payload["_material_profit_total"] += material_profit_total
            day_payload["_material_accrued_total"] += material_accrued_total

        days: list[dict[str, Any]] = []
        total_base_salary_total = Decimal("0")
        total_base_salary_count = 0
        total_shift_accrual_total = Decimal("0")
        total_shift_accrual_count = 0
        total_work_total = Decimal("0")
        total_work_accrued_total = Decimal("0")
        total_material_total = Decimal("0")
        total_material_cost_total = Decimal("0")
        total_material_profit_total = Decimal("0")
        total_material_accrued_total = Decimal("0")
        total_repair_orders = 0
        total_works = 0
        total_materials = 0
        for day_key in sorted(grouped_days.keys(), reverse=True):
            day = grouped_days[day_key]
            day["repair_orders"].sort(
                key=lambda item: (item["closed_at_iso"], item["repair_order_number"]),
                reverse=True,
            )
            day_work_count = sum(int(item["work_count"]) for item in day["repair_orders"])
            day_material_count = sum(int(item["material_count"]) for item in day["repair_orders"])
            day_base_salary_count = len(day["base_salary_accruals"])
            day_shift_accrual_count = len(day.get("shift_accruals", []))
            day_order_count = len(day["repair_orders"])
            day_base_salary_total = day.pop("_base_salary_total")
            day_shift_accrual_total = day.pop("_shift_accrual_total")
            day_work_total = day.pop("_work_total")
            day_work_accrued_total = day.pop("_work_accrued_total")
            day_material_total = day.pop("_material_total")
            day_material_cost_total = day.pop("_material_cost_total")
            day_material_profit_total = day.pop("_material_profit_total")
            day_material_accrued_total = day.pop("_material_accrued_total")
            day["totals"] = self._employee_salary_report_totals_payload(
                repair_order_count=day_order_count,
                work_count=day_work_count,
                work_total=day_work_total,
                base_salary_count=day_base_salary_count,
                base_salary_total=day_base_salary_total,
                shift_accrual_count=day_shift_accrual_count,
                shift_accrual_total=day_shift_accrual_total,
                work_accrued_total=day_work_accrued_total,
                material_count=day_material_count,
                material_total=day_material_total,
                material_cost_total=day_material_cost_total,
                material_profit_total=day_material_profit_total,
                material_accrued_total=day_material_accrued_total,
            )
            days.append(day)
            total_base_salary_count += day_base_salary_count
            total_base_salary_total += day_base_salary_total
            total_shift_accrual_count += day_shift_accrual_count
            total_shift_accrual_total += day_shift_accrual_total
            total_repair_orders += day_order_count
            total_works += day_work_count
            total_materials += day_material_count
            total_work_total += day_work_total
            total_work_accrued_total += day_work_accrued_total
            total_material_total += day_material_total
            total_material_cost_total += day_material_cost_total
            total_material_profit_total += day_material_profit_total
            total_material_accrued_total += day_material_accrued_total

        totals = self._employee_salary_report_totals_payload(
            repair_order_count=total_repair_orders,
            work_count=total_works,
            work_total=total_work_total,
            base_salary_count=total_base_salary_count,
            base_salary_total=total_base_salary_total,
            shift_accrual_count=total_shift_accrual_count,
            shift_accrual_total=total_shift_accrual_total,
            work_accrued_total=total_work_accrued_total,
            material_count=total_materials,
            material_total=total_material_total,
            material_cost_total=total_material_cost_total,
            material_profit_total=total_material_profit_total,
            material_accrued_total=total_material_accrued_total,
        )
        text = self._employee_salary_report_text(
            employee=employee,
            period=period,
            totals=totals,
            days=days,
        )
        return {
            "period": period,
            "days": days,
            "totals": totals,
            "text": text,
            "markdown": text,
        }

    def get_employee_salary_report(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            employees = self._employees_from_settings(bundle["settings"])
            employees_by_id = {item["id"]: item for item in employees}
            shift_accruals = self._employee_shift_accruals_from_settings(
                bundle["settings"], employees_by_id=employees_by_id
            )
            employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
            if not employee_id:
                self._fail(
                    "validation_error",
                    "Нужно передать employee_id.",
                    details={"field": "employee_id"},
                )
            month = self._validated_payroll_month(payload.get("month"))
            employee = next((item for item in employees if item["id"] == employee_id), None)
            if employee is None:
                self._fail(
                    "not_found",
                    "Сотрудник не найден.",
                    status_code=404,
                    details={"employee_id": employee_id},
                )
            report = self._build_employee_salary_report(
                bundle["cards"], employee, month=month, shift_accruals=shift_accruals
            )
            return {
                "employee_id": employee_id,
                "employee_name": employee["name"],
                "period": report["period"],
                "file_name": normalize_file_name(
                    f"employee-accrual-report-{employee['name']}-{report['period']['month']}.md"
                ),
                "text": report["text"],
                "markdown": report["markdown"],
                "days": report["days"],
                "totals": report["totals"],
                "meta": {
                    "schema_version": "employee_salary_report.v3",
                    "month": report["period"]["month"],
                    "days_total": len(report["days"]),
                    "repair_order_total": report["totals"]["repair_order_count"],
                    "work_count": report["totals"]["work_count"],
                    "accrued_total": report["totals"]["accrued_total"],
                },
            }

    def list_employees(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            employees = self._employees_from_settings(bundle["settings"])
            employees_by_id = {item["id"]: item for item in employees}
            shift_accruals = self._employee_shift_accruals_from_settings(
                bundle["settings"], employees_by_id=employees_by_id
            )
            month = self._validated_payroll_month(payload.get("month"))
            report = self._build_payroll_report(
                bundle["cards"],
                employees,
                shift_accruals=shift_accruals,
                month=month,
            )
            cashboxes = bundle["cashboxes"]
            cash_transactions = bundle["cash_transactions"]
            employee_balances = {
                employee["id"]: self._build_employee_salary_ledger(
                    bundle["cards"],
                    cashboxes,
                    cash_transactions,
                    employee,
                    shift_accruals=shift_accruals,
                    months=6,
                )["balance_total"]
                for employee in employees
            }
            employees = [
                {
                    **employee,
                    "balance_total": employee_balances.get(employee["id"], "0"),
                }
                for employee in employees
            ]
            return {
                "employees": employees,
                "month": month,
                "summary": report["summary"],
                "detail_rows": report["detail_rows"],
            }

    def save_employee(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            actor_name, source = self._audit_identity(payload, default_source="api")
            settings = dict(bundle["settings"])
            employees = self._employees_from_settings(settings)
            create_mode = normalize_bool(payload.get("create_mode"), default=False)
            if create_mode:
                payload = dict(payload)
                payload.pop("employee_id", None)
                payload.pop("id", None)
            employee_id = (
                ""
                if create_mode
                else normalize_text(payload.get("employee_id"), default="", limit=64)
            )
            existing = next((item for item in employees if item["id"] == employee_id), None)
            created = existing is None
            if created and len(employees) >= EMPLOYEES_MAX_COUNT:
                self._fail(
                    "validation_error",
                    f"Можно сохранить не более {EMPLOYEES_MAX_COUNT} сотрудников.",
                    details={"field": EMPLOYEES_SETTING_KEY, "max_count": EMPLOYEES_MAX_COUNT},
                )
            employee = self._validated_employee_payload(payload, existing=existing)
            next_employees = [item for item in employees if item["id"] != employee["id"]]
            next_employees.append(employee)
            next_employees.sort(
                key=lambda item: (not item["is_active"], item["name"].casefold(), item["id"])
            )
            settings[EMPLOYEES_SETTING_KEY] = next_employees
            self._append_event(
                bundle["events"],
                actor_name=actor_name,
                source=source,
                action="employee_saved",
                message=f"{actor_name} {'добавил' if created else 'обновил'} сотрудника",
                card_id=None,
                details={"employee_id": employee["id"], "name": employee["name"]},
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
                events=bundle["events"],
                settings=settings,
            )
            return {"employee": employee, "employees": next_employees, "created": created}

    def toggle_employee(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            actor_name, source = self._audit_identity(payload, default_source="api")
            settings = dict(bundle["settings"])
            employees = self._employees_from_settings(settings)
            employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
            if not employee_id:
                self._fail(
                    "validation_error",
                    "Нужно передать employee_id.",
                    details={"field": "employee_id"},
                )
            target = next((item for item in employees if item["id"] == employee_id), None)
            if target is None:
                self._fail(
                    "not_found",
                    "Сотрудник не найден.",
                    status_code=404,
                    details={"employee_id": employee_id},
                )
            target["is_active"] = not bool(target.get("is_active"))
            target["updated_at"] = model_helpers.utc_now_iso()
            settings[EMPLOYEES_SETTING_KEY] = employees
            self._append_event(
                bundle["events"],
                actor_name=actor_name,
                source=source,
                action="employee_toggled",
                message=f"{actor_name} изменил активность сотрудника",
                card_id=None,
                details={"employee_id": target["id"], "is_active": target["is_active"]},
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
                events=bundle["events"],
                settings=settings,
            )
            return {"employee": target, "employees": employees}

    def delete_employee(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            bundle = self._store.read_bundle()
            actor_name, source = self._audit_identity(payload, default_source="api")
            settings = dict(bundle["settings"])
            employees = self._employees_from_settings(settings)
            employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
            if not employee_id:
                self._fail(
                    "validation_error",
                    "РќСѓР¶РЅРѕ РїРµСЂРµРґР°С‚СЊ employee_id.",
                    details={"field": "employee_id"},
                )
            target = next((item for item in employees if item["id"] == employee_id), None)
            if target is None:
                self._fail(
                    "not_found",
                    "РЎРѕС‚СЂСѓРґРЅРёРє РЅРµ РЅР°Р№РґРµРЅ.",
                    status_code=404,
                    details={"employee_id": employee_id},
                )
            next_employees = [item for item in employees if item["id"] != employee_id]
            settings[EMPLOYEES_SETTING_KEY] = next_employees
            self._append_event(
                bundle["events"],
                actor_name=actor_name,
                source=source,
                action="employee_deleted",
                message=f"{actor_name} СѓРґР°Р»РёР» СЃРѕС‚СЂСѓРґРЅРёРєР°",
                card_id=None,
                details={"employee_id": employee_id, "name": target["name"]},
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
                events=bundle["events"],
                settings=settings,
            )
            return {"deleted": True, "employee_id": employee_id, "employees": next_employees}

    def _parse_payroll_decimal(self, value, *, default: Decimal = Decimal("0")) -> Decimal:
        raw = normalize_text(value, default="", limit=40).replace(" ", "").replace(",", ".")
        if not raw:
            return default
        try:
            return Decimal(raw)
        except InvalidOperation:
            return default

    def _format_payroll_decimal(self, value: Decimal) -> str:
        quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        text = format(quantized, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    def _repair_order_row_decimal_or_none(self, value: object) -> Decimal | None:
        text = normalize_text(value, default="", limit=40)
        if not text:
            return None
        try:
            return Decimal(text.replace(" ", "").replace(",", "."))
        except InvalidOperation:
            return None

    def _material_cost_total(self, row: RepairOrderRow) -> Decimal | None:
        quantity_source = row.material_quantity_snapshot or row.quantity
        cost_source = row.material_cost_price_snapshot or row.cost_price
        quantity = self._repair_order_row_decimal_or_none(quantity_source)
        cost_price = self._repair_order_row_decimal_or_none(cost_source)
        if quantity is None or cost_price is None:
            return None
        return quantity * cost_price

    def _material_sale_total(self, row: RepairOrderRow) -> Decimal:
        quantity_source = row.material_quantity_snapshot or row.quantity
        price_source = row.material_price_snapshot or row.price
        quantity = self._repair_order_row_decimal_or_none(quantity_source)
        price = self._repair_order_row_decimal_or_none(price_source)
        if quantity is None or price is None:
            return row.total_value()
        return quantity * price

    def _work_salary_override_enabled(self, row: RepairOrderRow) -> bool:
        return normalize_text(
            row.work_salary_override_enabled, default="", limit=16
        ).casefold() in {
            "1",
            "true",
            "yes",
            "on",
            "да",
        }

    def _work_salary_guarantee(self, row: RepairOrderRow) -> Decimal:
        return max(self._parse_payroll_decimal(row.work_salary_guarantee), Decimal("0"))

    def _work_salary_override_percent(self, row: RepairOrderRow) -> Decimal:
        percent = self._parse_payroll_decimal(row.work_salary_percent_override)
        return min(max(percent, Decimal("0")), Decimal("100"))

    def _work_salary_override_amount(self, row: RepairOrderRow) -> tuple[Decimal, Decimal]:
        guarantee = self._work_salary_guarantee(row)
        percent = self._work_salary_override_percent(row)
        percent_base = max(row.total_value() - guarantee, Decimal("0"))
        return guarantee + (percent_base * percent / Decimal("100")), percent

    def _work_salary_scheme(self, row: RepairOrderRow) -> str:
        if self._work_salary_override_enabled(row):
            guarantee = self._work_salary_guarantee(row)
            percent = self._work_salary_override_percent(row)
            return (
                "Гарантия "
                + self._employee_salary_report_money(guarantee)["display"]
                + " + "
                + self._format_payroll_decimal(percent)
                + "%"
            )
        percent = normalize_text(row.work_percent_snapshot, default="", limit=40)
        return f"Работы {percent}%" if percent else "Работы"

    def _material_has_salary_snapshot(self, row: RepairOrderRow) -> bool:
        return any(
            [
                row.material_executor_id_snapshot,
                row.material_executor_name_snapshot,
                row.material_quantity_snapshot,
                row.material_price_snapshot,
                row.material_cost_price_snapshot,
                row.material_percent_snapshot,
                row.material_profit,
                row.material_salary_amount,
                row.material_salary_accrued_at,
            ]
        )

    def _clear_material_salary_snapshot(self, row: RepairOrderRow) -> None:
        row.material_executor_id_snapshot = ""
        row.material_executor_name_snapshot = ""
        row.material_quantity_snapshot = ""
        row.material_price_snapshot = ""
        row.material_cost_price_snapshot = ""
        row.material_percent_snapshot = ""
        row.material_profit = ""
        row.material_salary_amount = ""
        row.material_salary_accrued_at = ""

    def _material_salary_employee_id(self, row: RepairOrderRow) -> str:
        return row.material_executor_id_snapshot or row.executor_id

    def _material_salary_employee_name(self, row: RepairOrderRow) -> str:
        return row.material_executor_name_snapshot or row.executor_name

    def _normalize_payroll_mode(self, value, *, default: str = PAYROLL_MODE_PERCENT_ONLY) -> str:
        normalized = normalize_text(value, default=default, limit=32).lower()
        if normalized not in PAYROLL_ALLOWED_MODES:
            return default
        return normalized

    def _validated_payroll_month(self, value) -> str:
        normalized = normalize_text(value, default="", limit=7)
        if re.fullmatch(r"\d{4}-\d{2}", normalized):
            return normalized
        return model_helpers.utc_now().astimezone(business_timezone()).strftime("%Y-%m")

    def _payroll_month_bounds(self, month: str) -> tuple[datetime, datetime]:
        normalized_month = self._validated_payroll_month(month)
        year, month_number = [int(part) for part in normalized_month.split("-", 1)]
        timezone = business_timezone()
        period_start = datetime(year, month_number, 1, tzinfo=timezone)
        if month_number == 12:
            period_end = datetime(year + 1, 1, 1, tzinfo=timezone)
        else:
            period_end = datetime(year, month_number + 1, 1, tzinfo=timezone)
        return period_start, period_end

    def _employee_has_weekly_base_salary(self, employee: dict[str, Any]) -> bool:
        salary_mode = self._normalize_payroll_mode(employee.get("salary_mode"))
        if salary_mode not in {PAYROLL_MODE_SALARY_ONLY, PAYROLL_MODE_SALARY_PLUS_PERCENT}:
            return False
        return self._parse_payroll_decimal(employee.get("base_salary", "")) > Decimal("0")

    def _employee_weekly_base_salary_accruals(
        self,
        employee: dict[str, Any],
        *,
        period_start: datetime,
        period_end: datetime,
        as_of: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if not self._employee_has_weekly_base_salary(employee):
            return []
        timezone = business_timezone()
        start_at = period_start.astimezone(timezone)
        end_at = period_end.astimezone(timezone)
        as_of_at = (as_of or model_helpers.utc_now()).astimezone(timezone)
        created_at = parse_datetime(employee.get("created_at"))
        if created_at is not None:
            start_at = max(start_at, created_at.astimezone(timezone))
        if end_at <= start_at:
            return []
        amount = self._parse_payroll_decimal(employee.get("base_salary", ""))
        days_until_friday = (PAYROLL_WEEKLY_BASE_SALARY_WEEKDAY - start_at.weekday()) % 7
        candidate_date = (start_at + timedelta(days=days_until_friday)).date()
        candidate = datetime(
            candidate_date.year,
            candidate_date.month,
            candidate_date.day,
            PAYROLL_WEEKLY_BASE_SALARY_HOUR,
            PAYROLL_WEEKLY_BASE_SALARY_MINUTE,
            tzinfo=timezone,
        )
        if candidate < start_at:
            candidate += timedelta(days=7)
        accruals: list[dict[str, Any]] = []
        while candidate < end_at and candidate <= as_of_at:
            accruals.append({"accrued_at": candidate, "amount": amount})
            candidate += timedelta(days=7)
        return accruals

    def _normalized_employee_shift_accrual(
        self,
        payload: Any,
        *,
        employees_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
        if not employee_id:
            return None
        amount_minor = normalize_money_minor(payload.get("amount_minor"), default=0)
        if amount_minor < 1:
            amount_minor = normalize_money_minor(payload.get("amount"), default=0)
        if amount_minor < 1:
            return None
        employee = (employees_by_id or {}).get(employee_id)
        amount = Decimal(amount_minor) / Decimal("100")
        created_at = parse_business_datetime(payload.get("created_at")) or model_helpers.utc_now()
        updated_at = parse_business_datetime(payload.get("updated_at")) or created_at
        note = self._validated_cash_transaction_note(
            payload.get("note") or EMPLOYEE_SHIFT_ACCRUAL_NOTE
        )
        return {
            "id": normalize_text(payload.get("id") or str(uuid.uuid4()), default="", limit=64)
            or str(uuid.uuid4()),
            "employee_id": employee_id,
            "employee_name": normalize_text(
                payload.get("employee_name"),
                default=employee.get("name", "") if employee else "",
                limit=80,
            ),
            "amount": self._format_payroll_decimal(amount),
            "amount_minor": amount_minor,
            "note": note,
            "created_at": created_at.isoformat(),
            "updated_at": updated_at.isoformat(),
            "actor_name": normalize_actor_name(payload.get("actor_name")),
            "source": normalize_source(payload.get("source"), default="api"),
        }

    def _employee_shift_accrual_storage_payload(self, accrual: dict[str, Any]) -> dict[str, Any]:
        amount_minor = normalize_money_minor(accrual.get("amount_minor"), default=0)
        if amount_minor < 1:
            amount_minor = normalize_money_minor(accrual.get("amount"), default=0)
        amount = Decimal(amount_minor) / Decimal("100") if amount_minor >= 1 else Decimal("0")
        return {
            "id": normalize_text(accrual.get("id") or str(uuid.uuid4()), default="", limit=64)
            or str(uuid.uuid4()),
            "employee_id": normalize_text(accrual.get("employee_id"), default="", limit=64),
            "employee_name": normalize_text(accrual.get("employee_name"), default="", limit=80),
            "amount": self._format_payroll_decimal(amount),
            "amount_minor": amount_minor,
            "note": self._validated_cash_transaction_note(
                accrual.get("note") or EMPLOYEE_SHIFT_ACCRUAL_NOTE
            ),
            "created_at": normalize_text(accrual.get("created_at"), default="", limit=64),
            "updated_at": normalize_text(accrual.get("updated_at"), default="", limit=64),
            "actor_name": normalize_actor_name(accrual.get("actor_name")),
            "source": normalize_source(accrual.get("source"), default="api"),
        }

    def _serialize_employee_shift_accrual(self, accrual: dict[str, Any]) -> dict[str, Any]:
        payload = self._employee_shift_accrual_storage_payload(accrual)
        amount_minor = normalize_money_minor(payload.get("amount_minor"), default=0)
        return {**payload, "amount_display": format_money_minor(amount_minor)}

    def _employee_shift_accruals_from_settings(
        self,
        settings: dict[str, Any],
        *,
        employees_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        raw_items = settings.get(EMPLOYEE_SHIFT_ACCRUALS_SETTING_KEY)
        if not isinstance(raw_items, list):
            return []
        accruals: list[dict[str, Any]] = []
        for item in raw_items:
            normalized = self._normalized_employee_shift_accrual(
                item, employees_by_id=employees_by_id
            )
            if normalized is None:
                continue
            accruals.append(normalized)
        accruals.sort(
            key=lambda item: (
                self._repair_order_sortable_datetime(item.get("created_at")),
                item.get("id") or "",
            )
        )
        return accruals

    def _normalized_employee_record(
        self, payload: Any, *, existing: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        now_iso = model_helpers.utc_now_iso()
        existing = existing or {}
        employee_id = normalize_text(
            payload.get("id")
            or payload.get("employee_id")
            or existing.get("id")
            or str(uuid.uuid4()),
            default="",
            limit=64,
        )
        name = normalize_text(payload.get("name"), default=existing.get("name", ""), limit=80)
        if not employee_id or not name:
            return None
        position = normalize_text(
            payload.get("position"), default=existing.get("position", ""), limit=80
        )
        salary_mode = self._normalize_payroll_mode(
            payload.get("salary_mode", existing.get("salary_mode"))
        )
        base_salary = self._format_payroll_decimal(
            self._parse_payroll_decimal(payload.get("base_salary", existing.get("base_salary", "")))
        )
        work_percent = self._format_payroll_decimal(
            self._parse_payroll_decimal(
                payload.get("work_percent", existing.get("work_percent", ""))
            )
        )
        material_percent = self._format_payroll_decimal(
            self._parse_payroll_decimal(
                payload.get(
                    "material_percent",
                    existing.get("material_percent", DEFAULT_MATERIAL_PERCENT),
                )
            )
        )
        note = normalize_text(payload.get("note"), default=existing.get("note", ""), limit=240)
        is_active = normalize_bool(
            payload.get("is_active"),
            default=normalize_bool(existing.get("is_active"), default=True),
        )
        created_at = (
            normalize_text(
                existing.get("created_at") or payload.get("created_at"),
                default=now_iso,
                limit=40,
            )
            or now_iso
        )
        updated_at = (
            normalize_text(
                payload.get("updated_at"), default=existing.get("updated_at", now_iso), limit=40
            )
            or now_iso
        )
        return {
            "id": employee_id,
            "name": name,
            "position": position,
            "salary_mode": salary_mode,
            "base_salary": base_salary,
            "work_percent": work_percent,
            "material_percent": material_percent,
            "is_active": is_active,
            "note": note,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def _employees_from_settings(self, settings: dict[str, Any]) -> list[dict[str, Any]]:
        raw_items = settings.get(EMPLOYEES_SETTING_KEY)
        if not isinstance(raw_items, list):
            return []
        employees: list[dict[str, Any]] = []
        for item in raw_items:
            normalized = self._normalized_employee_record(item)
            if normalized is None:
                continue
            employees.append(normalized)
        employees.sort(
            key=lambda item: (not item["is_active"], item["name"].casefold(), item["id"])
        )
        return employees

    def _validated_employee_payload(
        self, payload: dict[str, Any], *, existing: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        employee = self._normalized_employee_record(payload, existing=existing)
        if employee is None:
            self._fail(
                "validation_error", "Нужно указать имя сотрудника.", details={"field": "name"}
            )
        employee["updated_at"] = model_helpers.utc_now_iso()
        return employee

    def _apply_repair_order_payroll_snapshot(
        self, order: RepairOrder, settings: dict[str, Any]
    ) -> RepairOrder:
        if order.status != REPAIR_ORDER_STATUS_CLOSED:
            next_work_rows: list[dict[str, str]] = []
            next_material_rows: list[dict[str, str]] = []
            changed = False
            for source_row in order.works:
                row = RepairOrderRow.from_dict(
                    source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
                )
                if any(
                    [
                        row.salary_mode_snapshot,
                        row.base_salary_snapshot,
                        row.work_percent_snapshot,
                        row.salary_amount,
                        row.salary_accrued_at,
                    ]
                ):
                    changed = True
                row.salary_mode_snapshot = ""
                row.base_salary_snapshot = ""
                row.work_percent_snapshot = ""
                row.salary_amount = ""
                row.salary_accrued_at = ""
                next_work_rows.append(row.to_dict())
            for source_row in order.materials:
                row = RepairOrderRow.from_dict(
                    source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
                )
                if self._material_has_salary_snapshot(row):
                    changed = True
                self._clear_material_salary_snapshot(row)
                next_material_rows.append(row.to_dict())
            if not changed:
                return order
            return RepairOrder.from_dict(
                {
                    **order.to_storage_dict(),
                    "works": next_work_rows,
                    "materials": next_material_rows,
                }
            )
        employees_by_id = {item["id"]: item for item in self._employees_from_settings(settings)}
        next_work_rows: list[dict[str, str]] = []
        accrued_at = order.closed_at or self._repair_order_now()
        for source_row in order.works:
            row = RepairOrderRow.from_dict(
                source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
            )
            employee = employees_by_id.get(row.executor_id)
            if employee is None:
                row.salary_mode_snapshot = ""
                row.base_salary_snapshot = ""
                row.work_percent_snapshot = ""
                row.salary_amount = ""
                row.salary_accrued_at = ""
                next_work_rows.append(row.to_dict())
                continue
            row.executor_name = employee["name"]
            row.salary_mode_snapshot = employee["salary_mode"]
            row.base_salary_snapshot = employee["base_salary"]
            salary_amount = Decimal("0")
            if self._work_salary_override_enabled(row):
                salary_amount, applied_percent = self._work_salary_override_amount(row)
                row.work_percent_snapshot = self._format_payroll_decimal(applied_percent)
            elif employee["salary_mode"] in {
                PAYROLL_MODE_PERCENT_ONLY,
                PAYROLL_MODE_SALARY_PLUS_PERCENT,
            }:
                row.work_percent_snapshot = employee["work_percent"]
                salary_amount = (
                    row.total_value()
                    * self._parse_payroll_decimal(employee["work_percent"])
                    / Decimal("100")
                )
            else:
                row.work_percent_snapshot = employee["work_percent"]
            row.salary_amount = self._format_payroll_decimal(salary_amount)
            row.salary_accrued_at = accrued_at
            next_work_rows.append(row.to_dict())
        next_material_rows: list[dict[str, str]] = []
        order_is_paid = order.is_paid()
        for source_row in order.materials:
            row = RepairOrderRow.from_dict(
                source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
            )
            if not order_is_paid:
                self._clear_material_salary_snapshot(row)
                next_material_rows.append(row.to_dict())
                continue
            if row.material_salary_accrued_at:
                row.material_executor_id_snapshot = (
                    row.material_executor_id_snapshot or row.executor_id
                )
                row.material_executor_name_snapshot = (
                    row.material_executor_name_snapshot or row.executor_name
                )
                row.material_quantity_snapshot = row.material_quantity_snapshot or row.quantity
                next_material_rows.append(row.to_dict())
                continue
            employee = employees_by_id.get(row.executor_id)
            cost_total = self._material_cost_total(row)
            if employee is None or cost_total is None:
                self._clear_material_salary_snapshot(row)
                next_material_rows.append(row.to_dict())
                continue
            material_percent = self._parse_payroll_decimal(employee.get("material_percent", ""))
            profit = max(row.total_value() - cost_total, Decimal("0"))
            salary_amount = profit * material_percent / Decimal("100")
            row.executor_name = employee["name"]
            row.material_executor_id_snapshot = employee["id"]
            row.material_executor_name_snapshot = employee["name"]
            row.material_quantity_snapshot = row.quantity
            row.material_price_snapshot = row.price
            row.material_cost_price_snapshot = row.cost_price
            row.material_percent_snapshot = self._format_payroll_decimal(material_percent)
            row.material_profit = self._format_payroll_decimal(profit)
            row.material_salary_amount = self._format_payroll_decimal(salary_amount)
            row.material_salary_accrued_at = accrued_at
            next_material_rows.append(row.to_dict())
        return RepairOrder.from_dict(
            {**order.to_storage_dict(), "works": next_work_rows, "materials": next_material_rows}
        )

    def _build_payroll_report(
        self,
        cards: list[Card],
        employees: list[dict[str, Any]],
        *,
        shift_accruals: list[dict[str, Any]] | None = None,
        month: str,
        employee_id: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        selected_employee_id = normalize_text(employee_id, default="", limit=64)
        month_key = month.replace("-", "")
        period_start, period_end = self._payroll_month_bounds(month)
        employees_by_id = {item["id"]: item for item in employees}

        def empty_summary(employee: dict[str, Any]) -> dict[str, Any]:
            base_salary = self._parse_payroll_decimal(employee.get("base_salary", ""))
            return {
                "employee_id": employee["id"],
                "employee_name": employee["name"],
                "position": employee.get("position", ""),
                "salary_mode": employee.get("salary_mode", ""),
                "work_percent": employee.get("work_percent", ""),
                "material_percent": employee.get("material_percent", DEFAULT_MATERIAL_PERCENT),
                "base_salary": self._format_payroll_decimal(base_salary),
                "base_salary_accruals_count": 0,
                "base_salary_accrued_total": Decimal("0"),
                "shift_accruals_count": 0,
                "shift_accrued_total": Decimal("0"),
                "works_count": 0,
                "works_total": Decimal("0"),
                "work_accrued_total": Decimal("0"),
                "materials_count": 0,
                "materials_total": Decimal("0"),
                "materials_cost_total": Decimal("0"),
                "materials_profit_total": Decimal("0"),
                "materials_accrued_total": Decimal("0"),
            }

        summaries: dict[str, dict[str, Any]] = {}
        for employee in employees:
            if selected_employee_id and employee["id"] != selected_employee_id:
                continue
            summaries[employee["id"]] = empty_summary(employee)
        detail_rows_by_order: dict[tuple[str, str], dict[str, Any]] = {}
        base_salary_detail_rows: list[dict[str, Any]] = []
        for employee in employees:
            if selected_employee_id and employee["id"] != selected_employee_id:
                continue
            summary = summaries.get(employee["id"])
            if summary is None:
                continue
            for accrual in self._employee_weekly_base_salary_accruals(
                employee,
                period_start=period_start,
                period_end=period_end,
            ):
                amount = accrual["amount"]
                accrued_at = accrual["accrued_at"]
                summary["base_salary_accruals_count"] += 1
                summary["base_salary_accrued_total"] += amount
                base_salary_detail_rows.append(
                    {
                        "row_type": "base_salary",
                        "type_label": "Оклад",
                        "employee_id": employee["id"],
                        "employee_name": employee["name"],
                        "closed_at": accrued_at.strftime("%d.%m.%Y %H:%M"),
                        "repair_order_number": "",
                        "card_id": "",
                        "vehicle": "",
                        "works_count": 0,
                        "work_total": Decimal("0"),
                        "materials_count": 0,
                        "material_name": "Недельный оклад",
                        "material_total": Decimal("0"),
                        "material_cost_total": Decimal("0"),
                        "material_profit": Decimal("0"),
                        "material_percent": "",
                        "salary_amount": amount,
                    }
                )
        shift_detail_rows: list[dict[str, Any]] = []
        for shift_accrual in shift_accruals or []:
            current_employee_id = normalize_text(
                shift_accrual.get("employee_id"), default="", limit=64
            )
            if not current_employee_id:
                continue
            if selected_employee_id and current_employee_id != selected_employee_id:
                continue
            created_at = parse_business_datetime(shift_accrual.get("created_at"))
            if created_at is None:
                continue
            created_at = created_at.astimezone(business_timezone())
            if created_at < period_start or created_at >= period_end:
                continue
            amount = Decimal(normalize_money_minor(shift_accrual.get("amount_minor"))) / Decimal(
                "100"
            )
            if amount <= Decimal("0"):
                continue
            if current_employee_id not in summaries:
                employee = employees_by_id.get(current_employee_id, {})
                summaries[current_employee_id] = {
                    "employee_id": current_employee_id,
                    "employee_name": shift_accrual.get("employee_name")
                    or employee.get("name")
                    or "Сотрудник",
                    "position": employee.get("position", ""),
                    "salary_mode": employee.get("salary_mode", ""),
                    "work_percent": employee.get("work_percent", ""),
                    "material_percent": employee.get("material_percent", ""),
                    "base_salary": employee.get("base_salary", "0"),
                    "base_salary_accruals_count": 0,
                    "base_salary_accrued_total": Decimal("0"),
                    "shift_accruals_count": 0,
                    "shift_accrued_total": Decimal("0"),
                    "works_count": 0,
                    "works_total": Decimal("0"),
                    "work_accrued_total": Decimal("0"),
                    "materials_count": 0,
                    "materials_total": Decimal("0"),
                    "materials_cost_total": Decimal("0"),
                    "materials_profit_total": Decimal("0"),
                    "materials_accrued_total": Decimal("0"),
                }
            summary = summaries[current_employee_id]
            note = shift_accrual.get("note") or EMPLOYEE_SHIFT_ACCRUAL_NOTE
            summary["shift_accruals_count"] += 1
            summary["shift_accrued_total"] += amount
            shift_detail_rows.append(
                {
                    "row_type": "shift_accrual",
                    "type_label": "Смены",
                    "employee_id": current_employee_id,
                    "employee_name": summary["employee_name"],
                    "closed_at": created_at.strftime("%d.%m.%Y %H:%M"),
                    "repair_order_number": "",
                    "card_id": "",
                    "vehicle": "",
                    "works_count": 0,
                    "work_total": Decimal("0"),
                    "materials_count": 0,
                    "material_name": note,
                    "material_total": Decimal("0"),
                    "material_cost_total": Decimal("0"),
                    "material_profit": Decimal("0"),
                    "material_percent": "",
                    "salary_amount": amount,
                }
            )
        material_detail_rows: list[dict[str, Any]] = []
        for card in cards:
            order = card.repair_order
            if order.status != REPAIR_ORDER_STATUS_CLOSED:
                continue
            closed_sort_key = self._repair_order_closed_sort_value(card)
            if not closed_sort_key.startswith(month_key):
                continue
            for source_row in order.works:
                row = RepairOrderRow.from_dict(
                    source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
                )
                current_employee_id = row.executor_id
                if not current_employee_id:
                    continue
                if selected_employee_id and current_employee_id != selected_employee_id:
                    continue
                if current_employee_id not in summaries:
                    summaries[current_employee_id] = {
                        "employee_id": current_employee_id,
                        "employee_name": self._material_salary_employee_name(row) or "Сотрудник",
                        "position": "",
                        "salary_mode": row.salary_mode_snapshot,
                        "work_percent": row.work_percent_snapshot,
                        "material_percent": "",
                        "base_salary": "0",
                        "base_salary_accruals_count": 0,
                        "base_salary_accrued_total": Decimal("0"),
                        "shift_accruals_count": 0,
                        "shift_accrued_total": Decimal("0"),
                        "works_count": 0,
                        "works_total": Decimal("0"),
                        "work_accrued_total": Decimal("0"),
                        "materials_count": 0,
                        "materials_total": Decimal("0"),
                        "materials_cost_total": Decimal("0"),
                        "materials_profit_total": Decimal("0"),
                        "materials_accrued_total": Decimal("0"),
                    }
                summary = summaries[current_employee_id]
                work_total = row.total_value()
                accrued_total = self._parse_payroll_decimal(row.salary_amount)
                summary["works_count"] += 1
                summary["works_total"] += work_total
                summary["work_accrued_total"] += accrued_total
                detail_key = (current_employee_id, card.id)
                detail_row = detail_rows_by_order.setdefault(
                    detail_key,
                    {
                        "row_type": "work",
                        "type_label": "Работа",
                        "employee_id": current_employee_id,
                        "employee_name": summary["employee_name"],
                        "closed_at": order.closed_at,
                        "repair_order_number": order.number,
                        "card_id": card.id,
                        "vehicle": order.vehicle or card.vehicle,
                        "works_count": 0,
                        "work_total": Decimal("0"),
                        "salary_amount": Decimal("0"),
                        "materials_count": 0,
                        "material_name": "",
                        "material_total": Decimal("0"),
                        "material_cost_total": Decimal("0"),
                        "material_profit": Decimal("0"),
                        "material_percent": "",
                    },
                )
                detail_row["works_count"] += 1
                detail_row["work_total"] += work_total
                detail_row["salary_amount"] += accrued_total
            for source_row in order.materials:
                row = RepairOrderRow.from_dict(
                    source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
                )
                current_employee_id = self._material_salary_employee_id(row)
                if not current_employee_id or not row.material_salary_accrued_at:
                    continue
                if selected_employee_id and current_employee_id != selected_employee_id:
                    continue
                if current_employee_id not in summaries:
                    summaries[current_employee_id] = {
                        "employee_id": current_employee_id,
                        "employee_name": self._material_salary_employee_name(row) or "Сотрудник",
                        "position": "",
                        "salary_mode": "",
                        "work_percent": "",
                        "material_percent": row.material_percent_snapshot,
                        "base_salary": "0",
                        "base_salary_accruals_count": 0,
                        "base_salary_accrued_total": Decimal("0"),
                        "shift_accruals_count": 0,
                        "shift_accrued_total": Decimal("0"),
                        "works_count": 0,
                        "works_total": Decimal("0"),
                        "work_accrued_total": Decimal("0"),
                        "materials_count": 0,
                        "materials_total": Decimal("0"),
                        "materials_cost_total": Decimal("0"),
                        "materials_profit_total": Decimal("0"),
                        "materials_accrued_total": Decimal("0"),
                    }
                summary = summaries[current_employee_id]
                material_total = self._material_sale_total(row)
                material_cost_total = self._material_cost_total(row) or Decimal("0")
                material_profit = self._parse_payroll_decimal(row.material_profit)
                accrued_total = self._parse_payroll_decimal(row.material_salary_amount)
                summary["materials_count"] += 1
                summary["materials_total"] += material_total
                summary["materials_cost_total"] += material_cost_total
                summary["materials_profit_total"] += material_profit
                summary["materials_accrued_total"] += accrued_total
                material_detail_rows.append(
                    {
                        "row_type": "material",
                        "type_label": "Материал",
                        "employee_id": current_employee_id,
                        "employee_name": summary["employee_name"],
                        "closed_at": order.closed_at,
                        "repair_order_number": order.number,
                        "card_id": card.id,
                        "vehicle": order.vehicle or card.vehicle,
                        "works_count": 0,
                        "work_total": Decimal("0"),
                        "materials_count": 1,
                        "material_name": row.name,
                        "material_total": material_total,
                        "material_cost_total": material_cost_total,
                        "material_profit": material_profit,
                        "material_percent": row.material_percent_snapshot,
                        "salary_amount": accrued_total,
                    }
                )
        summary_rows: list[dict[str, Any]] = []
        for item in summaries.values():
            base_salary = self._parse_payroll_decimal(item["base_salary"])
            base_salary_accrued_total = item["base_salary_accrued_total"]
            shift_accrued_total = item["shift_accrued_total"]
            works_total = item["works_total"]
            work_accrued_total = item["work_accrued_total"]
            materials_total = item["materials_total"]
            materials_cost_total = item["materials_cost_total"]
            materials_profit_total = item["materials_profit_total"]
            materials_accrued_total = item["materials_accrued_total"]
            accrued_total = (
                base_salary_accrued_total
                + shift_accrued_total
                + work_accrued_total
                + materials_accrued_total
            )
            total_salary = accrued_total
            summary_rows.append(
                {
                    "employee_id": item["employee_id"],
                    "employee_name": item["employee_name"],
                    "position": item["position"],
                    "salary_mode": item["salary_mode"],
                    "work_percent": item["work_percent"],
                    "material_percent": item["material_percent"],
                    "base_salary": self._format_payroll_decimal(base_salary),
                    "base_salary_accruals_count": item["base_salary_accruals_count"],
                    "base_salary_accrued_total": self._format_payroll_decimal(
                        base_salary_accrued_total
                    ),
                    "shift_accruals_count": item["shift_accruals_count"],
                    "shift_accrued_total": self._format_payroll_decimal(shift_accrued_total),
                    "works_count": item["works_count"],
                    "works_total": self._format_payroll_decimal(works_total),
                    "work_accrued_total": self._format_payroll_decimal(work_accrued_total),
                    "materials_count": item["materials_count"],
                    "materials_total": self._format_payroll_decimal(materials_total),
                    "materials_cost_total": self._format_payroll_decimal(materials_cost_total),
                    "materials_profit_total": self._format_payroll_decimal(materials_profit_total),
                    "materials_accrued_total": self._format_payroll_decimal(
                        materials_accrued_total
                    ),
                    "accrued_total": self._format_payroll_decimal(accrued_total),
                    "total_salary": self._format_payroll_decimal(total_salary),
                }
            )
        summary_rows.sort(
            key=lambda item: (Decimal(item["total_salary"] or "0"), item["employee_name"]),
            reverse=True,
        )
        detail_rows: list[dict[str, Any]] = []
        for item in (
            base_salary_detail_rows
            + shift_detail_rows
            + list(detail_rows_by_order.values())
            + material_detail_rows
        ):
            detail_rows.append(
                {
                    "row_type": item["row_type"],
                    "type_label": item["type_label"],
                    "employee_id": item["employee_id"],
                    "employee_name": item["employee_name"],
                    "closed_at": item["closed_at"],
                    "repair_order_number": item["repair_order_number"],
                    "card_id": item["card_id"],
                    "vehicle": item["vehicle"],
                    "works_count": item["works_count"],
                    "work_total": self._format_payroll_decimal(item["work_total"]),
                    "materials_count": item["materials_count"],
                    "material_name": item["material_name"],
                    "material_total": self._format_payroll_decimal(item["material_total"]),
                    "material_cost_total": self._format_payroll_decimal(
                        item["material_cost_total"]
                    ),
                    "material_profit": self._format_payroll_decimal(item["material_profit"]),
                    "material_percent": item["material_percent"],
                    "salary_amount": self._format_payroll_decimal(item["salary_amount"]),
                }
            )
        detail_rows.sort(
            key=lambda item: (
                self._repair_order_sortable_datetime(item["closed_at"]),
                item["repair_order_number"],
                item["vehicle"],
                item["row_type"],
                item.get("material_name") or "",
            ),
            reverse=True,
        )
        return {"summary": summary_rows, "detail_rows": detail_rows}
