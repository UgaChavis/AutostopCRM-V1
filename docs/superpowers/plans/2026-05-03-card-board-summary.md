# Card Board Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a hidden AI-managed card board summary that replaces raw description preview on board cards and is writable through a dedicated MCP/API command.

**Architecture:** Store the summary and freshness metadata on `Card`. UI reads `board_summary` first and falls back to `description_preview`. Business logic lives in `CardService`; API/MCP/Telegram tools relay into that service and do not duplicate filesystem or board logic.

**Tech Stack:** Python dataclasses/services/API, FastMCP tools, inline browser HTML/JS, unittest, ruff.

---

### Task 1: RED Tests

**Files:**
- Modify: `tests/test_service.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_mcp.py`
- Modify: `tests/test_web_assets.py`

Steps:
- Add service tests proving `set_card_board_summary` stores a five-line hidden summary, exposes freshness metadata, marks it stale after a normal card edit, and writes audit log entries with before/after.
- Add API and MCP tests proving the command is available as a write action and returns the same service payload.
- Add web asset tests proving board cards prefer `board_summary` over `description_preview`.
- Run the focused tests and verify they fail because the new command/fields do not exist yet.

### Task 2: Domain And API

**Files:**
- Modify: `src/minimal_kanban/models.py`
- Modify: `src/minimal_kanban/services/card_service.py`
- Modify: `src/minimal_kanban/services/snapshot_service.py`
- Modify: `src/minimal_kanban/api/server.py`

Steps:
- Add hidden card fields: `board_summary`, `board_summary_updated_at`, `board_summary_source`, `board_summary_card_fingerprint`.
- Add computed `board_summary_stale` and include fields in full and compact serialization.
- Add `CardService.set_card_board_summary(payload)` with validation: max 5 non-empty lines, max 560 chars, no accidental normal description/title mutation.
- Add audit event `board_summary_changed` and card journal before/after rendering.
- Add `POST /api/set_card_board_summary`.

### Task 3: MCP, Agent Tools, UI

**Files:**
- Modify: `src/minimal_kanban/mcp/client.py`
- Modify: `src/minimal_kanban/mcp/server.py`
- Modify: `src/minimal_kanban/telegram_ai/crm_tools.py`
- Modify: `src/minimal_kanban/connection_card.py`
- Modify: `src/minimal_kanban/web_assets.py`

Steps:
- Add MCP tool `set_card_board_summary(card_id, summary, actor_name)` with write annotations.
- Add BoardApiClient relay and Telegram AI CRM tool exposure.
- Change board card preview selection to `board_summary || description_preview || description`.
- Keep user card modal free of a new visible field.

### Task 4: Docs, Checks, Release

**Files:**
- Modify: `README.md`
- Modify: `API_GUIDE.md`
- Modify: `MCP_GUIDE.md`
- Modify: `PROJECT_HANDOFF.md`
- Modify: `docs/TELEGRAM_AI_BOARD_MANAGER.md`

Steps:
- Document the hidden AI board summary, API endpoint, MCP tool, journal behavior, and stale flag.
- Run focused tests, `scripts/run_checks.ps1`, service/API/web-assets tests, MCP smoke, and `doctor.ps1`.
- Commit, push `autostopcrm-v1`, deploy `/opt/autostopcrm/deploy.sh`, and run public/server live connector checks.
