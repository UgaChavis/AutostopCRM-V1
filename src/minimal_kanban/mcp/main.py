from __future__ import annotations

import os
import signal
import time

from ..api.server import ApiServer
from ..config import (
    get_api_bearer_token,
    get_api_host,
    get_api_port,
    get_board_api_url,
    get_mcp_bearer_token,
    get_mcp_host,
    get_mcp_path,
    get_mcp_port,
    get_mcp_public_base_url,
    get_mcp_public_endpoint_url,
)
from ..logging_setup import close_logger, configure_logging
from ..operator_auth import OperatorAuthService
from ..settings_service import SettingsService
from ..settings_store import SettingsStore
from ..services.card_service import CardService
from ..storage.json_store import JsonStore
from .client import BoardApiClient, BoardApiTransportError, discover_board_api
from .runtime import McpServerRuntime
from .server import create_mcp_server


def _env_list(name: str) -> tuple[str, ...]:
    raw_value = (os.environ.get(name) or "").strip()
    if not raw_value:
        return ()
    values: list[str] = []
    seen: set[str] = set()
    for chunk in raw_value.splitlines():
        for item in chunk.split(","):
            text = item.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            values.append(text)
    return tuple(values)


def _reachable_board_api_url(
    configured_api_base_url: str | None,
    *,
    bearer_token: str | None,
    logger,
) -> str | None:
    if not configured_api_base_url:
        return None
    try:
        if BoardApiClient(configured_api_base_url, bearer_token=bearer_token, logger=logger).health().get("ok"):
            return configured_api_base_url
    except BoardApiTransportError:
        return None
    return None


def _runtime_bind_host(configured_host: str | None, *, env_explicit: bool) -> str:
    host = str(configured_host or "").strip() or "0.0.0.0"
    if env_explicit:
        return host
    if host in {"127.0.0.1", "localhost"}:
        return "0.0.0.0"
    return host


