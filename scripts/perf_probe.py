from __future__ import annotations

import argparse
import gzip
import json
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProbeResult:
    label: str
    status: int
    duration_ms: float
    bytes_received: int
    content_encoding: str
    server_timing: str


def _url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    gzip_ok: bool = False,
    timeout: float = 15.0,
) -> tuple[dict[str, Any], ProbeResult]:
    headers = {"Content-Type": "application/json"}
    data = None
    if gzip_ok:
        headers["Accept-Encoding"] = "gzip"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        _url(base_url, path),
        data=data,
        headers=headers,
        method=method,
    )
    started_at = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body = response.read()
            duration_ms = (time.perf_counter() - started_at) * 1000
            encoding = str(response.headers.get("Content-Encoding", "") or "")
            body = gzip.decompress(raw_body) if encoding.lower() == "gzip" else raw_body
            payload_data = json.loads(body.decode("utf-8"))
            return payload_data, ProbeResult(
                label=path,
                status=response.status,
                duration_ms=duration_ms,
                bytes_received=len(raw_body),
                content_encoding=encoding,
                server_timing=str(response.headers.get("Server-Timing", "") or ""),
            )
    except urllib.error.HTTPError as exc:
        raw_body = exc.read()
        duration_ms = (time.perf_counter() - started_at) * 1000
        encoding = str(exc.headers.get("Content-Encoding", "") or "")
        body = gzip.decompress(raw_body) if encoding.lower() == "gzip" else raw_body
        payload_data = json.loads(body.decode("utf-8"))
        return payload_data, ProbeResult(
            label=path,
            status=exc.code,
            duration_ms=duration_ms,
            bytes_received=len(raw_body),
            content_encoding=encoding,
            server_timing=str(exc.headers.get("Server-Timing", "") or ""),
        )


def measure(
    base_url: str,
    label: str,
    path: str,
    *,
    iterations: int,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    gzip_ok: bool = False,
) -> tuple[dict[str, Any] | None, list[ProbeResult]]:
    results: list[ProbeResult] = []
    latest_payload: dict[str, Any] | None = None
    for _ in range(max(1, iterations)):
        latest_payload, result = request_json(
            base_url,
            path,
            method=method,
            payload=payload,
            gzip_ok=gzip_ok,
        )
        results.append(
            ProbeResult(
                label,
                result.status,
                result.duration_ms,
                result.bytes_received,
                result.content_encoding,
                result.server_timing,
            )
        )
    return latest_payload, results


def summarize(results: list[ProbeResult]) -> dict[str, Any]:
    durations = [item.duration_ms for item in results]
    sizes = [item.bytes_received for item in results]
    latest = results[-1]
    return {
        "label": latest.label,
        "status": latest.status,
        "avg_ms": round(statistics.mean(durations), 1),
        "min_ms": round(min(durations), 1),
        "max_ms": round(max(durations), 1),
        "bytes": int(statistics.mean(sizes)),
        "encoding": latest.content_encoding or "identity",
        "server_timing": latest.server_timing,
    }


def evaluate_thresholds(
    rows: list[dict[str, Any]], thresholds: dict[str, float]
) -> list[dict[str, object]]:
    rows_by_label = {str(row.get("label") or ""): row for row in rows}
    violations: list[dict[str, object]] = []
    for threshold_key, max_value in thresholds.items():
        if max_value <= 0:
            continue
        label, _, metric = threshold_key.rpartition(".")
        row = rows_by_label.get(label)
        if row is None:
            continue
        actual = row.get(metric)
        if not isinstance(actual, int | float):
            continue
        if float(actual) > max_value:
            violations.append(
                {
                    "label": label,
                    "metric": metric,
                    "actual": actual,
                    "max": float(max_value),
                }
            )
    return violations


def first_card_id(snapshot_payload: dict[str, Any], fallback: str = "") -> str:
    cards = snapshot_payload.get("data", {}).get("cards", [])
    if isinstance(cards, list) and cards:
        return str(cards[0].get("id") or "").strip()
    return fallback


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only AutoStop CRM latency and payload probe."
    )
    parser.add_argument("--base-url", default="https://crm.autostopcrm.ru")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--card-id", default="")
    parser.add_argument("--max-snapshot-identity-ms", type=float, default=0.0)
    parser.add_argument("--max-snapshot-identity-bytes", type=float, default=0.0)
    parser.add_argument("--max-snapshot-gzip-ms", type=float, default=0.0)
    parser.add_argument("--max-snapshot-gzip-bytes", type=float, default=0.0)
    parser.add_argument("--max-revision-ms", type=float, default=0.0)
    parser.add_argument("--max-get-card-ms", type=float, default=0.0)
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    snapshot_payload, results = measure(
        args.base_url,
        "snapshot.identity",
        "/api/get_board_snapshot?compact=1&include_archive=0",
        iterations=args.iterations,
    )
    rows.append(summarize(results))

    _, results = measure(
        args.base_url,
        "snapshot.gzip",
        "/api/get_board_snapshot?compact=1&include_archive=0",
        iterations=args.iterations,
        gzip_ok=True,
    )
    rows.append(summarize(results))

    _, results = measure(
        args.base_url,
        "revision",
        "/api/get_board_revision?compact=1&include_archive=0",
        iterations=args.iterations,
    )
    rows.append(summarize(results))

    card_id = args.card_id or first_card_id(snapshot_payload or {})
    if card_id:
        _, results = measure(
            args.base_url,
            "get_card",
            "/api/get_card",
            iterations=args.iterations,
            method="POST",
            payload={"card_id": card_id},
        )
        rows.append(summarize(results))

    thresholds = {
        "snapshot.identity.avg_ms": args.max_snapshot_identity_ms,
        "snapshot.identity.bytes": args.max_snapshot_identity_bytes,
        "snapshot.gzip.avg_ms": args.max_snapshot_gzip_ms,
        "snapshot.gzip.bytes": args.max_snapshot_gzip_bytes,
        "revision.avg_ms": args.max_revision_ms,
        "get_card.avg_ms": args.max_get_card_ms,
    }
    violations = evaluate_thresholds(rows, thresholds)
    output = {
        "base_url": args.base_url,
        "iterations": args.iterations,
        "rows": rows,
        "threshold_status": "failed" if violations else "passed",
        "violations": violations,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
