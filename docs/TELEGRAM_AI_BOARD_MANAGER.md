# Telegram AI Board Manager

Этот документ фиксирует новую серверную надстройку AutoStop CRM: Telegram-first AI manager для управления CRM через Telegram.

## Статус реализации

- runtime: `main_telegram_ai.py`
- пакет: `src/minimal_kanban/telegram_ai/`
- Docker service: `autostopcrm-telegram-ai`
- Telegram transport: long polling, без публичного webhook-порта
- CRM write path: только через `BoardApiClient` и локальный HTTP API
- model-failure fallback treats transient OpenAI outages and rate limits as recoverable paths
- audit: `/root/.minimal-kanban/telegram_ai/audit.jsonl` внутри production volume
- state: `/root/.minimal-kanban/telegram_ai/state.json`
- conversation memory: `/root/.minimal-kanban/telegram_ai/conversation.jsonl`
- downloads/temp: `/root/.minimal-kanban/telegram_ai/downloads`
- current production commit must be verified with `git rev-parse --short HEAD`; historical commit notes below are not a release pin

Старый green-button/VIN agent не является основой новой системы. Он оставлен как legacy/compatibility слой, чтобы не ломать текущую CRM и MCP поверхность.

## Historical checkpoint: 2026-04-28

This is a historical checkpoint, not the current release pin:

- local branch, GitHub branch, and production were aligned on `autostopcrm-v1` at `18e1326` at the time of this checkpoint
- full local regression suite passed: `470/470 OK`
- production live diagnostics passed:
  - public site `https://crm.autostopcrm.ru`: `200 OK`
  - local API: OK
  - public anonymous writes: blocked with `401`
  - MCP: OK, `60` tools
  - Docker services: `autostopcrm` healthy, `autostopcrm-telegram-ai` running
- live Telegram AI web-search smoke passed inside the production container:
  - query: `Найди в интернете артикул воздушного фильтра для Toyota Land Cruiser Prado J150 2010 дизель 3.0`
  - result included OEM `17801-30080`
- important production caution remains: default admin credentials are still enabled and should be rotated in a separate controlled pass

Current product goal:

- Telegram AI is the active AI layer for owner-controlled CRM operations.
- It must handle text, voice, photo, conversation follow-ups, CRM reads/writes, audit, rollback basics, and explicit internet-search commands.
- Next feature direction is a composed parts-search flow: read a CRM card, extract vehicle/VIN context, perform internet search for requested parts, and optionally write a compact note/attachment back to CRM.

## Runtime flow

```text
Telegram update
  -> TelegramBotClient long polling
  -> normalize_update
  -> TelegramAuthService
  -> voice/photo processing through local STT first, then OpenAI when needed
  -> CRMContextBuilder
  -> TelegramAIOpenAIClient decision JSON
  -> CRMToolRegistry
  -> BoardApiClient / local HTTP API
  -> read-after-write verification
  -> TelegramAIOpenAIClient final response from actual tool results
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
AUTOSTOP_AI_STRONG_MODEL=gpt-5.4
AUTOSTOP_AI_REASONING_EFFORT=medium
AUTOSTOP_AI_STRONG_REASONING_EFFORT=high
AUTOSTOP_AI_MAX_BATCH_CARDS=20
AUTOSTOP_AI_AUDIT_ENABLED=1
AUTOSTOP_AI_CONVERSATION_MEMORY_LIMIT=12
AUTOSTOP_CRM_API_BASE_URL=http://autostopcrm:41731
```

Optional:

