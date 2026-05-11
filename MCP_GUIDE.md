# Руководство по MCP

MCP server даёт tool-based доступ к одной текущей AutoStop CRM board из ChatGPT, Responses API и совместимых MCP-клиентов.

MCP не дублирует бизнес-логику UI. Он вызывает local HTTP API и работает через тот же `CardService`.

```text
MCP tool call
  -> MCP adapter
  -> local HTTP API
  -> CardService
  -> JsonStore
```

Источник правды по tools: `src/minimal_kanban/mcp/server.py` и live `tools/list`.
Не фиксируйте количество tools в документации.

## Runtime Files

- `main_mcp.py`
- `src/minimal_kanban/mcp/server.py`
- `src/minimal_kanban/mcp/client.py`
- `src/minimal_kanban/mcp/runtime.py`
- `src/minimal_kanban/mcp/auth.py`
- `src/minimal_kanban/mcp/oauth_provider.py`
- `scripts/run_mcp_server.ps1`

## URL И Запуск

Локальный URL по умолчанию:

```text
http://127.0.0.1:41831/mcp
```

Запуск:

```powershell
.\scripts\run_mcp_server.ps1
```

или:

```powershell
python .\main_mcp.py
```

## Backend Selection

MCP server:

1. берёт `MINIMAL_KANBAN_BOARD_API_URL`, если переменная задана;
2. иначе ищет уже работающий local API;
3. иначе поднимает hidden backend сам.

Так MCP работает и рядом с открытой CRM, и как отдельный headless runtime.

## Environment

MCP runtime:

- `MINIMAL_KANBAN_MCP_HOST`
- `MINIMAL_KANBAN_MCP_PORT`
- `MINIMAL_KANBAN_MCP_PORT_FALLBACK_LIMIT`
- `MINIMAL_KANBAN_MCP_PATH`
- `MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL`
- `MINIMAL_KANBAN_MCP_BEARER_TOKEN`

Backend API:

- `MINIMAL_KANBAN_BOARD_API_URL`
- `MINIMAL_KANBAN_API_BEARER_TOKEN`

Saved settings: `%APPDATA%\Minimal Kanban\settings.json`.
Explicit env variables win.

## Auth

Local/dev clients may use bearer token.

For ChatGPT connector, production path can publish embedded OAuth/DCR metadata when bearer mode is enabled. In that flow the user links through ChatGPT instead of pasting bearer tokens manually.

## Optional AutostopManager

If `AutostopManager` is mounted next to CRM or `AUTOSTOP_MANAGER_PATH` points to it, the same MCP endpoint may also expose manager memory/source tools:

- `remember`
- `recall`
- `add_manager_task`
- `today_context`
- `manager_journal`
- `sync_knowledge_base`
- `probe_knowledge_base`
- `search_knowledge_base`
- `audit_knowledge_base`
- `lookup_original_parts`
- `recommend_automotive_sources`
- `recommend_fluid_maintenance_sources`
- `recommend_service_management_actions`

CRM remains the source of truth for cards, clients, vehicles, repair orders, files, payments and cashboxes. Manager memory is only for durable manager facts, decisions and knowledge navigation.

## Obsidian Knowledge Vault

For manager-agent work, the AutoStop Obsidian vault is the human-readable
knowledge layer for CRM/MCP/connector procedures:

- cloud vault: `C:\Users\User\Мой диск\Obsidian CRM\AutostopCRM`
- desktop mirror: `C:\Users\User\Desktop\Obsidian CRM\AutostopCRM`
- open first: `Home.md`, then `80_Codex\Codex interaction.md`

Use it for playbooks, source routing, Bases, and operator-readable notes. Do
not store full CRM exports, raw Gmail threads, credentials, bearer tokens,
cashbox ledgers, client databases, or copied licensed manuals there.

Manager CRM summaries may be written to Obsidian only as safe snapshots:
board load, cashbox balances/totals, repair-order counts, client-quality
signals, and shared-file metadata. Raw client rows, phone lists, VIN/license
tables, full cash journals, and full repair-order text remain live CRM data
unless the owner approves that exact cloud export.

## Рекомендуемый Порядок

Начинайте с коротких read-команд:

1. `ping_connector`
2. `get_connector_identity`
3. `bootstrap_context`
4. `get_runtime_status`, если неясны auth, tunnel или runtime
5. focused search/read
6. write только после определения target
7. read-back verification

Предпочитайте:

- `review_board` перед полным wall export;
- `get_cards(compact=true)` или compact snapshot перед тяжёлыми board reads;
- `search_cards` перед широким чтением доски;
- `suggest_clients_for_card` или `search_clients` перед `create_client`;
- `get_card_context` перед card writes;
- `get_card_log`, когда важны audit/recovery.

Тяжёлые reads используйте точечно:

- `get_gpt_wall`
- `get_board_content`
- `get_board_events` с большими limits
- full `get_card`
- large attachment/base64 reads

## Tool Groups

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

Clients:

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

## Правила Записи

- Перед write-action прочитайте live context.
- Пишите patch-only: меняйте только подтверждённые поля.
- После write-action перечитайте target и проверьте результат.
- Не move/archive/delete карточки, файлы, клиентов, оплаты, работы или материалы без явной команды владельца.
- Для клиента сначала search/suggest, потом create/link.
- Для документов используйте CRM PDF export, а не отдельный PDF-генератор агента.

## Board Summary

`set_card_board_summary(card_id, summary, actor_name=None)` обновляет короткое AI-managed preview на доске.

Правила:

- максимум 5 непустых строк;
- максимум 560 символов;
- без телефона, VIN, полного имени клиента, raw diagnostic dump или длинной жалобы;
- не меняет `title` или `description`;
- после обычных card edits обновите summary и проверьте `board_summary_stale=false`.

Рекомендуемая форма:

```text
Что сейчас: ...
Стадия: ...
Следующее действие: ...
Важно: ...
```

## Команда `Приберись`

`Приберись`, `прибейсь`, `прибери доску`, `обслужи доску` - agent procedures, а не один MCP tool.

Порядок:

1. прочитать live card/board context;
2. patch-only обновить подтверждённые поля;
3. сохранить operator data, works, materials, prices, payments, files and historical notes;
4. не move/archive cards без отдельной явной команды;
5. refresh `board_summary` after card content/profile/tag changes;
6. reread and verify.

## VIN/Profile Enrichment

If VIN/chassis/frame exists and aggregate profile fields are empty:

- use local knowledge and `lookup_original_parts` first when available;
- use internet search only when current source-backed confirmation is needed;
- fill only confirmed `engine_model`, `gearbox_model`, `drivetrain` and source metadata;
- preserve `manual_fields`;
- put uncertainty into `oem_notes` or `tentative_fields`, not into confirmed fields.

## Not MCP Runtime Tools

These remain API/UI/compatibility paths:

- `autofill_vehicle_data`
- `autofill_repair_order`
- `cleanup_card_content`

Не представляйте их как обычные ChatGPT connector tools.

## Проверки

Local:

```powershell
.\scripts\run_mcp_server.ps1
python -m unittest tests.test_mcp tests.test_mcp_main tests.test_connection_card -v
```

Local smoke:

```powershell
python scripts\check_live_connector.py --strict --skip-public-site --skip-public-write-protection --local-api-url http://127.0.0.1:41731 --mcp-url http://127.0.0.1:41831/mcp --operator-username admin --operator-password admin --expect-admin
```

Production checks live in `docs/OPERATIONS_RUNBOOK.md`.
