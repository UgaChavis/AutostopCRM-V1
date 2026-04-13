from __future__ import annotations

from dataclasses import dataclass

from .base import ScenarioContext, ScenarioExecutionResult


@dataclass(frozen=True)
class MaintenanceLookupScenarioExecutor:
    scenario_id: str = "maintenance_lookup"

    def execute(self, context: ScenarioContext) -> ScenarioExecutionResult:
        runtime = context.runtime
        facts = context.facts
        if runtime is None:
            raise ValueError("MaintenanceLookupScenarioExecutor requires runtime.")
        runtime._record_log_action(
            task_id=context.task_id,
            run_id=context.run_id,
            step=0,
            level="RUN",
            phase="tool",
            message="maintenance lookup started.",
        )
        payload = runtime._run_autofill_tool(
            task_id=context.task_id,
            run_id=context.run_id,
            step=1,
            tool_name="estimate_maintenance",
            args={
                "service_type": facts["maintenance_query"],
                "vehicle_context": facts["vehicle_context"],
            },
            reason="Build a compact maintenance plan for the current mileage and vehicle context",
        )
        if payload is None:
            return ScenarioExecutionResult(scenario_id=self.scenario_id, status="failed")
        orchestration_payload = runtime._response_data(payload) or payload
        return ScenarioExecutionResult(
            scenario_id=self.scenario_id,
            status="success",
            tool_calls_used=1,
            orchestration_updates={"estimate_maintenance": orchestration_payload},
            tool_results=[
                runtime._build_tool_result(
                    "estimate_maintenance",
                    payload,
                    status="success",
                    reason="Build a compact maintenance plan for the current mileage and vehicle context",
                    scenario_id=self.scenario_id,
                    evidence_ref="mileage",
                )
            ],
        )
