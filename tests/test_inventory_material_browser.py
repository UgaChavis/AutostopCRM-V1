"""Inventory issue/return recovery on disposable native synthetic transactions."""

import json
import time

from tests.browser_ui_support import UiBrowserTestCase


class InventoryMaterialBrowserTests(UiBrowserTestCase):
    def test_successful_issue_preserves_unsaved_work_draft_without_persisting_it(self):
        item, card_id = self.open_material()
        self.page.locator("#repairOrderAddWorkRowButton").click()
        row = self.page.locator("#repairOrderWorksBody tr[data-repair-order-row]").last
        row.locator('[data-repair-order-cell="name"]').fill("Synthetic unsaved work")
        row.locator('[data-repair-order-cell="quantity"]').fill("1")
        row.locator('[data-repair-order-cell="price"]').fill("250")
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.page.wait_for_function("!state.inventoryMaterialSaving")
        names = self.page.locator(
            '#repairOrderWorksBody [data-repair-order-cell="name"]'
        ).evaluate_all("els=>els.map(e=>e.value)")
        self.assertIn("Synthetic unsaved work", names)
        self.assertEqual(self.stored_card(card_id)["repair_order"]["works"], [])
        self.assertEqual(
            self.runtime.service.get_inventory_item({"item_id": item["id"]})["item"]["quantity"],
            "9",
        )

    def open_material(self):
        item = self.runtime.service.save_inventory_item(
            {
                "name": "Synthetic material reconciliation",
                "quantity": "10",
                "cost_price": "100",
                "sale_price": "150",
            }
        )["item"]
        card_id = self.create_card("Synthetic material card")
        self.load_board()
        self.open_card(card_id)
        self.page.locator("#repairOrderButton").click()
        self.page.locator("#repairOrderModal.is-open").wait_for()
        self.page.locator("#repairOrderInventoryToggleButton").click()
        self.page.wait_for_function("state.inventoryLoaded")
        self.page.locator(f'[data-repair-order-inventory-item-id="{item["id"]}"]').click()
        self.page.locator("#repairOrderInventoryQuantityInput").fill("1")
        return item, card_id

    def test_pending_issue_locks_quantity_and_material_selection(self):
        item, _ = self.open_material()
        held = []
        self.page.route("**/api/write_off_inventory_item", lambda route: held.append(route))
        self.addCleanup(lambda: [route.abort("failed") for route in held])
        self.page.locator("#repairOrderInventoryIssueButton").click()
        deadline = time.monotonic() + 5
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)
        self.assertTrue(self.page.locator("#repairOrderInventoryQuantityInput").is_disabled())
        self.assertTrue(self.page.locator("#repairOrderInventoryFillButton").is_disabled())
        self.assertFalse(self.page.evaluate("closeNamedModal('repair-order')"))
        self.assertFalse(self.page.evaluate("closeNamedModal('card')"))
        self.assertTrue(self.page.evaluate("beginRepairOrderMutation() === null"))
        self.page.locator("[data-repair-order-inventory-item-id]").first.evaluate("e=>e.click()")
        self.page.locator("#repairOrderInventoryFillButton").evaluate("e=>e.click()")
        self.assertEqual(self.page.evaluate("state.repairOrderInventorySelectedId"), item["id"])
        self.page.evaluate("writeOffInventoryItem()")
        self.assertEqual(self.writes.count("write_off_inventory_item"), 1)
        held.pop().continue_()
        self.page.wait_for_function("!state.inventoryMaterialSaving")

    def test_lost_issue_reply_reconciles_ui_stock_and_native_row_without_repeating_write(self):
        item, card_id = self.open_material()

        def lose_reply(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            route.abort("failed")

        self.page.route("**/api/write_off_inventory_item", lose_reply)
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.page.wait_for_function("!state.inventoryMaterialSaving")
        self.assertEqual(
            self.runtime.service.get_inventory_item({"item_id": item["id"]})["item"]["quantity"],
            "9",
        )
        self.assertEqual(
            self.page.evaluate(
                "id=>state.inventoryItems.find(item=>item.id===id).quantity", item["id"]
            ),
            "9",
        )
        self.assertEqual(self.writes.count("write_off_inventory_item"), 1)
        self.assertTrue(
            self.stored_card(card_id)["repair_order"]["materials"][0]["inventory_movement_id"]
        )
        self.assertTrue(self.page.locator("#repairOrderInventoryIssueButton").is_disabled())
        self.assertTrue(self.page.locator("#repairOrderInventoryReturnButton").is_enabled())

    def wait_material(self):
        self.page.wait_for_function("!state.inventoryMaterialSaving")

    def assert_controls_ready(self):
        self.assertTrue(self.page.locator("#repairOrderInventoryQuantityInput").is_enabled())
        self.assertTrue(
            self.page.locator('#repairOrderModal [data-close="repair-order"]').first.is_enabled()
        )

    def stored_item(self, item):
        return self.runtime.service.get_inventory_item({"item_id": item["id"]})["item"]

    def lose_reply(self, route):
        response = route.fetch()
        self.assertEqual(response.status, 200)
        route.abort("failed")

    def reject(self, route):
        route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"ok": False, "error": {"message": "Synthetic read failure"}}),
        )

    def test_native_issue_return_preserves_rules_and_exact_ledger_links(self):
        item, card_id = self.open_material()
        self.page.locator("#repairOrderInventoryQuantityInput").fill("1.25")
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        self.assertEqual(self.stored_item(item)["quantity"], "8.75")
        order = self.stored_card(card_id)["repair_order"]
        row = order["materials"][0]
        movement_id = row["inventory_movement_id"]
        self.assertEqual((row["quantity"], row["cost_price"], row["price"]), ("1.25", "100", "150"))
        self.assert_controls_ready()
        self.page.locator("#repairOrderInventoryReturnButton").click()
        self.wait_material()
        self.assertEqual(self.stored_item(item)["quantity"], "10")
        self.assertEqual(
            self.stored_card(card_id)["repair_order"]["materials"][0]["inventory_movement_id"], ""
        )
        movements = self.runtime.service.list_inventory_movements({"card_id": card_id})["movements"]
        self.assertEqual(len(movements), 2)
        self.assertEqual(movements[1]["related_movement_id"], movement_id)
        self.assertEqual(self.writes.count("write_off_inventory_item"), 1)
        self.assertEqual(self.writes.count("return_inventory_movement"), 1)

    def test_lost_return_reply_reconciles_once_and_keeps_other_unsaved_fields(self):
        item, card_id = self.open_material()
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        self.page.locator("#repairOrderComment").fill("Synthetic unsaved note")
        self.page.route("**/api/return_inventory_movement", self.lose_reply)
        self.page.locator("#repairOrderInventoryReturnButton").click()
        self.wait_material()
        self.assertEqual(self.stored_item(item)["quantity"], "10")
        self.assertEqual(
            self.page.evaluate("id=>state.inventoryItems.find(i=>i.id===id).quantity", item["id"]),
            "10",
        )
        self.assertEqual(
            self.page.locator("#repairOrderComment").input_value(), "Synthetic unsaved note"
        )
        self.assertEqual(self.stored_card(card_id)["repair_order"]["comment"], "")
        self.assertEqual(self.writes.count("return_inventory_movement"), 1)
        self.assertTrue(self.page.locator("#repairOrderInventoryReturnButton").is_disabled())
        self.assert_controls_ready()

    def test_known_issue_with_failed_refresh_retries_read_even_when_stock_is_zero(self):
        item, card_id = self.open_material()
        self.page.locator("#repairOrderInventoryQuantityInput").fill("10")
        self.page.route("**/api/list_inventory_items?*", self.reject)
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        self.assertEqual(self.stored_item(item)["quantity"], "0")
        self.assert_controls_ready()
        self.assertTrue(self.page.locator("#repairOrderInventoryIssueButton").is_enabled())
        self.page.unroute("**/api/list_inventory_items?*")
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        self.assertEqual(self.writes.count("write_off_inventory_item"), 1)
        self.assertEqual(
            len(self.runtime.service.list_inventory_movements({"card_id": card_id})["movements"]), 1
        )
        self.assertTrue(self.page.locator("#repairOrderInventoryIssueButton").is_disabled())

    def test_known_return_with_failed_refresh_never_returns_stock_twice(self):
        item, card_id = self.open_material()
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        self.page.route("**/api/list_inventory_items?*", self.reject)
        self.page.locator("#repairOrderInventoryReturnButton").click()
        self.wait_material()
        self.assertEqual(self.stored_item(item)["quantity"], "10")
        self.assertTrue(self.page.locator("#repairOrderInventoryReturnButton").is_enabled())
        self.page.unroute("**/api/list_inventory_items?*")
        self.page.locator("#repairOrderInventoryReturnButton").click()
        self.wait_material()
        self.assertEqual(self.writes.count("return_inventory_movement"), 1)
        self.assertEqual(
            len(self.runtime.service.list_inventory_movements({"card_id": card_id})["movements"]), 2
        )
        self.assertTrue(self.page.locator("#repairOrderInventoryReturnButton").is_disabled())
        self.assertTrue(self.page.evaluate("state.inventoryLoaded"))

    def test_issue_preserves_unsaved_tags_and_marks_order_dirty(self):
        _, card_id = self.open_material()
        # Enable the existing optional section without changing any persisted preferences.
        self.page.locator('[data-repair-order-section="tags"]').evaluate(
            "e=>{e.classList.remove('hidden');e.setAttribute('aria-hidden','false')}"
        )
        self.page.locator("#repairOrderTagInput").fill("Synthetic draft")
        self.page.locator("#repairOrderTagAddButton").click()
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        self.assertEqual(self.page.evaluate("state.repairOrderTags[0]?.label"), "SYNTHETIC DRAFT")
        self.assertEqual(self.stored_card(card_id)["repair_order"]["tags"], [])
        self.assertTrue(
            self.page.locator("#repairOrderSaveButton").evaluate(
                "e=>e.classList.contains('is-dirty')"
            )
        )

    def test_return_preserves_unsaved_material_quantity_and_price(self):
        _, card_id = self.open_material()
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        row = self.page.locator("#repairOrderMaterialsBody tr[data-repair-order-row]").first
        row.locator('[data-repair-order-cell="quantity"]').fill("3,25")
        row.locator('[data-repair-order-cell="price"]').fill("777,50")
        self.page.locator("#repairOrderInventoryReturnButton").click()
        self.wait_material()
        row = self.page.locator("#repairOrderMaterialsBody tr[data-repair-order-row]").first
        self.assertEqual(row.locator('[data-repair-order-cell="quantity"]').input_value(), "3,25")
        self.assertEqual(row.locator('[data-repair-order-cell="price"]').input_value(), "777,50")
        self.assertEqual(self.stored_card(card_id)["repair_order"]["materials"][0]["quantity"], "1")

    def test_native_unsaved_row_limit_keeps_unrelated_material_draft_and_stock(self):
        item, card_id = self.open_material()
        row = self.page.locator("#repairOrderMaterialsBody tr[data-repair-order-row]").first
        row.locator('[data-repair-order-cell="name"]').fill("Synthetic other material draft")
        row.locator('[data-repair-order-cell="quantity"]').fill("2")
        self.page.locator("#repairOrderAddMaterialRowButton").click()
        self.page.locator('#repairOrderMaterialsBody [data-repair-order-cell="name"]').last.focus()
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        names = self.page.locator(
            '#repairOrderMaterialsBody [data-repair-order-cell="name"]'
        ).evaluate_all("els=>els.map(e=>e.value)")
        self.assertEqual(names, ["Synthetic other material draft", ""])
        self.assertEqual(self.stored_card(card_id)["repair_order"]["materials"], [])
        self.assertEqual(self.stored_item(item)["quantity"], "10")
        self.assertEqual(self.writes.count("write_off_inventory_item"), 1)
        self.assert_controls_ready()

    def test_real_card_revision_conflict_preserves_draft_and_restores_controls(self):
        item, card_id = self.open_material()
        self.page.locator("#repairOrderComment").fill("Synthetic conflict draft")
        peer = self.runtime.service.update_card(
            {"card_id": card_id, "title": "Synthetic peer revision"}
        )["card"]
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        self.assertEqual(self.stored_item(item)["quantity"], "10")
        self.assertEqual(self.stored_card(card_id)["title"], peer["title"])
        self.assertEqual(
            self.page.locator("#repairOrderComment").input_value(), "Synthetic conflict draft"
        )
        self.assert_controls_ready()

    def test_real_stock_revision_conflict_keeps_native_balance_and_unsaved_draft(self):
        item, card_id = self.open_material()
        self.page.locator("#repairOrderComment").fill("Synthetic stock conflict draft")
        self.runtime.service.replenish_inventory_item({"item_id": item["id"], "quantity": "1"})
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        self.assertEqual(self.stored_item(item)["quantity"], "11")
        self.assertEqual(self.stored_card(card_id)["repair_order"]["materials"], [])
        self.assertEqual(
            self.page.locator("#repairOrderComment").input_value(), "Synthetic stock conflict draft"
        )
        self.assertEqual(self.writes.count("write_off_inventory_item"), 1)
        self.assert_controls_ready()

    def test_mismatched_receipt_requires_native_readback_before_applying_result(self):
        item, card_id = self.open_material()

        def wrong_card(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            result = response.json()
            result["data"]["card"]["id"] = "synthetic-wrong-card"
            route.fulfill(response=response, body=json.dumps(result))

        self.page.route("**/api/write_off_inventory_item", wrong_card)
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        self.assertEqual(self.page.evaluate("state.activeCard.id"), card_id)
        self.assertEqual(self.stored_item(item)["quantity"], "9")
        self.assertEqual(self.writes.count("write_off_inventory_item"), 1)
        self.assertEqual(
            self.page.evaluate("state.activeCard.repair_order.materials[0].inventory_movement_id"),
            self.stored_card(card_id)["repair_order"]["materials"][0]["inventory_movement_id"],
        )
        self.assert_controls_ready()

    def test_unconfirmed_issue_retries_only_after_unchanged_read_with_original_guards(self):
        item, _ = self.open_material()
        self.page.route("**/api/write_off_inventory_item", lambda route: route.abort("failed"))
        bodies = []
        self.page.on(
            "request",
            lambda request: (
                bodies.append(request.post_data_json)
                if request.url.endswith("/api/write_off_inventory_item")
                else None
            ),
        )
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        self.assertEqual(self.stored_item(item)["quantity"], "10")
        self.page.unroute("**/api/write_off_inventory_item")
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        self.assertEqual(self.stored_item(item)["quantity"], "9")
        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[0], bodies[1])

    def test_failed_readback_holds_changed_intent_and_prevents_another_write(self):
        item, _ = self.open_material()
        self.page.route("**/api/write_off_inventory_item", self.lose_reply)
        self.page.route("**/api/get_card?*", self.reject)
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        self.assertEqual(self.stored_item(item)["quantity"], "9")
        self.page.locator("#repairOrderInventoryQuantityInput").fill("2")
        self.page.unroute("**/api/write_off_inventory_item")
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        self.assertEqual(self.writes.count("write_off_inventory_item"), 1)
        self.assertEqual(self.stored_item(item)["quantity"], "9")
        self.assert_controls_ready()

    def test_write_deadline_and_late_receipt_preserve_current_draft(self):
        item, _ = self.open_material()
        self.page.evaluate("""() => {
          const original=globalThis.setTimeout;
          globalThis.setTimeout=(callback,delay,...args)=>original(callback,delay===90000?80:delay,...args);
        }""")
        held = []
        self.page.route("**/api/write_off_inventory_item", lambda route: held.append(route))
        self.addCleanup(lambda: [route.abort("failed") for route in held])
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        self.assert_controls_ready()
        self.assertEqual(len(held), 1)
        held.pop().abort("failed")
        self.assertEqual(self.stored_item(item)["quantity"], "10")

    def test_stalled_refresh_releases_controls_and_old_read_cannot_restore_stale_stock(self):
        item, _ = self.open_material()
        self.page.evaluate("""() => {
          const original=globalThis.setTimeout;
          globalThis.setTimeout=(callback,delay,...args)=>original(callback,delay===20000?80:delay,...args);
        }""")
        held = []
        self.page.route("**/api/list_inventory_items?*", lambda route: held.append(route))
        self.addCleanup(lambda: [route.abort("failed") for route in held])
        self.page.locator("#repairOrderInventoryIssueButton").click()
        self.wait_material()
        self.assert_controls_ready()
        self.assertEqual(self.stored_item(item)["quantity"], "9")
        self.assertEqual(len(held), 1)
        held.pop().fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "data": {"items": [{**item, "quantity": "10"}]}}),
        )
        self.page.wait_for_timeout(100)
        self.assertEqual(
            self.page.evaluate("id=>state.inventoryItems.find(i=>i.id===id).quantity", item["id"]),
            "9",
        )
        self.assertEqual(self.writes.count("write_off_inventory_item"), 1)

    def test_logout_releases_old_controls_and_old_receipt_does_not_replace_new_card(self):
        item, _ = self.open_material()
        held = []
        self.page.route("**/api/write_off_inventory_item", lambda route: held.append(route))
        self.addCleanup(lambda: [route.abort("failed") for route in held])
        self.page.locator("#repairOrderInventoryIssueButton").click()
        deadline = time.monotonic() + 5
        while not held and time.monotonic() < deadline:
            self.page.wait_for_timeout(20)
        self.assertEqual(len(held), 1)
        self.page.evaluate("clearOperatorSession({openLogin:false})")
        self.assertTrue(self.page.locator("#repairOrderInventoryQuantityInput").is_enabled())
        self.page.evaluate(
            "state.activeCard={id:'synthetic-replacement',title:'Replacement card'};state.editingId='synthetic-replacement';"
        )
        held.pop().fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "data": {
                        "item": {**item, "quantity": "9"},
                        "card": {"id": "old"},
                        "movement": {"id": "old"},
                    },
                }
            ),
        )
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate("state.activeCard.title"), "Replacement card")
        self.assertEqual(self.page.evaluate("state.inventoryItems"), [])
        self.assertFalse(self.page.evaluate("state.inventoryMaterialSaving"))
