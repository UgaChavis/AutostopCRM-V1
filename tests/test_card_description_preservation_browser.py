"""Preserve exact descriptions and reject oversized drafts in a disposable CRM."""

import json
from pathlib import Path

from tests.browser_ui_support import UiBrowserTestCase


class CardDescriptionPreservationBrowserTests(UiBrowserTestCase):
    def test_legacy_crlf_survives_unrelated_desktop_save(self):
        card_id = self.create_card("Legacy UI", description="initial")
        path = Path(self.runtime.temp_dir.name) / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        description = "A\r\n\r\nB\rC\u00a0D"
        next(card for card in state["cards"] if card["id"] == card_id)["description"] = description
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        self.load_board()
        self.open_card(card_id)
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), description)
        self.page.locator("#cardTitle").fill("Other title")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assertEqual(self.stored_card(card_id)["description"], description)

    def test_removing_one_of_three_newlines_saves_the_edit(self):
        card_id = self.create_card("Newline collision", description="A\n\n\nB")
        self.load_board()
        self.open_card(card_id)
        self.page.evaluate("""() => {
          const editor = document.querySelector('#cardDescriptionEditor');
          const range = document.createRange();
          range.setStart(editor.lastChild, 0);
          range.collapse(true);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          editor.focus();
        }""")
        self.page.keyboard.press("Backspace")
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), "A\n\nB")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assertEqual(self.stored_card(card_id)["description"], "A\n\nB")

    def test_legacy_crlf_noop_http_and_cas(self):
        card_id = self.create_card("Legacy CRLF", description="original")
        path = Path(self.runtime.temp_dir.name) / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        next(card for card in state["cards"] if card["id"] == card_id)["description"] = "A\r\nB\rC"
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        before = path.read_bytes()
        card = self.stored_card(card_id)
        version = card["updated_at"]
        headers = {"X-Operator-Session": self.session_token}

        def update(**payload):
            return self.context.request.post(
                self.runtime.base_url + "/api/update_card",
                headers=headers,
                data={"card_id": card_id, **payload},
            )

        same = update(description="A\r\nB\rC", expected_updated_at=version)
        self.assertEqual(same.status, 200)
        self.assertFalse(same.json()["data"]["meta"]["changed"])
        self.assertEqual(path.read_bytes(), before)
        changed = update(
            title="Changed title", description="A\r\nB\rC", expected_updated_at=version
        )
        self.assertEqual(changed.status, 200)
        self.assertEqual(self.stored_card(card_id)["description"], "A\r\nB\rC")
        after = json.loads(path.read_text(encoding="utf-8"))
        actions = [event["action"] for event in after["events"] if event.get("card_id") == card_id]
        self.assertNotIn("description_changed", actions)
        conflict = update(description="Another", expected_updated_at=version)
        self.assertEqual(conflict.status, 409)
        self.assertEqual(self.stored_card(card_id)["description"], "A\r\nB\rC")
        version = self.stored_card(card_id)["updated_at"]
        changed_description = update(description="New\r\ntext", expected_updated_at=version)
        self.assertEqual(changed_description.status, 200)
        self.assertEqual(self.stored_card(card_id)["description"], "New\ntext")

    def test_untouched_description_round_trips_all_codepoints(self):
        description = "First\n\n\nFinal\u00a0part\n" + "😀" * 10005
        card_id = self.create_card("Original title", description=description)
        self.load_board()
        self.open_card(card_id)
        editor = self.page.locator("#cardDescriptionEditor")
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), description)
        self.assertEqual(editor.text_content().count("😀"), 10005)
        editor.focus()
        self.page.locator("#cardTitle").fill("Changed title")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assertEqual(self.stored_card(card_id)["description"], description)

    def test_edit_undo_and_link_preserve_the_correct_baseline(self):
        description = "First\n\n\nFinal\u00a0part"
        card_id = self.create_card("Original", description=description)
        self.load_board()
        self.open_card(card_id)
        editor = self.page.locator("#cardDescriptionEditor")
        initial_html = editor.inner_html()
        editor.click()
        self.page.keyboard.press("End")
        self.page.keyboard.insert_text("X")
        self.assertNotEqual(editor.inner_html(), initial_html)
        self.assertNotEqual(self.page.evaluate("getCardDescriptionValue()"), description)
        self.page.keyboard.press("Control+z")
        self.assertEqual(editor.inner_html(), initial_html)
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), description)
        editor.fill("Edited description")
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), "Edited description")
        self.page.evaluate("applyCardClientLinkResult(state.activeCard)")
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), "Edited description")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assertEqual(self.stored_card(card_id)["description"], "Edited description")
        self.page.locator("[data-create-in]").first.click()
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), "")

    def test_limit_preserves_draft_and_stops_create_and_update(self):
        card_id = self.create_card("Existing", description="Original")
        self.load_board()
        self.open_card(card_id)
        editor = self.page.locator("#cardDescriptionEditor")
        oversized = "😀" * 20001
        editor.fill(oversized)
        self.page.locator("#saveCardButton").click()
        self.assert_card_open()
        self.assertIn("20000", self.page.locator("#statusLine").inner_text())
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), oversized)
        self.assertNotIn("update_card", self.writes)
        self.assertEqual(self.stored_card(card_id)["description"], "Original")
        editor.fill("😀" * 20000)
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assertEqual(self.stored_card(card_id)["description"], "😀" * 20000)
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("Oversized new card")
        editor.fill(oversized)
        self.page.locator("#saveCardButton").click()
        self.assertNotIn("create_card", self.writes)
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), oversized)
        self.assertIsNone(self.page.evaluate("ensureRepairOrderCard()"))
        self.assertNotIn("create_card", self.writes)
