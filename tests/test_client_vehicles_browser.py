"""Client vehicle mutations, client drafts and uncertain replies on real storage."""

from __future__ import annotations

import json

from tests.browser_ui_support import UiBrowserTestCase


class ClientVehiclesBrowserTests(UiBrowserTestCase):
    def test_vehicle_keys_separate_sources_and_accept_legacy_unicode(self) -> None:
        self.load_board()
        result = self.page.evaluate("""() => {
          const legacy = String.fromCharCode(0xd800);
          const saved = {id: 'c:foreign'};
          const candidate = {id: 'c:foreign', card_id: 'foreign'};
          const a = clientProfileVehicleKey(saved), b = clientProfileVehicleKey(candidate);
          const legacyKey = clientProfileVehicleKey({id: legacy});
          return {different: a !== b, suggestionDifferent:
            clientSuggestionVehicleKey(saved) !== clientSuggestionVehicleKey(candidate),
            legacyLength: legacyKey.length, legacyCode: legacyKey.charCodeAt(2)};
        }""")
        self.assertEqual(
            result,
            {
                "different": True,
                "suggestionDifferent": True,
                "legacyLength": 3,
                "legacyCode": 0xD800,
            },
        )

    def test_suggestion_requires_verified_hydration_and_does_not_reuse_empty_preview(self) -> None:
        self.load_board()
        result = self.page.evaluate("""async () => {
          const original = api;
          const client = {id: 'synthetic-preview', vehicles_preview: [{id: 'preview-id', vehicle: 'PREVIEW'}]};
          state.clientSuggestions = [client];
          state.clientSuggestionProfiles = {};
          state.pendingCardClientId = '';
          try {
            api = async () => { throw new Error('SYNTHETIC HYDRATION FAILURE'); };
            await chooseClientSuggestion(client.id, clientSuggestionVehicleKey(client.vehicles_preview[0]));
            const afterFailure = state.pendingCardClientId;
            api = async () => ({client: {id: client.id}, vehicles: []});
            await chooseClientSuggestion(client.id, clientSuggestionVehicleKey(client.vehicles_preview[0]));
            return {afterFailure, afterEmpty: state.pendingCardClientId,
              visibleVehicles: clientSuggestionVehicles(client).length};
          } finally { api = original; }
        }""")
        self.assertEqual(result, {"afterFailure": "", "afterEmpty": "", "visibleVehicles": 0})
        self.assertNotIn("upsert_client_vehicle", self.writes)
        self.assertNotIn("link_card_to_client", self.writes)

    def test_suggestion_card_candidate_is_saved_once_with_new_owned_id(self) -> None:
        self.load_board()
        result = self.page.evaluate("""async () => {
          const original = api;
          const candidate = {id: 'foreign-id', card_id: 'foreign-card', vehicle: 'CANDIDATE'};
          const client = {id: 'synthetic-owner', vehicles_preview: [candidate]};
          state.clientSuggestions = [client];
          state.clientSuggestionProfiles = {[client.id]: {client: {id: client.id}, vehicles: [candidate]}};
          let writes = 0, body;
          try {
            api = async (_path, options) => { writes++; body = options.body; return {vehicle: {id: 'owned-id', vehicle: 'CANDIDATE'}}; };
            await ensureStableClientSuggestionVehicle(client.id, candidate);
            const stored = state.clientSuggestionProfiles[client.id].vehicles[0];
            const again = await ensureStableClientSuggestionVehicle(client.id, stored);
            return {writes, body, stored, again};
          } finally { api = original; }
        }""")
        self.assertEqual(result["writes"], 1)
        self.assertNotIn("client_vehicle_id", result["body"])
        self.assertEqual(result["stored"]["id"], "owned-id")
        self.assertEqual(result["stored"]["card_id"], "")
        self.assertEqual(result["again"]["id"], "owned-id")

    def test_card_candidate_with_another_clients_vehicle_id_cannot_edit_or_delete_saved_vehicle(
        self,
    ) -> None:
        phone = "+7 900 111-22-33"
        for client_id, model in (
            ("synthetic-owner-a", "MODEL A"),
            ("synthetic-owner-b", "MODEL B"),
        ):
            self.runtime.service.create_client(
                {
                    "id": client_id,
                    "display_name": client_id,
                    "phone": phone,
                    "vehicles": [{"id": "shared-vehicle", "vehicle": model}],
                }
            )
        card = self.runtime.service.create_card(
            {
                "title": "OTHER CLIENT CARD",
                "client_id": "synthetic-owner-b",
                "client_vehicle_id": "shared-vehicle",
            }
        )["card"]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.evaluate("id => selectClient(id)", "synthetic-owner-a")
        self.page.wait_for_function(
            "state.clientsActiveProfile?.client?.id === 'synthetic-owner-a'"
        )
        items = self.page.locator("#clientVehiclesList .client-mini--vehicle")
        self.assertEqual(items.count(), 2)
        self.assertIn("MODEL A", items.nth(0).inner_text())
        self.assertIn("MODEL B", items.nth(1).inner_text())
        self.assertIn("из связанной карточки", items.nth(1).inner_text().lower())
        self.assertTrue(items.nth(1).locator("button", has_text="УДАЛИТЬ").is_disabled())
        items.nth(1).locator("[data-client-vehicle-edit]").click()
        self.assertEqual(self.page.locator("[data-client-vehicle-editor]").count(), 1)
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="vehicle"]').input_value(), "MODEL B"
        )
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("MODEL B COPY")
        with self.page.expect_request("**/api/upsert_client_vehicle") as outgoing:
            self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertNotIn("client_vehicle_id", outgoing.value.post_data_json)
        owner = self.runtime.service.get_client(
            {"client_id": "synthetic-owner-a", "strict_client_links": True}
        )
        stored = {vehicle["id"]: vehicle["vehicle"] for vehicle in owner["client"]["vehicles"]}
        self.assertEqual(stored["shared-vehicle"], "MODEL A")
        self.assertIn("MODEL B COPY", stored.values())
        self.assertEqual(
            self.runtime.service.get_card({"card_id": card["id"]})["card"]["client_id"],
            "synthetic-owner-b",
        )

    def open_new_vehicle(self, name: str) -> str:
        client_id = self.create_client_and_open(name)
        self.page.locator("#clientVehicleAddButton").click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("Toyota synthetic")
        self.page.locator('[data-client-vehicle-field="vin"]').fill("JTM12345678901234")
        self.page.locator('[data-client-vehicle-field="license_plate"]').fill("a 123 aa 24")
        return client_id

    def save_vehicle(self) -> None:
        self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function(
            "state.clientVehicleMutationCheckpoint !== null && !state.clientMutationRequest"
        )

    def test_lost_existing_vehicle_reply_confirms_same_id_without_second_update(self) -> None:
        client_id = self.open_new_vehicle("Client vehicle lost edit")
        self.save_vehicle()
        self.page.locator("[data-client-vehicle-edit]").first.click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("Toyota confirmed edit")

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/upsert_client_vehicle", lose_reply)
        self.save_vehicle()
        self.assertEqual(self.page.locator("[data-client-vehicle-save]").count(), 0)
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 2)
        vehicles = self.runtime.service.get_client({"client_id": client_id})["vehicles"]
        self.assertEqual(len(vehicles), 1)
        self.assertEqual(vehicles[0]["vehicle"], "Toyota confirmed edit")

    def test_lost_delete_reply_confirms_absence_and_retains_client_draft(self) -> None:
        client_id = self.open_new_vehicle("Client vehicle lost delete")
        self.save_vehicle()
        self.page.locator("#clientCommentInput").fill("Unsaved comment after delete")

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/delete_client_vehicle", lose_reply)
        self.page.once("dialog", lambda dialog: dialog.accept())
        with self.page.expect_request("**/api/delete_client_vehicle"):
            self.page.locator("[data-client-vehicle-delete]").first.click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(self.runtime.service.get_client({"client_id": client_id})["vehicles"], [])
        self.assertEqual(self.writes.count("delete_client_vehicle"), 1)
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "Unsaved comment after delete"
        )
        self.assertFalse(self.page.locator("#clientSaveButton").is_disabled())

    def test_pending_vehicle_save_freezes_fields_and_ignores_double_submit(self) -> None:
        self.open_new_vehicle("Client vehicle pending controls")
        held = []
        self.page.route("**/api/upsert_client_vehicle", lambda route: held.append(route))
        self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("Boolean(state.clientMutationRequest)")
        self.assertTrue(self.page.locator('[data-client-vehicle-field="vehicle"]').is_disabled())
        self.assertTrue(self.page.locator("#clientCommentInput").is_disabled())
        self.page.evaluate("document.querySelector('[data-client-vehicle-save]').click()")
        self.page.wait_for_timeout(60)
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 1)
        self.assertFalse(self.page.locator("#clientCommentInput").is_disabled())

    def test_vehicle_validation_failure_retains_editor_and_allows_definite_retry(self) -> None:
        self.open_new_vehicle("Client vehicle validation retry")
        self.page.route(
            "**/api/upsert_client_vehicle",
            lambda route: route.fulfill(
                status=422,
                content_type="application/json",
                body=json.dumps({"ok": False, "error": {"message": "Synthetic validation"}}),
            ),
        )
        self.save_vehicle()
        self.assertFalse(self.page.locator('[data-client-vehicle-field="vehicle"]').is_disabled())
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
            "Toyota synthetic",
        )
        self.page.unroute("**/api/upsert_client_vehicle")
        self.save_vehicle()
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 2)

    def test_vehicle_create_edit_delete_roundtrip_preserves_native_normalization(self) -> None:
        client_id = self.open_new_vehicle("Client vehicle lifecycle")
        self.save_vehicle()
        self.page.locator("[data-client-vehicle-edit]").first.wait_for()
        vehicle_key = self.page.locator("[data-client-vehicle-edit]").first.get_attribute(
            "data-client-vehicle-edit"
        )
        vehicle_id = self.runtime.service.get_client({"client_id": client_id})["vehicles"][0]["id"]
        self.page.locator(f'[data-client-vehicle-edit="{vehicle_key}"]').click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("Toyota synthetic edited")
        self.save_vehicle()
        self.page.locator(f'[data-client-vehicle-delete="{vehicle_key}"]').wait_for()
        profile = self.runtime.service.get_client({"client_id": client_id})
        vehicle = next(item for item in profile["vehicles"] if item["id"] == vehicle_id)
        self.assertEqual(vehicle["vehicle"], "Toyota synthetic edited")
        self.assertEqual(vehicle["vin"], "JTM12345678901234")
        self.assertEqual(vehicle["license_plate"], "a 123 aa 24")
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator(f'[data-client-vehicle-delete="{vehicle_key}"]').click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(self.runtime.service.get_client({"client_id": client_id})["vehicles"], [])
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 2)
        self.assertEqual(self.writes.count("delete_client_vehicle"), 1)

    def test_vehicle_refresh_preserves_unsaved_client_fields_and_dirty_state(self) -> None:
        client_id = self.open_new_vehicle("Client vehicle with unsaved profile")
        self.page.locator("#clientCommentInput").fill("Unsaved client comment")
        self.save_vehicle()
        self.page.locator("[data-client-vehicle-edit]").first.wait_for()
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "Unsaved client comment"
        )
        self.assertFalse(self.page.locator("#clientSaveButton").is_disabled())
        self.assertEqual(
            self.runtime.service.get_client({"client_id": client_id})["client"]["comment"], ""
        )

    def test_lost_new_vehicle_reply_reads_profile_and_blocks_second_creation(self) -> None:
        client_id = self.open_new_vehicle("Client vehicle uncertain create")

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/upsert_client_vehicle", lose_reply)
        self.save_vehicle()
        self.save_vehicle()
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 1)
        self.assertEqual(
            len(self.runtime.service.get_client({"client_id": client_id})["vehicles"]), 1
        )
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
            "Toyota synthetic",
        )
