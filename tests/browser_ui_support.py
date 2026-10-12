"""Real UI tests on disposable CRM state; never use a desktop client's profile."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path

from tests.source_path_support import prepend_scripts_path

prepend_scripts_path()

from browser_smoke_runtime import start_temp_runtime  # noqa: E402


class UiBrowserTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if sys.platform == "win32":
            policy = asyncio.get_event_loop_policy()
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            cls.addClassCleanup(asyncio.set_event_loop_policy, policy)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            if os.environ.get("AUTOSTOP_REQUIRE_UI_BROWSER") == "1":
                raise RuntimeError("Playwright is required for UI regression tests") from error
            raise unittest.SkipTest("Playwright is not installed") from error
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        if not Path(cls.playwright.chromium.executable_path).exists():
            if os.environ.get("AUTOSTOP_REQUIRE_UI_BROWSER") == "1":
                raise RuntimeError("Chromium is required for UI regression tests")
            raise unittest.SkipTest("Playwright Chromium is not installed")
        cls.browser = cls.playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        cls.addClassCleanup(cls.browser.close)
        cls.runtime = start_temp_runtime(start_port=45031)
        cls.addClassCleanup(cls.runtime.close)

    def setUp(self) -> None:
        self.context = self.browser.new_context(
            viewport={"width": 1440, "height": 960},
            extra_http_headers=self.runtime.auth_headers,
        )
        self.addCleanup(self.context.close)
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        ).json()["data"]["session"]["token"]
        self.session_token = token
        self.context.add_init_script(
            "localStorage.setItem('kanban-operator-session', " + json.dumps(token) + ");"
        )
        self.page = self.context.new_page()
        self.page_errors: list[str] = []
        self.writes: list[str] = []
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.page.on(
            "request",
            lambda request: (
                self.writes.append(request.url.rsplit("/", 1)[-1])
                if request.method == "POST"
                else None
            ),
        )

    def tearDown(self) -> None:
        self.assertEqual(self.page_errors, [])

    def load_board(self) -> None:
        self.page.goto(self.runtime.browser_url)
        self.page.wait_for_function(
            "state.snapshot && state.operatorSessionToken && state.operatorProfile && state.personalBoardPreferences && window.__AUTOSTOP_UI_BOUND__"
        )
        self.page.evaluate("stopSnapshotPolling()")

    def create_card(self, title: str, **fields: object) -> str:
        return self.runtime.service.create_card(
            {"title": title, "source": "ui", "actor_name": "admin", **fields}
        )["card"]["id"]

    def create_client_and_open(self, name: str) -> str:
        client_id = self.runtime.service.create_client(
            {"client_type": "company", "display_name": name, "source": "ui"}
        )["client"]["id"]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.locator(f'#clientsList [data-client-id="{client_id}"]').click()
        self.page.wait_for_function(
            "id => state.clientsActiveProfile?.client?.id === id", arg=client_id
        )
        return client_id

    def open_card(self, card_id: str) -> None:
        self.page.locator(
            f'.card[data-card-id="{card_id}"]:not([data-virtual-card="true"])'
        ).click()
        self.page.locator("#cardModal.is-open").wait_for()
        self.page.wait_for_function("state.activeCardIsFull === true")
        self.page.evaluate(
            "clearCardOpenSideEffectTimer(); cancelDeferredCardSeen(state.editingId);"
        )

    def stored_card(self, card_id: str) -> dict:
        return self.context.request.get(
            self.runtime.base_url + "/api/get_card?card_id=" + card_id,
            headers={"X-Operator-Session": self.session_token},
        ).json()["data"]["card"]

    def finish_save(self) -> None:
        self.page.wait_for_function("state.cardSaveInFlight === false")

    def assert_card_open(self) -> None:
        self.assertTrue(self.page.locator("#cardModal.is-open").is_visible())

    def assert_card_closed(self) -> None:
        self.assertFalse(
            self.page.locator("#cardModal").evaluate("e => e.classList.contains('is-open')")
        )
