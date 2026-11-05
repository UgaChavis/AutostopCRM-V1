"""A duplicate client receipt must not link a card or discard entered fields."""

from tests.browser_ui_support import UiBrowserTestCase


class CardClientDuplicateDraftBrowserTests(UiBrowserTestCase):
    marker = "autostop:card-client-create-pending"

    def prepare(self, *, new_card=False):
        client = self.runtime.service.create_client(
            {
                "client_type": "person",
                "display_name": "SYNTHETIC DUPLICATE",
                "phone": "+79991234567",
                "comment": "SYNTHETIC OLD",
            }
        )["client"]["id"]
        card = "" if new_card else self.create_card("SYNTHETIC CARD")
        self.load_board()
        if card:
            self.open_card(card)
        else:
            self.page.locator("[data-create-in]").first.click()
            self.page.locator("#cardTitle").fill("SYNTHETIC NEW CARD")
        return client, card

    def popup(self, *, vehicle=False):
        if vehicle:
            self.page.locator("#vehicleField_display_name").fill("SYNTHETIC VEHICLE")
        self.page.locator("[data-open-card-client-create]").click()
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC DUPLICATE")
        self.page.locator("[data-card-client-phone-input]").first.fill("+79991234567")
        self.page.locator("#cardClientCreateCommentInput").fill("SYNTHETIC NEW")

    def save(self):
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")

    def assert_no_followup_writes(self):
        for action in ("update_client", "link_card_to_client", "upsert_client_vehicle"):
            self.assertEqual(self.writes.count(action), 0, action)

    def test_existing_card_keeps_duplicate_draft_and_allows_changed_identity(self):
        client, card = self.prepare()
        self.popup()
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assert_no_followup_writes()
        self.assertFalse(self.stored_card(card)["client_id"])
        self.assertTrue(self.page.locator("#cardClientCreateModal").is_visible())
        self.assertEqual(
            self.page.locator("#cardClientCreateCommentInput").input_value(), "SYNTHETIC NEW"
        )
        self.assertIn("НЕ СОХРАНЕНЫ", self.page.locator("#statusLine").inner_text())
        self.page.locator("#cardClientCreateCommentInput").fill("SYNTHETIC CHANGED")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assert_no_followup_writes()
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC DISTINCT")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 2)
        self.assertEqual(
            self.writes.count("link_card_to_client"),
            1,
            self.page.locator("#statusLine").inner_text(),
        )
        self.assertEqual(self.writes.count("update_client"), 0)
        self.assertNotEqual(self.stored_card(card)["client_id"], client)
        self.assertEqual(
            self.runtime.service.get_client({"client_id": client})["client"]["comment"],
            "SYNTHETIC OLD",
        )

    def test_new_card_does_not_create_vehicle_until_identity_changes(self):
        client, _ = self.prepare(new_card=True)
        self.popup(vehicle=True)
        self.assertTrue(self.page.locator("#cardClientCreateVehicleInput").is_checked())
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assert_no_followup_writes()
        self.page.locator("[data-card-client-phone-input]").first.fill("+79997654321")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 2)
        self.assertEqual(self.writes.count("upsert_client_vehicle"), 1)
        self.assertNotEqual(self.page.evaluate("state.pendingCardClientId"), client)
        self.assertEqual(
            self.runtime.service.get_client({"client_id": client})["client"]["comment"],
            "SYNTHETIC OLD",
        )

    def test_marker_failure_keeps_receipt_and_explicit_cancel_recovers(self):
        self.prepare()
        self.popup()
        self.page.evaluate(
            """key => { const old=Storage.prototype.removeItem;
          Storage.prototype.removeItem=function(k){if(k===key) throw Error('denied');return old.call(this,k)};
          window.restoreSyntheticRemove=()=>Storage.prototype.removeItem=old;}""",
            self.marker,
        )
        self.save()
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC DISTINCT")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assert_no_followup_writes()
        self.page.locator('#cardClientCreateModal [data-close="card-client-create"]').first.click()
        self.assertTrue(self.page.locator("#cardClientCreateModal").is_visible())
        self.page.evaluate("() => window.restoreSyntheticRemove()")
        self.page.locator('#cardClientCreateModal [data-close="card-client-create"]').first.click()
        self.assertFalse(self.page.locator("#cardClientCreateModal").is_visible())
        self.assertIsNone(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker))
        self.assert_no_followup_writes()

    def test_changed_identity_waits_for_marker_and_another_save(self):
        self.prepare()
        self.popup()
        self.page.evaluate(
            """key => { const old=Storage.prototype.removeItem;
          Storage.prototype.removeItem=function(k){if(k===key) throw Error('denied');return old.call(this,k)};
          window.restoreSyntheticRemove=()=>Storage.prototype.removeItem=old;}""",
            self.marker,
        )
        self.save()
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC MARKER DISTINCT")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.page.evaluate("() => window.restoreSyntheticRemove()")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertIsNone(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker))
        self.save()
        self.assertEqual(self.writes.count("create_client"), 2)
        self.assertEqual(self.writes.count("link_card_to_client"), 1)
        self.assertEqual(self.writes.count("update_client"), 0)

    def test_incomplete_duplicate_receipt_retains_marker_and_never_links(self):
        self.prepare()
        self.popup()

        def incomplete(route):
            response = route.fetch().json()
            response["data"]["meta"].pop("duplicate_of")
            route.fulfill(json=response)

        self.page.route("**/api/create_client", incomplete)
        self.save()
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assert_no_followup_writes()
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.assertTrue(self.page.locator("#cardClientCreateModal").is_visible())

    def test_suggestion_keeps_card_draft_and_never_links_duplicate(self):
        client, card = self.prepare()
        self.page.locator("#vehicleField_customer_name").fill("SYNTHETIC DUPLICATE")
        self.page.locator("#vehicleCustomerPhoneFields [data-vehicle-phone-input]").first.fill(
            "+79991234567"
        )
        self.page.evaluate("async () => { await createClientFromCardSuggestion(); }")
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assert_no_followup_writes()
        self.assertFalse(self.stored_card(card)["client_id"])
        self.page.evaluate("async () => { await createClientFromCardSuggestion(); }")
        self.assertEqual(self.writes.count("create_client"), 1)
        self.page.locator("#vehicleField_customer_name").fill("SYNTHETIC SUGGESTION DISTINCT")
        self.page.evaluate("async () => { await createClientFromCardSuggestion(); }")
        self.assertEqual(self.writes.count("create_client"), 2)
        self.assertEqual(
            self.writes.count("link_card_to_client"),
            1,
            self.page.locator("#statusLine").inner_text(),
        )
        self.assertNotEqual(self.stored_card(card)["client_id"], client)
