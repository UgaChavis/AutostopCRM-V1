from __future__ import annotations

import inspect
import os
import sys
from collections.abc import Callable
from logging import Logger
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..deployment_security import load_agent_gateway_security_policy
from .agent_gateway_support import MANAGER_GATEWAY_DEPENDENCY_NAMES


class AutostopManagerUnavailableError(RuntimeError):
    pass


class AutostopManagerCompatibilityError(RuntimeError):
    pass


def _add_autostop_manager_path() -> None:
    configured_path = os.environ.get("AUTOSTOP_MANAGER_PATH", "").strip()
    repo_root = Path(__file__).resolve().parents[3]
    candidates = []
    if configured_path:
        candidates.append(Path(configured_path).expanduser())
    candidates.extend(
        [
            repo_root.parent / "AutostopManager",
            repo_root.parent.parent / "AutostopManager",
            Path("/opt/AutostopManager"),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            candidate_text = str(candidate)
            if candidate_text not in sys.path:
                sys.path.insert(0, candidate_text)
            break


def build_autostop_manager_store_read_client() -> Any | None:
    """Create the sibling Manager's read-token-only Store client, if configured."""

    _add_autostop_manager_path()
    try:
        from autostop_manager.config import get_store_api_url, get_store_read_token
        from autostop_manager.store_api import StoreApiClient
    except Exception:
        return None
    api_url = get_store_api_url()
    read_token = get_store_read_token()
    if not api_url or not read_token:
        return None
    return StoreApiClient(
        api_url=api_url,
        read_token=read_token,
        manage_token="",
        quote_token="",
    )


def _resolve_autostop_manager_registrar(
    logger: Logger,
    *,
    required: bool,
) -> Callable[..., Any] | None:
    _add_autostop_manager_path()

    try:
        from autostop_manager.mcp_tools import register_manager_memory_tools
    except Exception as exc:  # pragma: no cover - optional sibling project
        if required or load_agent_gateway_security_policy().production:
            raise AutostopManagerUnavailableError(
                "AutostopManager Gateway dependencies are unavailable."
            ) from None
        logger.info("autostop_manager.memory_tools unavailable: %s", exc)
        return None
    return register_manager_memory_tools


def _validate_autostop_manager_registrar(registrar: Callable[..., Any]) -> None:
    try:
        inspect.signature(registrar).bind(
            object(),
            include_tools=MANAGER_GATEWAY_DEPENDENCY_NAMES,
        )
    except (TypeError, ValueError):
        raise AutostopManagerCompatibilityError(
            "AutostopManager registrar does not support selective tool registration."
        ) from None


def preflight_autostop_manager_registrar(logger: Logger, *, strict: bool = False) -> None:
    registrar = _resolve_autostop_manager_registrar(logger, required=strict)
    if registrar is not None:
        _validate_autostop_manager_registrar(registrar)


def _try_register_autostop_manager_tools(server: FastMCP, logger: Logger) -> None:
    registrar = _resolve_autostop_manager_registrar(logger, required=False)
    if registrar is None:
        return
    _validate_autostop_manager_registrar(registrar)

    registrar(server, include_tools=MANAGER_GATEWAY_DEPENDENCY_NAMES)
    tools = getattr(getattr(server, "_tool_manager", None), "_tools", {})
    missing = MANAGER_GATEWAY_DEPENDENCY_NAMES - set(tools)
    if missing and load_agent_gateway_security_policy().production:
        raise RuntimeError(f"AutostopManager Gateway dependencies missing: {sorted(missing)}")
    logger.info("autostop_manager.memory_tools registered=%s missing=%s", len(tools), len(missing))
