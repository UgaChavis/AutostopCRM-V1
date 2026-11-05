"""Client reads cannot select or confirm a different client."""

import json

from tests.browser_ui_support import UiBrowserTestCase


class ClientProfileIdentityBrowserTests(UiBrowserTestCase):
    def clients(self):
        result = [
            self.runtime.service.create_client({"client_type": "company", "display_name": name})[
                "client"
            ]["id"]
            for name in ("SYNTHETIC CLIENT A", "SYNTHETIC CLIENT B")
        ]
        self.load_board()
        return result

    def test_desktop_wrong_identity_and_partial_response_cannot_enable_write(self):
        first, other = self.clients()
        self.page.locator("#clientsButton").click()
        for mode in ("wrong", "missing_id", "missing_comment"):

            def partial(route):
                response = route.fetch().json()
                if mode == "wrong":
                    response["data"]["client"]["id"] = other
                elif mode == "missing_id":
                    del response["data"]["client"]["id"]
                else:
                    del response["data"]["client"]["comment"]
                route.fulfill(json=response)

            self.page.route("**/api/get_client?**", partial)
            self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
            self.page.wait_for_function(
                "document.querySelector('#statusLine').textContent.includes('ПРОФИЛЬ КЛИЕНТА НЕ ПОДТВЕРЖДЁН')"
            )
            self.assertFalse(self.page.locator("#clientDisplayNameInput").is_visible())
            self.assertTrue(self.page.evaluate("beginClientMutation({allowNew:true}) === null"))
            self.page.unroute("**/api/get_client?**", partial)
            self.page.evaluate("setStatus('', false)")
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "SYNTHETIC CLIENT A"
        )
        self.assertEqual(
            self.runtime.service.get_client({"client_id": other})["client"]["display_name"],
            "SYNTHETIC CLIENT B",
        )

    def test_mobile_wrong_identity_cannot_change_selection(self):
        first, other = self.clients()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.locator('[data-mobile-view="more"]').click()
        self.page.locator('[data-mobile-open="clients"]').click()

        def wrong(route):
            payload = route.fetch().json()
            payload["data"]["client"]["id"] = other
            route.fulfill(json=payload)

        self.page.route("**/api/get_client?**", wrong)
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{first}"]').click()
        self.page.wait_for_function("!state.mobileClientProfileLoading")
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), first)
        self.assertIsNone(self.page.evaluate("state.clientsActiveProfile"))
        self.page.unroute("**/api/get_client?**", wrong)
        self.page.locator(f'#mobileClientsList [data-mobile-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.assertIn("SYNTHETIC CLIENT A", self.page.locator("#mobileClientDetail").inner_text())

    def test_wrong_update_id_keeps_pending_and_prevents_other_client_write(self):
        first, other = self.clients()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("#clientCommentInput").fill("SYNTHETIC EDIT")
        writes = []

        def wrong(route):
            payload = route.fetch().json()
            writes.append(1)
            payload["data"]["client"]["id"] = other
            route.fulfill(json=payload)

        self.page.route("**/api/update_client", wrong)
        self.page.locator("#clientSaveButton").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), first)
        self.assertTrue(self.page.evaluate("state.clientProfileSaveCheckpoint.uncertain"))
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC EDIT")
        self.assertEqual(
            self.runtime.service.get_client({"client_id": other})["client"]["display_name"],
            "SYNTHETIC CLIENT B",
        )
        self.page.unroute("**/api/update_client", wrong)
        self.page.locator("#clientSaveButton").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(writes), 1)
        self.assertFalse(self.page.evaluate("state.clientProfileSaveCheckpoint.uncertain"))

    def test_lost_update_wrong_client_readback_keeps_checkpoint(self):
        first, other = self.clients()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("#clientCommentInput").fill("SYNTHETIC UNCERTAIN")
        writes = []

        def lose(route):
            writes.append(1)
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        def wrong(route):
            payload = route.fetch().json()
            payload["data"]["client"]["id"] = other
            route.fulfill(json=payload)

        self.page.route("**/api/update_client", lose)
        self.page.route("**/api/get_client?**", wrong)
        self.page.locator("#clientSaveButton").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertTrue(self.page.evaluate("state.clientProfileSaveCheckpoint.uncertain"))
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC UNCERTAIN"
        )
        self.page.locator("#clientSaveButton").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(writes), 1)
        self.assertTrue(self.page.evaluate("state.clientProfileSaveCheckpoint.uncertain"))
        self.page.unroute("**/api/get_client?**", wrong)
        self.page.locator("#clientSaveButton").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertFalse(self.page.evaluate("state.clientProfileSaveCheckpoint.uncertain"))
        self.assertEqual(len(writes), 1)

    def test_lost_vehicle_update_wrong_client_readback_cannot_confirm(self):
        first, other = self.clients()
        self.runtime.service.upsert_client_vehicle(
            {"client_id": first, "vehicle": {"vehicle": "SYNTHETIC CAR"}}
        )
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("[data-client-vehicle-edit]").first.click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC CAR EDIT")
        writes = []

        def lose(route):
            writes.append(1)
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        def wrong(route):
            payload = route.fetch().json()
            payload["data"]["client"]["id"] = other
            route.fulfill(json=payload)

        self.page.route("**/api/upsert_client_vehicle", lose)
        self.page.route("**/api/get_client?**", wrong)
        self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertTrue(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
            "SYNTHETIC CAR EDIT",
        )
        self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(writes), 1)
        self.assertTrue(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        self.page.unroute("**/api/get_client?**", wrong)
        self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertFalse(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        self.assertEqual(len(writes), 1)

    def test_confirmed_vehicle_write_does_not_apply_wrong_client_refresh(self):
        first, other = self.clients()
        self.runtime.service.upsert_client_vehicle(
            {"client_id": first, "vehicle": {"vehicle": "SYNTHETIC CAR"}}
        )
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("[data-client-vehicle-edit]").first.click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC REFRESH DRAFT")

        def wrong(route):
            payload = route.fetch().json()
            payload["data"]["client"]["id"] = other
            route.fulfill(json=payload)

        self.page.route("**/api/get_client?**", wrong)
        self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(self.page.evaluate("state.clientsActiveProfile.client.id"), first)
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
            "SYNTHETIC REFRESH DRAFT",
        )
        self.assertEqual(
            self.runtime.service.get_client({"client_id": other})["client"]["display_name"],
            "SYNTHETIC CLIENT B",
        )

    def test_card_client_update_and_readback_wrong_id_remain_uncertain(self):
        first, other = self.clients()
        card = self.create_card("SYNTHETIC CARD FOR CLIENT")
        self.load_board()
        self.open_card(card)
        original = self.runtime.service.get_client({"client_id": first})["client"]
        self.page.evaluate(
            """client => {
            const ownerKey = JSON.stringify([state.viewerStateGeneration, state.operatorSessionToken,
              state.cardEditingGeneration || 0, state.editingId, null]);
            state.cardClientCreateCheckpoint = { ownerKey, clientId: client.id, client, vehicleId: '',
              payload: {}, key: 'previous', uncertain: '', createVehicle: false, linked: true };
        }""",
            original,
        )
        writes = []

        def wrong_update(route):
            payload = route.fetch().json()
            writes.append(1)
            payload["data"]["client"]["id"] = other
            route.fulfill(json=payload)

        def wrong_read(route):
            payload = route.fetch().json()
            payload["data"]["client"]["id"] = other
            route.fulfill(json=payload)

        self.page.route("**/api/update_client", wrong_update)
        self.page.route("**/api/get_client?**", wrong_read)
        result = self.page.evaluate("""async () => createClientForCard(
            {}, {display_name: 'SYNTHETIC CARD EDIT'}, {createVehicleFromCard: false}
        )""")
        self.assertFalse(result)
        self.assertIn("НЕ ПОДТВЕРЖДЁН", self.page.locator("#statusLine").text_content())
        self.assertEqual(len(writes), 1)
        self.assertEqual(self.page.evaluate("state.cardClientCreateCheckpoint.clientId"), first)
        self.assertEqual(self.page.evaluate("state.cardClientCreateCheckpoint.uncertain"), "client")
        self.assertEqual(
            self.runtime.service.get_client({"client_id": other})["client"]["display_name"],
            "SYNTHETIC CLIENT B",
        )

    def test_restricted_operator_opens_full_client_response(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC RESTRICTED"})[
            "client"
        ]["id"]
        response = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "synthetic-profile-viewer",
                "password": "synthetic-profile-password",
                "role": "operator",
            },
        )
        self.assertEqual(response.status, 200)
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "synthetic-profile-viewer", "password": "synthetic-profile-password"},
        ).json()["data"]["session"]["token"]
        context = self.browser.new_context(
            viewport={"width": 1440, "height": 960}, extra_http_headers=self.runtime.auth_headers
        )
        self.addCleanup(context.close)
        context.add_init_script(
            "localStorage.setItem('kanban-operator-session', " + json.dumps(token) + ");"
        )
        self.page = context.new_page()
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientsSearchInput").fill("SYNTHETIC RESTRICTED")
        self.page.locator(f'#clientsList [data-client-id="{client}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client
        )
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "SYNTHETIC RESTRICTED"
        )

    def test_created_vehicle_wrong_refresh_retries_only_read_and_keeps_draft(self):
        first, other = self.clients()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("#clientVehicleAddButton").click()
        field = self.page.locator('[data-client-vehicle-field="vehicle"]')
        field.fill("SYNTHETIC NEW VEHICLE")
        writes = []

        def count_write(route):
            writes.append(route.request.post_data_json)
            route.continue_()

        def wrong_read(route):
            payload = route.fetch().json()
            payload["data"]["client"]["id"] = other
            route.fulfill(json=payload)

        self.page.route("**/api/upsert_client_vehicle", count_write)
        self.page.route("**/api/get_client?**", wrong_read)
        for index, value in enumerate(
            (
                "SYNTHETIC NEW VEHICLE",
                "SYNTHETIC CHANGED VEHICLE",
                "SYNTHETIC NEW VEHICLE",
            )
        ):
            field.fill(value)
            if index == 0:
                with self.page.expect_request("**/api/upsert_client_vehicle"):
                    self.page.locator("[data-client-vehicle-save]").click()
            else:
                self.page.locator("[data-client-vehicle-save]").click()
            self.page.wait_for_function("!state.clientMutationRequest")
            self.assertEqual(len(writes), 1)
            self.assertEqual(field.input_value(), value)
        self.assertEqual(
            len(self.runtime.service.get_client({"client_id": first})["client"]["vehicles"]), 1
        )
        self.page.unroute("**/api/get_client?**", wrong_read)
        with self.page.expect_response("**/api/get_client?**"):
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(writes), 1)
        self.assertEqual(self.page.locator("[data-client-vehicle-editor]").count(), 0)
        self.assertEqual(
            len(self.runtime.service.get_client({"client_id": first})["client"]["vehicles"]), 1
        )

    def test_lost_delete_requires_absence_in_both_vehicle_lists(self):
        first, _ = self.clients()
        vehicle = self.runtime.service.upsert_client_vehicle(
            {"client_id": first, "vehicle": {"vehicle": "SYNTHETIC DELETE VEHICLE"}}
        )["vehicle"]
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        writes = []

        def lose(route):
            writes.append(1)
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        mode = ["stored"]

        def inconsistent(route):
            payload = route.fetch().json()
            if mode[0] == "stored":
                payload["data"]["client"]["vehicles"] = [vehicle]
            elif mode[0] == "projected":
                payload["data"]["vehicles"] = [{**vehicle, "card_id": ""}]
            else:
                payload["data"]["client"]["vehicles"] = [{}]
            route.fulfill(json=payload)

        self.page.route("**/api/delete_client_vehicle", lose)
        self.page.route("**/api/get_client?**", inconsistent)
        for index, case in enumerate(("stored", "projected", "broken")):
            mode[0] = case
            self.page.once("dialog", lambda dialog: dialog.accept())
            if index == 0:
                with self.page.expect_request("**/api/delete_client_vehicle"):
                    self.page.locator("[data-client-vehicle-delete]").first.click()
            else:
                self.page.locator("[data-client-vehicle-delete]").first.click()
            self.page.wait_for_function("!state.clientMutationRequest")
            self.assertEqual(len(writes), 1)
            self.assertTrue(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        self.page.unroute("**/api/get_client?**", inconsistent)
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator("[data-client-vehicle-delete]").first.click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertFalse(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            self.runtime.service.get_client({"client_id": first})["client"]["vehicles"], []
        )

    def test_created_vehicle_wrong_refresh_marker_survives_reload(self):
        first, other = self.clients()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("#clientVehicleAddButton").click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC RELOAD VEHICLE")
        writes = []

        def count_write(route):
            writes.append(1)
            route.continue_()

        def wrong_read(route):
            payload = route.fetch().json()
            payload["data"]["client"]["id"] = other
            route.fulfill(json=payload)

        self.page.route("**/api/upsert_client_vehicle", count_write)
        self.page.route("**/api/get_client?**", wrong_read)
        with self.page.expect_request("**/api/upsert_client_vehicle"):
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(writes), 1)
        self.page.unroute("**/api/get_client?**", wrong_read)
        self.page.reload(wait_until="domcontentloaded")
        self.page.wait_for_function(
            "state.snapshot && state.operatorSessionToken && state.operatorProfile && state.personalBoardPreferences && window.__AUTOSTOP_UI_BOUND__"
        )
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("#clientVehicleAddButton").click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC RELOAD VEHICLE")
        self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(writes), 1)
        self.assertIn("ПОВТОР ОСТАНОВЛЕН", self.page.locator("#statusLine").text_content())
        self.assertEqual(
            len(self.runtime.service.get_client({"client_id": first})["client"]["vehicles"]), 1
        )

    def test_new_vehicle_requires_session_storage_before_write(self):
        first, _ = self.clients()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("#clientVehicleAddButton").click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC STORAGE VEHICLE")
        self.page.evaluate("""() => {
          const original = Storage.prototype.setItem;
          Storage.prototype.setItem = function(key, value) {
            if (key.startsWith('autostop:client-vehicle:')) throw new Error('SYNTHETIC STORAGE');
            return original.call(this, key, value);
          };
        }""")
        self.writes.clear()
        self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function(
            "document.querySelector('#statusLine').textContent.includes('НЕВОЗМОЖНО СОХРАНИТЬ ЗАЩИТУ')"
        )
        self.assertNotIn("upsert_client_vehicle", self.writes)
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
            "SYNTHETIC STORAGE VEHICLE",
        )

    def test_created_vehicle_empty_authoritative_readback_keeps_marker(self):
        first, _ = self.clients()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("#clientVehicleAddButton").click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC EMPTY READBACK")
        writes = []

        def count_write(route):
            writes.append(1)
            route.continue_()

        def empty_stored(route):
            payload = route.fetch().json()
            payload["data"]["client"]["vehicles"] = []
            payload["data"]["vehicles"] = []
            payload["data"]["meta"]["vehicles_total"] = 0
            payload["data"]["client"]["stats"]["vehicles_total"] = 0
            route.fulfill(json=payload)

        self.page.route("**/api/upsert_client_vehicle", count_write)
        self.page.route("**/api/get_client?**", empty_stored)
        with self.page.expect_request("**/api/upsert_client_vehicle"):
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertIn("АВТОМОБИЛЬ НЕ ПОДТВЕРЖДЁН", self.page.locator("#statusLine").text_content())
        self.assertEqual(
            self.page.evaluate(
                "id => sessionStorage.getItem('autostop:client-vehicle:' + JSON.stringify([id, '']))",
                first,
            ),
            "1",
        )
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
            "SYNTHETIC EMPTY READBACK",
        )
        with self.page.expect_response("**/api/get_client?**"):
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(writes), 1)
        self.page.unroute("**/api/get_client?**", empty_stored)
        with self.page.expect_response("**/api/get_client?**"):
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(writes), 1)
        self.assertEqual(self.page.locator("[data-client-vehicle-editor]").count(), 0)
        self.assertEqual(
            len(self.runtime.service.get_client({"client_id": first})["client"]["vehicles"]), 1
        )

    def test_new_vehicle_unknown_403_never_repeats_post(self):
        first, _ = self.clients()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("#clientVehicleAddButton").click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill(
            "SYNTHETIC UNKNOWN REJECTION"
        )
        writes = []

        def reject(route):
            writes.append(1)
            route.fulfill(
                status=403,
                json={"ok": False, "error": {"code": "synthetic_unknown", "message": "SYNTHETIC"}},
            )

        self.page.route("**/api/upsert_client_vehicle", reject)
        with self.page.expect_request("**/api/upsert_client_vehicle"):
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertTrue(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        with self.page.expect_response("**/api/get_client?**"):
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(writes), 1)
        self.assertTrue(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
            "SYNTHETIC UNKNOWN REJECTION",
        )

    def test_new_vehicle_wrong_client_in_upsert_response_stays_uncertain(self):
        first, other = self.clients()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("#clientVehicleAddButton").click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC WRONG OWNER")
        writes = []

        def wrong_owner(route):
            payload = route.fetch().json()
            writes.append(1)
            payload["data"]["client"]["id"] = other
            route.fulfill(json=payload)

        self.page.route("**/api/upsert_client_vehicle", wrong_owner)
        with self.page.expect_request("**/api/upsert_client_vehicle"):
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertTrue(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        with self.page.expect_response("**/api/get_client?**"):
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(writes), 1)
        self.assertTrue(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        self.assertEqual(
            len(self.runtime.service.get_client({"client_id": first})["client"]["vehicles"]), 1
        )

    def test_lost_vehicle_update_requires_authoritative_vehicle_id(self):
        first, _ = self.clients()
        self.runtime.service.upsert_client_vehicle(
            {"client_id": first, "vehicle": {"vehicle": "SYNTHETIC OWNED CAR"}}
        )
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("[data-client-vehicle-edit]").first.click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC OWNED CAR EDIT")
        writes = []

        def lose(route):
            writes.append(1)
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        def empty_stored(route):
            payload = route.fetch().json()
            payload["data"]["client"]["vehicles"] = []
            route.fulfill(json=payload)

        self.page.route("**/api/upsert_client_vehicle", lose)
        self.page.route("**/api/get_client?**", empty_stored)
        with self.page.expect_request("**/api/upsert_client_vehicle"):
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertTrue(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        self.page.unroute("**/api/get_client?**", empty_stored)
        with self.page.expect_response("**/api/get_client?**"):
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertFalse(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        self.assertEqual(len(writes), 1)

    def test_existing_vehicle_unknown_403_blocks_update_retries(self):
        first, _ = self.clients()
        self.runtime.service.upsert_client_vehicle(
            {"client_id": first, "vehicle": {"vehicle": "SYNTHETIC EXISTING"}}
        )
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("[data-client-vehicle-edit]").first.click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC UPDATE REJECTED")
        writes = []

        def reject(route):
            writes.append(route.request.url)
            route.fulfill(
                status=403,
                json={"ok": False, "error": {"code": "synthetic_unknown", "message": "SYNTHETIC"}},
            )

        self.page.route("**/api/upsert_client_vehicle", reject)
        with self.page.expect_request("**/api/upsert_client_vehicle"):
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertTrue(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        with self.page.expect_response("**/api/get_client?**"):
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(writes), 1)
        self.assertTrue(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))

    def test_existing_vehicle_unknown_403_blocks_delete_retries(self):
        first, _ = self.clients()
        self.runtime.service.upsert_client_vehicle(
            {"client_id": first, "vehicle": {"vehicle": "SYNTHETIC EXISTING"}}
        )
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        writes = []

        def reject(route):
            writes.append(route.request.url)
            route.fulfill(
                status=403,
                json={"ok": False, "error": {"code": "synthetic_unknown", "message": "SYNTHETIC"}},
            )

        self.page.route("**/api/delete_client_vehicle", reject)
        for attempt in range(2):
            self.page.once("dialog", lambda dialog: dialog.accept())
            if attempt == 0:
                with self.page.expect_request("**/api/delete_client_vehicle"):
                    self.page.locator("[data-client-vehicle-delete]").first.click()
            else:
                with self.page.expect_response("**/api/get_client?**"):
                    self.page.locator("[data-client-vehicle-delete]").first.click()
            self.page.wait_for_function("!state.clientMutationRequest")
            self.assertEqual(len(writes), 1)
            self.assertTrue(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))

    def test_confirmed_delete_invalid_refresh_retries_only_read(self):
        first, other = self.clients()
        vehicle = self.runtime.service.upsert_client_vehicle(
            {"client_id": first, "vehicle": {"vehicle": "SYNTHETIC DELETE RECEIPT"}}
        )["vehicle"]
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        writes = []

        def count_write(route):
            writes.append(1)
            route.continue_()

        mode = ["wrong_client"]

        def partial(route):
            payload = route.fetch().json()
            if mode[0] == "wrong_client":
                payload["data"]["client"]["id"] = other
            else:
                payload["data"]["client"]["vehicles"] = [vehicle]
            route.fulfill(json=payload)

        self.page.route("**/api/delete_client_vehicle", count_write)
        self.page.route("**/api/get_client?**", partial)
        self.page.once("dialog", lambda dialog: dialog.accept())
        with self.page.expect_request("**/api/delete_client_vehicle"):
            self.page.locator("[data-client-vehicle-delete]").first.click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(writes), 1)
        self.assertTrue(
            self.page.evaluate("Boolean(state.clientVehicleMutationCheckpoint.confirmedResult)")
        )
        mode[0] = "still_present"
        self.page.once("dialog", lambda dialog: dialog.accept())
        with self.page.expect_response("**/api/get_client?**"):
            self.page.locator("[data-client-vehicle-delete]").first.click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(writes), 1)
        self.assertTrue(
            self.page.evaluate("Boolean(state.clientVehicleMutationCheckpoint.confirmedResult)")
        )
        self.page.unroute("**/api/get_client?**", partial)
        self.page.once("dialog", lambda dialog: dialog.accept())
        with self.page.expect_response("**/api/get_client?**"):
            self.page.locator("[data-client-vehicle-delete]").first.click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            self.runtime.service.get_client({"client_id": first})["client"]["vehicles"], []
        )

    def test_uncertain_vehicle_a_blocks_b_and_can_be_verified_after_return(self):
        first, other = [
            self.runtime.service.create_client(
                {"client_type": "company", "display_name": f"SYNTHETIC CYCLE {name}"}
            )["client"]["id"]
            for name in ("ALPHA", "BETA")
        ]
        self.load_board()
        vehicle_a = self.runtime.service.upsert_client_vehicle(
            {"client_id": first, "vehicle": {"vehicle": "SYNTHETIC CAR A"}}
        )["vehicle"]["id"]
        self.runtime.service.upsert_client_vehicle(
            {"client_id": other, "vehicle": {"vehicle": "SYNTHETIC CAR B"}}
        )
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientsSearchInput").fill("SYNTHETIC CYCLE")
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("[data-client-vehicle-edit]").first.click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC CAR A EDIT")
        writes = []

        def lose(route):
            writes.append(route.request.post_data_json)
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        def wrong(route):
            payload = route.fetch().json()
            payload["data"]["client"]["id"] = other
            route.fulfill(json=payload)

        self.page.route("**/api/upsert_client_vehicle", lose)
        self.page.route("**/api/get_client?**", wrong)
        with self.page.expect_request("**/api/upsert_client_vehicle"):
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertTrue(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        self.page.unroute("**/api/get_client?**", wrong)
        self.page.locator("[data-client-vehicle-cancel]").click()
        self.page.locator(f'#clientsList [data-client-id="{other}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=other
        )
        self.page.locator("[data-client-vehicle-edit]").first.click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC CAR B EDIT")
        self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function(
            "document.querySelector('#statusLine').textContent.includes('ПРЕДЫДУЩАЯ ОПЕРАЦИЯ')"
        )
        self.assertEqual(len(writes), 1)
        self.page.locator("[data-client-vehicle-cancel]").click()
        self.page.locator(f'#clientsList [data-client-id="{first}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=first
        )
        self.page.locator("[data-client-vehicle-edit]").first.click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC CAR A EDIT")
        with self.page.expect_response("**/api/get_client?**"):
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(writes), 1)
        self.assertFalse(self.page.evaluate("state.clientVehicleMutationCheckpoint.uncertain"))
        self.assertEqual(
            self.runtime.service.get_client({"client_id": first})["client"]["vehicles"][0]["id"],
            vehicle_a,
        )
