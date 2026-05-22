# AutoStop CRM API Guide

The local HTTP API serves the browser/desktop UI, MCP adapter, Telegram AI
worker, smoke scripts, and local integrations.

Source of truth: `src/minimal_kanban/api/server.py`. This guide groups the
routes and documents safety-critical contracts; it is not a replacement for
route-level tests.

## Base Contract

- Local default base URL: `http://127.0.0.1:41731`.
- Production public base URL: `https://crm.autostopcrm.ru`.
- Health check: `GET /api/health`.
- Most JSON routes accept `POST`; read-only routes documented as `GET|POST`
  also accept query parameters through `GET`.
- Download routes return file bytes instead of the JSON envelope.

If `MINIMAL_KANBAN_API_BEARER_TOKEN` is set, every route except
`GET /api/health` requires:

```http
Authorization: Bearer <token>
```

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

Read:

- `GET|POST /api/list_columns`
- `GET|POST /api/get_cards`
- `GET|POST /api/get_card`
- `GET|POST /api/get_card_log`
- `POST /api/get_card_context`
- `GET|POST /api/get_board_revision`
- `GET|POST /api/get_board_snapshot`
- `GET|POST /api/get_board_context`
- `GET|POST /api/review_board`
- `GET|POST /api/get_board_content`
- `GET|POST /api/get_board_events`
- `GET|POST /api/get_gpt_wall`
- `GET|POST /api/search_cards`
- `GET|POST /api/list_archived_cards`
- `GET|POST /api/list_overdue_cards`

Write:

- `POST /api/create_column`, `/api/rename_column`, `/api/move_column`,
  `/api/delete_column`
- `POST /api/create_card`, `/api/update_card`, `/api/set_card_deadline`,
  `/api/set_card_indicator`, `/api/set_card_board_summary`
- `POST /api/move_card`, `/api/bulk_move_cards`, `/api/mark_card_ready`,
  `/api/mark_card_seen`
- `POST /api/archive_card`, `/api/restore_card`
- `POST /api/create_sticky`, `/api/update_sticky`, `/api/move_sticky`,
  `/api/delete_sticky`
- `POST /api/update_board_settings`

Use compact board reads for repeated UI/agent refreshes:
`get_board_revision?compact=1&include_archive=0`,
`get_board_snapshot?compact=1&include_archive=0`, or
`get_cards(compact=true)`.

`get_card_log` returns compact event details by default. For maintenance/debug
reads, pass `include_full_details=true`; the server hydrates archived
`before/after` values from `audit-archive` when the active event details were
compacted out of `state.json`.

## Board Summary

`POST /api/set_card_board_summary`

`summary` is the short AI-managed board preview, separate from full
`description`.

Rules:

- up to 5 non-empty lines;
- up to 560 characters;
- no phone, VIN, full client name, raw diagnostic dump, or long complaint text;
- does not modify `title` or `description`;
- writes `board_summary_changed`.

After normal changes to `title`, `description`, `tags`, or `vehicle_profile`,
agents should refresh `board_summary` and verify `board_summary_stale=false`.

## Clients And Vehicles

Read:

- `GET|POST /api/list_clients`
- `GET|POST /api/search_clients`
- `GET|POST /api/get_client`
- `GET|POST /api/get_client_stats`
- `GET|POST /api/suggest_clients_for_card`

Write:

- `POST /api/create_client`, `/api/update_client`, `/api/delete_client`
- `POST /api/link_card_to_client`, `/api/unlink_card_from_client`
- `POST /api/upsert_client_vehicle`, `/api/delete_client_vehicle`

Before creating a client, use search/suggest. `client_id` links a card to a
client; `client_vehicle_id` links the card to a specific saved vehicle. Deleting
clients/vehicles and overwriting card fields are destructive and require clear
owner intent.

## Repair Orders And Printing

Read:

- `GET|POST /api/list_repair_orders`
- `POST /api/get_repair_order`
- `POST /api/get_repair_order_text`
- `POST /api/get_repair_order_print_workspace`
- `POST /api/get_inspection_sheet_form`
- `GET /api/repair_order_text?card_id=CARD_ID`

Write/generate:

- `POST /api/update_repair_order`, `/api/set_repair_order_status`
- `POST /api/replace_repair_order_works`,
  `/api/replace_repair_order_materials`
- `POST /api/save_inspection_sheet_form`,
  `/api/autofill_inspection_sheet_form`
- `POST /api/preview_repair_order_print_documents`
- `POST /api/export_repair_order_print_pdf`
- `POST /api/print_repair_order_documents`
- `POST /api/save_print_template`, `/api/duplicate_print_template`,
  `/api/delete_print_template`
- `POST /api/set_default_print_template`, `/api/save_print_module_settings`

Maintenance-only:

- `POST /api/correct_repair_order_number`

Repair-order number is immutable after first assignment. Normal UI/API/MCP
updates must not replace it. Historical fixes require the runbook maintenance
flow: backup, read-only/dry-run audit, owner approval, and post-fix checks.

Supported print documents include repair order, vehicle acceptance act, invoice,
invoice factura, inspection sheet, completion act, and parts sale. Agents should
use CRM PDF export instead of building independent PDFs.

