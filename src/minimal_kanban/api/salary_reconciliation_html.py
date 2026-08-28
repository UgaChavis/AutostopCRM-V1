from __future__ import annotations

import html

from ..models import business_timezone, parse_datetime


def _html_text(value: object, *, fallback: str = "-") -> str:
    text = str(value if value is not None else "").strip()
    return html.escape(text or fallback, quote=True)


def _employee_salary_reconciliation_vehicle_html(row: dict) -> str:
    vehicle = str(row.get("vehicle") or "").strip()
    plate = str(row.get("license_plate") or "").strip()
    if vehicle and plate:
        return (
            f"{_html_text(vehicle, fallback='')}"
            f'<br><span class="muted">госномер: {_html_text(plate, fallback="")}</span>'
        )
    return _html_text(vehicle or plate)


def _employee_salary_reconciliation_rows_html(report: dict) -> str:
    rows = report.get("rows")
    if not isinstance(rows, list) or not rows:
        return (
            '<tr><td colspan="11" class="empty">'
            f"{_employee_salary_reconciliation_empty_text(report)}"
            "</td></tr>"
        )
    rendered: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rendered.append(
            "<tr>"
            f'<td class="is-num">{_html_text(row.get("number"), fallback="")}</td>'
            f"<td>{_html_text(row.get('date'))}</td>"
            f"<td>{_html_text(row.get('kind_label'))}</td>"
            f"<td>{_html_text(row.get('repair_order_number'))}</td>"
            f"<td>{_employee_salary_reconciliation_vehicle_html(row)}</td>"
            f"<td>{_html_text(row.get('item'))}</td>"
            f"<td>{_html_text(row.get('calculation_base'))}</td>"
            f"<td>{_html_text(row.get('scheme'))}</td>"
            f'<td class="money">{_html_text(row.get("accrued_display"), fallback="")}</td>'
            f'<td class="money">{_html_text(row.get("payment_display"), fallback="")}</td>'
            f"<td>{_html_text(row.get('note'), fallback='')}</td>"
            "</tr>"
        )
    return "".join(rendered) or (
        '<tr><td colspan="11" class="empty">'
        f"{_employee_salary_reconciliation_empty_text(report)}"
        "</td></tr>"
    )


def _employee_salary_reconciliation_empty_text(report: dict) -> str:
    period = report.get("period")
    if isinstance(period, dict):
        label = str(period.get("label") or "").strip()
        if label:
            return _html_text(f"За период {label} движений нет.", fallback="")
    return "За выбранный период движений нет."


def _employee_salary_reconciliation_totals_html(report: dict) -> str:
    totals = report.get("totals")
    if not isinstance(totals, dict):
        totals = {}
    items = (
        ("Всего начислено", totals.get("accrued_total_display") or totals.get("accrued_total")),
        ("Выплачено", totals.get("payout_total_display") or totals.get("payout_total")),
        ("Авансы", totals.get("advance_total_display") or totals.get("advance_total")),
        (
            "Корректировка баланса",
            totals.get("adjustment_total_display") or totals.get("adjustment_total"),
        ),
        (
            "Итог к выплате",
            totals.get("amount_due_total_display") or totals.get("amount_due_total"),
        ),
    )
    return "".join(
        '<div class="summary-item">'
        f"<span>{_html_text(label)}</span>"
        f"<strong>{_html_text(value or '0')}</strong>"
        "</div>"
        for label, value in items
    )


def _employee_salary_reconciliation_print_date(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = parse_datetime(raw)
    if parsed is None:
        return raw
    return parsed.astimezone(business_timezone()).strftime("%d.%m.%Y")


def _employee_salary_reconciliation_print_html(report: dict) -> bytes:
    employee = report.get("employee")
    if not isinstance(employee, dict):
        employee = {}
    period = report.get("period")
    if not isinstance(period, dict):
        period = {}
    generated_at = _employee_salary_reconciliation_print_date(period.get("generated_at"))
    body = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        "<title>Акт сверки зарплаты</title>"
        "<style>"
        "@page { size: A4 landscape; margin: 12mm; }"
        'body { margin: 0; color: #111; background: #fff; font: 12px/1.35 "Segoe UI", Arial, sans-serif; }'
        ".toolbar { position: sticky; top: 0; display: flex; justify-content: flex-end; gap: 8px; padding: 10px 0; background: #fff; border-bottom: 1px solid #ddd; margin-bottom: 18px; }"
        ".print-button { border: 1px solid #111; background: #111; color: #fff; padding: 8px 14px; cursor: pointer; font-weight: 700; letter-spacing: .04em; }"
        "h1 { margin: 0 0 10px; font-size: 22px; line-height: 1.15; }"
        ".meta { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 18px; margin-bottom: 14px; }"
        ".meta div, .summary-item { border: 1px solid #d4d4d4; padding: 7px 8px; }"
        ".meta span, .summary-item span { display: block; color: #555; font-size: 10px; text-transform: uppercase; }"
        ".meta strong, .summary-item strong { display: block; margin-top: 2px; font-size: 13px; }"
        ".summary { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; margin: 10px 0 16px; }"
        "table { width: 100%; border-collapse: collapse; table-layout: fixed; }"
        "th, td { border: 1px solid #c9c9c9; padding: 5px 6px; vertical-align: top; word-break: break-word; }"
        "th { background: #efefef; text-align: left; font-size: 10px; text-transform: uppercase; }"
        ".is-num, .money { text-align: right; white-space: nowrap; }"
        ".muted { color: #555; }"
        ".empty { text-align: center; padding: 18px; color: #555; }"
        ".signatures { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; margin-top: 28px; }"
        ".signature { border-top: 1px solid #111; padding-top: 6px; min-height: 34px; }"
        "@media print { .toolbar { display: none; } body { font-size: 11px; } th, td { padding: 4px 5px; } }"
        "</style></head><body>"
        '<div class="toolbar"><button class="print-button" type="button" onclick="window.print()">ПЕЧАТЬ</button></div>'
        "<main>"
        "<h1>Акт сверки зарплаты</h1>"
        '<section class="meta">'
        f"<div><span>Сотрудник</span><strong>{_html_text(employee.get('name'), fallback='Сотрудник')}</strong></div>"
        f"<div><span>Должность</span><strong>{_html_text(employee.get('position'), fallback='Не указана')}</strong></div>"
        f"<div><span>Период</span><strong>{_html_text(period.get('label'), fallback='Последние 30 дней')}</strong></div>"
        "</section>"
        f'<section class="summary">{_employee_salary_reconciliation_totals_html(report)}</section>'
        "<table><thead><tr>"
        '<th style="width:34px;">№</th><th style="width:84px;">Дата</th><th style="width:76px;">Движение</th><th style="width:58px;">ЗН</th>'
        '<th style="width:130px;">Авто / госномер</th><th>Работа / позиция</th><th style="width:120px;">База расчета</th>'
        '<th style="width:105px;">Схема</th><th style="width:92px;">Начислено</th><th style="width:98px;">Выплата / аванс</th><th>Примечание</th>'
        f"</tr></thead><tbody>{_employee_salary_reconciliation_rows_html(report)}</tbody></table>"
        '<section class="signatures">'
        '<div class="signature">Бухгалтер</div>'
        '<div class="signature">Сотрудник</div>'
        f'<div class="signature">Дата{": " + _html_text(generated_at, fallback="") if generated_at else ""}</div>'
        "</section>"
        "</main></body></html>"
    )
    return body.encode("utf-8")
