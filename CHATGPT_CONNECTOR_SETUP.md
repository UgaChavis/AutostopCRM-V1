# Подключение AutoStop CRM к ChatGPT

Файл должен оставаться под этим именем: его копируют release/runtime-пути и проверяют тесты.

## Что подключается

ChatGPT подключается к публичному MCP endpoint текущей AutoStop CRM board:

```text
https://crm.autostopcrm.ru/mcp
```

Если endpoint работает с bearer auth, сервер публикует embedded OAuth/DCR metadata, и ChatGPT проходит linking flow. Для Responses API и ручных MCP-клиентов bearer token можно передавать напрямую.

## Что должно быть включено

В настройках интеграции CRM:

- integration enabled;
- local API enabled;
- MCP enabled;
- public HTTPS base URL, tunnel URL или full MCP URL override;
- MCP auth mode и token, если нужен protected endpoint.

## ChatGPT connector flow

1. Убедитесь, что итоговый MCP URL начинается с `https://`.
2. В ChatGPT откройте Apps & Connectors и создайте новый MCP connector.
3. Name: `AutoStop CRM`.
4. Description: `Автосервисная CRM с доской, клиентами, заказ-нарядами, кассами и файлами`.
5. URL: итоговый `.../mcp`.
6. Если ChatGPT попросит linking, пройдите embedded OAuth flow.
7. Первый вызов в новом чате: `ping_connector`.
8. Второй вызов: `bootstrap_context`.
9. При сомнениях по tunnel/auth/runtime вызвать `get_runtime_status`.

## Проверочные tools

Точный список tools проверяйте live через `tools/list`, connection card или `scripts/check_live_connector.py`.

Для smoke обычно достаточно увидеть:

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

Если рядом подключен `AutostopManager`, могут появиться `today_context`, `remember`, `lookup_original_parts` и другие manager-memory/source tools.

## Правила для агента

- Работать только с текущей AutoStop CRM board.
- Перед write-action прочитать live context.
- Для клиента сначала `suggest_clients_for_card` или `search_clients`, потом create/link.
- Для уборки карточки обновлять подтверждённые поля через `update_card`, затем отдельно `set_card_board_summary`.
- Не двигать и не архивировать карточки по команде `Приберись`, если владелец отдельно не попросил это действие.
- Для VIN/profile enrichment писать только source-backed факты и не перетирать manual fields.
- Для документов использовать CRM PDF tool, а не отдельный PDF-генератор.

## Responses API

Используйте тот же `server_url`. Список allowed tools лучше брать из live `tools/list`, а не из статического JSON-примера.

Если MCP работает в bearer mode, передавайте `authorization` в MCP tool payload.

## Безопасность

- Connector имеет доступ к одной текущей CRM board.
- Bearer token не нужно вставлять в обычный ChatGPT connector, если embedded OAuth linking работает.
- Для более строгого production auth следующим шагом нужен отдельный IdP/authorization layer.
