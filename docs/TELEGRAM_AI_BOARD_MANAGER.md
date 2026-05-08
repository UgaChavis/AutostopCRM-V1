# Telegram AI Board Manager

Техническая карта in-repo Telegram AI worker.

Не используйте этот файл как release checkpoint. Текущий commit, services и live checks проверяются через `docs/OPERATIONS_RUNBOOK.md`.

## Статус

- entrypoint: `main_telegram_ai.py`
- package: `src/minimal_kanban/telegram_ai/`
- Docker service: `autostopcrm-telegram-ai`
- transport: Telegram long polling, без публичного webhook port
- CRM path: `BoardApiClient` -> local HTTP API
- audit/state/conversation/downloads: `/root/.minimal-kanban/telegram_ai/`

Старый card-indicator/VIN enrichment path остаётся compatibility behavior. Новый owner-facing AI-контур ведётся здесь.

## Runtime Flow

```text
Telegram update
  -> normalize_update
  -> owner authorization
  -> text / voice / photo processing
  -> CRM context builder
  -> OpenAI decision JSON
  -> CRM tool registry
  -> local HTTP API
  -> read-after-write verification
  -> final Telegram response
  -> redacted audit
```

## Environment

Required when enabled:

```env
AUTOSTOP_TELEGRAM_AI_ENABLED=1
AUTOSTOP_TELEGRAM_BOT_TOKEN=telegram-bot-token
AUTOSTOP_TELEGRAM_OWNER_IDS=123456789
OPENAI_API_KEY=sk-...
AUTOSTOP_CRM_API_BASE_URL=http://autostopcrm:41731
```

Recommended:

```env
AUTOSTOP_AI_MODEL=gpt-5.4-mini
AUTOSTOP_AI_STRONG_MODEL=gpt-5.4
AUTOSTOP_AI_REASONING_EFFORT=medium
AUTOSTOP_AI_STRONG_REASONING_EFFORT=high
AUTOSTOP_AI_WEB_SEARCH_ENABLED=1
AUTOSTOP_AI_AUDIT_ENABLED=1
AUTOSTOP_AI_CONVERSATION_MEMORY_LIMIT=12
```

Optional:

```env
AUTOSTOP_AI_VISION_MODEL=gpt-5.4-mini
AUTOSTOP_AI_TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe
AUTOSTOP_AI_LOCAL_TRANSCRIPTION_MODEL=base
AUTOSTOP_AI_AUTOPILOT_ENABLED=0
AUTOSTOP_AI_AUTOPILOT_INTERVAL_MINUTES=30
OPENAI_BASE_URL=https://api.openai.com/v1
```

Если enabled flag, bot token, owner IDs или OpenAI key отсутствуют, worker остаётся safe-disabled и не должен раскрывать CRM data.

## Modules

- `config.py` - env parsing, paths, safety defaults.
- `telegram_client.py` - Bot API polling, replies, file download.
- `normalizer.py` - text, voice, photo, document normalization.
- `auth.py` - owner authorization.
- `openai_client.py` - Responses API, vision, transcription, web search.
- `context.py` - compact CRM context.
- `crm_tools.py` - explicit CRM tool registry and verification.
- `audit.py` - redacted audit.
- `memory.py` - compact conversation memory.
- `orchestrator.py` - main command flow.
- `worker.py` - long-running polling process.
- `autopilot.py` - disabled-by-default skeleton.

## Response Contract

Worker sends one final reply per incoming update.

For tool commands:

```text
decision JSON -> execute tools -> verify -> final response -> Telegram reply
```

Правила:

- не обещать deferred follow-up без реальной background queue;
- card reads должны быть human summaries, а не tool logs;
- не показывать internal tool names, raw ids, technical column ids и raw payload dumps в ответах.

## Internet Search

Direct internet-search route runs before CRM planning for explicit phrases:

- `найди в интернете`
- `поищи в интернете`
- `проверь в интернете`
- `загугли`
- `web search`

It also catches obvious research terms: `официальный сайт`, `артикул`, `OEM`, `аналог`, `цена`, `источник`, `ссылка`.

Direct search does not write to CRM. Composed workflow must be explicit:

