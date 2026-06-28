from __future__ import annotations

# ruff: noqa: E402
import argparse
import json
import math
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.config import get_state_file
from minimal_kanban.services.repair_order_number_audit import (
    format_repair_order_number_audit_text,
    limited_repair_order_number_audit_data,
    repair_order_number_audit_payload,
)

UrlOpen = Callable[[urllib.request.Request, float], Any]
AUDIT_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
AUDIT_STATE_MAX_BYTES = 100 * 1024 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _urlopen_no_redirect(request: urllib.request.Request, timeout: float) -> Any:
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def build_audit(state: dict[str, Any]) -> dict[str, Any]:
    return repair_order_number_audit_payload(state)


def _limited_data(payload: dict[str, Any], *, issue_limit: int) -> dict[str, Any]:
    return limited_repair_order_number_audit_data(payload, issue_limit=issue_limit)


def _format_text(payload: dict[str, Any], *, issue_limit: int) -> str:
    return format_repair_order_number_audit_text(payload, issue_limit=issue_limit)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _json_safe_value(value: Any, *, depth: int = 8) -> Any:
    if depth <= 0:
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item, depth=depth - 1)
            for key, item in value.items()
            if key is not None
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    return str(value)


def _json_dumps(payload: Any) -> str:
    return json.dumps(_json_safe_value(payload), ensure_ascii=False, indent=2, allow_nan=False)


def _bounded_issue_limit(value: object) -> int:
    if isinstance(value, bool):
        return 50
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return 50
    if not math.isfinite(numeric) or not numeric.is_integer():
        return 50
    if numeric < 0:
        return 0
    if numeric > 500:
        return 500
    return int(numeric)


def _bounded_timeout_seconds(value: object) -> float:
    if isinstance(value, bool):
        return 15.0
    try:
        numeric = float(15.0 if value is None or value == "" else value)
    except (OverflowError, TypeError, ValueError):
        return 15.0
    if not math.isfinite(numeric):
        return 15.0
    if numeric < 1.0:
        return 1.0
    if numeric > 300.0:
        return 300.0
    return numeric


def _read_response_body(response) -> bytes:
    raw = response.read(AUDIT_RESPONSE_MAX_BYTES + 1)
    if len(raw) > AUDIT_RESPONSE_MAX_BYTES:
        raise ValueError("repair order number audit response is too large")
    return raw


def _read_state_text(state_path: Path) -> str:
    with state_path.open("rb") as handle:
        raw = handle.read(AUDIT_STATE_MAX_BYTES + 1)
    if len(raw) > AUDIT_STATE_MAX_BYTES:
        raise ValueError("repair order number audit state file is too large")
    return raw.decode("utf-8")


def fetch_audit(
    base_url: str,
    *,
    timeout: float = 15.0,
    urlopen: UrlOpen = _urlopen_no_redirect,
) -> dict[str, Any]:
    request = urllib.request.Request(
        _url(base_url, "/api/repair_order_number_audit"),
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw_body = _read_response_body(response)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ValueError("repair order number audit response redirected") from exc
        raise
    try:
        payload = json.loads(raw_body.decode("utf-8"), parse_constant=_reject_json_constant)
    except RecursionError as exc:
        raise ValueError("API response JSON is too deeply nested") from exc
    if not isinstance(payload, dict):
        raise ValueError("API response must be a JSON object")
    return payload


def _read_state_audit(state_file: str) -> dict[str, Any]:
    state_path = Path(state_file)
    try:
        state = json.loads(
            _read_state_text(state_path),
            parse_constant=_reject_json_constant,
        )
    except RecursionError as exc:
        raise ValueError("state file JSON is too deeply nested") from exc
    if not isinstance(state, dict):
        raise ValueError("state file must contain a JSON object")
    return repair_order_number_audit_payload(state)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only dry-run audit of AutoStop CRM repair order numbers."
    )
    parser.add_argument("--state-file", default=str(get_state_file()))
    parser.add_argument(
        "--base-url",
        default="",
        help="Optional CRM base URL; when set, reads /api/repair_order_number_audit instead of local state.",
    )
    parser.add_argument("--timeout", default=15.0)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--issue-limit", default=50)
    args = parser.parse_args()
    args.issue_limit = _bounded_issue_limit(args.issue_limit)
    args.timeout = _bounded_timeout_seconds(args.timeout)

    try:
        payload = (
            fetch_audit(args.base_url, timeout=args.timeout)
            if args.base_url
            else _read_state_audit(args.state_file)
        )
    except OSError as exc:
        print(_json_dumps({"ok": False, "error": str(exc)}))
        return 2
    except json.JSONDecodeError as exc:
        print(_json_dumps({"ok": False, "error": f"Invalid JSON: {exc}"}))
        return 2
    except ValueError as exc:
        print(_json_dumps({"ok": False, "error": str(exc)}))
        return 2
    except urllib.error.URLError as exc:
        print(_json_dumps({"ok": False, "error": str(exc)}))
        return 2

    if args.format == "text":
        print(format_repair_order_number_audit_text(payload, issue_limit=args.issue_limit))
    else:
        print(
            _json_dumps(
                {
                    "ok": bool(payload.get("ok")),
                    "data": limited_repair_order_number_audit_data(
                        payload,
                        issue_limit=args.issue_limit,
                    ),
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
