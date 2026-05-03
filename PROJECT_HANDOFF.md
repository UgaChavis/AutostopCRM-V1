# AutoStop CRM Project Handoff

This is the primary developer handoff document for branch `autostopcrm-v1`.

Use it as the operational overview after reading `00_START_HERE_AUTOSTOP_CRM.md`.

Supplement it with `docs/OPERATIONS_RUNBOOK.md` for the practical release path.

It should answer four questions fast:

1. What the product is right now.
2. How the codebase is structured.
3. What changed most recently.
4. What must stay stable during the next iteration.

## 1. Product Snapshot

AutoStop CRM is a production-oriented CRM for an auto workshop built around a kanban board, local API, MCP surface, and a new Telegram-first AI Board Manager worker.

Current product scope:

- kanban board with cards, custom columns, archive, stickies, unread markers, and update markers
- drag-and-drop for cards and board columns
- clients module with optional card links, physical/person organization profiles, repair history and requisites
- vehicle profile handling and card autofill
- repair orders with works, materials, payments, status flow, exports, and printing
- operator authentication and admin user management
- cashboxes, cash transactions, employees, and payroll reports
- shared Files workspace for common workshop documents
- MCP server for ChatGPT / OpenAI tool access
- Telegram AI Board Manager for owner-controlled CRM operations through text, voice and photo messages
- lower-right card indicator that enqueues the bounded agent-driven card enrichment flow without opening the agent modal

Legacy names still exist and are expected:

- Python package name: `minimal_kanban`
- local app data root: `%APPDATA%\\Minimal Kanban`
- some connector texts still mention `Minimal Kanban`

## 2. Current Branch And Environments

Active working branch:

- `autostopcrm-v1`

Environment rule:

- local repo, GitHub `autostopcrm-v1`, and working production should stay aligned on the same branch head
- current synced HEAD changes frequently; verify it with `git rev-parse --short HEAD` before acting on this file
- after any meaningful change, verify all three explicitly instead of relying on stale notes in this file

Working production DNS:

- CRM: `https://crm.autostopcrm.ru`
- MCP: `https://crm.autostopcrm.ru/mcp`
- server IP at verification time: `46.8.254.243`

Working server repository path:

- `/opt/autostopcrm`

Important operational note:

- production still currently accepts the default admin account
- this is a real risk, but it has not been rotated in the current line yet

Current alignment rule:

- local `autostopcrm-v1`, GitHub `autostopcrm-v1`, and production `/opt/autostopcrm` must be verified with commands before release work
- this handoff intentionally avoids pinning a stale commit hash; use `git rev-parse --short HEAD` and the runbook sync commands

Last verified sync snapshot:

- date: `2026-05-03`
- local HEAD, GitHub `origin/autostopcrm-v1`, and production HEAD were verified aligned after the dead-helper cleanup; verify the exact current commit with the runbook commands
- production working tree had one untracked file during verification: `telegram-ai.env`
- application cleanup commit: `2c7c544` `Remove dead web helpers and refresh docs`

## 3. Runtime Architecture

The system is intentionally split into layers.

```text
Desktop UI / Browser UI
        ->
local HTTP API
        ->
CardService + domain services
        ->
JsonStore / persistent JSON files

External ChatGPT / OpenAI / MCP client
        ->
MCP server
        ->
local HTTP API
        ->
same CardService and storage

```

Core rule:

- UI, MCP, and agent should converge on the same business core instead of duplicating logic

## 4. Code Map

### Entrypoints

- `main.py`: desktop application entry
- `main_mcp.py`: MCP-only entry
- `main_telegram_ai.py`: Telegram AI Board Manager worker entry

### Runtime assembly

- `src/minimal_kanban/app.py`: desktop bootstrap, API startup, auth, settings, MCP runtime, tunnel runtime
- `src/minimal_kanban/config.py`: ports, paths, environment configuration
- `src/minimal_kanban/logging_setup.py`: logging setup

### Domain and storage

