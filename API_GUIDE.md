# AutoStop CRM API Guide

The local HTTP API serves the browser UI, MCP adapter, smoke scripts, and local
integrations. Source of truth:
`src/minimal_kanban/api/server.py`. This guide lists route groups and
safety-critical contracts only.

## Base Contract

- Local base URL: `http://127.0.0.1:41731`.
- Production public base URL: `https://crm.autostopcrm.ru`.
- Health: `GET /api/health`.
- Most JSON routes accept `POST`; read-only routes marked `GET|POST` also
  accept query parameters through `GET`.
- Download routes return file bytes.

If `MINIMAL_KANBAN_API_BEARER_TOKEN` is set, every route except health requires
`Authorization: Bearer <token>`.

Success envelope:

```json
{"ok": true, "data": {}, "error": null, "meta": {"request_id": "uuid"}}
```

Error envelope:

```json
{"ok": false, "data": null, "error": {"code": "validation_error", "message": "..."}, "meta": {"request_id": "uuid"}}
```

Common error codes: `validation_error`, `not_found`, `unauthorized`,
`forbidden`, `archived_card`, `storage_limit_exceeded`, `internal_error`.

## Board, Cards, And Journal

Read routes include `list_columns`, `get_cards`, `get_card`, `get_card_log`,
`get_card_context`, `get_board_revision`, `get_board_snapshot`,
`get_board_context`, `review_board`, `get_board_content`, `get_board_events`,
`get_gpt_wall`, `search_cards`, `list_archived_cards`, and
`list_overdue_cards`. High-level manager read routes include
`manager_board_scan`, `list_ready_unpaid_cards`, `triage_inbox_cards`,
`list_cards_missing_manager_data`, `audit_repair_order_consistency`, and
`audit_client_links`.

Write routes include column CRUD/reorder, card create/update/move/archive,
deadline/indicator changes, sticky CRUD, `mark_card_ready`, `mark_card_seen`,
`bulk_move_cards`, `restore_card`, `update_board_settings`, and
`set_card_board_summary`. High-level manager write routes include
`bulk_set_deadline_if_below`, `bulk_refresh_board_summaries`, `cleanup_card`,
`apply_ready_unpaid_followups`, `run_manager_operation`, and
`rollback_manager_run`; these default to `dry_run`, while `apply` requires
`actor_name`.

Use compact board reads for repeated UI/agent refreshes:
`get_board_revision?compact=1&include_archive=0`,
`get_board_snapshot?compact=1&include_archive=0`, or
`get_cards(compact=true)`.

`get_card_log` returns compact event details by default. For maintenance/debug,
pass `include_full_details=true`; the server hydrates archived `before/after`
values from `audit-archive` when active event details were compacted out.

`set_card_board_summary` writes the short AI-managed board preview. It is capped
at 5 non-empty lines and 560 characters and must not contain phone, VIN, full
client name, raw diagnostic dump, or long complaint text.

High-traffic write routes such as `update_card`, `set_card_deadline`,
`set_card_board_summary`, `set_card_indicator`, and `bulk_move_cards` accept
`response_mode=full|compact`. Compact mode returns changed ids, timestamps, and
verification metadata instead of full card payloads.

## Clients And Vehicles

Read: `list_clients`, `search_clients`, `get_client`, `get_client_stats`,
`suggest_clients_for_card`.

Write: `create_client`, `update_client`, `delete_client`,
`link_card_to_client`, `unlink_card_from_client`, `upsert_client_vehicle`,
`delete_client_vehicle`.

Before creating a client, use search/suggest. `client_id` links a card to a
client; `client_vehicle_id` links to a specific saved vehicle. Client profiles
store `phone`/`email` as primary values and `phones`/`emails` as up to three
saved contacts each. Deleting clients/vehicles and overwriting card fields
require clear owner intent.

## Repair Orders And Printing

Read: `list_repair_orders`, `get_repair_order`, `get_repair_order_text`,
`get_repair_order_print_workspace`, `get_inspection_sheet_form`,
`/api/repair_order_number_audit`, and `/api/repair_order_text?card_id=CARD_ID`.
`list_repair_orders` accepts `compact=true` and `redact_private=true` for
diagnostic runs that should not return phone/VIN/client fields.

