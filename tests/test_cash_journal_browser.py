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
            entry("older", "income", "2026-09-20", "a", "Alpha", note="older synthetic income"),
        ]
        days = [
            {"key": date, "date": date, "label": date}
            for date in ("2026-10-04", "2026-10-03", "2026-10-02", "2026-09-20")
        ]
        journal = {
            "entries": entries,
            "days": days,
            "weeks": [],
            "months": [],
            "totals": {"count": len(entries)},
            "meta": {"months": 3, "total": len(entries), "returned": len(entries)},
            "markdown": "# Synthetic cash journal\n\nOpening balance: 0",
        }
        self.page.route(
            "**/api/get_cash_journal?**",
            lambda route: route.fulfill(status=200, json={"ok": True, "data": journal}),
        )
        self.load_board()
        self.page.locator("#cashboxesButton").click()
        self.page.locator("#cashboxesModal.is-open").wait_for()
        self.page.wait_for_function("state.cashboxesLoaded && !state.cashboxesLoadController")
        redundant_lists = []

        def fail_cashbox_list(route):
            redundant_lists.append(route.request.url)
            route.fulfill(status=503, json={"ok": False, "error": {"message": "Unavailable"}})

        self.page.route("**/api/list_cashboxes?limit=200", fail_cashbox_list)
        self.page.locator("#cashboxJournalButton").click()
        self.page.locator(
            "#cashboxJournalModal.is-open .cashbox-journal-operation-row"
        ).first.wait_for()

        rows = self.page.locator("#cashboxJournalModal .cashbox-journal-operation-row")
        transfers = self.page.locator(
            "#cashboxJournalModal .cashbox-journal-operation-row--transfer"
        )
        self.assertEqual(rows.count(), 5)
        self.assertEqual(transfers.count(), 2)
        self.assertIn("Alpha → Beta", transfers.nth(0).inner_text())
        self.assertIn("Alpha → Beta", transfers.nth(1).inner_text())
        self.assertIn(
            "ordinary synthetic income", self.page.locator("#cashboxJournalText").inner_text()
        )

        def apply_filters(filters):
            self.page.evaluate("filters => { state.cashboxJournalFilters = filters; }", filters)
            self.page.locator("#cashboxJournalLedgerButton").click()

        apply_filters({"period": "7"})
        self.assertEqual(rows.count(), 4)
        self.assertNotIn(
            "older synthetic income", self.page.locator("#cashboxJournalText").inner_text()
        )
        apply_filters({"type": "transfer"})
        self.assertEqual(rows.count(), 2)
        self.assertEqual(transfers.count(), 2)
        apply_filters({"cashbox": "b"})
        self.assertEqual(rows.count(), 3)
        apply_filters({"query": "ordinary synthetic income"})
        self.assertEqual(rows.count(), 1)
        self.assertIn("ordinary synthetic income", rows.first.inner_text())
        apply_filters({})

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
        self.page.locator('[data-close="cashbox-journal"]').click()
        with self.page.expect_download() as download:
            self.page.locator("#cashboxJournalDownloadButton").click()
        self.assertEqual(
            download.value.path().read_text(encoding="utf-8"), journal["markdown"] + "\n"
        )
        self.assertEqual(redundant_lists, [])
