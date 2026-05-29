from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
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
    build_repair_order_number_audit,
    format_repair_order_number_audit_text,
    limited_repair_order_number_audit_data,
    repair_order_number_audit_payload,
)


UrlOpen = Callable[[urllib.request.Request, float], Any]


def _url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def build_audit(state: dict[str, Any]) -> dict[str, Any]:
    return repair_order_number_audit_payload(state)


def _limited_data(payload: dict[str, Any], *, issue_limit: int) -> dict[str, Any]:
    return limited_repair_order_number_audit_data(payload, issue_limit=issue_limit)


def _format_text(payload: dict[str, Any], *, issue_limit: int) -> str:
    return format_repair_order_number_audit_text(payload, issue_limit=issue_limit)


def fetch_audit(
    base_url: str,
    *,
    timeout: float = 15.0,
    urlopen: UrlOpen = urllib.request.urlopen,
) -> dict[str, Any]:
    request = urllib.request.Request(
        _url(base_url, "/api/repair_order_number_audit"),
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:
        raw_body = response.read()
    return json.loads(raw_body.decode("utf-8"))


def _read_state_audit(state_file: str) -> dict[str, Any]:
    state_path = Path(state_file)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    return repair_order_number_audit_payload(state if isinstance(state, dict) else {})


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
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--issue-limit", type=int, default=50)
    args = parser.parse_args()

    try:
        payload = (
            fetch_audit(args.base_url, timeout=args.timeout)
            if args.base_url
            else _read_state_audit(args.state_file)
        )
    except OSError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    except json.JSONDecodeError as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"Invalid JSON: {exc}"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    except urllib.error.URLError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    if args.format == "text":
        print(format_repair_order_number_audit_text(payload, issue_limit=args.issue_limit))
    else:
        print(
            json.dumps(
                {
                    "ok": bool(payload.get("ok")),
                    "data": limited_repair_order_number_audit_data(
                        payload,
                        issue_limit=args.issue_limit,
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
