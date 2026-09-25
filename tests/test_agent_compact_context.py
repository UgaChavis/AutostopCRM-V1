from __future__ import annotations

import math
import unittest

if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.agent.compact_context import compact_context_fingerprint  # noqa: E402


class AgentCompactContextTests(unittest.TestCase):
    def test_compact_context_fingerprint_sanitizes_non_finite_numbers(self) -> None:
        fingerprint = compact_context_fingerprint(
            {"kind": "compact_context", "score": math.nan, "items": [math.inf, -math.inf]}
        )
        sanitized_fingerprint = compact_context_fingerprint(
            {"kind": "compact_context", "score": None, "items": [None, None]}
        )

        self.assertRegex(fingerprint, r"^[0-9a-f]{16}$")
        self.assertEqual(fingerprint, sanitized_fingerprint)


if __name__ == "__main__":
    unittest.main()
