"""Check the performance timer against a delayed synthetic card response."""

from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

from scripts.perf_workflows import wait_for_card_ready


@unittest.skipIf(async_playwright is None, "Playwright is not installed")
class PerfCardReadyBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_loaded_card_with_the_requested_id(self) -> None:
        async with async_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).is_file():
                if os.environ.get("AUTOSTOP_REQUIRE_UI_BROWSER") == "1":
                    self.fail("Chromium is required for UI regression tests")
                self.skipTest("Playwright Chromium is not installed")
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = await browser.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                requested = asyncio.Event()
                release = asyncio.Event()

                async def respond(route):
                    requested.set()
                    await release.wait()
                    await route.fulfill(
                        json={"id": "card-1"},
                        headers={"access-control-allow-origin": "*"},
                    )

                await page.route("**/*", respond)
                await page.set_content("""<button id="open">Open</button>
                    <div id="cardModal"><div id="cardDescriptionEditor"></div></div>
                    <script>
                      const state = {activeCardIsFull: true, editingId: 'old-card'};
                      document.querySelector('#open').onclick = async () => {
                        const modal = document.querySelector('#cardModal');
                        modal.classList.add('is-open');
                        modal.dataset.cardLoading = 'true';
                        const result = await (await fetch('https://synthetic.invalid/card')).json();
                        state.editingId = result.id;
                        state.activeCardIsFull = true;
                        delete modal.dataset.cardLoading;
                      };
                    </script>""")
                await page.click("#open")
                await asyncio.wait_for(requested.wait(), timeout=3)
                ready = asyncio.create_task(wait_for_card_ready(page, "card-1"))
                try:
                    await page.wait_for_timeout(100)
                    self.assertFalse(ready.done(), "Loading shell must not count as ready")
                    await page.evaluate(
                        "delete document.querySelector('#cardModal').dataset.cardLoading"
                    )
                    await page.wait_for_timeout(100)
                    self.assertFalse(ready.done(), "A different card must not count as ready")
                    await page.evaluate(
                        "document.querySelector('#cardModal').dataset.cardLoading = 'true'"
                    )
                    release.set()
                    await asyncio.wait_for(ready, timeout=3)
                    self.assertEqual(errors, [])
                finally:
                    release.set()
                    if not ready.done():
                        ready.cancel()
                        await asyncio.gather(ready, return_exceptions=True)
            finally:
                await browser.close()