Write/generate: `update_repair_order`, `set_repair_order_status`,
`replace_repair_order_works`, `replace_repair_order_materials`,
`save_inspection_sheet_form`, `autofill_inspection_sheet_form`,
`preview_repair_order_print_documents`, `export_repair_order_print_pdf`,
`print_repair_order_documents`, print template CRUD/default/settings routes.

Maintenance-only: `/api/correct_repair_order_number`.

Repair-order numbers are immutable after first assignment.
`repair_order_number_audit.v1` is read-only/dry-run diagnostics for missing,
duplicate, nonnumeric, skipped, time-inverted, and payment-note mismatched
numbers. Historical fixes must use the runbook maintenance flow: backup,
read-only/dry-run audit, owner approval, and post-fix checks.

Supported print documents include repair order, acceptance act, invoice,
invoice factura, inspection sheet, completion act, and parts sale. Agents
should use CRM PDF export instead of independent PDF generation. The same
print routes also support "Документ без карточки": send
`document_without_card=true`, `manual_document`, optional `request_text`, and
selected document/template ids to `get_repair_order_print_workspace`,
`preview_repair_order_print_documents`, `export_repair_order_print_pdf`, or
`print_repair_order_documents`. Manual payloads may include `tax_label`
(`НДС (5%)` or `Без НДС`) in addition to client requisites, vehicle, works,
materials, payments, dates, numbers, and comments. They are rendered by the existing
PrintServiceProfile, template engine, standard AutoStop templates, and PDF
renderer; do not introduce separate PDF/HTML templates for AutoStop documents.

Repair-order payment totals use the shared backend calculation: cashless
payments add 15% taxes/fees from the cashless paid amount, those fees remain in
the client's cash debt, and `due_total`/`payment_status` follow the cash debt.
The printed repair order and completion act show cash and cashless доплата
rows; they do not print a separate `Налоги и сборы` row. The invoice keeps its
full cashless `Итого` and VAT, then subtracts all repair-order prepayments in
`Всего к оплате`.

Repair-order work rows may include salary override fields:
`work_salary_override_enabled`, `work_salary_guarantee`,
`work_salary_percent_override`, and `work_salary_note`. When enabled, payroll
uses `guarantee + max(work total - guarantee, 0) * executor_percent / 100`.

## Inventory

Read: `list_inventory_items`, `search_inventory_items`,
`get_inventory_item`, and `list_inventory_movements`.

Write: `save_inventory_item`, `replenish_inventory_item`,
`write_off_inventory_item`, and `return_inventory_movement`.

Inventory positions are intentionally minimal: name, optional catalog number,
unit (`шт` or `л`), quantity, cost price, sale price, and metadata. Liter
positions support fractional quantities. There are no visible warehouse
documents, reserves, suppliers, storage places, batches, FIFO, or transfers.

Every stock-changing operation records an internal inventory movement. Manual
repair-order material edits do not change stock. Only
`write_off_inventory_item` decrements stock and writes a linked material row
with snapshot cost/sale prices; `return_inventory_movement` restores stock and
clears only the technical warehouse link on that material row.

## Cashboxes, Finance, Employees, Payroll

Read: `list_cashboxes`, `get_cashbox`, `get_cash_journal`, `/api/finance_audit`,
`list_employees`, `get_payroll_report`, `get_employee_salary_ledger`,
`get_employee_salary_report`, `get_employee_salary_reconciliation`, and
`employee_salary_reconciliation_print`.

Write: `create_cashbox`, `reorder_cashboxes`, `create_cashbox_transfer`,
`delete_cashbox`, `create_cash_transaction`,
`create_employee_salary_transaction`, `/api/create_employee_shift_accrual`,
`cancel_cash_transaction`, `cancel_last_cash_transaction`, `save_employee`, `toggle_employee`,
`delete_employee`.

Maintenance-only: `/api/finance_audit/apply_safe_fixes`.

Manual `create_cash_transaction` expenses require `note` with at least 10
visible characters. Income operations, transfers, salary payouts, and repair
order payments keep their existing note behavior.

