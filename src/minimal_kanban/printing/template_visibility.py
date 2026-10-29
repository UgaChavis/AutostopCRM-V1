"""Conservative print-CSS visibility proof for required monetary notes.

This is deliberately not a layout engine. Ordinary selectors, the cascade and
print/screen media are supported; external styles, dynamic conditions and
clipping/positioning of required text fail closed. It runs without Qt or a
browser in the HTTP preview worker and never fetches template resources.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from xml.etree.ElementTree import Element, SubElement

import cssselect2
import tinycss2
from tinycss2.color3 import parse_color

_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_HIDDEN = frozenset("head script style template noscript iframe object canvas select title".split())
_INITIAL = {
    "display": "block",
    "visibility": "visible",
    "opacity": "1",
    "content-visibility": "visible",
    "font-size": "16px",
    "line-height": "normal",
    "color": "black",
    "-webkit-text-fill-color": "currentcolor",
    "background-color": "transparent",
}
_INHERITED = frozenset(
    {"visibility", "font-size", "line-height", "color", "-webkit-text-fill-color"}
)
_NEUTRAL = {
    "position": {"static", "relative"},
    "overflow": {"visible"},
    "overflow-x": {"visible"},
    "overflow-y": {"visible"},
    "clip": {"auto"},
    "clip-path": {"none"},
    "filter": {"none"},
    "transform": {"none"},
    "scale": {"none", "1"},
    "translate": {"none", "0"},
    "rotate": {"none", "0deg"},
    "text-indent": {"0", "0px"},
    "animation": {"none"},
    "animation-name": {"none"},
    "mask": {"none"},
    "mask-image": {"none"},
    "-webkit-mask": {"none"},
    "-webkit-mask-image": {"none"},
    "content": {"normal", "none"},
    "zoom": {"normal", "1", "100%"},
    "all": set(),
    "inset": {"auto", "0", "0px"},
    "top": {"auto", "0", "0px"},
    "right": {"auto", "0", "0px"},
    "bottom": {"auto", "0", "0px"},
    "left": {"auto", "0", "0px"},
}
_VAR = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*))?\)")


class _Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Element("html")
        self.body = SubElement(self.root, "body")
        self.stack = [self.root, self.body]
        self.text: list[tuple[Element, str]] = []
        self.styles: list[tuple[str, str]] = []
        self.dynamic = False
        self.node_count = 2

    def handle_starttag(self, tag, attrs):
        self.node_count += 1
        if self.node_count > 12000 or len(self.stack) > 128:
            raise ValueError("Print markup exceeds static verification limits")
        attributes = {key: value or "" for key, value in attrs}
        if tag in {"html", "body"}:
            target = self.root if tag == "html" else self.body
            target.attrib.update(attributes)
            return
        node = SubElement(self.stack[-1], tag, attributes)
        if tag == "script" or any(key.startswith("on") for key in attributes):
            self.dynamic = True
        if tag == "link" and "stylesheet" in attributes.get("rel", "").lower().split():
            self.dynamic |= _print_media(attributes.get("media", "")) is not False
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        size = len(self.stack)
        self.handle_starttag(tag, attrs)
        del self.stack[size:]

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        node = self.stack[-1]
        if node.tag == "style":
            self.styles.append((node.get("media", ""), data))
        self.text.append((node, data))
        # Selector :empty and text-sensitive structural tests need actual text.
        if len(node):
            node[-1].tail = (node[-1].tail or "") + data
        else:
            node.text = (node.text or "") + data


def _print_media(media: str) -> bool | None:
    """Unknown print conditions cannot prove required text visible."""
    outcomes = []
    for query in media.lower().strip().split(","):
        query = " ".join(query.split())
        if query in {"", "all", "print", "only print", "not screen"}:
            outcomes.append(True)
        elif query in {"screen", "only screen", "not print", "not all"} or re.match(
            r"(?:only )?screen\s+and\b", query
        ):
            outcomes.append(False)
        else:
            outcomes.append(None)
    return True if True in outcomes else None if None in outcomes else False


def _declarations(tokens):
    collapsed = {}
    for index, (name, value, important) in enumerate(_raw_declarations(tokens)):
        if (important, index) >= collapsed.get(name, ((-1, -1), ""))[0]:
            collapsed[name] = ((important, index), value)
        if len(collapsed) > 256:
            raise ValueError("Too many CSS properties")
    return [(name, value, priority[0]) for name, (priority, value) in collapsed.items()]


def _raw_declarations(tokens):
    for item in tinycss2.parse_declaration_list(tokens, skip_comments=True, skip_whitespace=True):
        if item.type != "declaration":
            continue
        name = item.name if item.name.startswith("--") else item.lower_name
        value = tinycss2.serialize(item.value).strip()
        if name == "font":
            # Preserve the shorthand's place in the cascade for ordinary fonts.
            size = next(
                (token for token in item.value if token.type in {"dimension", "percentage"}), None
            )
            yield "font-size", tinycss2.serialize([size]) if size else "unknown", item.important
        elif name == "background":
            color = next((token for token in item.value if parse_color(token) is not None), None)
            value = (
                tinycss2.serialize(item.value).strip()
                if color is None
                else tinycss2.serialize([color])
            )
            # A simple color or var() is common in the approved templates.
            if len([token for token in item.value if token.type != "whitespace"]) == 1:
                value = tinycss2.serialize(item.value).strip()
            if value == "none":
                value = "transparent"
            yield "background-color", value, item.important
        else:
            yield name, value, item.important


def _stylesheet(matcher, css, *, order, depth=0):
    if depth > 16:
        raise ValueError("Nested print CSS exceeds verification limits")
    for rule in tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True):
        if rule.type == "at-rule":
            if rule.lower_at_keyword == "media":
                applies = _print_media(tinycss2.serialize(rule.prelude))
                if applies is None:
                    raise ValueError("Conditional print CSS")
                if applies and rule.content is not None:
                    _stylesheet(matcher, rule.content, order=order, depth=depth + 1)
            elif rule.lower_at_keyword not in {
                "page",
                "font-face",
                "charset",
                "keyframes",
                "-webkit-keyframes",
            }:
                raise ValueError("External or unsupported CSS")
        elif rule.type == "qualified-rule":
            selector_text = tinycss2.serialize(rule.prelude)
            if len(selector_text) > 512 or re.search(r":has\s*\(", selector_text, re.IGNORECASE):
                raise ValueError("Selector exceeds static verification limits")
            declarations = list(_declarations(rule.content))
            for selector in cssselect2.compile_selector_list(rule.prelude):
                order[0] += 1
                order[1] += len(declarations)
                if order[0] > 1024:
                    raise ValueError("Too many print selectors")
                matcher.add_selector(selector, (declarations, order[0]))


def _variables(value, custom):
    for _ in range(16):
        if "var(" not in value:
            return value.strip().lower()
        replaced = _VAR.sub(lambda match: custom.get(match[1], match[2] or "unknown"), value)
        if replaced == value:
            break
        value = replaced
    return "unknown"


def _style(wrapper, matcher, parent):
    cascade = {}
    for specificity, _, pseudo, (declarations, order) in matcher.match(wrapper):
        for name, value, important in declarations:
            key = (important, 0, specificity, order)
            if pseudo and name in {*_INITIAL, *_NEUTRAL, "font", "background"}:
                name, value = "all", "unknown"
            if key >= cascade.get(name, ((-1,), ""))[0]:
                cascade[name] = (key, value)
    for order, (name, value, important) in enumerate(
        _declarations(wrapper.etree_element.get("style", ""))
    ):
        key = (important, 1, (0, 0, 0), order)
        if key >= cascade.get(name, ((-1,), ""))[0]:
            cascade[name] = (key, value)
    custom = {key: value for key, value in parent.items() if key.startswith("--")}
    custom.update({key: value for key, (_, value) in cascade.items() if key.startswith("--")})
    if len(custom) > 64:
        raise ValueError("Too many inherited CSS variables")
    result = dict(custom)
    for name in _INITIAL.keys() | _NEUTRAL.keys() | cascade.keys():
        if name.startswith("--"):
            continue
        default = (
            parent.get(name, _INITIAL.get(name, "initial"))
            if name in _INHERITED
            else _INITIAL.get(name, "initial")
        )
        value = _variables(cascade[name][1], custom) if name in cascade else default
        if value == "inherit" or value == "unset" and name in _INHERITED:
            value = parent.get(name, _INITIAL.get(name, "initial"))
        elif value in {"initial", "unset", "revert"}:
            value = _INITIAL.get(name, "initial")
        result[name] = value
    return result


def _positive(value):
    if value in {
        "normal",
        "medium",
        "small",
        "large",
        "smaller",
        "larger",
        "x-small",
        "x-large",
        "xx-small",
        "xx-large",
    }:
        return True
    match = re.fullmatch(
        r"([+]?(?:\d+(?:\.\d*)?|\.\d+))(?:px|pt|pc|mm|cm|in|em|rem|ex|ch|%)?", value
    )
    return bool(match and float(match[1]) > 0)


def _structurally_hidden(wrapper):
    node = wrapper.etree_element
    if node.tag in _HIDDEN or "hidden" in node.attrib:
        return True
    current = wrapper
    while current.parent is not None:
        parent = current.parent.etree_element
        if (
            parent.tag == "details"
            and "open" not in parent.attrib
            and current.etree_element
            is not next((child for child in parent if child.tag == "summary"), None)
        ):
            return True
        current = current.parent
    return False


def visible_print_text(source: str) -> str:
    try:
        return _visible_print_text(source)
    except (ValueError, SyntaxError, RecursionError, cssselect2.SelectorError):
        # Untrusted editable templates must never turn a preview into a 500.
        return ""


def _visible_print_text(source: str) -> str:
    """Return only text whose static print visibility can be established."""
    document = _Document()
    document.feed(source)
    document.close()
    if document.dynamic:
        return ""
    matcher = cssselect2.Matcher()
    try:
        order = [0, 0]
        for media, css in document.styles:
            applies = _print_media(media)
            if applies is None:
                return ""
            if applies:
                _stylesheet(matcher, css, order=order)
    except (ValueError, cssselect2.SelectorError):
        return ""
    if document.node_count * max(order) > 1_000_000:
        return ""
    states, visible = {}, set()
    for wrapper in cssselect2.ElementWrapper.from_html_root(document.root).iter_subtree():
        parent = (
            states.get(wrapper.parent.etree_element, ({}, False, parse_color("white")))
            if wrapper.parent
            else ({}, False, parse_color("white"))
        )
        style = _style(wrapper, matcher, parent[0])
        if re.search(r"(?:%|em|ex|ch)$", style["font-size"]) and not _positive(
            parent[0].get("font-size", "16px")
        ):
            style["font-size"] = "0px"
        blocked = parent[1] or _structurally_hidden(wrapper)
        blocked |= (
            style["display"] in {"none", "unknown"} or style["content-visibility"] != "visible"
        )
        blocked |= not _positive(style["opacity"])
        blocked |= any(
            style.get(name, "initial") not in {*neutral, "initial"}
            for name, neutral in _NEUTRAL.items()
        )
        color = parse_color(style["color"])
        fill = parse_color(style["-webkit-text-fill-color"])
        if fill != "currentColor":
            color = fill
        background = parse_color(style["background-color"])
        backdrop = (
            parent[2]
            if background is not None and background != "currentColor" and background.alpha == 0
            else background
        )
        states[wrapper.etree_element] = style, blocked, backdrop
        readable = (
            color is not None
            and color != "currentColor"
            and color.alpha > 0
            and backdrop is not None
            and color != backdrop
        )
        if (
            not blocked
            and readable
            and style["visibility"] == "visible"
            and _positive(style["font-size"])
            and _positive(style["line-height"])
        ):
            visible.add(wrapper.etree_element)
    return "".join(text for node, text in document.text if node in visible)
