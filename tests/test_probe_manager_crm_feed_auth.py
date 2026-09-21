from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import probe_manager_crm_feed_auth as feed_probe


def _manager_env(path: Path, token: str) -> None:
    path.write_text(
        f"{feed_probe.MANAGER_URL_KEY}={feed_probe.EXPECTED_MCP_URL}\n"
        f"{feed_probe.MANAGER_TOKEN_KEY}={token}\n",
        encoding="utf-8",
    )
    os.chown(path, 0, 0)
    os.chmod(path, 0o600)


def test_probe_requires_live_scheduler_credential_and_returns_no_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = "release-token-" + "a" * 48
    manager_env = tmp_path / ".crm-mcp.env"
    _manager_env(manager_env, token)
    configured = {
        feed_probe.MANAGER_URL_KEY: feed_probe.EXPECTED_MCP_URL,
        feed_probe.MANAGER_TOKEN_KEY: token,
    }
    pids = iter((4312, 4312))
    monkeypatch.setattr(feed_probe, "_service_main_pid", lambda _unit: next(pids))
    monkeypatch.setattr(feed_probe, "_live_manager_environment", lambda _pid: configured.copy())
    observed: list[str] = []
    monkeypatch.setattr(feed_probe, "_post_readiness", lambda value: observed.append(value))

    result = feed_probe.probe(manager_env)

    assert observed == [token]
    assert result["live_credential_matches"] is True
    assert token not in json.dumps(result)


def test_probe_rejects_scheduler_that_still_has_previous_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manager_env = tmp_path / ".crm-mcp.env"
    _manager_env(manager_env, "new-token-" + "n" * 48)
    monkeypatch.setattr(feed_probe, "_service_main_pid", lambda _unit: 4312)
    monkeypatch.setattr(
        feed_probe,
        "_live_manager_environment",
        lambda _pid: {
            feed_probe.MANAGER_URL_KEY: feed_probe.EXPECTED_MCP_URL,
            feed_probe.MANAGER_TOKEN_KEY: "old-token-" + "o" * 48,
        },
    )

    with pytest.raises(RuntimeError, match="automation_release_scheduler_credential_stale"):
        feed_probe.probe(manager_env)


def test_readiness_http_probe_accepts_only_exact_non_mutating_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            assert (host, port, timeout) == ("127.0.0.1", 8000, 10)
            self.request_values: tuple[object, ...] | None = None

        def request(self, method: str, path: str, *, body: bytes, headers: dict[str, str]) -> None:
            self.request_values = (method, path, body, headers)
            assert headers["X-Autostop-Automation-Protocol"] == "crm_digest_v1"

        @staticmethod
        def getresponse() -> Response:
            return Response()

        @staticmethod
        def close() -> None:
            return None

    monkeypatch.setattr(feed_probe.http.client, "HTTPConnection", Connection)

    result = feed_probe._post_readiness("secret-token-" + "x" * 48)

    assert result["consumer_registered"] is False
    assert result["high_water"] == 17
