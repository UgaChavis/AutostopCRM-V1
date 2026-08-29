from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import statistics
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from minimal_kanban.mcp.manager_registration import (  # noqa: E402, I001
    AutostopManagerCompatibilityError,
    AutostopManagerUnavailableError,
)


_LOCAL_ISOLATED_ENVIRONMENT = {
    "AUTOSTOP_STORE_API_URL": "",
    "AUTOSTOP_STORE_READ_TOKEN": "",
    "AUTOSTOP_STORE_QUOTE_TOKEN": "",
    "AUTOSTOP_STORE_MANAGE_TOKEN": "",
    "AUTOSTOP_STORE_OWNER_TOKEN": "",
    "MINIMAL_KANBAN_MCP_BEARER_TOKEN": "",
}
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class _RedactingArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        if message.startswith(("unrecognized arguments:", "ambiguous option:")):
            message = "Unsupported command-line arguments."
        super().error(message)


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


_ENVIRONMENT_LEASE_LOCK = threading.Lock()


@dataclass
class _EnvironmentLease:
    snapshot: dict[str, tuple[bool, str]] = field(repr=False)
    active: bool = True

    @classmethod
    def apply(cls, updates: dict[str, str]) -> _EnvironmentLease:
        global _ACTIVE_ENVIRONMENT_LEASE
        with _ENVIRONMENT_LEASE_LOCK:
            if _ACTIVE_ENVIRONMENT_LEASE is not None and _ACTIVE_ENVIRONMENT_LEASE.active:
                raise RuntimeError("An isolated MCP environment lease is already active.")
            lease = cls(
                snapshot={key: (key in os.environ, os.environ.get(key, "")) for key in updates}
            )
            os.environ.update(updates)
            _ACTIVE_ENVIRONMENT_LEASE = lease
            return lease

    def restore(self) -> None:
        global _ACTIVE_ENVIRONMENT_LEASE
        with _ENVIRONMENT_LEASE_LOCK:
            if not self.active:
                return
            if _ACTIVE_ENVIRONMENT_LEASE is not self:
                raise RuntimeError("Cannot restore an environment lease owned by another runtime.")
            for key, (existed, value) in self.snapshot.items():
                if existed:
                    os.environ[key] = value
                else:
                    os.environ.pop(key, None)
            self.active = False
            _ACTIVE_ENVIRONMENT_LEASE = None


_ACTIVE_ENVIRONMENT_LEASE: _EnvironmentLease | None = None


def _try_cleanup(callback: Callable[[], Any]) -> bool:
    try:
        callback()
    except BaseException:  # noqa: BLE001 - cleanup failure is represented by the return value.
        return False
    return True


@dataclass
class LocalMcpRuntime:
    mcp_url: str
    card_id: str = field(repr=False)
    api_runtime: Any = field(repr=False)
    mcp_runtime: Any | None = field(repr=False)
    environment_lease: _EnvironmentLease = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        _ACTIVE_LOCAL_RUNTIMES.pop(id(self), None)
        try:
            try:
                if self.mcp_runtime is not None:
                    self.mcp_runtime.stop()
            except BaseException:  # noqa: BLE001 - keep isolation while MCP may still run.
                _try_cleanup(self.api_runtime.close)
                raise
            self.api_runtime.close()
            self.environment_lease.restore()
        except BaseException:  # noqa: BLE001 - retain every owner for an explicit retry.
            _retain_failed_local_runtime(self)
            raise
        _release_retained_local_runtime(self)
        self._closed = True


_ACTIVE_LOCAL_RUNTIMES: dict[int, LocalMcpRuntime] = {}
_RETAINED_FAILED_LOCAL_RUNTIMES: list[LocalMcpRuntime] = []


def _retain_failed_local_runtime(runtime: LocalMcpRuntime) -> None:
    if not any(retained is runtime for retained in _RETAINED_FAILED_LOCAL_RUNTIMES):
        _RETAINED_FAILED_LOCAL_RUNTIMES.append(runtime)


def _release_retained_local_runtime(runtime: LocalMcpRuntime) -> None:
    _RETAINED_FAILED_LOCAL_RUNTIMES[:] = [
        retained for retained in _RETAINED_FAILED_LOCAL_RUNTIMES if retained is not runtime
    ]


def _retry_retained_local_runtime_cleanup() -> None:
    for retained in tuple(_RETAINED_FAILED_LOCAL_RUNTIMES):
        retained.close()


def _split_mcp_url(value: Any) -> SplitResult:
    raw = str(value or "")
    if not raw or raw != raw.strip() or any(character.isspace() for character in raw):
        raise ValueError("MCP URL must be a non-empty absolute HTTP(S) URL.")
    try:
        parsed = urlsplit(raw)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("MCP URL is invalid.") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("MCP URL must be an absolute HTTP(S) URL.")
    return parsed


