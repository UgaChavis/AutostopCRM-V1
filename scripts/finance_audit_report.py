from __future__ import annotations

import argparse
import json
import math
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

UrlOpen = Callable[[urllib.request.Request, float], Any]
AUDIT_RESPONSE_MAX_BYTES = 4 * 1024 * 1024


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _urlopen_no_redirect(request: urllib.request.Request, timeout: float) -> Any:
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _json_safe_value(value: Any, *, depth: int = 8) -> Any:
    if depth <= 0:
        return str(value)
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item, depth=depth - 1) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    return str(value)


def _json_dumps(payload: Any) -> str:
    return json.dumps(
        _json_safe_value(payload),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )


def _url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _read_response_body(response) -> bytes:
    raw = response.read(AUDIT_RESPONSE_MAX_BYTES + 1)
    if len(raw) > AUDIT_RESPONSE_MAX_BYTES:
        raise ValueError("finance audit response is too large")
    return raw


def _load_audit_response(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8"), parse_constant=_reject_json_constant)
    except RecursionError as exc:
        raise ValueError("finance audit response JSON is too deeply nested") from exc
    if not isinstance(payload, dict):
        raise ValueError("finance audit response must be a JSON object")
    return payload


def fetch_audit(
    base_url: str,
    *,
    timeout: float = 15.0,
    urlopen: UrlOpen = _urlopen_no_redirect,
) -> dict[str, Any]:
    request = urllib.request.Request(
        _url(base_url, "/api/finance_audit"),
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw_body = _read_response_body(response)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ValueError("finance audit response redirected") from exc
        raise
    return _load_audit_response(raw_body)


def summarize_audit(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    issues = data.get("issues") if isinstance(data.get("issues"), list) else []
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    counts_by_code: dict[str, int] = {}
    severity_counts = {"error": 0, "warning": 0, "info": 0}
    safe_fix_count = 0
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code") or "unknown")
        severity = str(issue.get("severity") or "info")
        counts_by_code[code] = counts_by_code.get(code, 0) + 1
        if severity in severity_counts:
            severity_counts[severity] += 1
        if issue.get("safe_fix_available"):
            safe_fix_count += 1
    return {
        "schema_version": str(meta.get("schema_version") or ""),
        "read_only": bool(meta.get("read_only")),
        "issues_total": _safe_non_negative_int(summary.get("issues_total"), default=len(issues)),
        "errors": severity_counts["error"],
        "warnings": severity_counts["warning"],
        "info": severity_counts["info"],
        "safe_fix_count": _safe_non_negative_int(
            summary.get("safe_fix_count"), default=safe_fix_count
        ),
        "counts_by_code": dict(sorted(counts_by_code.items())),
    }


def _coerce_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or not numeric.is_integer() or numeric > 1_000_000_000:
        return None
    number = int(numeric)
    return max(0, number)


def _safe_non_negative_int(value: object, *, default: int) -> int:
    parsed = _coerce_non_negative_int(value)
    return max(0, default) if parsed is None else parsed


def _bounded_issue_limit(value: object) -> int:
    if isinstance(value, bool):
        return 30
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return 30
    if not math.isfinite(numeric) or not numeric.is_integer():
        return 30
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


def _text(value: object) -> str:
    return str(value or "").strip()


def _format_issue_context(issue: dict[str, Any]) -> str:
    parts: list[str] = []
    field_labels = (
        ("id", "id"),
        ("card_id", "card_id"),
        ("repair_order_number", "zn"),
        ("repair_order_vehicle", "vehicle"),
        ("repair_order_payment_id", "payment_id"),
        ("cash_transaction_id", "cash_transaction_id"),
        ("cashbox_id", "cashbox_id"),
    )
    for field_name, label in field_labels:
        value = _text(issue.get(field_name))
        if value:
            parts.append(f"{label}={value}")
    amount_minor = issue.get("amount_minor")
    amount_minor_value = _coerce_non_negative_int(amount_minor)
    if amount_minor_value:
        parts.append(f"amount_minor={amount_minor_value}")
    data = issue.get("data")
    if isinstance(data, dict):
        for field_name in (
            "due_total",
            "paid_total",
            "grand_total",
            "cashbox_id",
            "payment_id",
            "cash_transaction_id",
            "stored_note",
            "expected_note",
        ):
            value = _text(data.get(field_name))
            if value:
                parts.append(f"{field_name}={value}")
    parts.append(f"safe_fix={'yes' if issue.get('safe_fix_available') else 'no'}")
    return " ".join(parts)


def _format_text(summary: dict[str, Any], payload: dict[str, Any], *, issue_limit: int) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    issues = data.get("issues") if isinstance(data.get("issues"), list) else []
    lines = [
        "AutoStop CRM finance audit",
        f"schema: {summary['schema_version']}",
        f"read_only: {summary['read_only']}",
        (
            "issues: "
            f"{summary['issues_total']} "
            f"(errors={summary['errors']}, warnings={summary['warnings']}, info={summary['info']})"
        ),
        f"safe_fixes_available: {summary['safe_fix_count']}",
    ]
    for issue in issues[: max(0, issue_limit)]:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code") or "unknown")
        severity = str(issue.get("severity") or "info")
        message = str(issue.get("message") or "")
        context = _format_issue_context(issue)
        lines.append(f"- [{severity}] {code}: {message} {context}".rstrip())
    return "\n".join(lines)


def _limited_data(payload: dict[str, Any], *, issue_limit: int) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    limited = dict(data)
    issues = data.get("issues")
    if isinstance(issues, list):
        limited["issues"] = issues[: max(0, issue_limit)]
    return limited


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only AutoStop CRM finance audit report.")
    parser.add_argument("--base-url", default="https://crm.autostopcrm.ru")
    parser.add_argument("--timeout", default=15.0)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--issue-limit", default=30)
    args = parser.parse_args()
    args.issue_limit = _bounded_issue_limit(args.issue_limit)
    args.timeout = _bounded_timeout_seconds(args.timeout)

    try:
        payload = fetch_audit(args.base_url, timeout=args.timeout)
    except (urllib.error.URLError, ValueError) as exc:
        print(_json_dumps({"ok": False, "error": str(exc)}))
        return 2
    summary = summarize_audit(payload)
    if args.format == "text":
        print(_format_text(summary, payload, issue_limit=args.issue_limit))
    else:
        print(
            _json_dumps(
                {
                    "ok": bool(payload.get("ok")),
                    "summary": summary,
                    "data": _limited_data(payload, issue_limit=args.issue_limit),
                }
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
