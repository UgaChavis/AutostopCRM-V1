"""Opening employee report orders with delayed synthetic responses."""

from tests.browser_ui_support import UiBrowserTestCase


class EmployeeOrderLoadingBrowserTests(UiBrowserTestCase):
    def test_employee_change_suppresses_late_order_and_error_without_retry(self):
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.wait_for_function("state.employeesWorkspaceLoading === false")
        employee = self.page.evaluate("state.activeEmployeeId")
        order_id = self.runtime.payroll_card_id
        self.page.evaluate(
            """([employee, order]) => {
          state.employees.push({...state.employees.find(item => item.id === employee), id: 'synthetic-b'});
          state.payrollReport = {summary: [], detail_rows: [{employee_id: employee,
            card_id: order, repair_order_number: 'SYNTH-ORDER'}]};
        }""",
            [employee, order_id],
        )
        employee_a = self.page.locator(f'[data-employee-id="{employee}"]')
        employee_a.click()
        held = []
        self.page.route("**/api/get_repair_order", lambda route: held.append(route))
        for return_to_a, status in [(False, 200), (True, 503)]:
            employee_a.click()
            self.page.get_by_text("SYNTH-ORDER").click()
            self.page.wait_for_timeout(100)
            self.assertEqual(len(held), 1)
            self.page.locator('[data-employee-id="synthetic-b"]').click()
            if return_to_a:
                employee_a.click()
            self.page.evaluate("setStatus('SYNTHETIC BASELINE', false)")
            response = (
                {"ok": True, "data": {"card": {"id": order_id}, "repair_order": {}}}
                if status == 200
                else {"ok": False, "error": {"message": "OLD FAILURE"}}
            )
            held.pop().fulfill(status=status, json=response)
            self.page.wait_for_timeout(100)
            self.assertEqual(len(held), 0, "No automatic retry")
            self.assertFalse(self.page.locator("#repairOrderModal").is_visible())
            self.assertEqual(self.page.locator("#statusLine").inner_text(), "SYNTHETIC BASELINE")

    def test_month_roundtrip_suppresses_late_order_and_error_without_retry(self):
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.wait_for_function("state.employeesWorkspaceLoading === false")
        order_id = self.runtime.payroll_card_id
        held = []
        self.page.route("**/api/get_repair_order", lambda route: held.append(route))
        for status in [200, 503]:
            self.page.evaluate(
                """id => {
              document.querySelector('#employeesReportShell').dataset.detailsOpen = 'true';
              const table = document.querySelector('#employeesDetailTable');
              table.innerHTML = '<tr data-open-repair-order="1"><td>SYNTH-ORDER</td></tr>';
              table.firstElementChild.dataset.cardId = id;
            }""",
                order_id,
            )
            self.page.get_by_text("SYNTH-ORDER").click()
            self.page.wait_for_timeout(100)
            self.assertEqual(len(held), 1)
            self.page.evaluate("""() => {
              const month = state.payrollMonth;
              prepareEmployeesWorkspaceData(month === '2020-01' ? '2020-02' : '2020-01');
              prepareEmployeesWorkspaceData(month);
            }""")
            self.page.evaluate("setStatus('SYNTHETIC BASELINE', false)")
            response = (
                {"ok": True, "data": {"card": {"id": order_id}, "repair_order": {}}}
                if status == 200
                else {"ok": False, "error": {"message": "OLD FAILURE"}}
            )
            held.pop().fulfill(status=status, json=response)
            self.page.wait_for_timeout(100)
            self.assertEqual(len(held), 0, "No automatic retry")
            self.assertFalse(self.page.locator("#repairOrderModal").is_visible())
            self.assertEqual(self.page.locator("#statusLine").inner_text(), "SYNTHETIC BASELINE")

    def test_rerender_keeps_pending_request_and_invalidated_request_cannot_clear_new_one(self):
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.wait_for_function("state.employeesWorkspaceLoading === false")
        employee = self.page.evaluate("state.activeEmployeeId")
        order_id = self.runtime.payroll_card_id
        self.page.evaluate(
            """([employee, order]) => {
          state.employees.push({...state.employees.find(item => item.id === employee), id: 'synthetic-employee-b'});
          state.payrollReport = {summary: [], detail_rows: [
            {employee_id: employee, card_id: order, repair_order_number: 'SYNTH-ORDER'},
            {employee_id: 'synthetic-employee-b', card_id: order, repair_order_number: 'SYNTH-ORDER'}
          ]};
        }""",
            [employee, order_id],
        )
        employee_a = self.page.locator(f'[data-employee-id="{employee}"]')
        employee_a.click()
        held = []
        self.page.route("**/api/get_repair_order", lambda route: held.append(route))
        self.page.get_by_text("SYNTH-ORDER").click()
        self.page.get_by_text("Открываем заказ-наряд").wait_for()
        self.page.wait_for_timeout(100)
        employee_a.click()
        self.assertTrue(self.page.get_by_text("Открываем заказ-наряд").is_visible())
        self.page.get_by_text("SYNTH-ORDER").click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1, "Rerender must not cause a duplicate POST")

        self.page.locator('[data-employee-id="synthetic-employee-b"]').click()
        employee_a.click()
        self.page.get_by_text("SYNTH-ORDER").click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 2, "Changing employees must invalidate the old request")
        held.pop(0).fulfill(
            status=200, json={"ok": True, "data": {"card": {"id": order_id}, "repair_order": {}}}
        )
        self.page.wait_for_timeout(100)
        self.assertTrue(self.page.get_by_text("Открываем заказ-наряд").is_visible())
        self.assertFalse(self.page.locator("#repairOrderModal").is_visible())
        held.pop().fulfill(status=503, json={"ok": False, "error": {"message": "New failure"}})
        self.page.get_by_text("Не удалось открыть заказ-наряд").wait_for()

    def test_invalidated_order_can_be_reopened_and_old_reply_keeps_new_indicator(self):
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.wait_for_function("state.employeesWorkspaceLoading === false")
        order_id, card_id = self.runtime.payroll_card_id, self.runtime.card_id
        self.page.evaluate(
            """ids => {
          document.querySelector('#employeesReportShell').dataset.detailsOpen = 'true';
          const table = document.querySelector('#employeesDetailTable');
          table.replaceChildren();
          ids.forEach((id, index) => {
            const row = document.createElement('tr');
            row.dataset.cardId = id;
            row.dataset.openRepairOrder = index ? '0' : '1';
            const cell = document.createElement('td');
            cell.textContent = index ? 'Synthetic card B' : 'Synthetic order A';
            row.appendChild(cell); table.appendChild(row);
          });
        }""",
            [order_id, card_id],
        )
        orders, cards = [], []
        self.page.route("**/api/get_repair_order", lambda route: orders.append(route))
        self.page.route("**/api/get_card?**", lambda route: cards.append(route))
        self.page.get_by_text("Synthetic order A").click()
        self.page.get_by_text("Открываем заказ-наряд").wait_for()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(orders), 1)
        self.page.get_by_text("Synthetic card B").click()
        self.page.locator("#cardModal[data-card-loading].is-open").wait_for()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(cards), 1)
        self.page.keyboard.press("Escape")
        cards.pop().fulfill(
            status=200, json={"ok": True, "data": {"card": {"id": card_id, "title": "old"}}}
        )
        self.page.get_by_text("Synthetic order A").click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(orders), 2, "An invalidated request must not block a new click")
        orders.pop(0).fulfill(
            status=200,
            json={"ok": True, "data": {"card": {"id": order_id}, "repair_order": {}}},
        )
        self.page.wait_for_timeout(100)
        self.assertIn(
            "Открываем заказ-наряд", self.page.get_by_text("Synthetic order A").inner_text()
        )
        self.assertEqual(self.page.locator("[data-employee-order-pending]").count(), 1)
        self.assertFalse(self.page.locator("#repairOrderModal").is_visible())
        orders.pop().continue_()
        self.page.locator("#repairOrderModal.is-open").wait_for()
        self.page.wait_for_function("!document.querySelector('[data-employee-order-pending]')")

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
