"""Editing a card-derived vehicle must not silently create a separate copy."""

from tests.browser_ui_support import UiBrowserTestCase
from tests.client_vehicle_browser_support import await_client_vehicle_action


class ClientVehicleCardSourceBrowserTests(UiBrowserTestCase):
    def open_candidate(self, *, second=False):
        service = self.runtime.service
        client = service.create_client({"display_name": "SYNTHETIC CLIENT"})["client"]
        card = service.create_card(
            {
                "title": "SYNTHETIC CARD",
                "client_id": client["id"],
                "vehicle": "SYNTHETIC CAR",
                "vehicle_profile": {
                    "vin": "JTM12345678901234",
                    "registration_plate": "A123AA77",
                    "production_year": 2019,
                },
            }
        )["card"]
        other = None
        if second:
            other = service.create_card(
                {
                    "title": "SECOND SYNTHETIC CARD",
                    "client_id": client["id"],
                    "vehicle": "SYNTHETIC CAR",
                    "vehicle_profile": {
                        "vin": "JTM12345678901234",
                        "registration_plate": "A123AA77",
                        "production_year": 2020,
                    },
                }
            )["card"]
        self.load_board()
        self.page.click("#clientsButton")
        self.page.locator(f'#clientsList [data-client-id="{client["id"]}"]').click()
        self.page.wait_for_function(
            "id => state.clientsDesktopProfile?.client?.id === id", arg=client["id"]
        )
        self.page.locator(f'[data-client-vehicle-edit="c:{card["id"]}"]').click()
        self.page.locator('[data-client-vehicle-field="license_plate"]').fill("B234BB77")
        return client, card, other

    def start_previous_write(self, card_id, *, uncertain):
        # Simulate a write already in flight before this guard was installed.
        return self.page.evaluate(
            """async ({cardId, uncertain}) => {
              const clientId = state.clientsActiveId;
              const candidate = findActiveClientVehicle('c:' + cardId);
              const path = '/api/upsert_client_vehicle';
              const body = {client_id: clientId,
                vehicle: readClientVehicleEditorPayload('c:' + cardId), sync_linked_cards: true,
                expected_client_updated_at: state.clientVehicleEditor.expectedClientUpdatedAt};
              const effect = beginCandidateVehicleCreation(clientId, candidate);
              const result = await api(path, {method: 'POST', body});
              const ownerKey = JSON.stringify([state.viewerStateGeneration, state.operatorSessionToken,
                clientId, path, '__new__']);
              state.clientVehicleMutationCheckpoint = {ownerKey, body, effect, uncertain,
                ...(uncertain ? {} : {confirmedResult: result})};
            }""",
            {"cardId": card_id, "uncertain": uncertain},
        )

    def test_candidate_edit_preserves_input_without_write(self):
        client, card, _ = self.open_candidate()
        await_client_vehicle_action(self.page)
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 0)
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="license_plate"]').input_value(),
            "B234BB77",
        )
        self.assertIn(
            "ИЗМЕНИТЕ АВТОМОБИЛЬ В СВЯЗАННОЙ КАРТОЧКЕ",
            self.page.locator("#statusLine").inner_text(),
        )
        self.assertEqual(
            self.runtime.service.get_client({"client_id": client["id"]})["client"]["vehicles"], []
        )
        current = self.runtime.service.get_card({"card_id": card["id"]})["card"]
        self.assertEqual(current["vehicle_profile"]["registration_plate"].lower(), "a123aa77")

    def test_uncertain_candidate_can_be_checked_without_second_post(self):
        _, card, _ = self.open_candidate()

        self.start_previous_write(card["id"], uncertain=True)
        self.assertTrue(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        with self.page.expect_response("**/api/get_client?**"):
            await_client_vehicle_action(self.page)
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 1)
        self.assertTrue(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="license_plate"]').input_value(),
            "B234BB77",
        )
        self.assertIn("НЕ ПОДТВЕРЖДЁН", self.page.locator("#statusLine").inner_text())

    def test_confirmed_candidate_can_be_refreshed_without_second_post(self):
        client, card, _ = self.open_candidate()
        self.start_previous_write(card["id"], uncertain=False)
        self.assertTrue(
            self.page.evaluate("Boolean(state.clientVehicleMutationCheckpoint.confirmedResult)")
        )
        with self.page.expect_response("**/api/get_client?**"):
            await_client_vehicle_action(self.page)
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 1)
        self.assertIsNone(self.page.evaluate("state.clientVehicleEditor"))
        self.assertEqual(
            len(self.runtime.service.get_client({"client_id": client["id"]})["client"]["vehicles"]),
            1,
        )

    def test_other_candidate_cannot_reuse_confirmed_receipt_with_same_fields(self):
        client, card, other = self.open_candidate(second=True)
        self.start_previous_write(card["id"], uncertain=False)
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator("[data-client-vehicle-cancel]").click()
        self.page.locator(f'[data-client-vehicle-edit="c:{other["id"]}"]').click()
        self.page.locator('[data-client-vehicle-field="license_plate"]').fill("B234BB77")
        reads = []
        self.page.on(
            "request",
            lambda request: (
                reads.append(request.url) if "/api/get_client?" in request.url else None
            ),
        )
        await_client_vehicle_action(self.page)
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 1)
        self.assertEqual(reads, [])
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="license_plate"]').input_value(),
            "B234BB77",
        )
        self.assertIn("ДРУГОМУ АВТОМОБИЛЮ", self.page.locator("#statusLine").inner_text())
        self.assertTrue(
            self.page.evaluate("Boolean(state.clientVehicleMutationCheckpoint.confirmedResult)")
        )
        self.assertEqual(
            len(self.runtime.service.get_client({"client_id": client["id"]})["client"]["vehicles"]),
            1,
        )
