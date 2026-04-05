from __future__ import annotations

from logging import Logger
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ..config import get_mcp_bearer_token, get_mcp_host, get_mcp_path, get_mcp_port, get_mcp_public_base_url
from ..settings_models import derive_allowed_hosts, derive_allowed_origins
from .auth import build_auth_settings
from .client import BoardApiClient, BoardApiTransportError
from .oauth_provider import EmbeddedOAuthAuthorizationServerProvider


class DeadlinePayload(BaseModel):
    days: int = Field(default=0, ge=0, le=365)
    hours: int = Field(default=0, ge=0, le=23)
    minutes: int = Field(default=0, ge=0, le=59)
    seconds: int = Field(default=0, ge=0, le=59)


class JsonEnvelope(BaseModel):
    ok: bool
    data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None


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


def _single_board_rule_text() -> str:
    return (
        "This connector may operate only on the current Minimal Kanban board served by this exact MCP/API endpoint. "
        "Do not use it for Trello, YouGile, or any other kanban connector."
    )


def _scoped_description(summary: str) -> str:
    return f"{summary} {_single_board_rule_text()}"


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
        "product_name": "Minimal Kanban",
        "board_name": "Current Minimal Kanban Board",
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
        auth_settings = build_auth_settings(server_base_url, path=resolved_path, resource_url=effective_resource_url)
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
            f"Minimal Kanban MCP connector for exactly one board instance at {effective_resource_url}. "
            f"{_single_board_rule_text()} "
            "Before any write operation, first call bootstrap_context. "
            "If bootstrap_context is unavailable, call get_connector_identity, then get_board_context, then get_gpt_wall. "
            "If there is any doubt about tunnels, auth, or runtime state, call get_runtime_status. "
            "After the board and target are confirmed, perform writes strictly by card_id, sticky_id, and column id. "
            "If the user asks about some other kanban product or board, do not use this connector."
        ),
        host=resolved_host,
        port=resolved_port,
        streamable_http_path=resolved_path,
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

    def _relay(tool_name: str, response: dict) -> JsonEnvelope:
        logger.info(
            "mcp_tool tool=%s ok=%s connector=%s resource_url=%s",
            tool_name,
            response.get("ok"),
            connector_identity["connector_name"],
            connector_identity["resource_url"],
        )
        return JsonEnvelope.model_validate(response)

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

    def _safe_health() -> dict[str, Any]:
        try:
            return board_api.health()
        except BoardApiTransportError as exc:
            return {
                "ok": False,
                "error": {
                    "code": "board_api_unreachable",
                    "message": str(exc),
                },
            }

    def _safe_board_context() -> dict[str, Any]:
        try:
            return board_api.get_board_context()
        except BoardApiTransportError as exc:
            return {
                "ok": False,
                "error": {
                    "code": "board_context_unreachable",
                    "message": str(exc),
                },
            }

    def _safe_gpt_wall(*, include_archived: bool, event_limit: int) -> dict[str, Any]:
        try:
            return board_api.get_gpt_wall(include_archived=include_archived, event_limit=event_limit)
        except BoardApiTransportError as exc:
            return {
                "ok": False,
                "error": {
                    "code": "gpt_wall_unreachable",
                    "message": str(exc),
                },
            }

    def _runtime_status_payload() -> dict[str, Any]:
        health_response = _safe_health()
        board_context_response = _safe_board_context()
        runtime_status = {
            "connector_identity": dict(connector_identity),
            "preferred_bootstrap_tools": list(preferred_bootstrap_tools),
            "api_health": health_response.get("data") if health_response.get("ok") else None,
            "api_health_error": health_response.get("error") if not health_response.get("ok") else None,
            "board_context": board_context_response.get("data") if board_context_response.get("ok") else None,
            "board_context_error": board_context_response.get("error") if not board_context_response.get("ok") else None,
            "resource_visibility": "public_https" if connector_identity["resource_url"].startswith("https://") else "local_only",
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
        lines.append(
            "recommended_bootstrap: bootstrap_context -> get_runtime_status (only if diagnostics are needed) -> writes"
        )
        return "\n".join(lines) + "\n"

    def _bootstrap_wall_preview(wall_data: dict[str, Any]) -> dict[str, Any]:
        cards = wall_data.get("cards") if isinstance(wall_data.get("cards"), list) else []
        events = wall_data.get("events") if isinstance(wall_data.get("events"), list) else []
        stickies = wall_data.get("stickies") if isinstance(wall_data.get("stickies"), list) else []
        preview_cards: list[dict[str, Any]] = []
        attention_cards: list[dict[str, Any]] = []
        for card in cards[:8]:
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
            if card.get("status") in {"warning", "critical", "expired"} or card.get("indicator") in {"yellow", "red"}:
                attention_cards.append(preview)

        preview_events: list[dict[str, Any]] = []
        for event in events[:12]:
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
        for sticky in stickies[:5]:
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
            "attention_cards": attention_cards[:5],
            "events_preview": preview_events,
            "stickies_preview": preview_stickies,
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
        attention_cards = wall_preview.get("attention_cards") if isinstance(wall_preview.get("attention_cards"), list) else []
        if attention_cards:
            lines.append("attention_cards:")
            for card in attention_cards[:5]:
                lines.append(
                    f"- {card.get('short_id') or card.get('id')}: {card.get('vehicle') or '-'} / {card.get('title') or '-'} | {card.get('column_label') or card.get('column') or '-'} | {card.get('status') or '-'} | {card.get('indicator') or '-'}"
                )
        preview_events = wall_preview.get("events_preview") if isinstance(wall_preview.get("events_preview"), list) else []
        if preview_events:
            lines.append("recent_events:")
            for event in preview_events[:8]:
                lines.append(
                    f"- {event.get('timestamp') or '-'} | {event.get('actor_name') or '-'} | {event.get('card_short_id') or '-'} | {event.get('message') or '-'}"
                )
        lines.append("next_step: if full card text is needed, call get_gpt_wall explicitly")
        return "\n".join(lines) + "\n"

    @server.tool(
        name="get_connector_identity",
        description=_scoped_description(
            "Return the hard identity of this MCP connector: name, resource_url, auth mode, and the rule that it manages only the current Minimal Kanban board."
        ),
        annotations=_read_tool_annotations("Connector Identity"),
        structured_output=True,
    )
    def get_connector_identity() -> JsonEnvelope:
        return _relay(
            "get_connector_identity",
            {
                "ok": True,
                "data": {
                    "identity": dict(connector_identity),
                    "text": _identity_text(),
                },
                "error": None,
                "meta": {"tool": "get_connector_identity"},
            },
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
        return _relay(
            "ping_connector",
            {
                "ok": True,
                "data": {
                    "connector_name": connector_identity["connector_name"],
                    "resource_url": connector_identity["resource_url"],
                    "board_scope": connector_identity["board_scope"],
                    "message": "pong",
                    "text": (
                        "[CONNECTOR PING]\n"
                        f"connector_name: {connector_identity['connector_name']}\n"
                        f"resource_url: {connector_identity['resource_url']}\n"
                        "message: pong\n"
                    ),
                },
            },
        )

    @server.tool(
        name="bootstrap_context",
        description=_scoped_description(
            "Return the recommended lightweight startup bundle for GPT: connector identity, board context, a compact wall preview, and the preferred write flow for this board."
        ),
        annotations=_read_tool_annotations("Bootstrap Context"),
        structured_output=True,
    )
    def bootstrap_context(include_archived: bool = False, event_limit: int = 25) -> JsonEnvelope:
        wall_response = _safe_gpt_wall(include_archived=include_archived, event_limit=event_limit)
        board_context_response = _safe_board_context()
        if not wall_response.get("ok"):
            error = dict(wall_response.get("error") or {})
            error.setdefault("code", "bootstrap_failed")
            error.setdefault("message", "bootstrap_context failed while reading the GPT wall.")
            return _relay(
                "bootstrap_context",
                {
                    "ok": False,
                    "data": {
                        "identity": dict(connector_identity),
                        "preferred_bootstrap_tools": list(preferred_bootstrap_tools),
                    },
                    "error": error,
                    "meta": {"tool": "bootstrap_context"},
                },
            )

        board_context_payload = None
        if board_context_response.get("ok") and isinstance(board_context_response.get("data"), dict):
            board_context_payload = dict(board_context_response["data"])
        elif isinstance(wall_response.get("data"), dict) and isinstance(wall_response["data"].get("board_context"), dict):
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

        return _relay(
            "bootstrap_context",
            {
                "ok": True,
                "data": {
                    "identity": dict(connector_identity),
                    "board_context": board_context_payload,
                    "gpt_wall_preview": wall_preview,
                    "preferred_bootstrap_tools": list(preferred_bootstrap_tools),
                    "recommended_write_flow": [
                        "bootstrap_context",
                        "confirm board_name and scope_rule",
                        "call get_gpt_wall only when full card text or long event history is needed",
                        "for mass column migrations prefer bulk_move_cards over many sequential move_card calls",
                        "perform write tools by card_id, sticky_id, and column id only",
                    ],
                    "text": bootstrap_text,
                },
                "error": None,
                "meta": {"tool": "bootstrap_context"},
            },
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
        runtime_status = _runtime_status_payload()
        return _relay(
            "get_runtime_status",
            {
                "ok": True,
                "data": {
                    "runtime_status": runtime_status,
                    "text": _runtime_status_text(runtime_status),
                },
                "error": None,
                "meta": {"tool": "get_runtime_status"},
            },
        )

    @server.tool(
        name="list_columns",
        description=_scoped_description("List all columns of the current Minimal Kanban board."),
        annotations=_read_tool_annotations("List Columns"),
        structured_output=True,
    )
    def list_columns() -> JsonEnvelope:
        return _relay("list_columns", board_api.list_columns())

    @server.tool(
        name="create_column",
        description=_scoped_description("Create a new column on the current Minimal Kanban board."),
        annotations=_write_tool_annotations("Create Column"),
        structured_output=True,
    )
    def create_column(label: str, actor_name: str | None = None) -> JsonEnvelope:
        return _relay("create_column", board_api.create_column(label, actor_name=actor_name))

    @server.tool(
        name="create_sticky",
        description=_scoped_description(
            "Create a sticky note on the current Minimal Kanban board. Sticky notes belong only to this board instance."
        ),
        annotations=_write_tool_annotations("Create Sticky"),
        structured_output=True,
    )
    def create_sticky(
        text: str,
        deadline: DeadlinePayload,
        x: int = 0,
        y: int = 0,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay(
            "create_sticky",
            board_api.create_sticky(text=text, x=x, y=y, deadline=deadline.model_dump(), actor_name=actor_name),
        )

    @server.tool(
        name="get_cards",
        description=_scoped_description("Return cards from the current Minimal Kanban board. Archived cards are excluded by default."),
        annotations=_read_tool_annotations("List Cards"),
        structured_output=True,
    )
    def get_cards(include_archived: bool = False) -> JsonEnvelope:
        return _relay("get_cards", board_api.get_cards(include_archived=include_archived))

    @server.tool(
        name="get_card",
        description=_scoped_description("Return one card by card_id from the current Minimal Kanban board."),
        annotations=_read_tool_annotations("Get Card"),
        structured_output=True,
    )
    def get_card(card_id: str) -> JsonEnvelope:
        return _relay("get_card", board_api.get_card(card_id))

    @server.tool(
        name="get_board_snapshot",
        description=_scoped_description(
            "Return a structured snapshot of the current Minimal Kanban board: columns, active cards, archived tail, stickies, and settings."
        ),
        annotations=_read_tool_annotations("Board Snapshot"),
        structured_output=True,
    )
    def get_board_snapshot(archive_limit: int = 10) -> JsonEnvelope:
        return _relay("get_board_snapshot", board_api.get_board_snapshot(archive_limit=archive_limit))

    @server.tool(
        name="get_board_context",
        description=_scoped_description(
            "Return the board context for this connector only: board name, scope, allowed columns, counts, and a scope rule. Call this before write operations."
        ),
        annotations=_read_tool_annotations("Board Context"),
        structured_output=True,
    )
    def get_board_context() -> JsonEnvelope:
        return _relay("get_board_context", board_api.get_board_context())

    @server.tool(
        name="update_board_settings",
        description=_scoped_description("Update board-wide settings for the current Minimal Kanban board. Currently supports board_scale."),
        annotations=_write_tool_annotations("Update Board Settings"),
        structured_output=True,
    )
    def update_board_settings(board_scale: float, actor_name: str | None = None) -> JsonEnvelope:
        return _relay(
            "update_board_settings",
            board_api.update_board_settings(board_scale=board_scale, actor_name=actor_name),
        )

    @server.tool(
        name="get_gpt_wall",
        description=_scoped_description(
            "Return the hidden GPT wall for the current Minimal Kanban board: full card text, structured board state, and recent events."
        ),
        annotations=_read_tool_annotations("GPT Wall"),
        structured_output=True,
    )
    def get_gpt_wall(include_archived: bool = True, event_limit: int = 100) -> JsonEnvelope:
        response = board_api.get_gpt_wall(include_archived=include_archived, event_limit=event_limit)
        if response.get("ok") and isinstance(response.get("data"), dict):
            data = dict(response["data"])
            board_context_payload = data.get("board_context")
            if not isinstance(board_context_payload, dict):
                context_response = board_api.get_board_context()
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
            response = {**response, "data": data}
        return _relay("get_gpt_wall", response)

    @server.tool(
        name="get_card_log",
        description=_scoped_description("Return the audit log of one card from the current Minimal Kanban board."),
        annotations=_read_tool_annotations("Card Log"),
        structured_output=True,
    )
    def get_card_log(card_id: str) -> JsonEnvelope:
        return _relay("get_card_log", board_api.get_card_log(card_id))

    @server.tool(
        name="list_archived_cards",
        description=_scoped_description("List archived cards from the current Minimal Kanban board."),
        annotations=_read_tool_annotations("Archived Cards"),
        structured_output=True,
    )
    def list_archived_cards(limit: int = 10) -> JsonEnvelope:
        return _relay("list_archived_cards", board_api.list_archived_cards(limit=limit))

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
        return _relay(
            "search_cards",
            board_api.search_cards(
                query=query,
                include_archived=include_archived,
                column=column,
                tag=tag,
                indicator=indicator,
                status=status,
                limit=limit,
            ),
        )

    @server.tool(
        name="autofill_vehicle_data",
        description=_scoped_description(
            "Parse vehicle data from free text and/or an uploaded image payload, enrich missing fields from reliable references, and return a normalized vehicle profile draft for the current Minimal Kanban board."
        ),
        annotations=_read_tool_annotations("Autofill Vehicle Data"),
        structured_output=True,
    )
    def autofill_vehicle_data(
        raw_text: str = "",
        image_base64: str | None = None,
        image_filename: str | None = None,
        image_mime_type: str | None = None,
        vehicle_profile: dict[str, Any] | None = None,
        vehicle: str | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> JsonEnvelope:
        return _relay(
            "autofill_vehicle_data",
            board_api.autofill_vehicle_data(
                raw_text=raw_text,
                image_base64=image_base64,
                image_filename=image_filename,
                image_mime_type=image_mime_type,
                vehicle_profile=vehicle_profile,
                vehicle=vehicle,
                title=title,
                description=description,
            ),
        )

    @server.tool(
        name="create_card",
        description=_scoped_description(
            "Create a card on the current Minimal Kanban board with vehicle, title, description, optional tags, optional target column, optional vehicle_profile, and a deadline."
        ),
        annotations=_write_tool_annotations("Create Card"),
        structured_output=True,
    )
    def create_card(
        title: str,
        deadline: DeadlinePayload,
        vehicle: str = "",
        description: str = "",
        column: str | None = None,
        tags: list[str] | None = None,
        vehicle_profile: dict[str, Any] | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay(
            "create_card",
            board_api.create_card(
                vehicle=vehicle,
                title=title,
                description=description,
                column=column,
                tags=tags,
                deadline=deadline.model_dump(),
                vehicle_profile=vehicle_profile,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="update_card",
        description=_scoped_description(
            "Update an existing card on the current Minimal Kanban board. Supported fields: vehicle, title, description, tags, deadline, and vehicle_profile."
        ),
        annotations=_write_tool_annotations("Update Card"),
        structured_output=True,
    )
    def update_card(
        card_id: str,
        vehicle: str | None = None,
        title: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        deadline: DeadlinePayload | None = None,
        vehicle_profile: dict[str, Any] | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay(
            "update_card",
            board_api.update_card(
                card_id=card_id,
                vehicle=vehicle,
                title=title,
                description=description,
                tags=tags,
                deadline=deadline.model_dump() if deadline is not None else None,
                vehicle_profile=vehicle_profile,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="update_sticky",
        description=_scoped_description("Update the text or deadline of a sticky note on the current Minimal Kanban board."),
        annotations=_write_tool_annotations("Update Sticky"),
        structured_output=True,
    )
    def update_sticky(
        sticky_id: str,
        text: str | None = None,
        deadline: DeadlinePayload | None = None,
        actor_name: str | None = None,
    ) -> JsonEnvelope:
        return _relay(
            "update_sticky",
            board_api.update_sticky(
                sticky_id=sticky_id,
                text=text,
                deadline=deadline.model_dump() if deadline is not None else None,
                actor_name=actor_name,
            ),
        )

    @server.tool(
        name="move_sticky",
        description=_scoped_description("Move a sticky note on the current Minimal Kanban board to a new x/y position."),
        annotations=_write_tool_annotations("Move Sticky"),
        structured_output=True,
    )
    def move_sticky(sticky_id: str, x: int, y: int, actor_name: str | None = None) -> JsonEnvelope:
        return _relay("move_sticky", board_api.move_sticky(sticky_id=sticky_id, x=x, y=y, actor_name=actor_name))

    @server.tool(
        name="delete_sticky",
        description=_scoped_description("Delete a sticky note from the current Minimal Kanban board."),
        annotations=_write_tool_annotations("Delete Sticky", destructive=True),
        structured_output=True,
    )
    def delete_sticky(sticky_id: str, actor_name: str | None = None) -> JsonEnvelope:
        return _relay("delete_sticky", board_api.delete_sticky(sticky_id=sticky_id, actor_name=actor_name))

    @server.tool(
        name="set_card_deadline",
        description=_scoped_description("Change only the deadline of a card on the current Minimal Kanban board."),
        annotations=_write_tool_annotations("Set Card Deadline"),
        structured_output=True,
    )
    def set_card_deadline(card_id: str, deadline: DeadlinePayload, actor_name: str | None = None) -> JsonEnvelope:
        return _relay(
            "set_card_deadline",
            board_api.set_card_deadline(card_id=card_id, deadline=deadline.model_dump(), actor_name=actor_name),
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
        return _relay(
            "set_card_indicator",
            board_api.set_card_indicator(card_id=card_id, indicator=indicator, actor_name=actor_name),
        )

    @server.tool(
        name="move_card",
        description=_scoped_description("Move a card to another column on the current Minimal Kanban board using the target column id."),
        annotations=_write_tool_annotations("Move Card"),
        structured_output=True,
    )
    def move_card(card_id: str, column: str, actor_name: str | None = None) -> JsonEnvelope:
        return _relay("move_card", board_api.move_card(card_id=card_id, column=column, actor_name=actor_name))

    @server.tool(
        name="bulk_move_cards",
        description=_scoped_description(
            "Move multiple cards to one target column on the current Minimal Kanban board in a single write call. Prefer this over long chains of sequential move_card calls."
        ),
        annotations=_write_tool_annotations("Bulk Move Cards", idempotent=True),
        structured_output=True,
    )
    def bulk_move_cards(card_ids: list[str], column: str, actor_name: str | None = None) -> JsonEnvelope:
        return _relay(
            "bulk_move_cards",
            board_api.bulk_move_cards(card_ids=card_ids, column=column, actor_name=actor_name),
        )

    @server.tool(
        name="archive_card",
        description=_scoped_description("Archive a card on the current Minimal Kanban board."),
        annotations=_write_tool_annotations("Archive Card", destructive=True),
        structured_output=True,
    )
    def archive_card(card_id: str, actor_name: str | None = None) -> JsonEnvelope:
        return _relay("archive_card", board_api.archive_card(card_id=card_id, actor_name=actor_name))

    @server.tool(
        name="restore_card",
        description=_scoped_description("Restore an archived card back onto the current Minimal Kanban board."),
        annotations=_write_tool_annotations("Restore Card"),
        structured_output=True,
    )
    def restore_card(card_id: str, column: str | None = None, actor_name: str | None = None) -> JsonEnvelope:
        return _relay("restore_card", board_api.restore_card(card_id=card_id, column=column, actor_name=actor_name))

    @server.tool(
        name="list_overdue_cards",
        description=_scoped_description("List overdue cards from the current Minimal Kanban board. Archived cards are excluded by default."),
        annotations=_read_tool_annotations("Overdue Cards"),
        structured_output=True,
    )
    def list_overdue_cards(include_archived: bool = False) -> JsonEnvelope:
        return _relay("list_overdue_cards", board_api.list_overdue_cards(include_archived=include_archived))

    return server
