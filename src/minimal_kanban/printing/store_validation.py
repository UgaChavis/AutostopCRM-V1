"""Validate stored structures before tolerant legacy normalization can discard data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import PrintModuleError
from .models import InspectionSheetFormData, PrintModuleSettings, PrintTemplateRecord


def print_store_unavailable(path: Path) -> PrintModuleError:
    return PrintModuleError(
        "print_store_unavailable",
        f"Не удалось прочитать данные печати ({path.name}). "
        "Файл сохранён без изменений; требуется восстановить его или доступ к нему.",
        status_code=503,
        details={"file": path.name},
    )


def require_store_shape(valid: bool, path: Path) -> None:
    if not valid:
        raise print_store_unavailable(path)


def _scalar_fields(payload: dict, keys: Any, path: Path) -> None:
    # Older files legitimately contain blank/null/numeric scalar values. Keep
    # their established normalization, but never turn a container into text.
    require_store_shape(all(not isinstance(payload.get(key), (dict, list)) for key in keys), path)


def validate_settings_store(payload: dict, path: Path) -> None:
    defaults = PrintModuleSettings().to_dict()
    _scalar_fields(payload, defaults.keys() - {"service_profile", "default_template_ids"}, path)
    for name in ("service_profile", "default_template_ids"):
        if name not in payload:
            continue
        value = payload[name]
        require_store_shape(isinstance(value, dict), path)
        _scalar_fields(value, defaults[name] if name == "service_profile" else value, path)
    if "_service_profile_explicit_empty_fields" in payload:
        marked = payload["_service_profile_explicit_empty_fields"]
        require_store_shape(
            isinstance(marked, list) and all(isinstance(key, str) for key in marked), path
        )
    if "_tax_label_explicit" in payload:
        require_store_shape(isinstance(payload["_tax_label_explicit"], bool), path)


def validate_template_record(payload: Any, path: Path) -> PrintTemplateRecord:
    require_store_shape(isinstance(payload, dict), path)
    _scalar_fields(
        payload,
        ("id", "document_type", "name", "content", "created_at", "updated_at", "source"),
        path,
    )
    record = PrintTemplateRecord.from_dict(payload)
    if record is None:
        raise print_store_unavailable(path)
    return record


def validate_inspection_form(payload: Any, path: Path) -> None:
    require_store_shape(isinstance(payload, dict), path)
    row_fields = {"planned_work_rows", "planned_material_rows"}
    _scalar_fields(payload, InspectionSheetFormData().to_dict().keys() - row_fields, path)
    for name in row_fields & payload.keys():
        rows = payload[name]
        # Older textual-only forms use blank/null here; row lists also support
        # legacy string entries. Preserve both representations.
        if rows is None or rows == "":
            continue
        require_store_shape(isinstance(rows, list), path)
        for row in rows:
            if isinstance(row, dict):
                _scalar_fields(row, ("name", "quantity"), path)
            else:
                require_store_shape(not isinstance(row, list), path)
