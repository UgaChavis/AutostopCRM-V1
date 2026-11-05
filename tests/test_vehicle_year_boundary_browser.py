"""Editing a card must not remove year-like digits from its vehicle model."""

from tests.browser_ui_support import UiBrowserTestCase


class VehicleYearBoundaryBrowserTests(UiBrowserTestCase):
    def make_card(self, model="S2000"):
        return self.create_card(
            "SYNTHETIC VEHICLE YEAR",
            vehicle_profile={
                "make_display": "Honda",
                "model_display": model,
                "production_year": 2000,
            },
        )

    def test_browser_splitter_requires_a_separate_year_token(self):
        self.load_board()
        for text, model in (
            ("Honda S2000", "S2000"),
            ("Honda S2000 2000", "S2000"),
            ("Honda   S2000   2000", "S2000"),
            ("Honda S2000 2001", "S2000 2001"),
        ):
            with self.subTest(text=text):
                actual = self.page.evaluate("text => splitVehicleDisplayName(text, 2000)", text)
                self.assertEqual(actual, {"make_display": "Honda", "model_display": model})

    def test_desktop_title_only_save_keeps_vehicle_model(self):
        card = self.make_card()
        self.load_board()
        self.open_card(card)
        self.assertEqual(
            self.page.locator('[data-vehicle-field-input="display_name"]').input_value(),
            "Honda S2000",
        )
        self.page.locator("#cardTitle").fill("SYNTHETIC CHANGED TITLE")
        with self.page.expect_request("**/api/update_card") as sent:
            self.page.click("#saveCardButton")
        self.finish_save()
        profile = sent.value.post_data_json["vehicle_profile"]
        self.assertEqual(profile["model_display"], "S2000")
        saved = self.stored_card(card)["vehicle_profile"]
        self.assertEqual(saved["model_display"], "S2000")
        self.assertEqual(saved["production_year"], 2000)

    def test_desktop_manually_entered_name_with_or_without_year(self):
        cases = (
            ("Honda S2000", "S2000"),
            ("Honda S2000 2000", "S2000"),
            ("Honda S2000 2001", "S2000 2001"),
        )
        cards = [self.make_card("Civic") for _ in cases]
        self.load_board()
        for card, (value, expected_model) in zip(cards, cases, strict=True):
            with self.subTest(value=value):
                self.open_card(card)
                self.page.locator('[data-vehicle-field-input="display_name"]').fill(value)
                with self.page.expect_request("**/api/update_card") as sent:
                    self.page.click("#saveCardButton")
                self.finish_save()
                self.assertEqual(
                    sent.value.post_data_json["vehicle_profile"]["model_display"], expected_model
                )
                self.assertEqual(
                    self.stored_card(card)["vehicle_profile"]["model_display"], expected_model
                )

    def test_mobile_manual_name_and_unrelated_save_keep_vehicle_model(self):
        cases = (
            (None, "S2000"),
            ("Honda S2000", "S2000"),
            ("Honda S2000 2000", "S2000"),
            ("Honda S2000 2001", "S2000 2001"),
        )
        cards = [
            self.make_card() if value is None else self.make_card("Civic") for value, _ in cases
        ]
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        for card, (value, expected_model) in zip(cards, cases, strict=True):
            with self.subTest(value=value):
                self.page.locator(f'[data-mobile-card-id="{card}"]').click()
                self.page.wait_for_function(
                    "id => state.mobileCardId === id && !state.mobileCardLoading", arg=card
                )
                if value is None:
                    self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC CHANGED TITLE")
                else:
                    self.page.click('[data-mobile-card-tab="vehicle"]')
                    self.page.locator("#mobileCardVehicleDisplayName").fill(value)
                with self.page.expect_request("**/api/update_card") as sent:
                    self.page.click("#mobileCardSaveButton")
                self.page.wait_for_function("!state.mobileCardSaving")
                self.assertEqual(
                    sent.value.post_data_json["vehicle_profile"]["model_display"], expected_model
                )
                self.assertEqual(
                    self.stored_card(card)["vehicle_profile"]["model_display"], expected_model
                )
                self.page.click("#mobileCardBackButton")
