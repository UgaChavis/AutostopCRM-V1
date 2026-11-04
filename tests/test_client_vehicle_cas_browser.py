"""Client vehicle writes use the version of the profile shown to the operator."""

from tests.browser_ui_support import UiBrowserTestCase


class ClientVehicleCasBrowserTests(UiBrowserTestCase):
    def prepare(self, *, vehicle=True):
        service = self.runtime.service
        client = service.create_client(
            {"display_name": "SYNTHETIC VEHICLE " + self._testMethodName}
        )["client"]
        stored = (
            service.upsert_client_vehicle(
                {
                    "client_id": client["id"],
                    "vehicle": {"vehicle": "SYNTHETIC ORIGINAL", "license_plate": "А123АА77"},
                }
            )["vehicle"]
            if vehicle
            else None
        )
        self.load_board()
        self.page.click("#clientsButton")
        self.page.locator(f'#clientsList [data-client-id="{client["id"]}"]').click()
        self.page.wait_for_function(
            "id => state.clientsDesktopProfile?.client?.id === id", arg=client["id"]
        )
        return client["id"], stored

    def edit(self, vehicle):
        self.page.locator(f'[data-client-vehicle-edit="v:{vehicle["id"]}"]').click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC EDIT A")

    def test_concurrent_vehicle_edit_is_rejected_and_draft_kept(self):
        client_id, vehicle = self.prepare()
        version = self.page.evaluate("state.clientsDesktopProfile.client.updated_at")
        self.edit(vehicle)
        other = self.runtime.service.upsert_client_vehicle(
            {
                "client_id": client_id,
                "client_vehicle_id": vehicle["id"],
                "vehicle": {"license_plate": "В234ВВ77"},
            }
        )["vehicle"]
        latest = self.runtime.service.get_client({"client_id": client_id})["client"]
        self.page.evaluate(
            "version => { state.clientsDesktopProfile.client.updated_at = version; }",
            latest["updated_at"],
        )
        with self.page.expect_response("**/api/upsert_client_vehicle") as response:
            self.page.locator("[data-client-vehicle-save]").click()
        self.assertEqual(response.value.status, 409)
        self.assertEqual(
            response.value.request.post_data_json["expected_client_updated_at"], version
        )
        self.page.wait_for_function("!state.clientMutationRequest")
        current = self.runtime.service.get_client({"client_id": client_id})["client"]["vehicles"][0]
        self.assertEqual(current["license_plate"], other["license_plate"])
        self.assertEqual(current["vehicle"], "SYNTHETIC ORIGINAL")
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
            "SYNTHETIC EDIT A",
        )
        self.assertFalse(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))

    def test_concurrent_delete_is_rejected_without_unlink(self):
        client_id, vehicle = self.prepare()
        version = self.page.evaluate("state.clientsDesktopProfile.client.updated_at")
        other = self.runtime.service.upsert_client_vehicle(
            {
                "client_id": client_id,
                "client_vehicle_id": vehicle["id"],
                "vehicle": {"license_plate": "В234ВВ77"},
            }
        )["vehicle"]
        self.page.once("dialog", lambda dialog: dialog.accept())
        with self.page.expect_response("**/api/delete_client_vehicle") as response:
            self.page.locator(f'[data-client-vehicle-delete="v:{vehicle["id"]}"]').click()
        self.assertEqual(response.value.status, 409)
        self.assertEqual(
            response.value.request.post_data_json["expected_client_updated_at"], version
        )
        self.page.wait_for_function("!state.clientMutationRequest")
        current = self.runtime.service.get_client({"client_id": client_id})["client"]["vehicles"]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["license_plate"], other["license_plate"])
        self.assertFalse(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))

    def test_conflicting_create_clears_marker_and_succeeds_after_explicit_refresh(self):
        client_id, _ = self.prepare(vehicle=False)
        self.page.click("#clientVehicleAddButton")
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC NEW")
        self.runtime.service.update_client({"client_id": client_id, "comment": "OTHER OPERATOR"})
        with self.page.expect_response("**/api/upsert_client_vehicle") as response:
            self.page.locator("[data-client-vehicle-save]").click()
        self.assertEqual(response.value.status, 409)
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
            "SYNTHETIC NEW",
        )
        self.assertEqual(self.page.evaluate("state.clientCandidateVehicleEffects.size"), 0)
        self.assertEqual(
            self.runtime.service.get_client({"client_id": client_id})["client"]["vehicles"], []
        )
        self.page.locator("[data-client-vehicle-cancel]").click()
        self.page.locator(f'#clientsList [data-client-id="{client_id}"]').click()
        self.page.wait_for_function(
            "state.clientsDesktopProfile?.client?.comment === 'OTHER OPERATOR'"
        )
        self.page.click("#clientVehicleAddButton")
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC NEW")
        with self.page.expect_response("**/api/upsert_client_vehicle") as response:
            self.page.locator("[data-client-vehicle-save]").click()
        self.assertEqual(response.value.status, 200)
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(
            len(self.runtime.service.get_client({"client_id": client_id})["client"]["vehicles"]), 1
        )

    def test_confirmed_receipt_retries_read_only_with_original_version(self):
        client_id, _ = self.prepare(vehicle=False)
        self.page.click("#clientVehicleAddButton")
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC RECEIPT")

        def fail_read(route):
            route.fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC READ FAILURE"}}
            )

        self.page.route("**/api/get_client?**", fail_read)
        with self.page.expect_response("**/api/upsert_client_vehicle") as response:
            self.page.locator("[data-client-vehicle-save]").click()
        self.assertEqual(response.value.status, 200)
        self.page.wait_for_function(
            "!state.clientMutationRequest && !!state.clientVehicleMutationCheckpoint?.confirmedResult"
        )
        original_version = response.value.request.post_data_json["expected_client_updated_at"]
        latest = self.runtime.service.get_client({"client_id": client_id})["client"]
        self.assertNotEqual(latest["updated_at"], original_version)
        self.page.evaluate(
            "version => { state.clientsDesktopProfile.client.updated_at = version; }",
            latest["updated_at"],
        )
        self.page.unroute("**/api/get_client?**", fail_read)
        self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest && !state.clientVehicleEditor")
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 1)
        self.assertEqual(
            self.page.evaluate(
                "state.clientVehicleMutationCheckpoint.body.expected_client_updated_at"
            ),
            original_version,
        )
        self.assertEqual(
            len(self.runtime.service.get_client({"client_id": client_id})["client"]["vehicles"]), 1
        )

    def test_missing_revision_blocks_before_create_marker_or_post(self):
        client_id, _ = self.prepare(vehicle=False)
        self.page.evaluate("state.clientsDesktopProfile.client.updated_at = ''")
        self.page.click("#clientVehicleAddButton")
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC NO VERSION")
        self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 0)
        self.assertEqual(self.page.evaluate("state.clientCandidateVehicleEffects.size"), 0)
        self.assertEqual(
            self.runtime.service.get_client({"client_id": client_id})["client"]["vehicles"], []
        )
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
            "SYNTHETIC NO VERSION",
        )

    def test_late_mobile_profile_cannot_replace_open_vehicle_editor(self):
        client_id, vehicle = self.prepare()
        self.page.click('[data-close="clients"]')
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.click('[data-mobile-view="more"]')
        self.page.click('[data-mobile-open="clients"]')
        held = []

        def hold_mobile(route):
            if "order_limit=8" in route.request.url:
                held.append(route)
            else:
                route.continue_()

        self.page.route("**/api/get_client?**", hold_mobile)
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{client_id}"]').click()
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.click("#clientsButton")
        self.page.wait_for_function(
            "id => state.clientsDesktopProfile?.client?.id === id && state.clientsActiveProfile === state.clientsDesktopProfile",
            arg=client_id,
        )
        self.edit(vehicle)
        original_version = self.page.evaluate("state.clientVehicleEditor.expectedClientUpdatedAt")
        held.pop().continue_()
        self.page.wait_for_timeout(150)
        self.assertEqual(
            self.page.evaluate("state.clientVehicleEditor.expectedClientUpdatedAt"),
            original_version,
        )
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
            "SYNTHETIC EDIT A",
        )
        with self.page.expect_response("**/api/upsert_client_vehicle") as response:
            self.page.locator("[data-client-vehicle-save]").click()
        self.assertEqual(response.value.status, 200)
        self.assertEqual(
            response.value.request.post_data_json["expected_client_updated_at"], original_version
        )
