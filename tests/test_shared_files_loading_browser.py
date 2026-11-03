"""Shared files open immediately without exposing or changing stale state."""

from tests.browser_ui_support import UiBrowserTestCase


class SharedFilesLoadingBrowserTests(UiBrowserTestCase):
    def test_reopen_during_rename_keeps_reopen_guidance_without_repeating_write(self):
        self.load_board()
        file = {"id": "synthetic-file", "original_name": "before.txt", "size_bytes": 10}
        reads = []

        def list_files(route):
            reads.append(route.request.url)
            route.fulfill(
                status=200,
                json={
                    "ok": True,
                    "data": {"files": [file], "storage": {"used_bytes": 10, "limit_bytes": 100}},
                },
            )

        self.page.route("**/api/list_shared_files", list_files)
        self.page.locator("#sharedFilesButton").click()
        self.page.wait_for_function("els.sharedFilesModal.dataset.sharedFilesReady === 'true'")
        self.page.locator('[data-shared-file-id="synthetic-file"]').click()
        held = []
        self.page.route("**/api/rename_shared_file", lambda route: held.append(route))
        self.page.on("dialog", lambda dialog: dialog.accept("after.txt"))
        self.page.locator("#sharedFilesRenameButton").click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.page.locator('#sharedFilesModal [data-close="shared-files"]').click()
        self.page.locator("#sharedFilesButton").click()
        self.assertIn("ОТКРОЙТЕ ФАЙЛЫ СНОВА", self.page.locator("#sharedFilesMeta").inner_text())
        file["original_name"] = "after.txt"
        held.pop().fulfill(status=200, json={"ok": True, "data": {"file": file}})
        self.page.wait_for_function("!state.sharedFilesMutationRequest")
        self.assertIn("ОТКРОЙТЕ ФАЙЛЫ СНОВА", self.page.locator("#sharedFilesMeta").inner_text())
        self.assertFalse(self.page.locator("#sharedFilesDesktop").is_visible())
        self.assertTrue(self.page.locator(".shared-files-toolbar").evaluate("e => e.inert"))
        self.page.locator('#sharedFilesModal [data-close="shared-files"]').click()
        before_reopen = len(reads)
        self.page.locator("#sharedFilesButton").click()
        self.page.get_by_text("after.txt").wait_for()
        self.assertEqual(len(reads), before_reopen + 1)
        self.assertEqual(self.writes.count("rename_shared_file"), 1)

    def test_loading_and_error_keep_keyboard_focus_on_close(self):
        self.load_board()
        held = []
        self.page.route("**/api/list_shared_files", lambda route: held.append(route))
        close = self.page.locator('#sharedFilesModal [data-close="shared-files"]')
        self.page.locator("#sharedFilesButton").click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.assertTrue(close.evaluate("e => document.activeElement === e"))
        close.focus()
        held.pop().fulfill(status=200, json={"ok": True, "data": {"files": [], "storage": {}}})
        self.page.wait_for_function("els.sharedFilesModal.dataset.sharedFilesReady === 'true'")
        self.assertTrue(close.evaluate("e => document.activeElement === e"))
        close.click()
        self.page.locator("#sharedFilesButton").click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        held.pop().fulfill(status=403, json={"ok": False, "error": {"message": "Denied"}})
        self.page.wait_for_function("els.sharedFilesMeta.textContent.includes('Denied')")
        self.assertTrue(close.evaluate("e => document.activeElement === e"))

    def test_failed_refresh_keeps_reopen_guidance_during_rename(self):
        self.load_board()
        file = {"id": "synthetic-file", "original_name": "before.txt", "size_bytes": 10}
        fail_refresh = []

        def list_files(route):
            if fail_refresh:
                route.fulfill(
                    status=503, json={"ok": False, "error": {"message": "Refresh failed"}}
                )
            else:
                route.fulfill(
                    status=200, json={"ok": True, "data": {"files": [file], "storage": {}}}
                )

        self.page.route("**/api/list_shared_files", list_files)
        self.page.locator("#sharedFilesButton").click()
        self.page.wait_for_function("els.sharedFilesModal.dataset.sharedFilesReady === 'true'")
        self.page.locator('[data-shared-file-id="synthetic-file"]').click()
        held = []
        self.page.route("**/api/rename_shared_file", lambda route: held.append(route))
        self.page.on("dialog", lambda dialog: dialog.accept("after.txt"))
        self.page.locator("#sharedFilesRenameButton").click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.page.locator('#sharedFilesModal [data-close="shared-files"]').click()
        self.page.locator("#sharedFilesButton").click()
        fail_refresh.append(True)
        held.pop().fulfill(status=200, json={"ok": True, "data": {"file": file}})
        self.page.wait_for_function("!state.sharedFilesMutationRequest")
        self.assertIn("ОТКРОЙТЕ ФАЙЛЫ СНОВА", self.page.locator("#sharedFilesMeta").inner_text())
        self.assertIn("Refresh failed", self.page.locator("#statusLine").inner_text())
        self.assertEqual(self.writes.count("rename_shared_file"), 1)

    def test_loading_late_responses_access_failure_and_file_actions(self):
        self.load_board()
        held = []
        self.page.route("**/api/list_shared_files", lambda route: held.append(route))
        self.page.evaluate("""() => {
          state.sharedFiles = [{id:'old', original_name:'OLD PRIVATE FILE'}];
          state.sharedFilesActiveId = 'old';
          state.sharedFilesClipboardId = 'old';
          document.querySelector('#sharedFilesDesktop').textContent = 'OLD PRIVATE FILE';
        }""")

        def open_pending(expected=1):
            self.page.locator("#sharedFilesButton").click()
            self.page.locator("#sharedFilesModal.is-open").wait_for(timeout=1000)
            self.page.wait_for_timeout(100)
            self.assertEqual(len(held), expected)
            self.assertIn("ЗАГРУЗКА", self.page.locator("#sharedFilesMeta").inner_text())
            self.assertFalse(self.page.locator("#sharedFilesDesktop").is_visible())
            self.assertTrue(self.page.locator(".shared-files-toolbar").evaluate("e => e.inert"))
            self.assertTrue(self.page.locator("#sharedFilesDesktop").evaluate("e => e.inert"))

        def fulfill(rows):
            held.pop(0).fulfill(
                status=200,
                json={
                    "ok": True,
                    "data": {"files": rows, "storage": {"used_bytes": 0, "limit_bytes": 1}},
                },
            )

        open_pending()
        self.page.locator("#sharedFilesButton").dispatch_event("click")
        self.assertEqual(len(held), 1)
        self.assertEqual(
            self.page.evaluate("state.modalStack.filter(x => x.key === 'shared-files').length"), 1
        )
        self.page.evaluate("""async () => {
          const file = new File(['synthetic'], 'synthetic.txt', {type:'text/plain'});
          const data = new DataTransfer(); data.items.add(file);
          const desktop = document.querySelector('#sharedFilesDesktop');
          desktop.dispatchEvent(new ClipboardEvent('paste', {bubbles:true, clipboardData:data}));
          desktop.dispatchEvent(new DragEvent('drop', {bubbles:true, dataTransfer:data}));
          const input = document.querySelector('#sharedFilesInput');
          input.files = data.files;
          input.dispatchEvent(new Event('change', {bubbles:true}));
          document.querySelector('#sharedFilesPasteButton').click();
          document.querySelector('[data-shared-files-menu-action="paste-clipboard"]').click();
          document.querySelector('[data-shared-files-menu-action="paste-crm"]').click();
          await new Promise(resolve => setTimeout(resolve, 50));
        }""")
        self.assertFalse(any("shared_file" in url for url in self.writes), self.writes)
        self.page.locator('#sharedFilesModal [data-close="shared-files"]').click()
        open_pending(expected=2)
        fulfill([{"id": "late", "original_name": "LATE PRIVATE FILE"}])
        self.page.wait_for_timeout(100)
        self.assertTrue(self.page.locator("#sharedFilesModal").is_visible())
        self.assertFalse(self.page.locator("#sharedFilesDesktop").is_visible())
        self.assertNotEqual(self.page.evaluate("state.sharedFiles[0]?.id"), "late")
        fulfill([{"id": "reopened", "original_name": "REOPENED FILE"}])
        self.page.get_by_text("REOPENED FILE").wait_for()
        self.page.locator('#sharedFilesModal [data-close="shared-files"]').click()

        open_pending()
        self.page.evaluate("state.viewerStateGeneration += 1")
        fulfill([{"id": "session-old", "original_name": "OTHER SESSION"}])
        self.page.wait_for_timeout(100)
        self.assertNotEqual(self.page.evaluate("state.sharedFiles[0]?.id"), "session-old")
        self.page.locator('#sharedFilesModal [data-close="shared-files"]').click()

        open_pending()
        held.pop().fulfill(status=403, json={"ok": False, "error": {"message": "Synthetic denied"}})
        self.page.wait_for_function(
            "document.querySelector('#sharedFilesMeta').textContent.includes('Synthetic denied')"
        )
        self.assertIn("Synthetic denied", self.page.locator("#sharedFilesMeta").text_content())
        self.assertFalse(self.page.locator("#sharedFilesDesktop").is_visible())
        self.assertTrue(self.page.locator(".shared-files-toolbar").evaluate("e => e.inert"))
        self.page.locator('#sharedFilesModal [data-close="shared-files"]').click()

        open_pending()
        held.pop().fulfill(
            status=503, json={"ok": False, "error": {"message": "Synthetic failure"}}
        )
        self.page.wait_for_function(
            "document.querySelector('#sharedFilesMeta').textContent.includes('Synthetic failure')"
        )
        self.assertFalse(self.page.locator("#sharedFilesDesktop").is_visible())
        self.page.locator('#sharedFilesModal [data-close="shared-files"]').click()

        self.page.evaluate("state.sharedFilesMutationRequest = {synthetic: true}")
        self.page.locator("#sharedFilesButton").click()
        self.page.locator("#sharedFilesModal.is-open").wait_for(timeout=1000)
        self.assertIn("ДОЖДИТЕСЬ", self.page.locator("#sharedFilesMeta").inner_text())
        self.assertTrue(self.page.evaluate("state.sharedFilesMutationRequest.synthetic"))
        self.assertEqual(held, [])
        self.page.locator('#sharedFilesModal [data-close="shared-files"]').click()
        self.page.evaluate("state.sharedFilesMutationRequest = null")

        open_pending()
        fulfill([{"id": "fresh", "original_name": "FRESH SYNTHETIC FILE"}])
        self.page.get_by_text("FRESH SYNTHETIC FILE").wait_for()
        self.assertFalse(self.page.locator(".shared-files-toolbar").evaluate("e => e.inert"))
        self.assertFalse(self.page.locator("#sharedFilesDesktop").evaluate("e => e.inert"))
        self.assertFalse(any("shared_file" in url for url in self.writes), self.writes)
