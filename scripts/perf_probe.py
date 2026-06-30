from __future__ import annotations

import argparse
import gzip
import json
import logging
import math
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.json_safety import reject_deeply_nested_json  # noqa: E402

PERF_PROBE_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
PERF_PROBE_MAX_ITERATIONS = 100
PERF_PROBE_MAX_THRESHOLD = 3_600_000.0


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _urlopen_no_redirect(request: urllib.request.Request, *, timeout: float):
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
        return {
            str(key): _json_safe_value(item, depth=depth - 1)
            for key, item in value.items()
            if key is not None
        }
    if isinstance(value, list | tuple | set):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    return str(value)


def _json_dumps(payload: Any) -> str:
    return json.dumps(_json_safe_value(payload), ensure_ascii=False, indent=2, allow_nan=False)


def _read_response_body(response) -> bytes:
    raw = response.read(PERF_PROBE_RESPONSE_MAX_BYTES + 1)
    if len(raw) > PERF_PROBE_RESPONSE_MAX_BYTES:
        raise ValueError("perf probe response is too large")
    return raw


def _decompress_gzip_body(raw_body: bytes) -> bytes:
    with gzip.GzipFile(fileobj=BytesIO(raw_body)) as stream:
        body = stream.read(PERF_PROBE_RESPONSE_MAX_BYTES + 1)
    if len(body) > PERF_PROBE_RESPONSE_MAX_BYTES:
        raise ValueError("perf probe gzip response is too large after decompression")
    return body


def _load_response_json(raw_body: bytes, *, context: str) -> dict[str, Any]:
    try:
        payload_data = json.loads(raw_body.decode("utf-8"), parse_constant=_reject_json_constant)
    except RecursionError as exc:
        raise ValueError(f"{context} JSON is too deeply nested") from exc
    reject_deeply_nested_json(payload_data, message=f"{context} JSON is too deeply nested")
    if not isinstance(payload_data, dict):
        raise ValueError(f"{context} must be a JSON object")
    return payload_data


@dataclass(frozen=True)
class ProbeResult:
    label: str
    status: int
    duration_ms: float
    bytes_received: int
    content_encoding: str
    server_timing: str


@dataclass
class LocalTempServer:
    base_url: str
    server: Any
    temp_dir: tempfile.TemporaryDirectory[str]

    def stop(self) -> None:
        self.server.stop()
        self.temp_dir.cleanup()


def start_local_temp_server() -> LocalTempServer:
    import sys

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    from minimal_kanban.api.server import ApiServer
    from minimal_kanban.services.card_service import CardService
    from minimal_kanban.storage.json_store import JsonStore

    temp_dir = tempfile.TemporaryDirectory()
    logger = logging.getLogger("perf_probe.local_temp_server")
    logger.addHandler(logging.NullHandler())
    store = JsonStore(state_file=Path(temp_dir.name) / "state.json", logger=logger)
    service = CardService(
        store,
        logger,
        attachments_dir=Path(temp_dir.name) / "attachments",
        repair_orders_dir=Path(temp_dir.name) / "repair-orders",
    )
    service.create_card(
        {
            "vehicle": "Smoke Vehicle",
            "title": "Performance probe card",
            "description": "Temporary local card for perf_probe.",
            "deadline": {"hours": 2},
            "actor_name": "PERF",
        }
    )
    server = ApiServer(
        service,
        logger,
        host="127.0.0.1",
        start_port=42751,
        fallback_limit=20,
        bearer_token="",
    )
    server.start()
    return LocalTempServer(base_url=server.base_url, server=server, temp_dir=temp_dir)


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
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    request = urllib.request.Request(
        _url(base_url, path),
        data=data,
        headers=headers,
        method=method,
    )
    started_at = time.perf_counter()
    try:
        with _urlopen_no_redirect(request, timeout=timeout) as response:
            raw_body = _read_response_body(response)
            duration_ms = (time.perf_counter() - started_at) * 1000
            encoding = str(response.headers.get("Content-Encoding", "") or "")
            body = _decompress_gzip_body(raw_body) if encoding.lower() == "gzip" else raw_body
            payload_data = _load_response_json(body, context="API response")
            return payload_data, ProbeResult(
                label=path,
                status=response.status,
                duration_ms=duration_ms,
                bytes_received=len(raw_body),
                content_encoding=encoding,
                server_timing=str(response.headers.get("Server-Timing", "") or ""),
            )
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ValueError(f"API request redirected: {path}") from exc
        raw_body = _read_response_body(exc)
        duration_ms = (time.perf_counter() - started_at) * 1000
        encoding = str(exc.headers.get("Content-Encoding", "") or "")
        body = _decompress_gzip_body(raw_body) if encoding.lower() == "gzip" else raw_body
        payload_data = _load_response_json(body, context="API error response")
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
        if not math.isfinite(max_value) or max_value <= 0:
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


