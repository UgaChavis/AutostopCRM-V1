# AutoStop CRM AI Agent Audit

Date: `2026-04-14`
Branch: `autostopCRM`

This document is a focused audit of the current server AI agent.

It answers four questions:

1. How the agent works today.
2. What is already structurally strong.
3. Why the current system still feels "not truly multilayered".
4. What changes would make it materially smarter and more reliable.

## 1. Executive Summary

The current server AI is no longer a raw free-form worker. It already has a serious contract layer:

- `read -> evidence -> plan -> tools -> patch -> write -> verify`
- formal run trace objects in `contracts.py`
- code-level required-tool policy in `policy.py`
- deterministic structured autofill executor in `runner.py`
- bounded external automotive and web tools in `tools.py`

That is the good part.

The main limitation is different:

- the system is only partially multilayered in reasoning;
- it is fully multilayered in structure, but still shallow in cognition;
- the strongest "intelligence" today is mostly heuristic scenario routing plus bounded tool execution;
- the model loop and structured autofill still share contracts, but not a genuinely shared reasoning engine.

So the system is not "broken in architecture". It is underdeveloped in inference depth.

## 2. Current Architecture

Main files:

- `src/minimal_kanban/agent/control.py`
- `src/minimal_kanban/agent/runner.py`
- `src/minimal_kanban/agent/contracts.py`
- `src/minimal_kanban/agent/policy.py`
- `src/minimal_kanban/agent/tools.py`
- `src/minimal_kanban/agent/openai_client.py`

Current runtime shape:

```text
trigger
  ->
AgentControlService
  ->
AgentStorage queue / schedules / status
  ->
AgentRunner
  ->
context read
  ->
EvidenceResult
  ->
PlanResult
  ->
tool execution
  ->
PatchResult
  ->
CRM write
  ->
VerifyResult
  ->
follow-up decision
```

The system has two execution modes:

1. `structured_card`
   Card autofill path.
   Deterministic and bounded.

2. `model_loop`
   Manual and scheduled tasks.
   Model-driven next-step loop, but still wrapped by the same contract shell.

## 3. What Is Strong Already

### 3.1. Contracts Exist

`contracts.py` is a real improvement over the previous loose state.

The following structures are explicit:

- `EvidenceResult`
- `PlanResult`
- `ToolResult`
- `PatchResult`
- `VerifyResult`
- `OrchestrationTrace`

This matters because the agent can now be inspected as a staged run instead of only as prompt output.

### 3.2. Policy Is In Code

`policy.py` correctly moved critical constraints out of prompts.

Examples:

- VIN enrichment requires `decode_vin`
- parts lookup requires `find_part_numbers`
- DTC scenario requires `decode_dtc`
- fault research requires `search_fault_info`

This is exactly the right direction for production AI.

### 3.3. Autofill Is Bounded

The autofill branch in `runner.py` is not open-ended agentic chaos.

It:

- reads card context;
- extracts facts;
- builds a limited scenario plan;
- runs only required tools;
- writes additively;
- verifies after write.

This is safer than a general "let the model do everything" design.

### 3.4. External Tools Are Gated

`tools.py` keeps automotive lookups and web access small and explicit.

This is operationally sound:

- low external request budget;
- named tool set;
- no unrestricted browsing loop;
- shared transport through `BoardApiClient`.

## 4. What Is Not Truly Multilayered Yet

The system looks multilayered from the outside, but several layers are still thinner than they should be.

### 4.1. Evidence Extraction Is Mostly Heuristic

In `runner.py`, the autofill evidence model is still built mainly from:

- regexes;
- keyword checks;
- hand-coded symptom hints;
- hand-coded maintenance hints;
- hand-coded part hints;
- hand-coded waiting-state rules.

This means the agent does not deeply understand context.
It mostly classifies context by brittle signal detection.

Practical consequence:

- it works for expected text shapes;
- it degrades fast on messy operator input;
- it is weak on semantically implicit problems;
- it struggles when customer context, vehicle context, and technical symptoms are mixed together.

### 4.2. Planner Is Rule-Driven, Not Evidence-Reasoning

`_build_card_autofill_plan()` in `runner.py` is still a budgeted scenario selector driven by local booleans:

- `vin_found`
- `explicit_part_found`
- `maintenance_context_found`
- `dtc_found`
- `fault_symptoms_found`

That is not wrong, but it is not a real planner.

It is a deterministic scenario picker.

Practical consequence:

