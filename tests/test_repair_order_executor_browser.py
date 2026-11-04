"""Saving an untouched order must not replace an unlinked executor name."""

from tests.browser_ui_support import UiBrowserTestCase


class RepairOrderExecutorBrowserTests(UiBrowserTestCase):
    def test_return_to_unavailable_employee_keeps_original_name(self):
        service = self.runtime.service
        inactive = service.save_employee({"name": "SYNTHETIC INACTIVE", "position": "Мастер"})[
            "employee"
        ]["id"]
        service.toggle_employee({"employee_id": inactive})
        missing = "synthetic-missing-employee"
        historical = 'SYNTHETIC "<img src=x>" & HISTORICAL'
        client = service.create_client({"display_name": "SYNTHETIC UNAVAILABLE"})["client"]["id"]
        card = service.create_card({"title": "SYNTHETIC UNAVAILABLE", "client_id": client})["card"][
            "id"
        ]
        service.update_card(
            {
                "card_id": card,
                "repair_order": {
                    "works": [
                        {
                            "name": "SYNTHETIC MISSING",
                            "quantity": "1",
                            "price": "100",
                            "executor_id": missing,
                            "executor_name": "",
                        },
                        {
                            "name": "SYNTHETIC INACTIVE",
                            "quantity": "1",
                            "price": "100",
                            "executor_id": inactive,
                            "executor_name": "",
                        },
                        {
                            "name": "SYNTHETIC HISTORICAL",
                            "quantity": "1",
                            "price": "100",
                            "executor_id": inactive,
                            "executor_name": historical,
                        },
                    ],
                    "materials": [
                        {
                            "name": "SYNTHETIC MATERIAL",
                            "quantity": "1",
                            "price": "10",
                            "executor_id": missing,
                            "executor_name": "",
                        }
                    ],
                },
            }
        )
        self.load_board()
        self.page.locator("#repairOrdersButton").click()
        self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{card}"]').click()
        self.page.wait_for_function("state.repairOrderWriteReady && !state.repairOrderLoading")
        works = self.page.locator('#repairOrderWorksBody [data-repair-order-cell="executor_id"]')
        materials = self.page.locator(
            '#repairOrderMaterialsBody [data-repair-order-cell="executor_id"]'
        )
        self.assertEqual(works.nth(0).locator("option:checked").inner_text(), "Сотрудник")
        self.assertEqual(works.nth(1).locator("option:checked").inner_text(), "Сотрудник")
        self.assertEqual(works.nth(2).locator("option:checked").inner_text(), historical)
        for selector, employee_id in (
            (works.nth(0), missing),
            (works.nth(1), inactive),
            (works.nth(2), inactive),
            (materials.nth(0), missing),
        ):
            selector.select_option(index=0)
            selector.select_option(employee_id)
        with self.page.expect_response("**/api/update_repair_order") as response:
            self.page.locator("#repairOrderSaveButton").click()
        self.assertEqual(response.value.status, 200)
        self.page.wait_for_function("!state.repairOrderMutationRequest")
        saved = service.get_card({"card_id": card})["card"]["repair_order"]
        self.assertEqual(
            [(row["executor_id"], row["executor_name"]) for row in saved["works"]],
            [(missing, ""), (inactive, ""), (inactive, historical)],
        )
        self.assertEqual(
            (saved["materials"][0]["executor_id"], saved["materials"][0]["executor_name"]),
            (missing, ""),
        )
        self.assertEqual(self.page.locator("#repairOrderModal img").count(), 0)

    def test_empty_legacy_selected_and_cleared_executors(self):
        service = self.runtime.service
        employee_id = self.runtime.employee_id
        employee = next(
            item
            for item in service.list_employees({"references_only": True})["employees"]
            if item["id"] == employee_id
        )
        legacy = 'SYNTHETIC "<img src=x>" & LEGACY'
        historical = 'SYNTHETIC "<img src=x>" & HISTORICAL'
        client = service.create_client({"display_name": "SYNTHETIC EXECUTOR"})["client"]["id"]
        card = service.create_card({"title": "SYNTHETIC ORDER", "client_id": client})["card"]["id"]
        service.update_card(
            {
                "card_id": card,
                "repair_order": {
                    "works": [
                        {"name": "SYNTHETIC EMPTY", "quantity": "1", "price": "100"},
                        {
                            "name": "SYNTHETIC LEGACY",
                            "quantity": "1",
                            "price": "100",
                            "executor_name": legacy,
                        },
                        {
                            "name": "SYNTHETIC ASSIGNED",
                            "quantity": "1",
                            "price": "100",
                            "executor_id": employee_id,
                            "executor_name": historical,
                        },
                        {
                            "name": "SYNTHETIC ASSIGNED WITHOUT NAME",
                            "quantity": "1",
                            "price": "100",
                            "executor_id": employee_id,
                            "executor_name": "",
                        },
                    ],
                    "materials": [
                        {
                            "name": "SYNTHETIC MATERIAL",
                            "quantity": "1",
                            "price": "10",
                            "executor_name": legacy,
                        },
                        {
                            "name": "SYNTHETIC MATERIAL HISTORICAL",
                            "quantity": "1",
                            "price": "10",
                            "executor_id": employee_id,
                            "executor_name": historical,
                        },
                    ],
                },
            }
        )
        self.load_board()
        self.page.locator("#repairOrdersButton").click()
        self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{card}"]').click()
        self.page.wait_for_function("state.repairOrderWriteReady && !state.repairOrderLoading")
        works = self.page.locator('#repairOrderWorksBody [data-repair-order-cell="executor_id"]')
        material = self.page.locator(
            '#repairOrderMaterialsBody [data-repair-order-cell="executor_id"]'
        )
        self.assertEqual(
            works.nth(1).locator("option:checked").inner_text(), legacy + " (без привязки)"
        )
        self.assertEqual(
            material.nth(0).locator("option:checked").inner_text(), legacy + " (без привязки)"
        )
        self.assertEqual(self.page.locator("#repairOrderModal img").count(), 0)
        self.assertFalse(self.page.evaluate("repairOrderModalHasUnsavedChanges()"))

        def save():
            with self.page.expect_response("**/api/update_repair_order") as response:
                self.page.locator("#repairOrderSaveButton").click()
            self.assertEqual(response.value.status, 200)
            self.page.wait_for_function("!state.repairOrderMutationRequest")
            return service.get_card({"card_id": card})["card"]["repair_order"]

        saved = save()
        self.assertEqual(saved["works"][0]["executor_name"], "")
        self.assertEqual(saved["works"][1]["executor_name"], legacy)
        self.assertEqual(saved["works"][2]["executor_id"], employee_id)
        self.assertEqual(saved["works"][2]["executor_name"], historical)
        self.assertEqual(saved["works"][3]["executor_id"], employee_id)
        self.assertEqual(saved["works"][3]["executor_name"], "")
        self.assertEqual(saved["materials"][0]["executor_name"], legacy)
        self.assertEqual(saved["materials"][1]["executor_name"], historical)

        works.nth(1).select_option(index=0)
        material.nth(0).select_option(index=0)
        saved = save()
        self.assertEqual(
            (saved["works"][1]["executor_id"], saved["works"][1]["executor_name"]), ("", "")
        )
        self.assertEqual(
            (saved["materials"][0]["executor_id"], saved["materials"][0]["executor_name"]), ("", "")
        )

        works.nth(1).select_option(employee_id)
        saved = save()
        self.assertEqual(
            (saved["works"][1]["executor_id"], saved["works"][1]["executor_name"]),
            (employee_id, employee["name"]),
        )
        works.nth(1).select_option(index=0)
        saved = save()
        self.assertEqual(
            (saved["works"][1]["executor_id"], saved["works"][1]["executor_name"]), ("", "")
        )
        self.assertEqual(saved["works"][2]["executor_id"], employee_id)
        self.assertEqual(saved["works"][2]["executor_name"], historical)
        self.assertEqual(saved["works"][3]["executor_name"], "")

        works.nth(2).select_option(index=0)
        works.nth(2).select_option(employee_id)
        material.nth(1).select_option(index=0)
        material.nth(1).select_option(employee_id)
        saved = save()
        self.assertEqual(saved["works"][2]["executor_name"], employee["name"])
        self.assertEqual(saved["materials"][1]["executor_name"], employee["name"])
        self.assertEqual(saved["works"][3]["executor_name"], "")
