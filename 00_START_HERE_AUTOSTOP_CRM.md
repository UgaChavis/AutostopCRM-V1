# AutoStop CRM: First Read

This is the first file a new developer or agent should read in branch `autostopcrm-v1`.

## Current Truth

- branch: `autostopcrm-v1`
- current synced HEAD must be verified before work with `git rev-parse --short HEAD`
- local, GitHub, and production should be kept aligned on the same `autostopcrm-v1` HEAD
- the local working clone is currently aligned to `origin/autostopcrm-v1`
- production CRM: `https://crm.autostopcrm.ru`
- production MCP: `https://crm.autostopcrm.ru/mcp`
- production server IP at last verification: `46.8.254.243`
- production repo path: `/opt/autostopcrm`
- operator runbook: `docs/OPERATIONS_RUNBOOK.md`
- workflow guide: `docs/CODEX_WORKFLOW.md`

## What The Product Is

AutoStop CRM is an auto-workshop CRM built around:

- kanban board and card workflow
- client directory with optional card links, repair history, vehicles and organization requisites
- drag-and-drop card movement and column reordering
- vehicle profile enrichment
- repair orders, works, materials, payments, printing
- operator authentication and admin users
- cashboxes, employees, payroll
- MCP server for external tool access
- Telegram AI Board Manager worker for owner-controlled CRM operations
- background card enrichment action from the card indicator

## First Read Order

1. `00_START_HERE_AUTOSTOP_CRM.md`
2. `MASTER-PLAN.md`
3. `PROJECT_HANDOFF.md`
4. `docs/OPERATIONS_RUNBOOK.md`
5. `docs/CODEX_WORKFLOW.md`
6. `README.md`
7. `AUTOSTOPCRM_FULL_INSTRUCTION.txt`
8. `API_GUIDE.md`
9. `MCP_GUIDE.md`
10. `src/minimal_kanban/services/card_service.py`
11. `src/minimal_kanban/mcp/server.py`
12. `src/minimal_kanban/web_assets.py`

## Main Runtime Layers

```text
UI
 -> local API
 -> CardService + domain services
 -> JsonStore

External ChatGPT / MCP client
 -> MCP server
 -> local API
 -> same business core

```

## Key Files

- `main.py`: desktop entry
- `main_mcp.py`: MCP entry
- `main_telegram_ai.py`: Telegram AI worker entry
- `src/minimal_kanban/api/server.py`: API surface
- `src/minimal_kanban/services/card_service.py`: business core
- `src/minimal_kanban/services/column_service.py`: column ordering and column operations
- `src/minimal_kanban/mcp/server.py`: MCP server
- `src/minimal_kanban/web_assets.py`: browser UI

## Current AI Status

The active AI product direction is now the Telegram AI Board Manager:

- run `main_telegram_ai.py` or Docker service `autostopcrm-telegram-ai`
- receive text, voice, or photo commands from the authorized Telegram owner
- call OpenAI for a structured decision
- call OpenAI web-search for explicit internet-search commands
- execute CRM tools only through the local HTTP API
- verify writes and record redacted audit
- keep compact per-chat memory for follow-up Telegram commands
- answer from real tool results, not from pre-tool promises

The older lower-right card enrichment button remains compatibility behavior, but it is not the base for new AI development.

## Recent Practical Changes

- Telegram AI direct internet-search is active for phrases like `найди в интернете` and `загугли`
- Telegram AI complex CRM planning can use strong model `gpt-5.4`, while direct web-search stays on `gpt-5.4-mini` for production stability
- Telegram AI live web-search was verified on production with an auto-parts query after timeout/429 stabilization
- employees module now supports up to `15` employees without stale-ID overwrite on create
- employees workspace was rebuilt into a clearer master-detail layout
- board columns can now be reordered left-to-right with native drag-and-drop
- column drag capture now starts from the whole column, not only a narrow header area

## Current Verification Baseline

- last known full-suite baseline before this update cycle was green
- latest targeted local regressions for `service + api + web_assets` are green
- latest targeted `service + api + web_assets + MCP` runs are green
- latest full local regression after Telegram AI stabilization: `431/431 OK`
- latest synced production checkpoint for Telegram AI stabilization: `fa3f574`
- production site: `200 OK`
- production MCP at last verification: OK with `59` tools
- this deployment path covers the CRM repo at `/opt/autostopcrm` and its optional in-repo Telegram AI worker; VPN helpers are separate deploy targets

## Current Clients Module

- topbar button: `КЛИЕНТЫ`
- supported profiles: physical person, IP, OOO, company
- cards can stay unlinked for one-off clients or link to a `client_id`
- client history is derived from explicit `client_id` plus matching customer name/phone in card profile and repair order
- MCP exposes client search, profile, stats, create/update, card link/unlink and card suggestions

## Documentation Layout

Primary active docs kept in the repo root:

- `00_START_HERE_AUTOSTOP_CRM.md`
- `MASTER-PLAN.md`
- `PROJECT_HANDOFF.md`
- `README.md`
- `AUTOSTOPCRM_FULL_INSTRUCTION.txt`
- `API_GUIDE.md`
- `MCP_GUIDE.md`
- `README_SETTINGS.md`
- `docs/TELEGRAM_AI_BOARD_MANAGER.md`
- `docs/AUTOSTOP_TELEGRAM_AI_SETUP_RU.md`

Obsolete root-level release docs and duplicated doc bundles were removed during the April 2026 cleanup pass.

## Current Risks

- production still uses the default admin account and needs a separate credential rotation pass
- the CRM now depends on the external/shared agent runtime being started if you want the enrichment flow to complete end-to-end
- direct Telegram AI web-search is intentionally tuned for reliability, not maximum reasoning; do not switch it back to strong-model search without live timeout and 429 checks

## Rule For Future Updates

Update this file whenever:

- a new commit is pushed to GitHub
- production is redeployed
- server address or DNS changes
- AI orchestration changes materially
- the recommended first-read order changes
- the set of active root-level docs changes
