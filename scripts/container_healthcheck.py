from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request


API_HEALTH_URL = "http://127.0.0.1:41731/api/health"
MCP_URL = "http://127.0.0.1:41831/mcp"
READY_MCP_STATUSES = {200, 204, 307, 308, 400, 401, 403, 405, 406}


def _check_api() -> bool:
    with urllib.request.urlopen(API_HEALTH_URL, timeout=5.0) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return bool(response.status == 200 and payload.get("ok"))


def _check_mcp() -> bool:
    request = urllib.request.Request(MCP_URL, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
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
