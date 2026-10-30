from __future__ import annotations

import re
from copy import deepcopy
from decimal import Decimal
from typing import Any

from ..models import Card, ClientProfile
from ..repair_order import (
    REPAIR_ORDER_PAYMENT_NET_RATE,
    RepairOrder,
    repair_order_cashless_gross_value,
)
from ..services.document_money import (
    DocumentMoneyError,
    build_canonical_document_money,
    document_number,
    money,
    net_line_money,
)
from .errors import PrintModuleError
from .formatting import (
    _money_display,
    _money_words_display,
    _number_display,
    _parse_decimal,
)
from .invoice_vat import invoice_tax_payload, invoice_vat_rows
from .models import CompletionActFormData, CompletionActItemData, PrintModuleSettings


class CompletionActCalculationMixin:
    def _completion_act_projection(self, form, calculation, record, source_fingerprint) -> dict:
        from .service import _request_payload_fingerprint

        active = record is not None and not record.deleted
        required = bool(
            active
            and form.money_policy_version == 2
            and (
                record.money_policy_version == 1 or record.source_fingerprint != source_fingerprint
            )
        )
        fingerprint = _request_payload_fingerprint(
            {
                "draft_version": record.version if record else 0,
                "source_fingerprint": source_fingerprint,
                "policy": form.money_policy_version,
                "mode": form.calculation_mode,
                "items": [item.to_dict() for item in form.items],
                "totals": {key: calculation["totals"][key] for key in ("base", "vat", "gross")},
                "tax": calculation["tax_context"],
            }
        )
        return {
            "recalculation_required": required,
            "calculation_fingerprint": fingerprint,
            "projected_totals": deepcopy(calculation["totals"]),
            "projected_tax_context": deepcopy(calculation["tax_context"]),
        }

    @staticmethod
    def _require_completion_act_recalculation(projection, accepted, *, dry_run: bool) -> None:
        if accepted is not None and (
            not isinstance(accepted, str) or re.fullmatch(r"[0-9a-f]{64}", accepted) is None
        ):
            raise PrintModuleError(
                "validation_error",
                "Передайте отпечаток принятого расчёта акта.",
                details={"field": "accepted_calculation_fingerprint"},
            )
        if (
            not dry_run
            and projection["recalculation_required"]
            and (accepted != projection["calculation_fingerprint"])
        ):
            raise PrintModuleError(
                "completion_act_recalculation_required",
                "Обновите расчёт акта, сравните суммы и сохраните принятый расчёт.",
                status_code=409,
                details={"recalculation_required": True},
            )

    @staticmethod
    def _act_record_policy(record: Any) -> tuple[int, str]:
        return (
            (record.money_policy_version, record.calculation_mode)
            if record is not None and not record.deleted
            else (2, "order_cashless")
        )

    def _completion_act_decimal(self, value: Any, *, field: str) -> Decimal | None:
        from .service import _COMPLETION_ACT_MONEY_ABS_MAX, _COMPLETION_ACT_QUANTITY_ABS_MAX

        maximum = (
            _COMPLETION_ACT_QUANTITY_ABS_MAX
            if field.endswith(".quantity")
            else _COMPLETION_ACT_MONEY_ABS_MAX
        )
        try:
            return document_number(value, field=field, maximum=maximum)
        except DocumentMoneyError as error:
            raise PrintModuleError(
                "validation_error",
                str(error),
                details={
                    "field": field,
                    "max_decimal_places": 11,
                    "max_quantity" if field.endswith(".quantity") else "max_amount": str(maximum),
                },
            ) from error

    def _linked_act_rows(self, order: RepairOrder, tax: dict, mode: str) -> list[dict]:
        rows = [
            (section, index, row)
            for section, values in (("works", order.works), ("materials", order.materials))
            for index, row in enumerate(values)
        ]
        totals = [money(row.total_value()) for _, _, row in rows]
        quantities = [_parse_decimal(row.quantity) for _, _, row in rows]
        cashless = mode == "order_cashless"
        target = (
            repair_order_cashless_gross_value(sum(totals, Decimal(0)))
            if cashless
            else money(sum(totals, Decimal(0)))
        )
        try:
            calculation = build_canonical_document_money(
                totals,
                quantities,
                target_total=target,
                net_rate=REPAIR_ORDER_PAYMENT_NET_RATE if cashless else Decimal(1),
                tax_rate=tax["rate"],
            )
        except DocumentMoneyError:
            return [
                {
                    "id": f"{section}:{row.id or f'{section}-{index + 1}'}",
                    "section": section,
                    "name": row.name,
                    "unit": "ч" if section == "works" else row.inventory_unit or "шт",
                    "quantity": row.quantity,
                    "price": row.price,
                    "subtotal": total,
                    "vat": Decimal(0),
                    "gross": total,
                    "money_error": True,
                }
                for (section, index, row), total in zip(rows, totals, strict=True)
            ]
        # Source tax still owns the established allocation of gross cents.
        # The print policy only splits VAT out of those unchanged row totals.
        projected = invoice_vat_rows(
            [
                {"total": result.gross, "quantity": quantity}
                for result, quantity in zip(calculation.rows, quantities, strict=True)
            ]
        )
        return [
            {
                "id": f"{section}:{row.id or f'{section}-{index + 1}'}",
                "section": section,
                "name": row.name,
                "unit": "ч" if section == "works" else row.inventory_unit or "шт",
                "quantity": row.quantity,
                "price": (
                    format(result["regulated_price"], "f")
                    if result.get("regulated_price") is not None
                    else row.price
                ),
                "subtotal": result.get("subtotal", result["total"]),
                "vat": result.get("vat", Decimal(0)),
                "gross": result["total"],
                "money_error": bool(
                    result.get("money_error") or result.get("regulated_money_error")
                ),
            }
            for (section, index, row), result in zip(rows, projected, strict=True)
        ]

    def _prepare_linked_act_form(
        self,
        form: CompletionActFormData,
        order: RepairOrder,
        settings: Any,
        mode: str = "order_cashless",
    ) -> CompletionActFormData:
        tax = self._document_tax(order, settings)
        form.calculation_mode = mode
        form.items = [
            CompletionActItemData.from_dict(row)
            for row in self._linked_act_rows(order, tax, form.calculation_mode)
        ]
        return form

    def _effective_act_state(
        self,
        card: Any,
        order: RepairOrder,
        client: Any,
        settings: Any,
        explicit: dict | None = None,
        *,
        record: Any = ...,
        recalculate: bool = False,
    ) -> tuple:
        from .service import _nested_merge

        cycle_key = self._completion_act_cycle_key(card, order)
        if record is ...:
            record = self._read_completion_act_record(cycle_key)
        active = record is not None and not record.deleted
        fresh = self._default_completion_act_form(card, order, client, settings)
        base = (
            self._raw_completion_act_form(card, order, client, settings)
            if active and record.money_policy_version == 1
            else fresh
        )
        payload = _nested_merge(base.to_dict(), record.overrides if active else {})
        if active:
            payload.update(
                money_policy_version=record.money_policy_version,
                calculation_mode=record.calculation_mode,
            )
        if explicit is not None:
            payload = {**explicit}
            payload.setdefault("money_policy_version", record.money_policy_version if active else 2)
            payload.setdefault(
                "calculation_mode", record.calculation_mode if active else "manual_net"
            )
        form = self._normalized_completion_act_form(payload)
        fingerprint = self._completion_act_source_fingerprint(
            card,
            order,
            client,
            settings,
            cycle_key=cycle_key,
            money_policy=self._act_record_policy(record),
        )
        stale = bool(active and record.source_fingerprint != fingerprint)
        if (
            active
            and record.money_policy_version == 2
            and record.money_snapshot
            and (explicit is None or stale and not recalculate)
        ):
            calculation = deepcopy(record.money_snapshot["calculation"])
            if explicit is None:
                form = self._normalized_completion_act_form(record.money_snapshot["form"])
        else:
            calculation = self._completion_act_calculation(form, order=order, settings=settings)
        calculation["is_stale"] = stale
        calculation["legacy_review_pending"] = bool(active and record.money_policy_version == 1)
        calculation["calculation_fingerprint"] = (
            self._completion_act_projection(form, calculation, record, fingerprint)[
                "calculation_fingerprint"
            ]
            if recalculate or not (stale or calculation["legacy_review_pending"])
            else None
        )
        return fresh, form, calculation, record, fingerprint, stale

    def _completion_act_calculation(
        self, form: CompletionActFormData, *, order: RepairOrder | None = None, settings: Any = None
    ) -> dict[str, Any]:
        from .service import _completion_act_checked_money, _display

        if form.money_policy_version == 1:
            result = self._legacy_completion_act_calculation(form)
            result.update(
                money_policy_version=1,
                calculation_mode="manual_net",
                tax_context={"label": "НДС 5%", "known": True, "rate": "0.05", "source": "legacy"},
            )
            result["totals"].update(has_vat=True, tax_label="НДС 5%")
            result["warnings"].insert(
                0, "Старый расчёт акта: обновите расчёт и сохраните новую версию перед выпуском."
            )
            return result
        tax = invoice_tax_payload()
        linked = (
            self._linked_act_rows(
                order,
                self._document_tax(order, settings or self._read_settings()),
                form.calculation_mode,
            )
            if order is not None and form.calculation_mode != "manual_net"
            else []
        )
        errors = []
        if form.calculation_mode != "manual_net" and [item.id for item in form.items] != [
            item["id"] for item in linked
        ]:
            errors.append(
                {
                    "code": "linked_act_rows_changed",
                    "field": "items",
                    "message": "Для изменения строк переключите акт в ручной режим.",
                }
            )
        linked_by_id = {row["id"]: row for row in linked}
        items = []
        base = vat_total = gross = Decimal(0)
        missing = [
            key
            for key, value in (
                ("document_number", form.document_number),
                ("document_date", form.document_date),
                ("performer.legal_name", form.performer.legal_name),
                ("customer.legal_name", form.customer.legal_name),
            )
            if not value
        ]
        warnings = []
        if not form.items:
            missing.append("items")
            warnings.append("В акте нет работ и материалов.")
        for index, item in enumerate(form.items):
            quantity = self._completion_act_decimal(item.quantity, field=f"items[{index}].quantity")
            price = self._completion_act_decimal(item.price, field=f"items[{index}].price")
            subtotal = None
            vat = Decimal(0)
            source = linked_by_id.get(item.id)
            if form.calculation_mode != "manual_net" and source is not None:
                if source.get("money_error"):
                    errors.append(
                        {
                            "code": "unit_precision_unavailable",
                            "row": index + 1,
                            "field": "price",
                            "message": "Цена выходит за допустимый диапазон или не согласуется с суммой при точности до 11 разрядов.",
                        }
                    )
                if (
                    quantity != _parse_decimal(source["quantity"])
                    or price != _parse_decimal(source["price"])
                    or item.name != source["name"]
                    or item.unit != source["unit"]
                    or item.section != source["section"]
                ):
                    errors.append(
                        {
                            "code": "linked_act_price_changed",
                            "field": f"items[{index}]",
                            "message": "Количество или цена связанного акта изменены. Выберите ручной режим.",
                        }
                    )
                subtotal, vat, row_gross = source["subtotal"], source["vat"], source["gross"]
            elif quantity is not None and price is not None:
                subtotal, vat, row_gross = net_line_money(price, quantity, tax["rate"])
                _completion_act_checked_money(subtotal, field=f"items[{index}].total")
            else:
                warnings.append(
                    f"Строка {index + 1}: заполните количество и цену для расчёта суммы."
                )
            if subtotal is not None:
                base += subtotal
                vat_total += vat
                gross += row_gross
            if not item.name:
                warnings.append(f"Строка {index + 1}: не указано наименование.")
            if not item.unit:
                warnings.append(f"Строка {index + 1}: не указана единица измерения.")
            quantity_display = _number_display(quantity)
            price_display = _number_display(price, minimum_places=2)
            items.append(
                {
                    "index": index + 1,
                    "id": item.id,
                    "section": item.section,
                    "name": _display(item.name),
                    "unit_display": _display(item.unit),
                    "quantity_display": quantity_display,
                    "compact_quantity": len(quantity_display) > 14,
                    "price_without_vat": price,
                    "price_without_vat_display": price_display,
                    "compact_price": len(price_display) > 17,
                    "sum_without_vat": subtotal,
                    "sum_without_vat_display": _money_display(subtotal),
                    "vat": format(vat, "f"),
                }
            )
        for name, value in (("base", base), ("vat", vat_total), ("gross", gross)):
            _completion_act_checked_money(value, field=f"totals.{name}")
        totals = {
            "item_count": len(items),
            "item_count_display": str(len(items)),
            "vat_rate": format(tax["rate"] * 100, "f"),
            "vat_rate_display": tax["rate_display"],
            "tax_label": tax["label"] or "НДС не определён",
            "has_vat": tax["has_vat"],
        }
        for name, value in (("base", base), ("vat", vat_total), ("gross", gross)):
            totals.update(
                {
                    name: format(money(value), "f"),
                    f"{name}_display": _money_display(value),
                    f"{name}_words_display": _money_words_display(value),
                }
            )
        return {
            "items": items,
            "totals": totals,
            "warnings": warnings,
            "missing_fields": missing,
            "money_policy_version": 2,
            "calculation_mode": form.calculation_mode,
            "tax_context": {**tax, "rate": str(tax["rate"])},
            "money_errors": errors,
        }

    def _completion_act_source_fingerprint(
        self,
        card: Card,
        order: RepairOrder,
        client: ClientProfile | None,
        settings: PrintModuleSettings,
        *,
        cycle_key: str,
        money_policy: tuple[int, str] = (2, "order_cashless"),
    ) -> str:
        from .service import _request_payload_fingerprint

        fresh = self._default_completion_act_form(card, order, client, settings)
        payload = {
            "cycle_key": cycle_key,
            "money_policy_version": money_policy[0],
            "calculation_mode": money_policy[1],
            "linked_client_id": card.client_id,
            "fresh_form": fresh.to_dict(),
            "tax_context": self._document_tax(order, settings),
            "print_tax_context": invoice_tax_payload(),
            "source_money_rows": [
                {
                    "section": section,
                    **{
                        key: getattr(row, key)
                        for key in ("id", "name", "quantity", "price", "total", "inventory_unit")
                    },
                }
                for section, rows in (("works", order.works), ("materials", order.materials))
                for row in rows
            ],
        }
        return _request_payload_fingerprint(payload)

    def _completion_act_sources(
        self,
        form: CompletionActFormData,
        *,
        has_exact_client: bool,
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        party_fields = form.performer.to_dict().keys()
        sources: dict[str, Any] = {
            "document_number": "repair_order",
            "document_date": "repair_order",
            "basis": "empty",
            "performer": {key: "settings" for key in party_fields},
            "customer": {
                key: (
                    "client"
                    if has_exact_client
                    else ("repair_order" if key == "legal_name" else "empty")
                )
                for key in form.customer.to_dict()
            },
            "items": [{key: "repair_order" for key in item.to_dict()} for item in form.items],
            "acceptance_text": "system",
        }

        def mark_manual(target: Any, changed: Any) -> Any:
            if isinstance(target, dict) and isinstance(changed, dict):
                for key, value in changed.items():
                    if key in target:
                        target[key] = mark_manual(target[key], value)
                return target
            if isinstance(changed, list):
                return [
                    {key: "manual" for key in item} if isinstance(item, dict) else "manual"
                    for item in changed
                ]
            return "manual"

        return mark_manual(sources, overrides)

    def _completion_act_response(
        self,
        card: Card,
        order: RepairOrder,
        *,
        client: ClientProfile | None,
        settings: PrintModuleSettings,
        idempotent_replay: bool = False,
    ) -> dict[str, Any]:
        cycle_key = self._completion_act_cycle_key(card, order)
        record = self._read_completion_act_record(cycle_key)
        exists = record is not None and not record.deleted
        state = "absent" if record is None else "active" if exists else "reset_tombstone"
        overrides = record.overrides if exists and record is not None else {}
        fresh, effective, calculation, record, source_fingerprint, is_stale = (
            self._effective_act_state(card, order, client, settings, record=record)
        )
        warnings = list(calculation["warnings"])
        if is_stale:
            warnings.insert(
                0,
                "Исходные данные CRM изменились после сохранения черновика; ручные правки сохранены.",
            )
        return {
            "card_id": card.id,
            "document_type": "completion_act",
            "form": effective.to_dict(),
            "effective": effective.to_dict(),
            "fresh_form": fresh.to_dict(),
            "suggested_defaults": fresh.to_dict(),
            "sources": self._completion_act_sources(
                fresh,
                has_exact_client=client is not None,
                overrides=overrides,
            ),
            "draft": {
                "exists": exists,
                "version": record.version if record is not None else 0,
                "state": state,
                "revision": record.version if record is not None else 0,
                "last_operation": record.operation if record is not None else None,
                "cycle_key": cycle_key,
                "updated_at": record.updated_at if record is not None else "",
                "filled_by": record.filled_by if record is not None else "",
                "source": record.source if record is not None else "",
                "source_fingerprint": (
                    record.source_fingerprint if record is not None else source_fingerprint
                ),
                "current_source_fingerprint": source_fingerprint,
                "is_stale": is_stale,
                "idempotent_replay": idempotent_replay,
            },
            "totals": calculation["totals"],
            "money_policy_version": effective.money_policy_version,
            "calculation_mode": effective.calculation_mode,
            "tax_context": calculation["tax_context"],
            "linked_forms": {
                mode: self._prepare_linked_act_form(
                    CompletionActFormData.from_dict(fresh.to_dict()), order, settings, mode
                ).to_dict()
                for mode in ("order_cashless", "order_cash")
            },
            "warnings": warnings,
            "missing_fields": calculation["missing_fields"],
        }

    def _legacy_completion_act_calculation(self, form: CompletionActFormData) -> dict[str, Any]:
        from .service import (
            _COMPLETION_ACT_VAT_RATE,
            _completion_act_checked_money,
            _completion_act_quantity_display,
            _display,
        )

        warnings: list[str] = []
        missing_fields: list[str] = []
        for field_name, value in (
            ("document_number", form.document_number),
            ("document_date", form.document_date),
            ("performer.legal_name", form.performer.legal_name),
            ("customer.legal_name", form.customer.legal_name),
        ):
            if not value:
                missing_fields.append(field_name)
        if not form.items:
            missing_fields.append("items")
            warnings.append("В акте нет работ и материалов.")
        items_payload: list[dict[str, Any]] = []
        base = Decimal("0")
        for index, item in enumerate(form.items):
            quantity = self._legacy_completion_act_decimal(
                item.quantity, field=f"items[{index}].quantity"
            )
            price = self._legacy_completion_act_decimal(item.price, field=f"items[{index}].price")
            line_total: Decimal | None = None
            if quantity is not None and price is not None:
                line_total = _completion_act_checked_money(
                    quantity * price, field=f"items[{index}].total"
                )
                base = _completion_act_checked_money(base + line_total, field="totals.base")
            else:
                warnings.append(
                    f"Строка {index + 1}: заполните количество и цену для расчёта суммы."
                )
            if not item.name:
                warnings.append(f"Строка {index + 1}: не указано наименование.")
            if not item.unit:
                warnings.append(f"Строка {index + 1}: не указана единица измерения.")
            items_payload.append(
                {
                    "index": index + 1,
                    "id": item.id,
                    "section": item.section,
                    "name": _display(item.name),
                    "unit_display": _display(item.unit),
                    "quantity_display": _completion_act_quantity_display(quantity),
                    "price_without_vat": price,
                    "price_without_vat_display": _money_display(price),
                    "sum_without_vat": line_total,
                    "sum_without_vat_display": _money_display(line_total),
                }
            )
        base = _completion_act_checked_money(base, field="totals.base")
        vat = _completion_act_checked_money(base * _COMPLETION_ACT_VAT_RATE, field="totals.vat")
        gross = _completion_act_checked_money(base + vat, field="totals.gross")
        if missing_fields:
            warnings.insert(0, "Часть полей акта не заполнена; печать остаётся доступной.")
        totals = {
            "item_count": len(items_payload),
            "item_count_display": str(len(items_payload)),
            "base": format(base, "f"),
            "base_display": _money_display(base),
            "vat_rate": "5",
            "vat_rate_display": "5%",
            "vat": format(vat, "f"),
            "vat_display": _money_display(vat),
            "gross": format(gross, "f"),
            "gross_display": _money_display(gross),
            "base_words_display": _money_words_display(base),
            "vat_words_display": _money_words_display(vat),
            "gross_words_display": _money_words_display(gross),
        }
        return {
            "items": items_payload,
            "totals": totals,
            "warnings": list(dict.fromkeys(warnings)),
            "missing_fields": missing_fields,
        }

    def _legacy_completion_act_decimal(self, value: Any, *, field: str) -> Decimal | None:
        from .service import (
            _COMPLETION_ACT_QUANTITY_ABS_MAX,
            _completion_act_checked_money,
            _normalize_text,
        )

        text = _normalize_text(value, limit=48)
        if not text:
            return None
        parsed = _parse_decimal(text)
        if parsed is None or not parsed.is_finite() or parsed < Decimal("0"):
            raise PrintModuleError(
                "validation_error",
                "Количество и цена в акте должны быть неотрицательными числами.",
                details={"field": field},
            )
        if field.endswith(".price"):
            rounded = _completion_act_checked_money(parsed, field=field)
            if rounded != parsed:
                raise PrintModuleError(
                    "validation_error",
                    "Цена в акте должна содержать не более двух знаков после запятой.",
                    details={"field": field, "max_decimal_places": 2},
                )
            return rounded
        if field.endswith(".quantity"):
            if parsed > _COMPLETION_ACT_QUANTITY_ABS_MAX:
                raise PrintModuleError(
                    "validation_error",
                    "Количество в акте превышает допустимый печатный предел.",
                    details={
                        "field": field,
                        "max_quantity": format(_COMPLETION_ACT_QUANTITY_ABS_MAX, "f"),
                    },
                )
            decimal_places = max(0, -parsed.normalize().as_tuple().exponent)
            if decimal_places > 3:
                raise PrintModuleError(
                    "validation_error",
                    "Количество в акте должно содержать не более трёх знаков после запятой.",
                    details={"field": field, "max_decimal_places": 3},
                )
        return parsed
