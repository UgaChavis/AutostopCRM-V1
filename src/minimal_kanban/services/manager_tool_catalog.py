"""Validate and serve the immutable, revision-pinned Manager instruction bundle."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from ..storage.limited_io import read_text_limited
from .errors import ServiceError

BUNDLE_SCHEMA = "autostop.automotive-tools.bundle.v1"
BUNDLE_PATH = Path(__file__).parents[1] / "web_app_assets/source/automotive_tool_catalog.json"
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
TOOL_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")


def content_hash(bundle: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in bundle.items()
        if key not in {"content_hash", "source_revision"}
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def validate_bundle(bundle: Any) -> dict[str, Any]:
    if not isinstance(bundle, dict) or bundle.get("schema_version") != BUNDLE_SCHEMA:
        raise ValueError("Unsupported automotive catalog schema")
    if not isinstance(bundle.get("source_revision"), str) or not re.fullmatch(
        r"[0-9a-f]{40}", bundle["source_revision"]
    ):
        raise ValueError("Catalog requires an exact Manager source revision")
    if bundle.get("content_hash") != content_hash(bundle):
        raise ValueError("Automotive catalog content hash mismatch")
    modules, tools = bundle.get("modules"), bundle.get("tools")
    if not isinstance(modules, list) or not 1 <= len(modules) <= 32:
        raise ValueError("Invalid catalog modules")
    if not isinstance(tools, list) or not 1 <= len(tools) <= 2000:
        raise ValueError("Invalid catalog operations")
    schemas = bundle.get("native_schemas", {})
    if (
        not isinstance(schemas, dict)
        or len(schemas) > 2000
        or any(
            not isinstance(name, str) or not isinstance(schema, dict)
            for name, schema in schemas.items()
        )
    ):
        raise ValueError("Invalid embedded operation schemas")
    ids: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict) or not TOOL_ID_RE.fullmatch(str(tool.get("tool_id", ""))):
            raise ValueError("Invalid stable operation ID")
        if tool["tool_id"] in ids:
            raise ValueError("Duplicate catalog operation")
        ids.add(tool["tool_id"])
        for field in ("title", "purpose", "provider_id", "instruction_ref", "instruction_text"):
            if not isinstance(tool.get(field), str) or len(tool[field]) > 100000:
                raise ValueError(f"Invalid operation {field}")
        if not isinstance(tool.get("implementation_state"), str) or tool[
            "implementation_state"
        ] not in {
            "implemented",
            "planned",
            "retired",
            "historical",
        }:
            raise ValueError("Invalid technical implementation state")
        for field in ("input_fields", "limitations", "outputs", "errors", "also_used_in"):
            values = tool.get(field, [])
            if (
                not isinstance(values, list)
                or len(values) > 2000
                or any(not isinstance(value, str) or len(value) > 100000 for value in values)
            ):
                raise ValueError(f"Invalid operation {field}")
        schema_ref = tool.get("input_schema_ref")
        if schema_ref is not None and (
            not isinstance(schema_ref, str) or schema_ref not in schemas
        ):
            raise ValueError("Unknown embedded operation schema")
        if tool.get("invocation") is not None and not isinstance(tool["invocation"], dict):
            raise ValueError("Invalid operation invocation")
    keys: set[str] = set()
    elements: set[str] = set()
    for module in modules:
        if not isinstance(module, dict):
            raise ValueError("Invalid module")
        for field in (
            "module_key",
            "element_id",
            "title",
            "purpose",
            "instruction_ref",
            "instruction_text",
        ):
            if not isinstance(module.get(field), str) or len(module[field]) > 100000:
                raise ValueError(f"Invalid module {field}")
        if module["module_key"] in keys or module["element_id"] in elements:
            raise ValueError("Duplicate module binding")
        keys.add(module["module_key"])
        elements.add(module["element_id"])
        references = module.get("tool_ids")
        if (
            not isinstance(references, list)
            or any(not isinstance(ident, str) or ident not in ids for ident in references)
            or len(set(references)) != len(references)
        ):
            raise ValueError("Unknown operation binding")
    return bundle


@cache
def _installed_bundle() -> dict[str, Any]:
    try:
        return validate_bundle(
            json.loads(
                read_text_limited(
                    BUNDLE_PATH, max_bytes=MAX_BUNDLE_BYTES, label="automotive catalog"
                )
            )
        )
    except (OSError, ValueError) as error:
        raise ServiceError(
            "manager_tool_catalog_unavailable",
            "Каталог инструментов не прошёл проверку установленного пакета.",
            status_code=503,
        ) from error


def load_bundle() -> dict[str, Any]:
    return copy.deepcopy(_installed_bundle())


def installed_metadata() -> dict[str, Any]:
    return bundle_metadata(_installed_bundle())


def bundle_metadata(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": True,
        **{field: bundle[field] for field in ("schema_version", "source_revision", "content_hash")},
    }
