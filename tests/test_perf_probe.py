from __future__ import annotations

import gzip
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
from urllib.request import Request

if __package__:
    from tests.http_fixture_support import FakeReadableResponse
    from tests.module_loader_support import load_module_from_file
else:
    from http_fixture_support import FakeReadableResponse
    from module_loader_support import load_module_from_file

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "perf_probe.py"


def load_perf_probe_module() -> ModuleType:
    return load_module_from_file("perf_probe", SCRIPT_PATH)


class FakeHttpResponse(FakeReadableResponse):
    status = 200

    def __init__(self, body: bytes, *, headers: dict[str, str] | None = None) -> None:
        super().__init__(body)
        self.headers = headers or {}


class PerfProbeTests(unittest.TestCase):
    def test_loader_restores_previous_module_entry(self) -> None:
        module_name = "perf_probe"
        previous_module = sys.modules.get(module_name)
        had_previous_module = module_name in sys.modules
        sentinel = ModuleType(module_name)
        sys.modules[module_name] = sentinel

        try:
            loaded_module = load_perf_probe_module()

            self.assertIsNot(loaded_module, sentinel)
            self.assertIs(sys.modules[module_name], sentinel)
        finally:
            if had_previous_module:
                sys.modules[module_name] = previous_module
            else:
                sys.modules.pop(module_name, None)

    def test_local_temp_server_cleans_directory_when_stop_fails(self) -> None:
        module = load_perf_probe_module()
        temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(temp_dir.name)

        class FailingServer:
            def stop(self) -> None:
                raise RuntimeError("server stop failed")

        local_server = module.LocalTempServer(
            base_url="http://127.0.0.1:42751",
            server=FailingServer(),
            temp_dir=temp_dir,
        )

        with self.assertRaisesRegex(RuntimeError, "server stop failed"):
            local_server.stop()

        self.assertFalse(temp_path.exists())

    def test_local_temp_server_does_not_accumulate_null_handlers(self) -> None:
        module = load_perf_probe_module()
        logger = module.logging.Logger("perf-probe-local-temp-server-test")

        with (
            patch.object(module.logging, "getLogger", return_value=logger),
            patch("minimal_kanban.api.server.ApiServer") as api_server_class,
            patch("minimal_kanban.services.card_service.CardService"),
            patch("minimal_kanban.storage.json_store.JsonStore"),
        ):
            api_server_class.return_value.base_url = "http://127.0.0.1:42751"
            first_server = module.start_local_temp_server()
            self.addCleanup(first_server.stop)
            second_server = module.start_local_temp_server()
            self.addCleanup(second_server.stop)

            null_handlers = [
                handler
                for handler in logger.handlers
                if isinstance(handler, module.logging.NullHandler)
            ]
            self.assertEqual(len(null_handlers), 1)

    def test_thresholds_report_named_latency_and_payload_violations(self) -> None:
        module = load_perf_probe_module()
        rows = [
            {"label": "snapshot.gzip", "avg_ms": 480.0, "bytes": 45_020},
            {"label": "revision", "avg_ms": 252.5, "bytes": 637},
            {"label": "get_card", "avg_ms": 235.5, "bytes": 7433},
        ]

        violations = module.evaluate_thresholds(
            rows,
            {
                "snapshot.gzip.avg_ms": 450.0,
                "snapshot.gzip.bytes": 40_000,
                "revision.avg_ms": 300.0,
                "get_card.avg_ms": 250.0,
            },
        )

        self.assertEqual(
            violations,
            [
                {
                    "label": "snapshot.gzip",
                    "metric": "avg_ms",
                    "actual": 480.0,
                    "max": 450.0,
                },
                {
                    "label": "snapshot.gzip",
                    "metric": "bytes",
                    "actual": 45020,
                    "max": 40000.0,
                },
            ],
        )

        self.assertEqual(
            module.evaluate_thresholds(rows, {"snapshot.gzip.avg_ms": float("inf")}),
            [],
        )

    def test_iterations_are_bounded_before_probe_loop(self) -> None:
        module = load_perf_probe_module()

        self.assertEqual(module._bounded_iterations(1e308), 100)
        self.assertEqual(module._bounded_iterations(0), 1)
        self.assertEqual(module._bounded_iterations("bad"), 3)
        self.assertEqual(module._bounded_threshold(1e308), module.PERF_PROBE_MAX_THRESHOLD)
        self.assertEqual(module._bounded_threshold(-1), 0.0)
        self.assertEqual(module._bounded_threshold("bad"), 0.0)
        self.assertEqual(module._bounded_warmup_iterations(1e308), 100)
        self.assertEqual(module._bounded_warmup_iterations(-1), 0)
        self.assertEqual(module._bounded_warmup_iterations(200), 100)

    def test_measure_runs_warmups_without_including_them_in_samples(self) -> None:
        module = load_perf_probe_module()
        calls: list[int] = []

        def fake_request(*_args: object, **_kwargs: object) -> tuple[dict[str, object], object]:
            calls.append(len(calls))
            return {"ok": True}, module.ProbeResult(
                "raw", 200, float(len(calls)), 10, "", "app;dur=1"
            )

        with patch.object(module, "request_json", side_effect=fake_request):
            _, results = module.measure(
                "https://crm.autostopcrm.ru",
                "revision",
                "/api/get_board_revision",
                iterations=3,
                warmup_iterations=2,
            )

        self.assertEqual(len(calls), 5)
        self.assertEqual(len(results), 3)
        self.assertEqual([item.duration_ms for item in results], [3.0, 4.0, 5.0])

    def test_summary_keeps_all_server_timing_samples_and_percentiles(self) -> None:
        module = load_perf_probe_module()
        results = [
            module.ProbeResult(
                "revision",
                200,
                duration,
                100,
                "",
                f"app;dur={duration / 2}, lock;dur={index}",
            )
            for index, duration in enumerate((10.0, 20.0, 30.0, 40.0), start=1)
        ]

        summary = module.summarize(results)

        self.assertEqual(summary["p50_ms"], 30.0)
        self.assertEqual(summary["p95_ms"], 40.0)
        self.assertEqual(len(summary["server_timing_samples"]), 4)
        self.assertEqual(summary["server_timing_metrics"]["app"]["p95_ms"], 20.0)
        self.assertEqual(summary["server_timing_metrics"]["lock"]["samples"], 4)

    def test_nested_server_timing_threshold_uses_p95(self) -> None:
        module = load_perf_probe_module()
        row = {
            "label": "revision",
            "p95_ms": 120.0,
            "server_timing_metrics": {"app": {"p95_ms": 21.0}},
        }

        violations = module.evaluate_thresholds(
            [row],
            {"revision.server_timing_metrics.app.p95_ms": 20.0},
        )

        self.assertEqual(violations[0]["label"], "revision")
        self.assertEqual(violations[0]["metric"], "server_timing_metrics.app.p95_ms")
        self.assertEqual(violations[0]["actual"], 21.0)

    def test_request_json_rejects_nonstandard_json_constants(self) -> None:
        module = load_perf_probe_module()

        with patch.object(
            module,
            "_urlopen_no_redirect",
            return_value=FakeHttpResponse(b'{"ok": true, "data": NaN}'),
        ):
            with self.assertRaisesRegex(ValueError, "Unsupported JSON constant: NaN"):
                module.request_json("https://crm.autostopcrm.ru", "/api/health")

    def test_request_json_sends_bearer_only_when_supplied(self) -> None:
        module = load_perf_probe_module()
        requests: list[Request] = []

        def fake_open(request: Request, *, timeout: float) -> FakeHttpResponse:
            _ = timeout
            requests.append(request)
            return FakeHttpResponse(b'{"ok": true}')

        with patch.object(module, "_urlopen_no_redirect", side_effect=fake_open):
            module.request_json("http://127.0.0.1:41731", "/api/health")
            module.request_json(
                "http://127.0.0.1:41731", "/api/health", bearer_token="synthetic-token"
            )

        self.assertIsNone(requests[0].get_header("Authorization"))
        self.assertEqual(requests[1].get_header("Authorization"), "Bearer synthetic-token")

        with self.assertRaisesRegex(ValueError, "Invalid API bearer token") as error:
            module.request_json(
                "http://127.0.0.1:41731", "/api/health", bearer_token="hidden\nvalue"
            )
        self.assertNotIn("hidden", str(error.exception))

    def test_request_json_rejects_deeply_nested_response(self) -> None:
        module = load_perf_probe_module()
        deep_json = ("[" * 5000 + "0" + "]" * 5000).encode("utf-8")

        with patch.object(
            module,
            "_urlopen_no_redirect",
            return_value=FakeHttpResponse(deep_json),
        ):
            with self.assertRaisesRegex(ValueError, "API response JSON is too deeply nested"):
                module.request_json("https://crm.autostopcrm.ru", "/api/health")

    def test_request_json_rejects_oversized_response(self) -> None:
        module = load_perf_probe_module()

        with (
            patch.object(module, "PERF_PROBE_RESPONSE_MAX_BYTES", 4),
            patch.object(
                module,
                "_urlopen_no_redirect",
                return_value=FakeHttpResponse(b"12345"),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "perf probe response is too large"):
                module.request_json("https://crm.autostopcrm.ru", "/api/health")

    def test_request_json_rejects_oversized_decompressed_gzip_response(self) -> None:
        module = load_perf_probe_module()
        compressed = gzip.compress(b'{"data":"' + (b"x" * 128) + b'"}')

        with (
            patch.object(module, "PERF_PROBE_RESPONSE_MAX_BYTES", 64),
            patch.object(
                module,
                "_urlopen_no_redirect",
                return_value=FakeHttpResponse(
                    compressed,
                    headers={"Content-Encoding": "gzip"},
                ),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "gzip response is too large"):
                module.request_json("https://crm.autostopcrm.ru", "/api/health")

    def test_request_json_rejects_redirect_response(self) -> None:
        module = load_perf_probe_module()
        redirect = module.urllib.error.HTTPError(
            url="https://crm.autostopcrm.ru/api/open_card",
            code=302,
            msg="Found",
            hdrs={"Location": "https://example.test/api/open_card"},
            fp=None,
        )

        with patch.object(module, "_urlopen_no_redirect", side_effect=redirect):
            with self.assertRaisesRegex(ValueError, "API request redirected"):
                module.request_json(
                    "https://crm.autostopcrm.ru",
                    "/api/open_card",
                    method="POST",
                    payload={"card_id": "card-1"},
                )

    def test_json_dumps_sanitizes_nonfinite_values(self) -> None:
        module = load_perf_probe_module()

        encoded = module._json_dumps({"ok": True, "avg_ms": float("nan")})

        self.assertNotIn("NaN", encoded)
        self.assertEqual(json.loads(encoded), {"ok": True, "avg_ms": None})

    def test_json_dumps_handles_self_referential_payload(self) -> None:
        module = load_perf_probe_module()
        payload: dict[str, object] = {"ok": True}
        payload["self"] = payload

        encoded = module._json_dumps(payload)
        decoded = json.loads(encoded)
        node = decoded
        for _ in range(8):
            node = node["self"]

        self.assertIsInstance(node, str)

    def test_main_returns_nonzero_when_thresholds_are_exceeded(self) -> None:
        module = load_perf_probe_module()

        def fake_measure(
            base_url: str,
            label: str,
            path: str,
            *,
            iterations: int,
            warmup_iterations: int = 0,
            method: str = "GET",
            payload: dict[str, object] | None = None,
            gzip_ok: bool = False,
            bearer_token: str = "",
        ) -> tuple[dict[str, object] | None, list[object]]:
            _ = (
                base_url,
                path,
                iterations,
                warmup_iterations,
                method,
                payload,
                gzip_ok,
                bearer_token,
            )
            if label == "snapshot.identity":
                return {"data": {"cards": [{"id": "card-1"}]}}, [
                    module.ProbeResult(label, 200, 600.0, 305_000, "", "app;dur=60")
                ]
            return {}, [module.ProbeResult(label, 200, 480.0, 45_020, "gzip", "app;dur=58")]

        stdout = io.StringIO()
        with (
            patch.object(module, "measure", side_effect=fake_measure),
            patch.object(
                sys,
                "argv",
                [
                    "perf_probe.py",
                    "--base-url",
                    "https://crm.autostopcrm.ru",
                    "--iterations",
                    "1",
                    "--max-snapshot-gzip-ms",
                    "450",
                ],
            ),
            redirect_stdout(stdout),
        ):
            exit_code = module.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["threshold_status"], "failed")
        self.assertEqual(payload["violations"][0]["label"], "snapshot.gzip")

    def test_main_can_probe_temporary_local_server(self) -> None:
        module = load_perf_probe_module()

        class FakeLocalServer:
            base_url = "http://127.0.0.1:42751"

            def __init__(self) -> None:
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

        fake_server = FakeLocalServer()
        seen_base_urls: list[str] = []
        seen_tokens: list[str] = []

        def fake_measure(
            base_url: str,
            label: str,
            path: str,
            *,
            iterations: int,
            warmup_iterations: int = 0,
            method: str = "GET",
            payload: dict[str, object] | None = None,
            gzip_ok: bool = False,
            bearer_token: str = "",
        ) -> tuple[dict[str, object] | None, list[object]]:
            _ = (path, iterations, warmup_iterations, method, payload, gzip_ok, bearer_token)
            seen_base_urls.append(base_url)
            seen_tokens.append(bearer_token)
            if label == "snapshot.identity":
                return {"data": {"cards": [{"id": "card-1"}]}}, [
                    module.ProbeResult(label, 200, 10.0, 1000, "", "")
                ]
            return {}, [module.ProbeResult(label, 200, 10.0, 1000, "", "")]

        stdout = io.StringIO()
        with (
            patch.object(module, "start_local_temp_server", return_value=fake_server),
            patch.object(module, "measure", side_effect=fake_measure),
            patch.dict(os.environ, {"MINIMAL_KANBAN_API_BEARER_TOKEN": "synthetic-token"}),
            patch.object(
                sys,
                "argv",
                ["perf_probe.py", "--local-temp-server", "--iterations", "1"],
            ),
            redirect_stdout(stdout),
        ):
            exit_code = module.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertTrue(fake_server.stopped)
        self.assertTrue(payload["local_temp_server"])
        self.assertEqual(payload["base_url"], fake_server.base_url)
        self.assertEqual(set(seen_base_urls), {fake_server.base_url})
        self.assertEqual(set(seen_tokens), {""})

    def test_main_reads_remote_api_token_from_environment_without_printing_it(self) -> None:
        module = load_perf_probe_module()
        seen_tokens: list[str] = []

        def fake_measure(
            base_url: str,
            label: str,
            path: str,
            *,
            iterations: int,
            warmup_iterations: int = 0,
            method: str = "GET",
            payload: dict[str, object] | None = None,
            gzip_ok: bool = False,
            bearer_token: str = "",
        ) -> tuple[dict[str, object] | None, list[object]]:
            _ = (base_url, path, iterations, warmup_iterations, method, payload, gzip_ok)
            seen_tokens.append(bearer_token)
            return {}, [module.ProbeResult(label, 200, 1.0, 16, "", "")]

        stdout = io.StringIO()
        with (
            patch.object(module, "measure", side_effect=fake_measure),
            patch.dict(os.environ, {"PERF_TEST_API_TOKEN": "synthetic-token"}),
            patch.object(
                sys,
                "argv",
                ["perf_probe.py", "--iterations", "1", "--token-env", "PERF_TEST_API_TOKEN"],
            ),
            redirect_stdout(stdout),
        ):
            exit_code = module.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(seen_tokens, ["synthetic-token"] * 3)
        self.assertNotIn("synthetic-token", stdout.getvalue())

    def test_main_reports_probe_errors_and_stops_temporary_server(self) -> None:
        module = load_perf_probe_module()

        class FakeLocalServer:
            base_url = "http://127.0.0.1:42751"

            def __init__(self) -> None:
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

        fake_server = FakeLocalServer()
        stdout = io.StringIO()
        with (
            patch.object(module, "start_local_temp_server", return_value=fake_server),
            patch.object(
                module,
                "measure",
                side_effect=json.JSONDecodeError("bad json", "{", 0),
            ),
            patch.object(
                sys,
                "argv",
                ["perf_probe.py", "--local-temp-server", "--iterations", "1"],
            ),
            redirect_stdout(stdout),
        ):
            exit_code = module.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["base_url"], fake_server.base_url)
        self.assertIn("bad json", payload["error"])
        self.assertTrue(fake_server.stopped)


if __name__ == "__main__":
    unittest.main()
