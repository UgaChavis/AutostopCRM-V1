# AutoStop CRM Project Handoff

This is the primary developer handoff document for branch `autostopCRM`.

Use it as the operational overview after reading `00_START_HERE_AUTOSTOP_CRM.md` and `MASTER-PLAN.md`.

It should answer four questions fast:

1. What the product is right now.
2. How the codebase is structured.
3. What changed most recently.
4. What must stay stable during the next iteration.

## 1. Product Snapshot

AutoStop CRM is a production-oriented CRM for an auto workshop built around a kanban board, local API, MCP surface, and a separate server AI worker.

Current product scope:

- kanban board with cards, custom columns, archive, stickies, unread markers, and update markers
- drag-and-drop for cards and board columns
- vehicle profile handling and card autofill
- repair orders with works, materials, payments, status flow, exports, and printing
- operator authentication and admin user management
- cashboxes, cash transactions, employees, and payroll reports
- MCP server for ChatGPT / OpenAI tool access
- separate long-running server AI agent with tasks, schedules, autofill, and follow-up logic

Legacy names still exist and are expected:

- Python package name: `minimal_kanban`
- local app data root: `%APPDATA%\\Minimal Kanban`
- some connector texts still mention `Minimal Kanban`

## 2. Current Branch And Environments

Active working branch:

- `autostopCRM`

Not the active line for current production work:

- `autostopCRM-V2`

Environment rule:

- local repo, GitHub `autostopCRM`, and working production should stay aligned on the same branch head
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

Server AI worker
        ->
Agent queue / scheduler
        ->
local HTTP API + bounded research tools
        ->
contract-based write / verify / follow-up flow
```

Core rule:

- UI, MCP, and agent should converge on the same business core instead of duplicating logic

## 4. Code Map

### Entrypoints

- `main.py`: desktop application entry
- `main_mcp.py`: MCP-only entry
- `main_agent.py`: agent worker entry

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

### Agent layer

- `src/minimal_kanban/agent/control.py`: queue, schedules, status, heartbeat, autofill triggers
- `src/minimal_kanban/agent/runner.py`: main orchestration core
- `src/minimal_kanban/agent/contracts.py`: orchestration contracts
- `src/minimal_kanban/agent/policy.py`: scenario policy and required-tool gates
- `src/minimal_kanban/agent/tools.py`: bounded tool dispatch
- `src/minimal_kanban/agent/storage.py`: tasks, runs, schedules, actions persistence
- `src/minimal_kanban/agent/automotive_tools.py`: automotive external research tools
- `src/minimal_kanban/agent/web_tools.py`: bounded web access helpers
- `src/minimal_kanban/agent/openai_client.py`: OpenAI model calls
- `src/minimal_kanban/agent/instructions.py`: base system rules for the server agent

### Printing

- `src/minimal_kanban/printing/service.py`: print logic
- `src/minimal_kanban/printing/pdf.py`: PDF generation
- `src/minimal_kanban/printing/template_engine.py`: template rendering

### UI

- `src/minimal_kanban/ui/main_window.py`: desktop shell
- `src/minimal_kanban/ui/settings_window.py`: settings UI
- `src/minimal_kanban/web_assets.py`: browser-facing UI assets

## 5. AI Agent Status

The current server AI is built around one contract:

- `read -> evidence -> plan -> tools -> patch -> write -> verify`

Current design rules:

- required external tools are enforced by policy for key scenarios
- writes are patch-oriented and additive
- read-after-write verification is part of success
- follow-up remains bounded and server-owned
- MCP remains an external control plane, not the internal transport of the worker

Current important scenario families:

- VIN enrichment
- parts lookup
- DTC / fault research
- maintenance advisory
- card normalization
- repair-order assistance

Recent practical agent changes:

- follow-up limits were relaxed and made more context-aware
- `agent_status` became more honest about readiness and availability reasons
- scheduler status now exposes more diagnostics
- follow-up no-op passes became quieter and back off more cleanly
- per-run cache was added for repeated automotive lookup calls
- MCP client read-path became more resilient on invalid JSON and transient read errors
- mixed MCP test runs no longer rely on noisy `ResourceWarning` behavior

Important files for AI work:

- `src/minimal_kanban/agent/runner.py`
- `src/minimal_kanban/agent/contracts.py`
- `src/minimal_kanban/agent/policy.py`
- `src/minimal_kanban/agent/tools.py`
- `src/minimal_kanban/agent/control.py`
- `src/minimal_kanban/agent/scenarios`

## 6. Most Recent Development State

Latest completed wave, in practical terms:

- employee creation was fixed so stale IDs no longer overwrite existing staff records
- employees module now supports up to `15` employees
- employees UI was rebuilt into a clearer master-detail workspace
- board column reordering was added with native HTML5 drag-and-drop
- column drag capture was widened from a narrow header handle to the whole column shell
- AI follow-up became quieter and less wasteful on repeated no-op cycles
- MCP and server-agent test/runtime paths were cleaned up and hardened

Most recent important commits in the current line:

- `1796ec9` `Fix board column drag capture area`
- `04d3cd9` `Add board column drag and drop reordering`
- `ca8a725` `Fix employees create mode overwrite path`
- `cccfd83` `Modernize employees module workspace UI`
- `1309577` `Fix employees create mode and support up to fifteen`
- `c157434` `Improve agent follow-up caching and backoff`
- `d4693b0` `Refine agent follow-up and MCP read resilience`
- `cfe7f9a` `Improve agent follow-up limits and status visibility`

Current stability note:

- this branch is still an incremental production line, not a refactor branch
- recent work favored local fixes, targeted regression coverage, and production-safe behavior

## 7. Production Verification Snapshot

At the last verification after `1796ec9`, production reported:

- site returns `200 OK`
- MCP live check passes
- agent runtime check returns `ok`
- `autostopcrm` container is healthy
- `autostopcrm-agent` container is up
- MCP tool count is still `60`
- agent runtime model is `gpt-5.4-mini`

Operational reality:

- production is currently healthy enough for continued iterative work
- the main workflow risk is accidental drift between local, GitHub, and server state

## 8. Test And Verification Baseline

Main local regression command:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
```

