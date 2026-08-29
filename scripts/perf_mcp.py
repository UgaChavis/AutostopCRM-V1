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
from collections.abc import Awaitable, Callable, Mapping
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
from minimal_kanban.mcp.agent_gateway_support import (  # noqa: E402, I001
    AGENT_GATEWAY_FORMAT,
    PERMANENT_AGENT_GATEWAY_TOOL_NAMES,
)


_LOCAL_ISOLATED_ENVIRONMENT = {
    "AUTOSTOP_DEPLOYMENT_ENV": "development",
    "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "0",
    "AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED": "1",
    "AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED": "0",
    "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "0",
    "AUTOSTOP_AGENT_SERVICE_IDENTITY": "perf-mcp-local",
    "AUTOSTOP_MCP_OAUTH_ENABLED": "0",
    "AUTOSTOP_STORE_API_URL": "",
    "AUTOSTOP_STORE_READ_TOKEN": "",
    "AUTOSTOP_STORE_QUOTE_TOKEN": "",
    "AUTOSTOP_STORE_MANAGE_TOKEN": "",
    "AUTOSTOP_STORE_OWNER_TOKEN": "",
    "MINIMAL_KANBAN_MCP_BEARER_TOKEN": "",
}
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_GATEWAY_SCENARIOS = (
    "mcp.tools_list",
    "mcp.agent_bootstrap",
    "mcp.agent_board_digest",
    "mcp.agent_entity_context",
    "mcp.agent_board_workflow_dry_run",
)
_LOCAL_WORKFLOW_SCENARIO = "mcp.agent_board_workflow_dry_run"


class GatewayV2SurfaceMismatchError(RuntimeError):
    def __init__(self, *, actual_tool_count: int) -> None:
        super().__init__("Gateway v2 public tool surface is incompatible.")
        self.actual_tool_count = actual_tool_count


class LocalMcpOwnershipError(RuntimeError):
    pass


def _single_nested_preflight_error(error: BaseException) -> BaseException | None:
    leaves: list[BaseException] = []

    def collect(current: BaseException) -> None:
        if isinstance(current, BaseExceptionGroup):
            for nested in current.exceptions:
                collect(nested)
            return
        leaves.append(current)

    collect(error)
    if len(leaves) == 1 and isinstance(
        leaves[0],
        (GatewayV2SurfaceMismatchError, LocalMcpOwnershipError),
    ):
        return leaves[0]
    return None


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
    mcp_url: str = field(repr=False)
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
                "AUTOSTOP_MAINTENANCE_MARKER": str(base_dir / "maintenance-disabled"),
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


def _strict_gateway_envelope(result: Any) -> tuple[dict[str, Any] | None, str | None]:
    is_error = getattr(result, "isError", False) is True
    payload = getattr(result, "structuredContent", None)
    if not isinstance(payload, Mapping):
        return None, "tool_error" if is_error else "invalid_structured_content"
    envelope = dict(payload)
    if (
        envelope.get("format") != AGENT_GATEWAY_FORMAT
        or type(envelope.get("ok")) is not bool
        or not isinstance(envelope.get("status"), str)
        or not envelope["status"].strip()
        or any(
            not isinstance(envelope.get(key), Mapping)
            for key in ("summary", "verification", "page", "meta")
        )
        or any(
            not isinstance(envelope.get(key), list)
            for key in ("changes", "warnings", "next_actions")
        )
    ):
        return None, "invalid_gateway_envelope"
    if is_error and envelope["ok"]:
        return None, "tool_result_inconsistent"
    return envelope, None if envelope["ok"] else "application_error"


