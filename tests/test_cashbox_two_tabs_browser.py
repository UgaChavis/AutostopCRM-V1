from __future__ import annotations

import time

from tests.browser_ui_support import UiBrowserTestCase


class CashboxTwoTabsBrowserTests(UiBrowserTestCase):
    def test_delayed_initialization_keeps_movement_unseen_in_both_tabs(self) -> None:
        summary_path = "**/api/list_cashboxes?limit=1"
        ack_path = "**/api/mark_cashbox_notifications_seen"
        # The foreground tab opens the cashbox before obtaining a background summary.
        self.page.route(summary_path, lambda route: route.abort("failed"))
        self.load_board()
        peer = self.context.new_page()
        peer.route(summary_path, lambda route: route.abort("failed"))
        peer.goto(self.runtime.browser_url)
        peer.wait_for_function("state.operatorProfile && window.__AUTOSTOP_UI_BOUND__")
        peer.evaluate("stopSnapshotPolling()")
        peer.wait_for_function("!state.cashboxNotificationRefreshPromise")
        peer.unroute(summary_path)

        def take_held_route(page, routes):
            deadline = time.monotonic() + 5
            while not routes and time.monotonic() < deadline:
                page.wait_for_timeout(20)
            self.assertEqual(len(routes), 1)
            return routes.pop()

        def movement(note):
            return self.runtime.service.create_cash_transaction(
                {
                    "cashbox_id": self.runtime.cashbox_id,
                    "direction": "income",
                    "amount": "1",
                    "actor_name": "Other operator",
                    "note": note,
                }
            )["transaction"]["id"]

        first_id = movement("Shown in foreground tab")
        foreground_acks = []
        self.page.route(ack_path, lambda route: foreground_acks.append(route))
        self.page.locator("#cashboxesButton").click()
        foreground = take_held_route(self.page, foreground_acks)
        self.assertIn(
            "Shown in foreground tab", self.page.locator("#cashboxTransactions").inner_text()
        )
        self.assertEqual(foreground.request.post_data_json["through_transaction_id"], first_id)

        second_id = movement("Arrived after the visible cashbox boundary")
        polls = []
        peer.route(summary_path, lambda route: polls.append(route))
        peer.bring_to_front()
        peer.evaluate("void refreshCashboxNotification()")
        poll = take_held_route(peer, polls)
        response = poll.fetch()
        notification = response.json()["data"]["notification"]
        self.assertFalse(notification["initialized"])
        self.assertEqual(notification["through_transaction_id"], second_id)
        with self.page.expect_response(ack_path):
            foreground.continue_()
        self.page.wait_for_function("state.cashboxNotification?.unread_count === 1")

        with peer.expect_response(ack_path) as background:
            poll.fulfill(response=response)
        self.assertFalse(background.value.json()["data"]["meta"]["changed"])
        self.assertTrue(background.value.request.post_data_json["initialize_only"])
        peer.wait_for_function("state.cashboxNotification?.unread_count === 1")
        self.assertFalse(peer.locator("#cashboxesModal").is_visible())
        self.assertEqual(
            peer.locator("#cashboxesButton").get_attribute("data-cashbox-notification-tone"),
            "income",
        )
        listed = self.context.request.get(
            self.runtime.base_url + "/api/list_cashboxes?limit=1",
            headers={"X-Operator-Session": self.session_token},
        ).json()["data"]["notification"]
        self.assertEqual(
            [item["transaction_id"] for item in listed["unread_transactions"]], [second_id]
        )
