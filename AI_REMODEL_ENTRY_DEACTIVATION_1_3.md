# AI Remodel Entry Deactivation 1.3

This document is the repo-safe technical map for legacy AI UX deactivation.
It is not a UI spec and does not change runtime behavior by itself.

## Canonical entry exposure table

| Entry surface | Current behavior | Current status | Rollout status | Replacement target | Next module owner |
| --- | --- | --- | --- | --- | --- |
| `board_dock_button` | Opens the mixed board agent modal | Legacy UX entry | `legacy_only` | `ai_chat` | Module 1.2 AI Chat Console |
| `card_agent_button` | Opens the card-scoped legacy agent modal | Legacy UX entry | `legacy_only` | `full_card_enrichment` | Module 1.3 Card Enrichment Pipeline |
| `agent_manual_prompt` | Submits freeform manual task text | Legacy UX entry | `legacy_only` | `ai_chat` | Module 1.2 AI Chat Console |
| `quick_prompts` | Prefills canned prompts into the legacy textarea | Legacy shortcut surface | `legacy_only` | `ai_chat / full_card_enrichment` | Module 1.2 + Module 1.3 |
| `agent_tasks_modal` | Shows manual tasks and schedule controls | Legacy scheduler shell | `legacy_only` | `board_control` | Module 1.4 Board Control Mode |
| `card_autofill_toggle` | Toggles card autofill and opens mini-prompt panel | Legacy card enrichment trigger | `legacy_only` | `full_card_enrichment` | Module 1.3 Card Enrichment Pipeline |
| `agent_status_surface` | Shows readiness / autofill state | Infrastructure surface | `active` | `none` | Module 1.x shared runtime |
| `agent_enqueue_task_api` | Accepts manual tasks for the worker queue | Backend foundation | `active` | `ai_chat` | Module 1.2 AI Chat Console |
| `agent_scheduled_tasks_api` | Stores and executes scheduled tasks | Backend scheduler seam | `legacy_only` or `replaced` via resolver | `board_control` | Module 1.4 Board Control Mode |
| `set_card_ai_autofill_api` | Enables/disables card autofill and mini prompt | Backend card trigger seam | `legacy_only` or `replaced` via resolver | `full_card_enrichment` | Module 1.3 Card Enrichment Pipeline |
| `card_created_auto_trigger` | Launches on-create card autofill schedules | Backend card-created trigger | `legacy_only` or `replaced` via resolver | `full_card_enrichment` | Module 1.3 Card Enrichment Pipeline |

## Notes

- `scenario registry` stays separate from `mode resolver`.
- `mode resolver` stays separate from `entry exposure`.
- Legacy UI remains visible by default until a later module replaces it.
- Future surfaces stay hidden until their replacement scenario is rolled out.

