from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

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
