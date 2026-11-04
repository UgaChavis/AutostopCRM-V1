"""Mobile order edits preserve existing row identity and non-editable metadata."""

import json

from tests.browser_ui_support import UiBrowserTestCase


class MobileRepairOrderRowPreservationBrowserTests(UiBrowserTestCase):
    def prepare_order(self, *, restricted=False, injected_readback=None):
        worker = self.runtime.service.save_employee(
            {"name": "SYNTHETIC ROW WORKER", "salary_mode": "percent_only", "work_percent": "50"}
        )["employee"]
        card = self.create_card("SYNTHETIC ROW ORDER")
        self.runtime.service.get_repair_order({"card_id": card, "create_if_missing": True})
        self.runtime.service.update_repair_order(
            {
                "card_id": card,
                "repair_order": {
                    "works": [
                        {
                            "name": "SYNTHETIC FIRST WORK",
                            "quantity": "1",
                            "price": "1000",
                            "executor_id": worker["id"],
                            "executor_name": worker["name"],
                            "work_salary_cost_price": "123.456",
                            "work_salary_note": "SYNTHETIC SALARY NOTE",
                            "work_percent_snapshot": "37.77",
                            "salary_amount": "55.555",
                        },
                        {"name": "SYNTHETIC REMOVED WORK", "quantity": "1", "price": "5"},
                    ],
                    "materials": [
                        {
                            "name": "SYNTHETIC MATERIAL",
                            "quantity": "1",
                            "price": "20",
                            "cost_price": "4",
                            "executor_id": worker["id"],
                            "executor_name": worker["name"],
                            "material_percent_snapshot": "12.345",
                            "material_salary_amount": "2.345",
                        }
                    ],
                },
            }
        )
        before = self.runtime.service.get_repair_order(
            {"card_id": card, "create_if_missing": False}
        )["repair_order"]
        if restricted:
            created = self.context.request.post(
                self.runtime.base_url + "/api/save_operator_user",
                headers={"X-Operator-Session": self.session_token},
                data={
                    "username": "synthetic-row-viewer",
                    "password": "synthetic-row-viewer-password",
                    "role": "operator",
                },
            )
            self.assertEqual(created.status, 200)
            token = self.context.request.post(
                self.runtime.base_url + "/api/login_operator",
                data={
                    "username": "synthetic-row-viewer",
                    "password": "synthetic-row-viewer-password",
                },
            ).json()["data"]["session"]["token"]
            context = self.browser.new_context(
                viewport={"width": 390, "height": 844}, extra_http_headers=self.runtime.auth_headers
            )
            self.addCleanup(context.close)
            context.add_init_script(
                "localStorage.setItem('kanban-operator-session', " + json.dumps(token) + ");"
            )
            self.page = context.new_page()
            self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        else:
            self.page.set_viewport_size({"width": 390, "height": 844})
        self.load_board()
        self.page.locator('[data-mobile-view="repair-orders"]').click()
        if injected_readback:
            self.page.route("**/api/get_repair_order", injected_readback)
        self.page.locator(f'#mobileRepairOrdersList [data-open-repair-order-card="{card}"]').click()
        self.page.wait_for_function(
            "id => state.mobileRepairOrderCardId === id && !state.mobileRepairOrderLoading",
            arg=card,
        )
        return card, before

    def exercise_edit(self, *, restricted):
        card, before = self.prepare_order(restricted=restricted)
        self.page.locator('[data-mobile-repair-order-tab="works"]').click()
        rows = self.page.locator("#mobileRepairOrderWorks [data-mobile-repair-order-row]")
        rows.nth(0).locator('[data-mobile-repair-row-field="name"]').fill("SYNTHETIC EDITED WORK")
        rows.nth(1).locator('[data-mobile-repair-order-remove="works"]').click()
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        after = self.runtime.service.get_repair_order(
            {"card_id": card, "create_if_missing": False}
        )["repair_order"]
        self.assertEqual([row["name"] for row in after["works"]], ["SYNTHETIC EDITED WORK"])
        for section in ("works", "materials"):
            previous, current = before[section][0], after[section][0]
            for key in (
                "id",
                "executor_id",
                "executor_name",
                "work_salary_cost_price",
                "work_salary_note",
                "work_percent_snapshot",
                "salary_amount",
                "material_percent_snapshot",
                "material_salary_amount",
            ):
                self.assertEqual(current[key], previous[key], (section, key))

    def test_admin_edit_preserves_row_metadata(self):
        self.exercise_edit(restricted=False)

    def test_restricted_edit_preserves_server_private_metadata(self):
        self.exercise_edit(restricted=True)

    def test_new_row_does_not_inherit_deleted_row_metadata(self):
        card, before = self.prepare_order()
        self.page.locator('[data-mobile-repair-order-tab="works"]').click()
        rows = self.page.locator("#mobileRepairOrderWorks [data-mobile-repair-order-row]")
        rows.nth(1).locator('[data-mobile-repair-order-remove="works"]').click()
        rows.first.locator('[data-mobile-repair-order-remove="works"]').click()
        rows.first.locator('[data-mobile-repair-row-field="name"]').fill("SYNTHETIC NEW WORK")
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        after = self.runtime.service.get_repair_order(
            {"card_id": card, "create_if_missing": False}
        )["repair_order"]
        self.assertEqual(len(after["works"]), 1)
        self.assertEqual(after["works"][0]["executor_id"], "")
        self.assertNotIn(after["works"][0]["id"], [row["id"] for row in before["works"]])

    def test_duplicate_source_row_id_blocks_save(self):
        card, before = self.prepare_order()
        self.page.evaluate(
            "() => { state.mobileRepairOrderCard.repair_order.works[1].id = state.mobileRepairOrderCard.repair_order.works[0].id; }"
        )
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC MUST NOT SAVE")
        self.writes.clear()
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.assertNotIn("update_repair_order", self.writes)
        self.assertIn("НЕ УДАЛОСЬ ПРОВЕРИТЬ СТРОКИ", self.page.locator("#statusLine").inner_text())
        after = self.runtime.service.get_repair_order(
            {"card_id": card, "create_if_missing": False}
        )["repair_order"]
        self.assertEqual(after, before)

    def test_noncanonical_source_row_id_blocks_save(self):
        card, before = self.prepare_order()
        self.page.evaluate(
            "() => { state.mobileRepairOrderCard.repair_order.works[0].id = ' ' + state.mobileRepairOrderCard.repair_order.works[0].id; }"
        )
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC MUST NOT SAVE")
        self.writes.clear()
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.assertNotIn("update_repair_order", self.writes)
        self.assertIn("НЕ УДАЛОСЬ ПРОВЕРИТЬ СТРОКИ", self.page.locator("#statusLine").inner_text())
        self.page.evaluate("() => { state.mobileRepairOrderCard.repair_order.works[0].id = 123; }")
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.assertNotIn("update_repair_order", self.writes)
        for value in ("work  id", "work\u0085id", "x" * 81):
            self.page.evaluate(
                "id => { state.mobileRepairOrderCard.repair_order.works[0].id = id; }", value
            )
            self.page.locator("#mobileRepairOrderSaveButton").click()
            self.assertNotIn("update_repair_order", self.writes)
        self.page.evaluate(
            "id => { state.mobileRepairOrderCard.repair_order.works[0].id = id; document.querySelector('#mobileRepairOrderWorks [data-mobile-repair-order-row]').dataset.mobileRepairOrderRowId = ' ' + id; }",
            before["works"][0]["id"],
        )
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.assertNotIn("update_repair_order", self.writes)
        after = self.runtime.service.get_repair_order(
            {"card_id": card, "create_if_missing": False}
        )["repair_order"]
        self.assertEqual(after, before)

    def test_admin_closing_order_keeps_executor_and_records_salary_snapshot(self):
        card, before = self.prepare_order()
        worker_id = before["works"][0]["executor_id"]
        self.page.locator("#mobileRepairOrderStatusSelect").select_option("closed")
        self.page.locator('[data-mobile-repair-order-tab="payments"]').click()
        self.page.locator("#mobileRepairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#mobileRepairOrderPaymentAmount").fill("1025")
        self.page.locator("#mobileRepairOrderAddPaymentButton").click()
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        order = self.runtime.service.get_repair_order(
            {"card_id": card, "create_if_missing": False}
        )["repair_order"]
        self.assertEqual(order["status"], "closed")
        self.assertEqual(order["works"][0]["executor_id"], worker_id)
        self.assertEqual(order["works"][0]["work_executor_id_snapshot"], worker_id)
        self.assertTrue(order["works"][0]["salary_amount"])
        self.assertEqual(len(order["payments"]), 1)

    def test_missing_source_rows_block_save(self):
        card, before = self.prepare_order()
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC MUST NOT SAVE")
        self.writes.clear()
        self.page.evaluate("() => { delete state.mobileRepairOrderCard.repair_order.works; }")
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.assertNotIn("update_repair_order", self.writes)
        self.page.evaluate("() => { state.mobileRepairOrderCard.repair_order.works = null; }")
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.assertNotIn("update_repair_order", self.writes)
        after = self.runtime.service.get_repair_order(
            {"card_id": card, "create_if_missing": False}
        )["repair_order"]
        self.assertEqual(after, before)

    def exercise_changed_row_readback(self, *, change_total=False):
        card, before = self.prepare_order()
        self.assertEqual(before["works"][0]["work_salary_cost_price"], "123.46")
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC UNSAVED READBACK")
        self.page.locator('[data-mobile-repair-order-tab="payments"]').click()
        self.page.locator("#mobileRepairOrderPaymentCashbox").select_option(self.runtime.cashbox_id)
        self.page.locator("#mobileRepairOrderPaymentAmount").fill("123")
        self.page.locator("#mobileRepairOrderAddPaymentButton").click()
        before_cash = len(self.runtime.state_store.read_bundle()["cash_transactions"])
        writes = []

        def lost_reply(route):
            writes.append(route.request.url)
            self.assertEqual(route.fetch().status, 200)
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        def changed_readback(route):
            payload = route.fetch().json()
            work = payload["data"]["card"]["repair_order"]["works"][0]
            if change_total:
                work["total"] = "1000.001"
            else:
                work["work_salary_cost_price"] = "123.456"
            route.fulfill(json=payload)

        self.page.route("**/api/update_repair_order", lost_reply)
        self.page.route("**/api/get_repair_order", changed_readback)
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertTrue(self.page.evaluate("repairOrderWriteNeedsVerification()"))
        self.assertTrue(self.page.locator("#mobileRepairOrderSaveButton").is_disabled())
        self.assertEqual(
            self.page.locator("#mobileRepairOrderClient").input_value(),
            "SYNTHETIC UNSAVED READBACK",
        )
        self.page.evaluate("saveMobileRepairOrder()")
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            len(self.runtime.state_store.read_bundle()["cash_transactions"]), before_cash + 1
        )

    def test_payment_readback_does_not_hide_concurrent_salary_metadata_change(self):
        self.exercise_changed_row_readback()

    def test_payment_readback_does_not_round_changed_total(self):
        self.exercise_changed_row_readback(change_total=True)

    def test_raw_server_salary_metadata_is_sent_without_numeric_rounding(self):
        def inject_legacy_snapshot(route):
            payload = route.fetch().json()
            work = payload["data"]["card"]["repair_order"]["works"][0]
            work["salary_amount"] = "55.555"
            work["work_salary_cost_price"] = "9007199254740993.001"
            route.fulfill(json=payload)

        self.prepare_order(injected_readback=inject_legacy_snapshot)
        self.page.locator("#mobileRepairOrderClient").fill("SYNTHETIC RAW SNAPSHOT")
        sent = []

        def capture(route):
            sent.append(route.request.post_data_json)
            route.fulfill(
                status=400,
                json={"ok": False, "error": {"code": "validation_error", "message": "SYNTHETIC"}},
            )

        self.page.route("**/api/update_repair_order", capture)
        self.page.locator("#mobileRepairOrderSaveButton").click()
        self.page.wait_for_function("!state.mobileRepairOrderSaving")
        self.assertEqual(len(sent), 1)
        work = sent[0]["repair_order"]["works"][0]
        self.assertEqual(work["salary_amount"], "55.555")
        self.assertEqual(work["work_salary_cost_price"], "9007199254740993.001")
