"""An uncertain card link must not be retried or saved as another vehicle."""

from tests.browser_ui_support import UiBrowserTestCase


class CardClientLinkReceiptBrowserTests(UiBrowserTestCase):
    def open_vehicle_choice(self):
        service = self.runtime.service
        client = service.create_client({"display_name": "SYNTHETIC RECEIPT CLIENT"})["client"]
        first = service.upsert_client_vehicle(
            {"client_id": client["id"], "vehicle": {"vehicle": "SYNTHETIC X", "vin": "VINX"}}
        )["vehicle"]
        second = service.upsert_client_vehicle(
            {"client_id": client["id"], "vehicle": {"vehicle": "SYNTHETIC Y", "vin": "VINY"}}
        )["vehicle"]
        card_id = self.create_card("SYNTHETIC RECEIPT CARD")
        service.link_card_to_client(
            {"card_id": card_id, "client_id": client["id"], "client_vehicle_id": first["id"]}
        )
        profile = service.get_client({"client_id": client["id"], "order_limit": 5})
        self.load_board()
        self.open_card(card_id)
        self.page.evaluate(
            """profile => {
                state.clientSuggestions = [profile.client];
                state.clientSuggestionProfiles = {[profile.client.id]: profile};
            }""",
            profile,
        )
        return card_id, client, first, second

    def choose(self, client, vehicle):
        profile = self.runtime.service.get_client({"client_id": client["id"], "order_limit": 5})
        self.page.evaluate(
            """profile => {
                state.clientSuggestions = [profile.client];
                state.clientSuggestionProfiles = {[profile.client.id]: profile};
            }""",
            profile,
        )
        self.page.evaluate(
            """async ({clientId, vehicleId}) => {
                const vehicle = state.clientSuggestionProfiles[clientId].vehicles.find(v => v.id === vehicleId);
                await chooseClientSuggestion(clientId, clientSuggestionVehicleKey(vehicle));
            }""",
            {"clientId": client["id"], "vehicleId": vehicle["id"]},
        )

    def open_mobile_card(self, card_id):
        self.page.evaluate("closeCardModal()")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.wait_for_function("state.mobileLite")
        self.page.evaluate("id => openMobileCardDetail(id)", card_id)
        self.page.wait_for_function(
            "id => state.mobileCardId === id && !state.mobileCardLoading", arg=card_id
        )

    def test_unknown_desktop_link_blocks_mobile_card_save(self):
        card_id, client, first, second = self.open_vehicle_choice()
        self.page.route(
            "**/api/link_card_to_client",
            lambda route: route.fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}}
            ),
        )
        self.choose(client, second)
        self.open_mobile_card(card_id)
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC MOBILE DRAFT")
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertEqual(self.writes.count("update_card"), 0)
        self.assertEqual(self.stored_card(card_id)["client_vehicle_id"], first["id"])
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE DRAFT"
        )
        self.assertFalse(
            self.page.evaluate("id => state.mobileCardWriteVerificationPending.has(id)", card_id)
        )
        self.assertIn("НЕ ПОДТВЕРЖДЁН", self.page.locator("#statusLine").inner_text())

    def test_unavailable_link_storage_blocks_mobile_save_locally(self):
        card_id = self.create_card("SYNTHETIC MOBILE STORAGE")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator(f'[data-mobile-card-id="{card_id}"]').click()
        self.page.wait_for_function(
            "id => state.mobileCardId === id && !state.mobileCardLoading", arg=card_id
        )
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC MOBILE STORAGE DRAFT")
        self.page.evaluate("""() => {
          const get = Storage.prototype.getItem;
          Storage.prototype.getItem = function(key) {
            if (key.startsWith('autostop:card-link:')) throw new Error('synthetic storage');
            return get.call(this, key);
          };
        }""")
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertEqual(self.writes.count("update_card"), 0)
        self.assertFalse(
            self.page.evaluate("id => state.mobileCardWriteVerificationPending.has(id)", card_id)
        )
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(),
            "SYNTHETIC MOBILE STORAGE DRAFT",
        )
        self.assertIn("НЕВОЗМОЖНО ПРОВЕРИТЬ", self.page.locator("#statusLine").inner_text())

    def test_mobile_save_cold_module_cannot_write_after_viewer_reset(self):
        card_id = self.create_card("SYNTHETIC COLD MOBILE")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator(f'[data-mobile-card-id="{card_id}"]').click()
        self.page.wait_for_function(
            "id => state.mobileCardId === id && !state.mobileCardLoading", arg=card_id
        )
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC COLD DRAFT")
        self.assertFalse(
            self.page.evaluate("Boolean(boardModuleRecords.get('card_save')?.exports)")
        )
        held = []
        module_path = self.page.evaluate("BOARD_MODULE_MANIFEST.card_save")
        self.page.route("**" + module_path, lambda route: held.append(route))
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("state.mobileCardSaving")
        self.page.evaluate("() => { void saveMobileCardDetail(); }")
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.assertEqual(self.writes.count("update_card"), 0)
        self.page.evaluate("resetViewerScopedState()")
        held.pop().continue_()
        self.page.wait_for_timeout(200)
        self.assertEqual(self.writes.count("update_card"), 0)
        self.assertEqual(self.stored_card(card_id)["title"], "SYNTHETIC COLD MOBILE")

    def test_mobile_save_module_load_error_is_local_and_preserves_draft(self):
        card_id = self.create_card("SYNTHETIC MOBILE MODULE")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator(f'[data-mobile-card-id="{card_id}"]').click()
        self.page.wait_for_function(
            "id => state.mobileCardId === id && !state.mobileCardLoading", arg=card_id
        )
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC MODULE DRAFT")
        module_path = self.page.evaluate("BOARD_MODULE_MANIFEST.card_save")
        self.page.route("**" + module_path, lambda route: route.abort("failed"))
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertEqual(self.writes.count("update_card"), 0)
        self.assertFalse(
            self.page.evaluate("id => state.mobileCardWriteVerificationPending.has(id)", card_id)
        )
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MODULE DRAFT"
        )
        self.assertIn(
            "НЕ УДАЛОСЬ ЗАГРУЗИТЬ МОДУЛЬ", self.page.locator("#statusLine").inner_text().upper()
        )

    def test_503_cannot_save_changed_vehicle_on_old_link_or_retry_after_reload(self):
        card_id, client, first, second = self.open_vehicle_choice()
        self.page.route(
            "**/api/link_card_to_client",
            lambda route: route.fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}}
            ),
        )
        self.choose(client, second)
        self.page.locator("#cardTitle").fill("SYNTHETIC UNSAVED DRAFT")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_open()
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC UNSAVED DRAFT")
        self.assertEqual(self.writes.count("update_card"), 0)
        self.assertEqual(self.stored_card(card_id)["client_vehicle_id"], first["id"])
        self.page.evaluate("openCardClientCreateModal()")
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC NEW CLIENT")
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertEqual(self.writes.count("create_client"), 0)
        self.assertEqual(
            self.page.locator("#cardClientCreateNameInput").input_value(), "SYNTHETIC NEW CLIENT"
        )
        self.page.reload()
        self.open_card(card_id)
        self.page.locator("#cardTitle").fill("SYNTHETIC AFTER RELOAD")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_open()
        self.assertEqual(self.writes.count("update_card"), 0)
        self.assertEqual(self.writes.count("link_card_to_client"), 1)

    def test_empty_success_is_unknown_and_no_second_link(self):
        card_id, client, first, second = self.open_vehicle_choice()
        self.page.route(
            "**/api/link_card_to_client",
            lambda route: route.fulfill(status=200, json={"ok": True, "data": {}}),
        )
        self.choose(client, second)
        self.page.locator("#cardVehicle").fill("SYNTHETIC UNSAVED VEHICLE")
        self.choose(client, second)
        self.assertEqual(self.writes.count("link_card_to_client"), 1)
        self.assertEqual(
            self.page.locator("#cardVehicle").input_value(), "SYNTHETIC UNSAVED VEHICLE"
        )
        self.assertEqual(self.stored_card(card_id)["client_vehicle_id"], first["id"])
        self.assertIn("НЕ ПОДТВЕРЖД", self.page.locator("#statusLine").inner_text())

    def test_real_missing_vehicle_cannot_save_draft_over_old_vehicle_link(self):
        card_id, client, first, second = self.open_vehicle_choice()
        self.runtime.service.delete_client_vehicle(
            {"client_id": client["id"], "client_vehicle_id": second["id"], "unlink_cards": True}
        )
        with self.page.expect_response("**/api/link_card_to_client") as response:
            self.page.evaluate(
                """async ({clientId, vehicleId}) => {
                  const vehicle = state.clientSuggestionProfiles[clientId].vehicles.find(v => v.id === vehicleId);
                  await chooseClientSuggestion(clientId, clientSuggestionVehicleKey(vehicle));
                }""",
                {"clientId": client["id"], "vehicleId": second["id"]},
            )
        self.assertEqual(response.value.status, 404)
        self.assertEqual(response.value.json()["error"]["code"], "not_found")
        self.assertEqual(self.page.evaluate("state.pendingCardClientVehicleId"), second["id"])
        self.page.evaluate("""() => {
          const field = document.querySelector('[data-vehicle-field-input="vin"]');
          field.value = 'SYNTHETIC MANUAL VIN';
          field.dispatchEvent(new Event('input', {bubbles:true}));
        }""")
        self.assertEqual(self.page.evaluate("state.pendingCardClientVehicleId"), second["id"])
        self.page.locator("#cardTitle").fill("SYNTHETIC NOT FOUND DRAFT")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_open()
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC NOT FOUND DRAFT")
        self.assertEqual(self.writes.count("update_card"), 0)
        self.assertEqual(self.stored_card(card_id)["client_vehicle_id"], first["id"])
        self.assertIn("НЕ ПОДТВЕРЖДЕНЫ", self.page.locator("#statusLine").inner_text())

    def test_manual_fields_on_linked_card_keep_link_and_can_be_saved(self):
        card_id, client, first, _ = self.open_vehicle_choice()
        for key, value in (
            ("customer_name", "SYNTHETIC MANUAL NAME"),
            ("customer_phone", "1234567890"),
            ("vin", "JTM12345678901234"),
            ("registration_plate", "B123BB77"),
        ):
            with self.subTest(key=key):
                self.page.evaluate(
                    """({key, value}) => {
                  const field = document.querySelector('[data-vehicle-field-input="' + key + '"]');
                  field.value = value;
                  field.dispatchEvent(new Event('input', {bubbles:true}));
                }""",
                    {"key": key, "value": value},
                )
                self.assertEqual(self.page.evaluate("state.pendingCardClientId"), client["id"])
                self.assertEqual(
                    self.page.evaluate("state.pendingCardClientVehicleId"), first["id"]
                )
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_closed()
        saved = self.stored_card(card_id)
        self.assertEqual(saved["client_id"], client["id"])
        self.assertEqual(saved["client_vehicle_id"], first["id"])
        self.assertEqual(saved["vehicle_profile"]["vin"], "JTM12345678901234")

    def test_manual_edit_on_new_card_clears_suggested_client_and_vehicle(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC NEW DRAFT"})[
            "client"
        ]
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("SYNTHETIC NEW MANUAL DRAFT")
        self.page.evaluate("client => { state.clientSuggestions = [client]; }", client)
        self.page.evaluate(
            "id => chooseClientSuggestion(id, '', {createNewVehicle: true})", client["id"]
        )
        self.assertTrue(self.page.evaluate("state.pendingCreateClientVehicleFromCard"))
        self.page.evaluate("""() => {
          const field = document.querySelector('[data-vehicle-field-input="vin"]');
          field.value = 'JTM12345678901234';
          field.dispatchEvent(new Event('input', {bubbles:true}));
        }""")
        self.assertFalse(
            self.page.evaluate(
                "state.pendingCardClientId || state.pendingCardClientVehicleId || state.pendingCreateClientVehicleFromCard"
            )
        )
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_closed()
        self.assertEqual(self.writes.count("link_card_to_client"), 0)

    def test_confirmed_selection_without_vehicle_keeps_existing_link_and_saves(self):
        card_id, client, first, _ = self.open_vehicle_choice()
        with self.page.expect_response("**/api/link_card_to_client") as response:
            self.page.evaluate("id => chooseClientSuggestion(id)", client["id"])
        self.assertEqual(response.value.status, 200)
        self.assertEqual(self.page.evaluate("state.pendingCardClientVehicleId"), first["id"])
        self.page.locator("#cardTitle").fill("SYNTHETIC CLIENT ONLY SAVED")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_closed()
        self.assertEqual(self.stored_card(card_id)["client_vehicle_id"], first["id"])

    def test_missing_active_link_fields_cannot_be_saved_as_empty(self):
        _, _, _, _ = self.open_vehicle_choice()
        self.page.locator("#cardTitle").fill("SYNTHETIC MISSING LINK DRAFT")
        for key in ("client_id", "client_vehicle_id"):
            with self.subTest(key=key):
                self.page.evaluate(
                    "key => { window.savedLinkField = state.activeCard[key]; delete state.activeCard[key]; }",
                    key,
                )
                self.page.locator("#saveCardButton").click()
                self.finish_save()
                self.assert_card_open()
                self.assertEqual(self.writes.count("update_card"), 0)
                self.assertEqual(
                    self.page.locator("#cardTitle").input_value(), "SYNTHETIC MISSING LINK DRAFT"
                )
                self.page.evaluate("key => { state.activeCard[key] = window.savedLinkField; }", key)

    def test_confirmed_created_vehicle_is_not_marked_for_creation_again(self):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC NEW VEHICLE"})[
            "client"
        ]
        card_id = self.create_card(
            "SYNTHETIC VEHICLE SOURCE",
            vehicle="SYNTHETIC UNIQUE VEHICLE",
            vehicle_profile={"vin": "JTM12345678901234", "registration_plate": "A123AA77"},
        )
        self.load_board()
        self.open_card(card_id)
        self.page.evaluate("client => { state.clientSuggestions = [client]; }", client)
        with self.page.expect_response("**/api/link_card_to_client") as response:
            self.page.evaluate(
                "id => chooseClientSuggestion(id, '', {createNewVehicle: true})", client["id"]
            )
        self.assertEqual(response.value.status, 200)
        vehicle_id = response.value.json()["data"]["card"]["client_vehicle_id"]
        self.assertTrue(vehicle_id)
        self.assertEqual(self.page.evaluate("state.pendingCardClientVehicleId"), vehicle_id)
        self.assertFalse(self.page.evaluate("state.pendingCreateClientVehicleFromCard"))
        self.page.locator("#cardTitle").fill("SYNTHETIC VEHICLE SAVED")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_closed()
        self.assertEqual(self.stored_card(card_id)["client_vehicle_id"], vehicle_id)
        self.assertEqual(self.writes.count("link_card_to_client"), 1)

    def test_unavailable_storage_stops_link_before_post(self):
        _, client, _, second = self.open_vehicle_choice()
        self.page.evaluate(
            """() => {
                const get = Storage.prototype.getItem;
                Storage.prototype.getItem = function(key) {
                  if (key.startsWith('autostop:card-link:')) throw new Error('synthetic storage');
                  return get.call(this, key);
                };
            }"""
        )
        self.choose(client, second)
        self.assertEqual(self.writes.count("link_card_to_client"), 0)
        self.assertIn("НЕВОЗМОЖНО", self.page.locator("#statusLine").inner_text())
        self.page.locator("#cardTitle").fill("SYNTHETIC STORAGE DRAFT")
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_open()
        self.assertEqual(self.writes.count("update_card"), 0)
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC STORAGE DRAFT")
        self.assertIn(
            "НЕВОЗМОЖНО ПРОВЕРИТЬ СОСТОЯНИЕ ПРИВЯЗКИ", self.page.locator("#statusLine").inner_text()
        )

    def test_id_only_success_does_not_replace_full_card_or_clear_draft(self):
        card_id, client, first, second = self.open_vehicle_choice()
        self.page.locator("#cardTitle").fill("SYNTHETIC VISIBLE DRAFT")

        def partial(route):
            request = route.request.post_data_json
            route.fulfill(
                status=200,
                json={
                    "ok": True,
                    "data": {
                        "card": {
                            "id": request["card_id"],
                            "client_id": request["client_id"],
                            "client_vehicle_id": request["client_vehicle_id"],
                            "updated_at": "2026-01-01T00:00:00Z",
                        }
                    },
                },
            )

        self.page.route("**/api/link_card_to_client", partial)
        self.choose(client, second)
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC VISIBLE DRAFT")
        self.assertEqual(self.page.evaluate("state.activeCard.title"), "SYNTHETIC RECEIPT CARD")
        self.assertEqual(self.stored_card(card_id)["client_vehicle_id"], first["id"])
        self.choose(client, second)
        self.assertEqual(self.writes.count("link_card_to_client"), 1)

    def test_failed_marker_write_can_retry_only_if_no_marker_was_written(self):
        _, client, _, second = self.open_vehicle_choice()
        self.page.evaluate(
            """() => {
                window.originalStorageSet = Storage.prototype.setItem;
                window.originalStorageGet = Storage.prototype.getItem;
                Storage.prototype.setItem = function(key, value) {
                  if (key.startsWith('autostop:card-link:')) {
                    window.syntheticStorageUnavailable = true;
                    throw new Error('synthetic storage');
                  }
                  return window.originalStorageSet.call(this, key, value);
                };
                Storage.prototype.getItem = function(key) {
                  if (key.startsWith('autostop:card-link:') && window.syntheticStorageUnavailable)
                    throw new Error('synthetic storage read');
                  return window.originalStorageGet.call(this, key);
                };
            }"""
        )
        self.choose(client, second)
        self.assertEqual(self.writes.count("link_card_to_client"), 0)
        self.page.evaluate("""() => {
          Storage.prototype.setItem = window.originalStorageSet;
          Storage.prototype.getItem = window.originalStorageGet;
        }""")
        self.choose(client, second)
        self.assertEqual(self.writes.count("link_card_to_client"), 1)

    def test_partially_written_marker_blocks_retry(self):
        _, client, _, second = self.open_vehicle_choice()
        self.page.evaluate(
            """() => {
                window.originalStorageSet = Storage.prototype.setItem;
                Storage.prototype.setItem = function(key, value) {
                  const result = window.originalStorageSet.call(this, key, value);
                  if (key.startsWith('autostop:card-link:')) throw new Error('synthetic partial write');
                  return result;
                };
            }"""
        )
        self.choose(client, second)
        self.page.evaluate("() => { Storage.prototype.setItem = window.originalStorageSet; }")
        self.choose(client, second)
        self.assertEqual(self.writes.count("link_card_to_client"), 0)
        self.assertIn("НЕ ПОДТВЕРЖД", self.page.locator("#statusLine").inner_text())

    def test_empty_success_during_new_card_save_preserves_checkpoint_and_draft(self):
        self.load_board()
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardTitle").fill("SYNTHETIC PENDING LINK")
        self.page.evaluate(
            "id => applyClientSuggestionToVehicleProfile({id, display_name:'SYNTHETIC CLIENT'})",
            self.runtime.client_id,
        )
        self.page.route(
            "**/api/link_card_to_client",
            lambda route: route.fulfill(status=200, json={"ok": True, "data": {}}),
        )
        with self.page.expect_response("**/api/link_card_to_client"):
            self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_open()
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC PENDING LINK")
        self.assertTrue(self.page.evaluate("Boolean(state.cardPersistCheckpoint.linkBody)"))
        self.assertEqual(self.page.evaluate("state.cardPersistCheckpoint.uncertain"), "link")
        card_id = self.page.evaluate("state.cardPersistCheckpoint.cardId")
        self.assertTrue(card_id)
        self.assertEqual(
            self.page.evaluate(
                "id => sessionStorage.getItem('autostop:card-link:' + JSON.stringify(id))",
                card_id,
            ),
            "1",
        )
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assertEqual(self.writes.count("create_card"), 1)
        self.assertEqual(self.writes.count("update_card"), 0)
        self.assertEqual(self.writes.count("link_card_to_client"), 1)
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC PENDING LINK")
        self.assertEqual(self.page.evaluate("state.cardPersistCheckpoint.uncertain"), "link")
        self.assertFalse(self.stored_card(card_id)["client_id"])

    def test_existing_card_with_unconfirmed_client_keeps_draft_without_writes(self):
        card_id = self.create_card("SYNTHETIC SAVED CARD")
        self.load_board()
        self.open_card(card_id)
        self.page.locator("#cardTitle").fill("SYNTHETIC PENDING LINK")
        self.page.evaluate(
            "id => applyClientSuggestionToVehicleProfile({id, display_name:'SYNTHETIC CLIENT'})",
            self.runtime.client_id,
        )
        self.page.locator("#saveCardButton").click()
        self.finish_save()
        self.assert_card_open()
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC PENDING LINK")
        self.assertEqual(self.writes.count("update_card"), 0)
        self.assertEqual(self.writes.count("link_card_to_client"), 0)
        self.assertFalse(self.stored_card(card_id)["client_id"])
        self.assertIn("НЕ ПОДТВЕРЖДЕНЫ", self.page.locator("#statusLine").inner_text())

    def test_408_and_429_remain_uncertain(self):
        for status in (408, 429):
            with self.subTest(status=status):
                card_id, client, first, second = self.open_vehicle_choice()
                self.page.route(
                    "**/api/link_card_to_client",
                    lambda route: route.fulfill(
                        status=status,
                        json={
                            "ok": False,
                            "error": {"code": "validation_error", "message": "SYNTHETIC"},
                        },
                    ),
                )
                before = self.writes.count("link_card_to_client")
                self.choose(client, second)
                self.choose(client, second)
                self.assertEqual(self.writes.count("link_card_to_client"), before + 1)
                self.assertEqual(self.stored_card(card_id)["client_vehicle_id"], first["id"])
                self.page.unroute("**/api/link_card_to_client")