## Cashboxes, Finance, Employees, Payroll

Read:

- `GET|POST /api/list_cashboxes`
- `GET|POST /api/get_cashbox`
- `GET|POST /api/get_cash_journal`
- `GET|POST /api/finance_audit`
- `GET|POST /api/list_employees`
- `GET|POST /api/get_payroll_report`
- `GET|POST /api/get_employee_salary_ledger`
- `GET|POST /api/get_employee_salary_report`

Write:

- `POST /api/create_cashbox`, `/api/reorder_cashboxes`,
  `/api/create_cashbox_transfer`, `/api/delete_cashbox`
- `POST /api/create_cash_transaction`,
  `/api/create_employee_salary_transaction`,
  `/api/cancel_last_cash_transaction`
- `POST /api/save_employee`, `/api/toggle_employee`, `/api/delete_employee`

Maintenance-only:

- `POST /api/finance_audit/apply_safe_fixes`

Manual `create_cash_transaction` expenses require `note` with at least 10
visible characters. Income operations, transfers, salary payouts, and repair
order payments keep their existing note behavior.

`get_cashbox` supports operation pagination:

- `transaction_limit` - page size;
- `transaction_offset` - offset;
- response `meta.has_more` tells whether another page exists.

If pagination parameters are omitted, the route keeps compatible first-page
behavior.

`get_cash_journal` returns structured `cash_journal.v2` data for UI and agents.
The operator UI is journal-first: compact operation rows, transfer pairs shown
as one logical `cashbox -> cashbox` row, and no visible finance-audit entrypoint.

`finance_audit.v1` is internal read-only diagnostics. Apply safe fixes only
through the audit-first runbook path.

`get_employee_salary_report` returns `employee_salary_report.v3` for a selected
employee and month `YYYY-MM`; it includes closed repair-order works and accrued
amounts, not advances or salary scheme setup.

## Files

Card attachments:

- `POST /api/add_card_attachment`, `/api/remove_card_attachment`
- `POST /api/list_card_attachments`, `/api/get_card_attachment`,
  `/api/read_card_attachment`
- `GET /api/attachment?card_id=CARD_ID&attachment_id=ATTACHMENT_ID`

Shared files:

- `GET|POST /api/list_shared_files`
- `GET|POST /api/get_shared_file_info`
- `POST /api/fetch_shared_file`, `/api/upload_shared_file`,
  `/api/rename_shared_file`
- `POST /api/delete_shared_file`, `/api/copy_shared_file`,
  `/api/paste_shared_file`
- `POST /api/paste_shared_files_from_clipboard`,
  `/api/update_shared_file_position`
- `GET /api/shared_file?file_id=FILE_ID`

Shared file storage is capped at 500 MB. Dangerous executable/script extensions
are blocked. Delete is destructive.

## Operators

- `POST /api/login_operator`
- `POST /api/logout_operator`
- `GET|POST /api/get_operator_profile`
- `GET|POST /api/list_operator_users`
- `POST /api/save_operator_user`
- `POST /api/delete_operator_user`
- `GET|POST /api/get_operator_user_report`
- `POST /api/open_card`

Admin-only routes go through `OperatorAuthService`. Smoke checks should use
`AUTOSTOP_SMOKE_OPERATOR_USERNAME` and `AUTOSTOP_SMOKE_OPERATOR_PASSWORD`, not
hard-coded default credentials.

## Agent And Compatibility Routes

Read:

- `GET|POST /api/agent_status`
- `GET|POST /api/agent_tasks`
- `GET|POST /api/agent_actions`
- `GET|POST /api/agent_scheduled_tasks`

Write/compatibility:

- `POST /api/agent_enqueue_task`
- `POST /api/save_agent_scheduled_task`
- `POST /api/delete_agent_scheduled_task`
- `POST /api/pause_agent_scheduled_task`
- `POST /api/resume_agent_scheduled_task`
- `POST /api/run_agent_scheduled_task`
- `POST /api/run_full_card_enrichment`
- `POST /api/cleanup_card_content`
- `POST /api/autofill_vehicle_data`
- `POST /api/autofill_repair_order`

These routes remain for API/UI compatibility. The current owner-facing AI path
is Telegram AI and MCP/local API tools.

## Settings And Environment

Saved settings live in `%APPDATA%\Minimal Kanban\settings.json`. Relevant code:

- `src/minimal_kanban/settings_models.py`
- `src/minimal_kanban/settings_store.py`
- `src/minimal_kanban/settings_service.py`
- `src/minimal_kanban/ui/settings_window.py`

Explicit environment variables override saved settings. Secrets are redacted in
logs, but `settings.json` is not system-encrypted.

## Verification

Local API/MCP smoke:

```powershell
python scripts\check_live_connector.py --strict --skip-public-site --skip-public-write-protection --local-api-url http://127.0.0.1:41731 --mcp-url http://127.0.0.1:41831/mcp --operator-username $env:AUTOSTOP_SMOKE_OPERATOR_USERNAME --operator-password $env:AUTOSTOP_SMOKE_OPERATOR_PASSWORD --expect-admin
```

Route-level behavior lives in `tests/test_api.py`, `tests/test_service.py`,
`tests/test_mcp.py`, and focused tests around the touched module.