def _safe_url_netloc(parsed: SplitResult) -> str:
    hostname = str(parsed.hostname or "").lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{host}:{parsed.port}" if parsed.port is not None else host


def _validated_mcp_url(value: Any) -> str:
    parsed = _split_mcp_url(value)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("MCP URL must not contain user credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("MCP URL must not contain a query or fragment.")
    if parsed.scheme.lower() == "http" and parsed.hostname not in _LOOPBACK_HOSTS:
        raise ValueError("Remote MCP URLs must use HTTPS.")
    return urlunsplit((parsed.scheme.lower(), _safe_url_netloc(parsed), parsed.path or "/", "", ""))


def _report_mcp_url(value: Any) -> str:
    try:
        parsed = _split_mcp_url(value)
        return urlunsplit((parsed.scheme.lower(), _safe_url_netloc(parsed), "", "", ""))
    except (TypeError, ValueError):
        return "<invalid-mcp-url>"


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
    _retry_retained_local_runtime_cleanup()
    logger = _logger()
    from minimal_kanban.mcp.manager_registration import preflight_autostop_manager_registrar

    preflight_autostop_manager_registrar(logger, strict=True)

    from browser_smoke_runtime import start_temp_runtime

    api_runtime = start_temp_runtime(start_port=args.start_port)
    environment_lease: _EnvironmentLease | None = None
    local_runtime: LocalMcpRuntime | None = None
    try:
        base_dir = Path(api_runtime.temp_dir.name)
        manager_env_file = base_dir / "autostop-manager-empty.env"
        manager_env_file.write_text(
            "# Isolated MCP performance environment.\n",
            encoding="utf-8",
        )
        environment_lease = _EnvironmentLease.apply(
            {
                **_LOCAL_ISOLATED_ENVIRONMENT,
                "AUTOSTOP_MANAGER_DB": str(base_dir / "autostop-manager.sqlite3"),
                "AUTOSTOP_MANAGER_ENV_FILE": str(manager_env_file),
            }
        )
        local_runtime = LocalMcpRuntime(
            mcp_url="",
            card_id=api_runtime.card_id,
            api_runtime=api_runtime,
            mcp_runtime=None,
            environment_lease=environment_lease,
        )
        from minimal_kanban.mcp.client import BoardApiClient
        from minimal_kanban.mcp.runtime import McpServerRuntime
        from minimal_kanban.mcp.server import create_mcp_server

        board_api = BoardApiClient(
            api_runtime.base_url,
            bearer_token=api_runtime.api_token,
        )
        mcp_server = create_mcp_server(
            board_api,
            logger,
            host="127.0.0.1",
            port=args.mcp_start_port,
            path="/mcp",
            bearer_token="",
            public_endpoint_url=f"http://127.0.0.1:{args.mcp_start_port}/mcp",
        )
        mcp_runtime = McpServerRuntime(mcp_server, logger, auth_mode="none")
        local_runtime.mcp_runtime = mcp_runtime
        local_runtime.mcp_url = mcp_runtime.base_url
        mcp_runtime.start()
        _ACTIVE_LOCAL_RUNTIMES[id(local_runtime)] = local_runtime
        return local_runtime
    except BaseException:  # noqa: BLE001 - partial local startup must release every resource.
        if local_runtime is not None:
            _try_cleanup(local_runtime.close)
        else:
            _try_cleanup(api_runtime.close)
        raise


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
        application_error = isinstance(payload, dict) and payload.get("ok") is False
        return {
            "duration_ms": duration_ms,
            "payload_bytes": payload_size(payload),
            "error": "tool_error"
            if getattr(result, "isError", False)
            else "application_error"
            if application_error
            else None,
        }
    except Exception as exc:  # noqa: BLE001 - perf report should capture all failures.
        return {
            "duration_ms": 0.0,
            "payload_bytes": 0,
            "error": type(exc).__name__,
        }


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
                "error": None,
            },
            [tool.name for tool in result.tools],
        )
    except Exception as exc:  # noqa: BLE001
        return (
            {
                "duration_ms": 0.0,
                "payload_bytes": 0,
                "error": type(exc).__name__,
            },
            [],
        )


