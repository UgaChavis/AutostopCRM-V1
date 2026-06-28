from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WATCHDOG_PATH = PROJECT_ROOT / "scripts" / "production_watchdog.py"


def load_watchdog_module():
    spec = importlib.util.spec_from_file_location("production_watchdog", WATCHDOG_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("production_watchdog.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeHttpProbe:
    def __init__(self, module, results_by_url):
        self._module = module
        self._results_by_url = {url: list(results) for url, results in results_by_url.items()}
        self.urls: list[str] = []

    def __call__(self, url, *, timeout, expect_json_ok=False, acceptable_statuses=None):
        self.urls.append(url)
        results = self._results_by_url.get(url)
        if not results:
            return self._module.EndpointResult(ok=True, status=200)
        result = results.pop(0)
        if isinstance(result, bool):
            return self._module.EndpointResult(ok=result, status=200 if result else None)
        return result


class FakeCommandRunner:
    def __init__(self, module):
        self._module = module
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command, *, timeout):
        self.commands.append(tuple(command))
        if command[:3] == ["docker", "compose", "ps"]:
            return self._module.CommandResult(0, stdout="autostopcrm\n")
        if command[:2] == ["docker", "inspect"]:
            return self._module.CommandResult(0, stdout="healthy\n")
        return self._module.CommandResult(0, stdout="")


class ProductionWatchdogTests(unittest.TestCase):
    def test_run_command_detaches_child_stdin(self) -> None:
        module = load_watchdog_module()
        completed = module.subprocess.CompletedProcess(["docker", "ps"], 0, "ok", "")

        with patch.object(module.subprocess, "run", return_value=completed) as run:
            result = module.run_command(["docker", "ps"], timeout=5)

        self.assertEqual(result.code, 0)
        self.assertEqual(result.stdout, "ok")
        self.assertIs(run.call_args.kwargs["stdin"], module.subprocess.DEVNULL)

    def test_config_from_env_rejects_invalid_numeric_values(self) -> None:
        module = load_watchdog_module()

        with patch.dict(
            os.environ,
            {
                "AUTOSTOP_WATCHDOG_TIMEOUT": "inf",
                "AUTOSTOP_WATCHDOG_COMMAND_TIMEOUT": "1e308",
                "AUTOSTOP_WATCHDOG_RECOVERY_DELAY": "bad",
            },
            clear=False,
        ):
            config = module.config_from_env(argparse.Namespace(root_dir=""))

        self.assertEqual(config.request_timeout_seconds, 5.0)
        self.assertEqual(config.command_timeout_seconds, 3600)
        self.assertEqual(config.post_recovery_delay_seconds, 8.0)

        with patch.dict(
            os.environ,
            {
                "AUTOSTOP_WATCHDOG_COMMAND_TIMEOUT": "1.5",
            },
            clear=False,
        ):
            fractional = module.config_from_env(argparse.Namespace(root_dir=""))

        self.assertEqual(fractional.command_timeout_seconds, 30)

        with patch.dict(
            os.environ,
            {
                "AUTOSTOP_WATCHDOG_TIMEOUT": "1e308",
                "AUTOSTOP_WATCHDOG_RECOVERY_DELAY": "1e308",
            },
            clear=False,
        ):
            bounded = module.config_from_env(argparse.Namespace(root_dir=""))

        self.assertEqual(bounded.request_timeout_seconds, 300.0)
        self.assertEqual(bounded.post_recovery_delay_seconds, 3600.0)

    def test_config_from_env_clamps_too_low_numeric_values(self) -> None:
        module = load_watchdog_module()

        with patch.dict(
            os.environ,
            {
                "AUTOSTOP_WATCHDOG_TIMEOUT": "0",
                "AUTOSTOP_WATCHDOG_COMMAND_TIMEOUT": "0",
                "AUTOSTOP_WATCHDOG_RECOVERY_DELAY": "-10",
            },
            clear=False,
        ):
            config = module.config_from_env(argparse.Namespace(root_dir=""))

        self.assertEqual(config.request_timeout_seconds, 0.1)
        self.assertEqual(config.command_timeout_seconds, 1)
        self.assertEqual(config.post_recovery_delay_seconds, 0.0)

    def test_endpoint_result_rejects_nonstandard_json_constants(self) -> None:
        module = load_watchdog_module()

        result = module._endpoint_result_from_response(
            200,
            b'{"ok": true, "latency_ms": NaN}',
            statuses={200},
            expect_json_ok=True,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.status, 200)
        self.assertIn("Unsupported JSON constant: NaN", result.error)

    def test_endpoint_result_rejects_deeply_nested_json(self) -> None:
        module = load_watchdog_module()
        deep_json = ("[" * 5000 + "0" + "]" * 5000).encode("utf-8")

        result = module._endpoint_result_from_response(
            200,
            deep_json,
            statuses={200},
            expect_json_ok=True,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.status, 200)
        self.assertIn("health response JSON is too deeply nested", result.error)

    def test_endpoint_result_rejects_non_object_json(self) -> None:
        module = load_watchdog_module()

        result = module._endpoint_result_from_response(
            200,
            b"[]",
            statuses={200},
            expect_json_ok=True,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.status, 200)
        self.assertIn("health response must be a JSON object", result.error)

    def test_check_endpoint_rejects_oversized_json_response(self) -> None:
        module = load_watchdog_module()

        class HugeResponse:
            status = 200

            def __init__(self) -> None:
                self.read_sizes: list[int] = []

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                _ = (exc_type, exc, tb)
                return False

            def read(self, size: int = -1) -> bytes:
                self.read_sizes.append(size)
                return b"x" * max(0, size)

        response = HugeResponse()

        with patch.object(module, "_urlopen_no_redirect", return_value=response):
            result = module.check_endpoint(
                "http://127.0.0.1:8000/api/health",
                timeout=1.0,
                expect_json_ok=True,
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.status, 200)
        self.assertIn("health response exceeds", result.error)
        self.assertEqual(
            response.read_sizes,
            [module.WATCHDOG_ENDPOINT_RESPONSE_MAX_BYTES + 1],
        )

    def test_check_endpoint_reports_redirect_status_without_following_it(self) -> None:
        module = load_watchdog_module()
        redirect = module.urllib.error.HTTPError(
            url="https://crm.autostopcrm.ru",
            code=302,
            msg="Found",
            hdrs={"Location": "https://example.test/"},
            fp=None,
        )

        with patch.object(module, "_urlopen_no_redirect", side_effect=redirect):
            result = module.check_endpoint(
                "https://crm.autostopcrm.ru",
                timeout=1.0,
                expect_json_ok=False,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.status, 302)

    def test_restarts_compose_service_when_host_ui_port_is_missing(self) -> None:
        module = load_watchdog_module()
        config = module.WatchdogConfig(
            local_api_health_url="http://127.0.0.1:8000/api/health",
            local_mcp_url="http://127.0.0.1:8001/mcp",
            public_site_url="https://crm.autostopcrm.ru",
            post_recovery_delay_seconds=0,
        )
        runner = FakeCommandRunner(module)
        probe = FakeHttpProbe(
            module,
            {
                config.local_api_health_url: [False, True],
                config.local_mcp_url: [True, True],
                config.public_site_url: [True],
            },
        )

        exit_code = module.ProductionWatchdog(
            config=config,
            run_command=runner,
            check_endpoint=probe,
            sleep=lambda seconds: None,
            log=lambda message: None,
        ).run_once()

        self.assertEqual(exit_code, 0)
        self.assertIn(("docker", "compose", "restart", "autostopcrm"), runner.commands)

    def test_skips_recovery_while_deploy_lock_is_held(self) -> None:
        module = load_watchdog_module()
        config = module.WatchdogConfig(
            local_api_health_url="http://127.0.0.1:8000/api/health",
            local_mcp_url="http://127.0.0.1:8001/mcp",
            public_site_url="https://crm.autostopcrm.ru",
            post_recovery_delay_seconds=0,
        )
        runner = FakeCommandRunner(module)
        probe = FakeHttpProbe(
            module,
            {
                config.local_api_health_url: [False],
                config.local_mcp_url: [False],
                config.public_site_url: [False],
            },
        )

        exit_code = module.ProductionWatchdog(
            config=config,
            run_command=runner,
            check_endpoint=probe,
            is_deploy_in_progress=lambda: True,
            sleep=lambda seconds: None,
            log=lambda message: None,
        ).run_once()

        self.assertEqual(exit_code, 0)
        self.assertEqual(runner.commands, [])
        self.assertEqual(probe.urls, [])

    def test_reloads_nginx_when_public_site_fails_but_local_upstream_is_healthy(self) -> None:
        module = load_watchdog_module()
        config = module.WatchdogConfig(
            local_api_health_url="http://127.0.0.1:8000/api/health",
            local_mcp_url="http://127.0.0.1:8001/mcp",
            public_site_url="https://crm.autostopcrm.ru",
            post_recovery_delay_seconds=0,
        )
        runner = FakeCommandRunner(module)
        probe = FakeHttpProbe(
            module,
            {
                config.local_api_health_url: [True, True],
                config.local_mcp_url: [True, True],
                config.public_site_url: [False, True],
            },
        )

        exit_code = module.ProductionWatchdog(
            config=config,
            run_command=runner,
            check_endpoint=probe,
            sleep=lambda seconds: None,
            log=lambda message: None,
        ).run_once()

        self.assertEqual(exit_code, 0)
        self.assertIn(("nginx", "-t"), runner.commands)
        self.assertIn(("systemctl", "reload", "nginx"), runner.commands)
        self.assertNotIn(("docker", "compose", "restart", "autostopcrm"), runner.commands)


if __name__ == "__main__":
    unittest.main()
