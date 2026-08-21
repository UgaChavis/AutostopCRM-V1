from __future__ import annotations

import base64
import hashlib
import html
import json
import math
import os
import re
import stat
import threading
import unicodedata
import uuid
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any

from ..models import Card, ClientProfile, parse_datetime, utc_now_iso
from ..repair_order import (
    REPAIR_ORDER_PAYMENT_METHOD_CASHLESS,
    RepairOrder,
    RepairOrderRow,
    repair_order_cashless_gross_value,
)
from ..storage.change_feed_projection import project_print_module
from ..storage.change_feed_store import ChangeFeedStore
from ..storage.file_lock import ProcessFileLock
from ..storage.limited_io import read_bytes_limited
from .defaults import BUILTIN_PRINT_DOCUMENTS, PRINT_BASE_STYLES, builtin_template_records
from .errors import PrintModuleError
from .formatting import (
    _RU_MONTHS_GENITIVE,
    _invoice_tax_payload,
    _line_breaks_html,
    _money_display,
    _money_ruble_display,
    _money_words_display,
    _parse_decimal,
    _round_money,
    _split_tax_included_amount,
)
from .layout import COMPLETION_ACT_LAYOUT
from .manual_documents import (
    ManualDocumentProfile,
    _first_multiline,
    _first_text,
    _manual_table_rows,
    _manual_vehicle_payload,
    _merge_manual_document_payload,
    _normalize_document_type,
    _normalize_multiline,
    _normalize_text,
    _parse_manual_document_text,
)
from .models import (
    COMPLETION_ACT_ITEMS_MAX,
    SUPPORTED_PRINT_DOCUMENT_TYPES,
    CompletionActDraftData,
    CompletionActFormData,
    CompletionActItemData,
    CompletionActPartyData,
    InspectionSheetFormData,
    PrintDocumentDefinition,
    PrintModuleSettings,
    PrintTemplateRecord,
)
from .pdf import PdfRenderError, render_html_to_pdf_bytes
from .printers import PrinterBackendError, list_printers, print_html
from .template_engine import TemplateRenderError, render_template

_SETTINGS_FILE_NAME = "settings.json"
_TEMPLATES_FILE_NAME = "templates.json"
_INSPECTION_SHEET_FORMS_FILE_NAME = "inspection_sheet_forms.json"
_COMPLETION_ACT_FORMS_FILE_NAME = "completion_act_forms.json"
_COMPLETION_ACT_FORMS_DIR_NAME = "completion_act_forms"
PRINT_JSON_FILE_MAX_BYTES = 1 * 1024 * 1024
COMPLETION_ACT_FORMS_FILE_MAX_BYTES = 64 * 1024 * 1024
COMPLETION_ACT_FORM_RECORD_MAX_BYTES = 1 * 1024 * 1024
COMPLETION_ACT_FORMS_MAX_RECORDS = 8192
COMPLETION_ACT_FEED_RECONCILE_BATCH = 16
PRINT_BRAND_LOGO_MAX_BYTES = 512 * 1024
PRINT_TEMPLATE_CONTENT_MAX_CHARS = 200_000
_PAGE_BREAK_MARKER = "<!-- AUTOSTOPCRM_PAGE_BREAK -->"
_REGULATED_LANDSCAPE_DOCUMENT_TYPES = {"invoice_factura", "upd"}
_SENTENCE_SPLIT_RE = re.compile(r"[\n\r]+|(?<=[.!?])\s+")
_UNSAFE_FILE_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_COMPLETION_ACT_RECORD_FILE_RE = re.compile(r"\A[0-9a-f]{64}\.json\Z")
_COMPLETION_ACT_TEMP_FILE_RE = re.compile(r"\A\.[0-9a-f]{64}\.[0-9a-f]{32}\.tmp\Z")
_BRAND_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "autostop_brand_logo.png"
_JSON_SAFE_MAX_DEPTH = 8
_COMPLETION_ACT_VAT_RATE = Decimal("0.05")
_COMPLETION_ACT_MONEY_ABS_MAX = Decimal("999999999.99")
_COMPLETION_ACT_QUANTITY_ABS_MAX = Decimal("99999.999")
_COMPLETION_ACT_MAX_PAGES = 40
_COMPLETION_ACT_FINAL_PAGE_MAX_EXTRA_UNITS = 40
_COMPLETION_ACT_FINAL_PAGE_COMBINED_EXTRA_UNITS = 32
_COMPLETION_ACT_ACCEPTANCE_COMBINED_EXTRA_UNITS = 10
_COMPLETION_ACT_ACCEPTANCE_MAX_LINES = 30
_COMPLETION_ACT_WIDE_LATIN = frozenset("MWmw@#%&")
_COMPLETION_ACT_ACCEPTANCE_TEXT = (
    "Вышеперечисленные работы (услуги) выполнены полностью и в срок. "
    "Заказчик претензий по объему, качеству и срокам оказания услуг не имеет."
)


def _completion_act_checked_money(value: Decimal, *, field: str) -> Decimal:
    try:
        if not value.is_finite() or value.copy_abs() > _COMPLETION_ACT_MONEY_ABS_MAX:
            raise InvalidOperation
        rounded = _round_money(value)
    except (InvalidOperation, OverflowError):
        raise PrintModuleError(
            "validation_error",
            "Сумма в акте превышает допустимый денежный предел.",
            details={
                "field": field,
                "max_amount": format(_COMPLETION_ACT_MONEY_ABS_MAX, "f"),
            },
        ) from None
    if not rounded.is_finite() or rounded.copy_abs() > _COMPLETION_ACT_MONEY_ABS_MAX:
        raise PrintModuleError(
            "validation_error",
            "Сумма в акте превышает допустимый денежный предел.",
            details={
                "field": field,
                "max_amount": format(_COMPLETION_ACT_MONEY_ABS_MAX, "f"),
            },
        )
    return rounded


def _completion_act_quantity_display(value: Decimal | None) -> str:
    if value is None:
        return "—"
    normalized = format(value.normalize(), "f")
    return normalized.replace(".", ",")


def _completion_act_mutation_locked(method: Any) -> Any:
    @wraps(method)
    def wrapped(self: PrintModuleService, *args: Any, **kwargs: Any) -> Any:
        with self._completion_act_lock:
            try:
                with self._completion_act_process_lock.acquire():
                    self._migrate_legacy_completion_act_forms(process_locked=True)
                    return method(self, *args, **kwargs)
            except TimeoutError as exc:
                raise PrintModuleError(
                    "completion_act_lock_timeout",
                    "Черновик акта временно занят другим процессом; повторите действие.",
                    status_code=503,
                ) from exc

    return wrapped


def _nested_changed_paths(current: Any, baseline: Any, *, prefix: str = "form") -> list[str]:
    if isinstance(current, dict) and isinstance(baseline, dict):
        paths: list[str] = []
        for key in sorted(set(current) | set(baseline)):
            paths.extend(
                _nested_changed_paths(
                    current.get(key),
                    baseline.get(key),
                    prefix=f"{prefix}.{key}",
                )
            )
        return paths
    if isinstance(current, list) and isinstance(baseline, list):
        paths = []
        for index in range(max(len(current), len(baseline))):
            paths.extend(
                _nested_changed_paths(
                    current[index] if index < len(current) else None,
                    baseline[index] if index < len(baseline) else None,
                    prefix=f"{prefix}[{index}]",
                )
            )
        return paths
    return [] if current == baseline else [prefix]


def _completion_act_estimated_lines(value: Any, *, chars_per_line: int) -> int:
    """Return a conservative line estimate for the fixed A4 act typography."""

    text = str(value or "")
    logical_lines = text.splitlines() or [""]
    estimated = 0
    for line in logical_lines:
        unbroken = bool(line) and not any(character.isspace() for character in line)
        width_units = sum(
            0
            if unicodedata.combining(character)
            else 3
            if unicodedata.east_asian_width(character) in {"W", "F"}
            else 2
            if character in _COMPLETION_ACT_WIDE_LATIN
            or (
                unbroken
                and (
                    "\u0400" <= character <= "\u052f"
                    or "\u2de0" <= character <= "\u2dff"
                    or "\ua640" <= character <= "\ua69f"
                )
            )
            else 1
            for character in line
        )
        estimated += max(1, math.ceil(width_units / max(1, chars_per_line)))
    return estimated


def _completion_act_item_page_weight(item: dict[str, Any]) -> int:
    """Measure one indivisible table row in tenths of a millimetre."""

    estimated_lines = max(
        1,
        _completion_act_estimated_lines(item.get("name"), chars_per_line=42),
        _completion_act_estimated_lines(item.get("unit_display"), chars_per_line=8),
        _completion_act_estimated_lines(item.get("quantity_display"), chars_per_line=10),
    )
    return (
        COMPLETION_ACT_LAYOUT.row_base_units
        + max(0, estimated_lines - 1) * COMPLETION_ACT_LAYOUT.row_extra_line_units
    )


def _completion_act_party_summary_text(party: CompletionActPartyData) -> str:
    return ", ".join(
        value
        for value in (
            party.legal_name,
            f"ИНН {party.inn}" if party.inn else "",
            f"КПП {party.kpp}" if party.kpp else "",
            party.address,
        )
        if value
    )


def _completion_act_first_page_capacity(form: CompletionActFormData) -> int:
    title = (
        f"Акт о сдаче-приемке выполненных работ № {form.document_number} от {form.document_date}"
    )
    extra_units = max(
        0,
        _completion_act_estimated_lines(title, chars_per_line=70) - 2,
    )
    for party in (form.performer, form.customer):
        extra_units += max(
            0,
            _completion_act_estimated_lines(
                _completion_act_party_summary_text(party), chars_per_line=72
            )
            - 2,
        )
    extra_units += max(
        0,
        _completion_act_estimated_lines(form.basis, chars_per_line=80) - 2,
    )
    fixed_height = (
        COMPLETION_ACT_LAYOUT.first_header_base_units
        + extra_units * COMPLETION_ACT_LAYOUT.row_extra_line_units
    )
    return max(0, COMPLETION_ACT_LAYOUT.regular_table_body_units - fixed_height)


def _completion_act_party_final_extra_units(party: CompletionActPartyData) -> int:
    estimates = (
        (party.legal_name, 38, 2),
        (party.inn, 18, 1),
        (party.kpp, 18, 1),
        (party.address, 44, 2),
        (party.settlement_account, 24, 1),
        (party.bank_name, 44, 2),
        (party.bik, 18, 1),
        (party.correspondent_account, 24, 1),
        (party.signer_position, 18, 2),
        (party.signer_name, 20, 2),
    )
    return sum(
        max(
            0,
            _completion_act_estimated_lines(value, chars_per_line=width) - baseline,
        )
        for value, width, baseline in estimates
    )


def _completion_act_final_page_capacity(form: CompletionActFormData) -> int:
    acceptance_extra = max(
        0,
        _completion_act_estimated_lines(form.acceptance_text, chars_per_line=90) - 2,
    )
    party_extra = max(
        _completion_act_party_final_extra_units(form.performer),
        _completion_act_party_final_extra_units(form.customer),
    )
    fixed_height = (
        COMPLETION_ACT_LAYOUT.final_block_base_units
        + (acceptance_extra + party_extra) * COMPLETION_ACT_LAYOUT.row_extra_line_units
    )
    return max(0, COMPLETION_ACT_LAYOUT.regular_table_body_units - fixed_height)


def _completion_act_final_page_extra_units(form: CompletionActFormData) -> int:
    acceptance_extra = max(
        0,
        _completion_act_estimated_lines(form.acceptance_text, chars_per_line=90) - 2,
    )
    party_extra = max(
        _completion_act_party_final_extra_units(form.performer),
        _completion_act_party_final_extra_units(form.customer),
    )
    return acceptance_extra + party_extra


def _completion_act_validate_final_layout(form: CompletionActFormData) -> None:
    acceptance_lines = len(form.acceptance_text.splitlines() or [""])
    acceptance_extra_units = max(
        0,
        _completion_act_estimated_lines(form.acceptance_text, chars_per_line=90) - 2,
    )
    closing_extra_units = _completion_act_final_page_extra_units(form)
    final_text_values = [form.acceptance_text]
    for party in (form.performer, form.customer):
        final_text_values.extend(
            (
                party.legal_name,
                party.address,
                party.bank_name,
                party.signer_position,
                party.signer_name,
            )
        )
    has_wide_glyph = any(
        unicodedata.east_asian_width(character) in {"W", "F"}
        for value in final_text_values
        for character in value
    )
    is_too_tall = (
        acceptance_lines > _COMPLETION_ACT_ACCEPTANCE_MAX_LINES
        or closing_extra_units > _COMPLETION_ACT_FINAL_PAGE_MAX_EXTRA_UNITS
        or (
            has_wide_glyph and closing_extra_units > _COMPLETION_ACT_FINAL_PAGE_COMBINED_EXTRA_UNITS
        )
        or (
            acceptance_extra_units > _COMPLETION_ACT_ACCEPTANCE_COMBINED_EXTRA_UNITS
            and closing_extra_units > _COMPLETION_ACT_FINAL_PAGE_COMBINED_EXTRA_UNITS
        )
    )
    if not is_too_tall:
        return
    raise PrintModuleError(
        "validation_error",
        "Текст и реквизиты не помещаются в финальный блок акта; сократите их.",
        details={
            "field": "completion_act.final_block",
            "max_layout_units": _COMPLETION_ACT_FINAL_PAGE_MAX_EXTRA_UNITS,
            "actual_layout_units": closing_extra_units,
            "max_acceptance_lines": _COMPLETION_ACT_ACCEPTANCE_MAX_LINES,
            "actual_acceptance_lines": acceptance_lines,
        },
    )


def _completion_act_page_chunks(
    form: CompletionActFormData, items: list[dict[str, Any]]
) -> list[list[dict[str, Any]]]:
    """Build logical pages that also remain single physical A4 pages.

    The final accounting/signature block consumes substantially more space than
    an ordinary continuation page. Text fields and row names are variable-height,
    so a fixed number-of-rows split is not sufficient.
    """

    if not items:
        return [[]]
    first_capacity = _completion_act_first_page_capacity(form)
    final_capacity = _completion_act_final_page_capacity(form)
    combined_capacity = max(
        0,
        COMPLETION_ACT_LAYOUT.regular_table_body_units
        - COMPLETION_ACT_LAYOUT.first_header_base_units
        - COMPLETION_ACT_LAYOUT.final_block_base_units
        - (
            max(0, _completion_act_first_page_capacity(CompletionActFormData()) - first_capacity)
            + max(0, _completion_act_final_page_capacity(CompletionActFormData()) - final_capacity)
        ),
    )
    heights = [_completion_act_item_page_weight(item) for item in items]
    total_height = sum(heights)
    if total_height <= combined_capacity:
        return [list(items)]

    index = 0
    remaining_height = total_height

    def take_page(capacity: int, *, force_progress: bool) -> list[dict[str, Any]]:
        nonlocal index, remaining_height
        start = index
        used = 0
        while index < len(items) and used + heights[index] <= capacity:
            used += heights[index]
            index += 1
        if force_progress and index == start and index < len(items):
            # Retain progress for one legacy row heavier than a page. The
            # aggregate page bound below still rejects an unprintable document.
            used = heights[index]
            index += 1
        remaining_height -= used
        return list(items[start:index])

    pages: list[list[dict[str, Any]]] = [take_page(first_capacity, force_progress=False)]
    while remaining_height > final_capacity:
        pages.append(take_page(COMPLETION_ACT_LAYOUT.regular_table_body_units, force_progress=True))
        if len(pages) >= _COMPLETION_ACT_MAX_PAGES:
            raise PrintModuleError(
                "validation_error",
                "Строки акта не помещаются в допустимое количество страниц; сократите текст.",
                details={
                    "field": "completion_act.items_layout",
                    "max_pages": _COMPLETION_ACT_MAX_PAGES,
                },
            )
    pages.append(list(items[index:]))
    if len(pages) > _COMPLETION_ACT_MAX_PAGES:
        raise PrintModuleError(
            "validation_error",
            "Строки акта не помещаются в допустимое количество страниц; сократите текст.",
            details={
                "field": "completion_act.items_layout",
                "max_pages": _COMPLETION_ACT_MAX_PAGES,
            },
        )
    return pages


def _normalize_template_content(value: Any) -> str:
    content = _normalize_multiline(value, limit=PRINT_TEMPLATE_CONTENT_MAX_CHARS + 1)
    if len(content) > PRINT_TEMPLATE_CONTENT_MAX_CHARS:
        raise PrintModuleError(
            "validation_error",
            "Шаблон слишком большой для сохранения или предпросмотра.",
            details={"max_size_chars": PRINT_TEMPLATE_CONTENT_MAX_CHARS},
        )
    return content


def _display(value: Any, *, fallback: str = "—", limit: int = 4000) -> str:
    text = _normalize_text(value, limit=limit)
    return text or fallback


def _date_display(value: Any, *, fallback: str = "—") -> str:
    text = _normalize_text(value, limit=64)
    if not text:
        return fallback
    parsed = parse_datetime(text)
    if parsed is None:
        return text
    return parsed.strftime("%d.%m.%Y %H:%M")


def _date_only_display(value: Any, *, fallback: str = "—") -> str:
    text = _normalize_text(value, limit=64)
    if not text:
        return fallback
    parsed = parse_datetime(text)
    if parsed is None:
        return text.split()[0] if text.split() else fallback
    return parsed.strftime("%d.%m.%Y")


