"""A malformed duplicate receipt must not clear an uncertain create marker."""

from tests.browser_ui_support import UiBrowserTestCase


class ClientDuplicateReceiptBrowserTests(UiBrowserTestCase):
    def check_incomplete_receipt(self, variant):
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientNewButton").click()
        self.page.locator("#clientTypeInput").select_option("company")
        self.page.locator("#clientDisplayNameInput").fill("SYNTHETIC RECEIPT " + variant)
        self.page.locator("#clientCommentInput").fill("SYNTHETIC DRAFT")
        created = []

        def corrupt(route):
            response = route.fetch().json()
            self.assertTrue(response["data"]["meta"]["created"])
            client_id = response["data"]["client"]["id"]
            created.append(client_id)
            response["data"]["meta"] = {
                "created": False,
                "duplicate": True,
                "duplicate_of": client_id,
            }
            if variant == "missing duplicate":
                response["data"]["meta"].pop("duplicate")
            elif variant == "missing id":
                response["data"]["meta"].pop("duplicate_of")
            else:
                response["data"]["meta"]["duplicate_of"] = "different-id"
            route.fulfill(json=response)

        self.page.route("**/api/create_client", corrupt)
        self.page.evaluate("async () => { await saveClientProfile(); }")
        self.assertEqual(len(created), 1)
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(
            self.page.evaluate("sessionStorage.getItem('autostop:card-client-create-pending')"), "1"
        )
        self.assertIn("НЕ ПОДТВЕРДИЛ", self.page.locator("#statusLine").inner_text())
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC DRAFT")
        self.page.locator("#clientDisplayNameInput").fill("SYNTHETIC CHANGED " + variant)
        self.page.evaluate("async () => { await saveClientProfile(); }")
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.writes.count("update_client"), 0)
        self.assertEqual(
            self.page.evaluate("sessionStorage.getItem('autostop:card-client-create-pending')"), "1"
        )
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC DRAFT")
        self.assertEqual(
            self.runtime.service.get_client({"client_id": created[0]})["client"]["comment"],
            "SYNTHETIC DRAFT",
        )

    def test_missing_duplicate_flag(self):
        self.check_incomplete_receipt("missing duplicate")

    def test_missing_duplicate_id(self):
        self.check_incomplete_receipt("missing id")

    def test_mismatched_duplicate_id(self):
        self.check_incomplete_receipt("wrong id")
