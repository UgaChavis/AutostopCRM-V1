"""Server-side monetary checks shared by preview, PDF and native printing."""

from __future__ import annotations

import html
import re
from decimal import Decimal
from typing import Any

from ..services.document_money import (
    DocumentMoneyError,
    document_number,
    money,
    net_line_money,
    resolve_document_tax,
)
from .errors import PrintModuleError
from .formatting import _money_display, _parse_decimal
from .invoice_tax_visibility import invoice_tax_summary_visible
from .regulated_tax_visibility import regulated_tax_summary_visible
from .template_engine import template_value_paths
from .template_money import (
    money_template_fields,
    rounding_notes_present,
    template_money_sources,
    template_uses_tax,
)

MONEY_DOCUMENTS = frozenset(
    {
        "repair_order",
        "vehicle_acceptance_act",
        "invoice",
        "invoice_factura",
        "upd",
        "completion_act",
        "parts_sale",
    }
)
TAX_DOCUMENTS = frozenset({"invoice", "invoice_factura", "upd", "completion_act"})


def document_money_validation(document: Any, context: dict, template: Any) -> dict:
    result = {"money_policy_version": 2, "valid": True, "errors": [], "warnings": []}
    errors, warnings = result["errors"], result["warnings"]
    paths = (
        tuple(template_value_paths(template.content, context)) if not template.is_builtin else ()
    )
    has_money, raw = (
        money_template_fields(template.content, paths=paths)
        if not template.is_builtin
        else (False, False)
    )
    sources = template_money_sources(paths)
    invoice_money = document.id == "invoice" or "invoice" in sources
    regulated_money = document.id in {"invoice_factura", "upd"} or "regulated" in sources
    regulated_unit_money = regulated_money or any(
        path.rsplit(".", 1)[-1] in {"regulated_price", "regulated_price_display"} for path in paths
    )
    act_money = document.id == "completion_act" or "completion_act" in sources
    if act_money and not context["completion_act"]:
        errors.append(_unverified_template_issue())
        act_money = False
    if raw:
        errors.append(_unverified_template_issue())
    tax = context["money_tax_context"]
    needs_tax = document.id in TAX_DOCUMENTS or (
        not template.is_builtin and template_uses_tax(template.content, paths=paths)
    )
    if needs_tax and not tax["known"]:
        errors.append(
            {
                "code": "tax_status_unknown",
                "field": "tax_label",
                "message": "Укажите налоговый статус заказа или профиля печати.",
            }
        )
    if (
        document.id == "invoice"
        and not template.is_builtin
        and not invoice_tax_summary_visible(template.content, context)
    ):
        errors.append(
            {
                "code": "invoice_vat_summary_missing",
                "field": "invoice.tax_label",
                "message": "Шаблон счёта должен показывать строку «В том числе НДС 5%» и сумму НДС.",
            }
        )
    if (
        document.id in {"invoice_factura", "upd"}
        and not template.is_builtin
        and not regulated_tax_summary_visible(template.content, context)
    ):
        errors.append(
            {
                "code": "regulated_vat_summary_unverified",
                "field": "regulated.tax_rate_display",
                "message": "Шаблон должен показывать ставку НДС 5% и сумму НДС из расчёта документа или каждой строки; другие налоговые статусы недопустимы.",
            }
        )
    if document.id not in MONEY_DOCUMENTS and not has_money:
        result["valid"] = not errors
        return result
    if document.id == "invoice":
        total_fields = {
            key: context["invoice"][key] for key in ("total", "vat", "prepayment", "amount_due")
        }
    elif document.id == "parts_sale":
        total_fields = {"materials": context["totals"]["materials"]}
    elif document.id == "vehicle_acceptance_act":
        total_fields = {"cash_total": context["totals"]["cash_total"]}
    elif document.id in TAX_DOCUMENTS:
        total_fields = document_money_summary(document, context)
    else:
        total_fields = {
            key: context["totals"][key]
            for key in (
                "works",
                "materials",
                "cash_total",
                "noncash_total",
                "prepayment",
                "due",
                "grand",
            )
        }
    if invoice_money:
        total_fields.update(
            {
                f"invoice.{key}": context["invoice"][key]
                for key in ("total", "vat", "prepayment", "amount_due")
            }
        )
    if regulated_money:
        total_fields.update(
            {f"regulated.{key}": context["regulated"][key] for key in ("total_with_tax", "vat")}
        )
    if act_money:
        total_fields.update(
            {
                f"completion_act.{key}": context["completion_act"]["totals"][key]
                for key in ("gross", "vat")
            }
        )
    if "totals" in sources:
        total_fields.update(
            {
                f"totals.{key}": context["totals"][key]
                for key in (
                    "works",
                    "materials",
                    "cash_total",
                    "noncash_total",
                    "prepayment",
                    "due",
                    "grand",
                )
            }
        )
    for field, value in total_fields.items():
        try:
            document_number(value, field=field)
        except DocumentMoneyError as error:
            errors.append(
                {"code": "document_total_out_of_range", "field": field, "message": str(error)}
            )
    rows = (
        []
        if document.id == "completion_act"
        and context["completion_act"].get("calculation_mode") == "manual_net"
        and not (sources - {"completion_act"})
        else context["materials"]
        if document.id == "parts_sale" and not (sources - {"materials"})
        else context["works"] + context["materials"]
    )
    for index, row in enumerate(rows):
        try:
            quantity = document_number(row["quantity"], field="quantity")
            price = document_number(row["price"], field="price")
            total = document_number(row["total"], field="total")
            if quantity is None or price is None or total is None:
                errors.append(
                    {
                        "code": "line_money_missing",
                        "row": index + 1,
                        "field": "quantity"
                        if quantity is None
                        else "price"
                        if price is None
                        else "total",
                        "message": "Заполните количество, цену и сумму денежной строки.",
                    }
                )
            if quantity is not None and price is not None and total is not None:
                delta = money(total - net_line_money(price, quantity)[0])
                if abs(delta) > Decimal("0.01") or (quantity == 0 and total != 0):
                    errors.append(
                        {
                            "code": "line_amount_mismatch",
                            "row": index + 1,
                            "field": "total",
                            "delta": str(delta),
                            "message": "Цена и количество не согласованы с суммой строки.",
                        }
                    )
        except DocumentMoneyError as error:
            errors.append({"code": "number_out_of_range", "row": index + 1, "message": str(error)})
    if invoice_money:
        adjustment = context["invoice"]["settlement_rounding_adjustment"]
        if abs(adjustment) > Decimal("0.02"):
            errors.append(
                {
                    "code": "settlement_mismatch",
                    "field": "amount_due",
                    "delta": str(adjustment),
                    "message": "Остаток к оплате не согласован с предоплатой.",
                }
            )
    if act_money:
        act = context["completion_act"]
        errors.extend(act.get("money_errors", []))
        if act.get("money_policy_version") == 1 or act.get("legacy_review_pending"):
            errors.append(
                {
                    "code": "legacy_money_policy",
                    "message": "Обновите расчёт старого акта и сохраните новую версию.",
                }
            )
        if act.get("is_stale"):
            errors.append(
                {
                    "code": "stale_money_snapshot",
                    "message": "Исходные данные изменились. Обновите расчёт акта.",
                }
            )
    displayed_rows = (
        context["invoice"]["line_items"]
        if document.id == "invoice"
        else context["regulated"]["rows"]
        if document.id in {"invoice_factura", "upd"}
        else context["completion_act"].get("items", [])
        if document.id == "completion_act"
        else context["materials"]
        if document.id == "parts_sale"
        else rows
    )
    aggregate_rows = displayed_rows
    additional_rows = []
    if sources & {"totals", "future_repair_order_v2", "vehicle_acceptance_act"}:
        additional_rows.extend(rows)
    if "works" in sources:
        additional_rows.extend(context["works"])
    if sources & {"materials", "parts_sale", "parts_sale_items"}:
        additional_rows.extend(context["materials"])
    if "line_items" in sources:
        additional_rows.extend(context["line_items"])
    if invoice_money:
        additional_rows.extend(context["invoice"]["line_items"])
    if regulated_money:
        additional_rows.extend(context["regulated"]["rows"])
    if act_money:
        additional_rows.extend(context["completion_act"].get("items", []))
    displayed_rows = list({id(row): row for row in [*displayed_rows, *additional_rows]}.values())
    act_row_ids = (
        {id(row) for row in context["completion_act"].get("items", [])} if act_money else set()
    )
    for index, row in enumerate(displayed_rows):
        if id(row) in act_row_ids and (
            row.get("quantity_display") == "—"
            or row.get("price_without_vat") is None
            or row.get("sum_without_vat") is None
        ):
            errors.append(
                {
                    "code": "line_money_missing",
                    "row": index + 1,
                    "field": "items",
                    "message": "Заполните количество и цену денежной строки.",
                }
            )
        if row.get("money_error") or (regulated_unit_money and row.get("regulated_money_error")):
            errors.append(
                {
                    "code": "unit_precision_unavailable",
                    "row": index + 1,
                    "field": "price",
                    "message": "Цена выходит за допустимый диапазон или не согласуется с суммой до копейки при точности до 11 разрядов.",
                }
            )
    if any(
        re.search(
            r",\d{3,}", str(row.get("price_display", row.get("price_without_vat_display", "")))
        )
        for row in displayed_rows
    ):
        warnings.append(
            {
                "code": "extended_unit_precision",
                "message": "В цене сохранены дополнительные разряды для точного расчёта суммы строки.",
            }
        )
    if (
        not template.is_builtin
        and not raw
        and not rounding_notes_present(
            template.content,
            context,
            displayed_rows,
            settlement=invoice_money and context["invoice"]["has_settlement_rounding_adjustment"],
            aggregate_settlement=document.id == "invoice",
            aggregate_rows=aggregate_rows,
        )
    ):
        errors.append(_unverified_template_issue(rounding=True))
    result["valid"] = not errors
    return result


