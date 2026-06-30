from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

DEFAULT_JSON_MAX_DEPTH = 512


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
