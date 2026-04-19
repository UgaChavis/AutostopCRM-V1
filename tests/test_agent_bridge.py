"""Tests for CRM-side VIN enrichment bridge."""

# ruff: noqa: E402
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.bridge import normalize_card_enrichment_patch


class AgentBridgeTests(unittest.TestCase):
    def test_normalize_card_enrichment_patch_preserves_vehicle_profile_meta(self) -> None:
        patch = normalize_card_enrichment_patch(
            {
                "description": "  По VIN найдено подтверждение.  ",
                "vehicle": "  Toyota Land Cruiser 4.0  ",
                "vehicle_profile": {
                    "vin": "JTEBU3FJX05027767",
                    "make_display": "Toyota",
                    "model_display": "Land Cruiser 4.0",
                    "raw_input_text": "VIN: JTEBU3FJX05027767\nengine: 1GR-FE",
                    "warnings": ["exact decode not confirmed", "exact decode not confirmed"],
                    "source_summary": "VIN web research",
                    "image_parse_status": "not_attempted",
                    "unknown_field": "drop me",
                },
                "unknown_top_level": "drop me too",
            }
        )

        self.assertEqual(patch["description"], "По VIN найдено подтверждение.")
        self.assertEqual(patch["vehicle"], "Toyota Land Cruiser 4.0")
        self.assertEqual(patch["vehicle_profile"]["vin"], "JTEBU3FJX05027767")
        self.assertEqual(
            patch["vehicle_profile"]["raw_input_text"], "VIN: JTEBU3FJX05027767\nengine: 1GR-FE"
        )
        self.assertEqual(
            patch["vehicle_profile"]["warnings"],
            ["exact decode not confirmed", "exact decode not confirmed"],
        )
        self.assertNotIn("unknown_field", patch["vehicle_profile"])
        self.assertNotIn("unknown_top_level", patch)


if __name__ == "__main__":
    unittest.main()
