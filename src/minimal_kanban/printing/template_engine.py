from __future__ import annotations

import html
import re
from typing import Any


class TemplateRenderError(ValueError):
    pass


_TOKEN_RE = re.compile(r"{{{\s*([^{}]+?)\s*}}}|{{\s*([#/^]?)([^{}]+?)\s*}}")


def _is_truthy(value: Any) -> bool:
    if isinstance(value, (list, dict)):
        return bool(value)
    return bool(value)


def _lookup_in_context(context: Any, key: str) -> Any:
    if key == ".":
        return context
    if isinstance(context, dict):
        return context.get(key)
    if hasattr(context, key):
        return getattr(context, key)
    return None


def _resolve(path: str, stack: list[Any]) -> Any:
    parts = [part for part in str(path or "").strip().split(".") if part]
    if not parts:
        return ""
    for context in stack:
        current: Any = context
        found = True
        for index, part in enumerate(parts):
            if index == 0 and part == ".":
                current = context
                continue
            current = _lookup_in_context(current, part)
            if current is None:
                found = False
                break
        if found:
            return current
    return ""


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Да" if value else ""
    if isinstance(value, (list, dict)):
        return ""
    return str(value)


def render_template(template: str, context: dict[str, Any]) -> str:
    rendered, next_index = _render_segment(str(template or ""), [context], 0, stop_name=None)
    if next_index != len(str(template or "")):
        raise TemplateRenderError("Поврежден шаблон: лишние закрывающие теги секций.")
    return rendered


def _extract_section(template: str, start_index: int, stop_name: str) -> tuple[str, int]:
    depth = 1
    index = start_index
    while True:
        match = _TOKEN_RE.search(template, index)
        if match is None:
            raise TemplateRenderError(f"Не закрыта секция шаблона: {stop_name}")
        raw_name = match.group(1)
        sigil = match.group(2) or ""
        name = (match.group(3) or "").strip()
        if raw_name is None and name == stop_name:
            if sigil in ("#", "^"):
                depth += 1
            elif sigil == "/":
                depth -= 1
                if depth == 0:
                    return template[start_index:match.start()], match.end()
        index = match.end()


def _render_segment(template: str, stack: list[Any], start_index: int, stop_name: str | None) -> tuple[str, int]:
    out: list[str] = []
    index = start_index
    while True:
        match = _TOKEN_RE.search(template, index)
        if match is None:
            if stop_name is not None:
                raise TemplateRenderError(f"Не закрыта секция шаблона: {stop_name}")
            out.append(template[index:])
            return "".join(out), len(template)
        out.append(template[index:match.start()])
        raw_name = match.group(1)
        if raw_name is not None:
            out.append(_stringify(_resolve(raw_name, stack)))
            index = match.end()
            continue
        sigil = match.group(2) or ""
        name = (match.group(3) or "").strip()
        index = match.end()
        if sigil == "":
            out.append(html.escape(_stringify(_resolve(name, stack))))
            continue
        if sigil == "/":
            if stop_name == name:
                return "".join(out), index
            raise TemplateRenderError(f"Закрывающая секция {name} не соответствует открытому блоку.")
        inner, next_index = _extract_section(template, index, name)
        value = _resolve(name, stack)
        if sigil == "#":
            if isinstance(value, list):
                for item in value:
                    rendered, _ = _render_segment(inner, [item, *stack], 0, stop_name=None)
                    out.append(rendered)
            elif isinstance(value, dict):
                rendered, _ = _render_segment(inner, [value, *stack], 0, stop_name=None)
                out.append(rendered)
            elif _is_truthy(value):
                rendered, _ = _render_segment(inner, [value, *stack], 0, stop_name=None)
                out.append(rendered)
        elif sigil == "^":
            if not _is_truthy(value):
                rendered, _ = _render_segment(inner, stack, 0, stop_name=None)
                out.append(rendered)
        else:
            raise TemplateRenderError(f"Неподдерживаемый тег шаблона: {sigil}{name}")
        index = next_index
