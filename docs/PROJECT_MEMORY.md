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
- Telegram AI escalates complex multi-step/VIN/OEM/parts/research CRM-planning commands from `AUTOSTOP_AI_MODEL` to `AUTOSTOP_AI_STRONG_MODEL` with `AUTOSTOP_AI_STRONG_REASONING_EFFORT`
- direct internet-search is intentionally kept on the base model `gpt-5.4-mini` with low search context and one retry; live tests showed strong-model web-search could timeout or return 429, so do not restore strong web-search without a production smoke test
- the old AutostopAI repository and VIN/green-button worker experiments are legacy context only; do not use them as the base for new product work
- the card indicator flow can remain as compatibility behavior, but new AI work should go through the Telegram Board Manager unless the user explicitly reopens the card-button feature
- the CRM deploy path in this repo targets `/opt/autostopcrm`; it now includes both `autostopcrm` and the optional in-repo `autostopcrm-telegram-ai` service
- `agent/remodel.py` now uses `StrEnum` for its AI enum sets, and the surrounding `agent/*` modules were reformatted so import blocks stay canonical without touching behavior

## Telegram AI Checkpoint: 2026-04-25

- commit synced locally, on GitHub, and on production: `fa3f574`
- production repo: `/opt/autostopcrm`
- production services: `autostopcrm`, `autostopcrm-telegram-ai`
- live CRM URL: `https://crm.autostopcrm.ru`
- live MCP URL: `https://crm.autostopcrm.ru/mcp`
- local full suite result before documentation pass: `431/431 OK`
- production live check before documentation pass:
  - site `200 OK`
  - API OK, active cards `60`
  - MCP OK, tool count `50`
  - anonymous public writes blocked with `401`
  - Telegram AI container running
- live web-search smoke inside `autostopcrm-telegram-ai` succeeded for a Prado J150 air-filter query and returned OEM `17801-30080` with sources
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
