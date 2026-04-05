# Отладка MCP Startup

## Как запускается MCP

1. Приложение поднимает локальный Board API.
2. В Settings можно нажать `Запустить MCP сервер`.
3. UI создаёт `McpRuntimeController`.
4. Контроллер поднимает `McpServerRuntime` поверх FastMCP и локального Board API.
5. Runtime не считает старт успешным, пока:
   - порт не начал слушать;
   - endpoint `/mcp` не начал отвечать;
   - поток uvicorn не завершился аварийно.

## Где лежат логи

- Основной лог приложения:
  - `%APPDATA%\Minimal Kanban\logs\minimal-kanban.log`
- Отдельный MCP startup log:
  - `%APPDATA%\Minimal Kanban\logs\mcp-startup.log`

В Settings есть кнопка `Открыть лог MCP`.

## Что означает ошибка `Unable to configure formatter 'default'`

Эта ошибка приходила из logging-конфигурации uvicorn. В packaged/runtime-сценарии uvicorn мог падать на своей стандартной `dictConfig`, где используется formatter с именем `default`.

Практически это означало:

- локальный API уже работал;
- MCP URL в UI выглядел корректным;
- но uvicorn падал до нормального старта MCP runtime;
- UI получал сырую техническую ошибку.

## Что теперь изменено

- MCP runtime больше не зависит от стандартного uvicorn `default` formatter.
- Для uvicorn используется fail-safe схема:
  - сначала попытка подключить shared handlers приложения;
  - если это не удалось, включается простой stream fallback;
  - ошибка логирования больше не должна убивать startup.
- Startup считается успешным только после readiness probe:
  - bind на host/port;
  - ответ endpoint `/mcp`.

## Как работает fallback logging

1. Runtime пытается привязать `uvicorn`, `uvicorn.error`, `uvicorn.access` к handlers приложения.
2. Если это не получилось, runtime пишет предупреждение в `mcp-startup.log`.
3. Затем включает простой fallback logger без `dictConfig`.
4. MCP продолжает стартовать.

## Как проверить локальный MCP

Вариант через UI:

1. Откройте `Settings`.
2. Убедитесь, что `MCP` включён.
3. Нажмите `Запустить MCP сервер`.
4. Проверьте поля:
   - `Состояние MCP runtime`
   - `Текущий runtime URL MCP`
   - `Статус MCP`
   - `Общий статус`
5. Нажмите `Проверить MCP локально`.

Признаки успешного старта:

- `Состояние MCP runtime` показывает, что сервер запущен;
- `Текущий runtime URL MCP` не пустой;
- `Статус MCP` становится успешным;
- в `mcp-startup.log` есть записи:
  - `mcp.start.begin`
  - `mcp.start.port_bound`
  - `mcp.start.endpoint_ready`
  - `mcp.start.ready`

## Как понять, что сервер реально поднялся

Недостаточно только открыть порт. Сейчас правильный успех означает:

- поток uvicorn не умер сразу после старта;
- порт слушает;
- `GET /mcp` возвращает валидный runtime-ответ или ожидаемый код вроде `401/405`, то есть route реально существует.

Если порт открыт, но endpoint не отвечает, startup считается ошибкой.

## Что делать, если старт не удался

1. Нажмите `Скопировать ошибку запуска`.
2. Нажмите `Открыть лог MCP`.
3. Проверьте:
   - не занят ли порт;
   - корректен ли `Path MCP`;
   - не включён ли bearer без нужного токена;
   - нет ли traceback в `mcp-startup.log`.

Если ошибка связана с логированием, UI теперь показывает короткое сообщение:

- `Ошибка запуска MCP сервера. Проблема в конфигурации логирования.`

Техническая деталь при этом остаётся в разделе диагностики и в `mcp-startup.log`.

## Что означает ошибка `421 Misdirected Request / Invalid Host header`

Это уже не проблема tunnel как такового. Она означает, что запрос дошёл до `/mcp`, но MCP runtime отклонил внешний `Host` header из-за DNS rebinding protection.

Теперь Minimal Kanban автоматически:

- разрешает локальные host/origin для `127.0.0.1`, `localhost`, `[::1]`;
- добавляет host из `Public HTTPS Base URL`;
- добавляет host из `Tunnel URL`;
- добавляет host из `Full MCP URL override`;
- даёт вручную указать дополнительные `allowed hosts` и `allowed origins` в Settings.

Если в диагностике видно сообщение:

- `MCP runtime отклоняет внешний Host header. Нужно разрешить host из Tunnel URL / external domain.`

проверьте:

1. заполнено ли `Tunnel URL` или `Public HTTPS Base URL`;
2. совпадает ли домен tunnel с тем, который реально приходит в `Host`;
3. нужен ли дополнительный host в поле `Дополнительные allowed hosts`;
4. после изменения allowlist перезапущен ли MCP runtime.

## Переход к внешнему HTTPS URL

После успешного локального старта:

1. Убедитесь, что локальный MCP реально запущен.
2. Укажите:
   - `Public HTTPS Base URL`, или
   - `Tunnel URL`, или
   - `Full MCP URL override`
3. Проверьте поле `Итоговый MCP URL для GPT-агента`.
4. Нажмите `Проверить внешний endpoint`.
5. Только после этого копируйте URL в ChatGPT Agent Builder.

Если в мастере подключения показывается предупреждение:

- `Локальный MCP работает, но для ChatGPT нужен внешний HTTPS URL`

это означает, что локальный старт уже в порядке, но наружу MCP ещё не выведен.
