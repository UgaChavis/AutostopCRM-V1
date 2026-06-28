from __future__ import annotations

import html
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from ..repair_order import RepairOrder
from .manual_documents import _normalize_multiline, _normalize_text

_MONEY_QUANT = Decimal("0.01")
_MONEY_ABS_MAX = Decimal("999999999999999.99")
_INVOICE_VAT_RATE = Decimal("0.05")
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


def _parse_decimal(value: Any) -> Decimal | None:
    raw = "" if value is None else str(value).strip().replace(" ", "").replace(",", ".")
    if not raw:
        return None
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed.copy_abs() > _MONEY_ABS_MAX:
        return None
    return parsed


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
    quantized = _round_money(parsed)
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
    quantized = _round_money(parsed)
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
