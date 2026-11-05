"""Unsupported vehicle field clearing must not be reported as saved."""

from tests.browser_ui_support import UiBrowserTestCase
from tests.client_vehicle_browser_support import await_client_vehicle_action


class ClientVehicleClearBrowserTests(UiBrowserTestCase):
    def open_vehicle(self):
        service = self.runtime.service
        client = service.create_client({"display_name": "SYNTHETIC CLEAR " + self._testMethodName})[
            "client"
        ]
        vehicle = service.upsert_client_vehicle(
            {
                "client_id": client["id"],
                "vehicle": {
                    "vehicle": "SYNTHETIC CAR",
                    "vin": "JTM12345678901234",
                    "license_plate": "А123АА77",
                },
            }
        )["vehicle"]
        self.load_board()
        self.page.click("#clientsButton")
        self.page.locator(f'#clientsList [data-client-id="{client["id"]}"]').click()
        self.page.wait_for_function(
            "id => state.clientsDesktopProfile?.client?.id === id", arg=client["id"]
        )
        self.page.locator(f'[data-client-vehicle-edit="v:{vehicle["id"]}"]').click()
        return client, vehicle

    def test_each_unsupported_clear_keeps_all_inputs_without_post(self):
        client, vehicle = self.open_vehicle()
        for field in ("vehicle", "vin", "license_plate"):
            with self.subTest(field=field):
                selector = f'[data-client-vehicle-field="{field}"]'
                original = self.page.locator(selector).input_value()
                self.page.locator(selector).fill("")
                await_client_vehicle_action(self.page)
                self.assertEqual(self.page.locator(selector).input_value(), "")
                self.assertIn(
                    "ОЧИСТКА ПОЛЕЙ АВТОМОБИЛЯ ПОКА НЕДОСТУПНА",
                    self.page.locator("#statusLine").inner_text(),
                )
                self.assertEqual(self.writes.count("upsert_client_vehicle"), 0)
                self.page.locator(selector).fill(original)
        stored = self.runtime.service.get_client({"client_id": client["id"]})["client"]["vehicles"][
            0
        ]
        self.assertEqual(stored["id"], vehicle["id"])
        self.assertEqual(stored["license_plate"], vehicle["license_plate"])

    def test_nonempty_edit_and_new_vehicle_remain_supported(self):
        client, vehicle = self.open_vehicle()
        self.page.locator('[data-client-vehicle-field="license_plate"]').fill("В234ВВ77")
        with self.page.expect_response("**/api/upsert_client_vehicle") as response:
            await_client_vehicle_action(self.page)
        self.assertEqual(response.value.status, 200)
        current = self.runtime.service.get_client({"client_id": client["id"]})["client"]["vehicles"]
        self.assertEqual(current[0]["id"], vehicle["id"])
        self.assertNotEqual(current[0]["license_plate"], vehicle["license_plate"])
        self.page.click("#clientVehicleAddButton")
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC NEW CAR")
        with self.page.expect_response("**/api/upsert_client_vehicle") as response:
            await_client_vehicle_action(self.page)
        self.assertEqual(response.value.status, 200)
        self.assertEqual(
            len(self.runtime.service.get_client({"client_id": client["id"]})["client"]["vehicles"]),
            2,
        )

    def test_pending_write_reads_back_before_rejecting_changed_draft(self):
        client, vehicle = self.open_vehicle()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC FIRST EDIT")

        def lose_write(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        def fail_read(route):
            route.fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC READ FAILURE"}}
            )

        self.page.route("**/api/upsert_client_vehicle", lose_write)
        self.page.route("**/api/get_client?**", fail_read)
        self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function(
            "state.clientVehicleMutationCheckpoint?.uncertain && !state.clientMutationRequest"
        )
        self.page.unroute("**/api/get_client?**", fail_read)
        for field in ("vehicle", "vin", "license_plate"):
            self.page.locator(f'[data-client-vehicle-field="{field}"]').fill("")
        with self.page.expect_response("**/api/get_client?**"):
            await_client_vehicle_action(self.page)
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 1)
        self.assertTrue(
            self.page.evaluate("Boolean(state.clientVehicleMutationCheckpoint.confirmedResult)")
        )
        for field in ("vehicle", "vin", "license_plate"):
            self.assertEqual(
                self.page.locator(f'[data-client-vehicle-field="{field}"]').input_value(), ""
            )
        self.assertIn(
            "ПРЕДЫДУЩАЯ ЗАПИСЬ ПОДТВЕРЖДЕНА", self.page.locator("#statusLine").inner_text()
        )
        current = self.runtime.service.get_client({"client_id": client["id"]})["client"][
            "vehicles"
        ][0]
        self.assertEqual(current["vehicle"], "SYNTHETIC FIRST EDIT")
        self.assertEqual(current["license_plate"], vehicle["license_plate"])
