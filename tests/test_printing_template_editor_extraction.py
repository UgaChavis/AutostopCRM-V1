from __future__ import annotations

import unittest

from minimal_kanban.printing.web_module import PRINTING_WEB_MODULE_SCRIPT
from minimal_kanban.printing.web_template_editor import (
    PRINTING_TEMPLATE_EDITOR_HELPERS_SCRIPT,
    PRINTING_TEMPLATE_EDITOR_WORKFLOW_SCRIPT,
)


class PrintingTemplateEditorExtractionTests(unittest.TestCase):
    def test_fragments_are_assembled_once_in_original_order(self) -> None:
        self.assertEqual(
            1, PRINTING_WEB_MODULE_SCRIPT.count(PRINTING_TEMPLATE_EDITOR_HELPERS_SCRIPT)
        )
        self.assertEqual(
            1, PRINTING_WEB_MODULE_SCRIPT.count(PRINTING_TEMPLATE_EDITOR_WORKFLOW_SCRIPT)
        )
        self.assertLess(
            PRINTING_WEB_MODULE_SCRIPT.index(PRINTING_TEMPLATE_EDITOR_HELPERS_SCRIPT),
            PRINTING_WEB_MODULE_SCRIPT.index(PRINTING_TEMPLATE_EDITOR_WORKFLOW_SCRIPT),
        )
