"""Pure text presentation of already-calculated employee accrual reports."""

from __future__ import annotations

from typing import Any


def _totals_lines(totals: dict[str, object]) -> list[str]:
    return [
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
        f"Начислений от ЗН:     {totals['repair_order_accrual_count']}",
        f"Отмен начислений ЗН:  {totals['repair_order_accrual_reversal_count']}",
        f"Начислено от ЗН:      {totals['repair_order_accrual_total_display']}",
        f"Начислено:            {totals['accrued_total_display']}",
    ]


def _base_salary_lines(salary: dict[str, Any]) -> list[str]:
    return ["Оклад | " + f"{salary['created_at']} | " + f"начислено: {salary['amount_display']}"]


def _shift_lines(shift: dict[str, Any]) -> list[str]:
    return [
        "Смены | "
        + f"{shift['created_at']} | "
        + f"{shift['note']} | "
        + f"начислено: {shift['amount_display']}"
    ]


def _work_lines(work: dict[str, Any]) -> list[str]:
    lines = [f"  - {work['name']}"]
    if work["quantity"] or work["price"]:
        lines.append(
            f"    Кол-во: {work['quantity'] or '-'} | " + f"Цена: {work['price_display'] or '-'}"
        )
    lines.append(f"    Стоимость: {work['total_display']}")
    if work.get("scheme"):
        lines.append(f"    Схема: {work['scheme']}")
    lines.append(f"    Начислено: {work['accrued_display']}")
    return lines


def _material_lines(material: dict[str, Any]) -> list[str]:
    lines = [f"  - Материал: {material['name']}"]
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
    return lines


def _order_lines(order: dict[str, Any]) -> list[str]:
    lines = [
        "ЗН "
        + f"{order['repair_order_number']} | {order['vehicle']} | "
        + f"госномер: {order['license_plate']}"
    ]
    lines.append(
        f"Работ: {order['work_count']} | "
        + f"Стоимость работ: {order['work_total_display']} | "
        + f"Материалов: {order['material_count']} | "
        + f"Прибыль материалов: {order['material_profit_total_display']} | "
        + f"Начислено: {order['accrued_total_display']}"
    )
    for work in order["works"]:
        lines.extend(_work_lines(work))
    for material in order["materials"]:
        lines.extend(_material_lines(material))
    lines.append("")
    return lines


def _day_lines(day: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for salary in day.get("base_salary_accruals", []):
        lines.extend(_base_salary_lines(salary))
    for shift in day.get("shift_accruals", []):
        lines.extend(_shift_lines(shift))
    for accrual in day.get("repair_order_accruals", []):
        label = "Отмена" if accrual.get("kind") == "reversal" else "Начисление"
        lines.append(
            f"{label} от ЗН {accrual['repair_order_number']} | "
            + f"база: {accrual['base_amount_display']} | "
            + f"схема: {accrual['scheme']} | "
            + f"начислено: {accrual['amount_display']}"
        )
    for order in day["repair_orders"]:
        lines.extend(_order_lines(order))
    day_totals = day["totals"]
    lines.append(
        "Итого за день: "
        + f"заказ-нарядов {day_totals['repair_order_count']}, "
        + f"окладов {day_totals['base_salary_count']}, "
        + f"смен {day_totals['shift_accrual_count']}, "
        + f"работ {day_totals['work_count']}, "
        + f"материалов {day_totals['material_count']}, "
        + f"начислений от ЗН {day_totals['repair_order_accrual_count']}, "
        + f"отмен ЗН {day_totals['repair_order_accrual_reversal_count']}, "
        + f"стоимость {day_totals['work_total_display']}, "
        + f"прибыль материалов {day_totals['material_profit_total_display']}, "
        + f"начислено {day_totals['accrued_total_display']}"
    )
    return lines


def employee_salary_report_text(
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
    lines.extend(_totals_lines(totals))
    for day in days:
        lines.extend(["", str(day["label"]), ""])
        lines.extend(_day_lines(day))
    return "\n".join(lines).strip()
