from __future__ import annotations

import html
import json
import math
import os
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from logging import Logger
from pathlib import Path
from time import perf_counter
from typing import Annotated, Any, Literal
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from mcp.server.auth.provider import AuthorizeError
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from ..config import (
    get_mcp_bearer_token,
    get_mcp_host,
    get_mcp_path,
    get_mcp_port,
    get_mcp_public_base_url,
)
from ..deployment_security import load_agent_gateway_security_policy
from ..services.snapshot_service import GPT_WALL_AGENT_EVENT_LIMIT
from ..settings_models import derive_allowed_hosts, derive_allowed_origins
from .agent_gateway_v2 import register_agent_gateway_v2
from .auth import StaticBearerTokenVerifier, build_auth_settings
from .client import BoardApiClient, BoardApiTransportError
from .oauth_provider import (
    DEFAULT_KANBAN_SCOPES,
    OAUTH_CONSENT_PATH,
    ProductionOAuthAuthorizationServerProvider,
)
from .tool_registry import MCP_TOOL_GROUPS, PUBLIC_MCP_TOOL_NAMES

_AUTOSTOP_MANAGER_READ_ONLY_TOOLS = frozenset(
    {
        "agent_brief",
        "audit_knowledge_annotations",
        "audit_knowledge_base",
        "audit_memory",
        "audit_skill_registry",
        "cleanup_audit",
        "crm_health_plan",
        "estimate_repair_work_cost",
        "get_store_analytics_report",
        "list_manager_runs",
        "lookup_original_parts",
        "memory_context_for",
        "memory_gaps",
        "memory_map",
        "memory_topics",
        "prepare_manager_context",
        "probe_knowledge_base",
        "recall",
        "recall_lessons",
        "recommend_automotive_sources",
        "recommend_fluid_maintenance_sources",
        "recommend_service_management_actions",
        "search_knowledge_base",
        "system_audit",
        "today_context",
    }
)

_AUTOSTOP_MANAGER_WRITE_TOOLS = frozenset(
    {
        "add_manager_task",
        "curate_memory",
        "finish_manager_run",
        "learn_from_feedback",
        "manager_journal",
        "record_manager_run_event",
        "remember",
        "start_manager_run",
        "sync_knowledge_base",
    }
)


def _reject_bool_int(value: Any) -> Any:
    if isinstance(value, bool):
        raise ValueError("boolean values are not valid integer parameters")
    return value


McpInt = Annotated[int | float | str, BeforeValidator(_reject_bool_int)]


def _try_register_autostop_manager_tools(server: FastMCP, logger: Logger) -> None:
    configured_path = os.environ.get("AUTOSTOP_MANAGER_PATH", "").strip()
    repo_root = Path(__file__).resolve().parents[3]
    candidates = []
    if configured_path:
        candidates.append(Path(configured_path).expanduser())
    candidates.extend(
        [
            repo_root.parent / "AutostopManager",
            repo_root.parent.parent / "AutostopManager",
            Path("/opt/AutostopManager"),
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            candidate_text = str(candidate)
            if candidate_text not in sys.path:
                sys.path.insert(0, candidate_text)
            break

    try:
        from autostop_manager.mcp_tools import register_manager_memory_tools
    except Exception as exc:  # pragma: no cover - optional sibling project
        logger.info("autostop_manager.memory_tools unavailable: %s", exc)
        return

    register_manager_memory_tools(server)
    logger.info("autostop_manager.memory_tools registered")


class DeadlinePayload(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"days": 1, "hours": 0, "minutes": 0, "seconds": 0},
                {"total_seconds": 5400},
            ]
        }
    )

    days: McpInt = Field(default=0, ge=0, le=365, description="Whole days in the deadline delta.")
    hours: McpInt = Field(default=0, ge=0, le=23, description="Hours in the deadline delta.")
    minutes: McpInt = Field(default=0, ge=0, le=59, description="Minutes in the deadline delta.")
    seconds: McpInt = Field(default=0, ge=0, le=59, description="Seconds in the deadline delta.")
    total_seconds: McpInt = Field(
        default=0,
        ge=0,
        le=31_536_000,
        description="Optional shorthand for the full deadline in seconds. Can be combined with days, hours, minutes, and seconds.",
    )


class StickyDeadlinePayload(DeadlinePayload):
    total_seconds: McpInt = Field(default=0, ge=0, le=31_536_000)


class TagPayload(BaseModel):
    label: str = Field(min_length=1, max_length=24)
    color: Literal["green", "yellow", "red"] = "green"


class RepairOrderRowPayload(BaseModel):
    name: str = Field(default="", max_length=240)
    catalog_number: str = Field(default="", max_length=160)
    catalogNumber: str = Field(default="", max_length=160)
    quantity: str = Field(default="", max_length=40)
    cost_price: str = Field(default="", max_length=40)
    costPrice: str = Field(default="", max_length=40)
    price: str = Field(default="", max_length=40)
    total: str = Field(default="", max_length=40)
    executor_id: str = Field(default="", max_length=64)
    executor_name: str = Field(default="", max_length=160)
    work_executor_id_snapshot: str = Field(default="", max_length=64)
    work_executor_name_snapshot: str = Field(default="", max_length=160)
    material_executor_id_snapshot: str = Field(default="", max_length=64)
    material_executor_name_snapshot: str = Field(default="", max_length=160)
    material_quantity_snapshot: str = Field(default="", max_length=40)


class RepairOrderPaymentPayload(BaseModel):
    id: str | None = Field(default=None, max_length=80)
    amount: str = Field(default="", max_length=40)
    paid_at: str | None = Field(default=None, max_length=32)
    note: str | None = Field(default=None, max_length=240)
    payment_method: Literal["cash", "cashless", "card"] | None = None
    actor_name: str | None = Field(default=None, max_length=160)
    cashbox_id: str | None = Field(default=None, max_length=80)
    cashbox_name: str | None = Field(default=None, max_length=160)
    cash_transaction_id: str | None = Field(default=None, max_length=80)


class RepairOrderPatchPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "client": "Иван Иванов",
                    "comment": "Согласовать дальнейшую диагностику",
                    "note": "Комментарий мастера",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "2000"}],
                },
                {
                    "clientInformation": "Информация для клиента",
                    "master_comment": "Внутренняя заметка мастера",
                    "advancePayment": "500",
                },
            ]
        },
    )

    number: str | None = Field(default=None, max_length=40)
    date: str | None = Field(default=None, max_length=32)
    status: Literal["open", "ready", "closed"] | None = None
    opened_at: str | None = Field(default=None, max_length=32)
    openedAt: str | None = Field(default=None, max_length=32)
    closed_at: str | None = Field(default=None, max_length=32)
    closedAt: str | None = Field(default=None, max_length=32)
    client: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=160)
    vehicle: str | None = Field(default=None, max_length=160)
    license_plate: str | None = Field(default=None, max_length=160)
    licensePlate: str | None = Field(default=None, max_length=160)
    vin: str | None = Field(default=None, max_length=160)
    mileage: str | None = Field(default=None, max_length=160)
    odometer: str | None = Field(default=None, max_length=160)
    payment_method: Literal["cash", "cashless", "card"] | None = None
    paymentMethod: Literal["cash", "cashless", "card"] | None = None
    prepayment: str | None = Field(default=None, max_length=40)
    advance_payment: str | None = Field(default=None, max_length=40)
    advancePayment: str | None = Field(default=None, max_length=40)
    payments: list[RepairOrderPaymentPayload] | None = None
    payment_history: list[RepairOrderPaymentPayload] | None = None
    reason: str | None = Field(default=None, max_length=4000)
    comment: str | None = Field(default=None, max_length=4000)
    client_information: str | None = Field(default=None, max_length=4000)
    clientInformation: str | None = Field(default=None, max_length=4000)
    note: str | None = Field(default=None, max_length=4000)
    master_comment: str | None = Field(default=None, max_length=4000)
    masterComment: str | None = Field(default=None, max_length=4000)
    internal_comment: str | None = Field(default=None, max_length=4000)
    internalComment: str | None = Field(default=None, max_length=4000)
    tags: list[TagPayload] | None = None
    works: list[RepairOrderRowPayload] | None = None
    materials: list[RepairOrderRowPayload] | None = None


class ClientVehiclePayload(BaseModel):
    id: str | None = Field(default=None, max_length=128)
    vehicle: str | None = Field(default=None, max_length=160)
    brand: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    vin: str | None = Field(default=None, max_length=160)
    license_plate: str | None = Field(default=None, max_length=160)
    year: str | None = Field(default=None, max_length=16)
    mileage: str | None = Field(default=None, max_length=160)
    body_number: str | None = Field(default=None, max_length=160)
    chassis_number: str | None = Field(default=None, max_length=160)
    engine_code: str | None = Field(default=None, max_length=160)
    engine_model: str | None = Field(default=None, max_length=160)
    gearbox_type: str | None = Field(default=None, max_length=160)
    gearbox_model: str | None = Field(default=None, max_length=160)
    drivetrain: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=2000)


class ClientProfilePayload(BaseModel):
    client_type: Literal["person", "ip", "ooo", "company"] = "person"
    last_name: str | None = Field(default=None, max_length=120)
    first_name: str | None = Field(default=None, max_length=120)
    middle_name: str | None = Field(default=None, max_length=120)
    display_name: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=80)
    phones: list[str] | None = Field(default=None, max_length=3)
    email: str | None = Field(default=None, max_length=160)
    emails: list[str] | None = Field(default=None, max_length=3)
    comment: str | None = Field(default=None, max_length=2000)
    legal_name: str | None = Field(default=None, max_length=160)
    short_name: str | None = Field(default=None, max_length=160)
    inn: str | None = Field(default=None, max_length=160)
    kpp: str | None = Field(default=None, max_length=160)
    ogrn: str | None = Field(default=None, max_length=160)
    checking_account: str | None = Field(default=None, max_length=160)
    bank_name: str | None = Field(default=None, max_length=160)
    bik: str | None = Field(default=None, max_length=160)
    correspondent_account: str | None = Field(default=None, max_length=160)
    legal_address: str | None = Field(default=None, max_length=160)
    actual_address: str | None = Field(default=None, max_length=160)
    contact_person: str | None = Field(default=None, max_length=160)
    contact_position: str | None = Field(default=None, max_length=160)
    vehicles: list[ClientVehiclePayload] | None = None


class ClientPatchPayload(BaseModel):
    client_type: Literal["person", "ip", "ooo", "company"] | None = None
    last_name: str | None = Field(default=None, max_length=120)
    first_name: str | None = Field(default=None, max_length=120)
    middle_name: str | None = Field(default=None, max_length=120)
    display_name: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=80)
    phones: list[str] | None = Field(default=None, max_length=3)
    email: str | None = Field(default=None, max_length=160)
    emails: list[str] | None = Field(default=None, max_length=3)
    comment: str | None = Field(default=None, max_length=2000)
    legal_name: str | None = Field(default=None, max_length=160)
    short_name: str | None = Field(default=None, max_length=160)
    inn: str | None = Field(default=None, max_length=160)
    kpp: str | None = Field(default=None, max_length=160)
    ogrn: str | None = Field(default=None, max_length=160)
    checking_account: str | None = Field(default=None, max_length=160)
    bank_name: str | None = Field(default=None, max_length=160)
    bik: str | None = Field(default=None, max_length=160)
    correspondent_account: str | None = Field(default=None, max_length=160)
    legal_address: str | None = Field(default=None, max_length=160)
    actual_address: str | None = Field(default=None, max_length=160)
    contact_person: str | None = Field(default=None, max_length=160)
    contact_position: str | None = Field(default=None, max_length=160)
    vehicles: list[ClientVehiclePayload] | None = None


def _deadline_part_value(value: Any, *, maximum: int) -> int:
    if isinstance(value, bool):
        return 0
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return 0
    if not math.isfinite(numeric) or not numeric.is_integer():
        return 0
    if numeric <= 0:
        return 0
    if numeric > maximum:
        return maximum
    return int(numeric)


def _resolved_create_card_deadline(deadline: DeadlinePayload | None) -> dict[str, int]:
    if deadline is None:
        return {"days": 1, "hours": 0, "minutes": 0, "seconds": 0}
    payload = deadline.model_dump()
    resolved = {
        "days": _deadline_part_value(payload.get("days"), maximum=365),
        "hours": _deadline_part_value(payload.get("hours"), maximum=23),
        "minutes": _deadline_part_value(payload.get("minutes"), maximum=59),
        "seconds": _deadline_part_value(payload.get("seconds"), maximum=59),
        "total_seconds": _deadline_part_value(payload.get("total_seconds"), maximum=31_536_000),
    }
    if resolved["total_seconds"] > 0:
        return resolved
    if not any(resolved.get(part, 0) > 0 for part in ("days", "hours", "minutes", "seconds")):
        return {"days": 1, "hours": 0, "minutes": 0, "seconds": 0}
    return resolved


class JsonEnvelope(BaseModel):
    ok: bool
    data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None


class ConnectorIdentityPayload(BaseModel):
    connector_name: str
    product_name: str
    board_name: str
    board_scope: str
    board_key: str
    scope_rule: str
    resource_url: str
    server_base_url: str
    streamable_http_path: str
    local_bind: str
    board_api_base_url: str
    auth_mode: str
    host: str
    port: int


class ConnectorIdentityToolData(BaseModel):
    identity: ConnectorIdentityPayload
    text: str


class ConnectorIdentityEnvelope(BaseModel):
    ok: bool
    data: ConnectorIdentityToolData
    error: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None


CONNECTOR_SCHEMA_VERSION = "2026-04-13"
CONNECTOR_VERSION = "autostopcrm-mcp-2026-04-13"
_CANONICAL_TOOL_PATH_PREFIX = "/AutoStopCRM"


def _base_url_from_endpoint(url: str | None) -> str | None:
    normalized_url = _absolute_http_url(url)
    if not normalized_url:
        return None
    parsed = urlsplit(normalized_url)
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _absolute_http_url(url: str | None) -> str | None:
    if not url:
        return None
    normalized_url = str(url).strip().rstrip("/")
    if not normalized_url:
        return None
    try:
        parsed = urlsplit(normalized_url)
        parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return normalized_url


def _connector_name_from_url(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "local").strip().lower()
    except ValueError:
        host = "local"
    sanitized = "".join(char if char.isalnum() else "-" for char in host).strip("-")
    sanitized = sanitized or "local"
    return f"autostopcrm-this-board-only-{sanitized}"


def _canonical_tool_path(tool_name: str) -> str:
    normalized_tool = str(tool_name or "").strip().strip("/")
    if not normalized_tool:
        return _CANONICAL_TOOL_PATH_PREFIX
    return f"{_CANONICAL_TOOL_PATH_PREFIX}/{normalized_tool}"


def _normalize_tool_path_alias(path: str | None) -> str:
    parts = [segment for segment in str(path or "").split("/") if segment]
    if len(parts) >= 3 and parts[0].casefold() == "autostopcrm" and parts[1].startswith("link_"):
        parts = [parts[0]] + parts[2:]
    if not parts:
        return ""
    return "/" + "/".join(parts)


def _external_product_text(text: str) -> str:
    return (
        str(text or "")
        .replace("Current Minimal Kanban Board", "Current AutoStop CRM Board")
        .replace("current Minimal Kanban board", "current AutoStop CRM board")
        .replace("Minimal Kanban MCP connector", "AutoStop CRM MCP connector")
        .replace("Minimal Kanban MCP tool", "AutoStop CRM MCP tool")
        .replace("Minimal Kanban", "AutoStop CRM")
    )


def _single_board_rule_text() -> str:
    return _external_product_text(
        "This connector may operate only on the current AutoStop CRM board served by this exact MCP/API endpoint. "
        "Do not use it for Trello, YouGile, or any other kanban connector."
    )


def _tool_scope_suffix() -> str:
    return "Scope: current AutoStop CRM board only."


def _scoped_description(summary: str) -> str:
    return f"{_external_product_text(summary)} {_tool_scope_suffix()}"