- `src/minimal_kanban/models.py`: core board and finance models
- `src/minimal_kanban/vehicle_profile.py`: vehicle profile schema and normalization
- `src/minimal_kanban/repair_order.py`: repair order model
- `src/minimal_kanban/storage/json_store.py`: persistent JSON storage

### Service layer

- `src/minimal_kanban/services/card_service.py`: main orchestration service for board and a large part of business behavior
- `src/minimal_kanban/services/column_service.py`: column ordering and column operations
- `src/minimal_kanban/services/snapshot_service.py`: snapshots, wall, compact reads, search
- `src/minimal_kanban/services/vehicle_profile_service.py`: profile enrichment and normalization

### API and auth

- `src/minimal_kanban/api/server.py`: local HTTP API, auth gates, write protection, transport behavior
- `src/minimal_kanban/operator_auth.py`: operator sessions, admin users, auth flows

### MCP layer

- `src/minimal_kanban/mcp/server.py`: MCP tool registration and transport surface
- `src/minimal_kanban/mcp/client.py`: board API client
- `src/minimal_kanban/mcp/runtime.py`: MCP runtime server wrapper
- `src/minimal_kanban/mcp/oauth_provider.py`: embedded OAuth metadata and provider behavior

### Telegram AI layer

- `src/minimal_kanban/telegram_ai/`: Telegram polling, auth, OpenAI calls, CRM tool registry, verification and audit
- `docs/TELEGRAM_AI_BOARD_MANAGER.md`: technical runtime map, env variables, tools and test commands
- `docs/AUTOSTOP_TELEGRAM_AI_SETUP_RU.md`: Russian setup and production verification guide committed to GitHub
- `C:\Users\User\Desktop\AUTOSTOP_TELEGRAM_AI_SETUP_RU.md`: Russian setup/runbook for creating the Telegram bot and starting the worker

### Retired agent layer

- old server-agent modules now remain only as internal compatibility code
- they are no longer part of active startup, deploy, or visible UI flows
- current product behavior keeps only the background card-enrichment trigger through `CardService`

### Printing

- `src/minimal_kanban/printing/service.py`: print logic
- `src/minimal_kanban/printing/pdf.py`: PDF generation
- `src/minimal_kanban/printing/template_engine.py`: template rendering

### UI

- `src/minimal_kanban/ui/main_window.py`: desktop shell
- `src/minimal_kanban/ui/settings_window.py`: settings UI
- `src/minimal_kanban/web_assets.py`: browser-facing UI assets

## 5. AI Runtime Status

Current active AI direction:

- Telegram AI Board Manager is the new main AI runtime
- it runs as `main_telegram_ai.py` or Docker service `autostopcrm-telegram-ai`
- it receives owner commands through Telegram long polling
- it calls OpenAI for structured decisions, voice transcription, photo/vision extraction, and explicit internet search
- it writes to CRM only through the local HTTP API and verifies writes by read-back
- every run writes redacted audit under `telegram_ai/audit.jsonl`
- it keeps compact per-chat memory so follow-up commands can refer to recent cards/actions
- it answers from completed tool results; future-promise phrases like `сейчас пришлю` are treated as a bug
- direct internet-search uses the base model for stability; complex CRM planning can escalate to the strong model

Compatibility behavior:

- the old card indicator / VIN-enrichment flow remains compatibility code
- do not use the old AutostopAI repository as the base for new product work
- do not add direct storage writes, shell actions, or hard delete behavior to the Telegram AI worker

## 6. Most Recent Development State

Latest completed wave, in practical terms:

