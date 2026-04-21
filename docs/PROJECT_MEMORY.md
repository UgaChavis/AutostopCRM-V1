# AutoStop CRM Project Memory

Use this file for durable notes that should not be rediscovered every session.

- primary branch truth is now `autostopcrm-v1`
- `autostopCRM` is legacy and should be treated as historical until it is removed
- the local working clone now tracks `autostop-v1/autostopcrm-v1`

## Recurring Themes

- employees module changes need UI, API, and regression coverage together
- payroll actions should be smoke-tested on live UI after deploys
- create mode in employees must keep the selected record separate from new entries
- the employee month/report view can hide or discard state if not guarded carefully
- production verification is not optional after meaningful deployment work
- live UI smoke checks catch issues that unit tests can miss
- server sync is a separate step from local green tests
- the card modal's lower-right `cardAgentButton` now launches the full card enrichment flow in the background and does not auto-open the agent modal
- `run_full_card_enrichment` is the active card-button entrypoint again, backed by `/api/run_full_card_enrichment`
- the agent surface routes in `api/server.py` are active again; status/tasks/actions/scheduled-task routes are wired to `CardService`, but the green card button itself stays on the card view
- `CardService` now attaches an agent controller through the shared embedded-agent bootstrap used by both `app.py` and `mcp.main`; the old local-cleanup path still remains only as fallback when no controller is attached
- VIN enrichment is now bounded but multi-step: `decode_vin` runs first, then sparse VIN results trigger `search_web` and `fetch_page_excerpt` for the same VIN only, and the card patch merges only confirmed vehicle facts
- VIN web parsing has a known trap: prepending `explicit_vehicle` with a year can suppress model detection, so model parsing should prefer the raw web text first; the web follow-up also needs to skip 403 pages and continue to later results instead of stopping early
- VIN web parsing should stay strict about field quality: drop generic `engine_model` candidates like `size`, trim trailing label noise like `CDN. Transmission`, and only accept `gearbox_model` when it looks like a real gearbox code, not a model echo
- VIN enrichment must keep `gearbox_type` and `gearbox_model` separate: a transmission style like `automatic` should never be written into `gearbox_model`; the runner and scenario now store it as `gearbox_type`/`transmission` instead
- VIN enrichment must also continue into web follow-up when `decode_vin` is only `insufficient`; the live VIN may still be present, and the scenario now treats web-confirmed fields as enough to finish the write
- same-VIN board context must win not only in the vehicle profile patch but also in the rendered vehicle label; otherwise the passport can be correct while the card header still shows a stale `Rio / 1983` decode
- the card indicator button must use the open card state (`state.activeCard` / `state.editingId`) as the source of truth; relying only on `agentContext` can make the button say "open the card" even when the card modal is already open
- same-VIN board context is now the stronger fallback when it conflicts with sparse or noisy VIN/web decode results; model/year/engine/gearbox/drivetrain should not be overwritten by a weaker `Rio / 1983`-style parse if the board already has a richer same-VIN profile
- the CRM ↔ agent bridge is now pinned in `docs/VIN_ENRICHMENT_BRIDGE.md`: task payload uses `card_id` + `purpose=card_enrichment`, the worker must read `get_card_context(card_id)` first, and the only CRM write path is `update_card` with `description`, `vehicle`, and `vehicle_profile`; the bridge now passes the full `VehicleProfile` shape through without an extra VIN-specific trimming layer, so helper fields like `raw_input_text` and `warnings` survive the handoff
- the full-card button flow now uses `purpose=card_enrichment` in the agent task metadata, while legacy `card_autofill` remains only for the older autofill path; this keeps the green-button flow distinct from the older followup/autofill routines
- the full-card button payload now sends the VIN-only instruction through plain `task_text` instead of legacy `ai_autofill_prompt` / `ai_log_tail` fields; that trims noise without changing the bridge contract
- the full-card button payload no longer forwards `vehicle` or `context_packet`; the worker reads the card context itself, so those hints were removed from the green-button path to keep it minimal
- dead legacy server-agent compatibility stubs were removed from `card_service.py`; only the live `set_card_ai_autofill` and `run_full_card_enrichment` paths remain in the class body
- a dead helper alias `_extract_autofill_symptom_query_legacy_unused` was removed from `agent/runner.py`; the canonical helper is `_extract_autofill_symptom_query`
- the green-button flow no longer calls `agent_status()` just to set `server_available`; the button path now treats an attached agent control as available and skips that extra hot-path status probe
- the green card button now only enqueues background work and updates the indicator; it no longer opens the agent modal surface on click
- the CRM deploy path in this repo only targets `/opt/autostopcrm`; the AI worker and VPN helpers are separate repositories and need their own deploy targets
- `agent/remodel.py` now uses `StrEnum` for its AI enum sets, and the surrounding `agent/*` modules were reformatted so import blocks stay canonical without touching behavior

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
