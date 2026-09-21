#!/usr/bin/env python3
"""Prove the running scheduler can authenticate to the non-mutating CRM feed probe.

The credential is compared between the root-owned Manager environment file and
the live scheduler process without ever being written to stdout, argv or a
release artifact.  The CRM endpoint used here never creates a consumer,
delivery or ACK.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

MANAGER_URL_KEY = "AUTOSTOP_CRM_MCP_URL"
MANAGER_TOKEN_KEY = "AUTOSTOP_CRM_MCP_BEARER_TOKEN"
EXPECTED_MCP_URL = "http://127.0.0.1:8001/mcp"
SCHEDULER_UNIT = "autostop-manager-scheduler.service"
READINESS_PATH = "/api/change_feed/readiness"
READINESS_FORMAT = "crm_change_feed_readiness_v1"
READINESS_CONSUMER = "manager.crm_digest_v1"
MAX_ENV_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _read_bounded_file(path: Path, *, limit: int, require_root_private: bool) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise RuntimeError("automation_release_manager_env_invalid")
        if require_root_private and (info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600):
            raise RuntimeError("automation_release_manager_env_invalid")
        payload = os.read(descriptor, limit + 1)
        if len(payload) > limit:
            raise RuntimeError("automation_release_manager_env_invalid")
        return payload
    finally:
        os.close(descriptor)


def _parse_manager_environment(payload: bytes, *, nul_separated: bool) -> dict[str, str]:
    try:
        raw_items = payload.split(b"\0") if nul_separated else payload.splitlines()
        lines = [item.decode("utf-8") for item in raw_items if item]
    except UnicodeDecodeError as exc:
        raise RuntimeError("automation_release_manager_env_invalid") from exc
    values: dict[str, str] = {}
    for line in lines:
        match = _ENV_LINE.fullmatch(line)
        if match is None:
            if nul_separated:
                continue
            raise RuntimeError("automation_release_manager_env_invalid")
        key, value = match.groups()
        if key not in {MANAGER_URL_KEY, MANAGER_TOKEN_KEY}:
            if nul_separated:
                continue
            raise RuntimeError("automation_release_manager_env_invalid")
        if key in values:
            raise RuntimeError("automation_release_manager_env_invalid")
        values[key] = value
    token = values.get(MANAGER_TOKEN_KEY, "")
    if (
        values.get(MANAGER_URL_KEY) != EXPECTED_MCP_URL
        or not 32 <= len(token) <= 512
        or any(char.isspace() or ord(char) < 33 or ord(char) > 126 for char in token)
    ):
        raise RuntimeError("automation_release_manager_env_invalid")
    return values


def _service_main_pid(unit: str = SCHEDULER_UNIT) -> int:
    if unit != SCHEDULER_UNIT:
        raise RuntimeError("automation_release_scheduler_unit_invalid")
    result = subprocess.run(
        (
            "systemctl",
            "show",
            unit,
            "--property=LoadState",
            "--property=ActiveState",
            "--property=SubState",
            "--property=MainPID",
            "--no-pager",
        ),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    raw_pid = values.get("MainPID", "")
    if (
        result.returncode != 0
        or values.get("LoadState") != "loaded"
        or values.get("ActiveState") != "active"
        or values.get("SubState") != "running"
        or not raw_pid.isdecimal()
        or int(raw_pid) <= 1
    ):
        raise RuntimeError("automation_release_scheduler_not_running")
    return int(raw_pid)


def _live_manager_environment(pid: int) -> dict[str, str]:
    if pid <= 1:
        raise RuntimeError("automation_release_scheduler_not_running")
    payload = _read_bounded_file(
        Path("/proc") / str(pid) / "environ",
        limit=MAX_ENV_BYTES,
        require_root_private=False,
    )
    return _parse_manager_environment(payload, nul_separated=True)


def _post_readiness(token: str) -> dict[str, Any]:
    connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=10)
    try:
        connection.request(
            "POST",
            READINESS_PATH,
            body=b"{}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Autostop-Automation-Protocol": "crm_digest_v1",
            },
        )
        response = connection.getresponse()
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        status_code = response.status
    finally:
        connection.close()
    if status_code != 200 or len(payload) > MAX_RESPONSE_BYTES:
        raise RuntimeError("automation_release_crm_feed_auth_failed")
    try:
        envelope = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("automation_release_crm_feed_auth_failed") from exc
    data = envelope.get("data") if isinstance(envelope, dict) else None
    if (
        envelope.get("ok") is not True
        or not isinstance(data, dict)
        or data.get("format") != READINESS_FORMAT
        or data.get("consumer_id") != READINESS_CONSUMER
        or isinstance(data.get("high_water"), bool)
        or not isinstance(data.get("high_water"), int)
        or int(data["high_water"]) < 0
        or not isinstance(data.get("generation"), str)
        or not data["generation"]
        or type(data.get("consumer_registered")) is not bool
        or type(data.get("pending_delivery")) is not bool
        or type(data.get("pending_publish")) is not bool
    ):
        raise RuntimeError("automation_release_crm_feed_auth_failed")
    return data


def probe(manager_env: Path, *, unit: str = SCHEDULER_UNIT) -> dict[str, Any]:
    configured = _parse_manager_environment(
        _read_bounded_file(manager_env, limit=16 * 1024, require_root_private=True),
        nul_separated=False,
    )
    first_pid = _service_main_pid(unit)
    live = _live_manager_environment(first_pid)
    if live != configured:
        raise RuntimeError("automation_release_scheduler_credential_stale")
    _post_readiness(live[MANAGER_TOKEN_KEY])
    if _service_main_pid(unit) != first_pid:
        raise RuntimeError("automation_release_scheduler_restarted_during_probe")
    return {
        "ok": True,
        "scheduler_running": True,
        "live_credential_matches": True,
        "crm_change_feed_ready": True,
        "consumer_unchanged": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manager-env", type=Path, required=True)
    parser.add_argument("--unit", default=SCHEDULER_UNIT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = probe(args.manager_env, unit=args.unit)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
