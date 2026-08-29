# AutoStop CRM: ChatGPT And Responses MCP Access

This note covers client and authentication compatibility for
`https://crm.autostopcrm.ru/mcp`. Tool behavior and write rules belong in
[MCP_GUIDE.md](MCP_GUIDE.md); deployment, credentials, and live verification
belong in [the operations runbook](docs/OPERATIONS_RUNBOOK.md).

## Production Contract

- The endpoint is scoped to one current AutoStop CRM board. Public anonymous
  reads and writes are rejected; Public anonymous writes must remain blocked.
- Production uses `AUTOSTOP_MCP_OAUTH_ENABLED=1` and
  `AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED=0`.
- The owner-approved OAuth 2.1 flow uses authorization code with PKCE S256,
  explicit administrator approval, short-lived access tokens, rotating refresh
  tokens, and exact audience/scope validation. Authorization is never
  auto-approved.
- Encrypted authorization state survives container replacement. The rotating
  bearer is internal compatibility for server smoke and Responses API access;
  it is not a ChatGPT/Codex client configuration shortcut.

## Supported Clients

- ChatGPT Apps/Connectors discover the protected resource, register a client,
  and open the CRM administrator approval page.
- Codex is configured with only the public MCP URL and no static headers; then
  run `codex mcp login autostopcrm`. Current Codex uses a per-server
  `/callback/<12-character-id>` loopback redirect on a high local port.
- Responses API remote MCP tools use `server_url` plus the tool-level
  `authorization` field on every Response creation request:

```json
{
  "type": "mcp",
  "server_label": "autostop_crm",
  "server_url": "https://crm.autostopcrm.ru/mcp",
  "authorization": "<current bearer token>"
}
```

OpenAI does not store `authorization` in the Response object. Provision the
real token through the client secret/environment mechanism; never put it in
source, documentation, shell history, logs, or ordinary chat. ChatGPT and Codex
rotate refresh tokens automatically. After explicit revocation or loss of the
local credential store, repeat the owner-approved link flow.

Authoritative OpenAI references: [Apps SDK authentication](https://developers.openai.com/apps-sdk/build/auth)
and [MCP and Connectors](https://developers.openai.com/api/docs/guides/tools-connectors-mcp).

## First Calls And Safety

1. Call `agent_bootstrap`.
2. Use `agent_board_digest`, `agent_search`, and `agent_entity_context` to find
   exact targets.
3. For a mutation, create `prepare_action_contract`, run the named workflow in
   `dry_run`, then `apply` with a unique idempotency key, and reread the target.

Use `get_runtime_status` only for explicit runtime/auth diagnostics. The client
must expose the current Gateway v2 surface and no low-level legacy tool names;
the exact source-derived contract is in `MCP_GUIDE.md`.

Keep normal tool approvals enabled for sensitive actions. Read live context
before every write, and do not move, archive, delete, or change money, client,
file, order, or inventory data without explicit owner intent.

## Verification

Client setup uses the standard OAuth flow only. Run the exhaustive Gateway
check inside the CRM container so the internal compatibility bearer stays
server-local; follow `MCP_GUIDE.md` and the operations runbook for the exact
read-only/dry-run/synthetic release check.
