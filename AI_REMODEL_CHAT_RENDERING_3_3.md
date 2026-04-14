# AI Remodel 3.3 - Chat Rendering

## Scope

This step adds a minimal message rendering layer for the `ai_chat` window.

## Supported message forms

- `user`
- `assistant`
- `system` / `status`

## Rendering rules

- assistant messages use a small markdown-safe renderer
- user messages remain plain and copyable
- message history stays local to the chat window
- long responses scroll inside the messages area

## Non-goals

- no full AI runtime
- no knowledge/doc/internet integration
- no settings logic
- no full-card enrichment
- no board control

## Follow-up

- add richer markdown and context-aware rendering later
- wire settings and context sources without changing the window layout