def _read_tool_annotations(title: str | None = None) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


def _write_tool_annotations(
    title: str | None = None,
    *,
    destructive: bool = False,
    idempotent: bool = False,
) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=False,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=False,
    )


def _title_from_tool_name(tool_name: str) -> str:
    return " ".join(part.capitalize() for part in str(tool_name).split("_") if part)


def _compact_mapping_payload(payload: dict[str, Any] | BaseModel | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    if isinstance(payload, BaseModel):
        return payload.model_dump(exclude_none=True)
    return {str(key): value for key, value in dict(payload).items() if value is not None}


def _tag_list_payload(tags: list[Any] | None) -> list[Any] | None:
    if tags is None:
        return None
    payload: list[Any] = []
    for tag in tags:
        if isinstance(tag, BaseModel):
            payload.append(tag.model_dump(exclude_none=True))
        elif isinstance(tag, dict):
            payload.append({str(key): value for key, value in tag.items() if value is not None})
        else:
            payload.append(str(tag))
    return payload


def _annotate_autostop_manager_tools(server: FastMCP, logger: Logger) -> None:
    tool_manager = getattr(server, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", None)
    if not isinstance(tools, dict):
        return

    updated = 0
    for tool_name in _AUTOSTOP_MANAGER_READ_ONLY_TOOLS:
        tool = tools.get(tool_name)
        if tool is None:
            continue
        tool.annotations = _read_tool_annotations(
            getattr(tool, "title", None) or _title_from_tool_name(tool_name)
        )
        updated += 1

    for tool_name in _AUTOSTOP_MANAGER_WRITE_TOOLS:
        tool = tools.get(tool_name)
        if tool is None:
            continue
        tool.annotations = _write_tool_annotations(
            getattr(tool, "title", None) or _title_from_tool_name(tool_name)
        )
        updated += 1

    if updated:
        logger.info("autostop_manager.memory_tools annotations updated: %s", updated)


_OAUTH_CONSENT_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
}


def _oauth_consent_page(
    request_id: str,
    *,
    client_name: str,
    scopes: list[str],
    error: str = "",
) -> str:
    safe_request_id = html.escape(str(request_id or ""), quote=True)
    safe_client_name = html.escape(str(client_name or "Codex / ChatGPT")[:120])
    safe_scopes = html.escape(", ".join(scopes))
    error_block = (
        '<p class="error" role="alert">Не удалось подтвердить вход администратора.</p>'
        if error
        else ""
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Подключение AutoStop CRM</title>
  <style>
    body {{ font: 16px/1.45 system-ui, sans-serif; margin: 0; background: #f4f6f8; color: #17202a; }}
    main {{ max-width: 440px; margin: 8vh auto; background: white; padding: 28px; border-radius: 14px; box-shadow: 0 12px 40px #18223022; }}
    h1 {{ font-size: 24px; margin: 0 0 10px; }}
    p {{ color: #52606d; }}
    label {{ display: block; margin: 16px 0 6px; font-weight: 650; }}
    input {{ box-sizing: border-box; width: 100%; padding: 11px 12px; border: 1px solid #b7c2cc; border-radius: 8px; font: inherit; }}
    button {{ width: 100%; margin-top: 22px; padding: 12px; border: 0; border-radius: 8px; background: #1261a0; color: white; font: inherit; font-weight: 700; cursor: pointer; }}
    .meta {{ font-size: 14px; }} .error {{ color: #b42318; font-weight: 650; }}
  </style>
</head>
<body><main>
  <h1>Подключить AutoStop CRM</h1>
  <p>Клиент <strong>{safe_client_name}</strong> запрашивает доступ к текущей CRM.</p>
  <p class="meta">Разрешения: {safe_scopes}</p>
  {error_block}
  <form method="post" action="{OAUTH_CONSENT_PATH}" autocomplete="on">
    <input type="hidden" name="request_id" value="{safe_request_id}">
    <label for="username">Логин администратора CRM</label>
    <input id="username" name="username" type="text" autocomplete="username" required maxlength="120">
    <label for="password">Пароль</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required maxlength="512">
    <button type="submit">Подтвердить подключение</button>
  </form>
</main></body></html>"""


def _nested_oauth_value(value: object, key: str, *, depth: int = 0) -> object:
    if depth > 5:
        return None
    if isinstance(value, dict):
        candidate = value.get(key)
        if candidate not in (None, "", [], {}):
            return candidate
        for item in value.values():
            found = _nested_oauth_value(item, key, depth=depth + 1)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(value, list):
        for item in value[:25]:
            found = _nested_oauth_value(item, key, depth=depth + 1)
            if found not in (None, "", [], {}):
                return found
    return None


def _authenticate_oauth_owner(
    board_api: BoardApiClient, username: str, password: str
) -> str | None:
    response = board_api._request(
        "/api/login_operator",
        {
            "username": username,
            "password": password,
            "source": "mcp_oauth_consent",
        },
        method="POST",
    )
    if not bool(response.get("ok")):
        return None
    data = response.get("data")
    session_token = str(_nested_oauth_value(data, "token") or "").strip()
    resolved_username = str(_nested_oauth_value(data, "username") or username).strip()
    is_admin = _nested_oauth_value(data, "is_admin") is True
    try:
        if session_token:
            board_api._request(
                "/api/logout_operator",
                {"source": "mcp_oauth_consent"},
                method="POST",
                extra_headers={"X-Operator-Session": session_token},
            )
    except BoardApiTransportError:
        pass
    return resolved_username if is_admin and session_token else None


def _truthy_environment(name: str, *, default: bool = False) -> bool:
    raw = str(os.environ.get(name) or "").strip().casefold()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def create_mcp_server(
    board_api: BoardApiClient,
    logger: Logger,
    *,
    host: str | None = None,
    port: int | None = None,
    path: str | None = None,
    bearer_token: str | None = None,
    public_base_url: str | None = None,
    tunnel_url: str | None = None,
    public_endpoint_url: str | None = None,
    allowed_hosts: list[str] | tuple[str, ...] | None = None,
    allowed_origins: list[str] | tuple[str, ...] | None = None,
    oauth_state_file: Path | None = None,
) -> FastMCP:
    resolved_host = host or get_mcp_host()
    resolved_port = port or get_mcp_port()
    resolved_path = path or get_mcp_path()
    resolved_token = bearer_token if bearer_token is not None else get_mcp_bearer_token()
    raw_resource_url = (public_endpoint_url or "").strip().rstrip("/")
    resource_url = _absolute_http_url(raw_resource_url) or ""
    server_base_url = (
        _absolute_http_url(public_base_url)
        or _base_url_from_endpoint(resource_url)
        or _absolute_http_url(get_mcp_public_base_url())
        or f"http://{resolved_host}:{resolved_port}"
    ).rstrip("/")
    effective_resource_url = resource_url or f"{server_base_url}{resolved_path}"
    gateway_policy = load_agent_gateway_security_policy()
    oauth_enabled = _truthy_environment(
        "AUTOSTOP_MCP_OAUTH_ENABLED", default=not gateway_policy.production
    )
    connector_name_url = (
        raw_resource_url if raw_resource_url and not resource_url else effective_resource_url
    )
    connector_name = _connector_name_from_url(connector_name_url)
    connector_identity = {
        "connector_name": connector_name,
        "product_name": "AutoStop CRM",
        "board_name": "Current AutoStop CRM Board",
        "board_scope": "single_local_board_instance",
        "board_key": "autostopcrm/current-board",
        "scope_rule": _single_board_rule_text(),
        "resource_url": effective_resource_url,
        "server_base_url": server_base_url,
        "streamable_http_path": resolved_path,
        "local_bind": f"http://{resolved_host}:{resolved_port}{resolved_path}",
        "board_api_base_url": board_api.base_url,
        "auth_mode": (
            "oauth_2_1_pkce"
            if resolved_token and oauth_enabled
            else "bearer_only"
            if resolved_token
            else "none"
        ),
        "host": resolved_host,
        "port": resolved_port,
    }
    preferred_bootstrap_tools = [
        "bootstrap_context",
        "get_connector_identity",
        "get_board_context",
        "review_board",
        "get_board_content",
        "get_board_events",
        "get_gpt_wall",
    ]
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=derive_allowed_hosts(
            f"http://{resolved_host}:{resolved_port}{resolved_path}",
            public_base_url,
            tunnel_url,
            public_endpoint_url,
            extra_hosts=allowed_hosts,
        ),
        allowed_origins=derive_allowed_origins(
            f"http://{resolved_host}:{resolved_port}{resolved_path}",
            public_base_url,
            tunnel_url,
            public_endpoint_url,
            extra_origins=allowed_origins,
        ),
    )

    auth_settings = None
    auth_server_provider = None
    token_verifier = None
    if resolved_token:
        auth_settings = build_auth_settings(
            server_base_url,
            path=resolved_path,
            resource_url=effective_resource_url,
            oauth_enabled=oauth_enabled,
        )
        if oauth_enabled:
            auth_server_provider = ProductionOAuthAuthorizationServerProvider(
                issuer_url=server_base_url,
                resource_url=effective_resource_url,
                legacy_bearer_token=resolved_token,
                state_file=oauth_state_file,
                logger=logger,
            )
        else:
            token_verifier = StaticBearerTokenVerifier(
                resolved_token,
                resource_url=effective_resource_url,
                client_id=gateway_policy.service_identity,
            )

    server = FastMCP(
        name=connector_name,
        instructions=(
            _external_product_text(
                f"AutoStop CRM MCP connector for exactly one board instance at {effective_resource_url}. "
            )
            + " "
            f"{_single_board_rule_text()} "
            "Use one protocol: agent_bootstrap, then agent_board_digest, then agent_search or "
            "agent_entity_context, then prepare_action_contract, then a named workflow in dry_run "
            "and apply modes, followed by an exact target reread. Use raw discovery only when no "
            "named workflow covers the request. If auth or runtime state is unclear, call "
            "get_runtime_status. "
            "If the user asks about some other kanban product or board, do not use this connector."
        ),
        host=resolved_host,
        port=resolved_port,
        streamable_http_path=resolved_path,
        # Prefer direct JSON responses for request/response flows. The MCP client
        # still keeps the standalone GET stream for notifications, but avoiding
        # per-request SSE streams reduces transport overhead and sidesteps noisy
        # cleanup issues in the upstream SSE response path.
        json_response=True,
        stateless_http=True,
        auth=auth_settings,
        auth_server_provider=auth_server_provider,
        token_verifier=token_verifier,
        transport_security=transport_security,
        log_level="WARNING",
    )
    if isinstance(auth_server_provider, ProductionOAuthAuthorizationServerProvider):
        failed_attempts: dict[str, list[float]] = {}
        failed_attempts_lock = threading.Lock()

        @server.custom_route(OAUTH_CONSENT_PATH, methods=["GET", "POST"])
        async def oauth_consent(request: Request) -> Response:
            request_id = str(request.query_params.get("request_id") or "").strip()
            form: dict[str, list[str]] = {}
            if request.method == "POST":
                content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
                if content_type != "application/x-www-form-urlencoded":
                    return HTMLResponse(
                        "Unsupported request.", status_code=415, headers=_OAUTH_CONSENT_HEADERS
                    )
                body = await request.body()
                if len(body) > 8192:
                    return HTMLResponse(
                        "Request is too large.", status_code=413, headers=_OAUTH_CONSENT_HEADERS
                    )
                form = parse_qs(body.decode("utf-8", errors="strict"), keep_blank_values=True)
                request_id = str((form.get("request_id") or [""])[0]).strip()

            pending = auth_server_provider.get_pending_authorization(request_id)
            if pending is None:
                return HTMLResponse(
                    "Authorization request is missing or expired.",
                    status_code=400,
                    headers=_OAUTH_CONSENT_HEADERS,
                )
            client_name = str(pending.get("client_name") or "Codex / ChatGPT")
            scopes = [str(item) for item in pending.get("scopes") or []]
            if request.method == "GET":
                return HTMLResponse(
                    _oauth_consent_page(request_id, client_name=client_name, scopes=scopes),
                    headers=_OAUTH_CONSENT_HEADERS,
                )

            client_host = str(request.client.host if request.client else "unknown")
            now = time.monotonic()
            with failed_attempts_lock:
                recent = [item for item in failed_attempts.get(client_host, []) if now - item < 300]
                failed_attempts[client_host] = recent
                rate_limited = len(recent) >= 5
            if rate_limited:
                return HTMLResponse(
                    "Too many failed attempts. Try again later.",
                    status_code=429,
                    headers={**_OAUTH_CONSENT_HEADERS, "Retry-After": "300"},
                )

            username = str((form.get("username") or [""])[0]).strip()
            password = str((form.get("password") or [""])[0])
            owner = None
            try:
                owner = _authenticate_oauth_owner(board_api, username, password)
            except (BoardApiTransportError, UnicodeError, ValueError):
                owner = None
            if owner is None:
                with failed_attempts_lock:
                    failed_attempts.setdefault(client_host, []).append(now)
                return HTMLResponse(
                    _oauth_consent_page(
                        request_id,
                        client_name=client_name,
                        scopes=scopes,
                        error="invalid_credentials",
                    ),
                    status_code=401,
                    headers=_OAUTH_CONSENT_HEADERS,
                )
            with failed_attempts_lock:
                failed_attempts.pop(client_host, None)
            try:
                callback_url = auth_server_provider.approve_authorization(request_id, subject=owner)
            except AuthorizeError:
                return HTMLResponse(
                    "Authorization request is missing or expired.",
                    status_code=400,
                    headers=_OAUTH_CONSENT_HEADERS,
                )
            return RedirectResponse(
                callback_url,
                status_code=302,
                headers=_OAUTH_CONSENT_HEADERS,
            )

    logger.info(
        "mcp.transport_security hosts=%s origins=%s",
        transport_security.allowed_hosts,
        transport_security.allowed_origins,
    )
    _try_register_autostop_manager_tools(server, logger)
    _annotate_autostop_manager_tools(server, logger)
    logger.info(
        "mcp.tool_registry groups=%s public_tools=%s",
        ",".join(sorted(MCP_TOOL_GROUPS)),
        len(PUBLIC_MCP_TOOL_NAMES),
    )

    def _relay(tool_name: str, response: dict) -> JsonEnvelope:
        response = _attach_response_meta(tool_name, response)
        logger.info(
            "mcp_tool tool=%s ok=%s connector=%s resource_url=%s",
            tool_name,
            response.get("ok"),
            connector_identity["connector_name"],
            connector_identity["resource_url"],
        )
        return JsonEnvelope.model_validate(response)

    def _payload_bytes_estimate(response: dict[str, Any]) -> int:
        try:
            return len(
                json.dumps(
                    response,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                    allow_nan=False,
                ).encode("utf-8")
            )
        except (OverflowError, TypeError, ValueError):
            return 0

    def _dict_or_empty(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    def _normalize_board_response(
        response: Any,
        *,
        error_code: str = "board_api_malformed_response",
    ) -> dict[str, Any]:
        if not isinstance(response, dict):
            return {
                "ok": False,
                "data": None,
                "error": {
                    "code": error_code,
                    "message": "Board API returned a malformed response envelope.",
                    "response_type": type(response).__name__,
                },
            }
        payload = dict(response)
        payload["ok"] = bool(payload.get("ok", False))
        data = payload.get("data")
        if data is not None and not isinstance(data, dict):
            return {
                "ok": False,
                "data": None,
                "error": {
                    "code": error_code,
                    "message": "Board API returned non-object response data.",
                    "data_type": type(data).__name__,
                },
                "meta": _dict_or_empty(payload.get("meta")),
            }
        error = payload.get("error")
        if error is not None and not isinstance(error, dict):
            payload["error"] = {
                "code": "board_api_error",
                "message": str(error),
            }
        elif error is None and not payload["ok"]:
            payload["error"] = {
                "code": "board_api_error",
                "message": "Board API request failed without a structured error.",
            }
        if "meta" in payload:
            payload["meta"] = _dict_or_empty(payload.get("meta"))
        return payload

    def _malformed_board_response(
        tool_name: str,
        started_at: float,
        *,
        exc: Exception,
        applied_params: dict[str, Any] | None = None,
    ) -> JsonEnvelope:
        return _relay_error(
            tool_name,
            {
                "code": "board_api_malformed_response",
                "message": "Board API returned a response this MCP tool could not normalize.",
                "details": type(exc).__name__,
            },
            meta=_timed_meta(
                tool_name,
                started_at,
                meta={"applied_params": applied_params} if applied_params else None,
            ),
        )

    def _normalize_limit(
        value: Any,
        *,
        default: int,
        minimum: int = 1,
        maximum: int | None = None,
    ) -> int:
        if isinstance(value, bool):
            value = default
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            numeric = float(default)
        if not math.isfinite(numeric) or not numeric.is_integer():
            numeric = float(default)
        if numeric < minimum:
            return minimum
        if maximum is not None and numeric > maximum:
            return maximum
        normalized = int(numeric)
        return normalized

    def _attach_response_meta(tool_name: str, response: dict) -> dict:
        payload = dict(response)
        meta = _dict_or_empty(payload.get("meta"))
        meta.setdefault("tool", tool_name)
        meta.setdefault("response_mode", "default")
        payload["meta"] = meta
        meta.setdefault("payload_bytes_estimate", _payload_bytes_estimate(payload))
        return payload

    def _timed_meta(
        tool_name: str,
        started_at: float,
        *,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(meta or {})
        elapsed_ms = round(max(perf_counter() - started_at, 0.0) * 1000, 3)
        payload.setdefault("tool", tool_name)
        payload.setdefault("request_id", uuid4().hex)
        payload.setdefault("timestamp", datetime.now(UTC).isoformat())
        payload.setdefault("latency_ms", elapsed_ms)
        payload.setdefault("duration_ms", elapsed_ms)
        payload.setdefault("schema_version", CONNECTOR_SCHEMA_VERSION)
        payload.setdefault("connector_version", CONNECTOR_VERSION)
        payload.setdefault("canonical_tool_path", _canonical_tool_path(tool_name))
        payload.setdefault(
            "normalized_canonical_tool_path",
            _normalize_tool_path_alias(_canonical_tool_path(tool_name)),
        )
        payload.setdefault(
            "path_alias_rule", "/AutoStopCRM/link_<alias>/<tool> -> /AutoStopCRM/<tool>"
        )
        return payload

    def _relay_data(
        tool_name: str,
        data: dict[str, Any],
        *,
        meta: dict[str, Any] | None = None,
    ) -> JsonEnvelope:
        response: dict[str, Any] = {
            "ok": True,
            "data": data,
            "error": None,
        }
        if meta is not None:
            response["meta"] = meta
        return _relay(tool_name, response)

    def _relay_error(
        tool_name: str,
        error: dict[str, Any],
        *,
        data: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> JsonEnvelope:
        response: dict[str, Any] = {
            "ok": False,
            "data": data,
            "error": error,
        }
        if meta is not None:
            response["meta"] = meta
        return _relay(tool_name, response)

    def _relay_identity_data(
        data: dict[str, Any],
        *,
        meta: dict[str, Any] | None = None,
    ) -> ConnectorIdentityEnvelope:
        response: dict[str, Any] = {
            "ok": True,
            "data": data,
            "error": None,
        }
        if meta is not None:
            response["meta"] = meta
        response = _attach_response_meta("get_connector_identity", response)
        logger.info(
            "mcp_tool tool=%s ok=%s connector=%s resource_url=%s",
            "get_connector_identity",
            True,
            connector_identity["connector_name"],
            connector_identity["resource_url"],
        )
        return ConnectorIdentityEnvelope.model_validate(response)

    def _relay_board_call(
        tool_name: str,
        fetcher: Callable[[], dict[str, Any]],
        *,
        error_code: str = "board_api_unreachable",
        params: dict[str, Any] | None = None,
        transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> JsonEnvelope:
        started_at = perf_counter()
        applied_params = {key: value for key, value in (params or {}).items() if value is not None}
        try:
            response = _normalize_board_response(fetcher())
        except BoardApiTransportError as exc:
            return _relay_error(
                tool_name,
                {
                    "code": error_code,
                    "message": str(exc),
                },
                meta=_timed_meta(
                    tool_name,
                    started_at,
                    meta={"applied_params": applied_params} if applied_params else None,
                ),
            )
        except (OverflowError, TypeError, ValueError) as exc:
            return _malformed_board_response(
                tool_name,
                started_at,
                exc=exc,
                applied_params=applied_params,
            )
        if transform is not None:
            try:
                response = _normalize_board_response(transform(response))
            except (AttributeError, KeyError, OverflowError, TypeError, ValueError) as exc:
                return _malformed_board_response(
                    tool_name,
                    started_at,
                    exc=exc,
                    applied_params=applied_params,
                )
        response_meta = _dict_or_empty(response.get("meta"))
        data_payload = response.get("data") if isinstance(response.get("data"), dict) else {}
        data_meta = data_payload.get("meta") if isinstance(data_payload, dict) else {}
        if isinstance(data_meta, dict):
            for key in (
                "response_mode",
                "view_mode",
                "compact",
                "include_archived",
                "event_limit",
                "limit",
            ):
                if key in data_meta:
                    response_meta.setdefault(key, data_meta[key])
        response["meta"] = _timed_meta(tool_name, started_at, meta=response_meta)
        if applied_params:
            response["meta"].setdefault("applied_params", applied_params)
        return _relay(tool_name, response)

    def _with_data_meta(
        response: dict[str, Any],
        **fields: Any,
    ) -> dict[str, Any]:
        if not response.get("ok") or not isinstance(response.get("data"), dict):
            return response
        data = dict(response["data"])
        meta = _dict_or_empty(data.get("meta"))
        meta.setdefault("schema_version", CONNECTOR_SCHEMA_VERSION)
        for key, value in fields.items():
            if value is not None:
                meta[key] = value
        data["meta"] = meta
        data.setdefault("schema_version", CONNECTOR_SCHEMA_VERSION)
        return {**response, "data": data}

    def _with_cards_list_meta(
        response: dict[str, Any],
        *,
        include_archived: bool,
        compact: bool,
        response_mode: str,
    ) -> dict[str, Any]:
        if not response.get("ok") or not isinstance(response.get("data"), dict):
            return response
        data = dict(response["data"])
        cards = data.get("cards") if isinstance(data.get("cards"), list) else []
        source_meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        return _with_data_meta(
            {**response, "data": data},
            response_mode=response_mode,
            view_mode="compact" if compact else "full",
            include_archived=include_archived,
            compact=compact,
            returned=len(cards),
            has_more=bool(source_meta.get("has_more", False)),
        )

    def _with_text_section_meta(
        response: dict[str, Any],
        *,
        response_mode: str,
        view_mode: str,
        text_key: str = "text",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not response.get("ok") or not isinstance(response.get("data"), dict):
            return response
        data = dict(response["data"])
        payload = {
            "response_mode": response_mode,
            "view_mode": view_mode,
        }
        if text_key in data:
            payload["text_encoding"] = "utf-8"
            payload["text_present"] = bool(str(data.get(text_key) or "").strip())
        if extra:
            payload.update({key: value for key, value in extra.items() if value is not None})
        return _with_data_meta({**response, "data": data}, **payload)

    def _with_connector_identity(response: dict[str, Any]) -> dict[str, Any]:
        if not response.get("ok") or not isinstance(response.get("data"), dict):
            return response
        data = dict(response["data"])
        data["connector_identity"] = dict(connector_identity)
        return {**response, "data": data}

    def _identity_text() -> str:
        return (
            "[CONNECTOR IDENTITY]\n"
            f"connector_name: {connector_identity['connector_name']}\n"
            f"product_name: {connector_identity['product_name']}\n"
            f"board_name: {connector_identity['board_name']}\n"
            f"board_key: {connector_identity['board_key']}\n"
            f"board_scope: {connector_identity['board_scope']}\n"
            f"resource_url: {connector_identity['resource_url']}\n"
            f"server_base_url: {connector_identity['server_base_url']}\n"
            f"streamable_http_path: {connector_identity['streamable_http_path']}\n"
            f"board_api_base_url: {connector_identity['board_api_base_url']}\n"
            f"auth_mode: {connector_identity['auth_mode']}\n"
            f"scope_rule: {connector_identity['scope_rule']}\n"
            "operation_rule: before any write, call bootstrap_context first; if needed, then get_runtime_status\n"
        )

    def _transport_error_response(error_code: str, exc: BoardApiTransportError) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": error_code,
                "message": str(exc),
            },
        }

    def _safe_board_call(
        fetcher: Callable[[], dict[str, Any]], *, error_code: str
    ) -> dict[str, Any]:
        try:
            return _normalize_board_response(fetcher())
        except BoardApiTransportError as exc:
            return _transport_error_response(error_code, exc)
        except (OverflowError, TypeError, ValueError) as exc:
            return {
                "ok": False,
                "error": {
                    "code": "board_api_malformed_response",
                    "message": "Board API returned a malformed response envelope.",
                    "details": type(exc).__name__,
                },
            }

    def _safe_health() -> dict[str, Any]:
        return _safe_board_call(board_api.health, error_code="board_api_unreachable")

    def _safe_board_context() -> dict[str, Any]:
        return _safe_board_call(board_api.get_board_context, error_code="board_context_unreachable")

    def _safe_gpt_wall(
        *,
        include_archived: bool,
        event_limit: int,
        compact: bool,
    ) -> dict[str, Any]:
        return _safe_board_call(
            lambda: board_api.get_gpt_wall(
                include_archived=include_archived,
                event_limit=event_limit,
                compact=compact,
            ),
            error_code="gpt_wall_unreachable",
        )

    def _runtime_status_payload() -> dict[str, Any]:
        health_response = _safe_health()
        board_context_response = _safe_board_context()
        board_context_payload = (
            board_context_response.get("data") if board_context_response.get("ok") else None
        )
        board_context_data = (
            board_context_payload if isinstance(board_context_payload, dict) else {}
        )
        context = (
            board_context_data.get("context")
            if isinstance(board_context_data.get("context"), dict)
            else {}
        )
        runtime_status = {
            "connector_identity": dict(connector_identity),
            "preferred_bootstrap_tools": list(preferred_bootstrap_tools),
            "api_health": health_response.get("data") if health_response.get("ok") else None,
            "api_health_error": health_response.get("error")
            if not health_response.get("ok")
            else None,
            "board_context": board_context_payload,
            "board_context_summary": {
                "board_name": context.get("board_name", connector_identity["board_name"]),
                "columns_total": context.get("columns_total", 0),
                "active_cards_total": context.get("active_cards_total", 0),
                "archived_cards_total": context.get("archived_cards_total", 0),
                "stickies_total": context.get("stickies_total", 0),
            },
            "board_context_available_via": "get_board_context",
            "board_context_error": board_context_response.get("error")
            if not board_context_response.get("ok")
            else None,
            "resource_visibility": "public_https"
            if connector_identity["resource_url"].startswith("https://")
            else "local_only",
            "resource_configured": bool(connector_identity["resource_url"]),
        }
        return runtime_status

    def _runtime_status_text(runtime_status: dict[str, Any]) -> str:
        api_health = runtime_status.get("api_health") or {}
        board_context = runtime_status.get("board_context") or {}
        context = board_context.get("context") or {}
        lines = [
            "[RUNTIME STATUS]",
            f"connector_name: {connector_identity['connector_name']}",
            f"resource_url: {connector_identity['resource_url']}",
            f"board_api_base_url: {connector_identity['board_api_base_url']}",
            f"auth_mode: {connector_identity['auth_mode']}",
            f"resource_visibility: {runtime_status['resource_visibility']}",
        ]
        if api_health:
            lines.extend(
                [
                    f"api_status: {api_health.get('status', 'unknown')}",
                    f"api_base_url: {api_health.get('base_url', connector_identity['board_api_base_url'])}",
                    f"api_bind_host: {api_health.get('bind_host', connector_identity['host'])}",
                    f"api_auth_required: {api_health.get('auth_required', False)}",
                ]
            )
        else:
            error = runtime_status.get("api_health_error") or {}
            lines.append(f"api_error: {error.get('message', 'unknown')}")
        if context:
            lines.extend(
                [
                    f"board_name: {context.get('board_name', connector_identity['board_name'])}",
                    f"columns_total: {context.get('columns_total', 0)}",
                    f"active_cards_total: {context.get('active_cards_total', 0)}",
                    f"archived_cards_total: {context.get('archived_cards_total', 0)}",
                    f"stickies_total: {context.get('stickies_total', 0)}",
                ]
            )
        else:
            error = runtime_status.get("board_context_error") or {}
            lines.append(f"board_context_error: {error.get('message', 'unknown')}")
        lines.append("full_board_context_tool: get_board_context")
        lines.append("recommended_bootstrap: bootstrap_context -> get_runtime_status -> writes")
        return "\n".join(lines) + "\n"

    def _enrich_gpt_wall_response(response: Any) -> dict[str, Any]:
        if not isinstance(response, dict):
            return _normalize_board_response(response)
        if not response.get("ok") or not isinstance(response.get("data"), dict):
            return response
        data = dict(response["data"])
        board_context_payload = data.get("board_context")
        if not isinstance(board_context_payload, dict):
            context_response = _safe_board_context()
            if context_response.get("ok") and isinstance(context_response.get("data"), dict):
                board_context_payload = dict(context_response["data"])
        if isinstance(board_context_payload, dict):
            data["board_context"] = board_context_payload
        data["connector_identity"] = dict(connector_identity)
        wall_text = str(data.get("text") or "").strip()
        board_context_text = ""
        if isinstance(data.get("board_context"), dict):
            board_context_text = str(data["board_context"].get("text") or "").strip()
        sections = [_identity_text()]
        if board_context_text:
            sections.append(board_context_text)
        if wall_text:
            sections.append(wall_text)
        data["text"] = "\n\n".join(section for section in sections if section)
        return {**response, "data": data}

    def _bootstrap_wall_preview(wall_data: dict[str, Any]) -> dict[str, Any]:
        cards = wall_data.get("cards") if isinstance(wall_data.get("cards"), list) else []
        events = wall_data.get("events") if isinstance(wall_data.get("events"), list) else []
        stickies = wall_data.get("stickies") if isinstance(wall_data.get("stickies"), list) else []
        cards_preview_limit = 8
        events_preview_limit = 12
        stickies_preview_limit = 5
        preview_cards: list[dict[str, Any]] = []
        attention_cards: list[dict[str, Any]] = []
        for card in cards[:cards_preview_limit]:
            if not isinstance(card, dict):
                continue
            preview = {
                "id": card.get("id"),
                "short_id": card.get("short_id"),
                "vehicle": card.get("vehicle"),
                "title": card.get("title"),
                "column": card.get("column"),
                "column_label": card.get("column_label"),
                "status": card.get("status"),
                "indicator": card.get("indicator"),
                "tags": card.get("tags"),
            }
            preview_cards.append(preview)
            if card.get("status") in {"warning", "critical", "expired"} or card.get(
                "indicator"
            ) in {"yellow", "red"}:
                attention_cards.append(preview)

        preview_events: list[dict[str, Any]] = []
        for event in events[:events_preview_limit]:
            if not isinstance(event, dict):
                continue
            preview_events.append(
                {
                    "timestamp": event.get("timestamp"),
                    "actor_name": event.get("actor_name"),
                    "message": event.get("message"),
                    "card_short_id": event.get("card_short_id"),
                }
            )

        preview_stickies: list[dict[str, Any]] = []
        for sticky in stickies[:stickies_preview_limit]:
            if not isinstance(sticky, dict):
                continue
            preview_stickies.append(
                {
                    "id": sticky.get("id"),
                    "short_id": sticky.get("short_id"),
                    "text": sticky.get("text"),
                }
            )

        return {
            "meta": dict(wall_data.get("meta") or {}),
            "cards_preview": preview_cards,
            "cards_preview_total": len(cards),
            "cards_preview_limit": cards_preview_limit,
            "cards_preview_truncated": len(cards) > cards_preview_limit,
            "attention_cards": attention_cards[:5],
            "events_preview": preview_events,
            "events_preview_total": len(events),
            "events_preview_limit": events_preview_limit,
            "events_preview_truncated": len(events) > events_preview_limit,
            "stickies_preview": preview_stickies,
            "stickies_preview_total": len(stickies),
            "stickies_preview_limit": stickies_preview_limit,
            "stickies_preview_truncated": len(stickies) > stickies_preview_limit,
            "review_tool": "review_board",
            "board_content_tool": "get_board_content",
            "event_log_tool": "get_board_events",
            "full_wall_tool": "get_gpt_wall",
        }

    def _bootstrap_context_text(
        *,
        board_context_payload: dict[str, Any] | None,
        wall_preview: dict[str, Any],
    ) -> str:
        context = {}
        if isinstance(board_context_payload, dict):
            context = dict(board_context_payload.get("context") or {})
        lines = [
            "[BOOTSTRAP CONTEXT]",
            f"connector_name: {connector_identity['connector_name']}",
            f"board_name: {context.get('board_name', connector_identity['board_name'])}",
            f"board_scope: {context.get('board_scope', connector_identity['board_scope'])}",
            f"resource_url: {connector_identity['resource_url']}",
            f"scope_rule: {connector_identity['scope_rule']}",
            f"columns_total: {context.get('columns_total', 0)}",
            f"active_cards_total: {context.get('active_cards_total', 0)}",
            f"archived_cards_total: {context.get('archived_cards_total', 0)}",
            f"stickies_total: {context.get('stickies_total', 0)}",
        ]
        columns = context.get("columns") if isinstance(context.get("columns"), list) else []
        if columns:
            rendered_columns = ", ".join(
                str(item.get("label") or item.get("id") or "").strip()
                for item in columns
                if isinstance(item, dict)
            )
            lines.append(f"columns: {rendered_columns}")
        attention_cards = (
            wall_preview.get("attention_cards")
            if isinstance(wall_preview.get("attention_cards"), list)
            else []
        )
        if attention_cards:
            lines.append("attention_cards:")
            for card in attention_cards[:5]:
                lines.append(
                    f"- {card.get('short_id') or card.get('id')}: {card.get('vehicle') or '-'} / {card.get('title') or '-'} | {card.get('column_label') or card.get('column') or '-'} | {card.get('status') or '-'} | {card.get('indicator') or '-'}"
                )
        preview_events = (
            wall_preview.get("events_preview")
            if isinstance(wall_preview.get("events_preview"), list)
            else []
        )
        if preview_events:
            lines.append("recent_events:")
            for event in preview_events[:8]:
                lines.append(
                    f"- {event.get('timestamp') or '-'} | {event.get('actor_name') or '-'} | {event.get('card_short_id') or '-'} | {event.get('message') or '-'}"
                )
        lines.append(
            "next_step: call review_board or get_cards(compact=true) for fast triage, get_board_content(view_mode=agent) for board context, get_board_events(event_limit=100) for the latest Markdown journal, or get_gpt_wall(view_mode=full) only when a heavy full wall export is needed"
        )
        return "\n".join(lines) + "\n"

    @server.tool(
        name="get_connector_identity",
        description=_scoped_description(
            "Return the hard identity of this MCP connector: name, resource_url, auth mode, and the rule that it manages only the current AutoStop CRM board."
        ),
        annotations=_read_tool_annotations("Connector Identity"),
        structured_output=True,
    )
    def get_connector_identity() -> ConnectorIdentityEnvelope:
        started_at = perf_counter()
        return _relay_identity_data(
            {
                "identity": dict(connector_identity),
                "text": _identity_text(),
            },
            meta=_timed_meta(
                "get_connector_identity",
                started_at,
                meta={"response_mode": "identity"},
            ),
        )

    @server.tool(
        name="ping_connector",
        description=_scoped_description(
            "Return the lightest possible connector ping. Use this first when you need to verify that ChatGPT can execute any AutoStop CRM MCP tool at all."
        ),
        annotations=_read_tool_annotations("Connector Ping"),
        structured_output=True,
    )
    def ping_connector() -> JsonEnvelope:
        started_at = perf_counter()
        return _relay_data(
            "ping_connector",
            {
                "connector_name": connector_identity["connector_name"],
                "resource_url": connector_identity["resource_url"],
                "board_scope": connector_identity["board_scope"],
                "message": "pong",
                "schema_version": CONNECTOR_SCHEMA_VERSION,
                "text": (
                    "[CONNECTOR PING]\n"
                    f"connector_name: {connector_identity['connector_name']}\n"
                    f"resource_url: {connector_identity['resource_url']}\n"
                    f"canonical_tool_path: {_canonical_tool_path('ping_connector')}\n"
                    "message: pong\n"
                ),
            },
            meta=_timed_meta("ping_connector", started_at, meta={"response_mode": "ping"}),
        )

    @server.tool(
        name="bootstrap_context",
        description=_scoped_description(
            "Return the lightweight startup bundle for GPT: connector identity, board context, a compact wall preview, and the write flow for this board."
        ),
        annotations=_read_tool_annotations("Bootstrap Context"),
        structured_output=True,
    )
    def bootstrap_context(
        include_archived: bool = False,
        event_limit: McpInt = 50,
        compact: bool = True,
    ) -> JsonEnvelope:
        started_at = perf_counter()
        effective_event_limit = (
            _normalize_limit(event_limit, default=50, maximum=GPT_WALL_AGENT_EVENT_LIMIT)
            if compact
            else _normalize_limit(event_limit, default=50, maximum=5000)
        )
        wall_response = _safe_gpt_wall(
            include_archived=include_archived,
            event_limit=effective_event_limit,
            compact=compact,
        )
        board_context_response = _safe_board_context()
        if not wall_response.get("ok"):
            error = dict(wall_response.get("error") or {})
            error.setdefault("code", "bootstrap_failed")
            error.setdefault("message", "bootstrap_context failed while reading the GPT wall.")
            return _relay_error(
                "bootstrap_context",
                error,
                data={
                    "identity": dict(connector_identity),
                    "preferred_bootstrap_tools": list(preferred_bootstrap_tools),
                },
                meta=_timed_meta(
                    "bootstrap_context",
                    started_at,
                    meta={
                        "applied_params": {
                            "include_archived": include_archived,
                            "event_limit": effective_event_limit,
                            "compact": compact,
                        },
                        "response_mode": "compact_bootstrap" if compact else "summary_bootstrap",
                    },
                ),
            )

        board_context_payload = None
        if board_context_response.get("ok") and isinstance(
            board_context_response.get("data"), dict
        ):
            board_context_payload = dict(board_context_response["data"])
        elif isinstance(wall_response.get("data"), dict) and isinstance(
            wall_response["data"].get("board_context"), dict
        ):
            board_context_payload = dict(wall_response["data"]["board_context"])

        wall_data = dict(wall_response.get("data") or {})
        if board_context_payload is not None:
            wall_data["board_context"] = board_context_payload
        wall_data["connector_identity"] = dict(connector_identity)
        wall_preview = _bootstrap_wall_preview(wall_data)
        bootstrap_text = _bootstrap_context_text(
            board_context_payload=board_context_payload,
            wall_preview=wall_preview,
        )

        return _relay_data(
            "bootstrap_context",
            {
                "schema_version": CONNECTOR_SCHEMA_VERSION,
                "identity": dict(connector_identity),
                "board_context": board_context_payload,
                "gpt_wall_preview": wall_preview,
                "preferred_bootstrap_tools": list(preferred_bootstrap_tools),
                "canonical_tool_paths": {
                    tool_name: _canonical_tool_path(tool_name)
                    for tool_name in ("ping_connector", "bootstrap_context", "get_runtime_status")
                },
                "tool_path_policy": {
                    "prefer_canonical_short_path": True,
                    "normalize_link_alias_to_canonical": True,
                    "alias_example": f"/AutoStopCRM/link_alias/bootstrap_context -> {_canonical_tool_path('bootstrap_context')}",
                },
                "recommended_write_flow": [
                    "bootstrap_context",
                    "confirm board_name and scope_rule",
                    "call review_board or get_cards(compact=true) for fast operational triage",
                    "call get_board_content(view_mode=agent) for compact Markdown board state; pass view_mode=full only for heavy exports",
                    "call get_board_events(event_limit=100) for the newest-first Markdown journal of the latest board changes",
                    "call get_gpt_wall(view_mode=full) only when both hidden machine wall sections are needed in a heavy full response",
                    "for mass column migrations prefer bulk_move_cards over many sequential move_card calls",
                    "perform write tools by card_id, sticky_id, and column id only",
                ],
                "text": bootstrap_text,
            },
            meta=_timed_meta(
                "bootstrap_context",
                started_at,
                meta={
                    "applied_params": {
                        "include_archived": include_archived,
                        "event_limit": effective_event_limit,
                        "compact": compact,
                    },
                    "response_mode": "compact_bootstrap" if compact else "summary_bootstrap",
                },
            ),
        )

    @server.tool(
        name="get_runtime_status",
        description=_scoped_description(
            "Return runtime diagnostics for this connector: effective MCP identity, board API health, board counts, and whether the endpoint is publicly reachable in principle."
        ),
        annotations=_read_tool_annotations("Runtime Status"),
        structured_output=True,
    )
    def get_runtime_status() -> JsonEnvelope:
        started_at = perf_counter()
        runtime_status = _runtime_status_payload()
        return _relay_data(
            "get_runtime_status",
            {
                "schema_version": CONNECTOR_SCHEMA_VERSION,
                "runtime_status": runtime_status,
                "canonical_tool_paths": {
                    tool_name: _canonical_tool_path(tool_name)
                    for tool_name in ("ping_connector", "bootstrap_context", "get_runtime_status")
                },
                "full_board_context_tool": "get_board_context",
                "text": _runtime_status_text(runtime_status),
            },
            meta=_timed_meta(
                "get_runtime_status", started_at, meta={"response_mode": "diagnostics"}
            ),
        )

    @server.tool(
        name="list_columns",
        description=_scoped_description("List all columns of the current AutoStop CRM board."),
        annotations=_read_tool_annotations("List Columns"),
        structured_output=True,
    )
    def list_columns() -> JsonEnvelope:
        return _relay_board_call("list_columns", board_api.list_columns)

    @server.tool(
        name="create_column",
        description=_scoped_description("Create a new column on the current AutoStop CRM board."),
        annotations=_write_tool_annotations("Create Column"),
        structured_output=True,
    )
    def create_column(
        label: str | None = None,
        name: str | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "create_column",
            lambda: board_api.create_column(
                label,
                name=name,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="rename_column",
        description=_scoped_description(
            "Rename an existing column on the current AutoStop CRM board while keeping the same column id."
        ),
        annotations=_write_tool_annotations("Rename Column", idempotent=True),
        structured_output=True,
    )
    def rename_column(column_id: str, label: str, actor_name: str | None = None) -> JsonEnvelope:
        return _relay_board_call(
            "rename_column",
            lambda: board_api.rename_column(column_id, label, actor_name=actor_name),
        )

    @server.tool(
        name="delete_column",
        description=_scoped_description(
            "Delete an empty column from the current AutoStop CRM board. The last remaining column cannot be removed."
        ),
        annotations=_write_tool_annotations("Delete Column", destructive=True),
        structured_output=True,
    )
    def delete_column(column_id: str, actor_name: str | None = None) -> JsonEnvelope:
        return _relay_board_call(
            "delete_column",
            lambda: board_api.delete_column(column_id, actor_name=actor_name),
        )

    @server.tool(
        name="create_sticky",
        description=_scoped_description(
            "Create a sticky note on the current AutoStop CRM board. Sticky notes belong only to this board instance. "
            "The deadline accepts either days/hours/minutes/seconds or total_seconds."
        ),
        annotations=_write_tool_annotations("Create Sticky"),
        structured_output=True,
    )
    def create_sticky(
        text: str,
        deadline: StickyDeadlinePayload,
        x: McpInt = 0,
        y: McpInt = 0,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "create_sticky",
            lambda: board_api.create_sticky(
                text=text, x=x, y=y, deadline=deadline.model_dump(), actor_name=actor_name
            ),
        )

    @server.tool(
        name="get_cards",
        description=_scoped_description(
            "Return cards from the current AutoStop CRM board. Archived cards are excluded by default. "
            "Use compact=true for board scans with lighter payloads; set compact=false when full vehicle_profile, repair_order, attachments, and ai_autofill_log are needed."
        ),
        annotations=_read_tool_annotations("List Cards"),
        structured_output=True,
    )
    def get_cards(include_archived: bool = False, compact: bool = True) -> JsonEnvelope:
        return _relay_board_call(
            "get_cards",
            lambda: board_api.get_cards(include_archived=include_archived, compact=compact),
            params={"include_archived": include_archived, "compact": compact},
            transform=lambda response: _with_cards_list_meta(
                response,
                include_archived=include_archived,
                compact=compact,
                response_mode="list",
            ),
        )

    @server.tool(
        name="get_card",
        description=_scoped_description(
            "Return one card by card_id from the current AutoStop CRM board, including the full vehicle_profile and the compact vehicle_profile_compact used by the 1.1 card layout."
        ),
        annotations=_read_tool_annotations("Get Card"),
        structured_output=True,
    )
    def get_card(card_id: str) -> JsonEnvelope:
        return _relay_board_call("get_card", lambda: board_api.get_card(card_id))

    @server.tool(
        name="list_card_attachments",
        description=_scoped_description(
            "List attachment metadata for one card from the current AutoStop CRM board without returning file bytes. Use this before reading any attached file."
        ),
        annotations=_read_tool_annotations("List Card Attachments"),
        structured_output=True,
    )
    def list_card_attachments(card_id: str, include_removed: bool = False) -> JsonEnvelope:
        return _relay_board_call(
            "list_card_attachments",
            lambda: board_api.list_card_attachments(card_id, include_removed=include_removed),
            params={"card_id": card_id, "include_removed": include_removed},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="attachment_list",
                view_mode="metadata",
                include_removed=include_removed,
            ),
        )

    @server.tool(
        name="get_card_attachment",
        description=_scoped_description(
            "Return safe metadata for one card attachment from the current AutoStop CRM board, including content kind, size, hash, and download path, but not file bytes."
        ),
        annotations=_read_tool_annotations("Get Card Attachment"),
        structured_output=True,
    )
    def get_card_attachment(card_id: str, attachment_id: str) -> JsonEnvelope:
        return _relay_board_call(
            "get_card_attachment",
            lambda: board_api.get_card_attachment(card_id, attachment_id),
            params={"card_id": card_id, "attachment_id": attachment_id},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="attachment_metadata",
                view_mode="metadata",
            ),
        )

    @server.tool(
        name="read_card_attachment",
        description=_scoped_description(
            "Read one card attachment for an agent. Text, DOCX, XLSX, and simple PDFs return bounded text; images return dimensions and can include bounded base64/data_url when include_base64=true or mode=base64."
        ),
        annotations=_read_tool_annotations("Read Card Attachment"),
        structured_output=True,
    )
    def read_card_attachment(
        card_id: str,
        attachment_id: str,
        mode: Literal["preview", "text", "base64", "auto"] = "preview",
        max_chars: McpInt = 12_000,
        include_base64: bool = False,
        max_base64_bytes: McpInt = 1_048_576,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "read_card_attachment",
            lambda: board_api.read_card_attachment(
                card_id,
                attachment_id,
                mode=mode,
                max_chars=max_chars,
                include_base64=include_base64,
                max_base64_bytes=max_base64_bytes,
            ),
            params={
                "card_id": card_id,
                "attachment_id": attachment_id,
                "mode": mode,
                "max_chars": max_chars,
                "include_base64": include_base64,
                "max_base64_bytes": max_base64_bytes,
            },
            transform=lambda response: _with_data_meta(
                response,
                response_mode="attachment_read",
                view_mode=mode,
            ),
        )

    @server.tool(
        name="list_shared_files",
        description=_scoped_description(
            "List shared workshop files from the AutoStop CRM Files module without returning file bytes."
        ),
        annotations=_read_tool_annotations("List Shared Files"),
        structured_output=True,
    )
    def list_shared_files() -> JsonEnvelope:
        return _relay_board_call(
            "list_shared_files",
            board_api.list_shared_files,
            transform=lambda response: _with_data_meta(
                response,
                response_mode="shared_file_list",
                view_mode="metadata",
            ),
        )

    @server.tool(
        name="get_shared_file_info",
        description=_scoped_description(
            "Return metadata for one shared workshop file from the AutoStop CRM Files module, including size, name, position, and download path."
        ),
        annotations=_read_tool_annotations("Get Shared File Info"),
        structured_output=True,
    )
    def get_shared_file_info(file_id: str) -> JsonEnvelope:
        return _relay_board_call(
            "get_shared_file_info",
            lambda: board_api.get_shared_file_info(file_id),
            params={"file_id": file_id},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="shared_file_metadata",
                view_mode="metadata",
            ),
        )

    @server.tool(
        name="download_shared_file",
        description=_scoped_description(
            "Fetch one shared workshop file through the AutoStop CRM backend. Small files can return base64; larger files return metadata and download path without file bytes."
        ),
        annotations=_read_tool_annotations("Download Shared File"),
        structured_output=True,
    )
    def download_shared_file(
        file_id: str,
        include_base64: bool = True,
        max_base64_bytes: McpInt = 2_097_152,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "download_shared_file",
            lambda: board_api.download_shared_file(
                file_id,
                include_base64=include_base64,
                max_base64_bytes=max_base64_bytes,
            ),
            params={
                "file_id": file_id,
                "include_base64": include_base64,
                "max_base64_bytes": max_base64_bytes,
            },
            transform=lambda response: _with_data_meta(
                response,
                response_mode="shared_file_download",
                view_mode="base64" if include_base64 else "metadata",
            ),
        )

    @server.tool(
        name="upload_shared_file",
        description=_scoped_description(
            "Upload one file into the AutoStop CRM Files module. Pass file_name and base64 content; executable script/install extensions are rejected by the backend."
        ),
        annotations=_write_tool_annotations("Upload Shared File"),
        structured_output=True,
    )
    def upload_shared_file(
        file_name: str,
        content_base64: str,
        mime_type: str = "application/octet-stream",
        x: McpInt = 0,
        y: McpInt = 0,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "upload_shared_file",
            lambda: board_api.upload_shared_file(
                file_name=file_name,
                content_base64=content_base64,
                mime_type=mime_type,
                x=x,
                y=y,
                actor_name=actor_name,
            ),
            params={"file_name": file_name, "mime_type": mime_type, "x": x, "y": y},
        )

    @server.tool(
        name="delete_shared_file",
        description=_scoped_description(
            "Delete one file from the AutoStop CRM Files module. This is a destructive write action."
        ),
        annotations=_write_tool_annotations("Delete Shared File", destructive=True),
        structured_output=True,
    )
    def delete_shared_file(file_id: str, actor_name: str | None = None) -> JsonEnvelope:
        return _relay_board_call(
            "delete_shared_file",
            lambda: board_api.delete_shared_file(file_id, actor_name=actor_name),
            params={"file_id": file_id},
        )

    @server.tool(
        name="update_shared_file_position",
        description=_scoped_description(
            "Update the saved x/y icon position for one file in the AutoStop CRM Files module."
        ),
        annotations=_write_tool_annotations("Update Shared File Position", idempotent=True),
        structured_output=True,
    )
    def update_shared_file_position(
        file_id: str, x: int, y: int, actor_name: str | None = None
    ) -> JsonEnvelope:
        return _relay_board_call(
            "update_shared_file_position",
            lambda: board_api.update_shared_file_position(
                file_id,
                x=x,
                y=y,
                actor_name=actor_name,
            ),
            params={"file_id": file_id, "x": x, "y": y},
        )

    @server.tool(
        name="get_card_context",
        description=_scoped_description(
            "Return the focused operational context of one card from the current AutoStop CRM board: card data, recent card events, attachment summaries, board context, and repair-order text when available. "
            "Use view_mode=agent for the default GPT workflow and view_mode=full when a human-style full read is needed."
        ),
        annotations=_read_tool_annotations("Card Context"),
        structured_output=True,
    )
    def get_card_context(
        card_id: str,
        event_limit: McpInt = 20,
        include_repair_order_text: bool = True,
        view_mode: Literal["agent", "full"] = "agent",
    ) -> JsonEnvelope:
        effective_event_limit = _normalize_limit(event_limit, default=20, maximum=200)
        return _relay_board_call(
            "get_card_context",
            lambda: board_api.get_card_context(
                card_id,
                event_limit=effective_event_limit,
                include_repair_order_text=include_repair_order_text,
            ),
            params={
                "card_id": card_id,
                "event_limit": effective_event_limit,
                "include_repair_order_text": include_repair_order_text,
                "view_mode": view_mode,
            },
            transform=lambda response: _with_text_section_meta(
                response,
                response_mode="agent_context" if view_mode == "agent" else "full",
                view_mode=view_mode,
                extra={
                    "event_limit": effective_event_limit,
                    "include_repair_order_text": include_repair_order_text,
                },
            ),
        )

    @server.tool(
        name="get_board_snapshot",
        description=_scoped_description(
            "Return a structured snapshot of the current AutoStop CRM board: columns, active cards, archived tail, stickies, and settings. "
            "Cards in the snapshot include vehicle_profile_compact for the 1.1 vehicle card view. "
            "Use compact=true for lighter GPT scans and include_archive=false when the archived tail is not needed."
        ),
        annotations=_read_tool_annotations("Board Snapshot"),
        structured_output=True,
    )
    def get_board_snapshot(
        archive_limit: McpInt = 10,
        compact: bool = False,
        include_archive: bool = True,
    ) -> JsonEnvelope:
        effective_archive_limit = (
            _normalize_limit(archive_limit, default=30, maximum=50) if include_archive else 0
        )
        return _relay_board_call(
            "get_board_snapshot",
            lambda: board_api.get_board_snapshot(
                archive_limit=effective_archive_limit,
                compact=compact,
                include_archive=include_archive,
            ),
            params={
                "archive_limit": effective_archive_limit,
                "compact": compact,
                "include_archive": include_archive,
            },
            transform=lambda response: _with_data_meta(
                response,
                response_mode="snapshot",
                view_mode="compact" if compact else "full",
                archive_limit=effective_archive_limit,
                include_archive=include_archive,
                compact=compact,
            ),
        )

    @server.tool(
        name="get_board_context",
        description=_scoped_description(
            "Return the board context for this connector only: board name, scope, allowed columns, counts, scope rule, and the compact 1.1 vehicle profile schema with the card_content_first UI flow. Call this before write operations."
        ),
        annotations=_read_tool_annotations("Board Context"),
        structured_output=True,
    )
    def get_board_context() -> JsonEnvelope:
        return _relay_board_call(
            "get_board_context",
            board_api.get_board_context,
            transform=lambda response: _with_text_section_meta(
                response,
                response_mode="summary",
                view_mode="summary",
                extra={
                    "full_snapshot_tool": "get_board_snapshot",
                    "content_tool": "get_board_content",
                    "events_tool": "get_board_events",
                },
            ),
        )

    @server.tool(
        name="review_board",
        description=_scoped_description(
            "Return an operational board review for the current AutoStop CRM board: summary counts, per-column load, manager alerts, priority cards, and recent important events."
        ),
        annotations=_read_tool_annotations("Board Review"),
        structured_output=True,
    )
    def review_board(
        stale_hours: McpInt = 48,
        overload_threshold: McpInt = 5,
        priority_limit: McpInt = 5,
        recent_event_limit: McpInt = 10,
    ) -> JsonEnvelope:
        effective_stale_hours = _normalize_limit(stale_hours, default=48, maximum=720)
        effective_overload_threshold = _normalize_limit(overload_threshold, default=5, maximum=100)
        effective_priority_limit = _normalize_limit(priority_limit, default=5, maximum=20)
        effective_recent_event_limit = _normalize_limit(recent_event_limit, default=10, maximum=50)
        return _relay_board_call(
            "review_board",
            lambda: board_api.review_board(
                stale_hours=effective_stale_hours,
                overload_threshold=effective_overload_threshold,
                priority_limit=effective_priority_limit,
                recent_event_limit=effective_recent_event_limit,
            ),
            error_code="review_board_unreachable",
            params={
                "stale_hours": effective_stale_hours,
                "overload_threshold": effective_overload_threshold,
                "priority_limit": effective_priority_limit,
                "recent_event_limit": effective_recent_event_limit,
            },
        )

    @server.tool(
        name="manager_board_scan",
        description=_scoped_description(
            "Run a compact manager scan of the current AutoStop CRM board: active counts, overdue/critical cards, inbox, ready unpaid cards, missing manager data, overloaded columns, and repair-order consistency hints."
        ),
        annotations=_read_tool_annotations("Manager Board Scan"),
        structured_output=True,
    )
    def manager_board_scan(limit: McpInt = 50) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=50, maximum=200)
        return _relay_board_call(
            "manager_board_scan",
            lambda: board_api.manager_board_scan(limit=effective_limit),
            params={"limit": effective_limit},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="manager_board_scan",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="list_ready_unpaid_cards",
        description=_scoped_description(
            "List ready vehicles that still appear unpaid or tagged as waiting for payment, with compact card and repair-order payment state."
        ),
        annotations=_read_tool_annotations("Ready Unpaid Cards"),
        structured_output=True,
    )
    def list_ready_unpaid_cards(limit: McpInt = 50) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=50, maximum=200)
        return _relay_board_call(
            "list_ready_unpaid_cards",
            lambda: board_api.list_ready_unpaid_cards(limit=effective_limit),
            params={"limit": effective_limit},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="ready_unpaid_cards",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="triage_inbox_cards",
        description=_scoped_description(
            "Classify inbox cards into compact triage buckets and recommended next CRM tools without changing the board."
        ),
        annotations=_read_tool_annotations("Triage Inbox Cards"),
        structured_output=True,
    )
    def triage_inbox_cards(limit: McpInt = 50) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=50, maximum=200)
        return _relay_board_call(
            "triage_inbox_cards",
            lambda: board_api.triage_inbox_cards(limit=effective_limit),
            params={"limit": effective_limit},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="inbox_triage",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="list_cards_missing_manager_data",
        description=_scoped_description(
            "List active cards missing manager-critical data such as board summary, fresh summary, VIN, client link, or description."
        ),
        annotations=_read_tool_annotations("Missing Manager Data"),
        structured_output=True,
    )
    def list_cards_missing_manager_data(
        limit: McpInt = 50, kinds: list[str] | None = None
    ) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=50, maximum=200)
        return _relay_board_call(
            "list_cards_missing_manager_data",
            lambda: board_api.list_cards_missing_manager_data(limit=effective_limit, kinds=kinds),
            params={"limit": effective_limit, "kinds": kinds},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="missing_manager_data",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="audit_repair_order_consistency",
        description=_scoped_description(
            "Audit consistency between card ready state, archive state, payment tags, and repair-order status without changing the board."
        ),
        annotations=_read_tool_annotations("Repair Order Consistency Audit"),
        structured_output=True,
    )
    def audit_repair_order_consistency(limit: McpInt = 50) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=50, maximum=200)
        return _relay_board_call(
            "audit_repair_order_consistency",
            lambda: board_api.audit_repair_order_consistency(limit=effective_limit),
            params={"limit": effective_limit},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="repair_order_consistency_audit",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="audit_client_links",
        description=_scoped_description(
            "Find active cards without a linked client and return compact candidate client matches. Defaults to redacted private fields."
        ),
        annotations=_read_tool_annotations("Client Links Audit"),
        structured_output=True,
    )
    def audit_client_links(
        limit: McpInt = 50,
        candidate_limit: McpInt = 3,
        redact_private: bool = True,
    ) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=50, maximum=200)
        effective_candidate_limit = _normalize_limit(candidate_limit, default=3, maximum=10)
        return _relay_board_call(
            "audit_client_links",
            lambda: board_api.audit_client_links(
                limit=effective_limit,
                candidate_limit=effective_candidate_limit,
                redact_private=redact_private,
            ),
            params={
                "limit": effective_limit,
                "candidate_limit": effective_candidate_limit,
                "redact_private": redact_private,
            },
            transform=lambda response: _with_data_meta(
                response,
                response_mode="client_link_audit",
                view_mode="compact",
                redact_private=redact_private,
            ),
        )

    @server.tool(
        name="list_cashboxes",
        description=_scoped_description(
            "List all cashboxes of the current AutoStop CRM board instance with compact balance statistics."
        ),
        annotations=_read_tool_annotations("List Cashboxes"),
        structured_output=True,
    )
    def list_cashboxes(limit: McpInt = 200) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=200, maximum=1000)
        return _relay_board_call(
            "list_cashboxes",
            lambda: board_api.list_cashboxes(limit=effective_limit),
            error_code="cashboxes_unreachable",
            params={"limit": effective_limit},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="list",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="get_cash_journal",
        description=_scoped_description(
            "Return the cashbox journal for the current board as machine-readable JSON "
            "with entries/days/weeks/months/totals plus a human-readable Markdown report. "
            "Use this for cashbox audit, reconciliation, and readable journal review."
        ),
        annotations=_read_tool_annotations("Get Cash Journal"),
        structured_output=True,
    )
    def get_cash_journal(months: McpInt = 3, limit: McpInt = 5000) -> JsonEnvelope:
        effective_months = _normalize_limit(months, default=3, maximum=12)
        effective_limit = _normalize_limit(limit, default=5000, maximum=10000)
        return _relay_board_call(
            "get_cash_journal",
            lambda: board_api.get_cash_journal(months=effective_months, limit=effective_limit),
            error_code="cash_journal_unreachable",
            params={"months": effective_months, "limit": effective_limit},
        )

    @server.tool(
        name="get_cashbox",
        description=_scoped_description(
            "Return one cashbox with its statistics and transaction journal."
        ),
        annotations=_read_tool_annotations("Get Cashbox"),
        structured_output=True,
    )
    def get_cashbox(
        cashbox_id: str, transaction_limit: McpInt = 300, transaction_offset: McpInt = 0
    ) -> JsonEnvelope:
        effective_transaction_limit = _normalize_limit(transaction_limit, default=300, maximum=5000)
        effective_transaction_offset = _normalize_limit(
            transaction_offset, default=0, minimum=0, maximum=1_000_000
        )
        return _relay_board_call(
            "get_cashbox",
            lambda: board_api.get_cashbox(
                cashbox_id,
                transaction_limit=effective_transaction_limit,
                transaction_offset=effective_transaction_offset,
            ),
            error_code="cashbox_unreachable",
            params={
                "cashbox_id": cashbox_id,
                "transaction_limit": effective_transaction_limit,
                "transaction_offset": effective_transaction_offset,
            },
        )

    @server.tool(
        name="create_cashbox",
        description=_scoped_description(
            "Create a new cashbox for money inflow and outflow tracking."
        ),
        annotations=_write_tool_annotations("Create Cashbox"),
        structured_output=True,
    )
    def create_cashbox(name: str, actor_name: str | None = None) -> JsonEnvelope:
        return _relay_board_call(
            "create_cashbox",
            lambda: board_api.create_cashbox(name, actor_name=actor_name),
            error_code="cashbox_write_unreachable",
        )

    @server.tool(
        name="delete_cashbox",
        description=_scoped_description("Delete an empty cashbox from the current board instance."),
        annotations=_write_tool_annotations("Delete Cashbox", destructive=True),
        structured_output=True,
    )
    def delete_cashbox(cashbox_id: str, actor_name: str | None = None) -> JsonEnvelope:
        return _relay_board_call(
            "delete_cashbox",
            lambda: board_api.delete_cashbox(cashbox_id, actor_name=actor_name),
            error_code="cashbox_write_unreachable",
        )

    @server.tool(
        name="create_cash_transaction",
        description=_scoped_description(
            "Create one cash transaction in a cashbox. Use direction income or expense and pass either amount_minor or amount. Expense requires note with at least 10 visible characters."
        ),
        annotations=_write_tool_annotations("Create Cash Transaction"),
        structured_output=True,
    )
    def create_cash_transaction(
        cashbox_id: str,
        direction: Literal["income", "expense"],
        amount_minor: McpInt | None = None,
        amount: str | None = None,
        note: str = "",
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "create_cash_transaction",
            lambda: board_api.create_cash_transaction(
                cashbox_id=cashbox_id,
                direction=direction,
                amount_minor=amount_minor,
                amount=amount,
                note=note,
                actor_name=actor_name,
            ),
            error_code="cashbox_write_unreachable",
        )

    @server.tool(
        name="list_inventory_items",
        description=_scoped_description(
            "List minimal warehouse items with current quantity, unit, cost price, and sale price."
        ),
        annotations=_read_tool_annotations("List Inventory Items"),
        structured_output=True,
    )
    def list_inventory_items(query: str | None = None, limit: McpInt = 200) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=200, maximum=500)
        return _relay_board_call(
            "list_inventory_items",
            lambda: board_api.list_inventory_items(query=query, limit=effective_limit),
            error_code="inventory_unreachable",
            params={"query": query, "limit": effective_limit},
        )

    @server.tool(
        name="search_inventory_items",
        description=_scoped_description("Search warehouse items by name, catalog number, or id."),
        annotations=_read_tool_annotations("Search Inventory Items"),
        structured_output=True,
    )
    def search_inventory_items(query: str = "", limit: McpInt = 50) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=50, maximum=500)
        return _relay_board_call(
            "search_inventory_items",
            lambda: board_api.search_inventory_items(query=query, limit=effective_limit),
            error_code="inventory_unreachable",
            params={"query": query, "limit": effective_limit},
        )

    @server.tool(
        name="get_inventory_item",
        description=_scoped_description(
            "Return one warehouse item and its recent technical movements."
        ),
        annotations=_read_tool_annotations("Get Inventory Item"),
        structured_output=True,
    )
    def get_inventory_item(item_id: str) -> JsonEnvelope:
        return _relay_board_call(
            "get_inventory_item",
            lambda: board_api.get_inventory_item(item_id),
            error_code="inventory_unreachable",
        )

    @server.tool(
        name="list_inventory_movements",
        description=_scoped_description(
            "List technical warehouse movements, optionally filtered by item_id or card_id."
        ),
        annotations=_read_tool_annotations("List Inventory Movements"),
        structured_output=True,
    )
    def list_inventory_movements(
        item_id: str | None = None,
        card_id: str | None = None,
        limit: McpInt = 200,
    ) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=200, maximum=500)
        return _relay_board_call(
            "list_inventory_movements",
            lambda: board_api.list_inventory_movements(
                item_id=item_id, card_id=card_id, limit=effective_limit
            ),
            error_code="inventory_unreachable",
            params={"item_id": item_id, "card_id": card_id, "limit": effective_limit},
        )

    @server.tool(
        name="save_inventory_item",
        description=_scoped_description(
            "Create or update a minimal warehouse item. New items may include an initial quantity."
        ),
        annotations=_write_tool_annotations("Save Inventory Item"),
        structured_output=True,
    )
    def save_inventory_item(
        name: str,
        item_id: str | None = None,
        catalog_number: str = "",
        unit: Literal["шт", "л"] = "шт",
        quantity: str = "0",
        cost_price: str = "0",
        sale_price: str = "0",
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        payload: dict[str, object] = {
            "name": name,
            "catalog_number": catalog_number,
            "unit": unit,
            "quantity": quantity,
            "cost_price": cost_price,
            "sale_price": sale_price,
        }
        if item_id:
            payload["item_id"] = item_id
        return _relay_board_call(
            "save_inventory_item",
            lambda: board_api.save_inventory_item(payload, actor_name=actor_name),
            error_code="inventory_write_unreachable",
        )

    @server.tool(
        name="replenish_inventory_item",
        description=_scoped_description(
            "Increase warehouse quantity for an existing item and optionally update prices."
        ),
        annotations=_write_tool_annotations("Replenish Inventory Item"),
        structured_output=True,
    )
    def replenish_inventory_item(
        item_id: str,
        quantity: str,
        cost_price: str | None = None,
        sale_price: str | None = None,
        note: str | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "replenish_inventory_item",
            lambda: board_api.replenish_inventory_item(
                item_id,
                quantity,
                cost_price=cost_price,
                sale_price=sale_price,
                note=note,
                actor_name=actor_name,
            ),
            error_code="inventory_write_unreachable",
        )

    @server.tool(
        name="write_off_inventory_item",
        description=_scoped_description(
            "Write off an item from warehouse into a repair-order material row. "
            "This is the only operation that changes stock from a repair order."
        ),
        annotations=_write_tool_annotations("Write Off Inventory Item"),
        structured_output=True,
    )
    def write_off_inventory_item(
        item_id: str,
        card_id: str,
        quantity: str,
        row_index: McpInt | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "write_off_inventory_item",
            lambda: board_api.write_off_inventory_item(
                item_id,
                card_id=card_id,
                quantity=quantity,
                row_index=row_index,
                actor_name=actor_name,
            ),
            error_code="inventory_write_unreachable",
        )

    @server.tool(
        name="return_inventory_movement",
        description=_scoped_description(
            "Return a previous warehouse write-off and unlink the material row from stock."
        ),
        annotations=_write_tool_annotations("Return Inventory Movement"),
        structured_output=True,
    )
    def return_inventory_movement(
        movement_id: str,
        card_id: str | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "return_inventory_movement",
            lambda: board_api.return_inventory_movement(
                movement_id, card_id=card_id, actor_name=actor_name
            ),
            error_code="inventory_write_unreachable",
        )

    @server.tool(
        name="update_board_settings",
        description=_scoped_description(
            "Update board-wide settings for the current AutoStop CRM board. Currently supports board_scale."
        ),
        annotations=_write_tool_annotations("Update Board Settings"),
        structured_output=True,
    )
    def update_board_settings(board_scale: float, actor_name: str | None = None) -> JsonEnvelope:
        return _relay_board_call(
            "update_board_settings",
            lambda: board_api.update_board_settings(board_scale=board_scale, actor_name=actor_name),
        )

    @server.tool(
        name="get_board_content",
        description=_scoped_description(
            "Return the hidden machine wall board-content section as Markdown for the current AutoStop CRM board: columns, card content, archived card content by default, sticky notes, compact vehicle profiles, and board context, without the event journal. "
            "This can be a heavy read when include_archived=true or view_mode=full. Use view_mode=agent for a lighter GPT-oriented read; that mode keeps cards compact and caps the recent wall slice."
        ),
        annotations=_read_tool_annotations("Board Content"),
        structured_output=True,
    )
    def get_board_content(
        include_archived: bool = True,
        view_mode: Literal["agent", "full"] = "agent",
    ) -> JsonEnvelope:
        return _relay_board_call(
            "get_board_content",
            lambda: board_api.get_board_content(
                include_archived=include_archived,
                view_mode=view_mode,
            ),
            error_code="board_content_unreachable",
            params={
                "include_archived": include_archived,
                "view_mode": view_mode,
            },
            transform=lambda response: _with_text_section_meta(
                _with_connector_identity(response),
                response_mode="agent_context" if view_mode == "agent" else "export",
                view_mode=view_mode,
                extra={
                    "include_archived": include_archived,
                    "text_format": "markdown",
                    "section_kind": "board_content",
                },
            ),
        )

    @server.tool(
        name="get_board_events",
        description=_scoped_description(
            "Return the hidden machine wall event-log section as Markdown for the current AutoStop CRM board: newest-first events, what happened, when, by whom, and which card it affected when available. "
            "The default event_limit is 100. Large limits are heavy; use a small event_limit for routine diagnostics. Use include_archived to keep the surrounding board-content read aligned with archive visibility; events remain a newest-first audit slice."
        ),
        annotations=_read_tool_annotations("Board Events"),
        structured_output=True,
    )
    def get_board_events(
        event_limit: McpInt = 100,
        include_archived: bool = True,
        view_mode: Literal["audit", "full"] = "audit",
    ) -> JsonEnvelope:
        effective_event_limit = _normalize_limit(event_limit, default=100, maximum=5000)
        return _relay_board_call(
            "get_board_events",
            lambda: board_api.get_board_events(
                event_limit=effective_event_limit,
                include_archived=include_archived,
                view_mode=view_mode,
            ),
            error_code="board_events_unreachable",
            params={
                "event_limit": effective_event_limit,
                "include_archived": include_archived,
                "view_mode": view_mode,
            },
            transform=lambda response: _with_text_section_meta(
                _with_connector_identity(response),
                response_mode="audit",
                view_mode=view_mode,
                extra={
                    "event_limit": effective_event_limit,
                    "include_archived": include_archived,
                    "text_format": "markdown",
                    "section_kind": "event_log",
                    "event_order": "newest_first",
                },
            ),
        )

    @server.tool(
        name="get_gpt_wall",
        description=_scoped_description(
            "Return the hidden machine wall aggregate for the current AutoStop CRM board as Markdown: full card text, structured board state, newest-first recent events, compact 1.1 vehicle profile summaries for each card, and separated board_content / event_log sections. "
            "This is the heaviest board context tool. Use view_mode=agent for the normal GPT context flow; that mode keeps cards compact and the event slice short. Use view_mode=full only for wide diagnostics or exports."
        ),
        annotations=_read_tool_annotations("GPT Wall"),
        structured_output=True,
    )
    def get_gpt_wall(
        include_archived: bool = True,
        event_limit: McpInt = 100,
        view_mode: Literal["agent", "full"] = "agent",
    ) -> JsonEnvelope:
        compact_cards = view_mode == "agent"
        effective_event_limit = (
            _normalize_limit(event_limit, default=100, maximum=GPT_WALL_AGENT_EVENT_LIMIT)
            if compact_cards
            else _normalize_limit(event_limit, default=100, maximum=5000)
        )
        return _relay_board_call(
            "get_gpt_wall",
            lambda: _enrich_gpt_wall_response(
                board_api.get_gpt_wall(
                    include_archived=include_archived,
                    event_limit=effective_event_limit,
                    compact=compact_cards,
                )
            ),
            error_code="gpt_wall_unreachable",
            params={
                "include_archived": include_archived,
                "event_limit": effective_event_limit,
                "view_mode": view_mode,
            },
            transform=lambda response: _with_text_section_meta(
                response,
                response_mode="agent_context" if view_mode == "agent" else "full",
                view_mode=view_mode,
                extra={
                    "include_archived": include_archived,
                    "event_limit": effective_event_limit,
                    "text_format": "markdown",
                    "section_kind": "gpt_wall",
                    "event_order": "newest_first",
                    "cards_compact": compact_cards,
                },
            ),
        )

    @server.tool(
        name="get_card_log",
        description=_scoped_description(
            "Return the card_journal.v2 audit log of one card from the current AutoStop CRM board. By default this keeps the legacy full format with raw changes and Markdown. Use compact=true and limit=50 for a fast GPT-safe journal slice without heavy raw/Markdown fields."
        ),
        annotations=_read_tool_annotations("Card Log"),
        structured_output=True,
    )
    def get_card_log(
        card_id: str,
        limit: McpInt | None = None,
        compact: bool = False,
        include_full_details: bool = False,
        view_mode: Literal["audit", "full"] = "audit",
    ) -> JsonEnvelope:
        effective_limit = limit
        if compact:
            effective_limit = _normalize_limit(effective_limit, default=50, maximum=1000)
        return _relay_board_call(
            "get_card_log",
            lambda: board_api.get_card_log(
                card_id,
                limit=effective_limit,
                compact=compact,
                include_full_details=include_full_details,
            ),
            params={
                "card_id": card_id,
                "limit": effective_limit,
                "compact": compact,
                "include_full_details": include_full_details,
                "view_mode": view_mode,
            },
            transform=lambda response: _with_data_meta(
                response,
                response_mode="audit",
                view_mode=view_mode,
                compact=compact,
                text_encoding=None if compact else "utf-8",
            ),
        )

    @server.tool(
        name="list_clients",
        description=_scoped_description(
            "List clients and organizations from the current AutoStop CRM board. Use this for the Clients module overview; it returns compact client rows with phone/phones, type, and optional statistics. A client can have up to 3 phones; phone is the first/main one."
        ),
        annotations=_read_tool_annotations("List Clients"),
        structured_output=True,
    )
    def list_clients(limit: McpInt = 100, include_stats: bool = True) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=100, maximum=1000)
        return _relay_board_call(
            "list_clients",
            lambda: board_api.list_clients(limit=effective_limit, include_stats=include_stats),
            params={"limit": effective_limit, "include_stats": include_stats},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="client_list",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="search_clients",
        description=_scoped_description(
            "Search clients and organizations by name, any saved phone, email, INN, vehicle, VIN, or license plate. Use before creating a client; when a vehicle is known, choose the matching vehicles_preview[].id and pass it as client_vehicle_id to link_card_to_client."
        ),
        annotations=_read_tool_annotations("Search Clients"),
        structured_output=True,
    )
    def search_clients(query: str = "", limit: McpInt = 10) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=10, maximum=100)
        return _relay_board_call(
            "search_clients",
            lambda: board_api.search_clients(query=query, limit=effective_limit),
            params={"query": query, "limit": effective_limit},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="client_search",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="get_client",
        description=_scoped_description(
            "Return one client profile with related vehicles and recent repair orders from the current AutoStop CRM board."
        ),
        annotations=_read_tool_annotations("Get Client"),
        structured_output=True,
    )
    def get_client(client_id: str, order_limit: McpInt = 30) -> JsonEnvelope:
        effective_order_limit = _normalize_limit(order_limit, default=30, maximum=200)
        return _relay_board_call(
            "get_client",
            lambda: board_api.get_client(client_id, order_limit=effective_order_limit),
            params={"client_id": client_id, "order_limit": effective_order_limit},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="client_profile",
                view_mode="profile",
            ),
        )

    @server.tool(
        name="get_client_stats",
        description=_scoped_description(
            "Return compact statistics for one client: linked cards, repair orders, active/closed order counts, vehicles, and last visit."
        ),
        annotations=_read_tool_annotations("Client Stats"),
        structured_output=True,
    )
    def get_client_stats(client_id: str) -> JsonEnvelope:
        return _relay_board_call(
            "get_client_stats",
            lambda: board_api.get_client_stats(client_id),
            params={"client_id": client_id},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="client_stats",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="create_client",
        description=_scoped_description(
            "Create a person, IP, OOO, or organization client profile. For several phone numbers pass phones with up to 3 strings; phone remains the first/main number. This does not automatically change any card unless link_card_to_client is called afterwards."
        ),
        annotations=_write_tool_annotations("Create Client"),
        structured_output=True,
    )
    def create_client(client: ClientProfilePayload, actor_name: str | None = None) -> JsonEnvelope:
        return _relay_board_call(
            "create_client",
            lambda: board_api.create_client(
                client.model_dump(exclude_none=True),
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="update_client",
        description=_scoped_description(
            "Patch an existing client profile. Pass only the fields to change; phones may contain up to 3 numbers and phone remains the first/main number. Linked cards are not overwritten by this command."
        ),
        annotations=_write_tool_annotations("Update Client", idempotent=True),
        structured_output=True,
    )
    def update_client(
        client_id: str,
        patch: ClientPatchPayload,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "update_client",
            lambda: board_api.update_client(
                client_id,
                patch.model_dump(exclude_none=True),
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="delete_client",
        description=_scoped_description(
            "Delete one client profile. By default this rejects clients still linked to cards; set allow_linked only after operator confirmation."
        ),
        annotations=_write_tool_annotations("Delete Client", destructive=True),
        structured_output=True,
    )
    def delete_client(
        client_id: str,
        allow_linked: bool = False,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "delete_client",
            lambda: board_api.delete_client(
                client_id,
                allow_linked=allow_linked,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="link_card_to_client",
        description=_scoped_description(
            "Link one card to an existing client and, when known, a concrete client vehicle. Pass client_vehicle_id from search_clients/get_client to fill the vehicle passport; use create_vehicle_from_card=true when this is the same client but a new car."
        ),
        annotations=_write_tool_annotations("Link Card To Client", idempotent=True),
        structured_output=True,
    )
    def link_card_to_client(
        card_id: str,
        client_id: str,
        client_vehicle_id: str | None = None,
        create_vehicle_from_card: bool = False,
        sync_vehicle_fields: bool = True,
        sync_fields: bool = True,
        overwrite_card_fields: bool = False,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "link_card_to_client",
            lambda: board_api.link_card_to_client(
                card_id,
                client_id,
                client_vehicle_id=client_vehicle_id,
                create_vehicle_from_card=create_vehicle_from_card,
                sync_vehicle_fields=sync_vehicle_fields,
                sync_fields=sync_fields,
                overwrite_card_fields=overwrite_card_fields,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="upsert_client_vehicle",
        description=_scoped_description(
            "Create or update one vehicle inside an existing client profile. Use this before link_card_to_client when the operator identifies a new vehicle for an existing client."
        ),
        annotations=_write_tool_annotations("Upsert Client Vehicle", idempotent=True),
        structured_output=True,
    )
    def upsert_client_vehicle(
        client_id: str,
        vehicle: ClientVehiclePayload | None = None,
        client_vehicle_id: str | None = None,
        card_id: str | None = None,
        sync_linked_cards: bool | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "upsert_client_vehicle",
            lambda: board_api.upsert_client_vehicle(
                client_id,
                vehicle.model_dump(exclude_none=True) if vehicle is not None else None,
                client_vehicle_id=client_vehicle_id,
                card_id=card_id,
                sync_linked_cards=sync_linked_cards,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="delete_client_vehicle",
        description=_scoped_description(
            "Delete one vehicle from a client profile by client_vehicle_id. This does not delete cards or repair orders; with unlink_cards=true it only clears that concrete vehicle link from related cards."
        ),
        annotations=_write_tool_annotations("Delete Client Vehicle", destructive=True),
        structured_output=True,
    )
    def delete_client_vehicle(
        client_id: str,
        client_vehicle_id: str,
        unlink_cards: bool = True,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "delete_client_vehicle",
            lambda: board_api.delete_client_vehicle(
                client_id,
                client_vehicle_id,
                unlink_cards=unlink_cards,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="unlink_card_from_client",
        description=_scoped_description(
            "Remove the client link from one card without deleting the client or erasing free-text client fields."
        ),
        annotations=_write_tool_annotations("Unlink Card From Client", idempotent=True),
        structured_output=True,
    )
    def unlink_card_from_client(card_id: str, actor_name: str | None = None) -> JsonEnvelope:
        return _relay_board_call(
            "unlink_card_from_client",
            lambda: board_api.unlink_card_from_client(card_id, actor_name=actor_name),
        )

    @server.tool(
        name="suggest_clients_for_card",
        description=_scoped_description(
            "Suggest existing clients for one card using the card's free-text client name, phone, repair order data, and optional query."
        ),
        annotations=_read_tool_annotations("Suggest Clients For Card"),
        structured_output=True,
    )
    def suggest_clients_for_card(
        card_id: str, query: str | None = None, limit: McpInt = 8
    ) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=8, maximum=30)
        return _relay_board_call(
            "suggest_clients_for_card",
            lambda: board_api.suggest_clients_for_card(card_id, query=query, limit=effective_limit),
            params={"card_id": card_id, "query": query, "limit": effective_limit},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="client_suggestions",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="list_repair_orders",
        description=_scoped_description(
            "List repair orders from the current AutoStop CRM board with status filtering, search, sorting, card links, client, vehicle, and text-file metadata."
        ),
        annotations=_read_tool_annotations("List Repair Orders"),
        structured_output=True,
    )
    def list_repair_orders(
        limit: McpInt = 50,
        status: Literal["open", "ready", "closed", "all"] = "open",
        query: str | None = None,
        sort_by: Literal["number", "opened_at", "closed_at"] | None = None,
        sort_dir: Literal["asc", "desc"] | None = None,
        compact: bool = False,
        redact_private: bool = False,
    ) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=300, maximum=300)
        return _relay_board_call(
            "list_repair_orders",
            lambda: board_api.list_repair_orders(
                limit=effective_limit,
                status=status,
                query=query,
                sort_by=sort_by,
                sort_dir=sort_dir,
                compact=compact,
                redact_private=redact_private,
            ),
            params={
                "limit": effective_limit,
                "status": status,
                "query": query,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
                "compact": compact,
                "redact_private": redact_private,
            },
            transform=lambda response: _with_data_meta(
                response,
                response_mode="list",
                view_mode="compact" if compact else "full",
                compact=compact,
                redact_private=redact_private,
            ),
        )

    @server.tool(
        name="get_repair_order",
        description=_scoped_description(
            "Return the structured repair order of one card from the current AutoStop CRM board."
        ),
        annotations=_read_tool_annotations("Get Repair Order"),
        structured_output=True,
    )
    def get_repair_order(card_id: str) -> JsonEnvelope:
        return _relay_board_call("get_repair_order", lambda: board_api.get_repair_order(card_id))

    @server.tool(
        name="get_repair_order_text",
        description=_scoped_description(
            "Return the text rendering of one repair order from the current AutoStop CRM board together with file metadata."
        ),
        annotations=_read_tool_annotations("Repair Order Text"),
        structured_output=True,
    )
    def get_repair_order_text(card_id: str) -> JsonEnvelope:
        return _relay_board_call(
            "get_repair_order_text",
            lambda: board_api.get_repair_order_text(card_id),
        )

    @server.tool(
        name="download_repair_order_print_pdf",
        description=_scoped_description(
            "Export one repair order, invoice, invoice-factura, UPD, completion act, acceptance act, parts sale document, or inspection sheet as the same CRM-generated PDF that operators download from the print window. Returns application/pdf base64 for attaching to emails or messages."
        ),
        annotations=_read_tool_annotations("Download Repair Order PDF"),
        structured_output=True,
    )
    def download_repair_order_print_pdf(
        card_id: str,
        selected_document_ids: list[
            Literal[
                "repair_order",
                "vehicle_acceptance_act",
                "invoice",
                "invoice_factura",
                "upd",
                "inspection_sheet",
                "completion_act",
                "parts_sale",
            ]
        ]
        | None = None,
        selected_template_ids: dict[str, str] | None = None,
        print_settings: dict[str, Any] | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "download_repair_order_print_pdf",
            lambda: board_api.download_repair_order_print_pdf(
                card_id=card_id,
                selected_document_ids=selected_document_ids,
                selected_template_ids=selected_template_ids,
                print_settings=print_settings,
            ),
            params={
                "card_id": card_id,
                "selected_document_ids": selected_document_ids,
                "selected_template_ids": selected_template_ids,
                "print_settings": print_settings,
            },
            transform=lambda response: _with_data_meta(
                response,
                response_mode="repair_order_pdf_download",
                view_mode="base64_pdf",
                mime_type="application/pdf",
            ),
        )

    @server.tool(
        name="create_document_without_card_pdf",
        description=_scoped_description(
            "Create a standard AutoStop PDF without a CRM card through the CRM print module. "
            "Use this for invoices, invoice-facturas, UPD, completion acts, vehicle acceptance acts, repair orders, inspection sheets, defect reports, and parts sale documents when the user provides the data manually in text. "
            "You may omit document_type when request_text clearly names the document in Russian, for example УПД, акт выполненных работ, дефектовка, заказ-наряд, счет-фактура, or продажа запчастей."
        ),
        annotations=_read_tool_annotations("Create Document Without Card PDF"),
        structured_output=True,
    )
    def create_document_without_card_pdf(
        request_text: str,
        document_type: str = "",
        manual_document: dict[str, Any] | None = None,
        selected_template_ids: dict[str, str] | None = None,
        print_settings: dict[str, Any] | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "create_document_without_card_pdf",
            lambda: board_api.create_document_without_card_pdf(
                request_text=request_text,
                document_type=document_type,
                manual_document=manual_document,
                selected_template_ids=selected_template_ids,
                print_settings=print_settings,
            ),
            params={
                "request_text": request_text,
                "document_type": document_type,
                "manual_document": manual_document,
                "selected_template_ids": selected_template_ids,
                "print_settings": print_settings,
            },
            transform=lambda response: _with_data_meta(
                response,
                response_mode="manual_document_pdf_download",
                view_mode="base64_pdf",
                mime_type="application/pdf",
            ),
        )

    @server.tool(
        name="list_archived_cards",
        description=_scoped_description("List archived cards from the current AutoStop CRM board."),
        annotations=_read_tool_annotations("Archived Cards"),
        structured_output=True,
    )
    def list_archived_cards(limit: McpInt = 10, compact: bool = False) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=30, maximum=100)
        return _relay_board_call(
            "list_archived_cards",
            lambda: board_api.list_archived_cards(limit=effective_limit, compact=compact),
            params={"limit": effective_limit, "compact": compact},
            transform=lambda response: _with_cards_list_meta(
                response,
                include_archived=True,
                compact=compact,
                response_mode="archive_list",
            ),
        )

    @server.tool(
        name="search_cards",
        description=_scoped_description(
            "Search cards only inside the current AutoStop CRM board using query and optional filters such as column, tag, indicator, and status."
        ),
        annotations=_read_tool_annotations("Search Cards"),
        structured_output=True,
    )
    def search_cards(
        query: str | None = None,
        include_archived: bool = False,
        column: str | None = None,
        tag: str | None = None,
        indicator: Literal["green", "yellow", "red"] | None = None,
        status: Literal["ok", "warning", "critical", "expired"] | None = None,
        limit: McpInt = 20,
    ) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=20, maximum=100)
        return _relay_board_call(
            "search_cards",
            lambda: board_api.search_cards(
                query=query,
                include_archived=include_archived,
                column=column,
                tag=tag,
                indicator=indicator,
                status=status,
                limit=effective_limit,
            ),
            params={
                "query": query,
                "include_archived": include_archived,
                "column": column,
                "tag": tag,
                "indicator": indicator,
                "status": status,
                "limit": effective_limit,
            },
            transform=lambda response: _with_data_meta(
                response,
                response_mode="search",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="create_card",
        description=_scoped_description(
            "Create a card on the current AutoStop CRM board with vehicle, title, description, optional tags, optional target column, optional vehicle_profile, and a deadline. "
            "vehicle must contain make/model only, and title must contain the short essence of the issue, task, or result. "
            "If deadline is omitted or all-zero, the connector uses a default of one day. "
            "For the 1.1 vehicle card flow, prefer the compact vehicle fields: make_display, model_display, production_year, vin, engine_model, gearbox_model, drivetrain, and oem_notes."
        ),
        annotations=_write_tool_annotations("Create Card"),
        structured_output=True,
    )
    def create_card(
        title: str,
        deadline: DeadlinePayload | None = None,
        vehicle: str = "",
        description: str = "",
        column: str | None = None,
        tags: list[TagPayload | str] | None = None,
        vehicle_profile: dict[str, Any] | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "create_card",
            lambda: board_api.create_card(
                vehicle=vehicle,
                title=title,
                description=description,
                column=column,
                tags=_tag_list_payload(tags),
                deadline=_resolved_create_card_deadline(deadline),
                vehicle_profile=_compact_mapping_payload(vehicle_profile),
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="update_card",
        description=_scoped_description(
            "Update an existing card on the current AutoStop CRM board. Supported fields: vehicle, title, description, tags, deadline, and vehicle_profile. "
            "Keep vehicle limited to make/model only, and keep title limited to the short essence of the issue, task, or result. "
            "Keep manual vehicle fields authoritative; later autofill results must not silently overwrite them."
        ),
        annotations=_write_tool_annotations("Update Card"),
        structured_output=True,
    )
    def update_card(
        card_id: str,
        vehicle: str | None = None,
        title: str | None = None,
        description: str | None = None,
        tags: list[TagPayload | str] | None = None,
        deadline: DeadlinePayload | None = None,
        vehicle_profile: dict[str, Any] | None = None,
        actor_name: str | None = None,
        expected_updated_at: str | None = None,
        response_mode: Literal["full", "compact"] = "full",
    ) -> JsonEnvelope:
        return _relay_board_call(
            "update_card",
            lambda: board_api.update_card(
                card_id=card_id,
                vehicle=vehicle,
                title=title,
                description=description,
                tags=_tag_list_payload(tags),
                deadline=deadline.model_dump() if deadline is not None else None,
                vehicle_profile=_compact_mapping_payload(vehicle_profile),
                actor_name=actor_name,
                expected_updated_at=expected_updated_at,
                response_mode=response_mode,
            ),
        )

    @server.tool(
        name="set_card_board_summary",
        description=_scoped_description(
            "Set the hidden AI-managed board summary for one card. This is the short text shown on the board card instead of raw description preview. "
            "Use only after reading get_card_context. Keep it Russian, human-readable, max five non-empty lines, focused on what should happen next. "
            "Do not include phone numbers, VIN, private client data, or long technical dumps."
        ),
        annotations=_write_tool_annotations("Set Card Board Summary"),
        structured_output=True,
    )
    def set_card_board_summary(
        card_id: str,
        summary: str,
        actor_name: str | None = None,
        response_mode: Literal["full", "compact"] = "full",
    ) -> JsonEnvelope:
        return _relay_board_call(
            "set_card_board_summary",
            lambda: board_api.set_card_board_summary(
                card_id=card_id,
                summary=summary,
                actor_name=actor_name,
                response_mode=response_mode,
            ),
        )

    @server.tool(
        name="bulk_set_deadline_if_below",
        description=_scoped_description(
            "Set card timers in bulk only when remaining time is below a threshold. Defaults to dry_run; apply requires actor_name and returns compact verification."
        ),
        annotations=_write_tool_annotations("Bulk Set Deadline If Below", idempotent=True),
        structured_output=True,
    )
    def bulk_set_deadline_if_below(
        mode: Literal["dry_run", "apply"] = "dry_run",
        min_total_seconds: McpInt = 172800,
        target_total_seconds: McpInt = 172800,
        limit: McpInt = 200,
        include_archived: bool = False,
        card_ids: list[str] | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        effective_min_total_seconds = _normalize_limit(
            min_total_seconds, default=172800, maximum=31_536_000
        )
        effective_target_total_seconds = _normalize_limit(
            target_total_seconds,
            default=effective_min_total_seconds,
            maximum=31_536_000,
        )
        effective_target_total_seconds = max(
            effective_target_total_seconds, effective_min_total_seconds
        )
        effective_limit = _normalize_limit(limit, default=200, maximum=1000)
        return _relay_board_call(
            "bulk_set_deadline_if_below",
            lambda: board_api.bulk_set_deadline_if_below(
                mode=mode,
                min_total_seconds=effective_min_total_seconds,
                target_total_seconds=effective_target_total_seconds,
                limit=effective_limit,
                include_archived=include_archived,
                card_ids=card_ids,
                actor_name=actor_name,
            ),
            params={
                "mode": mode,
                "min_total_seconds": effective_min_total_seconds,
                "target_total_seconds": effective_target_total_seconds,
                "limit": effective_limit,
                "include_archived": include_archived,
                "card_ids": card_ids,
            },
            transform=lambda response: _with_data_meta(
                response,
                response_mode="manager_operation_result",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="bulk_refresh_board_summaries",
        description=_scoped_description(
            "Refresh missing or stale hidden board summaries with deterministic compact summaries. Defaults to dry_run; apply requires actor_name."
        ),
        annotations=_write_tool_annotations("Bulk Refresh Board Summaries", idempotent=True),
        structured_output=True,
    )
    def bulk_refresh_board_summaries(
        mode: Literal["dry_run", "apply"] = "dry_run",
        limit: McpInt = 100,
        only_missing: bool = False,
        only_stale: bool = False,
        card_ids: list[str] | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        effective_limit = _normalize_limit(limit, default=100, maximum=500)
        return _relay_board_call(
            "bulk_refresh_board_summaries",
            lambda: board_api.bulk_refresh_board_summaries(
                mode=mode,
                limit=effective_limit,
                only_missing=only_missing,
                only_stale=only_stale,
                card_ids=card_ids,
                actor_name=actor_name,
            ),
            params={
                "mode": mode,
                "limit": effective_limit,
                "only_missing": only_missing,
                "only_stale": only_stale,
                "card_ids": card_ids,
            },
            transform=lambda response: _with_data_meta(
                response,
                response_mode="manager_operation_result",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="cleanup_card",
        description=_scoped_description(
            "Apply or preview a compact card cleanup patch: title, vehicle, description, tags, deadline, vehicle_profile, and optional board summary refresh. Defaults to dry_run; apply requires actor_name."
        ),
        annotations=_write_tool_annotations("Cleanup Card", idempotent=True),
        structured_output=True,
    )
    def cleanup_card(
        card_id: str,
        mode: Literal["dry_run", "apply"] = "dry_run",
        actor_name: str | None = None,
        expected_updated_at: str | None = None,
        response_mode: Literal["full", "compact"] = "compact",
        refresh_summary: bool = False,
        summary: str | None = None,
        vehicle: str | None = None,
        title: str | None = None,
        description: str | None = None,
        tags: list[TagPayload | str] | None = None,
        deadline: DeadlinePayload | None = None,
        vehicle_profile: dict[str, Any] | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "cleanup_card",
            lambda: board_api.cleanup_card(
                card_id=card_id,
                mode=mode,
                actor_name=actor_name,
                expected_updated_at=expected_updated_at,
                response_mode=response_mode,
                refresh_summary=refresh_summary,
                summary=summary,
                vehicle=vehicle,
                title=title,
                description=description,
                tags=_tag_list_payload(tags),
                deadline=deadline.model_dump() if deadline is not None else None,
                vehicle_profile=_compact_mapping_payload(vehicle_profile),
            ),
            params={
                "card_id": card_id,
                "mode": mode,
                "response_mode": response_mode,
                "refresh_summary": refresh_summary,
            },
            transform=lambda response: _with_data_meta(
                response,
                response_mode="manager_operation_result",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="apply_ready_unpaid_followups",
        description=_scoped_description(
            "Preview or apply safe follow-ups for ready unpaid cards: waiting-payment tag, deadline floor, and compact board summary refresh. Defaults to dry_run; apply requires actor_name."
        ),
        annotations=_write_tool_annotations("Apply Ready Unpaid Followups", idempotent=True),
        structured_output=True,
    )
    def apply_ready_unpaid_followups(
        mode: Literal["dry_run", "apply"] = "dry_run",
        target_total_seconds: McpInt = 172800,
        limit: McpInt = 50,
        refresh_summary: bool = True,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        effective_target_total_seconds = _normalize_limit(
            target_total_seconds,
            default=172800,
            maximum=31_536_000,
        )
        effective_limit = _normalize_limit(limit, default=50, maximum=200)
        return _relay_board_call(
            "apply_ready_unpaid_followups",
            lambda: board_api.apply_ready_unpaid_followups(
                mode=mode,
                target_total_seconds=effective_target_total_seconds,
                limit=effective_limit,
                refresh_summary=refresh_summary,
                actor_name=actor_name,
            ),
            params={
                "mode": mode,
                "target_total_seconds": effective_target_total_seconds,
                "limit": effective_limit,
                "refresh_summary": refresh_summary,
            },
            transform=lambda response: _with_data_meta(
                response,
                response_mode="manager_operation_result",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="run_manager_operation",
        description=_scoped_description(
            "Dispatch one high-level manager operation by name with a nested payload. Defaults nested write operations to dry_run unless payload/mode asks for apply."
        ),
        annotations=_write_tool_annotations("Run Manager Operation", idempotent=True),
        structured_output=True,
    )
    def run_manager_operation(
        operation: str,
        payload: dict[str, Any] | None = None,
        mode: Literal["dry_run", "apply"] = "dry_run",
        actor_name: str | None = None,
        limit: McpInt | None = None,
    ) -> JsonEnvelope:
        operation_key = str(operation or "")
        manager_operation_limit_maximums = {
            "manager_board_scan": 200,
            "list_ready_unpaid_cards": 200,
            "triage_inbox_cards": 200,
            "list_cards_missing_manager_data": 200,
            "audit_repair_order_consistency": 200,
            "audit_client_links": 200,
            "bulk_set_deadline_if_below": 1000,
            "bulk_refresh_board_summaries": 500,
            "apply_ready_unpaid_followups": 200,
        }
        effective_limit_maximum = manager_operation_limit_maximums.get(operation_key, 200)
        effective_limit = (
            _normalize_limit(limit, default=50, maximum=effective_limit_maximum)
            if limit is not None
            else None
        )
        effective_payload = dict(payload) if isinstance(payload, dict) else payload
        if isinstance(effective_payload, dict) and "limit" in effective_payload:
            effective_payload["limit"] = _normalize_limit(
                effective_payload.get("limit"),
                default=50,
                maximum=effective_limit_maximum,
            )
        return _relay_board_call(
            "run_manager_operation",
            lambda: board_api.run_manager_operation(
                operation=operation,
                payload=effective_payload,
                mode=mode,
                actor_name=actor_name,
                limit=effective_limit,
            ),
            params={"operation": operation, "mode": mode, "limit": effective_limit},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="manager_operation",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="rollback_manager_run",
        description=_scoped_description(
            "Preview or apply rollback actions emitted by a previous manager operation response. Rollback by run_id alone is intentionally not supported without explicit rollback_actions."
        ),
        annotations=_write_tool_annotations("Rollback Manager Run", idempotent=True),
        structured_output=True,
    )
    def rollback_manager_run(
        mode: Literal["dry_run", "apply"] = "dry_run",
        rollback_actions: list[dict[str, Any]] | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "rollback_manager_run",
            lambda: board_api.rollback_manager_run(
                mode=mode,
                rollback_actions=rollback_actions,
                actor_name=actor_name,
            ),
            params={"mode": mode, "rollback_actions": rollback_actions},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="manager_operation_result",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="update_repair_order",
        description=_scoped_description(
            "Patch the structured repair order of one card on the current AutoStop CRM board. Pass a JSON object with only the fields to change; unspecified fields remain unchanged."
        ),
        annotations=_write_tool_annotations("Update Repair Order"),
        structured_output=True,
    )
    def update_repair_order(
        card_id: str,
        repair_order: RepairOrderPatchPayload,
        expected_updated_at: str | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        repair_order_payload = (
            repair_order
            if isinstance(repair_order, RepairOrderPatchPayload)
            else RepairOrderPatchPayload.model_validate(repair_order)
        )
        return _relay_board_call(
            "update_repair_order",
            lambda: board_api.update_repair_order(
                card_id=card_id,
                repair_order=repair_order_payload.model_dump(exclude_none=True),
                expected_updated_at=expected_updated_at,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="set_repair_order_status",
        description=_scoped_description(
            "Set the status of one repair order on the current AutoStop CRM board. Use open for active orders, ready for completed vehicles waiting for handoff/payment, and closed for archived orders."
        ),
        annotations=_write_tool_annotations("Set Repair Order Status"),
        structured_output=True,
    )
    def set_repair_order_status(
        card_id: str,
        status: Literal["open", "ready", "closed"],
        expected_updated_at: str | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "set_repair_order_status",
            lambda: board_api.set_repair_order_status(
                card_id=card_id,
                status=status,
                expected_updated_at=expected_updated_at,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="mark_card_ready",
        description=_scoped_description(
            "Mark one vehicle card as ready: move it to the system 'Готовые автомобили' column, add the 'Готов' card tag, and move its repair order to the ready list. If an operator says the car is ready, use this tool instead of closing the repair order."
        ),
        annotations=_write_tool_annotations("Mark Card Ready"),
        structured_output=True,
    )
    def mark_card_ready(card_id: str, actor_name: str | None = None) -> JsonEnvelope:
        return _relay_board_call(
            "mark_card_ready",
            lambda: board_api.mark_card_ready(card_id=card_id, actor_name=actor_name),
        )

    @server.tool(
        name="replace_repair_order_works",
        description=_scoped_description(
            "Replace the full Works table of a repair order on the current AutoStop CRM board."
        ),
        annotations=_write_tool_annotations("Replace Repair Order Works"),
        structured_output=True,
    )
    def replace_repair_order_works(
        card_id: str,
        rows: list[RepairOrderRowPayload],
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "replace_repair_order_works",
            lambda: board_api.replace_repair_order_works(
                card_id=card_id,
                rows=[row.model_dump() for row in rows],
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="replace_repair_order_materials",
        description=_scoped_description(
            "Replace the full Materials table of a repair order on the current AutoStop CRM board."
        ),
        annotations=_write_tool_annotations("Replace Repair Order Materials"),
        structured_output=True,
    )
    def replace_repair_order_materials(
        card_id: str,
        rows: list[RepairOrderRowPayload],
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "replace_repair_order_materials",
            lambda: board_api.replace_repair_order_materials(
                card_id=card_id,
                rows=[row.model_dump() for row in rows],
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="update_sticky",
        description=_scoped_description(
            "Update the text or deadline of a sticky note on the current AutoStop CRM board. "
            "The deadline accepts either days/hours/minutes/seconds or total_seconds."
        ),
        annotations=_write_tool_annotations("Update Sticky"),
        structured_output=True,
    )
    def update_sticky(
        sticky_id: str,
        text: str | None = None,
        deadline: StickyDeadlinePayload | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "update_sticky",
            lambda: board_api.update_sticky(
                sticky_id=sticky_id,
                text=text,
                deadline=deadline.model_dump() if deadline is not None else None,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="move_sticky",
        description=_scoped_description(
            "Move a sticky note on the current AutoStop CRM board to a new x/y position."
        ),
        annotations=_write_tool_annotations("Move Sticky"),
        structured_output=True,
    )
    def move_sticky(sticky_id: str, x: int, y: int, actor_name: str | None = None) -> JsonEnvelope:
        return _relay_board_call(
            "move_sticky",
            lambda: board_api.move_sticky(sticky_id=sticky_id, x=x, y=y, actor_name=actor_name),
        )

    @server.tool(
        name="delete_sticky",
        description=_scoped_description(
            "Delete a sticky note from the current AutoStop CRM board."
        ),
        annotations=_write_tool_annotations("Delete Sticky", destructive=True),
        structured_output=True,
    )
    def delete_sticky(sticky_id: str, actor_name: str | None = None) -> JsonEnvelope:
        return _relay_board_call(
            "delete_sticky",
            lambda: board_api.delete_sticky(sticky_id=sticky_id, actor_name=actor_name),
        )

    @server.tool(
        name="set_card_deadline",
        description=_scoped_description(
            "Change only the deadline of a card on the current AutoStop CRM board. "
            "The deadline accepts either days/hours/minutes/seconds or total_seconds."
        ),
        annotations=_write_tool_annotations("Set Card Deadline"),
        structured_output=True,
    )
    def set_card_deadline(
        card_id: str,
        deadline: DeadlinePayload,
        actor_name: str | None = None,
        response_mode: Literal["full", "compact"] = "full",
    ) -> JsonEnvelope:
        return _relay_board_call(
            "set_card_deadline",
            lambda: board_api.set_card_deadline(
                card_id=card_id,
                deadline=deadline.model_dump(),
                actor_name=actor_name,
                response_mode=response_mode,
            ),
        )

    @server.tool(
        name="set_card_indicator",
        description=_scoped_description(
            "Service tool for changing the signal lamp state of a card. Because the indicator is derived from time, this operation recalculates the deadline to reach the requested color."
        ),
        annotations=_write_tool_annotations("Set Card Indicator"),
        structured_output=True,
    )
    def set_card_indicator(
        card_id: str,
        indicator: Literal["green", "yellow", "red"],
        actor_name: str | None = None,
        response_mode: Literal["full", "compact"] = "full",
    ) -> JsonEnvelope:
        return _relay_board_call(
            "set_card_indicator",
            lambda: board_api.set_card_indicator(
                card_id=card_id,
                indicator=indicator,
                actor_name=actor_name,
                response_mode=response_mode,
            ),
        )

    @server.tool(
        name="move_card",
        description=_scoped_description(
            "Move a card on the current AutoStop CRM board using the target column id. "
            "Optionally pass before_card_id to reorder inside the same column or insert before another card in the target column."
        ),
        annotations=_write_tool_annotations("Move Card"),
        structured_output=True,
    )
    def move_card(
        card_id: str,
        column: str,
        before_card_id: str | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "move_card",
            lambda: board_api.move_card(
                card_id=card_id,
                column=column,
                before_card_id=before_card_id,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="bulk_move_cards",
        description=_scoped_description(
            "Move multiple cards to one target column on the current AutoStop CRM board in a single write call. Prefer this over long chains of sequential move_card calls."
        ),
        annotations=_write_tool_annotations("Bulk Move Cards", idempotent=True),
        structured_output=True,
    )
    def bulk_move_cards(
        card_ids: list[str],
        column: str,
        actor_name: str | None = None,
        response_mode: Literal["full", "compact"] = "full",
    ) -> JsonEnvelope:
        return _relay_board_call(
            "bulk_move_cards",
            lambda: board_api.bulk_move_cards(
                card_ids=card_ids,
                column=column,
                actor_name=actor_name,
                response_mode=response_mode,
            ),
        )

    @server.tool(
        name="archive_card",
        description=_scoped_description("Archive a card on the current AutoStop CRM board."),
        annotations=_write_tool_annotations("Archive Card", destructive=True),
        structured_output=True,
    )
    def archive_card(card_id: str, actor_name: str | None = None) -> JsonEnvelope:
        return _relay_board_call(
            "archive_card",
            lambda: board_api.archive_card(card_id=card_id, actor_name=actor_name),
        )

    @server.tool(
        name="restore_card",
        description=_scoped_description(
            "Restore an archived card back onto the current AutoStop CRM board."
        ),
        annotations=_write_tool_annotations("Restore Card"),
        structured_output=True,
    )
    def restore_card(
        card_id: str, column: str | None = None, actor_name: str | None = None
    ) -> JsonEnvelope:
        return _relay_board_call(
            "restore_card",
            lambda: board_api.restore_card(card_id=card_id, column=column, actor_name=actor_name),
        )

    @server.tool(
        name="list_overdue_cards",
        description=_scoped_description(
            "List overdue cards from the current AutoStop CRM board. Archived cards are excluded by default."
        ),
        annotations=_read_tool_annotations("Overdue Cards"),
        structured_output=True,
    )
    def list_overdue_cards(include_archived: bool = False) -> JsonEnvelope:
        return _relay_board_call(
            "list_overdue_cards",
            lambda: board_api.list_overdue_cards(include_archived=include_archived),
        )

    registered_gateway_tools = register_agent_gateway_v2(
        server,
        board_api,
        connector_identity=connector_identity,
        agent_bearer_token=resolved_token,
    )
    if registered_gateway_tools:
        logger.info(
            "mcp.agent_gateway_v2 enabled tools=%s",
            ",".join(sorted(registered_gateway_tools)),
        )
    if oauth_enabled:
        security_schemes = [{"type": "oauth2", "scopes": list(DEFAULT_KANBAN_SCOPES)}]
        for tool in server._tool_manager.list_tools():
            tool.meta = {
                **(dict(tool.meta) if isinstance(tool.meta, dict) else {}),
                "securitySchemes": security_schemes,
            }

    return server
