"""Keep overlong vehicle fields in the form instead of truncating them."""

from tests.browser_ui_support import UiBrowserTestCase


class CardVehicleTextGuardBrowserTests(UiBrowserTestCase):
    def test_desktop_five_fields_and_normalized_boundary(self):
        card = self.create_card("SYNTHETIC ORIGINAL")
        self.load_board()
        self.open_card(card)
        self.page.locator("#cardTitle").fill("SYNTHETIC DRAFT")
        for name in (
            "customer_name",
            "registration_plate",
            "engine_model",
            "gearbox_model",
            "drivetrain",
        ):
            field = self.page.locator("#vehicleField_" + name)
            field.fill("🚗" * 121)
            self.page.locator("#saveCardButton").click()
            self.assertEqual(self.writes.count("update_card"), 0, name)
            self.assertEqual(field.input_value(), "🚗" * 121, name)
            self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC DRAFT")
            self.assertIn("120", self.page.locator("#statusLine").inner_text())
            field.fill("")
        self.page.locator("#vehicleField_engine_model").fill("🚗" * 120)
        self.page.locator("#vehicleField_gearbox_model").fill("A" + "\u00a0" * 150 + "B")
        with self.page.expect_response("**/api/update_card") as response:
            self.page.locator("#saveCardButton").click()
        self.assertEqual(response.value.status, 200)
        self.finish_save()
        profile = self.stored_card(card)["vehicle_profile"]
        self.assertEqual(profile["engine_model"], "🚗" * 120)
        self.assertEqual(profile["gearbox_model"], "A B")

    def test_mobile_finds_field_and_preserves_draft(self):
        card = self.create_card("SYNTHETIC MOBILE")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator(f'[data-mobile-card-id="{card}"]').click()
        self.page.wait_for_function(
            "id => state.mobileCardId === id && !state.mobileCardLoading", arg=card
        )
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC MOBILE DRAFT")
        self.page.click('[data-mobile-card-tab="vehicle"]')
        name = self.page.locator("#mobileCardVehicleCustomerName")
        name.fill("N" * 121)
        self.page.click('[data-mobile-card-tab="overview"]')
        self.page.locator("#mobileCardSaveButton").click()
        self.assertEqual(self.writes.count("update_card"), 0)
        self.assertEqual(self.page.evaluate("state.mobileCardTab"), "vehicle")
        self.assertEqual(name.input_value(), "N" * 121)
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE DRAFT"
        )
        name.fill("N" * 120)
        with self.page.expect_response("**/api/update_card") as response:
            self.page.locator("#mobileCardSaveButton").click()
        self.assertEqual(response.value.status, 200)
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertEqual(self.stored_card(card)["vehicle_profile"]["customer_name"], "N" * 120)

    def test_new_card_order_and_client_create_stop_before_write(self):
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("SYNTHETIC NEW")
        self.page.locator("#vehicleField_display_name").fill("SYNTHETIC VEHICLE")
        field = self.page.locator("#vehicleField_engine_model")
        field.fill("E" * 121)
        self.page.evaluate(
            "state.clientSuggestions = [{id: 'synthetic', display_name: 'Synthetic'}]"
        )
        self.page.evaluate("chooseClientSuggestion('synthetic')")
        self.assertEqual(self.page.evaluate("state.pendingCardClientId"), "")
        self.page.locator("#repairOrderButton").click()
        self.page.locator("#repairOrderModal.is-open").wait_for()
        self.page.locator("#repairOrderClient").fill("ORDER DRAFT")
        self.page.locator("#repairOrderSaveButton").click()
        self.page.wait_for_function("!state.repairOrderSaveInFlight")
        self.assertNotIn("create_card", self.writes)
        self.assertNotIn("update_repair_order", self.writes)
        self.assertEqual(field.input_value(), "E" * 121)
        self.assertEqual(self.page.locator("#repairOrderClient").input_value(), "ORDER DRAFT")

    def test_existing_client_link_blocks_before_request(self):
        card = self.create_card("SYNTHETIC LINK")
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC CLIENT"})["client"]
        self.load_board()
        self.open_card(card)
        self.page.locator("#vehicleField_customer_name").fill("N" * 121)
        self.page.evaluate("client => { state.clientSuggestions = [client]; }", client)
        self.page.evaluate("id => chooseClientSuggestion(id)", client["id"])
        self.assertNotIn("link_card_to_client", self.writes)
        self.assertEqual(self.page.locator("#vehicleField_customer_name").input_value(), "N" * 121)
        self.assertEqual(self.stored_card(card)["client_id"], "")

    def test_client_creation_blocks_vehicle_write_but_allows_client_only(self):
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("SYNTHETIC NEW CARD")
        self.page.locator("#vehicleField_display_name").fill("SYNTHETIC VEHICLE")
        field = self.page.locator("#vehicleField_engine_model")
        field.fill("E" * 121)
        self.page.locator("[data-open-card-client-create]").click()
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC CLIENT")
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertNotIn("create_client", self.writes)
        self.assertNotIn("upsert_client_vehicle", self.writes)
        self.assertEqual(field.input_value(), "E" * 121)
        self.assertEqual(
            self.page.locator("#cardClientCreateNameInput").input_value(), "SYNTHETIC CLIENT"
        )
        self.page.locator("#cardClientCreateVehicleInput").uncheck()
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertNotIn("upsert_client_vehicle", self.writes)
        self.assertEqual(field.input_value(), "E" * 121)
