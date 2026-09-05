# AutoStop CRM: ChatGPT And Responses MCP Access

This note covers client compatibility for `https://crm.autostopcrm.ru/mcp`.
Tool behavior and action guards are in [MCP_GUIDE.md](MCP_GUIDE.md); operations
and credentials are in the [operations runbook](docs/OPERATIONS_RUNBOOK.md).

## Authentication

- Public anonymous reads are rejected; Public anonymous writes must remain
  blocked.
- Production uses owner-approved OAuth 2.1 authorization code with PKCE S256,
  administrator approval, short-lived access tokens, rotating refresh tokens,
  and exact audience/scope validation. Authorization is never silently granted.
- Encrypted authorization state survives container replacement. The rotating
  bearer is server-side compatibility for smoke and Responses API access, not a
  static ChatGPT or Codex value.

## Clients

- ChatGPT Apps/Connectors discover the protected resource, register a client,
  and open the administrator approval page.
- Codex uses the public MCP URL and `codex mcp login autostopcrm`; no static
  headers.
- Responses API tools send `server_url` and tool-level `authorization` on each
  response:

```json
{"type":"mcp","server_label":"autostop_crm","server_url":"https://crm.autostopcrm.ru/mcp","authorization":"<current bearer token>"}
```

Keep real tokens in a client secret/environment mechanism, never source,
ordinary chat, shell history, or logs. Repeat the owner-approved link flow
after revocation or loss of local credentials.

## Natural Use And Safety

Use enough relevant CRM, Store, and sanctioned conversation context to answer
or find the real blocker. `agent_bootstrap`, `agent_search`,
`agent_entity_context`, board reads, workflows, and raw capability discovery
are available paths, not a required order. Use `get_runtime_status` only for
runtime or auth diagnostics.

There is no universal response template or dry-run. Native confirmation is
required at real impact: money, published customer prices, orders,
deletion/archive, a new external recipient, deployment, or secrets. Keep normal
tool approvals for those actions; never bypass authorization or hidden tools.

OpenAI references: [Apps SDK authentication](https://developers.openai.com/apps-sdk/build/auth)
and [MCP and Connectors](https://developers.openai.com/api/docs/guides/tools-connectors-mcp).