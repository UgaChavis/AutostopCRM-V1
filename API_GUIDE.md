# Руководство по API

Локальный HTTP API обслуживает UI, desktop shell, MCP adapter, Telegram AI worker и локальные интеграции.

Источник правды по маршрутам: `src/minimal_kanban/api/server.py`.

## База

- адрес по умолчанию: `http://127.0.0.1:41731`
- health-check: `GET /api/health`
- формат JSON: request body -> response envelope
- большинство JSON-маршрутов вызываются через `POST`
- read-only маршруты, отмеченные ниже как `GET|POST`, также принимают query params через `GET`
- прямые download routes возвращают файл, а не JSON envelope

Если задан `MINIMAL_KANBAN_API_BEARER_TOKEN`, все маршруты кроме `GET /api/health` требуют:

```http
Authorization: Bearer <token>
```

## Envelope

Успех:

```json
{"ok": true, "data": {}, "error": null, "meta": {"request_id": "uuid"}}
```

Ошибка:

```json
{"ok": false, "data": null, "error": {"code": "validation_error", "message": "..."}, "meta": {"request_id": "uuid"}}
```

Типовые `error.code`: `validation_error`, `not_found`, `unauthorized`, `archived_card`, `storage_limit_exceeded`, `internal_error`.

## Доска и карточки

Read:

- `GET|POST /api/list_columns`
- `GET|POST /api/get_cards`
- `GET|POST /api/get_card`
- `POST /api/get_card_context`
- `GET|POST /api/get_card_log`
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

- `POST /api/create_column`, `/api/rename_column`, `/api/move_column`, `/api/delete_column`
- `POST /api/create_card`, `/api/update_card`, `/api/set_card_deadline`, `/api/set_card_indicator`
- `POST /api/move_card`, `/api/bulk_move_cards`, `/api/mark_card_ready`, `/api/mark_card_seen`
- `POST /api/archive_card`, `/api/restore_card`
- `POST /api/create_sticky`, `/api/update_sticky`, `/api/move_sticky`, `/api/delete_sticky`
- `POST /api/update_board_settings`

`review_board` и compact snapshot предпочтительнее широких exports. `get_board_content`, `get_board_events` и `get_gpt_wall` нужны для полного агентского контекста, когда точечных reads недостаточно.

## Board Summary

`POST /api/set_card_board_summary`

Payload:

```json
{
  "card_id": "CARD_ID",
  "summary": "Что сейчас: ...\nСтадия: ...\nСледующее действие: ..."
}
```

Правила:

- до 5 непустых строк;
- до 560 символов;
- без телефона, VIN, полного имени клиента, сырых диагностических дампов и длинных жалоб;
- не меняет `title` или `description`;
- пишет событие `board_summary_changed`.

После обычных изменений карточки агент должен отдельно обновить `board_summary` и проверить `board_summary_stale=false`.

## Клиенты

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

Перед созданием клиента используйте search/suggest. `client_id` связывает карточку с клиентом, `client_vehicle_id` - с конкретным сохранённым автомобилем. Деструктивные удаления и `overwrite_card_fields=true` требуют явного намерения владельца.

## Заказ-наряды и печать

Read:

- `GET|POST /api/list_repair_orders`
- `POST /api/get_repair_order`
- `POST /api/get_repair_order_text`
- `POST /api/get_repair_order_print_workspace`
- `POST /api/get_inspection_sheet_form`
- `GET /api/repair_order_text?card_id=CARD_ID`

Write / generate:

- `POST /api/update_repair_order`, `/api/set_repair_order_status`
- `POST /api/replace_repair_order_works`, `/api/replace_repair_order_materials`
- `POST /api/save_inspection_sheet_form`, `/api/autofill_inspection_sheet_form`
- `POST /api/preview_repair_order_print_documents`
- `POST /api/export_repair_order_print_pdf`
- `POST /api/print_repair_order_documents`
- `POST /api/save_print_template`, `/api/duplicate_print_template`, `/api/delete_print_template`
- `POST /api/set_default_print_template`, `/api/save_print_module_settings`

Поддерживаемые документы: `repair_order`, `vehicle_acceptance_act`, `invoice`, `invoice_factura`, `inspection_sheet`, `completion_act`, `parts_sale`.

Для агентов основной путь PDF - `export_repair_order_print_pdf` или MCP `download_repair_order_print_pdf`, без отдельного PDF-генератора.

