"""Mobile order text must not be silently truncated by server normalization."""

from tests.browser_ui_support import UiBrowserTestCase


class MobileRepairOrderTextLimitsBrowserTests(UiBrowserTestCase):
    def open_order(self, *, closed=False):
        card_id = self.create_card("SYNTHETIC TEXT LIMIT")
        self.runtime.service.get_repair_order({"card_id": card_id, "create_if_missing": True})
        if closed:
            bundle = self.runtime.service._store.read_bundle()
            card = next(card for card in bundle["cards"] if card.id == card_id)
            card.repair_order.status = "closed"
            card.repair_order.closed_at = "05.11.2026 10:00"
            self.runtime.service._store.write_bundle(**bundle)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        if closed:
            self.page.evaluate("id => openMobileRepairOrderDetail(id)", card_id)
        else:
            self.page.locator(
                f'#mobileRepairOrdersList [data-open-repair-order-card="{card_id}"]'
            ).click()
        self.page.wait_for_function(
            "id => state.mobileRepairOrderCardId === id && !state.mobileRepairOrderLoading",
            arg=card_id,
        )
        return card_id

    def save(self):
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")

    def test_text_fields_at_ascii_and_unicode_boundaries(self):
        card_id = self.open_order()
        self.page.locator('[data-mobile-repair-order-tab="materials"]').click()
        self.page.locator('[data-mobile-repair-order-add-row="materials"]').click()
        material = self.page.locator(
            "#mobileRepairOrderMaterials [data-mobile-repair-order-row]"
        ).last
        material.locator('[data-mobile-repair-row-field="name"]').fill("SYNTHETIC")
        fields = (
            ("#mobileRepairOrderClient", "КЛИЕНТ", "client", "client"),
            ("#mobileRepairOrderVehicle", "АВТОМОБИЛЬ", "vehicle", "client"),
            (
                '#mobileRepairOrderMaterials [data-mobile-repair-row-field="catalog_number"]',
                "АРТИКУЛ",
                "catalog_number",
                "materials",
            ),
        )
        for selector, label, key, tab in fields:
            for good, bad in (("A" * 160, "A" * 161), ("A" * 158 + "😀" * 2, "A" * 159 + "😀" * 2)):
                with self.subTest(field=key, unicode="😀" in good):
                    self.page.locator(f'[data-mobile-repair-order-tab="{tab}"]').click()
                    field = self.page.locator(selector).last
                    field.fill(good)
                    self.save()
                    stored = self.runtime.service.get_repair_order(
                        {"card_id": card_id, "create_if_missing": False}
                    )["repair_order"]
                    actual = stored["materials"][0][key] if key == "catalog_number" else stored[key]
                    self.assertEqual(actual, good)
                    field = self.page.locator(selector).last
                    field.fill(bad)
                    before = list(self.writes)
                    self.save()
                    self.assertEqual(self.writes, before)
                    self.assertEqual(field.input_value(), bad)
                    self.assertIn(label, self.page.locator("#mobileStatusLine").inner_text())
                    self.assertIn("160", self.page.locator("#mobileStatusLine").inner_text())
                    field.fill(good)

    def test_python_whitespace_and_bom(self):
        card_id = self.open_order()
        field = self.page.locator("#mobileRepairOrderClient")
        for value, expected in (
            ("A" * 155 + " " * 20, "A" * 155),
            ("A" * 150 + "\u0085" * 20 + "B" * 5, "A" * 150 + " " + "B" * 5),
            ("\ufeff" + "A" * 160 + "\ufeff", "A" * 160),
        ):
            with self.subTest(value=repr(value)):
                field.fill(value)
                self.save()
                actual = self.runtime.service.get_repair_order(
                    {"card_id": card_id, "create_if_missing": False}
                )["repair_order"]["client"]
                self.assertEqual(actual, expected)
        value = "A" * 159 + "\ufeff" + "B"
        field.fill(value)
        before = list(self.writes)
        self.save()
        self.assertEqual(self.writes, before)
        self.assertEqual(field.input_value(), value)
        self.assertIn("КЛИЕНТ", self.page.locator("#mobileStatusLine").inner_text())

    def test_closed_order_never_previews_or_reopens_with_overlong_draft(self):
        card_id = self.open_order(closed=True)
        self.page.evaluate(
            "els.mobileRepairOrderDetail.querySelector('#mobileRepairOrderClient').value = 'X'.repeat(161)"
        )
        before = list(self.writes)
        self.save()
        self.assertEqual(self.writes, before)
        self.assertEqual(self.page.locator("#mobileRepairOrderClient").input_value(), "X" * 161)
        self.assertIn("КЛИЕНТ", self.page.locator("#mobileStatusLine").inner_text())
        result = self.runtime.service.get_repair_order(
            {"card_id": card_id, "create_if_missing": False}
        )
        self.assertEqual(result["repair_order"]["status"], "closed")

    def test_overlong_draft_does_not_write_pending_payment_or_status(self):
        card_id = self.open_order()
        self.page.locator("#mobileRepairOrderClient").fill("X" * 161)
        self.page.locator("#mobileRepairOrderStatusSelect").select_option("ready")
        self.page.locator('[data-mobile-repair-order-tab="payments"]').click()
        self.page.locator("#mobileRepairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#mobileRepairOrderPaymentAmount").fill("123")
        self.page.locator("#mobileRepairOrderAddPaymentButton").click()
        self.assertEqual(self.page.locator("[data-mobile-repair-order-payment-row]").count(), 1)
        before = list(self.writes)
        self.save()
        self.assertEqual(self.writes, before)
        self.assertEqual(self.page.locator("#mobileRepairOrderClient").input_value(), "X" * 161)
        self.assertEqual(self.page.locator("#mobileRepairOrderStatusSelect").input_value(), "ready")
        self.assertEqual(self.page.locator("[data-mobile-repair-order-payment-row]").count(), 1)
        result = self.runtime.service.get_repair_order(
            {"card_id": card_id, "create_if_missing": False}
        )["repair_order"]
        self.assertEqual(result["status"], "open")
        self.assertEqual(result["payments"], [])

    def test_overlong_row_name_focuses_its_own_tab_without_write(self):
        self.open_order()
        for section in ("works", "materials"):
            with self.subTest(section=section):
                self.page.locator(f'[data-mobile-repair-order-tab="{section}"]').click()
                self.page.locator(f'[data-mobile-repair-order-add-row="{section}"]').click()
                field = self.page.locator(
                    f'#mobileRepairOrder{section.title()} [data-mobile-repair-row-field="name"]'
                ).last
                # Imported/previously populated values can exceed the input's maxlength.
                field.evaluate(
                    "field => {field.value = 'X'.repeat(241); field.dispatchEvent(new Event('input', {bubbles:true}));}"
                )
                self.page.locator('[data-mobile-repair-order-tab="client"]').click()
                before = list(self.writes)
                self.save()
                self.assertEqual(self.writes, before)
                self.assertEqual(field.input_value(), "X" * 241)
                self.assertEqual(self.page.evaluate("state.mobileRepairOrderTab"), section)
                self.assertIn("240", self.page.locator("#mobileStatusLine").inner_text())
                field.fill("SYNTHETIC")
