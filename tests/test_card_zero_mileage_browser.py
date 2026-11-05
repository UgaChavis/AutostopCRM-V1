"""A mileage value the server cannot store must remain in the editor."""

from tests.browser_ui_support import UiBrowserTestCase


class CardZeroMileageBrowserTests(UiBrowserTestCase):
    def assert_zero_rejected(self, field, save, write):
        field.fill("0")
        before = self.writes.count(write)
        save.click()
        self.page.wait_for_function(
            "document.querySelector('#statusLine').textContent.includes('НУЛЕВОЙ ПРОБЕГ')"
        )
        self.assertEqual(self.writes.count(write), before)
        self.assertEqual(field.input_value(), "0")

    def test_desktop_create_and_update_keep_zero_until_corrected(self):
        card = self.create_card("SYNTHETIC ZERO ORIGINAL")
        self.load_board()
        self.open_card(card)
        self.page.locator("#cardTitle").fill("SYNTHETIC ZERO DRAFT")
        field = self.page.locator("#vehicleField_mileage")
        self.assert_zero_rejected(field, self.page.locator("#saveCardButton"), "update_card")
        self.assertEqual(self.stored_card(card)["title"], "SYNTHETIC ZERO ORIGINAL")
        field.fill("1")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assertEqual(self.stored_card(card)["vehicle_profile"]["mileage"], 1)

        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("SYNTHETIC ZERO NEW")
        self.assert_zero_rejected(field, self.page.locator("#saveCardButton"), "create_card")
        field.fill("")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assertEqual(self.writes.count("create_card"), 1)

    def test_mobile_create_and_update_keep_zero_until_corrected(self):
        card = self.create_card("SYNTHETIC MOBILE ORIGINAL")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator(f'[data-mobile-card-id="{card}"]').click()
        self.page.wait_for_function(
            "id => state.mobileCardId === id && !state.mobileCardLoading", arg=card
        )
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC MOBILE DRAFT")
        self.page.click('[data-mobile-card-tab="vehicle"]')
        field = self.page.locator("#mobileCardVehicleMileage")
        self.assert_zero_rejected(field, self.page.locator("#mobileCardSaveButton"), "update_card")
        self.assertEqual(self.stored_card(card)["title"], "SYNTHETIC MOBILE ORIGINAL")
        field.fill("1")
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertEqual(self.stored_card(card)["vehicle_profile"]["mileage"], 1)
        self.page.locator("#mobileCardBackButton").click()

        self.page.locator("#mobileCardCreateButton").click()
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC MOBILE NEW")
        self.page.click('[data-mobile-card-tab="vehicle"]')
        self.assert_zero_rejected(field, self.page.locator("#mobileCardSaveButton"), "create_card")
        self.assertIsNone(
            self.page.evaluate("sessionStorage.getItem('autostop:mobile-card-create-pending')")
        )
        field.fill("")
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function(
            "!state.mobileCardSaving && !state.mobileCardCreating && !!state.mobileCardId"
        )
        self.assertEqual(self.writes.count("create_card"), 1)

    def test_creating_order_cannot_create_card_with_zero(self):
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("SYNTHETIC ORDER CARD")
        self.page.locator("#vehicleField_mileage").fill("0")
        self.page.locator("#repairOrderButton").click()
        self.page.locator("#repairOrderModal.is-open").wait_for()
        self.page.locator("#repairOrderClient").fill("SYNTHETIC ORDER DRAFT")
        self.page.locator("#repairOrderSaveButton").click()
        self.page.wait_for_function("!state.repairOrderSaveInFlight")
        self.assertIn("НУЛЕВОЙ ПРОБЕГ", self.page.locator("#statusLine").inner_text())
        self.assertEqual(self.writes.count("create_card"), 0)
        self.assertEqual(self.writes.count("update_repair_order"), 0)
        self.assertEqual(self.page.locator("#vehicleField_mileage").input_value(), "0")
        self.assertEqual(
            self.page.locator("#repairOrderClient").input_value(), "SYNTHETIC ORDER DRAFT"
        )

    def test_client_creation_with_vehicle_is_blocked_before_any_write(self):
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("SYNTHETIC CLIENT CARD")
        self.page.locator("#vehicleField_display_name").fill("SYNTHETIC VEHICLE")
        self.page.locator("#vehicleField_mileage").fill("0")
        self.page.locator("[data-open-card-client-create]").click()
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC CLIENT")
        self.assertTrue(self.page.locator("#cardClientCreateVehicleInput").is_checked())
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertIn("НУЛЕВОЙ ПРОБЕГ", self.page.locator("#statusLine").inner_text())
        self.assertEqual(self.writes.count("create_client"), 0)
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 0)
        self.assertIsNone(
            self.page.evaluate("sessionStorage.getItem('autostop:card-client-create-pending')")
        )
        self.assertEqual(
            self.page.locator("#cardClientCreateNameInput").input_value(), "SYNTHETIC CLIENT"
        )
        self.page.locator("#cardClientCreateVehicleInput").uncheck()
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 0)
        self.assertEqual(self.page.locator("#vehicleField_mileage").input_value(), "0")