def run() -> int:
    logger = configure_logging()
    embedded_api_server: ApiServer | None = None
    mcp_runtime: McpServerRuntime | None = None
    try:
        settings_store = SettingsStore(logger=logger)
        settings_service = SettingsService(settings_store, logger)
        settings = settings_service.load()

        if os.environ.get("MINIMAL_KANBAN_API_BEARER_TOKEN") is not None:
            api_bearer_token = get_api_bearer_token()
        elif settings.local_api.local_api_auth_mode == "bearer":
            api_bearer_token = settings.local_api.local_api_bearer_token or settings.auth.local_api_bearer_token or settings.auth.access_token or get_api_bearer_token()
        else:
            api_bearer_token = None

        if os.environ.get("MINIMAL_KANBAN_BOARD_API_URL") is not None or os.environ.get("MINIMAL_KANBAN_API_BASE_URL") is not None:
            configured_api_base_url = get_board_api_url()
        elif settings.general.use_local_api or settings.local_api.local_api_base_url_override:
            configured_api_base_url = settings.local_api.effective_local_api_url
        else:
            configured_api_base_url = None
        api_base_url = _reachable_board_api_url(
            configured_api_base_url,
            bearer_token=api_bearer_token,
            logger=logger,
        )
        if not api_base_url:
            api_base_url = get_board_api_url() or discover_board_api(bearer_token=api_bearer_token)
        if not api_base_url:
            store = JsonStore(logger=logger)
            service = CardService(store, logger)
            seeded_demo = service.ensure_demo_board()
            if seeded_demo:
                logger.info("embedded_api_demo_seeded=true")
            operator_service = OperatorAuthService(store, service, logger=logger)
            resolved_api_host = _runtime_bind_host(
                get_api_host() if os.environ.get("MINIMAL_KANBAN_API_HOST") is not None else settings.local_api.local_api_host,
                env_explicit=os.environ.get("MINIMAL_KANBAN_API_HOST") is not None,
            )
            embedded_api_server = ApiServer(
                service,
                logger,
                operator_service=operator_service,
                host=resolved_api_host,
                start_port=get_api_port() if os.environ.get("MINIMAL_KANBAN_API_PORT") is not None else settings.local_api.local_api_port,
                bearer_token=api_bearer_token,
            )
            embedded_api_server.start()
            api_base_url = embedded_api_server.base_url
            logger.info("embedded_api_started_for_mcp url=%s", api_base_url)
        else:
            logger.info("using_existing_board_api url=%s", api_base_url)

        if os.environ.get("MINIMAL_KANBAN_MCP_BEARER_TOKEN") is not None:
            mcp_bearer_token = get_mcp_bearer_token()
        elif settings.mcp.mcp_auth_mode == "bearer":
            mcp_bearer_token = settings.mcp.mcp_bearer_token or settings.auth.mcp_bearer_token or settings.auth.access_token or get_mcp_bearer_token()
        else:
            mcp_bearer_token = None

        if os.environ.get("MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL") is not None:
            mcp_public_base_url = get_mcp_public_base_url()
        else:
            mcp_public_base_url = settings.mcp.public_https_base_url or get_mcp_public_base_url() or settings.mcp.tunnel_url or None

        if os.environ.get("MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL") is not None:
            mcp_public_endpoint_url = get_mcp_public_endpoint_url()
        else:
            mcp_public_endpoint_url = settings.mcp.full_mcp_url_override or None

        resolved_mcp_host = _runtime_bind_host(
            get_mcp_host() if os.environ.get("MINIMAL_KANBAN_MCP_HOST") is not None else settings.mcp.mcp_host,
            env_explicit=os.environ.get("MINIMAL_KANBAN_MCP_HOST") is not None,
        )
        resolved_mcp_port = get_mcp_port() if os.environ.get("MINIMAL_KANBAN_MCP_PORT") is not None else settings.mcp.mcp_port
        resolved_mcp_path = get_mcp_path() if os.environ.get("MINIMAL_KANBAN_MCP_PATH") is not None else settings.mcp.mcp_path
        resolved_auth_mode = "bearer" if mcp_bearer_token else "none"
        logger.info(
            "mcp.main.config board_api_url=%s host=%s port=%s path=%s auth_mode=%s public_base_url=%s public_endpoint_url=%s",
            api_base_url,
            resolved_mcp_host,
            resolved_mcp_port,
            resolved_mcp_path,
            resolved_auth_mode,
            mcp_public_base_url or "",
            mcp_public_endpoint_url or "",
        )

        board_api = BoardApiClient(api_base_url, bearer_token=api_bearer_token, logger=logger)
        configured_allowed_hosts = tuple(settings.mcp.allowed_hosts) + _env_list("MINIMAL_KANBAN_MCP_ALLOWED_HOSTS")
        configured_allowed_origins = tuple(settings.mcp.allowed_origins) + _env_list("MINIMAL_KANBAN_MCP_ALLOWED_ORIGINS")
        server = create_mcp_server(
            board_api,
            logger,
            host=resolved_mcp_host,
            port=resolved_mcp_port,
            path=resolved_mcp_path,
            bearer_token=mcp_bearer_token,
            public_base_url=mcp_public_base_url,
            tunnel_url=settings.mcp.tunnel_url or None,
            public_endpoint_url=mcp_public_endpoint_url,
            allowed_hosts=configured_allowed_hosts,
            allowed_origins=configured_allowed_origins,
        )
        mcp_runtime = McpServerRuntime(server, logger, auth_mode=resolved_auth_mode)
        try:
            mcp_runtime.start()
        except Exception as exc:
            logger.exception("mcp.main.start_failed error=%s", exc)
            return 1

        stop_requested = False

        def _stop_requested(_signum, _frame) -> None:
            nonlocal stop_requested
            stop_requested = True

        signal.signal(signal.SIGINT, _stop_requested)
        signal.signal(signal.SIGTERM, _stop_requested)

        while not stop_requested:
            time.sleep(0.2)

        return 0
    finally:
        if mcp_runtime is not None:
            mcp_runtime.stop()
        if embedded_api_server is not None:
            embedded_api_server.stop()
        close_logger(logger)


if __name__ == "__main__":
    raise SystemExit(run())
