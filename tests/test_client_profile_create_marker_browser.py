"""A shared create marker protects standalone and card client creation."""

from tests.browser_ui_support import UiBrowserTestCase


class ClientProfileCreateMarkerBrowserTests(UiBrowserTestCase):
    marker = "autostop:card-client-create-pending"

    def open_profile(self, name):
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientsModal.is-open").wait_for()
        self.page.locator("#clientNewButton").click()
        self.page.locator("#clientTypeInput").select_option("company")
        self.page.locator("#clientDisplayNameInput").fill(name)

    def save(self):
        self.page.evaluate("async () => { await saveClientProfile(); }")

    def open_popup(self, card):
        self.page.evaluate("id => openCardById(id)", card)
        self.page.wait_for_function(
            "id => state.editingId === id && state.activeCardIsFull", arg=card
        )
        self.page.locator("[data-open-card-client-create]").click()
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC CARD CLIENT")

    def test_standalone_unknown_blocks_card_and_reload(self):
        card = self.create_card("SYNTHETIC CROSS FLOW")
        self.load_board()
        self.open_profile("SYNTHETIC STANDALONE")
        self.page.route(
            "**/api/create_client", lambda route: (route.fetch(), route.abort("failed"))
        )
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.page.locator('[data-close="clients"]').click()
        self.open_popup(card)
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertEqual(self.writes.count("create_client"), 1)
        self.page.reload()
        self.page.wait_for_function(
            "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
        )
        self.page.evaluate("stopSnapshotPolling()")
        self.open_profile("SYNTHETIC OTHER")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "SYNTHETIC OTHER"
        )

    def test_card_unknown_blocks_standalone(self):
        card = self.create_card("SYNTHETIC REVERSE FLOW")
        self.load_board()
        self.open_popup(card)
        self.page.route(
            "**/api/create_client", lambda route: (route.fetch(), route.abort("failed"))
        )
        self.page.locator("#cardClientCreateSaveButton").click()
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.page.locator('#cardClientCreateModal [data-close="card-client-create"]').first.click()
        self.page.locator('[data-close="card"]').first.click()
        self.open_profile("SYNTHETIC REVERSE CLIENT")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)

    def test_confirmed_receipt_survives_storage_remove_failure(self):
        self.load_board()
        self.open_profile("SYNTHETIC RECEIPT")
        self.page.evaluate(
            """key => {const old=Storage.prototype.removeItem;
          Storage.prototype.removeItem=function(k){if(k===key) throw Error('denied');return old.call(this,k)};
          window.restoreSyntheticRemove=()=>Storage.prototype.removeItem=old;}""",
            self.marker,
        )
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        saved_id = self.page.evaluate("state.clientProfileSaveCheckpoint?.clientId")
        self.assertTrue(saved_id)
        self.assertTrue(self.page.evaluate("state.clientProfileSaveCheckpoint?.uncertain"))
        self.page.evaluate("() => { window.restoreSyntheticRemove(); }")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertIsNone(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker))
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), saved_id)

    def test_confirmed_create_or_duplicate_keeps_changed_draft(self):
        self.runtime.service.create_client(
            {
                "client_type": "company",
                "display_name": "SYNTHETIC DUPLICATE",
                "phone": "+79991234567",
            }
        )
        self.load_board()
        for name, expected in (("SYNTHETIC NEW", "СОЗДАН"), ("SYNTHETIC DUPLICATE", "НАЙДЕН")):
            with self.subTest(expected=expected):
                if expected == "НАЙДЕН":
                    self.page.reload()
                    self.page.wait_for_function(
                        "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
                    )
                    self.page.evaluate("stopSnapshotPolling()")
                self.open_profile(name)
                self.page.locator("#clientPhoneInput").fill("+79991234567")
                self.page.evaluate(
                    """key => {const old=Storage.prototype.removeItem;
                  Storage.prototype.removeItem=function(k){if(k===key) throw Error('denied');return old.call(this,k)};
                  window.restoreSyntheticRemove=()=>Storage.prototype.removeItem=old;}""",
                    self.marker,
                )
                before = self.writes.count("create_client")
                self.save()
                self.assertEqual(self.writes.count("create_client"), before + 1)
                self.assertEqual(
                    self.page.evaluate("state.clientProfileSaveCheckpoint.receipt.meta.created"),
                    expected == "СОЗДАН",
                )
                self.page.locator("#clientDisplayNameInput").fill("SYNTHETIC UNSAVED DRAFT")
                self.page.evaluate("() => { window.restoreSyntheticRemove(); }")
                self.save()
                self.assertEqual(self.writes.count("create_client"), before + 1)
                self.assertEqual(
                    self.page.locator("#clientDisplayNameInput").input_value(),
                    "SYNTHETIC UNSAVED DRAFT",
                )
                self.assertIn(expected, self.page.locator("#statusLine").inner_text())
                self.assertIsNone(
                    self.page.evaluate("key => sessionStorage.getItem(key)", self.marker)
                )
                self.page.locator('[data-close="clients"]').click()

    def test_storage_denial_blocks_only_create(self):
        client = self.runtime.service.create_client(
            {"client_type": "company", "display_name": "SYNTHETIC EDIT"}
        )["client"]["id"]
        self.load_board()
        self.open_profile("SYNTHETIC BLOCKED")
        self.page.evaluate(
            """key => {const old=Storage.prototype.setItem;
          Storage.prototype.setItem=function(k,v){if(k===key) throw Error('denied');return old.call(this,k,v)};}""",
            self.marker,
        )
        self.save()
        self.assertEqual(self.writes.count("create_client"), 0)
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "SYNTHETIC BLOCKED"
        )
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator(f'#clientsList [data-client-id="{client}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client
        )
        self.page.locator("#clientDisplayNameInput").fill("SYNTHETIC EDITED")
        self.save()
        self.assertEqual(self.writes.count("update_client"), 1)

    def test_ambiguous_responses_and_owner_change_keep_guard(self):
        self.load_board()
        self.open_profile("SYNTHETIC AMBIGUOUS")
        for status in (408, 429, 200):
            with self.subTest(status=status):
                self.page.route(
                    "**/api/create_client",
                    lambda route: route.fulfill(
                        status=status,
                        json={
                            "ok": status == 200,
                            "data": {
                                "client": {"id": "synthetic", "updated_at": "2026-01-01"},
                                "meta": {"created": True},
                            },
                        },
                    ),
                )
                self.save()
                count = self.writes.count("create_client")
                self.save()
                self.assertEqual(self.writes.count("create_client"), count)
                self.assertEqual(
                    self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1"
                )
                self.page.unroute("**/api/create_client")
                self.page.evaluate("key => sessionStorage.removeItem(key)", self.marker)
                self.page.reload()
                self.page.wait_for_function(
                    "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
                )
                self.page.evaluate("stopSnapshotPolling()")
                self.open_profile("SYNTHETIC AMBIGUOUS")

    def test_late_success_after_logout_keeps_marker(self):
        self.load_board()
        self.open_profile("SYNTHETIC OLD OWNER")
        held = []
        self.page.route("**/api/create_client", lambda route: held.append(route))
        with self.page.expect_request("**/api/create_client"):
            self.page.locator("#clientSaveButton").click()
        self.page.evaluate("clearOperatorSession()")
        held.pop().continue_()
        self.page.wait_for_timeout(150)
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.assertEqual(self.writes.count("create_client"), 1)
