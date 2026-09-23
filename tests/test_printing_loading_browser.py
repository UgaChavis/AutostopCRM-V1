"""Printing shell races against the real lazy module on a disposable CRM."""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from browser_smoke_runtime import start_temp_runtime

from minimal_kanban.web_app_assets.assembler import BOARD_WEB_APP_MODULE_MANIFEST


class PrintingLoadingBrowserTests(unittest.TestCase):
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
        cls.runtime = start_temp_runtime(start_port=42981)
        cls.addClassCleanup(cls.runtime.close)

    def setUp(self) -> None:
        self.context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            extra_http_headers=self.runtime.auth_headers,
        )
        self.addCleanup(self.context.close)
        session = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        ).json()["data"]["session"]["token"]
        self.context.add_init_script(
            "localStorage.setItem('kanban-operator-session', " + json.dumps(session) + ");"
        )
        self.page = self.context.new_page()
        self.errors: list[str] = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.runtime.browser_url)
        self.page.wait_for_function("window.__AUTOSTOP_UI_BOUND__ === true")
        self.page.wait_for_function("state.snapshot && state.operatorSessionToken")
        self.page.evaluate(
            "id => openCardWorkspace(id, {openRepairOrder:true})", self.runtime.card_id
        )
        self.page.locator("#repairOrderPrintButton").wait_for(state="visible")

    def wait_load_state(self, value: str) -> None:
        self.page.wait_for_function(
            "value => document.querySelector('#repairOrderPrintModal').dataset.printLoadState === value",
            arg=value,
        )

    def test_cold_close_retry_hot_load_and_preview_readiness(self) -> None:
        scripts, workspaces, previews = [], [], []
        self.page.route(
            "**" + BOARD_WEB_APP_MODULE_MANIFEST["printing"], lambda route: scripts.append(route)
        )
        self.page.route(
            "**/api/get_repair_order_print_workspace", lambda route: workspaces.append(route)
        )
        self.page.route(
            "**/api/preview_repair_order_print_documents", lambda route: previews.append(route)
        )
        self.page.locator("#repairOrderPrintButton").click()
        self.wait_load_state("loading")
        self.assertTrue(self.page.locator("#repairOrderPrintRunButton").is_disabled())
        self.assertEqual(
            self.page.locator("#repairOrderPrintPreviewFrame").get_attribute("srcdoc"), ""
        )
        self.page.wait_for_function("document.querySelector('script[src*=\"board.\"]') !== null")
        self.page.locator("#repairOrderPrintCloseX").click()
        self.assertFalse(self.page.locator("#repairOrderPrintModal").is_visible())
        self.page.locator("#repairOrderPrintButton").click()
        self.page.wait_for_timeout(50)
        self.assertEqual(len(workspaces), 2)
        self.assertEqual(len(scripts), 1)
        scripts.pop().continue_()
        workspaces.pop(0).continue_()
        workspaces.pop(0).fulfill(
            status=503, json={"ok": False, "error": "Synthetic workspace failure"}
        )
        self.wait_load_state("error")
        self.assertTrue(self.page.locator("#repairOrderPrintRunButton").is_disabled())
        self.page.locator("#repairOrderPrintRetryButton").click()
        self.wait_load_state("loading")
        self.page.wait_for_timeout(50)
        workspaces.pop().continue_()
        self.wait_load_state("documents-ready")
        self.assertGreater(
            self.page.locator("#repairOrderPrintDocuments [data-print-document]").count(), 0
        )
        self.page.wait_for_timeout(50)
        previews.pop().continue_()
        self.wait_load_state("ready")
        self.page.locator("#repairOrderPrintCloseX").click()
        self.page.locator("#repairOrderPrintButton").click()
        self.wait_load_state("loading")
        self.assertTrue(self.page.locator("#repairOrderPrintRunButton").is_disabled())
        self.assertTrue(self.page.locator("#repairOrderPrintExportButton").is_disabled())
        self.assertEqual(
            self.page.locator("#repairOrderPrintDocuments [data-print-document]").count(), 0
        )
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(50)
        workspaces.pop().continue_()
        self.page.wait_for_timeout(100)
        self.assertFalse(self.page.locator("#repairOrderPrintModal").is_visible())
        self.assertEqual(previews, [])
        self.assertEqual(self.errors, [])

    def test_unsaved_card_is_created_once_and_printed_in_same_shell(self) -> None:
        self.page.evaluate("closeCardModal(); openCardModal(null);")
        self.page.locator("#cardTitle").fill("Synthetic unsaved print shell")
        self.page.evaluate("openRepairOrderModal()")
        created = []
        self.page.on(
            "request",
            lambda request: (
                created.append(request.url) if request.url.endswith("/api/create_card") else None
            ),
        )
        self.page.locator("#repairOrderPrintButton").click()
        self.wait_load_state("ready")
        self.assertEqual(len(created), 1)
        self.assertTrue(self.page.evaluate("Boolean(state.editingId)"))
        self.assertGreater(
            self.page.locator("#repairOrderPrintDocuments [data-print-document]").count(), 0
        )
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
