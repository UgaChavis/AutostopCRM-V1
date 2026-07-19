from __future__ import annotations

import os
import socket
import sys
import traceback
from dataclasses import replace
from hashlib import sha1


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
        mutex_name = (
            f"Local\\{APP_SLUG}-{sha1(app_data_key, usedforsecurity=False).hexdigest()[:16]}"
        )
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(None, True, mutex_name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed.")
        if ctypes.get_last_error() == 183:
            kernel32.CloseHandle(handle)
            raise TimeoutError("Экземпляр AutoStop CRM уже запущен.")

        class _WindowsMutexGuard:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                kernel32.ReleaseMutex(handle)
                kernel32.CloseHandle(handle)

        return _WindowsMutexGuard()

    return ProcessFileLock(get_app_data_dir() / "app.instance.lock", timeout_seconds=0.0).acquire()


def _create_qt_runtime():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPixmap
    from PySide6.QtWidgets import QApplication, QSplashScreen

    from .texts import APP_DISPLAY_NAME

    app = QApplication.instance()
    app_created = app is None
    if app is None:
        app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setQuitOnLastWindowClosed(True)

    splash = None
    if app_created:
        splash_pixmap = QPixmap(480, 170)
        splash_pixmap.fill(QColor("#18211b"))
        splash = QSplashScreen(splash_pixmap, Qt.WindowType.WindowStaysOnTopHint)
        splash.setFont(QFont("Segoe UI", 10))
    return app, app_created, splash


def _show_splash(app, splash, message: str) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor

    from .texts import APP_DISPLAY_NAME

    if splash is None:
        return
    splash.showMessage(
        f"{APP_DISPLAY_NAME}\n{message}",
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom),
        QColor("#f1efe4"),
    )
    app.processEvents()


def _close_splash(app, splash) -> None:
    if splash is None:
        return
    splash.close()
    splash.deleteLater()
    app.processEvents()


def _load_runtime_modules():
    from .agent.bootstrap import start_embedded_agent_runtime
    from .api.server import ApiServer
    from .config import get_api_bearer_token, get_api_host, get_api_port
    from .integration_runtime import McpRuntimeController
    from .logging_setup import close_logger, configure_logging
    from .operator_activity import OperatorActivityService
    from .operator_auth import OperatorAuthService
    from .services.card_service import CardService
    from .settings_service import SettingsService
    from .settings_store import SettingsStore
    from .storage.json_store import JsonStore
    from .texts import (
        STARTUP_ERROR_MESSAGE,
        STARTUP_ERROR_TITLE,
        UNEXPECTED_ERROR_MESSAGE,
        UNEXPECTED_ERROR_TITLE,
    )
    from .tunnel_runtime import TunnelRuntimeController
    from .ui.main_window import MainWindow

    return {
        "start_embedded_agent_runtime": start_embedded_agent_runtime,
        "ApiServer": ApiServer,
        "get_api_bearer_token": get_api_bearer_token,
        "get_api_host": get_api_host,
        "get_api_port": get_api_port,
        "McpRuntimeController": McpRuntimeController,
        "close_logger": close_logger,
        "configure_logging": configure_logging,
        "OperatorActivityService": OperatorActivityService,
        "OperatorAuthService": OperatorAuthService,
        "CardService": CardService,
        "SettingsService": SettingsService,
        "SettingsStore": SettingsStore,
        "JsonStore": JsonStore,
        "TunnelRuntimeController": TunnelRuntimeController,
        "MainWindow": MainWindow,
        "STARTUP_ERROR_MESSAGE": STARTUP_ERROR_MESSAGE,
        "STARTUP_ERROR_TITLE": STARTUP_ERROR_TITLE,
        "UNEXPECTED_ERROR_MESSAGE": UNEXPECTED_ERROR_MESSAGE,
        "UNEXPECTED_ERROR_TITLE": UNEXPECTED_ERROR_TITLE,
    }


def _resolve_api_runtime_config(settings, get_api_host, get_api_port, get_api_bearer_token):
    host_from_env = os.environ.get("MINIMAL_KANBAN_API_HOST")
    api_host = get_api_host() if host_from_env is not None else settings.local_api.local_api_host
    if host_from_env is None and api_host in {"127.0.0.1", "localhost"}:
        api_host = "0.0.0.0"
    api_port = (
        get_api_port()
        if os.environ.get("MINIMAL_KANBAN_API_PORT") is not None
        else settings.local_api.local_api_port
    )
    if os.environ.get("MINIMAL_KANBAN_API_BEARER_TOKEN") is not None:
        api_bearer_token = get_api_bearer_token()
    elif settings.local_api.local_api_auth_mode == "bearer":
        api_bearer_token = (
            settings.local_api.local_api_bearer_token
            or settings.auth.local_api_bearer_token
            or settings.auth.access_token
            or None
        )
    else:
        api_bearer_token = None
    return api_host, api_port, api_bearer_token


def _start_api_server(
    app,
    splash,
    service,
    logger,
    operator_service,
    api_host,
    api_port,
    api_bearer_token,
    ApiServer,
    STARTUP_ERROR_TITLE,
    STARTUP_ERROR_MESSAGE,
):
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
        _close_splash(app, splash)
        from PySide6.QtWidgets import QMessageBox

        if _suppress_error_dialogs():
            return None
        QMessageBox.critical(None, STARTUP_ERROR_TITLE, STARTUP_ERROR_MESSAGE)
        return None
    return api_server


