"""Client search races on a disposable CRM browser runtime."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

if __package__:
    from tests.source_path_support import prepend_scripts_path
else:
    from source_path_support import prepend_scripts_path

prepend_scripts_path()

from browser_smoke_runtime import start_temp_runtime


def _search_query(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.path != "/api/search_clients":
        return None
    return parse_qs(parsed.query).get("query", [None])[0]


class _ClientsSearchBrowserCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if sys.platform == "win32":
            raise unittest.SkipTest("POSIX disposable browser runtime required")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            if os.environ.get("AUTOSTOP_REQUIRE_CLIENTS_SEARCH_BROWSER") == "1":
                raise RuntimeError(
                    "Playwright is required for client search browser tests"
                ) from exc
            raise unittest.SkipTest("Playwright is not installed") from exc

        cls.runtime = start_temp_runtime(start_port=46871)
        cls.addClassCleanup(cls.runtime.close)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        if not Path(cls.playwright.chromium.executable_path).exists():
            if os.environ.get("AUTOSTOP_REQUIRE_CLIENTS_SEARCH_BROWSER") == "1":
                raise RuntimeError("Chromium is required for client search browser tests")
            raise unittest.SkipTest("Playwright Chromium is not installed")
        cls.browser = cls.playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        cls.addClassCleanup(cls.browser.close)


class MobileClientsSearchBrowserTests(_ClientsSearchBrowserCase):
    def setUp(self) -> None:
        self.context = self.browser.new_context(
            viewport={"width": 390, "height": 844},
            is_mobile=True,
            extra_http_headers=self.runtime.auth_headers,
        )
        self.addCleanup(self.context.close)
        login = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        ).json()
        self.assertTrue(login["ok"])
        session = login["data"]["session"]["token"]
        self.context.add_init_script(
            "localStorage.setItem('kanban-operator-session', " + json.dumps(session) + ");"
        )
        self.page = self.context.new_page()
        self.page.goto(self.runtime.browser_url, wait_until="domcontentloaded")
        self.page.wait_for_function("window.__AUTOSTOP_UI_BOUND__ === true")
        self.page.wait_for_function("document.body.classList.contains('is-mobile-lite')")
        self.page.click('[data-mobile-view="more"]')
        self.page.click('[data-mobile-open="clients"]')
        self.page.wait_for_selector("#mobileClientsPanel:not([hidden])")
        self.page.wait_for_function(
            """() => {
              const meta = document.querySelector('#mobileClientsMeta')?.textContent || '';
              return document.querySelectorAll('#mobileClientsList [data-mobile-client-id]').length > 0
                && !meta.includes('ЗАГРУЗКА');
            }"""
        )

    def _assert_query_fields(self, query: str) -> None:
        self.assertEqual(self.page.locator("#mobileClientsSearchInput").input_value(), query)
        self.assertEqual(self.page.locator("#clientsSearchInput").input_value(), query.strip())

    def _wait_for_held_route(self, held: dict, query: str) -> None:
        for _ in range(100):
            if query in held:
                return
            self.page.wait_for_timeout(50)
        self.fail(f"Synthetic search route was not captured for {query}")

    def test_rerender_preserves_search_draft_and_clear_fetches_full_list(self) -> None:
        with self.page.expect_response(
            lambda response: _search_query(response.url) == "Smoke" and response.status == 200
        ):
            self.page.fill("#mobileClientsSearchInput", "Smoke")
            self.page.evaluate("document.querySelector('[data-mobile-view=more]').click()")
            self._assert_query_fields("Smoke")
        self.page.wait_for_function(
            """() => {
              const meta = document.querySelector('#mobileClientsMeta')?.textContent || '';
              const rows = Array.from(document.querySelectorAll('#mobileClientsList [data-mobile-client-id]'));
              return !meta.includes('ЗАГРУЗКА') && rows.length > 0
                && rows.every(row => row.textContent.toLowerCase().includes('smoke'));
            }"""
        )

        with self.page.expect_response(
            lambda response: _search_query(response.url) == "Smoke" and response.status == 200
        ):
            self.page.fill("#mobileClientsSearchInput", "Smoke ")
            self.page.evaluate("document.querySelector('[data-mobile-view=more]').click()")
            self._assert_query_fields("Smoke ")
            self.page.wait_for_timeout(260)
            self._assert_query_fields("Smoke ")
        with self.page.expect_response(
            lambda response: (
                _search_query(response.url) == "Smoke Client" and response.status == 200
            )
        ):
            self.page.fill("#mobileClientsSearchInput", "Smoke Client")
            self.page.evaluate("document.querySelector('[data-mobile-view=more]').click()")
            self._assert_query_fields("Smoke Client")
        self._assert_query_fields("Smoke Client")

        with self.page.expect_response(
            lambda response: (
                urlsplit(response.url).path == "/api/list_clients" and response.status == 200
            )
        ):
            self.page.fill("#mobileClientsSearchInput", "")
            self.page.evaluate("document.querySelector('[data-mobile-view=more]').click()")
            self._assert_query_fields("")
        self.page.wait_for_function(
            """() => {
              const meta = document.querySelector('#mobileClientsMeta')?.textContent || '';
              return !meta.includes('ЗАГРУЗКА')
                && document.querySelectorAll('#mobileClientsList [data-mobile-client-id]').length > 0;
            }"""
        )
        self._assert_query_fields("")

        hidden_searches = []
        self.page.on(
            "request",
            lambda request: (
                hidden_searches.append(request) if _search_query(request.url) == "Smoke" else None
            ),
        )
        self.page.fill("#mobileClientsSearchInput", "Smoke")
        self.page.evaluate("document.querySelector('#mobileClientsBackButton').click()")
        self.page.wait_for_timeout(260)
        self.assertEqual(hidden_searches, [])
        with self.page.expect_response(
            lambda response: _search_query(response.url) == "Smoke" and response.status == 200
        ):
            self.page.click('[data-mobile-open="clients"]')
        self._assert_query_fields("Smoke")

    def test_late_search_response_cannot_replace_newer_query(self) -> None:
        held = {}

        def hold_search(route) -> None:
            query = _search_query(route.request.url)
            if query in {"Smoke", "NoMatch"}:
                held[query] = route
            else:
                route.continue_()

        self.page.route("**/api/search_clients?*", hold_search)
        try:
            with self.page.expect_request(lambda request: _search_query(request.url) == "Smoke"):
                self.page.fill("#mobileClientsSearchInput", "Smoke")
            self._wait_for_held_route(held, "Smoke")
            with self.page.expect_request(lambda request: _search_query(request.url) == "NoMatch"):
                self.page.fill("#mobileClientsSearchInput", "NoMatch")
            self._wait_for_held_route(held, "NoMatch")

            with self.page.expect_response(lambda response: _search_query(response.url) == "Smoke"):
                held.pop("Smoke").continue_()
            self._assert_query_fields("NoMatch")
            self.assertIn("ЗАГРУЗКА", self.page.locator("#mobileClientsMeta").inner_text())
            self.assertEqual(
                self.page.locator("#mobileClientsList [data-mobile-client-id]").count(), 0
            )

            with self.page.expect_response(
                lambda response: _search_query(response.url) == "NoMatch"
            ):
                held.pop("NoMatch").continue_()
            self.page.wait_for_function(
                """() => {
                  const meta = document.querySelector('#mobileClientsMeta')?.textContent || '';
                  return !meta.includes('ЗАГРУЗКА')
                    && document.querySelectorAll('#mobileClientsList [data-mobile-client-id]').length === 0;
                }"""
            )
            self._assert_query_fields("NoMatch")
        finally:
            for route in held.values():
                route.continue_()


class DesktopClientsSearchBrowserTests(_ClientsSearchBrowserCase):
    def test_clearing_search_fetches_full_list(self) -> None:
        context = self.browser.new_context(
            viewport={"width": 1440, "height": 900},
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
        page.click("#clientsButton")
        page.locator("#clientsSearchInput").wait_for(state="visible")

        with page.expect_response(
            lambda response: _search_query(response.url) == "Smoke" and response.status == 200
        ):
            page.fill("#clientsSearchInput", "Smoke")
        with page.expect_response(
            lambda response: (
                urlsplit(response.url).path == "/api/list_clients" and response.status == 200
            )
        ):
            page.fill("#clientsSearchInput", "")
        self.assertEqual(page.locator("#clientsSearchInput").input_value(), "")


if __name__ == "__main__":
    unittest.main()
