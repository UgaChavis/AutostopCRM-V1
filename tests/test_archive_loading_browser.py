"""Archive loading states on disposable CRM data."""

from tests.browser_ui_support import UiBrowserTestCase


class ArchiveLoadingBrowserTests(UiBrowserTestCase):
    def setUp(self):
        super().setUp()
        self.load_board()
        self.held = []
        self.page.route("**/api/list_archived_cards?**", lambda route: self.held.append(route))

    def open_archive(self):
        self.page.locator("#archiveButton").click()
        self.page.locator("#archiveModal.is-open").wait_for()
        self.page.wait_for_timeout(100)

    def test_empty_and_nonempty_results_leave_loading_state(self):
        self.open_archive()
        self.assertEqual(len(self.held), 1)
        self.held.pop().fulfill(status=200, json={"ok": True, "data": {"cards": []}})
        self.page.wait_for_function("state.archiveLoaded && !state.archiveLoading")
        self.assertIn("АРХИВ ПУСТ", self.page.locator("#archiveList").inner_text())
        self.page.locator("#archiveSearchInput").fill("synthetic missing")
        self.assertIn("НИЧЕГО НЕ НАЙДЕНО", self.page.locator("#archiveList").inner_text())
        self.page.locator('#archiveModal [data-close="archive"]').click()
        self.open_archive()
        self.assertEqual(len(self.held), 1)
        self.held.pop().fulfill(
            status=200,
            json={
                "ok": True,
                "data": {"cards": [{"id": "synthetic-card", "title": "synthetic archive card"}]},
            },
        )
        self.page.wait_for_function("state.archiveLoaded && !state.archiveLoading")
        self.page.locator("#archiveSearchInput").fill("")
        self.assertIn("synthetic archive card", self.page.locator("#archiveList").inner_text())

    def test_error_and_retry(self):
        self.open_archive()
        self.held.pop().fulfill(
            status=503, json={"ok": False, "error": {"message": "Synthetic failure"}}
        )
        self.page.get_by_text("НЕ УДАЛОСЬ ЗАГРУЗИТЬ АРХИВ").wait_for()
        self.assertFalse(self.page.evaluate("state.archiveLoaded"))
        self.page.locator('#archiveModal [data-close="archive"]').click()
        self.open_archive()
        self.held.pop().fulfill(status=200, json={"ok": True, "data": {"cards": []}})
        self.page.get_by_text("АРХИВ ПУСТ").wait_for()

    def test_close_and_old_viewer_reply_do_not_disrupt_new_load(self):
        self.open_archive()
        self.page.locator('#archiveModal [data-close="archive"]').click()
        self.assertFalse(self.page.locator("#archiveModal").is_visible())
        self.held.pop().fulfill(status=200, json={"ok": True, "data": {"cards": []}})
        self.page.wait_for_function("state.archiveLoaded && !state.archiveLoading")
        self.assertFalse(self.page.locator("#archiveModal").is_visible())
        self.open_archive()
        self.assertIn("АРХИВ ПУСТ", self.page.locator("#archiveList").inner_text())
        self.page.locator('#archiveModal [data-close="archive"]').click()
        self.open_archive()
        self.page.locator('#archiveModal [data-close="archive"]').click()
        self.page.evaluate("""() => {
          resetViewerScopedState();
          void loadArchive(false);
        }""")
        self.page.wait_for_timeout(100)
        self.assertEqual(len(self.held), 2)
        self.held.pop(0).fulfill(
            status=200,
            json={"ok": True, "data": {"cards": [{"id": "old-card", "title": "old viewer"}]}},
        )
        self.page.wait_for_timeout(100)
        self.assertFalse(self.page.locator("#archiveModal").is_visible())
        self.assertTrue(self.page.evaluate("Boolean(state.archiveLoading)"))
        self.assertFalse(self.page.evaluate("state.archiveLoaded"))
        self.held.pop().fulfill(status=200, json={"ok": True, "data": {"cards": []}})
        self.page.wait_for_function("state.archiveLoaded && !state.archiveLoading")
        self.assertIn("АРХИВ ПУСТ", self.page.locator("#archiveList").inner_text())
