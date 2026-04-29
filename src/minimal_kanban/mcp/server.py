from __future__ import annotations

import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from logging import Logger
from pathlib import Path
from time import perf_counter
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from ..config import (
    get_mcp_bearer_token,
    get_mcp_host,
    get_mcp_path,
    get_mcp_port,
    get_mcp_public_base_url,
)
from ..settings_models import derive_allowed_hosts, derive_allowed_origins
from .auth import build_auth_settings
from .client import BoardApiClient, BoardApiTransportError
from .oauth_provider import EmbeddedOAuthAuthorizationServerProvider
from ..services.snapshot_service import GPT_WALL_AGENT_EVENT_LIMIT


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

    days: int = Field(default=0, ge=0, le=365, description="Whole days in the deadline delta.")
    hours: int = Field(default=0, ge=0, le=23, description="Hours in the deadline delta.")
    minutes: int = Field(default=0, ge=0, le=59, description="Minutes in the deadline delta.")
    seconds: int = Field(default=0, ge=0, le=59, description="Seconds in the deadline delta.")
    total_seconds: int = Field(
        default=0,
        ge=0,
        le=31_536_000,
        description="Optional shorthand for the full deadline in seconds. Can be combined with days, hours, minutes, and seconds.",
    )


class StickyDeadlinePayload(DeadlinePayload):
    total_seconds: int = Field(default=0, ge=0, le=31_536_000)


class TagPayload(BaseModel):
    label: str = Field(min_length=1, max_length=24)
    color: Literal["green", "yellow", "red"] = "green"


class VehicleProfilePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    make_display: str | None = Field(default=None, max_length=120)
    model_display: str | None = Field(default=None, max_length=120)
    generation_or_platform: str | None = Field(default=None, max_length=120)
    production_year: int | None = Field(default=None, ge=1900, le=2100)
    customer_phone: str | None = Field(default=None, max_length=120)
    customer_phones: list[str] | None = Field(default=None, max_length=3)
    customer_name: str | None = Field(default=None, max_length=120)
    vin: str | None = Field(default=None, max_length=32)
    registration_plate: str | None = Field(default=None, max_length=120)
    pts_series: str | None = Field(default=None, max_length=120)
    pts_number: str | None = Field(default=None, max_length=120)
    sts_series: str | None = Field(default=None, max_length=120)
    sts_number: str | None = Field(default=None, max_length=120)
    body_number: str | None = Field(default=None, max_length=120)
    chassis_number: str | None = Field(default=None, max_length=120)
    engine_code: str | None = Field(default=None, max_length=120)
    engine_model: str | None = Field(default=None, max_length=120)
    engine_displacement_l: float | None = Field(default=None, ge=0, le=20)
    engine_power_hp: int | None = Field(default=None, ge=0, le=5000)
    gearbox_type: str | None = Field(default=None, max_length=120)
    gearbox_model: str | None = Field(default=None, max_length=120)
    drivetrain: str | None = Field(default=None, max_length=120)
    fuel_type: str | None = Field(default=None, max_length=120)
    oil_engine_capacity_l: float | None = Field(default=None, ge=0, le=30)
    oil_gearbox_capacity_l: float | None = Field(default=None, ge=0, le=30)
    coolant_capacity_l: float | None = Field(default=None, ge=0, le=50)
    steering_system_type: str | None = Field(default=None, max_length=120)
    brake_front_type: str | None = Field(default=None, max_length=120)
    brake_rear_type: str | None = Field(default=None, max_length=120)
    wheel_bolt_pattern: str | None = Field(default=None, max_length=120)
    oem_notes: str | None = Field(default=None, max_length=1200)
    source_summary: str | None = Field(default=None, max_length=120)
    source_confidence: float | None = Field(default=None, ge=0, le=1)
    source_links_or_refs: list[str] | None = None
    data_completion_state: (
        Literal[
            "manually_entered",
            "partially_autofilled",
            "mostly_autofilled",
            "verified",
        ]
        | None
    ) = None
    manual_fields: list[str] | None = None
    autofilled_fields: list[str] | None = None
    tentative_fields: list[str] | None = None
    field_sources: dict[str, str] | None = None
    raw_input_text: str | None = Field(default=None, max_length=6000)
    raw_image_text: str | None = Field(default=None, max_length=6000)
    image_parse_status: str | None = Field(default=None, max_length=120)
    warnings: list[str] | None = None


