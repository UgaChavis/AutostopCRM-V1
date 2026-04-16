# AutoStop CRM: First Read

This is the first file a new developer or agent should read in branch `autostopCRM`.

## Current Truth

- branch: `autostopCRM`
- current synced HEAD must be verified before work with `git rev-parse --short HEAD`
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
- local card cleanup action from the card indicator

## First Read Order

1. `00_START_HERE_AUTOSTOP_CRM.md`
2. `MASTER-PLAN.md`
3. `PROJECT_HANDOFF.md`
4. `README.md`
5. `AUTOSTOPCRM_FULL_INSTRUCTION.txt`
6. `API_GUIDE.md`
7. `MCP_GUIDE.md`
8. `src/minimal_kanban/services/card_service.py`
9. `src/minimal_kanban/mcp/server.py`
10. `src/minimal_kanban/web_assets.py`

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
- `main_agent.py`: retired compatibility stub
- `src/minimal_kanban/api/server.py`: API surface
- `src/minimal_kanban/services/card_service.py`: business core
- `src/minimal_kanban/services/column_service.py`: column ordering and column operations
- `src/minimal_kanban/mcp/server.py`: MCP server
- `src/minimal_kanban/web_assets.py`: browser UI

## Current Cleanup Status

The active in-product AI behavior is now only the local card cleanup action:

- click the card indicator
- run local cleanup through `CardService.cleanup_card_content`
- patch only obvious local fields
- verify after write
- refresh the card

There is no active server AI worker in startup or deploy.

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
- `web_assets.py` still contains inert legacy agent/chat code paths that are no longer wired into the visible UI

## Rule For Future Updates

Update this file whenever:

- a new commit is pushed to GitHub
- production is redeployed
- server address or DNS changes
- AI orchestration changes materially
- the recommended first-read order changes
- the set of active root-level docs changes
