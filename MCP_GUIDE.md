# Руководство по MCP

Этот документ описывает отдельный MCP server, который добавлен поверх текущей kanban-доски.

## Зачем нужен MCP server

MCP server нужен для прямого tool-based доступа к доске из:

- ChatGPT app / Developer mode
- Responses API
- собственных клиентов, умеющих работать с MCP

MCP server не двигает мышь и не эмулирует UI.

Он работает правильно по архитектуре:

```text
MCP tool call
  ->
адаптер MCP
  ->
локальный API доски
  ->
CardService
  ->
JsonStore
```

## Что именно добавлено

Ключевые файлы:

- `src/minimal_kanban/mcp/client.py`
- `src/minimal_kanban/mcp/server.py`
- `src/minimal_kanban/mcp/runtime.py`
- `src/minimal_kanban/mcp/auth.py`
- `src/minimal_kanban/mcp/main.py`
- `scripts/run_mcp_server.ps1`
- `main_mcp.py`

## Доступные MCP tools

Текущий runtime-tool inventory: `50` tools.

## Как выбирать команды без лишнего payload

Сейчас у коннектора уже достаточно инструментов, поэтому полезнее не добавлять новые, а вызывать существующие в правильном порядке.

### Легкие команды для старта и повседневной работы

- `ping_connector`
- `get_connector_identity`
- `bootstrap_context`
- `get_runtime_status`
- `get_board_context`
- `review_board`
- `list_columns`
- `search_cards`
- `get_cards(compact=true)`

### Команды для точечного чтения

- `get_card_context`
- `get_card`
- `list_card_attachments`
- `get_card_attachment`
- `read_card_attachment`
- `get_card_log`
- `get_repair_order`
- `list_repair_orders`
- `get_repair_order_text`
- `list_overdue_cards`
- `list_archived_cards`

### Тяжелые команды, которые стоит вызывать редко

- `get_gpt_wall`
- `get_board_content`
- `get_board_snapshot`
- `get_board_events`
- `get_card` в полном режиме
- `get_card_context` с длинной историей

Правило по умолчанию:

1. сначала брать короткий диагностический инструмент;
2. потом узкий поиск или фокусный read;
3. только после этого открывать тяжелый экспорт.

Для тяжелых инструментов держите лимиты как можно ниже:

- `get_gpt_wall`: `include_archived=false`, `event_limit=10..20`
- `get_board_content`: `include_archived=false`, `view_mode=agent`
- `get_board_events`: `event_limit=20..50` для обычной работы
- `get_board_snapshot`: `compact=true`, `include_archive=false`, `archive_limit` минимально нужный
- `get_card_context`: `event_limit=5..20`, `include_repair_order_text=false`, если текст не нужен

### Служебные и диагностические

- `ping_connector`
- `get_connector_identity`
- `bootstrap_context`
- `get_runtime_status`

### Доска и карточки

- `list_columns`
- `create_column`
- `rename_column`
- `delete_column`
- `get_cards`
- `get_card`
- `get_card_context`
- `list_card_attachments`
- `get_card_attachment`
- `read_card_attachment`
- `get_board_snapshot`
- `get_board_context`
- `review_board`
- `get_board_content`
- `get_board_events`
- `get_gpt_wall`
- `get_card_log`
- `list_archived_cards`
- `search_cards`
- `list_overdue_cards`
- `create_card`
- `update_card`
- `set_card_deadline`
- `set_card_indicator`
- `move_card`
- `bulk_move_cards`
- `archive_card`
- `restore_card`

### Вложения карточек

- `list_card_attachments`
- `get_card_attachment`
- `read_card_attachment`

Правильный порядок чтения вложений:

1. `get_card_context(card_id)` или `get_card(card_id)`, чтобы понять, есть ли вложения.
2. `list_card_attachments(card_id)`, чтобы получить безопасный список файлов без байтов.
3. `get_card_attachment(card_id, attachment_id)`, если нужны размер, `sha256`, тип и download path.
4. `read_card_attachment(card_id, attachment_id, mode="preview"|"text"|"base64")`, только для выбранного файла.

Ограничения:

- `read_card_attachment` возвращает bounded text для TXT/DOCX/XLSX и best-effort text для простых PDF.
- Изображения не проходят OCR внутри CRM; инструмент возвращает размеры и может отдать `base64/data_url`, если включить `include_base64=true` или `mode="base64"`.
- Не включайте base64 по умолчанию. Для изображений используйте его только когда агент действительно будет анализировать файл vision-моделью.
- Держите `max_chars` и `max_base64_bytes` минимально достаточными.

### Sticky notes

- `create_sticky`
- `update_sticky`
- `move_sticky`
- `delete_sticky`

### Repair orders

- `list_repair_orders`
- `get_repair_order`
- `get_repair_order_text`
- `update_repair_order`
- `set_repair_order_status`
- `replace_repair_order_works`
- `replace_repair_order_materials`

### Cashboxes

- `list_cashboxes`
- `get_cashbox`
- `create_cashbox`
- `delete_cashbox`
- `create_cash_transaction`

### Board settings

- `update_board_settings`

### Что не входит в MCP runtime

- `autofill_vehicle_data`
- `autofill_repair_order`

Эти автозаполнения остаются API/UI-only surface и не регистрируются в MCP runtime.

