"""The mobile client search draft must survive a panel rerender."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

if __package__:
    from tests.source_path_support import prepend_scripts_path
else:
    from source_path_support import prepend_scripts_path

prepend_scripts_path()

from browser_smoke_runtime import start_temp_runtime  # noqa: E402


def _query(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.path != "/api/search_clients":
        return None
    return parse_qs(parsed.query).get("query", [None])[0]


class MobileClientSearchDraftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if sys.platform == "win32":
            raise unittest.SkipTest("POSIX disposable browser runtime required")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise unittest.SkipTest("Playwright is not installed") from exc
        cls.runtime = start_temp_runtime(start_port=46881)
        cls.addClassCleanup(cls.runtime.close)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        if not Path(cls.playwright.chromium.executable_path).exists():
            raise unittest.SkipTest("Playwright Chromium is not installed")
        cls.browser = cls.playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        cls.addClassCleanup(cls.browser.close)

    def test_input_survives_rerender_and_empty_query_fetches_list(self) -> None:
        context = self.browser.new_context(
            viewport={"width": 390, "height": 844},
            is_mobile=True,
            extra_http_headers=self.runtime.auth_headers,
        )
        self.addCleanup(context.close)
        login = context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        ).json()
        self.assertTrue(login["ok"])
        session = login["data"]["session"]["token"]
        context.add_init_script(
            "localStorage.setItem('kanban-operator-session', " + json.dumps(session) + ");"
        )
        page = context.new_page()
        page.goto(self.runtime.browser_url, wait_until="domcontentloaded")
        page.wait_for_function("window.__AUTOSTOP_UI_BOUND__ === true")
        page.wait_for_function("document.body.classList.contains('is-mobile-lite')")
        page.click('[data-mobile-view="more"]')
        page.click('[data-mobile-open="clients"]')
        page.wait_for_selector("#mobileClientsPanel:not([hidden])")
        page.wait_for_function(
            """() => !document.querySelector('#mobileClientsMeta')?.textContent.includes('ЗАГРУЗКА')"""
        )

        with page.expect_response(
            lambda response: _query(response.url) == "Smoke" and response.status == 200
        ):
            page.fill("#mobileClientsSearchInput", "Smoke")
            page.click('[data-mobile-view="more"]')
            self.assertEqual(page.locator("#mobileClientsSearchInput").input_value(), "Smoke")
        page.wait_for_function(
            """() => !document.querySelector('#mobileClientsMeta')?.textContent.includes('ЗАГРУЗКА')"""
        )
        self.assertEqual(page.locator("#mobileClientsSearchInput").input_value(), "Smoke")

        with page.expect_response(
            lambda response: (
                urlsplit(response.url).path == "/api/list_clients" and response.status == 200
            )
        ):
            page.fill("#mobileClientsSearchInput", "")
            page.click('[data-mobile-view="more"]')
            self.assertEqual(page.locator("#mobileClientsSearchInput").input_value(), "")


if __name__ == "__main__":
    unittest.main()
