"""Prove a custom invoice visibly prints its canonical included-VAT summary.

The check never edits a saved template or appends a second tax row. It accepts
ordinary text blocks and table rows, using the same conservative print-CSS
proof as the monetary-note guard. Unsupported layouts fail closed.
"""

from __future__ import annotations

import html
import re
import uuid
from decimal import Decimal
from html.parser import HTMLParser

from .template_engine import TemplateRenderError, render_template
from .template_visibility import visible_print_text

_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_BLOCK = frozenset(
    "html body address article aside blockquote dd div dl dt fieldset figcaption "
    "figure footer form h1 h2 h3 h4 h5 h6 header li main nav ol p pre section "
    "table tbody td tfoot th thead tr ul".split()
)
_INCLUDED = r"\bВ\s+том\s+числе\s*:?\s*"
_VAT_RATE = re.compile(r"\bНДС\s*[:=–—-]?\s*\(?\s*(\d+(?:[.,]\d+)?)\s*%", re.IGNORECASE)


def _conflicting_tax_status(text: str, expected_label: str) -> bool:
    """Compare explicit rates, without classifying ambiguous ``без НДС`` prose.

    That phrase also names a legitimate net-price column or tax-base subtotal,
    so arbitrary HTML cannot safely establish whether it is a tax status.
    """
    canonical = _VAT_RATE.search(expected_label)
    if canonical is None:
        return True
    expected_rate = Decimal(canonical[1].replace(",", "."))
    return any(
        Decimal(match[1].replace(",", ".")) != expected_rate for match in _VAT_RATE.finditer(text)
    )


class _TextLocations(HTMLParser):
    """Keep text provenance without adding elements that would change selectors."""

    def __init__(self, prefix: str):
        super().__init__(convert_charrefs=True)
        self.prefix = prefix
        self.output: list[str] = []
        self.stack = [(0, "body")]
        self.epochs = {0: 0}
        self.nodes = 0
        self.locations: list[tuple[tuple[int, int], int | None]] = []

    def _block(self):
        return next(node for node, tag in reversed(self.stack) if tag in _BLOCK)

    def _boundary(self):
        node = self._block()
        self.epochs[node] = self.epochs.get(node, 0) + 1

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 12000 or len(self.stack) > 128:
            raise ValueError("Print markup exceeds static verification limits")
        if tag in _BLOCK or tag in {"br", "hr"}:
            self._boundary()
        self.output.append(self.get_starttag_text())
        if tag not in _VOID:
            self.stack.append((self.nodes, tag))
            self.epochs[self.nodes] = 0

    def handle_startendtag(self, tag, attrs):
        size = len(self.stack)
        self.handle_starttag(tag, attrs)
        del self.stack[size:]
        if tag in _BLOCK:
            self._boundary()

    def handle_endtag(self, tag):
        self.output.append(f"</{tag}>")
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index][1] == tag:
                del self.stack[index:]
                if tag in _BLOCK:
                    self._boundary()
                return

    def handle_data(self, data):
        if self.stack[-1][1] in {"style", "script"}:
            self.output.append(data)
            return
        node = self._block()
        row = next((node for node, tag in reversed(self.stack) if tag == "tr"), None)
        index = len(self.locations)
        self.locations.append(((node, self.epochs[node]), row))
        self.output.append(
            f"{self.prefix}T{index}S{self.prefix}"
            f"{html.escape(data, quote=False)}"
            f"{self.prefix}T{index}E{self.prefix}"
        )

    def handle_comment(self, data):
        self.output.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.output.append(f"<!{decl}>")

    def handle_pi(self, data):
        self.output.append(f"<?{data}>")


def invoice_tax_summary_visible(content: str, context: dict) -> bool:
    """Require visible canonical label/amount with an adjacent included-VAT label.

    The caller supplies the effective invoice tax policy. The label must follow
    ``В том числе`` in the same text block; the amount must follow in that block
    or another cell of the same table row. Scalar template sections are traced
    by their resolved canonical paths, so neither literals nor matching values
    from unrelated fields substitute for the invoice fields. An explicit visible
    different VAT percentage blocks an otherwise valid row.
    """
    invoice = context.get("invoice")
    if not isinstance(invoice, dict):
        return False
    expected = {
        f"invoice.{name}": str(invoice.get(name) or "") for name in ("tax_label", "vat_display")
    }
    if not all(value.strip() for value in expected.values()):
        return False
    prefix = "\ue000" + uuid.uuid4().hex + "\ue001"
    emissions: list[str] = []

    def trace(path, value, stack, text):
        if path not in expected or str(value) != expected[path]:
            return text
        index = len(emissions)
        emissions.append(path)
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
    blocks: dict[tuple[int, int], list[str]] = {}
    offsets: dict[tuple[int, int], int] = {}
    amount_groups: dict[tuple[int, int], int] = {}
    amount_rows: dict[int, int] = {}
    fields = []
    plain_text = []
    previous_group = None
    value_marker = re.compile(escaped + r"V\d+[SE]" + escaped)
    for text_match in text_pattern.finditer(visible):
        text_index = int(text_match[1])
        group, row = document.locations[text_index]
        text = text_match[2]
        if group != previous_group:
            plain_text.append("\n")
            previous_group = group
        plain_text.append(value_marker.sub("", text))
        offset = offsets.get(group, 0)
        blocks.setdefault(group, []).append(text)
        offsets[group] = offset + len(text)
        for field_match in field_pattern.finditer(text):
            path = emissions[int(field_match[1])]
            if field_match[2] != expected[path]:
                continue
            start, end = offset + field_match.start(), offset + field_match.end()
            if path == "invoice.vat_display":
                amount_groups[group] = start
                if row is not None:
                    amount_rows[row] = text_index
            else:
                fields.append((group, row, text_index, start, end))
    if _conflicting_tax_status("".join(plain_text), expected["invoice.tax_label"]):
        return False
    included_pattern = re.compile(
        _INCLUDED + r"(?=" + escaped + r"V\d+S" + escaped + r")", re.IGNORECASE
    )
    included = {
        group: {match.end() for match in included_pattern.finditer("".join(parts))}
        for group, parts in blocks.items()
    }
    for group, row, text_index, start, end in fields:
        if start not in included[group]:
            continue
        if amount_groups.get(group, -1) >= end:
            return True
        if row is not None and amount_rows.get(row, -1) > text_index:
            return True
    return False
