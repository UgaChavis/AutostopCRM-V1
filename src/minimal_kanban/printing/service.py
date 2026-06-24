from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..models import Card, ClientProfile, parse_datetime, utc_now_iso
from ..repair_order import (
    REPAIR_ORDER_PAYMENT_METHOD_CASHLESS,
    RepairOrder,
    RepairOrderRow,
    repair_order_cashless_gross_value,
)
from .defaults import BUILTIN_PRINT_DOCUMENTS, PRINT_BASE_STYLES, builtin_template_records
from .models import (
    SUPPORTED_PRINT_DOCUMENT_TYPES,
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
_PAGE_BREAK_MARKER = "<!-- AUTOSTOPCRM_PAGE_BREAK -->"
_REGULATED_LANDSCAPE_DOCUMENT_TYPES = {"invoice_factura", "upd"}
_SENTENCE_SPLIT_RE = re.compile(r"[\n\r]+|(?<=[.!?])\s+")
_MONEY_QUANT = Decimal("0.01")
_INVOICE_VAT_RATE = Decimal("0.05")
_BRAND_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "autostop_brand_logo.png"
_RU_MONTHS_GENITIVE = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
_MONEY_UNITS_MALE = (
    "",
    "один",
    "два",
    "три",
    "четыре",
    "пять",
    "шесть",
    "семь",
    "восемь",
    "девять",
)
_MONEY_UNITS_FEMALE = (
    "",
    "одна",
    "две",
    "три",
    "четыре",
    "пять",
    "шесть",
    "семь",
    "восемь",
    "девять",
)
_MONEY_TEENS = (
    "десять",
    "одиннадцать",
    "двенадцать",
    "тринадцать",
    "четырнадцать",
    "пятнадцать",
    "шестнадцать",
    "семнадцать",
    "восемнадцать",
    "девятнадцать",
)
_MONEY_TENS = (
    "",
    "",
    "двадцать",
    "тридцать",
    "сорок",
    "пятьдесят",
    "шестьдесят",
    "семьдесят",
    "восемьдесят",
    "девяносто",
)
_MONEY_HUNDREDS = (
    "",
    "сто",
    "двести",
    "триста",
    "четыреста",
    "пятьсот",
    "шестьсот",
    "семьсот",
    "восемьсот",
    "девятьсот",
)
_MONEY_SCALES = (
    ("тысяча", "тысячи", "тысяч", True),
    ("миллион", "миллиона", "миллионов", False),
    ("миллиард", "миллиарда", "миллиардов", False),
    ("триллион", "триллиона", "триллионов", False),
)


class PrintModuleError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


@dataclass(slots=True)
class ManualDocumentProfile:
    card: Card
    client: ClientProfile | None = None
    request_text: str = ""


def _normalize_text(value: Any, *, limit: int = 4000) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _normalize_multiline(value: Any, *, limit: int = 120_000) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()[:limit]


def _normalize_document_type(value: Any) -> str:
    document_type = _normalize_text(value, limit=64)
    if document_type not in SUPPORTED_PRINT_DOCUMENT_TYPES:
        raise PrintModuleError(
            "validation_error",
            "Указан неподдерживаемый тип печатного документа.",
            details={"document_type": document_type},
        )
    return document_type


def _first_text(*values: Any, limit: int = 4000) -> str:
    for value in values:
        text = _normalize_text(value, limit=limit)
        if text:
            return text
    return ""


def _first_multiline(*values: Any, limit: int = 4000) -> str:
    for value in values:
        text = _normalize_multiline(value, limit=limit)
        if text:
            return text
    return ""


def _manual_table_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            row = dict(item)
        else:
            row = {"name": item}
        name = _normalize_text(row.get("name") or row.get("title"), limit=240)
        catalog_number = _normalize_text(
            row.get("catalog_number") or row.get("catalogNumber") or row.get("article"),
            limit=160,
        )
        quantity = _normalize_text(row.get("quantity") or row.get("qty") or "1", limit=40)
        price = _normalize_text(row.get("price") or row.get("unit_price") or "", limit=40)
        total = _normalize_text(row.get("total") or row.get("amount") or "", limit=40)
        if not name and not total:
            continue
        rows.append(
            {
                "name": name,
                "catalog_number": catalog_number,
                "quantity": quantity or "1",
                "price": price,
                "total": total,
            }
        )
        if len(rows) >= 100:
            break
    return rows


def _manual_vehicle_payload(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            "name": _first_text(
                value.get("name"),
                value.get("vehicle"),
                value.get("display_name"),
                value.get("model"),
                limit=160,
            ),
            "license_plate": _first_text(
                value.get("license_plate"),
                value.get("licensePlate"),
                value.get("plate"),
                limit=40,
            ),
            "vin": _first_text(value.get("vin"), value.get("VIN"), limit=80),
            "mileage": _first_text(
                value.get("mileage"),
                value.get("odometer"),
                value.get("run"),
                limit=40,
            ),
        }
    return {
        "name": _normalize_text(value, limit=160),
        "license_plate": "",
        "vin": "",
        "mileage": "",
    }


def _parse_manual_line_item_rows(values: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw_value in values:
        for raw_item in re.split(r"[;\n]+", raw_value):
            item = _normalize_text(raw_item, limit=300).rstrip(".")
            if not item:
                continue
            parts = [part.strip() for part in re.split(r"[|\t]", item) if part.strip()]
            if len(parts) >= 3:
                rows.append(
                    {
                        "name": _normalize_text(parts[0], limit=240),
                        "quantity": parts[1].replace(",", "."),
                        "price": parts[2].replace(",", "."),
                        "total": parts[3].replace(",", ".") if len(parts) >= 4 else "",
                    }
                )
            else:
                row_match = re.match(
                    r"(?P<name>.+?)\s+(?P<qty>\d+(?:[,.]\d+)?)\s*(?:x|х|\*)\s*(?P<price>\d+(?:[,.]\d+)?)$",
                    item,
                    flags=re.IGNORECASE,
                )
                if row_match:
                    rows.append(
                        {
                            "name": _normalize_text(row_match.group("name"), limit=240),
                            "quantity": row_match.group("qty").replace(",", "."),
                            "price": row_match.group("price").replace(",", "."),
                        }
                    )
                else:
                    rows.append({"name": item, "quantity": "1", "price": ""})
            if len(rows) >= 20:
                return rows
    return rows


def _parse_manual_line_items(text: str, label: str) -> list[dict[str, str]]:
    pattern = rf"{label}\s*:\s*(.+?)(?:\n|\.|$)"
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    return _parse_manual_line_item_rows([match.group(1)])


def _parse_manual_payment_rows(values: list[str]) -> list[dict[str, str]]:
    payments: list[dict[str, str]] = []
    for raw_value in values:
        for raw_item in re.split(r"[\n]+", raw_value):
            item = _normalize_text(raw_item, limit=300).rstrip(".")
            if not item:
                continue
            parts = [part.strip() for part in re.split(r"[|\t;]", item)]
            payments.append(
                {
                    "amount": parts[0] if parts else item,
                    "paid_at": parts[1] if len(parts) >= 2 else "",
                    "payment_method": parts[2] if len(parts) >= 3 else "",
                    "note": " · ".join(part for part in parts[3:] if part)
                    if len(parts) >= 4
                    else "",
                }
            )
            if len(payments) >= 20:
                return payments
    return payments


def _manual_text_field(fields: dict[str, str], *aliases: str) -> str:
    for alias in aliases:
        value = fields.get(alias)
        if value:
            return value
    return ""


def _manual_text_key(value: str) -> str:
    return _normalize_text(value, limit=64).lower().replace("ё", "е")


def _manual_tax_label_from_text(request_text: str) -> str:
    normalized = _normalize_text(request_text, limit=20_000).lower().replace("ё", "е")
    if not normalized:
        return ""
    if "без ндс" in normalized:
        return "Без НДС"
    vat_match = re.search(r"\bндс\s*(?:\(|:)?\s*(\d{1,2}(?:[,.]\d{1,2})?)\s*%?", normalized)
    if vat_match:
        return f"НДС ({vat_match.group(1).replace('.', ',')}%)"
    return ""


def _manual_value_is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, dict):
        return all(_manual_value_is_blank(item) for item in value.values())
    if isinstance(value, list):
        return all(_manual_value_is_blank(item) for item in value)
    return not _normalize_multiline(value, limit=4000)


def _merge_manual_document_payload(parsed: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(parsed)
    for key, value in raw.items():
        current = merged.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            merged[key] = _merge_manual_document_payload(current, value)
            continue
        if _manual_value_is_blank(value):
            merged.setdefault(key, value)
            continue
        merged[key] = value
    return merged


def _parse_manual_document_text(request_text: str) -> dict[str, Any]:
    text = _normalize_multiline(request_text, limit=20_000)
    if not text:
        return {}
    section_by_key = {
        "работы": "works",
        "работа": "works",
        "услуги": "works",
        "материалы": "materials",
        "запчасти": "materials",
        "детали": "materials",
        "оплаты": "payments",
        "оплата": "payments",
        "платежи": "payments",
    }
    fields: dict[str, str] = {}
    sections: dict[str, list[str]] = {"works": [], "materials": [], "payments": []}
    current_section = ""
    for raw_line in text.split("\n"):
        line = _normalize_text(raw_line, limit=1000)
        if not line:
            continue
        key_match = re.match(r"^(?P<key>[^:：]{1,40})\s*[:：]\s*(?P<value>.*)$", line)
        if key_match:
            key = _manual_text_key(key_match.group("key"))
            value = _normalize_text(key_match.group("value"), limit=1000)
            section = section_by_key.get(key)
            if section:
                current_section = section
                if value:
                    sections[section].append(value)
                continue
            fields[key] = value
            current_section = ""
            continue
        if current_section:
            sections[current_section].append(line)
    number_match = re.search(
        r"(?:№|номер|счет|счёт|акт|заказ-наряд|зн)\s*[:#№-]?\s*([A-Za-zА-Яа-я0-9/_-]{2,40})",
        text,
        flags=re.IGNORECASE,
    )
    date_match = re.search(r"\b(\d{1,2}[./]\d{1,2}[./]\d{2,4})\b", text)
    client_match = re.search(r"\bдля\s+(.+?)(?:\.|\n|$)", text, flags=re.IGNORECASE)
    client_name = _first_text(
        _manual_text_field(fields, "клиент", "заказчик", "покупатель", "контрагент"),
        client_match.group(1).strip() if client_match else "",
        limit=160,
    )
    return {
        "document_number": _first_text(
            _manual_text_field(fields, "номер", "№", "документ", "счет", "счёт", "акт"),
            number_match.group(1) if number_match else "",
            limit=40,
        ),
        "document_date": _first_text(
            _manual_text_field(fields, "дата"),
            date_match.group(1) if date_match else "",
            limit=32,
        ),
        "tax_label": _first_text(
            _manual_text_field(fields, "ндс", "налог", "налоговый режим", "tax_label"),
            _manual_tax_label_from_text(text),
            limit=48,
        ),
        "client": {
            "display_name": client_name,
            "legal_name": _manual_text_field(fields, "юр лицо", "юридическое лицо"),
            "phone": _manual_text_field(fields, "телефон", "тел", "phone"),
            "inn": _manual_text_field(fields, "инн"),
            "kpp": _manual_text_field(fields, "кпп"),
            "checking_account": _manual_text_field(fields, "р/с", "расчетный счет"),
            "bank_name": _manual_text_field(fields, "банк"),
            "bik": _manual_text_field(fields, "бик"),
            "correspondent_account": _manual_text_field(fields, "к/с", "корреспондентский счет"),
            "legal_address": _manual_text_field(fields, "адрес", "юридический адрес"),
        },
        "vehicle": {
            "name": _manual_text_field(fields, "автомобиль", "авто", "машина"),
            "license_plate": _manual_text_field(fields, "госномер", "номер авто", "гос номер"),
            "vin": _manual_text_field(fields, "vin", "вин"),
            "mileage": _manual_text_field(fields, "пробег"),
        },
        "works": _parse_manual_line_item_rows(sections["works"])
        or _parse_manual_line_items(text, "работы"),
        "materials": _parse_manual_line_item_rows(sections["materials"])
        or _parse_manual_line_items(text, "материалы"),
        "payments": _parse_manual_payment_rows(sections["payments"]),
        "comment": _first_multiline(_manual_text_field(fields, "комментарий"), text, limit=4000),
    }


def _parse_decimal(value: Any) -> Decimal | None:
    raw = "" if value is None else str(value).strip().replace(" ", "").replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def _invoice_tax_payload(order: RepairOrder) -> dict[str, Any]:
    raw_label = _normalize_text(getattr(order, "tax_label", ""), limit=48)
    if not raw_label:
        return {
            "label": "НДС (5%)",
            "rate_display": "5%",
            "rate": _INVOICE_VAT_RATE,
            "has_vat": True,
        }
    normalized = raw_label.lower().replace("ё", "е")
    if "без ндс" in normalized:
        return {
            "label": "Без НДС",
            "rate_display": "0%",
            "rate": Decimal("0"),
            "has_vat": False,
        }
    percent_match = re.search(r"(\d{1,2}(?:[,.]\d{1,2})?)\s*%", normalized)
    if percent_match:
        try:
            rate = Decimal(percent_match.group(1).replace(",", ".")) / Decimal("100")
        except InvalidOperation:
            rate = _INVOICE_VAT_RATE
        return {
            "label": raw_label,
            "rate_display": f"{percent_match.group(1).replace('.', ',')}%",
            "rate": rate,
            "has_vat": rate != Decimal("0"),
        }
    return {
        "label": raw_label,
        "rate_display": "5%",
        "rate": _INVOICE_VAT_RATE,
        "has_vat": True,
    }


def _money_display(value: Any, *, trim_kopeks: bool = False, currency: bool = False) -> str:
    parsed = _parse_decimal(value)
    if parsed is None:
        return "—"
    quantized = parsed.quantize(_MONEY_QUANT)
    text = format(quantized, "f")
    whole, dot, fraction = text.partition(".")
    grouped_whole = f"{int(whole):,}".replace(",", " ")
    if dot and not (trim_kopeks and fraction[:2] == "00"):
        result = f"{grouped_whole},{fraction[:2]}"
    else:
        result = grouped_whole
    return f"{result} ₽" if currency else result


def _money_ruble_display(value: Any) -> str:
    return _money_display(value, trim_kopeks=True, currency=True)


def _plural_form(number: int, forms: tuple[str, str, str]) -> str:
    n = abs(int(number))
    if 11 <= (n % 100) <= 14:
        return forms[2]
    last = n % 10
    if last == 1:
        return forms[0]
    if 2 <= last <= 4:
        return forms[1]
    return forms[2]


def _triplet_words(number: int, *, feminine: bool = False) -> str:
    number = max(0, min(999, int(number)))
    words: list[str] = []
    hundreds = number // 100
    tens_units = number % 100
    tens = tens_units // 10
    units = tens_units % 10
    if hundreds:
        words.append(_MONEY_HUNDREDS[hundreds])
    if 10 <= tens_units <= 19:
        words.append(_MONEY_TEENS[tens_units - 10])
    else:
        if tens:
            words.append(_MONEY_TENS[tens])
        if units:
            words.append((_MONEY_UNITS_FEMALE if feminine else _MONEY_UNITS_MALE)[units])
    return " ".join(words)


def _integer_words(number: int) -> str:
    number = max(0, int(number))
    if number == 0:
        return "ноль"
    chunks: list[str] = []
    group_index = 0
    while number > 0:
        triplet = number % 1000
        if triplet:
            feminine = bool(
                group_index == 1
                or (
                    _MONEY_SCALES[group_index - 1][3]
                    if group_index > 1 and group_index - 1 < len(_MONEY_SCALES)
                    else False
                )
            )
            words = _triplet_words(triplet, feminine=feminine)
            if group_index > 0 and group_index - 1 < len(_MONEY_SCALES):
                scale_forms = _MONEY_SCALES[group_index - 1][:3]
                words = f"{words} {_plural_form(triplet, scale_forms)}".strip()
            chunks.append(words)
        number //= 1000
        group_index += 1
    return " ".join(reversed(chunks)).strip()


def _money_words_display(value: Any) -> str:
    parsed = _parse_decimal(value)
    if parsed is None:
        return "—"
    quantized = parsed.quantize(_MONEY_QUANT)
    sign = "минус " if quantized < 0 else ""
    quantized = abs(quantized)
    whole = int(quantized)
    cents = int((quantized - whole) * 100)
    rubles = _integer_words(whole)
    ruble_word = _plural_form(whole, ("рубль", "рубля", "рублей"))
    kopeks = f"{cents:02d}"
    kopek_word = _plural_form(cents, ("копейка", "копейки", "копеек"))
    text = f"{sign}{rubles} {ruble_word} {kopeks} {kopek_word}"
    return text[:1].upper() + text[1:] if text else "—"


def _line_breaks_html(value: Any, *, fallback: str = "—") -> str:
    text = _normalize_multiline(value, limit=20_000)
    if not text:
        return html.escape(fallback)
    return "<br>".join(html.escape(line) for line in text.split("\n"))


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
    if kpp_text.lower().replace("ё", "е") in {"не применяется для ип", "не применяется"}:
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
    if section == "works":
        return {"unit_code_display": "—", "unit_display": "н/ч"}
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
    rate = tax["rate"] if tax.get("has_vat") else Decimal("0")
    vat = _round_money(subtotal * rate)
    total_with_tax = _round_money(subtotal + vat)
    unit_payload = _regulated_unit_payload(item)
    return {
        **item,
        **unit_payload,
        "index": index + 1,
        "product_code_display": "—",
        "name": _display(item.get("name"), limit=260),
        "quantity_display": _display(item.get("quantity_display") or item.get("quantity")),
        "price_display": _money_display(price),
        "subtotal": subtotal,
        "subtotal_display": _money_display(subtotal),
        "tax_rate_display": tax["rate_display"] if tax.get("has_vat") else "Без НДС",
        "vat": vat,
        "vat_display": _money_display(vat) if tax.get("has_vat") else "Без НДС",
        "total_with_tax": total_with_tax,
        "total_with_tax_display": _money_display(total_with_tax),
        "excise_display": "Без акциза",
        "line_code_display": "—",
        "country_display": "—",
        "country_code_display": "—",
        "country_name_display": "—",
        "customs_declaration_display": "—",
    }


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
    rows = [
        _regulated_line_item_dict(item, index=index, tax=tax)
        for index, item in enumerate(line_items)
    ]
    subtotal = _round_money(
        sum((_parse_decimal(item.get("subtotal")) or Decimal("0") for item in rows), Decimal("0"))
    )
    vat = _round_money(
        sum((_parse_decimal(item.get("vat")) or Decimal("0") for item in rows), Decimal("0"))
    )
    total_with_tax = _round_money(subtotal + vat)
    service_profile = settings.service_profile
    seller_legal_name = _individual_entrepreneur_display(
        service_profile.legal_name,
        fallback=_display(service_profile.company_name),
    )
    seller_signer = _regulated_override_text(
        overrides,
        "seller_signer",
        fallback=_service_signer_display(service_profile.legal_name, service_profile.company_name),
        limit=120,
    )
    seller_position = _regulated_override_text(
        overrides,
        "seller_position",
        fallback="ИП" if "Индивидуальный предприниматель" in seller_legal_name else "Руководитель",
        limit=120,
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
        fallback="Передача на территории сервиса",
        limit=240,
    )
    buyer_position = _regulated_override_text(
        overrides,
        "buyer_position",
        fallback=client_position or "Представитель покупателя",
        limit=120,
    )
    buyer_signer = _regulated_override_text(
        overrides,
        "buyer_signer",
        fallback=client_contact or "—",
        limit=120,
    )
    shipper = _regulated_override_text(
        overrides,
        "shipper",
        fallback=f"{seller_legal_name}, {service_profile.address}",
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
    return {
        "document_number_display": document_number,
        "document_date_display": document_date,
        "document_date_long_display": document_date_long,
        "correction_display": "—",
        "correction_date_display": "—",
        "linked_invoice_display": linked_invoice,
        "seller_name_display": seller_legal_name,
        "seller_address_display": _display(service_profile.address, limit=300),
        "seller_inn_kpp_display": _inn_kpp_display(service_profile.inn, service_profile.kpp),
        "seller_registration_display": _service_registration_display(service_profile.ogrn),
        "seller_position_display": seller_position,
        "seller_signer_display": seller_signer,
        "shipper_display": shipper,
        "consignee_display": consignee,
        "payment_document_display": payment_document,
        "shipment_document_display": shipment_document,
        "upd_shipment_document_display": upd_shipment_document,
        "buyer_name_display": buyer_name,
        "buyer_address_display": buyer_address,
        "buyer_inn_kpp_display": _inn_kpp_display(buyer_inn, buyer_kpp),
        "buyer_position_display": buyer_position,
        "buyer_signer_display": buyer_signer,
        "currency_display": "Российский рубль, 643",
        "state_contract_display": "—",
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


def _safe_json_read(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return deepcopy(default)


def _safe_json_write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@lru_cache(maxsize=1)
def _brand_logo_data_uri() -> str:
    try:
        data = _BRAND_LOGO_PATH.read_bytes()
    except OSError:
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
        "unit_display": "усл. ед." if section == "works" else "шт.",
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

    def __init__(self, base_dir: Path) -> None:
        self._root_dir = Path(base_dir) / "printing"
        self._root_dir.mkdir(parents=True, exist_ok=True)
        self._settings_path = self._root_dir / _SETTINGS_FILE_NAME
        self._templates_path = self._root_dir / _TEMPLATES_FILE_NAME
        self._inspection_sheet_forms_path = self._root_dir / _INSPECTION_SHEET_FORMS_FILE_NAME
        self._builtin_documents = {item.id: item for item in BUILTIN_PRINT_DOCUMENTS}
        self._builtin_templates = {item.id: item for item in builtin_template_records()}

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
        resolved_active = self._resolved_active_document_id(active_document_id, selected_ids)
        documents_payload: list[dict[str, Any]] = []
        for document_id in selected_ids:
            document = self._document_definition(document_id)
            template = self._resolve_template(
                document_type=document_id,
                template_id=(selected_template_ids or {}).get(document_id, ""),
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
                    template_overrides=template_overrides,
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
        document_payloads = [
            self._rendered_document_payload(
                card,
                order,
                self._document_definition(document_id),
                self._resolve_template(
                    document_type=document_id,
                    template_id=(selected_template_ids or {}).get(document_id, ""),
                    settings=settings,
                ),
                client=client,
                settings=settings,
                template_overrides=template_overrides,
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
            )
        except PdfRenderError as exc:
            raise PrintModuleError("pdf_error", str(exc), status_code=500) from exc
        file_name = self._build_export_file_name(card, selected_ids)
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
        document_payloads = [
            self._rendered_document_payload(
                card,
                order,
                self._document_definition(document_id),
                self._resolve_template(
                    document_type=document_id,
                    template_id=(selected_template_ids or {}).get(document_id, ""),
                    settings=settings,
                ),
                client=client,
                settings=settings,
                template_overrides=template_overrides,
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
        normalized_name = _normalize_text(name, limit=120)
        normalized_content = _normalize_multiline(content, limit=200_000)
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
            "template_count": len(template_map.get(document.id, [])),
            "is_default_selected": document.id == "repair_order",
            "supports_form_fill": document.id == "inspection_sheet",
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
        if template_overrides and document.id in template_overrides:
            effective_template = PrintTemplateRecord(
                id="preview:override",
                document_type=document.id,
                name="Предпросмотр шаблона",
                content=_normalize_multiline(template_overrides.get(document.id, ""), limit=200_000)
                or template.content,
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

    def _normalized_document_ids(self, value: list[str] | None) -> list[str]:
        normalized: list[str] = []
        for raw in value or ["repair_order"]:
            candidate = _normalize_text(raw, limit=64)
            if candidate in self._builtin_documents and candidate not in normalized:
                normalized.append(candidate)
        return normalized or ["repair_order"]

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

    def _read_custom_templates(self) -> list[PrintTemplateRecord]:
        raw = _safe_json_read(self._templates_path, default=[])
        if not isinstance(raw, list):
            return []
        templates: list[PrintTemplateRecord] = []
        for item in raw:
            record = PrintTemplateRecord.from_dict(item)
            if (
                record is not None
                and not record.is_builtin
                and record.document_type in SUPPORTED_PRINT_DOCUMENT_TYPES
            ):
                templates.append(record)
        return templates

    def _write_custom_templates(self, records: list[PrintTemplateRecord]) -> None:
        payload = [record.to_dict() for record in records if not record.is_builtin]
        _safe_json_write(self._templates_path, payload)

    def _templates_by_document_type(
        self, *, settings: PrintModuleSettings
    ) -> dict[str, list[PrintTemplateRecord]]:
        combined = list(self._builtin_templates.values()) + self._read_custom_templates()
        grouped: dict[str, list[PrintTemplateRecord]] = {
            document_type: [] for document_type in SUPPORTED_PRINT_DOCUMENT_TYPES
        }
        for record in combined:
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
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
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
        invoice_tax_amount = _round_money(
            invoice_total * invoice_tax["rate"] / (Decimal("1") + invoice_tax["rate"])
        )
        invoice_tax_display = _money_display(invoice_tax_amount)
        invoice_total_display = _money_display(invoice_total)
        invoice_total_words_display = _money_words_display(invoice_total)
        invoice_prepayment = payment_summary["total_paid"]
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
        if not _normalize_text(order.client):
            missing.append("client")
        if not _normalize_text(order.phone):
            missing.append("phone")
        if document.id not in {"parts_sale"} and not _normalize_text(
            order.vehicle or card.vehicle_display()
        ):
            missing.append("vehicle")
        if document.id not in {"parts_sale"} and not _normalize_text(order.vin):
            missing.append("vin")
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

    def _build_export_file_name(self, card: Card, selected_document_ids: list[str]) -> str:
        doc_part = "-".join(selected_document_ids[:3]) if selected_document_ids else "print"
        number = _normalize_text(card.repair_order.number, limit=32) or "draft"
        return f"autostopcrm-{doc_part}-{number}.pdf"
