"""Client vehicle mutations, client drafts and uncertain replies on real storage."""

from __future__ import annotations

import json

from tests.browser_ui_support import UiBrowserTestCase


class ClientVehiclesBrowserTests(UiBrowserTestCase):
    def test_candidate_marker_after_definite_rejection_allows_retry_without_private_data(
        self,
    ) -> None:
        self.load_board()
        result = self.page.evaluate("""async () => {
          await ensureBoardModule('card_save');
          const original = api, clientId = 'opaque-client', cardId = 'opaque-card';
          const candidate = {card_id: cardId, vehicle: 'PRIVATE SYNTHETIC MODEL', vin: 'PRIVATE VIN'};
          const key = 'autostop:client-vehicle:' + JSON.stringify([clientId, cardId]);
          state.clientSuggestionProfiles = {[clientId]: {vehicles: [candidate]}};
          state.clientSuggestions = [{id: clientId}];
          let rejectWrite, posts = 0;
          try {
            api = () => { posts++; return new Promise((_resolve, reject) => {rejectWrite = reject;}); };
            const first = ensureStableClientSuggestionVehicle(clientId, candidate, {isCurrent: () => true});
            const marker = sessionStorage.getItem(key);
            const effect = state.clientCandidateVehicleEffects.get(key);
            const safeMarker = key === 'autostop:client-vehicle:["opaque-client","opaque-card"]'
              && marker === '1' && !JSON.stringify(effect).includes(state.operatorSessionToken)
              && !JSON.stringify(effect).includes(candidate.vehicle) && !JSON.stringify(effect).includes(candidate.vin);
            rejectWrite(Object.assign(new Error('rejected'), {status: 422, code: 'validation_error'}));
            try { await first; } catch (_) {}
            const afterReject = sessionStorage.getItem(key);
            api = async () => { posts++; return {client: {id: clientId}, vehicle: {id: 'owned-id', vehicle: 'SYNTHETIC'}}; };
            const next = await ensureStableClientSuggestionVehicle(clientId, candidate, {isCurrent: () => true});
            return {safeMarker, afterReject, posts, savedId: next?.id, afterSuccess: sessionStorage.getItem(key)};
          } finally { api = original; sessionStorage.removeItem(key); state.clientCandidateVehicleEffects.clear(); }
        }""")
        self.assertEqual(
            result,
            {
                "safeMarker": True,
                "afterReject": None,
                "posts": 2,
                "savedId": "owned-id",
                "afterSuccess": None,
            },
        )

    def test_candidate_storage_failure_or_corruption_blocks_before_write(self) -> None:
        self.load_board()
        result = self.page.evaluate("""async () => {
          await ensureBoardModule('card_save');
          const originalApi = api, get = Storage.prototype.getItem, set = Storage.prototype.setItem,
            remove = Storage.prototype.removeItem;
          const candidate = {card_id: 'synthetic-storage-card', vehicle: 'SYNTHETIC'};
          const key = 'autostop:client-vehicle:' + JSON.stringify(['synthetic-storage-client', candidate.card_id]);
          let posts = 0;
          api = async () => { posts++; return {vehicle: {id: 'unexpected'}}; };
          const attempt = async () => { try { await ensureStableClientSuggestionVehicle('synthetic-storage-client', candidate); return false; } catch (_) { return true; } };
          try {
            Storage.prototype.getItem = () => { throw new Error('unavailable'); };
            const unreadable = await attempt();
            Storage.prototype.getItem = get;
            Storage.prototype.setItem = () => { throw new Error('quota'); };
            const unwritable = await attempt();
            Storage.prototype.setItem = set;
            state.clientCandidateVehicleEffects.clear();
            sessionStorage.setItem(key, 'corrupted-value');
            const corrupted = await attempt();
            sessionStorage.removeItem(key);
            const effect = beginCandidateVehicleCreation('synthetic-storage-client', candidate);
            Storage.prototype.removeItem = function(k) { remove.call(this, k); throw new Error('after removal'); };
            finishCandidateVehicleCreation(effect, {confirmed: true});
            Storage.prototype.removeItem = remove;
            const blockedAfterRemoveFailure = state.clientCandidateVehicleEffects.has(key)
              && sessionStorage.getItem(key) === '1';
            return {posts, unreadable, unwritable, corrupted, blockedAfterRemoveFailure};
          } finally {
            api = originalApi; Storage.prototype.getItem = get; Storage.prototype.setItem = set;
            Storage.prototype.removeItem = remove; sessionStorage.removeItem(key);
            state.clientCandidateVehicleEffects.clear();
          }
        }""")
        self.assertEqual(
            result,
            {
                "posts": 0,
                "unreadable": True,
                "unwritable": True,
                "corrupted": True,
                "blockedAfterRemoveFailure": True,
            },
        )

    def test_cold_module_concurrent_same_candidate_sends_once(self) -> None:
        self.load_board()
        result = self.page.evaluate("""async () => {
          const original = api;
          const candidate = {card_id: 'source-card', vehicle: 'SYNTHETIC'};
          state.clientSuggestionProfiles = {'synthetic-client': {vehicles: [candidate]}};
          state.clientSuggestions = [{id: 'synthetic-client'}];
          let posts = 0;
          try {
            api = async () => { posts++; return {client: {id: 'synthetic-client'}, vehicle: {id: 'created', vehicle: 'SYNTHETIC'}}; };
            const context = {isCurrent: () => true};
            const [a, b] = await Promise.all([
              ensureStableClientSuggestionVehicle('synthetic-client', candidate, context),
              ensureStableClientSuggestionVehicle('synthetic-client', candidate, context),
            ]);
            return {posts, a: a?.id, b: b?.id};
          } finally { api = original; }
        }""")
        self.assertEqual(result, {"posts": 1, "a": "created", "b": "created"})

    def test_cold_module_does_not_start_write_after_context_switch(self) -> None:
        self.load_board()
        module_path = self.page.evaluate("BOARD_MODULE_MANIFEST.card_save")
        held = []
        self.page.route("**" + module_path, lambda route: held.append(route))
        self.page.evaluate("""() => {
          const candidate = {card_id: 'source-card', vehicle: 'SYNTHETIC'};
          window.syntheticCurrent = true;
          window.syntheticPending = ensureStableClientSuggestionVehicle('synthetic-client', candidate,
            {isCurrent: () => window.syntheticCurrent});
        }""")
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.page.evaluate("window.syntheticCurrent = false")
        held.pop().continue_()
        self.assertFalse(self.page.evaluate("async () => Boolean(await window.syntheticPending)"))
        self.assertNotIn("upsert_client_vehicle", self.writes)

    def test_cold_suggestion_failures_never_link_client_without_selected_vehicle(self) -> None:
        for failure in ("module", "timeout"):
            with self.subTest(failure=failure):
                self.load_board()
                module_path = self.page.evaluate("BOARD_MODULE_MANIFEST.card_save")
                if failure == "module":
                    pattern = "**" + module_path
                    self.page.route(pattern, lambda route: route.abort("failed"))
                else:
                    pattern = "**/api/upsert_client_vehicle"
                    self.page.route(
                        pattern,
                        lambda route: route.fulfill(
                            status=408,
                            content_type="application/json",
                            body=json.dumps(
                                {
                                    "ok": False,
                                    "error": {
                                        "code": "validation_error",
                                        "message": "SYNTHETIC TIMEOUT",
                                    },
                                }
                            ),
                        ),
                    )
                self.writes.clear()
                result = self.page.evaluate("""async () => {
                  const client = {id: 'synthetic-client'};
                  const candidate = {id: 'foreign-id', card_id: 'source-card', vehicle: 'SYNTHETIC'};
                  state.editingId = 'synthetic-target';
                  state.activeCard = {id: 'synthetic-target'};
                  state.clientSuggestions = [client];
                  state.clientSuggestionProfiles = {[client.id]: {client, vehicles: [candidate]}};
                  state.pendingCardClientId = 'old-client';
                  state.pendingCardClientVehicleId = 'old-vehicle';
                  await chooseClientSuggestion(client.id, clientSuggestionVehicleKey(candidate));
                  return {client: state.pendingCardClientId, vehicle: state.pendingCardClientVehicleId,
                    blocked: state.clientCandidateVehicleEffects.size};
                }""")
                self.assertEqual(result["client"], "old-client")
                self.assertEqual(result["vehicle"], "old-vehicle")
                self.assertNotIn("link_card_to_client", self.writes)
                self.assertEqual(self.writes.count("upsert_client_vehicle"), failure == "timeout")
                self.assertEqual(result["blocked"], 1 if failure == "timeout" else 0)
                self.page.unroute(pattern)

    def test_candidate_creation_guard_survives_context_switch_and_uncertain_errors(self) -> None:
        self.load_board()
        result = self.page.evaluate("""async () => {
          await ensureBoardModule('card_save');
          const original = api;
          const candidate = {card_id: 'synthetic-source', vehicle: 'CAR'};
          const clearEffects = () => {
            for (const effect of state.clientCandidateVehicleEffects.values()) sessionStorage.removeItem(effect.key);
            state.clientCandidateVehicleEffects.clear();
          };
          let resolve, posts = 0, current = true;
          api = () => { posts++; return new Promise(done => {resolve = done;}); };
          try {
            const first = ensureStableClientSuggestionVehicle('synthetic-client', candidate, {isCurrent: () => current});
            current = false;
            let blocked = false;
            try { await ensureStableClientSuggestionVehicle('synthetic-client', candidate, {isCurrent: () => true}); }
            catch (_) { blocked = true; }
            resolve({vehicle: {id: 'created', vehicle: 'CAR'}});
            await first;
            const lateRetained = state.clientCandidateVehicleEffects.size;
            const outcomes = [];
            for (const [status, code] of [[408, 'validation_error'], [429, 'validation_error'], [409, 'invalid_json'], [500, 'error'], [422, 'validation_error']]) {
              clearEffects();
              const effect = beginCandidateVehicleCreation('synthetic-client', candidate);
              finishCandidateVehicleCreation(effect, {error: {status, code}});
              outcomes.push(state.clientCandidateVehicleEffects.size);
            }
            clearEffects();
            const oldEffect = beginCandidateVehicleCreation('synthetic-client', candidate);
            const storedSession = Object.hasOwn(oldEffect, 'session');
            resetViewerScopedState();
            finishCandidateVehicleCreation(oldEffect, {confirmed: true});
            let blockedAfterReset = false;
            try { beginCandidateVehicleCreation('synthetic-client', candidate); }
            catch (_) { blockedAfterReset = true; }
            return {posts, blocked, lateRetained, outcomes, blockedAfterReset, storedSession};
          } finally { api = original; clearEffects(); }
        }""")
        self.assertEqual(
            result,
            {
                "posts": 1,
                "blocked": True,
                "lateRetained": 1,
                "outcomes": [1, 1, 1, 1, 0],
                "blockedAfterReset": True,
                "storedSession": False,
            },
        )

    def test_lost_suggestion_create_blocks_desktop_retry_for_same_card_candidate(self) -> None:
        phone = "+7 900 222-33-44"
        for client_id, model in (
            ("uncertain-owner-a", "MODEL A"),
            ("uncertain-owner-b", "MODEL B"),
        ):
            self.runtime.service.create_client(
                {
                    "id": client_id,
                    "display_name": client_id,
                    "phone": phone,
                    "vehicles": [{"id": "shared-uncertain", "vehicle": model}],
                }
            )
        source = self.runtime.service.create_card(
            {
                "title": "SYNTHETIC OTHER CARD",
                "client_id": "uncertain-owner-b",
                "client_vehicle_id": "shared-uncertain",
            }
        )["card"]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.evaluate("id => selectClient(id)", "uncertain-owner-a")
        self.page.wait_for_function(
            "state.clientsActiveProfile?.client?.id === 'uncertain-owner-a'"
        )

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/upsert_client_vehicle", lose_reply)
        self.page.evaluate(
            """async sourceId => {
          const candidate = state.clientsActiveProfile.vehicles.find(item => item.card_id === sourceId);
          try { await ensureStableClientSuggestionVehicle('uncertain-owner-a', candidate, {isCurrent: () => true}); }
          catch (_) {}
        }""",
            source["id"],
        )
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 1)
        candidate_row = self.page.locator("#clientVehiclesList .client-mini--vehicle").nth(1)
        candidate_row.locator("[data-client-vehicle-edit]").click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("UNSAVED CHANGE")
        self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 1)
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
            "UNSAVED CHANGE",
        )
        self.page.unroute("**/api/upsert_client_vehicle")
        self.runtime.service.update_card({"card_id": source["id"], "vehicle": "MODEL B CHANGED"})
        self.page.reload()
        self.page.wait_for_function(
            "state.snapshot && state.operatorSessionToken && state.operatorProfile && state.personalBoardPreferences && window.__AUTOSTOP_UI_BOUND__"
        )
        self.page.evaluate("stopSnapshotPolling()")
        self.page.locator("#clientsButton").click()
        self.page.evaluate("id => selectClient(id)", "uncertain-owner-a")
        self.page.wait_for_function(
            "state.clientsActiveProfile?.client?.id === 'uncertain-owner-a'"
        )
        candidate_row = self.page.locator("#clientVehiclesList .client-mini--vehicle").filter(
            has_text="MODEL B CHANGED"
        )
        candidate_row.locator("[data-client-vehicle-edit]").click()
        self.page.locator('[data-client-vehicle-field="vehicle"]').fill("CHANGED AFTER RELOAD")
        self.page.locator("[data-client-vehicle-save]").click()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 1)
        self.assertEqual(
            self.page.locator('[data-client-vehicle-field="vehicle"]').input_value(),
            "CHANGED AFTER RELOAD",
        )

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
          const key = 'autostop:client-vehicle:' + JSON.stringify([client.id, candidate.card_id]);
          state.clientSuggestions = [client];
          state.clientSuggestionProfiles = {[client.id]: {client: {id: client.id}, vehicles: [candidate]}};
          let writes = 0, body;
          try {
            api = async (_path, options) => { writes++; body = options.body; return {client: {id: client.id}, vehicle: {id: 'owned-id', vehicle: 'CANDIDATE'}}; };
            await ensureStableClientSuggestionVehicle(client.id, candidate);
            const stored = state.clientSuggestionProfiles[client.id].vehicles[0];
            const again = await ensureStableClientSuggestionVehicle(client.id, stored);
            return {writes, body, stored, again, marker: sessionStorage.getItem(key)};
          } finally { api = original; }
        }""")
        self.assertEqual(result["writes"], 1)
        self.assertNotIn("client_vehicle_id", result["body"])
        self.assertEqual(result["stored"]["id"], "owned-id")
        self.assertEqual(result["stored"]["card_id"], "")
        self.assertEqual(result["again"]["id"], "owned-id")
        self.assertIsNone(result["marker"])

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
                body=json.dumps(
                    {
                        "ok": False,
                        "error": {"code": "validation_error", "message": "Synthetic validation"},
                    }
                ),
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
