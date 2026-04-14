# AI Remodel Scenario Map 1.1

This document fixes the canonical scenario-definition layer for the AutoStop CRM AI remodel.

## Canonical Scenarios

| Scenario | Purpose | Trigger | Actor mode | Context sources | Write policy | Entry surfaces | Legacy replacement | Future owner |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ai_chat` | Future full-size conversational AI console for operators | `user_invoked` | `interactive` | `card_context`, `repair_order_context`, `wall_digest`, `curated_internal_docs`, `internet_lookup` | `read_heavy_restricted_write` | `future_ai_chat_window` | `legacy_agent_modal_manual_tasks` | `Module 1.2 AI Chat Console` |
| `full_card_enrichment` | Future bounded card enrichment flow launched from the card indicator | `user_invoked` | `interactive` | `card_context`, `repair_order_context`, `wall_digest`, `attachments` | `bounded_write` | `card_indicator` | `legacy_card_agent_button_and_card_autofill_menu` | `Module 1.3 Card Enrichment Pipeline` |
| `board_control` | Future background board hygiene mode for delta-driven maintenance | `scheduled` | `background` | `delta_board_context`, `wall_digest`, `card_context`, `repair_order_context` | `bounded_background_write` | `settings_toggle`, `background_scheduler` | `legacy_board_scheduler_and_manual_board_agent_review` | `Module 1.4 Board Control Mode` |

## Explicit Boundaries

- `ai_chat` is conversational and read-heavy. It is not the old agent modal and not a background daemon.
- `full_card_enrichment` is deterministic, card-scoped, and bounded. It is not a freeform assistant and not a board-wide mode.
- `board_control` is scheduled and delta-oriented. It is not user chat, not a free agent, and not an autonomous dispatcher.

## Canonical Code Layer

The scenario registry is defined in:

- `src/minimal_kanban/agent/remodel.py`

The runtime exposure path is:

- `src/minimal_kanban/agent/control.py`

## Legacy And Reuse

Legacy UX is still present and intentionally not removed in module `1.1`.

Reusable backend layers remain:

- `CardService`
- local API
- orchestration contracts
- policy engine
- bounded tools
- wall / snapshot / card / repair-order / attachments context primitives

## Non-Goals For 1.1

- no UI implementation
- no runtime switch-over
- no new chat window
- no full enrichment executor
- no board control scheduler
- no broad refactor of runner or control
