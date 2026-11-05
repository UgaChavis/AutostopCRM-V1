"""Pending client creation must keep its card and popup until the response."""

from tests.browser_ui_support import UiBrowserTestCase


class CardClientCreateInFlightBrowserTests(UiBrowserTestCase):
    def test_pending_create_blocks_close_navigation_and_allows_success(self):
        first = self.create_card("SYNTHETIC FIRST")
        second = self.create_card("SYNTHETIC SECOND")
        self.load_board()
        self.open_card(first)
        self.page.locator("#cardTitle").fill("SYNTHETIC CARD DRAFT")
        self.page.locator("[data-open-card-client-create]").click()
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC NEW CLIENT")
        self.page.locator("#cardClientCreateCommentInput").fill("SYNTHETIC CLIENT NOTE")
        held = []

        def delay(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            held.append((route, response))

        self.page.route("**/api/create_client", delay)
        self.page.click("#cardClientCreateSaveButton")
        self.page.wait_for_function(
            "state.cardClientCreateSaving && !!state.cardClientMutationRequest"
        )
        for _ in range(100):
            if held:
                break
            self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.assertEqual(
            self.page.evaluate("sessionStorage.getItem('autostop:card-client-create-pending')"), "1"
        )
        generation = self.page.evaluate("state.cardClientCreateGeneration")
        self.page.evaluate("document.querySelector('[data-open-card-client-create]').focus()")
        self.page.keyboard.press("Enter")
        self.assertEqual(self.page.evaluate("state.cardClientCreateGeneration"), generation)
        self.assertTrue(self.page.evaluate("state.cardClientCreateSaving"))
        self.assertEqual(
            self.page.locator("#cardClientCreateCommentInput").input_value(),
            "SYNTHETIC CLIENT NOTE",
        )
        self.page.locator('#cardClientCreateModal [data-close="card-client-create"]').first.click()
        self.assertTrue(
            self.page.locator("#cardClientCreateModal").evaluate(
                "e => e.classList.contains('is-open')"
            )
        )
        self.page.locator('#cardClientCreateModal [data-close="card-client-create"]').last.click()
        self.page.keyboard.press("Escape")
        self.page.locator("#cardClientCreateModal").dispatch_event("click")
        self.page.evaluate("document.querySelector('#cardModal [data-close=card]').click()")
        self.assertFalse(self.page.evaluate("closeNamedModal('card')"))
        self.page.evaluate("""() => {state.modalStack.unshift({key:'clients',parentKey:''});
            state.modalStack.find(entry=>entry.key==='card').parentKey='clients';}""")
        self.assertFalse(self.page.evaluate("closeNamedModal('clients')"))
        self.page.evaluate("id => openCardById(id)", second)
        self.page.evaluate("openNewCardInColumn('inbox')")
        self.assertEqual(self.page.evaluate("state.editingId"), first)
        self.assertTrue(
            self.page.locator("#cardClientCreateModal").evaluate(
                "e => e.classList.contains('is-open')"
            )
        )
        self.assertEqual(
            self.page.locator("#cardClientCreateNameInput").input_value(), "SYNTHETIC NEW CLIENT"
        )
        self.assertEqual(
            self.page.locator("#cardClientCreateCommentInput").input_value(),
            "SYNTHETIC CLIENT NOTE",
        )
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC CARD DRAFT")
        self.assertEqual(self.writes.count("create_client"), 1)
        held[0][0].fulfill(response=held[0][1])
        self.page.wait_for_function(
            "!state.cardClientCreateSaving && !state.cardClientMutationRequest"
        )
        self.assertFalse(
            self.page.locator("#cardClientCreateModal").evaluate(
                "e => e.classList.contains('is-open')"
            )
        )
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.writes.count("link_card_to_client"), 1)
        self.assertIsNone(
            self.page.evaluate("sessionStorage.getItem('autostop:card-client-create-pending')")
        )
        self.assertEqual(self.page.locator("#cardTitle").input_value(), "SYNTHETIC CARD DRAFT")
        self.assertEqual(self.page.evaluate("state.editingId"), first)

    def test_permission_revocation_hides_employee_ui_without_claiming_create_succeeded(self):
        card = self.create_card("SYNTHETIC PERMISSION CARD")
        self.load_board()
        self.page.locator("#employeesButton").click()
        self.page.locator("#employeesModal.is-open").wait_for()
        self.page.evaluate("id => openCardById(id)", card)
        self.page.wait_for_function("state.activeCardIsFull")
        self.page.locator("#cardTitle").fill("SYNTHETIC CARD DRAFT")
        self.page.locator("[data-open-card-client-create]").click()
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC CLIENT")
        self.page.evaluate("""() => {const original=setStatus; window.revokeStatuses=[];
            setStatus=(message,...args)=>{window.revokeStatuses.push(message);return original(message,...args)};}""")
        held = []

        def delay(route):
            response = route.fetch()
            self.assertEqual(response.status, 200)
            held.append((route, response))

        self.page.route("**/api/create_client", delay)
        self.page.click("#cardClientCreateSaveButton")
        for _ in range(100):
            if held:
                break
            self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.page.evaluate("""() => {state.operatorProfile.user.permissions=[];
            state.employeesCashboxesAccess=true;state.employeesReadAccess=true;syncEmployeesCashboxesAccessUi();}""")
        self.assertFalse(self.page.locator("#employeesModal").is_visible())
        self.assertFalse(self.page.locator("#cardModal").is_visible())
        self.assertFalse(self.page.locator("#cardClientCreateModal").is_visible())
        self.assertFalse(
            self.page.evaluate("state.employeesCashboxesAccess || state.employeesReadAccess")
        )
        self.assertFalse(self.page.evaluate("state.cardClientCreateSaving"))
        self.assertFalse(
            any("КЛИЕНТ СОЗДАН" in msg for msg in self.page.evaluate("window.revokeStatuses"))
        )
        held[0][0].fulfill(response=held[0][1])
        self.page.wait_for_function("!state.cardClientMutationRequest")
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.writes.count("link_card_to_client"), 0)
        self.assertEqual(
            self.page.evaluate("sessionStorage.getItem('autostop:card-client-create-pending')"), "1"
        )
        self.assertFalse(self.page.locator("#employeesModal").is_visible())
        self.page.unroute("**/api/create_client", delay)
        self.page.evaluate("id => openCardById(id)", card)
        self.page.wait_for_function(
            "id => state.editingId === id && state.activeCardIsFull", arg=card
        )
        self.page.locator("[data-open-card-client-create]").click()
        self.page.locator("#cardClientCreateNameInput").fill("SYNTHETIC CLIENT")
        self.page.click("#cardClientCreateSaveButton")
        self.page.wait_for_function("!state.cardClientCreateSaving")
        self.assertEqual(self.writes.count("create_client"), 1)
        self.assertEqual(self.writes.count("link_card_to_client"), 0)
