# AutoStop CRM Agent Orchestration

Server AI now runs through one shared orchestration contract instead of keeping card autofill completely separate from the rest of the worker logic.

## Shared Contract

Every run is modeled as:

`read -> evidence -> plan -> tools -> patch -> write -> verify`

The same run trace is persisted into agent `runs` with:

- trigger metadata
- context snapshot id
- `EvidenceResult`
- `PlanResult`
- normalized `ToolResult` entries
- `PatchResult`
- `VerifyResult`

## Execution Modes

The shared orchestration core supports two executors:

1. `structured_card`
   Used for server card autofill.
   This stays deterministic and bounded.

2. `model_loop`
   Used for manual and scheduled tasks.
   These tasks still use the model decision loop, but now under the same evidence / plan / policy / verify contract.

## Card Autofill Pipeline

Card autofill remains a bounded deterministic scenario executor:

1. `context read`
   Reads `get_card_context(card_id)` and may add a short related-card slice from `search_cards()` when VIN or stable vehicle context justifies it.
2. `evidence extraction`
   Extracts VIN, mileage, DTC, likely parts, symptom signal, maintenance signal, waiting state, known vehicle facts, and missing vehicle fields.
3. `planning`
   Selects one or more scenarios:
   - VIN enrichment
   - part lookup
   - maintenance lookup
   - DTC decode
   - fault research
   - normalization
4. `tool execution`
   Calls only the bounded external tools needed for the selected scenarios.
5. `patch building`
   Builds additive card updates and bounded vehicle-profile patches.
6. `write + verify`
   Writes through the runner contract layer and re-reads the CRM state before success is recorded.

## Required-Tool Policy

These rules are enforced in code, not just in prompts:

- VIN scenario requires `decode_vin`
- parts scenario requires `find_part_numbers`
- maintenance scenario requires `estimate_maintenance`
- DTC scenario requires `decode_dtc`
- fault scenario requires `search_fault_info`

For model-loop tasks, the runner blocks premature final answers until required tools are executed.

## External Tools

- `decode_vin(vin)`
- `find_part_numbers(query, vehicle)`
- `estimate_price_ru(part_number, vehicle)`
- `decode_dtc(code, vehicle_context)`
- `search_fault_info(query, vehicle_context)`

## Tool Limits

- External request budget is reset per task.
- Budget is capped to a small number of calls per run.
- Only whitelisted domains and trusted sources are used.
- Raw tool outputs are normalized into compact structured trace entries before patch building.

## Writer Rules

- Never delete manual prices, article numbers, phone numbers, VIN, or operator notes.
- Do not duplicate existing card text.
- Add only net-new short lines in autofill mode.
- Prefix AI additions with `ИИ:` or `AI:`.
- Use patch-bound writes instead of unrestricted rewrites.
- Re-read CRM state after writing and mark verification warnings when the result diverges from the planned patch.

## Follow-Up Rules

- First autofill pass runs immediately after enabling autofill.
- Follow-up ownership remains with the server autofill flow and `CardService`.
- Duplicate active processing for the same card is blocked.
- Unchanged cards slow down; changed cards speed up.
- Waiting-state cards stay on slower revisit cadence.
- The orchestration trace also records whether follow-up is still needed.

## Operator Prompt Rules

- Mini-prompt influences scenario selection.
- It should stay short and operational.
- Prompt is guidance, not permission to overwrite manual facts.
