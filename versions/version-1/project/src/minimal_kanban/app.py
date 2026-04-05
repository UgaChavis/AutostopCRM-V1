from __future__ import annotations

import os
import socket
import sys
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox

from .api.server import ApiServer
from .config import get_api_bearer_token, get_api_host, get_api_port
from .integration_runtime import McpRuntimeController
from .logging_setup import close_logger, configure_logging
from .settings_service import SettingsService
from .settings_store import SettingsStore
from .services.card_service import CardService
from .storage.json_store import JsonStore
from .texts import (
    APP_DISPLAY_NAME,
    STARTUP_ERROR_MESSAGE,
    STARTUP_ERROR_TITLE,
    UNEXPECTED_ERROR_MESSAGE,
    UNEXPECTED_ERROR_TITLE,
)
from .ui.main_window import MainWindow


def _suppress_error_dialogs() -> bool:
    return os.environ.get("MINIMAL_KANBAN_SUPPRESS_ERROR_DIALOGS", "").strip() == "1"


def _detect_network_host() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        detected = sock.getsockname()[0]
        if detected and not detected.startswith("127."):
            return detected
    except OSError:
        pass
    finally:
        sock.close()
    try:
        hostname = socket.gethostname()
        candidate = socket.gethostbyname(hostname)
        if candidate and not candidate.startswith("127."):
            return candidate
    except OSError:
        pass
    return "127.0.0.1"


def run() -> int:
    logger = configure_logging()
    store = JsonStore(logger=logger)
    service = CardService(store, logger)
    settings_store = SettingsStore(logger=logger)
    settings_service = SettingsService(settings_store, logger)
    settings = settings_service.load()
    try:
        service.ensure_demo_board()
    except Exception as exc:
        logger.exception("failed_to_seed_demo_board error=%s", exc)
    host_from_env = os.environ.get("MINIMAL_KANBAN_API_HOST")
    api_host = get_api_host() if host_from_env is not None else settings.local_api.local_api_host
    if host_from_env is None and api_host in {"127.0.0.1", "localhost"}:
        api_host = "0.0.0.0"
    api_port = get_api_port() if os.environ.get("MINIMAL_KANBAN_API_PORT") is not None else settings.local_api.local_api_port
    if os.environ.get("MINIMAL_KANBAN_API_BEARER_TOKEN") is not None:
        api_bearer_token = get_api_bearer_token()
    elif settings.local_api.local_api_auth_mode == "bearer":
        api_bearer_token = settings.local_api.local_api_bearer_token or settings.auth.local_api_bearer_token or settings.auth.access_token or None
    else:
        api_bearer_token = None
    api_server = ApiServer(
        service,
        logger,
        host=api_host,
        start_port=api_port,
        bearer_token=api_bearer_token,
    )

    try:
        try:
            api_server.start()
        except Exception as exc:
            logger.exception("failed_to_start_api error=%s", exc)
            if _suppress_error_dialogs():
                return 1
            app = QApplication(sys.argv)
            app.setApplicationName(APP_DISPLAY_NAME)
            QMessageBox.critical(None, STARTUP_ERROR_TITLE, STARTUP_ERROR_MESSAGE)
            return 1

        app = QApplication(sys.argv)
        app.setApplicationName(APP_DISPLAY_NAME)
        app.setApplicationDisplayName(APP_DISPLAY_NAME)
        app.setQuitOnLastWindowClosed(True)

        def _handle_exception(exc_type, exc_value, exc_traceback) -> None:
            logger.exception(
                "unhandled_exception type=%s error=%s traceback=%s",
                exc_type.__name__,
                exc_value,
                "".join(traceback.format_tb(exc_traceback)),
            )
            if _suppress_error_dialogs():
                return
            QMessageBox.critical(
                None,
                UNEXPECTED_ERROR_TITLE,
                UNEXPECTED_ERROR_MESSAGE,
            )

        sys.excepthook = _handle_exception

        mcp_controller: McpRuntimeController | None = None
        try:
            mcp_controller = McpRuntimeController(board_api_url=api_server.base_url, logger=logger)
            local_board_url = api_server.base_url
            network_board_url = f"http://{_detect_network_host()}:{api_server.port}"
            window = MainWindow(local_board_url, network_board_url, settings_service, mcp_controller=mcp_controller)
            window.show()
            return app.exec()
        finally:
            if mcp_controller is not None:
                mcp_controller.stop()
            api_server.stop()
    finally:
        close_logger(logger)
