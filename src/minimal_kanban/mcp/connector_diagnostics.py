from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .agent_gateway_support import DIAGNOSTIC_TOOL_NAMES
from .payloads import ConnectorIdentityEnvelope, JsonEnvelope


@dataclass(frozen=True, slots=True)
class ConnectorDiagnosticsContext:
    connector_identity: Mapping[str, Any]
    schema_version: str
    scoped_description: Callable[[str], str]
    read_tool_annotations: Callable[[str], ToolAnnotations]
    canonical_tool_path: Callable[[str], str]
    timed_meta: Callable[..., dict[str, Any]]
    relay_data: Callable[..., JsonEnvelope]
    relay_identity_data: Callable[..., ConnectorIdentityEnvelope]
    identity_text: Callable[[], str]
    runtime_status_payload: Callable[[], dict[str, Any]]
    runtime_status_text: Callable[[dict[str, Any]], str]


def register_connector_diagnostics(
    server: FastMCP,
    context: ConnectorDiagnosticsContext,
) -> frozenset[str]:
    @server.tool(
        name="get_connector_identity",
        description=context.scoped_description(
            "Return the hard identity of this MCP connector: name, resource_url, auth mode, and the rule that it manages only the current AutoStop CRM board."
        ),
        annotations=context.read_tool_annotations("Connector Identity"),
        structured_output=True,
    )
    def get_connector_identity() -> ConnectorIdentityEnvelope:
        started_at = perf_counter()
        return context.relay_identity_data(
            {
                "identity": dict(context.connector_identity),
                "text": context.identity_text(),
            },
            meta=context.timed_meta(
                "get_connector_identity",
                started_at,
                meta={"response_mode": "identity"},
            ),
        )

    @server.tool(
        name="ping_connector",
        description=context.scoped_description(
            "Return the lightest possible connector ping. Use this first when you need to verify that ChatGPT can execute any AutoStop CRM MCP tool at all."
        ),
        annotations=context.read_tool_annotations("Connector Ping"),
        structured_output=True,
    )
    def ping_connector() -> JsonEnvelope:
        started_at = perf_counter()
        return context.relay_data(
            "ping_connector",
            {
                "connector_name": context.connector_identity["connector_name"],
                "resource_url": context.connector_identity["resource_url"],
                "board_scope": context.connector_identity["board_scope"],
                "message": "pong",
                "schema_version": context.schema_version,
                "text": (
                    "[CONNECTOR PING]\n"
                    f"connector_name: {context.connector_identity['connector_name']}\n"
                    f"resource_url: {context.connector_identity['resource_url']}\n"
                    f"canonical_tool_path: {context.canonical_tool_path('ping_connector')}\n"
                    "message: pong\n"
                ),
            },
            meta=context.timed_meta(
                "ping_connector",
                started_at,
                meta={"response_mode": "ping"},
            ),
        )

    @server.tool(
        name="get_runtime_status",
        description=context.scoped_description(
            "Return runtime diagnostics for this connector: effective MCP identity, board API health, board counts, and whether the endpoint is publicly reachable in principle."
        ),
        annotations=context.read_tool_annotations("Runtime Status"),
        structured_output=True,
    )
    def get_runtime_status() -> JsonEnvelope:
        started_at = perf_counter()
        runtime_status = context.runtime_status_payload()
        return context.relay_data(
            "get_runtime_status",
            {
                "schema_version": context.schema_version,
                "runtime_status": runtime_status,
                "canonical_tool_paths": {
                    tool_name: context.canonical_tool_path(tool_name)
                    for tool_name in (
                        "ping_connector",
                        "bootstrap_context",
                        "get_runtime_status",
                    )
                },
                "full_board_context_tool": "get_board_context",
                "text": context.runtime_status_text(runtime_status),
            },
            meta=context.timed_meta(
                "get_runtime_status",
                started_at,
                meta={"response_mode": "diagnostics"},
            ),
        )

    return DIAGNOSTIC_TOOL_NAMES
