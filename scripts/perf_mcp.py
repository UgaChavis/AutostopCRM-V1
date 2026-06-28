from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import statistics
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


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


@dataclass
class LocalMcpRuntime:
    mcp_url: str
    card_id: str
    api_runtime: Any
    mcp_runtime: Any

    def close(self) -> None:
        self.mcp_runtime.stop()
        self.api_runtime.close()


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return ordered[index]


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


def _safe_int(value: Any, *, default: int = 0, maximum: int = 1_000_000_000) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(parsed) or not parsed.is_integer():
        return default
    if parsed < 0:
        return default
    if parsed > maximum:
        return maximum
    return int(parsed)


def _bounded_iterations(value: Any) -> int:
    return max(1, _safe_int(value, default=3, maximum=100))


def _bounded_port(value: Any, *, default: int) -> int:
    port = _safe_int(value, default=default, maximum=65535)
    return port if port >= 1 else default


def payload_size(payload: Any) -> int:
    try:
        return len(
            json.dumps(
                _json_safe_value(payload),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
    except (OverflowError, TypeError, ValueError):
        return 0


def summarize(samples: list[dict[str, Any]], scenario: str) -> dict[str, Any]:
    durations = [_safe_float(item.get("duration_ms")) for item in samples]
    payload_sizes = [_safe_int(item.get("payload_bytes")) for item in samples]
    meta_entries = [item.get("meta") for item in samples if isinstance(item.get("meta"), dict)]
    errors = [str(item.get("error")) for item in samples if item.get("error")]
    return {
        "scenario": scenario,
        "iterations": len(samples),
        "avg_ms": round(statistics.mean(durations), 1) if durations else 0.0,
        "min_ms": round(min(durations), 1) if durations else 0.0,
        "max_ms": round(max(durations), 1) if durations else 0.0,
        "p95_ms": round(percentile(durations, 0.95), 1),
        "request_count": 1,
        "payload_bytes": round(statistics.mean(payload_sizes)) if payload_sizes else 0,
        "server_timing": [],
        "ui_perf_entries": [],
        "meta": meta_entries[-3:],
        "console_errors": [],
        "page_errors": [],
        "failed_requests": errors,
    }


def skipped_row(scenario: str, reason: str) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "skipped": True,
        "reason": reason,
        "iterations": 0,
        "avg_ms": 0.0,
        "min_ms": 0.0,
        "max_ms": 0.0,
        "p95_ms": 0.0,
        "request_count": 0,
        "payload_bytes": 0,
        "server_timing": [],
        "ui_perf_entries": [],
        "console_errors": [],
        "page_errors": [],
        "failed_requests": [],
    }


def _logger() -> logging.Logger:
    logger = logging.getLogger("autostop.perf_mcp")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


def start_local_mcp_runtime(args: argparse.Namespace) -> LocalMcpRuntime:
    from browser_smoke import start_temp_runtime

    from minimal_kanban.mcp.client import BoardApiClient
    from minimal_kanban.mcp.runtime import McpServerRuntime
    from minimal_kanban.mcp.server import create_mcp_server

    logger = _logger()
    api_runtime = start_temp_runtime(start_port=args.start_port)
    board_api = BoardApiClient(api_runtime.base_url)
    mcp_server = create_mcp_server(
        board_api,
        logger,
        host="127.0.0.1",
        port=args.mcp_start_port,
        path="/mcp",
        bearer_token=None,
        public_endpoint_url=f"http://127.0.0.1:{args.mcp_start_port}/mcp",
    )
    mcp_runtime = McpServerRuntime(mcp_server, logger, auth_mode="none")
    mcp_runtime.start()
    return LocalMcpRuntime(
        mcp_url=mcp_runtime.base_url,
        card_id=api_runtime.card_id,
        api_runtime=api_runtime,
        mcp_runtime=mcp_runtime,
    )


async def _measure(awaitable_factory: Callable[[], Awaitable[Any]]) -> tuple[float, Any]:
    started_at = time.perf_counter()
    result = await awaitable_factory()
    return (time.perf_counter() - started_at) * 1000, result


def _structured_payload(result: Any) -> Any:
    payload = getattr(result, "structuredContent", None)
    if payload is not None:
        return payload
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return result


async def _call_tool_sample(
    session: ClientSession,
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    try:
        duration_ms, result = await _measure(lambda: session.call_tool(tool_name, args))
        payload = _structured_payload(result)
        return {
            "duration_ms": duration_ms,
            "payload_bytes": payload_size(payload),
            "meta": payload.get("meta") if isinstance(payload, dict) else None,
            "error": None
            if not getattr(result, "isError", False)
            else payload.get("error")
            if isinstance(payload, dict)
            else "tool_error",
        }
    except Exception as exc:  # noqa: BLE001 - perf report should capture all failures.
        return {"duration_ms": 0.0, "payload_bytes": 0, "meta": None, "error": str(exc)}


async def _list_tools_sample(session: ClientSession) -> tuple[dict[str, Any], list[str]]:
    try:
        duration_ms, result = await _measure(session.list_tools)
        tools_payload = [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
            }
            for tool in result.tools
        ]
        return (
            {
                "duration_ms": duration_ms,
                "payload_bytes": payload_size(tools_payload),
                "meta": {"tool_count": len(result.tools)},
                "error": None,
            },
            [tool.name for tool in result.tools],
        )
    except Exception as exc:  # noqa: BLE001
        return (
            {"duration_ms": 0.0, "payload_bytes": 0, "meta": None, "error": str(exc)},
            [],
        )


async def _discover_card_id(session: ClientSession, requested: str) -> str:
    if requested.strip():
        return requested.strip()
    result = await session.call_tool("get_cards", {"compact": True, "include_archived": False})
    payload = result.structuredContent if hasattr(result, "structuredContent") else {}
    cards = payload.get("data", {}).get("cards", []) if isinstance(payload, dict) else []
    if isinstance(cards, list) and cards:
        return str(cards[0].get("id") or "").strip()
    return ""


async def _first_move_target(session: ClientSession, card_id: str) -> str:
    card_result = await session.call_tool("get_card", {"card_id": card_id})
    card_payload = card_result.structuredContent
    current_column = str(card_payload.get("data", {}).get("card", {}).get("column") or "")
    columns_result = await session.call_tool("list_columns", {})
    columns_payload = columns_result.structuredContent
    columns = columns_payload.get("data", {}).get("columns", [])
    if isinstance(columns, list):
        for column in columns:
            column_id = str(column.get("id") or "")
            if column_id and column_id != current_column:
                return column_id
    return current_column


async def _run_mcp_perf_payload(
    mcp_url: str,
    headers: dict[str, str],
    args: argparse.Namespace,
    local_runtime: LocalMcpRuntime | None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    scenario_samples: dict[str, list[dict[str, Any]]] = {
        "mcp.tools_list": [],
        "mcp.ping_connector": [],
        "mcp.runtime_status": [],
        "mcp.bootstrap_context_compact": [],
        "mcp.get_card": [],
        "mcp.get_card_log_compact": [],
        "mcp.update_card": [],
        "mcp.move_card": [],
    }
    timeout = httpx.Timeout(45.0, connect=10.0, read=45.0, write=45.0, pool=45.0)
    async with httpx.AsyncClient(
        headers=headers, timeout=timeout, follow_redirects=False
    ) as http_client:
        async with streamable_http_client(mcp_url, http_client=http_client) as (
            read,
            write,
            _,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                first_tools_sample, tool_names = await _list_tools_sample(session)
                scenario_samples["mcp.tools_list"].append(first_tools_sample)
                card_id = await _discover_card_id(
                    session,
                    args.card_id or (local_runtime.card_id if local_runtime else ""),
                )
                move_target = ""
                writes_enabled = bool(args.local_temp_server or args.allow_live_writes)
                if writes_enabled and card_id:
                    move_target = await _first_move_target(session, card_id)

                for index in range(max(1, args.iterations)):
                    if index > 0:
                        sample, _ = await _list_tools_sample(session)
                        scenario_samples["mcp.tools_list"].append(sample)
                    scenario_samples["mcp.ping_connector"].append(
                        await _call_tool_sample(session, "ping_connector", {})
                    )
                    scenario_samples["mcp.runtime_status"].append(
                        await _call_tool_sample(session, "get_runtime_status", {})
                    )
                    scenario_samples["mcp.bootstrap_context_compact"].append(
                        await _call_tool_sample(
                            session,
                            "bootstrap_context",
                            {"compact": True, "include_archived": False, "event_limit": 50},
                        )
                    )
                    if card_id:
                        scenario_samples["mcp.get_card"].append(
                            await _call_tool_sample(session, "get_card", {"card_id": card_id})
                        )
                        scenario_samples["mcp.get_card_log_compact"].append(
                            await _call_tool_sample(
                                session,
                                "get_card_log",
                                {"card_id": card_id, "compact": True, "limit": 50},
                            )
                        )
                        if writes_enabled:
                            scenario_samples["mcp.update_card"].append(
                                await _call_tool_sample(
                                    session,
                                    "update_card",
                                    {
                                        "card_id": card_id,
                                        "description": f"MCP perf update {index}",
                                        "actor_name": "PERF",
                                    },
                                )
                            )
                            scenario_samples["mcp.move_card"].append(
                                await _call_tool_sample(
                                    session,
                                    "move_card",
                                    {
                                        "card_id": card_id,
                                        "column": move_target,
                                        "actor_name": "PERF",
                                    },
                                )
                            )

                if not card_id:
                    rows.append(skipped_row("mcp.get_card", "No card_id available."))
                    rows.append(skipped_row("mcp.get_card_log_compact", "No card_id available."))
                if not writes_enabled:
                    rows.append(
                        skipped_row(
                            "mcp.update_card",
                            "Write scenarios require --local-temp-server or --allow-live-writes.",
                        )
                    )
                    rows.append(
                        skipped_row(
                            "mcp.move_card",
                            "Write scenarios require --local-temp-server or --allow-live-writes.",
                        )
                    )

                rows.extend(
                    summarize(samples, scenario)
                    for scenario, samples in scenario_samples.items()
                    if samples
                )
                return {
                    "mcp_url": mcp_url,
                    "tool_count": len(tool_names),
                    "card_id": card_id,
                    "safe_mode": {
                        "local_temp_server": bool(args.local_temp_server),
                        "allow_live_writes": bool(args.allow_live_writes),
                    },
                    "rows": rows,
                }


async def run_mcp_perf(args: argparse.Namespace) -> dict[str, Any]:
    local_runtime: LocalMcpRuntime | None = None
    mcp_url = args.mcp_url
    if args.local_temp_server:
        local_runtime = start_local_mcp_runtime(args)
        mcp_url = local_runtime.mcp_url

    headers: dict[str, str] = {}
    if args.bearer_token:
        headers["Authorization"] = f"Bearer {args.bearer_token}"

    try:
        return await _run_mcp_perf_payload(mcp_url, headers, args, local_runtime)
    finally:
        if local_runtime is not None:
            local_runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AutoStop CRM MCP performance workflows.")
    parser.add_argument("--mcp-url", default="https://crm.autostopcrm.ru/mcp")
    parser.add_argument("--iterations", default=3)
    parser.add_argument("--card-id", default="")
    parser.add_argument("--bearer-token", default="")
    parser.add_argument("--local-temp-server", action="store_true")
    parser.add_argument("--allow-live-writes", action="store_true")
    parser.add_argument("--start-port", default=42731)
    parser.add_argument("--mcp-start-port", default=42831)
    args = parser.parse_args()
    args.iterations = _bounded_iterations(args.iterations)
    args.start_port = _bounded_port(args.start_port, default=42731)
    args.mcp_start_port = _bounded_port(args.mcp_start_port, default=42831)

    try:
        result = asyncio.run(run_mcp_perf(args))
    except Exception as exc:  # noqa: BLE001 - perf CLI must report connection/setup failures.
        print(
            _json_dumps(
                {"ok": False, "mcp_url": args.mcp_url, "error": str(exc)},
            )
        )
        return 2
    print(_json_dumps(result))
    failed = [
        row
        for row in result.get("rows", [])
        if row.get("failed_requests") and not row.get("skipped")
    ]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
