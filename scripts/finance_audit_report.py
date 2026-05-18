from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

UrlOpen = Callable[[urllib.request.Request, float], Any]


def _url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def fetch_audit(
    base_url: str,
    *,
    timeout: float = 15.0,
    urlopen: UrlOpen = urllib.request.urlopen,
) -> dict[str, Any]:
    request = urllib.request.Request(
        _url(base_url, "/api/finance_audit"),
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:
        raw_body = response.read()
    return json.loads(raw_body.decode("utf-8"))


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
        "issues_total": int(summary.get("issues_total") or len(issues)),
        "errors": severity_counts["error"],
        "warnings": severity_counts["warning"],
        "info": severity_counts["info"],
        "safe_fix_count": int(summary.get("safe_fix_count") or safe_fix_count),
        "counts_by_code": dict(sorted(counts_by_code.items())),
    }


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
        issue_id = str(issue.get("id") or "")
        lines.append(f"- [{severity}] {code}: {message} {issue_id}".rstrip())
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
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--issue-limit", type=int, default=30)
    args = parser.parse_args()

    try:
        payload = fetch_audit(args.base_url, timeout=args.timeout)
    except urllib.error.URLError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    summary = summarize_audit(payload)
    if args.format == "text":
        print(_format_text(summary, payload, issue_limit=args.issue_limit))
    else:
        print(
            json.dumps(
                {
                    "ok": bool(payload.get("ok")),
                    "summary": summary,
                    "data": _limited_data(payload, issue_limit=args.issue_limit),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
