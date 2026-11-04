"""Client linking must use the observed card version."""

from tests.browser_ui_support import UiBrowserTestCase


class CardClientLinkRevisionBrowserTests(UiBrowserTestCase):
    def test_missing_card_revision_does_not_make_created_client_link_uncertain(self):
        card_id = self.create_card("SYNTHETIC CLIENT CREATION CARD")
        self.load_board()
        self.open_card(card_id)
        self.page.evaluate("state.activeCard.updated_at = ''; openCardClientCreateModal()")
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC CREATED CLIENT")
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("state.cardClientCreateSaving === false")
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.writes.count("link_card_to_client"), 0)
        self.assertTrue(self.page.evaluate("Boolean(state.cardClientCreateCheckpoint.clientId)"))
        self.assertEqual(self.page.evaluate("state.cardClientCreateCheckpoint.uncertain"), "")
        self.assertEqual(
            self.page.locator("#cardClientCreateNameInput").input_value(),
            "SYNTHETIC CREATED CLIENT",
        )
        self.assertIn("НЕТ ВЕРСИИ КАРТОЧКИ", self.page.locator("#statusLine").inner_text())
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("state.cardClientCreateSaving === false")
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.writes.count("link_card_to_client"), 0)
        self.assertEqual(self.page.evaluate("state.cardClientCreateCheckpoint.uncertain"), "")

    def test_stale_suggestion_keeps_draft_and_remote_vehicle(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC CLIENT"})["client"]
        vehicle = self.runtime.service.upsert_client_vehicle(
            {
                "client_id": client["id"],
                "vehicle": {"vehicle": "SYNTHETIC VEHICLE", "license_plate": "A111AA"},
            }
        )["vehicle"]
        card_id = self.create_card(
            "SYNTHETIC ORIGINAL", vehicle_profile={"registration_plate": "A111AA"}
        )
        profile = self.runtime.service.get_client({"client_id": client["id"], "order_limit": 5})
        self.load_board()
        self.open_card(card_id)
        version = self.page.evaluate("state.activeCard.updated_at")
        self.page.locator("#cardTitle").fill("SYNTHETIC UNSAVED DRAFT")
        remote = self.runtime.service.update_card(
            {
                "card_id": card_id,
                "expected_updated_at": version,
                "vehicle_profile": {"registration_plate": "B222BB"},
            }
        )["card"]
        self.assertNotEqual(remote["updated_at"], version)
        self.page.evaluate(
            """profile => {
                state.clientSuggestions = [profile.client];
                state.clientSuggestionProfiles = {[profile.client.id]: profile};
            }""",
            profile,
        )
        select = """async id => {
            const profile = state.clientSuggestionProfiles[id];
            const vehicle = profile.vehicles.find(item => !item.card_id);
            await chooseClientSuggestion(id, clientSuggestionVehicleKey(vehicle));
        }"""
        with self.page.expect_response("**/api/link_card_to_client") as response:
            self.page.evaluate(select, client["id"])
        self.assertEqual(response.value.status, 409)
        self.assertEqual(response.value.request.post_data_json["expected_card_updated_at"], version)
        self.assertEqual(
            self.stored_card(card_id)["vehicle_profile"]["registration_plate"], "b222bb"
        )
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC UNSAVED DRAFT")
        self.assertEqual(self.page.evaluate("state.pendingCardClientId"), client["id"])
        self.assertEqual(self.page.evaluate("state.pendingCardClientVehicleId"), vehicle["id"])
        self.assertEqual(self.writes.count("link_card_to_client"), 1)

        self.page.evaluate("state.activeCard.updated_at = ''")
        self.page.evaluate(
            """profile => {
                state.clientSuggestions = [profile.client];
                state.clientSuggestionProfiles = {[profile.client.id]: profile};
            }""",
            profile,
        )
        self.page.evaluate(select, client["id"])
        self.assertEqual(self.writes.count("link_card_to_client"), 1)
        self.assertIn("НЕТ ВЕРСИИ КАРТОЧКИ", self.page.locator("#statusLine").inner_text())
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC UNSAVED DRAFT")

    def test_link_after_own_card_create_uses_created_revision(self):
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("SYNTHETIC NEW CARD DRAFT")
        self.page.evaluate(
            "id => applyClientSuggestionToVehicleProfile({id, display_name:'SYNTHETIC CLIENT'})",
            self.runtime.client_id,
        )
        requests = []
        observed = []

        def concurrent_change(route):
            body = route.request.post_data_json
            requests.append(body)
            saved = self.runtime.service.get_card({"card_id": body["card_id"]})["card"]
            observed.append(saved["updated_at"])
            self.runtime.service.update_card(
                {"card_id": body["card_id"], "title": "SYNTHETIC REMOTE EDIT"}
            )
            route.continue_()

        self.page.route("**/api/link_card_to_client", concurrent_change)
        with self.page.expect_response("**/api/link_card_to_client") as response:
            self.page.locator("#saveCardButton").click()
        self.assertEqual(response.value.status, 409)
        self.page.wait_for_function("!state.cardSaveInFlight")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["expected_card_updated_at"], observed[0])
        self.assertEqual(self.writes.count("create_card"), 1)
        saved_id = requests[0]["card_id"]
        card = self.stored_card(saved_id)
        self.assertEqual(card["title"], "SYNTHETIC REMOTE EDIT")
        self.assertFalse(card["client_id"])
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC NEW CARD DRAFT")
        self.assertTrue(self.page.locator("#cardModal").is_visible())
