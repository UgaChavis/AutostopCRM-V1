"""Regression checks for calculation acceptance and linked-row editor sessions."""

from __future__ import annotations

from tests.browser_ui_support import UiBrowserTestCase


class PrintingMoneyBrowserTests(UiBrowserTestCase):
    def make_order(self, name: str, price: str = "100") -> str:
        card_id = self.create_card(name)
        self.runtime.service.update_repair_order(
            {
                "card_id": card_id,
                "repair_order": {
                    "tax_label": "НДС 5%",
                    "works": [
                        {"id": card_id + "-row", "name": name, "quantity": "1", "price": price}
                    ],
                },
            }
        )
        return card_id

    def open_editor(self, card_id: str) -> None:
        self.open_card(card_id)
        self.page.locator("#repairOrderButton").click()
        self.page.locator("#repairOrderModal.is-open").wait_for()
        self.page.locator("#repairOrderPrintButton").click()
        self.page.locator("#repairOrderPrintModal.is-open").wait_for()
        self.page.locator("[data-completion-act-editor-open]").click()
        self.page.locator("#completionActEditorModal.is-open").wait_for()

    def test_stale_manual_act_needs_review_then_accepts_only_that_calculation(self):
        card_id = self.make_order("Synthetic manual acceptance")
        loaded = self.runtime.service.get_completion_act_form({"card_id": card_id})
        form = loaded["form"]
        form["calculation_mode"] = "manual_net"
        form["items"] = [
            {"name": "Synthetic manual", "unit": "шт", "quantity": "1", "price": "100"}
        ]
        self.runtime.service.save_completion_act_form(
            {
                "card_id": card_id,
                "form": form,
                "expected_version": 0,
                "expected_source_fingerprint": loaded["draft"]["current_source_fingerprint"],
                "idempotency_key": "browser-initial",
            }
        )
        self.runtime.service.update_repair_order(
            {"card_id": card_id, "repair_order": {"tax_label": "НДС 20%"}}
        )
        self.load_board()
        self.open_editor(card_id)
        self.page.wait_for_function(
            "document.querySelector('#completionActGrossTotal')?.textContent === '105,00'"
        )
        self.assertTrue(self.page.locator("#completionActSaveButton").is_disabled())
        self.assertEqual(
            self.runtime.service.get_completion_act_form({"card_id": card_id})["totals"]["gross"],
            "105.00",
        )
        self.page.locator("#completionActUpdateCalculationButton").click()
        self.page.wait_for_function(
            "document.querySelector('#completionActEditorFooterMeta')?.textContent.includes('После: 120,00')"
        )
        self.assertIn(
            "До обновления: 105,00",
            self.page.locator("#completionActEditorFooterMeta").inner_text(),
        )
        self.assertTrue(self.page.locator("#completionActSaveButton").is_enabled())
        # Editing money invalidates the reviewed projection; free text does not.
        self.page.locator('[data-completion-act-section="items"]').click()
        self.page.locator('[data-completion-act-item-field="price"]').fill("110")
        self.assertTrue(self.page.locator("#completionActSaveButton").is_disabled())
        self.page.locator("#completionActUpdateCalculationButton").click()
        self.page.wait_for_function(
            "document.querySelector('#completionActEditorFooterMeta')?.textContent.includes('После: 132,00')"
        )
        self.page.locator('[data-completion-act-section="document"]').click()
        self.page.locator("#completionActBasis").fill("Synthetic free-text edit")
        self.page.wait_for_timeout(400)
        self.assertTrue(self.page.locator("#completionActSaveButton").is_enabled())
        with self.page.expect_response(
            lambda response: response.url.endswith("/api/save_completion_act_form")
        ) as response:
            self.page.locator("#completionActSaveButton").click()
        result = response.value.json()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["totals"]["gross"], "132.00")
        self.assertFalse(result["data"]["draft"]["is_stale"])
        self.assertEqual(result["data"]["form"]["basis"], "Synthetic free-text edit")

    def test_calculation_refresh_preserves_a_concurrently_saved_draft(self):
        card_id = self.make_order("Synthetic concurrent draft")
        loaded = self.runtime.service.get_completion_act_form({"card_id": card_id})
        form = loaded["form"]
        form["calculation_mode"] = "manual_net"
        form["items"] = [
            {"name": "Synthetic manual", "unit": "шт", "quantity": "1", "price": "100"}
        ]
        first = self.runtime.service.save_completion_act_form(
            {
                "card_id": card_id,
                "form": form,
                "expected_version": 0,
                "expected_source_fingerprint": loaded["draft"]["current_source_fingerprint"],
                "idempotency_key": "concurrent-initial",
            }
        )
        self.load_board()
        self.open_editor(card_id)
        self.page.wait_for_function(
            "document.querySelector('#completionActGrossTotal')?.textContent === '105,00'"
        )
        self.page.locator("#completionActBasis").fill("Local unsaved basis")
        other = first["form"]
        other["items"][0]["price"] = "200"
        other["basis"] = "Concurrently saved basis"
        concurrent = self.runtime.service.save_completion_act_form(
            {
                "card_id": card_id,
                "form": other,
                "expected_version": 1,
                "expected_source_fingerprint": first["draft"]["current_source_fingerprint"],
                "idempotency_key": "concurrent-other",
            }
        )
        self.assertEqual(concurrent["totals"]["gross"], "210.00")
        self.page.locator("#completionActUpdateCalculationButton").click()
        self.page.wait_for_function(
            "document.querySelector('#status')?.textContent.includes('другом окне') || "
            "document.querySelector('#completionActEditorFooterMeta')?.textContent.includes('другом окне')"
        )
        self.assertEqual(
            self.page.locator("#completionActBasis").input_value(), "Local unsaved basis"
        )
        with self.page.expect_response(
            lambda response: response.url.endswith("/api/save_completion_act_form")
        ) as saved:
            self.page.locator("#completionActSaveButton").click()
        self.assertEqual(saved.value.status, 409)
        unchanged = self.runtime.service.get_completion_act_form({"card_id": card_id})
        self.assertEqual(unchanged["draft"]["version"], 2)
        self.assertEqual(unchanged["totals"]["gross"], "210.00")
        self.assertEqual(unchanged["form"]["basis"], "Concurrently saved basis")

    def test_loading_another_card_cannot_reuse_linked_rows(self):
        a, b = self.make_order("Synthetic linked A"), self.make_order("Synthetic linked B", "200")
        self.load_board()
        self.open_editor(a)
        self.page.wait_for_function(
            "document.querySelector('[data-completion-act-item-field=\"name\"]')?.value === 'Synthetic linked A'"
        )
        self.page.locator("#completionActEditorCloseX").click()
        self.page.locator("#repairOrderPrintCloseX").click()
        self.page.locator('#repairOrderModal [data-close="repair-order"]').click()
        self.page.locator("#cardModalCloseButtonTop").click()
        held = []
        self.page.route("**/api/get_completion_act_form", lambda route: held.append(route))
        self.open_editor(b)
        self.page.locator('[data-completion-act-section="items"]').click()
        self.assertTrue(self.page.locator("#completionActCalculationMode").is_disabled())
        self.assertTrue(self.page.locator("#completionActSaveButton").is_disabled())
        self.assertEqual(self.page.locator('[data-completion-act-item-field="name"]').count(), 0)
        self.page.wait_for_function(
            "document.querySelector('#completionActEditorMeta')?.textContent.includes('Загрузка')"
        )
        self.assertEqual(len(held), 1)
        held.pop().continue_()
        self.page.wait_for_function(
            "document.querySelector('[data-completion-act-item-field=\"name\"]')?.value === 'Synthetic linked B'"
        )
        self.assertTrue(self.page.locator("#completionActCalculationMode").is_enabled())
        self.assertTrue(self.page.locator('[data-completion-act-item-field="price"]').is_disabled())