def _date_long_ru_display(value: Any, *, fallback: str = "—") -> str:
    text = _normalize_text(value, limit=64)
    if not text:
        return fallback
    parsed = parse_datetime(text)
    if parsed is None:
        return text
    month = _RU_MONTHS_GENITIVE[parsed.month] if 1 <= parsed.month <= 12 else ""
    if not month:
        return parsed.strftime("%d.%m.%Y")
    return f"{parsed.day:02d} {month} {parsed.year} г."


def _inn_kpp_display(inn: Any, kpp: Any) -> str:
    inn_text = _normalize_text(inn, limit=32)
    kpp_text = _normalize_text(kpp, limit=32)
    if kpp_text.lower().replace("ё", "е") in {
        "не применяется для ип",
        "не применяется",
        "нет",
        "-",
        "—",
    }:
        kpp_text = ""
    if inn_text and kpp_text:
        return f"{inn_text} / {kpp_text}"
    return inn_text or kpp_text or "—"


def _individual_entrepreneur_display(legal_name: Any, *, fallback: str = "—") -> str:
    text = _normalize_text(legal_name, limit=180)
    if not text:
        return fallback
    normalized = text.lower().replace("ё", "е")
    if normalized.startswith("ип "):
        return f"Индивидуальный предприниматель {text[3:].strip()}".strip()
    if normalized.startswith("индивидуальный предприниматель"):
        return text
    return text


def _service_registration_display(ogrn: Any) -> str:
    text = _normalize_text(ogrn, limit=64)
    if not text:
        return "—"
    if text == "319246800097453":
        return "319246800097453, 05.08.2019"
    return text


def _service_signer_display(legal_name: Any, company_name: Any) -> str:
    text = _normalize_text(legal_name, limit=180)
    if "Гришкявичус" in text:
        return "Гришкявичус К.В."
    return _display(company_name, fallback=text or "—", limit=120)


def _service_person_name_display(legal_name: Any, company_name: Any) -> str:
    text = _normalize_text(legal_name, limit=180)
    if "Гришкявичус" in text:
        return "Гришкявичус Константин Владиславович"
    return _display(company_name, fallback=text or "—", limit=120)


def _regulated_seller_address_display(legal_name: Any, address: Any) -> str:
    legal_text = _normalize_text(legal_name, limit=180)
    address_text = _normalize_text(address, limit=300)
    if "Гришкявичус" in legal_text and (
        not address_text or "семафорная" in address_text.lower().replace("ё", "е")
    ):
        return "КРАЙ КРАСНОЯРСКИЙ, ГОРОД КРАСНОЯРСК"
    return _display(address_text, limit=300)


def _regulated_overrides(value: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    nested = value.get("regulated") if isinstance(value.get("regulated"), dict) else {}
    return {
        **nested,
        **{key: item for key, item in value.items() if key != "regulated"},
    }


def _regulated_override_text(
    overrides: dict[str, Any],
    *keys: str,
    fallback: Any = "—",
    limit: int = 4000,
) -> str:
    for key in keys:
        text = _normalize_text(overrides.get(key), limit=limit)
        if text:
            return text
    return _display(fallback, limit=limit)


def _regulated_optional_text(
    overrides: dict[str, Any],
    *keys: str,
    fallback: Any = "",
    limit: int = 4000,
) -> str:
    for key in keys:
        if key in overrides:
            return _normalize_text(overrides.get(key), limit=limit)
    return _normalize_text(fallback, limit=limit)


def _print_render_orientation(settings: PrintModuleSettings, document_ids: list[str]) -> str:
    if any(document_id in _REGULATED_LANDSCAPE_DOCUMENT_TYPES for document_id in document_ids):
        return "landscape"
    return settings.orientation


def _regulated_unit_payload(item: dict[str, Any]) -> dict[str, str]:
    section = _normalize_text(item.get("section"), limit=32)
    unit = _normalize_text(item.get("inventory_unit") or item.get("unit_display"), limit=24).lower()
    unit = unit.replace(".", "")
    if section == "works" and (not unit or unit in {"усл ед", "услед", "услуга", "услуги", "усл"}):
        return {"unit_code_display": "", "unit_display": "н/ч"}
    if unit in {"л", "литр", "литра", "литров"}:
        return {"unit_code_display": "112", "unit_display": "л"}
    if unit in {"кг", "килограмм", "килограмма", "килограммов"}:
        return {"unit_code_display": "166", "unit_display": "кг"}
    if unit in {"м", "метр", "метра", "метров"}:
        return {"unit_code_display": "006", "unit_display": "м"}
    display = unit or "шт"
    if display in {"шт", "штука", "штуки", "штук"}:
        display = "шт"
    return {"unit_code_display": "796", "unit_display": display}


def _regulated_line_item_dict(
    item: dict[str, Any],
    *,
    index: int,
    tax: dict[str, Any],
) -> dict[str, Any]:
    quantity = _parse_decimal(item.get("quantity"))
    price = _parse_decimal(item.get("price")) or Decimal("0")
    subtotal = _parse_decimal(item.get("total"))
    if subtotal is None:
        subtotal = _round_money((quantity or Decimal("0")) * price)
    gross_total = repair_order_cashless_gross_value(subtotal)
    rate = tax["rate"] if tax.get("has_vat") else Decimal("0")
    if tax.get("has_vat"):
        subtotal, vat = _split_tax_included_amount(gross_total, rate)
    else:
        subtotal = gross_total
        vat = Decimal("0")
    total_with_tax = gross_total
    price_value = (
        _round_money(subtotal / quantity) if quantity and quantity > Decimal("0") else subtotal
    )
    unit_payload = _regulated_unit_payload(item)
    return {
        **item,
        **unit_payload,
        "index": index + 1,
        "product_code_display": "—",
        "name": _display(item.get("name"), limit=260),
        "quantity_display": _display(item.get("quantity_display") or item.get("quantity")),
        "price": price_value,
        "price_display": _money_display(price_value),
        "subtotal": subtotal,
        "subtotal_display": _money_display(subtotal),
        "tax_rate_display": tax["rate_display"] if tax.get("has_vat") else "Без НДС",
        "vat": vat,
        "vat_display": _money_display(vat) if tax.get("has_vat") else "Без НДС",
        "total_with_tax": total_with_tax,
        "total_with_tax_display": _money_display(total_with_tax),
        "excise_display": "Без акциза",
        "line_code_display": "",
        "country_display": "",
        "country_code_display": "",
        "country_name_display": "",
        "customs_declaration_display": "",
    }


def _balance_regulated_line_totals(
    line_items: list[dict[str, Any]],
    *,
    target_total: Decimal,
    tax_rate: Decimal,
    target_vat: Decimal | None = None,
) -> list[dict[str, Any]]:
    if not line_items:
        return line_items
    target_total = _round_money(target_total)
    if target_vat is None:
        _, target_vat = _split_tax_included_amount(target_total, tax_rate)
    target_vat = Decimal("0.00") if tax_rate <= Decimal("0") else _round_money(target_vat)
    balanced_items = [dict(item) for item in line_items]
    current_total = _round_money(
        sum(
            (_parse_decimal(item.get("total_with_tax")) or Decimal("0") for item in balanced_items),
            Decimal("0"),
        )
    )
    remaining_total_adjustment = _round_money(target_total - current_total)
    for item in reversed(balanced_items):
        if remaining_total_adjustment == Decimal("0"):
            break
        current_item_total = _round_money(
            _parse_decimal(item.get("total_with_tax")) or Decimal("0")
        )
        adjustment = (
            remaining_total_adjustment
            if remaining_total_adjustment > Decimal("0")
            else max(remaining_total_adjustment, -current_item_total)
        )
        total_with_tax = _round_money(current_item_total + adjustment)
        subtotal, vat = _split_tax_included_amount(total_with_tax, tax_rate)
        item.update(
            {
                "subtotal": subtotal,
                "vat": vat,
                "total_with_tax": total_with_tax,
            }
        )
        remaining_total_adjustment = _round_money(remaining_total_adjustment - adjustment)
    if remaining_total_adjustment != Decimal("0"):
        raise PrintModuleError(
            "validation_error",
            "Не удалось сбалансировать итог документа без отрицательной строки.",
        )

    current_vat = _round_money(
        sum(
            (_parse_decimal(item.get("vat")) or Decimal("0") for item in balanced_items),
            Decimal("0"),
        )
    )
    remaining_vat_adjustment = _round_money(target_vat - current_vat)
    for item in reversed(balanced_items):
        if remaining_vat_adjustment == Decimal("0"):
            break
        subtotal = _round_money(_parse_decimal(item.get("subtotal")) or Decimal("0"))
        vat = _round_money(_parse_decimal(item.get("vat")) or Decimal("0"))
        adjustment = (
            min(remaining_vat_adjustment, subtotal)
            if remaining_vat_adjustment > Decimal("0")
            else max(remaining_vat_adjustment, -vat)
        )
        item["subtotal"] = _round_money(subtotal - adjustment)
        item["vat"] = _round_money(vat + adjustment)
        remaining_vat_adjustment = _round_money(remaining_vat_adjustment - adjustment)
    if remaining_vat_adjustment != Decimal("0"):
        raise PrintModuleError(
            "validation_error",
            "Не удалось сбалансировать НДС документа без отрицательной строки.",
        )

    for item in balanced_items:
        total_with_tax = _round_money(_parse_decimal(item.get("total_with_tax")) or Decimal("0"))
        subtotal = _round_money(_parse_decimal(item.get("subtotal")) or Decimal("0"))
        vat = _round_money(_parse_decimal(item.get("vat")) or Decimal("0"))
        quantity = _parse_decimal(item.get("quantity"))
        price = (
            _round_money(subtotal / quantity) if quantity and quantity > Decimal("0") else subtotal
        )
        item.update(
            {
                "price": price,
                "price_display": _money_display(price),
                "subtotal": subtotal,
                "subtotal_display": _money_display(subtotal),
                "vat": vat,
                "vat_display": _money_display(vat) if tax_rate > Decimal("0") else "Без НДС",
                "total_with_tax": total_with_tax,
                "total_with_tax_display": _money_display(total_with_tax),
            }
        )
    return balanced_items


def _regulated_document_context(
    *,
    order: RepairOrder,
    settings: PrintModuleSettings,
    client: ClientProfile | None,
    line_items: list[dict[str, Any]],
    document_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    overrides = _regulated_overrides(document_overrides)
    tax = _invoice_tax_payload(order)
    raw_rows = [
        _regulated_line_item_dict(item, index=index, tax=tax)
        for index, item in enumerate(line_items)
    ]
    base_subtotal = _round_money(
        sum(
            (_parse_decimal(item.get("total")) or Decimal("0") for item in line_items),
            Decimal("0"),
        )
    )
    target_total = repair_order_cashless_gross_value(base_subtotal)
    tax_rate = tax["rate"] if tax.get("has_vat") else Decimal("0")
    _, target_vat = _split_tax_included_amount(target_total, tax_rate)
    rows = _balance_regulated_line_totals(
        raw_rows,
        target_total=target_total,
        tax_rate=tax_rate,
        target_vat=target_vat,
    )
    subtotal = _round_money(
        sum((_parse_decimal(item.get("subtotal")) or Decimal("0") for item in rows), Decimal("0"))
    )
    vat = _round_money(
        sum((_parse_decimal(item.get("vat")) or Decimal("0") for item in rows), Decimal("0"))
    )
    total_with_tax = _round_money(
        sum(
            (_parse_decimal(item.get("total_with_tax")) or Decimal("0") for item in rows),
            Decimal("0"),
        )
    )
    service_profile = settings.service_profile
    seller_legal_name = _individual_entrepreneur_display(
        service_profile.legal_name,
        fallback=_display(service_profile.company_name),
    )
    seller_is_individual = "Индивидуальный предприниматель" in seller_legal_name
    seller_signer = _regulated_override_text(
        overrides,
        "seller_signer",
        fallback=_service_signer_display(service_profile.legal_name, service_profile.company_name),
        limit=120,
    )
    seller_full_signer = _regulated_override_text(
        overrides,
        "seller_full_signer",
        fallback=_service_person_name_display(
            service_profile.legal_name,
            service_profile.company_name,
        ),
        limit=160,
    )
    seller_position = _regulated_override_text(
        overrides,
        "seller_position",
        fallback="ИП" if seller_is_individual else "Руководитель",
        limit=120,
    )
    seller_leader_position = _regulated_optional_text(
        overrides,
        "seller_leader_position",
        fallback="" if seller_is_individual else seller_position,
        limit=160,
    )
    seller_leader_signer = _regulated_optional_text(
        overrides,
        "seller_leader_signer",
        fallback="" if seller_is_individual else seller_signer,
        limit=160,
    )
    client_name = ""
    client_inn = ""
    client_kpp = ""
    client_address = ""
    client_contact = ""
    client_position = ""
    if client is not None:
        client_name = _first_text(
            client.legal_name,
            client.display_name,
            client.short_name,
            client.name(),
            limit=180,
        )
        client_inn = client.inn
        client_kpp = client.kpp
        client_address = _first_text(client.legal_address, client.actual_address, limit=300)
        client_contact = client.contact_person
        client_position = client.contact_position
    buyer_name = _regulated_override_text(
        overrides,
        "buyer_name",
        "client_name",
        fallback=_first_text(client_name, order.client, limit=180) or "—",
        limit=180,
    )
    buyer_address = _regulated_override_text(
        overrides,
        "buyer_address",
        "client_address",
        fallback=client_address or "—",
        limit=300,
    )
    buyer_inn = _regulated_override_text(
        overrides,
        "buyer_inn",
        "inn",
        fallback=client_inn,
        limit=32,
    )
    buyer_kpp = _regulated_override_text(
        overrides,
        "buyer_kpp",
        "kpp",
        fallback=client_kpp,
        limit=32,
    )
    document_number = _display(order.number, fallback="—", limit=40)
    document_date_value = order.date or order.opened_at
    document_date = _date_only_display(document_date_value)
    document_date_long = _date_long_ru_display(document_date_value)
    linked_invoice = _regulated_optional_text(
        overrides,
        "linked_invoice",
        fallback="",
        limit=120,
    )
    shipment_document = _regulated_optional_text(
        overrides,
        "shipment_document",
        fallback="",
        limit=120,
    )
    upd_shipment_document = _regulated_optional_text(
        overrides,
        "upd_shipment_document",
        "shipment_document",
        fallback=f"Универсальный передаточный документ №{document_number} от {document_date}",
        limit=180,
    )
    basis = _regulated_override_text(
        overrides,
        "basis",
        fallback=f"Счет на оплату №{document_number} от {document_date}",
        limit=240,
    )
    transport_details = _regulated_optional_text(
        overrides,
        "transport_details",
        fallback="",
        limit=240,
    )
    buyer_position = _regulated_optional_text(
        overrides,
        "buyer_position",
        fallback=client_position,
        limit=120,
    )
    buyer_signer = _regulated_optional_text(
        overrides,
        "buyer_signer",
        fallback=client_contact,
        limit=120,
    )
    shipper = _regulated_override_text(
        overrides,
        "shipper",
        fallback="он же",
        limit=360,
    )
    consignee = _regulated_override_text(
        overrides,
        "consignee",
        fallback=f"{buyer_name}, {buyer_address}",
        limit=360,
    )
    payment_document = _regulated_override_text(
        overrides,
        "payment_document",
        fallback=f"№ {document_number} от {document_date}",
        limit=160,
    )
    upd_payment_document = _regulated_optional_text(
        overrides,
        "upd_payment_document",
        fallback="",
        limit=160,
    )
    seller_address = _regulated_override_text(
        overrides,
        "seller_address",
        fallback=_regulated_seller_address_display(
            service_profile.legal_name,
            service_profile.address,
        ),
        limit=300,
    )
    seller_economic_subject = _display(
        f"{seller_legal_name}, ИНН {service_profile.inn}"
        if service_profile.inn
        else seller_legal_name,
        limit=260,
    )
    buyer_economic_subject = _display(
        ", ".join(
            part
            for part in (
                buyer_name,
                f"ИНН {buyer_inn}" if buyer_inn else "",
                f"КПП {buyer_kpp}" if buyer_kpp else "",
            )
            if part
        ),
        limit=260,
    )
    return {
        "document_number_display": document_number,
        "document_date_display": document_date,
        "document_date_long_display": document_date_long,
        "correction_display": "—",
        "correction_date_display": "—",
        "linked_invoice_display": linked_invoice,
        "seller_name_display": seller_legal_name,
        "seller_address_display": seller_address,
        "seller_inn_kpp_display": _inn_kpp_display(service_profile.inn, service_profile.kpp),
        "seller_registration_display": _service_registration_display(service_profile.ogrn),
        "seller_position_display": seller_position,
        "seller_signer_display": seller_signer,
        "seller_full_signer_display": seller_full_signer,
        "seller_leader_position_display": seller_leader_position,
        "seller_leader_signer_display": seller_leader_signer,
        "seller_economic_subject_display": seller_economic_subject,
        "shipper_display": shipper,
        "consignee_display": consignee,
        "payment_document_display": payment_document,
        "upd_payment_document_display": upd_payment_document,
        "shipment_document_display": shipment_document,
        "upd_shipment_document_display": upd_shipment_document,
        "buyer_name_display": buyer_name,
        "buyer_address_display": buyer_address,
        "buyer_inn_kpp_display": _inn_kpp_display(buyer_inn, buyer_kpp),
        "buyer_position_display": buyer_position,
        "buyer_signer_display": buyer_signer,
        "buyer_economic_subject_display": buyer_economic_subject,
        "currency_display": "Российский рубль, 643",
        "state_contract_display": "",
        "upd_status_code_display": "1",
        "upd_status_display": "1 - счет-фактура и передаточный документ (акт)",
        "upd_status_secondary_display": "2 - передаточный документ (акт)",
        "document_pages_display": "2",
        "basis_display": basis,
        "transport_details_display": transport_details,
        "rows": rows,
        "tax_label": tax["label"],
        "tax_rate_display": tax["rate_display"] if tax.get("has_vat") else "Без НДС",
        "subtotal": subtotal,
        "subtotal_display": _money_display(subtotal),
        "vat": vat,
        "vat_display": _money_display(vat) if tax.get("has_vat") else "Без НДС",
        "total_with_tax": total_with_tax,
        "total_with_tax_display": _money_display(total_with_tax),
        "total_words_display": _money_words_display(total_with_tax),
        "has_vat": tax.get("has_vat") and vat != Decimal("0"),
    }


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _safe_json_read(
    path: Path,
    *,
    default: Any,
    max_bytes: int | None = None,
) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(
            _read_print_json_text(path, max_bytes=max_bytes),
            parse_constant=_reject_json_constant,
        )
    except (ValueError, OSError, UnicodeDecodeError, RecursionError):
        return deepcopy(default)


def _read_print_json_text(path: Path, *, max_bytes: int | None = None) -> str:
    limit = PRINT_JSON_FILE_MAX_BYTES if max_bytes is None else max_bytes
    if path.stat().st_size > limit:
        raise ValueError("print json file is too large")
    with path.open("rb") as handle:
        payload = handle.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("print json file is too large")
    return payload.decode("utf-8")


def _json_safe_value(value: Any, *, depth: int = _JSON_SAFE_MAX_DEPTH) -> Any:
    if depth <= 0:
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, Decimal):
        return str(value) if value.is_finite() else "0"
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item, depth=depth - 1)
            for key, item in value.items()
            if key is not None
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    return str(value)


