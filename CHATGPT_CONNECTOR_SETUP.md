# AutoStop CRM: ChatGPT And Responses MCP Access

This note records the current client compatibility for
`https://crm.autostopcrm.ru/mcp`. Tool behavior is defined in
[MCP_GUIDE.md](MCP_GUIDE.md); deploy and credential rotation are in
[the operations runbook](docs/OPERATIONS_RUNBOOK.md).

## Current Status

Production AutoStop CRM is owner-controlled, OAuth-enabled, and fail-closed:

- anonymous reads and writes are rejected;
- `AUTOSTOP_MCP_OAUTH_ENABLED=1`;
- `AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED=0`;
- OAuth uses authorization code + PKCE S256, owner approval, short-lived access
  tokens, rotating refresh tokens, and exact audience/scope validation;
- authorization state is encrypted and persists across container replacement;
- scope is one current AutoStop CRM board.

This is an owner-approved OAuth 2.1 connection; authorization is never
auto-approved.

ChatGPT Apps and Codex discover the protected resource, dynamically register a
client, open the CRM administrator approval page, and retain a refresh session
in their protected credential store. A deploy may rotate the internal bearer
without breaking this OAuth session. Do not enable anonymous access or the old
auto-approved development OAuth provider. See OpenAI's
[Apps SDK authentication guide](https://developers.openai.com/apps-sdk/build/auth).

## Supported Clients

The endpoint is supported by:

- ChatGPT Apps/Connectors through the public OAuth flow;
- Codex through `codex mcp login autostopcrm` after configuring only the public
  MCP URL (no bearer/static headers);
- Responses API remote MCP tools using `server_url` and the tool-level
  `authorization` field.

Codex and ChatGPT automatically use refresh-token rotation. If a refresh token
is explicitly revoked or the local client credential store is lost, repeat the
owner-approved link flow; never copy the internal bearer into client config.

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
4. `prepare_action_contract` for a write
5. the applicable named workflow in `dry_run`, then `apply`
6. exact-target reread and verification

Use guarded raw discovery only when no named workflow covers the required
operation. The client must see exactly 24 Gateway v2 tools and no low-level
legacy names.

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
.\.venv\Scripts\python.exe scripts\check_mcp_oauth.py --mcp-url https://crm.autostopcrm.ru/mcp
```

This is a safe read-only/dry-run/synthetic check of all 24 visible tools.
