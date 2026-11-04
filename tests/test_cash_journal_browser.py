"""Synthetic journal rendering, transfer pairing and period filtering."""

from tests.browser_ui_support import UiBrowserTestCase


class CashJournalBrowserTests(UiBrowserTestCase):
    def test_transfer_pairs_and_period_filter(self):
        def entry(identifier, direction, date, cashbox, name, **extra):
            return {
                "id": identifier,
                "direction": direction,
                "business_date": date,
                "date": date,
                "time_short": "12:30",
                "cashbox_id": cashbox,
                "cashbox_name": name,
                "amount_minor": 10000,
                "note": "synthetic",
                "actor_name": "synthetic",
                **extra,
            }

        entries = [
            entry(
                "linked-out",
                "expense",
                "2026-10-02",
                "a",
                "Alpha",
                source_label="перемещение",
                related_transaction_id="linked-in",
            ),
            entry(
                "linked-in",
                "income",
                "2026-10-02",
                "b",
                "Beta",
                source_label="перемещение",
                related_transaction_id="linked-out",
            ),
            entry(
                "legacy-out",
                "expense",
                "2026-10-03",
                "a",
                "Alpha",
                source_label="перемещение",
                note="перемещение в Beta",
            ),
            entry(
                "legacy-in",
                "income",
                "2026-10-03",
                "b",
                "Beta",
                source_label="перемещение",
                note="перемещение из Alpha",
            ),
            entry("unpaired", "expense", "2026-10-04", "a", "Alpha", source_label="перемещение"),
            entry(
                "regular",
                "income",
                "2026-10-04",
                "b",
                "Beta",
                source_label="ручное",
                note="ordinary synthetic income",
            ),
        ]
        days = [
            {"key": date, "date": date, "label": date}
            for date in ("2026-10-04", "2026-10-03", "2026-10-02")
        ]
        journal = {
            "entries": entries,
            "days": days,
            "weeks": [],
            "months": [],
            "totals": {"count": len(entries)},
            "meta": {"months": 3, "total": len(entries), "returned": len(entries)},
        }
        self.page.route(
            "**/api/get_cash_journal?**",
            lambda route: route.fulfill(status=200, json={"ok": True, "data": journal}),
        )
        self.load_board()
        self.page.locator("#cashboxesButton").click()
        self.page.locator("#cashboxesModal.is-open").wait_for()
        self.page.locator("#cashboxJournalButton").click()
        self.page.locator(
            "#cashboxJournalModal.is-open .cashbox-journal-operation-row"
        ).first.wait_for()

        rows = self.page.locator("#cashboxJournalModal .cashbox-journal-operation-row")
        transfers = self.page.locator(
            "#cashboxJournalModal .cashbox-journal-operation-row--transfer"
        )
        self.assertEqual(rows.count(), 4)
        self.assertEqual(transfers.count(), 2)
        self.assertIn("Alpha → Beta", transfers.nth(0).inner_text())
        self.assertIn("Alpha → Beta", transfers.nth(1).inner_text())
        self.assertIn(
            "ordinary synthetic income", self.page.locator("#cashboxJournalText").inner_text()
        )

        self.page.locator("#cashboxJournalStatsButton").click()
        self.page.locator(
            '[data-cash-journal-period-kind="day"][data-cash-journal-period-key="2026-10-03"]'
        ).click()
        self.page.locator(
            "#cashboxJournalModal .cashbox-journal-operation-row--transfer"
        ).wait_for()
        self.assertEqual(rows.count(), 1)
        self.assertEqual(transfers.count(), 1)
        self.assertIn("Alpha → Beta", transfers.first.inner_text())
        self.assertNotIn(
            "ordinary synthetic income", self.page.locator("#cashboxJournalText").inner_text()
        )
