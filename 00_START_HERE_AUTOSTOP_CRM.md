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
- workflow guide: consolidated into `docs/OPERATIONS_RUNBOOK.md`

## What The Product Is

AutoStop CRM is an auto-workshop CRM built around:

- kanban board and card workflow
- client directory with optional card links, repair history, vehicles and organization requisites
- drag-and-drop card movement and column reordering
- vehicle profile enrichment
- repair orders, works, materials, payments, printing
- operator authentication and admin users
- cashboxes, employees, payroll
- shared Files workspace for common workshop documents
- MCP server for external tool access
- Telegram AI Board Manager worker for owner-controlled CRM operations
- background card enrichment action from the card indicator

## First Read Order

1. `00_START_HERE_AUTOSTOP_CRM.md`
2. `PROJECT_HANDOFF.md`
3. `README.md`
4. `docs/OPERATIONS_RUNBOOK.md`
5. `API_GUIDE.md`
6. `MCP_GUIDE.md`
7. `MASTER-PLAN.md` if product direction or module ownership matters
8. `README_SETTINGS.md`, `docs/PRINT_DOCUMENTS.md`, or Telegram docs only when touching those workflows
9. `src/minimal_kanban/services/card_service.py`
10. `src/minimal_kanban/mcp/server.py`
11. `src/minimal_kanban/web_assets.py`

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
- shared Files v1.0 is implemented locally: server folder, metadata index, 500 MB limit, API, UI, and MCP tools
- board topbar and cards were compacted for smaller monitors: rare module buttons moved left, button/card padding reduced, and the card signal row now shares space with tags
- shared Files now supports right-click paste from copied Windows Explorer files through a local clipboard backend fallback, plus the existing browser paste and drag-and-drop paths
- shared Files icon placement was stabilized around a grid with persisted positions and drag movement
- card journal UI was made minimal and recoverable: changes expose `до:` and `после:` text instead of hiding previous content
- updated-card badges now clear optimistically on hover/open so cards with `ОБНОВЛЕНО` do not wait on the API response before becoming clickable-feeling again
- hidden AI-managed card board summaries are available through API/MCP/Telegram and are shown on board cards before raw description text
- generated inline browser JavaScript is now checked with `scripts/check_web_assets_js.py` and through `scripts/run_checks.ps1`

## Current Verification Baseline

- latest local/GitHub/production synced commit must always be verified with `git rev-parse --short HEAD`
- latest verified sync on 2026-05-03: local, GitHub, and production were aligned at `061cda8`
- production site returned `200 OK`, Docker `autostopcrm` was healthy, and `autostopcrm-telegram-ai` was running at that baseline
- production MCP strict smoke returned `75` tools
- public anonymous write protection returned `401 unauthorized`
- latest local full unit discovery in this line ran `518` tests successfully
- generated browser JS syntax check is part of `scripts/run_checks.ps1`
- this deployment path covers the CRM repo at `/opt/autostopcrm` and its optional in-repo Telegram AI worker; VPN helpers are separate deploy targets

## Current Clients Module

- topbar button: `КЛИЕНТЫ`
- supported profiles: physical person, IP, OOO, company
- cards can stay unlinked for one-off clients or link to a `client_id`
- client history is derived from explicit `client_id` plus matching customer name/phone in card profile and repair order
- client profiles can store imported `vehicles[]`; search uses profile fields and saved vehicles first, then falls back to related repair history
- MCP exposes client search, profile, stats, create/update, delete, card link/unlink, and card suggestions
- the card modal now has an inline existing-client picker with phone and vehicle preview, and Chrome autofill suppression is enabled on the relevant client/card inputs
- the clients modal opens with a short first page and uses backend search across the full client directory, not a local filter over visible rows

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
- `CHATGPT_CONNECTOR_SETUP.md`
- `docs/OPERATIONS_RUNBOOK.md`
- `docs/PRINT_DOCUMENTS.md`
- `docs/TELEGRAM_AI_BOARD_MANAGER.md`
- `docs/AUTOSTOP_TELEGRAM_AI_SETUP_RU.md`

Duplicate workflow, memory, module-note, and stale MCP command docs should be deleted after their still-valid content is merged into the canonical files above.

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
