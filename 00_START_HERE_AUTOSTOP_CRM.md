# AutoStop CRM: First Read

This is the first file a new developer or agent should read in branch `autostopcrm-v1`.

## Current Truth

- branch: `autostopcrm-v1`
- current synced HEAD must be verified before work with `git rev-parse --short HEAD`
- local, GitHub, and production should be kept aligned on the same `autostopcrm-v1` HEAD
- `autostopCRM` is now a legacy line and should only be treated as historical context
- the local working clone is currently aligned to `autostop-v1/autostopcrm-v1`
- production CRM: `https://crm.autostopcrm.ru`
- production MCP: `https://crm.autostopcrm.ru/mcp`
- production server IP at last verification: `46.8.254.243`
- production repo path: `/opt/autostopcrm`
- operator runbook: `docs/OPERATIONS_RUNBOOK.md`
- workflow guide: `docs/CODEX_WORKFLOW.md`

## What The Product Is

AutoStop CRM is an auto-workshop CRM built around:

- kanban board and card workflow
- drag-and-drop card movement and column reordering
- vehicle profile enrichment
- repair orders, works, materials, payments, printing
- operator authentication and admin users
- cashboxes, employees, payroll
- MCP server for external tool access
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
- `src/minimal_kanban/api/server.py`: API surface
- `src/minimal_kanban/services/card_service.py`: business core
- `src/minimal_kanban/services/column_service.py`: column ordering and column operations
- `src/minimal_kanban/mcp/server.py`: MCP server
- `src/minimal_kanban/web_assets.py`: browser UI

## Current Cleanup Status

The active in-product AI behavior is now the card enrichment trigger on the lower-right indicator:

- click the card indicator
- enqueue `run_full_card_enrichment` through `CardService`
- stay on the card while the task runs in the background
- patch card data through the agent control / shared task storage flow
- refresh the card when the task completes

There is still no embedded worker started by default in CRM startup; the separate agent process is expected to consume the shared task queue.

## Recent Practical Changes

- employees module now supports up to `15` employees without stale-ID overwrite on create
- employees workspace was rebuilt into a clearer master-detail layout
- board columns can now be reordered left-to-right with native drag-and-drop
- column drag capture now starts from the whole column, not only a narrow header area

## Current Verification Baseline

- last known full-suite baseline before this update cycle was green
- latest targeted local regressions for `service + api + web_assets` are green
- latest targeted `service + api + web_assets + MCP` runs are green
- production site: `200 OK`
- production MCP: verify current tool count from live runtime before assuming a stale number
- this deployment path covers only the CRM repo at `/opt/autostopcrm`; the AI worker and VPN helpers are separate repos and are not part of this deploy target

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

Obsolete root-level release docs and duplicated doc bundles were removed during the April 2026 cleanup pass.

## Current Risks

- production still uses the default admin account and needs a separate credential rotation pass
- the CRM now depends on the external/shared agent runtime being started if you want the enrichment flow to complete end-to-end

## Rule For Future Updates

Update this file whenever:

- a new commit is pushed to GitHub
- production is redeployed
- server address or DNS changes
- AI orchestration changes materially
- the recommended first-read order changes
- the set of active root-level docs changes
