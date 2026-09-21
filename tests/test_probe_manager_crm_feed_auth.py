from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import probe_manager_crm_feed_auth as feed_probe


def _manager_env(path: Path, token: str) -> None:
    path.write_text(
        f"{feed_probe.MANAGER_URL_KEY}={feed_probe.EXPECTED_MCP_URL}\n"
        f"{feed_probe.MANAGER_TOKEN_KEY}={token}\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


class ManagerCrmFeedAuthProbeTests(unittest.TestCase):
    def test_probe_requires_live_scheduler_credential_and_returns_no_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token = "release-token-" + "a" * 48
            manager_env = Path(directory) / ".crm-mcp.env"
            _manager_env(manager_env, token)
            configured = {
                feed_probe.MANAGER_URL_KEY: feed_probe.EXPECTED_MCP_URL,
                feed_probe.MANAGER_TOKEN_KEY: token,
            }
            pids = iter((4312, 4312))
            observed: list[str] = []
            with (
                mock.patch.object(
                    feed_probe, "_read_bounded_file", return_value=manager_env.read_bytes()
                ),
                mock.patch.object(
                    feed_probe, "_service_main_pid", side_effect=lambda _unit: next(pids)
                ),
                mock.patch.object(
                    feed_probe, "_live_manager_environment", return_value=configured.copy()
                ),
                mock.patch.object(
                    feed_probe, "_post_readiness", side_effect=lambda value: observed.append(value)
                ),
            ):
                result = feed_probe.probe(manager_env)

            self.assertEqual(observed, [token])
            self.assertTrue(result["live_credential_matches"])
            self.assertNotIn(token, json.dumps(result))

    def test_probe_rejects_scheduler_that_still_has_previous_credential(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager_env = Path(directory) / ".crm-mcp.env"
            _manager_env(manager_env, "new-token-" + "n" * 48)
            with (
                mock.patch.object(
                    feed_probe, "_read_bounded_file", return_value=manager_env.read_bytes()
                ),
                mock.patch.object(feed_probe, "_service_main_pid", return_value=4312),
                mock.patch.object(
                    feed_probe,
                    "_live_manager_environment",
                    return_value={
                        feed_probe.MANAGER_URL_KEY: feed_probe.EXPECTED_MCP_URL,
                        feed_probe.MANAGER_TOKEN_KEY: "old-token-" + "o" * 48,
                    },
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "automation_release_scheduler_credential_stale"
                ):
                    feed_probe.probe(manager_env)

    def test_manager_environment_requires_root_private_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager_env = Path(directory) / ".crm-mcp.env"
            _manager_env(manager_env, "release-token-" + "a" * 48)
            real_info = manager_env.stat()
            unsafe_info = SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600,
                st_size=real_info.st_size,
                st_uid=1000,
            )
            with mock.patch.object(feed_probe.os, "fstat", return_value=unsafe_info):
                with self.assertRaisesRegex(RuntimeError, "automation_release_manager_env_invalid"):
                    feed_probe._read_bounded_file(
                        manager_env, limit=16 * 1024, require_root_private=True
                    )

    def test_readiness_http_probe_accepts_only_exact_non_mutating_contract(self) -> None:
        case = self

        class Response:
            status = 200

            @staticmethod
            def read(_limit: int) -> bytes:
                return json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "format": feed_probe.READINESS_FORMAT,
                            "generation": "technical-generation",
                            "high_water": 17,
                            "consumer_id": feed_probe.READINESS_CONSUMER,
                            "consumer_registered": False,
                            "pending_delivery": False,
                            "pending_publish": False,
                        },
                    }
                ).encode()

        class Connection:
            def __init__(self, host: str, port: int, *, timeout: int) -> None:
                case.assertEqual((host, port, timeout), ("127.0.0.1", 8000, 10))
                self.request_values: tuple[object, ...] | None = None

            def request(
                self, method: str, path: str, *, body: bytes, headers: dict[str, str]
            ) -> None:
                self.request_values = (method, path, body, headers)
                case.assertEqual(headers["X-Autostop-Automation-Protocol"], "crm_digest_v1")

            @staticmethod
            def getresponse() -> Response:
                return Response()

            @staticmethod
            def close() -> None:
                return None

        with mock.patch.object(feed_probe.http.client, "HTTPConnection", Connection):
            result = feed_probe._post_readiness("secret-token-" + "x" * 48)

        self.assertFalse(result["consumer_registered"])
        self.assertEqual(result["high_water"], 17)
