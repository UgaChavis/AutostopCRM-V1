# AutoStop CRM ChatGPT Connector Setup

This is the single role-specific setup note for the ChatGPT connector. The MCP
tool contract remains in `MCP_GUIDE.md`; route behavior remains in
`API_GUIDE.md` and the application code.

## Endpoint

- Connector URL: `https://crm.autostopcrm.ru/mcp`
- Scope: one current AutoStop CRM board only.
- Name: `AutoStop CRM`
- Description: `Auto service CRM with board, clients, repair orders, cashboxes, and files`

Use the production HTTPS endpoint above when it is healthy. Do not use stale
tunnel URLs for ChatGPT connector setup.

## CRM Settings

In CRM integration settings, enable:

- integration;
- local API;
- MCP;
- public HTTPS base or full MCP URL;
- MCP bearer mode. Production is fail-closed and will not start without a
  non-placeholder bearer token.

The final connector URL must begin with `https://` and end with `/mcp`.
Production does not expose the embedded auto-approved OAuth/DCR flow. It uses
owner-controlled bearer-only auth; keep
`AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED=0`.

On the production Codex host, configure the shared service credential with the
rotation helper instead of copying a token into this document or
`config.toml`:

```bash
cd /opt/autostopcrm
python3 scripts/configure_codex_mcp_auth.py rotate --generate
set -a
. /root/.config/autostopcrm/codex-mcp.env
set +a
python3 scripts/configure_codex_mcp_auth.py check
```

The resulting Codex entry uses
`bearer_token_env_var = "AUTOSTOPCRM_MCP_TOKEN"` plus a mode-`0600` static
Authorization fallback so the desktop app can reconnect before a process
restart. Restart Codex after loading the environment when practical. Normal
agent activity is audited as service identity `codex-owner-agent`.

## ChatGPT Setup

1. Open ChatGPT Apps & Connectors settings.
2. Add a custom MCP connector named `AutoStop CRM`.
3. Set the URL to `https://crm.autostopcrm.ru/mcp`.
4. Production does not expose embedded OAuth/DCR. Connect only from a client
   that can supply the owner bearer credential; otherwise keep this connector
   disabled rather than reopening anonymous or auto-approved access.
5. In a clean chat, enable only this connector while validating it.

First calls:

1. `agent_bootstrap`
2. `agent_board_digest(limit=100)`
3. `agent_search` and `agent_entity_context` for focused detail

Use `get_runtime_status` only for transport/runtime diagnostics; normal work
starts with `agent_bootstrap`.

For Responses API clients, use the same `server_url` and pass bearer
authorization in the MCP tool payload when bearer mode is enabled.

## Safety

- Public anonymous writes must remain blocked.
- Public anonymous reads must also be blocked in production.
- Read live context before every write.
- Write only after `agent_bootstrap`, exact-target identification, and an
  action contract or named workflow dry-run.
- Read back changed cards, files, clients, repair orders, or cashboxes.
- Do not move, archive, delete, or change money/client/file/order data without
  explicit owner intent.
- Do not paste bearer tokens into ordinary docs or chats.

## Verification

Local connector smoke:

```powershell
python scripts\check_live_connector.py --strict --skip-public-site --skip-public-write-protection --local-api-url http://127.0.0.1:41731 --mcp-url http://127.0.0.1:41831/mcp --operator-username $env:AUTOSTOP_SMOKE_OPERATOR_USERNAME --operator-password $env:AUTOSTOP_SMOKE_OPERATOR_PASSWORD --expect-admin
```

Production deploy and public smoke live in `docs/OPERATIONS_RUNBOOK.md`.
