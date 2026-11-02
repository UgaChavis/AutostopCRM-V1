"""The repair-order list opens immediately and owns its asynchronous reads."""

import os
from pathlib import Path

from tests.browser_ui_support import UiBrowserTestCase


class RepairOrdersLoadingBrowserTests(UiBrowserTestCase):
    def test_loading_freshness_and_close_while_pending(self):
        self.load_board()
        console_errors = []
        self.page.on(
            "console",
            lambda message: (
                console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        self.page.evaluate(
            """() => {
              const original = loadModalData;
              window.repairOrdersLoads = [];
              loadModalData = (path, options) => {
                const pending = original(path, options);
                if (path.startsWith('/api/list_repair_orders?')) window.repairOrdersLoads.push(pending);
                return pending;
              };
            }"""
        )
        held = []
        pattern = "**/api/list_repair_orders?**"

        def hold(route):
            held.append(route)
            self.page.evaluate("window.repairOrdersHeld = (window.repairOrdersHeld || 0) + 1")

        def wait_for_request(count):
            self.page.wait_for_function("n => window.repairOrdersHeld === n", arg=count)
            self.assertEqual(len(held), 1)

        def fulfill_orders(rows):
            held.pop().fulfill(
                status=200,
                json={
                    "ok": True,
                    "data": {
                        "repair_orders": rows,
                        "meta": {"status": "open", "sort_by": "number", "sort_dir": "desc"},
                    },
                },
            )

        def loading():
            self.page.locator("#repairOrdersModal.is-open").wait_for(state="visible")
            self.assertIn(
                "ЗАГРУЗКА ЗАКАЗ-НАРЯДОВ", self.page.locator("#repairOrdersList").inner_text()
            )
            self.assertEqual(
                self.page.locator("#repairOrdersList [data-open-repair-order-card]").count(), 0
            )

        self.page.route(pattern, hold)
        self.page.evaluate(
            """() => {
              state.repairOrdersItems = [{card_id:'old', number:'99'}];
              state.repairOrdersMetaState = {status:'open', sort_by:'opened_at', sort_dir:'desc'};
              document.querySelector('#repairOrdersList').textContent = 'СТАРАЯ ЗАПИСЬ';
              document.querySelector('#repairOrdersMeta').textContent = 'СТАРАЯ ОШИБКА';
            }"""
        )
        self.page.locator("#repairOrdersButton").click()
        loading()
        wait_for_request(1)
        self.assertNotIn("СТАРАЯ", self.page.locator("#repairOrdersModal").inner_text())
        self.page.locator("#repairOrdersButton").dispatch_event("click")
        self.assertEqual(len(held), 1)
        self.assertEqual(
            self.page.evaluate("state.modalStack.filter(x => x.key === 'repair-orders').length"), 1
        )

        screenshot_dir = os.environ.get("AUTOSTOP_REPAIR_ORDERS_SCREENSHOT_DIR")
        if screenshot_dir:
            Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(Path(screenshot_dir) / "loading.png"))

        fulfill_orders([{"card_id": "fresh", "number": "123", "summary": "Новая запись"}])
        self.page.locator('#repairOrdersList [data-open-repair-order-card="fresh"]').wait_for()
        self.page.locator('[data-close="repair-orders"]').click()
        self.page.locator("#repairOrdersButton").click()
        loading()
        wait_for_request(2)
        fulfill_orders([])
        self.page.get_by_text("ОТКРЫТЫХ ЗАКАЗ-НАРЯДОВ ПОКА НЕТ.").wait_for()

        self.page.locator('[data-close="repair-orders"]').click()
        self.page.locator("#repairOrdersButton").click()
        loading()
        wait_for_request(3)
        held.pop().fulfill(
            status=503, json={"ok": False, "error": {"message": "Synthetic list failure"}}
        )
        self.page.locator("#repairOrdersList").get_by_text("Synthetic list failure").wait_for()
        self.assertFalse(self.page.evaluate("state.repairOrdersSearchLoading"))

        self.page.locator('[data-close="repair-orders"]').click()
        self.page.locator("#repairOrdersButton").click()
        loading()
        wait_for_request(4)
        self.page.locator('[data-close="repair-orders"]').click()
        fulfill_orders([{"card_id": "late", "number": "77"}])
        self.page.evaluate("() => window.repairOrdersLoads[3]")
        self.assertFalse(self.page.locator("#repairOrdersModal").is_visible())
        self.assertEqual(
            self.page.locator('#repairOrdersList [data-open-repair-order-card="late"]').count(), 0
        )

        self.page.locator("#repairOrdersButton").click()
        loading()
        wait_for_request(5)
        self.page.evaluate("state.viewerStateGeneration += 1")
        fulfill_orders([{"card_id": "old-session", "number": "78"}])
        self.page.evaluate("() => window.repairOrdersLoads[4]")
        self.assertEqual(
            self.page.locator(
                '#repairOrdersList [data-open-repair-order-card="old-session"]'
            ).count(),
            0,
        )
        self.page.locator('[data-close="repair-orders"]').click()
        self.page.unroute(pattern)
        self.assertTrue(
            all(
                "Failed to load resource: the server responded with a status of 503" in error
                for error in console_errors
            ),
            console_errors,
        )
