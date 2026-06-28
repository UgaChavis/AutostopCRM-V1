from __future__ import annotations

import gzip
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "perf_probe.py"


def load_perf_probe_module():
    spec = importlib.util.spec_from_file_location("perf_probe", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("perf_probe.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeHttpResponse:
    status = 200

    def __init__(self, body: bytes, *, headers: dict[str, str] | None = None) -> None:
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._body
        return self._body[:size]


class PerfProbeTests(unittest.TestCase):
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

    def test_request_json_rejects_nonstandard_json_constants(self) -> None:
        module = load_perf_probe_module()

        with patch.object(
            module,
            "_urlopen_no_redirect",
            return_value=FakeHttpResponse(b'{"ok": true, "data": NaN}'),
        ):
            with self.assertRaisesRegex(ValueError, "Unsupported JSON constant: NaN"):
                module.request_json("https://crm.autostopcrm.ru", "/api/health")

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
            base_url, label, path, *, iterations, method="GET", payload=None, gzip_ok=False
        ):
            _ = (base_url, path, iterations, method, payload, gzip_ok)
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

        def fake_measure(
            base_url, label, path, *, iterations, method="GET", payload=None, gzip_ok=False
        ):
            _ = (path, iterations, method, payload, gzip_ok)
            seen_base_urls.append(base_url)
            if label == "snapshot.identity":
                return {"data": {"cards": [{"id": "card-1"}]}}, [
                    module.ProbeResult(label, 200, 10.0, 1000, "", "")
                ]
            return {}, [module.ProbeResult(label, 200, 10.0, 1000, "", "")]

        stdout = io.StringIO()
        with (
            patch.object(module, "start_local_temp_server", return_value=fake_server),
            patch.object(module, "measure", side_effect=fake_measure),
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
