"""A denied delete must not be attributed to the current operation."""

import base64

from tests.attachment_samples import PNG_1X1_BYTES
from tests.browser_ui_support import UiBrowserTestCase


class CardFileRemovalRejectionBrowserTests(UiBrowserTestCase):
    def test_rejected_and_unknown_results_with_existing_tombstone(self):
        cases = [
            (mobile, kind)
            for mobile in (False, True)
            for kind in ("already_removed", "archived", "unknown")
        ]
        cards = []
        for _ in cases:
            card = self.create_card("SYNTHETIC DELETE REJECTION")
            attachment = self.runtime.service.add_card_attachment(
                {
                    "card_id": card,
                    "file_name": "synthetic.png",
                    "mime_type": "image/png",
                    "content_base64": base64.b64encode(PNG_1X1_BYTES).decode("ascii"),
                    "source": "ui",
                }
            )["attachment"]["id"]
            cards.append((card, attachment))
        self.load_board()
        self.page.evaluate("""() => { const original=setStatus; window.recordedStatuses=[];
            setStatus=(message,...args)=>{window.recordedStatuses.push({message,args});return original(message,...args)}; }""")
        for (card, attachment), (mobile, kind) in zip(cards, cases, strict=True):
            self.page.set_viewport_size({"width": 390 if mobile else 1440, "height": 900})
            if mobile:
                self.page.wait_for_function("state.mobileLite")
                self.page.evaluate("id => openMobileCardDetail(id)", card)
                self.page.wait_for_function(
                    "id => state.mobileCardId === id && !state.mobileCardLoading", arg=card
                )
                self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC DRAFT")
                self.page.click('[data-mobile-card-tab="files"]')
                remove = self.page.locator(f'[data-mobile-card-remove-file="{attachment}"]')
            else:
                self.open_card(card)
                self.page.locator("#cardTitle").fill("SYNTHETIC DRAFT")
                self.page.click('#cardModal [data-tab="files"]')
                remove = self.page.locator(f'#fileList [data-remove-file="{attachment}"]')
            remove.wait_for()
            observed = []

            def intercept(route):
                if kind != "unknown":
                    self.runtime.service.remove_card_attachment(
                        {
                            "card_id": card,
                            "attachment_id": attachment,
                            "actor_name": "synthetic-other",
                        }
                    )
                    if kind == "archived":
                        self.runtime.service.archive_card(
                            {"card_id": card, "actor_name": "synthetic-other"}
                        )
                result = route.fetch()
                observed.append(result.status)
                if kind == "unknown":
                    route.abort("failed")
                else:
                    route.fulfill(response=result)

            self.page.route("**/api/remove_card_attachment", intercept)
            self.page.evaluate("window.recordedStatuses=[]")
            remove.click()
            self.page.wait_for_function(
                "!state.mobileCardFilesBusy" if mobile else "!state.cardFilesMutationRequest"
            )
            self.assertEqual(
                observed, [{"already_removed": 400, "archived": 409, "unknown": 200}[kind]]
            )
            self.assertTrue(self.stored_card(card)["attachments"][0]["removed"])
            messages = [entry["message"] for entry in self.page.evaluate("window.recordedStatuses")]
            if kind == "unknown":
                self.assertTrue(
                    any("ПРИ ПРОВЕРКЕ ФАЙЛ ОТМЕЧЕН УДАЛЁННЫМ" in msg for msg in messages)
                )
            else:
                self.assertFalse(
                    any("ПРИ ПРОВЕРКЕ ФАЙЛ ОТМЕЧЕН УДАЛЁННЫМ" in msg for msg in messages)
                )
                self.assertTrue(
                    any(
                        ("уже удалён" if kind == "already_removed" else "Архивную карточку") in msg
                        for msg in messages
                    )
                )
            self.assertEqual(
                self.page.locator(
                    "#mobileCardTitleInput" if mobile else "#cardTitle"
                ).input_value(),
                "SYNTHETIC DRAFT",
            )
            self.page.unroute("**/api/remove_card_attachment", intercept)
            if mobile:
                self.page.once("dialog", lambda dialog: dialog.accept())
                self.page.evaluate("closeMobileCardDetail()")
            else:
                self.page.evaluate("closeCardModal()")