- employee creation was fixed so stale IDs no longer overwrite existing staff records
- employees module now supports up to `15` employees
- employees UI was rebuilt into a clearer master-detail workspace
- board column reordering was added with native HTML5 drag-and-drop
- column drag capture was widened from a narrow header handle to the whole column shell
- old AI worker contour was rolled back to one local card-cleanup action
- visible AI chat / dock / popup entries were removed
- server deploy topology was reduced to the main `autostopcrm` service
- MCP and local API were kept as the active integration layers
- Telegram AI Board Manager was added back as a separate worker service, not as UI-thread logic and not as the old VIN-only agent
- the clients module now has inline existing-client search in the card editor, plus Chrome autofill suppression on the relevant client/card fields
- current clients UX also includes vehicle previews, better phone matching, debt summary, and more readable repair-order cards in the profile pane
- client profiles keep a minimal phone model: `phone` is the first/main number, `phones[]` stores up to 3 unique numbers, and client search matches any saved phone
- bulk client imports from cleaned Markdown JSONL preserve legal requisites, phones and saved profile vehicles; a state backup is created before every apply run
- client search is optimized for thousand-entry directories by checking profile fields and saved vehicles before falling back to related card history
- the clients modal does not preload the whole directory: initial UI load is capped, and typing in the search field calls backend search across all clients

Latest completed stabilization wave:

- Telegram AI final replies were moved after actual CRM tool execution, so the bot no longer says `сейчас пришлю` and then loses the result
- Telegram AI conversation memory was added for same-chat follow-up commands
- Telegram AI direct internet-search was added for explicit commands like `найди в интернете`, using OpenAI `web_search_preview`
- Telegram AI complex CRM decisions can escalate from `gpt-5.4-mini` to `gpt-5.4`
- Telegram AI web-search was stabilized after live timeout/429 failures: direct web-search now uses the base model with low search context and one retry
- this historical wave was synced and redeployed at `18e1326`; always verify the current production HEAD before release work
- repair-order modal stack from `desktop -> repair orders -> repair order -> nested windows` was fixed in UI shell so the repair-orders list remains the real parent layer
- opening a repair order from the list no longer intentionally closes the list first or leaves the user falling back into an unexpected card layer
- cashbox journal API/UI/MCP now returns machine-readable `cash_journal.v2` data (`entries`, `days`, `weeks`, `months`, `totals`) plus a Markdown human report grouped by months, weeks, and days; UI download saves `.md`
- repair-order cashless totals now follow the selected rule: cashless path = subtotal + `15%`, taxes reflect that `15%` component, and due totals use the same model across domain, API, UI, and MCP text output
- MCP repair-order expectations were updated to match the corrected cashless total model
- mobile-lite board mode now activates automatically on narrow screens and collapses the board into a single-column, low-noise layout with heavy controls hidden by default
- clients module was added to the topbar: operators can create people, IP/OOO/company profiles, store requisites, see related vehicles and repair-order history, and optionally link cards to clients without forcing every card into the client directory
- card vehicle-passport customer fields now show an inline existing-client picker with phone and vehicle preview; choosing a client fills customer fields and links after save for new cards
- client profiles can now store imported `vehicles[]` directly with stable vehicle ids; cards may link to both `client_id` and `client_vehicle_id`, so the add-card flow can choose a concrete car from a client's garage
- the Clients module vehicle block now supports add/edit/delete for saved client cars; edits sync VIN/license/model into linked card vehicle passports, and deletion removes only the car link, not cards or repair orders
- MCP client tools include client list/search/profile/stats/create/update/delete/link/unlink/suggestion plus client vehicle upsert/delete commands
- shared Files v1.0 was added as a small server-side file exchange: separate storage folder, JSON metadata index, 500 MB limit, browser UI, API routes, and MCP tools
- the board header and cards were compacted for smaller monitors: rare module buttons sit on the left, primary workflow buttons stay on the right, button/card padding is tighter, and the card signal row shares one line with tags
- shared Files now supports right-click paste from files copied in Windows Explorer through the local API clipboard backend fallback; browser clipboard paste and drag-and-drop upload remain available
- clients module audit fixed the connection-card allowed tool list, direct API nested `client`/`patch` payloads, and `+7`/`8` phone matching for client suggestions/history
- shared Files icon placement was stabilized around a non-overlapping grid, persisted icon positions, and drag movement
- card journal display is intentionally minimal: it keeps previous and new text visible as `до:` / `после:` so deleted text can be recovered from the journal
- hidden AI-managed card board summaries were added: API/MCP/Telegram can write a five-line `board_summary`, board cards show it before raw description text, and normal card edits mark the summary stale for agent refresh
- updated-card badges now clear optimistically in the browser on hover/open, while the existing API path still persists the seen state
- generated inline browser JavaScript is now extracted from `BOARD_WEB_APP_HTML` and checked with Node syntax validation through `scripts/check_web_assets_js.py`

