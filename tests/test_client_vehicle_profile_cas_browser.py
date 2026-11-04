"""Vehicle refresh must not rebase a conflicting client-profile draft."""

from tests.browser_ui_support import UiBrowserTestCase


class ClientVehicleProfileCasBrowserTests(UiBrowserTestCase):
    def prepare_client(self):
        client = self.runtime.service.create_client(
            {
                "display_name": "SYNTHETIC CAS " + self._testMethodName,
                "comment": "ORIGINAL",
                "phone": "7999" + str(sum(map(ord, self._testMethodName))).zfill(7),
            }
        )["client"]
        self.load_board()
        self.page.click("#clientsButton")
        self.page.locator(f'#clientsList [data-client-id="{client["id"]}"]').click()
        self.page.wait_for_function(
            "id => state.clientsDesktopProfile?.client?.id === id", arg=client["id"]
        )
        return client

    def add_vehicle(self, name):
        self.page.click("#clientVehicleAddButton")
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill(name)
        with self.page.expect_response("**/api/upsert_client_vehicle") as response:
            self.page.locator("[data-client-vehicle-save]").click()
        self.assertEqual(response.value.status, 200)
        self.page.wait_for_function("!state.clientMutationRequest")
        self.page.locator("#clientVehiclesList").get_by_text(name).wait_for()

    def change_before_vehicle_refresh(self, client_id, patch):
        def change(route):
            self.runtime.service.update_client({"client_id": client_id, **patch})
            route.continue_()

        self.page.route("**/api/get_client?**", change, times=1)

    def test_conflicting_comment_stays_unsaved_and_new_vehicle_is_kept(self):
        client = self.prepare_client()
        self.page.locator("#clientCommentInput").fill("SYNTHETIC DRAFT A")
        self.change_before_vehicle_refresh(client["id"], {"comment": "SYNTHETIC OPERATOR B"})
        self.add_vehicle("SYNTHETIC VEHICLE 1")
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC DRAFT A"
        )
        current = self.runtime.service.get_client({"client_id": client["id"]})["client"]
        self.assertEqual(current["comment"], "SYNTHETIC OPERATOR B")
        self.assertEqual(
            self.page.evaluate("state.clientsDesktopProfile.client.updated_at"),
            current["updated_at"],
        )
        with self.page.expect_response("**/api/update_client") as response:
            self.page.click("#clientSaveButton")
        self.assertEqual(response.value.status, 409)
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC DRAFT A"
        )
        self.assertEqual(
            self.runtime.service.get_client({"client_id": client["id"]})["client"]["comment"],
            "SYNTHETIC OPERATOR B",
        )
        self.assertEqual(
            len(self.runtime.service.get_client({"client_id": client["id"]})["client"]["vehicles"]),
            1,
        )

    def test_disjoint_changes_and_vehicle_can_all_be_saved(self):
        client = self.prepare_client()
        self.page.locator("#clientCommentInput").fill("SYNTHETIC DRAFT A")
        self.change_before_vehicle_refresh(
            client["id"], {"phone": "79990000002", "phones": ["79990000002"]}
        )
        self.add_vehicle("SYNTHETIC VEHICLE 2")
        self.assertEqual(self.page.locator("#clientPhoneInput").input_value(), "79990000002")
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC DRAFT A"
        )
        with self.page.expect_response("**/api/update_client") as response:
            self.page.click("#clientSaveButton")
        self.assertEqual(response.value.status, 200)
        self.page.wait_for_function("!state.clientMutationRequest")
        current = self.runtime.service.get_client({"client_id": client["id"]})["client"]
        self.assertEqual(current["comment"], "SYNTHETIC DRAFT A")
        self.assertEqual(current["phones"], ["79990000002"])
        self.assertEqual(len(current["vehicles"]), 1)

    def test_later_vehicle_refresh_does_not_clear_existing_conflict(self):
        client = self.prepare_client()
        self.page.locator("#clientCommentInput").fill("SYNTHETIC DRAFT A")
        self.change_before_vehicle_refresh(client["id"], {"comment": "SYNTHETIC OPERATOR B"})
        self.add_vehicle("SYNTHETIC VEHICLE 3")
        self.add_vehicle("SYNTHETIC VEHICLE 4")
        with self.page.expect_response("**/api/update_client") as response:
            self.page.click("#clientSaveButton")
        self.assertEqual(response.value.status, 409)
        self.page.wait_for_function("!state.clientMutationRequest")
        current = self.runtime.service.get_client({"client_id": client["id"]})["client"]
        self.assertEqual(current["comment"], "SYNTHETIC OPERATOR B")
        self.assertEqual(len(current["vehicles"]), 2)
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC DRAFT A"
        )

    def test_concurrent_phone_change_conflicts_as_a_pair(self):
        client = self.prepare_client()
        self.page.locator("#clientPhoneInput").fill("79990000003")
        self.change_before_vehicle_refresh(
            client["id"], {"phone": "79990000002", "phones": ["79990000002"]}
        )
        self.add_vehicle("SYNTHETIC VEHICLE 5")
        with self.page.expect_response("**/api/update_client") as response:
            self.page.click("#clientSaveButton")
        self.assertEqual(response.value.status, 409)
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(
            self.runtime.service.get_client({"client_id": client["id"]})["client"]["phones"],
            ["79990000002"],
        )
        self.assertEqual(self.page.locator("#clientPhoneInput").input_value(), "79990000003")

    def test_unconfirmed_profile_write_is_not_cleared_by_vehicle_refresh(self):
        client = self.prepare_client()
        self.page.locator("#clientCommentInput").fill("SYNTHETIC FIRST DRAFT")
        baseline = self.page.evaluate("state.clientsDraftBaseline")
        version = self.page.evaluate("state.clientsDraftRevision.version")

        def lose_write(route):
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        def fail_read(route):
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/update_client", lose_write)
        self.page.route("**/api/get_client?**", fail_read)
        self.page.click("#clientSaveButton")
        self.page.wait_for_function(
            "state.clientProfileSaveCheckpoint?.uncertain && !state.clientMutationRequest"
        )
        self.page.unroute("**/api/get_client?**", fail_read)
        self.page.unroute("**/api/update_client", lose_write)
        self.add_vehicle("SYNTHETIC VEHICLE PENDING")
        self.assertEqual(self.page.evaluate("state.clientsDraftBaseline"), baseline)
        self.assertEqual(self.page.evaluate("state.clientsDraftRevision.version"), version)
        self.assertTrue(self.page.evaluate("state.clientProfileSaveCheckpoint.uncertain"))
        self.assertFalse(self.page.locator("#clientSaveButton").is_disabled())
        self.page.locator("#clientCommentInput").fill("SYNTHETIC SECOND DRAFT")
        self.page.click("#clientSaveButton")
        self.page.wait_for_function(
            "!state.clientMutationRequest && state.clientProfileSaveCheckpoint.uncertain"
        )
        self.assertEqual(self.page.evaluate("state.clientsDraftRevision.version"), version)
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC SECOND DRAFT"
        )
        self.assertEqual(self.writes.count("update_client"), 1)
        current = self.runtime.service.get_client({"client_id": client["id"]})["client"]
        self.assertEqual(current["comment"], "ORIGINAL")
        self.assertEqual(len(current["vehicles"]), 1)

    def test_conflict_resets_after_explicit_profile_selection_and_logout(self):
        other = self.runtime.service.create_client({"display_name": "SYNTHETIC OTHER PROFILE"})[
            "client"
        ]
        client = self.prepare_client()
        self.page.locator("#clientCommentInput").fill("SYNTHETIC DRAFT A")
        self.change_before_vehicle_refresh(client["id"], {"comment": "SYNTHETIC OPERATOR B"})
        self.add_vehicle("SYNTHETIC VEHICLE RESET")
        self.assertTrue(self.page.evaluate("state.clientsDraftRevision.conflict"))
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator(f'#clientsList [data-client-id="{other["id"]}"]').click()
        self.page.wait_for_function(
            "id => state.clientsDesktopProfile?.client?.id === id", arg=other["id"]
        )
        self.assertFalse(self.page.evaluate("state.clientsDraftRevision.conflict"))
        self.assertEqual(
            self.page.evaluate("state.clientsDraftRevision.version"), other["updated_at"]
        )
        self.page.evaluate("() => logoutOperator()")
        self.assertIsNone(self.page.evaluate("state.clientsDraftRevision"))

    def test_vehicle_conflict_preserves_unknown_successful_profile_write(self):
        client = self.prepare_client()
        self.page.locator("#clientCommentInput").fill("SYNTHETIC UNKNOWN WRITE")

        def lose_write(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        def fail_read(route):
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/update_client", lose_write)
        self.page.route("**/api/get_client?**", fail_read)
        self.page.click("#clientSaveButton")
        self.page.wait_for_function(
            "state.clientProfileSaveCheckpoint?.uncertain && !state.clientMutationRequest"
        )
        self.page.unroute("**/api/update_client", lose_write)
        self.page.unroute("**/api/get_client?**", fail_read)
        self.page.click("#clientVehicleAddButton")
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("SYNTHETIC VEHICLE")
        with self.page.expect_response("**/api/upsert_client_vehicle") as response:
            self.page.locator("[data-client-vehicle-save]").click()
        self.assertEqual(response.value.status, 409)
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertTrue(self.page.evaluate("state.clientProfileSaveCheckpoint.uncertain"))
        self.assertEqual(self.page.evaluate("state.clientCandidateVehicleEffects.size"), 0)
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC UNKNOWN WRITE"
        )
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
            "SYNTHETIC VEHICLE",
        )
        current = self.runtime.service.get_client({"client_id": client["id"]})["client"]
        self.assertEqual(current["comment"], "SYNTHETIC UNKNOWN WRITE")
        self.assertEqual(current["vehicles"], [])
