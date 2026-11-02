"""Description links work in the real editor without changing stored card text."""

from __future__ import annotations

import os
from pathlib import Path

from tests.browser_ui_support import UiBrowserTestCase


class CardDescriptionLinksBrowserTests(UiBrowserTestCase):
    def paste_text(self, text: str) -> bool:
        return self.page.evaluate(
            """text => {
              const transfer = new DataTransfer();
              transfer.setData('text/plain', text);
              transfer.setData('text/html', '<img src=x onerror=alert(1)>');
              return !els.cardDescriptionEditor.dispatchEvent(new ClipboardEvent('paste', {
                bubbles: true, cancelable: true, clipboardData: transfer
              }));
            }""",
            text,
        )

    def select_link_text(self, link) -> None:
        link.evaluate(
            """anchor => {
              const range = document.createRange();
              range.selectNodeContents(anchor);
              const selection = window.getSelection();
              selection.removeAllRanges();
              selection.addRange(range);
              els.cardDescriptionEditor.focus();
            }"""
        )

    def route_example(self) -> None:
        self.context.route(
            "https://example.test/**",
            lambda route: route.fulfill(status=200, content_type="text/html", body="Example"),
        )

    def open_popup(self, locator):
        with self.page.expect_popup() as popup_info:
            locator.click()
        popup = popup_info.value
        popup.wait_for_load_state()
        return popup

    def test_existing_links_open_without_changing_description_or_formatting(self) -> None:
        description = (
            "  **Инструкция https://example.test/путь?q=1&mark=$&**\n"
            "Скобки (https://example.test/page_(part)). "
            "++www.example.test/manual++\n"
            "*Примечание* https://example.test/a**b**c?x=1++2++3  \n"
            "Текст <img src=x onerror=alert(1)> и javascript:alert(1). \ue000"
        )
        card_id = self.create_card("Existing links", description=description)
        self.route_example()
        console_errors = []
        self.page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        self.load_board()
        self.assertTrue(self.page.url.startswith(self.runtime.base_url))
        self.assertIn("AutoStop", self.page.title())
        self.open_card(card_id)
        editor = self.page.locator("#cardDescriptionEditor")
        links = editor.locator("a[data-description-link]")
        self.assertEqual(links.count(), 4)
        self.assertEqual(
            links.nth(0).get_attribute("href"), "https://example.test/путь?q=1&mark=$&"
        )
        self.assertEqual(links.nth(1).text_content(), "https://example.test/page_(part)")
        self.assertEqual(links.nth(2).get_attribute("href"), "https://www.example.test/manual")
        self.assertEqual(links.nth(3).text_content(), "https://example.test/a**b**c?x=1++2++3")
        self.assertEqual(editor.locator("img").count(), 0)
        self.assertEqual(editor.locator("strong").count(), 1)
        self.assertEqual(editor.locator("u").count(), 1)
        self.assertEqual(editor.locator("em").count(), 1)
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), description)

        self.page.locator("#cardTitle").fill("Unsaved title while link opens")
        popup = self.open_popup(links.nth(1))
        self.assertEqual(popup.url, "https://example.test/page_(part)")
        self.assertIsNone(popup.evaluate("window.opener"))
        popup.close()
        self.assert_card_open()
        self.assertEqual(
            self.page.locator("#cardTitle").input_value(), "Unsaved title while link opens"
        )
        screenshot = os.environ.get("AUTOSTOP_LINK_SCREENSHOT")
        if screenshot:
            self.page.screenshot(path=str(Path(screenshot)))
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assertEqual(self.stored_card(card_id)["description"], description)
        self.assertEqual(console_errors, [])

    def test_paste_links_are_clickable_immediately_and_survive_save(self) -> None:
        card_id = self.create_card("Pasted links")
        self.route_example()
        self.load_board()
        self.open_card(card_id)
        editor = self.page.locator("#cardDescriptionEditor")
        editor.click()
        self.assertTrue(self.paste_text(""), "HTML-only paste must not use the browser default")
        self.assertEqual(editor.inner_text(), "")
        pasted = "Документация: https://example.test/path?a=1&b=2.\nВторой: www.example.test/page"
        self.paste_text(pasted)
        links = editor.locator("a[data-description-link]")
        self.assertEqual(links.count(), 2)
        self.assertEqual(links.first.text_content(), "https://example.test/path?a=1&b=2")
        self.assertEqual(editor.locator("img").count(), 0)
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), pasted)
        self.page.keyboard.insert_text("!")
        pasted += "!"
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), pasted)
        popup = self.open_popup(links.first)
        self.assertEqual(popup.url, "https://example.test/path?a=1&b=2")
        popup.close()
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assertEqual(self.stored_card(card_id)["description"], pasted)
        self.open_card(card_id)
        self.assertEqual(editor.locator("a[data-description-link]").count(), 2)
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), pasted)

    def test_editing_link_updates_target_and_disables_invalid_scheme(self) -> None:
        card_id = self.create_card("Edit link", description="https://example.test/old")
        self.route_example()
        self.load_board()
        self.open_card(card_id)
        link = self.page.locator("#cardDescriptionEditor a[data-description-link]")

        def replace_link_text(value: str) -> None:
            self.select_link_text(link)
            self.page.keyboard.insert_text(value)

        replace_link_text("https://example.test/new")
        self.assertEqual(link.get_attribute("href"), "https://example.test/new")
        self.page.locator("#cardDescriptionEditor").focus()
        self.page.keyboard.press("Control+End")
        self.page.keyboard.insert_text("?q=1")
        self.page.locator("#cardTitle").click()
        self.assertEqual(link.get_attribute("href"), "https://example.test/new?q=1")
        popup = self.open_popup(link)
        self.assertEqual(popup.url, "https://example.test/new?q=1")
        popup.close()
        replace_link_text("javascript:alert(1)")
        self.assertIsNone(link.get_attribute("href"))
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), "javascript:alert(1)")

    def test_url_boundaries_and_format_markers_round_trip(self) -> None:
        self.load_board()
        url = "https://example.test/page"
        cases = [
            (f"Сайт «{url}», '{url}', “{url}”", [url] * 3),
            (url + "/O'Connor", [url + "/O'Connor"]),
            (f"https://example.test/x++y\n++{url}++", ["https://example.test/x++y", url]),
            (f"https://example.test/x**y **{url}**", ["https://example.test/x**y", url]),
            (f"https://example.test/x*y *{url}*", ["https://example.test/x*y", url]),
            (f"*Незакрытый курсив\n{url}*", [url + "*"]),
            (f"Маркер \ue0000\ue001 и \ue000e {url}", [url]),
            ("javascript:alert(1) file:///C:/a data:text/html,hello", []),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                result = self.page.evaluate(
                    """text => {
                      const root = document.createElement('div');
                      root.innerHTML = descriptionMarkdownToHtml(text);
                      return {
                        source: descriptionEditorToMarkdown(root),
                        links: [...root.querySelectorAll('a')].map(a => a.getAttribute('href'))
                      };
                    }""",
                    text,
                )
                self.assertEqual(result["source"], text)
                self.assertEqual(result["links"], expected)

    def test_multiline_paste_keeps_same_link_before_and_after_save(self) -> None:
        card_id = self.create_card("Multiline link paste")
        self.load_board()
        self.open_card(card_id)
        editor = self.page.locator("#cardDescriptionEditor")
        editor.click()
        text = "++Инструкция\nhttps://example.test/page++"
        self.paste_text(text)
        self.assertEqual(editor.locator("a").get_attribute("href"), "https://example.test/page")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assertEqual(self.stored_card(card_id)["description"], text)
        self.open_card(card_id)
        self.assertEqual(editor.locator("a").get_attribute("href"), "https://example.test/page")
        self.assertEqual(editor.locator("u a").count(), 1)

    def test_replacing_and_splitting_existing_link_restores_clickable_urls(self) -> None:
        card_id = self.create_card("Replace link", description="https://example.test/old")
        self.load_board()
        self.open_card(card_id)
        editor = self.page.locator("#cardDescriptionEditor")
        self.select_link_text(editor.locator("a"))
        text = "https://example.test/first\nhttps://example.test/second"
        self.paste_text(text)
        self.assertEqual(editor.locator("a[href]").count(), 2)
        self.select_link_text(editor.locator("a").last)
        replacement = "https://example.test/third note https://example.test/fourth"
        self.page.keyboard.insert_text(replacement)
        self.page.locator("#cardTitle").click()
        self.assertEqual(editor.locator("a[href]").count(), 3)
        expected = "https://example.test/first\n" + replacement
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), expected)
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assertEqual(self.stored_card(card_id)["description"], expected)

    def test_formatting_part_of_link_preserves_address_after_save(self) -> None:
        url = "https://example.test/path"
        card_id = self.create_card("Format link", description=url)
        self.route_example()
        self.load_board()
        self.open_card(card_id)
        link = self.page.locator("#cardDescriptionEditor a")
        link.evaluate(
            """anchor => {
              const range = document.createRange();
              range.setStart(anchor.firstChild, anchor.textContent.lastIndexOf('path'));
              range.setEnd(anchor.firstChild, anchor.textContent.length);
              const selection = window.getSelection();
              selection.removeAllRanges();
              selection.addRange(range);
              els.cardDescriptionEditor.focus();
            }"""
        )
        self.page.locator('[data-description-format="bold"]').click()
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), "**" + url + "**")
        self.page.locator('[data-description-format="bold"]').click()
        self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), "**" + url + "**")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assertEqual(self.stored_card(card_id)["description"], "**" + url + "**")
        self.open_card(card_id)
        self.assertEqual(link.get_attribute("href"), url)
        self.assertEqual(self.page.locator("#cardDescriptionEditor strong a").count(), 1)
        popup = self.open_popup(link)
        self.assertEqual(popup.url, url)
        popup.close()

    def test_paste_inside_link_preserves_joined_text_and_linebreaks(self) -> None:
        self.load_board()
        for replace_old, pasted, expected in [
            (
                True,
                "new\nhttps://example.test/another",
                "https://example.test/new\nhttps://example.test/another",
            ),
            (
                False,
                "new https://example.test/sec",
                "https://example.test/new https://example.test/secold",
            ),
        ]:
            with self.subTest(replace_old=replace_old):
                card_id = self.create_card(
                    "Split link with paste", description="https://example.test/old"
                )
                self.page.evaluate("async () => { await refreshSnapshot(true); }")
                self.open_card(card_id)
                editor = self.page.locator("#cardDescriptionEditor")
                editor.locator("a").evaluate(
                    """(anchor, replace) => {
                      const range = document.createRange();
                      range.setStart(anchor.firstChild, anchor.textContent.length - 3);
                      if (replace) range.setEnd(anchor.firstChild, anchor.textContent.length);
                      else range.collapse(true);
                      const selection = window.getSelection();
                      selection.removeAllRanges();
                      selection.addRange(range);
                      els.cardDescriptionEditor.focus();
                    }""",
                    replace_old,
                )
                self.paste_text(pasted)
                self.assertEqual(self.page.evaluate("getCardDescriptionValue()"), expected)
                self.page.locator("#cardTitle").click()
                self.assertEqual(
                    editor.locator("a[href]").evaluate_all(
                        "nodes => nodes.map(a => a.getAttribute('href'))"
                    ),
                    expected.split(),
                )
                self.page.locator("#saveCardButton").click()
                self.finish_save()
                self.assertEqual(self.stored_card(card_id)["description"], expected)

    def test_typed_link_becomes_clickable_after_leaving_editor(self) -> None:
        card_id = self.create_card("Typed link")
        self.route_example()
        self.load_board()
        self.open_card(card_id)
        editor = self.page.locator("#cardDescriptionEditor")
        editor.fill("Сайт https://example.test/typed")
        self.page.locator("#cardTitle").click()
        link = editor.locator("a[data-description-link]")
        self.assertEqual(link.count(), 1)
        popup = self.open_popup(link)
        self.assertEqual(popup.url, "https://example.test/typed")
        popup.close()

    def test_selecting_link_does_not_navigate_and_keyboard_enter_opens_it(self) -> None:
        card_id = self.create_card("Select link", description="https://example.test/long-address")
        self.route_example()
        self.load_board()
        self.open_card(card_id)
        link = self.page.locator("#cardDescriptionEditor a[data-description-link]")
        popups = []
        self.page.on("popup", lambda popup: popups.append(popup))
        box = link.bounding_box()
        self.page.mouse.move(box["x"] + 3, box["y"] + box["height"] / 2)
        self.page.mouse.down()
        self.page.mouse.move(box["x"] + box["width"] - 3, box["y"] + box["height"] / 2, steps=8)
        self.page.mouse.up()
        self.assertTrue(self.page.evaluate("!window.getSelection().isCollapsed"))
        self.assertEqual(popups, [])
        link.focus()
        with self.page.expect_popup() as popup_info:
            self.page.keyboard.press("Enter")
        popup = popup_info.value
        popup.wait_for_load_state()
        self.assertEqual(popup.url, "https://example.test/long-address")
        popup.close()