def _install_unhandled_exception_handler(logger, UNEXPECTED_ERROR_TITLE, UNEXPECTED_ERROR_MESSAGE):
    from PySide6.QtWidgets import QMessageBox

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


def _build_main_window(
    MainWindow,
    settings_service,
    mcp_controller,
    tunnel_controller,
    local_board_url: str,
    network_board_url: str,
):
    return MainWindow(
        local_board_url,
        network_board_url,
        settings_service,
        mcp_controller=mcp_controller,
        tunnel_controller=tunnel_controller,
    )


def _prepare_desktop_runtime(modules):
    logger = modules["configure_logging"]()
    store = modules["JsonStore"](logger=logger)
    service = modules["CardService"](store, logger)
    operator_activity_service = modules["OperatorActivityService"](logger=logger)
    operator_service = modules["OperatorAuthService"](
        store, service, activity_service=operator_activity_service, logger=logger
    )
    settings_store = modules["SettingsStore"](logger=logger)
    settings_service = modules["SettingsService"](settings_store, logger)
    settings = settings_service.load()
    settings = _reset_runtime_publication_state(settings_service, settings)
    try:
        service.ensure_demo_board()
    except Exception as exc:
        logger.exception("failed_to_seed_demo_board error=%s", exc)
    api_host, api_port, api_bearer_token = _resolve_api_runtime_config(
        settings,
        modules["get_api_host"],
        modules["get_api_port"],
        modules["get_api_bearer_token"],
    )
    return (
        logger,
        service,
        operator_service,
        settings_service,
        settings,
        api_host,
        api_port,
        api_bearer_token,
    )


def _launch_desktop_runtime(
    *,
    app,
    splash,
    modules,
    logger,
    service,
    operator_service,
    settings_service,
    api_host,
    api_port,
    api_bearer_token,
):
    _show_splash(app, splash, "Запускаю локальный API...")
    api_server = _start_api_server(
        app,
        splash,
        service,
        logger,
        operator_service,
        api_host,
        api_port,
        api_bearer_token,
        modules["ApiServer"],
        modules["STARTUP_ERROR_TITLE"],
        modules["STARTUP_ERROR_MESSAGE"],
    )
    if api_server is None:
        return 1, None, None, None, None

    agent_control = modules["start_embedded_agent_runtime"](
        service=service,
        logger=logger,
        board_api_url=api_server.base_url,
    )
    _install_unhandled_exception_handler(
        logger,
        modules["UNEXPECTED_ERROR_TITLE"],
        modules["UNEXPECTED_ERROR_MESSAGE"],
    )

    _show_splash(app, splash, "Готовлю окно...")
    mcp_controller = modules["McpRuntimeController"](
        board_api_url=api_server.base_url, logger=logger
    )
    tunnel_controller = modules["TunnelRuntimeController"](logger=logger)
    window = _build_main_window(
        modules["MainWindow"],
        settings_service,
        mcp_controller,
        tunnel_controller,
        api_server.base_url,
        f"http://{_detect_network_host()}:{api_server.port}",
    )
    window.show()
    app.processEvents()
    if splash is not None:
        splash.finish(window)
    return app.exec(), api_server, agent_control, mcp_controller, tunnel_controller


def _shutdown_desktop_runtime(
    *,
    instance_guard,
    instance_guard_entered: bool,
    splash,
    tunnel_controller,
    agent_control,
    mcp_controller,
    api_server,
    logger,
    modules,
) -> None:
    if instance_guard is not None and instance_guard_entered:
        instance_guard.__exit__(None, None, None)
    if splash is not None and splash.isVisible():
        splash.close()
    if tunnel_controller is not None:
        if _stop_tunnel_on_exit():
            tunnel_controller.stop()
        else:
            tunnel_controller.preserve_for_reuse()
    if agent_control is not None:
        agent_control.close()
    if mcp_controller is not None:
        mcp_controller.stop()
    if api_server is not None:
        api_server.stop()
    if logger is not None:
        modules["close_logger"](logger)


def run() -> int:
    instance_guard = None
    instance_guard_entered = False
    modules = None
    try:
        instance_guard = _acquire_instance_guard()
        instance_guard.__enter__()
        instance_guard_entered = True
    except TimeoutError:
        return 0

    logger = None
    api_server = None
    agent_control = None
    mcp_controller = None
    tunnel_controller = None
    try:
        modules = _load_runtime_modules()
        app, _, splash = _create_qt_runtime()
        if splash is not None:
            splash.show()
        _show_splash(app, splash, "Подготавливаю запуск...")
        (
            logger,
            service,
            operator_service,
            settings_service,
            _settings,
            api_host,
            api_port,
            api_bearer_token,
        ) = _prepare_desktop_runtime(modules)
        (
            exit_code,
            api_server,
            agent_control,
            mcp_controller,
            tunnel_controller,
        ) = _launch_desktop_runtime(
            app=app,
            splash=splash,
            modules=modules,
            logger=logger,
            service=service,
            operator_service=operator_service,
            settings_service=settings_service,
            api_host=api_host,
            api_port=api_port,
            api_bearer_token=api_bearer_token,
        )
        return exit_code
    finally:
        _shutdown_desktop_runtime(
            instance_guard=instance_guard,
            instance_guard_entered=instance_guard_entered,
            splash=splash,
            tunnel_controller=tunnel_controller,
            agent_control=agent_control,
            mcp_controller=mcp_controller,
            api_server=api_server,
            logger=logger,
            modules=modules or {"close_logger": lambda _logger: None},
        )
