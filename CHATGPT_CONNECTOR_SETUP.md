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
- MCP bearer mode and token only when the endpoint is protected.

The final connector URL must begin with `https://` and end with `/mcp`.
Production may expose embedded OAuth/DCR metadata for ChatGPT linking when
bearer mode is enabled.

## ChatGPT Setup

1. Open ChatGPT Apps & Connectors settings.
2. Add a custom MCP connector named `AutoStop CRM`.
3. Set the URL to `https://crm.autostopcrm.ru/mcp`.
4. Complete the embedded OAuth flow if ChatGPT asks for linking.
5. In a clean chat, enable only this connector while validating it.

First calls:

1. `ping_connector`
2. `bootstrap_context(compact=true)`
3. `get_runtime_status` when auth, tunnel, or runtime state is unclear

For Responses API clients, use the same `server_url` and pass bearer
authorization in the MCP tool payload when bearer mode is enabled.

## Safety

- Public anonymous writes must remain blocked.
- Read live context before every write.
- Write only after `bootstrap_context(compact=true)` and target identification.
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
