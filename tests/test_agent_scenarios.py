from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.scenarios import ScenarioContext, build_default_scenario_registry


class AgentScenarioRegistryTests(unittest.TestCase):
    def test_default_registry_contains_expected_scenarios(self) -> None:
        registry = build_default_scenario_registry()
        self.assertEqual(
            registry.names(),
            [
                "board_review",
                "cash_review",
                "dtc_lookup",
                "fault_research",
                "freeform_manual",
                "maintenance_lookup",
                "normalization",
                "parts_lookup",
                "repair_order_assistance",
                "vin_enrichment",
            ],
        )

    def test_registered_scenario_returns_passthrough_result(self) -> None:
        registry = build_default_scenario_registry()
        executor = registry.get("normalization")
        self.assertIsNotNone(executor)
        result = executor.execute(
            ScenarioContext(
                scenario_id="normalization",
                task_id="task-1",
                run_id="run-1",
                metadata={"purpose": "card_autofill"},
                facts={"vin": "WBAPF71060A798127"},
            )
        )
        self.assertEqual(result.scenario_id, "normalization")
        self.assertEqual(result.status, "registered")
        self.assertIn("legacy runner logic", result.notes[0])


if __name__ == "__main__":
    unittest.main()
