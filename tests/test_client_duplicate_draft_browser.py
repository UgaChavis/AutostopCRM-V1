"""A duplicate create receipt must not discard or overwrite the entered draft."""

from tests.browser_ui_support import UiBrowserTestCase


class ClientDuplicateDraftBrowserTests(UiBrowserTestCase):
    marker = "autostop:card-client-create-pending"

    def prepare_duplicate(self):
        client = self.runtime.service.create_client(
            {
                "client_type": "company",
                "display_name": "SYNTHETIC DUPLICATE",
                "phone": "+79991234567",
                "comment": "SYNTHETIC OLD",
            }
        )["client"]["id"]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientNewButton").click()
        self.page.locator("#clientTypeInput").select_option("company")
        self.page.locator("#clientDisplayNameInput").fill("SYNTHETIC DUPLICATE")
        self.page.locator("#clientPhoneInput").fill("+79991234567")
        self.page.locator("#clientCommentInput").fill("SYNTHETIC NEW")
        return client

    def save(self):
        self.page.evaluate("async () => { await saveClientProfile(); }")

    def test_duplicate_keeps_draft_until_explicit_selection(self):
        client = self.prepare_duplicate()
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC NEW")
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), "")
        self.assertTrue(self.page.evaluate("clientHasDraft()"))
        self.assertIn("НЕ СОХРАНЕНЫ", self.page.locator("#statusLine").inner_text())
        self.assertIsNone(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker))
        self.save()
        self.page.locator("#clientCommentInput").fill("SYNTHETIC NEWER")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.writes.count("update_client"), 0)
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC NEWER")
        self.assertEqual(
            self.runtime.service.get_client({"client_id": client})["client"]["comment"],
            "SYNTHETIC OLD",
        )
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator(f'#clientsList [data-client-id="{client}"]').click()
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC NEWER")
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator(f'#clientsList [data-client-id="{client}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client
        )
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC OLD")
        self.assertEqual(self.writes.count("update_client"), 0)

    def test_explicit_discard_allows_a_separate_new_client(self):
        self.prepare_duplicate()
        self.save()
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator("#clientNewButton").click()
        self.page.locator("#clientTypeInput").select_option("company")
        self.page.locator("#clientDisplayNameInput").fill("SYNTHETIC SEPARATE")
        self.page.locator("#clientCommentInput").fill("SYNTHETIC SEPARATE NOTE")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 2)
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC SEPARATE NOTE"
        )
        self.assertTrue(self.page.evaluate("Boolean(state.clientsActiveId)"))

    def test_changed_name_or_phone_allows_new_create_with_draft(self):
        client = self.prepare_duplicate()
        self.save()
        self.page.locator("#clientDisplayNameInput").fill("SYNTHETIC DISTINCT")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 2)
        self.assertEqual(self.writes.count("update_client"), 0)
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC NEW")
        self.assertNotEqual(self.page.evaluate("state.clientsActiveId"), client)
        self.page.locator("#clientNewButton").click()
        self.page.locator("#clientTypeInput").select_option("company")
        self.page.locator("#clientDisplayNameInput").fill("SYNTHETIC DUPLICATE")
        self.page.locator("#clientPhoneInput").fill("+79991234567")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 3)
        self.page.locator("#clientPhoneInput").fill("+79997654321")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 4)
        self.assertEqual(self.writes.count("update_client"), 0)
        self.assertNotEqual(self.page.evaluate("state.clientsActiveId"), client)
        self.assertEqual(
            self.runtime.service.get_client({"client_id": client})["client"]["comment"],
            "SYNTHETIC OLD",
        )

    def test_changed_identity_waits_for_marker_release_and_another_save(self):
        self.prepare_duplicate()
        self.page.evaluate(
            """key => {const old=Storage.prototype.removeItem;
          Storage.prototype.removeItem=function(k){if(k===key) throw Error('denied');return old.call(this,k)};
          window.restoreSyntheticRemove=()=>Storage.prototype.removeItem=old;}""",
            self.marker,
        )
        self.save()
        self.page.locator("#clientDisplayNameInput").fill("SYNTHETIC DISTINCT")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.page.evaluate("() => window.restoreSyntheticRemove()")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertIsNone(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker))
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "SYNTHETIC DISTINCT"
        )
        self.save()
        self.assertEqual(self.writes.count("create_client"), 2)
        self.assertEqual(self.writes.count("update_client"), 0)

    def test_failed_marker_removal_keeps_receipt_draft_and_recovery(self):
        client = self.prepare_duplicate()
        self.page.evaluate(
            """key => {const old=Storage.prototype.removeItem;
          Storage.prototype.removeItem=function(k){if(k===key) throw Error('denied');return old.call(this,k)};
          window.restoreSyntheticRemove=()=>Storage.prototype.removeItem=old;}""",
            self.marker,
        )
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker), "1")
        self.page.locator("#clientCommentInput").fill("SYNTHETIC NEWER")
        self.page.locator("#clientNewButton").click()
        self.page.locator(f'#clientsList [data-client-id="{client}"]').click()
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC NEWER")
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), "")
        self.page.evaluate("() => window.restoreSyntheticRemove()")
        self.save()
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertIsNone(self.page.evaluate("key => sessionStorage.getItem(key)", self.marker))
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC NEWER")
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator("#clientNewButton").click()
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "")
