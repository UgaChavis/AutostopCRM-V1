"""Real browser checks for duplicate, aborted and failed card suggestions."""

from collections import Counter
from urllib.parse import parse_qs, urlsplit

from tests.browser_ui_support import UiBrowserTestCase


def query(url):
    parts = urlsplit(url)
    if parts.path == "/api/search_clients":
        return parse_qs(parts.query).get("query", [None])[0]
    return None


class CardClientSuggestionsBrowserTests(UiBrowserTestCase):
    def setUp(self):
        super().setUp()
        self.load_board()
        self.assertEqual(self.page.title(), "AutoStop")
        self.assertEqual(
            self.page.url, urlsplit(self.runtime.browser_url)._replace(query="").geturl()
        )
        self.page.locator("[data-create-in]").first.click()
        self.page.locator("#cardModal.is-open").wait_for()
        self.name = self.page.locator("#vehicleField_customer_name")
        self.routes = []
        self.handled_routes = set()
        self.requests = Counter()
        self.failed = Counter()
        self.page.on("request", lambda request: self.requests.update([query(request.url)]))
        self.page.on("requestfailed", lambda request: self.failed.update([query(request.url)]))
        self.page.route("**/api/search_clients?*", lambda route: self.routes.append(route))

    def tearDown(self):
        for route in self.routes:
            if route not in self.handled_routes:
                route.abort()
        super().tearDown()

    def enter(self, value):
        with self.page.expect_request(lambda request: query(request.url) == value):
            self.name.fill(value)

    def route(self, value):
        for _ in range(80):
            matches = [route for route in self.routes if query(route.request.url) == value]
            if matches and len(matches) == self.requests[value]:
                current = matches[-1]
                self.handled_routes.add(current)
                return current
            self.page.wait_for_timeout(25)
        self.fail(f"Search request for {value} did not reach the route handler")

    def reply(self, value, clients):
        self.route(value).fulfill(json={"ok": True, "data": {"clients": clients}})

    def test_slow_suggestions_share_fallback_and_cancel_obsolete_queries(self):
        self.name.fill("SHARED DRAFT")
        self.enter("SHARED FIRST")
        self.name.blur()
        self.enter("SHARED SECOND")
        self.enter("SHARED FINAL")
        self.reply("SHARED", [{"id": "fallback", "display_name": "Synthetic fallback"}])
        self.reply("SHARED FINAL", [{"id": "final", "display_name": "Synthetic final"}])
        suggestions = self.page.locator("#clientMatchList [data-client-suggestion]")
        suggestions.last.wait_for()
        self.assertEqual(
            suggestions.evaluate_all("items => items.map(item => item.dataset.clientSuggestion)"),
            ["final", "fallback"],
        )
        self.name.blur()
        self.assertFalse(self.page.evaluate("Boolean(state.clientSuggestTimer)"))
        self.assertEqual(self.requests["SHARED"], 1)
        self.assertEqual(self.requests["SHARED DRAFT"], 0)
        self.assertEqual(self.requests["SHARED FIRST"], 1)
        self.assertEqual(self.requests["SHARED SECOND"], 1)
        self.assertEqual(self.requests["SHARED FINAL"], 1)
        self.assertEqual(self.failed["SHARED FIRST"], 1)
        self.assertEqual(self.failed["SHARED SECOND"], 1)
        self.assertEqual(self.failed["SHARED"], 0)

    def test_vehicle_field_change_keeps_pending_lookup(self):
        self.page.clock.install()
        year = self.page.locator("#vehicleField_production_year")
        year.fill("2023")
        self.page.clock.run_for(500)
        self.assertEqual(self.requests["2023"], 0)

        self.name.fill("SYNTHETIC")
        year.fill("2024")
        self.page.clock.run_for(500)
        self.reply("SYNTHETIC", [])
        self.reply("2024", [{"id": "vehicle-match", "display_name": "Synthetic match"}])
        self.page.locator('[data-client-suggestion="vehicle-match"]').wait_for()
        self.assertEqual(self.requests["2023"], 0)
        self.assertEqual(self.requests["2024"], 1)

    def test_escape_dismisses_pending_suggestions_without_closing_draft(self):
        title = self.page.locator("#cardTitle")
        title.fill("Synthetic unsaved draft")
        self.enter("SYNTHETIC")
        self.reply("SYNTHETIC", [{"id": "prior", "display_name": "Synthetic prior"}])
        prior = self.page.locator('[data-client-suggestion="prior"]')
        prior.wait_for()

        self.page.clock.install()
        self.name.fill("REFINED")
        self.assertFalse(prior.is_visible())
        self.name.press("Escape")
        self.assert_card_open()
        self.page.clock.run_for(500)
        self.assertEqual(self.requests["REFINED"], 0)

        self.enter("INFLIGHT")
        with self.page.expect_event(
            "requestfailed", predicate=lambda request: query(request.url) == "INFLIGHT"
        ):
            self.name.press("Escape")
        self.assert_card_open()
        self.assertEqual(title.input_value(), "Synthetic unsaved draft")
        self.assertEqual(self.name.input_value(), "INFLIGHT")
        self.assertFalse(self.page.locator("#clientMatchPanel").is_visible())

    def test_close_then_reopen_and_retry_after_network_failure(self):
        self.enter("SYNTHETIC")
        with self.page.expect_event(
            "requestfailed", predicate=lambda request: query(request.url) == "SYNTHETIC"
        ):
            self.page.locator("#cardModalCloseButtonTop").click()
        self.page.locator("#cardModal").wait_for(state="hidden")
        self.page.locator("[data-create-in]").first.click()
        self.enter("SYNTHETIC")
        self.route("SYNTHETIC").abort("failed")
        retry = self.page.locator("#clientMatchList [data-retry-client-suggestions]")
        create = self.page.locator("#clientMatchList [data-client-suggest-create]")
        retry.wait_for()
        self.assertFalse(create.is_visible())
        self.assertEqual(self.requests["SYNTHETIC"], 2)
        with self.page.expect_request(lambda request: query(request.url) == "SYNTHETIC"):
            retry.click()
        self.reply("SYNTHETIC", [])
        create.wait_for(timeout=4000)
        self.assertFalse(retry.is_visible())
        self.assertEqual(self.requests["SYNTHETIC"], 3)
