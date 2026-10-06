"""Browser regressions for delayed reads and uncertain manual-status writes."""

from __future__ import annotations

import json
import unittest

if __package__:
    from tests import test_manager_structure_browser as browser_support
else:
    import test_manager_structure_browser as browser_support


class ManagerToolConcurrencyBrowserTests(unittest.TestCase):
    setUpClass = classmethod(browser_support.ManagerStructureBrowserTests.setUpClass.__func__)
    setUp = browser_support.ManagerStructureBrowserTests.setUp
    read = browser_support.ManagerStructureBrowserTests.read
    automotive_fixture = browser_support.ManagerStructureBrowserTests.automotive_fixture

    def open_cards(self):
        self.page.clock.install()
        bundle = self.automotive_fixture()
        self.page.locator('.node[data-id="E2"]').click()
        self.page.locator(".tool-card").first.wait_for()
        return bundle, self.page.locator(".tool-card").first

    def hold_poll(self):
        self.page.evaluate("""() => {
            const fetch = window.fetch;
            window.heldDiagramReads = [];
            let holdNext = true;
            window.fetch = (url, options) => {
                const response = fetch(url, options);
                if (String(url) === '/api/manager_structure' && holdNext) {
                    holdNext = false;
                    return response.then(response => new Promise(resolve => {
                        window.heldDiagramReads.push({response, resolve});
                    }));
                }
                return response;
            };
        }""")
        self.page.clock.fast_forward(5000)
        self.page.wait_for_function("window.heldDiagramReads.length === 1")

    def release_poll(self):
        self.page.evaluate("""() => {
            const held = window.heldDiagramReads.shift();
            held.resolve(held.response);
        }""")

    def test_delayed_poll_cannot_revert_confirmed_status_or_version(self):
        _bundle, card = self.open_cards()
        self.hold_poll()
        before = self.read()["version"]
        card.locator("select").select_option("working")
        card.get_by_role("button", name="Сохранить отметку", exact=True).click()
        card.get_by_text("Сохранено", exact=True).wait_for()
        self.release_poll()
        self.page.wait_for_timeout(100)
        card.locator(".tool-status-label").get_by_text("Работает нормально", exact=True).wait_for()
        self.assertEqual(self.read()["version"], before + 1)
        card.locator("select").select_option("temporarily_unavailable")
        card.get_by_role("button", name="Сохранить отметку", exact=True).click()
        card.get_by_text("Сохранено", exact=True).wait_for()
        self.assertEqual(self.read()["version"], before + 2)
        self.assertEqual(self.errors, [])

    def test_same_hash_new_source_refreshes_catalog_and_keeps_draft_scroll_and_focus(self):
        bundle, card = self.open_cards()
        revision = "a" * 40
        fetched = []

        def graph(route):
            response = route.fetch()
            payload = response.json()
            payload["data"]["catalog_metadata"]["source_revision"] = revision
            route.fulfill(response=response, body=json.dumps(payload))

        def catalog(route):
            response = route.fetch()
            payload = response.json()
            payload["data"]["source_revision"] = revision
            fetched.append(payload["data"]["content_hash"])
            route.fulfill(response=response, body=json.dumps(payload))

        self.page.route(self.runtime.base_url + "/api/manager_structure", graph)
        self.page.route(self.runtime.base_url + "/api/manager_structure/tool_catalog", catalog)
        card.locator("select").select_option("temporarily_unavailable")
        self.page.evaluate("document.getElementById('toolDialogScroll').scrollTop=220")
        scroll = self.page.locator("#toolDialogScroll").evaluate("el => el.scrollTop")
        self.page.clock.fast_forward(5000)
        self.page.wait_for_function(
            'revision => document.getElementById("toolDialogMeta").title.includes(revision)',
            arg=revision,
        )
        self.assertEqual(fetched, [bundle["content_hash"]])
        self.assertEqual(card.locator("select").input_value(), "temporarily_unavailable")
        self.assertEqual(
            self.page.locator("#toolDialogScroll").evaluate("el => el.scrollTop"), scroll
        )
        self.page.locator("#closeToolDialog").focus()
        self.page.keyboard.press("Shift+Tab")
        self.assertTrue(
            self.page.evaluate('Boolean(document.activeElement.closest("#toolDialog"))')
        )
        self.page.keyboard.press("Tab")
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "closeToolDialog")
        self.assertEqual(self.errors, [])

    def test_pending_initial_catalog_cannot_render_a_replaced_source_pin(self):
        self.page.clock.install()
        bundle = self.automotive_fixture()
        revision = "b" * 40
        self.page.evaluate("""() => {
            const fetch = window.fetch;
            let holdNext = true;
            window.heldCatalog = null;
            window.renderedCatalogPins = [];
            new MutationObserver(() => {
                window.renderedCatalogPins.push(document.getElementById('toolDialogMeta').title);
            }).observe(document.getElementById('toolDialogMeta'), {attributes: true});
            window.fetch = (url, options) => {
                const response = fetch(url, options);
                if (String(url) === '/api/manager_structure/tool_catalog' && holdNext) {
                    holdNext = false;
                    return response.then(response => new Promise(resolve => {
                        window.heldCatalog = {response, resolve};
                    }));
                }
                return response;
            };
        }""")
        self.page.locator('.node[data-id="E2"]').click()
        self.page.wait_for_function("window.heldCatalog !== null")
        fetched = []

        def graph(route):
            response = route.fetch()
            payload = response.json()
            payload["data"]["catalog_metadata"]["source_revision"] = revision
            route.fulfill(response=response, body=json.dumps(payload))

        def catalog(route):
            response = route.fetch()
            payload = response.json()
            payload["data"]["source_revision"] = revision
            fetched.append(revision)
            route.fulfill(response=response, body=json.dumps(payload))

        self.page.route(self.runtime.base_url + "/api/manager_structure", graph)
        self.page.route(self.runtime.base_url + "/api/manager_structure/tool_catalog", catalog)
        self.page.clock.fast_forward(5000)
        self.page.wait_for_timeout(200)
        self.page.evaluate("window.heldCatalog.resolve(window.heldCatalog.response)")
        self.page.wait_for_function(
            'revision => document.getElementById("toolDialogMeta").title.includes(revision)',
            arg=revision,
        )
        self.assertEqual(fetched, [revision])
        pins = self.page.evaluate("window.renderedCatalogPins")
        self.assertFalse(any(bundle["source_revision"] in pin for pin in pins))
        self.assertEqual(self.errors, [])

    def test_lost_apply_response_replays_original_key_body_after_reopen(self):
        _bundle, card = self.open_cards()
        attempts, receipts = [], []

        def uncertain(route):
            attempts.append(route.request.post_data_json)
            response = route.fetch()
            receipts.append(response.json()["data"])
            if len(attempts) == 1:
                route.abort("failed")
            else:
                route.fulfill(response=response)

        self.page.route(self.runtime.base_url + "/api/manager_structure/apply", uncertain)
        before = self.read()["version"]
        card.locator("select").select_option("working")
        card.get_by_role("button", name="Сохранить отметку", exact=True).click()
        card.get_by_text("Ответ не подтверждён.", exact=False).wait_for()
        self.assertTrue(card.locator("select").is_disabled())
        self.assertEqual(self.read()["version"], before + 1)
        self.page.keyboard.press("Escape")
        self.page.locator('.node[data-id="E2"]').click()
        card = self.page.locator(".tool-card").first
        card.get_by_role("button", name="Проверить запись", exact=True).click()
        card.get_by_text("Сохранено", exact=True).wait_for()
        self.assertEqual(attempts[0], attempts[1])
        self.assertTrue(receipts[1]["deduplicated"])
        self.assertEqual(self.read()["version"], before + 1)
        ident = card.get_attribute("data-tool-id")
        self.assertEqual(self.read()["tool_statuses"][ident], receipts[0]["tool_status"])
        card.locator("select").select_option("not_commissioned")
        card.get_by_role("button", name="Сохранить отметку", exact=True).click()
        card.get_by_text("Сохранено", exact=True).wait_for()
        self.assertNotEqual(attempts[0]["idempotency_key"], attempts[2]["idempotency_key"])
        self.assertEqual(self.read()["version"], before + 2)
        self.assertEqual(self.errors, [])

    def test_late_owner_read_cannot_restore_controls_in_viewer_session(self):
        _bundle, _card = self.open_cards()
        created = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.admin},
            data={
                "username": "status-viewer",
                "password": "viewer-password-001",
                "role": "operator",
            },
        )
        self.assertEqual(created.status, 200)
        token = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "status-viewer", "password": "viewer-password-001"},
        ).json()["data"]["session"]["token"]
        self.hold_poll()
        self.page.evaluate("token => localStorage.setItem('kanban-operator-session',token)", token)
        self.page.clock.fast_forward(5000)
        self.page.wait_for_function('document.querySelector(".tool-card select")?.hidden === true')
        self.release_poll()
        self.page.wait_for_timeout(100)
        self.assertFalse(self.page.locator(".tool-card select").first.is_visible())
        self.assertFalse(
            self.page.get_by_role("button", name="Сохранить отметку").first.is_visible()
        )
        self.assertEqual(self.errors, [])
