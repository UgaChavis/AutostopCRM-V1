"""Dialog focus and reading position survive relevant constructor refreshes."""

from __future__ import annotations

import json
import os
import unittest

if __package__:
    from tests import test_manager_structure_browser as browser_support
else:
    import test_manager_structure_browser as browser_support


class ManagerDialogFocusBrowserTests(unittest.TestCase):
    setUpClass = classmethod(browser_support.ManagerStructureBrowserTests.setUpClass.__func__)
    setUp = browser_support.ManagerStructureBrowserTests.setUp
    read = browser_support.ManagerStructureBrowserTests.read
    automotive_fixture = browser_support.ManagerStructureBrowserTests.automotive_fixture

    def write(self, **change):
        response = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                **change,
                "expected_version": self.read()["version"],
                "idempotency_key": "dialog-focus-" + os.urandom(8).hex(),
            },
        )
        self.assertTrue(response.ok, response.text())
        return response.json()["data"]

    @staticmethod
    def instruction(heading):
        return f"# {heading}\n\n[Read more](https://example.com/info)\n\n" + "\n\n".join(
            ["Reading context"] * 80
        )

    def ordinary_fixture(self):
        self.page.clock.install()
        baseline = self.read()
        self.addCleanup(
            lambda: self.write(
                operation="replace",
                diagram={
                    field: baseline[field]
                    for field in ("schema_version", "canvas", "elements", "relations")
                },
            )
        )
        self.write(
            operation="replace",
            diagram={
                "schema_version": "autostopcrm.manager-structure.v1",
                "canvas": {"width": 1000, "height": 700},
                "elements": [
                    {
                        "id": "M1",
                        "title": "Reading module",
                        "instruction": self.instruction("Original instruction"),
                        "kind": "module",
                        "x": 40,
                        "y": 40,
                        "width": 300,
                        "height": 200,
                    },
                    {
                        "id": "M2",
                        "title": "Other module",
                        "kind": "module",
                        "x": 520,
                        "y": 100,
                        "width": 250,
                        "height": 120,
                    },
                ],
                "relations": [],
            },
        )
        self.page.reload()
        self.page.locator('.node[data-id="M1"]').click()
        link = self.page.locator("#toolDialogInstruction").get_by_role("link", name="Read more")
        link.focus()
        self.page.locator("#toolDialogScroll").evaluate("el => {el.scrollTop=120;}")
        self.page.evaluate(
            "window.readingAnchor = document.querySelector('#toolDialogInstruction a')"
        )
        return link

    def changed_catalog_routes(self, bundle, revision):
        def graph(route):
            response = route.fetch()
            payload = response.json()
            payload["data"]["catalog_metadata"]["source_revision"] = revision
            route.fulfill(response=response, body=json.dumps(payload))

        self.page.route(self.runtime.base_url + "/api/manager_structure", graph)
        self.page.route(
            self.runtime.base_url + "/api/manager_structure/tool_catalog",
            lambda route: route.fulfill(
                json={"ok": True, "data": {**bundle, "source_revision": revision}}
            ),
        )

    def test_unrelated_graph_poll_keeps_the_same_instruction_dom_and_focus(self):
        link = self.ordinary_fixture()
        version = self.read()["version"]
        self.write(operation="upsert_element", element={"id": "M2", "title": "Other updated"})
        self.page.clock.fast_forward(5000)
        self.page.locator('.node[data-id="M2"] .title-main').get_by_text(
            "Other updated", exact=True
        ).wait_for()
        self.assertTrue(
            link.evaluate("el => el === window.readingAnchor && el === document.activeElement")
        )
        self.assertEqual(self.page.locator("#toolDialogScroll").evaluate("el => el.scrollTop"), 120)
        self.assertEqual(self.read()["version"], version + 1)
        self.assertTrue(self.page.locator("#toolDialog").is_visible())
        self.assertEqual(self.errors, [])

    def test_changed_instruction_preserves_focus_on_its_matching_link(self):
        link = self.ordinary_fixture()
        updated = self.instruction("Updated instruction")
        self.write(operation="upsert_element", element={"id": "M1", "instruction": updated})
        self.page.clock.fast_forward(5000)
        self.page.locator("#toolDialogInstruction").get_by_role(
            "heading", name="Updated instruction", exact=True
        ).wait_for()
        self.assertTrue(
            link.evaluate("el => el !== window.readingAnchor && el === document.activeElement")
        )
        self.assertEqual(self.page.locator("#toolDialogScroll").evaluate("el => el.scrollTop"), 120)
        self.assertEqual(self.read()["elements"][0]["instruction"], updated)
        self.assertTrue(self.page.locator("#toolDialog").is_visible())
        self.assertEqual(self.errors, [])

    def test_catalog_refresh_preserves_expanded_details_focused_choice_and_scroll(self):
        self.page.clock.install()
        bundle = self.automotive_fixture()
        self.page.locator('.node[data-id="E2"]').click()
        card = self.page.locator(".tool-card").first
        card.wait_for()
        card.locator("summary").click()
        card.locator("select").select_option("temporarily_unavailable")
        card.locator("select").focus()
        self.page.locator("#toolDialogScroll").evaluate("el => {el.scrollTop=220;}")
        version = self.read()["version"]
        revision = "e" * 40
        self.changed_catalog_routes(bundle, revision)
        self.page.clock.fast_forward(5000)
        self.page.wait_for_function(
            "revision => document.getElementById('toolDialogMeta').title.includes(revision)",
            arg=revision,
        )
        self.assertTrue(card.locator("select").evaluate("el => el === document.activeElement"))
        self.assertEqual(card.locator("select").input_value(), "temporarily_unavailable")
        self.assertTrue(card.locator("details").evaluate("el => el.open"))
        self.assertEqual(self.page.locator("#toolDialogScroll").evaluate("el => el.scrollTop"), 220)
        self.assertIn(f"/blob/{revision}/", card.locator(".tool-technical a").get_attribute("href"))
        self.assertEqual(self.read()["version"], version)
        self.assertEqual(self.errors, [])

    def test_tab_during_pending_catalog_refresh_keeps_the_users_new_focus(self):
        self.page.clock.install()
        bundle = self.automotive_fixture()
        self.page.locator('.node[data-id="E2"]').click()
        card = self.page.locator(".tool-card").first
        card.wait_for()
        card.locator("select").focus()
        revision = "f" * 40
        self.changed_catalog_routes(bundle, revision)
        self.page.evaluate(
            """() => {
                const original = window.fetch;
                let holdNext = true;
                window.heldCatalog = null;
                window.fetch = (url, options) => {
                    const reply = original(url, options);
                    if (String(url) === '/api/manager_structure/tool_catalog' && holdNext) {
                        holdNext = false;
                        return reply.then(response => new Promise(resolve => {
                            window.heldCatalog = {response, resolve};
                        }));
                    }
                    return reply;
                };
            }"""
        )
        self.page.clock.fast_forward(5000)
        self.page.wait_for_function("window.heldCatalog !== null")
        self.page.keyboard.press("Tab")
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "closeToolDialog")
        self.page.evaluate("window.heldCatalog.resolve(window.heldCatalog.response)")
        self.page.wait_for_function(
            "revision => document.getElementById('toolDialogMeta').title.includes(revision)",
            arg=revision,
        )
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "closeToolDialog")
        self.assertTrue(card.locator("select").is_enabled())
        self.assertTrue(self.page.locator("#toolDialog").is_visible())
        self.assertEqual(self.errors, [])