class RepairOrderRowPayload(BaseModel):
    name: str = Field(default="", max_length=240)
    quantity: str = Field(default="", max_length=40)
    price: str = Field(default="", max_length=40)
    total: str = Field(default="", max_length=40)


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
    number: str | None = Field(default=None, max_length=40)
    date: str | None = Field(default=None, max_length=32)
    status: Literal["open", "ready", "closed"] | None = None
    opened_at: str | None = Field(default=None, max_length=32)
    closed_at: str | None = Field(default=None, max_length=32)
    client: str | None = Field(default=None, max_length=160)
    phone: str | None = Field(default=None, max_length=160)
    vehicle: str | None = Field(default=None, max_length=160)
    license_plate: str | None = Field(default=None, max_length=160)
    vin: str | None = Field(default=None, max_length=160)
    mileage: str | None = Field(default=None, max_length=160)
    payment_method: Literal["cash", "cashless", "card"] | None = None
    prepayment: str | None = Field(default=None, max_length=40)
    payments: list[RepairOrderPaymentPayload] | None = None
    reason: str | None = Field(default=None, max_length=4000)
    client_information: str | None = Field(default=None, max_length=4000)
    note: str | None = Field(default=None, max_length=4000)
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


def _resolved_create_card_deadline(deadline: DeadlinePayload | None) -> dict[str, int]:
    if deadline is None:
        return {"days": 1, "hours": 0, "minutes": 0, "seconds": 0}
    payload = deadline.model_dump()
    if int(payload.get("total_seconds", 0) or 0) > 0:
        return payload
    if not any(
        int(payload.get(part, 0) or 0) > 0
        for part in ("days", "hours", "minutes", "seconds")
    ):
        return {"days": 1, "hours": 0, "minutes": 0, "seconds": 0}
    return payload


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
    if not url:
        return None
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _connector_name_from_url(url: str) -> str:
    host = (urlsplit(url).hostname or "local").strip().lower()
    sanitized = "".join(char if char.isalnum() else "-" for char in host).strip("-")
    sanitized = sanitized or "local"
    return f"minimal-kanban-this-board-only-{sanitized}"


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
        "This connector may operate only on the current Minimal Kanban board served by this exact MCP/API endpoint. "
        "Do not use it for Trello, YouGile, or any other kanban connector."
    )


