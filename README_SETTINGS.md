# Структура settings.json

Файл настроек лежит по пути:

- `%APPDATA%\Minimal Kanban\settings.json`

Файл создаётся автоматически при первом запуске. Если файл повреждён, приложение делает резервную копию `settings.corrupted.json` и возвращается к значениям по умолчанию.

## Блоки настроек

### 1. `general`

Редактируемые поля:

- `integration_enabled`
- `use_local_api`
- `auto_connect_on_startup`
- `test_mode`

Назначение:

- управляет тем, включена ли интеграция вообще;
- нужно ли использовать локальный API в интеграционном слое;
- нужно ли пытаться запускать MCP автоматически при старте приложения;
- нужно ли запускать встроенные smoke checks при проверке локального API.

### 2. `local_api`

Редактируемые поля:

- `local_api_host`
- `local_api_port`
- `local_api_base_url_override`
- `local_api_auth_mode`
- `local_api_bearer_token`

Derived поля:

- `runtime_local_api_url`
- `effective_local_api_url`
- `local_api_health_url`

Логика:

- `runtime_local_api_url = http://{host}:{port}`
- `local_api_health_url = runtime_local_api_url + /api/health`
- `effective_local_api_url`:
  - сначала `local_api_base_url_override`
  - иначе `runtime_local_api_url`

### 3. `mcp`

Редактируемые поля:

- `mcp_enabled`
- `mcp_host`
- `mcp_port`
- `mcp_path`
- `public_https_base_url`
- `tunnel_url`
- `full_mcp_url_override`
- `mcp_auth_mode`
- `mcp_bearer_token`

Derived поля:

- `local_mcp_url`
- `derived_public_mcp_url`
- `derived_tunnel_mcp_url`
- `effective_mcp_url`

Приоритет вычисления `effective_mcp_url`:

1. `full_mcp_url_override`
2. `public_https_base_url + mcp_path`
3. `tunnel_url + mcp_path`
4. `local_mcp_url`

Важно:

- пункт 4 нужен в первую очередь для локальной диагностики;
- для внешнего подключения к ChatGPT нужен HTTPS endpoint.

### 4. `openai`

Редактируемые поля:

- `provider`
- `model`
- `base_url`
- `organization_id`
- `project_id`
- `timeout_seconds`

Назначение:

- хранит параметры OpenAI-compatible endpoint;
- используется при проверке внешнего OpenAI/GPT endpoint.

### 5. `auth`

Редактируемые поля:

- `auth_mode`
- `access_token`
- `local_api_bearer_token`
- `mcp_bearer_token`
- `openai_api_key`

Замечания:

- секреты сейчас не шифруются;
- UI скрывает их по умолчанию;
- секреты можно показать, скрыть и скопировать;
- для bearer-режима локального API и MCP есть кнопки генерации токена.

### 6. `diagnostics`

Обновляется автоматически:

- `local_api_status`
- `local_api_message`
- `mcp_status`
- `mcp_message`
- `external_status`
- `external_message`
- `openai_status`
- `openai_message`
- `overall_status`
- `last_local_api_check`
- `last_mcp_check`
- `last_external_endpoint_check`
- `last_openai_check`
- `last_full_check`
- `last_errors`
- `last_warnings`

Эти поля появляются после проверок из UI и нужны для ручной диагностики и последующего анализа.

## Что можно редактировать в UI

В окне `Settings -> Integration / GPT / MCP` доступны:

- все важные host, port, path, override URL и tunnel/public URL;
- режимы авторизации;
- токены;
- параметры OpenAI-compatible endpoint;
- запуск и остановка MCP runtime;
- полные проверки соединений;
- экспорт карточки подключения;
- экспорт текущего конфига интеграции.

## Что показывается как read-only

- `runtime_local_api_url`
- `local_api_health_url`
- `local_mcp_url`
- `derived_public_mcp_url`
- `derived_tunnel_mcp_url`
- `effective_local_api_url`
- `effective_mcp_url`

## Когда нужен перезапуск

Перезапуск приложения или MCP runtime нужен, если меняются:

- host;
- port;
- path;
- bearer token;
- режим auth для уже поднятого процесса.

Приложение показывает это предупреждение прямо в окне настроек.
