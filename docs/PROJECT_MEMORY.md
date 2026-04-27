# AutoStop CRM Project Memory

Use this file for durable notes that should not be rediscovered every session.

- primary branch truth is now `autostopcrm-v1`
- the local working clone now tracks `autostopcrm-v1`
- production deploy must use `origin/autostopcrm-v1`; `deploy.sh` defaults to that branch
- Telegram AI Board Manager now lives in the CRM repo as `main_telegram_ai.py` + `src/minimal_kanban/telegram_ai/`; the old AutostopAI/VIN worker is legacy and should not be used as the product base
- Telegram AI writes must go through `BoardApiClient`/local HTTP API and then read-after-write verification; no raw JSON storage writes from Telegram runtime

## Recurring Themes

- employees module changes need UI, API, and regression coverage together
- payroll actions should be smoke-tested on live UI after deploys
- create mode in employees must keep the selected record separate from new entries
- the employee month/report view can hide or discard state if not guarded carefully
- production verification is not optional after meaningful deployment work
- live UI smoke checks catch issues that unit tests can miss
- server sync is a separate step from local green tests
- the active AI product line is now Telegram-first: `main_telegram_ai.py` starts a separate long-polling worker and `src/minimal_kanban/telegram_ai/` owns authorization, OpenAI calls, CRM tools, verification and audit
- the Telegram worker is an in-repo Docker service named `autostopcrm-telegram-ai`; it opens no public port and talks to CRM through `http://autostopcrm:41731`
- owner access is mandatory; without `AUTOSTOP_TELEGRAM_OWNER_IDS`, bot token, OpenAI key, and `AUTOSTOP_TELEGRAM_AI_ENABLED=1`, the worker stays safe-disabled and does not expose CRM data
- all Telegram AI writes must use the explicit CRM tool registry and `BoardApiClient`; raw JSON storage writes, shell commands, hard delete, and secret disclosure are out of scope
- every write run should record redacted JSONL audit in `telegram_ai/audit.jsonl` and verify writes by reading CRM state back through the API
- Telegram AI must answer from completed `tool_results`, not from the pre-tool model promise; deferred phrases like `сейчас пришлю` are treated as a bug because the worker sends one reply per update
- Telegram AI has a direct internet-search route for explicit commands like `найди в интернете`/`загугли`; it uses OpenAI `web_search_preview`, skips CRM tools, and returns the result in the same Telegram reply
- Telegram AI also exposes `internet_search` as a model tool, so the decision JSON can request web search without relying only on the pre-route detector
- Telegram AI now attaches a compact `conversation_state.last_card` from the previous card/search result so follow-up commands like "this card" or "that one" can reuse the last selected card instead of asking for it again
- Telegram AI now also attaches `conversation_state.last_vin` when the previous verified card result includes a VIN, so follow-up commands can reuse the already-seen VIN instead of asking the user to resend it
- Telegram AI voice notes are transcribed locally first with `faster-whisper`; OpenAI transcription stays as fallback when the local backend is missing or unavailable
- Telegram AI escalates complex multi-step/VIN/OEM/parts/research CRM-planning commands from `AUTOSTOP_AI_MODEL` to `AUTOSTOP_AI_STRONG_MODEL` with `AUTOSTOP_AI_STRONG_REASONING_EFFORT`
- VIN/parts internet-search requests should keep using `AUTOSTOP_AI_STRONG_MODEL` with web-search enabled; do not downgrade that path to the base model just to shorten the prompt
- direct internet-search uses the base model for simple lookups, but complex VIN/OEM/parts searches now use the strong model with high reasoning and fall back once to the base model on transient OpenAI failure
- direct internet-search now retries transient OpenAI `429`/timeout errors before falling back to the base model, so short rate-limit spikes do not surface as hard failures
- direct internet-search Telegram replies should not include raw URLs or markdown links; sources are shown as readable names only
- Telegram AI card-read replies must be human-readable summaries, not execution logs: hide tool names, verification marks, raw status/tag/deadline fields, and technical column ids like `column_6`
- the Telegram worker now catches unexpected update-pipeline errors and still sends a fallback failure reply instead of going silent
- the old AutostopAI repository and VIN/green-button worker experiments are legacy context only; do not use them as the base for new product work
- the card indicator flow can remain as compatibility behavior, but new AI work should go through the Telegram Board Manager unless the user explicitly reopens the card-button feature
- the CRM deploy path in this repo targets `/opt/autostopcrm`; it now includes both `autostopcrm` and the optional in-repo `autostopcrm-telegram-ai` service
- `agent/remodel.py` now uses `StrEnum` for its AI enum sets, and the surrounding `agent/*` modules were reformatted so import blocks stay canonical without touching behavior

## Telegram AI Checkpoint: 2026-04-25

- commit synced locally, on GitHub, and on production: `c0f6188`
- production repo: `/opt/autostopcrm`
- production services: `autostopcrm`, `autostopcrm-telegram-ai`
- live CRM URL: `https://crm.autostopcrm.ru`
- live MCP URL: `https://crm.autostopcrm.ru/mcp`
- MCP tool count after clients module: `59`
- clients module is optional-link by design: a card may keep manual customer fields without a `client_id`; agents should search/suggest before creating client profiles
- client-module audit: connection-card/Responses API allowed tools must include all 9 client MCP tools; direct HTTP API accepts both nested `client`/`patch` payloads and flat UI/MCP payloads; `+7` and `8` phone prefixes are equivalent for client matching
- local targeted Telegram AI regression result before documentation pass: `48/48 OK`
- production live check before documentation pass:
  - deploy smoke passed after rebuild
  - API OK
  - MCP OK
  - Telegram AI container running
- live Telegram AI VIN follow-up smoke inside `autostopcrm-telegram-ai` succeeded; `conversation_state.last_vin` was preserved across the next turn and was forwarded into internet-search payloads
- live Telegram AI web-search payload smoke confirmed simple search uses `gpt-5.4-mini`/medium, complex VIN/OEM/parts search uses `gpt-5.4`/high, and Telegram formatting sections are present
- live Telegram AI voice behavior now prefers local `faster-whisper` first and only falls back to OpenAI transcription when the local backend cannot be used
- next likely feature: composed parts-search flow through Telegram:
  1. user references a card or asks for a part
  2. agent reads CRM card/context
  3. agent extracts VIN/vehicle facts
  4. agent performs internet search
  5. agent replies with sources
  6. optional follow-up writes a compact note or attachment to CRM

## Current Known Cautions

- default admin credentials are still a production concern
- browser-run artifacts should not be left in the worktree
- stale branch notes are less reliable than `git rev-parse` output
- Telegram AI live behavior depends on real production `.env` values; local tests mock Telegram/OpenAI by default and cannot prove a real bot token or OpenAI account is valid

## When To Add Notes Here

- when a bug repeats
- when a workflow takes more than one session to rediscover
- when a production quirk matters for future debugging
- when a command proves to be the safe default

## Example Note Format

- date
- symptom
- root cause
- fix
- verification
