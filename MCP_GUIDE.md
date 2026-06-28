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

Saved settings live in the compatibility path
`%APPDATA%\Minimal Kanban\settings.json`; explicit env variables win.
`MINIMAL_KANBAN_MCP_ALLOWED_HOSTS` and
`MINIMAL_KANBAN_MCP_ALLOWED_ORIGINS` add comma/newline-separated transport
security allowlist entries.

Production can publish embedded OAuth/DCR metadata for ChatGPT linking when
bearer mode is enabled. Embedded OAuth registration accepts ChatGPT connector
redirect URIs; manual MCP clients and Responses API integrations may pass bearer
auth directly.

## ChatGPT Connector

ChatGPT connects to the production MCP endpoint:

```text
https://crm.autostopcrm.ru/mcp
```

Use `CHATGPT_CONNECTOR_SETUP.md` as the single ChatGPT Apps & Connectors setup
checklist. This guide only defines the MCP transport and tool contract.
Connector scope is exactly one current AutoStop CRM board. The final connector
URL must start with `https://` and end with `/mcp`.

Identity calls return `product_name=AutoStop CRM`,
`board_name=Current AutoStop CRM Board`, `board_key=autostopcrm/current-board`,
and connector names shaped as `autostopcrm-this-board-only-<host>`.

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
vehicles, repair orders, inventory, files, payments, and cashboxes.

## Recommended Call Order

Begin each connector session with short read calls:

1. `ping_connector`
2. `get_connector_identity`
3. `bootstrap_context(compact=true)`
4. `manager_board_scan` for operational board triage
5. `get_runtime_status` when auth, tunnel, or runtime is unclear
6. focused search/read tools
7. write only after target is identified
8. read-back verification

Prefer compact reads: `manager_board_scan`, `review_board`,
`get_cards(compact=true)`,
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

Manager operations: `manager_board_scan`, `list_ready_unpaid_cards`,
`triage_inbox_cards`, `list_cards_missing_manager_data`,
`audit_repair_order_consistency`, `audit_client_links`,
`bulk_set_deadline_if_below`, `bulk_refresh_board_summaries`, `cleanup_card`,
`apply_ready_unpaid_followups`, `run_manager_operation`, and
`rollback_manager_run`. Write-capable manager operations default to `dry_run`;
`apply` requires `actor_name` and returns compact
`scanned/eligible/changed/skipped/errors/verification`.

Clients/vehicles: list/search/get/stats/suggest, client CRUD, card link/unlink,
vehicle upsert/delete.

Repair orders/PDF: list/get/text/PDF download, document-without-card PDF
creation, update, status change, replace works/materials.
`download_repair_order_print_pdf` exports documents from an existing CRM card.
`create_document_without_card_pdf` exports the same standard AutoStop templates
without a card from `request_text` and/or `manual_document`; omit
`document_type` to infer it from phrases such as `акт выполненных работ`,
`дефектовка`, `заказ-наряд`, `счет-фактура`, `УПД`, or `продажа запчастей`.
Pass `manual_document.tax_label` or a text line such as `НДС: Без НДС` when the
invoice must print a specific tax regime.
`list_repair_orders`
supports `compact=true` and `redact_private=true` for low-payload diagnostics.
Repair-order payment summaries treat every cashless payment as a gross incoming
amount: 15% is withheld as taxes/fees, and only 85% covers the repair-order
base cost. Cashless доплата is calculated as `cash_due / 0.85`, so paying the
displayed cashless amount closes the order after withholding. The order is paid
only when the cash debt is zero. In print exports, repair order and completion
act totals show cash/cashless доплата rows without a separate `Налоги и сборы`
row; the invoice uses gross cashless prices and VAT, then subtracts all
prepayments in `Всего к оплате`.

Inventory: `list_inventory_items`, `search_inventory_items`,
`get_inventory_item`, `list_inventory_movements`, `save_inventory_item`,
`replenish_inventory_item`, `write_off_inventory_item`, and
`return_inventory_movement`. There are no visible warehouse documents or
reserves. Write-off is explicit and creates a repair-order material row with
snapshot prices; manual material edits do not affect stock.

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
- Use `response_mode=compact` on high-traffic write calls when the caller only
  needs changed ids, updated timestamps, and verification metadata.
- Use manager write operations in `dry_run` first for board-wide work; only call
  `apply` with `actor_name` when the dry-run plan is safe.
- Do not move, archive, delete, or change money/client/file/order data without
  explicit owner intent.
- For clients, search/suggest before create/link.
- For documents, use CRM PDF export. For AutoStop documents with a card use
  `download_repair_order_print_pdf`; for "Документ без карточки" use
  `create_document_without_card_pdf`. Do not build independent PDF/HTML
  templates for invoices, acts, repair orders, invoice-facturas, UPD, defect
  reports, completion acts, or parts-sale documents.
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
