"""Inventory editor drafts on native conflicts and uncertain creation replies."""

import json
import time

from tests.browser_ui_support import UiBrowserTestCase


class InventoryActionsBrowserTests(UiBrowserTestCase):
    def hold(self, path):
        held = []

        def release():
            while held:
                held.pop().abort("failed")

        self.addCleanup(release)
        self.page.route(path, lambda route: held.append(route))
        return held

    def await_request(self, held):
        deadline = time.monotonic() + 5
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)

    def test_pending_write_locks_fields_and_cannot_change_the_selected_item(self):
        self.open_inventory()
        self.page.locator("#inventoryNewButton").click()
        self.fill_new_item()
        held = self.hold("**/api/save_inventory_item")
        self.page.locator("#inventorySaveButton").click()
        self.await_request(held)
        self.assertTrue(self.page.locator("#inventoryNameInput").is_disabled())
        self.assertTrue(self.page.locator("#inventoryNewButton").is_disabled())
        self.assertFalse(self.page.evaluate("closeNamedModal('inventory')"))
        self.assertEqual(
            self.page.locator("#inventoryNameInput").input_value(), "Synthetic uncertain stock item"
        )
        self.page.evaluate("resetInventoryForm()")
        self.assertEqual(
            self.page.locator("#inventoryNameInput").input_value(), "Synthetic uncertain stock item"
        )
        held.pop().continue_()
        self.page.wait_for_function("!state.inventorySaving")
        self.assertFalse(self.page.locator("#inventoryNameInput").is_disabled())
        self.assertEqual(self.writes.count("save_inventory_item"), 1)

    def stocked_item(self):
        return self.runtime.service.save_inventory_item(
            {
                "name": "Synthetic replenishment target",
                "quantity": "10",
                "cost_price": "100",
                "sale_price": "150",
            }
        )["item"]

    def select_stocked_item(self, item):
        self.open_inventory()
        self.page.locator(f'button[data-inventory-item-id="{item["id"]}"]').click()
        self.page.locator("#inventoryReplenishQuantityInput").fill("2.5")

    def test_lost_replenishment_reply_does_not_add_stock_twice(self):
        item = self.stocked_item()
        self.select_stocked_item(item)

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/replenish_inventory_item", lose_reply)
        self.page.locator("#inventoryReplenishButton").click()
        self.page.wait_for_function("!state.inventorySaving")
        self.assertEqual(self.page.locator("#inventoryQuantityInput").input_value(), "12.5")
        self.assertEqual(self.page.locator("#inventoryReplenishQuantityInput").input_value(), "2.5")
        self.page.locator("#inventoryReplenishButton").click()
        self.page.wait_for_function("!state.inventorySaving")
        self.assertEqual(self.writes.count("replenish_inventory_item"), 1)
        stored = self.runtime.service.get_inventory_item({"item_id": item["id"]})["item"]
        self.assertEqual(stored["quantity"], "12.5")

    def test_search_refresh_keeps_an_unsaved_name_draft(self):
        item = self.stocked_item()
        self.select_stocked_item(item)
        self.page.locator("#inventoryNameInput").fill("Synthetic unsaved search draft")
        with self.page.expect_response("**/api/search_inventory_items"):
            self.page.locator("#inventorySearchInput").fill("Synthetic replenishment target")
        self.assertEqual(
            self.page.locator("#inventoryNameInput").input_value(), "Synthetic unsaved search draft"
        )
        self.assertEqual(self.page.evaluate("state.inventoryActiveId"), item["id"])

    def test_empty_search_keeps_the_selected_id_and_draft(self):
        item = self.stocked_item()
        self.select_stocked_item(item)
        self.page.locator("#inventoryNameInput").fill("Synthetic draft outside the search")
        with self.page.expect_response("**/api/search_inventory_items"):
            self.page.locator("#inventorySearchInput").fill("synthetic-no-results-for-this-query")
        self.page.wait_for_timeout(30)
        self.assertEqual(self.page.evaluate("state.inventoryActiveId"), item["id"])
        self.assertEqual(
            self.page.locator("#inventoryNameInput").input_value(),
            "Synthetic draft outside the search",
        )

    def test_saved_rename_outside_the_filter_keeps_its_confirmed_id(self):
        item = self.stocked_item()
        self.select_stocked_item(item)
        with self.page.expect_response("**/api/search_inventory_items"):
            self.page.locator("#inventorySearchInput").fill("Synthetic replenishment target")
        self.page.locator("#inventoryNameInput").fill("Synthetic renamed outside the filter")
        self.page.locator("#inventorySaveButton").click()
        self.page.wait_for_function("!state.inventorySaving")
        self.assertEqual(self.page.evaluate("state.inventoryActiveId"), item["id"])
        self.assertEqual(
            self.page.locator("#inventoryNameInput").input_value(),
            "Synthetic renamed outside the filter",
        )
        self.assertEqual(
            self.page.locator("#inventoryStatusLine").get_attribute("data-tone"), "normal"
        )

    def test_uncommitted_replenishment_retry_uses_the_original_native_revision(self):
        item = self.stocked_item()
        self.select_stocked_item(item)
        bodies = []

        def lose_before_commit(route):
            bodies.append(route.request.post_data_json)
            route.abort("failed")

        self.page.route("**/api/replenish_inventory_item", lose_before_commit)
        self.page.locator("#inventoryReplenishButton").click()
        self.page.wait_for_function("!state.inventorySaving")
        self.page.unroute("**/api/replenish_inventory_item")
        with self.page.expect_request("**/api/replenish_inventory_item") as retry:
            self.page.locator("#inventoryReplenishButton").click()
        self.page.wait_for_function("!state.inventorySaving")
        self.assertEqual(retry.value.post_data_json, bodies[0])
        late = self.context.request.post(
            self.runtime.base_url + "/api/replenish_inventory_item",
            headers={"X-Operator-Session": self.session_token},
            data=bodies[0],
        )
        self.assertEqual(late.status, 409)
        stored = self.runtime.service.get_inventory_item({"item_id": item["id"]})["item"]
        self.assertEqual(stored["quantity"], "12.5")

    def test_confirmed_replenishment_with_failed_refresh_retries_only_reads(self):
        item = self.stocked_item()
        self.select_stocked_item(item)
        self.page.route(
            "**/api/list_inventory_items?**",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps(
                    {"ok": False, "error": {"message": "Synthetic stock refresh failure"}}
                ),
            ),
        )
        self.page.locator("#inventoryReplenishButton").click()
        self.page.wait_for_function("!state.inventorySaving")
        self.page.unroute("**/api/list_inventory_items?**")
        self.page.locator("#inventoryReplenishButton").click()
        self.page.wait_for_function("!state.inventorySaving")
        self.assertEqual(self.writes.count("replenish_inventory_item"), 1)
        stored = self.runtime.service.get_inventory_item({"item_id": item["id"]})["item"]
        self.assertEqual(stored["quantity"], "12.5")

    def test_logout_releases_fields_and_ignores_a_late_confirmed_create(self):
        self.open_inventory()
        self.page.locator("#inventoryNewButton").click()
        self.fill_new_item()
        held = self.hold("**/api/save_inventory_item")
        self.page.locator("#inventorySaveButton").click()
        self.await_request(held)
        route = held.pop()
        response = route.fetch()
        self.assertEqual(response.status, 200)
        self.page.evaluate("clearOperatorSession()")
        route.fulfill(response=response)
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate("state.inventoryItems.length"), 0)
        self.assertEqual(self.page.evaluate("state.inventoryActiveId"), "")
        self.assertFalse(self.page.locator("#inventoryNameInput").is_disabled())
        self.assertEqual(self.page.locator("#inventoryNameInput").input_value(), "")

    def test_mobile_pending_save_locks_fields_and_navigation(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="inventory"]').click()
        self.page.wait_for_function("state.inventoryLoaded && state.mobileView==='inventory'")
        self.page.locator("#mobileInventoryNewButton").click()
        self.page.locator("#mobileInventoryNameInput").fill("Synthetic mobile stock item")
        held = self.hold("**/api/save_inventory_item")
        self.page.locator("#mobileInventorySaveButton").click()
        self.await_request(held)
        self.assertTrue(self.page.locator("#mobileInventoryNameInput").is_disabled())
        self.assertFalse(self.page.evaluate("setMobileView('board')"))
        self.assertEqual(self.page.evaluate("state.mobileView"), "inventory")
        held.pop().continue_()
        self.page.wait_for_function("!state.inventorySaving")
        self.assertFalse(self.page.locator("#mobileInventoryNameInput").is_disabled())

    def test_write_deadline_keeps_draft_and_restores_controls_without_a_repeat(self):
        self.context.add_init_script(
            "const schedule=window.setTimeout; window.setTimeout=(fn,ms,...a)=>schedule(fn,ms===90000?150:ms,...a);"
        )
        self.open_inventory()
        self.page.locator("#inventoryNewButton").click()
        self.fill_new_item()
        held = self.hold("**/api/save_inventory_item")
        self.page.locator("#inventorySaveButton").click()
        self.await_request(held)
        self.page.wait_for_function("!state.inventorySaving", timeout=3000)
        self.assertEqual(
            self.page.locator("#inventoryNameInput").input_value(), "Synthetic uncertain stock item"
        )
        self.assertFalse(self.page.locator("#inventoryNameInput").is_disabled())
        self.assertEqual(self.writes.count("save_inventory_item"), 1)

    def test_confirmed_create_with_failed_refresh_retries_only_the_read(self):
        self.open_inventory()
        self.page.locator("#inventoryNewButton").click()
        self.fill_new_item()
        self.page.route(
            "**/api/list_inventory_items?**",
            lambda route: route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps(
                    {"ok": False, "error": {"message": "Synthetic list failure after create"}}
                ),
            ),
        )
        self.page.locator("#inventorySaveButton").click()
        self.page.wait_for_function("!state.inventorySaving")
        known_id = self.page.evaluate("state.inventoryActiveId")
        self.assertTrue(known_id)
        self.assertEqual(
            self.page.locator("#inventoryStatusLine").get_attribute("data-tone"), "error"
        )
        self.page.unroute("**/api/list_inventory_items?**")
        self.page.locator("#inventorySaveButton").click()
        self.page.wait_for_function("!state.inventorySaving")
        self.assertEqual(self.page.evaluate("state.inventoryActiveId"), known_id)
        self.assertEqual(self.writes.count("save_inventory_item"), 1)

    def open_inventory(self):
        self.load_board()
        self.page.locator("#inventoryButton").click()
        self.page.locator("#inventoryModal.is-open").wait_for()
        self.page.wait_for_function("state.inventoryLoaded && !state.inventorySaving")

    def fill_new_item(self):
        self.page.locator("#inventoryNameInput").fill("Synthetic uncertain stock item")
        self.page.locator("#inventoryCatalogInput").fill("UI-DRAFT-001")
        self.page.locator("#inventoryQuantityInput").fill("2")
        self.page.locator("#inventoryCostPriceInput").fill("100")
        self.page.locator("#inventorySalePriceInput").fill("150")

    def test_native_conflict_keeps_the_visible_price_and_name_draft(self):
        item = self.runtime.service.save_inventory_item(
            {"name": "Synthetic conflict item", "cost_price": "100", "sale_price": "150"}
        )["item"]
        self.open_inventory()
        self.page.locator(f'button[data-inventory-item-id="{item["id"]}"]').click()
        self.page.locator("#inventoryNameInput").fill("Retained inventory name draft")
        self.page.locator("#inventoryCostPriceInput").fill("200")
        peer = self.runtime.service.save_inventory_item(
            {"item_id": item["id"], "name": "Synthetic peer revision", "cost_price": "300"}
        )["item"]
        with self.page.expect_response("**/api/save_inventory_item") as response:
            self.page.locator("#inventorySaveButton").click()
        self.assertEqual(response.value.status, 409)
        self.page.wait_for_function("!state.inventorySaving")
        self.assertEqual(
            self.page.locator("#inventoryNameInput").input_value(), "Retained inventory name draft"
        )
        self.assertEqual(self.page.locator("#inventoryCostPriceInput").input_value(), "200")
        self.assertEqual(
            self.runtime.service.get_inventory_item({"item_id": item["id"]})["item"], peer
        )

    def test_lost_create_reply_cannot_create_the_reentered_draft_again(self):
        self.open_inventory()
        self.page.locator("#inventoryNewButton").click()
        self.fill_new_item()
        receipt_ids = []

        def lose_reply(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            receipt_ids.append(response.json()["data"]["item"]["id"])
            route.abort("failed")

        self.page.route("**/api/save_inventory_item", lose_reply)
        self.page.locator("#inventorySaveButton").click()
        self.page.wait_for_function("!state.inventorySaving")
        self.fill_new_item()
        self.page.locator("#inventorySaveButton").click()
        self.page.wait_for_function("!state.inventorySaving")
        self.assertEqual(self.writes.count("save_inventory_item"), 1)
        self.assertEqual(len(set(receipt_ids)), 1)

    def test_uncertain_create_does_not_block_an_independent_known_item_edit(self):
        other = self.stocked_item()
        self.open_inventory()
        self.page.locator("#inventoryNewButton").click()
        self.fill_new_item()

        def lose_reply(route):
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        self.page.route("**/api/save_inventory_item", lose_reply)
        self.page.locator("#inventorySaveButton").click()
        self.page.wait_for_function("!state.inventorySaving")
        self.page.unroute("**/api/save_inventory_item")
        self.page.locator(f'button[data-inventory-item-id="{other["id"]}"]').click()
        self.page.locator("#inventoryNameInput").fill("Synthetic independent existing item edit")
        self.page.locator("#inventorySaveButton").click()
        self.page.wait_for_function("!state.inventorySaving")
        self.assertEqual(self.writes.count("save_inventory_item"), 2)
        stored = self.runtime.service.get_inventory_item({"item_id": other["id"]})["item"]
        self.assertEqual(stored["name"], "Synthetic independent existing item edit")
        self.assertEqual(stored["quantity"], other["quantity"])
