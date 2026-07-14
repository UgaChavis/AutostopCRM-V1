from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "perf_mcp.py"


def load_perf_mcp_module():
    spec = importlib.util.spec_from_file_location("perf_mcp", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("perf_mcp.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PerfMcpTests(unittest.TestCase):
    def test_run_reads_bearer_from_environment_without_returning_it(self) -> None:
        module = load_perf_mcp_module()
        secret = "release-smoke-secret"
        captured_headers: dict[str, str] = {}

        async def fake_run(mcp_url, headers, args, local_runtime):
            _ = (mcp_url, args, local_runtime)
            captured_headers.update(headers)
            return {"rows": []}

        args = SimpleNamespace(
            mcp_url="https://crm.autostopcrm.ru/mcp",
            local_temp_server=False,
            bearer_token="",
            token_env="TEST_MCP_TOKEN",
        )
        with (
            patch.dict(module.os.environ, {"TEST_MCP_TOKEN": secret}),
            patch.object(module, "_run_mcp_perf_payload", side_effect=fake_run),
        ):
            result = asyncio.run(module.run_mcp_perf(args))

        self.assertEqual(captured_headers, {"Authorization": f"Bearer {secret}"})
        self.assertNotIn(secret, module._json_dumps(result))

    def test_payload_size_sanitizes_nonfinite_values(self) -> None:
        module = load_perf_mcp_module()

        size = module.payload_size({"value": float("nan")})
        encoded = module._json_dumps({"value": float("inf")})

        self.assertGreater(size, 0)
        self.assertNotIn("NaN", encoded)
        self.assertNotIn("Infinity", encoded)
        self.assertEqual(json.loads(encoded), {"value": None})

    def test_json_dumps_handles_self_referential_payload(self) -> None:
        module = load_perf_mcp_module()
        payload: dict[str, object] = {"ok": True}
        payload["self"] = payload

        encoded = module._json_dumps(payload)
        decoded = json.loads(encoded)
        node = decoded
        for _ in range(8):
            node = node["self"]

        self.assertIsInstance(node, str)

    def test_summarize_tolerates_invalid_numeric_sample_values(self) -> None:
        module = load_perf_mcp_module()

        summary = module.summarize(
            [
                {"duration_ms": "bad", "payload_bytes": True},
                {"duration_ms": "125.5", "payload_bytes": "2048"},
                {"duration_ms": "0", "payload_bytes": 1e308},
            ],
            scenario="demo",
        )

        self.assertEqual(summary["avg_ms"], 41.8)
        self.assertEqual(summary["min_ms"], 0.0)
        self.assertEqual(summary["max_ms"], 125.5)
        self.assertEqual(summary["payload_bytes"], 333_334_016)

    def test_cli_numeric_bounds_reject_huge_values(self) -> None:
        module = load_perf_mcp_module()

        self.assertEqual(module._bounded_iterations(1e308), 100)
        self.assertEqual(module._bounded_iterations("bad"), 3)
        self.assertEqual(module._bounded_port(1e308, default=42731), 65535)
        self.assertEqual(module._bounded_port(0, default=42731), 42731)

    def test_main_reports_connection_failure_without_traceback(self) -> None:
        module = load_perf_mcp_module()

        async def failing_run(_args):
            raise RuntimeError("connect failed")

        stdout = io.StringIO()
        with (
            patch.object(module, "run_mcp_perf", side_effect=failing_run),
            patch.object(sys, "argv", ["perf_mcp.py", "--mcp-url", "https://example.invalid/mcp"]),
            redirect_stdout(stdout),
        ):
            exit_code = module.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["mcp_url"], "https://example.invalid/mcp")
        self.assertEqual(payload["error"], "connect failed")

    def test_main_sanitizes_success_report_nonfinite_values(self) -> None:
        module = load_perf_mcp_module()

        async def fake_run(_args):
            return {"rows": [{"scenario": "demo", "avg_ms": float("nan")}]}

        stdout = io.StringIO()
        with (
            patch.object(module, "run_mcp_perf", side_effect=fake_run),
            patch.object(sys, "argv", ["perf_mcp.py"]),
            redirect_stdout(stdout),
        ):
            exit_code = module.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["rows"][0]["avg_ms"], None)


if __name__ == "__main__":
    unittest.main()
