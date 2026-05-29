from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

UrlOpen = Callable[[urllib.request.Request, float], Any]


def _url(base_url: str, path: str, query: dict[str, object] | None = None) -> str:
    target = base_url.rstrip("/") + "/" + path.lstrip("/")
    if query:
        target += "?" + urllib.parse.urlencode(query)
    return target


def _fetch_json(
    base_url: str,
    path: str,
    *,
    query: dict[str, object] | None = None,
    timeout: float = 15.0,
    urlopen: UrlOpen = urllib.request.urlopen,
) -> dict[str, Any]:
    request = urllib.request.Request(
        _url(base_url, path, query),
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:
        raw_body = response.read()
    return json.loads(raw_body.decode("utf-8"))


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
    normalized = value.quantize(Decimal("0.01"))
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


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


def _audit_report_totals(report: dict[str, Any], *, month: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    summaries = _summary_by_employee(report)
    detail_totals: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    detail_counts = Counter(_detail_duplicate_key(row) for row in _items(report.get("detail_rows")))
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

    for employee_id, summary in summaries.items():
        employee_name = str(summary.get("employee_name") or "")
        component_total = (
            _money(summary.get("base_salary_accrued_total"))
            + _money(summary.get("shift_accrued_total"))
            + _money(summary.get("work_accrued_total"))
            + _money(summary.get("materials_accrued_total"))
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
            if detail_value != summary_value:
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
    if int(ledger.get("journal_total") or len(journal_rows)) != len(journal_rows):
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
    urlopen: UrlOpen = urllib.request.urlopen,
    reference: datetime | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    employees_payload = _fetch_json(
        base_url, "/api/list_employees", timeout=timeout, urlopen=urlopen
    )
    employees = _items(_data(employees_payload).get("employees"))
    for month in _month_keys(months_back, reference=reference):
        report_payload = _fetch_json(
            base_url,
            "/api/get_payroll_report",
            query={"month": month},
            timeout=timeout,
            urlopen=urlopen,
        )
        issues.extend(_audit_report_totals(_data(report_payload), month=month))
    for employee in employees:
        employee_id = str(employee.get("id") or "")
        if not employee_id:
            continue
        ledger_payload = _fetch_json(
            base_url,
            "/api/get_employee_salary_ledger",
            query={"employee_id": employee_id, "months": ledger_months},
            timeout=timeout,
            urlopen=urlopen,
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
    parser.add_argument("--months-back", type=int, default=1)
    parser.add_argument("--ledger-months", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    parser.add_argument("--issue-limit", type=int, default=50)
    args = parser.parse_args()

    try:
        result = build_payroll_audit(
            args.base_url,
            months_back=args.months_back,
            ledger_months=args.ledger_months,
            timeout=args.timeout,
        )
    except urllib.error.URLError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    if args.format == "json":
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    else:
        print(_format_text(result, issue_limit=args.issue_limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
