# Minimal Kanban

Локальная минималистичная kanban-доска для Windows с тёмным интерфейсом, локальным JSON API и отдельным MCP server для будущего и текущего управления из ChatGPT.

Проект решает две задачи:

1. Даёт обычное локальное приложение-доску для ежедневной работы.
2. Даёт отдельный tool-based слой для ChatGPT: ChatGPT -> MCP -> локальный API доски -> изменение карточек и столбцов.

## Что умеет программа

- карточки и столбцы
- динамические столбцы
- дедлайны с обратным отсчётом
- автоматическая лампочка состояния `green / yellow / red`
- архивирование карточек
- локальное хранение состояния между перезапусками
- локальный JSON API
- отдельный MCP server для подключения к ChatGPT / Responses API / Apps SDK

## Архитектура

Слои разделены и не дублируют друг друга:

- UI: [main_window.py](src/minimal_kanban/ui/main_window.py)
- бизнес-логика: [card_service.py](src/minimal_kanban/services/card_service.py)
- хранение: [json_store.py](src/minimal_kanban/storage/json_store.py)
- локальный API: [server.py](src/minimal_kanban/api/server.py)
- MCP adapter: [server.py](src/minimal_kanban/mcp/server.py), [client.py](src/minimal_kanban/mcp/client.py), [main.py](src/minimal_kanban/mcp/main.py)

Рабочая схема:

```text
ChatGPT / Responses API / Apps SDK
        ->
remote MCP server
        ->
локальный JSON API доски
        ->
CardService
        ->
JsonStore / state.json
        ->
UI приложения видит актуальное состояние
```

## Стек

- Python 3.13
- PySide6
- PyInstaller
- FastMCP / `mcp` Python SDK
- стандартный `ThreadingHTTPServer` для локального API
- JSON-файл состояния в `%APPDATA%\\Minimal Kanban`

## Структура проекта

- [main.py](main.py) — запуск обычного UI-приложения
- [main_mcp.py](main_mcp.py) — запуск MCP server без UI
- [scripts/run_mcp_server.ps1](scripts/run_mcp_server.ps1) — удобный Windows-запуск MCP server
- [scripts/run_quality_pass.ps1](scripts/run_quality_pass.ps1) — полный локальный quality pass
- [scripts/prepare_release.ps1](scripts/prepare_release.ps1) — подготовка portable release
- [release/Start Kanban.exe](release/Start%20Kanban.exe) — portable-приложение

## Как запускать dev-режим

Обычное приложение:

1. Откройте PowerShell в корне проекта.
2. Выполните:

```powershell
.\scripts\run_dev.ps1
```

MCP server без UI:

1. Откройте PowerShell в корне проекта.
2. Выполните:

```powershell
.\scripts\run_mcp_server.ps1
```

Поведение MCP server:

- сначала он пытается найти уже запущенный локальный API доски
- если API уже работает, MCP использует его
- если API не найден, MCP server сам поднимает скрытый backend на той же логике и том же хранилище

## Как собирать production build

```powershell
.\scripts\build_app.ps1
```

Результат появляется в `dist\MinimalKanban`.

## Как собирать portable release

```powershell
.\scripts\prepare_release.ps1
```

Главный пользовательский файл после сборки:

- [Start Kanban.exe](release/Start%20Kanban.exe)

## Где хранятся данные

По умолчанию:

- `%APPDATA%\Minimal Kanban\state.json`
- `%APPDATA%\Minimal Kanban\logs\minimal-kanban.log`

Важно:

- карточки, столбцы, дедлайны и архив сохраняются между перезапусками
- для повышения устойчивости добавлен межпроцессный lock-файл `state.lock`

## Как устроен локальный API

Локальный API работает на `127.0.0.1` и по умолчанию стартует с порта `41731`.

Основные endpoint-ы:

- `create_card`
- `get_cards`
- `get_card`
- `update_card`
- `set_card_deadline`
- `set_card_indicator`
- `move_card`
- `archive_card`
- `list_columns`
- `create_column`
- `list_overdue_cards`

Подробности: [API_GUIDE.md](API_GUIDE.md)

## Как устроен MCP server

MCP server поднимает удалённые инструменты поверх текущего backend-а и не содержит внутри себя отдельной логики доски.

Доступные MCP tools:

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

Подробности:

- [MCP_GUIDE.md](MCP_GUIDE.md)
- [CHATGPT_CONNECT_GUIDE.md](CHATGPT_CONNECT_GUIDE.md)
- [mcp-tools-example.json](mcp-tools-example.json)

## Подготовка к будущей интеграции с ChatGPT

Проект уже подготовлен к tool-based управлению:

- используется отдельный remote MCP layer
- tool names короткие и предсказуемые
- backend не зашит в UI
- есть отдельный JSON API и отдельный MCP adapter
- есть примеры для OpenAI / Responses API

Полезные ссылки по актуальному стеку OpenAI:

- OpenAI MCP guide: [developers.openai.com/api/docs/mcp](https://developers.openai.com/api/docs/mcp)
- OpenAI MCP and connectors guide: [developers.openai.com/api/docs/guides/tools-connectors-mcp](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)

Что уже реализовано под этот этап:

- remote MCP server на FastMCP
- tool mapping на существующий backend
- bearer token для локального API
- bearer token для MCP server
- подробные инструкции по tunnel и подключению из ChatGPT

Что пока не реализовано:

- полноценный OAuth provider для ChatGPT app auth

Важно:

- OpenAI в актуальных документах рекомендует OAuth и dynamic client registration для публичных remote MCP server
- текущая реализация уже годится для локальной разработки, тестов, Responses API и dev tunnel-сценариев
- следующим этапом для production-grade подключения в ChatGPT workspace должен стать OAuth flow

## Полезные переменные окружения

Локальный API:

- `MINIMAL_KANBAN_API_HOST`
- `MINIMAL_KANBAN_API_PORT`
- `MINIMAL_KANBAN_API_PORT_FALLBACK_LIMIT`
- `MINIMAL_KANBAN_API_BEARER_TOKEN`

MCP server:

- `MINIMAL_KANBAN_MCP_HOST`
- `MINIMAL_KANBAN_MCP_PORT`
- `MINIMAL_KANBAN_MCP_PORT_FALLBACK_LIMIT`
- `MINIMAL_KANBAN_MCP_PATH`
- `MINIMAL_KANBAN_MCP_BEARER_TOKEN`
- `MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL`
- `MINIMAL_KANBAN_BOARD_API_URL`

## Настройки интеграции

В приложении появился отдельный settings-layer для ChatGPT / OpenAI / MCP. Он не смешан с `state.json` карточек и вынесен в отдельные слои:

- модель настроек: `src/minimal_kanban/settings_models.py`
- хранение настроек: `src/minimal_kanban/settings_store.py`
- сервис настроек: `src/minimal_kanban/settings_service.py`
- окно настроек: `src/minimal_kanban/ui/settings_window.py`

Главная кнопка настроек находится в правом верхнем углу главного окна и выглядит как неброская шестерёнка.

Что можно настраивать:

- включение интеграции
- использование локального API
- host и port локального API
- bearer token локального API
- provider, model, base URL, organization ID, project ID и timeout для OpenAI-совместимого API
- включение MCP
- host, port и path MCP
- локальный MCP endpoint
- публичный HTTPS-адрес или адрес туннеля
- bearer token MCP
- авто-подключение при запуске
- тестовый режим

Файл настроек:

- `%APPDATA%\Minimal Kanban\settings.json`

Рядом с ним могут появляться:

- `%APPDATA%\Minimal Kanban\settings.lock`
- `%APPDATA%\Minimal Kanban\settings.corrupted.json`

Поведение:

- `settings.json` сохраняется отдельно от `state.json`
- при повреждении `settings.json` приложение не падает и возвращается к безопасным значениям по умолчанию
- секреты скрыты в интерфейсе по умолчанию
- секреты не логируются открытым текстом
- текущий запущенный локальный API не перенастраивается "на лету"; изменения host, port и токенов применяются к следующим запускам и внешним подключениям

## Test connection

В окне настроек доступны отдельные кнопки:

- `Проверить локальный API`
- `Проверить MCP`
- `Проверить внешний endpoint`
- `Проверить OpenAI`
- `Проверить всё`

Они проверяют:

- локальный API доски через `/api/health`
- локальный MCP через реальное MCP-подключение и `tools/list`
- внешний MCP endpoint через реальное MCP-подключение и `tools/list`
- OpenAI-совместимый endpoint через запрос к `/models`

Результаты проверки сохраняются в секции `Диагностика / Status` внутри окна настроек. В конфигурации хранится время последней проверки и отдельные статусы для локального API, локального MCP, внешнего endpoint и OpenAI-compatible endpoint.

## Ограничения безопасности настроек

На текущем этапе:

- `OpenAI API key` и bearer token-ы сохраняются в `settings.json` без системного шифрования
- полноценный OAuth flow ещё не реализован
- интеграция с Windows Credential Manager и DPAPI только подготовлена архитектурно, но пока не включена
- при необходимости жёстких требований к безопасности следующий этап должен вынести секреты из `settings.json` в системное secure storage

## Проверка качества

Быстрый локальный прогон:

```powershell
.\scripts\run_quality_pass.ps1
```

Что проверяется:

- unit tests
- UI smoke tests
- API tests
- MCP integration tests
- portable build
- post-build verification

Отчёт по последнему этапу: [TEST_REPORT.md](TEST_REPORT.md)

## Подключение к GPT-агенту

Для текущего этапа в проект добавлено полноценное окно `Настройки интеграции` с явными полями для:

- локального API;
- MCP host / port / path;
- public HTTPS URL;
- tunnel URL;
- полного MCP URL;
- auth mode;
- bearer token локального API;
- bearer token MCP;
- access token;
- OpenAI API key;
- provider / model / base URL / organization / project / timeout.

Все критичные URL и токены видны из интерфейса и копируются отдельной кнопкой. Настройки хранятся отдельно от карточек:

- `%APPDATA%\Minimal Kanban\settings.json`

Подробная пошаговая инструкция по выводу программы в интернет и подключению GPT-агента находится в:

- [CONNECT_GPT_AGENT.md](CONNECT_GPT_AGENT.md)
- [INTERNET_PUBLISH_GUIDE.md](INTERNET_PUBLISH_GUIDE.md)
