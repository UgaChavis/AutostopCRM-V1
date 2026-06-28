from __future__ import annotations

from dataclasses import dataclass

from .base import ScenarioContext, ScenarioExecutionResult


def _prepare_parts_lookup_query(
    context: ScenarioContext,
    runtime: object,
    facts: dict[str, object],
    *,
    scenario_id: str,
) -> tuple[str, ScenarioExecutionResult | None]:
    scenario = context.scenario_payload
    part_query = str(scenario.get("query", "") or "").strip() or (
        facts["part_queries"][0] if facts["part_queries"] else ""
    )
    if not part_query:
        return (
            "",
            ScenarioExecutionResult(
                scenario_id=scenario_id,
                status="skipped",
                notes=["parts lookup skipped: no part query"],
            ),
        )
    if not runtime._card_autofill_can_run_parts_lookup(facts):
        runtime._record_log_action(
            task_id=context.task_id,
            run_id=context.run_id,
            step=0,
            level="INFO",
            phase="tool",
            message="parts lookup skipped: no trusted vehicle context after VIN gate.",
        )
        return (
            "",
            ScenarioExecutionResult(
                scenario_id=scenario_id,
                status="skipped",
                notes=["parts lookup skipped after VIN gate"],
                warnings=["parts lookup is waiting for trusted vehicle context"],
                needs_followup=True,
                followup_reason="parts_lookup_waiting_vehicle_context",
            ),
        )
    return part_query, None


def _parts_lookup_partial_result(
    *,
    scenario_id: str,
    part_payload: dict[str, object],
    orchestration_updates: dict[str, object],
    runtime: object,
) -> ScenarioExecutionResult:
    is_budget_exceeded = runtime._is_budget_exceeded_payload(part_payload)
    return ScenarioExecutionResult(
        scenario_id=scenario_id,
        status="partial",
        tool_calls_used=1,
        tool_results=[
            runtime._build_tool_result(
                "find_part_numbers",
                part_payload,
                status="partial",
                reason="Find OEM and analog part numbers for the main detected part request",
                scenario_id=scenario_id,
                evidence_ref="part_queries",
            )
        ],
        orchestration_updates=orchestration_updates,
        warnings=["parts lookup deferred: external budget exceeded"]
        if is_budget_exceeded
        else ["parts lookup returned partial result"],
        needs_followup=True,
        followup_reason="parts_lookup_budget_deferred"
        if is_budget_exceeded
        else "parts_lookup_partial",
    )


def _apply_parts_lookup_price_lookup(
    *,
    context: ScenarioContext,
    facts: dict[str, object],
    orchestration_updates: dict[str, object],
    runtime: object,
    scenario: dict[str, object],
    scenario_id: str,
    tool_calls_used: int,
    tool_results: list[dict[str, object]],
) -> tuple[int, list[dict[str, object]]]:
    if not bool(scenario.get("with_price")):
        return tool_calls_used, tool_results
    best_part_number = runtime._pick_best_part_number(orchestration_updates["find_part_numbers"])
    if not best_part_number:
        return tool_calls_used, tool_results
    price_payload = runtime._run_autofill_tool(
        task_id=context.task_id,
        run_id=context.run_id,
        step=tool_calls_used + 1,
        tool_name="estimate_price_ru",
        args={
            "part_number": best_part_number,
            "vehicle": facts["vehicle_context"],
            "limit": 5,
        },
        reason="Estimate Russian-market price for the strongest matched part number",
    )
    if price_payload is None:
        return tool_calls_used, tool_results
    tool_calls_used += 1
    orchestration_updates["estimate_price_ru"] = (
        runtime._response_data(price_payload) or price_payload
    )
    tool_results.append(
        runtime._build_tool_result(
            "estimate_price_ru",
            price_payload,
            status="success",
            reason="Estimate Russian-market price for the strongest matched part number",
            scenario_id=scenario_id,
            evidence_ref="part_queries",
        )
    )
    return tool_calls_used, tool_results


