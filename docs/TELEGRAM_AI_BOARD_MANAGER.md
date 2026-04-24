# Telegram AI Board Manager

Этот документ фиксирует новую серверную надстройку AutoStop CRM: Telegram-first AI manager для управления CRM через Telegram.

## Статус реализации

- runtime: `main_telegram_ai.py`
- пакет: `src/minimal_kanban/telegram_ai/`
- Docker service: `autostopcrm-telegram-ai`
- Telegram transport: long polling, без публичного webhook-порта
- CRM write path: только через `BoardApiClient` и локальный HTTP API
- audit: `/root/.minimal-kanban/telegram_ai/audit.jsonl` внутри production volume
- state: `/root/.minimal-kanban/telegram_ai/state.json`
- conversation memory: `/root/.minimal-kanban/telegram_ai/conversation.jsonl`
- downloads/temp: `/root/.minimal-kanban/telegram_ai/downloads`

Старый green-button/VIN agent не является основой новой системы. Он оставлен как legacy/compatibility слой, чтобы не ломать текущую CRM и MCP поверхность.

## Runtime flow

```text
Telegram update
  -> TelegramBotClient long polling
  -> normalize_update
  -> TelegramAuthService
  -> voice/photo processing through OpenAI when needed
  -> CRMContextBuilder
  -> TelegramAIOpenAIClient decision JSON
  -> CRMToolRegistry
  -> BoardApiClient / local HTTP API
  -> read-after-write verification
  -> TelegramAIAuditService
  -> Telegram reply
```

## Ports and services

- CRM internal API: `41731`
- MCP internal port: `41831`
- host mappings in compose: `127.0.0.1:8000 -> 41731`, `127.0.0.1:8001 -> 41831`
- Telegram AI worker opens no public port
- Telegram AI worker reaches CRM at `http://autostopcrm:41731` in Docker

## Environment variables

Required for activation:

```env
AUTOSTOP_TELEGRAM_AI_ENABLED=1
AUTOSTOP_TELEGRAM_BOT_TOKEN=telegram-bot-token
AUTOSTOP_TELEGRAM_OWNER_IDS=123456789
OPENAI_API_KEY=sk-...
```

Recommended:

```env
AUTOSTOP_AI_MODEL=gpt-5.4-mini
AUTOSTOP_AI_REASONING_EFFORT=medium
AUTOSTOP_AI_MAX_BATCH_CARDS=20
AUTOSTOP_AI_AUDIT_ENABLED=1
AUTOSTOP_AI_CONVERSATION_MEMORY_LIMIT=12
AUTOSTOP_CRM_API_BASE_URL=http://autostopcrm:41731
```

Optional:

```env
AUTOSTOP_AI_VISION_MODEL=gpt-5.4-mini
AUTOSTOP_AI_TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe
AUTOSTOP_AI_WEB_SEARCH_ENABLED=0
AUTOSTOP_AI_AUTOPILOT_ENABLED=0
AUTOSTOP_AI_AUTOPILOT_INTERVAL_MINUTES=30
OPENAI_BASE_URL=https://api.openai.com/v1
```

If owner IDs, bot token, or OpenAI key are missing, the worker stays in safe-disabled mode and does not expose CRM data.

## Implemented modules

- `config.py`: env parsing, data paths, safety defaults
- `telegram_client.py`: Telegram Bot API polling, replies, file download
- `normalizer.py`: text, voice, photo, document update normalization
- `auth.py`: owner authorization by stable Telegram user ID
- `openai_client.py`: Responses API decision JSON, image analysis, audio transcription
- `context.py`: compact CRM context builder from board snapshot/review/search
- `crm_tools.py`: explicit CRM tool registry, validation, execution, verification
- `audit.py`: redacted JSONL audit per run
- `memory.py`: compact per-chat conversation memory for follow-up commands
- `orchestrator.py`: command flow, media enrichment, model decision, tool execution
- `worker.py`: long-running polling process
- `autopilot.py`: disabled-by-default skeleton using the same future pipeline

## CRM tool registry v1

Read tools:

- `get_board_snapshot`
- `get_board_context`
- `get_board_content`
- `get_board_events`
- `review_board`
- `get_gpt_wall`
- `get_cards`
- `get_card`
- `search_cards`
- `get_card_context`
- `get_card_log`
- `list_card_attachments`
- `get_card_attachment`
- `read_card_attachment`
- `analyze_card_image_attachment`
- `list_overdue_cards`
- `list_columns`
- `list_archived_cards`
- `list_repair_orders`
- `get_repair_order`
- `get_repair_order_text`
- `list_cashboxes`
- `get_cashbox`

Write tools:

- `create_card`
- `update_card`
- `move_card`
- `bulk_move_cards`
- `archive_card`
- `restore_card`
- `cleanup_card_content`
- `attach_telegram_photo_to_card`
- `create_column`
- `rename_column`
- `create_sticky`
- `update_sticky`
- `move_sticky`
- `create_cashbox`
- `create_cash_transaction`
- `update_board_settings`
- `set_card_deadline`
- `set_card_indicator`
- `update_repair_order`
- `replace_repair_order_works`
- `replace_repair_order_materials`
- `set_repair_order_status`

All write tools require `role=owner`. There is no mandatory confirmation step for owner, but every write is verified by read-back where possible.

## Safety boundaries

Allowed:

- board/card operations through local API
- repair-order operations through local API
- OpenAI model calls for command interpretation, voice transcription and photo facts
- audit and short Telegram reports

Forbidden:

- shell commands from Telegram
- Git commands from Telegram
- source-code edits from runtime
- secret exposure in Telegram or audit
- raw JSON state edits
- hard delete of operational CRM data

Full-control means CRM full-control through approved tools, not server full-control.

## Telegram commands

Built-in commands:

- `/start`
- `/help`
- `/status`
- `Что ты сделал сегодня?`
- `Покажи последние действия AI`
- `Откати последнее действие`

Natural commands go to the model, for example:

```text
Кратко по доске
Создай карточку: BMW X5, диагностика пневмы, сегодня до 18:00
Найди просроченные карточки
Перенеси Камри в работу
Добавь в заказ-наряд Камри замену масла и фильтра
```

Voice messages are transcribed and then processed as normal text.

Conversation memory is enabled by default. The worker stores compact per-chat history so follow-up commands like `добавь туда описание`, `перенеси её`, or `прикрепи к этой карточке` can reuse recent verified card ids and actions. The memory stores summaries and ids, not raw file bytes.

Photo messages are sent through vision extraction first; extracted facts are passed into the same CRM decision flow. If the user asks to save the photo to a card, the worker uses `attach_telegram_photo_to_card` and stores the original image as a CRM attachment.

Photo attachment examples:

```text
Прикрепи это фото к карточке Camry
Посмотри фото в карточке BMW и скажи, что видно
Покажи вложения карточки Prado
```

## Tests

Focused test command:

```powershell
python -m unittest tests.test_telegram_ai
```

Covered:

- Telegram update parsing
- owner authorization and denial
- audit secret redaction
- built-in orchestration without model calls
- create/update/move CRM tools through real local `ApiServer`
- verification after writes
- compact context builder read path

## Production deployment

Normal path:

```bash
cd /opt/autostopcrm
git fetch origin autostopcrm-v1
git reset --hard origin/autostopcrm-v1
./deploy.sh
docker compose ps
docker compose logs --tail=100 autostopcrm-telegram-ai
```

Expected:

- `autostopcrm` healthy
- `autostopcrm-telegram-ai` running
- if `AUTOSTOP_TELEGRAM_AI_ENABLED=0`, worker logs `telegram_ai.disabled enabled=false` and sleeps
- if enabled but missing config, worker logs safe-disabled reason and sleeps
- if configured, worker logs `telegram_ai.started` with redacted token

## Rollback

Minimal rollback is implemented for the latest reversible AI tool result:

- `create_card` -> archive created card
- `move_card` -> move card back to the previous column
- `update_card` / deadline / indicator -> restore main card text/profile fields from before snapshot
- repair-order updates -> restore the previous repair-order snapshot where available
- `archive_card` -> restore card through the API

Rollback is intentionally not a raw storage edit and does not promise universal undo.

## Known limitations in this slice

- rollback is minimal and based on stored before-snapshots, not a universal transaction log
- autopilot is skeleton-only and disabled by default
- documents/PDFs from Telegram are normalized but not fully processed yet
- Telegram worker uses polling; webhook mode is intentionally deferred
- model output is validated at tool-name/role level, but deeper per-field schemas can be tightened in later passes
