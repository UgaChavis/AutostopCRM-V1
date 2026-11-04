"""Legacy order numbers remain identifiable in both list layouts."""

# The browser fixture selects this checkout before importing the application.
# ruff: noqa: I001
import json

from tests.browser_ui_support import UiBrowserTestCase

from minimal_kanban.repair_order import RepairOrder


class RepairOrdersLegacyNumberBrowserTests(UiBrowserTestCase):
    def test_full_numbers_and_search_for_both_roles_and_layouts(self):
        numbers = ("A-123", "B-123", "СТО-٤٢", "СЕРВИС-" + "9" * 33, "123456789012", "123", "1234")
        ids = [self.create_card("SYNTHETIC LEGACY " + str(index)) for index in range(len(numbers))]
        store = self.runtime.service._store
        bundle = store.read_bundle()
        for card_id, number in zip(ids, numbers, strict=True):
            card = next(card for card in bundle["cards"] if card.id == card_id)
            card.repair_order = RepairOrder.from_dict(
                {"number": number, "status": "open", "client": "SYNTHETIC"}
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
        created = self.context.request.post(
            self.runtime.base_url + "/api/save_operator_user",
            headers={"X-Operator-Session": self.session_token},
            data={
                "username": "synthetic-legacy-number-operator",
                "password": "synthetic-legacy-number-password",
                "role": "operator",
                "permissions": [],
                "expected_permissions": [],
            },
        )
        self.assertEqual(created.status, 200)
        login = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={
                "username": "synthetic-legacy-number-operator",
                "password": "synthetic-legacy-number-password",
            },
        )
        self.assertEqual(login.status, 200)
        operator_token = login.json()["data"]["session"]["token"]
        for role, token in (("admin", self.session_token), ("operator", operator_token)):
            for device, width in (("desktop", 1440), ("desktop", 1024), ("mobile", 390)):
                with self.subTest(role=role, device=device, width=width):
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
                    for card_id, number in zip(ids, numbers, strict=True):
                        listing = (
                            "#repairOrdersList"
                            if device == "desktop"
                            else "#mobileRepairOrdersList"
                        )
                        row = page.locator(f'{listing} [data-open-repair-order-card="{card_id}"]')
                        row.wait_for()
                        if device == "desktop":
                            label = row.locator(".repair-orders-row__number")
                            self.assertEqual(label.inner_text(), number)
                            geometry = label.evaluate(
                                """el => {
                                        const range = document.createRange();
                                        range.selectNodeContents(el);
                                        const row = el.closest('.repair-orders-row').getBoundingClientRect();
                                        const next = el.closest('.repair-orders-row').children[1].getBoundingClientRect();
                                        return [...range.getClientRects()].map(rect => ({
                                            left: rect.left - row.left, right: rect.right - next.left,
                                            bottom: rect.bottom - row.bottom
                                        }));
                                    }"""
                            )
                            self.assertTrue(
                                all(
                                    part["left"] >= 0 and part["right"] <= 0 and part["bottom"] <= 0
                                    for part in geometry
                                ),
                                f"number {number!r} at width {width}: {geometry}; row="
                                + str(
                                    row.evaluate(
                                        "el => ({classes: el.className, height: getComputedStyle(el).height, inline: el.getAttribute('style')})"
                                    )
                                ),
                            )
                            if number in ("123", "1234"):
                                self.assertEqual(row.bounding_box()["height"], 58)
                        else:
                            label = row.locator(".mobile-repair-order-row__title")
                            self.assertTrue(label.inner_text().startswith("ЗН " + number + " ·"))
                            self.assertTrue(
                                label.evaluate("el => el.scrollWidth <= el.clientWidth")
                            )
                    if device == "desktop":
                        page.locator('[data-repair-orders-search-field="number"]').click()
                        page.locator("#repairOrdersSearchInput").fill("A-123")
                        page.wait_for_function(
                            "state.repairOrdersMetaState?.query === 'A-123' && !state.repairOrdersSearchLoading"
                        )
                        self.assertEqual(
                            page.locator(
                                f'#repairOrdersList [data-open-repair-order-card="{ids[0]}"]'
                            ).count(),
                            1,
                        )
                        self.assertEqual(
                            page.locator(
                                f'#repairOrdersList [data-open-repair-order-card="{ids[1]}"]'
                            ).count(),
                            0,
                        )
                        page.locator("#repairOrdersSearchInput").fill("123")
                        page.wait_for_function(
                            "state.repairOrdersMetaState?.query === '123' && !state.repairOrdersSearchLoading"
                        )
                        for card_id in ids[:2]:
                            self.assertEqual(
                                page.locator(
                                    f'#repairOrdersList [data-open-repair-order-card="{card_id}"]'
                                ).count(),
                                0,
                            )
                    context.close()
