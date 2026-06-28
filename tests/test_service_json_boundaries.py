from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services import card_service, snapshot_service


class ServiceJsonBoundaryTests(unittest.TestCase):
    def _assert_self_referential_payload_is_sanitized(self, module) -> None:
        payload: dict[str, object] = {"ok": True, "ratio": 1.25}
        payload["self"] = payload

        encoded = module._json_dumps(payload, sort_keys=True)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["ratio"], 1.25)
        node = decoded
        for _ in range(8):
            node = node["self"]

        self.assertIsInstance(node, str)

    def test_card_service_json_dumps_sanitizes_non_finite_numbers(self) -> None:
        encoded = card_service._json_dumps(
            {
                "score": float("nan"),
                "ratio": 1.25,
                "nested": [float("inf"), -float("inf")],
                7: {"ok": True},
            },
            sort_keys=True,
        )

        self.assertNotIn("NaN", encoded)
        self.assertNotIn("Infinity", encoded)
        self.assertEqual(
            json.loads(encoded),
            {"score": None, "ratio": 1.25, "nested": [None, None], "7": {"ok": True}},
        )

    def test_card_service_json_dumps_handles_self_referential_payload(self) -> None:
        self._assert_self_referential_payload_is_sanitized(card_service)

    def test_snapshot_service_json_dumps_sanitizes_non_finite_numbers(self) -> None:
        encoded = snapshot_service._json_dumps(
            {
                "score": float("nan"),
                "ratio": 1.25,
                "nested": [float("inf"), -float("inf")],
                7: {"ok": True},
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        self.assertNotIn("NaN", encoded)
        self.assertNotIn("Infinity", encoded)
        self.assertEqual(
            json.loads(encoded),
            {"score": None, "ratio": 1.25, "nested": [None, None], "7": {"ok": True}},
        )

    def test_snapshot_service_json_dumps_handles_self_referential_payload(self) -> None:
        self._assert_self_referential_payload_is_sanitized(snapshot_service)


if __name__ == "__main__":
    unittest.main()
