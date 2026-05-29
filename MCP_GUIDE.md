# AutoStop CRM MCP Guide

The MCP server gives ChatGPT, Responses API clients, and compatible MCP clients
tool-based access to one current AutoStop CRM board.

```text
MCP tool call -> MCP adapter -> local HTTP API -> CardService -> JsonStore
```

Source of truth: `src/minimal_kanban/mcp/server.py`,
`src/minimal_kanban/mcp/tool_registry.py`, and live `tools/list`.

## Runtime And Auth

- Local default: `http://127.0.0.1:41831/mcp`
- Production: `https://crm.autostopcrm.ru/mcp`
- Entrypoints: `main_mcp.py`, `scripts/run_mcp_server.ps1`
- Client adapter: `src/minimal_kanban/mcp/client.py`

Run locally:

```powershell
.\scripts\run_mcp_server.ps1
```

MCP environment:

- `MINIMAL_KANBAN_MCP_HOST`
- `MINIMAL_KANBAN_MCP_PORT`
- `MINIMAL_KANBAN_MCP_PORT_FALLBACK_LIMIT`
- `MINIMAL_KANBAN_MCP_PATH`
- `MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL`
- `MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL`
- `MINIMAL_KANBAN_MCP_BEARER_TOKEN`
- `MINIMAL_KANBAN_MCP_ALLOWED_HOSTS`
- `MINIMAL_KANBAN_MCP_ALLOWED_ORIGINS`

Backend API environment:

- `MINIMAL_KANBAN_BOARD_API_URL`
- `MINIMAL_KANBAN_API_BEARER_TOKEN`

Saved settings live in `%APPDATA%\Minimal Kanban\settings.json`; explicit env
variables win. `MINIMAL_KANBAN_MCP_ALLOWED_HOSTS` and
`MINIMAL_KANBAN_MCP_ALLOWED_ORIGINS` add comma/newline-separated transport
security allowlist entries.

Production can publish embedded OAuth/DCR metadata for ChatGPT linking when
bearer mode is enabled. Manual MCP clients and Responses API integrations may
pass bearer auth directly.

## ChatGPT Connector

ChatGPT connects to the production MCP endpoint:

```text
https://crm.autostopcrm.ru/mcp
```

Connector scope is exactly one current AutoStop CRM board. In CRM integration
settings, enable integration, local API, MCP, public HTTPS base/full MCP URL,
and MCP auth mode/token when the endpoint is protected. The final connector URL
must start with `https://` and end with `/mcp`.

ChatGPT Apps & Connectors setup:

1. Create a new MCP connector.
2. Name: `AutoStop CRM`.
3. Description: `Автосервисная CRM с доской, клиентами, заказ-нарядами, кассами и файлами`.
4. URL: `https://crm.autostopcrm.ru/mcp`.
5. If ChatGPT asks for linking, complete the embedded OAuth flow.
6. First calls: `ping_connector`, then `bootstrap_context(compact=true)`.
7. If tunnel/auth/runtime is unclear, call `get_runtime_status`.

Responses API clients use the same `server_url`. Do not rely on a static JSON
tool list; fetch live tools or use connector discovery. In bearer mode, pass
authorization in the MCP tool payload.

## Optional AutostopManager Layer

When `AutostopManager` is mounted next to CRM or `AUTOSTOP_MANAGER_PATH` points
to it, the same MCP endpoint can expose optional manager memory/source tools
such as `estimate_repair_work_cost`, `lookup_original_parts`, `today_context`,
`agent_brief`, `remember`, or `system_audit`.

Release checks must compare actual tool names and explain optional manager-layer
differences. Do not treat a raw tool count mismatch as a CRM regression until
names are compared. CRM remains the source of truth for cards, clients,
vehicles, repair orders, files, payments, and cashboxes.

## Recommended Call Order

Begin each connector session with short read calls:

1. `ping_connector`
2. `get_connector_identity`
3. `bootstrap_context(compact=true)`
4. `get_runtime_status` when auth, tunnel, or runtime is unclear
5. focused search/read tools
6. write only after target is identified
7. read-back verification

Prefer compact reads: `review_board`, `get_cards(compact=true)`,
`search_cards`, `suggest_clients_for_card`, `get_card_context`, and
`get_card_log(compact=true, limit=50)`. Use
`get_card_log(include_full_details=true)` only for maintenance/debug recovery
from `audit-archive`.

## Tool Groups

Diagnostics: `ping_connector`, `get_connector_identity`, `bootstrap_context`,
`get_runtime_status`.

Board/cards: column CRUD, `get_cards`, `get_card`, `get_card_context`,
`get_card_log`, board snapshot/context/review/content/events/wall reads,
card search, overdue/archive reads, card create/update/move/bulk move,
deadline/indicator, ready/archive/restore, and `set_card_board_summary`.

Clients/vehicles: list/search/get/stats/suggest, client CRUD, card link/unlink,
vehicle upsert/delete.

Repair orders/PDF: list/get/text/PDF download, update, status change, replace
works/materials.

Cashboxes: list/get/journal, create/delete, and `create_cash_transaction`.
Manual expense transactions require a `note` with at least 10 visible
characters. `get_cashbox` supports `transaction_limit` and
`transaction_offset`.

Shared files/attachments: list/info/download/upload/delete/position update,
card attachment list/get/read.

Sticky/settings: sticky CRUD/move and `update_board_settings`.

Not normal MCP runtime tools: `autofill_vehicle_data`,
`autofill_repair_order`, `cleanup_card_content`.

## Write Rules

- Read live context before every write.
- Patch only confirmed fields.
- Read back the target and verify the result.
- Do not move, archive, delete, or change money/client/file/order data without
  explicit owner intent.
- For clients, search/suggest before create/link.
- For documents, use CRM PDF export.
- Repair-order numbers are immutable; corrections are maintenance-only.
- Finance audit safe fixes are maintenance-only and require the runbook
  audit-first flow.

`Приберись` is an agent procedure: read live context, patch confirmed fields,
preserve operator data/works/materials/prices/payments/files/history, refresh
`board_summary`, and reread. It does not move/archive cards unless separately
requested.

VIN/profile enrichment must preserve manual fields and write only source-backed
confirmed facts. Put uncertainty in `oem_notes` or `tentative_fields`.

## Security

- Connector scope is one current CRM board.
- Do not paste bearer tokens into ordinary docs or chats.
- Do not use stale tunnel URLs when `https://crm.autostopcrm.ru/mcp` is healthy.
- Public anonymous writes must remain blocked.
- Do not move raw client databases, phone rows, VIN/license tables, cashbox
  ledgers, credentials, bearer tokens, or full repair-order text into external
  knowledge stores without explicit owner approval.

## Checks

Local:

```powershell
.\scripts\run_mcp_server.ps1
python -m unittest tests.test_mcp tests.test_mcp_main tests.test_connection_card -v
```

Connector smoke:

```powershell
python scripts\check_live_connector.py --strict --skip-public-site --skip-public-write-protection --local-api-url http://127.0.0.1:41731 --mcp-url http://127.0.0.1:41831/mcp --operator-username $env:AUTOSTOP_SMOKE_OPERATOR_USERNAME --operator-password $env:AUTOSTOP_SMOKE_OPERATOR_PASSWORD --expect-admin
```

Production deploy and public smoke live in `docs/OPERATIONS_RUNBOOK.md`.
