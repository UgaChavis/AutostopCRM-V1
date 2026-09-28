"""A saved card's late hydration must not cancel its open print workspace."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

if __package__:
    from tests.source_path_support import prepend_scripts_path
else:
    from source_path_support import prepend_scripts_path

prepend_scripts_path()

from browser_smoke_runtime import start_temp_runtime  # noqa: E402


class PrintingHydrationOverlapBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if sys.platform == "win32":
            previous_policy = asyncio.get_event_loop_policy()
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            cls.addClassCleanup(asyncio.set_event_loop_policy, previous_policy)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise unittest.SkipTest("Playwright is not installed") from error
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        if not Path(cls.playwright.chromium.executable_path).exists():
            raise unittest.SkipTest("Playwright Chromium is not installed")
        cls.browser = cls.playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        cls.addClassCleanup(cls.browser.close)
        cls.runtime = start_temp_runtime(start_port=44931)
        cls.addClassCleanup(cls.runtime.close)

    def test_late_full_card_does_not_close_print_documents(self) -> None:
        context = self.browser.new_context(
            viewport={"width": 1440, "height": 960},
            extra_http_headers=self.runtime.auth_headers,
        )
        self.addCleanup(context.close)
        session = context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        ).json()["data"]["session"]["token"]
        context.add_init_script(
            "localStorage.setItem('kanban-operator-session', " + json.dumps(session) + ");"
        )
        page = context.new_page()
        errors: list[str] = []
        responses: list[tuple[str, int]] = []
        card_routes = []
        print_routes = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "response",
            lambda response: (
                responses.append((response.url.split("?", 1)[0], response.status))
                if response.url.split("?", 1)[0].endswith(
                    ("/api/get_card", "/api/get_repair_order_print_workspace")
                )
                else None
            ),
        )
        page.route("**/api/get_card?*", lambda route: card_routes.append(route))
        page.route(
            "**/api/get_repair_order_print_workspace",
            lambda route: print_routes.append(route),
        )
        page.goto(self.runtime.browser_url)
        page.wait_for_function("state.snapshot && state.operatorSessionToken")
        page.locator(
            f'.card[data-card-id="{self.runtime.client_card_id}"]:not([data-virtual-card="true"])'
        ).click()
        page.locator("#cardModal.is-open").wait_for()
        page.wait_for_function("() => state.activeCardIsFull === false")
        page.wait_for_function("() => window.__AUTOSTOP_UI_BOUND__ === true")
        self.assertEqual(len(card_routes), 1)
        page.locator("#repairOrderButton").click()
        page.locator("#repairOrderModal.is-open").wait_for()
        page.locator("#repairOrderPrintButton").click()
        page.locator("#repairOrderPrintModal.is-open").wait_for()
        page.wait_for_function(
            "() => document.querySelector('#repairOrderPrintModal').dataset.printLoadState === 'loading'"
        )
        self.assertEqual(len(print_routes), 1)
        print_has_focus = "modal => modal.contains(document.activeElement)"
        self.assertTrue(page.locator("#repairOrderPrintModal").evaluate(print_has_focus))

        card_routes.pop().continue_()
        page.wait_for_function("() => state.activeCardIsFull === true")
        page.wait_for_timeout(60)
        self.assertTrue(page.locator("#repairOrderPrintModal").evaluate(print_has_focus))
        self.assertEqual(page.evaluate("state.editingId"), self.runtime.client_card_id)
        self.assertTrue(page.locator("#repairOrderPrintModal.is-open").is_visible())
        self.assertTrue(page.locator("#repairOrderPrintModal").evaluate(print_has_focus))
        self.assertEqual(
            page.locator("#repairOrderPrintModal").get_attribute("data-print-load-state"),
            "loading",
        )
        print_routes.pop().continue_()
        page.locator(
            '#repairOrderPrintDocuments [data-print-document="inspection_sheet"]'
        ).wait_for(timeout=10000)
        page.wait_for_function(
            "() => document.querySelector('#repairOrderPrintModal').dataset.printLoadState === 'ready'",
            timeout=10000,
        )
        self.assertTrue(page.locator("#repairOrderPrintModal.is-open").is_visible())
        self.assertTrue(page.locator("#repairOrderPrintModal").evaluate(print_has_focus))
        self.assertTrue(
            any(url.endswith("/api/get_card") and status == 200 for url, status in responses)
        )
        self.assertIn(
            (self.runtime.base_url + "/api/get_repair_order_print_workspace", 200), responses
        )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
