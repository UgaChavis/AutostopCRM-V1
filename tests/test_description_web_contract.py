from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT / "src" / "minimal_kanban" / "web_app_assets" / "source" / "app_main_before_printing.js"
)


class DescriptionWebContractTests(unittest.TestCase):
    def test_card_description_save_does_not_trim_editor_payload(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")

        self.assertNotIn("return String(els.cardDescription?.value || '').trim();", source)
        self.assertIn("return String(els.cardDescription?.value || '');", source)
        self.assertNotIn(".replace(/[ \\t]+\\n/g, '\\n')", source)
        self.assertNotIn(
            "description: String(values.description ?? card.description ?? '').trim(),",
            source,
        )
        self.assertIn(
            "description: String(values.description ?? card.description ?? ''),",
            source,
        )
