# AutoStop CRM: Start Here

This is the first file a new developer or agent should read in branch `autostopCRM`.

## Current Truth

- branch: `autostopCRM`
- local, GitHub, and production should be kept aligned on the same `autostopCRM` HEAD
- production CRM: `https://crm.autostopcrm.ru`
- production MCP: `https://crm.autostopcrm.ru/mcp`
- production server IP at last verification: `46.8.254.243`
- production repo path: `/opt/autostopcrm`

## What The Product Is

AutoStop CRM is an auto-workshop CRM built around:

- kanban board and card workflow
- vehicle profile enrichment
- repair orders, works, materials, payments, printing
- operator authentication and admin users
- cashboxes, employees, payroll
- MCP server for external tool access
- separate long-running server AI worker

## First Read Order

1. `00_START_HERE_AUTOSTOP_CRM.md`
2. `PROJECT_HANDOFF.md`
3. `README.md`
4. `AUTOSTOPCRM_FULL_INSTRUCTION.txt`
5. `API_GUIDE.md`
6. `MCP_GUIDE.md`
7. `src/minimal_kanban/agent/runner.py`
8. `src/minimal_kanban/services/card_service.py`

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

Server AI worker
 -> task queue / scheduler
 -> orchestration core
 -> bounded tools
 -> patch / verify / follow-up
```

## Key Files

- `main.py`: desktop entry
- `main_mcp.py`: MCP entry
- `main_agent.py`: agent worker entry
- `src/minimal_kanban/api/server.py`: API surface
- `src/minimal_kanban/services/card_service.py`: business core
- `src/minimal_kanban/agent/control.py`: queue and schedules
- `src/minimal_kanban/agent/runner.py`: AI orchestration core
- `src/minimal_kanban/agent/contracts.py`: evidence / plan / patch / verify contracts
- `src/minimal_kanban/agent/scenarios`: structured deterministic scenario executors
- `src/minimal_kanban/mcp/server.py`: MCP server
- `src/minimal_kanban/web_assets.py`: browser UI

## Current AI Status

The server AI is now built around one contract:

- `read -> evidence -> plan -> tools -> patch -> write -> verify`

Important current behavior:

- card autofill uses structured deterministic orchestration
- quick card prompts such as `VIN`, `ЗАПЧАСТИ`, `ТО`, `ПОРЯДОК` now carry `quick_template` metadata
- those quick prompts route into the same structured card pipeline instead of the weaker free-form loop
- orchestration traces include per-scenario feedback, warnings, notes, and follow-up reasons

Known remaining limitation:

- if the external VIN source returns sparse data, the agent can still end with a partial VIN result
- the next likely improvement is a second VIN fallback source for weak European VIN decodes

## Current Verification Baseline

- full regression: `363/363 OK`
- production site: `200 OK`
- production MCP: `ok`, `60` tools
- production agent runtime: `ok`
- production agent model: `gpt-5.4-mini`

## Documentation Layout

Primary active docs kept in the repo root:

- `00_START_HERE_AUTOSTOP_CRM.md`
- `PROJECT_HANDOFF.md`
- `README.md`
- `AUTOSTOPCRM_FULL_INSTRUCTION.txt`
- `API_GUIDE.md`
- `MCP_GUIDE.md`
- `README_SETTINGS.md`
- `AI_AGENT_AUDIT_2026-04-14.md`
- `AI_AGENT_MODERNIZATION_PLAN.md`

Archived legacy docs are kept under:

- `docs/archive/legacy_root_docs`

## Current Risks

- production still uses the default admin account and needs a separate credential rotation pass
- MCP tests are green but still emit `anyio` `ResourceWarning` noise

## Rule For Future Updates

Update this file whenever:

- a new commit is pushed to GitHub
- production is redeployed
- server address or DNS changes
- AI orchestration changes materially
- the recommended first-read order changes
- the set of active root-level docs changes
