"""Constructor dialog recovery against a disposable local CRM only."""

from __future__ import annotations

import json
import os
import unittest

if __package__:
    from tests import test_manager_structure_browser as browser_support
else:
    import test_manager_structure_browser as browser_support


class ManagerDialogRecoveryBrowserTests(unittest.TestCase):
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

    def hold_next_reply(self, path: str, name: str) -> None:
        self.page.evaluate(
            """({path, name}) => {
                const original = window.fetch;
                let holdNext = true;
                window[name] = null;
                window.fetch = (url, options) => {
                    const reply = original(url, options);
                    if (String(url) === path && holdNext) {
                        holdNext = false;
                        return reply.then(response => new Promise(resolve => {
                            window[name] = {response, resolve};
                        }));
                    }
                    return reply;
                };
            }""",
            {"path": path, "name": name},
        )

    def release_reply(self, name: str) -> None:
        self.page.evaluate(
            "name => { const held = window[name]; held.resolve(held.response); }", name
        )

    def test_catalog_failure_has_explicit_retry_and_mobile_readback(self):
        self.page.clock.install()
        bundle = self.automotive_fixture()
        before = self.read()
        requests = []

        def transient_failure(route):
            requests.append(route.request.method)
            if len(requests) == 1:
                route.fulfill(
                    status=503,
                    json={
                        "ok": False,
                        "error": {"message": "Temporary catalog failure"},
                    },
                )
            else:
                route.continue_()

        self.page.route(
            self.runtime.base_url + "/api/manager_structure/tool_catalog", transient_failure
        )
        self.page.locator('.node[data-id="E2"]').click()
        self.page.get_by_text("Temporary catalog failure", exact=True).wait_for()
        self.assertEqual(self.page.locator("#toolDialogMeta").inner_text(), "Каталог не загружен")
        self.assertEqual(self.page.locator("#toolDialogScroll").get_attribute("aria-busy"), "false")
        self.page.clock.fast_forward(11000)
        self.page.wait_for_timeout(100)
        self.assertEqual(requests, ["GET"])
        self.assertEqual(self.page.locator(".tool-card").count(), 0)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.get_by_role("button", name="Повторить загрузку", exact=True).click()
        self.page.locator(".tool-card").first.wait_for()
        expected = next(module for module in bundle["modules"] if module["element_id"] == "E2")
        self.assertEqual(self.page.locator(".tool-card").count(), len(expected["tool_ids"]))
        self.assertFalse(self.page.locator("#toolCatalogRetry").is_visible())
        self.assertFalse(self.page.locator("#toolCatalogMessage").is_visible())
        self.assertEqual(requests, ["GET", "GET"])
        self.assertEqual(self.read(), before)
        self.assertFalse(
            self.page.locator("#toolDialog").evaluate("el => el.scrollWidth > el.clientWidth")
        )
        self.page.keyboard.press("Escape")
        self.assertTrue(
            self.page.locator('.node[data-id="E2"]').evaluate("el => el === document.activeElement")
        )
        self.assertEqual(self.errors, [])

    def test_deleted_module_closes_modal_and_late_catalog_cannot_reopen(self):
        self.page.clock.install()
        self.automotive_fixture()
        self.hold_next_reply("/api/manager_structure/tool_catalog", "heldCatalog")
        self.page.locator('.node[data-id="E2"]').click()
        self.page.wait_for_function("window.heldCatalog !== null")
        removed = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "remove_element",
                "id": "E2",
                "expected_version": self.read()["version"],
                "idempotency_key": "removed-dialog-" + os.urandom(8).hex(),
            },
        )
        self.assertEqual(removed.status, 200, removed.text())
        self.page.clock.fast_forward(5000)
        self.page.locator('.node[data-id="E2"]').wait_for(state="detached")
        self.assertFalse(self.page.locator("#toolDialog").is_visible())
        self.assertEqual(self.page.locator("#toolDialogInstruction").inner_text(), "")
        self.assertEqual(self.page.locator(".tool-card").count(), 0)
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "diagram")
        self.release_reply("heldCatalog")
        self.page.wait_for_timeout(100)
        self.assertFalse(self.page.locator("#toolDialog").is_visible())
        self.assertEqual(self.page.locator(".tool-card").count(), 0)
        self.assertEqual(self.errors, [])

    def test_status_confirmed_after_reopen_cleans_new_card_without_second_write(self):
        _bundle, card = self.open_cards()
        self.hold_next_reply("/api/manager_structure/apply", "heldApply")
        writes = []
        self.page.on(
            "request",
            lambda request: (
                writes.append(request.post_data_json)
                if request.url.endswith("/api/manager_structure/apply")
                else None
            ),
        )
        version = self.read()["version"]
        card.locator("select").select_option("working")
        card.get_by_role("button", name="Сохранить отметку", exact=True).click()
        self.page.wait_for_function("window.heldApply !== null")
        self.page.keyboard.press("Escape")
        self.page.locator('.node[data-id="E2"]').click()
        card = self.page.locator(".tool-card").first
        card.wait_for()
        self.assertTrue(card.locator("select").is_disabled())
        self.release_reply("heldApply")
        card.get_by_text("Сохранено", exact=True).wait_for()
        self.assertTrue(
            card.get_by_role("button", name="Сохранить отметку", exact=True).is_disabled()
        )
        self.assertTrue(card.locator("select").is_enabled())
        self.assertEqual(card.locator("select").input_value(), "working")
        self.assertEqual(self.read()["version"], version + 1)
        self.assertEqual(len(writes), 1)
        self.assertEqual(self.errors, [])

    def test_catalog_auth_failure_has_no_retry_or_stale_loading(self):
        self.page.clock.install()
        self.automotive_fixture()
        self.page.route(
            self.runtime.base_url + "/api/manager_structure/tool_catalog",
            lambda route: route.fulfill(
                status=401, json={"ok": False, "error": {"message": "Session expired"}}
            ),
        )
        self.page.locator('.node[data-id="E2"]').click()
        self.page.get_by_text("Session expired", exact=True).wait_for()
        self.assertEqual(self.page.locator("#toolDialogMeta").inner_text(), "Каталог не загружен")
        self.assertFalse(self.page.locator("#toolCatalogRetry").is_visible())
        self.assertTrue(self.page.locator("#toolCatalogRetry").is_disabled())
        self.assertEqual(self.page.locator(".tool-card").count(), 0)
        self.assertEqual(self.page.locator("#toolDialogScroll").get_attribute("aria-busy"), "false")
        self.page.keyboard.press("Escape")
        self.assertFalse(self.page.locator("#toolDialog").is_visible())
        self.assertEqual(self.errors, [])

    def test_late_catalog_refresh_cannot_move_another_modules_scroll(self):
        bundle, _card = self.open_cards()
        self.page.keyboard.press("Escape")
        added = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "upsert_element",
                "element": {
                    "id": "M1",
                    "title": "Another module",
                    "instruction": "\n\n".join(["A separate instruction"] * 180),
                    "x": 480,
                    "y": 320,
                    "width": 250,
                    "height": 140,
                    "kind": "module",
                },
                "expected_version": self.read()["version"],
                "idempotency_key": "scroll-module-" + os.urandom(8).hex(),
            },
        )
        self.assertEqual(added.status, 200, added.text())
        self.page.clock.fast_forward(5000)
        self.page.locator('.node[data-id="M1"]').wait_for()
        self.page.locator('.node[data-id="E2"]').click()
        self.page.locator(".tool-card").first.wait_for()
        self.page.locator("#toolDialogScroll").evaluate("el => { el.scrollTop=220; }")
        revision = "d" * 40

        def changed_pin(route):
            response = route.fetch()
            payload = response.json()
            payload["data"]["catalog_metadata"]["source_revision"] = revision
            route.fulfill(response=response, body=json.dumps(payload))

        replacement = {**bundle, "source_revision": revision}
        self.page.route(self.runtime.base_url + "/api/manager_structure", changed_pin)
        self.page.route(
            self.runtime.base_url + "/api/manager_structure/tool_catalog",
            lambda route: route.fulfill(json={"ok": True, "data": replacement}),
        )
        self.hold_next_reply("/api/manager_structure/tool_catalog", "heldRefreshedCatalog")
        self.page.clock.fast_forward(5000)
        self.page.wait_for_function("window.heldRefreshedCatalog !== null")
        self.page.keyboard.press("Escape")
        self.page.locator("#mapFit").click()
        self.page.locator('.node[data-id="M1"]').click()
        self.page.locator("#toolDialogTitle").get_by_text(
            "M1 · Another module", exact=True
        ).wait_for()
        self.page.locator("#toolDialogScroll").evaluate("el => { el.scrollTop=37; }")
        self.assertEqual(self.page.locator("#toolDialogScroll").evaluate("el => el.scrollTop"), 37)
        self.release_reply("heldRefreshedCatalog")
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.locator("#toolDialogScroll").evaluate("el => el.scrollTop"), 37)
        self.assertEqual(self.page.locator("#toolDialogTitle").inner_text(), "M1 · Another module")
        self.assertEqual(self.errors, [])
