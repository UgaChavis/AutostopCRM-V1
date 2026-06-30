from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.json_safety import reject_deeply_nested_json  # noqa: E402

API_HEALTH_URL = "http://127.0.0.1:41731/api/health"
MCP_URL = "http://127.0.0.1:41831/mcp"
READY_MCP_STATUSES = {200, 204, 307, 308, 400, 401, 403, 405, 406}
API_HEALTH_RESPONSE_MAX_BYTES = 1 * 1024 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _urlopen_no_redirect(request: str | urllib.request.Request, *, timeout: float):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _read_api_health_body(response) -> bytes:
    raw = response.read(API_HEALTH_RESPONSE_MAX_BYTES + 1)
    if len(raw) > API_HEALTH_RESPONSE_MAX_BYTES:
        raise ValueError("API health response is too large")
    return raw


def _load_api_health_json(raw: bytes):
    try:
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
    except RecursionError as exc:
        raise ValueError("API health response JSON is too deeply nested") from exc
    reject_deeply_nested_json(
        payload,
        message="API health response JSON is too deeply nested",
    )
    return payload


def _check_api() -> bool:
    with _urlopen_no_redirect(API_HEALTH_URL, timeout=5.0) as response:
        payload = _load_api_health_json(_read_api_health_body(response))
        if not isinstance(payload, dict):
            return False
        return bool(response.status == 200 and payload.get("ok"))


def _check_mcp() -> bool:
    request = urllib.request.Request(MCP_URL, method="GET", headers={"Accept": "application/json"})
    try:
        with _urlopen_no_redirect(request, timeout=5.0) as response:
            return response.status in READY_MCP_STATUSES
    except urllib.error.HTTPError as exc:
        return exc.code in READY_MCP_STATUSES


def main() -> int:
    try:
        api_ok = _check_api()
        mcp_ok = _check_mcp()
    except Exception:
        return 1
    return 0 if api_ok and mcp_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
