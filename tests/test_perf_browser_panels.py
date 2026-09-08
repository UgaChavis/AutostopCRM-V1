from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"


def load_script():
    spec = importlib.util.spec_from_file_location(
        "perf_browser_panels_under_test", SCRIPT_DIR / "perf_browser_panels.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(SCRIPT_DIR), *sys.path]):
        spec.loader.exec_module(module)
    return module


class PerfBrowserPanelsTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_script()

    def test_source_selection_and_harness_fingerprints_are_separate(self) -> None:
        with patch.object(
            self.module.perf,
            "configure_source_root",
            return_value={"source_sha256": "baseline-source"},
        ) as configure:
            result = self.module.application_environment("baseline-worktree")
        configure.assert_called_once_with("baseline-worktree")
        self.assertEqual(result["source_sha256"], "baseline-source")
        for name in ("measurement_sha256", "fixture_sha256"):
            self.assertRegex(result[name], r"^[0-9a-f]{64}$")

    async def test_every_cold_sample_has_fresh_context_and_untimed_preparation(self) -> None:
        events = []
        pages = [object(), object()]
        contexts = [
            SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
            for page in pages
        ]
        browser = SimpleNamespace(new_context=AsyncMock(side_effect=contexts))
        session = {"cookies": [], "origins": []}

        async def board(page, runtime):
            events.append(("board", page))

        async def prepare(page, runtime, panel):
            events.append(("prepare", page))

        async def measure(page, scenario, action, responses):
            events.append(("measure", page))
            await action(0)
            return {"duration_ms": 42}

        with (
            patch.object(self.module, "board_ready", board),
            patch.object(self.module, "prepare_panel", prepare),
            patch.object(self.module, "sample_action", measure),
            patch.object(self.module, "observe_page"),
            patch.object(self.module, "open_panel", AsyncMock()) as opening,
        ):
            for _ in pages:
                result = await self.module.cold_sample(
                    browser, session, object(), "cold_panel.printing", {}
                )
                self.assertEqual(result, {"duration_ms": 42})
        self.assertEqual(
            events, [(event, page) for page in pages for event in ("board", "prepare", "measure")]
        )
        self.assertEqual(opening.await_count, 2)
        self.assertEqual(browser.new_context.await_count, 2)
        for call in browser.new_context.call_args_list:
            self.assertEqual(
                call.kwargs, {"viewport": self.module.DESKTOP, "storage_state": session}
            )
        for context in contexts:
            context.close.assert_awaited_once()

    async def test_mobile_navigation_is_timed_and_context_closes_on_failure(self) -> None:
        page = object()
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context))

        async def measure(page, scenario, action, responses):
            await action(0)
            raise TimeoutError("fixture failure")

        with (
            patch.object(self.module, "board_ready", AsyncMock()) as board,
            patch.object(self.module, "prepare_panel", AsyncMock()) as prepare,
            patch.object(self.module, "sample_action", measure),
            patch.object(self.module, "observe_page"),
            self.assertRaises(TimeoutError),
        ):
            await self.module.cold_sample(
                browser, {}, "runtime", "startup.mobile_cold_authenticated", {}
            )
        board.assert_awaited_once_with(page, "runtime", mobile=True)
        prepare.assert_not_awaited()
        context.close.assert_awaited_once()
        self.assertEqual(browser.new_context.call_args.kwargs["viewport"], self.module.MOBILE)

    def test_query_waits_for_success_of_exact_request_not_stale_response(self) -> None:
        response = SimpleNamespace(
            url="http://127.0.0.1/api/search_clients?query=Smoke&limit=200",
            status=200,
            request=SimpleNamespace(post_data_json={"query": "Smoke"}),
        )
        self.assertTrue(self.module.is_query_response(response, "Smoke"))
        self.assertFalse(self.module.is_query_response(response, "Клиент"))
        response.status = 500
        self.assertFalse(self.module.is_query_response(response, "Smoke"))
        response.status = 200
        response.url = "http://127.0.0.1/api/list_clients"
        self.assertFalse(self.module.is_query_response(response, "Smoke"))

    async def test_failed_measurement_does_not_expose_browser_exception(self) -> None:
        with patch.object(
            self.module.perf,
            "measure_browser_action",
            AsyncMock(
                return_value={
                    "failed": True,
                    "error_type": "TimeoutError",
                    "error": "private-token-in-URL",
                }
            ),
        ):
            with self.assertRaises(self.module.MeasurementFailure) as caught:
                await self.module.sample_action(object(), "cold_panel.printing", AsyncMock(), [])
        self.assertEqual(caught.exception.scenario, "cold_panel.printing")
        self.assertNotIn("private-token", str(caught.exception))

    def test_summary_retains_individual_samples_for_independent_p95(self) -> None:
        samples = [{"duration_ms": n, "js_decoded_bytes": 1024} for n in range(1, 21)]
        summary = self.module.summarize("clients.query", samples)
        self.assertEqual(summary["iterations"], 20)
        self.assertEqual(summary["p95_ms"], 19.0)
        self.assertIs(summary["samples"], samples)
        self.assertEqual(summary["api_request_scope"], "action_page_only")


if __name__ == "__main__":
    unittest.main()
