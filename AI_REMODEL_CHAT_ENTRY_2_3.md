# AI Remodel 2.3: AI Chat Entry Seam

## Status

- Canonical future entry for `ai_chat`
- Separate from card trigger
- Separate from legacy popup/menu
- Backed by `scenario_registry` and `mode resolver`

## Current behavior

- Topbar `AI ЧАТ` opens the new AI entry shell in chat context.
- The shell is a placeholder/gated entry, not the final chat runtime.
- Legacy modal remains only as gated fallback and is not the primary path.

## Entry contract

- `ai_chat` is user-facing and interactive.
- It is read-heavy by default.
- It is not the legacy manual modal flow.
- It is not quick-prompts UX.

## Next module dependency

- Module 2.4 should replace the placeholder shell with the real AI chat runtime without changing the entry identity.