Most recent important commits in the current line:

- `2c7c544` `Remove dead web helpers and refresh docs`
- `061cda8` `Speed up updated card badge clearing`
- `43ce413` `Add AI card board summaries`
- `0ab779b` `Refine card journal with full before-after history`
- `82d1f3e` `Audit docs and harden browser asset checks`
- `fae732b` `Fix board startup newline in journal JS`
- `c8e081a` `Fix repeated client import matching`
- `1ab93bf` `Add client vehicle import support`
- `18e1326` `Consolidate MCP docs and remove stale guides`
- `2261e8d` `Refresh docs after clients and MCP audit`
- `524b114` `Audit clients module integrations`
- `269639e` `Suppress browser autofill in client forms`
- `1b17b1b` `Improve client order card readability`
- `0ac93c3` `Improve client phone search matching`
- `d102a42` `Add clients module and MCP tools`
- `fa3f574` `Make Telegram AI web search faster`
- `112f871` `Stabilize Telegram AI web search model`
- `7ccd981` `Add Telegram AI web search fallback`
- `e2081df` `Escalate complex Telegram AI requests to strong model`
- `dfbd65b` `Fix Telegram AI web search response mode`
- `7c3e02d` `Add direct Telegram AI internet search`
- `1796ec9` `Fix board column drag capture area`
- `04d3cd9` `Add board column drag and drop reordering`
- `ca8a725` `Fix employees create mode overwrite path`
- `cccfd83` `Modernize employees module workspace UI`
- `1309577` `Fix employees create mode and support up to fifteen`
- `c157434` `Improve agent follow-up caching and backoff`
- `d4693b0` `Refine agent follow-up and MCP read resilience`
- `cfe7f9a` `Improve agent follow-up limits and status visibility`
- `f52aa15` `Add scoped repair order search`
- `4879de3` `Add license plate search mode`

Current stability note:

- this branch is still an incremental production line, not a refactor branch
- recent work favored local fixes, targeted regression coverage, and production-safe behavior
- latest client-module and bulk-import pass is covered by local service/API/MCP/web-assets/connection-card tests and full discovery

## 7. Production Verification Snapshot

At the current verification baseline, production reported:

- site returns `200 OK`
- MCP live check passes
- `autostopcrm` container is healthy
- no separate `autostopcrm-agent` container is expected anymore
- `autostopcrm-telegram-ai` is expected when Telegram AI is enabled; it opens no public ports
- production repo HEAD matched local and GitHub during the post-cleanup deploy check; verify the exact current commit from command output
- public MCP returned `75` tools
- public anonymous write protection returned `401 unauthorized`

Operational reality:

- production is currently healthy enough for continued iterative work
- the main workflow risk is accidental drift between local, GitHub, and server state
- the server repo was not perfectly clean because of an untracked `telegram-ai.env`; treat that as environment drift, not branch drift

Current post-sync rule for this pass:

- local regression must be rerun after meaningful code or UI changes
- local/GitHub/production parity must be verified from command output, not from stale handoff notes
- connector and MCP live checks should be repeated after deploy
- board-summary checks should cover `set_card_board_summary`, `board_summary_stale`, card journal entry `board_summary_changed`, and board preview fallback to `description_preview`
- client MCP checks should cover `list_clients`, `search_clients`, `create_client`, `update_client`, `suggest_clients_for_card`, `link_card_to_client`, `unlink_card_from_client`, `get_client`, and `get_client_stats`
- the active API surface includes `/api/agent_status`, `/api/agent_tasks`, `/api/agent_actions`, `/api/agent_scheduled_tasks`, and `/api/agent_enqueue_task`
- `deploy.sh` must sync `autostopcrm-v1`

