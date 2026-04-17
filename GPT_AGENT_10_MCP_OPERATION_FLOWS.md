# AutoStop CRM MCP Operation Flows

## 1. Bootstrap And Safe Write Flow
1. Call `bootstrap_context()`.
2. Read `get_connector_identity()` only if identity needs to be restated separately.
3. Use `get_runtime_status()` only for diagnostics.
4. Before writes, load the exact target with `get_card()`, `get_card_context()`, `get_repair_order()`, or `get_cashbox()`.
5. Write strictly by ids returned from the current board.

## 2. Card Review And Update Flow
1. `search_cards()` or `get_cards()` to locate candidates.
2. `get_card_context(card_id)` to read the card with recent events and repair-order text.
3. If only the card body changes, use `update_card()`.
4. If only priority timing changes, use `set_card_deadline()` or `set_card_indicator()`.
5. If the card moves, use `move_card()` or `bulk_move_cards()`.
6. Archive only after checking repair-order state. Open repair orders block archive.

## 3. Repair-Order Flow
1. `get_card_context(card_id)` for full context.
2. `get_repair_order(card_id)` to open or lazily create the repair order.
3. `update_repair_order()` for header and client fields.
4. `replace_repair_order_works()` and `replace_repair_order_materials()` for row tables.
5. `set_repair_order_status(card_id, "closed")` only after payment is complete.
6. Use `get_repair_order_text()` for printable text view.

## 4. Cashbox Flow
1. `list_cashboxes()` or `get_cashbox()` to select the target.
2. `create_cash_transaction()` for income or expense.
3. Re-read with `get_cashbox()` when follow-up totals matter.

## 5. Manual Agent Task Flow
1. `agent_status()` to confirm availability.
2. `agent_enqueue_task(task_text, card_id?, card_title?, requested_by?)`.
3. Track with `agent_tasks()`.
4. Read detailed execution in `agent_actions(run_id?, task_id?)`.
5. Inspect summaries in `agent_runs()`.

## 6. Card Autofill Flow
1. `get_card_context(card_id)` first.
2. `set_card_ai_autofill(card_id, enabled=true, prompt?)`.
3. Confirm queue growth with `agent_tasks()`.
4. Watch execution with `agent_actions(task_id=...)`.
5. Re-read the card with `get_card_context(card_id)` after runs.
6. Re-check `agent_status()` if the UI suggests the worker is stale.
7. Disable with `set_card_ai_autofill(card_id, enabled=false)` when the follow-up window is no longer needed.

## 6A. Card Autofill Orchestration Flow
1. Load `get_card_context(card_id)` and inspect:
   - card body
   - vehicle
   - vehicle_profile
   - recent events
   - repair-order text when relevant
2. Derive the working facts:
   - VIN
   - make, model, and year
   - mileage
   - complaint
   - part requests
   - DTC
   - waiting-state hints
   - existing AI notes
3. If VIN or stable vehicle context exists, call `search_cards()` for a short board context slice.
4. Select scenarios:
   - VIN enrichment
   - part lookup
   - maintenance lookup
   - DTC lookup
   - fault research
   - normalization
5. Route only the needed external tools.
6. Normalize the raw tool outputs into short operational additions.
7. Patch the card additively with `update_card()`:
   - preserve manual notes
   - preserve numbers, prices, article numbers, and phone numbers
   - add only net-new `AI:` or `ИИ:` notes
8. Schedule the next follow-up only if the card changed meaningfully or the 4-hour window is still active.

## 7. Scheduled Task Flow
1. `save_agent_scheduled_task(...)` with explicit scope and schedule.
2. Verify with `agent_scheduled_tasks()`.
3. Use `run_agent_scheduled_task(task_id)` for immediate dry-run behavior.
4. Pause with `pause_agent_scheduled_task(task_id)` if the queue should stop receiving new runs.
5. Resume with `resume_agent_scheduled_task(task_id)`.
6. Delete with `delete_agent_scheduled_task(task_id)` when obsolete.

## 8. On-Create Enrichment Flow
1. Create a scheduled task with:
   - `schedule_type="on_create"`
   - `scope_type="column"` or `scope_type="all_cards"`
2. New cards in matching scope automatically queue one task.
3. Prevented duplicate: one active schedule will not enqueue multiple active tasks for the same new card.

## 9. Recommended Autofill Use Cases
- VIN present and profile fields missing:
  Use `set_card_ai_autofill()` or a manual agent task bound to the card.
- Clear part request:
  Prefer autofill follow-up; the pipeline will try OEM numbers and Russian price orientation.
- Maintenance request with mileage:
  Autofill produces a compact service plan with works and consumables.
- DTC present:
  Autofill adds a short decode and first-check hints.
- Symptom-only complaint:
  Autofill adds short diagnostic context and next-check notes.

## 10. Things To Avoid
- Do not write free-form summaries without re-reading the card context first.
- Do not overwrite manual prices, article numbers, or notes with speculative AI text.
- Do not bypass `set_card_ai_autofill()` by spamming repeated `agent_enqueue_task()` calls for the same card.
- Do not use this connector for any board other than the current AutoStop CRM board.
