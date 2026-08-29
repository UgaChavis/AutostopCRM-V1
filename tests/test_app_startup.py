from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.app import (
    _acquire_instance_guard,
    _launch_desktop_runtime,
    _reset_runtime_publication_state,
    _shutdown_desktop_runtime,
    _start_api_server,
    run,
)
from minimal_kanban.settings_service import SettingsService
from minimal_kanban.settings_store import SettingsStore


class AppStartupTests(unittest.TestCase):
    def test_failed_api_start_attempts_stop_before_returning(self) -> None:
        api_server = Mock()
        api_server.start.side_effect = RuntimeError("api start failed")
        ownership = {"api_server": None}

        with patch.dict(os.environ, {"MINIMAL_KANBAN_SUPPRESS_ERROR_DIALOGS": "1"}):
            result = _start_api_server(
                Mock(),
                None,
                Mock(),
                logging.getLogger("test.app.api_start"),
                Mock(),
                "127.0.0.1",
                41731,
                None,
                Mock(return_value=api_server),
                "Startup error",
                "Could not start",
                ownership=ownership,
            )

        self.assertIsNone(result)
        api_server.stop.assert_called_once_with()
        self.assertIsNone(ownership["api_server"])

    def test_launch_publishes_ownership_before_event_loop_failure(self) -> None:
        app = Mock()
        app.exec.side_effect = RuntimeError("event loop failed")
        api_server = Mock(base_url="http://127.0.0.1:41731", port=41731)
        agent_control = Mock()
        mcp_controller = Mock()
        tunnel_controller = Mock()
        window = Mock()
        ownership = {
            "api_server": None,
            "agent_control": None,
            "mcp_controller": None,
            "tunnel_controller": None,
        }
        modules = {
            "ApiServer": Mock(),
            "start_embedded_agent_runtime": Mock(return_value=agent_control),
            "McpRuntimeController": Mock(return_value=mcp_controller),
            "TunnelRuntimeController": Mock(return_value=tunnel_controller),
            "MainWindow": Mock(),
            "STARTUP_ERROR_TITLE": "Startup",
            "STARTUP_ERROR_MESSAGE": "Startup failure",
            "UNEXPECTED_ERROR_TITLE": "Unexpected",
            "UNEXPECTED_ERROR_MESSAGE": "Unexpected failure",
        }

        with (
            patch("minimal_kanban.app._start_api_server", return_value=api_server),
            patch("minimal_kanban.app._install_unhandled_exception_handler"),
            patch("minimal_kanban.app._show_splash"),
            patch("minimal_kanban.app._build_main_window", return_value=window),
            patch("minimal_kanban.app._detect_network_host", return_value="127.0.0.1"),
            self.assertRaisesRegex(RuntimeError, "event loop failed"),
        ):
            _launch_desktop_runtime(
                app=app,
                splash=None,
                modules=modules,
                logger=logging.getLogger("test.app.launch"),
                service=Mock(),
                operator_service=Mock(),
                settings_service=Mock(),
                api_host="127.0.0.1",
                api_port=41731,
                api_bearer_token=None,
                ownership=ownership,
            )

        self.assertIs(api_server, ownership["api_server"])
        self.assertIs(agent_control, ownership["agent_control"])
        self.assertIs(mcp_controller, ownership["mcp_controller"])
        self.assertIs(tunnel_controller, ownership["tunnel_controller"])

    def test_shutdown_releases_api_and_logger_after_mcp_stop_failure(self) -> None:
        agent_control = Mock()
        mcp_controller = Mock()
        mcp_controller.stop.side_effect = RuntimeError("mcp stop failed")
        api_server = Mock()
        close_logger = Mock()
        logger = logging.getLogger("test.app.shutdown")

        with self.assertRaisesRegex(RuntimeError, "mcp stop failed"):
            _shutdown_desktop_runtime(
                instance_guard=None,
                instance_guard_entered=False,
                splash=None,
                tunnel_controller=None,
                agent_control=agent_control,
                mcp_controller=mcp_controller,
                api_server=api_server,
                logger=logger,
                modules={"close_logger": close_logger},
            )

        agent_control.close.assert_called_once_with()
        api_server.stop.assert_called_once_with()
        close_logger.assert_called_once_with(logger)

    def test_shutdown_releases_later_resources_after_agent_stop_failure(self) -> None:
        events: list[str] = []

        class Guard:
            def __exit__(self, _exc_type, _exc, _traceback) -> None:
                events.append("guard")

        agent_control = Mock()
        agent_control.close.side_effect = RuntimeError("agent stop failed")
        mcp_controller = Mock()
        mcp_controller.stop.side_effect = lambda: events.append("mcp")
        api_server = Mock()
        api_server.stop.side_effect = lambda: events.append("api")
        close_logger = Mock(side_effect=lambda _logger: events.append("logger"))

        with self.assertRaisesRegex(RuntimeError, "agent stop failed"):
            _shutdown_desktop_runtime(
                instance_guard=Guard(),
                instance_guard_entered=True,
                splash=None,
                tunnel_controller=None,
                agent_control=agent_control,
                mcp_controller=mcp_controller,
                api_server=api_server,
                logger=logging.getLogger("test.app.agent_shutdown_failure"),
                modules={"close_logger": close_logger},
            )

        self.assertEqual(["mcp", "api", "logger", "guard"], events)

    def test_shutdown_releases_later_resources_after_tunnel_stop_failure(self) -> None:
        events: list[str] = []

        class Guard:
            def __exit__(self, _exc_type, _exc, _traceback) -> None:
                events.append("guard")

        tunnel_controller = Mock()
        tunnel_controller.stop.side_effect = RuntimeError("tunnel stop failed")
        agent_control = Mock()
        agent_control.close.side_effect = lambda: events.append("agent")
        mcp_controller = Mock()
        mcp_controller.stop.side_effect = lambda: events.append("mcp")
        api_server = Mock()
        api_server.stop.side_effect = lambda: events.append("api")
        close_logger = Mock(side_effect=lambda _logger: events.append("logger"))

        with (
            patch("minimal_kanban.app._stop_tunnel_on_exit", return_value=True),
            self.assertRaisesRegex(RuntimeError, "tunnel stop failed"),
        ):
            _shutdown_desktop_runtime(
                instance_guard=Guard(),
                instance_guard_entered=True,
                splash=None,
                tunnel_controller=tunnel_controller,
                agent_control=agent_control,
                mcp_controller=mcp_controller,
                api_server=api_server,
                logger=logging.getLogger("test.app.tunnel_shutdown_failure"),
                modules={"close_logger": close_logger},
            )

        self.assertEqual(["agent", "mcp", "api", "logger", "guard"], events)

    def test_shutdown_stops_tunnel_when_default_preserve_fails(self) -> None:
        events: list[str] = []
        tunnel_controller = Mock()
        tunnel_controller.preserve_for_reuse.side_effect = RuntimeError("tunnel preserve failed")
        tunnel_controller.stop.side_effect = lambda: events.append("tunnel_stop")
        agent_control = Mock()
        agent_control.close.side_effect = lambda: events.append("agent")
        close_logger = Mock(side_effect=lambda _logger: events.append("logger"))

        with (
            patch("minimal_kanban.app._stop_tunnel_on_exit", return_value=False),
            self.assertRaisesRegex(RuntimeError, "tunnel preserve failed"),
        ):
            _shutdown_desktop_runtime(
                instance_guard=None,
                instance_guard_entered=False,
                splash=None,
                tunnel_controller=tunnel_controller,
                agent_control=agent_control,
                mcp_controller=None,
                api_server=None,
                logger=logging.getLogger("test.app.tunnel_preserve_failure"),
                modules={"close_logger": close_logger},
            )

        tunnel_controller.preserve_for_reuse.assert_called_once_with()
        tunnel_controller.stop.assert_called_once_with()
        self.assertEqual(["tunnel_stop", "agent", "logger"], events)

    def test_shutdown_releases_instance_guard_last(self) -> None:
        events: list[str] = []

        class Guard:
            def __exit__(self, _exc_type, _exc, _traceback) -> None:
                events.append("guard")

        agent_control = Mock()
        agent_control.close.side_effect = lambda: events.append("agent")
        api_server = Mock()
        api_server.stop.side_effect = lambda: events.append("api")
        close_logger = Mock(side_effect=lambda _logger: events.append("logger"))

        _shutdown_desktop_runtime(
            instance_guard=Guard(),
            instance_guard_entered=True,
            splash=None,
            tunnel_controller=None,
            agent_control=agent_control,
            mcp_controller=None,
            api_server=api_server,
            logger=logging.getLogger("test.app.shutdown_order"),
            modules={"close_logger": close_logger},
        )

        self.assertEqual(["agent", "api", "logger", "guard"], events)

    def test_run_shows_splash_before_loading_heavy_runtime_modules(self) -> None:
        events: list[str] = []

        class Guard:
            def __enter__(self):
                events.append("guard")

            def __exit__(self, exc_type, exc, tb):
                events.append("guard_exit")

        class Splash:
            def show(self) -> None:
                events.append("splash_show")

            def isVisible(self) -> bool:
                return False

        def create_qt_runtime():
            events.append("qt")
            return object(), True, Splash()

        def show_splash(_app, _splash, message: str) -> None:
            events.append(f"message:{message}")

        def load_runtime_modules():
            events.append("runtime_imports")
            raise RuntimeError("stop after import-order proof")

        with (
            patch("minimal_kanban.app._acquire_instance_guard", return_value=Guard()),
            patch("minimal_kanban.app._create_qt_runtime", side_effect=create_qt_runtime),
            patch("minimal_kanban.app._show_splash", side_effect=show_splash),
            patch("minimal_kanban.app._load_runtime_modules", side_effect=load_runtime_modules),
            patch("minimal_kanban.app._shutdown_desktop_runtime"),
            self.assertRaisesRegex(RuntimeError, "import-order proof"),
        ):
            run()

        self.assertLess(events.index("splash_show"), events.index("runtime_imports"))
        self.assertLess(
            events.index("message:Загружаю компоненты..."),
            events.index("runtime_imports"),
        )

    def test_instance_guard_rejects_second_running_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"APPDATA": temp_dir}, clear=False):
                first = _acquire_instance_guard()
                first.__enter__()
                second = None
                second_entered = False
                try:
                    with self.assertRaises(TimeoutError):
                        second = _acquire_instance_guard()
                        second.__enter__()
                        second_entered = True
                finally:
                    if second is not None and second_entered:
                        second.__exit__(None, None, None)
                    first.__exit__(None, None, None)

    def test_reset_runtime_publication_state_clears_stale_tunnel_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_service = SettingsService(
                SettingsStore(
                    settings_file=settings_path, logger=logging.getLogger("test.settings")
                ),
                logging.getLogger("test.app"),
            )
            settings = settings_service.load()
            settings = settings_service.save(
                replace(
                    settings,
                    mcp=replace(settings.mcp, tunnel_url="https://stale.trycloudflare.com"),
                )
            )

            with patch(
                "minimal_kanban.desktop_connector_files.write_pending_connector_files"
            ) as write_pending:
                updated = _reset_runtime_publication_state(settings_service, settings)

            self.assertEqual(updated.mcp.tunnel_url, "")
            self.assertEqual(updated.mcp.effective_mcp_url, updated.mcp.local_mcp_url)
            self.assertEqual(settings_service.load().mcp.tunnel_url, "")
            write_pending.assert_called_once()

    def test_reset_runtime_publication_state_keeps_configured_public_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "settings.json"
            settings_service = SettingsService(
                SettingsStore(
                    settings_file=settings_path, logger=logging.getLogger("test.settings")
                ),
                logging.getLogger("test.app"),
            )
            settings = settings_service.load()
            settings = settings_service.save(
                replace(
                    settings,
                    mcp=replace(
                        settings.mcp,
                        public_https_base_url="https://kanban.example",
                        tunnel_url="https://stale.trycloudflare.com",
                    ),
                )
            )

            with patch(
                "minimal_kanban.desktop_connector_files.write_pending_connector_files"
            ) as write_pending:
                updated = _reset_runtime_publication_state(settings_service, settings)

            self.assertEqual(updated.mcp.tunnel_url, "")
            self.assertEqual(updated.mcp.effective_mcp_url, "https://kanban.example/mcp")
            write_pending.assert_not_called()

    def test_run_does_not_call_exit_when_instance_guard_enter_fails(self) -> None:
        class BrokenGuard:
            def __init__(self) -> None:
                self.exit_calls = 0

            def __enter__(self):
                raise RuntimeError("guard enter failed")

            def __exit__(self, exc_type, exc, tb):
                self.exit_calls += 1

        guard = BrokenGuard()
        with patch("minimal_kanban.app._acquire_instance_guard", return_value=guard):
            with self.assertRaises(RuntimeError):
                run()
        self.assertEqual(guard.exit_calls, 0)


if __name__ == "__main__":
    unittest.main()
