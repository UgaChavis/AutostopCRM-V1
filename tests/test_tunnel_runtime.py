from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import json


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.settings_models import IntegrationSettings
from minimal_kanban.tunnel_runtime import TunnelRuntimeController


class TunnelRuntimeControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(f"test.tunnel.runtime.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.controller = TunnelRuntimeController(logger=self.logger)
        self.settings = IntegrationSettings.defaults()

    def tearDown(self) -> None:
        self.controller.stop()

    def test_start_prefers_cloudflared_and_parses_log_url(self) -> None:
        process = Mock()
        process.poll.side_effect = [None, None, None]
        process.wait.return_value = 0

        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "cloudflared.log"

            def fake_popen(*args, **kwargs):
                stdout = kwargs["stdout"]
                stdout.write(
                    "2026-04-01T22:09:45Z INF Requesting new quick Tunnel on https://api.trycloudflare.com...\n"
                )
                stdout.write(
                    "2026-04-01T22:09:51Z INF |  https://acrylic-arrived-attend-delivery.trycloudflare.com |\n"
                )
                stdout.flush()
                return process

            with (
                patch.object(self.controller, "_find_cloudflared_executable", return_value="C:\\cloudflared.exe"),
                patch.object(self.controller, "_find_ngrok_executable", return_value="C:\\ngrok.exe"),
                patch.object(self.controller, "_create_log_file_path", return_value=log_path),
                patch("minimal_kanban.tunnel_runtime.subprocess.Popen", side_effect=fake_popen) as popen_mock,
                patch("minimal_kanban.tunnel_runtime.time.sleep", return_value=None),
            ):
                state = self.controller.start(self.settings)

        self.assertTrue(state.running)
        self.assertEqual(state.public_url, "https://acrylic-arrived-attend-delivery.trycloudflare.com")
        self.assertIn("cloudflared", state.message)
        command = popen_mock.call_args.args[0]
        self.assertEqual(command[:2], ["C:\\cloudflared.exe", "tunnel"])

    def test_start_reuses_existing_https_tunnel_when_ngrok_is_selected(self) -> None:
        with (
            patch.dict("os.environ", {"MINIMAL_KANBAN_TUNNEL_PROVIDER": "ngrok"}),
            patch.object(
                self.controller,
                "_fetch_tunnels_payload",
                return_value={
                    "tunnels": [
                        {
                            "public_url": "https://demo.ngrok-free.app",
                            "config": {"addr": "http://127.0.0.1:41831"},
                        }
                    ]
                },
            ),
        ):
            state = self.controller.start(self.settings)

        self.assertTrue(state.running)
        self.assertEqual(state.public_url, "https://demo.ngrok-free.app")
        self.assertFalse(state.owns_process)

    def test_start_falls_back_to_ngrok_when_cloudflared_is_missing(self) -> None:
        process = Mock()
        process.poll.side_effect = [None, None]
        process.wait.return_value = 0

        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "ngrok.log"

            with (
                patch.object(self.controller, "_find_cloudflared_executable", return_value=None),
                patch.object(self.controller, "_find_ngrok_executable", return_value="C:\\ngrok.exe"),
                patch.object(self.controller, "_create_log_file_path", return_value=log_path),
                patch("minimal_kanban.tunnel_runtime.subprocess.Popen", return_value=process) as popen_mock,
                patch.object(self.controller, "_probe_existing_ngrok_tunnel", side_effect=["", "https://demo.ngrok-free.app"]),
                patch("minimal_kanban.tunnel_runtime.time.sleep", return_value=None),
            ):
                state = self.controller.start(self.settings)

        self.assertTrue(state.running)
        self.assertEqual(state.public_url, "https://demo.ngrok-free.app")
        command = popen_mock.call_args.args[0]
        self.assertEqual(command[0], "C:\\ngrok.exe")

    def test_stop_terminates_owned_process(self) -> None:
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        self.controller._process = process
        self.controller._provider = "cloudflared"

        state = self.controller.stop()

        process.terminate.assert_called_once_with()
        process.wait.assert_called()
        self.assertFalse(state.running)
        self.assertEqual(state.public_url, "")

    def test_start_reuses_persisted_cloudflared_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "tunnel-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "provider": "cloudflared",
                        "public_url": "https://stable.trycloudflare.com",
                        "pid": 4242,
                        "target_port": self.settings.mcp.mcp_port,
                    }
                ),
                encoding="utf-8",
            )
            self.controller._state_file_path = state_path

            with (
                patch.object(self.controller, "_is_pid_alive", return_value=True),
                patch("minimal_kanban.tunnel_runtime.subprocess.Popen") as popen_mock,
            ):
                state = self.controller.start(self.settings)

        self.assertTrue(state.running)
        self.assertEqual(state.public_url, "https://stable.trycloudflare.com")
        self.assertIn("reused", state.message)
        popen_mock.assert_not_called()

    def test_preserve_for_reuse_writes_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "tunnel-state.json"
            self.controller._state_file_path = state_path
            self.controller._provider = "cloudflared"
            self.controller._target_port = self.settings.mcp.mcp_port
            process = Mock()
            process.poll.return_value = None
            process.pid = 5151
            self.controller._process = process
            self.controller._state = self.controller.state.__class__(
                running=True,
                public_url="https://stable.trycloudflare.com",
                message="Tunnel started.",
                owns_process=True,
            )

            with patch.object(self.controller, "_is_pid_alive", return_value=True):
                state = self.controller.preserve_for_reuse()

            payload = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertTrue(state.running)
        self.assertEqual(payload["provider"], "cloudflared")
        self.assertEqual(payload["public_url"], "https://stable.trycloudflare.com")
        self.assertEqual(payload["pid"], 5151)
        self.assertEqual(payload["target_port"], self.settings.mcp.mcp_port)

    def test_stop_terminates_persisted_pid_when_handle_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "tunnel-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "provider": "cloudflared",
                        "public_url": "https://stable.trycloudflare.com",
                        "pid": 6262,
                        "target_port": self.settings.mcp.mcp_port,
                    }
                ),
                encoding="utf-8",
            )
            self.controller._state_file_path = state_path
            self.controller._provider = "cloudflared"
            self.controller._persisted_pid = 6262

            with (
                patch.object(self.controller, "_is_pid_alive", return_value=True),
                patch("minimal_kanban.tunnel_runtime.subprocess.run") as run_mock,
            ):
                state = self.controller.stop()

        self.assertFalse(state.running)
        self.assertFalse(state_path.exists())
        run_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
