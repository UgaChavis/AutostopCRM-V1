from __future__ import annotations

import importlib.util
import json
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
        expected_activity = object()
        contexts = [
            SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
            for page in pages
        ]
        browser = SimpleNamespace(new_context=AsyncMock(side_effect=contexts))
        session = {"cookies": [], "origins": []}

        async def board(page, runtime):
            events.append(("board", page))

        async def prepare(page, runtime, panel, *, activity: object):
            self.assertIs(activity, expected_activity)
            events.append(("prepare", page))

        async def measure(page, scenario, action, responses, *, activity: object):
            self.assertIs(activity, expected_activity)
            events.append(("measure", page))
            await action(0)
            return {"duration_ms": 42}

        with (
            patch.object(self.module, "board_ready", board),
            patch.object(self.module, "prepare_panel", prepare),
            patch.object(self.module, "sample_action", measure),
            patch.object(self.module, "observe_page", return_value=expected_activity),
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
        expected_activity = object()
        context = SimpleNamespace(new_page=AsyncMock(return_value=page), close=AsyncMock())
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context))

        async def measure(page, scenario, action, responses, *, activity: object):
            self.assertIs(activity, expected_activity)
            await action(0)
            raise TimeoutError("fixture failure")

        with (
            patch.object(self.module, "board_ready", AsyncMock()) as board,
            patch.object(self.module, "prepare_panel", AsyncMock()) as prepare,
            patch.object(self.module, "sample_action", measure),
            patch.object(self.module, "observe_page", return_value=expected_activity),
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

    def test_observer_retains_only_path_and_request_lifecycle(self) -> None:
        handlers = {}
        page = SimpleNamespace(on=lambda name, callback: handlers.__setitem__(name, callback))
        responses = []
        events = dict.fromkeys(
            ("page_error_count", "console_error_count", "failed_request_count", "http_error_count"),
            0,
        )
        activity = self.module.observe_page(page, responses, events)
        request = SimpleNamespace(
            url="http://127.0.0.1/api/get_card?card_id=secret-card&token=secret-token",
            resource_type="fetch",
            failure=None,
        )
        response = SimpleNamespace(
            request=request,
            url=request.url,
            status=200,
            headers={"content-length": "17", "server-timing": "app;dur=2.5"},
        )

        activity.set_phase("preparation")
        handlers["request"](request)
        activity.set_phase("action")
        handlers["response"](response)
        handlers["requestfinished"](request)

        self.assertEqual(len(responses), 1)
        self.assertIs(activity.network_responses[0], responses[0])
        self.assertEqual(responses[0]["path"], "/api/get_card")
        self.assertEqual(responses[0]["started_phase"], "preparation")
        self.assertEqual(responses[0]["response_phase"], "action")
        self.assertEqual(responses[0]["finish_phase"], "action")
        self.assertEqual(responses[0]["bytes"], 17)
        self.assertNotIn("secret-card", repr(responses[0]))
        self.assertNotIn("secret-token", repr(responses[0]))
        self.assertFalse(activity.inflight)

    async def test_printing_preparation_waits_for_delayed_side_effects_and_idle(self) -> None:
        page = SimpleNamespace(wait_for_timeout=AsyncMock())
        activity = SimpleNamespace(wait_for_paths_idle=AsyncMock())

        await self.module.settle_printing_preparation(page, activity)

        self.assertEqual(activity.wait_for_paths_idle.await_count, 2)
        for call in activity.wait_for_paths_idle.await_args_list:
            self.assertEqual(call.args, (page, self.module.PRINTING_PREPARATION_PATHS))
        page.wait_for_timeout.assert_awaited_once_with(
            self.module.CARD_OPEN_SIDE_EFFECT_DELAY_MS + self.module.PREPARATION_QUIET_MS
        )

    async def test_sample_counts_only_requests_started_during_action(self) -> None:
        responses = []
        events = dict.fromkeys(
            ("page_error_count", "console_error_count", "failed_request_count", "http_error_count"),
            0,
        )
        activity = self.module.PageActivity(responses, events)
        page = SimpleNamespace(
            evaluate=AsyncMock(
                return_value={
                    "js_resource_count": 1,
                    "js_encoded_bytes": 11,
                    "js_decoded_bytes": 19,
                }
            )
        )

        async def measure(_page, *, scenario, iterations, responses, action):
            preparation_response = {
                "path": "/api/open_card",
                "started_phase": "preparation",
                "bytes": 101,
                "server_timing": "app;dur=100",
                "note": "cannot leak into action metrics",
            }
            responses.append(preparation_response)
            activity.network_responses.append(preparation_response)
            await action(0)

            return {
                "p50_ms": 21,
                "request_count": 2,
                "payload_bytes": 114,
                "server_timing": ["app;dur=100", "app;dur=3"],
                "resources": {"resource_count": 2, "resource_bytes": 114},
            }

        async def action(_index):
            action_response = {
                "path": "/api/render_repair_order",
                "started_phase": activity.phase,
                "bytes": 13,
                "server_timing": "app;dur=3, leak;desc=secret-header",
            }
            responses.append(action_response)
            activity.network_responses.append(action_response)

        with patch.object(self.module.perf, "measure_browser_action", side_effect=measure):
            sample = await self.module.sample_action(
                page,
                "cold_panel.printing",
                action,
                responses,
                activity=activity,
            )

        self.assertEqual(sample["request_count"], 1)
        self.assertEqual(sample["payload_bytes"], 13)
        self.assertEqual(sample["server_timing"], ["app;dur=3, leak"])
        self.assertEqual(sample["network_paths"], ["/api/render_repair_order"])
        self.assertEqual(sample["network_resource_types"], [""])
        self.assertEqual(sample["network_started_phases"], ["action"])
        self.assertEqual(sample["network_bytes"], [13])
        self.assertNotIn("cannot leak", repr(sample))
        self.assertNotIn("secret-header", repr(sample))
        encoded = self.module.perf.serialize_report({"series": [{"rows": [{"samples": [sample]}]}]})
        serialized_sample = json.loads(encoded)["series"][0]["rows"][0]["samples"][0]
        self.assertEqual(serialized_sample["network_paths"], ["/api/render_repair_order"])
        self.assertIsInstance(serialized_sample["network_response_headers_ms"], list)

    def test_summary_retains_individual_samples_for_independent_p95(self) -> None:
        samples = [{"duration_ms": n, "js_decoded_bytes": 1024} for n in range(1, 21)]
        summary = self.module.summarize("clients.query", samples)
        self.assertEqual(summary["iterations"], 20)
        self.assertEqual(summary["p95_ms"], 19.0)
        self.assertIs(summary["samples"], samples)
        self.assertEqual(summary["api_request_scope"], "requests_started_during_action")


if __name__ == "__main__":
    unittest.main()
