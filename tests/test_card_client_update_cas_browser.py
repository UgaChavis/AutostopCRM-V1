"""A retry from card client creation must not overwrite another operator."""

from tests.browser_ui_support import UiBrowserTestCase


class CardClientUpdateCasBrowserTests(UiBrowserTestCase):
    def prepare_failed_link(self):
        card = self.create_card("SYNTHETIC CARD", vehicle="Toyota")
        self.load_board()
        self.open_card(card)
        self.page.evaluate("openCardClientCreateModal()")
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC ORIGINAL")
        self.page.route("**/api/link_card_to_client", self.fail_link)
        self.save()
        self.assertTrue(self.page.locator("#cardClientCreateModal.is-open").is_visible())
        client = self.page.evaluate("state.cardClientCreateCheckpoint.client")
        self.assertTrue(client["id"] and client["updated_at"])
        return card, client

    @staticmethod
    def fail_link(route):
        route.fulfill(
            status=422,
            json={
                "ok": False,
                "error": {"code": "validation_error", "message": "SYNTHETIC LINK FAILURE"},
            },
        )

    def save(self):
        self.page.click("#cardClientCreateSaveButton")
        self.page.wait_for_function("!state.cardClientCreateSaving")

    def test_external_edit_rejected_and_draft_kept_on_repeat(self):
        _, client = self.prepare_failed_link()
        self.runtime.service.update_client({"client_id": client["id"], "comment": "OTHER OPERATOR"})
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC EDIT")
        updates = []
        self.page.on(
            "request",
            lambda request: (
                updates.append(request.post_data_json)
                if request.url.endswith("/api/update_client")
                else None
            ),
        )
        for _ in range(2):
            self.save()
            self.assertTrue(self.page.locator("#cardClientCreateModal.is-open").is_visible())
            self.assertEqual(
                self.page.locator("#cardClientCreateNameInput").input_value(), "SYNTHETIC EDIT"
            )
        self.assertEqual(
            [update["expected_updated_at"] for update in updates], [client["updated_at"]] * 2
        )
        stored = self.runtime.service.get_client({"client_id": client["id"]})["client"]
        self.assertEqual(stored["comment"], "OTHER OPERATOR")
        self.assertEqual(stored["display_name"], "SYNTHETIC ORIGINAL")
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.writes.count("link_card_to_client"), 1)

    def test_missing_version_never_sends_update(self):
        _, client = self.prepare_failed_link()
        self.page.evaluate("state.cardClientCreateCheckpoint.client.updated_at = ''")
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC EDIT")
        self.save()
        self.assertEqual(self.writes.count("update_client"), 0)
        self.assertEqual(
            self.page.locator("#cardClientCreateNameInput").input_value(), "SYNTHETIC EDIT"
        )
        self.assertEqual(
            self.runtime.service.get_client({"client_id": client["id"]})["client"]["display_name"],
            "SYNTHETIC ORIGINAL",
        )

    def test_uncertain_update_is_read_back_before_using_its_version(self):
        _, client = self.prepare_failed_link()
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC FIRST EDIT")
        updates = []

        def lost_update(route):
            updates.append(route.request.post_data_json)
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        def fail_read(route):
            route.fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC READ FAILURE"}}
            )

        self.page.route("**/api/update_client", lost_update)
        self.page.route("**/api/get_client?**", fail_read)
        self.save()
        self.assertEqual(self.page.evaluate("state.cardClientCreateCheckpoint.uncertain"), "client")
        first = self.runtime.service.get_client({"client_id": client["id"]})["client"]
        self.assertEqual(first["display_name"], "SYNTHETIC FIRST EDIT")
        self.assertEqual(updates[0]["expected_updated_at"], client["updated_at"])
        self.page.unroute("**/api/update_client", lost_update)
        self.page.unroute("**/api/get_client?**", fail_read)
        self.page.on(
            "request",
            lambda request: (
                updates.append(request.post_data_json)
                if request.url.endswith("/api/update_client")
                else None
            ),
        )
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC SECOND EDIT")
        self.save()
        self.assertEqual(updates[-1]["expected_updated_at"], first["updated_at"])
        self.assertEqual(
            self.runtime.service.get_client({"client_id": client["id"]})["client"]["display_name"],
            "SYNTHETIC SECOND EDIT",
        )
        self.assertEqual(self.writes.count("create_client"), 1)

    def test_parallel_vehicle_change_after_rejected_link_preserves_client_cas(self):
        card = self.create_card("SYNTHETIC CARD", vehicle="Toyota")
        self.load_board()
        self.open_card(card)
        self.page.evaluate(
            "applyVehicleProfileToForm({...readVehicleProfileForm(),display_name:'Toyota Test',make_display:'Toyota',model_display:'Test'}); openCardClientCreateModal();"
        )
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC ORIGINAL")
        self.page.locator("#cardClientCreateVehicleInput").check()

        def reject_link_after_parallel_vehicle(route):
            self.runtime.service.upsert_client_vehicle(
                {
                    "client_id": route.request.post_data_json["client_id"],
                    "vehicle": {"vehicle": "SYNTHETIC PARALLEL VEHICLE", "vin": "VIN123"},
                }
            )
            route.fulfill(
                status=400,
                json={
                    "ok": False,
                    "error": {"code": "validation_error", "message": "SYNTHETIC LINK REJECTION"},
                },
            )

        self.page.route("**/api/link_card_to_client", reject_link_after_parallel_vehicle)
        self.save()
        self.assertEqual(self.page.evaluate("state.cardClientCreateCheckpoint.uncertain"), "")
        checkpoint = self.page.evaluate("state.cardClientCreateCheckpoint")
        latest = self.runtime.service.get_client({"client_id": checkpoint["clientId"]})["client"]
        self.assertNotEqual(latest["updated_at"], checkpoint["client"]["updated_at"])
        self.assertEqual(len(latest["vehicles"]), 1)
        self.page.unroute("**/api/link_card_to_client", reject_link_after_parallel_vehicle)
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC EDIT")
        with self.page.expect_response("**/api/update_client") as update_response:
            self.save()
        self.assertEqual(update_response.value.status, 409)
        self.assertEqual(
            update_response.value.request.post_data_json["expected_updated_at"],
            checkpoint["client"]["updated_at"],
        )
        current = self.runtime.service.get_client({"client_id": checkpoint["clientId"]})["client"]
        self.assertEqual(current["display_name"], "SYNTHETIC ORIGINAL")
        self.assertEqual(len(current["vehicles"]), 1)
        self.assertEqual(self.writes.count("link_card_to_client"), 1)
        self.assertEqual(self.writes.count("update_client"), 1)
        self.assertTrue(self.page.locator("#cardClientCreateModal.is-open").is_visible())
        self.assertEqual(
            self.page.locator("#cardClientCreateNameInput").input_value(), "SYNTHETIC EDIT"
        )