def _bounded_threshold(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        numeric = float(default if value is None or value == "" else value)
    except (OverflowError, TypeError, ValueError):
        return default
    if not math.isfinite(numeric):
        return default
    if numeric < 0:
        return 0.0
    if numeric > PERF_PROBE_MAX_THRESHOLD:
        return PERF_PROBE_MAX_THRESHOLD
    return numeric


def _bounded_iterations(value: object, *, default: int = 3) -> int:
    if isinstance(value, bool):
        return default
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return default
    if not math.isfinite(numeric) or not numeric.is_integer():
        return default
    if numeric < 1:
        return 1
    if numeric > PERF_PROBE_MAX_ITERATIONS:
        return PERF_PROBE_MAX_ITERATIONS
    return int(numeric)


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
    parser.add_argument(
        "--local-temp-server",
        action="store_true",
        help="Start a temporary local API server with synthetic data and probe it.",
    )
    parser.add_argument("--iterations", default=3)
    parser.add_argument("--card-id", default="")
    parser.add_argument("--max-snapshot-identity-ms", default=0.0)
    parser.add_argument("--max-snapshot-identity-bytes", default=0.0)
    parser.add_argument("--max-snapshot-gzip-ms", default=0.0)
    parser.add_argument("--max-snapshot-gzip-bytes", default=0.0)
    parser.add_argument("--max-revision-ms", default=0.0)
    parser.add_argument("--max-get-card-ms", default=0.0)
    args = parser.parse_args()

    local_server: LocalTempServer | None = None
    base_url = args.base_url
    iterations = _bounded_iterations(args.iterations)
    args.max_snapshot_identity_ms = _bounded_threshold(args.max_snapshot_identity_ms)
    args.max_snapshot_identity_bytes = _bounded_threshold(args.max_snapshot_identity_bytes)
    args.max_snapshot_gzip_ms = _bounded_threshold(args.max_snapshot_gzip_ms)
    args.max_snapshot_gzip_bytes = _bounded_threshold(args.max_snapshot_gzip_bytes)
    args.max_revision_ms = _bounded_threshold(args.max_revision_ms)
    args.max_get_card_ms = _bounded_threshold(args.max_get_card_ms)
    try:
        local_server = start_local_temp_server() if args.local_temp_server else None
        base_url = local_server.base_url if local_server is not None else args.base_url
        rows: list[dict[str, Any]] = []
        snapshot_payload, results = measure(
            base_url,
            "snapshot.identity",
            "/api/get_board_snapshot?compact=1&include_archive=0",
            iterations=iterations,
        )
        rows.append(summarize(results))

        _, results = measure(
            base_url,
            "snapshot.gzip",
            "/api/get_board_snapshot?compact=1&include_archive=0",
            iterations=iterations,
            gzip_ok=True,
        )
        rows.append(summarize(results))

        _, results = measure(
            base_url,
            "revision",
            "/api/get_board_revision?compact=1&include_archive=0",
            iterations=iterations,
        )
        rows.append(summarize(results))

        card_id = args.card_id or first_card_id(snapshot_payload or {})
        if card_id:
            _, results = measure(
                base_url,
                "get_card",
                "/api/get_card",
                iterations=iterations,
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
            "base_url": base_url,
            "local_temp_server": bool(args.local_temp_server),
            "iterations": iterations,
            "rows": rows,
            "threshold_status": "failed" if violations else "passed",
            "violations": violations,
        }
        print(_json_dumps(output))
        return 1 if violations else 0
    except (
        OSError,
        TimeoutError,
        urllib.error.URLError,
        json.JSONDecodeError,
        gzip.BadGzipFile,
        ValueError,
    ) as exc:
        print(
            _json_dumps(
                {"ok": False, "base_url": base_url, "error": str(exc)},
            )
        )
        return 2
    finally:
        if local_server is not None:
            local_server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
