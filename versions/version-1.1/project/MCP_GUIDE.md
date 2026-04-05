# Руководство по MCP

Этот документ описывает отдельный MCP server, который добавлен поверх текущей kanban-доски.

## Зачем нужен MCP server

MCP server нужен для безопасного и предсказуемого tool-based доступа к доске из:

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

- `list_columns`
- `create_column`
- `get_cards`
- `get_card`
- `create_card`
- `update_card`
- `move_card`
- `archive_card`
- `set_card_indicator`
- `set_card_deadline`
- `list_overdue_cards`

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

Подробности в [TEST_REPORT.md](TEST_REPORT.md).

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