def _parts_lookup_followup_state(
    *, has_useful_parts: bool, orchestration_updates: dict[str, object], runtime: object
) -> tuple[list[str], bool, str]:
    warnings: list[str] = []
    needs_followup = False
    followup_reason = ""
    if not has_useful_parts:
        warnings.append("parts lookup returned no reliable candidate parts")
        needs_followup = True
        followup_reason = "parts_lookup_insufficient"
    price_payload = orchestration_updates.get("estimate_price_ru")
    if isinstance(price_payload, dict) and runtime._is_budget_exceeded_payload(price_payload):
        warnings.append("price lookup deferred: external budget exceeded")
        needs_followup = True
        if not followup_reason:
            followup_reason = "price_lookup_budget_deferred"
    return warnings, needs_followup, followup_reason


@dataclass(frozen=True)
class PartsLookupScenarioExecutor:
    scenario_id: str = "parts_lookup"

    def execute(self, context: ScenarioContext) -> ScenarioExecutionResult:
        runtime = context.runtime
        facts = context.facts
        scenario = context.scenario_payload
        if runtime is None:
            raise ValueError("PartsLookupScenarioExecutor requires runtime.")
        part_query, early_result = _prepare_parts_lookup_query(
            context, runtime, facts, scenario_id=self.scenario_id
        )
        if early_result is not None:
            return early_result
        runtime._record_log_action(
            task_id=context.task_id,
            run_id=context.run_id,
            step=0,
            level="RUN",
            phase="tool",
            message="parts lookup started.",
        )
        part_payload = runtime._run_autofill_tool(
            task_id=context.task_id,
            run_id=context.run_id,
            step=1,
            tool_name="find_part_numbers",
            args={
                "query": part_query,
                "vehicle": facts["vehicle_context"],
                "limit": 5,
            },
            reason="Find OEM and analog part numbers for the main detected part request",
        )
        if part_payload is None:
            return ScenarioExecutionResult(
                scenario_id=self.scenario_id,
                status="failed",
                warnings=["parts lookup request failed"],
                needs_followup=True,
                followup_reason="parts_lookup_failed",
            )
        orchestration_updates = {
            "find_part_numbers": runtime._response_data(part_payload) or part_payload
        }
        if runtime._is_partial_tool_payload(part_payload):
            return _parts_lookup_partial_result(
                scenario_id=self.scenario_id,
                part_payload=part_payload,
                orchestration_updates=orchestration_updates,
                runtime=runtime,
            )
        tool_results = [
            runtime._build_tool_result(
                "find_part_numbers",
                part_payload,
                status="success",
                reason="Find OEM and analog part numbers for the main detected part request",
                scenario_id=self.scenario_id,
                evidence_ref="part_queries",
            )
        ]
        tool_calls_used = 1
        has_useful_parts = runtime._part_lookup_has_useful_result(
            orchestration_updates["find_part_numbers"]
        )
        if isinstance(facts.get("evidence_model"), dict) and has_useful_parts:
            facts["evidence_model"]["external_result_sufficient"] = True
        tool_calls_used, tool_results = _apply_parts_lookup_price_lookup(
            context=context,
            facts=facts,
            orchestration_updates=orchestration_updates,
            runtime=runtime,
            scenario=scenario,
            scenario_id=self.scenario_id,
            tool_calls_used=tool_calls_used,
            tool_results=tool_results,
        )
        warnings, needs_followup, followup_reason = _parts_lookup_followup_state(
            has_useful_parts=has_useful_parts,
            orchestration_updates=orchestration_updates,
            runtime=runtime,
        )
        return ScenarioExecutionResult(
            scenario_id=self.scenario_id,
            status="success",
            tool_calls_used=tool_calls_used,
            tool_results=tool_results,
            orchestration_updates=orchestration_updates,
            warnings=warnings,
            needs_followup=needs_followup,
            followup_reason=followup_reason,
        )