def _unverified_template_issue(*, rounding: bool = False) -> dict:
    return {
        "code": "template_money_format_unverified",
        "field": "template",
        "message": (
            "Примечания округления соответствующих строк и зачёта предоплаты должны быть видны при печати. "
            "Если они уже добавлены, упростите их CSS: внешние стили, неизвестные условия, скрипты, обрезка и сложное позиционирование не позволяют подтвердить видимость."
            if rounding
            else "Используйте денежные поля *_display и видимые примечания округления соответствующих строк."
        ),
    }


def aggregate_money_validation(documents: list[dict]) -> dict:
    results = [item["money_validation"] for item in documents]
    return {
        "money_policy_version": 2,
        "valid": all(item["valid"] for item in results),
        "errors": [issue for item in results for issue in item["errors"]],
        "warnings": [issue for item in results for issue in item["warnings"]],
    }


def document_money_summary(document: Any, context: dict) -> dict:
    if document.id == "invoice":
        return {key: str(context["invoice"][key]) for key in ("total", "vat")}
    if document.id in {"invoice_factura", "upd"}:
        return {
            "total": str(context["regulated"]["total_with_tax"]),
            "vat": str(context["regulated"]["vat"]),
        }
    if document.id == "completion_act":
        totals = context["completion_act"]["totals"]
        return {"total": totals["gross"], "vat": totals["vat"]}
    return {}


