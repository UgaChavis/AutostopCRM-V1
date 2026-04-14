# AutoStop CRM Project Handoff

This file is the primary developer handoff document for branch `autostopCRM`.

Use it as the first read when a new agent or developer joins the project. It is meant to answer four questions fast:

1. What the product is right now.
2. How the codebase is structured.
3. What changed most recently.
4. What must stay stable during the next iteration.

This file should be updated whenever meaningful code is pushed to GitHub or deployed to the working production server.

## 1. Product Snapshot

AutoStop CRM is a production-oriented CRM for an auto workshop built around a kanban board, local API, MCP surface, and a separate server AI worker.

Current product scope:

- kanban board with cards, custom columns, archive, sticky notes, unread and updated markers
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

Known current environment alignment at the time of this update:

- local repo: `8c479fe`
- GitHub `autostopCRM`: `8c479fe`
- working production server `crm.autostopcrm.ru`: `8c479fe`

Working production DNS:

- CRM: `https://crm.autostopcrm.ru`
- MCP: `https://crm.autostopcrm.ru/mcp`
- server IP at verification time: `46.8.254.243`

Working server repository path:

- `/opt/autostopcrm`

Important operational note:

- production still currently accepts default admin credentials `admin/admin`
- this is a real security risk and should be changed in a dedicated pass

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
- `src/minimal_kanban/services/column_service.py`: column operations
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
- `src/minimal_kanban/agent/contracts.py`: formal orchestration contracts
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

The current server AI is no longer split into a free-form task loop on one side and a disconnected autofill pipeline on the other. The reasoning layer was unified into one orchestration framework.

Current core contract:

- `read -> evidence -> plan -> tools -> patch -> write -> verify`

Current agent design rules:

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

New structural status on top of the original orchestration core:

- deterministic autofill tool scenarios now have a dedicated `agent/scenarios` package and registry
- orchestration evidence now carries structured `fact_evidence` entries with `status`, `source`, and `confidence`
- planning is being split into deterministic eligibility and scenario strategy
- verification now records explicit outcome states such as `completed_confirmed` and `blocked_missing_source_data`
- card autofill adds a goal-level verifier on top of write verification
- weak VIN decode can now leave explicit fallback context evidence in the run trace instead of only a vague final note

Important files for AI work:

- `src/minimal_kanban/agent/runner.py`
- `src/minimal_kanban/agent/contracts.py`
- `src/minimal_kanban/agent/policy.py`
- `src/minimal_kanban/agent/tools.py`
- `src/minimal_kanban/agent/control.py`
- `src/minimal_kanban/agent/scenarios`
- `GPT_AGENT_11_AGENT_AUTOFILL_ORCHESTRATION.md`
- `AI_AGENT_AUDIT_2026-04-14.md`
- `AI_AGENT_MODERNIZATION_PLAN.md`

## 6. Most Recent Development State

Latest completed development wave:

- unified orchestration core for the server AI agent
- formalized evidence / plan / tool / patch / verify contracts
- added policy-gated required tools
- made write verification rely on completed tool results correctly
- tightened verify outcome handling so manual-field drift escalates to `needs_human_review`
- made post-write verification merge `get_card_context` with `get_card` so stale wrapped payloads do not hide real card state
- changed contract write verification to require full target-patch confirmation instead of treating one matched field as a fully confirmed write
- fixed deploy smoke behavior so server-side checks do not fail only because public URL is not reachable from inside the container
- refreshed project overview docs

Most recent important commits in the current line:

- `9b4553d` `Unify server agent orchestration core`
- `fd891d9` `Stabilize agent verification and deploy smoke`
- current working tree also contains the next modernization wave for the AI agent:
  - scenario registry and scenario executors
  - structured fact evidence
  - planning eligibility separation
  - explicit verify outcome states
  - goal-level autofill verification
  - VIN fallback evidence

What `fd891d9` represents in practice:

- local repo, GitHub, and working production were re-aligned
- agent verification bug was fixed
- server deploy smoke was stabilized
- production `crm.autostopcrm.ru` was verified as live with MCP and agent runtime responding

## 7. Production Verification Snapshot

At the time of this document update, the working production server reported:

- board columns: `18`
- active cards: `47`
- archived cards: `46`
- stickies: `1`
- repair orders: `22`
- MCP tool count: `60`
- agent runtime model: `gpt-5.4-mini`

Production verification that was already run:

- site returns `200 OK`
- local API returns board and auth responses correctly
- anonymous public write attempts are blocked
- MCP endpoint is live and tool list is reachable
- agent runtime heartbeat is live
- local full regression suite passes

## 8. Test And Verification Baseline

Main local regression command:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s .\tests -v
```

Current known baseline:

- `348/348 OK`

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

Known residual noise:

- some MCP tests still emit `anyio` `ResourceWarning` lines for unclosed memory receive streams
- suite is green, but this remains a cleanup candidate

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

- default admin credentials are still enabled in production
- server working tree contains unrelated untracked `amnezia` files on the production host
- some docs still carry older naming and operational assumptions
- MCP tests are green but still noisy because of stream cleanup warnings

Current cleanup policy:

- do not delete unrelated server-side files blindly
- do not rewrite production data by hand
- prefer GitHub-first changes, then deploy
- before server edits, always inspect `git status`

## 11. Recommended First Read Order For A New Agent

1. `PROJECT_HANDOFF.md`
2. `README.md`
3. `AUTOSTOPCRM_FULL_INSTRUCTION.txt`
4. `API_GUIDE.md`
5. `MCP_GUIDE.md`
6. `src/minimal_kanban/agent/runner.py`
7. `src/minimal_kanban/services/card_service.py`

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
