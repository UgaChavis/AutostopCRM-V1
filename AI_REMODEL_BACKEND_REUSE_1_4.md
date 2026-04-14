# AI Remodel Backend Reuse 1.4

This document is the repo-safe backend foundation map for the AI remodel.
It classifies what stays as-is, what needs adaptation, and what is legacy-only.

## Canonical backend reuse table

| Component | Current role | Reuse category | Future target | Adaptation note | Do-not-break note |
| --- | --- | --- | --- | --- | --- |
| `card_service` | Source of truth for cards, repair orders, attachments, and domain writes | `reuse_as_is` | `ai_chat`, `full_card_enrichment`, `board_control` | Keep business writes in the service core. | Do not move domain logic into the agent layer. |
| `local_api` | Internal read/write boundary for UI and agent runtime | `reuse_as_is` | All three scenarios | Keep the boundary stable while new entry points are added. | Preserve auth and operator checks. |
| `orchestration_contracts` | `read -> evidence -> plan -> tools -> patch -> write -> verify` schemas | `reuse_as_is` | All three scenarios | New modules should consume the same contracts, not ad hoc shapes. | Keep patch/write/verify discipline intact. |
| `policy_engine` | Tool gates and patch filtering | `reuse_as_is` | All three scenarios | Extend policy inputs for new scenarios instead of bypassing them. | Do not allow unbounded writes. |
| `snapshot_service` | Compact board/card/wall snapshot assembly | `reuse_as_is` | All three scenarios | This is the foundation for compact reads and wall digest. | Preserve revision/signature behavior. |
| `agent_storage` | Tasks, schedules, runs, actions, status, prompt, and memory persistence | `reuse_as_is` | All three scenarios | Reuse persistence for new scenario traces and state. | Keep file locking and JSON payload shapes stable. |
| `agent_runtime_api` | Agent status, runs, actions, tasks, enqueue route registration | `reuse_as_is` | All three scenarios | Runtime exposure stays stable while new paths are rolled out. | Preserve operator session semantics. |
| `runner_model_loop` | Claim/read/evidence/plan/tools/patch/write/verify loop | `reuse_with_adaptation` | All three scenarios | Will become scenario dispatch instead of a single mixed loop. | Keep tool accounting and trace flow stable. |
| `runner_autofill_executors` | Legacy autofill scenario executors and follow-up paths | `reuse_with_adaptation` | `full_card_enrichment`, `board_control` | This is the closest execution foundation for future bounded scenarios. | Preserve partial-result and verification semantics. |
| `control_scheduler` | Worker, scheduler, heartbeat, and task claim orchestration | `reuse_with_adaptation` | All three scenarios | Will keep supervision duties, later narrowed for board control. | Preserve heartbeat and claim semantics. |
| `automotive_tools` | VIN, parts, DTC, fault, and maintenance lookup helpers | `reuse_with_adaptation` | `ai_chat`, `full_card_enrichment` | Reuse as bounded domain research helpers under stronger policy. | Keep lookup budgeting and allow-list behavior. |
| `web_tools` | Internet search and fetch helpers | `reuse_with_adaptation` | `ai_chat` | Reuse for chat research under stricter policy gates. | Keep domain whitelist and budget limits. |
| `openai_client` | LLM API client used by the worker runtime | `reuse_as_is` | All three scenarios | Generic model transport should stay stable. | Preserve model selection and retry behavior. |
| `instructions` | System prompt and task prompt assembly | `reuse_with_adaptation` | All three scenarios | Split prompt assembly by scenario in later modules. | Keep fallback templates callable. |
| `source_registry` | Allowed source registry and source metadata | `reuse_as_is` | `ai_chat`, `full_card_enrichment` | Stable allow-list foundation for controlled research. | Preserve source whitelist contract. |
| `manual_prompt_bridge` | Freeform textarea -> worker task translation | `legacy_only_or_retire_later` | `ai_chat` | This is tied to the old modal UX and should shrink later. | Keep legacy compatibility until chat exists. |
| `quick_prompt_bridge` | Canned prompt preprocessing and injection | `legacy_only_or_retire_later` | `ai_chat`, `full_card_enrichment` | This is a convenience shim over the old modal flow. | Keep quick prompt shortcuts stable until replacement exists. |
| `autofill_bridge` | `set_card_ai_autofill` and on-create trigger plumbing | `legacy_only_or_retire_later` | `full_card_enrichment` | This seam is the old card autofill path and will be replaced later. | Preserve backward compatibility until the new path is ready. |
| `scheduler_task_bridge` | Scheduled task CRUD and run/pause/resume bridge | `reuse_with_adaptation` | `board_control` | Scheduling persistence stays useful, but the UX surface will change. | Preserve run/pause/resume semantics until replacement exists. |

## Forbidden backend moves

- Do not rewrite `runner` wholesale.
- Do not move domain logic out of `CardService`.
- Do not weaken `read -> evidence -> plan -> tools -> patch -> write -> verify`.
- Do not mix chat runtime and board daemon logic into one backend branch.
- Do not remove legacy backend pieces before a working replacement exists.

