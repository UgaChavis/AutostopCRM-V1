"""Read the immutable cold HTTP cut from the accepted embedded runtime."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from scripts.j1_forward_cold_http import (
    CONFIG_ENV,
    HASH_ENV,
    PUBLIC_ORIGIN,
    SCHEMA,
    STATUS_PATH,
    ColdHttpIngress,
    ColdHttpIngressUnavailableError,
    _canonical,
    _require,
    parse_document,
)

from . import cold_manager

__all__ = [
    "CONFIG_ENV",
    "HASH_ENV",
    "PUBLIC_ORIGIN",
    "SCHEMA",
    "STATUS_PATH",
    "ColdHttpIngress",
    "ColdHttpIngressUnavailableError",
    "parse_document",
    "load_http_ingress",
]


def load_http_ingress(*, environ=None) -> ColdHttpIngress | None:
    environment = os.environ if environ is None else environ
    path, digest = environment.get(CONFIG_ENV), environment.get(HASH_ENV)
    cold = (
        environment.get(cold_manager.BINDING_ENV) is not None
        or environment.get(cold_manager.HASH_ENV) is not None
    )
    if path is None and digest is None and not cold:
        _require(
            not Path(environment.get("AUTOSTOP_MANAGER_PATH", "")).is_relative_to(
                cold_manager.SOURCE_PARENT
            )
        )
        return None
    try:
        _require(cold and path and digest and re.fullmatch(r"[0-9a-f]{64}", digest))
        _require(
            all(
                environment.get(name) == os.environ.get(name)
                for name in (
                    cold_manager.BINDING_ENV,
                    cold_manager.HASH_ENV,
                    "AUTOSTOP_MANAGER_PATH",
                )
            )
        )
        # prepare_cold_manager runs before all application imports. Revalidate
        # its sealed, separately admitted seventh runtime binding here.
        runtime = cold_manager.importlib.import_module("autostop_manager.cold_runtime")
        runtime.require_role("crm-embedded")
        current = runtime.binding()
        _require(current is not None)
        binding = current.document
        expected = (
            f"/etc/autostop-manager-cold/{binding['generation']}/crm-embedded/http-ingress.json"
        )
        _require(path == expected)
        target = Path(path)
        cold_manager._protected(target, directory=False)
        _require(target.stat().st_size <= 8192)
        raw = target.read_bytes()
        _require(hashlib.sha256(raw).hexdigest() == digest)
        result = parse_document(json.loads(raw), binding)
        _require(raw == _canonical(result.document))
        _require(result.sha256 == digest)
        return result
    except ColdHttpIngressUnavailableError:
        raise
    except Exception:
        raise ColdHttpIngressUnavailableError("Cold HTTP ingress is unavailable.") from None


def validate_public_urls(environment):
    """Return the one exact cold endpoint path after physical config readback."""
    if not any(
        name in environment
        for name in (CONFIG_ENV, HASH_ENV, cold_manager.BINDING_ENV, cold_manager.HASH_ENV)
    ):
        return "/mcp"
    config = load_http_ingress(environ=environment)
    _require(config is not None)
    _require(environment.get("MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL") == config.base_url("mcp"))
    _require(
        environment.get("MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL") == config.base_url("mcp") + "/mcp"
    )
    return config.prefix("mcp") + "/mcp"
