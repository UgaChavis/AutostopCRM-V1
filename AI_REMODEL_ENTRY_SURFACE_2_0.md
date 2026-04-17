# AI Remodel Entry Surface 2.0

Module 2.0 introduces the first visible CRM entry-surface layer for the remodel without implementing the full scenarios yet.

## Current entry decisions

| Surface | Current behavior | Status | Mode gate | Replacement target |
| --- | --- | --- | --- | --- |
| `aiChatButton` | Opens the new AI shell in chat context | primary entry seam | `effective_mode.entry_exposure.future_ai_chat_window` | `ai_chat` |
| `agentDockButton` | Opens the new AI shell in board context | quiet board-level entry seam | `effective_mode.entry_exposure.future_ai_chat_window` | `ai_chat` |
| `cardAgentButton` | Opens the new AI shell in card context | quiet card-level entry seam | `effective_mode.entry_exposure.future_card_enrichment_trigger` | `full_card_enrichment` |
| `aiSurfaceLegacyButton` | Opens the legacy agent modal as fallback | legacy-gated fallback | `legacy_ux_enabled` | legacy compatibility only |

## Surface model

- `ai_chat` is the explicit workspace entry seam.
- `full_card_enrichment` is the card-scoped shell behind the card trigger.
- `board_control` remains hidden in the UI and is only represented as a future scenario tile.

## Status model

- `online` means the path is available or primary.
- `busy` means the path is gated or legacy-only.
- `idle` means the path is hidden or replaced.

## Integration seam

The UI now reads `ai_remodel.effective_mode.entry_exposure` from `agent_status` and uses that as the single source of truth for button state and shell copy.

## Next steps

- `2.1`: wire the new chat entry to a dedicated placeholder console shell.
- `2.2`: replace the card surface placeholder with bounded enrichment plumbing.
- `2.3`: keep `board_control` hidden but expose the settings seam.
- `2.4`: polish visibility and copy after the first replacement path is stable.
