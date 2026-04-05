from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.web_assets import BOARD_WEB_APP_HTML


class WebAssetsTests(unittest.TestCase):
    def test_board_settings_keep_slider_but_remove_wheel_zoom_binding(self) -> None:
        self.assertIn('id="boardScaleInput"', BOARD_WEB_APP_HTML)
        self.assertIn('class="scale-track"', BOARD_WEB_APP_HTML)
        self.assertNotIn("addEventListener('wheel'", BOARD_WEB_APP_HTML)
        self.assertNotIn("function handleBoardWheel", BOARD_WEB_APP_HTML)
        self.assertIn("grid-template-rows: auto auto minmax(0, 1fr);", BOARD_WEB_APP_HTML)
        self.assertIn("--board-gutter-left: 0px;", BOARD_WEB_APP_HTML)
        self.assertIn("--board-gutter-top: 0px;", BOARD_WEB_APP_HTML)
        self.assertIn(".status-shell .message {", BOARD_WEB_APP_HTML)
        self.assertIn("width: max-content;", BOARD_WEB_APP_HTML)

    def test_card_tag_editor_uses_compact_tag_controls(self) -> None:
        self.assertIn(".tag-list .tag {", BOARD_WEB_APP_HTML)
        self.assertIn('class="tag-suggestions" id="tagSuggestions"', BOARD_WEB_APP_HTML)
        self.assertIn('class="tag-entry"', BOARD_WEB_APP_HTML)
        self.assertIn('class="field field--tags"', BOARD_WEB_APP_HTML)
        self.assertIn("МЕТОК НЕТ", BOARD_WEB_APP_HTML)
        self.assertIn(".column > * {", BOARD_WEB_APP_HTML)


    def test_sticky_dock_uses_single_icon_button_without_dropdown(self) -> None:
        self.assertIn('class="sticky-dock__button" id="stickyDockButton"', BOARD_WEB_APP_HTML)
        self.assertIn('aria-label="Новый стикер"', BOARD_WEB_APP_HTML)
        self.assertIn('title="Новый стикер"', BOARD_WEB_APP_HTML)
        self.assertIn(".sticky-dock__button svg {", BOARD_WEB_APP_HTML)
        self.assertNotIn("stickyDockMenu", BOARD_WEB_APP_HTML)
        self.assertNotIn("stickyCreateButton", BOARD_WEB_APP_HTML)
        self.assertNotIn("toggleStickyMenu", BOARD_WEB_APP_HTML)
        self.assertNotIn("closeStickyMenu", BOARD_WEB_APP_HTML)

    def test_card_description_textarea_allows_extended_text(self) -> None:
        self.assertIn('id="cardDescription" maxlength="20000"', BOARD_WEB_APP_HTML)


if __name__ == "__main__":
    unittest.main()