```env
AUTOSTOP_AI_VISION_MODEL=gpt-5.4-mini
AUTOSTOP_AI_TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe
AUTOSTOP_AI_LOCAL_TRANSCRIPTION_MODEL=base
AUTOSTOP_AI_WEB_SEARCH_ENABLED=1
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
- `openai_client.py`: Responses API decision JSON, image analysis, local-first audio transcription
- `context.py`: compact CRM context builder from board snapshot/review/search
- `crm_tools.py`: explicit CRM tool registry, validation, execution, verification
- `audit.py`: redacted JSONL audit per run
- `memory.py`: compact per-chat conversation memory for follow-up commands
- `orchestrator.py`: command flow, media enrichment, model decision, tool execution, final answer pass
- `worker.py`: long-running polling process
- `autopilot.py`: disabled-by-default skeleton using the same future pipeline

## Response contract

The worker sends one Telegram reply for each incoming update. It must not promise
`сейчас пришлю`, `потом пришлю`, or any other deferred follow-up unless a real
background task queue is implemented.

For tool-based commands the required order is:

```text
decision JSON -> execute CRM tools -> verify -> final response from tool_results -> Telegram reply
```

If the final model pass fails, `response.py` falls back to deterministic summaries
from the real tool results. Read tools such as `get_card`, `get_card_context`,
`get_repair_order_text`, `get_cards`, `search_cards`, and board reports must
surface useful data directly in the same Telegram reply.

For card reads, the reply must look like a short human summary, not a tool log.
Do not show internal tool names, verification marks, raw ids, `status`, tags,
deadline payloads, or technical column ids such as `column_6`. Prefer title,
vehicle, VIN, meaningful column label, and a compact description.

## Internet search mode

The worker has a direct internet-search route before CRM tool planning, and the
model can also choose the `internet_search` tool from the catalog. The route is
triggered by explicit phrases such as:

- `найди в интернете`
- `поищи в интернете`
- `проверь в интернете`
- `загугли`
- `посмотри в интернете`
- `web search`

It also catches common pure research wording such as:

- `официальный сайт`
- `артикул`
- `OEM`
- `оригинал`
- `аналог`
- `цена`
- `стоимость`
- `где купить`
- `источник`
- `ссылка`

Flow:

```text
Telegram command -> internet_search -> OpenAI Responses API with web_search_preview -> final Telegram reply
```

This route does not execute CRM writes and does not call CRM tools. It is the
first slice for later scenarios like:

```text
найди в интернете воздушный фильтр для Toyota Corolla
зайди в карточку, возьми VIN и найди запчасть
```

The second scenario still needs a composed flow: `get_card_context -> extract
vehicle/VIN facts -> internet_search -> optional CRM note/update`.

Follow-up context note:

- the worker stores a compact `conversation_state.last_card` from the most recent card/search result
- the worker also stores `conversation_state.last_vin` when a recent verified card result includes a VIN, so follow-up commands like "use this VIN" can reuse it without asking again
- direct internet-search also receives the same follow-up state, so phrases like "найди по этому VIN" can reuse `conversation_state.last_vin` instead of asking the user to resend the VIN
- the model is instructed to reuse that card for follow-up commands such as `this card`, `that one`, `the previous card`, or `add description` unless the user explicitly names a different card
- if the previous run only produced multiple search candidates, the worker also exposes `conversation_state.card_candidates` so the model can ask a targeted clarification instead of restarting the search

Production stabilization note:

- Simple web-search uses the base model `AUTOSTOP_AI_MODEL` and a low search context size.
- Complex web-search for VIN, OEM, parts, compatibility, analogs, and source comparison uses `AUTOSTOP_AI_STRONG_MODEL` with `AUTOSTOP_AI_STRONG_REASONING_EFFORT`.
- When `conversation_state.last_vin` is available or the request is clearly VIN/parts-related, keep the strong model path and web-search enabled.
- Transient OpenAI `429` and timeout responses are retried before the worker falls back from the strong model to the base model.
- If the strong web-search call fails with a transient OpenAI error, the worker falls back once to the base model before surfacing an error to Telegram.
- The Telegram worker wraps update handling in a safety net and still sends a fallback failure reply if the update pipeline throws before the normal reply path.
- Direct web-search answers are prompted as Telegram-ready text with short sections, readable source names only, no raw links, and optional emoji.

## Model escalation

The worker uses two decision tiers:

- base model: `AUTOSTOP_AI_MODEL`, default `gpt-5.4-mini`
- strong model: `AUTOSTOP_AI_STRONG_MODEL`, default `gpt-5.4`

Normal short commands use the base model. Complex commands use the strong model
and `AUTOSTOP_AI_STRONG_REASONING_EFFORT`, default `high`.

Current complexity signals:

- long command text
- multi-step wording: `сначала`, `потом`, `затем`, `пошагово`
- research/analysis wording: `проанализируй`, `сравни`, `источники`, `ссылки`
- vehicle parts wording: `VIN`, `OEM`, `оригинал`, `аналоги`, `найди запчасти`
- mass or risky CRM work: `все карточки`, `по всем карточкам`, `заполни`, `обнови`

This applies to CRM decisions and final answer synthesis.

Direct `internet_search` model choice:

- simple lookup: `AUTOSTOP_AI_MODEL`
- complex parts/VIN/OEM lookup: `AUTOSTOP_AI_STRONG_MODEL`
- transient strong-model failure: one fallback call through `AUTOSTOP_AI_MODEL`
- `/status` reports whether internet search is enabled in the current runtime

## CRM tool registry v1

Read tools:

- `get_board_snapshot`
- `get_board_context`
- `get_board_content`
- `get_board_events`
- `review_board`
- `get_gpt_wall`
- `get_gpt_wall` is for broad combined reads; in agent flow it is compact, so prefer `get_board_content` and `get_board_events` first when you can
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

All write tools require `role=owner`. There is no mandatory confirmation step for owner, but every write is verified by read-back where possible; attachment writes are verified by the card attachment list instead of forcing an immediate file-byte read.

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

Voice messages are transcribed locally first and then processed as normal text.
If local STT is unavailable, OpenAI transcription is used as fallback.

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
- final Telegram response after CRM tool execution
- filtering stale future-promise phrases from model output
- direct internet-search command routing
- Responses API payload includes `web_search_preview` when internet search is enabled
- automatic strong-model escalation for complex Telegram requests
- web-search uses the base model with fast reasoning and retries once
- voice `.ogg` local transcription with `faster-whisper` first, OpenAI fallback when needed
- photo attachment and card-image analysis tool paths

Current known green commands:

```powershell
.\.venv\Scripts\python.exe -m ruff check src\minimal_kanban\telegram_ai tests\test_telegram_ai.py
.\.venv\Scripts\python.exe -m unittest tests.test_telegram_ai -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Historical results on `18e1326`; rerun the commands above for the current branch head:

- focused Telegram AI tests: `48/48 OK`
- full test suite: `470/470 OK`
- ruff: OK

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
- direct web-search currently answers in Telegram only; it does not yet combine with CRM card context in one automatic tool chain
- parts-search by card/VIN is the next composed workflow, not fully implemented yet
