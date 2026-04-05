# Подключение Minimal Kanban к GPT-агенту

Этот документ описывает реальный рабочий путь подключения текущей доски к GPT-агенту через MCP / HTTP endpoint без управления мышью и без эмуляции интерфейса.

Рабочая схема проекта:

```text
GPT-агент / ChatGPT / Responses API
  ->
MCP endpoint
  ->
локальный API доски
  ->
CardService
  ->
state.json / UI доски
```

## 1. Что уже есть в проекте

В проекте уже реализованы:

- локальная portable-доска для Windows;
- локальный HTTP API доски;
- отдельный MCP server поверх существующего backend;
- отдельный раздел `Settings` для всех адресов, endpoint-ов, токенов и параметров интеграции;
- bearer token режим для локального API и MCP;
- тестовые кнопки проверки локального API, локального MCP, внешнего endpoint и OpenAI-compatible endpoint.

## 2. Где открываются настройки

1. Запустите [Start Kanban.exe](C:/Users/User/Desktop/Codex/minimal-kanban/release/Start%20Kanban.exe).
2. В левом верхнем углу главного окна нажмите кнопку-шестерёнку.
3. Откроется окно `Настройки интеграции`.

Файл настроек хранится здесь:

- `%APPDATA%\Minimal Kanban\settings.json`

Карточки и колонки по-прежнему хранятся отдельно:

- `%APPDATA%\Minimal Kanban\state.json`

## 3. Какие поля в Settings за что отвечают

### General

- `Включить интеграцию`
  Включает сценарии подключения к GPT/OpenAI.
- `Использовать локальный API`
  Означает, что MCP и внешние клиенты должны ориентироваться на локально поднятый API доски.
- `Хост локального API`
  Адрес, на котором приложение поднимает локальный API. Обычно `127.0.0.1`.
- `Порт локального API`
  Порт локального API. По умолчанию `41731`.
- `Адрес запуска локального API`
  Итоговый локальный URL, который реально поднимает приложение. Поле только для просмотра и копирования.
- `Базовый URL API для интеграции`
  Необязательный override. Если это поле заполнено, именно его MCP использует как адрес API доски для внешних клиентов и агента.
- `Итоговый URL API для MCP и GPT-агента`
  Финальный адрес API, который использует интеграция. Если override пустой, сюда подставляется локальный URL.
- `Автоподключение при запуске`
  Подготовка под будущий auto-connect сценарий.
- `Тестовый режим`
  Флаг для dev/test сценариев и диагностики.

### Authentication / Credentials

- `Режим авторизации`
  Сейчас поддерживаются:
  - `Без авторизации`
  - `Bearer token`
- `Access token агента / OAuth`
  Резервное поле под будущий OAuth / app token. Может использоваться как запасной bearer token.
- `Bearer token локального API`
  Токен защиты локального API.
- `Bearer token MCP`
  Токен защиты MCP endpoint.
- `API key OpenAI`
  Ключ для проверки OpenAI-compatible endpoint.

### ChatGPT / OpenAI

- `Provider`
  Например `openai` или внутренний OpenAI-compatible provider.
- `Model`
  Модель, которую вы планируете использовать для агента.
- `Base URL`
  Базовый адрес OpenAI-compatible API.
- `Organization ID`
  Необязательный заголовок `OpenAI-Organization`.
- `Project ID`
  Необязательный заголовок `OpenAI-Project`.
- `Timeout, сек`
  Timeout для connection test.

### MCP

- `Включить MCP`
  Включает MCP endpoint.
- `Хост MCP`
  Адрес запуска MCP server, обычно `127.0.0.1`.
- `Порт MCP`
  Порт MCP server, по умолчанию `41831`.
- `Endpoint path`
  MCP path, по умолчанию `/mcp`.
- `Локальный MCP URL`
  Итоговый локальный адрес MCP. Только для просмотра и копирования.
- `Public HTTPS URL`
  Базовый публичный HTTPS адрес без path. Нужен для reverse proxy или стабильного домена.
- `Tunnel URL`
  Базовый адрес туннеля, например `https://...trycloudflare.com`.
