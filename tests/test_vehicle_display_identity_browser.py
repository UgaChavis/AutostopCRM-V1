"""Unedited combined names must preserve the original make/model boundary."""

from tests.browser_ui_support import UiBrowserTestCase


class VehicleDisplayIdentityBrowserTests(UiBrowserTestCase):
    def exercise_existing(self, mobile):
        scenarios = (
            (None, None, ("Land Rover", "Range Rover")),
            (None, "2021", ("Land Rover", "Range Rover")),
            ("Honda Civic", None, ("Honda", "Civic")),
            ("", None, ("", "")),
        )
        cards = [
            self.create_card(
                "SYNTHETIC IDENTITY",
                vehicle_profile={
                    "make_display": "Land Rover",
                    "model_display": "Range Rover",
                    "production_year": 2020,
                },
            )
            for _ in scenarios
        ]
        if mobile:
            self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        for card, (name, year, expected) in zip(cards, scenarios, strict=True):
            with self.subTest(mobile=mobile, name=name, year=year):
                if mobile:
                    self.page.locator(f'[data-mobile-card-id="{card}"]').click()
                    self.page.wait_for_function(
                        "id => state.mobileCardId === id && !state.mobileCardLoading", arg=card
                    )
                    self.assertFalse(
                        self.page.evaluate(
                            "mobileCardDraftSnapshot() !== state.mobileCardDraftBaseline"
                        )
                    )
                    self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC UPDATED")
                    if name is not None or year is not None:
                        self.page.click('[data-mobile-card-tab="vehicle"]')
                        if name is not None:
                            self.page.locator("#mobileCardVehicleDisplayName").fill(name)
                        if year is not None:
                            self.page.locator("#mobileCardVehicleYear").fill(year)
                    with self.page.expect_request("**/api/update_card") as sent:
                        self.page.click("#mobileCardSaveButton")
                    self.page.wait_for_function("!state.mobileCardSaving")
                else:
                    self.open_card(card)
                    self.assertFalse(self.page.evaluate("cardModalHasUnsavedChanges()"))
                    self.page.locator("#cardTitle").fill("SYNTHETIC UPDATED")
                    if name is not None:
                        self.page.locator('[data-vehicle-field-input="display_name"]').fill(name)
                    if year is not None:
                        self.page.locator('[data-vehicle-field-input="production_year"]').fill(year)
                    with self.page.expect_request("**/api/update_card") as sent:
                        self.page.click("#saveCardButton")
                    self.finish_save()
                payload = sent.value.post_data_json["vehicle_profile"]
                saved = self.stored_card(card)["vehicle_profile"]
                self.assertEqual((saved["make_display"], saved["model_display"]), expected)
                self.assertIn("display_name", payload)
                if year is not None:
                    self.assertEqual(saved["production_year"], 2021)
                if mobile:
                    self.page.click("#mobileCardBackButton")

    def test_desktop_existing(self):
        self.exercise_existing(False)

    def test_mobile_existing(self):
        self.exercise_existing(True)

    def test_desktop_edited_then_restored_and_new_card(self):
        card = self.create_card(
            "SYNTHETIC IDENTITY",
            vehicle_profile={
                "make_display": "Land Rover",
                "model_display": "Range Rover",
                "production_year": 2020,
            },
        )
        self.load_board()
        self.open_card(card)
        name = self.page.locator('[data-vehicle-field-input="display_name"]')
        original = name.input_value()
        name.fill("Honda Civic")
        self.assertTrue(self.page.evaluate("cardModalHasUnsavedChanges()"))
        name.fill(original)
        self.page.locator("#cardTitle").fill("SYNTHETIC UPDATED")
        with self.page.expect_request("**/api/update_card"):
            self.page.click("#saveCardButton")
        self.finish_save()
        saved = self.stored_card(card)["vehicle_profile"]
        self.assertEqual(
            (saved["make_display"], saved["model_display"]), ("Land Rover", "Range Rover")
        )
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("SYNTHETIC CREATE")
        self.page.locator('[data-vehicle-field-input="display_name"]').fill("Honda Civic")
        with self.page.expect_request("**/api/create_card") as sent:
            self.page.click("#saveCardButton")
        self.finish_save()
        profile = sent.value.post_data_json["vehicle_profile"]
        self.assertEqual((profile["make_display"], profile["model_display"]), ("Honda", "Civic"))

    def test_link_vehicle_keeps_updated_profile_and_unrelated_draft(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC CLIENT"})["client"]
        vehicle = self.runtime.service.upsert_client_vehicle(
            {
                "client_id": client["id"],
                "vehicle": {
                    "vehicle": "Toyota Camry",
                    "brand": "Toyota",
                    "model": "Camry",
                },
            }
        )["vehicle"]
        card = self.create_card("SYNTHETIC CARD")
        self.load_board()
        self.open_card(card)
        self.page.locator("#cardTitle").fill("SYNTHETIC UNSAVED DRAFT")
        result = self.page.evaluate(
            """async args => {
            const context = beginCardScopedRequest('cardClientMutationRequest', {allowNew:true});
            try {
                const module = await ensureBoardModule('card_save');
                const result = await module.linkActiveCardToClient(args.client, {clientVehicleId: args.vehicle, context});
                return {ok: Boolean(result), card: result?.card};
            } finally { finishCardScopedRequest(context); }
        }""",
            {"client": client["id"], "vehicle": vehicle["id"]},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC UNSAVED DRAFT")
        self.assertEqual(
            self.page.locator('[data-vehicle-field-input="display_name"]').input_value(),
            "Toyota Camry",
        )
        with self.page.expect_request("**/api/update_card"):
            self.page.click("#saveCardButton")
        self.finish_save()
        saved = self.stored_card(card)
        self.assertEqual(saved["title"], "SYNTHETIC UNSAVED DRAFT")
        self.assertEqual(
            (saved["vehicle_profile"]["make_display"], saved["vehicle_profile"]["model_display"]),
            ("Toyota", "Camry"),
        )

    def test_mobile_new_card_uses_entered_name(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator("#mobileCardCreateButton").click()
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC MOBILE CREATE")
        self.page.click('[data-mobile-card-tab="vehicle"]')
        self.page.locator("#mobileCardVehicleDisplayName").fill("Honda Civic")
        with self.page.expect_request("**/api/create_card") as sent:
            self.page.click("#mobileCardSaveButton")
        self.page.wait_for_function(
            "!state.mobileCardSaving && !state.mobileCardCreating && !!state.mobileCardId"
        )
        payload = sent.value.post_data_json["vehicle_profile"]
        self.assertEqual((payload["make_display"], payload["model_display"]), ("Honda", "Civic"))
        saved = self.stored_card(self.page.evaluate("state.mobileCardId"))["vehicle_profile"]
        self.assertEqual((saved["make_display"], saved["model_display"]), ("Honda", "Civic"))

    def test_create_with_name_retries_rejected_client_link_without_second_create(self):
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("SYNTHETIC CREATE LINK")
        self.page.locator('[data-vehicle-field-input="display_name"]').fill("Honda Civic")
        self.page.evaluate(
            "id => applyClientSuggestionToVehicleProfile({id, display_name: 'SYNTHETIC CLIENT'})",
            self.runtime.client_id,
        )
        count = []

        def reject_once(route):
            count.append(1)
            if len(count) == 1:
                route.fulfill(
                    status=422,
                    json={
                        "ok": False,
                        "error": {"code": "validation_error", "message": "SYNTHETIC"},
                    },
                )
            else:
                route.continue_()

        self.page.route("**/api/link_card_to_client", reject_once)
        self.page.locator("#saveCardButton").click()
        self.page.wait_for_function("!state.cardSaveInFlight && !!state.editingId")
        self.assertEqual(self.writes.count("create_card"), 1)
        self.assertFalse(self.page.evaluate("cardModalHasUnsavedChanges()"))
        self.page.locator("#saveCardButton").click()
        self.page.wait_for_function("!state.cardSaveInFlight")
        self.assertEqual(self.writes.count("create_card"), 1)
        self.assertEqual(len(count), 2)
        self.assertEqual(self.writes.count("update_card"), 0)

    def test_make_only_is_preserved_on_desktop_and_mobile(self):
        cards = [
            self.create_card(
                "SYNTHETIC MAKE ONLY",
                vehicle_profile={
                    "make_display": "Land Rover",
                    "model_display": "",
                    "production_year": 2020,
                },
            )
            for _ in range(2)
        ]
        self.load_board()
        self.open_card(cards[0])
        self.page.locator("#cardTitle").fill("SYNTHETIC UPDATED")
        self.page.click("#saveCardButton")
        self.finish_save()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.locator(f'[data-mobile-card-id="{cards[1]}"]').click()
        self.page.wait_for_function(
            "id => state.mobileCardId === id && !state.mobileCardLoading", arg=cards[1]
        )
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC UPDATED")
        self.page.click("#mobileCardSaveButton")
        self.page.wait_for_function("!state.mobileCardSaving")
        for card in cards:
            saved = self.stored_card(card)["vehicle_profile"]
            self.assertEqual((saved["make_display"], saved["model_display"]), ("Land Rover", ""))