async def _call_gateway_tool_sample(
    session: ClientSession,
    tool_name: str,
    args: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    try:
        duration_ms, result = await _measure(lambda: session.call_tool(tool_name, args))
        envelope, error = _strict_gateway_envelope(result)
        return (
            {
                "duration_ms": duration_ms,
                "payload_bytes": payload_size(envelope) if envelope is not None else 0,
                "error": error,
            },
            envelope,
        )
    except Exception:  # noqa: BLE001 - reports expose only one fixed transport code.
        return (
            {
                "duration_ms": 0.0,
                "payload_bytes": 0,
                "error": "transport_error",
            },
            None,
        )


async def _list_tools_sample(session: ClientSession) -> tuple[dict[str, Any], list[str]]:
    try:
        duration_ms, result = await _measure(session.list_tools)
        tools = getattr(result, "tools", None)
        if not isinstance(tools, list):
            raise TypeError("invalid tool list")
        tool_descriptors: list[dict[str, Any]] = []
        tool_names: list[str] = []
        for tool in tools:
            name = getattr(tool, "name", None)
            description = getattr(tool, "description", None)
            input_schema = getattr(tool, "inputSchema", None)
            if not isinstance(name, str):
                raise TypeError("invalid tool name")
            if description is not None and not isinstance(description, str):
                raise TypeError("invalid tool description")
            if not isinstance(input_schema, Mapping):
                raise TypeError("invalid tool input schema")
            tool_names.append(name)
            tool_descriptors.append(
                {
                    "name": name,
                    "description": description or "",
                    "inputSchema": dict(input_schema),
                }
            )
        return (
            {
                "duration_ms": duration_ms,
                "payload_bytes": payload_size(tool_descriptors),
                "error": None,
            },
            tool_names,
        )
    except Exception:  # noqa: BLE001 - reports expose only one fixed list error.
        return (
            {
                "duration_ms": 0.0,
                "payload_bytes": 0,
                "error": "tool_list_error",
            },
            [],
        )


def _require_gateway_v2_surface(tool_names: list[str]) -> None:
    if (
        len(tool_names) != len(PERMANENT_AGENT_GATEWAY_TOOL_NAMES)
        or frozenset(tool_names) != PERMANENT_AGENT_GATEWAY_TOOL_NAMES
    ):
        raise GatewayV2SurfaceMismatchError(actual_tool_count=len(tool_names))


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


def _safe_entity_id(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if (
        not normalized
        or normalized != value
        or len(normalized) > 160
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        return ""
    return normalized


def _card_id_from_digest(envelope: Mapping[str, Any] | None) -> str:
    if not isinstance(envelope, Mapping) or envelope.get("ok") is not True:
        return ""
    data = envelope.get("data")
    cards = data.get("cards") if isinstance(data, Mapping) else None
    if not isinstance(cards, list):
        return ""
    for card in cards:
        if isinstance(card, Mapping):
            card_id = _safe_entity_id(card.get("id"))
            if card_id:
                return card_id
    return ""


def _failed_sample(error: str) -> dict[str, Any]:
    return {"duration_ms": 0.0, "payload_bytes": 0, "error": error}


def _contract_violations(
    rows: list[dict[str, Any]],
    *,
    iterations: int,
    writes_enabled: bool,
) -> list[str]:
    violations: list[str] = []
    rows_by_scenario: dict[str, dict[str, Any]] = {}
    for row in rows:
        scenario = row.get("scenario")
        if not isinstance(scenario, str) or scenario not in _GATEWAY_SCENARIOS:
            violations.append("gateway:unexpected_scenario")
            continue
        if scenario in rows_by_scenario:
            violations.append(f"{scenario}:duplicate_scenario")
            continue
        rows_by_scenario[scenario] = row

    for scenario in _GATEWAY_SCENARIOS:
        row = rows_by_scenario.get(scenario)
        if row is None:
            violations.append(f"{scenario}:missing_scenario")
            continue
        skipped = row.get("skipped") is True
        remote_workflow = not writes_enabled and scenario == _LOCAL_WORKFLOW_SCENARIO
        if skipped:
            if not remote_workflow:
                violations.append(f"{scenario}:required_scenario_skipped")
            if row.get("iterations") != 0:
                violations.append(f"{scenario}:iteration_count_mismatch")
            continue
        if remote_workflow:
            violations.append(f"{scenario}:remote_workflow_not_skipped")
        if row.get("iterations") != iterations:
            violations.append(f"{scenario}:iteration_count_mismatch")
        failed_requests = row.get("failed_requests")
        if not isinstance(failed_requests, list):
            violations.append(f"{scenario}:invalid_failed_requests")
            continue
        for error in failed_requests:
            violations.append(f"{scenario}:{error}")
    return violations


async def _run_gateway_session(
    session: ClientSession,
    *,
    mcp_url: str,
    args: argparse.Namespace,
    local_runtime: LocalMcpRuntime | None,
) -> dict[str, Any]:
    scenario_samples: dict[str, list[dict[str, Any]]] = {
        scenario: [] for scenario in _GATEWAY_SCENARIOS
    }
    iterations = max(1, args.iterations)
    writes_enabled = _writes_enabled(local_runtime, mcp_url)
    if local_runtime is not None and not writes_enabled:
        raise LocalMcpOwnershipError("Local MCP runtime ownership is invalid.")
    first_tools_sample, tool_names = await _list_tools_sample(session)
    _require_gateway_v2_surface(tool_names)
    scenario_samples["mcp.tools_list"].append(first_tools_sample)
    local_card_id = (
        _safe_entity_id(local_runtime.card_id)
        if writes_enabled and local_runtime is not None
        else ""
    )

    for index in range(iterations):
        if index > 0:
            tools_sample, current_tool_names = await _list_tools_sample(session)
            _require_gateway_v2_surface(current_tool_names)
            scenario_samples["mcp.tools_list"].append(tools_sample)

        bootstrap_sample, _ = await _call_gateway_tool_sample(
            session,
            "agent_bootstrap",
            {"sample_limit": 8},
        )
        scenario_samples["mcp.agent_bootstrap"].append(bootstrap_sample)

        digest_sample, digest_envelope = await _call_gateway_tool_sample(
            session,
            "agent_board_digest",
            {"scope": "crm", "include_archived": False, "limit": 50},
        )
        scenario_samples["mcp.agent_board_digest"].append(digest_sample)
        card_id = local_card_id or _card_id_from_digest(digest_envelope)
        if card_id:
            context_sample, _ = await _call_gateway_tool_sample(
                session,
                "agent_entity_context",
                {"entity": "card", "entity_id": card_id, "detail": "summary"},
            )
            scenario_samples["mcp.agent_entity_context"].append(context_sample)
        else:
            scenario_samples["mcp.agent_entity_context"].append(
                _failed_sample("fixture_unavailable")
            )

        if writes_enabled:
            workflow_sample, _ = await _call_gateway_tool_sample(
                session,
                "agent_board_workflow",
                {
                    "operation": "manager_board_scan",
                    "payload": {"limit": 5},
                    "idempotency_key": f"perf-mcp-manager-board-scan-{index}",
                    "mode": "dry_run",
                },
            )
            scenario_samples["mcp.agent_board_workflow_dry_run"].append(workflow_sample)

    rows = [
        summarize(samples, scenario) for scenario, samples in scenario_samples.items() if samples
    ]
    if not writes_enabled:
        rows.append(
            skipped_row(
                "mcp.agent_board_workflow_dry_run",
                "Remote MCP targets are read-only.",
            )
        )
    violations = _contract_violations(
        rows,
        iterations=iterations,
        writes_enabled=writes_enabled,
    )
    return {
        "ok": not violations,
        "target_kind": "local_temp" if writes_enabled else "remote_read_only",
        "mcp_url": "<local-temp>" if writes_enabled else _report_mcp_url(mcp_url),
        "surface": AGENT_GATEWAY_FORMAT,
        "tool_count": len(tool_names),
        "safe_mode": {
            "local_temp_server": writes_enabled,
            "remote_read_only": not writes_enabled,
            "gateway_v2_only": True,
        },
        "rows": rows,
        "violations": violations,
        "threshold_status": "failed" if violations else "passed",
    }


async def _run_mcp_perf_payload(
    mcp_url: str,
    headers: dict[str, str],
    args: argparse.Namespace,
    local_runtime: LocalMcpRuntime | None,
) -> dict[str, Any]:
    timeout = httpx.Timeout(45.0, connect=10.0, read=45.0, write=45.0, pool=45.0)
    try:
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
                    return await _run_gateway_session(
                        session,
                        mcp_url=mcp_url,
                        args=args,
                        local_runtime=local_runtime,
                    )
    except BaseExceptionGroup as exc:
        preflight_error = _single_nested_preflight_error(exc)
        if preflight_error is not None:
            raise preflight_error from None
        raise


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
    except GatewayV2SurfaceMismatchError as exc:
        print(
            _json_dumps(
                {
                    "ok": False,
                    "error": "gateway_v2_surface_mismatch",
                    "stage": "mcp_preflight",
                    "expected_tool_count": len(PERMANENT_AGENT_GATEWAY_TOOL_NAMES),
                    "actual_tool_count": exc.actual_tool_count,
                }
            )
        )
        return 2
    except LocalMcpOwnershipError:
        print(
            _json_dumps(
                {
                    "ok": False,
                    "error": "local_runtime_ownership_invalid",
                    "stage": "local_preflight",
                }
            )
        )
        return 2
    except Exception as exc:  # noqa: BLE001 - perf CLI must report connection/setup failures.
        payload = {
            "ok": False,
            "target_kind": "local_temp" if args.local_temp_server else "remote_read_only",
            "error": type(exc).__name__,
        }
        if not args.local_temp_server:
            payload["mcp_url"] = _report_mcp_url(args.mcp_url)
        print(_json_dumps(payload))
        return 2
    print(_json_dumps(result))
    rows = result.get("rows")
    expected_target_kind = "local_temp" if args.local_temp_server else "remote_read_only"
    valid_rows = (
        rows if isinstance(rows, list) and all(isinstance(row, dict) for row in rows) else None
    )
    contract_violations = (
        _contract_violations(
            valid_rows,
            iterations=args.iterations,
            writes_enabled=args.local_temp_server,
        )
        if valid_rows is not None
        else ["report:invalid_rows"]
    )
    violations = result.get("violations")
    passed = (
        result.get("ok") is True
        and result.get("target_kind") == expected_target_kind
        and result.get("threshold_status") == "passed"
        and isinstance(violations, list)
        and not violations
        and not contract_violations
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
