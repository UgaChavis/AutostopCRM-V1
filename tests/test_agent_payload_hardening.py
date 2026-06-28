from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.contracts import (  # noqa: E402
    EvidenceResult,
    FactEvidence,
    OrchestrationTrace,
    PatchResult,
    PlanResult,
    ToolResult,
    VerifyResult,
)
from minimal_kanban.agent.policy import ToolPolicyEngine  # noqa: E402
from minimal_kanban.agent.runner import AgentRunner  # noqa: E402
from minimal_kanban.agent.scenarios.base import ScenarioExecutionResult  # noqa: E402
from minimal_kanban.agent.scenarios.vin_enrichment import (  # noqa: E402
    VinEnrichmentScenarioExecutor,
    _merge_web_enrichment,
)
from minimal_kanban.agent.storage import AgentStorage  # noqa: E402


class _BoardRuntimeStorage:
    def __init__(self, board_control: object) -> None:
        self.status = {"board_control": board_control}
        self.updated_board_control: dict[str, object] | None = None

    def read_status(self) -> dict[str, object]:
        return dict(self.status)

    def update_status(self, **kwargs) -> None:  # noqa: ANN003
        board_control = kwargs.get("board_control")
        if isinstance(board_control, dict):
            self.updated_board_control = dict(board_control)
            self.status["board_control"] = dict(board_control)


