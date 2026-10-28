"""Validate money placeholders and the rendered location of rounding notes."""

from __future__ import annotations

import re
import uuid
from html.parser import HTMLParser

from .template_engine import render_template, template_value_paths

_MONEY_FIELD = re.compile(
    r"(?:^|_)(?:price|total|subtotal|sum|amount|vat|prepayment|due|paid|gross|base|remaining|rounding_adjustment)(?:_|$)"
)
_QUANTITY_FIELD = re.compile(r"(?:^|_)quantity(?:_|$)")
_INVISIBLE_STYLE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*(?:hidden|collapse)|"
    r"opacity\s*:\s*(?:0(?:\.0*)?|\.0+)\s*(?:!important\s*)?(?:;|$))"
)


def money_template_fields(content: str) -> tuple[bool, bool]:
    has_money = raw = False
    for path in template_value_paths(content):
        parts = [part.strip() for part in path.split(".")]
        field = parts[-1]
        monetary = any(_MONEY_FIELD.search(part) for part in parts)
        has_money |= monetary or field == "rounding_notes_html"
        if (
            field != "rounding_notes_html"
            and not field.endswith("_display")
            and not field.startswith("has_")
            and not field.endswith("_rate")
            and (monetary or _QUANTITY_FIELD.search(field))
        ):
            raw = True
    return has_money, raw


class _VisibleText(HTMLParser):
    _void = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )
    _hidden = frozenset({"head", "script", "style", "template", "noscript"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        hidden = bool(
            self.stack
            and self.stack[-1][1]
            or tag in self._hidden
            or "hidden" in attributes
            or _INVISIBLE_STYLE.search((attributes.get("style") or "").lower())
        )
        if tag not in self._void:
            self.stack.append((tag, hidden))

    def handle_startendtag(self, tag, attrs):
        before = len(self.stack)
        self.handle_starttag(tag, attrs)
        del self.stack[before:]

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if not self.stack or not self.stack[-1][1]:
            self.text.append(data)


def rounding_notes_present(
    content: str, context: dict, rows: list[dict], *, settlement: bool
) -> bool:
    required = {
        id(row): row["rounding_adjustment_display"]
        for row in rows
        if row.get("has_rounding_adjustment")
    }
    if not required and not settlement:
        return True
    prefix = "\ue000" + uuid.uuid4().hex + "\ue001"
    emissions = []

    def trace(path, value, stack, text):
        index = len(emissions)
        row_id = next((id(item) for item in stack if id(item) in required), None)
        emissions.append((path.strip().split(".")[-1].strip(), row_id, value))
        return f"{prefix}{index}S{prefix}{text}{prefix}{index}E{prefix}"

    parser = _VisibleText()
    parser.feed(render_template(content, context, value_renderer=trace))
    parser.close()
    visible = "".join(parser.text)
    found = {
        int(match.group(1))
        for match in re.finditer(
            re.escape(prefix)
            + r"(\d+)S"
            + re.escape(prefix)
            + r"(.+?)"
            + re.escape(prefix)
            + r"\1E"
            + re.escape(prefix),
            visible,
            re.DOTALL,
        )
        if match.group(2).strip()
    }
    covered = set()
    settlement_found = False
    for index in found:
        field, row_id, value = emissions[index]
        if field == "rounding_notes_html" and value == context.get("rounding_notes_html") and value:
            return True
        if (
            field == "rounding_adjustment_display"
            and row_id in required
            and value == required[row_id]
        ):
            covered.add(row_id)
        if (
            field == "settlement_rounding_adjustment_display"
            and value == context["invoice"]["settlement_rounding_adjustment_display"]
        ):
            settlement_found = True
    return covered == set(required) and (not settlement or settlement_found)
