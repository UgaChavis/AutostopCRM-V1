"""Adding mobile order rows retains drafts and existing linked metadata."""

from tests.browser_ui_support import UiBrowserTestCase


class MobileRepairOrderAddRowsBrowserTests(UiBrowserTestCase):
    def open_order(self, card):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        self.page.locator(f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]').click()
        self.page.wait_for_function(
            "id => state.mobileRepairOrderCardId === id && !state.mobileRepairOrderLoading",
            arg=card,
        )

    def test_empty_order_add_keeps_one_placeholder_and_clean_close(self):
        card = self.create_card("SYNTHETIC EMPTY ROWS")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.open_order(card)
        for section, root in (
            ("works", "#mobileRepairOrderWorks"),
            ("materials", "#mobileRepairOrderMaterials"),
        ):
            self.page.locator(f'[data-mobile-repair-order-tab="{section}"]').click()
            self.assertEqual(self.page.locator(root + " [data-mobile-repair-order-row]").count(), 1)
            self.page.locator(f'[data-mobile-repair-order-add-row="{section}"]').click()
            self.page.locator(f'[data-mobile-repair-order-add-row="{section}"]').click()
            self.assertEqual(self.page.locator(root + " [data-mobile-repair-order-row]").count(), 1)
        dialogs = []
        self.page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
        self.page.locator("#mobileRepairOrderBackButton").click()
        self.assertEqual(dialogs, [])
        self.assertEqual(self.page.evaluate("state.mobileRepairOrderCardId"), "")

    def test_add_remove_and_save_preserves_work_and_inventory_links(self):
        worker = self.runtime.service.save_employee(
            {"name": "SYNTHETIC ADD WORKER", "salary_mode": "percent_only", "work_percent": "50"}
        )["employee"]
        item = self.runtime.service.save_inventory_item(
            {"name": "SYNTHETIC INVENTORY", "quantity": "10", "cost_price": "4", "sale_price": "20"}
        )["item"]
        card = self.create_card("SYNTHETIC ADD LINKED ROWS")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.runtime.service.update_repair_order(
            {
                "card_id": card,
                "repair_order": {
                    "works": [
                        {
                            "name": "SYNTHETIC OLD WORK",
                            "quantity": "1",
                            "price": "10",
                            "executor_id": worker["id"],
                            "executor_name": worker["name"],
                            "work_salary_note": "SYNTHETIC KEEP",
                        }
                    ]
                },
            }
        )
        self.runtime.service.write_off_inventory_item(
            {"card_id": card, "item_id": item["id"], "quantity": "1"}
        )
        before = self.runtime.service.get_repair_order(
            {"card_id": card, "create_if_missing": False}
        )["repair_order"]
        self.assertTrue(before["materials"][0]["inventory_movement_id"])
        self.open_order(card)
        self.page.locator('[data-mobile-repair-order-tab="works"]').click()
        works = self.page.locator("#mobileRepairOrderWorks [data-mobile-repair-order-row]")
        self.page.locator('[data-mobile-repair-order-add-row="works"]').click()
        self.page.locator('[data-mobile-repair-order-add-row="works"]').click()
        self.assertEqual(works.count(), 2)
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator("#mobileRepairOrderBackButton").click()
        self.assertEqual(self.page.evaluate("state.mobileRepairOrderCardId"), card)
        works.nth(1).locator('[data-mobile-repair-row-field="name"]').fill("SYNTHETIC REMOVE")
        works.nth(1).locator('[data-mobile-repair-order-remove="works"]').click()
        self.assertEqual(works.count(), 1)
        self.page.locator('[data-mobile-repair-order-add-row="works"]').click()
        works.nth(1).locator('[data-mobile-repair-row-field="name"]').fill("SYNTHETIC NEW WORK")
        self.page.locator('[data-mobile-repair-order-tab="materials"]').click()
        materials = self.page.locator("#mobileRepairOrderMaterials [data-mobile-repair-order-row]")
        self.page.locator('[data-mobile-repair-order-add-row="materials"]').click()
        self.page.locator('[data-mobile-repair-order-add-row="materials"]').click()
        self.assertEqual(materials.count(), 2)
        materials.nth(1).locator('[data-mobile-repair-row-field="name"]').fill(
            "SYNTHETIC NEW MATERIAL"
        )
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        after = self.runtime.service.get_repair_order(
            {"card_id": card, "create_if_missing": False}
        )["repair_order"]
        self.assertEqual(
            [row["name"] for row in after["works"]], ["SYNTHETIC OLD WORK", "SYNTHETIC NEW WORK"]
        )
        self.assertEqual(after["works"][0]["executor_id"], worker["id"])
        self.assertEqual(after["works"][0]["work_salary_note"], "SYNTHETIC KEEP")
        self.assertEqual(after["works"][1]["executor_id"], "")
        self.assertEqual(len(after["materials"]), 2)
        for key in ("id", "inventory_item_id", "inventory_movement_id", "inventory_unit"):
            self.assertEqual(after["materials"][0][key], before["materials"][0][key])
