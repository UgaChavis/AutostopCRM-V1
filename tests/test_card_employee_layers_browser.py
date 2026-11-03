"""Card and employee modal stacking on disposable CRM state."""

from tests.browser_ui_support import UiBrowserTestCase


class CardEmployeeLayersBrowserTests(UiBrowserTestCase):
    def open_employees(self):
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.wait_for_function("state.employeesWorkspaceLoading === false")

    def top_modal_at(self, selector):
        return self.page.locator(selector).evaluate("""element => {
          const rect = element.getBoundingClientRect();
          return document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
            ?.closest('.modal')?.id || '';
        }""")

    def open_card_from_employees(self, card_id):
        self.open_employees()
        self.page.evaluate(
            """id => {
          const shell = document.getElementById('employeesReportShell');
          shell.dataset.detailsOpen = 'true';
          const row = document.createElement('tr');
          row.dataset.cardId = id;
          const cell = document.createElement('td');
          cell.textContent = 'Synthetic card row';
          row.appendChild(cell);
          document.getElementById('employeesDetailTable').replaceChildren(row);
        }""",
            card_id,
        )
        self.page.locator("#employeesMonthInput").focus()
        self.page.get_by_text("Synthetic card row").click()
        self.page.wait_for_function(
            "id => state.activeCardIsFull && state.editingId === id", arg=card_id
        )
        self.assertEqual(self.page.evaluate("state.modalStack.at(-1).parentKey"), "employees")

    def test_card_order_print_and_return_stay_on_the_correct_layer(self):
        card_id = self.create_card("Synthetic employee card")
        self.load_board()
        self.open_card_from_employees(card_id)
        self.assertEqual(self.top_modal_at("#cardModalCloseButtonTop"), "cardModal")
        self.page.wait_for_function("document.activeElement.id === 'cardModalCloseButtonTop'")
        self.page.locator("#repairOrderButton").click()
        self.page.locator("#repairOrderModal.is-open").wait_for()
        self.assertEqual(
            self.top_modal_at('#repairOrderModal [data-close="repair-order"]'), "repairOrderModal"
        )
        self.page.locator("#repairOrderPrintButton").click()
        self.page.locator("#repairOrderPrintModal.is-open").wait_for()
        self.assertEqual(self.top_modal_at("#repairOrderPrintCloseX"), "repairOrderPrintModal")
        self.page.locator("#repairOrderPrintCloseX").click()
        self.page.locator('#repairOrderModal [data-close="repair-order"]').click()
        self.page.locator("#cardModalCloseButtonTop").click()
        self.assertTrue(self.page.locator("#employeesModal.is-open").is_visible())
        self.page.wait_for_function(
            "document.activeElement.matches('#employeesModal [data-close=employees]')"
        )
        self.assertFalse(
            self.page.locator("#cardModal").evaluate(
                "e => e.classList.contains('card-parent-employees')"
            )
        )

    def test_parent_close_and_logout_clear_layer_marker(self):
        card_id = self.create_card("Synthetic employee cleanup")
        self.load_board()
        self.open_card_from_employees(card_id)
        self.page.evaluate("closeNamedModal('employees')")
        self.assertFalse(
            self.page.locator("#cardModal").evaluate("e => e.classList.contains('is-open')")
        )
        self.assertFalse(
            self.page.locator("#cardModal").evaluate(
                "e => e.classList.contains('card-parent-employees')"
            )
        )
        self.open_card_from_employees(card_id)
        self.page.evaluate("clearOperatorSession()")
        self.assertFalse(
            self.page.locator("#cardModal").evaluate("e => e.classList.contains('is-open')")
        )
        self.assertFalse(
            self.page.locator("#cardModal").evaluate(
                "e => e.classList.contains('card-parent-employees')"
            )
        )

    def test_employees_opened_after_a_board_card_remain_on_top(self):
        card_id = self.create_card("Synthetic reverse order")
        self.load_board()
        self.open_card(card_id)
        self.page.evaluate("void openEmployeesModal()")
        self.page.locator("#employeesModal.is-open").wait_for()
        self.assertFalse(
            self.page.locator("#cardModal").evaluate(
                "e => e.classList.contains('card-parent-employees')"
            )
        )
        self.assertEqual(
            self.top_modal_at('#employeesModal [data-close="employees"]'), "employeesModal"
        )
