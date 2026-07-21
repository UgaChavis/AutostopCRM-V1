from __future__ import annotations

import logging
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.app import _acquire_instance_guard, _reset_runtime_publication_state, run
from minimal_kanban.settings_service import SettingsService
from minimal_kanban.settings_store import SettingsStore


class AppStartupTests(unittest.TestCase):
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
