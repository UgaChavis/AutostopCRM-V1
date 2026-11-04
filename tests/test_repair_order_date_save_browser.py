"""Opening and saving legacy order dates must preserve the calendar date."""

from tests.browser_ui_support import UiBrowserTestCase


class RepairOrderDateSaveBrowserTests(UiBrowserTestCase):
    def test_open_and_save_legacy_dates_without_editing(self):
        service = self.runtime.service
        cases = []
        for index, (original, expected, displayed) in enumerate(
            (
                ("4.11.2026 19:30", "04.11.2026 19:30", "04.11.26"),
                ("04.11.69 19:30", "04.11.1969 19:30", "04.11.69"),
                ("04.11.2069 19:30", "04.11.2069 19:30", "04.11.2069"),
                ("04.11.2026 19:30:45", "04.11.2026 19:30:45", "04.11.26"),
                ("4.11.69 9:3:00", "04.11.1969 09:03:00", "04.11.69"),
                ("4.1.2026 9:3:4", "04.01.2026 09:03:04", "04.01.26"),
                ("04.11.2069 19:30:45", "04.11.2069 19:30:45", "04.11.2069"),
            )
        ):
            client = service.create_client({"display_name": f"SYNTHETIC DATE {index}"})["client"][
                "id"
            ]
            card = service.create_card({"title": f"SYNTHETIC ORDER {index}", "client_id": client})[
                "card"
            ]["id"]
            service.update_card(
                {
                    "card_id": card,
                    "repair_order": {
                        "opened_at": original,
                        "works": [{"name": "SYNTHETIC WORK", "quantity": "1", "price": "100"}],
                    },
                }
            )
            cases.append((client, card, expected, displayed))

        for client, card, expected, displayed in cases:
            with self.subTest(expected=expected):
                self.load_board()
                self.page.locator("#repairOrdersButton").click()
                row = self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{card}"]')
                row.wait_for()
                self.assertEqual(row.locator(".repair-orders-row__opened").inner_text(), displayed)
                row.click()
                self.page.wait_for_function(
                    "state.repairOrderWriteReady && !state.repairOrderLoading"
                )
                with self.page.expect_response("**/api/update_repair_order") as response:
                    self.page.locator("#repairOrderSaveButton").click()
                self.assertEqual(response.value.status, 200)
                self.page.wait_for_function("!state.repairOrderMutationRequest")
                saved = service.get_client({"client_id": client})["repair_orders"][0]
                self.assertEqual(saved["opened_at"], expected)
