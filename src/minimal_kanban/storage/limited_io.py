from __future__ import annotations

from math import isfinite
from pathlib import Path
from uuid import uuid4

_COPY_CHUNK_BYTES = 1024 * 1024


def _normalize_max_bytes(max_bytes: int, *, label: str) -> int:
    if isinstance(max_bytes, bool | str):
        raise ValueError(f"{label} max bytes must be an integer")
    try:
        numeric = float(max_bytes)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} max bytes must be an integer") from exc
    if not isfinite(numeric) or not numeric.is_integer():
        raise ValueError(f"{label} max bytes must be an integer")
    if numeric < 0:
        raise ValueError(f"{label} max bytes must be non-negative")
    if numeric > 1_000_000_000:
        raise ValueError(f"{label} max bytes is too large")
    normalized = int(numeric)
    return normalized


def read_text_limited(path: Path, *, max_bytes: int, label: str) -> str:
    return read_bytes_limited(path, max_bytes=max_bytes, label=label).decode("utf-8")


def read_bytes_limited(path: Path, *, max_bytes: int, label: str) -> bytes:
    max_bytes = _normalize_max_bytes(max_bytes, label=label)
    if path.stat().st_size > max_bytes:
        raise ValueError(f"{label} is too large")
    with path.open("rb") as handle:
        payload = handle.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"{label} is too large")
    return payload


def copy_file_limited(source_path: Path, target_path: Path, *, max_bytes: int, label: str) -> int:
    max_bytes = _normalize_max_bytes(max_bytes, label=label)
    if source_path.stat().st_size > max_bytes:
        raise ValueError(f"{label} is too large")
    bytes_copied = 0
    temp_path = target_path.with_name(f".{target_path.name}.{uuid4().hex}.tmp")
    try:
        with source_path.open("rb") as source, temp_path.open("wb") as target:
            while True:
                chunk = source.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                bytes_copied += len(chunk)
                if bytes_copied > max_bytes:
                    raise ValueError(f"{label} is too large")
                target.write(chunk)
        temp_path.replace(target_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return bytes_copied
