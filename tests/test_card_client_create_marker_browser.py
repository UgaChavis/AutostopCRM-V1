"""Client creation from a card retains an opaque guard across uncertain outcomes."""

from tests.browser_ui_support import UiBrowserTestCase


class CardClientCreateMarkerBrowserTests(UiBrowserTestCase):
    marker = "autostop:card-client-create-pending"

    def open_popup(self, card):
        self.page.evaluate("id => openCardById(id)", card)
        self.page.wait_for_function(
            "id => state.editingId === id && state.activeCardIsFull", arg=card
        )
        self.page.locator("[data-open-card-client-create]").click()
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC CLIENT WITHOUT PHONE")
        self.page.locator("#cardClientCreateCommentInput").fill("SYNTHETIC PRIVATE NOTE")

    def test_confirmed_client_continues_same_checkpoint_after_link_rejection(self):
        card = self.create_card("SYNTHETIC RETRY CARD")
        self.load_board()
        self.open_popup(card)
        link_calls = []

        def link(route):
            link_calls.append(1)
            if len(link_calls) == 1:
                route.fulfill(
                    status=400,
                    json={
                        "ok": False,
                        "error": {"code": "validation_error", "message": "SYNTHETIC REJECT"},
                    },
                )
            else:
                route.continue_()

        self.page.route("**/api/link_card_to_client", link)
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.assertTrue(self.page.evaluate("Boolean(state.cardClientCreateCheckpoint?.clientId)"))
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(len(link_calls), 2)
        self.assertIsNone(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker))
        self.assertTrue(self.stored_card(card)["client_id"])

    def test_lost_response_blocks_reopen_reload_and_new_session(self):
        card = self.create_card("SYNTHETIC UNKNOWN CARD")
        self.load_board()
        self.open_popup(card)
        attempts = []

        def lost(route):
            attempts.append(1)
            self.assertEqual(
                self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1"
            )
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/create_client", lost)
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(
            self.page.locator("#cardClientCreateCommentInput").input_value(),
            "SYNTHETIC PRIVATE NOTE",
        )
        self.page.locator('#cardClientCreateModal [data-close="card-client-create"]').first.click()
        self.page.locator("[data-open-card-client-create]").click()
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC CLIENT WITHOUT PHONE")
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertEqual(len(attempts), 1)
        self.page.reload()
        self.page.wait_for_function(
            "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
        )
        self.page.evaluate("stopSnapshotPolling()")
        self.open_popup(card)
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertEqual(len(attempts), 1)
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        ).json()["data"]["session"]["token"]
        self.page.evaluate("token => setOperatorSessionToken(token, {persist:false})", token)
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.assertNotIn("SYNTHETIC", self.marker)
        self.assertNotIn(self.session_token, self.marker)

    def test_storage_unavailable_stops_before_post(self):
        card = self.create_card("SYNTHETIC STORAGE CARD")
        self.load_board()
        self.open_popup(card)
        self.page.evaluate(
            """key => {const old=Storage.prototype.setItem; Storage.prototype.setItem=function(k,v) {
          if(k===key) throw new Error('synthetic storage denied');return old.call(this,k,v);};}""",
            self.marker,
        )
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertEqual(self.writes.count("create_client"), 0)
        self.assertEqual(
            self.page.locator("#cardClientCreateCommentInput").input_value(),
            "SYNTHETIC PRIVATE NOTE",
        )

    def test_explicit_close_after_proven_link_rejection_allows_another_client(self):
        card = self.create_card("SYNTHETIC EXPLICIT CLOSE")
        self.load_board()
        self.open_popup(card)
        calls = []

        def reject_once(route):
            calls.append(1)
            if len(calls) == 1:
                route.fulfill(
                    status=400,
                    json={
                        "ok": False,
                        "error": {"code": "validation_error", "message": "SYNTHETIC REJECT"},
                    },
                )
            else:
                route.continue_()

        self.page.route("**/api/link_card_to_client", reject_once)
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        first_id = self.page.evaluate("state.cardClientCreateCheckpoint.clientId")
        self.assertTrue(first_id)
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.page.locator('#cardClientCreateModal [data-close="card-client-create"]').first.click()
        self.assertIsNone(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker))
        self.assertIn("КЛИЕНТ СОЗДАН", self.page.locator("#statusLine").inner_text())
        self.page.locator("[data-open-card-client-create]").click()
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC DIFFERENT CLIENT")
        with self.page.expect_response("**/api/create_client") as response:
            self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertNotEqual(response.value.json()["data"]["client"]["id"], first_id)
        self.assertEqual(self.writes.count("create_client"), 2)
        self.assertIsNone(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker))

    def test_explicit_close_retains_protection_if_storage_remove_fails(self):
        card = self.create_card("SYNTHETIC CLOSE STORAGE")
        self.load_board()
        self.open_popup(card)
        self.page.route(
            "**/api/link_card_to_client",
            lambda route: route.fulfill(
                status=400,
                json={
                    "ok": False,
                    "error": {"code": "validation_error", "message": "SYNTHETIC REJECT"},
                },
            ),
        )
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.page.evaluate(
            """key => {const old=Storage.prototype.removeItem; Storage.prototype.removeItem=function(k) {
            if(k===key) throw new Error('synthetic storage denied');return old.call(this,k);};}""",
            self.marker,
        )
        self.page.locator('#cardClientCreateModal [data-close="card-client-create"]').first.click()
        self.assertTrue(
            self.page.locator("#cardClientCreateModal").evaluate(
                "e=>e.classList.contains('is-open')"
            )
        )
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.assertEqual(
            self.page.locator("#cardClientCreateCommentInput").input_value(),
            "SYNTHETIC PRIVATE NOTE",
        )
        self.assertEqual(self.writes.count("create_client"), 1)

    def test_explicit_close_after_real_card_conflict_allows_new_creation(self):
        card = self.create_card("SYNTHETIC REAL CONFLICT")
        self.load_board()
        self.open_popup(card)
        statuses = []

        def concurrent_edit(route):
            self.runtime.service.update_card({"card_id": card, "title": "SYNTHETIC OTHER OPERATOR"})
            response = route.fetch()
            statuses.append(response.status)
            route.fulfill(response=response)

        self.page.route("**/api/link_card_to_client", concurrent_edit)
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertEqual(statuses, [409])
        self.assertTrue(self.page.evaluate("state.cardClientCreateCheckpoint.linkRejected"))
        self.page.locator('#cardClientCreateModal [data-close="card-client-create"]').first.click()
        self.assertIsNone(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker))
        self.assertIn("КЛИЕНТ СОЗДАН", self.page.locator("#statusLine").inner_text())
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.stored_card(card)["title"], "SYNTHETIC OTHER OPERATOR")

    def test_missing_card_version_allows_explicit_close_and_new_client(self):
        card = self.create_card("SYNTHETIC MISSING VERSION")
        self.load_board()
        self.open_popup(card)
        version = self.page.evaluate(
            "() => { const v = state.activeCard.updated_at; state.activeCard.updated_at = ''; return v; }"
        )
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        first_id = self.page.evaluate("state.cardClientCreateCheckpoint.clientId")
        self.assertTrue(first_id)
        self.assertTrue(self.page.evaluate("state.cardClientCreateCheckpoint.linkRejected"))
        self.assertEqual(self.writes.count("link_card_to_client"), 0)
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.page.locator('#cardClientCreateModal [data-close="card-client-create"]').first.click()
        self.assertIsNone(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker))
        self.assertIn("КЛИЕНТ СОЗДАН", self.page.locator("#statusLine").inner_text())
        self.page.evaluate("version => state.activeCard.updated_at = version", version)
        self.page.locator("[data-open-card-client-create]").click()
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC NEW CLIENT")
        with self.page.expect_response("**/api/create_client") as response:
            self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertNotEqual(response.value.json()["data"]["client"]["id"], first_id)
        self.assertEqual(self.writes.count("create_client"), 2)
        self.assertTrue(self.stored_card(card)["client_id"])

    def test_partial_link_marker_retains_create_guard(self):
        card = self.create_card("SYNTHETIC PARTIAL LINK MARKER")
        self.load_board()
        self.open_popup(card)
        self.page.evaluate("""() => {
          const old = Storage.prototype.setItem;
          Storage.prototype.setItem = function(k, v) {
            old.call(this, k, v);
            if (k.startsWith('autostop:card-link:')) throw new Error('synthetic partial storage');
          };
        }""")
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertTrue(self.page.evaluate("Boolean(state.cardClientCreateCheckpoint.clientId)"))
        self.assertFalse(self.page.evaluate("state.cardClientCreateCheckpoint.linkRejected"))
        self.assertEqual(self.writes.count("link_card_to_client"), 0)
        self.page.locator('#cardClientCreateModal [data-close="card-client-create"]').first.click()
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.assertEqual(self.writes.count("create_client"), 1)
