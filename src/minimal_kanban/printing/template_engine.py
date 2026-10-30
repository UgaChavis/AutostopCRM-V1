from __future__ import annotations

import html
import re
from collections.abc import Callable, Iterator
from typing import Any


class TemplateRenderError(ValueError):
    pass


_TOKEN_RE = re.compile(r"{{{\s*([^{}]+?)\s*}}}|{{\s*([#/^]?)([^{}]+?)\s*}}")
_MAX_SECTION_DEPTH = 80
_MAX_RENDER_STEPS = 100_000
_MAX_RENDER_CHARS = 8 * 1024 * 1024
ValueRenderer = Callable[[str, Any, list[Any], str], str]


class _RenderBudget:
    """Bound expansion before joins; even empty nested loops consume work."""

    def __init__(self):
        self.steps = _MAX_RENDER_STEPS
        self.characters = _MAX_RENDER_CHARS

    def step(self, token_characters: int = 0) -> None:
        # Very long names also cost work and memory in runtime-path tracing.
        self.steps -= 1 + token_characters // 64
        if self.steps < 0:
            raise TemplateRenderError("Слишком сложный шаблон: превышен предел обработки секций.")

    def emit(self, text: str) -> str:
        self.characters -= len(text)
        if self.characters < 0:
            raise TemplateRenderError("Результат шаблона превышает допустимый размер документа.")
        return text


def template_value_paths(template: str, context: dict | None = None) -> Iterator[str]:
    if context is not None:
        paths: list[str] = []

        def trace(path, value, stack, text):
            paths.append(path)
            return text

        render_template(template, context, value_renderer=trace)
        yield from paths
        return
    for match in _TOKEN_RE.finditer(str(template or "")):
        if match.group(1) is not None:
            yield match.group(1).strip()
        elif not match.group(2):
            yield (match.group(3) or "").strip()


def _render_value(
    path, stack, stack_paths, *, raw: bool, renderer: ValueRenderer | None, budget: _RenderBudget
) -> str:
    value, resolved_path = _resolve(path, stack, stack_paths)
    text = _stringify(value)
    if not raw:
        text = html.escape(text)
    return budget.emit(renderer(resolved_path, value, stack, text) if renderer else text)


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


def _resolve(path: str, stack: list[Any], stack_paths: list[str]) -> tuple[Any, str]:
    if str(path or "").strip() == ".":
        return stack[0], stack_paths[0]
    parts = [part for part in str(path or "").strip().split(".") if part]
    if not parts:
        return "", str(path or "")
    for context, context_path in zip(stack, stack_paths):
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
            return current, ".".join(part for part in (context_path, ".".join(parts)) if part)
    return "", str(path or "")


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Да" if value else ""
    if isinstance(value, (list, dict)):
        return ""
    return str(value)


def render_template(
    template: str, context: dict[str, Any], *, value_renderer: ValueRenderer | None = None
) -> str:
    source = str(template or "")
    if len(source) > _MAX_RENDER_CHARS:
        raise TemplateRenderError("Шаблон превышает допустимый размер документа.")
    try:
        rendered, next_index = _render_segment(
            source,
            [context],
            [""],
            0,
            stop_name=None,
            value_renderer=value_renderer,
            budget=_RenderBudget(),
        )
    except RecursionError as exc:
        raise TemplateRenderError("Слишком глубокая вложенность секций шаблона.") from exc
    if next_index != len(source):
        raise TemplateRenderError("Поврежден шаблон: лишние закрывающие теги секций.")
    return rendered


def _extract_section(
    template: str, start_index: int, stop_name: str, budget: _RenderBudget
) -> tuple[str, int]:
    depth = 1
    index = start_index
    while True:
        budget.step()
        match = _TOKEN_RE.search(template, index)
        if match is None:
            raise TemplateRenderError(f"Не закрыта секция шаблона: {stop_name}")
        budget.step(match.end() - match.start())
        raw_name = match.group(1)
        sigil = match.group(2) or ""
        name = (match.group(3) or "").strip()
        if raw_name is None and name == stop_name:
            if sigil in ("#", "^"):
                depth += 1
            elif sigil == "/":
                depth -= 1
                if depth == 0:
                    return template[start_index : match.start()], match.end()
        index = match.end()


