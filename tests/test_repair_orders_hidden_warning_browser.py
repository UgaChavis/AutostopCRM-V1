"""Hidden inconsistent orders must be disclosed without changing business data."""

# The browser fixture selects this checkout before importing the application.
# ruff: noqa: I001
import json

from tests.browser_ui_support import UiBrowserTestCase

from minimal_kanban.repair_order import RepairOrder


class RepairOrdersHiddenWarningBrowserTests(UiBrowserTestCase):
    def make_inconsistent_card(self):
        card_id = self.create_card("SYNTHETIC HIDDEN ORDER")
        store = self.runtime.service._store
        bundle = store.read_bundle()
        card = next(card for card in bundle["cards"] if card.id == card_id)
        card.archived = True
        card.repair_order = RepairOrder.from_dict(
            {"number": "123", "status": "open", "client": "SYNTHETIC"}
        )
        store.write_bundle(
            **{
                key: bundle[key]
                for key in (
                    "columns",
                    "cards",
                    "clients",
                    "stickies",
                    "cashboxes",
                    "cash_transactions",
                    "inventory_items",
                    "inventory_movements",
                    "events",
                    "settings",
                )
            }
        )
        return card_id

    def operator_token(self):
        response = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "synthetic-hidden-order-operator",
                "password": "synthetic-hidden-order-password",
                "role": "operator",
                "permissions": [],
                "expected_permissions": [],
            },
        )
        self.assertEqual(response.status, 200)
        response = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={
                "username": "synthetic-hidden-order-operator",
                "password": "synthetic-hidden-order-password",
            },
        )
        self.assertEqual(response.status, 200)
        return response.json()["data"]["session"]["token"]

    def test_hidden_warning_for_both_roles_views_and_filters(self):
        card_id = self.make_inconsistent_card()
        operator = self.operator_token()
        for role, token in (("admin", self.session_token), ("operator", operator)):
            archive = self.context.request.get(
                self.runtime.base_url + "/api/list_archived_cards?limit=30&compact=1",
                headers={"X-Operator-Session": token},
            )
            self.assertEqual(archive.status, 200)
            for device, width in (("desktop", 1440), ("mobile", 390)):
                with self.subTest(role=role, device=device):
                    context = self.browser.new_context(
                        viewport={"width": width, "height": 900},
                        extra_http_headers=self.runtime.auth_headers,
                    )
                    self.addCleanup(context.close)
                    context.add_init_script(
                        "localStorage.setItem('kanban-operator-session', "
                        + json.dumps(token)
                        + ");"
                    )
                    page = context.new_page()
                    page.goto(self.runtime.browser_url)
                    page.wait_for_function(
                        "state.snapshot && state.operatorProfile && window.__AUTOSTOP_UI_BOUND__"
                    )
                    page.evaluate("stopSnapshotPolling()")
                    page.locator(
                        "#repairOrdersButton"
                        if device == "desktop"
                        else '[data-mobile-view="repair-orders"]'
                    ).click()
                    page.wait_for_function(
                        "!!state.repairOrdersMetaState && !state.repairOrdersSearchLoading"
                    )
                    self.assertEqual(
                        page.evaluate("state.repairOrdersMetaState.inconsistent_total"), 1
                    )
                    self.assertFalse(
                        page.evaluate(
                            "id => state.repairOrdersItems.some(item => item.card_id === id)",
                            card_id,
                        )
                    )
                    container = page.locator(
                        "#repairOrdersMeta" if device == "desktop" else "#mobileRepairOrdersList"
                    )
                    self.assertTrue(container.is_visible())
                    self.assertIn("СКРЫТ", container.inner_text().upper())
                    self.assertIn("АРХИВ", container.inner_text().upper())
                    page.evaluate("setRepairOrdersFilter('closed')")
                    page.wait_for_function(
                        "state.repairOrdersMetaState?.status === 'closed' && !state.repairOrdersSearchLoading"
                    )
                    self.assertEqual(
                        page.evaluate("state.repairOrdersMetaState.inconsistent_total"), 1
                    )
                    self.assertIn("СКРЫТ", container.inner_text().upper())
                    page.evaluate(
                        """() => {
                            state.repairOrdersItems = [];
                            renderRepairOrdersView();
                        }"""
                    )
                    self.assertIn("СКРЫТ", container.inner_text().upper())
                    for value in (0, -1, 1.5, "1", None):
                        page.evaluate(
                            "value => { state.repairOrdersMetaState.inconsistent_total = value; renderRepairOrdersView(); }",
                            value,
                        )
                        self.assertNotIn("СКРЫТ", container.inner_text().upper())
                    page.evaluate(
                        """() => {
                            state.repairOrdersMetaState.inconsistent_total = 1;
                            setRepairOrdersSearchLoading(true);
                            renderRepairOrdersView();
                        }"""
                    )
                    self.assertNotIn("СКРЫТ", container.inner_text().upper())
                    context.close()

    def test_warning_for_empty_result_and_not_during_error(self):
        self.make_inconsistent_card()
        self.load_board()
        self.page.locator("#repairOrdersButton").click()
        self.page.wait_for_function(
            "!!state.repairOrdersMetaState && !state.repairOrdersSearchLoading"
        )
        self.page.locator("#repairOrdersSearchInput").fill("SYNTHETIC NONEXISTENT ORDER")
        self.page.wait_for_function(
            "!state.repairOrdersSearchLoading && state.repairOrdersMetaState?.returned === 0"
        )
        self.assertIn("СКРЫТ", self.page.locator("#repairOrdersMeta").inner_text().upper())
        self.page.route(
            "**/api/list_repair_orders?**",
            lambda route: route.fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}}
            ),
        )
        self.page.evaluate("() => { loadRepairOrders(false); }")
        self.page.wait_for_function("state.repairOrdersLoadError")
        self.assertNotIn("СКРЫТ", self.page.locator("#repairOrdersMeta").inner_text().upper())
        self.assertTrue(self.page.locator("#repairOrdersMeta").is_visible())
        self.assertEqual(
            self.page.locator("#repairOrdersMeta").get_attribute("aria-hidden"), "false"
        )
        self.assertIn("ОШИБКА", self.page.locator("#repairOrdersMeta").inner_text().upper())

    def test_missing_or_invalid_count_in_real_response_is_error(self):
        self.make_inconsistent_card()
        self.load_board()
        self.page.locator("#repairOrdersButton").click()
        self.page.wait_for_function(
            "!!state.repairOrdersMetaState && !state.repairOrdersSearchLoading"
        )
        self.assertIn("СКРЫТ", self.page.locator("#repairOrdersMeta").inner_text().upper())
        for value in ("missing", None, -1, 1.5, "1"):
            with self.subTest(value=value):

                def respond(route):
                    response = route.fetch()
                    self.assertEqual(response.status, 200)
                    result = response.json()
                    meta = result["data"]["meta"]
                    if value == "missing":
                        del meta["inconsistent_total"]
                    else:
                        meta["inconsistent_total"] = value
                    route.fulfill(response=response, json=result)

                self.page.route("**/api/list_repair_orders?**", respond)
                self.page.evaluate("() => { loadRepairOrders(false); }")
                self.page.wait_for_function("state.repairOrdersLoadError")
                self.assertTrue(self.page.locator("#repairOrdersMeta").is_visible())
                self.assertIn("ОШИБКА", self.page.locator("#repairOrdersMeta").inner_text().upper())
                self.assertIn(
                    "НЕ ПОДТВЕРЖДЁН", self.page.locator("#repairOrdersList").inner_text().upper()
                )
                self.page.unroute("**/api/list_repair_orders?**", respond)
        self.page.evaluate("() => { loadRepairOrders(false); }")
        self.page.wait_for_function(
            "!state.repairOrdersLoadError && !state.repairOrdersSearchLoading"
        )
        self.assertIn("СКРЫТ", self.page.locator("#repairOrdersMeta").inner_text().upper())
