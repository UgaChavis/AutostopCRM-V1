"""Desktop and mobile saves preserve long existing vehicle profile parts."""

from tests.browser_ui_support import UiBrowserTestCase


class VehicleLongDisplayBrowserTests(UiBrowserTestCase):
    def exercise_save(self, mobile):
        lengths = (44, 45, 109, 110)
        cards = [
            self.create_card(
                "SYNTHETIC LONG PROFILE",
                vehicle="SYNTHETIC SHORT",
                description="SYNTHETIC DESCRIPTION",
                vehicle_profile={
                    "make_display": "Land Rover",
                    "model_display": "R" * length,
                    "production_year": 2020,
                    "vin": "WVWZZZ1KZAW000001",
                },
            )
            for length in lengths
        ]
        if mobile:
            self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        for card_id, length in zip(cards, lengths, strict=True):
            with self.subTest(mobile=mobile, model_length=length):
                if mobile:
                    self.page.locator(f'[data-mobile-card-id="{card_id}"]').click()
                    self.page.wait_for_function(
                        "id => state.mobileCardId === id && !state.mobileCardLoading", arg=card_id
                    )
                    self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC UPDATED")
                    with self.page.expect_response("**/api/update_card") as response:
                        self.page.locator("#mobileCardSaveButton").click()
                    self.page.wait_for_function("!state.mobileCardSaving")
                else:
                    self.open_card(card_id)
                    self.page.locator("#cardTitle").fill("SYNTHETIC UPDATED")
                    with self.page.expect_response("**/api/update_card") as response:
                        self.page.locator("#saveCardButton").click()
                    self.finish_save()
                self.assertEqual(response.value.status, 200)
                saved = self.stored_card(card_id)
                self.assertEqual(saved["title"], "SYNTHETIC UPDATED")
                self.assertEqual(saved["vehicle_raw"], "SYNTHETIC SHORT")
                self.assertEqual(saved["description"], "SYNTHETIC DESCRIPTION")
                self.assertEqual(saved["vehicle_profile"]["make_display"], "Land Rover")
                self.assertEqual(saved["vehicle_profile"]["model_display"], "R" * length)
                self.assertEqual(saved["vehicle_profile"]["vin"], "WVWZZZ1KZAW000001")
                if mobile:
                    self.page.locator("#mobileCardBackButton").click()

    def test_desktop_full_form_preserves_long_profile(self):
        self.exercise_save(False)

    def test_mobile_full_form_preserves_long_profile(self):
        self.exercise_save(True)
