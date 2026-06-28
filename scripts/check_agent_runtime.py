from __future__ import annotations

import argparse
import json
import math
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


CHECK_AGENT_RUNTIME_RESPONSE_MAX_BYTES = 1 * 1024 * 1024
DEFAULT_MAX_HEARTBEAT_AGE_SECONDS = 30.0
MAX_HEARTBEAT_AGE_SECONDS = 86_400.0


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _urlopen_no_redirect(request: urllib.request.Request, *, timeout: float):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _read_response_body(response) -> bytes:
    raw = response.read(CHECK_AGENT_RUNTIME_RESPONSE_MAX_BYTES + 1)
    if len(raw) > CHECK_AGENT_RUNTIME_RESPONSE_MAX_BYTES:
        raise ValueError("API response is too large")
    return raw


def _load_json_response(raw: bytes) -> dict:
    try:
        decoded = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
    except RecursionError as exc:
        raise ValueError("API response JSON is too deeply nested") from exc
    if not isinstance(decoded, dict):
        raise ValueError("API response must be a JSON object")
    return decoded


def _request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    data = None
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    if payload is not None:
        data = json.dumps(payload, allow_nan=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with _urlopen_no_redirect(request, timeout=10.0) as response:
            return _load_json_response(_read_response_body(response))
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ValueError("API response redirected") from exc
        raise


def _login(base_url: str, username: str, password: str) -> str:
    response = _request_json(
        f"{base_url}/api/login_operator",
        method="POST",
        payload={"username": username, "password": password},
    )
    return response["data"]["session"]["token"]


def _heartbeat_age_seconds(value: str) -> float | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - timestamp).total_seconds())


def _bounded_heartbeat_age_seconds(value: object) -> float:
    if isinstance(value, bool):
        return DEFAULT_MAX_HEARTBEAT_AGE_SECONDS
    try:
        parsed = float(DEFAULT_MAX_HEARTBEAT_AGE_SECONDS if value is None or value == "" else value)
    except (OverflowError, TypeError, ValueError):
        return DEFAULT_MAX_HEARTBEAT_AGE_SECONDS
    if not math.isfinite(parsed):
        return DEFAULT_MAX_HEARTBEAT_AGE_SECONDS
    if parsed < 1.0:
        return 1.0
    if parsed > MAX_HEARTBEAT_AGE_SECONDS:
        return MAX_HEARTBEAT_AGE_SECONDS
    return parsed


def _default_api_url() -> str:
    candidate = (
        os.environ.get("MINIMAL_KANBAN_AGENT_BOARD_API_URL")
        or os.environ.get("AUTOSTOP_AGENT_BOARD_API_URL")
        or "http://127.0.0.1:41731"
    )
    return candidate.rstrip("/")


def _http_error_code(exc: Exception) -> int | None:
    return exc.code if isinstance(exc, urllib.error.HTTPError) else None


def _evaluate_agent_runtime_mode(
    *,
    base_url: str,
    token: str,
    max_heartbeat_age_seconds: float,
) -> tuple[str, dict[str, str]]:
    headers = {"X-Operator-Session": token} if token else {}
    try:
        payload = _request_json(
            f"{base_url}/api/agent_status",
            headers=headers,
        )
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        json.JSONDecodeError,
        KeyError,
        ValueError,
    ) as exc:
        if _http_error_code(exc) == 404:
            return "api_only", {"api_url": base_url, "reason": "agent_status_route_retired"}
        raise

    status = payload["data"]["status"]
    agent = payload["data"]["agent"]
    heartbeat_age = _heartbeat_age_seconds(str(status.get("last_heartbeat", "") or ""))
    if (
        agent.get("enabled")
        and heartbeat_age is not None
        and heartbeat_age <= max_heartbeat_age_seconds
    ):
        return "ok", {
            "api_url": base_url,
            "heartbeat_age_seconds": f"{heartbeat_age:.2f}",
            "model": str(agent.get("model", "") or ""),
        }
    if not agent.get("enabled"):
        return "api_only", {"api_url": base_url, "reason": "embedded_agent_disabled"}
    if heartbeat_age is None or heartbeat_age > max_heartbeat_age_seconds:
        return "api_only", {"api_url": base_url, "reason": "stale_or_missing_heartbeat"}
    return "api_only", {"api_url": base_url, "reason": "unknown_agent_state"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-api-url", default=_default_api_url())
    parser.add_argument(
        "--operator-username",
        default=os.environ.get("AUTOSTOP_SMOKE_OPERATOR_USERNAME", ""),
    )
    parser.add_argument(
        "--operator-password",
        default=os.environ.get("AUTOSTOP_SMOKE_OPERATOR_PASSWORD", ""),
    )
    parser.add_argument("--max-heartbeat-age-seconds", default=DEFAULT_MAX_HEARTBEAT_AGE_SECONDS)
    args = parser.parse_args(argv)
    max_heartbeat_age_seconds = _bounded_heartbeat_age_seconds(args.max_heartbeat_age_seconds)

    if not args.operator_username or not args.operator_password:
        print(
            "status: error",
            "type=missing_operator_credentials",
            "detail=set AUTOSTOP_SMOKE_OPERATOR_USERNAME/AUTOSTOP_SMOKE_OPERATOR_PASSWORD or pass both CLI options",
        )
        return 1

    try:
        token = _login(
            args.local_api_url.rstrip("/"), args.operator_username, args.operator_password
        )
        status, details = _evaluate_agent_runtime_mode(
            base_url=args.local_api_url.rstrip("/"),
            token=token,
            max_heartbeat_age_seconds=max_heartbeat_age_seconds,
        )
        if status == "ok":
            print(
                "status: ok",
                f"heartbeat_age_seconds={details['heartbeat_age_seconds']}",
                f"api_url={details['api_url']}",
                f"model={details['model']}",
            )
            return 0
        if status == "api_only":
            print(
                "status: api_only",
                f"api_url={details['api_url']}",
                f"reason={details['reason']}",
            )
            return 0
        print("status: error", f"api_url={args.local_api_url.rstrip('/')}", f"mode={status}")
        return 1
    except (
        KeyError,
        urllib.error.URLError,
        urllib.error.HTTPError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        print("status: error", f"type={type(exc).__name__}", f"detail={exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
