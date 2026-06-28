from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.runner_output import AgentRunnerOutputMixin  # noqa: E402


class DummyRunnerOutput(AgentRunnerOutputMixin):
    _max_tool_result_chars = 1000


class AgentRunnerOutputTests(unittest.TestCase):
    def test_preview_payload_sanitizes_non_finite_numbers(self) -> None:
        preview = DummyRunnerOutput()._preview_payload(
            {"ok": True, "score": math.nan, "items": [math.inf, -math.inf]}
        )

        self.assertEqual(
            json.loads(preview),
            {"ok": True, "score": None, "items": [None, None]},
        )
        self.assertNotIn("NaN", preview)
        self.assertNotIn("Infinity", preview)


if __name__ == "__main__":
    unittest.main()
