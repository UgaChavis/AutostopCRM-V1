from __future__ import annotations

import json
import logging
import os
import signal
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.settings_models import IntegrationSettings  # noqa: E402
from minimal_kanban.tunnel_runtime import TunnelRuntimeController  # noqa: E402


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._body
        return self._body[:size]


class TunnelRuntimeControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(f"test.tunnel.runtime.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.temp_dir = tempfile.TemporaryDirectory()
        self.controller = TunnelRuntimeController(logger=self.logger)
        self.controller._state_file_path = Path(self.temp_dir.name) / "tunnel-state.json"
        self.settings = IntegrationSettings.defaults()

    def tearDown(self) -> None:
        self.controller.stop()
        self.temp_dir.cleanup()

    def _process_identity(
        self,
        *,
        provider: str = "cloudflared",
        started: str = "linux-proc:100",
    ) -> dict[str, str]:
        identity = self.controller._normalize_process_identity(
            {
                "executable": str(Path(self.temp_dir.name) / f"{provider}.exe"),
                "started": started,
            },
            provider=provider,
        )
        self.assertIsNotNone(identity)
        return identity or {}

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
                patch.object(
                    self.controller,
                    "_find_cloudflared_executable",
                    return_value="C:\\cloudflared.exe",
                ),
                patch.object(
                    self.controller, "_find_ngrok_executable", return_value="C:\\ngrok.exe"
                ),
                patch.object(self.controller, "_create_log_file_path", return_value=log_path),
                patch(
                    "minimal_kanban.tunnel_runtime.subprocess.Popen", side_effect=fake_popen
                ) as popen_mock,
                patch("minimal_kanban.tunnel_runtime.time.sleep", return_value=None),
            ):
                state = self.controller.start(self.settings)

        self.assertTrue(state.running)
        self.assertEqual(
            state.public_url, "https://acrylic-arrived-attend-delivery.trycloudflare.com"
        )
        self.assertIn("cloudflared", state.message)
        command = popen_mock.call_args.args[0]
        self.assertEqual(command[:2], ["C:\\cloudflared.exe", "tunnel"])

    def test_start_reuses_existing_https_tunnel_when_ngrok_is_selected(self) -> None:
        with (
            patch.dict("os.environ", {"MINIMAL_KANBAN_TUNNEL_PROVIDER": "ngrok"}),
            patch.object(self.controller, "_find_ngrok_executable", return_value="ngrok"),
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

    def test_fetch_tunnels_payload_rejects_non_object_json(self) -> None:
        with patch(
            "minimal_kanban.tunnel_runtime._urlopen_no_redirect",
            return_value=FakeResponse(b"[]"),
        ):
            payload = self.controller._fetch_tunnels_payload()

        self.assertEqual(payload, {})

    def test_fetch_tunnels_payload_rejects_nonstandard_json_constants(self) -> None:
        with patch(
            "minimal_kanban.tunnel_runtime._urlopen_no_redirect",
            return_value=FakeResponse(b'{"tunnels":[{"public_url":NaN}]}'),
        ):
            payload = self.controller._fetch_tunnels_payload()

        self.assertEqual(payload, {})

    def test_fetch_tunnels_payload_rejects_oversized_response(self) -> None:
        with (
            patch("minimal_kanban.tunnel_runtime.NGROK_INSPECT_RESPONSE_MAX_BYTES", 4),
            patch(
                "minimal_kanban.tunnel_runtime._urlopen_no_redirect",
                return_value=FakeResponse(b"12345"),
            ),
        ):
            payload = self.controller._fetch_tunnels_payload()

        self.assertEqual(payload, {})

    def test_fetch_tunnels_payload_rejects_redirect_response(self) -> None:
        redirect = urllib.error.HTTPError(
            url="http://127.0.0.1:4040/api/tunnels",
            code=302,
            msg="Found",
            hdrs={"Location": "https://example.test/api/tunnels"},
            fp=None,
        )

        with patch("minimal_kanban.tunnel_runtime._urlopen_no_redirect", side_effect=redirect):
            payload = self.controller._fetch_tunnels_payload()

        self.assertEqual(payload, {})

    def test_target_base_url_brackets_ipv6_host(self) -> None:
        settings = IntegrationSettings.from_dict({"mcp": {"mcp_host": "::1", "mcp_port": 41831}})

        self.assertEqual(self.controller._target_base_url(settings), "http://[::1]:41831")

    def test_matches_target_uses_exact_host_and_port(self) -> None:
        self.assertTrue(
            self.controller._matches_target(
                "http://127.0.0.1:41831",
                target_host="127.0.0.1",
                target_port=41831,
            )
        )
        self.assertFalse(
            self.controller._matches_target(
                "http://127.0.0.1:41831",
                target_host="127.0.0.1",
                target_port=4183,
            )
        )
        self.assertTrue(
            self.controller._matches_target(
                "http://[::1]:41831",
                target_host="::1",
                target_port=41831,
            )
        )
        self.assertTrue(
            self.controller._matches_target(
                "http://[::1]:41831",
                target_host="::",
                target_port=41831,
            )
        )
        self.assertTrue(
            self.controller._matches_target(
                "http://localhost.:41831",
                target_host="[::]",
                target_port=41831,
            )
        )
        self.assertFalse(
            self.controller._matches_target(
                "http://evil127.0.0.1:41831",
                target_host="127.0.0.1",
                target_port=41831,
            )
        )

    def test_start_falls_back_to_ngrok_when_cloudflared_is_missing(self) -> None:
        process = Mock()
        process.poll.side_effect = [None, None]
        process.wait.return_value = 0

        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "ngrok.log"

            with (
                patch.object(self.controller, "_find_cloudflared_executable", return_value=None),
                patch.object(
                    self.controller, "_find_ngrok_executable", return_value="C:\\ngrok.exe"
                ),
                patch.object(self.controller, "_create_log_file_path", return_value=log_path),
                patch(
                    "minimal_kanban.tunnel_runtime.subprocess.Popen", return_value=process
                ) as popen_mock,
                patch.object(
                    self.controller,
                    "_probe_existing_ngrok_tunnel",
                    side_effect=["", "https://demo.ngrok-free.app"],
                ),
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

    def test_stop_keeps_normal_owned_process_handle_behavior_without_persisted_identity(
        self,
    ) -> None:
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        process.pid = 5151
        self.controller._process = process
        self.controller._provider = "cloudflared"

        with patch.object(self.controller, "_terminate_pid") as terminate_pid:
            state = self.controller.stop()

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=8)
        terminate_pid.assert_not_called()
        self.assertFalse(state.running)

    def test_stop_logs_when_owned_process_cannot_be_killed(self) -> None:
        process = Mock()
        process.pid = 777
        process.poll.return_value = None
        process.terminate.side_effect = RuntimeError("terminate failed")
        process.kill.side_effect = RuntimeError("kill failed")
        self.controller._process = process
        self.controller._provider = "cloudflared"
        state_path = self.controller._state_file_path
        state_path.write_text('{"pid":777}', encoding="utf-8")

        with (
            self.assertLogs(self.controller._logger, level="WARNING") as captured,
            self.assertRaisesRegex(RuntimeError, "777"),
        ):
            self.controller.stop()

        joined_logs = "\n".join(captured.output)
        self.assertIn("tunnel.process_terminate_failed", joined_logs)
        self.assertIn("tunnel.process_kill_failed", joined_logs)
        self.assertIn("pid=777", joined_logs)
        self.assertIs(process, self.controller._process)
        self.assertEqual(self.controller._provider, "cloudflared")
        self.assertTrue(state_path.exists())

        process.terminate.side_effect = None
        process.kill.side_effect = None
        process.wait.return_value = 0
        self.controller.stop()
        self.assertFalse(state_path.exists())

    def test_stop_retains_ownership_when_persisted_state_cannot_be_cleared(self) -> None:
        process = Mock()
        process.pid = 777
        process.poll.return_value = None
        process.wait.return_value = 0
        self.controller._process = process
        self.controller._persisted_pid = 777
        self.controller._provider = "cloudflared"

        with (
            patch.object(self.controller, "_clear_persisted_state", return_value=False),
            self.assertRaisesRegex(RuntimeError, "persisted state"),
        ):
            self.controller.stop()

        self.assertIs(process, self.controller._process)
        self.assertEqual(self.controller._persisted_pid, 777)
        self.assertEqual(self.controller._provider, "cloudflared")

        process.poll.return_value = 0
        self.controller.stop()

    def test_extract_cloudflared_url_uses_latest_non_api_url(self) -> None:
        log_path = Path(self.temp_dir.name) / "cloudflared.log"
        log_path.write_text(
            "\n".join(
                [
                    "https://first.trycloudflare.com",
                    "https://api.trycloudflare.com",
                    "https://second.trycloudflare.com",
                ]
            ),
            encoding="utf-8",
        )
        self.controller._log_file_path = log_path

        self.assertEqual(
            self.controller._extract_cloudflared_url_from_log(),
            "https://second.trycloudflare.com",
        )

    def test_extract_cloudflared_url_reads_bounded_log_tail(self) -> None:
        log_path = Path(self.temp_dir.name) / "cloudflared-large.log"
        log_path.write_text(
            "older https://old.trycloudflare.com\n"
            + ("x" * 128)
            + "\nlatest https://fresh.trycloudflare.com\n",
            encoding="utf-8",
        )
        self.controller._log_file_path = log_path

        with patch("minimal_kanban.tunnel_runtime.TUNNEL_LOG_TAIL_MAX_BYTES", 64):
            self.assertEqual(
                self.controller._extract_cloudflared_url_from_log(),
                "https://fresh.trycloudflare.com",
            )

    def test_first_existing_executable_ignores_directories_and_strips_quotes(self) -> None:
        directory_candidate = Path(self.temp_dir.name) / "cloudflared-dir.exe"
        directory_candidate.mkdir()
        file_candidate = Path(self.temp_dir.name) / "cloudflared.exe"
        file_candidate.write_text("", encoding="utf-8")

        self.assertEqual(
            self.controller._first_existing_executable(
                [str(directory_candidate), f'"{file_candidate}"']
            ),
            str(file_candidate),
        )

    def test_start_reuses_persisted_cloudflared_state(self) -> None:
        expected_identity = self._process_identity()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "tunnel-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "provider": "cloudflared",
                        "public_url": "https://stable.trycloudflare.com",
                        "pid": 4242,
                        "target_port": self.settings.mcp.mcp_port,
                        "process_identity": expected_identity,
                    }
                ),
                encoding="utf-8",
            )
            self.controller._state_file_path = state_path

            with (
                patch.object(
                    self.controller,
                    "_capture_process_identity",
                    return_value=expected_identity,
                ),
                patch("minimal_kanban.tunnel_runtime.subprocess.Popen") as popen_mock,
            ):
                state = self.controller.start(self.settings)

        self.assertTrue(state.running)
        self.assertEqual(state.public_url, "https://stable.trycloudflare.com")
        self.assertIn("reused", state.message)
        popen_mock.assert_not_called()

    def test_reuse_rejects_live_pid_when_process_identity_changed(self) -> None:
        state_path = Path(self.temp_dir.name) / "tunnel-state.json"
        expected_identity = self._process_identity()
        state_path.write_text(
            json.dumps(
                {
                    "provider": "cloudflared",
                    "public_url": "https://stable.trycloudflare.com",
                    "pid": 4242,
                    "target_port": self.settings.mcp.mcp_port,
                    "process_identity": expected_identity,
                }
            ),
            encoding="utf-8",
        )
        self.controller._state_file_path = state_path

        with patch.object(
            self.controller,
            "_capture_process_identity",
            return_value={
                **expected_identity,
                "started": "linux-proc:200",
            },
        ):
            state = self.controller._reuse_persisted_tunnel(self.settings)

        self.assertIsNone(state)
        self.assertIsNone(self.controller._persisted_pid)
        self.assertFalse(state_path.exists())

    def test_reuse_retains_live_legacy_state_without_process_identity(self) -> None:
        state_path = self.controller._state_file_path
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

        with (
            patch.object(self.controller, "_is_pid_alive", return_value=True),
            self.assertRaisesRegex(RuntimeError, "persisted identity"),
        ):
            self.controller._reuse_persisted_tunnel(self.settings)

        self.assertTrue(state_path.exists())

    def test_reuse_clears_dead_legacy_state_without_process_identity(self) -> None:
        state_path = self.controller._state_file_path
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

        with patch.object(self.controller, "_is_pid_alive", return_value=False):
            state = self.controller._reuse_persisted_tunnel(self.settings)

        self.assertIsNone(state)
        self.assertFalse(state_path.exists())

    def test_stop_never_terminates_reused_pid_after_identity_changes(self) -> None:
        state_path = Path(self.temp_dir.name) / "tunnel-state.json"
        expected_identity = self._process_identity()
        state_path.write_text(
            json.dumps(
                {
                    "provider": "cloudflared",
                    "public_url": "https://stable.trycloudflare.com",
                    "pid": 6262,
                    "target_port": self.settings.mcp.mcp_port,
                    "process_identity": expected_identity,
                }
            ),
            encoding="utf-8",
        )
        self.controller._state_file_path = state_path
        self.controller._provider = "cloudflared"
        self.controller._persisted_pid = 6262

        with (
            patch.object(self.controller, "_is_pid_alive", return_value=True),
            patch.object(
                self.controller,
                "_capture_process_identity",
                return_value={
                    **expected_identity,
                    "started": "linux-proc:200",
                },
            ),
            patch("minimal_kanban.tunnel_runtime.os.kill") as kill_mock,
            patch("minimal_kanban.tunnel_runtime.subprocess.run") as taskkill_mock,
        ):
            state = self.controller.stop()

        kill_mock.assert_not_called()
        taskkill_mock.assert_not_called()
        self.assertFalse(state.running)
        self.assertIsNone(self.controller._persisted_pid)
        self.assertFalse(state_path.exists())

    def test_stop_retains_live_pid_when_identity_query_is_unavailable(self) -> None:
        state_path = self.controller._state_file_path
        expected_identity = self._process_identity()
        state_path.write_text(
            json.dumps(
                {
                    "provider": "cloudflared",
                    "pid": 6262,
                    "process_identity": expected_identity,
                }
            ),
            encoding="utf-8",
        )

        with (
            patch.object(self.controller, "_is_pid_alive", return_value=True),
            patch.object(self.controller, "_capture_process_identity", return_value=None),
            self.assertRaisesRegex(RuntimeError, "could not be verified"),
        ):
            self.controller.stop()

        self.assertEqual(self.controller._provider, "cloudflared")
        self.assertEqual(self.controller._persisted_pid, 6262)
        self.assertTrue(state_path.exists())

    def test_stop_retains_live_legacy_pid_without_process_identity(self) -> None:
        state_path = self.controller._state_file_path
        state_path.write_text(
            json.dumps({"provider": "cloudflared", "pid": 6262}),
            encoding="utf-8",
        )

        with (
            patch.object(self.controller, "_is_pid_alive", return_value=True),
            self.assertRaisesRegex(RuntimeError, "persisted identity"),
        ):
            self.controller.stop()

        self.assertEqual(self.controller._provider, "cloudflared")
        self.assertEqual(self.controller._persisted_pid, 6262)
        self.assertTrue(state_path.exists())

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

            expected_identity = self._process_identity()
            with (
                patch.object(self.controller, "_is_pid_alive", return_value=True),
                patch.object(
                    self.controller,
                    "_capture_process_identity",
                    return_value=expected_identity,
                ),
            ):
                state = self.controller.preserve_for_reuse()

            payload = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertTrue(state.running)
        self.assertEqual(payload["provider"], "cloudflared")
        self.assertEqual(payload["public_url"], "https://stable.trycloudflare.com")
        self.assertEqual(payload["pid"], 5151)
        self.assertEqual(payload["target_port"], self.settings.mcp.mcp_port)
        self.assertEqual(payload["process_identity"], expected_identity)

    def test_preserve_for_reuse_retains_process_when_state_write_fails(self) -> None:
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

        with (
            patch.object(self.controller, "_is_pid_alive", return_value=True),
            patch.object(
                self.controller,
                "_capture_process_identity",
                return_value=self._process_identity(),
            ),
            patch.object(self.controller, "_write_persisted_state", return_value=False),
            self.assertRaisesRegex(RuntimeError, "persist"),
        ):
            self.controller.preserve_for_reuse()

        self.assertIs(process, self.controller._process)
        self.assertEqual(self.controller._persisted_pid, 5151)

    def test_stop_retains_persisted_pid_when_termination_is_uncertain(self) -> None:
        self.controller._provider = "cloudflared"
        self.controller._persisted_pid = 6262
        expected_identity = self._process_identity()
        self.controller._state_file_path.write_text(
            json.dumps(
                {
                    "provider": "cloudflared",
                    "pid": 6262,
                    "process_identity": expected_identity,
                }
            ),
            encoding="utf-8",
        )

        with (
            patch.object(self.controller, "_is_pid_alive", return_value=True),
            patch.object(
                self.controller,
                "_terminate_pid",
                side_effect=RuntimeError("pid termination failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "pid termination failed"),
        ):
            self.controller.stop()

        self.assertEqual(self.controller._persisted_pid, 6262)
        self.assertEqual(self.controller._provider, "cloudflared")

        self.controller._persisted_pid = None
        self.controller._provider = ""

    def test_reuse_persisted_state_rejects_invalid_public_url(self) -> None:
        state_path = Path(self.temp_dir.name) / "tunnel-state.json"
        state_path.write_text(
            json.dumps(
                {
                    "provider": "cloudflared",
                    "public_url": "http://stable.trycloudflare.com",
                    "pid": 4242,
                    "target_port": self.settings.mcp.mcp_port,
                }
            ),
            encoding="utf-8",
        )
        self.controller._state_file_path = state_path

        with patch.object(self.controller, "_is_pid_alive", return_value=True):
            state = self.controller._reuse_persisted_tunnel(self.settings)

        self.assertIsNone(state)
        self.assertFalse(state_path.exists())

    def test_read_persisted_state_rejects_nonstandard_json_constants(self) -> None:
        state_path = Path(self.temp_dir.name) / "tunnel-state.json"
        state_path.write_text('{"provider":"cloudflared","pid":NaN}', encoding="utf-8")
        self.controller._state_file_path = state_path

        self.assertEqual(self.controller._read_persisted_state(), {})

    def test_read_persisted_state_rejects_oversized_state_file(self) -> None:
        state_path = Path(self.temp_dir.name) / "tunnel-state.json"
        state_path.write_text('{"provider":"cloudflared","padding":"xxxxxxxx"}', encoding="utf-8")
        self.controller._state_file_path = state_path

        with patch("minimal_kanban.tunnel_runtime.TUNNEL_STATE_MAX_BYTES", 8):
            self.assertEqual(self.controller._read_persisted_state(), {})

    def test_normalize_pid_rejects_bool_and_fractional_values(self) -> None:
        self.assertIsNone(self.controller._normalize_pid(True))
        self.assertIsNone(self.controller._normalize_pid(12.5))
        self.assertIsNone(self.controller._normalize_pid(float("inf")))
        self.assertIsNone(self.controller._normalize_pid(10**30))
        self.assertEqual(self.controller._normalize_pid(12.0), 12)
        self.assertEqual(self.controller._normalize_pid("42"), 42)

    def test_write_persisted_state_creates_parent_and_writes_valid_json(self) -> None:
        state_path = Path(self.temp_dir.name) / "nested" / "tunnel-state.json"
        self.controller._state_file_path = state_path

        persisted = self.controller._write_persisted_state(
            provider="cloudflared",
            public_url="https://stable.trycloudflare.com",
            pid=5151,
            target_port=self.settings.mcp.mcp_port,
            process_identity=self._process_identity(),
        )

        payload = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["provider"], "cloudflared")
        self.assertEqual(payload["public_url"], "https://stable.trycloudflare.com")
        self.assertEqual(list(state_path.parent.glob("*.tmp")), [])
        self.assertTrue(persisted)

    def test_write_persisted_state_rejects_oversized_payload_without_clobbering(
        self,
    ) -> None:
        state_path = Path(self.temp_dir.name) / "tunnel-state.json"
        original_payload = {"provider": "cloudflared", "public_url": "https://old.example"}
        state_path.write_text(json.dumps(original_payload), encoding="utf-8")
        self.controller._state_file_path = state_path

        with patch("minimal_kanban.tunnel_runtime.TUNNEL_STATE_MAX_BYTES", 128):
            persisted = self.controller._write_persisted_state(
                provider="cloudflared",
                public_url="https://" + "x" * 512 + ".trycloudflare.com",
                pid=5151,
                target_port=self.settings.mcp.mcp_port,
                process_identity=self._process_identity(),
            )

        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), original_payload)
        self.assertEqual(list(state_path.parent.glob("*.tmp")), [])
        self.assertFalse(persisted)

    def test_write_persisted_state_does_not_overwrite_pid_time_tmp_file(self) -> None:
        state_path = Path(self.temp_dir.name) / "tunnel-state.json"
        self.controller._state_file_path = state_path
        fixed_time = 123456.789
        old_style_tmp = state_path.with_name(
            f".{state_path.name}.{os.getpid()}.{int(fixed_time * 1000)}.tmp"
        )
        old_style_tmp.write_text("sentinel", encoding="utf-8")

        with patch("minimal_kanban.tunnel_runtime.time.time", return_value=fixed_time):
            self.controller._write_persisted_state(
                provider="cloudflared",
                public_url="https://stable.trycloudflare.com",
                pid=5151,
                target_port=self.settings.mcp.mcp_port,
                process_identity=self._process_identity(),
            )

        self.assertEqual(old_style_tmp.read_text(encoding="utf-8"), "sentinel")
        old_style_tmp.unlink()
        self.assertEqual(list(state_path.parent.glob("*.tmp")), [])

    def test_stop_terminates_persisted_pid_when_handle_missing(self) -> None:
        expected_identity = self._process_identity()
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "tunnel-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "provider": "cloudflared",
                        "public_url": "https://stable.trycloudflare.com",
                        "pid": 6262,
                        "target_port": self.settings.mcp.mcp_port,
                        "process_identity": expected_identity,
                    }
                ),
                encoding="utf-8",
            )
            self.controller._state_file_path = state_path
            self.controller._provider = "cloudflared"
            self.controller._persisted_pid = 6262

            if os.name == "nt":
                with (
                    patch.object(self.controller, "_is_pid_alive", side_effect=[True, False]),
                    patch.object(
                        self.controller,
                        "_capture_process_identity",
                        return_value=expected_identity,
                    ),
                    patch("minimal_kanban.tunnel_runtime.subprocess.run") as terminate_mock,
                ):
                    state = self.controller.stop()
                terminate_mock.assert_called_once()
                self.assertEqual(terminate_mock.call_args.kwargs["timeout"], 10)
            else:
                with (
                    patch.object(self.controller, "_is_pid_alive", side_effect=[True, False]),
                    patch.object(
                        self.controller,
                        "_capture_process_identity",
                        return_value=expected_identity,
                    ),
                    patch("minimal_kanban.tunnel_runtime.os.kill") as terminate_mock,
                ):
                    state = self.controller.stop()
                terminate_mock.assert_called_once_with(6262, signal.SIGTERM)

        self.assertFalse(state.running)
        self.assertFalse(state_path.exists())


if __name__ == "__main__":
    unittest.main()