def _safe_json_write(path: Path, payload: Any, *, max_bytes: int | None = None) -> None:
    limit = PRINT_JSON_FILE_MAX_BYTES if max_bytes is None else max_bytes
    text = json.dumps(_json_safe_value(payload), ensure_ascii=False, indent=2, allow_nan=False)
    if len(text.encode("utf-8")) > limit:
        raise PrintModuleError(
            "validation_error",
            "Данные печатного модуля слишком большие для сохранения.",
            details={"max_size_bytes": limit},
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        if os.name != "nt":
            tmp_path.chmod(0o600)
        tmp_path.replace(path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _nested_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _nested_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _nested_sparse_diff(current: Any, baseline: Any) -> Any:
    if isinstance(current, dict) and isinstance(baseline, dict):
        result: dict[str, Any] = {}
        for key, value in current.items():
            if key not in baseline:
                continue
            difference = _nested_sparse_diff(value, baseline[key])
            if difference is not None:
                result[key] = difference
        return result or None
    if isinstance(current, list) and isinstance(baseline, list):
        return deepcopy(current) if current != baseline else None
    return deepcopy(current) if current != baseline else None


def _request_payload_fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            _json_safe_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _fsync_directory(path: Path) -> None:
    """Persist a directory entry update where directory fsync is supported."""

    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@lru_cache(maxsize=1)
def _brand_logo_data_uri() -> str:
    try:
        data = read_bytes_limited(
            _BRAND_LOGO_PATH,
            max_bytes=PRINT_BRAND_LOGO_MAX_BYTES,
            label="brand logo",
        )
    except (OSError, ValueError):
        return ""
    if not data:
        return ""
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _repair_row_dict(row: RepairOrderRow, *, section: str, index: int) -> dict[str, Any]:
    return {
        "index": index + 1,
        "section": section,
        "section_label": "Работы" if section == "works" else "Материалы",
        "name": row.name or "—",
        "catalog_number": row.catalog_number,
        "inventory_unit": row.inventory_unit,
        "quantity": row.quantity,
        "unit_display": row.inventory_unit or ("усл. ед." if section == "works" else "шт."),
        "price": row.price,
        "total": row.total or row.computed_total(),
        "quantity_display": row.quantity or "—",
        "price_display": _money_display(row.price),
        "total_display": _money_display(row.total or row.computed_total()),
    }


def _invoice_line_item_dict(item: dict[str, Any]) -> dict[str, Any]:
    invoice_item = dict(item)
    price = repair_order_cashless_gross_value(_parse_decimal(item.get("price")) or Decimal("0"))
    total = repair_order_cashless_gross_value(_parse_decimal(item.get("total")) or Decimal("0"))
    invoice_item["price"] = price
    invoice_item["total"] = total
    invoice_item["price_display"] = _money_display(price)
    invoice_item["total_display"] = _money_display(total)
    return invoice_item


def _invoice_print_prepayment_value(payment_summary: dict[str, Decimal]) -> Decimal:
    cash_like_prepayment = _round_money(
        payment_summary["base_paid_cash_only"] + payment_summary["base_paid_card"]
    )
    cashless_prepayment = _round_money(payment_summary["base_paid_noncash"])
    return _round_money(
        repair_order_cashless_gross_value(cash_like_prepayment) + cashless_prepayment
    )


def _balance_invoice_line_totals(
    line_items: list[dict[str, Any]], target_total: Decimal
) -> list[dict[str, Any]]:
    if not line_items:
        return line_items
    current_total = _round_money(
        sum(
            (_parse_decimal(item.get("total")) or Decimal("0") for item in line_items),
            Decimal("0"),
        )
    )
    adjustment = _round_money(target_total - current_total)
    if adjustment == Decimal("0"):
        return line_items
    balanced_items = list(line_items)
    last_index = len(balanced_items) - 1
    last_item = dict(balanced_items[last_index])
    total = _round_money((_parse_decimal(last_item.get("total")) or Decimal("0")) + adjustment)
    last_item["total"] = total
    last_item["total_display"] = _money_display(total)
    balanced_items[last_index] = last_item
    return balanced_items


def _print_safe_repair_order_dict(order: RepairOrder) -> dict[str, Any]:
    payload = order.to_dict()
    public_row_fields = {"name", "quantity", "price", "total"}
    for field_name in ("works", "materials"):
        rows = payload.get(field_name)
        if not isinstance(rows, list):
            continue
        payload[field_name] = [
            {key: value for key, value in row.items() if key in public_row_fields}
            for row in rows
            if isinstance(row, dict)
        ]
    return payload


def _client_invoice_context(
    client: ClientProfile | None,
    *,
    order_client: str,
    order_phone: str,
) -> dict[str, Any]:
    if client is None:
        return {
            "has_client": False,
            "has_requisites": False,
            "client_type": "",
            "type_label": "",
            "name_display": _display(order_client),
            "invoice_name_display": _display(order_client),
            "phone_display": _display(order_phone),
            "email_display": "—",
            "legal_name_display": "—",
            "short_name_display": "—",
            "inn": "—",
            "kpp": "—",
            "ogrn": "—",
            "checking_account": "—",
            "bank_name": "—",
            "bik": "—",
            "correspondent_account": "—",
            "legal_address": "—",
            "actual_address": "—",
            "contact_person": "—",
            "contact_position": "—",
        }

    requisites_values = (
        client.legal_name,
        client.short_name,
        client.inn,
        client.kpp,
        client.ogrn,
        client.checking_account,
        client.bank_name,
        client.bik,
        client.correspondent_account,
        client.legal_address,
        client.actual_address,
        client.contact_person,
        client.contact_position,
    )
    has_requisites = client.client_type in {"ip", "ooo", "company"} and any(
        _display(value, fallback="").strip() for value in requisites_values
    )
    invoice_name = client.legal_name or client.short_name or client.name()
    return {
        "has_client": True,
        "has_requisites": has_requisites,
        "client_type": client.client_type,
        "type_label": client.type_label(),
        "name_display": _display(client.name(), fallback=_display(order_client)),
        "invoice_name_display": _display(invoice_name, fallback=_display(order_client)),
        "phone_display": _display(client.phone, fallback=_display(order_phone)),
        "email_display": _display(client.email, fallback="—"),
        "legal_name_display": _display(client.legal_name, fallback=_display(invoice_name)),
        "short_name_display": _display(client.short_name, fallback=_display(invoice_name)),
        "inn": _display(client.inn),
        "kpp": _display(client.kpp),
        "ogrn": _display(client.ogrn),
        "checking_account": _display(client.checking_account),
        "bank_name": _display(client.bank_name),
        "bik": _display(client.bik),
        "correspondent_account": _display(client.correspondent_account),
        "legal_address": _display(client.legal_address),
        "actual_address": _display(client.actual_address),
        "contact_person": _display(client.contact_person),
        "contact_position": _display(client.contact_position),
    }


def _repair_order_warranty_terms_html() -> str:
    return (
        """
<p class="doc-terms__lead">Ниже приведены гарантийные и важные условия оформления заказ-наряда. Они действуют вместе с общими правилами приема и выдачи автомобиля.</p>
<ol class="doc-terms__list">
  <li><strong>Гарантия сервиса:</strong> автосервис несет гарантию только за качество выполненных работ и новые оригинальные запасные части, установленные сервисом. Для АКПП срок гарантийных обязательств может составлять до 6 месяцев и распространяется только на согласованные работы и новые оригинальные детали.</li>
  <li><strong>АКПП:</strong> при ремонте автоматической коробки передач восстанавливается только ее механическая часть. Коробка собирается на родном блоке управления (гидроблоке), и качество результата зависит в том числе от состояния этого блока. Гарантия на отремонтированную АКПП действует до 6 месяцев и распространяется только на работы и согласованные новые оригинальные детали. После первых 1000 км рекомендуется контроль уровня и состояния масла, а после 20-30 тыс. км - замена масла и масляного фильтра. Самостоятельное вскрытие, неправильная установка или нарушение эксплуатации аннулируют гарантию; если после ремонта блок управления КПП неисправен, расходы на его ремонт или замену несет заказчик.</li>
  <li><strong>ДВС, КПП и агрегаты:</strong> на Б/У и контрактные детали и агрегаты, включая ДВС, КПП, рулевую рейку и тому подобные узлы, гарантия не предоставляется. Ремонт турбокомпрессоров, топливных форсунок, ГБЦ и других восстановленных узлов также не является гарантийным и выполняется под ответственность заказчика.</li>
  <li><strong>Возврат деталей:</strong> снятые детали передаются заказчику только по требованию, заявленному до начала ремонта. Если требование не заявлено, детали утилизируются в процессе ремонта.</li>
  <li><strong>Автоэлектрика, диагностика и сроки:</strong> гарантия на автоэлектрику и электропроводку составляет 10 (десять) календарных дней. Результаты диагностики и поиска неисправности не являются абсолютными, а промежуточные работы оплачиваются клиентом даже если не дали окончательного результата. Согласованные сроки являются ориентировочными и могут меняться в зависимости от поставки запчастей, объема работ и выявленных неисправностей. Нахождение клиента в ремонтной зоне допускается только в присутствии мастера.</li>
  <li><strong>Хранение и выдача:</strong> после уведомления о готовности первые 2 дня хранения бесплатные, далее стоимость хранения составляет 150 рублей в сутки. Автомобиль выдается только после полной оплаты выполненных работ и использованных материалов.</li>
  <li><strong>Фотофиксация и сопутствующие работы:</strong> претензии по повреждениям кузова принимаются только если фотофиксация состояния автомобиля проведена при сдаче машины совместно с представителем сервиса. Работы и сопутствующие действия стоимостью до 5000 рублей могут выполняться без отдельного согласования, если они необходимы для продолжения ремонта.</li>
  <li><strong>Нюансы ремонта и drive-test:</strong> в рамках согласованной суммы дополнительные нюансы ремонта выполняются на усмотрение мастера и в интересах клиента. Оставляя автомобиль в ремонт, клиент соглашается на проверочный выезд по дорогам общего пользования для диагностики, адаптации и контроля результата.</li>
</ol>
        """
    ).strip()


def _repair_order_terms_summary_html() -> str:
    return (
        """
<p class="doc-terms__lead">Краткие условия оформления заказ-наряда.</p>
<ol class="doc-terms__list doc-terms__list--compact">
  <li><strong>Гарантия:</strong> 30 дней на работы и новые оригинальные запасные части.</li>
  <li><strong>АКПП:</strong> до 6 месяцев; ремонтируется механическая часть, после 1000 км рекомендован контроль масла.</li>
  <li><strong>Исключения:</strong> запчасти клиента, Б/У, контрактные и неоригинальные детали гарантией не покрываются.</li>
  <li><strong>Хранение:</strong> 2 дня бесплатно, далее 150 рублей в сутки.</li>
  <li><strong>Фотофиксация:</strong> претензии по кузову принимаются при фотофиксации при сдаче.</li>
  <li><strong>Оплата:</strong> выдача автомобиля после полной оплаты.</li>
</ol>
        """
    ).strip()


def _vehicle_acceptance_terms_html() -> str:
    return (
        """
<ol class="doc-terms__list">
  <li><strong>Ценные вещи:</strong> за оставленные в автомобиле ценные вещи и предметы, в том числе деньги, Auto Stop ответственности не несет.</li>
  <li><strong>Хранение:</strong> срок бесплатного нахождения автомобиля после выполнения работ и уведомления клиента о готовности составляет 2 дня. После этого стоимость хранения автомобиля составляет 150 рублей в сутки.</li>
  <li><strong>Выдача автомобиля:</strong> автомобиль выдается клиенту только после полной оплаты произведенных работ и использованных материалов.</li>
  <li><strong>Фотофиксация:</strong> претензии по повреждениям кузова принимаются только при проведении фотофиксации состояния автомобиля при сдаче машины совместно с представителем сервиса. Если фотофиксация не проводилась, претензии по повреждениям после выезда автомобиля из сервиса не принимаются.</li>
  <li><strong>Доставка деталей:</strong> доставка б/у запчастей, деталей с отдаленных складов, авторазборок и транспортировка деталей клиента выполняются за отдельную плату.</li>
  <li><strong>Дополнительные работы:</strong> сопутствующие работы стоимостью до 5000 рублей могут выполняться без отдельного согласования с клиентом, если они необходимы для продолжения ремонта.</li>
  <li><strong>Диагностика:</strong> результаты диагностики и поиска неисправности не являются абсолютными. Сервис предлагает план ремонта, а промежуточные действия оплачиваются клиентом даже если они не дали окончательного результата.</li>
  <li><strong>Сроки:</strong> согласованные сроки выполнения работ являются ориентировочными и могут меняться в зависимости от поставки запчастей, объема работ и выявленных неисправностей.</li>
  <li><strong>Ремонтная зона:</strong> нахождение клиента в ремонтной зоне допускается только в присутствии мастера и является эпизодическим.</li>
  <li><strong>Замененные детали:</strong> снятые детали возвращаются клиенту только по требованию, заявленному до начала ремонта. Если требование не заявлено, детали утилизируются в процессе ремонта.</li>
  <li><strong>Согласование:</strong> в рамках оговоренной суммы дополнительные нюансы ремонта выполняются на усмотрение мастера и в интересах клиента.</li>
  <li><strong>Драйв-тест:</strong> оставляя автомобиль в ремонт, клиент соглашается на проверочный выезд по дорогам общего пользования для диагностики, адаптации и контроля результата.</li>
</ol>
        """
    ).strip()


def _parts_sale_terms_html() -> str:
    return (
        """
<ol class="doc-terms__list">
  <li>Документ оформляет продажу запасных частей и материалов без привязки к конкретному автомобилю, если автомобиль в заказе не указан.</li>
  <li>Покупатель подтверждает получение указанных позиций, комплектность и внешний вид товара проверены при получении.</li>
  <li>Возврат и обмен товара производится в порядке, предусмотренном законодательством РФ, при сохранении товарного вида и документов на покупку.</li>
  <li>Гарантийные обязательства производителя или поставщика действуют при соблюдении правил установки и эксплуатации детали.</li>
</ol>
        """
    ).strip()


class PrintModuleService:
    """Storage-backed printing module for repair-order documents."""

    def __init__(
        self,
        base_dir: Path,
        *,
        change_feed_store: ChangeFeedStore | None = None,
        logger: Any | None = None,
    ) -> None:
        self._root_dir = Path(base_dir) / "printing"
        self._root_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self._root_dir.chmod(0o700)
        self._settings_path = self._root_dir / _SETTINGS_FILE_NAME
        self._templates_path = self._root_dir / _TEMPLATES_FILE_NAME
        self._inspection_sheet_forms_path = self._root_dir / _INSPECTION_SHEET_FORMS_FILE_NAME
        # Keep the legacy path for one-time migration and rollback-compatible
        # backups. New runtime records live in the sibling private directory.
        self._completion_act_forms_path = self._root_dir / _COMPLETION_ACT_FORMS_FILE_NAME
        self._completion_act_forms_dir = self._root_dir / _COMPLETION_ACT_FORMS_DIR_NAME
        self._completion_act_lock = threading.RLock()
        self._completion_act_feed_pending: set[str] = set()
        self._completion_act_process_lock = ProcessFileLock(
            self._completion_act_forms_path.with_suffix(".lock")
        )
        self._builtin_documents = {item.id: item for item in BUILTIN_PRINT_DOCUMENTS}
        self._builtin_templates = {item.id: item for item in builtin_template_records()}
        self._change_feed_store = change_feed_store
        self._logger = logger
        self._migrate_legacy_completion_act_forms()
        self._sync_change_feed(initialize=True)

    def manual_document_profile(
        self,
        payload: dict[str, Any] | None = None,
        *,
        request_text: str = "",
    ) -> ManualDocumentProfile:
        raw_payload = payload if isinstance(payload, dict) else {}
        parsed_payload = _parse_manual_document_text(request_text)
        merged_payload = _merge_manual_document_payload(parsed_payload, raw_payload)
        client_payload_raw = merged_payload.get("client") or merged_payload.get("counterparty")
        client_payload = client_payload_raw if isinstance(client_payload_raw, dict) else {}
        client_name = _first_text(
            merged_payload.get("client_name"),
            client_payload.get("display_name"),
            client_payload.get("legal_name"),
            client_payload.get("short_name"),
            client_payload_raw if isinstance(client_payload_raw, str) else "",
            limit=160,
        )
        client_phone = _first_text(
            merged_payload.get("client_phone"),
            client_payload.get("phone"),
            limit=80,
        )
        vehicle_payload = _manual_vehicle_payload(
            merged_payload.get("vehicle") or merged_payload.get("car") or {}
        )
        number = _first_text(
            merged_payload.get("document_number"),
            merged_payload.get("number"),
            merged_payload.get("repair_order_number"),
            limit=40,
        )
        document_date = _first_text(
            merged_payload.get("document_date"),
            merged_payload.get("date"),
            merged_payload.get("opened_at"),
            limit=32,
        )
        closed_at = _first_text(merged_payload.get("closed_at"), limit=32)
        works = _manual_table_rows(merged_payload.get("works"))
        materials = _manual_table_rows(merged_payload.get("materials"))
        repair_order_payload = {
            "number": number,
            "date": document_date,
            "opened_at": _first_text(merged_payload.get("opened_at"), document_date, limit=32),
            "closed_at": closed_at,
            "client": client_name,
            "phone": client_phone,
            "vehicle": vehicle_payload["name"],
            "license_plate": vehicle_payload["license_plate"],
            "vin": vehicle_payload["vin"],
            "mileage": vehicle_payload["mileage"],
            "payment_method": _first_text(merged_payload.get("payment_method"), limit=32),
            "tax_label": _first_text(merged_payload.get("tax_label"), limit=48),
            "prepayment": _first_text(merged_payload.get("prepayment"), limit=40),
            "payments": merged_payload.get("payments", []),
            "reason": _first_multiline(
                merged_payload.get("reason"),
                merged_payload.get("complaint"),
                limit=4000,
            ),
            "comment": _first_multiline(
                merged_payload.get("comment"),
                merged_payload.get("client_comment"),
                request_text,
                limit=4000,
            ),
            "note": _first_multiline(
                merged_payload.get("note"),
                merged_payload.get("master_comment"),
                limit=4000,
            ),
            "works": works,
            "materials": materials,
        }
        now = utc_now_iso()
        title = _first_text(
            merged_payload.get("title"),
            f"Документ без карточки {number}".strip(),
            "Документ без карточки",
            limit=120,
        )
        card = Card.from_dict(
            {
                "id": "manual-document",
                "vehicle": vehicle_payload["name"],
                "title": title,
                "description": _first_multiline(
                    request_text, merged_payload.get("comment"), limit=20000
                ),
                "column": "inbox",
                "archived": False,
                "created_at": now,
                "updated_at": now,
                "deadline_timestamp": now,
                "repair_order": repair_order_payload,
            }
        )
        client: ClientProfile | None = None
        if client_name or client_payload:
            client_payload = {
                **client_payload,
                "id": "manual-client",
                "display_name": _first_text(client_payload.get("display_name"), client_name),
                "phone": _first_text(client_payload.get("phone"), client_phone),
            }
            if not client_payload.get("client_type"):
                client_payload["client_type"] = (
                    "person"
                    if not any(
                        _first_text(client_payload.get(field))
                        for field in (
                            "legal_name",
                            "short_name",
                            "inn",
                            "kpp",
                            "ogrn",
                            "checking_account",
                            "bank_name",
                            "bik",
                            "correspondent_account",
                            "legal_address",
                            "actual_address",
                        )
                    )
                    else "company"
                )
            client = ClientProfile.from_dict(client_payload)
        return ManualDocumentProfile(
            card=card,
            client=client,
            request_text=_normalize_multiline(request_text, limit=20_000),
        )

    def workspace(self, card: Card, *, repair_order: RepairOrder | None = None) -> dict[str, Any]:
        settings = self._read_settings()
        template_map = self._templates_by_document_type(settings=settings)
        printers = list_printers(default_name=settings.default_printer)
        document_without_card = card.id == "manual-document"
        return {
            "card_id": card.id,
            "heading": card.heading(),
            "documents": [
                self._document_workspace_payload(
                    document, settings=settings, template_map=template_map
                )
                for document in BUILTIN_PRINT_DOCUMENTS
            ],
            "templates": {
                document_type: [
                    record.to_dict(
                        is_default=(settings.default_template_ids.get(document_type) == record.id)
                    )
                    for record in records
                ]
                for document_type, records in template_map.items()
            },
            "printers": printers,
            "settings": settings.to_dict(),
            "meta": {
                "default_document_id": "repair_order",
                "supported_document_types": list(SUPPORTED_PRINT_DOCUMENT_TYPES),
                "has_printers": bool(printers),
                "has_repair_order_data": not (repair_order or card.repair_order).is_empty(),
                "document_without_card": document_without_card,
            },
        }

    def preview_documents(
        self,
        card: Card,
        *,
        repair_order: RepairOrder | None = None,
        client: ClientProfile | None = None,
        selected_document_ids: list[str] | None = None,
        active_document_id: str | None = None,
        selected_template_ids: dict[str, str] | None = None,
        template_overrides: dict[str, str] | None = None,
        document_overrides: dict[str, Any] | None = None,
        print_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        order = repair_order or card.repair_order
        settings = self._merged_settings(print_settings)
        selected_ids = self._normalized_document_ids(selected_document_ids)
        selected_templates = self._normalized_template_selection_map(selected_template_ids)
        normalized_overrides = self._normalized_template_override_map(template_overrides)
        resolved_active = self._resolved_active_document_id(active_document_id, selected_ids)
        documents_payload: list[dict[str, Any]] = []
        for document_id in selected_ids:
            document = self._document_definition(document_id)
            template = self._resolve_template(
                document_type=document_id,
                template_id=selected_templates.get(document_id, ""),
                settings=settings,
            )
            documents_payload.append(
                self._preview_document_payload(
                    card,
                    order,
                    document,
                    template,
                    client=client,
                    settings=settings,
                    template_overrides=normalized_overrides,
                    document_overrides=document_overrides,
                )
            )
        return {
            "card_id": card.id,
            "heading": card.heading(),
            "documents": documents_payload,
            "active_document_id": resolved_active,
            "meta": {
                "selected_count": len(selected_ids),
                "page_count": sum(document["page_count"] for document in documents_payload),
            },
        }

    def export_documents_pdf(
        self,
        card: Card,
        *,
        repair_order: RepairOrder | None = None,
        client: ClientProfile | None = None,
        selected_document_ids: list[str] | None = None,
        selected_template_ids: dict[str, str] | None = None,
        template_overrides: dict[str, str] | None = None,
        document_overrides: dict[str, Any] | None = None,
        print_settings: dict[str, Any] | None = None,
    ) -> tuple[bytes, str, dict[str, Any]]:
        order = repair_order or card.repair_order
        settings = self._merged_settings(print_settings)
        selected_ids = self._normalized_document_ids(selected_document_ids)
        selected_templates = self._normalized_template_selection_map(selected_template_ids)
        normalized_overrides = self._normalized_template_override_map(template_overrides)
        document_payloads = [
            self._rendered_document_payload(
                card,
                order,
                self._document_definition(document_id),
                self._resolve_template(
                    document_type=document_id,
                    template_id=selected_templates.get(document_id, ""),
                    settings=settings,
                ),
                client=client,
                settings=settings,
                template_overrides=normalized_overrides,
                document_overrides=document_overrides,
            )
            for document_id in selected_ids
        ]
        combined_html = self._combined_document_html(document_payloads)
        render_orientation = _print_render_orientation(settings, selected_ids)
        try:
            pdf_bytes = render_html_to_pdf_bytes(
                combined_html,
                paper_size=settings.paper_size,
                orientation=render_orientation,
                title=f"AutoStop CRM {card.heading()}",
                allow_plain_text_fallback=False,
            )
        except PdfRenderError as exc:
            raise PrintModuleError("pdf_error", str(exc), status_code=500) from exc
        file_name = self._build_export_file_name(
            card,
            selected_ids,
            document_payloads=document_payloads,
        )
        return (
            pdf_bytes,
            file_name,
            {
                "documents": [
                    {
                        "id": payload["document"].id,
                        "label": payload["document"].label,
                        "template_id": payload["template"].id,
                        "template_name": payload["template"].name,
                    }
                    for payload in document_payloads
                ],
                "paper_size": settings.paper_size,
                "orientation": render_orientation,
            },
        )

    def print_documents(
        self,
        card: Card,
        *,
        repair_order: RepairOrder | None = None,
        client: ClientProfile | None = None,
        selected_document_ids: list[str] | None = None,
        selected_template_ids: dict[str, str] | None = None,
        template_overrides: dict[str, str] | None = None,
        document_overrides: dict[str, Any] | None = None,
        print_settings: dict[str, Any] | None = None,
        printer_name: str = "",
    ) -> dict[str, Any]:
        order = repair_order or card.repair_order
        settings = self._merged_settings(print_settings)
        requested_printer = _normalize_text(printer_name or settings.default_printer, limit=120)
        if not requested_printer:
            raise PrintModuleError(
                "validation_error",
                "Не выбран принтер. Сначала выберите принтер или экспортируйте PDF.",
            )
        selected_ids = self._normalized_document_ids(selected_document_ids)
        selected_templates = self._normalized_template_selection_map(selected_template_ids)
        normalized_overrides = self._normalized_template_override_map(template_overrides)
        document_payloads = [
            self._rendered_document_payload(
                card,
                order,
                self._document_definition(document_id),
                self._resolve_template(
                    document_type=document_id,
                    template_id=selected_templates.get(document_id, ""),
                    settings=settings,
                ),
                client=client,
                settings=settings,
                template_overrides=normalized_overrides,
                document_overrides=document_overrides,
            )
            for document_id in selected_ids
        ]
        combined_html = self._combined_document_html(document_payloads)
        render_orientation = _print_render_orientation(settings, selected_ids)
        try:
            print_html(
                combined_html,
                printer_name=requested_printer,
                copies=settings.copies,
                paper_size=settings.paper_size,
                orientation=render_orientation,
                title=f"AutoStop CRM {card.heading()}",
            )
        except PrinterBackendError as exc:
            raise PrintModuleError("printer_unavailable", str(exc), status_code=503) from exc
        return {
            "printer_name": requested_printer,
            "copies": settings.copies,
            "documents": [payload["document"].to_dict() for payload in document_payloads],
        }

    def save_template(
        self,
        *,
        document_type: str,
        name: str,
        content: str,
        template_id: str = "",
    ) -> dict[str, Any]:
        normalized_document_type = _normalize_document_type(document_type)
        if normalized_document_type == "completion_act":
            raise PrintModuleError(
                "completion_act_template_locked",
                "Для акта используется только встроенный стандартный шаблон.",
                status_code=409,
            )
        normalized_name = _normalize_text(name, limit=120)
        normalized_content = _normalize_template_content(content)
        if not normalized_name:
            raise PrintModuleError("validation_error", "Укажите название шаблона.")
        if not normalized_content:
            raise PrintModuleError("validation_error", "Шаблон не может быть пустым.")
        templates = self._read_custom_templates()
        existing = next((item for item in templates if item.id == template_id), None)
        now = utc_now_iso()
        if existing is not None:
            existing.name = normalized_name
            existing.content = normalized_content
            existing.updated_at = now
            record = existing
        else:
            record = PrintTemplateRecord(
                id=f"custom:{normalized_document_type}:{uuid.uuid4().hex}",
                document_type=normalized_document_type,
                name=normalized_name,
                content=normalized_content,
                created_at=now,
                updated_at=now,
                source="custom",
            )
            templates.append(record)
        self._write_custom_templates(templates)
        settings = self._read_settings()
        return {
            "template": record.to_dict(
                is_default=(
                    settings.default_template_ids.get(normalized_document_type) == record.id
                )
            ),
            "templates": self._template_payloads_for_document_type(
                normalized_document_type, settings=settings
            ),
        }

    def duplicate_template(self, *, template_id: str, name: str = "") -> dict[str, Any]:
        source = self._find_template(template_id)
        if source.document_type == "completion_act":
            raise PrintModuleError(
                "completion_act_template_locked",
                "Стандартный шаблон акта нельзя дублировать.",
                status_code=409,
            )
        now = utc_now_iso()
        duplicate = PrintTemplateRecord(
            id=f"custom:{source.document_type}:{uuid.uuid4().hex}",
            document_type=source.document_type,
            name=_normalize_text(name, limit=120) or f"{source.name} (копия)",
            content=source.content,
            created_at=now,
            updated_at=now,
            source="custom",
        )
        templates = self._read_custom_templates()
        templates.append(duplicate)
        self._write_custom_templates(templates)
        settings = self._read_settings()
        return {
            "template": duplicate.to_dict(is_default=False),
            "templates": self._template_payloads_for_document_type(
                source.document_type, settings=settings
            ),
        }

    def delete_template(self, *, template_id: str) -> dict[str, Any]:
        record = self._find_template(template_id)
        if record.is_builtin:
            raise PrintModuleError(
                "forbidden", "Встроенный шаблон нельзя удалить.", status_code=403
            )
        templates = [item for item in self._read_custom_templates() if item.id != template_id]
        self._write_custom_templates(templates)
        settings = self._read_settings()
        if settings.default_template_ids.get(record.document_type) == template_id:
            settings.default_template_ids.pop(record.document_type, None)
            self._write_settings(settings)
        return {
            "deleted": True,
            "document_type": record.document_type,
            "templates": self._template_payloads_for_document_type(
                record.document_type, settings=self._read_settings()
            ),
        }

    def set_default_template(self, *, document_type: str, template_id: str) -> dict[str, Any]:
        normalized_document_type = _normalize_document_type(document_type)
        if normalized_document_type == "completion_act":
            raise PrintModuleError(
                "completion_act_template_locked",
                "Для акта используется только встроенный стандартный шаблон.",
                status_code=409,
            )
        template = self._find_template(template_id)
        if template.document_type != normalized_document_type:
            raise PrintModuleError(
                "validation_error", "Шаблон не соответствует выбранному типу документа."
            )
        settings = self._read_settings()
        settings.default_template_ids[normalized_document_type] = template.id
        self._write_settings(settings)
        return {
            "document_type": normalized_document_type,
            "template_id": template.id,
            "templates": self._template_payloads_for_document_type(
                normalized_document_type, settings=settings
            ),
        }

    def save_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = self._merged_settings(payload or {})
        self._write_settings(settings)
        return {
            "settings": settings.to_dict(),
            "printers": list_printers(default_name=settings.default_printer),
        }

    def _document_workspace_payload(
        self,
        document: PrintDocumentDefinition,
        *,
        settings: PrintModuleSettings,
        template_map: dict[str, list[PrintTemplateRecord]],
    ) -> dict[str, Any]:
        selected_template = self._resolve_template(document_type=document.id, settings=settings)
        return {
            **document.to_dict(),
            "selected_template_id": selected_template.id,
            "selected_template_name": selected_template.name,
            "template_count": (
                1 if document.id == "completion_act" else len(template_map.get(document.id, []))
            ),
            "is_default_selected": document.id == "repair_order",
            "supports_form_fill": document.id == "inspection_sheet",
            "supports_completion_act_editor": document.id == "completion_act",
            "template_locked": document.id == "completion_act",
        }

    def _preview_document_payload(
        self,
        card: Card,
        order: RepairOrder,
        document: PrintDocumentDefinition,
        template: PrintTemplateRecord,
        *,
        client: ClientProfile | None,
        settings: PrintModuleSettings,
        template_overrides: dict[str, str] | None,
        document_overrides: dict[str, Any] | None,
    ) -> dict[str, Any]:
        rendered = self._rendered_document_payload(
            card,
            order,
            document,
            template,
            client=client,
            settings=settings,
            template_overrides=template_overrides,
            document_overrides=document_overrides,
        )
        preview_pages = self._preview_pages(rendered["document_html"], document=document)
        return {
            "id": document.id,
            "label": document.label,
            "template": rendered["template"].to_dict(
                is_default=(
                    settings.default_template_ids.get(document.id) == rendered["template"].id
                )
            ),
            "warnings": rendered["warnings"],
            "missing_fields": rendered["missing_fields"],
            "computed_totals": rendered["computed_totals"],
            "computed_items": rendered["computed_items"],
            "page_count": len(preview_pages),
            "pages": [
                {"number": index + 1, "html": page_html}
                for index, page_html in enumerate(preview_pages)
            ],
        }

    def _rendered_document_payload(
        self,
        card: Card,
        order: RepairOrder,
        document: PrintDocumentDefinition,
        template: PrintTemplateRecord,
        *,
        client: ClientProfile | None,
        settings: PrintModuleSettings,
        template_overrides: dict[str, str] | None,
        document_overrides: dict[str, Any] | None,
    ) -> dict[str, Any]:
        effective_template = template
        if (
            document.id != "completion_act"
            and template_overrides
            and document.id in template_overrides
        ):
            effective_template = PrintTemplateRecord(
                id="preview:override",
                document_type=document.id,
                name="Предпросмотр шаблона",
                content=template_overrides.get(document.id, "") or template.content,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
                source="custom",
            )
        context = self._build_document_context(
            card,
            order,
            document=document,
            settings=settings,
            client=client,
            document_overrides=document_overrides,
        )
        try:
            fragment = render_template(effective_template.content, context)
        except TemplateRenderError as exc:
            raise PrintModuleError("template_error", f"Шаблон поврежден: {exc}") from exc
        return {
            "document": document,
            "template": effective_template,
            "document_html": self._wrap_document_html(fragment, title=document.label),
            "warnings": context["meta"]["warnings"],
            "missing_fields": context["meta"]["missing_fields"],
            "computed_totals": (
                context.get("completion_act", {}).get("totals", {})
                if document.id == "completion_act"
                else {}
            ),
            "computed_items": (
                context.get("completion_act", {}).get("items", [])
                if document.id == "completion_act"
                else []
            ),
            "resolved_document_number": (
                context.get("completion_act", {}).get("document_number", "")
                if document.id == "completion_act"
                else ""
            ),
        }

    def _combined_document_html(self, payloads: list[dict[str, Any]]) -> str:
        bodies: list[str] = []
        for payload in payloads:
            body = self._extract_document_shell_content(payload["document_html"]).replace(
                _PAGE_BREAK_MARKER, ""
            )
            bodies.append(body)
        return self._wrap_document_html("\n".join(bodies), title="Печать документов AutoStop CRM")

    def _wrap_document_html(self, body_html: str, *, title: str) -> str:
        return (
            '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{html.escape(title)}</title>"
            f"<style>{PRINT_BASE_STYLES}</style>"
            '</head><body><div class="document-shell">'
            f"{body_html}"
            "</div></body></html>"
        )

    def _extract_body(self, document_html: str) -> str:
        match = re.search(r"<body[^>]*>(.*)</body>", document_html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return document_html
        return match.group(1)

    def _extract_document_shell_content(self, document_html: str) -> str:
        body = self._extract_body(document_html)
        match = re.search(
            r'^\s*<div class="document-shell">(.*)</div>\s*$',
            body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        return match.group(1) if match else body

    def _preview_pages(self, document_html: str, *, document: PrintDocumentDefinition) -> list[str]:
        body_html = self._extract_document_shell_content(document_html)
        chunks = [chunk.strip() for chunk in body_html.split(_PAGE_BREAK_MARKER) if chunk.strip()]
        if not chunks:
            chunks = [body_html]
        return [self._wrap_document_html(chunk, title=document.label) for chunk in chunks]

    def _document_definition(self, document_id: str) -> PrintDocumentDefinition:
        normalized = _normalize_document_type(document_id)
        return self._builtin_documents[normalized]

    def _normalized_document_ids(self, value: Any) -> list[str]:
        if value is None:
            raw_values: list[Any] = ["repair_order"]
        elif isinstance(value, str):
            raw_values = [value]
        elif isinstance(value, (list, tuple)):
            raw_values = list(value)
        else:
            raw_values = []
        normalized: list[str] = []
        for raw in raw_values:
            candidate = _normalize_text(raw, limit=64)
            if candidate in self._builtin_documents and candidate not in normalized:
                normalized.append(candidate)
        return normalized or ["repair_order"]

    def _normalized_template_selection_map(self, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, str] = {}
        for raw_document_type, raw_template_id in value.items():
            document_type = _normalize_text(raw_document_type, limit=64)
            if document_type not in self._builtin_documents:
                continue
            template_id = _normalize_text(raw_template_id, limit=128)
            if template_id:
                normalized[document_type] = template_id
        return normalized

    def _normalized_template_override_map(self, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, str] = {}
        for raw_document_type, raw_content in value.items():
            document_type = _normalize_text(raw_document_type, limit=64)
            if document_type not in self._builtin_documents:
                continue
            if document_type == "completion_act":
                continue
            content = _normalize_template_content(raw_content)
            if content:
                normalized[document_type] = content
        return normalized

    def _resolved_active_document_id(
        self, active_document_id: str | None, selected_ids: list[str]
    ) -> str:
        candidate = _normalize_text(active_document_id, limit=64)
        if candidate in selected_ids:
            return candidate
        return selected_ids[0]

    def _read_settings(self) -> PrintModuleSettings:
        raw = _safe_json_read(self._settings_path, default={})
        return PrintModuleSettings.from_dict(raw)

    def _write_settings(self, settings: PrintModuleSettings) -> None:
        _safe_json_write(self._settings_path, settings.to_dict())
        self._sync_change_feed()

    def _read_custom_templates(self) -> list[PrintTemplateRecord]:
        raw = _safe_json_read(self._templates_path, default=[])
        if not isinstance(raw, list):
            return []
        templates: list[PrintTemplateRecord] = []
        seen_ids: set[str] = set()
        for item in raw:
            record = PrintTemplateRecord.from_dict(item)
            if (
                record is not None
                and not record.is_builtin
                and record.id not in self._builtin_templates
                and record.id not in seen_ids
                and record.document_type in SUPPORTED_PRINT_DOCUMENT_TYPES
            ):
                seen_ids.add(record.id)
                templates.append(record)
        return templates

    def _write_custom_templates(self, records: list[PrintTemplateRecord]) -> None:
        payload = [record.to_dict() for record in records if not record.is_builtin]
        _safe_json_write(self._templates_path, payload)
        self._sync_change_feed()

    def _templates_by_document_type(
        self, *, settings: PrintModuleSettings
    ) -> dict[str, list[PrintTemplateRecord]]:
        combined = list(self._builtin_templates.values()) + self._read_custom_templates()
        grouped: dict[str, list[PrintTemplateRecord]] = {
            document_type: [] for document_type in SUPPORTED_PRINT_DOCUMENT_TYPES
        }
        for record in combined:
            if record.document_type == "completion_act" and not record.is_builtin:
                continue
            grouped.setdefault(record.document_type, []).append(record)
        for document_type, records in grouped.items():
            default_id = settings.default_template_ids.get(document_type, "")
            grouped[document_type] = sorted(
                records,
                key=lambda item: (
                    0 if item.id == default_id else 1,
                    0 if item.is_builtin else 1,
                    item.name.lower(),
                ),
            )
        return grouped

    def _resolve_template(
        self,
        *,
        document_type: str,
        template_id: str = "",
        settings: PrintModuleSettings,
    ) -> PrintTemplateRecord:
        if document_type == "completion_act":
            builtin_id = self._builtin_documents[document_type].default_template_id
            template = self._builtin_templates.get(builtin_id)
            if template is not None:
                return template
        grouped = self._templates_by_document_type(settings=settings)
        requested_id = _normalize_text(template_id, limit=128)
        if requested_id:
            for record in grouped.get(document_type, []):
                if record.id == requested_id:
                    return record
        default_id = settings.default_template_ids.get(document_type, "")
        if default_id:
            for record in grouped.get(document_type, []):
                if record.id == default_id:
                    return record
        for record in grouped.get(document_type, []):
            if record.id == self._builtin_documents[document_type].default_template_id:
                return record
        if grouped.get(document_type):
            return grouped[document_type][0]
        raise PrintModuleError(
            "not_found",
            "Шаблоны для документа не найдены.",
            status_code=404,
            details={"document_type": document_type},
        )

    def _find_template(self, template_id: str) -> PrintTemplateRecord:
        normalized = _normalize_text(template_id, limit=128)
        if normalized in self._builtin_templates:
            return self._builtin_templates[normalized]
        for record in self._read_custom_templates():
            if record.id == normalized:
                return record
        raise PrintModuleError(
            "not_found", "Шаблон не найден.", status_code=404, details={"template_id": normalized}
        )

    def _template_payloads_for_document_type(
        self, document_type: str, *, settings: PrintModuleSettings
    ) -> list[dict[str, Any]]:
        return [
            record.to_dict(
                is_default=(settings.default_template_ids.get(document_type) == record.id)
            )
            for record in self._templates_by_document_type(settings=settings).get(document_type, [])
        ]

    def _merged_settings(self, payload: dict[str, Any] | None) -> PrintModuleSettings:
        current = self._read_settings()
        if not isinstance(payload, dict) or not payload:
            return current
        merged = current.to_dict()
        service_profile = dict(current.service_profile.to_dict())
        if isinstance(payload.get("service_profile"), dict):
            for key, value in payload["service_profile"].items():
                service_profile[str(key)] = value
        merged.update({key: value for key, value in payload.items() if key != "service_profile"})
        merged["service_profile"] = service_profile
        return PrintModuleSettings.from_dict(merged)

    def get_completion_act_form(
        self,
        card: Card,
        *,
        repair_order: RepairOrder | None = None,
        client: ClientProfile | None = None,
    ) -> dict[str, Any]:
        order = repair_order or card.repair_order
        settings = self._read_settings()
        return self._completion_act_response(
            card,
            order,
            client=self._exact_completion_act_client(card, client),
            settings=settings,
        )

    @_completion_act_mutation_locked
    def save_completion_act_form(
        self,
        card: Card,
        *,
        repair_order: RepairOrder | None = None,
        client: ClientProfile | None = None,
        form_data: dict[str, Any] | None = None,
        expected_version: Any = None,
        expected_source_fingerprint: Any = None,
        idempotency_key: str = "",
        filled_by: str = "",
        source: str = "manual",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        order = repair_order or card.repair_order
        settings = self._read_settings()
        exact_client = self._exact_completion_act_client(card, client)
        cycle_key = self._completion_act_cycle_key(card, order)
        current = self._read_completion_act_record(cycle_key)
        current_version = current.version if current is not None else 0
        normalized_key = _normalize_text(idempotency_key, limit=128)
        if not normalized_key:
            raise PrintModuleError(
                "validation_error",
                "Нужен idempotency_key для безопасного сохранения акта.",
                details={"field": "idempotency_key"},
            )
        expected = self._completion_act_expected_version(expected_version)
        fresh = self._default_completion_act_form(card, order, exact_client, settings)
        current_source_fingerprint = self._completion_act_source_fingerprint(
            card,
            order,
            exact_client,
            settings,
            cycle_key=cycle_key,
        )
        if expected_source_fingerprint is None:
            # Direct in-process callers remain backwards compatible. Protected
            # HTTP/Gateway entrypoints require the fingerprint returned by GET.
            expected_source = current_source_fingerprint
        elif (
            not isinstance(expected_source_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_source_fingerprint) is None
        ):
            raise PrintModuleError(
                "validation_error",
                "Передайте актуальный отпечаток исходных данных акта.",
                details={"field": "expected_source_fingerprint"},
            )
        else:
            expected_source = expected_source_fingerprint
        form = self._normalized_completion_act_form(form_data)
        _completion_act_validate_final_layout(form)
        self._completion_act_calculation(form)
        request_fingerprint = _request_payload_fingerprint(
            {
                "operation": "save",
                "cycle_key": cycle_key,
                "form": form.to_dict(),
                "expected_source_fingerprint": expected_source,
            }
        )
        replay = self._completion_act_idempotent_replay(
            current,
            operation="save",
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
        )
        if replay:
            return self._completion_act_response(
                card,
                order,
                client=exact_client,
                settings=settings,
                idempotent_replay=True,
            )
        if expected != current_version:
            raise PrintModuleError(
                "completion_act_version_conflict",
                "Черновик акта уже изменён в другом окне.",
                status_code=409,
                details={
                    "expected_version": expected,
                    "current_version": current_version,
                    "cycle_key": cycle_key,
                },
            )
        if expected_source != current_source_fingerprint:
            raise PrintModuleError(
                "completion_act_source_conflict",
                "Исходные данные CRM изменились после открытия редактора акта.",
                status_code=409,
                details={
                    "expected_source_fingerprint": expected_source,
                    "current_source_fingerprint": current_source_fingerprint,
                    "cycle_key": cycle_key,
                },
            )
        sparse = _nested_sparse_diff(form.to_dict(), fresh.to_dict()) or {}
        if dry_run:
            response = self._completion_act_response(
                card,
                order,
                client=exact_client,
                settings=settings,
            )
            response["dry_run"] = {
                "validated": True,
                "would_change": not (
                    current is not None
                    and not current.deleted
                    and current.overrides == sparse
                    and current.source_fingerprint == current_source_fingerprint
                ),
                "current_version": current_version,
                "next_version": current_version + 1,
                "changed_paths": _nested_changed_paths(form.to_dict(), response["form"]),
                "projected_form": form.to_dict(),
                "source_fingerprint": current_source_fingerprint,
            }
            return response
        record = CompletionActDraftData(
            cycle_key=cycle_key,
            overrides=sparse,
            version=current_version + 1,
            source_fingerprint=current_source_fingerprint,
            updated_at=utc_now_iso(),
            filled_by=_normalize_text(filled_by, limit=120),
            source=_normalize_text(source, limit=32).lower() or "manual",
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            operation="save",
            deleted=False,
        )
        self._write_completion_act_record(record)
        return self._completion_act_response(
            card,
            order,
            client=exact_client,
            settings=settings,
        )

    @_completion_act_mutation_locked
    def reset_completion_act_form(
        self,
        card: Card,
        *,
        repair_order: RepairOrder | None = None,
        client: ClientProfile | None = None,
        expected_version: Any = None,
        expected_source_fingerprint: Any = None,
        idempotency_key: str = "",
        filled_by: str = "",
        source: str = "manual",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        order = repair_order or card.repair_order
        settings = self._read_settings()
        exact_client = self._exact_completion_act_client(card, client)
        cycle_key = self._completion_act_cycle_key(card, order)
        current = self._read_completion_act_record(cycle_key)
        current_version = current.version if current is not None else 0
        normalized_key = _normalize_text(idempotency_key, limit=128)
        if not normalized_key:
            raise PrintModuleError(
                "validation_error",
                "Нужен idempotency_key для безопасного сброса акта.",
                details={"field": "idempotency_key"},
            )
        expected = self._completion_act_expected_version(expected_version)
        current_source_fingerprint = self._completion_act_source_fingerprint(
            card,
            order,
            exact_client,
            settings,
            cycle_key=cycle_key,
        )
        if expected_source_fingerprint is not None:
            if (
                not isinstance(expected_source_fingerprint, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_source_fingerprint) is None
            ):
                raise PrintModuleError(
                    "validation_error",
                    "Передайте актуальный отпечаток исходных данных акта.",
                    details={"field": "expected_source_fingerprint"},
                )
        request_fingerprint = _request_payload_fingerprint(
            {"operation": "reset", "cycle_key": cycle_key}
        )
        replay = self._completion_act_idempotent_replay(
            current,
            operation="reset",
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
        )
        if replay:
            return self._completion_act_response(
                card,
                order,
                client=exact_client,
                settings=settings,
                idempotent_replay=True,
            )
        if expected != current_version:
            raise PrintModuleError(
                "completion_act_version_conflict",
                "Черновик акта уже изменён в другом окне.",
                status_code=409,
                details={
                    "expected_version": expected,
                    "current_version": current_version,
                    "cycle_key": cycle_key,
                },
            )
        if (
            expected_source_fingerprint is not None
            and expected_source_fingerprint != current_source_fingerprint
        ):
            raise PrintModuleError(
                "completion_act_source_conflict",
                "Исходные данные CRM изменились после открытия редактора акта.",
                status_code=409,
                details={
                    "expected_source_fingerprint": expected_source_fingerprint,
                    "current_source_fingerprint": current_source_fingerprint,
                    "cycle_key": cycle_key,
                },
            )
        if dry_run:
            response = self._completion_act_response(
                card,
                order,
                client=exact_client,
                settings=settings,
            )
            response["dry_run"] = {
                "validated": True,
                "would_change": bool(current is not None and not current.deleted),
                "current_version": current_version,
                "next_version": current_version + 1,
                "changed_paths": [
                    *(["draft.state"] if current is not None and not current.deleted else []),
                    *_nested_changed_paths(response["fresh_form"], response["form"]),
                ],
                "projected_form": response["fresh_form"],
                "source_fingerprint": current_source_fingerprint,
            }
            return response
        record = CompletionActDraftData(
            cycle_key=cycle_key,
            overrides={},
            version=current_version + 1,
            source_fingerprint=current_source_fingerprint,
            updated_at=utc_now_iso(),
            filled_by=_normalize_text(filled_by, limit=120),
            source=_normalize_text(source, limit=32).lower() or "manual",
            idempotency_key=normalized_key,
            request_fingerprint=request_fingerprint,
            operation="reset",
            deleted=True,
        )
        self._write_completion_act_record(record)
        return self._completion_act_response(
            card,
            order,
            client=exact_client,
            settings=settings,
        )

    def _completion_act_expected_version(self, value: Any) -> int:
        if isinstance(value, bool):
            value = None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = -1
        if parsed < 0:
            raise PrintModuleError(
                "validation_error",
                "Передайте актуальную версию черновика акта.",
                details={"field": "expected_version"},
            )
        return parsed

    def _completion_act_idempotent_replay(
        self,
        current: CompletionActDraftData | None,
        *,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> bool:
        if current is None or current.idempotency_key != idempotency_key:
            return False
        if current.operation == operation and current.request_fingerprint == request_fingerprint:
            return True
        raise PrintModuleError(
            "idempotency_conflict",
            "Этот idempotency_key уже использован для другого изменения акта.",
            status_code=409,
            details={"current_version": current.version},
        )

    def _completion_act_cycle_key(self, card: Card, order: RepairOrder) -> str:
        completed_cycles = len([item for item in order.cycles if isinstance(item, dict)])
        status = _normalize_text(order.status, limit=24).lower()
        cycle_number = (
            completed_cycles if status == "closed" and completed_cycles else completed_cycles + 1
        )
        return f"{_normalize_text(card.id, limit=128)}:cycle:{max(1, cycle_number)}"

    @staticmethod
    def _completion_act_store_corrupt() -> PrintModuleError:
        return PrintModuleError(
            "completion_act_store_corrupt",
            "Хранилище черновиков акта повреждено или недоступно; файл сохранён без изменений.",
            status_code=503,
        )

    def _ensure_completion_act_forms_dir(self) -> None:
        path = self._completion_act_forms_dir
        try:
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise OSError("completion act draft directory must be a regular directory")
            path.mkdir(parents=False, exist_ok=True)
            if path.is_symlink() or not path.is_dir():
                raise OSError("completion act draft directory must be a regular directory")
            if os.name != "nt":
                path.chmod(0o700)
        except OSError as exc:
            raise self._completion_act_store_corrupt() from exc

    @staticmethod
    def _completion_act_record_payload(
        raw: Any,
        *,
        cycle_key: str,
        allow_missing_cycle_key: bool = False,
    ) -> CompletionActDraftData:
        if not isinstance(raw, dict):
            raise ValueError("completion act record must be a JSON object")
        embedded_key = raw.get("cycle_key")
        if embedded_key is None and allow_missing_cycle_key:
            embedded_key = cycle_key
        if not isinstance(embedded_key, str) or embedded_key != cycle_key:
            raise ValueError("completion act record key mismatch")
        if "overrides" in raw and not isinstance(raw["overrides"], dict):
            raise ValueError("completion act overrides must be a JSON object")
        if "version" in raw and (type(raw["version"]) is not int or raw["version"] < 0):
            raise ValueError("completion act version is invalid")
        if "deleted" in raw and type(raw["deleted"]) is not bool:
            raise ValueError("completion act deleted flag is invalid")
        for field in (
            "cycle_key",
            "source_fingerprint",
            "updated_at",
            "filled_by",
            "source",
            "idempotency_key",
            "request_fingerprint",
            "operation",
        ):
            if field in raw and not isinstance(raw[field], str):
                raise ValueError(f"completion act {field} must be a string")
        record = CompletionActDraftData.from_dict({**raw, "cycle_key": cycle_key})
        if record.cycle_key != cycle_key:
            raise ValueError("completion act normalized key mismatch")
        return record

    @staticmethod
    def _completion_act_record_bytes(record: CompletionActDraftData) -> bytes:
        try:
            encoded = json.dumps(
                _json_safe_value(record.to_dict()),
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ).encode("utf-8")
        except (RecursionError, TypeError, ValueError) as exc:
            raise PrintModuleError(
                "validation_error",
                "Данные печатного модуля слишком большие для сохранения.",
                details={"max_size_bytes": COMPLETION_ACT_FORM_RECORD_MAX_BYTES},
            ) from exc
        if len(encoded) > COMPLETION_ACT_FORM_RECORD_MAX_BYTES:
            raise PrintModuleError(
                "validation_error",
                "Данные печатного модуля слишком большие для сохранения.",
                details={"max_size_bytes": COMPLETION_ACT_FORM_RECORD_MAX_BYTES},
            )
        return encoded

    def _completion_act_record_path(self, cycle_key: str) -> Path:
        normalized = _normalize_text(cycle_key, limit=180)
        if not normalized or normalized != cycle_key:
            raise self._completion_act_store_corrupt()
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return self._completion_act_forms_dir / f"{digest}.json"

    def _completion_act_store_inventory(self) -> tuple[dict[str, tuple[Path, int]], int]:
        self._ensure_completion_act_forms_dir()
        entries: dict[str, tuple[Path, int]] = {}
        total_bytes = 0
        try:
            with os.scandir(self._completion_act_forms_dir) as iterator:
                for index, entry in enumerate(iterator, start=1):
                    if index > COMPLETION_ACT_FORMS_MAX_RECORDS:
                        raise ValueError("completion act directory contains too many entries")
                    if entry.is_symlink():
                        raise OSError("completion act directory contains a symlink")
                    entry_stat = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(entry_stat.st_mode):
                        raise OSError("completion act directory contains a non-regular file")
                    if os.name != "nt" and stat.S_IMODE(entry_stat.st_mode) != 0o600:
                        raise OSError("completion act directory contains a non-private file")
                    if not (
                        _COMPLETION_ACT_RECORD_FILE_RE.fullmatch(entry.name)
                        or _COMPLETION_ACT_TEMP_FILE_RE.fullmatch(entry.name)
                    ):
                        raise OSError("completion act directory contains an unexpected file")
                    if entry_stat.st_size > COMPLETION_ACT_FORM_RECORD_MAX_BYTES:
                        raise ValueError("completion act record exceeds its byte limit")
                    total_bytes += entry_stat.st_size
                    if total_bytes > COMPLETION_ACT_FORMS_FILE_MAX_BYTES:
                        raise ValueError("completion act directory exceeds its byte quota")
                    entries[entry.name] = (
                        self._completion_act_forms_dir / entry.name,
                        entry_stat.st_size,
                    )
        except (OSError, ValueError) as exc:
            raise self._completion_act_store_corrupt() from exc
        return entries, total_bytes

    def _read_completion_act_json_file(self, path: Path, *, max_bytes: int) -> Any:
        try:
            path_stat = path.lstat()
            if not stat.S_ISREG(path_stat.st_mode) or stat.S_ISLNK(path_stat.st_mode):
                raise OSError("completion act store path must be a regular file")
            if os.name != "nt" and stat.S_IMODE(path_stat.st_mode) != 0o600:
                raise OSError("completion act store path must use private permissions")
            if path_stat.st_size > max_bytes:
                raise ValueError("completion act store path exceeds its byte limit")
            flags = os.O_RDONLY
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            try:
                opened_stat = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened_stat.st_mode)
                    or opened_stat.st_dev != path_stat.st_dev
                    or opened_stat.st_ino != path_stat.st_ino
                    or opened_stat.st_size > max_bytes
                ):
                    raise OSError("completion act store changed while opening")
                with os.fdopen(descriptor, "rb", closefd=False) as handle:
                    encoded = handle.read(max_bytes + 1)
            finally:
                os.close(descriptor)
            if len(encoded) > max_bytes:
                raise ValueError("completion act store path exceeds its byte limit")
            return json.loads(
                encoded.decode("utf-8"),
                parse_constant=_reject_json_constant,
            )
        except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise self._completion_act_store_corrupt() from exc

    def _read_completion_act_record_path(
        self,
        path: Path,
        *,
        cycle_key: str,
    ) -> CompletionActDraftData:
        raw = self._read_completion_act_json_file(
            path,
            max_bytes=COMPLETION_ACT_FORM_RECORD_MAX_BYTES,
        )
        try:
            return self._completion_act_record_payload(raw, cycle_key=cycle_key)
        except ValueError as exc:
            raise self._completion_act_store_corrupt() from exc

    def _read_legacy_completion_act_form_map(self) -> dict[str, CompletionActDraftData]:
        raw = self._read_completion_act_json_file(
            self._completion_act_forms_path,
            max_bytes=COMPLETION_ACT_FORMS_FILE_MAX_BYTES,
        )
        if not isinstance(raw, dict):
            raise self._completion_act_store_corrupt()
        normalized: dict[str, CompletionActDraftData] = {}
        try:
            for raw_key, raw_value in raw.items():
                cycle_key = _normalize_text(raw_key, limit=180)
                if not cycle_key or cycle_key != raw_key or cycle_key in normalized:
                    raise ValueError("invalid or duplicate completion act cycle key")
                normalized[cycle_key] = self._completion_act_record_payload(
                    raw_value,
                    cycle_key=cycle_key,
                    allow_missing_cycle_key=True,
                )
        except ValueError as exc:
            raise self._completion_act_store_corrupt() from exc
        return normalized

    def _write_completion_act_record_bytes(self, path: Path, encoded: bytes) -> None:
        self._ensure_completion_act_forms_dir()
        if path.parent != self._completion_act_forms_dir:
            raise self._completion_act_store_corrupt()
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise self._completion_act_store_corrupt()
        tmp_path = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(tmp_path, flags, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.close(descriptor)
            descriptor = -1
            os.replace(tmp_path, path)
            if os.name != "nt":
                path.chmod(0o600)
            _fsync_directory(self._completion_act_forms_dir)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _migrate_legacy_completion_act_forms(self, *, process_locked: bool = False) -> None:
        with self._completion_act_lock:
            self._ensure_completion_act_forms_dir()
            if not self._completion_act_forms_path.exists():
                if self._completion_act_forms_path.is_symlink():
                    raise self._completion_act_store_corrupt()
                return
            if not process_locked:
                try:
                    with self._completion_act_process_lock.acquire():
                        self._migrate_legacy_completion_act_forms(process_locked=True)
                    return
                except TimeoutError as exc:
                    raise PrintModuleError(
                        "completion_act_lock_timeout",
                        "Черновик акта временно занят другим процессом; повторите действие.",
                        status_code=503,
                    ) from exc

            legacy = self._read_legacy_completion_act_form_map()
            entries, total_bytes = self._completion_act_store_inventory()
            record_entries = {
                name: entry
                for name, entry in entries.items()
                if _COMPLETION_ACT_RECORD_FILE_RE.fullmatch(name)
            }
            pending: list[tuple[Path, bytes]] = []
            final_entry_count = len(entries)
            final_bytes = total_bytes
            for cycle_key, legacy_record in legacy.items():
                path = self._completion_act_record_path(cycle_key)
                encoded = self._completion_act_record_bytes(legacy_record)
                existing_entry = record_entries.get(path.name)
                if existing_entry is not None:
                    current = self._read_completion_act_record_path(
                        existing_entry[0],
                        cycle_key=cycle_key,
                    )
                    if current.version > legacy_record.version:
                        continue
                    if current.version == legacy_record.version:
                        if current.to_dict() != legacy_record.to_dict():
                            raise self._completion_act_store_corrupt()
                        continue
                    final_bytes -= existing_entry[1]
                else:
                    final_entry_count += 1
                final_bytes += len(encoded)
                pending.append((path, encoded))
            if (
                final_entry_count > COMPLETION_ACT_FORMS_MAX_RECORDS
                or final_bytes > COMPLETION_ACT_FORMS_FILE_MAX_BYTES
            ):
                raise self._completion_act_store_corrupt()
            for path, encoded in pending:
                self._write_completion_act_record_bytes(path, encoded)
            try:
                if self._completion_act_forms_path.is_symlink():
                    raise OSError("legacy completion act store became a symlink")
                self._completion_act_forms_path.unlink()
                _fsync_directory(self._root_dir)
            except OSError as exc:
                raise self._completion_act_store_corrupt() from exc

    def _read_completion_act_record(self, cycle_key: str) -> CompletionActDraftData | None:
        self._migrate_legacy_completion_act_forms()
        path = self._completion_act_record_path(cycle_key)
        if path.is_symlink():
            raise self._completion_act_store_corrupt()
        if not path.exists():
            return None
        return self._read_completion_act_record_path(path, cycle_key=cycle_key)

    def _read_completion_act_form_map(self) -> dict[str, CompletionActDraftData]:
        self._migrate_legacy_completion_act_forms()
        entries, _ = self._completion_act_store_inventory()
        normalized: dict[str, CompletionActDraftData] = {}
        for name, (path, _) in sorted(entries.items()):
            if not _COMPLETION_ACT_RECORD_FILE_RE.fullmatch(name):
                continue
            raw = self._read_completion_act_json_file(
                path,
                max_bytes=COMPLETION_ACT_FORM_RECORD_MAX_BYTES,
            )
            if not isinstance(raw, dict) or not isinstance(raw.get("cycle_key"), str):
                raise self._completion_act_store_corrupt()
            cycle_key = raw["cycle_key"]
            if self._completion_act_record_path(cycle_key) != path or cycle_key in normalized:
                raise self._completion_act_store_corrupt()
            try:
                normalized[cycle_key] = self._completion_act_record_payload(
                    raw,
                    cycle_key=cycle_key,
                )
            except ValueError as exc:
                raise self._completion_act_store_corrupt() from exc
        return normalized

    def _write_completion_act_record(self, record: CompletionActDraftData) -> None:
        path = self._completion_act_record_path(record.cycle_key)
        encoded = self._completion_act_record_bytes(record)
        entries, total_bytes = self._completion_act_store_inventory()
        current_entry = entries.get(path.name)
        if current_entry is None and len(entries) >= COMPLETION_ACT_FORMS_MAX_RECORDS:
            raise PrintModuleError(
                "validation_error",
                "Достигнут предел количества черновиков актов.",
                details={"max_records": COMPLETION_ACT_FORMS_MAX_RECORDS},
            )
        prospective_bytes = total_bytes - (current_entry[1] if current_entry else 0) + len(encoded)
        if prospective_bytes > COMPLETION_ACT_FORMS_FILE_MAX_BYTES:
            raise PrintModuleError(
                "validation_error",
                "Достигнут общий предел хранилища черновиков актов.",
                details={"max_size_bytes": COMPLETION_ACT_FORMS_FILE_MAX_BYTES},
            )
        self._write_completion_act_record_bytes(path, encoded)
        self._sync_completion_act_change_feed(record)

    def _exact_completion_act_client(
        self, card: Card, client: ClientProfile | None
    ) -> ClientProfile | None:
        if client is None or not card.client_id or client.id != card.client_id:
            return None
        return client

    def _default_completion_act_form(
        self,
        card: Card,
        order: RepairOrder,
        client: ClientProfile | None,
        settings: PrintModuleSettings,
    ) -> CompletionActFormData:
        profile = settings.service_profile
        performer = CompletionActPartyData(
            legal_name=profile.legal_name or profile.company_name,
            address=profile.address,
            inn=profile.inn,
            kpp=profile.kpp,
            ogrn=profile.ogrn,
            bank_name=profile.bank_name,
            bik=profile.bik,
            settlement_account=profile.settlement_account,
            correspondent_account=profile.correspondent_account,
            signer_position=profile.signer_position,
            signer_name=profile.signer_name,
        )
        if client is None:
            customer = CompletionActPartyData(legal_name=order.client)
        else:
            customer = CompletionActPartyData(
                legal_name=client.legal_name or client.short_name or client.name(),
                address=client.legal_address or client.actual_address,
                inn=client.inn,
                kpp=client.kpp,
                ogrn=client.ogrn,
                bank_name=client.bank_name,
                bik=client.bik,
                settlement_account=client.checking_account,
                correspondent_account=client.correspondent_account,
                signer_position=client.contact_position,
                signer_name=client.contact_person,
            )
        items: list[CompletionActItemData] = []
        for section, rows in (("works", order.works), ("materials", order.materials)):
            for index, row in enumerate(rows):
                item_id = row.id or f"{section}-{index + 1}"
                items.append(
                    CompletionActItemData(
                        id=f"{section}:{item_id}",
                        section=section,
                        name=row.name,
                        unit=("ч" if section == "works" else (row.inventory_unit or "шт")),
                        quantity=row.quantity,
                        price=row.price,
                    )
                )
        return CompletionActFormData(
            document_number=order.number,
            document_date=order.date or order.opened_at,
            basis="",
            performer=performer,
            customer=customer,
            items=items,
            acceptance_text=_COMPLETION_ACT_ACCEPTANCE_TEXT,
        )

    def _normalized_completion_act_form(self, value: Any) -> CompletionActFormData:
        if isinstance(value, dict):
            text_limits = {
                "document_number": 80,
                "document_date": 64,
                "basis": 500,
                "acceptance_text": 1_000,
            }
            party_limits = {
                "legal_name": 240,
                "address": 320,
                "inn": 32,
                "kpp": 32,
                "ogrn": 32,
                "bank_name": 240,
                "bik": 32,
                "settlement_account": 64,
                "correspondent_account": 64,
                "signer_position": 120,
                "signer_name": 160,
            }
            item_limits = {
                "id": 128,
                "section": 32,
                "name": 500,
                "unit": 24,
                "quantity": 48,
                "price": 48,
            }

            def validate_text(
                raw: Any,
                *,
                field: str,
                limit: int,
                allow_number: bool = False,
            ) -> None:
                if raw is None:
                    return
                is_number = type(raw) in {int, float, Decimal}
                if not isinstance(raw, str) and not (allow_number and is_number):
                    raise PrintModuleError(
                        "validation_error",
                        "Поле акта должно содержать текст или число допустимого типа.",
                        details={"field": field},
                    )
                if len(raw if isinstance(raw, str) else str(raw)) > limit:
                    raise PrintModuleError(
                        "validation_error",
                        "Поле акта превышает допустимую длину.",
                        details={"field": field, "max_length": limit},
                    )

            for field, limit in text_limits.items():
                validate_text(value.get(field), field=field, limit=limit)
            for party_name in ("performer", "customer"):
                party = value.get(party_name)
                if party is not None and not isinstance(party, dict):
                    raise PrintModuleError(
                        "validation_error",
                        "Реквизиты стороны акта должны быть объектом.",
                        details={"field": party_name},
                    )
                if not isinstance(party, dict):
                    continue
                for field, limit in party_limits.items():
                    validate_text(party.get(field), field=f"{party_name}.{field}", limit=limit)
            raw_items = value.get("items")
            if raw_items is not None and not isinstance(raw_items, list):
                raise PrintModuleError(
                    "validation_error",
                    "Строки акта должны быть переданы списком.",
                    details={"field": "items"},
                )
            if isinstance(raw_items, list):
                # Reject the bounded row count before touching any row. Preview,
                # PDF and save run under the card-service lock, so normalizing an
                # oversized attacker-controlled list first could block unrelated
                # CRM operations for seconds.
                if len(raw_items) > COMPLETION_ACT_ITEMS_MAX:
                    raise PrintModuleError(
                        "validation_error",
                        f"В одном акте можно сохранить не более {COMPLETION_ACT_ITEMS_MAX} строк.",
                        details={"field": "items", "max_items": COMPLETION_ACT_ITEMS_MAX},
                    )
                for index, item in enumerate(raw_items):
                    if not isinstance(item, dict):
                        raise PrintModuleError(
                            "validation_error",
                            "Каждая строка акта должна быть объектом.",
                            details={"field": f"items[{index}]"},
                        )
                    for field, limit in item_limits.items():
                        validate_text(
                            item.get(field),
                            field=f"items[{index}].{field}",
                            limit=limit,
                            allow_number=field in {"quantity", "price"},
                        )
        form = CompletionActFormData.from_dict(value)
        used_ids: set[str] = set()
        for index, item in enumerate(form.items):
            candidate = item.id or f"manual-{index + 1}"
            if candidate in used_ids:
                candidate = f"{candidate}-{index + 1}"
            item.id = candidate
            used_ids.add(candidate)
        return form

    def _completion_act_source_fingerprint(
        self,
        card: Card,
        order: RepairOrder,
        client: ClientProfile | None,
        settings: PrintModuleSettings,
        *,
        cycle_key: str,
    ) -> str:
        fresh = self._default_completion_act_form(card, order, client, settings)
        payload = {
            "cycle_key": cycle_key,
            "linked_client_id": card.client_id,
            "fresh_form": fresh.to_dict(),
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

    def _completion_act_calculation(self, form: CompletionActFormData) -> dict[str, Any]:
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
            quantity = self._completion_act_decimal(item.quantity, field=f"items[{index}].quantity")
            price = self._completion_act_decimal(item.price, field=f"items[{index}].price")
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

    def _completion_act_decimal(self, value: Any, *, field: str) -> Decimal | None:
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
        fresh = self._default_completion_act_form(card, order, client, settings)
        effective = self._normalized_completion_act_form(_nested_merge(fresh.to_dict(), overrides))
        calculation = self._completion_act_calculation(effective)
        source_fingerprint = self._completion_act_source_fingerprint(
            card,
            order,
            client,
            settings,
            cycle_key=cycle_key,
        )
        is_stale = bool(
            exists and record is not None and record.source_fingerprint != source_fingerprint
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
            "warnings": warnings,
            "missing_fields": calculation["missing_fields"],
        }

    def _completion_act_document_context(
        self,
        card: Card,
        order: RepairOrder,
        *,
        client: ClientProfile | None,
        settings: PrintModuleSettings,
        document_overrides: dict[str, Any] | None,
    ) -> dict[str, Any]:
        exact_client = self._exact_completion_act_client(card, client)
        fresh = self._default_completion_act_form(card, order, exact_client, settings)
        explicit = (
            document_overrides.get("completion_act")
            if isinstance(document_overrides, dict)
            and isinstance(document_overrides.get("completion_act"), dict)
            else None
        )
        if explicit is not None:
            form = self._normalized_completion_act_form(explicit)
        else:
            cycle_key = self._completion_act_cycle_key(card, order)
            record = self._read_completion_act_record(cycle_key)
            sparse = record.overrides if record is not None and not record.deleted else {}
            form = self._normalized_completion_act_form(_nested_merge(fresh.to_dict(), sparse))
        _completion_act_validate_final_layout(form)
        calculation = self._completion_act_calculation(form)
        items = calculation["items"]
        chunks = _completion_act_page_chunks(form, items)
        page_count = len(chunks)
        pages = [
            {
                "page_number": index + 1,
                "page_count": page_count,
                "page_break_before": index > 0,
                "page_break_marker": _PAGE_BREAK_MARKER if index > 0 else "",
                "is_first": index == 0,
                "is_final": index == page_count - 1,
                "items": chunk,
                "show_table": bool(chunk) or index == page_count - 1,
                "show_table_header": bool(chunk) or not items,
                "show_empty_items": not items,
                "show_totals": index == page_count - 1,
                "show_closing": index == page_count - 1,
                "show_summary": index == page_count - 1,
                "show_acceptance": index == page_count - 1 and bool(form.acceptance_text),
                "show_requisites": index == page_count - 1,
                "acceptance_text": form.acceptance_text if index == page_count - 1 else "",
            }
            for index, chunk in enumerate(chunks)
        ]
        return {
            "document_number": form.document_number,
            "document_number_display": _display(form.document_number),
            "document_date": form.document_date,
            "document_date_display": _date_long_ru_display(form.document_date),
            "basis": form.basis,
            "basis_display": form.basis,
            "performer": self._completion_act_party_context(form.performer),
            "customer": self._completion_act_party_context(form.customer),
            "items": items,
            "pages": pages,
            "first_page_items": pages[0]["items"],
            "final_page_items": pages[-1]["items"],
            "has_continuation_page": page_count > 1,
            "items_count": len(items),
            "items_count_words_display": str(len(items)),
            "totals": calculation["totals"],
            "acceptance_text": form.acceptance_text,
            "acceptance_text_html": _line_breaks_html(form.acceptance_text),
            "warnings": calculation["warnings"],
            "missing_fields": calculation["missing_fields"],
        }

    def _completion_act_party_context(self, party: CompletionActPartyData) -> dict[str, Any]:
        raw = party.to_dict()
        return {
            **raw,
            **{f"{key}_display": _display(value) for key, value in raw.items()},
        }

    def get_inspection_sheet_form(
        self, card: Card, *, repair_order: RepairOrder | None = None
    ) -> dict[str, Any]:
        order = repair_order or card.repair_order
        form = self._load_inspection_sheet_form(card, order)
        return {
            "card_id": card.id,
            "document_type": "inspection_sheet",
            "form": form.to_dict(),
            "meta": {
                "has_saved_draft": self._inspection_sheet_form_key(card)
                in self._read_inspection_sheet_form_map(),
                "updated_at": form.updated_at,
                "filled_by": form.filled_by,
                "source": form.source,
            },
        }

    def save_inspection_sheet_form(
        self,
        card: Card,
        *,
        repair_order: RepairOrder | None = None,
        form_data: dict[str, Any] | None = None,
        filled_by: str = "",
        source: str = "manual",
    ) -> dict[str, Any]:
        order = repair_order or card.repair_order
        base = self._default_inspection_sheet_form(card, order)
        payload = dict(base.to_dict())
        if isinstance(form_data, dict):
            payload.update(form_data)
        payload["updated_at"] = utc_now_iso()
        payload["filled_by"] = _normalize_text(filled_by, limit=120)
        payload["source"] = _normalize_text(source, limit=24).lower() or "manual"
        form = InspectionSheetFormData.from_dict(payload)
        forms = self._read_inspection_sheet_form_map()
        forms[self._inspection_sheet_form_key(card)] = form.to_dict()
        self._write_inspection_sheet_form_map(forms)
        return {
            "card_id": card.id,
            "document_type": "inspection_sheet",
            "form": form.to_dict(),
            "meta": {
                "updated_at": form.updated_at,
                "filled_by": form.filled_by,
                "source": form.source,
            },
        }

    def build_inspection_sheet_autofill_payload(
        self,
        card: Card,
        *,
        repair_order: RepairOrder | None = None,
    ) -> dict[str, Any]:
        order = repair_order or card.repair_order
        form = self._load_inspection_sheet_form(card, order)
        vehicle_display = order.vehicle or card.vehicle_display()
        return {
            "card": {
                "id": card.id,
                "heading": card.heading(),
                "title": card.title or "",
                "vehicle": card.vehicle or "",
                "description": card.description or "",
                "tags": [tag.label for tag in getattr(card, "tags", [])],
            },
            "repair_order": {
                "number": order.number,
                "client": order.client,
                "phone": order.phone,
                "vehicle": vehicle_display,
                "license_plate": order.license_plate,
                "vin": order.vin,
                "mileage": order.mileage,
                "reason": order.reason,
                "comment": order.comment,
                "note": order.note,
                "works": [
                    item["name"]
                    for item in [
                        _repair_row_dict(row, section="works", index=index)
                        for index, row in enumerate(order.works)
                    ]
                ],
                "materials": [
                    item["name"]
                    for item in [
                        _repair_row_dict(row, section="materials", index=index)
                        for index, row in enumerate(order.materials)
                    ]
                ],
                "work_rows": [
                    {
                        "name": item["name"],
                        "quantity": ""
                        if item["quantity_display"] == "—"
                        else item["quantity_display"],
                    }
                    for item in [
                        _repair_row_dict(row, section="works", index=index)
                        for index, row in enumerate(order.works)
                    ]
                ],
                "material_rows": [
                    {
                        "name": item["name"],
                        "quantity": ""
                        if item["quantity_display"] == "—"
                        else item["quantity_display"],
                    }
                    for item in [
                        _repair_row_dict(row, section="materials", index=index)
                        for index, row in enumerate(order.materials)
                    ]
                ],
            },
            "current_form": form.to_dict(),
            "suggested_defaults": self._default_inspection_sheet_form(card, order).to_dict(),
        }

    def _inspection_sheet_form_key(self, card: Card) -> str:
        if card.id == "manual-document":
            payload = {
                "title": card.title,
                "vehicle": card.vehicle,
                "description": card.description,
                "repair_order": card.repair_order.to_storage_dict(),
            }
            digest = hashlib.sha256(
                json.dumps(
                    _json_safe_value(payload),
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()[:16]
            return f"manual-document:{digest}"
        return _normalize_text(card.id, limit=128)

    def _read_inspection_sheet_form_map(self) -> dict[str, dict[str, Any]]:
        raw = _safe_json_read(self._inspection_sheet_forms_path, default={})
        if not isinstance(raw, dict):
            return {}
        normalized: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            card_id = _normalize_text(key, limit=128)
            if not card_id or not isinstance(value, dict):
                continue
            normalized[card_id] = InspectionSheetFormData.from_dict(value).to_dict()
        return normalized

    def _write_inspection_sheet_form_map(self, payload: dict[str, dict[str, Any]]) -> None:
        _safe_json_write(self._inspection_sheet_forms_path, payload)
        self._sync_change_feed()

    def reconcile_change_feed(self) -> None:
        self._sync_change_feed()
        self._reconcile_pending_completion_act_change_feed()

    def _sync_completion_act_change_feed(self, record: CompletionActDraftData) -> None:
        if self._change_feed_store is None:
            return
        try:
            projected = project_print_module(
                settings={},
                templates=[],
                inspection_sheet_forms={},
                completion_act_forms={record.cycle_key: record.to_dict()},
            )
            completion_projection = {
                key: value
                for key, value in projected.items()
                if value.entity_type == "completion_act_form"
            }
            self._change_feed_store.reconcile_external_projection_slice(
                "print_module",
                completion_projection,
            )
        except Exception as exc:  # pragma: no cover - next explicit reconciliation repairs it
            with self._completion_act_lock:
                self._completion_act_feed_pending.add(record.cycle_key)
            if self._logger is not None:
                self._logger.warning("print_change_feed_deferred error=%s", exc)
        else:
            with self._completion_act_lock:
                self._completion_act_feed_pending.discard(record.cycle_key)

    def _reconcile_pending_completion_act_change_feed(self) -> None:
        if self._change_feed_store is None:
            return
        with self._completion_act_lock:
            cycle_keys = sorted(self._completion_act_feed_pending)[
                :COMPLETION_ACT_FEED_RECONCILE_BATCH
            ]
        for cycle_key in cycle_keys:
            try:
                record = self._read_completion_act_record(cycle_key)
            except PrintModuleError as exc:  # pragma: no cover - corrupt store stays fail closed
                if self._logger is not None:
                    self._logger.warning(
                        "print_change_feed_deferred cycle=%s error=%s",
                        hashlib.sha256(cycle_key.encode("utf-8")).hexdigest()[:16],
                        exc.code,
                    )
                continue
            if record is None:
                with self._completion_act_lock:
                    self._completion_act_feed_pending.discard(cycle_key)
                continue
            self._sync_completion_act_change_feed(record)

    def _sync_change_feed(self, *, initialize: bool = False) -> None:
        if self._change_feed_store is None:
            return
        completion_act_forms = (
            {key: record.to_dict() for key, record in self._read_completion_act_form_map().items()}
            if initialize
            else {}
        )
        projected = project_print_module(
            settings=self._read_settings().to_dict(),
            templates=[item.to_dict() for item in self._read_custom_templates()],
            inspection_sheet_forms=self._read_inspection_sheet_form_map(),
            completion_act_forms=completion_act_forms,
        )
        try:
            if initialize:
                # Startup/explicit reconciliation is the only service path that
                # takes a bounded full draft snapshot.
                self._change_feed_store.initialize_external_projection("print_module", projected)
                self._change_feed_store.reconcile_external_projection("print_module", projected)
            else:
                self._change_feed_store.reconcile_external_projection_slice(
                    "print_module",
                    projected,
                    replace_entity_types={
                        "print_settings",
                        "print_template",
                        "inspection_sheet_form",
                    },
                )
        except Exception as exc:  # pragma: no cover - next feed read reconciles files
            if initialize:
                with self._completion_act_lock:
                    self._completion_act_feed_pending.update(completion_act_forms)
            if self._logger is not None:
                self._logger.warning("print_change_feed_deferred error=%s", exc)

    def _load_inspection_sheet_form(
        self, card: Card, order: RepairOrder
    ) -> InspectionSheetFormData:
        saved = self._read_inspection_sheet_form_map().get(self._inspection_sheet_form_key(card))
        if isinstance(saved, dict):
            return InspectionSheetFormData.from_dict(saved)
        return self._default_inspection_sheet_form(card, order)

    def _default_inspection_sheet_form(
        self, card: Card, order: RepairOrder
    ) -> InspectionSheetFormData:
        vehicle_display = _normalize_text(order.vehicle or card.vehicle_display(), limit=200)
        vin_or_plate = " · ".join(
            part
            for part in (
                _normalize_text(order.vin, limit=80),
                _normalize_text(order.license_plate, limit=40),
            )
            if part
        )
        return InspectionSheetFormData(
            client=order.client,
            vehicle=vehicle_display,
            vin_or_plate=vin_or_plate,
            complaint_summary=_normalize_multiline(order.reason, limit=16_000),
            findings=self._bullet_lines(order.note, fallback_source=order.comment),
            recommendations=self._bullet_lines(order.comment),
            planned_works=self._row_lines(order.works),
            planned_materials=self._row_lines(order.materials),
            planned_work_rows=self._default_inspection_sheet_table_rows(order.works),
            planned_material_rows=self._default_inspection_sheet_table_rows(order.materials),
            master_comment=_normalize_multiline(order.note, limit=16_000),
        )

    def _row_lines(self, rows: list[RepairOrderRow]) -> str:
        parts: list[str] = []
        for row in rows:
            name = _normalize_text(row.name, limit=240)
            quantity = _normalize_text(row.quantity, limit=40)
            if not name:
                continue
            parts.append(f"{name} — {quantity} шт." if quantity else name)
        return "\n".join(parts)

    def _default_inspection_sheet_table_rows(
        self, rows: list[RepairOrderRow]
    ) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for row in rows:
            name = _normalize_text(row.name, limit=240)
            quantity = _normalize_text(row.quantity, limit=40)
            if not name and not quantity:
                continue
            items.append({"name": name, "quantity": quantity})
        return items

    def _bullet_lines(self, value: Any, fallback_source: Any = "") -> str:
        points = self._bullet_points(value, fallback_source=fallback_source)
        return "\n".join(item["text"] for item in points if item.get("text"))

    def _inspection_sheet_list(self, value: Any) -> list[dict[str, str]]:
        return self._bullet_points(value)

    def _inspection_sheet_table_row(
        self, row: dict[str, Any], *, index: int
    ) -> dict[str, str | int]:
        name = _normalize_text(row.get("name"), limit=240)
        quantity = _normalize_text(row.get("quantity"), limit=40)
        return {
            "index": index + 1,
            "name": name or "—",
            "quantity": quantity,
            "quantity_display": quantity or "—",
        }

    def _inspection_sheet_table_rows(
        self,
        rows_value: Any,
        *,
        text_value: Any,
        fallback_rows: list[dict[str, Any]],
    ) -> list[dict[str, str | int]]:
        normalized_rows: list[dict[str, str | int]] = []
        if isinstance(rows_value, list):
            for index, item in enumerate(rows_value):
                if not isinstance(item, dict):
                    continue
                row = self._inspection_sheet_table_row(item, index=index)
                if row["name"] == "—" and row["quantity_display"] == "—":
                    continue
                normalized_rows.append(row)
        if normalized_rows:
            return normalized_rows
        list_rows = self._inspection_sheet_list(text_value)
        if list_rows:
            return [
                self._inspection_sheet_table_row(
                    {"name": item.get("text", ""), "quantity": ""}, index=index
                )
                for index, item in enumerate(list_rows)
            ]
        return [
            self._inspection_sheet_table_row(
                {
                    "name": item.get("name", ""),
                    "quantity": ""
                    if item.get("quantity_display") == "—"
                    else item.get("quantity_display", ""),
                },
                index=index,
            )
            for index, item in enumerate(fallback_rows)
            if item.get("name")
        ]

    def _inspection_sheet_missing_fields(self, form: InspectionSheetFormData) -> list[str]:
        missing: list[str] = []
        if not _normalize_text(form.client):
            missing.append("client")
        if not _normalize_text(form.vehicle):
            missing.append("vehicle")
        if not _normalize_text(form.complaint_summary):
            missing.append("complaint_summary")
        if not _normalize_text(form.findings):
            missing.append("findings")
        if not _normalize_text(form.recommendations):
            missing.append("recommendations")
        return missing

    def _build_document_context(
        self,
        card: Card,
        order: RepairOrder,
        *,
        document: PrintDocumentDefinition,
        settings: PrintModuleSettings,
        client: ClientProfile | None = None,
        document_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        works = [
            _repair_row_dict(row, section="works", index=index)
            for index, row in enumerate(order.works)
        ]
        materials = [
            _repair_row_dict(row, section="materials", index=index)
            for index, row in enumerate(order.materials)
        ]
        repair_order_payload = _print_safe_repair_order_dict(order)
        base_line_items = [
            {**item, "index": index + 1} for index, item in enumerate(works + materials)
        ]
        inspection_form = self._load_inspection_sheet_form(card, order)
        findings = self._bullet_points(order.note, fallback_source=order.comment)
        recommendations = self._bullet_points(order.comment)
        issue_points = self._bullet_points(order.reason)
        missing_fields = self._missing_fields(
            card,
            order,
            document=document,
            works=works,
            materials=materials,
        )
        inspection_planned_works = self._inspection_sheet_list(inspection_form.planned_works)
        inspection_planned_materials = self._inspection_sheet_list(
            inspection_form.planned_materials
        )
        inspection_planned_work_rows = self._inspection_sheet_table_rows(
            inspection_form.planned_work_rows,
            text_value=inspection_form.planned_works,
            fallback_rows=works,
        )
        inspection_planned_material_rows = self._inspection_sheet_table_rows(
            inspection_form.planned_material_rows,
            text_value=inspection_form.planned_materials,
            fallback_rows=materials,
        )
        if document.id == "inspection_sheet":
            findings = self._inspection_sheet_list(inspection_form.findings)
            recommendations = self._inspection_sheet_list(inspection_form.recommendations)
            missing_fields = self._inspection_sheet_missing_fields(inspection_form)
        warnings: list[str] = []
        payment_summary = order.payment_summary_value()
        payment_summary_display = {
            f"{key}_display": _money_display(value) for key, value in payment_summary.items()
        }
        payment_summary_ruble_display = {
            f"{key}_ruble_display": _money_ruble_display(value)
            for key, value in payment_summary.items()
        }
        invoice_cashless_total = repair_order_cashless_gross_value(payment_summary["base_total"])
        invoice_line_items = _balance_invoice_line_totals(
            [_invoice_line_item_dict(item) for item in base_line_items],
            invoice_cashless_total,
        )
        invoice_base_total = payment_summary["base_total"]
        invoice_total = invoice_cashless_total if document.id == "invoice" else invoice_base_total
        invoice_line_items_for_document = (
            invoice_line_items if document.id == "invoice" else base_line_items
        )
        invoice_tax = _invoice_tax_payload(order)
        _, invoice_tax_amount = _split_tax_included_amount(invoice_total, invoice_tax["rate"])
        invoice_tax_display = _money_display(invoice_tax_amount)
        invoice_total_display = _money_display(invoice_total)
        invoice_total_words_display = _money_words_display(invoice_total)
        invoice_prepayment = _invoice_print_prepayment_value(payment_summary)
        invoice_amount_due = _round_money(max(invoice_total - invoice_prepayment, Decimal("0")))
        cash_total = payment_summary["base_total"]
        noncash_total = repair_order_cashless_gross_value(payment_summary["base_total"])
        noncash_taxes_and_fees = noncash_total - payment_summary["base_total"]
        selected_due = (
            payment_summary["noncash_due"]
            if order.payment_method == REPAIR_ORDER_PAYMENT_METHOD_CASHLESS
            else payment_summary["cash_due"]
        )
        selected_due_label = (
            "Доплата по безналичному расчету"
            if order.payment_method == REPAIR_ORDER_PAYMENT_METHOD_CASHLESS
            else "Доплата по наличному расчету"
        )
        selected_due_display = _money_display(selected_due)
        selected_due_words_display = _money_words_display(selected_due)
        grand_total = payment_summary["base_total"] + payment_summary["taxes_and_fees"]
        grand_total_display = _money_display(grand_total)
        grand_total_words_display = _money_words_display(grand_total)
        total_paid_display = payment_summary_display["total_paid_display"]
        cash_prepayment = payment_summary["base_paid_cash_only"]
        card_prepayment = payment_summary["base_paid_card"]
        cash_like_prepayment = _round_money(cash_prepayment + card_prepayment)
        cashless_prepayment = payment_summary["base_paid_noncash"]
        client_context = _client_invoice_context(
            client,
            order_client=order.client,
            order_phone=order.phone,
        )
        regulated_context = _regulated_document_context(
            order=order,
            settings=settings,
            client=client,
            line_items=base_line_items,
            document_overrides=document_overrides,
        )
        completion_act_context: dict[str, Any] = {}
        if document.id == "completion_act":
            completion_act_context = self._completion_act_document_context(
                card,
                order,
                client=client,
                settings=settings,
                document_overrides=document_overrides,
            )
            missing_fields = completion_act_context["missing_fields"]
            warnings = completion_act_context["warnings"]
        else:
            if missing_fields:
                warnings.append("Часть полей не заполнена, проверьте документ перед печатью.")
            if not base_line_items:
                warnings.append("В документе нет работ и материалов.")
        return {
            "service": {
                **settings.service_profile.to_dict(),
                "company_name": _display(settings.service_profile.company_name),
                "legal_name": _display(
                    settings.service_profile.legal_name,
                    fallback=settings.service_profile.company_name or "—",
                ),
                "address": _display(settings.service_profile.address),
                "phone": _display(settings.service_profile.phone),
                "reception_phone": _display(settings.service_profile.reception_phone),
                "spare_parts_phone": _display(settings.service_profile.spare_parts_phone),
                "email": _display(settings.service_profile.email),
                "website": _display(settings.service_profile.website),
                "work_hours": _display(settings.service_profile.work_hours),
                "inn": _display(settings.service_profile.inn),
                "kpp": _display(settings.service_profile.kpp),
                "ogrn": _display(settings.service_profile.ogrn),
                "bank_name": _display(settings.service_profile.bank_name),
                "bik": _display(settings.service_profile.bik),
                "settlement_account": _display(settings.service_profile.settlement_account),
                "correspondent_account": _display(settings.service_profile.correspondent_account),
                "tax_label": _display(settings.service_profile.tax_label),
                "payment_purpose": _display(settings.service_profile.payment_purpose),
                "brand_logo_data_uri": _brand_logo_data_uri(),
            },
            "document": document.to_dict(),
            "card": {
                "id": card.id,
                "heading": card.heading(),
                "title": _display(card.title),
                "description": _display(card.description),
            },
            "repair_order": {
                **repair_order_payload,
                "number_display": _display(order.number),
                "date_display": _date_display(order.date),
                "opened_at_display": _date_display(order.opened_at),
                "closed_at_display": _date_display(order.closed_at),
                "status_label": "Закрыт"
                if str(order.status).strip().lower() == "closed"
                else "Открыт",
                "payment_method_label": _display(repair_order_payload.get("payment_method_label")),
                "prepayment_display": total_paid_display,
                "reason_display": _display(order.reason),
                "reason_html": _line_breaks_html(order.reason),
                "client_information_html": _line_breaks_html(order.comment),
                "note_display": _display(order.note),
                "terms_summary_html": _repair_order_terms_summary_html(),
                "warranty_terms_html": _repair_order_warranty_terms_html(),
                "acceptance_terms_html": _vehicle_acceptance_terms_html(),
                "payment_summary": payment_summary_display,
            },
            "client": {
                "name": order.client,
                "phone": order.phone,
                "name_display": _display(order.client),
                "phone_display": _display(order.phone),
                **client_context,
            },
            "vehicle": {
                "display_name": _display(order.vehicle or card.vehicle_display()),
                "license_plate": order.license_plate,
                "license_plate_display": _display(order.license_plate),
                "vin": order.vin,
                "vin_display": _display(order.vin),
                "mileage": order.mileage,
                "mileage_display": _display(order.mileage),
            },
            "dates": {
                "document_date_display": _date_display(order.date or order.opened_at),
                "document_date_only_display": _date_only_display(order.date or order.opened_at),
                "opened_at_display": _date_display(order.opened_at),
                "closed_at_display": _date_display(order.closed_at),
                "generated_at_display": _date_display(utc_now_iso()),
            },
            "works": works,
            "materials": materials,
            "line_items": invoice_line_items_for_document,
            "parts_sale_items": materials,
            "issue_points": issue_points,
            "findings": findings,
            "recommendations": recommendations,
            "vehicle_acceptance_act": {
                "photo_fixation_yes": "ДА",
                "photo_fixation_no": "НЕТ",
                "estimated_cost_display": _money_display(payment_summary["base_total"]),
                "terms_html": _vehicle_acceptance_terms_html(),
            },
            "parts_sale": {
                "items": materials,
                "terms_html": _parts_sale_terms_html(),
                "buyer_display": _display(order.client),
                "description": "Продажа запасных частей и материалов без привязки к автомобилю.",
            },
            "inspection_sheet": {
                **inspection_form.to_dict(),
                "client_display": _display(inspection_form.client),
                "vehicle_display": _display(inspection_form.vehicle),
                "vin_or_plate_display": _display(inspection_form.vin_or_plate),
                "complaint_summary_display": _display(inspection_form.complaint_summary),
                "complaint_summary_html": _line_breaks_html(inspection_form.complaint_summary),
                "findings": findings,
                "recommendations": recommendations,
                "planned_works": inspection_planned_works,
                "planned_materials": inspection_planned_materials,
                "planned_work_rows": inspection_planned_work_rows,
                "planned_material_rows": inspection_planned_material_rows,
                "planned_works_count": len(inspection_planned_works),
                "planned_materials_count": len(inspection_planned_materials),
                "planned_work_rows_count": len(inspection_planned_work_rows),
                "planned_material_rows_count": len(inspection_planned_material_rows),
                "master_comment_display": _display(inspection_form.master_comment),
                "master_comment_html": _line_breaks_html(inspection_form.master_comment),
                "updated_at_display": _date_display(inspection_form.updated_at),
                "filled_by_display": _display(inspection_form.filled_by),
                "source_display": _display(inspection_form.source),
            },
            "completion_act": completion_act_context,
            "totals": {
                "works": order.works_total_amount(),
                "materials": order.materials_total_amount(),
                "subtotal": payment_summary["base_total"],
                "taxes": payment_summary["taxes_and_fees"],
                "grand": grand_total,
                "prepayment": payment_summary["total_paid"],
                "due": selected_due,
                "works_display": _money_display(order.works_total_amount()),
                "materials_display": _money_display(order.materials_total_amount()),
                "subtotal_display": payment_summary_display["base_total_display"],
                "cash_total": cash_total,
                "cash_total_display": _money_display(cash_total),
                "cash_total_ruble_display": _money_ruble_display(cash_total),
                "noncash_total": noncash_total,
                "noncash_total_display": _money_display(noncash_total),
                "noncash_total_ruble_display": _money_ruble_display(noncash_total),
                "noncash_taxes_and_fees": noncash_taxes_and_fees,
                "noncash_taxes_and_fees_display": _money_display(noncash_taxes_and_fees),
                "noncash_taxes_and_fees_ruble_display": _money_ruble_display(
                    noncash_taxes_and_fees
                ),
                "taxes_display": payment_summary_display["taxes_and_fees_display"],
                "grand_display": grand_total_display,
                "grand_ruble_display": _money_ruble_display(grand_total),
                "grand_words_display": grand_total_words_display,
                "prepayment_display": total_paid_display,
                "prepayment_ruble_display": _money_ruble_display(payment_summary["total_paid"]),
                "cash_like_prepayment": cash_like_prepayment,
                "cash_like_prepayment_ruble_display": _money_ruble_display(cash_like_prepayment),
                "cash_prepayment": cash_prepayment,
                "cash_prepayment_ruble_display": _money_ruble_display(cash_prepayment),
                "card_prepayment": card_prepayment,
                "card_prepayment_ruble_display": _money_ruble_display(card_prepayment),
                "cashless_prepayment": cashless_prepayment,
                "cashless_prepayment_ruble_display": _money_ruble_display(cashless_prepayment),
                "due_label": selected_due_label,
                "due_display": selected_due_display,
                "due_ruble_display": _money_ruble_display(selected_due),
                "due_words_display": selected_due_words_display,
                "cash_due": payment_summary["cash_due"],
                "base_total_display": payment_summary_display["base_total_display"],
                "base_total_ruble_display": _money_ruble_display(payment_summary["base_total"]),
                "base_paid_cash_display": payment_summary_display["base_paid_cash_display"],
                "base_paid_noncash_display": payment_summary_display["base_paid_noncash_display"],
                "base_remaining_display": payment_summary_display["base_remaining_display"],
                "cash_due_display": payment_summary_display["cash_due_display"],
                "cash_due_ruble_display": _money_ruble_display(payment_summary["cash_due"]),
                "noncash_due": payment_summary["noncash_due"],
                "noncash_due_display": payment_summary_display["noncash_due_display"],
                "noncash_due_ruble_display": _money_ruble_display(payment_summary["noncash_due"]),
                "taxes_and_fees_display": payment_summary_display["taxes_and_fees_display"],
                "taxes_and_fees_ruble_display": _money_ruble_display(
                    payment_summary["taxes_and_fees"]
                ),
                "total_paid_display": total_paid_display,
                "total_paid_ruble_display": _money_ruble_display(payment_summary["total_paid"]),
                **payment_summary_ruble_display,
                "has_taxes": payment_summary["taxes_and_fees"] != Decimal("0"),
                "has_prepayment": payment_summary["total_paid"] != Decimal("0"),
                "has_cash_like_prepayment": cash_like_prepayment != Decimal("0"),
                "has_cash_prepayment": cash_prepayment != Decimal("0"),
                "has_card_prepayment": card_prepayment != Decimal("0"),
                "has_cashless_prepayment": cashless_prepayment != Decimal("0"),
                "has_payment_summary": True,
            },
            "invoice": {
                "line_items": invoice_line_items_for_document,
                "tax_label": invoice_tax["label"],
                "tax_rate_display": invoice_tax["rate_display"],
                "subtotal": invoice_total,
                "subtotal_display": invoice_total_display,
                "vat": invoice_tax_amount,
                "vat_display": invoice_tax_display,
                "total": invoice_total,
                "total_display": invoice_total_display,
                "total_words_display": invoice_total_words_display,
                "prepayment": invoice_prepayment,
                "prepayment_display": _money_display(invoice_prepayment),
                "amount_due": invoice_amount_due,
                "amount_due_display": _money_display(invoice_amount_due),
                "amount_due_words_display": _money_words_display(invoice_amount_due),
                "has_prepayment": invoice_prepayment != Decimal("0"),
                "has_vat": invoice_tax["has_vat"] and invoice_tax_amount != Decimal("0"),
            },
            "regulated": regulated_context,
            "meta": {
                "warnings": warnings,
                "missing_fields": missing_fields,
                "works_count": len(inspection_planned_works)
                if document.id == "inspection_sheet" and inspection_planned_works
                else len(works),
                "materials_count": len(inspection_planned_materials)
                if document.id == "inspection_sheet" and inspection_planned_materials
                else len(materials),
            },
        }

    def _missing_fields(
        self,
        card: Card,
        order: RepairOrder,
        *,
        document: PrintDocumentDefinition,
        works: list[dict[str, Any]],
        materials: list[dict[str, Any]],
    ) -> list[str]:
        missing: list[str] = []
        regulated_documents = {"invoice_factura", "upd"}
        if not _normalize_text(order.client):
            missing.append("client")
        if document.id not in regulated_documents and not _normalize_text(order.phone):
            missing.append("phone")
        if document.id not in {"parts_sale", *regulated_documents} and not _normalize_text(
            order.vehicle or card.vehicle_display()
        ):
            missing.append("vehicle")
        if document.id not in {"parts_sale", *regulated_documents} and not _normalize_text(
            order.vin
        ):
            missing.append("vin")
        if document.id in regulated_documents:
            if not works and not materials:
                missing.append("line_items")
            return missing
        if (
            document.id in {"repair_order", "invoice", "invoice_factura", "upd", "completion_act"}
            and not works
        ):
            missing.append("works")
        if (
            document.id
            in {
                "repair_order",
                "invoice",
                "invoice_factura",
                "upd",
                "completion_act",
                "parts_sale",
            }
            and not materials
        ):
            missing.append("materials")
        return missing

    def _bullet_points(self, value: Any, fallback_source: Any = "") -> list[dict[str, str]]:
        text = _normalize_multiline(value, limit=6000) or _normalize_multiline(
            fallback_source, limit=6000
        )
        if not text:
            return []
        points: list[dict[str, str]] = []
        seen: set[str] = set()
        for chunk in _SENTENCE_SPLIT_RE.split(text):
            cleaned = _normalize_text(chunk, limit=320)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            points.append({"text": cleaned})
            if len(points) >= 8:
                break
        return points

    def _build_export_file_name(
        self,
        card: Card,
        selected_document_ids: list[str],
        *,
        document_payloads: list[dict[str, Any]] | None = None,
    ) -> str:
        raw_doc_part = "-".join(selected_document_ids[:3]) if selected_document_ids else "print"
        doc_part = self._safe_file_name_part(raw_doc_part, default="print", limit=120)
        resolved_number: Any = card.repair_order.number
        if selected_document_ids == ["completion_act"] and document_payloads:
            resolved_number = document_payloads[0].get("resolved_document_number")
        number = self._safe_file_name_part(resolved_number, default="draft", limit=64)
        return f"autostopcrm-{doc_part}-{number}.pdf"

    def _safe_file_name_part(self, value: Any, *, default: str, limit: int) -> str:
        text = _normalize_text(value, limit=limit)
        text = _UNSAFE_FILE_NAME_RE.sub("-", text)
        text = re.sub(r"\s+", "-", text)
        text = re.sub(r"-{2,}", "-", text).strip(" .-")
        return text[:limit].strip(" .-") or default
