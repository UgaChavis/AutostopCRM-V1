# AI Remodel 3.1 - Chat Window Layout

## Scope

This module refines the `ai_chat` shell into a full-size chat workspace layout.

## Layout Zones

- `header` - title, status, future settings controls
- `messages area` - scrollable long-form conversation surface
- `input area` - dedicated multiline composer block for future send flow

## Non-goals

- no message runtime
- no markdown rendering
- no settings logic
- no knowledge/doc/internet wiring
- no legacy agent modal rewrite

## Current wiring

- chat entry remains tied to the canonical scenario map and mode resolver
- the chat window stays separate from the legacy popup/menu entry surfaces
- the layout is prepared for history, markdown, and context wiring in later modules

