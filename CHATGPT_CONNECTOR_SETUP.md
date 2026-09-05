# AutoStop CRM: ChatGPT And Responses MCP Access

This is the compact client/auth note for `https://crm.autostopcrm.ru/mcp`.
Tool behavior belongs in [MCP_GUIDE.md](MCP_GUIDE.md); operations and release
verification belong in the [runbook](docs/OPERATIONS_RUNBOOK.md).

## Client Contract

- The endpoint serves one protected CRM board. Public anonymous reads are
  rejected, and Public anonymous writes must remain blocked.
- Production uses owner-approved OAuth 2.1 authorization code with PKCE S256,
  administrator approval, rotating refresh tokens, and exact audience/scope
  validation. Keep `AUTOSTOP_MCP_OAUTH_ENABLED=1` and
  `AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED=0`; the encrypted state survives normal
  container replacement.
- ChatGPT Apps discover the protected resource and use its approval page.
  Codex uses the public URL with no static headers, then runs
  `codex mcp login autostopcrm`.
- Responses remote MCP tools send the current tool-level `authorization`:

```json
{
  "type": "mcp",
  "server_label": "autostop_crm",
  "server_url": "https://crm.autostopcrm.ru/mcp",
  "authorization": "<current bearer token>"
}
```

Provide that token through a secret/environment mechanism, never source,
documentation, shell history, logs, or ordinary chat. A revoked link uses the
normal owner-approved flow again.

Platform details: [Apps SDK authentication](https://developers.openai.com/apps-sdk/build/auth) and [MCP and Connectors](https://developers.openai.com/api/docs/guides/tools-connectors-mcp).

## Safe Use

1. Begin with `agent_bootstrap`; use focused reads to identify exact targets.
2. For a mutation, prepare the action, use dry-run then apply with a unique
   idempotency key, and reread the target.
3. Use `get_runtime_status` only for runtime/auth diagnostics. Keep approvals
   for sensitive actions and require explicit owner intent for money, client,
   file, order, inventory, archive, or delete changes.

The client must expose the current Gateway surface, not hidden legacy tools.
Use the runbook's read-only/dry-run/synthetic release check when verification
is authorized.