- the agent does not reason over alternative plans;
- it does not weigh ambiguity;
- it does not track uncertainty beyond a few booleans;
- it cannot explicitly say "VIN insufficient, but card vehicle context plus related cards make parts lookup still worthwhile under low confidence".

### 4.3. Shared Contract Does Not Yet Mean Shared Cognition

The system does have one contract shell.

But internally:

- `structured_card` uses hard-coded orchestration logic;
- `model_loop` uses model-step generation under prompt constraints.

So they are not truly one reasoning engine.

They are two executors living under one run envelope.

Practical consequence:

- architecture is unified;
- decision quality is still split.

### 4.4. Verify Layer Is Write-Centric

The verify layer is good for confirming whether a patch landed.

But it is still weak at evaluating whether the scenario objective was achieved.

Today verification is mostly about:

- did expected fields change;
- were manual fields preserved;
- were required tools executed;
- is follow-up needed.

It is still weak at:

- semantic correctness of the final state;
- confidence downgrade after weak external data;
- explicit "goal achieved vs partially achieved vs blocked by source quality".

### 4.5. Single-Source VIN Dependence Is a Hard Ceiling

The current VIN flow depends too heavily on one decoder result.

Recent production diagnosis showed exactly that:

- orchestration was functioning;
- but `decode_vin` returned insufficient data for a real VIN;
- therefore the system behaved conservatively and stopped short.

That is not only a tool-source problem.
It reveals that the agent has no strong secondary evidence strategy for VIN enrichment failure.

### 4.6. Runner Has Started To Grow Past Safe Size

`runner.py` has become too dense.

Evidence of this:

- large mixed responsibilities;
- scenario extraction, planning, tool dispatch, patch synthesis, verification, and text cleanup live together;
- duplicate method definition exists for `_extract_autofill_symptom_query()`.

This is not just a cleanliness issue.
It directly increases the chance of hidden behavior drift.

## 5. Root Architectural Diagnosis

The current agent is best described as:

`contract-governed, tool-bounded, heuristically planned, partially model-assisted`

It is not yet:

`evidence-reasoning, uncertainty-aware, scenario-composable`

That distinction matters.

The next improvement wave should not chase "more prompts".
It should make the internal decision model more explicit and layered.

## 6. Proposed Improvement Directions

## 6.1. Introduce A Real Evidence Layer

Current evidence should be upgraded from a flat boolean model into a richer state object.

Recommended additions:

- `fact_status`: `confirmed | inferred | weak_signal | absent`
- `fact_source`: `card | repair_order | related_cards | external_tool | model_inference`
- `fact_confidence`: numeric confidence per important fact
- `fact_conflicts`: list of conflicts such as `vin says one thing, card text says another`
- `blocking_unknowns`: explicit missing facts that prevent a high-confidence scenario

Example:

- VIN present: confirmed
- model/year from decoder: absent
- make/model from card title: weak_signal
- related cards same VIN: inferred support
- part request: inferred

This would let the planner work with graded evidence instead of booleans.

## 6.2. Split Planner Into Two Layers

Planner should become:

1. `scenario eligibility`
   Code-level, deterministic.
   Fast safety gate.

2. `scenario strategy`
   Evidence-aware and optionally model-assisted.
   Chooses ordering, confidence mode, and fallback behavior.

Right now those two are collapsed into one rule block.

Recommended outputs:

- primary scenario
- secondary scenario
- fallback scenario
- confidence mode: `high | medium | low`
- write mode: `confirmed_only | additive_draft | review_needed`
- retry strategy

This would make the planner more intelligent without making it unsafe.

## 6.3. Add Scenario Executors As Separate Modules

`runner.py` should stop owning each scenario directly.

Recommended decomposition:

- `agent/scenarios/vin_enrichment.py`
- `agent/scenarios/parts_lookup.py`
- `agent/scenarios/dtc_lookup.py`
- `agent/scenarios/fault_research.py`
- `agent/scenarios/maintenance_lookup.py`
- `agent/scenarios/normalization.py`
- `agent/scenarios/repair_order_assistance.py`

Each scenario module should expose a common interface:

- `prepare()`
- `required_tools()`
- `run_tools()`
- `build_patch()`
- `verify_goal()`

This would make the system genuinely multilayered in code, not only conceptually.

## 6.4. Introduce Uncertainty-Aware Outcome States

Scenario completion should not be only binary.

Recommended result states:

- `completed_confirmed`
- `completed_partial`
- `blocked_missing_source_data`
- `blocked_policy`
- `needs_human_review`
- `needs_followup`

