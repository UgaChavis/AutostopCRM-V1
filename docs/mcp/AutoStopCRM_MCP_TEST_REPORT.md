# AutoStop CRM MCP Test Report

Дата: 19.04.2026

## Что проверяется

- Все 49 MCP-команд объявлены в `tools/list`.
- Все 49 MCP-команд имеют прямой regression call через реальный MCP session.
- Полный write/destructive-прогон выполняется только на временной локальной доске.
- Production smoke должен быть read-only.

## Локальный sandbox coverage

Покрыты группы:

- Identity/bootstrap/runtime: `get_connector_identity`, `ping_connector`, `bootstrap_context`, `get_runtime_status`.
- Board context: `get_board_context`, `get_board_snapshot`, `review_board`.
- Hidden machine wall: `get_board_content`, `get_board_events`, `get_gpt_wall`.
- Cards: `create_card`, `get_cards`, `get_card`, `get_card_context`, `update_card`, `cleanup_card_content`, `search_cards`, `get_card_log`.
- Workflow: `move_card`, `bulk_move_cards`, `set_card_deadline`, `set_card_indicator`, `archive_card`, `restore_card`, `list_archived_cards`, `list_overdue_cards`.
- Columns/stickies: `list_columns`, `create_column`, `rename_column`, `delete_column`, `create_sticky`, `update_sticky`, `move_sticky`, `delete_sticky`.
- Vehicle/repair order: `autofill_vehicle_data`, `autofill_repair_order`, `update_repair_order`, `replace_repair_order_works`, `replace_repair_order_materials`, `set_repair_order_status`, `list_repair_orders`, `get_repair_order`, `get_repair_order_text`.
- Cashboxes: `list_cashboxes`, `create_cashbox`, `get_cashbox`, `create_cash_transaction`, `delete_cashbox`.

## Важные проверки поведения

- `get_board_content` возвращает Markdown с `# AutoStop CRM Board Content`.
- `get_board_events` возвращает Markdown с `# AutoStop CRM Event Log`.
- `text_format=markdown` присутствует в MCP meta.
- `get_board_events` по умолчанию использует `event_limit=100`.
- `delete_cashbox` работает только для пустой кассы; касса с движениями не удаляется.
- `delete_column` блокируется для непустого столбца.
- Невалидные `card_id`, `column_id`, `cashbox_id` возвращают структурированные ошибки, а не transport failure.

## Команды проверки

```powershell
.\scripts\run_checks.ps1
python -m unittest tests.test_mcp tests.test_mcp_main
python -m unittest tests.test_service tests.test_api
```

## Production smoke policy

На production разрешены только read-only проверки:

- `ping_connector`
- `bootstrap_context`
- `get_runtime_status`
- `get_board_content`
- `get_board_events`
- `list_cashboxes`

Write/destructive-команды на production не выполняются в рамках ревизии MCP.

## Статус

Локальный статус:

- `.\scripts\run_checks.ps1`: passed.
- `python -m unittest tests.test_mcp tests.test_mcp_main`: 35 tests passed.
- `python -m unittest tests.test_service tests.test_api`: 190 tests passed.

Production smoke и финальный release commit фиксируются в итоговом ответе агента после deploy.
