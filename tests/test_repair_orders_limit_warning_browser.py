"""The desktop list discloses a validated server limit without stale notices."""

from urllib.parse import parse_qs, urlparse

from tests.browser_ui_support import UiBrowserTestCase
from tests.test_repair_orders_loading_browser import complete_order


class RepairOrdersLimitWarningBrowserTests(UiBrowserTestCase):
    def test_limit_warning_loading_errors_and_stale_response(self):
        self.load_board()
        held = []

        def hold(route):
            held.append(route)
            self.page.evaluate(
                "window.syntheticOrderRequests = (window.syntheticOrderRequests || 0) + 1"
            )

        def wait_requests(count):
            self.page.wait_for_function(
                "count => window.syntheticOrderRequests === count", arg=count
            )

        def reply(route, returned, total, inconsistent=0, status="open"):
            rows = [
                complete_order({"card_id": f"SYNTHETIC-{i}", "number": str(i), "status": status})
                for i in range(returned)
            ]
            query = parse_qs(urlparse(route.request.url).query).get("query", [""])[0]
            route.fulfill(
                json={
                    "ok": True,
                    "data": {
                        "repair_orders": rows,
                        "meta": {
                            "status": status,
                            "query": query,
                            "sort_by": "number",
                            "sort_dir": "desc",
                            "limit": 300,
                            "total": total,
                            "returned": returned,
                            "inconsistent_total": inconsistent,
                            "has_more": total > 300,
                        },
                    },
                }
            )

        self.page.route("**/api/list_repair_orders?**", hold)
        self.page.locator("#repairOrdersButton").click()
        wait_requests(1)
        reply(held.pop(0), 300, 301, inconsistent=1)
        self.page.wait_for_function(
            "!state.repairOrdersSearchLoading && state.repairOrdersMetaState?.total === 301"
        )
        meta = self.page.locator("#repairOrdersMeta")
        self.assertTrue(meta.is_visible())
        self.assertEqual(meta.get_attribute("aria-hidden"), "false")
        self.assertIn("ПО ФИЛЬТРУ И ПОИСКУ: ПОКАЗАНО 300 ИЗ 301", meta.inner_text())
        self.assertIn("СКРЫТ", meta.inner_text())
        self.assertEqual(
            self.page.locator("#repairOrdersList [data-open-repair-order-card]").count(), 300
        )

        self.page.evaluate("() => { loadRepairOrders(false); }")
        wait_requests(2)
        self.assertTrue(meta.is_hidden())
        self.page.evaluate("() => { loadRepairOrders(false); }")
        wait_requests(3)
        reply(held.pop(1), 1, 1)
        self.page.wait_for_function(
            "!state.repairOrdersSearchLoading && state.repairOrdersMetaState?.total === 1"
        )
        self.assertTrue(meta.is_hidden())
        held.pop(0).fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC STALE"}})
        self.page.wait_for_timeout(100)
        self.assertTrue(meta.is_hidden())
        self.assertFalse(self.page.evaluate("state.repairOrdersLoadError"))
        self.assertEqual(self.page.evaluate("state.repairOrdersMetaState.total"), 1)

        self.page.evaluate("() => { loadRepairOrders(false); }")
        wait_requests(4)
        self.page.evaluate("() => { loadRepairOrders(false); }")
        wait_requests(5)
        reply(held.pop(1), 1, 1)
        self.page.wait_for_function(
            "!state.repairOrdersSearchLoading && state.repairOrdersMetaState?.total === 1"
        )
        reply(held.pop(0), 300, 301, inconsistent=1)
        self.page.wait_for_timeout(100)
        self.assertTrue(meta.is_hidden())
        self.assertEqual(self.page.evaluate("state.repairOrdersMetaState.total"), 1)

        self.page.evaluate("() => { loadRepairOrders(false); }")
        wait_requests(6)
        held.pop(0).fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC ERROR"}})
        self.page.wait_for_function("state.repairOrdersLoadError")
        self.assertIn("ОШИБКА", meta.inner_text())
        self.assertNotIn("ПОКАЗАНО", meta.inner_text())
        self.page.evaluate("() => { loadRepairOrders(false); }")
        wait_requests(7)
        reply(held.pop(0), 300, 301)
        self.page.wait_for_function(
            "!state.repairOrdersSearchLoading && !state.repairOrdersLoadError"
        )
        self.assertIn("ПО ФИЛЬТРУ И ПОИСКУ: ПОКАЗАНО 300 ИЗ 301", meta.inner_text())
        self.assertNotIn("СКРЫТ", meta.inner_text())

        self.page.locator("#repairOrdersSearchInput").fill("SYNTHETIC QUERY")
        self.assertTrue(meta.is_hidden())
        wait_requests(8)
        self.assertEqual(
            parse_qs(urlparse(held[0].request.url).query).get("query"), ["SYNTHETIC QUERY"]
        )
        reply(held.pop(0), 300, 300)
        self.page.wait_for_function(
            "!state.repairOrdersSearchLoading && state.repairOrdersMetaState?.total === 300"
        )
        self.assertTrue(meta.is_hidden())
        self.assertEqual(
            self.page.locator("#repairOrdersList [data-open-repair-order-card]").count(), 300
        )

        self.page.locator("#repairOrdersSearchInput").fill("SYNTHETIC SECOND")
        wait_requests(9)
        reply(held.pop(0), 300, 301)
        self.page.wait_for_function(
            "!state.repairOrdersSearchLoading && state.repairOrdersMetaState?.total === 301"
        )
        self.assertIn("ПОКАЗАНО 300 ИЗ 301", meta.inner_text())
        self.page.locator("#repairOrdersReadyTab").click()
        self.assertTrue(meta.is_hidden())
        wait_requests(10)
        self.assertEqual(parse_qs(urlparse(held[0].request.url).query).get("status"), ["ready"])
        reply(held.pop(0), 300, 300, status="ready")
        self.page.wait_for_function(
            "!state.repairOrdersSearchLoading && state.repairOrdersMetaState?.status === 'ready'"
        )
        self.assertTrue(meta.is_hidden())
