# AI Release Freeze 8.7

This document freezes the late-stabilization state of the AI remodel.

## Stable defaults

- `ai_chat` is the primary manual AI path.
- `full_card_enrichment` is the only card-scoped write scenario.
- `board_control` is hidden and disabled by default.
- Compact context is the primary local context path.
- Knowledge inputs are optional and controlled.

## Frozen entry points

- Card indicator opens `full_card_enrichment` only through the bounded card path.
- The dedicated chat entry opens `ai_chat`.
- Legacy modal and quick-prompt flows remain fallback-only.
- Background `board_control` stays non-primary and hidden behind settings and mode gates.

## Frozen behavior

- No new scenario identities are introduced here.
- No new runtime loops are added.
- No wide UI refactor is allowed in the freeze state.
- No broad legacy cleanup is required beyond already replaced surfaces.

## Verification stance

- Prefer targeted tests over broad regression runs.
- Keep `read -> evidence -> plan -> tools -> patch -> write -> verify` intact.
- Preserve compatibility with the current runtime and existing storage formats.
