"""Failure and cancellation contracts for disposable browser review contexts."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.source_path_support import prepend_scripts_path

prepend_scripts_path()

import browser_smoke_review as review  # noqa: E402


class BrowserLabCleanupTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform == "win32":
            policy = asyncio.get_event_loop_policy()
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            cls.addClassCleanup(asyncio.set_event_loop_policy, policy)

    @staticmethod
    def context(*, page_error=None, close_error=None):
        return SimpleNamespace(
            new_page=AsyncMock(return_value=Mock(), side_effect=page_error),
            close=AsyncMock(side_effect=close_error),
        )

    async def test_second_context_acquisition_failure_or_cancel_closes_first(self):
        for error in (RuntimeError("synthetic acquisition"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                admin = self.context()
                browser = SimpleNamespace(new_context=AsyncMock(side_effect=[admin, error]))
                with self.assertRaises(type(error)) as caught:
                    await review.exercise_employee_permission_refresh(browser, None)
                self.assertIs(caught.exception, error)
                admin.close.assert_awaited_once_with()
                admin.new_page.assert_not_awaited()

    async def test_failed_or_cancelled_viewer_cleanup_still_closes_admin(self):
        for close_error in (RuntimeError("synthetic close"), asyncio.CancelledError()):
            with self.subTest(error=type(close_error).__name__):
                body_error = ValueError("synthetic page")
                admin = self.context(page_error=body_error)
                viewer = self.context(close_error=close_error)
                order = Mock()
                order.attach_mock(viewer.close, "viewer")
                order.attach_mock(admin.close, "admin")
                browser = SimpleNamespace(new_context=AsyncMock(side_effect=[admin, viewer]))
                with self.assertRaises(type(close_error)) as caught:
                    await review.exercise_employee_permission_refresh(browser, None)
                self.assertIs(caught.exception, close_error)
                self.assertIs(caught.exception.__context__, body_error)
                self.assertEqual([item[0] for item in order.mock_calls], ["viewer", "admin"])

    async def test_review_page_setup_failure_or_cancel_closes_context(self):
        for error in (ValueError("synthetic page"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                context = self.context(page_error=error)
                browser = SimpleNamespace(new_context=AsyncMock(return_value=context))
                with self.assertRaises(type(error)) as caught:
                    await review.run_review_scenarios(browser, None)
                self.assertIs(caught.exception, error)
                context.close.assert_awaited_once_with()
                self.assertEqual(browser.new_context.await_count, 1)

    async def test_review_event_setup_failure_closes_context(self):
        error = RuntimeError("synthetic event setup")
        context = self.context()
        context.new_page.return_value.on.side_effect = error
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context))
        with self.assertRaises(RuntimeError) as caught:
            await review.run_review_scenarios(browser, None)
        self.assertIs(caught.exception, error)
        context.close.assert_awaited_once_with()

    async def test_scenario_failure_or_cancel_closes_context_and_stops_sequence(self):
        for error in (AssertionError("synthetic assertion"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                context = self.context()
                browser = SimpleNamespace(new_context=AsyncMock(return_value=context))
                with (
                    patch.object(review, "exercise_board_drop_order", AsyncMock(side_effect=error)),
                    patch.object(
                        review, "exercise_inventory_write_integrity", AsyncMock()
                    ) as later,
                    patch.object(
                        review, "exercise_employee_permission_refresh", AsyncMock()
                    ) as last,
                    self.assertRaises(type(error)) as caught,
                ):
                    await review.run_review_scenarios(browser, None)
                self.assertIs(caught.exception, error)
                context.close.assert_awaited_once_with()
                later.assert_not_awaited()
                last.assert_not_awaited()

    async def test_success_preserves_scenarios_order_results_and_per_context_cleanup(self):
        contexts = [self.context(), self.context()]
        browser = SimpleNamespace(new_context=AsyncMock(side_effect=contexts))
        observed = []

        async def scenario(page, runtime):
            index = len(observed)
            if index:
                contexts[index - 1].close.assert_awaited_once_with()
            self.assertIs(page, contexts[index].new_page.return_value)
            self.assertIs(runtime, runtime_marker)
            observed.append(index)
            return {str(index): True}

        async def employee(given_browser, runtime):
            self.assertIs(given_browser, browser)
            self.assertIs(runtime, runtime_marker)
            for context in contexts:
                context.close.assert_awaited_once_with()
            observed.append("employee")
            return {"employee": True}

        runtime_marker = object()
        with (
            patch.object(review, "exercise_board_drop_order", scenario),
            patch.object(review, "exercise_inventory_write_integrity", scenario),
            patch.object(review, "exercise_employee_permission_refresh", employee),
        ):
            result = await review.run_review_scenarios(browser, runtime_marker)
        self.assertEqual(observed, [0, 1, "employee"])
        self.assertEqual(result, {"0": True, "1": True, "employee": True})
        self.assertEqual(browser.new_context.await_count, 2)

    async def native_browser(self):
        try:
            from playwright.async_api import Error, async_playwright
        except ImportError as error:
            if os.environ.get("AUTOSTOP_REQUIRE_UI_BROWSER") == "1":
                raise RuntimeError("Playwright is required for browser lifecycle tests") from error
            raise unittest.SkipTest("Playwright is not installed") from error
        playwright = await self.enterAsyncContext(async_playwright())
        try:
            browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        except Error as error:
            if (
                "Executable doesn't exist" in str(error)
                and os.environ.get("AUTOSTOP_REQUIRE_UI_BROWSER") != "1"
            ):
                raise unittest.SkipTest("Playwright Chromium is not installed") from error
            raise
        self.addAsyncCleanup(browser.close)
        return browser

    async def test_native_page_creation_failure_closes_only_owned_context(self):
        browser = await self.native_browser()
        sentinel = await browser.new_context()
        self.addAsyncCleanup(sentinel.close)
        owned = await browser.new_context()
        self.addAsyncCleanup(owned.close)
        factory = SimpleNamespace(new_context=AsyncMock(return_value=owned))
        error = RuntimeError("synthetic new_page failure")
        with (
            patch.object(owned, "new_page", AsyncMock(side_effect=error)),
            self.assertRaises(RuntimeError) as caught,
        ):
            await review.run_review_scenarios(factory, None)
        self.assertIs(caught.exception, error)
        self.assertEqual(browser.contexts, [sentinel])
        page = await sentinel.new_page()
        await page.set_content("<h1>Unrelated context survives</h1>")
        self.assertEqual(await page.locator("h1").inner_text(), "Unrelated context survives")

    async def test_native_second_context_failure_closes_admin(self):
        browser = await self.native_browser()
        admin = await browser.new_context()
        self.addAsyncCleanup(admin.close)
        error = RuntimeError("synthetic second context failure")
        factory = SimpleNamespace(new_context=AsyncMock(side_effect=[admin, error]))
        with self.assertRaises(RuntimeError) as caught:
            await review.exercise_employee_permission_refresh(factory, None)
        self.assertIs(caught.exception, error)
        self.assertEqual(browser.contexts, [])
        self.assertTrue(browser.is_connected())

    async def test_native_cancellation_closes_context_and_keeps_driver_usable(self):
        browser = await self.native_browser()
        entered = asyncio.Event()

        async def blocked_scenario(page, runtime):
            entered.set()
            await asyncio.Event().wait()

        with patch.object(review, "exercise_board_drop_order", blocked_scenario):
            task = asyncio.create_task(review.run_review_scenarios(browser, None))
            self.addAsyncCleanup(self.cancel_task, task)
            await asyncio.wait_for(entered.wait(), timeout=5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(browser.contexts, [])
        context = await browser.new_context()
        self.addAsyncCleanup(context.close)
        page = await context.new_page()
        await page.set_content("<title>Driver still works</title>")
        self.assertEqual(await page.title(), "Driver still works")

    @staticmethod
    async def cancel_task(task):
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    unittest.main()