- `Полный MCP URL (override)`
  Если это поле заполнено, именно его нужно считать главным внешним MCP endpoint.
- `Полный MCP URL по public HTTPS`
  Производный адрес `Public HTTPS URL + path`.
- `Полный MCP URL по tunnel`
  Производный адрес `Tunnel URL + path`.
- `Итоговый MCP URL для GPT-агента`
  Главный внешний MCP URL. Приоритет:
  1. `Полный MCP URL (override)`
  2. `Public HTTPS URL + path`
  3. `Tunnel URL + path`

### Diagnostics / Status

Раздел показывает:

- путь к `settings.json`;
- текущий локальный API, с которым запущено приложение;
- время последней проверки;
- статусы и сообщения по:
  - локальному API,
  - локальному MCP,
  - внешнему endpoint,
  - OpenAI-compatible endpoint.

В разделе есть отдельные кнопки:

- `Проверить локальный API`
- `Проверить MCP`
- `Проверить внешний endpoint`
- `Проверить OpenAI`
- `Проверить всё`

## 4. Базовый локальный сценарий подготовки

1. Запустите программу.
2. Откройте `Настройки интеграции`.
3. В секции `General`:
   - включите `Включить интеграцию`;
   - оставьте `Использовать локальный API`;
   - проверьте host `127.0.0.1`;
   - проверьте port `41731`.
4. В секции `Authentication / Credentials`:
   - выберите `Bearer token`, если хотите защищать локальный API и MCP;
   - задайте `Bearer token локального API`;
   - задайте `Bearer token MCP`.
5. В секции `MCP`:
   - включите `Включить MCP`;
   - проверьте host `127.0.0.1`;
   - проверьте port `41831`;
   - проверьте path `/mcp`.
6. Нажмите `Применить`.
7. Нажмите:
   - `Проверить локальный API`;
   - `Проверить MCP`.
8. Убедитесь, что оба статуса стали `Успешно`.

## 5. Как вывести MCP endpoint в интернет

ChatGPT на телефоне не сможет обратиться к `127.0.0.1`, поэтому нужен внешний HTTPS endpoint.

Есть два практических варианта:

- HTTPS tunnel для dev/test;
- reverse proxy / домен для более стабильного сценария.

### Вариант A. HTTPS tunnel

Пример с `cloudflared`:

```powershell
cloudflared tunnel --url http://127.0.0.1:41831
```

После запуска вы получите адрес вида:

```text
https://example-name.trycloudflare.com
```

Что нужно записать в Settings:

- `Tunnel URL` = `https://example-name.trycloudflare.com`
- `Полный MCP URL по tunnel` появится автоматически
- `Итоговый MCP URL для GPT-агента` тоже обновится автоматически, если не задан override

### Вариант B. Reverse proxy / свой домен

Если у вас есть внешний HTTPS hostname:

- `https://kanban.example.com`

и reverse proxy проксирует его на:

- `http://127.0.0.1:41831/mcp`

то в Settings укажите:

- `Public HTTPS URL` = `https://kanban.example.com`
- `Endpoint path` = `/mcp`

После этого:

- `Полный MCP URL по public HTTPS` станет `https://kanban.example.com/mcp`
- этот URL можно использовать как основной внешний endpoint

## 6. Как проверить доступность endpoint снаружи

После того как tunnel или reverse proxy настроены:

1. Откройте `Настройки интеграции`.
2. Скопируйте поле `Итоговый MCP URL для GPT-агента`.
3. Нажмите `Проверить внешний endpoint`.
4. Убедитесь, что статус `Статус внешнего endpoint` стал `Успешно`.

Если нужна ручная проверка:

1. Откройте URL в браузере.
2. Если endpoint защищён bearer token, используйте клиент, который умеет передавать заголовок `Authorization`.
3. Для реальной проверки MCP лучше использовать встроенную кнопку `Проверить внешний endpoint`, потому что она делает настоящий MCP handshake и `tools/list`.

## 7. Как подключить MCP server к GPT-агенту

Нужные значения берутся прямо из Settings:

- адрес подключения агента:
  `Итоговый MCP URL для GPT-агента`
- если используется bearer auth:
  `Bearer token MCP`
- если агент должен ходить к OpenAI-compatible API:
  `Base URL`, `Model`, `API key OpenAI`, `Organization ID`, `Project ID`

### Минимальный сценарий для Responses API

Используйте `Итоговый MCP URL для GPT-агента` как `server_url` для MCP tool.

Пример структуры запроса есть в:

- [mcp-tools-example.json](C:/Users/User/Desktop/Codex/minimal-kanban/mcp-tools-example.json)

### Сценарий для ChatGPT / GPT-агента

Практический порядок:

1. Поднимите внешний HTTPS endpoint.
2. Скопируйте `Итоговый MCP URL для GPT-агента`.
3. Откройте интерфейс подключения custom MCP / agent tools.
4. Вставьте итоговый MCP URL.
5. Если клиент поддерживает bearer token для custom MCP, передайте `Bearer token MCP`.
6. Проверьте, что клиент видит tools:
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

## 8. Какие поля из Settings куда вставлять

### Для локального API клиента

- base URL клиента: `Итоговый URL API для MCP и GPT-агента`
- bearer token клиента: `Bearer token локального API`

### Для MCP server

- host: `Хост MCP`
- port: `Порт MCP`
- path: `Endpoint path`
- внешний URL: `Итоговый MCP URL для GPT-агента`
- bearer token: `Bearer token MCP`

### Для OpenAI-compatible подключения

- provider: `Provider`
- base URL: `Base URL`
- model: `Model`
- api key: `API key OpenAI`
- organization: `Organization ID`
- project: `Project ID`
- timeout: `Timeout, сек`

## 9. Как тестировать вручную после настройки

### Проверка внутри программы

1. Нажмите `Проверить локальный API`.
2. Нажмите `Проверить MCP`.
3. Нажмите `Проверить внешний endpoint`.
4. При необходимости нажмите `Проверить OpenAI`.

### Проверка с реальным агентом

После подключения MCP к агенту задайте по очереди команды:

1. `Покажи мои столбцы на доске.`
2. `Создай столбец "Ожидание".`
3. `Создай карточку "Позвонить клиенту" со сроком 0 дней и 4 часа.`
4. `Покажи все карточки.`
5. `Перемести карточку "Позвонить клиенту" в столбец "Ожидание".`
6. `Сделай этой карточке жёлтый индикатор.`

Проверка успешна, если:

- столбец появляется в UI;
- карточка создаётся в UI;
- карточка переезжает в другой столбец;
- меняется дедлайн / индикатор;
- после перезапуска доски изменения сохраняются.

## 10. Что делать после смены IP, домена или туннеля

Если изменился tunnel URL, домен или внешний path:

1. Откройте `Настройки интеграции`.
2. Обновите:
   - `Public HTTPS URL`, или
   - `Tunnel URL`, или
   - `Полный MCP URL (override)`.
3. Нажмите `Применить`.
4. Нажмите `Проверить внешний endpoint`.
5. Если изменились host/port/path уже запущенного MCP server, перезапустите внешний процесс MCP.
6. Обновите URL в конфигурации GPT-агента.

## 11. Ограничения безопасности текущего этапа

На текущем этапе уже есть:

- только ограниченный набор MCP tools для доски;
- валидация входных данных;
- bearer token для локального API;
- bearer token для MCP;
- отсутствие произвольного доступа к компьютеру.

На текущем этапе ещё нет:

- полноценного OAuth provider;
- системного secure storage для секретов;
- Windows Credential Manager / DPAPI интеграции.

Важно:

- секреты в `settings.json` не шифруются;
- не публикуйте внешний endpoint без реальной необходимости;
- для dev/test удобнее использовать короткоживущий tunnel;
- для production-grade подключения OpenAI рекомендует OAuth и корректную модель remote MCP auth.

Полезные официальные ссылки:

- [OpenAI MCP guide](https://developers.openai.com/api/docs/mcp)
- [OpenAI MCP and connectors guide](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)
