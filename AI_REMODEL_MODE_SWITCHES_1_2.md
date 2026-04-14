# AI Remodel Mode Switches 1.2

This document defines the safe switching layer for AutoStop CRM AI remodel.

## Existing Flags

- `MINIMAL_KANBAN_AI_LEGACY_UX_ENABLED`
- `MINIMAL_KANBAN_AI_CHAT_ENABLED`
- `MINIMAL_KANBAN_FULL_CARD_ENRICHMENT_ENABLED`
- `MINIMAL_KANBAN_BOARD_CONTROL_ENABLED`

## Rollout States

- `disabled`
- `hidden`
- `available`
- `primary`
- `legacy_only`

## Effective AI Mode

The canonical runtime resolver lives in:

- `src/minimal_kanban/agent/remodel.py`

It resolves:

- whether legacy UX stays enabled
- which scenario is the primary interactive path
- which scenarios are legacy-compatible only
- which scenarios are hidden
- which scenarios are background-only
- which scenarios are currently available

## Safe Defaults

Default state remains conservative:

- legacy UX enabled
- `ai_chat` disabled
- `full_card_enrichment` disabled
- `board_control` disabled

This prevents accidental exposure of the new scenarios before their next modules land.

## Use By Later Modules

- Module `1.3` should use the resolver to gate first replacement entry points.
- Module `1.4` should use the resolver to expose background board mode only when explicitly enabled.
- UI code should read the resolver output instead of inventing its own boolean logic.