`cancel_cash_transaction` cancels a selected journal row by `transaction_id`.
Payload requires `reason` with at least 10 visible characters. The service keeps
the original row, marks it cancelled, and creates one or more reversal rows; for
cashbox transfers both paired movements are reversed together.

`get_cashbox` supports `transaction_limit` and `transaction_offset`; response
`meta.has_more` tells whether another page exists.

`get_cash_journal` returns `cash_journal.v2`. The operator UI is journal-first:
compact operation rows, transfer pairs as one logical `cashbox -> cashbox` row,
and no visible finance-audit entrypoint.

`finance_audit.v1` is internal read-only diagnostics. Apply safe fixes only
through the audit-first runbook path.

`create_employee_shift_accrual` stores a manual non-cash payroll accrual for
one active employee. Payload: `employee_id`, `amount`, optional `note`
(default `Выплата за смены за текущую неделю`). It affects payroll reports,
employee salary ledger, and reconciliation act without changing cashboxes.

`delete_employee` is allowed only for employees without linked repair-order
rows, salary cash transactions, or manual shift accruals. Use
`toggle_employee` to deactivate employees that already have payroll history.

`get_employee_salary_report` returns `employee_salary_report.v3` for month
`YYYY-MM`; it includes closed works, material salary accruals, weekly base
salary accruals, and manual shift accruals, not advances.

`get_employee_salary_reconciliation` returns
`employee_salary_reconciliation.v1`. By default it covers the last 30 days.
Pass `days=7` for a rolling period or `date_from=YYYY-MM-DD` plus
`date_to=YYYY-MM-DD` for an exact business-date range. The print route accepts
the same parameters and uses the same salary reconciliation payload as clean
HTML.

## Files

Card attachments: add/remove/list/get/read and
`/api/attachment?card_id=CARD_ID&attachment_id=ATTACHMENT_ID`.

Shared files: list/info/fetch/upload/rename/delete/copy/paste/clipboard paste,
position update, and `/api/shared_file?file_id=FILE_ID`.

Shared file storage is capped at 500 MB. Dangerous executable/script
extensions are blocked. Delete is destructive.

## Operators

Routes: `login_operator`, `logout_operator`, `get_operator_profile`,
`list_operator_users`, `save_operator_user`, `set_operator_user_employee`,
`delete_operator_user`, `get_operator_user_report`, `list_operator_activity`,
`get_operator_activity_details`, `get_operator_activity_aggregates`,
`export_operator_activity`, and `open_card`.

Admin-only routes go through `OperatorAuthService`. Smoke checks should use
`AUTOSTOP_SMOKE_OPERATOR_USERNAME` and `AUTOSTOP_SMOKE_OPERATOR_PASSWORD`, not
hard-coded defaults.

`set_operator_user_employee` links an operator account to one active employee.
That link is used as the default executor for newly added repair-order material
rows. Empty `employee_id` removes the link; one employee cannot be linked to
multiple operator accounts.

Operator activity rows live outside `state.json` under
`operator-activity/current`, `operator-activity/details`, and
`operator-activity/aggregates`. Maintenance follows the runbook dry-run and
backup flow.

## Agent And Compatibility Routes

Compatibility routes include `agent_status`, `agent_tasks`, `agent_actions`,
`agent_scheduled_tasks`, scheduled task writes, `run_full_card_enrichment`,
`cleanup_card_content`, `autofill_vehicle_data`, and `autofill_repair_order`.
The current owner-facing AI path is MCP/local API tools.

## Settings And Verification

Saved settings live in the compatibility path
`%APPDATA%\Minimal Kanban\settings.json`; explicit environment variables
override saved settings. Secrets are redacted in logs, but `settings.json` is
not system-encrypted.

Local API/MCP smoke:

```powershell
python scripts\check_live_connector.py --strict --skip-public-site --skip-public-write-protection --local-api-url http://127.0.0.1:41731 --mcp-url http://127.0.0.1:41831/mcp --operator-username $env:AUTOSTOP_SMOKE_OPERATOR_USERNAME --operator-password $env:AUTOSTOP_SMOKE_OPERATOR_PASSWORD --expect-admin
```

Route behavior lives in `tests/test_api.py`, `tests/test_service.py`,
`tests/test_mcp.py`, and focused tests around touched modules.
