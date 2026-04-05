# Live Connector Check

Этот скрипт делает только read-only диагностику и не меняет состояние доски.

## Что он проверяет

- находится ли локальный API
- отвечает ли `/api/health`
- читается ли `get_board_context`
- доступен ли внешний MCP endpoint
- видны ли ключевые tools:
  - `ping_connector`
  - `bootstrap_context`
  - `get_gpt_wall`
- отвечает ли `ping_connector`

## Как запустить

```powershell
& ".\.venv\Scripts\python.exe" ".\scripts\check_live_connector.py"
```

## JSON-режим

```powershell
& ".\.venv\Scripts\python.exe" ".\scripts\check_live_connector.py" --json
```

## Когда полезно

- перед тестом голосового GPT-агента
- после смены tunnel/ngrok URL
- если ChatGPT видит коннектор, но ведёт себя странно
- если нужно быстро понять, жив ли локальный API и публичный MCP одновременно
