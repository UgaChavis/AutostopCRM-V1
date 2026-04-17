# Подключение Minimal Kanban к ChatGPT

Этот файл описывает уже актуальный сценарий подключения доски к ChatGPT через MCP.

## Что теперь умеет сервер

- отдаёт внешний MCP endpoint для ChatGPT и Responses API;
- в режиме `Bearer token` дополнительно поднимает встроенный OAuth 2.1 слой;
- публикует:
  - `/.well-known/oauth-protected-resource/...`
  - `/.well-known/oauth-authorization-server`
  - `POST /register`
  - `GET/POST /authorize`
  - `POST /token`
- поддерживает dynamic client registration и PKCE;
- продолжает принимать legacy bearer token для Responses API и ручных MCP-клиентов.

## Практический смысл

Есть два рабочих режима:

1. `ChatGPT connector`
- используйте публичный `https://.../mcp` URL;
- если у MCP включён `Bearer token`, ChatGPT проходит встроенный OAuth flow автоматически;
- вручную вставлять bearer token в обычный ChatGPT connector не требуется.

2. `Responses API / ручной MCP client`
- используйте тот же `server_url`;
- при `Bearer token` можно передавать `authorization` напрямую.

## Что должно быть настроено в приложении

1. Запустите [Start Kanban.exe](C:/Users/User/Desktop/Codex/minimal-kanban/release/Start%20Kanban.exe).
2. Откройте настройки интеграции через шестерёнку.
3. Убедитесь, что включены:
- интеграция;
- локальный API;
- MCP.
4. Задайте внешний HTTPS адрес:
- `Public HTTPS Base URL`, или
- `Tunnel URL`, или
- `Full MCP URL override`.
5. Если нужен защищённый режим:
- оставьте `MCP auth mode = Bearer token`;
- задайте `Bearer token MCP`.

## Как подключать в ChatGPT

1. Убедитесь, что итоговый MCP URL начинается с `https://`.
2. В ChatGPT откройте `Settings -> Apps & Connectors -> Create`.
3. Укажите:
- имя: `Minimal Kanban`
- описание: `Инженерная канбан-доска с журналом, сигналами и GPT-стеной`
- URL: итоговый внешний `.../mcp`
4. Нажмите `Create`.
5. Если ChatGPT запросит linking, пройдите встроенный OAuth flow.
6. После подключения проверьте, что видны tools:
- `get_gpt_wall`
- `get_board_snapshot`
- `search_cards`
- `create_card`
- `move_card`
- `update_card`

## Как подключать через Responses API

Используйте JSON с MCP tool. Пример можно собрать прямо из настроек приложения или взять шаблон из [mcp-tools-example.json](C:/Users/User/Desktop/Codex/minimal-kanban/mcp-tools-example.json).

Если MCP работает в `Bearer token` режиме, в tool payload можно передавать `authorization`.

## Что важно по безопасности

- встроенный OAuth слой сделан под текущую архитектуру общей доски с одинаковыми правами у всех;
- это удобно для приватного хоста и dev/test-публикации;
- для более строгого production-сценария следующим шагом всё равно лучше выносить авторизацию в отдельный IdP.