```text
read CRM context -> extract vehicle/VIN facts -> internet_search -> optional verified writeback
```

Model routing:

- normal CRM commands: `AUTOSTOP_AI_MODEL`;
- complex CRM commands: `AUTOSTOP_AI_STRONG_MODEL`;
- simple direct search: base model;
- complex VIN/OEM/parts search: strong model with transient fallback to base model.

## CRM Tools

Reads:

- board snapshots/reviews/content/events/wall;
- cards, card context, card log and attachments;
- clients and stats;
- repair orders and text/PDF exports;
- cashboxes and journals;
- shared files.

Writes:

- create/update/move/archive/restore cards;
- `set_card_board_summary`;
- columns and stickies;
- client create/update/link/vehicle operations;
- repair-order updates and line replacements;
- cashbox/transaction creation;
- shared-file upload/delete;
- Telegram photo attachment.

All writes require owner role and should verify read-back where possible.

## Board Summary And Cleanup

`set_card_board_summary` is the agent-facing way to update board preview.

Rules:

- read `get_card_context` or equivalent first;
- max five non-empty lines;
- focus on current state, stage, next action and one blocker;
- do not copy phone, VIN, full client identity, raw scan dumps or long complaint lists;
- preserve full `description`;
- after cleanup changes to title/description/tags/profile, refresh summary and verify `board_summary_stale=false`.

`Приберись` is a procedure, not one backend tool. It must not move/archive cards or change payments, works, materials, files or clients unless the owner explicitly asks.

## Safety

Allowed:

- CRM operations through approved local API tools;
- OpenAI calls for command interpretation, voice, photo and search;
- redacted audit and short Telegram reports.

Forbidden:

- shell/Git/server commands from Telegram;
- source-code edits from runtime;
- secret exposure;
- raw JSON state edits;
- hard deletion of operational CRM data without explicit owner intent and supported API path.

## Commands

Built-ins:

- `/start`
- `/help`
- `/status`
- `Что ты сделал сегодня?`
- `Покажи последние действия AI`
- `Откати последнее действие`

Natural examples:

```text
Кратко по доске
Создай карточку: BMW X5, диагностика пневмы, сегодня до 18:00
Найди просроченные карточки
Перенеси Камри в работу
Добавь в заказ-наряд Камри замену масла и фильтра
Найди в интернете артикул фильтра по этому VIN
```

## Tests

Focused:

```powershell
python -m unittest tests.test_telegram_ai -v
```

Useful before release:

```powershell
.\.venv\Scripts\python.exe -m ruff check src\minimal_kanban\telegram_ai tests\test_telegram_ai.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Deploy And Logs

Use the main runbook for deploy. Worker-specific checks:

```bash
cd /opt/autostopcrm
docker compose ps
docker compose logs --tail=100 autostopcrm-telegram-ai
```

Expected states:

- disabled config logs safe-disabled state;
- enabled and configured worker logs startup with redacted token;
- worker opens no public port.

## Production Smoke

```bash
cd /opt/autostopcrm
docker compose exec -T autostopcrm python scripts/check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url http://127.0.0.1:41731 --mcp-url https://crm.autostopcrm.ru/mcp --operator-username admin --operator-password admin --expect-admin
```

Direct web-search smoke inside the worker:

```bash
docker compose exec -T autostopcrm-telegram-ai sh -lc 'set -a; . /run/telegram-ai.env; cd /app; PYTHONPATH=/app/src python - <<'"'"'PY'"'"'
from minimal_kanban.telegram_ai.config import load_config
from minimal_kanban.telegram_ai.openai_client import TelegramAIOpenAIClient
client = TelegramAIOpenAIClient(load_config())
print(client.internet_search(command_text="Найди в интернете официальный сайт Toyota и ответь одной строкой с источником.", role="owner")[:800])
PY'
```

## Known Limits

- rollback is best-effort from stored before-snapshots, not a universal transaction layer;
- autopilot is disabled by default;
- document/PDF intake from Telegram is not a complete accounting workflow;
- webhook mode is deferred;
- CRM-context + internet-search + writeback workflows require careful source validation.