def _scoped_description(summary: str) -> str:
    return f"{_external_product_text(summary)} {_single_board_rule_text()}"


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
    resource_url = (public_endpoint_url or "").strip().rstrip("/")
    server_base_url = (
        public_base_url
        or _base_url_from_endpoint(resource_url)
        or get_mcp_public_base_url()
        or f"http://{resolved_host}:{resolved_port}"
    ).rstrip("/")
    effective_resource_url = resource_url or f"{server_base_url}{resolved_path}"
    connector_name = _connector_name_from_url(effective_resource_url)
    connector_identity = {
        "connector_name": connector_name,
        "product_name": "AutoStop CRM",
        "board_name": "Current AutoStop CRM Board",
        "board_scope": "single_local_board_instance",
        "board_key": "minimal-kanban/current-local-board",
        "scope_rule": _single_board_rule_text(),
        "resource_url": effective_resource_url,
        "server_base_url": server_base_url,
        "streamable_http_path": resolved_path,
        "local_bind": f"http://{resolved_host}:{resolved_port}{resolved_path}",
        "board_api_base_url": board_api.base_url,
        "auth_mode": "oauth_embedded" if resolved_token else "none",
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
    if resolved_token:
        auth_settings = build_auth_settings(
            server_base_url, path=resolved_path, resource_url=effective_resource_url
        )
        auth_server_provider = EmbeddedOAuthAuthorizationServerProvider(
            issuer_url=server_base_url,
            resource_url=effective_resource_url,
            legacy_bearer_token=resolved_token,
            state_file=oauth_state_file,
            logger=logger,
        )

    server = FastMCP(
        name=connector_name,
        instructions=(
            _external_product_text(
                f"Minimal Kanban MCP connector for exactly one board instance at {effective_resource_url}. "
            )
            + " "
            f"{_single_board_rule_text()} "
            "Before any write operation, first call bootstrap_context. "
            "If bootstrap_context is unavailable, call get_connector_identity, then get_board_content, then get_board_events. "
            "If there is any doubt about tunnels, auth, or runtime state, call get_runtime_status. "
            "After the board and target are known, perform writes by card_id, sticky_id, and column id. "
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
        transport_security=transport_security,
        log_level="WARNING",
    )
    logger.info(
        "mcp.transport_security hosts=%s origins=%s",
        transport_security.allowed_hosts,
        transport_security.allowed_origins,
    )
    _try_register_autostop_manager_tools(server, logger)

    def _relay(tool_name: str, response: dict) -> JsonEnvelope:
        logger.info(
            "mcp_tool tool=%s ok=%s connector=%s resource_url=%s",
            tool_name,
            response.get("ok"),
            connector_identity["connector_name"],
            connector_identity["resource_url"],
        )
        return JsonEnvelope.model_validate(response)

    def _timed_meta(
        tool_name: str,
        started_at: float,
        *,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(meta or {})
        payload.setdefault("tool", tool_name)
        payload.setdefault("request_id", uuid4().hex)
        payload.setdefault("timestamp", datetime.now(UTC).isoformat())
        payload.setdefault("latency_ms", round(max(perf_counter() - started_at, 0.0) * 1000, 3))
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
            response = dict(fetcher())
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
        if transform is not None:
            response = transform(response)
        response["meta"] = _timed_meta(tool_name, started_at, meta=dict(response.get("meta") or {}))
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
        meta = dict(data.get("meta") or {})
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
        return _with_data_meta(
            {**response, "data": data},
            response_mode=response_mode,
            view_mode="compact" if compact else "full",
            include_archived=include_archived,
            compact=compact,
            returned=len(cards),
            has_more=bool((data.get("meta") or {}).get("has_more", False)),
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
            return fetcher()
        except BoardApiTransportError as exc:
            return _transport_error_response(error_code, exc)

    def _safe_health() -> dict[str, Any]:
        return _safe_board_call(board_api.health, error_code="board_api_unreachable")

    def _safe_board_context() -> dict[str, Any]:
        return _safe_board_call(board_api.get_board_context, error_code="board_context_unreachable")

    def _safe_gpt_wall(*, include_archived: bool, event_limit: int) -> dict[str, Any]:
        return _safe_board_call(
            lambda: board_api.get_gpt_wall(
                include_archived=include_archived, event_limit=event_limit
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
        lines.append(
            "recommended_bootstrap: bootstrap_context -> get_runtime_status -> writes"
        )
        return "\n".join(lines) + "\n"

    def _enrich_gpt_wall_response(response: dict[str, Any]) -> dict[str, Any]:
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

    def _extract_gpt_wall_section_response(
        response: dict[str, Any],
        *,
        section_key: str,
    ) -> dict[str, Any]:
        enriched = _enrich_gpt_wall_response(response)
        if not enriched.get("ok") or not isinstance(enriched.get("data"), dict):
            return enriched
        data = dict(enriched["data"])
        sections = data.get("sections") if isinstance(data.get("sections"), dict) else {}
        section = dict(sections.get(section_key) or {})
        if not section:
            if section_key == "board_content":
                section = {
                    "meta": dict(data.get("meta") or {}),
                    "text": str(data.get("text") or ""),
                    "cards": list(data.get("cards") or []),
                    "stickies": list(data.get("stickies") or []),
                    "board_context": data.get("board_context"),
                }
            else:
                section = {
                    "meta": dict(data.get("meta") or {}),
                    "text": "",
                    "events": list(data.get("events") or []),
                }
        section["connector_identity"] = dict(connector_identity)
        return {**enriched, "data": section}

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
            "next_step: call get_board_content for the full hidden machine wall card state in Markdown, get_board_events(event_limit=100) for the latest Markdown event journal, or get_gpt_wall for both sections"
        )
        return "\n".join(lines) + "\n"

    @server.tool(
        name="get_connector_identity",
        description=_scoped_description(
            "Return the hard identity of this MCP connector: name, resource_url, auth mode, and the rule that it manages only the current Minimal Kanban board."
        ),
        annotations=_read_tool_annotations("Connector Identity"),
        structured_output=True,
    )
    def get_connector_identity() -> ConnectorIdentityEnvelope:
        return _relay_identity_data(
            {
                "identity": dict(connector_identity),
                "text": _identity_text(),
            },
            meta={"tool": "get_connector_identity"},
        )

    @server.tool(
        name="ping_connector",
        description=_scoped_description(
            "Return the lightest possible connector ping. Use this first when you need to verify that ChatGPT can execute any Minimal Kanban MCP tool at all."
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
    def bootstrap_context(include_archived: bool = True, event_limit: int = 100) -> JsonEnvelope:
        started_at = perf_counter()
        wall_response = _safe_gpt_wall(include_archived=include_archived, event_limit=event_limit)
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
                            "event_limit": event_limit,
                        },
                        "response_mode": "summary_bootstrap",
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
                    "call get_board_content for the full Markdown state of all cards, including archived cards by default",
                    "call get_board_events(event_limit=100) for the newest-first Markdown journal of the latest board changes",
                    "call get_gpt_wall to return both hidden machine wall sections in one response",
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
                        "event_limit": event_limit,
                    },
                    "response_mode": "summary_bootstrap",
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
        description=_scoped_description("List all columns of the current Minimal Kanban board."),
        annotations=_read_tool_annotations("List Columns"),
        structured_output=True,
    )
    def list_columns() -> JsonEnvelope:
        return _relay_board_call("list_columns", board_api.list_columns)

    @server.tool(
        name="create_column",
        description=_scoped_description("Create a new column on the current Minimal Kanban board."),
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
            "Rename an existing column on the current Minimal Kanban board while keeping the same column id."
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
            "Delete an empty column from the current Minimal Kanban board. The last remaining column cannot be removed."
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
            "Create a sticky note on the current Minimal Kanban board. Sticky notes belong only to this board instance. "
            "The deadline accepts either days/hours/minutes/seconds or total_seconds."
        ),
        annotations=_write_tool_annotations("Create Sticky"),
        structured_output=True,
    )
    def create_sticky(
        text: str,
        deadline: StickyDeadlinePayload,
        x: int = 0,
        y: int = 0,
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
            "Return cards from the current Minimal Kanban board. Archived cards are excluded by default. "
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
            "Return one card by card_id from the current Minimal Kanban board, including the full vehicle_profile and the compact vehicle_profile_compact used by the 1.1 card layout."
        ),
        annotations=_read_tool_annotations("Get Card"),
        structured_output=True,
    )
    def get_card(card_id: str) -> JsonEnvelope:
        return _relay_board_call("get_card", lambda: board_api.get_card(card_id))

    @server.tool(
        name="list_card_attachments",
        description=_scoped_description(
            "List attachment metadata for one card from the current Minimal Kanban board without returning file bytes. Use this before reading any attached file."
        ),
        annotations=_read_tool_annotations("List Card Attachments"),
        structured_output=True,
    )
    def list_card_attachments(
        card_id: str, include_removed: bool = False
    ) -> JsonEnvelope:
        return _relay_board_call(
            "list_card_attachments",
            lambda: board_api.list_card_attachments(
                card_id, include_removed=include_removed
            ),
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
            "Return safe metadata for one card attachment from the current Minimal Kanban board, including content kind, size, hash, and download path, but not file bytes."
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
        max_chars: int = 12_000,
        include_base64: bool = False,
        max_base64_bytes: int = 1_048_576,
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
        name="get_card_context",
        description=_scoped_description(
            "Return the focused operational context of one card from the current Minimal Kanban board: card data, recent card events, attachment summaries, board context, and repair-order text when available. "
            "Use view_mode=agent for the default GPT workflow and view_mode=full when a human-style full read is needed."
        ),
        annotations=_read_tool_annotations("Card Context"),
        structured_output=True,
    )
    def get_card_context(
        card_id: str,
        event_limit: int = 20,
        include_repair_order_text: bool = True,
        view_mode: Literal["agent", "full"] = "agent",
    ) -> JsonEnvelope:
        return _relay_board_call(
            "get_card_context",
            lambda: board_api.get_card_context(
                card_id,
                event_limit=event_limit,
                include_repair_order_text=include_repair_order_text,
            ),
            params={
                "card_id": card_id,
                "event_limit": event_limit,
                "include_repair_order_text": include_repair_order_text,
                "view_mode": view_mode,
            },
            transform=lambda response: _with_text_section_meta(
                response,
                response_mode="agent_context" if view_mode == "agent" else "full",
                view_mode=view_mode,
                extra={
                    "event_limit": event_limit,
                    "include_repair_order_text": include_repair_order_text,
                },
            ),
        )

    @server.tool(
        name="get_board_snapshot",
        description=_scoped_description(
            "Return a structured snapshot of the current Minimal Kanban board: columns, active cards, archived tail, stickies, and settings. "
            "Cards in the snapshot include vehicle_profile_compact for the 1.1 vehicle card view. "
            "Use compact=true for lighter GPT scans and include_archive=false when the archived tail is not needed."
        ),
        annotations=_read_tool_annotations("Board Snapshot"),
        structured_output=True,
    )
    def get_board_snapshot(
        archive_limit: int = 10,
        compact: bool = False,
        include_archive: bool = True,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "get_board_snapshot",
            lambda: board_api.get_board_snapshot(
                archive_limit=archive_limit,
                compact=compact,
                include_archive=include_archive,
            ),
            params={
                "archive_limit": archive_limit,
                "compact": compact,
                "include_archive": include_archive,
            },
            transform=lambda response: _with_data_meta(
                response,
                response_mode="snapshot",
                view_mode="compact" if compact else "full",
                archive_limit=archive_limit,
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
            "Return an operational board review for the current Minimal Kanban board: summary counts, per-column load, manager alerts, priority cards, and recent important events."
        ),
        annotations=_read_tool_annotations("Board Review"),
        structured_output=True,
    )
    def review_board(
        stale_hours: int = 48,
        overload_threshold: int = 5,
        priority_limit: int = 5,
        recent_event_limit: int = 10,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "review_board",
            lambda: board_api.review_board(
                stale_hours=stale_hours,
                overload_threshold=overload_threshold,
                priority_limit=priority_limit,
                recent_event_limit=recent_event_limit,
            ),
            error_code="review_board_unreachable",
        )

    @server.tool(
        name="list_cashboxes",
        description=_scoped_description(
            "List all cashboxes of the current AutoStop CRM board instance with compact balance statistics."
        ),
        annotations=_read_tool_annotations("List Cashboxes"),
        structured_output=True,
    )
    def list_cashboxes(limit: int = 200) -> JsonEnvelope:
        return _relay_board_call(
            "list_cashboxes",
            lambda: board_api.list_cashboxes(limit=limit),
            error_code="cashboxes_unreachable",
            params={"limit": limit},
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
    def get_cash_journal(months: int = 3, limit: int = 5000) -> JsonEnvelope:
        return _relay_board_call(
            "get_cash_journal",
            lambda: board_api.get_cash_journal(months=months, limit=limit),
            error_code="cash_journal_unreachable",
            params={"months": months, "limit": limit},
        )

    @server.tool(
        name="get_cashbox",
        description=_scoped_description(
            "Return one cashbox with its statistics and transaction journal."
        ),
        annotations=_read_tool_annotations("Get Cashbox"),
        structured_output=True,
    )
    def get_cashbox(cashbox_id: str, transaction_limit: int = 300) -> JsonEnvelope:
        return _relay_board_call(
            "get_cashbox",
            lambda: board_api.get_cashbox(cashbox_id, transaction_limit=transaction_limit),
            error_code="cashbox_unreachable",
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
        description=_scoped_description(
            "Delete an empty cashbox from the current board instance."
        ),
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
            "Create one cash transaction in a cashbox. Use direction income or expense and pass either amount_minor or amount."
        ),
        annotations=_write_tool_annotations("Create Cash Transaction"),
        structured_output=True,
    )
    def create_cash_transaction(
        cashbox_id: str,
        direction: Literal["income", "expense"],
        amount_minor: int | None = None,
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
        name="update_board_settings",
        description=_scoped_description(
            "Update board-wide settings for the current Minimal Kanban board. Currently supports board_scale."
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
            "Return the hidden machine wall board-content section as Markdown for the current Minimal Kanban board: columns, card content, archived card content by default, sticky notes, compact vehicle profiles, and board context, without the event journal. "
            "Use view_mode=agent for a lighter GPT-oriented read; that mode keeps cards compact and caps the recent wall slice. Use view_mode=full for a broader export-style dump."
        ),
        annotations=_read_tool_annotations("Board Content"),
        structured_output=True,
    )
    def get_board_content(
        include_archived: bool = True,
        view_mode: Literal["agent", "full"] = "agent",
    ) -> JsonEnvelope:
        wall_event_limit = (
            GPT_WALL_AGENT_EVENT_LIMIT if view_mode == "agent" else 100
        )
        return _relay_board_call(
            "get_board_content",
            lambda: _extract_gpt_wall_section_response(
                board_api.get_gpt_wall(
                    include_archived=include_archived,
                    event_limit=wall_event_limit,
                    compact=view_mode == "agent",
                ),
                section_key="board_content",
            ),
            error_code="board_content_unreachable",
            params={
                "include_archived": include_archived,
                "view_mode": view_mode,
                "event_limit": wall_event_limit,
            },
            transform=lambda response: _with_text_section_meta(
                response,
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
            "Return the hidden machine wall event-log section as Markdown for the current Minimal Kanban board: newest-first events, what happened, when, by whom, and which card it affected when available. "
            "The default event_limit is 100. Use include_archived to control whether archived-card events stay in the journal slice."
        ),
        annotations=_read_tool_annotations("Board Events"),
        structured_output=True,
    )
    def get_board_events(
        event_limit: int = 100,
        include_archived: bool = True,
        view_mode: Literal["audit", "full"] = "audit",
    ) -> JsonEnvelope:
        return _relay_board_call(
            "get_board_events",
            lambda: _extract_gpt_wall_section_response(
                board_api.get_gpt_wall(include_archived=include_archived, event_limit=event_limit),
                section_key="event_log",
            ),
            error_code="board_events_unreachable",
            params={
                "event_limit": event_limit,
                "include_archived": include_archived,
                "view_mode": view_mode,
            },
            transform=lambda response: _with_text_section_meta(
                response,
                response_mode="audit",
                view_mode=view_mode,
                extra={
                    "event_limit": event_limit,
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
            "Return the hidden machine wall aggregate for the current Minimal Kanban board as Markdown: full card text, structured board state, newest-first recent events, compact 1.1 vehicle profile summaries for each card, and separated board_content / event_log sections. "
            "Use view_mode=agent for the normal GPT context flow; that mode keeps cards compact and the event slice short. Use view_mode=full for wide diagnostics or exports."
        ),
        annotations=_read_tool_annotations("GPT Wall"),
        structured_output=True,
    )
    def get_gpt_wall(
        include_archived: bool = True,
        event_limit: int = 100,
        view_mode: Literal["agent", "full"] = "agent",
    ) -> JsonEnvelope:
        compact_cards = view_mode == "agent"
        effective_event_limit = (
            min(max(1, event_limit), GPT_WALL_AGENT_EVENT_LIMIT)
            if compact_cards
            else event_limit
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
            "Return the audit log of one card from the current Minimal Kanban board. Use limit to keep the journal slice compact for GPT."
        ),
        annotations=_read_tool_annotations("Card Log"),
        structured_output=True,
    )
    def get_card_log(
        card_id: str,
        limit: int | None = None,
        view_mode: Literal["audit", "full"] = "audit",
    ) -> JsonEnvelope:
        return _relay_board_call(
            "get_card_log",
            lambda: board_api.get_card_log(card_id, limit=limit),
            params={"card_id": card_id, "limit": limit, "view_mode": view_mode},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="audit",
                view_mode=view_mode,
                text_encoding="utf-8",
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
    def list_clients(limit: int = 100, include_stats: bool = True) -> JsonEnvelope:
        return _relay_board_call(
            "list_clients",
            lambda: board_api.list_clients(limit=limit, include_stats=include_stats),
            params={"limit": limit, "include_stats": include_stats},
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
    def search_clients(query: str = "", limit: int = 10) -> JsonEnvelope:
        return _relay_board_call(
            "search_clients",
            lambda: board_api.search_clients(query=query, limit=limit),
            params={"query": query, "limit": limit},
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
    def get_client(client_id: str, order_limit: int = 30) -> JsonEnvelope:
        return _relay_board_call(
            "get_client",
            lambda: board_api.get_client(client_id, order_limit=order_limit),
            params={"client_id": client_id, "order_limit": order_limit},
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
        card_id: str, query: str | None = None, limit: int = 8
    ) -> JsonEnvelope:
        return _relay_board_call(
            "suggest_clients_for_card",
            lambda: board_api.suggest_clients_for_card(card_id, query=query, limit=limit),
            params={"card_id": card_id, "query": query, "limit": limit},
            transform=lambda response: _with_data_meta(
                response,
                response_mode="client_suggestions",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="list_repair_orders",
        description=_scoped_description(
            "List repair orders from the current Minimal Kanban board with status filtering, search, sorting, card links, client, vehicle, and text-file metadata."
        ),
        annotations=_read_tool_annotations("List Repair Orders"),
        structured_output=True,
    )
    def list_repair_orders(
        limit: int = 50,
        status: Literal["open", "ready", "closed", "all"] = "open",
        query: str | None = None,
        sort_by: Literal["number", "opened_at", "closed_at"] | None = None,
        sort_dir: Literal["asc", "desc"] | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "list_repair_orders",
            lambda: board_api.list_repair_orders(
                limit=limit,
                status=status,
                query=query,
                sort_by=sort_by,
                sort_dir=sort_dir,
            ),
            params={
                "limit": limit,
                "status": status,
                "query": query,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
            },
            transform=lambda response: _with_data_meta(
                response,
                response_mode="list",
                view_mode="compact",
            ),
        )

    @server.tool(
        name="get_repair_order",
        description=_scoped_description(
            "Return the structured repair order of one card from the current Minimal Kanban board."
        ),
        annotations=_read_tool_annotations("Get Repair Order"),
        structured_output=True,
    )
    def get_repair_order(card_id: str) -> JsonEnvelope:
        return _relay_board_call("get_repair_order", lambda: board_api.get_repair_order(card_id))

    @server.tool(
        name="get_repair_order_text",
        description=_scoped_description(
            "Return the text rendering of one repair order from the current Minimal Kanban board together with file metadata."
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
        name="list_archived_cards",
        description=_scoped_description(
            "List archived cards from the current Minimal Kanban board."
        ),
        annotations=_read_tool_annotations("Archived Cards"),
        structured_output=True,
    )
    def list_archived_cards(limit: int = 10, compact: bool = False) -> JsonEnvelope:
        return _relay_board_call(
            "list_archived_cards",
            lambda: board_api.list_archived_cards(limit=limit, compact=compact),
            params={"limit": limit, "compact": compact},
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
            "Search cards only inside the current Minimal Kanban board using query and optional filters such as column, tag, indicator, and status."
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
        limit: int = 20,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "search_cards",
            lambda: board_api.search_cards(
                query=query,
                include_archived=include_archived,
                column=column,
                tag=tag,
                indicator=indicator,
                status=status,
                limit=limit,
            ),
            params={
                "query": query,
                "include_archived": include_archived,
                "column": column,
                "tag": tag,
                "indicator": indicator,
                "status": status,
                "limit": limit,
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
            "Create a card on the current Minimal Kanban board with vehicle, title, description, optional tags, optional target column, optional vehicle_profile, and a deadline. "
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
        tags: list[TagPayload] | None = None,
        vehicle_profile: VehicleProfilePayload | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "create_card",
            lambda: board_api.create_card(
                vehicle=vehicle,
                title=title,
                description=description,
                column=column,
                tags=[tag.model_dump() for tag in tags] if tags is not None else None,
                deadline=_resolved_create_card_deadline(deadline),
                vehicle_profile=vehicle_profile.model_dump(exclude_none=True)
                if vehicle_profile is not None
                else None,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="update_card",
        description=_scoped_description(
            "Update an existing card on the current Minimal Kanban board. Supported fields: vehicle, title, description, tags, deadline, and vehicle_profile. "
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
        tags: list[TagPayload] | None = None,
        deadline: DeadlinePayload | None = None,
        vehicle_profile: VehicleProfilePayload | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "update_card",
            lambda: board_api.update_card(
                card_id=card_id,
                vehicle=vehicle,
                title=title,
                description=description,
                tags=[tag.model_dump() for tag in tags] if tags is not None else None,
                deadline=deadline.model_dump() if deadline is not None else None,
                vehicle_profile=vehicle_profile.model_dump(exclude_none=True)
                if vehicle_profile is not None
                else None,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="update_repair_order",
        description=_scoped_description(
            "Patch the structured repair order of one card on the current Minimal Kanban board. Pass a JSON object with only the fields to change; unspecified fields remain unchanged."
        ),
        annotations=_write_tool_annotations("Update Repair Order"),
        structured_output=True,
    )
    def update_repair_order(
        card_id: str,
        repair_order: dict[str, Any],
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        repair_order_payload = RepairOrderPatchPayload.model_validate(repair_order)
        return _relay_board_call(
            "update_repair_order",
            lambda: board_api.update_repair_order(
                card_id=card_id,
                repair_order=repair_order_payload.model_dump(exclude_none=True),
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="set_repair_order_status",
        description=_scoped_description(
            "Set the status of one repair order on the current Minimal Kanban board. Use open for active orders, ready for completed vehicles waiting for handoff/payment, and closed for archived orders."
        ),
        annotations=_write_tool_annotations("Set Repair Order Status"),
        structured_output=True,
    )
    def set_repair_order_status(
        card_id: str,
        status: Literal["open", "ready", "closed"],
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay_board_call(
            "set_repair_order_status",
            lambda: board_api.set_repair_order_status(
                card_id=card_id, status=status, actor_name=actor_name
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
            "Replace the full Works table of a repair order on the current Minimal Kanban board."
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
            "Replace the full Materials table of a repair order on the current Minimal Kanban board."
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
            "Update the text or deadline of a sticky note on the current Minimal Kanban board. "
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
            "Move a sticky note on the current Minimal Kanban board to a new x/y position."
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
            "Delete a sticky note from the current Minimal Kanban board."
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
            "Change only the deadline of a card on the current Minimal Kanban board. "
            "The deadline accepts either days/hours/minutes/seconds or total_seconds."
        ),
        annotations=_write_tool_annotations("Set Card Deadline"),
        structured_output=True,
    )
    def set_card_deadline(
        card_id: str, deadline: DeadlinePayload, actor_name: str | None = None
    ) -> JsonEnvelope:
        return _relay_board_call(
            "set_card_deadline",
            lambda: board_api.set_card_deadline(
                card_id=card_id, deadline=deadline.model_dump(), actor_name=actor_name
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
    ) -> JsonEnvelope:
        return _relay_board_call(
            "set_card_indicator",
            lambda: board_api.set_card_indicator(
                card_id=card_id, indicator=indicator, actor_name=actor_name
            ),
        )

    @server.tool(
        name="move_card",
        description=_scoped_description(
            "Move a card on the current Minimal Kanban board using the target column id. "
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
            "Move multiple cards to one target column on the current Minimal Kanban board in a single write call. Prefer this over long chains of sequential move_card calls."
        ),
        annotations=_write_tool_annotations("Bulk Move Cards", idempotent=True),
        structured_output=True,
    )
    def bulk_move_cards(
        card_ids: list[str], column: str, actor_name: str | None = None
    ) -> JsonEnvelope:
        return _relay_board_call(
            "bulk_move_cards",
            lambda: board_api.bulk_move_cards(
                card_ids=card_ids, column=column, actor_name=actor_name
            ),
        )

    @server.tool(
        name="archive_card",
        description=_scoped_description("Archive a card on the current Minimal Kanban board."),
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
            "Restore an archived card back onto the current Minimal Kanban board."
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
            "List overdue cards from the current Minimal Kanban board. Archived cards are excluded by default."
        ),
        annotations=_read_tool_annotations("Overdue Cards"),
        structured_output=True,
    )
    def list_overdue_cards(include_archived: bool = False) -> JsonEnvelope:
        return _relay_board_call(
            "list_overdue_cards",
            lambda: board_api.list_overdue_cards(include_archived=include_archived),
        )

    return server
