"""Assumptions remain visible after saving a client vehicle on disposable storage."""

from tests.browser_ui_support import UiBrowserTestCase


class VehicleTentativeBrowserTests(UiBrowserTestCase):
    def test_card_and_client_display_assumption_after_unrelated_editor_save(self):
        client = self.runtime.service.create_client(
            {
                "display_name": "Synthetic tentative vehicle",
                "vehicles": [
                    {
                        "vehicle": "Synthetic car",
                        "engine_model": "Candidate engine",
                        "tentative_fields": ["engine_model"],
                        "field_sources": {"engine_model": "Synthetic catalog"},
                    }
                ],
            }
        )["client"]
        card_id = self.create_card(
            "Synthetic assumption",
            client_id=client["id"],
            client_vehicle_id=client["vehicles"][0]["id"],
        )
        self.load_board()
        self.open_card(card_id)
        summary = self.page.locator("#vehiclePanelSummary")
        self.assertTrue(summary.is_visible())
        self.assertIn("Предположительно", summary.inner_text())
        self.assertIn("Модель двигателя", summary.inner_text())
        self.page.evaluate("closeCardModal()")
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{client["id"]}"]').click()
        self.page.locator("[data-client-vehicle-edit]").first.click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("Updated synthetic car")
        self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function(
            "!state.clientMutationRequest && !state.clientVehicleEditor?.key"
        )
        self.assertIn(
            "Модель двигателя", self.page.locator("[data-vehicle-tentative]").inner_text()
        )
        vehicle = self.runtime.service.get_client({"client_id": client["id"]})["vehicles"][0]
        self.assertEqual(vehicle["tentative_fields"], ["engine_model"])
        self.assertEqual(vehicle["field_sources"], {"engine_model": "Synthetic catalog"})
