"""Do not discard invalid vehicle numbers in a disposable CRM."""

from tests.browser_ui_support import UiBrowserTestCase


class CardVehicleNumericGuardBrowserTests(UiBrowserTestCase):
    def test_desktop_invalid_numbers_and_boundary(self):
        card = self.create_card(
            "Numeric desktop", vehicle_profile={"mileage": 123, "production_year": 2021}
        )
        self.load_board()
        self.open_card(card)
        self.page.locator("#cardTitle").fill("Unsaved title")
        for field, values in (
            ("mileage", ("123.5", "1e2", "-1", "1000000001", "1000000000000")),
            ("production_year", ("2021.5", "1e2", "0", "-1", "1000000001")),
        ):
            control = self.page.locator("#vehicleField_" + field)
            original = control.input_value()
            for value in values:
                control.fill(value)
                writes = self.writes.count("update_card")
                self.page.locator("#saveCardButton").click()
                self.assertEqual(self.writes.count("update_card"), writes)
                self.assertEqual(control.input_value(), value)
                self.assertEqual(self.page.locator("#cardTitle").input_value(), "Unsaved title")
                self.assertIn(
                    "ПРОБЕГ" if field == "mileage" else "ГОД",
                    self.page.locator("#statusLine").inner_text(),
                )
            control.fill(original)
        mileage = self.page.locator("#vehicleField_mileage")
        mileage.fill("")
        mileage.press_sequentially("-")
        self.assertTrue(mileage.evaluate("e => e.validity.badInput"))
        self.page.locator("#saveCardButton").click()
        self.assertEqual(self.writes.count("update_card"), 0)
        self.assertTrue(mileage.evaluate("e => e.validity.badInput"))
        mileage.fill("1000000000")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assertEqual(self.stored_card(card)["vehicle_profile"]["mileage"], 1000000000)

    def test_mobile_invalid_draft_and_clear(self):
        card = self.create_card(
            "Numeric mobile", vehicle_profile={"mileage": 123, "production_year": 2021}
        )
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator(f'[data-mobile-card-id="{card}"]').click()
        self.page.wait_for_function("state.mobileCardId && !state.mobileCardLoading")
        self.page.click('[data-mobile-card-tab="vehicle"]')
        mileage = self.page.locator("#mobileCardVehicleMileage")
        year = self.page.locator("#mobileCardVehicleYear")
        for control, value in ((mileage, "1e2"), (year, "2021.5")):
            original = control.input_value()
            control.fill(value)
            self.page.locator("#mobileCardSaveButton").click()
            self.assertEqual(self.writes.count("update_card"), 0)
            self.assertEqual(control.input_value(), value)
            control.fill(original)
        mileage.fill("")
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertIsNone(self.stored_card(card)["vehicle_profile"]["mileage"])

    def test_new_card_order_and_client_selection_keep_invalid_draft(self):
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("Numeric new card")
        self.page.locator("#vehicleField_display_name").fill("Synthetic vehicle")
        mileage = self.page.locator("#vehicleField_mileage")
        mileage.fill("123.5")
        self.page.evaluate(
            "state.clientSuggestions = [{id: 'synthetic', display_name: 'Synthetic'}]"
        )
        self.page.evaluate("chooseClientSuggestion('synthetic')")
        self.assertEqual(mileage.input_value(), "123.5")
        self.assertEqual(self.page.evaluate("state.pendingCardClientId"), "")
        self.page.locator("#repairOrderButton").click()
        self.page.locator("#repairOrderModal.is-open").wait_for()
        self.page.locator("#repairOrderClient").fill("Order draft")
        self.page.locator("#repairOrderSaveButton").click()
        self.page.wait_for_function("!state.repairOrderSaveInFlight")
        self.assertNotIn("create_card", self.writes)
        self.assertNotIn("update_repair_order", self.writes)
        self.assertEqual(mileage.input_value(), "123.5")
        self.assertEqual(self.page.locator("#repairOrderClient").input_value(), "Order draft")

    def test_client_create_with_vehicle_stops_before_first_write(self):
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("Numeric client card")
        self.page.locator("#vehicleField_display_name").fill("Synthetic vehicle")
        mileage = self.page.locator("#vehicleField_mileage")
        mileage.fill("123.5")
        self.page.locator("[data-open-card-client-create]").click()
        self.page.locator("#cardClientCreateNameInput").fill("Synthetic client")
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertNotIn("create_client", self.writes)
        self.assertNotIn("upsert_client_vehicle", self.writes)
        self.assertEqual(mileage.input_value(), "123.5")
        self.assertEqual(
            self.page.locator("#cardClientCreateNameInput").input_value(), "Synthetic client"
        )
        self.page.locator("#cardClientCreateVehicleInput").uncheck()
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertNotIn("upsert_client_vehicle", self.writes)

    def test_existing_card_cannot_edit_numbers_while_link_is_in_flight(self):
        card = self.create_card("Numeric link", vehicle_profile={"mileage": 123})
        client = self.runtime.service.create_client({"display_name": "Synthetic link"})["client"]
        self.load_board()
        self.open_card(card)
        held = []

        def hold_link(route):
            held.append((route, route.fetch()))

        self.page.route("**/api/link_card_to_client", hold_link)
        self.page.evaluate("client => { state.clientSuggestions = [client]; }", client)
        self.page.evaluate("id => { void chooseClientSuggestion(id); }", client["id"])
        self.page.wait_for_timeout(300)
        self.assertEqual(len(held), 1)
        self.assertTrue(self.page.locator("#cardModal").evaluate("e => e.inert"))
        self.assertTrue(self.page.locator("#vehicleField_mileage").is_disabled())
        route, response = held.pop()
        route.fulfill(response=response)
        self.page.wait_for_function("!state.cardClientMutationRequest")
        self.assertFalse(self.page.locator("#cardModal").evaluate("e => e.inert"))
        self.assertFalse(self.page.locator("#vehicleField_mileage").is_disabled())
        self.assertEqual(self.page.locator("#vehicleField_mileage").input_value(), "123")

    def test_link_failure_releases_the_lock(self):
        card = self.create_card("Numeric link failure", vehicle_profile={"mileage": 123})
        client = self.runtime.service.create_client({"display_name": "Synthetic failure"})["client"]
        self.load_board()
        self.open_card(card)
        self.page.evaluate("client => { state.clientSuggestions = [client]; }", client)
        self.page.route("**/api/link_card_to_client", lambda route: route.abort("failed"))
        self.page.evaluate("id => chooseClientSuggestion(id)", client["id"])
        self.assertFalse(self.page.locator("#cardModal").evaluate("e => e.inert"))
        self.assertFalse(self.page.locator("#vehicleField_mileage").is_disabled())
        self.assertEqual(self.page.locator("#vehicleField_mileage").input_value(), "123")

    def test_viewer_reset_during_link_releases_the_lock(self):
        card = self.create_card("Numeric link reset", vehicle_profile={"mileage": 123})
        client = self.runtime.service.create_client({"display_name": "Synthetic reset"})["client"]
        self.load_board()
        self.open_card(card)
        held = []

        def hold_link(route):
            held.append((route, route.fetch()))

        self.page.route("**/api/link_card_to_client", hold_link)
        self.page.evaluate("client => { state.clientSuggestions = [client]; }", client)
        self.page.evaluate("id => { void chooseClientSuggestion(id); }", client["id"])
        self.page.wait_for_timeout(300)
        self.assertEqual(len(held), 1)
        self.assertTrue(self.page.locator("#cardModal").evaluate("e => e.inert"))
        self.page.evaluate("state.viewerStateGeneration += 1; resetCardModalState()")
        self.assertFalse(self.page.locator("#cardModal").evaluate("e => e.inert"))
        route, response = held.pop()
        route.fulfill(response=response)
        self.page.wait_for_timeout(100)
        self.assertIsNone(self.page.evaluate("state.activeCard"))

    def test_lazy_card_save_module_failure_releases_the_lock(self):
        card = self.create_card("Numeric loader", vehicle_profile={"mileage": 123})
        client = self.runtime.service.create_client({"display_name": "Synthetic loader"})["client"]
        self.load_board()
        module = self.page.evaluate("BOARD_MODULE_MANIFEST.card_save")
        self.page.route("**" + module, lambda route: route.abort("failed"))
        self.open_card(card)
        self.page.evaluate("client => { state.clientSuggestions = [client]; }", client)
        self.page.evaluate("id => chooseClientSuggestion(id)", client["id"])
        self.assertFalse(self.page.locator("#cardModal").evaluate("e => e.inert"))
        self.assertFalse(self.page.locator("#vehicleField_mileage").is_disabled())
        self.assertEqual(self.page.locator("#vehicleField_mileage").input_value(), "123")
        self.assertNotIn("link_card_to_client", self.writes)
        self.assertTrue(self.page.locator("#statusLine").inner_text())

    def test_replacement_card_is_unlocked_during_old_link(self):
        card_a = self.create_card("Numeric link A", vehicle_profile={"mileage": 123})
        card_b = self.create_card("Numeric link B", vehicle_profile={"mileage": 456})
        client = self.runtime.service.create_client({"display_name": "Synthetic switch"})["client"]
        self.load_board()
        self.open_card(card_a)
        held = []

        def hold_link(route):
            held.append((route, route.fetch()))

        self.page.route("**/api/link_card_to_client", hold_link)
        self.page.evaluate("client => { state.clientSuggestions = [client]; }", client)
        self.page.evaluate("id => { void chooseClientSuggestion(id); }", client["id"])
        self.page.wait_for_timeout(300)
        self.assertEqual(len(held), 1)
        replacement = self.stored_card(card_b)
        self.page.evaluate("card => { closeCardModal(); openCardModal(card); }", replacement)
        self.assertEqual(self.page.evaluate("state.editingId"), card_b)
        self.assertFalse(self.page.locator("#cardModal").evaluate("e => e.inert"))
        self.page.locator("#vehicleField_mileage").fill("789")
        route, response = held.pop()
        route.fulfill(response=response)
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate("state.editingId"), card_b)
        self.assertEqual(self.page.locator("#vehicleField_mileage").input_value(), "789")
        self.assertFalse(self.page.locator("#cardModal").evaluate("e => e.inert"))
