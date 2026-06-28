from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.runner import AgentRunner  # noqa: E402


class AgentRunnerSerializationTests(unittest.TestCase):
    def test_values_equal_sanitizes_non_finite_numbers(self) -> None:
        runner = object.__new__(AgentRunner)

        self.assertTrue(runner._values_equal({"score": math.nan}, {"score": None}))
        self.assertTrue(runner._values_equal([math.inf, -math.inf], [None, None]))

    def test_user_task_message_sanitizes_context_metadata(self) -> None:
        runner = object.__new__(AgentRunner)

        message = runner._build_user_task_message(
            {"id": "task-1", "mode": "manual", "source": "test", "task_text": "Проверь"},
            {"context": {"score": math.nan, "items": [math.inf]}},
            task_type="manual",
        )

        self.assertNotIn("NaN", message)
        self.assertNotIn("Infinity", message)
        self.assertIn('"score": null', message)
        self.assertIn('"items": [\n    null\n  ]', message)

    def test_autofill_plan_labels_sanitize_invalid_budget_and_skipped(self) -> None:
        runner = object.__new__(AgentRunner)

        plan = runner._normalize_card_autofill_plan_labels(
            {
                "scenarios": [{"name": "vin_enrichment"}],
                "skipped": math.inf,
                "budget_left": math.inf,
            }
        )

        self.assertEqual(plan["scenarios"][0]["label"], "VIN")
        self.assertEqual(plan["skipped"], [])
        self.assertEqual(plan["budget_left"], 0)


if __name__ == "__main__":
    unittest.main()
