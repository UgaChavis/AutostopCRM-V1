"""Desktop order text validation preserves the form before any write."""

from tests.browser_ui_support import UiBrowserTestCase


class DesktopRepairOrderTextLimitsBrowserTests(UiBrowserTestCase):
    def open_order(self):
        card_id = self.create_card("SYNTHETIC TEXT LIMIT")
        self.runtime.service.update_repair_order(
            {
                "card_id": card_id,
                "repair_order": {
                    "materials": [{"name": "SYNTHETIC", "catalog_number": "ORIGINAL"}]
                },
            }
        )
        self.load_board()
        self.open_card(card_id)
        self.page.locator("#repairOrderButton").click()
        self.page.wait_for_function("state.repairOrderWriteReady && !state.repairOrderLoading")
        return card_id

    def set_value(self, selector, value):
        # Existing legacy/API values can exceed the shorter desktop maxlength.
        self.page.locator(selector).first.evaluate(
            "(field, value) => {field.value = value; field.dispatchEvent(new Event('input', {bubbles:true}));}",
            value,
        )

    def test_text_boundaries_and_unicode_whitespace(self):
        card_id = self.open_order()
        fields = (
            ("#repairOrderClient", "КЛИЕНТ", "client"),
            ("#repairOrderVehicle", "АВТОМОБИЛЬ", "vehicle"),
            (
                '#repairOrderMaterialsBody [data-repair-order-cell="catalog_number"]',
                "АРТИКУЛ",
                "catalog_number",
            ),
        )
        for selector, label, key in fields:
            for good, bad in (("A" * 160, "A" * 161), ("A" * 158 + "😀" * 2, "A" * 159 + "😀" * 2)):
                with self.subTest(field=key, unicode="😀" in good):
                    self.set_value(selector, good)
                    with self.page.expect_response("**/api/update_repair_order") as response:
                        self.page.locator("#repairOrderSaveButton").click()
                    self.assertEqual(response.value.status, 200)
                    self.page.wait_for_function("!state.repairOrderMutationRequest")
                    stored = self.runtime.service.get_repair_order(
                        {"card_id": card_id, "create_if_missing": False}
                    )["repair_order"]
                    actual = stored["materials"][0][key] if key == "catalog_number" else stored[key]
                    self.assertEqual(actual, good)
                    self.set_value(selector, bad)
                    before = list(self.writes)
                    self.page.locator("#repairOrderSaveButton").click()
                    self.assertEqual(self.writes, before)
                    self.assertEqual(self.page.locator(selector).first.input_value(), bad)
                    self.assertIn(label, self.page.locator("#statusLine").inner_text())
                    self.set_value(selector, good)

        for value, expected in (
            ("A" * 155 + " " * 20, "A" * 155),
            ("A" * 150 + "\u0085" * 20 + "B" * 5, "A" * 150 + " " + "B" * 5),
            ("\ufeff" + "A" * 160 + "\ufeff", "A" * 160),
        ):
            with self.subTest(value=repr(value)):
                self.set_value("#repairOrderClient", value)
                with self.page.expect_response("**/api/update_repair_order") as response:
                    self.page.locator("#repairOrderSaveButton").click()
                self.assertEqual(response.value.status, 200)
                self.page.wait_for_function("!state.repairOrderMutationRequest")
                stored = self.runtime.service.get_repair_order(
                    {"card_id": card_id, "create_if_missing": False}
                )["repair_order"]
                self.assertEqual(stored["client"], expected)
        invalid = "A" * 159 + "\ufeff" + "B"
        self.set_value("#repairOrderClient", invalid)
        before = list(self.writes)
        self.page.locator("#repairOrderSaveButton").click()
        self.assertEqual(self.writes, before)
        self.assertEqual(self.page.locator("#repairOrderClient").input_value(), invalid)

    def test_payment_and_close_do_not_write_or_discard_draft(self):
        card_id = self.open_order()
        selector = '#repairOrderMaterialsBody [data-repair-order-cell="catalog_number"]'
        self.page.locator(selector).first.fill("X" * 161)
        self.page.locator("#repairOrderClient").fill("SYNTHETIC UNSAVED")
        self.page.locator("#repairOrderPaymentsButton").click()
        self.page.locator("#repairOrderPaymentsModal.is-open").wait_for()
        self.page.locator("#repairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#repairOrderPaymentAmount").fill("3")
        before = list(self.writes)
        self.page.locator("#repairOrderPaymentAddButton").click()
        self.assertEqual(self.writes, before)
        self.assertEqual(self.page.locator(selector).first.input_value(), "X" * 161)
        self.assertEqual(self.page.locator("#repairOrderClient").input_value(), "SYNTHETIC UNSAVED")
        self.assertEqual(self.page.locator("#repairOrderPaymentAmount").input_value(), "3")
        self.assertIn("АРТИКУЛ", self.page.locator("#repairOrderPaymentsMeta").inner_text())
        self.assertFalse(self.page.evaluate("id => repairOrderNeedsVerification(id)", card_id))
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator('#repairOrderPaymentsModal [data-close="repair-order-payments"]').click()
        before = list(self.writes)
        self.page.locator("#repairOrderCloseButton").click()
        self.assertEqual(self.writes, before)
        self.assertEqual(self.page.locator(selector).first.input_value(), "X" * 161)
        stored = self.runtime.service.get_repair_order(
            {"card_id": card_id, "create_if_missing": False}
        )["repair_order"]
        self.assertEqual(stored["payments"], [])
        self.assertEqual(stored["status"], "open")
