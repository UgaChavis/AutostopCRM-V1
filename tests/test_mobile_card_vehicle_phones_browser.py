"""Saving a mobile card preserves the vehicle profile's other phone numbers."""

from tests.browser_ui_support import UiBrowserTestCase


class MobileCardVehiclePhonesBrowserTests(UiBrowserTestCase):
    def test_save_keeps_other_phones_when_primary_is_unchanged_changed_or_cleared(self):
        first = "+7 700 111 22 33"
        second = "+7 700 444 55 66"
        third = "+7 700 777 88 99"
        cases = (
            (None, [first, second]),
            (third, [third, second]),
            ("", [second]),
            (second, [second]),
        )
        cards = [
            self.create_card(
                "SYNTHETIC PHONE TEST",
                vehicle_profile={"customer_phone": first, "customer_phones": [first, second]},
            )
            for _ in cases
        ]
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        for card, (phone, expected) in zip(cards, cases, strict=True):
            with self.subTest(phone=phone):
                self.page.locator(f'[data-mobile-card-id="{card}"]').click()
                self.page.wait_for_function(
                    "id => state.mobileCardId === id && !state.mobileCardLoading", arg=card
                )
                self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC CHANGED TITLE")
                if phone is not None:
                    self.page.click('[data-mobile-card-tab="vehicle"]')
                    self.page.locator("#mobileCardVehicleCustomerPhone").fill(phone)
                with self.page.expect_request("**/api/update_card") as request:
                    self.page.click("#mobileCardSaveButton")
                self.page.wait_for_function("!state.mobileCardSaving")
                sent = request.value.post_data_json["vehicle_profile"]
                saved = self.stored_card(card)["vehicle_profile"]
                self.assertEqual(sent["customer_phones"], expected)
                self.assertEqual(saved["customer_phones"], expected)
                self.assertEqual(saved["customer_phone"], expected[0])
                self.page.click("#mobileCardBackButton")
