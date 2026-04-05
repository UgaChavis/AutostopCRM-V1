from __future__ import annotations

from dataclasses import replace
from hashlib import sha1
import os
import socket
import sys
import traceback


def _suppress_error_dialogs() -> bool:
    return os.environ.get("MINIMAL_KANBAN_SUPPRESS_ERROR_DIALOGS", "").strip() == "1"


def _stop_tunnel_on_exit() -> bool:
    return os.environ.get("MINIMAL_KANBAN_STOP_TUNNEL_ON_EXIT", "").strip() == "1"


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


def _reset_runtime_publication_state(settings_service, settings):
    from .desktop_connector_files import write_pending_connector_files

    updated = settings
    if settings.mcp.tunnel_url:
        updated = settings_service.save(
            replace(
                settings,
                mcp=replace(settings.mcp, tunnel_url=""),
            )
        )
    if not updated.mcp.effective_mcp_url.startswith("https://"):
        try:
            write_pending_connector_files(
                auth_mode=updated.mcp.mcp_auth_mode,
                local_api_url=updated.local_api.effective_local_api_url,
            )
        except OSError:
            pass
    return updated


def _acquire_instance_guard():
    from .config import APP_SLUG, get_app_data_dir
    from .storage.file_lock import ProcessFileLock

    if os.name == "nt":  # pragma: no branch - Windows desktop runtime
        import ctypes

        app_data_key = str(get_app_data_dir()).strip().lower().encode("utf-8")
        mutex_name = f"Local\\{APP_SLUG}-{sha1(app_data_key).hexdigest()[:16]}"
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(None, True, mutex_name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed.")
        if ctypes.get_last_error() == 183:
            kernel32.CloseHandle(handle)
            raise TimeoutError("Minimal Kanban instance is already running.")

        class _WindowsMutexGuard:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                kernel32.ReleaseMutex(handle)
                kernel32.CloseHandle(handle)

        return _WindowsMutexGuard()

    return ProcessFileLock(get_app_data_dir() / "app.instance.lock", timeout_seconds=0.0).acquire()


def run() -> int:
    instance_guard = None
    instance_guard_entered = False
    try:
        instance_guard = _acquire_instance_guard()
        instance_guard.__enter__()
        instance_guard_entered = True
    except TimeoutError:
        return 0

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPixmap
    from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

    from .texts import (
        APP_DISPLAY_NAME,
        STARTUP_ERROR_MESSAGE,
        STARTUP_ERROR_TITLE,
        UNEXPECTED_ERROR_MESSAGE,
        UNEXPECTED_ERROR_TITLE,
    )

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setQuitOnLastWindowClosed(True)

    splash_pixmap = QPixmap(480, 170)
    splash_pixmap.fill(QColor("#18211b"))
    splash = QSplashScreen(splash_pixmap, Qt.WindowType.WindowStaysOnTopHint)
    splash.setFont(QFont("Segoe UI", 10))

    def update_splash(message: str) -> None:
        splash.showMessage(
            f"{APP_DISPLAY_NAME}\n{message}",
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom),
            QColor("#f1efe4"),
        )
        app.processEvents()

    splash.show()
    update_splash("Подготавливаю запуск...")

    logger = None
    api_server = None
    mcp_controller = None
    tunnel_controller = None

    try:
        update_splash("Загружаю модули...")
        from .api.server import ApiServer
        from .config import get_api_bearer_token, get_api_host, get_api_port
        from .integration_runtime import McpRuntimeController
        from .logging_setup import close_logger, configure_logging
        from .operator_auth import OperatorAuthService
        from .settings_service import SettingsService
        from .settings_store import SettingsStore
        from .services.card_service import CardService
        from .storage.json_store import JsonStore
        from .tunnel_runtime import TunnelRuntimeController
        from .ui.main_window import MainWindow

        update_splash("Подготавливаю хранилище...")
        logger = configure_logging()
        store = JsonStore(logger=logger)
        service = CardService(store, logger)
        operator_service = OperatorAuthService(store, service, logger=logger)
        settings_store = SettingsStore(logger=logger)
        settings_service = SettingsService(settings_store, logger)
        settings = settings_service.load()
        settings = _reset_runtime_publication_state(settings_service, settings)

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

        update_splash("Запускаю локальный API...")
        api_server = ApiServer(
            service,
            logger,
            operator_service=operator_service,
            host=api_host,
            start_port=api_port,
            bearer_token=api_bearer_token,
        )
        try:
            api_server.start()
        except Exception as exc:
            logger.exception("failed_to_start_api error=%s", exc)
            splash.close()
            if _suppress_error_dialogs():
                return 1
            QMessageBox.critical(None, STARTUP_ERROR_TITLE, STARTUP_ERROR_MESSAGE)
            return 1

        def _handle_exception(exc_type, exc_value, exc_traceback) -> None:
            assert logger is not None
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

        update_splash("Готовлю окно...")
        mcp_controller = McpRuntimeController(board_api_url=api_server.base_url, logger=logger)
        tunnel_controller = TunnelRuntimeController(logger=logger)
        local_board_url = api_server.base_url
        network_board_url = f"http://{_detect_network_host()}:{api_server.port}"
        window = MainWindow(
            local_board_url,
            network_board_url,
            settings_service,
            mcp_controller=mcp_controller,
            tunnel_controller=tunnel_controller,
        )
        window.show()
        app.processEvents()
        splash.finish(window)
        return app.exec()
    finally:
        if instance_guard is not None and instance_guard_entered:
            instance_guard.__exit__(None, None, None)
        if splash.isVisible():
            splash.close()
        if tunnel_controller is not None:
            if _stop_tunnel_on_exit():
                tunnel_controller.stop()
            else:
                tunnel_controller.preserve_for_reuse()
        if mcp_controller is not None:
            mcp_controller.stop()
        if api_server is not None:
            api_server.stop()
        if logger is not None:
            from .logging_setup import close_logger

            close_logger(logger)
