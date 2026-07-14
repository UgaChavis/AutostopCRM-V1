# AutoStop CRM: ChatGPT And Responses MCP Access

This note records the current client compatibility for
`https://crm.autostopcrm.ru/mcp`. Tool behavior is defined in
[MCP_GUIDE.md](MCP_GUIDE.md); deploy and credential rotation are in
[the operations runbook](docs/OPERATIONS_RUNBOOK.md).

## Current Status

Production AutoStop CRM is owner-controlled, bearer-only, and fail-closed:

- anonymous reads and writes are rejected;
- `AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED=0`;
- the deploy flow rotates the bearer credential;
- scope is one current AutoStop CRM board.

An authenticated custom ChatGPT app must use the MCP OAuth 2.1 authorization
flow. ChatGPT does not support presenting a custom API key supplied by the app
owner. Therefore the current bearer-only endpoint cannot be added directly as
a working ChatGPT app/connector. Do not enable anonymous access or the
auto-approved embedded development OAuth provider to work around this.

To support direct ChatGPT linking in the future, implement and review a real
OAuth 2.1 resource/authorization server, protected-resource metadata, PKCE,
token audience/scope validation, and per-tool security schemes. See OpenAI's
[Apps SDK authentication guide](https://developers.openai.com/apps-sdk/build/auth).

## Supported Clients

The endpoint is supported by:

- Codex and other MCP clients that can set
  `Authorization: Bearer <token>`;
- Responses API remote MCP tools using `server_url` and the tool-level
  `authorization` field.

Responses API shape:

```json
{
  "type": "mcp",
  "server_label": "autostop_crm",
  "server_url": "https://crm.autostopcrm.ru/mcp",
  "authorization": "<current bearer token>"
}
```

Send `authorization` on every Responses API creation request; OpenAI does not
store it in the Response object. Keep normal tool approvals enabled for
sensitive actions. The authoritative API behavior is documented in OpenAI's
[MCP and Connectors guide](https://developers.openai.com/api/docs/guides/tools-connectors-mcp).

Never put the real token in source, documentation, shell history, logs, or
ordinary chat. Provision it through the client secret/environment mechanism.

## First Calls

1. `agent_bootstrap`
2. `agent_board_digest`
3. `agent_search` and `agent_entity_context`
4. a named workflow after exact-target confirmation

Use `get_runtime_status` only for runtime/auth diagnostics.

## Safety

- Public anonymous writes must remain blocked.
- Public anonymous reads must remain blocked in production.
- Read live context before every write.
- Use a unique idempotency key and an action contract for mutations.
- Read back every changed entity and inspect workflow status.
- Do not move, archive, delete, or change money/client/file/order data without
  explicit owner intent.

## Verification

```powershell
.\.venv\Scripts\python.exe scripts\check_agent_gateway_v2.py --mcp-url https://crm.autostopcrm.ru/mcp --token-env AUTOSTOPCRM_MCP_TOKEN --exhaustive
```

This is a safe read-only/dry-run/synthetic check of all 24 visible tools.