def _render_section_block(
    sigil: str,
    inner: str,
    stack: list[Any],
    stack_paths: list[str],
    value: Any,
    value_path: str,
    *,
    section_depth: int,
    budget: _RenderBudget,
    value_renderer: ValueRenderer | None = None,
) -> str:
    rendered_parts: list[str] = []
    if sigil == "#":
        if isinstance(value, list):
            for item in value:
                rendered, _ = _render_segment(
                    inner,
                    [item, *stack],
                    [value_path, *stack_paths],
                    0,
                    stop_name=None,
                    section_depth=section_depth + 1,
                    value_renderer=value_renderer,
                    budget=budget,
                )
                rendered_parts.append(rendered)
        elif isinstance(value, dict):
            rendered, _ = _render_segment(
                inner,
                [value, *stack],
                [value_path, *stack_paths],
                0,
                stop_name=None,
                section_depth=section_depth + 1,
                value_renderer=value_renderer,
                budget=budget,
            )
            rendered_parts.append(rendered)
        elif _is_truthy(value):
            rendered, _ = _render_segment(
                inner,
                [value, *stack],
                [value_path, *stack_paths],
                0,
                stop_name=None,
                section_depth=section_depth + 1,
                value_renderer=value_renderer,
                budget=budget,
            )
            rendered_parts.append(rendered)
    elif sigil == "^":
        if not _is_truthy(value):
            rendered, _ = _render_segment(
                inner,
                stack,
                stack_paths,
                0,
                stop_name=None,
                section_depth=section_depth + 1,
                value_renderer=value_renderer,
                budget=budget,
            )
            rendered_parts.append(rendered)
    else:
        raise TemplateRenderError(f"Неподдерживаемый тег шаблона: {sigil}")
    return "".join(rendered_parts)


def _render_segment(
    template: str,
    stack: list[Any],
    stack_paths: list[str],
    start_index: int,
    stop_name: str | None,
    *,
    budget: _RenderBudget,
    section_depth: int = 0,
    value_renderer: ValueRenderer | None = None,
) -> tuple[str, int]:
    if section_depth > _MAX_SECTION_DEPTH:
        raise TemplateRenderError("Слишком глубокая вложенность секций шаблона.")
    out: list[str] = []
    index = start_index
    while True:
        budget.step()
        match = _TOKEN_RE.search(template, index)
        if match is None:
            if stop_name is not None:
                raise TemplateRenderError(f"Не закрыта секция шаблона: {stop_name}")
            out.append(budget.emit(template[index:]))
            return "".join(out), len(template)
        budget.step(match.end() - match.start())
        out.append(budget.emit(template[index : match.start()]))
        raw_name = match.group(1)
        if raw_name is not None:
            out.append(
                _render_value(
                    raw_name, stack, stack_paths, raw=True, renderer=value_renderer, budget=budget
                )
            )
            index = match.end()
            continue
        sigil = match.group(2) or ""
        name = (match.group(3) or "").strip()
        index = match.end()
        if sigil == "":
            out.append(
                _render_value(
                    name, stack, stack_paths, raw=False, renderer=value_renderer, budget=budget
                )
            )
            continue
        if sigil == "/":
            if stop_name == name:
                return "".join(out), index
            raise TemplateRenderError(
                f"Закрывающая секция {name} не соответствует открытому блоку."
            )
        inner, next_index = _extract_section(template, index, name, budget)
        value, value_path = _resolve(name, stack, stack_paths)
        out.append(
            _render_section_block(
                sigil,
                inner,
                stack,
                stack_paths,
                value,
                value_path,
                section_depth=section_depth,
                value_renderer=value_renderer,
                budget=budget,
            )
        )
        index = next_index
