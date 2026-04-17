from __future__ import annotations

import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.contracts import PatchResult, PlanResult
from minimal_kanban.agent.tools import AgentToolExecutor
from minimal_kanban.agent.policy import ToolPolicyEngine


class ToolPolicyEngineTests(unittest.TestCase):
    def test_build_plan_deduplicates_chain_and_normalizes_execution_mode(self) -> None:
        engine = ToolPolicyEngine()
        plan = engine.build_plan(
            scenario_chain=["VIN_ENRICHMENT", "vin_enrichment", "normalization", "normalization"],
            execution_mode="STRUCTURED_CARD",
            followup_enabled=True,
            notes=["first note"],
        )

        self.assertEqual(plan.scenario_chain, ["vin_enrichment", "normalization"])
        self.assertEqual(plan.scenario_id, "vin_enrichment")
        self.assertEqual(plan.execution_mode, "structured_card")
        self.assertEqual(plan.required_tools, ["decode_vin"])
        self.assertEqual(plan.write_mode, "patch_only_additive")
        self.assertTrue(plan.followup_policy["enabled"])

    def test_filter_patch_honors_forbidden_targets(self) -> None:
        engine = ToolPolicyEngine()
        plan = PlanResult(
            scenario_id="custom",
            scenario_chain=["custom"],
            execution_mode="model_loop",
            needs_external_tools=False,
            allowed_write_targets=["title", "vehicle_profile", "repair_order", "repair_order_works", "repair_order_materials"],
            forbidden_write_targets=["vehicle_profile", "repair_order_works"],
        )
        patch = PatchResult(
            card_patch={
                "title": "Updated title",
                "vehicle_profile": {"vin": "WBAPF71060A798127"},
                "description": "Should be removed",
            },
            repair_order_patch={"status": "open"},
            repair_order_works=[{"name": "ignored"}],
            repair_order_materials=[{"name": "kept"}],
            append_only_notes=["note"],
        )

        filtered = engine.filter_patch(plan, patch)

        self.assertEqual(filtered.card_patch, {"title": "Updated title"})
        self.assertEqual(filtered.repair_order_patch, {"status": "open"})
        self.assertEqual(filtered.repair_order_works, [])
        self.assertEqual(filtered.repair_order_materials, [{"name": "kept"}])
        self.assertEqual(filtered.append_only_notes, ["note"])

    def test_tool_source_type_normalizes_tool_name_case(self) -> None:
        engine = ToolPolicyEngine()
        self.assertEqual(engine.tool_source_type("DECODE_VIN"), "external_vin")
        self.assertEqual(engine.tool_source_type("Search_Fault_Info"), "external_fault")

    def test_build_plan_ignores_unknown_scenarios_and_falls_back_cleanly(self) -> None:
        engine = ToolPolicyEngine()
        plan = engine.build_plan(
            scenario_chain=["unknown_scenario", "also_unknown"],
            execution_mode="MODEL_LOOP",
            followup_enabled=False,
        )

        self.assertEqual(plan.scenario_id, "freeform_manual")
        self.assertEqual(plan.scenario_chain, ["freeform_manual"])
        self.assertEqual(plan.execution_mode, "model_loop")
        self.assertEqual(plan.required_tools, [])
        self.assertEqual(plan.allowed_write_targets, [])
        self.assertEqual(plan.forbidden_write_targets, [])
        self.assertEqual(plan.followup_policy["mode"], "none")

    def test_agent_tool_executor_accepts_mixed_case_tool_names(self) -> None:
        class _FakeBoardApi:
            def health(self) -> dict[str, object]:
                return {"ok": True}

            def review_board(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def list_columns(self) -> dict[str, object]:
                return {"ok": True}

            def get_board_snapshot(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def search_cards(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def get_card(self, card_id: str) -> dict[str, object]:
                return {"ok": True, "card_id": card_id}

            def get_card_context(self, card_id: str, **kwargs) -> dict[str, object]:
                return {"ok": True, "card_id": card_id}

            def create_card(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def update_card(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def move_card(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def archive_card(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def restore_card(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def list_repair_orders(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def get_repair_order(self, card_id: str) -> dict[str, object]:
                return {"ok": True, "card_id": card_id}

            def update_repair_order(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def replace_repair_order_works(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def replace_repair_order_materials(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def set_repair_order_status(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def list_cashboxes(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

            def get_cashbox(self, cashbox_id: str, **kwargs) -> dict[str, object]:
                return {"ok": True, "cashbox_id": cashbox_id}

            def create_cashbox(self, name: str, **kwargs) -> dict[str, object]:
                return {"ok": True, "name": name}

            def delete_cashbox(self, cashbox_id: str, **kwargs) -> dict[str, object]:
                return {"ok": True, "cashbox_id": cashbox_id}

            def create_cash_transaction(self, **kwargs) -> dict[str, object]:
                return {"ok": True}

        executor = AgentToolExecutor(_FakeBoardApi())
        payload = executor.execute("DECODE_VIN", {"vin": "WBAPF71060A798127"})
        self.assertEqual(payload["vin"], "WBAPF71060A798127")


if __name__ == "__main__":
    unittest.main()
