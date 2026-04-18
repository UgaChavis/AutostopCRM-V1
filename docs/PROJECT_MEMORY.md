# AutoStop CRM Project Memory

Use this file for durable notes that should not be rediscovered every session.

## Recurring Themes

- employees module changes need UI, API, and regression coverage together
- payroll actions should be smoke-tested on live UI after deploys
- create mode in employees must keep the selected record separate from new entries
- the employee month/report view can hide or discard state if not guarded carefully
- production verification is not optional after meaningful deployment work
- live UI smoke checks catch issues that unit tests can miss
- server sync is a separate step from local green tests
- the card modal's lower-right `cardAgentButton` now launches the full card enrichment flow and opens the agent surface for progress
- `run_full_card_enrichment` is the active card-button entrypoint again, backed by `/api/run_full_card_enrichment`
- the agent surface routes in `api/server.py` are active again; status/tasks/actions/scheduled-task routes are wired to `CardService`
- `CardService` now attaches an agent controller through the shared embedded-agent bootstrap used by both `app.py` and `mcp.main`; the old local-cleanup path still remains only as fallback when no controller is attached
- VIN enrichment is now bounded but multi-step: `decode_vin` runs first, then sparse VIN results trigger `search_web` and `fetch_page_excerpt` for the same VIN only, and the card patch merges only confirmed vehicle facts
- VIN web parsing has a known trap: prepending `explicit_vehicle` with a year can suppress model detection, so model parsing should prefer the raw web text first; the web follow-up also needs to skip 403 pages and continue to later results instead of stopping early
- VIN web parsing should stay strict about field quality: drop generic `engine_model` candidates like `size`, trim trailing label noise like `CDN. Transmission`, and only accept `gearbox_model` when it looks like a real gearbox code, not a model echo
- VIN enrichment must keep `gearbox_type` and `gearbox_model` separate: a transmission style like `automatic` should never be written into `gearbox_model`; the runner and scenario now store it as `gearbox_type`/`transmission` instead
- the card indicator button must use the open card state (`state.activeCard` / `state.editingId`) as the source of truth; relying only on `agentContext` can make the button say "open the card" even when the card modal is already open

## Current Known Cautions

- default admin credentials are still a production concern
- browser-run artifacts should not be left in the worktree
- stale branch notes are less reliable than `git rev-parse` output
- web-followup for VIN cards should stay constrained to VIN-derived vehicle facts and not branch into parts/DTC/maintenance unless the card explicitly asks for that

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
