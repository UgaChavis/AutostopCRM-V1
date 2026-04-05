from __future__ import annotations

from dataclasses import dataclass
from logging import Logger

from .mcp.client import BoardApiClient
from .mcp.runtime import McpRuntimeStartupError, McpServerRuntime
from .mcp.server import create_mcp_server
from .settings_models import IntegrationSettings


@dataclass(slots=True, frozen=True)
class McpRuntimeState:
    running: bool
    runtime_url: str
    message: str
    error: str = ""
    details: str = ""


class McpRuntimeController:
    def __init__(self, *, board_api_url: str, logger: Logger) -> None:
        self._board_api_url = board_api_url.rstrip("/")
        self._logger = logger.getChild("mcp.runtime_control")
        self._runtime: McpServerRuntime | None = None
        self._state = McpRuntimeState(
            running=False,
            runtime_url="",
            message="MCP сервер не запущен.",
            error="",
            details="",
        )

    @property
    def state(self) -> McpRuntimeState:
        return self._state

    def start(self, settings: IntegrationSettings) -> McpRuntimeState:
        if self._runtime is not None:
            self._state = McpRuntimeState(
                running=True,
                runtime_url=self._runtime.base_url,
                message="MCP сервер уже запущен.",
                error="",
                details="",
            )
            return self._state

        board_api = BoardApiClient(
            self._board_api_url,
            bearer_token=self._local_api_token(settings),
            timeout_seconds=float(settings.openai.timeout_seconds),
            logger=self._logger,
        )
        server = create_mcp_server(
            board_api,
            self._logger,
            host=settings.mcp.mcp_host,
            port=settings.mcp.mcp_port,
            path=settings.mcp.mcp_path,
            bearer_token=self._mcp_token(settings),
            public_base_url=settings.mcp.public_https_base_url or settings.mcp.tunnel_url or None,
            tunnel_url=settings.mcp.tunnel_url or None,
            public_endpoint_url=settings.mcp.full_mcp_url_override or None,
            allowed_hosts=settings.mcp.allowed_hosts,
            allowed_origins=settings.mcp.allowed_origins,
        )
        runtime = McpServerRuntime(server, self._logger, auth_mode=settings.mcp.mcp_auth_mode)
        try:
            runtime.start()
        except Exception as exc:
            self._logger.exception("mcp.start_failed error=%s", exc)
            if isinstance(exc, McpRuntimeStartupError):
                message = exc.user_message
                details = exc.technical_details
            else:
                message = "Ошибка запуска MCP сервера."
                details = str(exc)
            self._state = McpRuntimeState(
                running=False,
                runtime_url="",
                message=message,
                error=details,
                details=details,
            )
            return self._state

        self._runtime = runtime
        self._state = McpRuntimeState(
            running=True,
            runtime_url=runtime.base_url,
            message=f"MCP сервер запущен: {runtime.base_url}",
            error="",
            details="",
        )
        self._logger.info("mcp.start_ok url=%s", runtime.base_url)
        return self._state

    def stop(self) -> McpRuntimeState:
        if self._runtime is None:
            self._state = McpRuntimeState(
                running=False,
                runtime_url="",
                message="MCP сервер уже остановлен.",
                error="",
                details="",
            )
            return self._state

        runtime = self._runtime
        self._runtime = None
        runtime.stop()
        self._state = McpRuntimeState(
            running=False,
            runtime_url="",
            message="MCP сервер остановлен.",
            error="",
            details="",
        )
        self._logger.info("mcp.stop_ok")
        return self._state

    def restart(self, settings: IntegrationSettings) -> McpRuntimeState:
        self.stop()
        return self.start(settings)

    def _local_api_token(self, settings: IntegrationSettings) -> str | None:
        if settings.local_api.local_api_auth_mode != "bearer":
            return None
        return settings.local_api.local_api_bearer_token or settings.auth.local_api_bearer_token or settings.auth.access_token or None

    def _mcp_token(self, settings: IntegrationSettings) -> str | None:
        if settings.mcp.mcp_auth_mode != "bearer":
            return None
        return settings.mcp.mcp_bearer_token or settings.auth.mcp_bearer_token or settings.auth.access_token or None