Current known verification baseline:

- last known full-suite baseline before the latest UI/employee work was green
- latest targeted regressions for `tests.test_service`, `tests.test_api`, and `tests.test_web_assets` are green
- latest targeted mixed `tests.test_agent`, `tests.test_mcp`, `tests.test_mcp_main`, and `tests.test_api` runs are green
- MCP tests now pass cleanly even with `-W error::ResourceWarning`
- import smoke for `main.py`, `main_mcp.py`, and `main_agent.py` is green

Main test areas:

- `tests/test_api.py`
- `tests/test_service.py`
- `tests/test_mcp.py`
- `tests/test_mcp_main.py`
- `tests/test_agent.py`
- `tests/test_printing_service.py`
- `tests/test_settings_service.py`
- `tests/test_settings_ui.py`
- `tests/test_ui_smoke.py`
- `tests/test_web_assets.py`

## 9. Deployment Workflow

Primary deploy path:

```bash
cd /opt/autostopcrm
git fetch origin autostopCRM
git reset --hard origin/autostopCRM
./deploy.sh
```

Current compose services:

- `autostopcrm`: main app, local API, MCP
- `autostopcrm-agent`: separate worker

Useful production verification commands:

```bash
cd /opt/autostopcrm
docker compose ps
docker compose exec -T autostopcrm python scripts/check_live_connector.py --strict --site-url https://crm.autostopcrm.ru --expect-https --local-api-url http://127.0.0.1:41731 --mcp-url https://crm.autostopcrm.ru/mcp --operator-username admin --operator-password admin --expect-admin
docker compose exec -T autostopcrm python scripts/check_agent_runtime.py --local-api-url http://127.0.0.1:41731 --operator-username admin --operator-password admin
```

## 10. Current Risks And Cleanup Targets

Known risks:

- production still currently accepts the default admin account
- some docs still carry older naming and historical assumptions
- board column drag currently relies on native HTML5 DnD and should be rechecked on touch-oriented setups

Current cleanup policy:

- do not delete unrelated server-side files blindly
- do not rewrite production data by hand
- prefer GitHub-first changes, then deploy
- before server edits, always inspect `git status`

## 11. Recommended First Read Order For A New Agent

1. `00_START_HERE_AUTOSTOP_CRM.md`
2. `PROJECT_HANDOFF.md`
3. `README.md`
4. `AUTOSTOPCRM_FULL_INSTRUCTION.txt`
5. `API_GUIDE.md`
6. `MCP_GUIDE.md`
7. `src/minimal_kanban/agent/runner.py`
8. `src/minimal_kanban/services/card_service.py`
9. `src/minimal_kanban/web_assets.py`

## 12. Maintenance Rule For This File

Update this file when any of the following happens:

- a new architectural layer is added or materially changed
- the server AI behavior changes
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