## Как MCP server выбирает backend

Порядок работы такой:

1. Проверяет переменную `MINIMAL_KANBAN_BOARD_API_URL`.
2. Если она задана, работает с этим локальным API.
3. Если переменная не задана, пытается найти уже работающий API доски на стандартных портах.
4. Если API не найден, поднимает скрытый backend сам и использует его.

Это сделано для двух сценариев:

- приложение доски уже открыто, и MCP должен менять живую доску
- приложение не открыто, но нужно управлять той же доской через то же хранилище

## Как запустить MCP server

Самый простой способ на Windows:

```powershell
.\scripts\run_mcp_server.ps1
```

Прямой Python-запуск:

```powershell
python .\main_mcp.py
```

или:

```powershell
python -m minimal_kanban.mcp.main
```

## Какой адрес использует MCP server

По умолчанию:

- host: `127.0.0.1`
- port: `41831`
- path: `/mcp`

Полный локальный URL по умолчанию:

```text
http://127.0.0.1:41831/mcp
```

## Переменные окружения MCP

### Базовые

- `MINIMAL_KANBAN_MCP_HOST`
- `MINIMAL_KANBAN_MCP_PORT`
- `MINIMAL_KANBAN_MCP_PORT_FALLBACK_LIMIT`
- `MINIMAL_KANBAN_MCP_PATH`

### Подключение к backend

- `MINIMAL_KANBAN_BOARD_API_URL`
- `MINIMAL_KANBAN_API_BEARER_TOKEN`

### Защита MCP server

- `MINIMAL_KANBAN_MCP_BEARER_TOKEN`
- `MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL`

## Авторизация

### Что уже реализовано

В коде уже есть простой bearer token режим:

- локальный API можно защитить через `MINIMAL_KANBAN_API_BEARER_TOKEN`
- сам MCP server можно защитить через `MINIMAL_KANBAN_MCP_BEARER_TOKEN`

Если `MINIMAL_KANBAN_MCP_BEARER_TOKEN` задан, то к MCP server нужно обращаться с:

```http
Authorization: Bearer ваш_секрет
```

### Что важно понимать

Такой bearer token режим подходит для:

- локальной разработки
- тестов
- прямой интеграции через Responses API
- собственных клиентов

Для публичного подключения в ChatGPT workspace OpenAI рекомендует OAuth и dynamic client registration. Структура проекта уже подготовлена под следующий шаг, но полноценный OAuth provider в этом этапе ещё не добавлен.

## Как тестировать MCP локально

1. Запустите MCP server:

```powershell
.\scripts\run_mcp_server.ps1
```

2. Убедитесь, что локальный URL отвечает:

```text
http://127.0.0.1:41831/mcp
```

3. Прогоните автотесты:

```powershell
python -m unittest discover -s .\tests -v
```

Отдельный интеграционный тест MCP уже добавлен:

- `tests/test_mcp.py`

## Поведение инструмента `set_card_indicator`

В текущем MVP лампочка карточки вычисляется из дедлайна, а не хранится отдельно.

Поэтому `set_card_indicator` сделан как служебный инструмент:

- `green` — передвигает дедлайн так, чтобы времени было достаточно
- `yellow` — передвигает дедлайн так, чтобы карточка вошла в warning-зону
- `red` — делает карточку просроченной

Это сохранено намеренно, чтобы не ломать текущую минималистичную countdown-модель карточки.

## Что проверено

Реально прогнано:

- старт MCP server
- подключение MCP client к `streamable-http`
- `tools/list`
- `create_column`
- `create_card`
- `update_card`
- `move_card`
- `set_card_indicator`
- `archive_card`
- `list_overdue_cards`
- структурированные ошибки для невалидных случаев

Полная проверка current runtime tool surface должна опираться на `tools/list` в живом MCP runtime и на `tests/test_mcp.py`.

## Ограничения текущей реализации

- для прямого подключения в ChatGPT app production-grade вариантом всё ещё остаётся OAuth
- текущий bearer token режим не заменяет полноценный OAuth flow
- при интеграционных тестах MCP SDK оставляет шумные shutdown warning-и в stdout/stderr, но сами тесты проходят успешно

## Как MCP связан с окном настроек

В приложении добавлено отдельное окно `Настройки интеграции`, которое сохраняет параметры MCP в `%APPDATA%\Minimal Kanban\settings.json`.

Какие поля использует MCP server:

- включение MCP
- host MCP
- port MCP
- path MCP
- публичный HTTPS-адрес или адрес туннеля
- bearer token MCP
- bearer token локального API
- host и port локального API доски

Поведение:

- при запуске `main_mcp.py` или `scripts/run_mcp_server.ps1` MCP server сначала читает `settings.json`
- если нужные `MINIMAL_KANBAN_*` переменные окружения заданы явно, они имеют приоритет над сохранёнными настройками
- если локальный API по адресу из настроек не найден, MCP server пытается найти уже работающий backend или поднять встроенный локальный API сам

Кнопка `Проверить соединение` в окне настроек для MCP делает не простой ping, а полноценную проверку:

1. открывает streamable HTTP MCP-подключение
2. выполняет `initialize`
3. вызывает `tools/list`

Это позволяет сразу увидеть, что endpoint действительно отдаёт MCP tools, а не просто отвечает как обычный веб-сервер.
