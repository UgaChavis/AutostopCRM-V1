"""Keep SDK validation errors from echoing redacted repair-order requests."""

from mcp.types import CallToolRequest, CallToolResult, ServerResult, TextContent


def _redacted_order_request(name: str, arguments: dict) -> bool:
    if name == "agent_search":
        return arguments.get("entity") == "repair_order"
    if name == "call_raw_capability":
        raw_name = arguments.get("name")
        if not isinstance(raw_name, str) or raw_name.strip() != "list_repair_orders":
            return False
        arguments = arguments.get("arguments")
        if not isinstance(arguments, dict):
            return False
    elif name != "list_repair_orders":
        return False
    return "redact_private" in arguments and arguments["redact_private"] is not False


def install_repair_order_wire_safety(server) -> None:
    # The SDK validates JSON schema before Tool.run. Preserve that schema and
    # handler, replacing only error results for explicitly redacted requests.
    handlers = server._mcp_server.request_handlers
    original = handlers[CallToolRequest]

    async def safe_call(request):
        result = await original(request)
        arguments = request.params.arguments or {}
        if not isinstance(arguments, dict) or not _redacted_order_request(
            request.params.name, arguments
        ):
            return result
        body = result.root if isinstance(result, ServerResult) else None
        if body is None or (isinstance(body, CallToolResult) and body.isError):
            return ServerResult(
                CallToolResult(
                    content=[
                        TextContent(type="text", text="Redacted repair-order request failed.")
                    ],
                    isError=True,
                )
            )
        return result

    handlers[CallToolRequest] = safe_call
