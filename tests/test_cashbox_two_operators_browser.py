from __future__ import annotations

from tests.browser_ui_support import UiBrowserTestCase


class CashboxTwoOperatorsBrowserTests(UiBrowserTestCase):
    def setUp(self):
        super().setUp()
        self.viewer = {"X-Operator-Session": self.session_token}
        self.writer = self.browser.new_context(extra_http_headers=self.runtime.auth_headers)
        self.addCleanup(self.writer.close)
        credentials = {"username": "cashbox-test-writer", "password": "synthetic-password"}
        self.post(
            self.context,
            "save_operator_user",
            self.viewer,
            {**credentials, "role": "operator", "permissions": ["employees_cashboxes_access"]},
        )
        token = self.post(self.writer, "login_operator", {}, credentials)["session"]["token"]
        self.writer_headers = {"X-Operator-Session": token}
        listed = self.listed()
        self.first, self.second = [box["id"] for box in listed["cashboxes"][:2]]
        self.post(
            self.context,
            "mark_cashbox_notifications_seen",
            self.viewer,
            {
                "exact_boundary": True,
                "through_transaction_id": listed["notification"]["through_transaction_id"],
            },
        )
        self.load_board()
        self.rows = self.page.locator("#cashboxTransactions [data-cashbox-transaction-id]")
        self.more = self.page.locator("#cashboxTransactions [data-cashbox-transactions-load-more]")

    def post(self, context, route, headers, payload):
        response = context.request.post(
            self.runtime.base_url + "/api/" + route, headers=headers, data=payload
        )
        self.assertTrue(response.ok, f"{route} returned {response.status}")
        return response.json()["data"]

    def listed(self):
        return self.context.request.get(
            self.runtime.base_url + "/api/list_cashboxes?limit=200", headers=self.viewer
        ).json()["data"]

    def movement(self, box, note):
        revision = next(
            item["updated_at"] for item in self.listed()["cashboxes"] if item["id"] == box
        )
        return self.post(
            self.writer,
            "create_cash_transaction",
            self.writer_headers,
            {
                "cashbox_id": box,
                "note": note,
                "direction": "income",
                "amount_minor": 123,
                "expected_updated_at": revision,
            },
        )["transaction"]["id"]

    def wait_unread(self, ids):
        self.page.wait_for_function(
            """ids => {
              const n = state.cashboxNotification;
              return n?.unread_count === ids.length &&
                ids.every(id => n.unread_transactions.some(row => row.transaction_id === id));
            }""",
            arg=ids,
        )
        notification = self.listed()["notification"]
        self.assertCountEqual(
            [r["transaction_id"] for r in notification["unread_transactions"]], ids
        )

    def test_each_box_and_backdated_payment_stay_unread_until_shown(self):
        other = self.movement(self.second, "Other cashbox")
        first = self.movement(self.first, "Visible cashbox")
        self.page.locator("#cashboxesButton").click()
        self.page.locator(
            f'#cashboxTransactions [data-cashbox-transaction-id="{first}"]'
        ).wait_for()
        self.wait_unread([other])

        card = self.create_card("Synthetic backdated payment")
        self.post(
            self.writer,
            "update_repair_order",
            self.writer_headers,
            {
                "card_id": card,
                "repair_order": {
                    "payments": [
                        {
                            "id": "backdated",
                            "cashbox_id": self.first,
                            "amount": "3.46",
                            "paid_at": "15.10.2020 09:00",
                            "note": "Backdated after receipt",
                        }
                    ]
                },
            },
        )
        earlier = next(
            r["transaction_id"]
            for r in self.listed()["notification"]["unread_transactions"]
            if r["cashbox_id"] == self.first
        )
        newest = self.movement(self.first, "Registered after the backdated payment")
        draft = self.page.locator("#cashboxNoteInput")
        draft.fill("Unsaved note")
        self.page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        self.page.locator(
            f'#cashboxTransactions [data-cashbox-transaction-id="{newest}"]'
        ).wait_for()
        self.page.evaluate("stopSnapshotPolling()")
        self.assertEqual(draft.input_value(), "Unsaved note")
        self.assertEqual(
            self.page.locator(
                f'#cashboxTransactions [data-cashbox-transaction-id="{earlier}"]'
            ).count(),
            0,
        )
        self.assertIn(
            earlier,
            [r["transaction_id"] for r in self.listed()["notification"]["unread_transactions"]],
        )
        for _ in range(2):
            with self.page.expect_response("**/api/get_cashbox?**"):
                self.more.click()
        self.page.locator(
            f'#cashboxTransactions [data-cashbox-transaction-id="{earlier}"]'
        ).wait_for()
        self.wait_unread([other])
        self.page.locator(f'#cashboxesList [data-cashbox-id="{self.second}"]').click()
        self.page.locator(
            f'#cashboxTransactions [data-cashbox-transaction-id="{other}"]'
        ).wait_for()
        self.wait_unread([])
        # The active box is now outside the background poll's limit of one.
        before = self.page.evaluate("state.activeCashbox.statistics.balance_minor")
        another = self.movement(self.second, "New payment in the open second cashbox")
        self.page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        self.page.locator(
            f'#cashboxTransactions [data-cashbox-transaction-id="{another}"]'
        ).wait_for()
        self.assertEqual(
            self.page.evaluate("state.activeCashbox.statistics.balance_minor"), before + 123
        )
        self.wait_unread([])

    def test_new_movement_between_pages_reloads_a_consistent_history(self):
        self.page.locator("#cashboxesButton").click()
        self.rows.first.wait_for()
        self.page.wait_for_function(
            "!state.cashboxesLoadController && !state.cashboxNotificationSeenPromise"
        )
        self.assertEqual(self.rows.count(), 100)
        newest = self.movement(self.first, "Inserted between pages")
        self.more.click()
        self.page.locator(
            f'#cashboxTransactions [data-cashbox-transaction-id="{newest}"]'
        ).wait_for()
        self.page.wait_for_function("!state.cashboxesLoadController")
        if self.rows.count() == 100:
            self.more.click()
        self.page.wait_for_function(
            "document.querySelectorAll('#cashboxTransactions [data-cashbox-transaction-id]').length === 200"
        )
        ids = self.rows.evaluate_all("rows => rows.map(row => row.dataset.cashboxTransactionId)")
        detail = self.context.request.get(
            self.runtime.base_url
            + f"/api/get_cashbox?cashbox_id={self.first}&compact=1&transaction_limit=200",
            headers=self.viewer,
        ).json()["data"]
        self.assertEqual(len(set(ids)), 200)
        self.assertEqual(ids, [r["id"] for r in detail["transactions"]])
