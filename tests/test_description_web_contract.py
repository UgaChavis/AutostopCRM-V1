from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from tests.web_asset_function_support import functions

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT / "src" / "minimal_kanban" / "web_app_assets" / "source" / "app_main_before_printing.js"
)


class DescriptionWebContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_card_description_save_does_not_trim_editor_payload(self) -> None:
        script = (
            """
const assert = require('node:assert/strict');
const original = '  A\\n\\n\\nB\\u00a0  ';
const state = {cardDescriptionBaseline: {raw: original, html: 'initial'}};
const els = {cardDescription: {value: ''}, cardDescriptionEditor: {innerHTML: 'initial'}};
function descriptionEditorToMarkdown() { return '  edited\\n\\ntext  '; }
"""
            + functions(
                "app_main_before_printing.js",
                "syncCardDescriptionSourceFromEditor",
                "getCardDescriptionValue",
            )
            + """
assert.equal(getCardDescriptionValue(), original);
assert.equal(els.cardDescription.value, original);
els.cardDescriptionEditor.innerHTML = 'edited';
assert.equal(getCardDescriptionValue(), '  edited\\n\\ntext  ');
els.cardDescriptionEditor.innerHTML = 'initial';
assert.equal(getCardDescriptionValue(), original);
els.cardDescriptionEditor = null;
els.cardDescription.value = '  fallback\\n  ';
assert.equal(getCardDescriptionValue(), '  fallback\\n  ');
"""
        )
        result = subprocess.run(
            ["node"], input=script, text=True, encoding="utf-8", capture_output=True, timeout=15
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_mobile_description_payload_keeps_whitespace(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn(".replace(/[ \\t]+\\n/g, '\\n')", source)
        self.assertNotIn(
            "description: String(values.description ?? card.description ?? '').trim(),",
            source,
        )
        self.assertIn(
            "description: String(values.description ?? card.description ?? ''),",
            source,
        )


if __name__ == "__main__":
    unittest.main()