Номер заказ-наряда присваивается один раз при первом создании/открытии
заказ-наряда и дальше не меняется через UI/API/MCP. Обычные update routes
игнорируют пустой или отсутствующий `number` у уже созданного заказ-наряда и
отклоняют попытку заменить номер ошибкой `repair_order_number_immutable`.
Исторические расхождения проверяются только read-only dry-run отчётом
`scripts/repair_order_number_audit.py`; исправления выполняются отдельной
maintenance-процедурой после backup и подтверждения владельца.

## Кассы, сотрудники, payroll

Read:

- `GET|POST /api/list_cashboxes`
- `GET|POST /api/get_cashbox`
- `GET|POST /api/get_cash_journal`
- `GET|POST /api/list_employees`
- `GET|POST /api/get_payroll_report`
- `GET|POST /api/get_employee_salary_ledger`
- `GET|POST /api/get_employee_salary_report`

Write:

- `POST /api/create_cashbox`, `/api/reorder_cashboxes`, `/api/create_cashbox_transfer`, `/api/delete_cashbox`
- `POST /api/create_cash_transaction`, `/api/create_employee_salary_transaction`, `/api/cancel_last_cash_transaction`
- `POST /api/save_employee`, `/api/toggle_employee`, `/api/delete_employee`

`get_cash_journal` возвращает structured entries/groups и Markdown-текст для human review.
Browser UI использует structured `cash_journal.v2` как основной источник:
операции рендерятся батчами, пары перемещений отображаются одной строкой, а
legacy-пары без `transfer_group_id` сопоставляются на клиенте по дате, времени,
сумме, оператору и направлению касс. `finance_audit.v1` остаётся внутренней
read-only/diagnostic схемой и не имеет пользовательского entrypoint в кассовом
UI.

## Файлы

Card attachments:

- `POST /api/add_card_attachment`, `/api/remove_card_attachment`
- `POST /api/list_card_attachments`, `/api/get_card_attachment`, `/api/read_card_attachment`
- `GET /api/attachment?card_id=CARD_ID&attachment_id=ATTACHMENT_ID`

Shared files:

- `GET|POST /api/list_shared_files`
- `GET|POST /api/get_shared_file_info`
- `POST /api/fetch_shared_file`, `/api/upload_shared_file`, `/api/rename_shared_file`
- `POST /api/delete_shared_file`, `/api/copy_shared_file`, `/api/paste_shared_file`
- `POST /api/paste_shared_files_from_clipboard`, `/api/update_shared_file_position`
- `GET /api/shared_file?file_id=FILE_ID`

Ограничение общей папки: 500 MB. Backend блокирует опасные executable/script расширения. Delete destructive.

## Операторы

- `POST /api/login_operator`
- `POST /api/logout_operator`
- `GET|POST /api/get_operator_profile`
- `GET|POST /api/list_operator_users`
- `POST /api/save_operator_user`
- `POST /api/delete_operator_user`
- `GET|POST /api/get_operator_user_report`
- `POST /api/open_card`

Admin-only routes проверяются через `OperatorAuthService`.

## Agent / compatibility

Read:

- `GET|POST /api/agent_status`
- `GET|POST /api/agent_tasks`
- `GET|POST /api/agent_actions`
- `GET|POST /api/agent_scheduled_tasks`

Write:

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

Эти пути оставлены для API/UI/compatibility. Новый owner-facing AI-контур - Telegram AI и MCP/local API tools.

## Settings

Settings live in `%APPDATA%\Minimal Kanban\settings.json`.

Relevant modules:

- `src/minimal_kanban/settings_models.py`
- `src/minimal_kanban/settings_store.py`
- `src/minimal_kanban/settings_service.py`
- `src/minimal_kanban/ui/settings_window.py`

Env variables override saved settings. Secrets redact in logs, but `settings.json` is not system-encrypted.

## Verification

Local API/MCP smoke:

Используйте smoke-учётку из окружения, а не default admin credentials:

```powershell
python scripts\check_live_connector.py --strict --skip-public-site --skip-public-write-protection --local-api-url http://127.0.0.1:41731 --mcp-url http://127.0.0.1:41831/mcp --operator-username $env:AUTOSTOP_SMOKE_OPERATOR_USERNAME --operator-password $env:AUTOSTOP_SMOKE_OPERATOR_PASSWORD --expect-admin
```

For route-level behavior, use `tests/test_api.py`, `tests/test_service.py`, `tests/test_mcp.py`, and focused tests around the touched module.
