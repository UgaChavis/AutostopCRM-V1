from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..models import Card, ClientProfile
from .errors import PrintModuleError
from .models import SUPPORTED_PRINT_DOCUMENT_TYPES


@dataclass(slots=True)
class ManualDocumentProfile:
    card: Card
    client: ClientProfile | None = None
    request_text: str = ""


def _normalize_text(value: Any, *, limit: int = 4000) -> str:
    raw = "" if value is None or value is False else value
    return " ".join(str(raw).strip().split())[:limit]


def _normalize_multiline(value: Any, *, limit: int = 120_000) -> str:
    raw = "" if value is None or value is False else value
    return str(raw).replace("\r\n", "\n").replace("\r", "\n").strip()[:limit]


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


def _first_present(mapping: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _manual_table_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            row = dict(item)
        else:
            row = {"name": item}
        name = _normalize_text(_first_present(row, "name", "title"), limit=240)
        catalog_number = _normalize_text(
            _first_present(row, "catalog_number", "catalogNumber", "article"),
            limit=160,
        )
        quantity = _normalize_text(_first_present(row, "quantity", "qty", default="1"), limit=40)
        price = _normalize_text(_first_present(row, "price", "unit_price"), limit=40)
        total = _normalize_text(_first_present(row, "total", "amount"), limit=40)
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
