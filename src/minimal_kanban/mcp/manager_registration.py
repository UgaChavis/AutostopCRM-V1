from __future__ import annotations

import inspect
import os
import sys
from collections.abc import Callable
from importlib import import_module
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


def _resolve_autostop_manager_registrar(
    logger: Logger,
    *,
    required: bool,
) -> Callable[..., Any] | None:
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


def _store_quote_telegram_transport() -> Any | None:
    socket_value = os.environ.get("AUTOSTOP_STORE_QUOTE_TELEGRAM_SOCKET", "").strip()
    if not socket_value:
        return None
    socket_path = Path(socket_value)
    if not socket_path.is_absolute():
        raise AutostopManagerCompatibilityError("Store quote Telegram socket path must be absolute.")
    try:
        transport_module = import_module("autostop_manager.store_quote_telegram_transport")
    except Exception:  # pragma: no cover - resolved together with the Manager release.
        raise AutostopManagerCompatibilityError("Store quote Telegram transport is unavailable.") from None
    try:
        return transport_module.create_work_store_quote_transport(socket_path=socket_path)
    except (OSError, TypeError, ValueError):
        raise AutostopManagerCompatibilityError("Store quote Telegram transport is invalid.") from None


def _registrar_kwargs(telegram_transport: Any | None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"include_tools": MANAGER_GATEWAY_DEPENDENCY_NAMES}
    if telegram_transport is not None:
        kwargs["telegram_sender"] = telegram_transport
    return kwargs


def _validate_autostop_manager_registrar(registrar: Callable[..., Any], telegram_transport: Any | None = None) -> None:
    try:
        inspect.signature(registrar).bind(object(), **_registrar_kwargs(telegram_transport))
    except (TypeError, ValueError):
        raise AutostopManagerCompatibilityError(
            "AutostopManager registrar does not support selective tool registration."
        ) from None


def preflight_autostop_manager_registrar(logger: Logger, *, strict: bool = False) -> None:
    registrar = _resolve_autostop_manager_registrar(logger, required=strict)
    if registrar is not None:
        _validate_autostop_manager_registrar(registrar, _store_quote_telegram_transport())


def _try_register_autostop_manager_tools(server: FastMCP, logger: Logger) -> None:
    registrar = _resolve_autostop_manager_registrar(logger, required=False)
    if registrar is None:
        return
    telegram_transport = _store_quote_telegram_transport()
    kwargs = _registrar_kwargs(telegram_transport)
    _validate_autostop_manager_registrar(registrar, telegram_transport)

    registrar(server, **kwargs)
    tools = getattr(getattr(server, "_tool_manager", None), "_tools", {})
    missing = MANAGER_GATEWAY_DEPENDENCY_NAMES - set(tools)
    if missing and load_agent_gateway_security_policy().production:
        raise RuntimeError(f"AutostopManager Gateway dependencies missing: {sorted(missing)}")
    logger.info("autostop_manager.memory_tools registered=%s missing=%s", len(tools), len(missing))
