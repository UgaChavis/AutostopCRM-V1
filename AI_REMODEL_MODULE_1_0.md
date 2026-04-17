# AI Remodel Module 1.0 Skeleton

This file fixes the skeleton state for the AI remodel in branch `autostopCRM`.

The goal of module `1.0` is not to ship the new chat, `full_card_enrichment`, or `board_control`.
The goal is to separate:

- legacy AI UX
- reusable AI backend
- future AI scenario layer

## 1. Canonical New Scenario Map

The canonical new AI scenario identities now live in:

- `src/minimal_kanban/agent/remodel.py`

Defined scenario IDs:

- `ai_chat`
- `full_card_enrichment`
- `board_control`

Current stage:

- all three are `planned`
- all three are disabled by default
- legacy UX remains enabled by default

## 2. Feature Flags And Mode Switches

The lightweight runtime switch layer is environment-based and defined in:

- `src/minimal_kanban/agent/remodel.py`

Flags:

- `MINIMAL_KANBAN_AI_LEGACY_UX_ENABLED=1` default `true`
- `MINIMAL_KANBAN_AI_CHAT_ENABLED=1` default `false`
- `MINIMAL_KANBAN_FULL_CARD_ENRICHMENT_ENABLED=1` default `false`
- `MINIMAL_KANBAN_BOARD_CONTROL_ENABLED=1` default `false`

These flags are exposed in `AgentControlService.agent_status()` under:

- `ai_remodel.phase`
- `ai_remodel.feature_flags`
- `ai_remodel.scenarios`

This creates a repo-safe mode map without changing current UX behavior.

## 3. Current AI Entry Point Map

### 3.1 Legacy UX Entry Points

These are current user-facing AI entry points and are treated as legacy UX for the remodel:

- `src/minimal_kanban/web_assets.py`
  - board dock button `agentDockButton` -> `openAgentModal('board')`
  - card button `cardAgentButton` -> `openAgentModal('card')`
  - quick prompts in `quickAgentPrompts(context)`
  - manual run button `agentRunButton` -> `enqueueAgentTask()`
  - tasks modal `agentTasksModal`
  - card autofill controls `agentAutofillButton` / `agentAutofillPrompt*`

### 3.2 Reusable Backend Entry Points

These are current backend entry points that remain valid foundations:

- `src/minimal_kanban/agent/control.py`
  - `agent_enqueue_task()`
  - `agent_status()`
  - `agent_tasks()`
  - `agent_actions()`
  - `agent_runs()`
  - scheduled task methods
  - `enqueue_card_autofill_task()`
  - `handle_card_created()`
- `src/minimal_kanban/services/card_service.py`
  - `set_card_ai_autofill()`
  - `trigger_due_ai_followups()`

### 3.3 Infrastructure-Only Entry Points

These are not product UX entry points, but infrastructure surfaces:

- `main_agent.py`
- `src/minimal_kanban/agent/control.py`
  - worker thread startup
  - scheduler loop
- `src/minimal_kanban/api/server.py`
  - `/api/agent_*`
  - `/api/set_card_ai_autofill`
- MCP status/control routes over the same backend

## 4. Legacy UX Deactivation Map

This is the current deactivation map. Nothing here is removed in module `1.0`.

| Legacy entry point | Current role | Future replacement |
| --- | --- | --- |
| Board dock AI button | Opens old board agent modal | `ai_chat` |
| Card agent button | Opens old card-scoped agent modal | `full_card_enrichment` trigger + later `ai_chat` access |
| Manual prompt textarea in agent modal | Freeform manual task entry | `ai_chat` |
| Quick prompts in agent modal | Legacy task shortcuts | split between `ai_chat` and `full_card_enrichment` |
| Agent tasks modal and schedule editor | Legacy mixed task UX | later narrowed into `board_control` and explicit admin tooling |
| Card autofill toggle and mini-prompt panel | Legacy card AI UX | `full_card_enrichment` and later board/background controls |

Compatibility note:

- old entry points stay live until their scenario-specific replacements exist
- module `1.0` only labels them as legacy and introduces switches for the new model

## 5. Reuse Map

### 5.1 Reuse As-Is

- `src/minimal_kanban/services/card_service.py` business writes and read orchestration boundaries
- local API routes and transport boundary
- `src/minimal_kanban/agent/contracts.py`
- `src/minimal_kanban/agent/policy.py`
- bounded tool layer in `src/minimal_kanban/agent/tools.py`
- wall / snapshot / card / repair-order read primitives
- patch / write / verify behavior

### 5.2 Reuse With Adaptation

- `src/minimal_kanban/agent/runner.py`
- `src/minimal_kanban/agent/control.py`
- `src/minimal_kanban/agent/scenarios`
- status exposure from `agent_status`
- UI trigger glue in `src/minimal_kanban/web_assets.py`
- current card autofill launch path

### 5.3 Legacy-Only Or Candidate For Later Retirement

- old combined agent modal UX in `src/minimal_kanban/web_assets.py`
- quick prompt menu behavior as a primary user flow
- tasks modal as mixed board/card/manual/schedule UX
- labels that still present the old generic `agent` product surface instead of the new three-scenario model

## 6. Module 1.0 Boundary

What module `1.0` deliberately does not do:

- no new chat window
- no real `full_card_enrichment` UI wiring
- no real `board_control` scheduler mode
- no deletion of old agent UX
- no refactor of `CardService` or the orchestration contract

This file is the repo-safe map for future modules `1.1+`.
