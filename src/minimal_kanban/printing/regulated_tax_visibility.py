"""Prove custom regulated documents visibly use their canonical VAT fields."""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

from ..services.document_money import resolve_document_tax
from .invoice_tax_visibility import _TextLocations
from .template_engine import TemplateRenderError, render_template
from .template_visibility import visible_print_text

_EXPLICIT_RATE = re.compile(
    r"\b(?:НДС|налоговая\s+ставка)\s*[:=–—(,\-]?\s*"
    r"(?:(?:по\s+)?ставк[аеиу]\s*[:=–—-]?\s*)?(\d+(?:[.,]\d+)?)\s*%",
    re.IGNORECASE,
)
_RATE_BEFORE_VAT = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*%\s*\)?\s*НДС\b",
    re.IGNORECASE,
)
_BARE_RATE = re.compile(r"\s*(\d+(?:[.,]\d+)?)\s*%\s*[.;]?\s*")
_WITHOUT_VAT = re.compile(
    r"\s*(?:в\s+том\s+числе\s*[:=–—-]?\s*)?"
    r"(?:(?:НДС|налоговая\s+ставка|налоговый\s+режим|ставка)\s*[:=–—-]?\s*)?"
    r"без\s+НДС\s*(?:[:=–—-]?\s*0(?:[.,]00)?\s*(?:руб\.?|₽)?)?\s*[.;]?\s*",
    re.IGNORECASE,
)


def _literal_conflict(blocks: list[str], expected_rate: Decimal) -> bool:
    for block in blocks:
        # Do not join independent blocks: a surcharge note ending in 15%
        # followed by the VAT summary does not mean "15% НДС".
        if any(
            Decimal(match[1].replace(",", ".")) != expected_rate
            for pattern in (_EXPLICIT_RATE, _RATE_BEFORE_VAT)
            for match in pattern.finditer(block)
        ):
            return True
        if re.search(r"(?:НДС\s+не\s+облагается|не\s+облагается\s+НДС)", block, re.IGNORECASE):
            return True
        rate = _BARE_RATE.fullmatch(block)
        if rate and Decimal(rate[1].replace(",", ".")) != expected_rate:
            return True
        # A standalone status conflicts; ordinary net-price/base headings such
        # as "Цена без НДС" and "Стоимость без НДС" remain legitimate.
        if _WITHOUT_VAT.fullmatch(block):
            return True
    return False


def regulated_tax_summary_visible(content: str, context: dict) -> bool:
    """Require canonical rate/amount pairs, retaining field and row provenance.

    A table must show each canonical row's pair in the same text block or HTML
    table row. A summary-only template may show the document's canonical pair.
    Matching literals, unrelated fields and CSS-hidden values cannot qualify.
    """
    regulated = context.get("regulated", {})
    tax = resolve_document_tax(regulated.get("tax_label", ""))
    if not tax["known"] or not tax["has_vat"]:
        return False
    rows = {id(row): row for row in regulated.get("rows", [])}
    prefix = "\ue000" + uuid.uuid4().hex + "\ue001"
    emissions = []
    uses_rows = False

    def trace(path, value, stack, text):
        nonlocal uses_rows
        row_id = next((id(item) for item in stack if id(item) in rows), None)
        uses_rows |= row_id is not None and path.startswith("regulated.rows.")
        kind = ""
        owner = row_id
        if path in {"regulated.tax_label", "regulated.tax_rate_display"}:
            kind = "rate"
        elif path == "regulated.vat_display":
            kind, owner = "amount", None
        elif row_id is not None and path == "regulated.rows.tax_rate_display":
            kind = "rate"
        elif row_id is not None and path == "regulated.rows.vat_display":
            kind = "amount"
        is_tax = path.rsplit(".", 1)[-1] in {"tax_label", "tax_rate_display"}
        if not kind and not is_tax:
            return text
        resolved = resolve_document_tax(value) if is_tax else tax
        conflict = is_tax and (
            not resolved["known"] or not resolved["has_vat"] or resolved["rate"] != tax["rate"]
        )
        index = len(emissions)
        emissions.append((kind, owner, str(value), conflict))
        return f"{prefix}V{index}S{prefix}{text}{prefix}V{index}E{prefix}"

    try:
        document = _TextLocations(prefix)
        document.feed(render_template(content, context, value_renderer=trace))
        document.close()
        visible = visible_print_text("".join(document.output))
    except (TemplateRenderError, ValueError, RecursionError):
        return False
    escaped = re.escape(prefix)
    text_pattern = re.compile(
        escaped + r"T(\d+)S" + escaped + r"(.*?)" + escaped + r"T\1E" + escaped, re.DOTALL
    )
    field_pattern = re.compile(
        escaped + r"V(\d+)S" + escaped + r"(.*?)" + escaped + r"V\1E" + escaped, re.DOTALL
    )
    value_marker = re.compile(escaped + r"V\d+[SE]" + escaped)
    blocks = {}
    pairs = {}
    for match in text_pattern.finditer(visible):
        group, html_row = document.locations[int(match[1])]
        blocks.setdefault(group, []).append(value_marker.sub("", match[2]))
        for field in field_pattern.finditer(match[2]):
            kind, owner, expected, conflict = emissions[int(field[1])]
            if not field[2].strip() or field[2] != expected:
                continue
            if conflict:
                return False
            if kind:
                pairs.setdefault((owner, "block", group), set()).add(kind)
                if html_row is not None:
                    pairs.setdefault((owner, "row", html_row), set()).add(kind)
    if _literal_conflict(["".join(parts) for parts in blocks.values()], tax["rate"] * 100):
        return False
    paired_owners = {key[0] for key, kinds in pairs.items() if kinds == {"rate", "amount"}}
    return set(rows) <= paired_owners if uses_rows else None in paired_owners
