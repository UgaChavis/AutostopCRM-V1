# Подключение AutoStop CRM к ChatGPT

Файл должен оставаться под именем `CHATGPT_CONNECTOR_SETUP.md`: его используют
release/runtime-пути и проверяют тесты.

## Что Подключается

ChatGPT подключается к публичному MCP endpoint текущей AutoStop CRM board:

```text
https://crm.autostopcrm.ru/mcp
```

Connector работает только с этой CRM board. Источник правды по tools - live
`tools/list`, `src/minimal_kanban/mcp/server.py` и `MCP_GUIDE.md`.

Если MCP endpoint работает с bearer auth, сервер может публиковать embedded
OAuth/DCR metadata, и ChatGPT проходит linking flow. Responses API и ручные MCP
клиенты могут передавать bearer auth напрямую.

## Что Должно Быть Включено

В настройках CRM integration:

- integration enabled;
- local API enabled;
- MCP enabled;
- public HTTPS base URL или full MCP URL override;
- MCP auth mode и token, если endpoint защищён.

Итоговый URL для ChatGPT должен начинаться с `https://` и заканчиваться на
`/mcp`.

## ChatGPT Flow

1. Откройте ChatGPT Apps & Connectors.
2. Создайте новый MCP connector.
3. Name: `AutoStop CRM`.
4. Description:
   `Автосервисная CRM с доской, клиентами, заказ-нарядами, кассами и файлами`.
5. URL: `https://crm.autostopcrm.ru/mcp`.
6. Если ChatGPT просит linking, пройти embedded OAuth flow.
7. Первый вызов: `ping_connector`.
8. Второй вызов: `bootstrap_context(compact=true)`.
9. При сомнениях по tunnel/auth/runtime: `get_runtime_status`.

## Smoke Tools

Для базового smoke достаточно увидеть:

- `ping_connector`
- `bootstrap_context`
- `get_runtime_status`
- `review_board`
- `search_cards`
- `get_card_context`
- `search_clients`
- `list_shared_files`
- `download_repair_order_print_pdf`
- `create_card`
- `update_card`
- `set_card_board_summary`
- `move_card`

Если рядом подключен `AutostopManager`, могут появиться optional manager tools,
например `estimate_repair_work_cost`, `lookup_original_parts`, `today_context`,
`agent_brief`, `remember`, `system_audit`. Сравнивайте tool names, а не только
общее количество.

## Agent Rules

- Работать только с текущей AutoStop CRM board.
- Перед write-action читать live context.
- После write-action читать target повторно и проверять результат.
- Для клиента сначала `suggest_clients_for_card` или `search_clients`, потом
  create/link.
- Для уборки карточки обновлять подтверждённые поля через `update_card`, затем
  отдельно `set_card_board_summary`.
- Не двигать и не архивировать карточки по команде `Приберись`, если владелец
  отдельно не попросил это действие.
- Для VIN/profile enrichment писать только source-backed факты и не перетирать
  manual fields.
- Для документов использовать CRM PDF tool, а не отдельный PDF generator.
- Finance safe-fixes and repair-order number corrections are maintenance-only.

## Responses API

Use the same `server_url`. Do not rely on a static JSON tool list; fetch live
tools or use connector discovery. In bearer mode, pass authorization in the MCP
tool payload.

## Manager Knowledge

For manager-agent work, use the AutoStop Obsidian vault as a knowledge layer,
not as CRM state storage:

```text
C:\Users\User\Мой диск\Obsidian CRM\AutostopCRM
```

Start with `Home.md`, then `80_Codex\Codex interaction.md`.

CRM remains source of truth for live cards, clients, vehicles, repair orders,
files, payments, and cashboxes. Do not move raw client databases, phone rows,
VIN/license tables, cashbox ledgers, credentials, bearer tokens, or full
repair-order text into Obsidian without explicit owner approval for that export.

## Security

- Connector scope is one current CRM board.
- Do not paste bearer tokens into ordinary docs or chats.
- Do not use stale tunnel URLs when `https://crm.autostopcrm.ru/mcp` is healthy.
- Public anonymous writes must remain blocked.
- For stricter production auth, add a dedicated identity provider/authorization
  layer as a separate project.
