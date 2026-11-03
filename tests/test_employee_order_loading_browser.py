"""Opening employee report orders with delayed synthetic responses."""

from tests.browser_ui_support import UiBrowserTestCase


class EmployeeOrderLoadingBrowserTests(UiBrowserTestCase):
    def test_loading_failure_duplicate_and_stale_responses(self):
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.wait_for_function("state.employeesWorkspaceLoading === false")
        card_ids = [self.runtime.payroll_card_id, self.runtime.card_id]

        def show_rows():
            self.page.evaluate(
                """ids => {
              document.querySelector('#employeesReportShell').dataset.detailsOpen = 'true';
              const table = document.querySelector('#employeesDetailTable');
              table.replaceChildren();
              ids.forEach((id, index) => {
                const row = document.createElement('tr');
                row.dataset.cardId = id; row.dataset.openRepairOrder = '1';
                const cell = document.createElement('td'); cell.textContent = 'Synthetic row ' + index;
                row.appendChild(cell); table.appendChild(row);
              });
            }""",
                card_ids,
            )

        held = []
        self.page.route("**/api/get_repair_order", lambda route: held.append(route))
        show_rows()
        self.page.get_by_text("Synthetic row 0").click()
        self.page.get_by_role("status").get_by_text("Открываем заказ-наряд").wait_for()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.assertFalse(self.page.locator("#repairOrderModal").is_visible())
        self.page.get_by_text("Synthetic row 0").dispatch_event("click")
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)

        self.page.get_by_text("Synthetic row 1").click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 2)
        self.assertEqual(self.page.locator("[data-employee-order-status]").count(), 1)
        self.assertIn("Открываем", self.page.get_by_text("Synthetic row 1").inner_text())
        held.pop(0).fulfill(
            status=503, json={"ok": False, "error": {"message": "Synthetic old failure"}}
        )
        self.page.wait_for_timeout(100)
        self.assertFalse(self.page.locator("#repairOrderModal").is_visible())
        self.assertNotIn("Не удалось", self.page.locator("#employeesDetailTable").inner_text())
        held.pop().continue_()
        self.page.locator("#repairOrderModal.is-open").wait_for()
        self.page.wait_for_function("!document.querySelector('[data-employee-order-status]')")
        self.assertEqual(self.page.evaluate("state.modalStack.at(-1).parentKey"), "employees")
        self.page.locator('#repairOrderModal [data-close="repair-order"]').click()
        self.assertTrue(self.page.locator("#employeesModal").is_visible())

        show_rows()
        self.page.get_by_text("Synthetic row 0").click()
        self.page.get_by_role("status").get_by_text("Открываем заказ-наряд").wait_for()
        self.page.wait_for_timeout(100)
        held.pop().fulfill(
            status=503, json={"ok": False, "error": {"message": "Synthetic failure"}}
        )
        self.page.get_by_role("status").get_by_text("Не удалось открыть заказ-наряд").wait_for()
        self.assertFalse(self.page.locator("#repairOrderModal").is_visible())

        self.page.get_by_text("Synthetic row 0").click()
        self.page.get_by_role("status").get_by_text("Открываем заказ-наряд").wait_for()
        self.page.wait_for_timeout(100)
        self.page.locator('#employeesModal [data-close="employees"]').click()
        held.pop().continue_()
        self.page.wait_for_timeout(100)
        self.assertFalse(self.page.locator("#repairOrderModal").is_visible())
        self.assertFalse(self.page.locator("#employeesModal").is_visible())

        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.wait_for_function("state.employeesWorkspaceLoading === false")
        show_rows()
        self.page.get_by_text("Synthetic row 0").click()
        self.page.get_by_role("status").get_by_text("Открываем заказ-наряд").wait_for()
        self.page.wait_for_timeout(100)
        self.page.evaluate("clearOperatorSession()")
        held.pop().continue_()
        self.page.wait_for_timeout(100)
        self.assertFalse(self.page.locator("#repairOrderModal").is_visible())
        self.assertEqual(self.page.locator("[data-employee-order-status]").count(), 0)