def batch_document_money_summary(document: Any, context: dict) -> dict:
    summary = document_money_summary(document, context)
    if document.id == "completion_act":
        act = context["completion_act"]
        if act.get("calculation_mode") != "order_cashless":
            return summary
        # These totals belong to the displayed calculation, including a saved
        # snapshot. The current order's tax may differ from that saved status.
        tax = resolve_document_tax(act["totals"]["tax_label"])
    elif document.id in {"invoice", "invoice_factura", "upd"}:
        tax = context["money_tax_context"]
    else:
        return summary
    return {
        **summary,
        "tax_status": {
            "known": tax["known"],
            "has_vat": tax["has_vat"],
            "rate": format(tax["rate"].normalize(), "f"),
        },
    }


def rounding_notes_html(document: Any, context: dict) -> str:
    rows = (
        context["invoice"]["line_items"]
        if document.id == "invoice"
        else context["regulated"]["rows"]
        if document.id in {"invoice_factura", "upd"}
        else context["materials"]
        if document.id == "parts_sale"
        else context["works"] + context["materials"]
    )
    notes = [
        f"Строка {index + 1}: округление {row['rounding_adjustment_display']} руб."
        for index, row in enumerate(rows)
        if row.get("has_rounding_adjustment")
    ]
    if document.id == "invoice" and context["invoice"]["has_settlement_rounding_adjustment"]:
        notes.append(
            "Округление зачёта предоплаты: "
            + context["invoice"]["settlement_rounding_adjustment_display"]
            + " руб."
        )
    return "".join(f"<div>{html.escape(note)}</div>" for note in notes)


def validate_document_batch(payloads: list[dict]) -> list[dict]:
    ids = {item["document"].id for item in payloads}
    summaries = [
        item.get("money_summary")
        for item in payloads
        if item["document"].id in {"invoice", "invoice_factura", "upd"}
        or item["document"].id == "completion_act"
        and item.get("calculation_mode") == "order_cashless"
    ]
    mismatch = bool(summaries and any(item != summaries[0] for item in summaries))
    for item in payloads:
        validation = item["money_validation"]
        if mismatch and item.get("money_summary"):
            validation["errors"].append(
                {
                    "code": "document_totals_mismatch",
                    "message": "Итоги или НДС связанных документов не совпадают.",
                }
            )
            validation["valid"] = False
        if (
            item["document"].id == "completion_act"
            and ids & {"invoice", "invoice_factura", "upd"}
            and item.get("calculation_mode") != "order_cashless"
        ):
            validation["errors"].append(
                {
                    "code": "document_basis_mismatch",
                    "message": "В общем комплекте используйте акт по безналичной стоимости. Ручной или наличный акт выпускается отдельно.",
                }
            )
            validation["valid"] = False
        for issue in validation["errors"] + validation["warnings"]:
            issue["document"] = item["document"].id
        item["warnings"] = list(
            dict.fromkeys(
                item["warnings"]
                + [issue["message"] for issue in validation["errors"] + validation["warnings"]]
            )
        )
    return payloads


def require_valid_document_batch(payloads: list[dict]) -> None:
    failures = [
        item["money_validation"] for item in payloads if not item["money_validation"]["valid"]
    ]
    if failures:
        raise PrintModuleError(
            "document_money_validation_failed",
            "Исправьте денежные ошибки перед выпуском документа.",
            status_code=422,
            details={"money_validation": failures},
        )


def row_rounding_fields(row: Any) -> dict:
    quantity, price = _parse_decimal(row.quantity), _parse_decimal(row.price)
    total = _parse_decimal(row.total or row.computed_total())
    delta = (
        money(total - net_line_money(price, quantity)[0])
        if quantity is not None and price is not None and total is not None
        else Decimal(0)
    )
    return {
        "rounding_adjustment": delta,
        "rounding_adjustment_display": ("+" if delta > 0 else "") + _money_display(delta),
        "has_rounding_adjustment": delta != 0,
    }
