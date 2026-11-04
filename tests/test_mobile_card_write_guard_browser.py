"""Mobile card writes fail closed without losing their editable draft."""

from tests.browser_ui_support import UiBrowserTestCase


class MobileCardWriteGuardBrowserTests(UiBrowserTestCase):
    marker = "autostop:mobile-card-create-pending"

    def open_new(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator("#mobileCardCreateButton").click()
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC MOBILE NEW")

    def open_existing(self):
        card = self.create_card("SYNTHETIC MOBILE ORIGINAL")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator(f'[data-mobile-card-id="{card}"]').click()
        self.page.wait_for_function(
            "id => state.mobileCardId === id && !state.mobileCardLoading", arg=card
        )
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC MOBILE EDIT")
        return card

    def test_unknown_create_marker_survives_reload_and_blocks_repeat(self):
        self.open_new()
        created = []

        def lose_reply(route):
            self.assertEqual(
                self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1"
            )
            response = route.fetch()
            self.assertEqual(response.status, 200)
            created.append(response.json()["data"]["card"]["id"])
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/create_card", lose_reply)
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE NEW"
        )
        self.assertEqual(
            self.runtime.service.get_card({"card_id": created[0]})["card"]["title"],
            "SYNTHETIC MOBILE NEW",
        )
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.page.locator("#mobileCardSaveButton").click()
        self.assertEqual(len(created), 1)
        self.page.reload(wait_until="domcontentloaded")
        self.page.wait_for_function("window.__AUTOSTOP_UI_BOUND__ && state.operatorSessionToken")
        self.page.locator("#mobileCardCreateButton").click()
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC OTHER BODY")
        self.page.locator("#mobileCardSaveButton").click()
        self.assertEqual(len(created), 1)
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")

    def test_storage_failure_prevents_create(self):
        self.open_new()
        self.page.evaluate(
            """key => { const original = Storage.prototype.setItem;
          Storage.prototype.setItem = function(name, value) { if (name === key) throw new Error('SYNTHETIC'); return original.call(this, name, value); };
        }""",
            self.marker,
        )
        self.writes.clear()
        self.page.locator("#mobileCardSaveButton").click()
        self.assertNotIn("create_card", self.writes)
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE NEW"
        )

    def test_validation_rejection_clears_marker_and_allows_create(self):
        self.open_new()

        def reject(route):
            route.fulfill(
                status=400,
                json={"ok": False, "error": {"code": "validation_error", "message": "SYNTHETIC"}},
            )

        self.page.route("**/api/create_card", reject)
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertIsNone(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker))
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE NEW"
        )
        self.page.unroute("**/api/create_card", reject)
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function(
            "!state.mobileCardSaving && !state.mobileCardCreating && !!state.mobileCardId"
        )
        self.assertIsNone(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker))
        self.assertEqual(
            self.runtime.service.get_card({"card_id": self.page.evaluate("state.mobileCardId")})[
                "card"
            ]["title"],
            "SYNTHETIC MOBILE NEW",
        )

    def test_unknown_update_preserves_draft_until_fresh_reopen(self):
        card = self.open_existing()
        writes = []

        def lose_reply(route):
            writes.append(route.request.url)
            self.assertEqual(route.fetch().status, 200)
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/update_card", lose_reply)
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE EDIT"
        )
        self.assertTrue(self.page.locator("#mobileCardSaveButton").is_disabled())
        self.page.evaluate("saveMobileCardDetail()")
        self.assertEqual(len(writes), 1)
        self.page.unroute("**/api/update_card", lose_reply)
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator("#mobileCardBackButton").click()

        def reject_read(route):
            route.fulfill(
                status=400,
                json={"ok": False, "error": {"code": "validation_error", "message": "SYNTHETIC"}},
            )

        self.page.route("**/api/get_card?**", reject_read)
        self.page.locator(f'[data-mobile-card-id="{card}"]').click()
        self.page.wait_for_function("!state.mobileCardLoading && !state.mobileCardId")
        self.assertTrue(
            self.page.evaluate("id => state.mobileCardWriteVerificationPending.has(id)", card)
        )
        self.page.unroute("**/api/get_card?**", reject_read)

        def partial_read(route):
            payload = route.fetch().json()
            payload["data"]["card"]["vehicle_profile"] = {}
            route.fulfill(json=payload)

        self.page.route("**/api/get_card?**", partial_read)
        self.page.locator(f'[data-mobile-card-id="{card}"]').click()
        self.page.wait_for_function("!state.mobileCardLoading && !state.mobileCardId")
        self.assertTrue(
            self.page.evaluate("id => state.mobileCardWriteVerificationPending.has(id)", card)
        )
        self.page.unroute("**/api/get_card?**", partial_read)

        def warn_read(route):
            payload = route.fetch().json()
            payload["data"]["read_warnings"] = ["client_vehicle_link_unavailable"]
            route.fulfill(json=payload)

        self.page.route("**/api/get_card?**", warn_read)
        self.page.locator(f'[data-mobile-card-id="{card}"]').click()
        self.page.wait_for_function(
            "id => state.mobileCardId === id && !state.mobileCardLoading", arg=card
        )
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE EDIT"
        )
        self.assertFalse(
            self.page.evaluate("id => state.mobileCardWriteVerificationPending.has(id)", card)
        )
        self.assertIn(
            "client_vehicle_link_unavailable", self.page.evaluate("state.mobileCard.read_warnings")
        )
        self.assertIn("Связь карточки", self.page.locator("#mobileCardLinkWarning").inner_text())

    def test_validation_rejection_preserves_existing_card_draft(self):
        self.open_existing()
        self.page.route(
            "**/api/update_card",
            lambda route: route.fulfill(
                status=400,
                json={"ok": False, "error": {"code": "validation_error", "message": "SYNTHETIC"}},
            ),
        )
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE EDIT"
        )
        self.assertFalse(self.page.locator("#mobileCardSaveButton").is_disabled())

    def test_navigation_preserves_existing_card_fields_until_save(self):
        card = self.open_existing()
        self.page.locator("#mobileCardDescriptionInput").fill("SYNTHETIC DESCRIPTION DRAFT")
        self.page.locator('[data-mobile-card-tab="vehicle"]').click()
        self.page.locator("#mobileCardVehiclePlate").fill("12345")
        self.page.locator('[data-mobile-view="more"]').click()
        self.page.locator('[data-mobile-view="board"]').click()
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE EDIT"
        )
        self.assertEqual(
            self.page.locator("#mobileCardDescriptionInput").input_value(),
            "SYNTHETIC DESCRIPTION DRAFT",
        )
        self.assertEqual(self.page.locator("#mobileCardVehiclePlate").input_value(), "12345")
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function(
            "!state.mobileCardSaving && state.mobileCard?.title === 'SYNTHETIC MOBILE EDIT'"
        )
        saved = self.runtime.service.get_card({"card_id": card})["card"]
        self.assertEqual(saved["description"], "SYNTHETIC DESCRIPTION DRAFT")
        self.assertEqual(saved["vehicle_profile"]["registration_plate"], "12345")

    def test_navigation_preserves_new_card_fields(self):
        self.open_new()
        self.page.locator("#mobileCardDescriptionInput").fill("SYNTHETIC NEW DESCRIPTION")
        self.page.locator('[data-mobile-view="more"]').click()
        self.page.locator('[data-mobile-view="board"]').click()
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE NEW"
        )
        self.assertEqual(
            self.page.locator("#mobileCardDescriptionInput").input_value(),
            "SYNTHETIC NEW DESCRIPTION",
        )

    def test_retryable_http_errors_keep_create_marker(self):
        self.open_new()
        for status in (408, 429):
            attempts = []

            def reject(route):
                attempts.append(route.request.url)
                route.fulfill(
                    status=status,
                    json={
                        "ok": False,
                        "error": {"code": "validation_error", "message": "SYNTHETIC"},
                    },
                )

            self.page.route("**/api/create_card", reject)
            self.page.locator("#mobileCardSaveButton").click()
            self.page.wait_for_function("!state.mobileCardSaving")
            self.page.locator("#mobileCardSaveButton").click()
            self.assertEqual(len(attempts), 1)
            self.assertEqual(
                self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1"
            )
            self.page.unroute("**/api/create_card", reject)
            self.page.evaluate("key => sessionStorage.removeItem(key)", self.marker)

    def test_success_without_card_id_keeps_create_marker(self):
        self.open_new()
        attempts = []

        def malformed(route):
            attempts.append(route.request.url)
            route.fulfill(json={"ok": True, "data": {"card": {}}})

        self.page.route("**/api/create_card", malformed)
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.page.locator("#mobileCardSaveButton").click()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE NEW"
        )

    def test_inflight_create_cannot_switch_card_or_start_another(self):
        other = self.create_card("SYNTHETIC OTHER CONTEXT")
        self.open_new()
        held = []
        self.page.route("**/api/create_card", lambda route: held.append(route))
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("state.mobileCardSaving")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.evaluate(
            "id => { openMobileNewCard(); openMobileCardDetail(id); closeMobileCardDetail(); }",
            other,
        )
        self.assertTrue(self.page.evaluate("state.mobileCardCreating"))
        self.assertTrue(self.page.evaluate("state.mobileCardSaving"))
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE NEW"
        )
        held.pop().fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")

    def test_success_with_invalid_card_id_keeps_create_marker(self):
        self.open_new()
        self.page.route(
            "**/api/create_card",
            lambda route: route.fulfill(
                json={
                    "ok": True,
                    "data": {"card": {"id": "synthetic-not-uuid", "updated_at": "2026-01-01"}},
                }
            ),
        )
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.assertTrue(self.page.evaluate("state.mobileCardCreating"))

    def test_clean_close_and_dirty_close_confirmation(self):
        self.open_existing()
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC MOBILE ORIGINAL")
        dialogs = []

        def dismiss(dialog):
            dialogs.append(dialog.message)
            dialog.dismiss()

        self.page.on("dialog", dismiss)
        self.page.locator("#mobileCardBackButton").click()
        self.assertEqual(dialogs, [])
        self.assertEqual(self.page.evaluate("state.mobileCardId"), "")

    def test_draft_blocks_close_and_new_until_confirmed(self):
        card = self.open_existing()
        self.page.locator('[data-mobile-card-tab="vehicle"]').click()
        self.page.locator("#mobileCardVehiclePlate").fill("12345")
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator("#mobileCardBackButton").click()
        self.assertEqual(self.page.evaluate("state.mobileCardId"), card)
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE EDIT"
        )
        self.assertEqual(self.page.locator("#mobileCardVehiclePlate").input_value(), "12345")
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.evaluate("openMobileNewCard()")
        self.assertEqual(self.page.evaluate("state.mobileCardId"), card)
        self.assertEqual(self.page.locator("#mobileCardVehiclePlate").input_value(), "12345")
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.evaluate("openMobileNewCard()")
        self.assertTrue(self.page.evaluate("state.mobileCardCreating"))
        self.assertEqual(self.page.locator("#mobileCardTitleInput").input_value(), "")
        self.assertEqual(self.page.locator("#mobileCardVehiclePlate").input_value(), "")

    def test_tags_and_optional_deadline_fields_trigger_discard_guard(self):
        self.open_existing()
        self.page.locator("#mobileCardTitleInput").fill("SYNTHETIC MOBILE ORIGINAL")
        self.page.evaluate("""() => {
          els.mobileCardDeadlineDays = document.createElement('input');
          els.mobileCardDeadlineHours = document.createElement('input');
          els.mobileCardDeadlineDays.value = '3';
          els.mobileCardDeadlineHours.value = '4';
        }""")
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator("#mobileCardBackButton").click()
        self.assertTrue(self.page.evaluate("Boolean(state.mobileCardId)"))
        self.page.evaluate("""() => {
          els.mobileCardDeadlineDays = null;
          els.mobileCardDeadlineHours = null;
          state.mobileCard.tag_items = [{label: 'SYNTHETIC TAG', color: 'green'}];
        }""")
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator("#mobileCardBackButton").click()
        self.assertTrue(self.page.evaluate("Boolean(state.mobileCardId)"))

    def test_partial_create_response_keeps_marker_and_draft(self):
        self.open_new()
        attempts = []

        def partial(route):
            attempts.append(route.request.url)
            route.fulfill(
                json={
                    "ok": True,
                    "data": {
                        "card": {
                            "id": "11111111-1111-4111-8111-111111111111",
                            "updated_at": "2026-01-01T00:00:00Z",
                        }
                    },
                }
            )

        self.page.route("**/api/create_card", partial)
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.assertTrue(self.page.evaluate("state.mobileCardCreating"))
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE NEW"
        )
        self.page.locator("#mobileCardSaveButton").click()
        self.assertEqual(len(attempts), 1)

    def test_partial_update_response_blocks_retry_and_keeps_draft(self):
        card = self.open_existing()
        attempts = []

        def partial(route):
            attempts.append(route.request.url)
            route.fulfill(
                json={
                    "ok": True,
                    "data": {"card": {"id": card, "updated_at": "2026-01-01T00:00:00Z"}},
                }
            )

        self.page.route("**/api/update_card", partial)
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE EDIT"
        )
        self.assertTrue(self.page.locator("#mobileCardSaveButton").is_disabled())
        self.assertTrue(
            self.page.evaluate("id => state.mobileCardWriteVerificationPending.has(id)", card)
        )
        self.page.evaluate("saveMobileCardDetail()")
        self.assertEqual(len(attempts), 1)

    def exercise_file_refresh(self, *, concurrent_edit):
        card = self.open_existing()
        version_before = self.page.evaluate("state.mobileCard.updated_at")
        self.page.locator('[data-mobile-card-tab="files"]').click()

        def file_response(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            if concurrent_edit:
                self.runtime.service.update_card(
                    {"card_id": card, "title": "SYNTHETIC OTHER OPERATOR"}
                )
            route.fulfill(response=response)

        self.page.route("**/api/add_card_attachment", file_response)
        with self.page.expect_response("**/api/add_card_attachment"):
            self.page.locator("#mobileCardFileInput").set_input_files(
                {"name": "synthetic.txt", "mimeType": "text/plain", "buffer": b"synthetic"}
            )
        self.page.wait_for_function("!state.mobileCardFilesBusy")
        backend = self.runtime.service.get_card({"card_id": card})["card"]
        self.assertNotEqual(backend["updated_at"], version_before)
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE EDIT"
        )
        self.assertTrue(
            any(item["file_name"] == "synthetic.txt" for item in backend["attachments"])
        )
        if concurrent_edit:
            self.assertEqual(backend["title"], "SYNTHETIC OTHER OPERATOR")
            self.assertEqual(self.page.evaluate("state.mobileCard.updated_at"), version_before)
            self.assertTrue(
                self.page.evaluate("id => state.mobileCardWriteVerificationPending.has(id)", card)
            )
            self.assertTrue(self.page.locator("#mobileCardSaveButton").is_disabled())
            self.writes.clear()
            self.page.evaluate("saveMobileCardDetail()")
            self.assertNotIn("update_card", self.writes)
            self.assertEqual(
                self.runtime.service.get_card({"card_id": card})["card"]["title"],
                "SYNTHETIC OTHER OPERATOR",
            )
        else:
            self.assertEqual(
                self.page.evaluate("state.mobileCard.updated_at"), backend["updated_at"]
            )
            self.assertFalse(
                self.page.evaluate("id => state.mobileCardWriteVerificationPending.has(id)", card)
            )
            self.page.locator("#mobileCardSaveButton").click()
            self.page.wait_for_function(
                "!state.mobileCardSaving && state.mobileCard?.title === 'SYNTHETIC MOBILE EDIT'"
            )
            self.assertEqual(
                self.runtime.service.get_card({"card_id": card})["card"]["title"],
                "SYNTHETIC MOBILE EDIT",
            )

    def test_file_refresh_blocks_overwriting_other_operator(self):
        self.exercise_file_refresh(concurrent_edit=True)

    def test_file_refresh_safely_advances_version_when_fields_match(self):
        self.exercise_file_refresh(concurrent_edit=False)

    def test_mobile_save_blocks_desktop_card_until_finished(self):
        other = self.create_card("SYNTHETIC DESKTOP B")
        first = self.open_existing()
        held = []
        self.page.route("**/api/update_card", lambda route: held.append(route))
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("state.mobileCardSaving")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.locator(f'.card[data-card-id="{other}"]:not([data-virtual-card="true"])').click()
        self.assertFalse(self.page.locator("#cardModal").is_visible())
        self.assertNotEqual(self.page.evaluate("state.editingId"), other)
        held.pop().continue_()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.page.unroute("**/api/update_card")
        self.page.locator(f'.card[data-card-id="{other}"]:not([data-virtual-card="true"])').click()
        self.page.wait_for_function(
            "id => state.editingId === id && state.activeCardIsFull", arg=other
        )
        self.page.locator("#cardTitle").fill("SYNTHETIC DESKTOP B EDIT")
        self.page.locator("#saveCardButton").click()
        self.page.wait_for_function("!state.cardSaveInFlight")
        self.assertEqual(
            self.runtime.service.get_card({"card_id": first})["card"]["title"],
            "SYNTHETIC MOBILE EDIT",
        )
        self.assertEqual(
            self.runtime.service.get_card({"card_id": other})["card"]["title"],
            "SYNTHETIC DESKTOP B EDIT",
        )

    def test_dirty_mobile_draft_requires_confirmation_before_desktop_open(self):
        other = self.create_card("SYNTHETIC DESKTOP OTHER")
        first = self.open_existing()
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator(f'.card[data-card-id="{other}"]:not([data-virtual-card="true"])').click()
        self.assertFalse(self.page.locator("#cardModal").is_visible())
        self.assertEqual(self.page.evaluate("state.mobileCardId"), first)
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE EDIT"
        )
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator(f'.card[data-card-id="{other}"]:not([data-virtual-card="true"])').click()
        self.page.wait_for_function(
            "id => state.editingId === id && state.activeCardIsFull", arg=other
        )
        self.assertEqual(
            self.runtime.service.get_card({"card_id": first})["card"]["title"],
            "SYNTHETIC MOBILE ORIGINAL",
        )

    def test_desktop_modal_blocks_mobile_open_until_closed(self):
        first = self.create_card("SYNTHETIC DESKTOP LOCK")
        other = self.create_card("SYNTHETIC MOBILE BLOCKED")
        self.load_board()
        self.open_card(first)
        self.page.locator("#cardTitle").fill("SYNTHETIC DESKTOP DRAFT")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.evaluate("id => { openMobileCardDetail(id); }", other)
        self.assertFalse(self.page.evaluate("Boolean(state.mobileCardId)"))
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC DESKTOP DRAFT")
        self.assertEqual(self.page.evaluate("state.editingId"), first)

    def test_mobile_file_write_blocks_desktop_card_until_finished(self):
        other = self.create_card("SYNTHETIC DESKTOP AFTER FILE")
        first = self.open_existing()
        self.page.locator('[data-mobile-card-tab="files"]').click()
        held = []
        self.page.route("**/api/add_card_attachment", lambda route: held.append(route))
        self.page.locator("#mobileCardFileInput").set_input_files(
            {"name": "synthetic.txt", "mimeType": "text/plain", "buffer": b"synthetic"}
        )
        self.page.wait_for_function("state.mobileCardFilesBusy")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.locator(f'.card[data-card-id="{other}"]:not([data-virtual-card="true"])').click()
        self.assertFalse(self.page.locator("#cardModal").is_visible())
        held.pop().continue_()
        self.page.wait_for_function("!state.mobileCardFilesBusy")
        self.assertEqual(self.page.evaluate("state.mobileCardId"), first)
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator(f'.card[data-card-id="{other}"]:not([data-virtual-card="true"])').click()
        self.page.wait_for_function(
            "id => state.editingId === id && state.activeCardIsFull", arg=other
        )
        self.assertEqual(
            self.runtime.service.get_card({"card_id": first})["card"]["title"],
            "SYNTHETIC MOBILE ORIGINAL",
        )

    def test_mobile_order_open_blocks_desktop_card_until_finished(self):
        first = self.create_card("SYNTHETIC MOBILE ORDER A")
        other = self.create_card("SYNTHETIC DESKTOP ORDER B")
        self.runtime.service.get_repair_order({"card_id": first, "create_if_missing": True})
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        held = []
        self.page.route("**/api/get_repair_order", lambda route: held.append(route))
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.locator(
            f'#mobileRepairOrdersList [data-open-repair-order-card="{first}"]'
        ).click()
        self.page.wait_for_function("state.mobileRepairOrderLoading")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.locator(f'.card[data-card-id="{other}"]:not([data-virtual-card="true"])').click()
        self.assertFalse(self.page.locator("#cardModal").is_visible())
        held.pop().continue_()
        self.page.wait_for_function("!state.mobileRepairOrderLoading")
        self.page.locator(f'.card[data-card-id="{other}"]:not([data-virtual-card="true"])').click()
        self.page.wait_for_function(
            "id => state.editingId === id && state.activeCardIsFull", arg=other
        )
        self.page.locator("#cardTitle").fill("SYNTHETIC ORDER B EDIT")
        self.page.locator("#saveCardButton").click()
        self.page.wait_for_function("!state.cardSaveInFlight")
        self.assertEqual(
            self.runtime.service.get_card({"card_id": first})["card"]["title"],
            "SYNTHETIC MOBILE ORDER A",
        )
        self.assertEqual(
            self.runtime.service.get_card({"card_id": other})["card"]["title"],
            "SYNTHETIC ORDER B EDIT",
        )

    def test_desktop_modal_blocks_mobile_order_open(self):
        first = self.create_card("SYNTHETIC DESKTOP ORDER LOCK")
        other = self.create_card("SYNTHETIC MOBILE ORDER BLOCKED")
        self.runtime.service.get_repair_order({"card_id": other, "create_if_missing": True})
        self.load_board()
        self.open_card(first)
        self.page.locator("#cardTitle").fill("SYNTHETIC DESKTOP ORDER DRAFT")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.evaluate("id => { openMobileRepairOrderDetail(id); }", other)
        self.assertFalse(self.page.evaluate("Boolean(state.mobileRepairOrderCardId)"))
        self.assertEqual(self.page.evaluate("state.editingId"), first)
        self.assertEqual(
            self.page.locator("#cardTitle").input_value(), "SYNTHETIC DESKTOP ORDER DRAFT"
        )

    def test_mobile_order_save_blocks_desktop_and_preserves_id(self):
        first = self.create_card("SYNTHETIC MOBILE ORDER SAVE A")
        other = self.create_card("SYNTHETIC DESKTOP ORDER SAVE B")
        self.runtime.service.get_repair_order({"card_id": first, "create_if_missing": True})
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.locator(
            f'#mobileRepairOrdersList [data-open-repair-order-card="{first}"]'
        ).click()
        self.page.wait_for_function("!state.mobileRepairOrderLoading")
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC ORDER DRAFT")
        held = []
        self.page.route("**/api/update_repair_order", lambda route: held.append(route))
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("state.mobileRepairOrderSaving")
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.locator(f'.card[data-card-id="{other}"]:not([data-virtual-card="true"])').click()
        self.assertFalse(self.page.locator("#cardModal").is_visible())
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC ORDER DRAFT"
        )
        held.pop().continue_()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.page.locator(f'.card[data-card-id="{other}"]:not([data-virtual-card="true"])').click()
        self.page.wait_for_function(
            "id => state.editingId === id && state.activeCardIsFull", arg=other
        )
        self.page.locator("#cardTitle").fill("SYNTHETIC DESKTOP B EDIT")
        self.page.locator("#saveCardButton").click()
        self.page.wait_for_function("!state.cardSaveInFlight")
        self.assertEqual(
            self.runtime.service.get_card({"card_id": first})["card"]["title"],
            "SYNTHETIC MOBILE ORDER SAVE A",
        )
        self.assertEqual(
            self.runtime.service.get_card({"card_id": other})["card"]["title"],
            "SYNTHETIC DESKTOP B EDIT",
        )

    def test_dirty_mobile_order_requires_confirmation_before_desktop_open(self):
        first = self.create_card("SYNTHETIC MOBILE ORDER DRAFT A")
        other = self.create_card("SYNTHETIC DESKTOP ORDER DRAFT B")
        self.runtime.service.get_repair_order({"card_id": first, "create_if_missing": True})
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.locator(
            f'#mobileRepairOrdersList [data-open-repair-order-card="{first}"]'
        ).click()
        self.page.wait_for_function("!state.mobileRepairOrderLoading")
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC ORDER UNSAVED")
        self.page.set_viewport_size({"width": 1440, "height": 960})
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator(f'.card[data-card-id="{other}"]:not([data-virtual-card="true"])').click()
        self.assertFalse(self.page.locator("#cardModal").is_visible())
        self.assertEqual(self.page.evaluate("state.mobileRepairOrderCardId"), first)
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC ORDER UNSAVED"
        )
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator(f'.card[data-card-id="{other}"]:not([data-virtual-card="true"])').click()
        self.page.wait_for_function(
            "id => state.editingId === id && state.activeCardIsFull", arg=other
        )
        self.assertEqual(self.page.evaluate("state.mobileRepairOrderCardId"), "")

    def test_late_desktop_order_open_cannot_replace_mobile_order(self):
        first = self.create_card("SYNTHETIC MOBILE LATE A")
        other = self.create_card("SYNTHETIC DESKTOP LATE B")
        for card in (first, other):
            self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.load_board()
        held = []

        def delay_desktop(route):
            if route.request.post_data_json["card_id"] == other:
                held.append(route)
            else:
                route.continue_()

        self.page.route("**/api/get_repair_order", delay_desktop)
        self.page.evaluate("id => { openRepairOrderCard(id); }", other)
        self.page.wait_for_timeout(150)
        self.assertEqual(len(held), 1)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.evaluate("id => { openMobileRepairOrderDetail(id); }", first)
        self.page.wait_for_function(
            "id => state.mobileRepairOrderCardId === id && !state.mobileRepairOrderLoading",
            arg=first,
        )
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC LATE DRAFT")
        held.pop().continue_()
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.evaluate("state.editingId"), first)
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(), "SYNTHETIC LATE DRAFT"
        )
        self.assertFalse(self.page.locator("#repairOrderModal").is_visible())

    def test_partial_vehicle_profile_after_create_keeps_marker(self):
        self.open_new()
        created = []

        def partial(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            payload = response.json()
            created.append(payload["data"]["card"]["id"])
            payload["data"]["card"]["vehicle_profile"] = {}
            route.fulfill(json=payload)

        self.page.route("**/api/create_card", partial)
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.assertTrue(self.page.evaluate("state.mobileCardCreating"))
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE NEW"
        )
        self.page.locator("#mobileCardSaveButton").click()
        self.assertEqual(len(created), 1)

    def test_partial_vehicle_profile_after_update_blocks_retry(self):
        card = self.open_existing()

        def partial(route):
            payload = route.fetch().json()
            payload["data"]["card"]["vehicle_profile"] = {}
            route.fulfill(json=payload)

        self.page.route("**/api/update_card", partial)
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE EDIT"
        )
        self.assertTrue(
            self.page.evaluate("id => state.mobileCardWriteVerificationPending.has(id)", card)
        )
        self.assertTrue(self.page.locator("#mobileCardSaveButton").is_disabled())

    def test_null_vehicle_metadata_in_response_blocks_retry(self):
        card = self.open_existing()

        def partial(route):
            payload = route.fetch().json()
            payload["data"]["card"]["vehicle_profile"]["manual_fields"] = None
            route.fulfill(json=payload)

        self.page.route("**/api/update_card", partial)
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertTrue(
            self.page.evaluate("id => state.mobileCardWriteVerificationPending.has(id)", card)
        )
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE EDIT"
        )

    def test_partial_card_response_keeps_draft_and_blocks_retry(self):
        card = self.open_existing()
        checks = self.page.evaluate("""() => {
          const card = state.mobileCard;
          const missing = ['client_id', 'client_vehicle_id', 'vehicle_raw', 'created_at',
            'deadline_timestamp', 'deadline_total_seconds', 'timer_state', 'archived', 'attachments', 'repair_order'];
          return { full: mobileCardHasRequiredFields(card), missing: missing.map(key => {
            const partial = { ...card }; delete partial[key]; return mobileCardHasRequiredFields(partial);
          }), brokenOrder: mobileCardHasRequiredFields({ ...card, repair_order: {} }) };
        }""")
        self.assertTrue(checks["full"])
        self.assertFalse(any(checks["missing"]))
        self.assertFalse(checks["brokenOrder"])

        def partial(route):
            payload = route.fetch().json()
            del payload["data"]["card"]["client_id"]
            route.fulfill(json=payload)

        self.page.route("**/api/update_card", partial)
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertTrue(
            self.page.evaluate("id => state.mobileCardWriteVerificationPending.has(id)", card)
        )
        self.assertEqual(
            self.page.locator("#mobileCardTitleInput").input_value(), "SYNTHETIC MOBILE EDIT"
        )
        self.assertTrue(self.page.locator("#mobileCardSaveButton").is_disabled())

    def test_create_vehicle_profile_label_accepts_server_normalization(self):
        self.open_new()
        self.page.locator('[data-mobile-card-tab="vehicle"]').click()
        self.page.locator('[data-mobile-vehicle-field="display_name"]').fill("Toyota  Camry")
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertFalse(self.page.evaluate("state.mobileCardCreating"))
        self.assertIsNone(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker))
        card = self.runtime.service.get_card({"card_id": self.page.evaluate("state.mobileCardId")})[
            "card"
        ]
        self.assertEqual(card["vehicle"], "Toyota Camry")
        self.assertEqual(card["vehicle_profile"]["make_display"], "Toyota")
        self.assertEqual(card["vehicle_profile"]["model_display"], "Camry")

    def test_update_vehicle_profile_label_accepts_server_normalization(self):
        card_id = self.create_card("SYNTHETIC VEHICLE LABEL")
        self.runtime.service.update_card(
            {
                "card_id": card_id,
                "vehicle_profile": {"make_display": "Toyota", "model_display": "Camry"},
            }
        )
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator(f'[data-mobile-card-id="{card_id}"]').click()
        self.page.wait_for_function("!state.mobileCardLoading && !!state.mobileCardId")
        self.page.locator('[data-mobile-card-tab="vehicle"]').click()
        self.page.locator('[data-mobile-vehicle-field="display_name"]').fill("Honda   Civic")
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertFalse(
            self.page.evaluate("id => state.mobileCardWriteVerificationPending.has(id)", card_id)
        )
        card = self.runtime.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(card["vehicle"], "Honda Civic")
        self.assertEqual(card["vehicle_profile"]["make_display"], "Honda")
        self.assertEqual(card["vehicle_profile"]["model_display"], "Civic")

    def test_create_vehicle_label_accepts_server_whitespace_normalization(self):
        self.open_new()
        self.page.locator("#mobileCardVehicleInput").fill("Toyota   Camry")
        self.page.locator("#mobileCardSaveButton").click()
        self.page.wait_for_function("!state.mobileCardSaving")
        self.assertFalse(self.page.evaluate("state.mobileCardCreating"))
        self.assertIsNone(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker))
        card = self.runtime.service.get_card({"card_id": self.page.evaluate("state.mobileCardId")})[
            "card"
        ]
        self.assertEqual(card["vehicle"], "Toyota Camry")
