# AutoStop CRM MCP Guide

The MCP server gives ChatGPT, Responses API clients, and compatible MCP clients
tool-based access to one current AutoStop CRM board.

MCP does not own business logic:

```text
MCP tool call
  -> MCP adapter
  -> local HTTP API
  -> CardService
  -> JsonStore
```

Source of truth: `src/minimal_kanban/mcp/server.py`,
`src/minimal_kanban/mcp/tool_registry.py`, and live `tools/list`.

## Runtime

- Local default: `http://127.0.0.1:41831/mcp`
- Production: `https://crm.autostopcrm.ru/mcp`
- Entrypoints: `main_mcp.py`, `scripts/run_mcp_server.ps1`
- Client adapter: `src/minimal_kanban/mcp/client.py`
- Runtime/auth: `runtime.py`, `auth.py`, `oauth_provider.py`

Run locally:

```powershell
.\scripts\run_mcp_server.ps1
```

or:

```powershell
python .\main_mcp.py
```

Backend selection:

1. Use `MINIMAL_KANBAN_BOARD_API_URL` when set.
2. Reuse an already running local API when available.
3. При необходимости поднимает скрытый backend.

## Environment And Auth

MCP runtime:

- `MINIMAL_KANBAN_MCP_HOST`
- `MINIMAL_KANBAN_MCP_PORT`
- `MINIMAL_KANBAN_MCP_PORT_FALLBACK_LIMIT`
- `MINIMAL_KANBAN_MCP_PATH`
- `MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL`
- `MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL`
- `MINIMAL_KANBAN_MCP_BEARER_TOKEN`

Backend API:

- `MINIMAL_KANBAN_BOARD_API_URL`
- `MINIMAL_KANBAN_API_BEARER_TOKEN`

Saved settings live in `%APPDATA%\Minimal Kanban\settings.json`; explicit env
variables win.

Production can publish embedded OAuth/DCR metadata for ChatGPT linking when
bearer mode is enabled. Manual MCP clients and Responses API integrations may
pass bearer auth directly.

## Optional AutostopManager Layer

When `AutostopManager` is mounted next to CRM or `AUTOSTOP_MANAGER_PATH` points
to it, the same MCP endpoint can expose optional manager memory/source tools.
For example, production may expose `estimate_repair_work_cost` while a local
CRM-only workspace does not.

Release checks must compare actual tool names and explain optional manager-layer
differences. Do not treat a raw tool count mismatch as a CRM regression until
the names are compared.

CRM remains the source of truth for cards, clients, vehicles, repair orders,
files, payments, and cashboxes. Manager memory is only for durable manager
facts, decisions, source routing, and knowledge navigation.

## Recommended Call Order

Начинайте каждую новую connector-сессию с коротких read-вызовов:

1. `ping_connector`
2. `get_connector_identity`
3. `bootstrap_context(compact=true)`
4. `get_runtime_status` when auth, tunnel, or runtime is unclear
5. focused search/read tools
6. write only after target is identified
7. read-back verification

Prefer:

- `review_board` before full wall export;
- `get_cards(compact=true)` and compact snapshot before heavy board reads;
- `search_cards` before broad board scans;
- `suggest_clients_for_card` or `search_clients` before `create_client`;
- `get_card_context` before card writes;
- `get_card_log(compact=true, limit=50)` for fast audit reads;
- `get_card_log(include_full_details=true)` only for maintenance/debug raw
  `before/after` recovery from `audit-archive`.

## CRM Tool Groups

Diagnostics:

- `ping_connector`
- `get_connector_identity`
- `bootstrap_context`
- `get_runtime_status`

Board and cards:

- `list_columns`, `create_column`, `rename_column`, `delete_column`
- `get_cards`, `get_card`, `get_card_context`, `get_card_log`
- `get_board_snapshot`, `get_board_context`, `review_board`
- `get_board_content`, `get_board_events`, `get_gpt_wall`
- `search_cards`, `list_overdue_cards`, `list_archived_cards`
- `create_card`, `update_card`, `move_card`, `bulk_move_cards`
- `set_card_deadline`, `set_card_indicator`, `mark_card_ready`
- `archive_card`, `restore_card`
- `set_card_board_summary`

Clients and vehicles:

- `list_clients`, `search_clients`, `get_client`, `get_client_stats`
- `create_client`, `update_client`, `delete_client`
- `suggest_clients_for_card`
- `link_card_to_client`, `unlink_card_from_client`
- `upsert_client_vehicle`, `delete_client_vehicle`

Repair orders and PDF:

- `list_repair_orders`
- `get_repair_order`
- `get_repair_order_text`
- `download_repair_order_print_pdf`
- `update_repair_order`
- `set_repair_order_status`
- `replace_repair_order_works`
- `replace_repair_order_materials`

Cashboxes:

- `list_cashboxes`
- `get_cashbox`
- `get_cash_journal`
- `create_cashbox`
- `delete_cashbox`
- `create_cash_transaction`

Manual `create_cash_transaction` expenses require a `note` with at least 10
visible characters. Income operations and non-manual finance flows are
unchanged.

`get_cashbox` supports `transaction_limit` and `transaction_offset`; use small
pages for cashboxes with many operations.

Shared files and attachments:

- `list_shared_files`
- `get_shared_file_info`
- `download_shared_file`
- `upload_shared_file`
- `delete_shared_file`
- `update_shared_file_position`
- `list_card_attachments`
- `get_card_attachment`
- `read_card_attachment`

Sticky notes and settings:

- `create_sticky`, `update_sticky`, `move_sticky`, `delete_sticky`
- `update_board_settings`

Not normal MCP runtime tools:

- `autofill_vehicle_data`
- `autofill_repair_order`
- `cleanup_card_content`

Those remain API/UI/compatibility paths.

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

## Board Summary And Cleanup

`set_card_board_summary(card_id, summary, actor_name=None)` writes the short
board preview. It must stay under 5 non-empty lines and 560 characters and must
not contain phone, VIN, full client name, raw diagnostic dump, or long complaint
text.

`Приберись` is an agent procedure:

1. read live card/board context;
2. patch confirmed fields only;
3. preserve operator data, works, materials, prices, payments, files, and
   historical notes;
4. do not move/archive cards unless separately requested;
5. refresh `board_summary`;
6. reread and verify.

## VIN/Profile Enrichment

When VIN/chassis/frame exists and profile fields are empty:

- preserve `manual_fields`;
- fill only source-backed confirmed `engine_model`, `gearbox_model`,
  `drivetrain`, and source metadata;
- put uncertainty in `oem_notes` or `tentative_fields`;
- use optional manager/source tools first when available.

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