This would fix the current UX mismatch where users feel that the agent "finished" when it really "ended conservatively with incomplete evidence".

## 6.5. Add Secondary Evidence Strategy For VIN Failure

This is a high-value practical improvement.

If `decode_vin` is insufficient:

- derive weak vehicle hints from card title, vehicle field, repair order, and related cards;
- explicitly mark them as non-confirmed;
- allow a lower-confidence continuation path for non-critical scenarios;
- keep confirmed-only policy for sensitive fields.

This would let the agent still be useful when VIN decoding is weak, without fabricating hard facts.

Recommended policy:

- vehicle identity fields: confirmed-only
- symptom analysis: may continue on weak vehicle context
- parts lookup: may continue only in `draft/review_needed` mode
- prices: never confirmed from weak vehicle context

## 6.6. Add Related-Card Evidence As First-Class Input

Related cards are currently only a contextual add-on.

They should become a formal evidence source with weighted trust.

Use cases:

- repeated VIN across cards;
- repeated complaint patterns;
- repeated confirmed repair history;
- repeated vehicle facts already confirmed earlier.

This can materially improve real workshop workflows.

Recommended rule:

- related-card facts never override current card facts;
- but they may strengthen weak evidence and suggest a scenario.

## 6.7. Add A Goal Verifier Separate From Write Verifier

Keep current write verification, but add scenario-goal verification.

Examples:

- VIN scenario goal verifier:
  - was vehicle profile enriched with new confirmed facts;
  - if not, was the run correctly downgraded to partial/block state.

- fault scenario goal verifier:
  - did the card get a concise diagnostic note tied to symptoms and sources;
  - was cause phrased as probable rather than confirmed.

This is the missing half of your current verify layer.

## 6.8. Make Follow-Up Smarter

Follow-up today is adaptive but still mostly mechanical.

It should become evidence-driven.

Recommended follow-up reasons:

- waiting for better VIN source
- waiting for operator-entered mileage
- waiting for customer decision
- waiting for parts availability
- waiting for diagnostic code confirmation

This would let the system schedule for a reason, not just because a scenario did not fully complete.

## 6.9. Add Tool Outcome Normalization By Reliability Class

Current `ToolResult` already has `status`, `source_type`, and `confidence`.

Extend it with:

- `reliability_class`: `authoritative | trusted_external | heuristic_external | derived_internal`
- `coverage_status`: `full | partial | sparse | failed`
- `safe_write_scope`: fields allowed from this tool result

This would make downstream patch logic safer and more explainable.

## 6.10. Reduce Prompt Burden

The model should stop carrying responsibilities that belong in code.

Recommended rule:

- prompts decide interpretation and synthesis only;
- code decides gates, required tools, write scope, and completion state.

This is especially important for long-term maintainability.

## 7. Priority Roadmap

Recommended implementation order:

### Priority 1. Scenario modularization

Why:

- biggest maintainability gain;
- lowest conceptual risk;
- directly reduces `runner.py` fragility.

### Priority 2. Rich evidence model

Why:

- unlocks smarter planning;
- makes reasoning inspectable;
- improves related-card and weak-VIN behavior.

### Priority 3. Goal verifier

Why:

- fixes current "finished vs actually useful" mismatch;
- improves follow-up decisions.

### Priority 4. VIN fallback strategy

Why:

- highest user-visible improvement for real workshop cards;
- directly addresses current production pain.

### Priority 5. Strategy planner

Why:

- turns rule routing into actual evidence-aware planning;
- makes the agent feel meaningfully smarter.

## 8. Concrete Near-Term Improvements

These are the fastest high-value changes without rewriting the whole agent.

1. Move each scenario out of `runner.py` into dedicated modules.
2. Replace boolean evidence flags with graded evidence entries.
3. Add explicit outcome states for `partial`, `blocked`, and `review_needed`.
4. Promote related-card evidence to a formal trace field.
5. Add a second VIN enrichment path or weak-context fallback mode.
6. Add scenario-specific goal verification.
7. Remove duplicate helper definitions and reduce `runner.py` surface area.

## 9. Final Assessment

The current agent is already safer and more structured than a naive "GPT worker".

That is a real achievement.

But the system is still stronger as an execution shell than as a reasoning engine.

The next stage should not be framed as "make the prompt better".
It should be framed as:

- richer evidence,
- better scenario strategy,
- clearer outcome states,
- scenario modules,
- stronger fallback logic when external sources are incomplete.

That is the path from a bounded production assistant to a genuinely multilayered CRM agent.
