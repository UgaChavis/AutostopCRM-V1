from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.json_safety import reject_deeply_nested_json  # noqa: E402

UrlOpen = Callable[[urllib.request.Request, float], Any]
AUDIT_RESPONSE_MAX_BYTES = 32 * 1024 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _urlopen_no_redirect(request: urllib.request.Request, timeout: float) -> Any:
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _json_safe_value(value: Any, *, depth: int = 8) -> Any:
    if depth <= 0:
        return str(value)
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item, depth=depth - 1) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    return str(value)


def _json_dumps(payload: Any) -> str:
    return json.dumps(
        _json_safe_value(payload),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )


def _bounded_int(value: object, *, default: int, minimum: int = 0, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return default
    if not math.isfinite(numeric) or not numeric.is_integer():
        return default
    if numeric < minimum:
        return minimum
    if numeric > maximum:
        return maximum
    return int(numeric)


def _bounded_timeout_seconds(value: object) -> float:
    if isinstance(value, bool):
        return 15.0
    try:
        numeric = float(15.0 if value is None or value == "" else value)
    except (OverflowError, TypeError, ValueError):
        return 15.0
    if not math.isfinite(numeric):
        return 15.0
    if numeric < 1.0:
        return 1.0
    if numeric > 300.0:
        return 300.0
    return numeric


def _url(base_url: str, path: str, query: dict[str, object] | None = None) -> str:
    target = base_url.rstrip("/") + "/" + path.lstrip("/")
    if query:
        target += "?" + urllib.parse.urlencode(query)
    return target


def _read_response_body(response) -> bytes:
    raw = response.read(AUDIT_RESPONSE_MAX_BYTES + 1)
    if len(raw) > AUDIT_RESPONSE_MAX_BYTES:
        raise ValueError("payroll audit response is too large")
    return raw


def _load_audit_response(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8"), parse_constant=_reject_json_constant)
    except RecursionError as exc:
        raise ValueError("payroll audit response JSON is too deeply nested") from exc
    reject_deeply_nested_json(
        payload,
        message="payroll audit response JSON is too deeply nested",
    )
    if not isinstance(payload, dict):
        raise ValueError("payroll audit response must be a JSON object")
    return payload


def _fetch_json(
    base_url: str,
    path: str,
    *,
    query: dict[str, object] | None = None,
    timeout: float = 15.0,
    urlopen: UrlOpen = _urlopen_no_redirect,
) -> dict[str, Any]:
    request = urllib.request.Request(
        _url(base_url, path, query),
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw_body = _read_response_body(response)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ValueError("payroll audit response redirected") from exc
        raise
    return _load_audit_response(raw_body)


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _money(value: Any) -> Decimal:
    raw = str(value or "0").strip().replace(" ", "").replace(",", ".")
    if not raw:
        return Decimal("0")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return Decimal("0")


def _format_money(value: Decimal) -> str:
    normalized = _round_money(value)
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _bool_text(value: Any) -> bool:
    raw = str(value or "").strip().casefold()
    return raw in {"1", "true", "yes", "y", "on", "да"}


def _journal_total_matches(value: Any, row_count: int) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, bool):
        return False
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return False
    if not math.isfinite(numeric) or not numeric.is_integer() or numeric > 1_000_000_000:
        return False
    return int(numeric) == row_count


def _percent(value: Any) -> Decimal:
    return min(max(_money(value), Decimal("0")), Decimal("100"))


def _work_row_total(row: dict[str, Any]) -> Decimal:
    if row.get("salary_accrued_at") and (
        row.get("work_quantity_snapshot")
        or row.get("work_price_snapshot")
        or row.get("work_total_snapshot")
    ):
        quantity = _money(row.get("work_quantity_snapshot"))
        price = _money(row.get("work_price_snapshot"))
        if (
            str(row.get("work_quantity_snapshot") or "").strip()
            and str(row.get("work_price_snapshot") or "").strip()
        ):
            return quantity * price
        if str(row.get("work_total_snapshot") or "").strip():
            return _money(row.get("work_total_snapshot"))
    quantity = _money(row.get("quantity"))
    price = _money(row.get("price"))
    if quantity > Decimal("0") and price > Decimal("0"):
        return quantity * price
    return _money(row.get("total"))


def _work_salary_employee_id(row: dict[str, Any]) -> str:
    return str(row.get("work_executor_id_snapshot") or row.get("executor_id") or "")


def _expected_work_salary(row: dict[str, Any]) -> Decimal | None:
    total = _work_row_total(row)
    cost_price = max(_money(row.get("work_salary_cost_price")), Decimal("0"))
    if _bool_text(row.get("work_salary_override_enabled")):
        guarantee = max(_money(row.get("work_salary_guarantee")), Decimal("0"))
        percent = _percent(row.get("work_salary_percent_override"))
        percent_base = max(total - guarantee - cost_price, Decimal("0"))
        return guarantee + (percent_base * percent / Decimal("100"))

    mode = str(row.get("salary_mode_snapshot") or "").strip().casefold()
    if mode in {"percent_only", "salary_plus_percent"}:
        percent = _percent(row.get("work_percent_snapshot"))
        percent_base = max(total - cost_price, Decimal("0"))
        return percent_base * percent / Decimal("100")
    if mode == "salary_only":
        return Decimal("0")
    return None


def _month_keys(months_back: int, *, reference: datetime | None = None) -> list[str]:
    reference = reference or datetime.now()
    year = reference.year
    month = reference.month
    keys: list[str] = []
    for _index in range(max(1, months_back)):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return keys


def _issue(
    *,
    code: str,
    severity: str,
    message: str,
    employee_id: str = "",
    employee_name: str = "",
    month: str = "",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parts = [code, employee_id or "all", month or "all", str(len(data or {}))]
    return {
        "id": ":".join(parts),
        "code": code,
        "severity": severity,
        "message": message,
        "employee_id": employee_id,
        "employee_name": employee_name,
        "month": month,
        "data": data or {},
        "safe_fix_available": False,
    }


def _summary_by_employee(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("employee_id") or ""): item
        for item in _items(report.get("summary"))
        if str(item.get("employee_id") or "")
    }


def _detail_duplicate_key(row: dict[str, Any]) -> tuple[str, ...]:
    return (
        str(row.get("row_type") or ""),
        str(row.get("employee_id") or ""),
        str(row.get("card_id") or ""),
        str(row.get("repair_order_number") or ""),
        str(row.get("closed_at") or ""),
        str(row.get("vehicle") or ""),
        str(row.get("material_name") or ""),
        str(row.get("salary_amount") or ""),
        str(row.get("accrual_id") or ""),
    )


def _ledger_duplicate_key(row: dict[str, Any]) -> tuple[str, ...]:
    return (
        str(row.get("kind") or ""),
        str(row.get("employee_id") or ""),
        str(row.get("card_id") or ""),
        str(row.get("repair_order_number") or ""),
        str(row.get("closed_at") or row.get("created_at") or ""),
        str(row.get("work_name") or row.get("material_name") or row.get("note") or ""),
        str(row.get("amount") or row.get("accrued") or row.get("payment") or ""),
    )


def _detail_totals_by_employee(report: dict[str, Any]) -> dict[str, dict[str, Decimal]]:
    detail_totals: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for row in _items(report.get("detail_rows")):
        employee_id = str(row.get("employee_id") or "")
        if not employee_id:
            continue
        row_type = str(row.get("row_type") or "")
        amount = _money(row.get("salary_amount"))
        detail_totals[employee_id]["accrued_total"] += amount
        if row_type == "base_salary":
            detail_totals[employee_id]["base_salary_accrued_total"] += amount
        elif row_type == "shift_accrual":
            detail_totals[employee_id]["shift_accrued_total"] += amount
        elif row_type == "work":
            detail_totals[employee_id]["work_accrued_total"] += amount
        elif row_type == "material":
            detail_totals[employee_id]["materials_accrued_total"] += amount
        elif row_type in {"repair_order_accrual", "repair_order_accrual_reversal"}:
            detail_totals[employee_id]["repair_order_accrued_total"] += amount
    return detail_totals


def _audit_report_material_formula_issues(
    report: dict[str, Any], *, month: str
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for row in _items(report.get("detail_rows")):
        if str(row.get("row_type") or "") != "material":
            continue
        employee_id = str(row.get("employee_id") or "")
        if not employee_id:
            continue
        amount = _money(row.get("salary_amount"))
        material_profit = _money(row.get("material_profit"))
        material_percent = _money(row.get("material_percent"))
        expected_amount = material_profit * material_percent / Decimal("100")
        if _round_money(expected_amount) == _round_money(amount):
            continue
        issues.append(
            _issue(
                code="payroll_material_salary_formula_mismatch",
                severity="error",
                message="Начисление по материалу не равно прибыль * процент сотрудника.",
                employee_id=employee_id,
                employee_name=str(row.get("employee_name") or ""),
                month=month,
                data={
                    "card_id": row.get("card_id"),
                    "repair_order_number": row.get("repair_order_number"),
                    "material_name": row.get("material_name"),
                    "material_profit": _format_money(material_profit),
                    "material_percent": _format_money(material_percent),
                    "expected_salary_amount": _format_money(expected_amount),
                    "salary_amount": _format_money(amount),
                },
            )
        )
    return issues


def _audit_report_repair_order_formula_issues(
    report: dict[str, Any], *, month: str
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for row in _items(report.get("detail_rows")):
        row_type = str(row.get("row_type") or "")
        if row_type not in {"repair_order_accrual", "repair_order_accrual_reversal"}:
            continue
        amount = _money(row.get("salary_amount"))
        base = _money(row.get("base_amount") or row.get("work_total"))
        percent = _percent(row.get("repair_order_percent"))
        expected = _round_money(base * percent / Decimal("100"))
        if row_type == "repair_order_accrual_reversal":
            expected = -expected
        if _round_money(amount) == expected:
            continue
        issues.append(
            _issue(
                code="payroll_repair_order_salary_formula_mismatch",
                severity="error",
                message="Начисление от заказ-наряда не равно зафиксированная база * процент.",
                employee_id=str(row.get("employee_id") or ""),
                employee_name=str(row.get("employee_name") or ""),
                month=month,
                data={
                    "card_id": row.get("card_id"),
                    "repair_order_number": row.get("repair_order_number"),
                    "base_amount": _format_money(base),
                    "percent": _format_money(percent),
                    "expected_salary_amount": _format_money(expected),
                    "salary_amount": _format_money(amount),
                    "accrual_id": row.get("accrual_id"),
                    "related_accrual_id": row.get("related_accrual_id"),
                },
            )
        )
    return issues


def _audit_report_summary_issues(
    report: dict[str, Any], *, month: str, detail_totals: dict[str, dict[str, Decimal]]
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for employee_id, summary in _summary_by_employee(report).items():
        employee_name = str(summary.get("employee_name") or "")
        component_total = (
            _money(summary.get("base_salary_accrued_total"))
            + _money(summary.get("shift_accrued_total"))
            + _money(summary.get("work_accrued_total"))
            + _money(summary.get("materials_accrued_total"))
            + _money(summary.get("repair_order_accrued_total"))
        )
        accrued_total = _money(summary.get("accrued_total"))
        if component_total != accrued_total:
            issues.append(
                _issue(
                    code="payroll_summary_total_mismatch",
                    severity="error",
                    message="Сумма компонентов зарплаты не равна accrued_total.",
                    employee_id=employee_id,
                    employee_name=employee_name,
                    month=month,
                    data={
                        "component_total": _format_money(component_total),
                        "accrued_total": _format_money(accrued_total),
                    },
                )
            )
        for field_name, detail_value in detail_totals.get(employee_id, {}).items():
            summary_value = _money(summary.get(field_name))
            if detail_value == summary_value:
                continue
            issues.append(
                _issue(
                    code="payroll_detail_total_mismatch",
                    severity="error",
                    message="Детализация зарплаты не сходится с итогом сотрудника.",
                    employee_id=employee_id,
                    employee_name=employee_name,
                    month=month,
                    data={
                        "field": field_name,
                        "detail_total": _format_money(detail_value),
                        "summary_total": _format_money(summary_value),
                    },
                )
            )
    return issues


def _audit_report_duplicate_issues(report: dict[str, Any], *, month: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    detail_counts = Counter(_detail_duplicate_key(row) for row in _items(report.get("detail_rows")))
    for key, count in detail_counts.items():
        if count <= 1 or not key[1]:
            continue
        issues.append(
            _issue(
                code="payroll_duplicate_detail_row",
                severity="warning",
                message="В отчете зарплат есть полностью повторяющаяся строка начисления.",
                employee_id=key[1],
                month=month,
                data={"duplicate_count": count, "key": list(key)},
            )
        )
    return issues


def _audit_report_totals(report: dict[str, Any], *, month: str) -> list[dict[str, Any]]:
    detail_totals = _detail_totals_by_employee(report)
    issues: list[dict[str, Any]] = []
    issues.extend(_audit_report_material_formula_issues(report, month=month))
    issues.extend(_audit_report_repair_order_formula_issues(report, month=month))
    issues.extend(_audit_report_summary_issues(report, month=month, detail_totals=detail_totals))
    issues.extend(_audit_report_duplicate_issues(report, month=month))
    return issues


def _audit_report_employee_references(
    report: dict[str, Any],
    *,
    month: str,
    employee_ids: set[str],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    names_by_employee: dict[str, str] = {}
    row_types_by_employee: dict[str, set[str]] = defaultdict(set)
    rows_by_employee: Counter[str] = Counter()
    for summary in _items(report.get("summary")):
        employee_id = str(summary.get("employee_id") or "")
        if not employee_id:
            continue
        names_by_employee[employee_id] = str(summary.get("employee_name") or "")
        row_types_by_employee[employee_id].add("summary")
    for row in _items(report.get("detail_rows")):
        employee_id = str(row.get("employee_id") or "")
        if not employee_id:
            continue
        names_by_employee.setdefault(employee_id, str(row.get("employee_name") or ""))
        row_types_by_employee[employee_id].add(str(row.get("row_type") or "detail"))
        rows_by_employee[employee_id] += 1

    for employee_id in sorted(set(names_by_employee) | set(rows_by_employee)):
        if employee_id in employee_ids:
            continue
        issues.append(
            _issue(
                code="payroll_accrual_missing_employee",
                severity="error",
                message=(
                    "В отчете зарплат есть начисления на сотрудника, которого нет в "
                    "справочнике; ведомость и история сотрудника будут недоступны."
                ),
                employee_id=employee_id,
                employee_name=names_by_employee.get(employee_id, ""),
                month=month,
                data={
                    "detail_rows": rows_by_employee.get(employee_id, 0),
                    "row_types": sorted(row_types_by_employee.get(employee_id, set())),
                },
            )
        )
    return issues


def _work_detail_card_ids(report: dict[str, Any]) -> set[str]:
    return {
        str(row.get("card_id") or "")
        for row in _items(report.get("detail_rows"))
        if str(row.get("row_type") or "")
        in {"work", "repair_order_accrual", "repair_order_accrual_reversal"}
        and str(row.get("card_id") or "")
    }


def _cards_by_id_from_list(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cards: dict[str, dict[str, Any]] = {}
    for card in _items(payload.get("cards")):
        card_id = str(card.get("id") or "")
        if card_id:
            cards[card_id] = card
    return cards


def _reported_work_card_totals(report: dict[str, Any]) -> dict[tuple[str, str], Decimal]:
    reported_totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for row in _items(report.get("detail_rows")):
        if str(row.get("row_type") or "") != "work":
            continue
        employee_id = str(row.get("employee_id") or "")
        card_id = str(row.get("card_id") or "")
        if not employee_id or not card_id:
            continue
        reported_totals[(employee_id, card_id)] += _money(row.get("salary_amount"))
    return reported_totals


def _stored_work_card_totals(
    report: dict[str, Any],
    *,
    cards_by_id: dict[str, dict[str, Any]],
    month: str,
) -> tuple[dict[tuple[str, str], Decimal], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    stored_totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for (employee_id, card_id), _reported_total in sorted(
        _reported_work_card_totals(report).items()
    ):
        card = cards_by_id.get(card_id, {})
        repair_order = card.get("repair_order") if isinstance(card, dict) else {}
        if not isinstance(repair_order, dict):
            continue
        for index, row in enumerate(_items(repair_order.get("works")), start=1):
            if _work_salary_employee_id(row) != employee_id or not row.get("salary_accrued_at"):
                continue
            amount = _money(row.get("salary_amount"))
            stored_totals[(employee_id, card_id)] += amount
            expected = _expected_work_salary(row)
            if expected is None or _round_money(expected) == _round_money(amount):
                continue
            issues.append(
                _issue(
                    code="payroll_work_salary_formula_mismatch",
                    severity="error",
                    message=(
                        "Начисление по работе не равно формуле выплаты исполнителю, "
                        "процента и себестоимости."
                    ),
                    employee_id=employee_id,
                    employee_name=str(row.get("work_executor_name_snapshot") or ""),
                    month=month,
                    data={
                        "card_id": card_id,
                        "repair_order_number": repair_order.get("number"),
                        "work_index": index,
                        "work_name": row.get("name"),
                        "work_total": _format_money(_work_row_total(row)),
                        "work_salary_guarantee": _format_money(
                            _money(row.get("work_salary_guarantee"))
                        ),
                        "work_salary_cost_price": _format_money(
                            _money(row.get("work_salary_cost_price"))
                        ),
                        "work_percent": str(
                            row.get("work_salary_percent_override")
                            if _bool_text(row.get("work_salary_override_enabled"))
                            else row.get("work_percent_snapshot")
                        ),
                        "expected_salary_amount": _format_money(expected),
                        "salary_amount": _format_money(amount),
                    },
                )
            )
    return stored_totals, issues


def _audit_work_card_total_mismatch_issues(
    reported_totals: dict[tuple[str, str], Decimal],
    stored_totals: dict[tuple[str, str], Decimal],
    *,
    month: str,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for (employee_id, card_id), reported_total in sorted(reported_totals.items()):
        stored_total = stored_totals.get((employee_id, card_id), Decimal("0"))
        if _round_money(reported_total) == _round_money(stored_total):
            continue
        issues.append(
            _issue(
                code="payroll_work_report_card_total_mismatch",
                severity="error",
                message="Итог работ в отчете зарплат не совпадает с начислениями в карточке.",
                employee_id=employee_id,
                month=month,
                data={
                    "card_id": card_id,
                    "reported_salary_amount": _format_money(reported_total),
                    "stored_salary_amount": _format_money(stored_total),
                },
            )
        )
    return issues


def _audit_work_card_formulas(
    report: dict[str, Any],
    *,
    month: str,
    cards_by_id: dict[str, dict[str, Any]],
    audit_current_order_accruals: bool,
) -> list[dict[str, Any]]:
    reported_totals = _reported_work_card_totals(report)
    stored_totals, formula_issues = _stored_work_card_totals(
        report,
        cards_by_id=cards_by_id,
        month=month,
    )
    issues: list[dict[str, Any]] = []
    issues.extend(formula_issues)
    issues.extend(
        _audit_work_card_total_mismatch_issues(
            reported_totals,
            stored_totals,
            month=month,
        )
    )
    if audit_current_order_accruals:
        issues.extend(
            _audit_repair_order_cash_base_issues(
                report,
                cards_by_id=cards_by_id,
                month=month,
            )
        )
    return issues


def _audit_repair_order_cash_base_issues(
    report: dict[str, Any],
    *,
    cards_by_id: dict[str, dict[str, Any]],
    month: str,
) -> list[dict[str, Any]]:
    rows = _items(report.get("detail_rows"))
    reversed_accrual_ids = {
        str(row.get("related_accrual_id") or "")
        for row in rows
        if str(row.get("row_type") or "") == "repair_order_accrual_reversal"
        and str(row.get("related_accrual_id") or "")
    }
    issues: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("row_type") or "") != "repair_order_accrual":
            continue
        accrual_id = str(row.get("accrual_id") or "")
        if accrual_id and accrual_id in reversed_accrual_ids:
            continue
        card_id = str(row.get("card_id") or "")
        card = cards_by_id.get(card_id)
        repair_order = card.get("repair_order") if isinstance(card, dict) else None
        if not isinstance(repair_order, dict):
            continue
        recorded_base = _money(row.get("base_amount") or row.get("work_total"))
        cash_price = _money(repair_order.get("subtotal_total"))
        if _round_money(recorded_base) != _round_money(cash_price):
            issues.append(
                _issue(
                    code="payroll_repair_order_cash_base_mismatch",
                    severity="error",
                    message=(
                        "База начисления от заказ-наряда не равна его стоимости за наличный расчёт."
                    ),
                    employee_id=str(row.get("employee_id") or ""),
                    employee_name=str(row.get("employee_name") or ""),
                    month=month,
                    data={
                        "card_id": card_id,
                        "repair_order_number": row.get("repair_order_number"),
                        "recorded_base": _format_money(recorded_base),
                        "cash_price_subtotal_total": _format_money(cash_price),
                        "accrual_id": accrual_id,
                    },
                )
            )
        if str(repair_order.get("status") or "").casefold() != "closed" or not _bool_text(
            repair_order.get("is_paid")
        ):
            issues.append(
                _issue(
                    code="payroll_repair_order_accrual_not_qualified",
                    severity="error",
                    message=(
                        "Активное начисление от заказ-наряда относится к наряду, который "
                        "сейчас не закрыт или не полностью оплачен."
                    ),
                    employee_id=str(row.get("employee_id") or ""),
                    employee_name=str(row.get("employee_name") or ""),
                    month=month,
                    data={
                        "card_id": card_id,
                        "repair_order_number": row.get("repair_order_number"),
                        "status": repair_order.get("status"),
                        "is_paid": repair_order.get("is_paid"),
                        "accrual_id": accrual_id,
                    },
                )
            )
    return issues


def _audit_ledger(ledger: dict[str, Any], employee: dict[str, Any]) -> list[dict[str, Any]]:
    employee_id = str(employee.get("id") or ledger.get("employee_id") or "")
    employee_name = str(employee.get("name") or ledger.get("employee_name") or "")
    issues: list[dict[str, Any]] = []
    accrued = _money(ledger.get("accrued_total"))
    payout = _money(ledger.get("payout_total"))
    advance = _money(ledger.get("advance_total"))
    balance = _money(ledger.get("balance_total"))
    expected_balance = accrued - payout - advance
    if expected_balance != balance:
        issues.append(
            _issue(
                code="payroll_ledger_balance_mismatch",
                severity="error",
                message="Баланс ведомости не равен начислено минус выплаты и авансы.",
                employee_id=employee_id,
                employee_name=employee_name,
                data={
                    "expected_balance": _format_money(expected_balance),
                    "balance_total": _format_money(balance),
                    "accrued_total": _format_money(accrued),
                    "payout_total": _format_money(payout),
                    "advance_total": _format_money(advance),
                },
            )
        )
    journal_rows = _items(ledger.get("journal_rows"))
    if not _journal_total_matches(ledger.get("journal_total"), len(journal_rows)):
        issues.append(
            _issue(
                code="payroll_ledger_journal_count_mismatch",
                severity="warning",
                message="journal_total ведомости не совпадает с числом строк журнала.",
                employee_id=employee_id,
                employee_name=employee_name,
                data={
                    "journal_total": ledger.get("journal_total"),
                    "rows": len(journal_rows),
                },
            )
        )
    duplicate_counts = Counter(_ledger_duplicate_key(row) for row in journal_rows)
    for key, count in duplicate_counts.items():
        if count <= 1 or not key[1]:
            continue
        issues.append(
            _issue(
                code="payroll_duplicate_ledger_row",
                severity="warning",
                message="В ведомости сотрудника есть полностью повторяющаяся строка.",
                employee_id=employee_id,
                employee_name=employee_name,
                data={"duplicate_count": count, "key": list(key)},
            )
        )
    return issues


def build_payroll_audit(
    base_url: str,
    *,
    months_back: int = 1,
    ledger_months: int = 6,
    timeout: float = 15.0,
    urlopen: UrlOpen | None = None,
    reference: datetime | None = None,
) -> dict[str, Any]:
    safe_urlopen = urlopen or _urlopen_no_redirect
    issues: list[dict[str, Any]] = []
    employees_payload = _fetch_json(
        base_url, "/api/list_employees", timeout=timeout, urlopen=safe_urlopen
    )
    employees = _items(_data(employees_payload).get("employees"))
    employee_ids = {str(employee.get("id") or "") for employee in employees}
    all_cards_by_id: dict[str, dict[str, Any]] | None = None
    month_keys = _month_keys(months_back, reference=reference)
    for month_index, month in enumerate(month_keys):
        report_payload = _fetch_json(
            base_url,
            "/api/get_payroll_report",
            query={"month": month},
            timeout=timeout,
            urlopen=safe_urlopen,
        )
        report = _data(report_payload)
        card_ids = _work_detail_card_ids(report)
        cards_by_id: dict[str, dict[str, Any]] = {}
        if card_ids:
            if all_cards_by_id is None:
                cards_payload = _fetch_json(
                    base_url,
                    "/api/get_cards",
                    query={"include_archived": "true"},
                    timeout=timeout,
                    urlopen=safe_urlopen,
                )
                all_cards_by_id = _cards_by_id_from_list(_data(cards_payload))
            cards_by_id.update(all_cards_by_id)
            for card_id in sorted(card_ids - set(cards_by_id)):
                card_payload = _fetch_json(
                    base_url,
                    "/api/get_card",
                    query={"card_id": card_id},
                    timeout=timeout,
                    urlopen=safe_urlopen,
                )
                card = _data(card_payload).get("card")
                if isinstance(card, dict):
                    cards_by_id[card_id] = card
                    all_cards_by_id[card_id] = card
        issues.extend(
            _audit_report_employee_references(
                report,
                month=month,
                employee_ids=employee_ids,
            )
        )
        issues.extend(_audit_report_totals(report, month=month))
        issues.extend(
            _audit_work_card_formulas(
                report,
                month=month,
                cards_by_id=cards_by_id,
                audit_current_order_accruals=month_index == 0,
            )
        )
    for employee in employees:
        employee_id = str(employee.get("id") or "")
        if not employee_id:
            continue
        ledger_payload = _fetch_json(
            base_url,
            "/api/get_employee_salary_ledger",
            query={"employee_id": employee_id, "months": ledger_months},
            timeout=timeout,
            urlopen=safe_urlopen,
        )
        issues.extend(_audit_ledger(_data(ledger_payload), employee))

    severity_counts = Counter(str(issue.get("severity") or "info") for issue in issues)
    return {
        "schema": "payroll_audit.v1",
        "read_only": True,
        "base_url": base_url.rstrip("/"),
        "summary": {
            "employees_total": len(employees),
            "months_checked": max(1, months_back),
            "ledger_months": max(1, ledger_months),
            "issues_total": len(issues),
            "errors": severity_counts.get("error", 0),
            "warnings": severity_counts.get("warning", 0),
            "info": severity_counts.get("info", 0),
            "safe_fixes_available": 0,
        },
        "issues": issues,
    }


def _format_issue_context(issue: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, label in (
        ("employee_id", "employee_id"),
        ("employee_name", "employee"),
        ("month", "month"),
    ):
        value = str(issue.get(key) or "").strip()
        if value:
            parts.append(f"{label}={value}")
    data = issue.get("data")
    if isinstance(data, dict):
        for key in sorted(data):
            value = data[key]
            if value not in ("", None, [], {}):
                parts.append(f"{key}={value}")
    parts.append(f"safe_fix={'yes' if issue.get('safe_fix_available') else 'no'}")
    return " ".join(parts)


def _format_text(result: dict[str, Any], *, issue_limit: int) -> str:
    summary = result["summary"]
    lines = [
        "AutoStop CRM payroll audit",
        f"schema: {result['schema']}",
        f"read_only: {result['read_only']}",
        (
            "issues: "
            f"{summary['issues_total']} "
            f"(errors={summary['errors']}, warnings={summary['warnings']}, info={summary['info']})"
        ),
        f"employees_total: {summary['employees_total']}",
        f"months_checked: {summary['months_checked']}",
        f"ledger_months: {summary['ledger_months']}",
        f"safe_fixes_available: {summary['safe_fixes_available']}",
    ]
    for issue in result["issues"][: max(0, issue_limit)]:
        lines.append(
            f"- [{issue['severity']}] {issue['code']}: "
            f"{issue['message']} {_format_issue_context(issue)}".rstrip()
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only AutoStop CRM payroll audit report.")
    parser.add_argument("--base-url", default="https://crm.autostopcrm.ru")
    parser.add_argument("--months-back", default=1)
    parser.add_argument("--ledger-months", default=6)
    parser.add_argument("--timeout", default=15.0)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    parser.add_argument("--issue-limit", default=50)
    args = parser.parse_args()
    args.months_back = _bounded_int(args.months_back, default=1, minimum=1, maximum=24)
    args.ledger_months = _bounded_int(args.ledger_months, default=6, minimum=1, maximum=24)
    args.issue_limit = _bounded_int(args.issue_limit, default=50, minimum=0, maximum=500)
    args.timeout = _bounded_timeout_seconds(args.timeout)

    try:
        result = build_payroll_audit(
            args.base_url,
            months_back=args.months_back,
            ledger_months=args.ledger_months,
            timeout=args.timeout,
        )
    except (urllib.error.URLError, ValueError) as exc:
        print(_json_dumps({"ok": False, "error": str(exc)}))
        return 2
    if args.format == "json":
        print(_json_dumps({"ok": True, **result}))
    else:
        print(_format_text(result, issue_limit=args.issue_limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
