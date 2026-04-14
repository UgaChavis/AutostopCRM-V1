# AutoStop CRM: Start Here

This is the first file a new developer or agent should read in branch `autostopCRM`.

## Current Truth

- branch: `autostopCRM`
- current synced HEAD at last verification: `1796ec9` `Fix board column drag capture area`
- local, GitHub, and production should be kept aligned on the same `autostopCRM` HEAD
- production CRM: `https://crm.autostopcrm.ru`
- production MCP: `https://crm.autostopcrm.ru/mcp`
- production server IP at last verification: `46.8.254.243`
- production repo path: `/opt/autostopcrm`

## What The Product Is

AutoStop CRM is an auto-workshop CRM built around:

- kanban board and card workflow
- drag-and-drop card movement and column reordering
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
9. `src/minimal_kanban/web_assets.py`

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
- `src/minimal_kanban/services/column_service.py`: column ordering and column operations
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
- quick card prompts from the UI route into the same structured card pipeline instead of a weaker free-form loop
- orchestration traces include per-scenario feedback, warnings, notes, and follow-up reasons
- agent follow-up now uses per-run cache and quieter backoff for repeated no-op passes
- MCP read-path and mixed MCP test runs were recently stabilized

Known remaining limitation:

- if the external VIN source returns sparse data, the agent can still end with a partial VIN result
- the next likely improvement is a second VIN fallback source for weak European VIN decodes

## Recent Practical Changes

- employees module now supports up to `15` employees without stale-ID overwrite on create
- employees workspace was rebuilt into a clearer master-detail layout
- board columns can now be reordered left-to-right with native drag-and-drop
- column drag capture now starts from the whole column, not only a narrow header area

## Current Verification Baseline

- last known full-suite baseline before this update cycle was green
- latest targeted local regressions for `service + api + web_assets` are green
- latest targeted mixed `agent + MCP + API` runs are green
- MCP tests now pass cleanly even with `ResourceWarning` escalated to error
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
- board column drag is based on native HTML5 DnD and should still be rechecked on touch-heavy or unusual browser setups

## Rule For Future Updates

Update this file whenever:

- a new commit is pushed to GitHub
- production is redeployed
- server address or DNS changes
- AI orchestration changes materially
- the recommended first-read order changes
- the set of active root-level docs changes
