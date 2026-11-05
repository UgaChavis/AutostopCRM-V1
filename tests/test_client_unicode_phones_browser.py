"""Different Unicode phone numbers survive display, edits and readback."""

from tests.browser_ui_support import UiBrowserTestCase


class ClientUnicodePhonesBrowserTests(UiBrowserTestCase):
    phones = ["7١٢٣", "7٤٥٦", "7٧٨٩"]

    def create_phone_client(self):
        return self.runtime.service.create_client(
            {"display_name": "SYNTHETIC UNICODE", "phone": self.phones[0], "phones": self.phones}
        )["client"]

    def open_phone_client(self, client):
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{client["id"]}"]').click()
        self.page.wait_for_function(
            "id => state.clientsDesktopProfile?.client?.id === id", arg=client["id"]
        )

    def test_profile_mobile_display_and_desktop_save_preserve_all_phones(self):
        client = self.create_phone_client()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.click('[data-mobile-view="more"]')
        self.page.click('[data-mobile-open="clients"]')
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{client["id"]}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id && !state.mobileClientProfileLoading",
            arg=client["id"],
        )
        line = self.page.locator("#mobileClientDetail .mobile-client-detail__meta").inner_text()
        for phone in self.phones:
            self.assertIn(phone, line)
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.open_phone_client(client)
        self.assertEqual(
            self.page.locator("#clientPhoneFields [data-client-phone-input]").evaluate_all(
                "items => items.map(item => item.value)"
            ),
            self.phones,
        )
        self.page.locator("#clientCommentInput").fill("SYNTHETIC EDIT")
        with self.page.expect_response("**/api/update_client") as response:
            self.page.locator("#clientSaveButton").click()
        self.assertEqual(response.value.status, 200)
        self.page.wait_for_function("!state.clientMutationRequest")
        saved = self.runtime.service.get_client({"client_id": client["id"]})["client"]
        self.assertEqual(saved["phones"], self.phones)
        self.assertEqual(saved["phone"], self.phones[0])

    def test_card_roundtrip_preserves_phones_on_desktop_and_mobile(self):
        cards = [
            self.create_card(
                "SYNTHETIC PHONE CARD " + view,
                vehicle_profile={"customer_phone": self.phones[0], "customer_phones": self.phones},
            )
            for view in ("DESKTOP", "MOBILE")
        ]
        self.load_board()
        self.open_card(cards[0])
        self.page.locator("#cardTitle").fill("SYNTHETIC DESKTOP EDIT")
        with self.page.expect_response("**/api/update_card") as response:
            self.page.locator("#saveCardButton").click()
        self.assertEqual(response.value.status, 200)
        self.finish_save()
        self.assertEqual(
            self.stored_card(cards[0])["vehicle_profile"]["customer_phones"], self.phones
        )

        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.locator(f'[data-mobile-card-id="{cards[1]}"]').click()
        self.page.wait_for_function(
            "id => state.mobileCardId === id && !state.mobileCardLoading", arg=cards[1]
        )
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC MOBILE EDIT")
        with self.page.expect_response("**/api/update_card") as response:
            self.page.locator("#mobileCardSaveButton").click()
        self.assertEqual(response.value.status, 200)
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertEqual(
            self.stored_card(cards[1])["vehicle_profile"]["customer_phones"], self.phones
        )

    def test_partial_unicode_readback_does_not_confirm_unknown_write(self):
        client = self.create_phone_client()
        self.load_board()
        self.open_phone_client(client)
        self.page.locator("#clientCommentInput").fill("SYNTHETIC DRAFT")

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        def partial_readback(route):
            response = route.fetch()
            body = response.json()
            body["data"]["client"]["phones"] = [self.phones[0]]
            route.fulfill(response=response, json=body)

        self.page.route("**/api/update_client", lose_reply)
        self.page.route("**/api/get_client?**", partial_readback)
        self.page.locator("#clientSaveButton").click()
        self.page.wait_for_function(
            "!state.clientMutationRequest && state.clientProfileSaveCheckpoint?.uncertain"
        )
        self.assertEqual(self.writes.count("update_client"), 1)
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC DRAFT")
        self.assertEqual(
            self.runtime.service.get_client({"client_id": client["id"]})["client"]["phones"],
            self.phones,
        )
        self.page.locator("#clientSaveButton").click()
        self.page.wait_for_function(
            "!state.clientMutationRequest && state.clientProfileSaveCheckpoint?.uncertain"
        )
        self.assertEqual(self.writes.count("update_client"), 1)
        self.page.unroute("**/api/get_client?**", partial_readback)
        self.page.locator("#clientSaveButton").click()
        self.page.wait_for_function(
            "!state.clientMutationRequest && !state.clientProfileSaveCheckpoint?.uncertain"
        )
        self.assertEqual(self.writes.count("update_client"), 1)

    def test_ascii_dedup_unicode_identity_and_limit(self):
        self.load_board()
        result = self.page.evaluate(
            """() => ({
              ascii: normalizePhoneList(['+7 123', '7-123', '555']),
              unicode: normalizePhoneList(['7١٢٣', '7٤٥٦', '7١٢٣', '7٧٨٩', '999']),
              blanks: normalizePhoneList(['', '7١٢٣', ''], {preserveEmpty: true}),
              differentCase: normalizePhoneList(['xЯ', 'xя']),
            })"""
        )
        self.assertEqual(result["ascii"], ["+7 123", "555"])
        self.assertEqual(result["unicode"], self.phones)
        self.assertEqual(result["blanks"], ["", self.phones[0], ""])
        self.assertEqual(result["differentCase"], ["xЯ", "xя"])
