# AutoStop CRM MCP Guide

The MCP server gives ChatGPT, Responses API clients, and compatible MCP clients
tool-based access to one current AutoStop CRM board.

```text
MCP tool call -> MCP adapter -> local HTTP API -> CardService -> JsonStore
```

Source of truth: `src/minimal_kanban/mcp/server.py`,
`src/minimal_kanban/mcp/agent_gateway_v2.py`,
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

Production uses owner-controlled bearer-only auth. Embedded OAuth/DCR remains a
local compatibility option, but its auto-approved connector flow is not an
owner-authentication boundary and must stay disabled in production with
`AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED=0`. Manual Codex/MCP clients pass bearer
auth through `AUTOSTOPCRM_MCP_TOKEN`.

Production is fail-closed: `scripts/container_entrypoint.py` validates
bearer-only mode, a
non-placeholder `MINIMAL_KANBAN_MCP_BEARER_TOKEN`, HTTPS public URLs, stable
`AUTOSTOP_AGENT_SERVICE_IDENTITY`, an absolute maintenance marker, and every
Agent Gateway kill switch before starting `main_mcp.py`. Anonymous reads and
writes are not a supported production mode.

Agent Gateway v2 is the Codex-first production surface. It keeps the permanent
tool list compact (`agent_bootstrap`, board/search/context and domain workflows,
workflow ledger controls, diagnostics, and three lazy raw-discovery tools).
Low-level CRM capabilities remain implemented but are available only behind
`discover_raw_capabilities` -> `get_raw_capability_schema` ->
`call_raw_capability`. The last call requires the current schema hash. Missing
UI/API reads and mutations are exposed lazily as guarded `api:/api/...`
capabilities; they are never added to the permanent tool list. Writes still
require the durable workflow ledger, idempotency fingerprint,
finance/destructive kill switches, and a route-bound schema hash. Mutating
calls force the audited `codex-owner-agent` identity; caller-supplied human
actor names are ignored. Operator-administration routes additionally require
local service-identity headers and the MCP bearer token and never accept this
identity through the public reverse proxy.

Every applied write is reread. If the executor applied a change but its
readback cannot be proven, the workflow remains non-terminal in
`compensating`; it is never reported as a clean failure that may be retried
blindly. Workflow lifecycle writes use state-version compare-and-swap.

Runtime kill switches are:

- `AUTOSTOP_AGENT_GATEWAY_ENABLED`;
- `AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED`;
- `AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED`;
- `AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED`;
- `AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED`;
- `AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED`.

All six switches must be explicitly provisioned as `0` or `1` in production;
there are no production defaults. With the master switch off, production
exposes diagnostics only, not the low-level write surface. A switch change takes
effect when the CRM container is recreated. The mail switch controls the
refs-only CRM/manager bridge, not the separately authenticated Gmail
connector; Gmail mutations keep their own exact-target workflow controls.

`AUTOSTOP_MAINTENANCE_MARKER` blocks API write routes with `503
maintenance_mode` while read/health routes remain available. The production
deploy additionally stops the old CRM process before its atomic release backup,
so first-time upgrades are protected even when the old image predates the
marker contract.

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

The read-only v2 release check is:

```bash
python scripts/check_agent_gateway_v2.py --mcp-url https://crm.autostopcrm.ru/mcp --token-env AUTOSTOPCRM_MCP_TOKEN
```

It verifies anonymous rejection, the exact 24-tool production surface,
tool-list payload budgets, compact board/search/context reads, and the workflow
registry without printing board data or the token. Add `--exhaustive` to call
all 24 tools with read-only, dry-run, or synthetic terminal workflow inputs.

## Mounted AutostopManager Layer

When `AutostopManager` is mounted next to CRM or `AUTOSTOP_MANAGER_PATH` points
to it, its raw manager capabilities and v2 ledger implementation are loaded
behind the gateway. Production still advertises exactly 24 tools. Manager
memory/source capabilities such as `estimate_repair_work_cost`,
`lookup_original_parts`, `today_context`, or `remember` are available only
through schema-hashed raw discovery when no named workflow covers the request.

Release checks compare the exact visible names and separately audit raw
registry counts. CRM remains the source of truth for cards, clients, vehicles,
repair orders, inventory, files, payments, and cashboxes.

## Recommended Call Order

Begin each Codex session with the compact v2 surface:

1. `agent_bootstrap`
2. `agent_board_digest` for board-wide scope
3. `agent_search` and `agent_entity_context` for exact targets
4. `list_agent_workflows` / `prepare_action_contract` for a managed operation
5. the narrow board, finance, inventory, or document workflow
6. `workflow_status` plus read-back verification
7. raw discovery only when no workflow covers the owner request

`ping_connector`, `get_connector_identity`, and `get_runtime_status` remain
small diagnostics. Lower-level card, board, and manager capabilities are never
advertised directly; discover and invoke them only through the schema-hashed
raw escape hatch when no named v2 workflow covers the request.

## Hidden Raw Capability Groups

These groups describe capabilities behind lazy discovery; they are not all
advertised in production `tools/list`.

Diagnostics: `ping_connector`, `get_connector_identity`, and
`get_runtime_status` are visible; deeper board diagnostics are raw-only.

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
For an actual repair-order payment, use
`agent_finance_workflow(operation="record_repair_order_payment")`, not a manual
cash transaction. It requires the exact card/cashbox, amount, payment method,
and current `expected_updated_at`; it blocks stale revisions and overpayment,
then verifies the payment row, linked cash transaction, and cash-journal entry.
Use `agent_document_workflow(operation="download_repair_order_print_pdf")` for
documents from an existing CRM card. Use
`agent_document_workflow(operation="create_document_without_card_pdf")` for
the same standard AutoStop templates without a card from `request_text` and/or
`manual_document`; omit
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
The finance workflow also covers cashbox transfer/reorder, salary payout/shift
accrual, transaction cancellation, and finance safe-fix actions through the
guarded internal API fallback.
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
- Named Gateway v2 workflows return compact write and verification metadata by
  default; allow large output only for an explicitly requested document/file.
- Use `agent_board_workflow` in `mode="dry_run"` first for board-wide work;
  switch to `mode="apply"` with a unique idempotency key only when the plan is
  safe.
- Do not move, archive, delete, or change money/client/file/order data without
  explicit owner intent.
- For clients, search/suggest before create/link.
- For documents, use CRM PDF export through `agent_document_workflow`. For
  AutoStop documents with a card use operation
  `download_repair_order_print_pdf`; for "Документ без карточки" use operation
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
python scripts\check_agent_gateway_v2.py --mcp-url http://127.0.0.1:41831/mcp --exhaustive
```

Production deploy and public smoke live in `docs/OPERATIONS_RUNBOOK.md`.