def _writes_enabled(local_runtime: LocalMcpRuntime | None, mcp_url: str) -> bool:
    if type(local_runtime) is not LocalMcpRuntime:
        return False
    if _ACTIVE_LOCAL_RUNTIMES.get(id(local_runtime)) is not local_runtime:
        return False
    if (
        local_runtime._closed
        or not isinstance(local_runtime.environment_lease, _EnvironmentLease)
        or not local_runtime.environment_lease.active
        or local_runtime.environment_lease is not _ACTIVE_ENVIRONMENT_LEASE
        or local_runtime.mcp_url != mcp_url
    ):
        return False
    try:
        parsed = _split_mcp_url(mcp_url)
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "http"
        and parsed.hostname in _LOOPBACK_HOSTS
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
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


async def _move_column_pair(session: ClientSession, card_id: str) -> tuple[str, str]:
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
                return current_column, column_id
    return current_column, current_column


def _move_target_for_iteration(columns: tuple[str, str], index: int) -> str:
    current_column, alternative_column = columns
    if not current_column or not alternative_column or current_column == alternative_column:
        return ""
    return alternative_column if index % 2 == 0 else current_column


def _write_skip_rows(
    *, card_id: str, writes_enabled: bool, move_columns: tuple[str, str]
) -> list[dict[str, Any]]:
    if not writes_enabled:
        reason = "Write scenarios require a process-created local temp runtime."
        return [
            skipped_row("mcp.update_card", reason),
            skipped_row("mcp.move_card", reason),
        ]
    if not card_id:
        reason = "No card_id available."
        return [
            skipped_row("mcp.update_card", reason),
            skipped_row("mcp.move_card", reason),
        ]
    if not _move_target_for_iteration(move_columns, 0):
        return [
            skipped_row(
                "mcp.move_card",
                "No distinct local temp columns are available for write samples.",
            )
        ]
    return []


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
        headers=headers,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
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
                    local_runtime.card_id if local_runtime else "",
                )
                move_columns = ("", "")
                writes_enabled = _writes_enabled(local_runtime, mcp_url)
                if writes_enabled and card_id:
                    move_columns = await _move_column_pair(session, card_id)

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
                            move_target = _move_target_for_iteration(move_columns, index)
                            if move_target:
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
                rows.extend(
                    _write_skip_rows(
                        card_id=card_id,
                        writes_enabled=writes_enabled,
                        move_columns=move_columns,
                    )
                )

                rows.extend(
                    summarize(samples, scenario)
                    for scenario, samples in scenario_samples.items()
                    if samples
                )
                return {
                    "mcp_url": _report_mcp_url(mcp_url),
                    "tool_count": len(tool_names),
                    "safe_mode": {
                        "local_temp_server": writes_enabled,
                        "remote_read_only": not writes_enabled,
                    },
                    "rows": rows,
                }


async def run_mcp_perf(args: argparse.Namespace) -> dict[str, Any]:
    local_runtime: LocalMcpRuntime | None = None
    mcp_url = _validated_mcp_url(args.mcp_url)
    if args.local_temp_server:
        local_runtime = start_local_mcp_runtime(args)
        mcp_url = local_runtime.mcp_url

    headers: dict[str, str] = {}
    if local_runtime is None:
        bearer_token = str(os.environ.get(args.token_env, "") or "").strip()
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"

    try:
        return await _run_mcp_perf_payload(mcp_url, headers, args, local_runtime)
    finally:
        if local_runtime is not None:
            local_runtime.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = _RedactingArgumentParser(
        description="Run AutoStop CRM MCP performance workflows.",
        allow_abbrev=False,
    )
    parser.add_argument("--mcp-url", default="https://crm.autostopcrm.ru/mcp")
    parser.add_argument("--iterations", default=3)
    parser.add_argument(
        "--token-env",
        default="MINIMAL_KANBAN_MCP_BEARER_TOKEN",
        help="Read the bearer from this environment variable instead of a process argument.",
    )
    parser.add_argument("--local-temp-server", action="store_true")
    parser.add_argument("--start-port", default=42731)
    parser.add_argument("--mcp-start-port", default=42831)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    args.iterations = _bounded_iterations(args.iterations)
    args.start_port = _bounded_port(args.start_port, default=42731)
    args.mcp_start_port = _bounded_port(args.mcp_start_port, default=42831)

    try:
        result = asyncio.run(run_mcp_perf(args))
    except (AutostopManagerCompatibilityError, AutostopManagerUnavailableError) as exc:
        error = (
            "autostop_manager_incompatible"
            if isinstance(exc, AutostopManagerCompatibilityError)
            else "autostop_manager_unavailable"
        )
        print(_json_dumps({"ok": False, "error": error, "stage": "local_preflight"}))
        return 2
    except Exception as exc:  # noqa: BLE001 - perf CLI must report connection/setup failures.
        print(
            _json_dumps(
                {
                    "ok": False,
                    "mcp_url": _report_mcp_url(args.mcp_url),
                    "error": type(exc).__name__,
                },
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
