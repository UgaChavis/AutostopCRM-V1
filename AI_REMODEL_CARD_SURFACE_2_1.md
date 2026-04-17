# AI Remodel Card Surface 2.1

Module 2.1 turns the card-level AI control into a quiet indicator/trigger rather than an old agent launcher.

## Current location

- The card surface lives in `src/minimal_kanban/web_assets.py` as `#cardAgentButton`.
- It now opens the remodel AI shell in card context instead of directly presenting the legacy modal as the primary path.

## Status model

The card surface uses the remodel exposure state for `future_card_enrichment_trigger` and maps it to a compact UI status:

- `online` -> AI available / ready
- `busy` -> AI running / action in progress or gated legacy compatibility
- `error` -> AI failed state
- `idle` -> hidden / unavailable / not exposed

## Behavior

- The card trigger is intentionally quiet and small.
- It is not the main menu launcher anymore.
- The legacy agent modal is still reachable as a gated fallback.
- The card surface is already aligned with the future `full_card_enrichment` scenario, but the pipeline itself is not implemented yet.

## Mode binding

The indicator reads the remodel mode exposure from `ai_remodel.effective_mode.entry_exposure.future_card_enrichment_trigger`.

## Next steps

- `2.2`: connect the card trigger to a bounded enrichment placeholder path.
- `2.3`: refine card copy and status behavior once the bounded pipeline exists.
