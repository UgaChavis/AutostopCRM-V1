from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time
import traceback
from logging import Logger

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP

from ..logging_setup import configure_mcp_startup_logger


_READY_HTTP_STATUSES = {200, 204, 307, 308, 400, 401, 403, 405, 406}


class McpRuntimeStartupError(RuntimeError):
    def __init__(self, user_message: str, technical_details: str = "") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.technical_details = technical_details or user_message


class McpServerRuntime:
    def __init__(self, server: FastMCP, logger: Logger, *, auth_mode: str = "none") -> None:
        self._server = server
        self._logger = logger
        self._startup_logger = configure_mcp_startup_logger()
        self._thread: threading.Thread | None = None
        self._uvicorn_server: uvicorn.Server | None = None
        self._startup_error: BaseException | None = None
        self._startup_traceback = ""
        self.logging_mode = "unconfigured"
        self.host = server.settings.host
        self.port = server.settings.port
        self.path = server.settings.streamable_http_path
        self.auth_mode = auth_mode

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"

    def start(self) -> None:
        if self._thread is not None:
            return

        self._startup_error = None
        self._startup_traceback = ""
        self.logging_mode = self._configure_uvicorn_loggers()
        self._log(
            logging.INFO,
            "mcp.start.begin host=%s port=%s path=%s auth_mode=%s logging_mode=%s",
            self.host,
            self.port,
            self.path,
            self.auth_mode,
            self.logging_mode,
        )

        app = self._server.streamable_http_app()
        config = uvicorn.Config(
            app=app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
            log_config=None,
        )
        self._uvicorn_server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._run_server, name="minimal-kanban-mcp", daemon=True)
        self._thread.start()

        try:
            self._wait_until_port_is_bound()
            self._wait_until_endpoint_is_ready()
        except Exception:
            self._stop_failed_start()
            raise

        self._log(logging.INFO, "mcp.start.ready url=%s", self.base_url)
        self._logger.info("mcp_server_started url=%s", self.base_url)

    def stop(self) -> None:
        if self._uvicorn_server is None:
            return
        self._uvicorn_server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._thread = None
        self._uvicorn_server = None
        self._log(logging.INFO, "mcp.stop.complete")
        self._logger.info("mcp_server_stopped")

    def _run_server(self) -> None:
        assert self._uvicorn_server is not None
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._uvicorn_server.serve())
        except BaseException as exc:  # pragma: no cover - thread safety path
            self._startup_error = exc
            self._startup_traceback = traceback.format_exc()
            self._log(
                logging.ERROR,
                "mcp.start.thread_failed error=%s traceback=%s",
                exc,
                self._startup_traceback,
            )
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()

    def _wait_until_port_is_bound(self, timeout_seconds: float = 10.0) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            self._raise_if_start_failed()
            if self._thread is not None and not self._thread.is_alive():
                raise self._build_startup_error(
                    RuntimeError("Поток MCP runtime завершился до открытия порта."),
                )
            if self._is_port_open():
                self._log(logging.INFO, "mcp.start.port_bound host=%s port=%s", self.host, self.port)
                return
            time.sleep(0.1)
        raise McpRuntimeStartupError(
            "Ошибка запуска MCP сервера. Порт не начал слушать.",
            f"MCP runtime не открыл порт {self.host}:{self.port} за {timeout_seconds:.1f} сек.",
        )

    def _wait_until_endpoint_is_ready(self, timeout_seconds: float = 10.0) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            self._raise_if_start_failed()
            ready, status_code, detail = self._probe_endpoint_once()
            if ready:
                self._log(
                    logging.INFO,
                    "mcp.start.endpoint_ready url=%s status=%s detail=%s",
                    self.base_url,
                    status_code,
                    detail,
                )
                return
            time.sleep(0.15)
        raise McpRuntimeStartupError(
            "Ошибка запуска MCP сервера. Endpoint /mcp не отвечает.",
            f"MCP endpoint {self.base_url} не прошёл readiness probe. Последняя деталь: {detail}",
        )

    def _probe_endpoint_once(self) -> tuple[bool, int | None, str]:
        try:
            response = httpx.get(self.base_url, follow_redirects=False, timeout=0.75)
        except httpx.HTTPError as exc:
            return False, None, str(exc)
        detail = response.reason_phrase or response.text[:160]
        return response.status_code in _READY_HTTP_STATUSES, response.status_code, detail

    def _raise_if_start_failed(self) -> None:
        if self._startup_error is None:
            return
        raise self._build_startup_error(self._startup_error)

    def _build_startup_error(self, exc: BaseException) -> McpRuntimeStartupError:
        details = self._startup_traceback or str(exc) or exc.__class__.__name__
        if "Unable to configure formatter 'default'" in details:
            return McpRuntimeStartupError(
                "Ошибка запуска MCP сервера. Проблема в конфигурации логирования.",
                details,
            )
        return McpRuntimeStartupError(
            "Ошибка запуска MCP сервера. Подробности сохранены в журнале MCP.",
            details,
        )

    def _stop_failed_start(self) -> None:
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        self._uvicorn_server = None

    def _is_port_open(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((self.host, self.port)) == 0

    def _configure_uvicorn_loggers(self) -> str:
        try:
            return self._share_app_handlers_with_uvicorn()
        except Exception as exc:  # pragma: no cover - defensive fallback
            self._install_fallback_uvicorn_logging(exc)
            return "basic_stream_fallback"

    def _share_app_handlers_with_uvicorn(self) -> str:
        handlers = self._collect_effective_handlers()
        if not handlers:
            raise RuntimeError("У основного logger нет доступных handlers.")

        for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            target = logging.getLogger(logger_name)
            target.handlers.clear()
            for handler in handlers:
                target.addHandler(handler)
            target.setLevel(logging.INFO if logger_name != "uvicorn.access" else logging.WARNING)
            target.propagate = False

        self._log(logging.DEBUG, "mcp.start.logging_config mode=shared_app_handlers handlers=%s", len(handlers))
        return "shared_app_handlers"

    def _collect_effective_handlers(self) -> list[logging.Handler]:
        current: Logger | None = self._logger
        while current is not None:
            if current.handlers:
                return list(current.handlers)
            if not current.propagate:
                break
            current = current.parent
        return []

    def _install_fallback_uvicorn_logging(self, exc: Exception) -> None:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)

        for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            target = logging.getLogger(logger_name)
            target.handlers.clear()
            target.addHandler(handler)
            target.setLevel(logging.INFO if logger_name != "uvicorn.access" else logging.WARNING)
            target.propagate = False

        self._log(
            logging.WARNING,
            "mcp.start.logging_fallback reason=%s",
            exc,
        )

    def _log(self, level: int, message: str, *args) -> None:
        self._logger.log(level, message, *args)
        self._startup_logger.log(level, message, *args)
