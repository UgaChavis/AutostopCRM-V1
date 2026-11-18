from __future__ import annotations

import re

from tests.browser_ui_support import UiBrowserTestCase


class BoardPollingBrowserTests(UiBrowserTestCase):
    def test_board_poll_continues_while_cash_read_and_receipt_are_held(self) -> None:
        self.page.clock.install()
        self.load_board()
        self.page.wait_for_function(
            "!state.cashboxNotificationRefreshPromise && !state.cashboxNotificationSeenPromise"
        )
        self.page.evaluate(
            """() => {
                Object.defineProperty(document, 'hidden', {configurable:true, get: () => false});
            }"""
        )
        cash_reads, receipts = [], []
        self.page.route(
            re.compile(r"/api/list_cashboxes\?limit=1$"), lambda route: cash_reads.append(route)
        )
        self.page.route(
            "**/api/mark_cashbox_notifications_seen", lambda route: receipts.append(route)
        )
        self.page.evaluate("startSnapshotPolling()")

        def advance_board_poll():
            with self.page.expect_request(
                lambda request: "/api/get_board_revision?" in request.url
            ):
                self.page.clock.run_for(8000)
            self.page.wait_for_function(
                "state.pollHandle !== null && !state.snapshotRevisionRequest"
            )

        advance_board_poll()
        self.page.wait_for_function("Boolean(state.cashboxNotificationRefreshPromise)")
        advance_board_poll()
        self.assertEqual(len(cash_reads), 1)
        with self.page.expect_request("**/api/mark_cashbox_notifications_seen"):
            cash_reads[0].fulfill(
                json={
                    "ok": True,
                    "data": {"notification": {"initialized": False, "through_transaction_id": ""}},
                }
            )
        advance_board_poll()
        advance_board_poll()
        self.assertEqual(len(receipts), 1)
        self.assertEqual(len(cash_reads), 1)
        receipts[0].fulfill(json={"ok": True, "data": {"notification": {"initialized": True}}})
        self.page.wait_for_function(
            "!state.cashboxNotificationSeenPromise && state.cashboxNotification?.initialized === true"
        )

    def test_resume_coalesces_revision_and_late_failure_cannot_affect_new_session(self) -> None:
        self.page.clock.install()
        self.load_board()
        self.page.evaluate(
            """() => {
                window.testHidden = false;
                Object.defineProperty(document, 'hidden', {configurable:true, get: () => window.testHidden});
            }"""
        )
        revisions = []
        self.page.route("**/api/get_board_revision?**", lambda route: revisions.append(route))
        with self.page.expect_request("**/api/get_board_revision?**"):
            self.page.evaluate("void (window.oldRevision = refreshSnapshotRevision())")
        self.page.evaluate(
            """() => {
                window.testHidden = true;
                document.dispatchEvent(new Event('visibilitychange'));
                window.testHidden = false;
                document.dispatchEvent(new Event('visibilitychange'));
            }"""
        )
        self.page.wait_for_function("!state.operatorProfileRequest")
        self.assertEqual(len(revisions), 1)

        response = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        )
        self.assertTrue(response.ok)
        self.page.evaluate(
            """profile => {
                clearOperatorSession();
                renderOperatorProfile(profile);
                startSnapshotPolling();
                setStatus('НОВЫЙ СЕАНС', false);
                window.newBoardTimer = state.pollHandle;
            }""",
            response.json()["data"],
        )
        revisions[0].abort("failed")
        self.page.evaluate("window.oldRevision")
        self.assertEqual(len(revisions), 1)
        self.assertTrue(self.page.evaluate("state.pollHandle === window.newBoardTimer"))
        self.assertEqual(self.page.evaluate("els.statusLine.textContent"), "НОВЫЙ СЕАНС")