## 8. Test And Verification Baseline

Main local regression command:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
```

Current known verification baseline:

- `scripts/doctor.ps1` must pass before release
- `scripts/run_checks.ps1` must pass, including generated browser-JS syntax validation
- `python -m unittest discover -s tests -v` must pass before deploy
- 2026-05-03 latest full local pass: `518` unittest tests passed after the dead legacy web-helper cleanup
- `python scripts/audit_localization.py` must pass when UI/docs text changed
- isolated browser smoke on 2026-05-03 loaded the board, opened topbar modules, opened a card journal, and reported no console errors or failed requests
- post-cleanup public smoke on 2026-05-03: root HTML `200 OK`, about `998902` bytes in `869 ms`; compact no-archive board snapshot about `269666` bytes in `664 ms`

Main test areas:

- `tests/test_api.py`
- `tests/test_service.py`
- `tests/test_mcp.py`
- `tests/test_mcp_main.py`
- `tests/test_printing_service.py`
- `tests/test_settings_service.py`
- `tests/test_settings_ui.py`
- `tests/test_ui_smoke.py`
- `tests/test_web_assets.py`

## 9. Deployment Workflow

Primary deploy path:

```bash
cd /opt/autostopcrm
git fetch origin autostopcrm-v1
git reset --hard origin/autostopcrm-v1
./deploy.sh
```

Current compose services:

- `autostopcrm`: main app, local API, MCP
- `autostopcrm-telegram-ai`: Telegram long-polling AI worker, CRM API client and audit writer

Useful production verification commands:

```bash
cd /opt/autostopcrm
docker compose ps
docker compose exec -T autostopcrm python scripts/check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url http://127.0.0.1:41731 --mcp-url https://crm.autostopcrm.ru/mcp --operator-username admin --operator-password admin --expect-admin
```

## 10. Current Risks And Cleanup Targets

Known risks:

- production still currently accepts the default admin account
- some docs and source files still carry older naming and historical assumptions
- `web_assets.py` still contains inert legacy agent/chat functions that are no longer wired into the visible UX
- board column drag currently relies on native HTML5 DnD and should be rechecked on touch-oriented setups
- `src/minimal_kanban/web_assets.py` is still a large inline asset file; split it only as a separate measured phase with rollback verification

Current cleanup policy:

- do not delete unrelated server-side files blindly
- do not rewrite production data by hand
- prefer GitHub-first changes, then deploy
- before server edits, always inspect `git status`
- keep documentation canonical; duplicate workflow, memory, module-note, and stale MCP command references should be merged into `README.md`, this handoff, `docs/OPERATIONS_RUNBOOK.md`, `API_GUIDE.md`, or `MCP_GUIDE.md`

## 11. Recommended First Read Order For A New Agent

1. `00_START_HERE_AUTOSTOP_CRM.md`
2. `PROJECT_HANDOFF.md`
3. `README.md`
4. `docs/OPERATIONS_RUNBOOK.md`
5. `API_GUIDE.md`
6. `MCP_GUIDE.md`
7. `AUTOSTOPCRM_FULL_INSTRUCTION.txt` for server-specific legacy setup details
8. `src/minimal_kanban/services/card_service.py`
9. `src/minimal_kanban/mcp/server.py`
10. `src/minimal_kanban/web_assets.py`

## 12. Maintenance Rule For This File

Update this file when any of the following happens:

- a new architectural layer is added or materially changed
- the cleanup flow or MCP/UI contract changes materially
- the working production domain or server changes
- the main branch policy changes
- regression baseline changes
- a deploy changes the production commit

When updating it, keep these sections current:

- environment alignment
- recent commits
- production verification snapshot
- current risks
- next developer orientation notes
