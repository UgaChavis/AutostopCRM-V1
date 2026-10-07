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

    def observe_redraws(self):
        self.page.evaluate("""() => {
            window.diagramRedraws = 0;
            new MutationObserver(() => { window.diagramRedraws++; })
                .observe(document.getElementById('diagram'), {childList: true});
        }""")

    def test_initial_automation_reply_preserves_focused_module_and_enter(self):
        bundle = self.automotive_fixture()
        self.page.clock.install()
        self.page.add_init_script("""(() => {
            const fetch = window.fetch;
            window.heldAutomationReplies = [];
            window.fetch = (url, options) => {
                const response = fetch(url, options);
                if (String(url) === '/api/automation_center/status') {
                    return response.then(response => new Promise(resolve => {
                        window.heldAutomationReplies.push({response, resolve});
                    }));
                }
                return response;
            };
        })();""")
        self.page.reload()
        module = self.page.locator('.node[data-id="E1"]')
        module.wait_for()
        self.page.wait_for_function("window.heldAutomationReplies.length === 1")
        self.observe_redraws()
        module.focus()
        self.assertTrue(module.evaluate("el => el === document.activeElement"))
        self.page.evaluate("""() => {
            const held = window.heldAutomationReplies.shift();
            held.resolve(held.response);
        }""")
        self.page.wait_for_function("window.diagramRedraws > 0")
        self.assertTrue(module.evaluate("el => el === document.activeElement"))
        self.page.keyboard.press("Enter")
        self.page.locator(".tool-card").first.wait_for()
        expected = next(
            item["tool_ids"] for item in bundle["modules"] if item["element_id"] == "E1"
        )
        self.assertEqual(
            self.page.locator(".tool-card").evaluate_all(
                "cards => cards.map(card => card.dataset.toolId)"
            ),
            expected,
        )
        # An automation redraw must also leave an already open dialog's focus alone.
        self.page.locator("#closeToolDialog").focus()
        self.observe_redraws()
        self.page.clock.fast_forward(15000)
        self.page.wait_for_function("window.heldAutomationReplies.length === 1")
        self.page.evaluate("""() => {
            const held = window.heldAutomationReplies.shift();
            held.resolve(held.response);
        }""")
        self.page.wait_for_function("window.diagramRedraws > 0")
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "closeToolDialog")
        self.assertEqual(self.errors, [])

    def test_version_poll_preserves_focused_selected_relation_and_controls(self):
        self.automotive_fixture()
        snapshot = self.read()
        diagram = {
            field: snapshot[field]
            for field in ("schema_version", "canvas", "elements", "relations")
        }
        diagram["elements"][1].update(parent=None, kind="module", x=520, y=320)
        diagram["relations"] = [
            {
                "id": "R1",
                "from": "E1",
                "to": "E2",
                "kind": "exchange",
                "direction": "forward",
                "path": "M350 140 H450 V347.5 H520",
                "label_x": 450,
                "label_y": 240,
                "label": "Initial relation",
            }
        ]
        result = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "replace",
                "diagram": diagram,
                "expected_version": snapshot["version"],
                "idempotency_key": "focus-relation-fixture",
            },
        )
        self.assertEqual(result.status, 200, result.text())
        self.page.clock.install()
        self.page.reload()
        self.page.locator("#modeToggle").click()
        self.page.locator("#mode").get_by_text("Владелец", exact=False).wait_for()
        self.page.wait_for_load_state("networkidle")
        edge = self.read()["relations"][0]
        self.page.locator("#jump").select_option("relation:" + edge["id"])
        label = self.page.locator('[name="label"]')
        self.assertTrue(label.evaluate("el => el === document.activeElement"))
        relation = self.page.locator(f'.edge[data-id="{edge["id"]}"]')
        relation.focus()
        self.assertTrue(relation.evaluate("el => el === document.activeElement"))
        labels = iter(
            ["First external revision", "Second external revision", "Third external revision"]
        )
        revisions = []

        def graph(route):
            response = route.fetch()
            payload = response.json()
            payload["data"]["version"] += len(revisions) + 1
            if len(revisions) == 3:
                payload["data"]["relations"] = []
            else:
                updated = next(
                    item for item in payload["data"]["relations"] if item["id"] == edge["id"]
                )
                updated["label"] = next(labels)
            revisions.append(payload["data"]["version"])
            route.fulfill(response=response, body=json.dumps(payload))

        self.page.route(self.runtime.base_url + "/api/manager_structure", graph)
        self.page.clock.fast_forward(5000)
        self.page.wait_for_function(
            "document.querySelector('[name=label]').value === 'First external revision'"
        )
        self.assertTrue(relation.evaluate("el => el === document.activeElement"))
        # Explicit activation still moves focus into the relation editor.
        self.page.keyboard.press("Enter")
        self.assertTrue(label.evaluate("el => el === document.activeElement"))
        self.page.clock.fast_forward(5000)
        self.page.wait_for_function(
            "document.querySelector('[name=label]').value === 'Second external revision'"
        )
        self.assertTrue(label.evaluate("el => el === document.activeElement"))
        self.page.locator("#fit").focus()
        self.page.clock.fast_forward(5000)
        self.page.wait_for_function(
            "document.querySelector('[name=label]').value === 'Third external revision'"
        )
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "fit")
        relation.focus()
        self.page.clock.fast_forward(5000)
        relation.wait_for(state="detached")
        self.assertFalse(
            self.page.evaluate("Boolean(document.activeElement.closest('.node,.edge,#editor'))")
        )
        self.assertEqual(len(revisions), 4)
        self.assertEqual(self.errors, [])

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
