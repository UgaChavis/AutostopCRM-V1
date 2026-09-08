from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

DEFAULT_JSON_MAX_DEPTH = 512


def bounded_dict_list(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        items.append(dict(item))
        if len(items) >= limit:
            break
    return items


def bounded_text_list(value: Any, *, limit: int) -> list[str]:
    if isinstance(value, str):
        raw_items: list[Any] = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        return []
    items: list[str] = []
    for raw in raw_items:
        text = str(raw or "").strip()
        if not text:
            continue
        items.append(text)
        if len(items) >= limit:
            break
    return items


def find_mapping(value: Any, key: str, expected: Any, *, depth: int = 0) -> dict[str, Any] | None:
    if depth > 7:
        return None
    if isinstance(value, Mapping):
        if key in value and str(value.get(key)) == str(expected):
            return dict(value)
        for item in value.values():
            found = find_mapping(item, key, expected, depth=depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value[:200]:
            found = find_mapping(item, key, expected, depth=depth + 1)
            if found is not None:
                return found
    return None


def json_safe_value(value: Any, *, depth: int = 8, nonfinite=None, drop_none_keys=True) -> Any:
    """Bound and sanitize JSON without mixing API and persisted-number policies."""
    if depth <= 0:
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else nonfinite
    if isinstance(value, dict):
        return {
            str(key): json_safe_value(
                item, depth=depth - 1, nonfinite=nonfinite, drop_none_keys=drop_none_keys
            )
            for key, item in value.items()
            if key is not None or not drop_none_keys
        }
    if isinstance(value, (list, tuple, set)):
        return [
            json_safe_value(
                item, depth=depth - 1, nonfinite=nonfinite, drop_none_keys=drop_none_keys
            )
            for item in value
        ]
    return str(value)


def json_safe_storage_value(value: Any, *, depth: int = 8) -> Any:
    return json_safe_value(value, depth=depth, nonfinite=0.0)


def json_safe_api_value(value: Any, *, depth: int = 8) -> Any:
    return json_safe_value(value, depth=depth, drop_none_keys=False)


def reject_deeply_nested_json(
    value: Any,
    *,
    max_depth: int = DEFAULT_JSON_MAX_DEPTH,
    message: str = "JSON is too deeply nested",
) -> None:
    """Reject decoded JSON payloads that are too deep to handle safely."""

    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > max_depth:
            raise ValueError(message)
        if isinstance(current, Mapping):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            stack.extend((item, depth + 1) for item in current)
