"""A chosen vehicle retains its make and model when a later link conflicts."""

from tests.browser_ui_support import UiBrowserTestCase


class ChosenVehicleIdentityBrowserTests(UiBrowserTestCase):
    def exercise(self, name_edits, expected):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC CHOSEN CLIENT"})[
            "client"
        ]
        vehicle = self.runtime.service.upsert_client_vehicle(
            {
                "client_id": client["id"],
                "vehicle": {
                    "vehicle": "Land Rover Range Rover",
                    "brand": "Land Rover",
                    "model": "Range Rover",
                    "year": 2020,
                },
            }
        )["vehicle"]
        profile = self.runtime.service.get_client({"client_id": client["id"], "order_limit": 5})
        self.assertEqual((vehicle["brand"], vehicle["model"]), ("Land Rover", "Range Rover"))
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("SYNTHETIC NEW CARD")
        self.page.evaluate(
            """profile => {
            state.clientSuggestions = [profile.client];
            state.clientSuggestionProfiles = {[profile.client.id]: profile};
        }""",
            profile,
        )
        self.page.evaluate(
            """async args => {
            const vehicle = state.clientSuggestionProfiles[args.client].vehicles.find(v => v.id === args.vehicle);
            await chooseClientSuggestion(args.client, clientSuggestionVehicleKey(vehicle));
        }""",
            {"client": client["id"], "vehicle": vehicle["id"]},
        )
        name = self.page.locator('[data-vehicle-field-input="display_name"]')
        self.assertEqual(name.input_value(), "Land Rover Range Rover")
        for edit in name_edits:
            name.fill(edit)
            # Dirty calculation reads and normalizes the profile while editing.
            self.page.evaluate("cardModalHasUnsavedChanges()")

        def concurrent_update(route):
            self.runtime.service.update_card(
                {
                    "card_id": route.request.post_data_json["card_id"],
                    "title": "SYNTHETIC OTHER OPERATOR",
                }
            )
            route.continue_()

        self.page.route("**/api/link_card_to_client", concurrent_update)
        with self.page.expect_request("**/api/create_card") as sent:
            with self.page.expect_response("**/api/link_card_to_client") as linked:
                self.page.locator("#saveCardButton").click()
        self.page.wait_for_function("!state.cardSaveInFlight")
        self.assertEqual(linked.value.status, 409)
        sent_profile = sent.value.post_data_json["vehicle_profile"]
        card_id = sent.value.response().json()["data"]["card"]["id"]
        saved = self.stored_card(card_id)["vehicle_profile"]
        if name_edits != [""]:
            self.assertEqual(
                (sent_profile["make_display"], sent_profile["model_display"]), expected
            )
        self.assertEqual((saved["make_display"], saved["model_display"]), expected)

    def test_selected_name_survives_link_conflict(self):
        self.exercise([], ("Land Rover", "Range Rover"))

    def test_explicit_name_edit_is_saved(self):
        self.exercise(["Honda Civic"], ("Honda", "Civic"))

    def test_explicit_name_clear_is_saved(self):
        self.exercise([""], ("", ""))

    def test_name_edited_then_restored_retains_original_parts(self):
        self.exercise(["Honda Civic", "Land Rover Range Rover"], ("Land Rover", "Range Rover"))

    def test_switch_vehicle_preview_and_cancel_then_success(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC SWITCH CLIENT"})[
            "client"
        ]
        vehicles = [
            self.runtime.service.upsert_client_vehicle(
                {
                    "client_id": client["id"],
                    "vehicle": {
                        "vehicle": name,
                        "brand": brand,
                        "model": model,
                    },
                }
            )["vehicle"]
            for name, brand, model in (
                ("Land Rover Range Rover", "Land Rover", "Range Rover"),
                ("Alfa Romeo Giulia", "Alfa Romeo", "Giulia"),
            )
        ]
        profile = self.runtime.service.get_client({"client_id": client["id"], "order_limit": 5})
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("SYNTHETIC CANCEL")
        self.page.evaluate(
            """profile => {state.clientSuggestions=[profile.client]; state.clientSuggestionProfiles={[profile.client.id]:profile}}""",
            profile,
        )
        choose = """async args => {const vehicle=state.clientSuggestionProfiles[args.client].vehicles.find(v=>v.id===args.vehicle); await chooseClientSuggestion(args.client, clientSuggestionVehicleKey(vehicle));}"""
        self.page.evaluate(choose, {"client": client["id"], "vehicle": vehicles[0]["id"]})
        self.assertEqual(self.page.evaluate("state.vehicleSelectedNameBaseline.make"), "Land Rover")
        self.page.evaluate("closeCardModal()")
        self.assertIsNone(self.page.evaluate("state.vehicleSelectedNameBaseline"))
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("SYNTHETIC SWITCH")
        self.page.evaluate(
            """profile => {state.clientSuggestions=[profile.client]; state.clientSuggestionProfiles={[profile.client.id]:profile}}""",
            profile,
        )
        self.page.evaluate(choose, {"client": client["id"], "vehicle": vehicles[0]["id"]})
        self.page.evaluate(
            """profile => {state.clientSuggestions=[profile.client]; state.clientSuggestionProfiles={[profile.client.id]:profile}}""",
            profile,
        )
        self.page.evaluate(choose, {"client": client["id"], "vehicle": vehicles[1]["id"]})
        self.assertEqual(self.page.evaluate("state.vehicleSelectedNameBaseline.make"), "Alfa Romeo")
        name = self.page.locator('[data-vehicle-field-input="display_name"]')
        self.page.evaluate(
            "state.vehicleProfileDraft.tentative_fields = ['make_display','model_display']"
        )
        name.fill("Honda Civic")
        self.page.evaluate("cardModalHasUnsavedChanges(); refreshVehiclePanel()")
        self.assertEqual(self.page.evaluate("state.vehicleProfileDraft.make_display"), "Honda")
        self.assertIn("Honda", self.page.locator("#vehiclePanelSummary").inner_text())
        name.fill("Alfa Romeo Giulia")
        with self.page.expect_request("**/api/create_card") as sent:
            with self.page.expect_response("**/api/link_card_to_client") as linked:
                self.page.click("#saveCardButton")
        self.page.wait_for_function("!state.cardSaveInFlight")
        self.assertEqual(linked.value.status, 200)
        vp = sent.value.post_data_json["vehicle_profile"]
        self.assertEqual((vp["make_display"], vp["model_display"]), ("Alfa Romeo", "Giulia"))
        card = sent.value.response().json()["data"]["card"]["id"]
        saved = self.stored_card(card)["vehicle_profile"]
        self.assertEqual((saved["make_display"], saved["model_display"]), ("Alfa Romeo", "Giulia"))