class AgentPayloadHardeningTests(unittest.TestCase):
    def test_runner_safe_int_helpers_clamp_large_finite_values(self) -> None:
        runner = object.__new__(AgentRunner)

        self.assertEqual(runner._safe_non_negative_int(1e308), 1_000_000_000)
        self.assertEqual(runner._safe_non_negative_int(-1e308), 0)
        self.assertEqual(
            runner._summarize_price_summary({"price_summary": {"offers_total": 1e308}}),
            "",
        )

    def test_contract_to_dict_normalizes_corrupted_payload_shapes(self) -> None:
        evidence = EvidenceResult(
            context_kind="card",
            confirmed_facts=["bad"],  # type: ignore[arg-type]
            fact_evidence={
                "vin": FactEvidence("vin", confidence=float("nan"), notes="note"),
                "bool": FactEvidence("bool", confidence=True),
            },  # type: ignore[arg-type]
            missing_data="vin",  # type: ignore[arg-type]
            scenario_signals=["bad"],  # type: ignore[arg-type]
        )
        plan = PlanResult(
            scenario_id="vin_enrichment",
            scenario_chain="vin_enrichment",  # type: ignore[arg-type]
            execution_mode="model_loop",
            needs_external_tools=True,
            required_tools="decode_vin",  # type: ignore[arg-type]
            followup_policy=["bad"],  # type: ignore[arg-type]
        )
        tool = ToolResult(
            tool_name="decode_vin",
            status="success",
            source_type="external_vin",
            confidence=float("inf"),
            data=["bad"],  # type: ignore[arg-type]
        )
        patch = PatchResult(
            card_patch=["bad"],  # type: ignore[arg-type]
            repair_order_works="bad",  # type: ignore[arg-type]
            append_only_notes="note",  # type: ignore[arg-type]
            warnings="warn",  # type: ignore[arg-type]
        )
        verify = VerifyResult(
            applied_ok=True,
            fields_changed="vehicle_profile",  # type: ignore[arg-type]
            warnings="verify warning",  # type: ignore[arg-type]
        )
        trace = OrchestrationTrace(
            version="v1",
            trigger=["bad"],  # type: ignore[arg-type]
            context_snapshot_id="ctx",
            evidence=evidence,
            plan=plan,
            scenario_feedback="bad",  # type: ignore[arg-type]
            tool_results=[tool, "bad"],  # type: ignore[list-item]
            patch=patch,
            verify=verify,
        ).to_dict()

        self.assertEqual(trace["trigger"], {})
        self.assertEqual(trace["evidence"]["confirmed_facts"], {})
        self.assertEqual(trace["evidence"]["missing_data"], ["vin"])
        self.assertEqual(trace["evidence"]["fact_evidence"]["vin"]["confidence"], 0.0)
        self.assertEqual(trace["evidence"]["fact_evidence"]["bool"]["confidence"], 0.0)
        self.assertEqual(trace["plan"]["scenario_chain"], ["vin_enrichment"])
        self.assertEqual(trace["plan"]["followup_policy"], {})
        self.assertEqual(trace["tool_results"][0]["confidence"], 0.0)
        self.assertEqual(trace["tool_results"][0]["data"], {})
        self.assertEqual(trace["patch"]["card_patch"], {})
        self.assertEqual(trace["patch"]["append_only_notes"], ["note"])
        self.assertEqual(trace["verify"]["fields_changed"], ["vehicle_profile"])

    def test_policy_filter_patch_uses_safe_patch_payload(self) -> None:
        engine = ToolPolicyEngine()
        plan = PlanResult(
            scenario_id="custom",
            scenario_chain=["custom"],
            execution_mode="model_loop",
            needs_external_tools=False,
            allowed_write_targets=["title"],
        )
        patch = PatchResult(
            card_patch=["bad"],  # type: ignore[arg-type]
            repair_order_patch=["bad"],  # type: ignore[arg-type]
            append_only_notes="note",  # type: ignore[arg-type]
        )

        filtered = engine.filter_patch(plan, patch)

        self.assertEqual(filtered.card_patch, {})
        self.assertEqual(filtered.repair_order_patch, {})
        self.assertEqual(filtered.append_only_notes, ["note"])

    def test_policy_vin_enrichment_bypass_still_normalizes_patch_shape(self) -> None:
        engine = ToolPolicyEngine()
        plan = PlanResult(
            scenario_id="vin_enrichment",
            scenario_chain=["vin_enrichment"],
            execution_mode="model_loop",
            needs_external_tools=True,
        )
        patch = PatchResult(
            card_patch=["bad"],  # type: ignore[arg-type]
            append_only_notes="note",  # type: ignore[arg-type]
        )

        filtered = engine.filter_patch(plan, patch)

        self.assertEqual(filtered.card_patch, {})
        self.assertEqual(filtered.append_only_notes, ["note"])

    def test_runner_autofill_vehicle_patch_rejects_bad_numeric_values(self) -> None:
        runner = object.__new__(AgentRunner)

        patch = AgentRunner._autofill_vehicle_patch(
            runner,
            facts={"vin": "JSAZC72S001234567", "vehicle_profile": {}},
            decoded_vin={
                "vin": "JSAZC72S001234567",
                "model_year": "9" * 20,
                "engine_power_hp": "9" * 20,
                "web_source_urls": "https://example.com",
                "web_enrichment_fields": "engine_power_hp",
            },
            vin_decode_status="success",
        )

        self.assertNotIn("production_year", patch)
        self.assertNotIn("engine_power_hp", patch)
        self.assertEqual(patch["source_links_or_refs"], [])

    def test_runner_price_summary_ignores_corrupt_numbers(self) -> None:
        runner = object.__new__(AgentRunner)

        self.assertEqual(
            AgentRunner._summarize_price_summary(
                runner,
                {
                    "price_summary": {
                        "offers_total": "inf",
                        "min_rub": "nan",
                        "max_rub": "999999999999999999999",
                    }
                },
            ),
            "",
        )

    def test_runner_board_control_task_update_normalizes_runtime_containers(self) -> None:
        storage = _BoardRuntimeStorage(
            {
                "card_cache": ["bad"],
                "recent_traces": "bad",
                "written_count": 1e308,
            }
        )
        runner = object.__new__(AgentRunner)
        runner._storage = storage  # type: ignore[attr-defined]

        AgentRunner._update_board_control_runtime_after_task(
            runner,
            task={
                "metadata": {
                    "purpose": "board_control",
                    "context": {"kind": "card", "card_id": "card-1"},
                }
            },
            orchestration={
                "patch": {"card_patch": {"description": "updated"}},
                "verify": {"applied_ok": True},
            },
        )

        assert storage.updated_board_control is not None
        self.assertEqual(storage.updated_board_control["written_count"], 1_000_000_000)
        self.assertEqual(
            storage.updated_board_control["card_cache"]["card-1"]["last_result"],  # type: ignore[index]
            "written",
        )
        self.assertEqual(
            storage.updated_board_control["recent_traces"][0]["status"],  # type: ignore[index]
            "written",
        )

    def test_runner_board_control_failure_update_normalizes_runtime_containers(self) -> None:
        storage = _BoardRuntimeStorage(
            {
                "card_cache": ["bad"],
                "recent_traces": "bad",
                "error_count": 1e308,
            }
        )
        runner = object.__new__(AgentRunner)
        runner._storage = storage  # type: ignore[attr-defined]

        AgentRunner._update_board_control_runtime_after_failure(
            runner,
            task={
                "metadata": {
                    "purpose": "board_control",
                    "context": {"kind": "card", "card_id": "card-1"},
                }
            },
            error="boom",
        )

        assert storage.updated_board_control is not None
        self.assertEqual(storage.updated_board_control["error_count"], 1_000_000_000)
        self.assertEqual(
            storage.updated_board_control["card_cache"]["card-1"]["last_result"],  # type: ignore[index]
            "failed",
        )
        self.assertEqual(
            storage.updated_board_control["recent_traces"][0]["status"],  # type: ignore[index]
            "failed",
        )

    def test_runner_scenario_loop_normalizes_bad_scenario_result_fields(self) -> None:
        class _BadPayloadExecutor:
            scenario_id = "bad_payload"

            def execute(self, context):  # noqa: ANN001
                return ScenarioExecutionResult(
                    scenario_id=context.scenario_id,
                    status="ok",
                    tool_calls_used=float("inf"),  # type: ignore[arg-type]
                    notes="single note",  # type: ignore[arg-type]
                    warnings="single warning",  # type: ignore[arg-type]
                    orchestration_updates=["bad"],  # type: ignore[arg-type]
                    facts_updates=["bad"],  # type: ignore[arg-type]
                    tool_results=["bad"],  # type: ignore[list-item]
                )

        class _FakeBoardApi:
            pass

        class _NullModel:
            model = "offline-null"

        with tempfile.TemporaryDirectory(prefix="autostopcrm-test-") as temp_dir:
            storage = AgentStorage(base_dir=Path(temp_dir))
            runner = AgentRunner(
                storage=storage,
                board_api=_FakeBoardApi(),  # type: ignore[arg-type]
                model_client=_NullModel(),  # type: ignore[arg-type]
                logger=logging.getLogger("autostopcrm.test"),
            )
            runner._scenario_registry.register(_BadPayloadExecutor())

            facts = {
                "card": {"id": "card-1", "title": "Test", "description": ""},
                "vehicle_profile": {},
                "vin": "",
                "autofill_plan": {
                    "scenarios": [{"name": "bad_payload", "label": "BAD", "cost": 1}]
                },
            }
            result = runner._execute_card_autofill_task(
                {"id": "task-1", "task_text": "Проверь карточку"},
                run_id="run-1",
                metadata={
                    "purpose": "card_enrichment",
                    "context": {"kind": "card", "card_id": "card-1"},
                },
                facts=facts,
                plan=PlanResult(
                    scenario_id="bad_payload",
                    scenario_chain=["bad_payload"],
                    execution_mode="structured_card",
                    needs_external_tools=False,
                ),
            )

            actions = storage.list_actions(limit=20)

        feedback = facts["_scenario_feedback"][0]
        self.assertEqual(feedback["tool_calls_used"], 0)
        self.assertEqual(result[3], 0)
        self.assertEqual(feedback["notes"], ["single note"])
        self.assertEqual(feedback["warnings"], ["single warning"])
        self.assertTrue(any(action.get("message") == "single note" for action in actions))
        self.assertTrue(any(action.get("message") == "single warning" for action in actions))

    def test_vin_scenario_patch_normalizes_year_confidence_and_links(self) -> None:
        executor = VinEnrichmentScenarioExecutor()
        patch = executor._build_card_patch(
            facts={"vin": "JSAZC72S001234567"},
            orchestration_payload={
                "make": "Suzuki",
                "model": "Swift",
                "model_year": "9" * 20,
                "source_confidence": "nan",
                "source_links_or_refs": "https://one.example\nhttps://two.example",
            },
            vin_status="success",
        )

        profile = patch["vehicle_profile"]
        self.assertNotIn("production_year", profile)
        self.assertEqual(profile["source_confidence"], 0.0)
        self.assertEqual(
            profile["source_links_or_refs"],
            ["https://one.example", "https://two.example"],
        )

    def test_merge_web_enrichment_accepts_non_dict_decode_payload(self) -> None:
        merged, fields = _merge_web_enrichment(["bad"], {"engine_model": "K12B"})  # type: ignore[arg-type]

        self.assertEqual(merged["engine_model"], "K12B")
        self.assertEqual(fields, ["engine_model"])


if __name__ == "__main__":
    unittest.main()
