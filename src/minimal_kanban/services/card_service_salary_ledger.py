from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .. import models as model_helpers
from ..models import (
    Card,
    CashBox,
    CashTransaction,
    business_timezone,
    format_money_minor,
    normalize_actor_name,
    normalize_money_minor,
    normalize_source,
    normalize_text,
    parse_business_datetime,
    parse_datetime,
)
from ..operator_permissions import (
    SALARY_BALANCE_RESET_PERMISSION,
    operator_has_permission,
)
from ..repair_order import REPAIR_ORDER_STATUS_CLOSED, RepairOrderRow
from .payroll_constants import (
    EMPLOYEE_SHIFT_ACCRUAL_NOTE,
)
from .payroll_constants import (
    repair_order_payroll_scheme as _repair_order_payroll_scheme,
)

EMPLOYEE_SALARY_BALANCE_RESETS_SETTING_KEY = "employee_salary_balance_resets"


class CardServiceSalaryLedgerMixin:
    def _employee_salary_ledger_revision(
        self,
        cards: list[Card],
        cash_transactions: list[CashTransaction],
        employee: dict[str, Any],
        *,
        shift_accruals: list[dict[str, Any]],
        repair_order_accruals: list[dict[str, Any]],
        salary_balance_resets: list[dict[str, Any]],
        as_of: datetime,
        period_start: datetime,
    ) -> str:
        employee_id = employee["id"]
        weekly_base_start = parse_datetime(employee.get("created_at")) or period_start
        weekly_sources = [
            {
                "accrued_at": item["accrued_at"].isoformat(),
                "amount": self._format_payroll_decimal(item["amount"]),
            }
            for item in self._employee_weekly_base_salary_accruals(
                employee,
                period_start=weekly_base_start,
                period_end=as_of + timedelta(seconds=1),
                as_of=as_of,
            )
        ]
        card_sources: list[dict[str, Any]] = []
        for card in cards:
            order = card.repair_order
            if order.payroll_postings:
                postings = [
                    dict(item)
                    for item in order.payroll_postings
                    if item.get("employee_id") == employee_id
                ]
                if postings:
                    card_sources.append(
                        {
                            "card_id": card.id,
                            "status": order.status,
                            "closed_at": order.closed_at,
                            "postings": postings,
                        }
                    )
                continue
            works = []
            for source_row in order.works:
                row = RepairOrderRow.from_dict(
                    source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
                )
                if self._work_salary_employee_id(row) == employee_id:
                    works.append(row.to_dict())
            materials = []
            for source_row in order.materials:
                row = RepairOrderRow.from_dict(
                    source_row.to_dict() if isinstance(source_row, RepairOrderRow) else source_row
                )
                if self._material_salary_employee_id(row) == employee_id:
                    materials.append(row.to_dict())
            if works or materials:
                card_sources.append(
                    {
                        "card_id": card.id,
                        "status": order.status,
                        "closed_at": order.closed_at,
                        "works": works,
                        "materials": materials,
                    }
                )
        payload = {
            "employee": employee,
            "weekly": weekly_sources,
            "shift_accruals": [
                item for item in shift_accruals if item.get("employee_id") == employee_id
            ],
            "repair_order_accruals": [
                item for item in repair_order_accruals if item.get("employee_id") == employee_id
            ],
            "salary_balance_resets": [
                item for item in salary_balance_resets if item.get("employee_id") == employee_id
            ],
            "cash_transactions": [
                item.to_storage_dict()
                for item in cash_transactions
                if item.employee_id == employee_id
                and normalize_text(item.transaction_kind, default="", limit=32).casefold()
                in {"salary_payout", "salary_advance"}
            ],
            "cards": card_sources,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _employee_salary_balance_reset_request_fingerprint(
        *, employee_id: str, balance_minor: int, balance_revision: str
    ) -> str:
        canonical = json.dumps(
            {
                "employee_id": employee_id,
                "balance_minor": balance_minor,
                "balance_revision": balance_revision,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _build_employee_salary_ledger(
        self,
        cards: list[Card],
        cashboxes: list[CashBox],
        cash_transactions: list[CashTransaction],
        employee: dict[str, Any],
        *,
        shift_accruals: list[dict[str, Any]] | None = None,
        repair_order_accruals: list[dict[str, Any]] | None = None,
        salary_balance_resets: list[dict[str, Any]] | None = None,
        months: int = 6,
        period_only_totals: bool = False,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        repair_order_accruals = self._legacy_overall_accruals_without_postings(
            cards, repair_order_accruals or []
        )
        now = as_of or model_helpers.utc_now()
        period_start = now - timedelta(days=30 * months)
        employee_id = employee["id"]
        cashboxes_by_id = {cashbox.id: cashbox for cashbox in cashboxes}
        journal_rows: list[dict[str, Any]] = []
        accrual_total = Decimal("0")
        payout_total = Decimal("0")
        advance_total = Decimal("0")
        balance_reset_total = Decimal("0")
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

        for order_accrual in repair_order_accruals or []:
            if order_accrual.get("employee_id") != employee_id:
                continue
            created_at = parse_business_datetime(order_accrual.get("created_at"))
            if created_at is None:
                continue
            sign = -1 if order_accrual.get("kind") == "reversal" else 1
            amount_minor = sign * int(normalize_money_minor(order_accrual.get("amount_minor")))
            amount = Decimal(amount_minor) / Decimal("100")
            is_recent = created_at.astimezone(UTC) >= period_start
            if period_only_totals:
                if not is_recent:
                    continue
                accrual_total += amount
            else:
                accrual_total += amount
                if not is_recent:
                    continue
            base_minor = int(normalize_money_minor(order_accrual.get("base_amount_minor")))
            percent = order_accrual.get("percent") or "0"
            journal_rows.append(
                {
                    "kind": (
                        "repair_order_accrual_reversal" if sign < 0 else "repair_order_accrual"
                    ),
                    "kind_label": "ОТМЕНА % ЗН" if sign < 0 else "% ОТ ЗАКАЗ-НАРЯДА",
                    "created_at": created_at.astimezone(business_timezone()).strftime(
                        "%d.%m.%Y %H:%M"
                    ),
                    "closed_at": order_accrual.get("qualified_at") or "",
                    "repair_order_number": order_accrual.get("repair_order_number") or "",
                    "card_id": order_accrual.get("card_id") or "",
                    "vehicle": "",
                    "work_name": _repair_order_payroll_scheme(percent),
                    "accrual_id": order_accrual.get("id") or "",
                    "related_accrual_id": order_accrual.get("related_accrual_id") or "",
                    "base_amount_minor": base_minor,
                    "base_amount_display": format_money_minor(base_minor),
                    "percent": percent,
                    "amount_minor": amount_minor,
                    "amount_display": format_money_minor(amount_minor),
                    "source_label": "заказ-наряд, стоимость за наличный расчёт",
                    "scheme": _repair_order_payroll_scheme(percent),
                }
            )

        for card in cards:
            order = card.repair_order
            if order.payroll_postings:
                for posting in order.payroll_postings:
                    if posting.get("employee_id") != employee_id:
                        continue
                    created_at = parse_business_datetime(posting.get("created_at"))
                    if created_at is None:
                        continue
                    is_recent = created_at.astimezone(UTC) >= period_start
                    amount_minor = int(posting.get("amount_minor") or 0)
                    amount = Decimal(amount_minor) / Decimal("100")
                    if period_only_totals:
                        if not is_recent:
                            continue
                        accrual_total += amount
                    else:
                        accrual_total += amount
                        if not is_recent:
                            continue
                    is_reversal = posting.get("kind") == "reversal" or amount_minor < 0
                    posting_type = posting.get("posting_type") or "work"
                    if posting_type == "repair_order":
                        posting_kind = (
                            "repair_order_accrual_reversal"
                            if is_reversal
                            else "repair_order_accrual"
                        )
                        posting_label = "ОТМЕНА % ЗН" if is_reversal else "% ОТ ЗАКАЗ-НАРЯДА"
                    else:
                        posting_kind = (
                            "material_accrual_reversal"
                            if is_reversal and posting_type == "material"
                            else "accrual_reversal"
                            if is_reversal
                            else "material_accrual"
                            if posting_type == "material"
                            else "accrual"
                        )
                        posting_label = (
                            "ОТМЕНА МАТЕРИАЛА"
                            if is_reversal and posting_type == "material"
                            else "ОТМЕНА НАЧИСЛЕНИЯ"
                            if is_reversal
                            else "НАЧИСЛЕНИЕ МАТЕРИАЛ"
                            if posting_type == "material"
                            else "НАЧИСЛЕНИЕ"
                        )
                    journal_rows.append(
                        {
                            "kind": posting_kind,
                            "kind_label": posting_label,
                            "created_at": created_at.astimezone(business_timezone()).strftime(
                                "%d.%m.%Y %H:%M"
                            ),
                            "closed_at": posting.get("created_at") or "",
                            "repair_order_number": posting.get("repair_order_number")
                            or order.number,
                            "card_id": card.id,
                            "vehicle": order.vehicle or card.vehicle,
                            "work_name": posting.get("row_name") or "% от заказ-наряда",
                            "amount_minor": amount_minor,
                            "amount_display": self._format_payroll_decimal(amount),
                            "source_label": (
                                "материал"
                                if posting_type == "material"
                                else "заказ-наряд, стоимость за наличный расчёт"
                                if posting_type == "repair_order"
                                else "заказ-наряд"
                            ),
                            "scheme": posting.get("scheme") or posting.get("percent") or "",
                            "percent": posting.get("percent") or "",
                            "base_amount_minor": int(
                                (
                                    self._parse_payroll_decimal(posting.get("base_amount"))
                                    * Decimal("100")
                                ).to_integral_value(rounding=ROUND_HALF_UP)
                            ),
                            "accrual_id": posting.get("id") or "",
                            "related_accrual_id": posting.get("related_posting_id") or "",
                        }
                    )
                continue
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
                if self._work_salary_employee_id(row) != employee_id or not row.salary_accrued_at:
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

        for balance_reset in salary_balance_resets or []:
            if balance_reset.get("employee_id") != employee_id:
                continue
            amount_minor = int(balance_reset.get("amount_minor") or 0)
            if not amount_minor:
                continue
            amount = Decimal(amount_minor) / Decimal("100")
            created_at = parse_business_datetime(balance_reset.get("created_at"))
            if created_at is None:
                continue
            is_recent = created_at.astimezone(UTC) >= period_start
            if period_only_totals:
                if not is_recent:
                    continue
                balance_reset_total += amount
            else:
                balance_reset_total += amount
                if not is_recent:
                    continue
            actor_name = normalize_actor_name(balance_reset.get("actor_name"), default="")
            journal_rows.append(
                {
                    "kind": "salary_balance_reset",
                    "kind_label": "ОБНУЛЕНИЕ БАЛАНСА",
                    "created_at": created_at.astimezone(business_timezone()).strftime(
                        "%d.%m.%Y %H:%M"
                    ),
                    "closed_at": "",
                    "repair_order_number": "",
                    "card_id": "",
                    "vehicle": "",
                    "work_name": "",
                    "balance_reset_id": balance_reset.get("id") or "",
                    "amount_minor": amount_minor,
                    "amount_display": format_money_minor(amount_minor),
                    "source_label": "некассовая корректировка",
                    "note": f"Оператор: {actor_name}" if actor_name else "",
                    "actor_name": actor_name,
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
        balance_total = accrual_total - payout_total - advance_total + balance_reset_total
        balance_minor = int(
            (balance_total * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP)
        )
        balance_revision = self._employee_salary_ledger_revision(
            cards,
            cash_transactions,
            employee,
            shift_accruals=shift_accruals or [],
            repair_order_accruals=repair_order_accruals,
            salary_balance_resets=salary_balance_resets or [],
            as_of=now,
            period_start=period_start,
        )
        return {
            "employee_id": employee_id,
            "employee_name": employee["name"],
            "position": employee["position"],
            "period_months": months,
            "period_start": period_start.isoformat(),
            "balance_total": self._format_payroll_decimal(balance_total),
            "balance_display": self._format_payroll_decimal(balance_total),
            "balance_minor": balance_minor,
            "balance_revision": balance_revision,
            "accrued_total": self._format_payroll_decimal(accrual_total),
            "accrued_total_display": self._format_payroll_decimal(accrual_total),
            "payout_total": self._format_payroll_decimal(payout_total),
            "payout_total_display": self._format_payroll_decimal(payout_total),
            "advance_total": self._format_payroll_decimal(advance_total),
            "advance_total_display": self._format_payroll_decimal(advance_total),
            "balance_reset_total": self._format_payroll_decimal(balance_reset_total),
            "balance_reset_total_display": self._format_payroll_decimal(balance_reset_total),
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
            repair_order_accruals = self._employee_repair_order_accruals_from_settings(
                bundle["settings"], employees_by_id=employees_by_id
            )
            salary_balance_resets = self._employee_salary_balance_resets_from_settings(
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
                repair_order_accruals=repair_order_accruals,
                salary_balance_resets=salary_balance_resets,
                months=months,
            )
            return ledger

    def _employee_salary_balance_minor_from_bundle(
        self,
        bundle: dict[str, Any],
        employee: dict[str, Any],
        *,
        shift_accruals: list[dict[str, Any]],
        repair_order_accruals: list[dict[str, Any]],
        months: int,
    ) -> int:
        employees_by_id = {employee["id"]: employee}
        salary_balance_resets = self._employee_salary_balance_resets_from_settings(
            bundle["settings"], employees_by_id=employees_by_id
        )
        ledger = self._build_employee_salary_ledger(
            bundle["cards"],
            bundle["cashboxes"],
            bundle["cash_transactions"],
            employee,
            shift_accruals=shift_accruals,
            repair_order_accruals=repair_order_accruals,
            salary_balance_resets=salary_balance_resets,
            months=months,
        )
        return int(ledger["balance_minor"])

    def reset_employee_salary_balance(self, payload: dict | None = None) -> dict:
        with self._lock:
            payload = payload or {}
            operator_session = payload.get("_operator_session")
            if not operator_has_permission(operator_session, SALARY_BALANCE_RESET_PERMISSION):
                self._fail(
                    "forbidden",
                    "Нет права на обнуление зарплатного баланса.",
                    status_code=403,
                    details={"permission": SALARY_BALANCE_RESET_PERMISSION},
                )
            employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
            if not employee_id:
                self._fail(
                    "validation_error",
                    "Нужно передать employee_id.",
                    details={"field": "employee_id"},
                )
            expected_balance_minor = self._employee_salary_balance_reset_minor(
                payload.get("expected_balance_minor")
            )
            if expected_balance_minor is None:
                self._fail(
                    "validation_error",
                    "Нужен точный текущий баланс в копейках.",
                    details={"field": "expected_balance_minor"},
                )
            expected_balance_revision = normalize_text(
                payload.get("expected_balance_revision"), default="", limit=64
            ).casefold()
            if not re.fullmatch(r"[0-9a-f]{64}", expected_balance_revision):
                self._fail(
                    "validation_error",
                    "Нужна точная ревизия зарплатного баланса.",
                    details={"field": "expected_balance_revision"},
                )
            idempotency_key = normalize_text(payload.get("idempotency_key"), default="", limit=128)
            if not idempotency_key:
                self._fail(
                    "validation_error",
                    "Нужен ключ защиты от повторного запроса.",
                    details={"field": "idempotency_key"},
                )
            request_fingerprint = self._employee_salary_balance_reset_request_fingerprint(
                employee_id=employee_id,
                balance_minor=expected_balance_minor,
                balance_revision=expected_balance_revision,
            )

            bundle = self._store.read_bundle()
            settings = dict(bundle["settings"])
            employees = self._employees_from_settings(settings)
            employees_by_id = {item["id"]: item for item in employees}
            employee = employees_by_id.get(employee_id)
            if employee is None:
                self._fail(
                    "not_found",
                    "Сотрудник не найден.",
                    status_code=404,
                    details={"employee_id": employee_id},
                )
            shift_accruals = self._employee_shift_accruals_from_settings(
                settings, employees_by_id=employees_by_id
            )
            repair_order_accruals = self._employee_repair_order_accruals_from_settings(
                settings, employees_by_id=employees_by_id
            )
            salary_balance_resets = self._employee_salary_balance_resets_from_settings(
                settings, employees_by_id=employees_by_id
            )
            operation_at = model_helpers.utc_now()
            ledger = self._build_employee_salary_ledger(
                bundle["cards"],
                bundle["cashboxes"],
                bundle["cash_transactions"],
                employee,
                shift_accruals=shift_accruals,
                repair_order_accruals=repair_order_accruals,
                salary_balance_resets=salary_balance_resets,
                months=6,
                as_of=operation_at,
            )

            existing_reset = next(
                (
                    item
                    for item in salary_balance_resets
                    if item.get("idempotency_key") == idempotency_key
                ),
                None,
            )
            if existing_reset is not None:
                if existing_reset.get("request_fingerprint") != request_fingerprint:
                    self._fail(
                        "salary_balance_reset_idempotency_conflict",
                        "Ключ повтора уже использован для другого обнуления.",
                        status_code=409,
                        details={"idempotency_key": idempotency_key},
                    )
                return {
                    "balance_reset": self._serialize_employee_salary_balance_reset(existing_reset),
                    "ledger": ledger,
                    "meta": {"applied": True, "replayed": True},
                }

            current_balance_minor = int(ledger["balance_minor"])
            current_balance_revision = str(ledger["balance_revision"])
            if (
                current_balance_minor != expected_balance_minor
                or current_balance_revision != expected_balance_revision
            ):
                self._fail(
                    "salary_balance_reset_conflict",
                    "Баланс уже изменился. Обновите зарплатный лист и подтвердите новую сумму.",
                    status_code=409,
                    details={
                        "employee_id": employee_id,
                        "expected_balance_minor": expected_balance_minor,
                        "current_balance_minor": current_balance_minor,
                        "current_balance_display": ledger["balance_display"],
                        "expected_balance_revision": expected_balance_revision,
                        "current_balance_revision": current_balance_revision,
                    },
                )
            if current_balance_minor == 0:
                return {
                    "balance_reset": None,
                    "ledger": ledger,
                    "meta": {"applied": False, "replayed": False, "reason": "already_zero"},
                }

            actor_name = normalize_actor_name(
                operator_session.get("audit_actor_name") or operator_session.get("username"),
                default="ОПЕРАТОР",
            )
            source = normalize_source(payload.get("source"), default="ui")
            reset = {
                "id": str(uuid.uuid4()),
                "employee_id": employee_id,
                "employee_name": employee["name"],
                "amount_minor": -current_balance_minor,
                "balance_before_minor": current_balance_minor,
                "balance_after_minor": 0,
                "idempotency_key": idempotency_key,
                "ledger_revision_before": current_balance_revision,
                "request_fingerprint": request_fingerprint,
                "created_at": operation_at.isoformat(),
                "actor_name": actor_name,
                "source": source,
            }
            normalized_reset = self._normalized_employee_salary_balance_reset(
                reset, employees_by_id=employees_by_id
            )
            if normalized_reset is None:
                raise RuntimeError("Salary balance reset invariant is invalid.")
            next_resets = [*salary_balance_resets, normalized_reset]
            next_ledger = self._build_employee_salary_ledger(
                bundle["cards"],
                bundle["cashboxes"],
                bundle["cash_transactions"],
                employee,
                shift_accruals=shift_accruals,
                repair_order_accruals=repair_order_accruals,
                salary_balance_resets=next_resets,
                months=6,
                as_of=operation_at,
            )
            if int(next_ledger["balance_minor"]) != 0:
                raise RuntimeError("Salary balance reset did not produce an exact zero balance.")

            settings[EMPLOYEE_SALARY_BALANCE_RESETS_SETTING_KEY] = [
                self._employee_salary_balance_reset_storage_payload(item) for item in next_resets
            ]
            self._append_event(
                bundle["events"],
                actor_name=actor_name,
                source=source,
                action="employee_salary_balance_reset",
                message=f"{actor_name} обнулил зарплатный баланс сотрудника",
                card_id=None,
                details={
                    "employee_id": employee_id,
                    "employee_name": employee["name"],
                    "balance_reset_id": normalized_reset["id"],
                    "balance_before_minor": current_balance_minor,
                    "amount_minor": -current_balance_minor,
                    "balance_after_minor": 0,
                },
            )
            self._save_bundle(
                bundle,
                columns=bundle["columns"],
                cards=bundle["cards"],
                cashboxes=bundle["cashboxes"],
                cash_transactions=bundle["cash_transactions"],
                events=bundle["events"],
                settings=settings,
                require_compare_and_swap=True,
            )
            return {
                "balance_reset": self._serialize_employee_salary_balance_reset(normalized_reset),
                "ledger": next_ledger,
                "meta": {"applied": True, "replayed": False},
            }

    @staticmethod
    def _employee_salary_balance_reset_minor(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return normalize_money_minor(value, default=0)
        text = str(value or "").strip()
        if not re.fullmatch(r"-?\d+", text):
            return None
        return normalize_money_minor(int(text), default=0)

    def _normalized_employee_salary_balance_reset(
        self,
        payload: Any,
        *,
        employees_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        reset_id = normalize_text(payload.get("id"), default="", limit=64)
        employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
        idempotency_key = normalize_text(payload.get("idempotency_key"), default="", limit=128)
        ledger_revision_before = normalize_text(
            payload.get("ledger_revision_before"), default="", limit=64
        ).casefold()
        request_fingerprint = normalize_text(
            payload.get("request_fingerprint"), default="", limit=64
        ).casefold()
        amount_minor = self._employee_salary_balance_reset_minor(payload.get("amount_minor"))
        balance_before_minor = self._employee_salary_balance_reset_minor(
            payload.get("balance_before_minor")
        )
        balance_after_minor = self._employee_salary_balance_reset_minor(
            payload.get("balance_after_minor")
        )
        created_at = parse_business_datetime(payload.get("created_at"))
        if (
            not reset_id
            or not employee_id
            or not idempotency_key
            or not re.fullmatch(r"[0-9a-f]{64}", ledger_revision_before)
            or not re.fullmatch(r"[0-9a-f]{64}", request_fingerprint)
            or amount_minor in {None, 0}
            or balance_before_minor in {None, 0}
            or balance_after_minor != 0
            or amount_minor != -balance_before_minor
            or created_at is None
        ):
            return None
        employee = (employees_by_id or {}).get(employee_id)
        return {
            "id": reset_id,
            "employee_id": employee_id,
            "employee_name": normalize_text(
                payload.get("employee_name"),
                default=employee.get("name", "") if employee else "",
                limit=80,
            ),
            "amount_minor": amount_minor,
            "balance_before_minor": balance_before_minor,
            "balance_after_minor": 0,
            "idempotency_key": idempotency_key,
            "ledger_revision_before": ledger_revision_before,
            "request_fingerprint": request_fingerprint,
            "created_at": created_at.isoformat(),
            "actor_name": normalize_actor_name(payload.get("actor_name")),
            "source": normalize_source(payload.get("source"), default="ui"),
        }

    def _employee_salary_balance_reset_storage_payload(
        self, reset: dict[str, Any]
    ) -> dict[str, Any]:
        return self._normalized_employee_salary_balance_reset(reset) or {}

    def _serialize_employee_salary_balance_reset(self, reset: dict[str, Any]) -> dict[str, Any]:
        payload = self._employee_salary_balance_reset_storage_payload(reset)
        amount_minor = int(payload.get("amount_minor") or 0)
        before_minor = int(payload.get("balance_before_minor") or 0)
        return {
            **payload,
            "amount_display": format_money_minor(amount_minor),
            "balance_before_display": format_money_minor(before_minor),
            "balance_after_display": format_money_minor(0),
        }

    def _employee_salary_balance_resets_from_settings(
        self,
        settings: dict[str, Any],
        *,
        employees_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        raw_items = settings.get(EMPLOYEE_SALARY_BALANCE_RESETS_SETTING_KEY)
        if raw_items is None:
            return []
        if not isinstance(raw_items, list):
            self._fail(
                "salary_balance_reset_history_invalid",
                "История обнулений зарплатного баланса повреждена.",
                status_code=500,
            )
        resets: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_idempotency_keys: set[str] = set()
        for index, item in enumerate(raw_items):
            normalized = self._normalized_employee_salary_balance_reset(
                item, employees_by_id=employees_by_id
            )
            if (
                normalized is None
                or normalized["id"] in seen_ids
                or normalized["idempotency_key"] in seen_idempotency_keys
            ):
                self._fail(
                    "salary_balance_reset_history_invalid",
                    "История обнулений зарплатного баланса повреждена.",
                    status_code=500,
                    details={"record_index": index},
                )
            seen_ids.add(normalized["id"])
            seen_idempotency_keys.add(normalized["idempotency_key"])
            resets.append(normalized)
        resets.sort(
            key=lambda item: (
                self._repair_order_sortable_datetime(item.get("created_at")),
                item.get("id") or "",
            )
        )
        return resets
