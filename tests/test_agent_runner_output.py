from __future__ import annotations

import json
import math
import unittest

if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

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
