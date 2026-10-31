from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

if __package__:
    from tests.module_loader_support import load_module_from_file
else:
    from module_loader_support import load_module_from_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WATCHDOG_PATH = PROJECT_ROOT / "scripts" / "production_watchdog.py"


def load_watchdog_module():
    return load_module_from_file("production_watchdog", WATCHDOG_PATH)


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
    def test_module_loader_restores_sys_modules_state(self) -> None:
        module_name = "production_watchdog"
        with patch.dict(sys.modules):
            sys.modules.pop(module_name, None)
            module = load_watchdog_module()

            self.assertEqual(module.__name__, module_name)
            self.assertFalse(
                module_name in sys.modules,
                "loader should not leave a temporary module entry",
            )

        sentinel = object()
        with patch.dict(sys.modules, {module_name: sentinel}):
            module = load_watchdog_module()

            self.assertEqual(module.__name__, module_name)
            self.assertIs(sys.modules[module_name], sentinel)

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


class DefaultCompatibilityTests(unittest.TestCase):
    def test_missing_container_still_runs_compose_up(self) -> None:
        module = load_watchdog_module()
        config = module.WatchdogConfig(post_recovery_delay_seconds=0)
        runner = FakeCommandRunner(module)

        def run(command, *, timeout):
            if command[:3] == ["docker", "compose", "ps"]:
                runner.commands.append(tuple(command))
                return module.CommandResult(0, stdout="")
            return runner(command, timeout=timeout)

        result = module.ProductionWatchdog(
            config=config,
            run_command=run,
            check_endpoint=FakeHttpProbe(module, {}),
            is_deploy_in_progress=lambda: False,
            sleep=lambda _: None,
            log=lambda _: None,
        ).run_once()
        self.assertEqual(result, 0)
        self.assertIn(("docker", "compose", "up", "-d", "autostopcrm"), runner.commands)

    def test_failed_reload_still_tries_restart(self) -> None:
        module = load_watchdog_module()
        config = module.WatchdogConfig(post_recovery_delay_seconds=0)
        runner = FakeCommandRunner(module)

        def run(command, *, timeout):
            if command == ["systemctl", "reload", "nginx"]:
                runner.commands.append(tuple(command))
                return module.CommandResult(1)
            return runner(command, timeout=timeout)

        result = module.ProductionWatchdog(
            config=config,
            run_command=run,
            check_endpoint=FakeHttpProbe(module, {config.public_site_url: [False, True]}),
            is_deploy_in_progress=lambda: False,
            sleep=lambda _: None,
            log=lambda _: None,
        ).run_once()
        self.assertEqual(result, 0)
        self.assertIn(("systemctl", "restart", "nginx"), runner.commands)

    def test_default_does_not_participate_in_experimental_marker(self) -> None:
        module = load_watchdog_module()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "deploy.lock"
            with module.watchdog_fence(path) as fence:
                fence.begin_effect("synthetic", "synthetic", None)
            config = module.WatchdogConfig(deploy_lock_path=path, post_recovery_delay_seconds=0)
            runner = FakeCommandRunner(module)
            result = module.ProductionWatchdog(
                config=config,
                run_command=runner,
                check_endpoint=FakeHttpProbe(module, {config.local_api_health_url: [False, True]}),
                sleep=lambda _: None,
                log=lambda _: None,
            ).run_once()
            self.assertEqual(result, 0)
            self.assertIn(("docker", "compose", "restart", "autostopcrm"), runner.commands)
            self.assertTrue(path.with_name(path.name + ".watchdog-unknown").exists())

    def test_main_selects_existing_watchdog(self) -> None:
        module = load_watchdog_module()
        with (
            patch.object(module, "ProductionWatchdog") as default,
            patch.object(module, "ExperimentalProductionWatchdog") as experimental,
            patch.object(module.os, "chdir"),
        ):
            default.return_value.run_once.return_value = 0
            self.assertEqual(module.main([]), 0)
        default.assert_called_once()
        experimental.assert_not_called()


class ExperimentalFakeCommandRunner(FakeCommandRunner):
    def __call__(self, command, *, timeout):
        self.commands.append(tuple(command))
        if command[:3] == ["docker", "compose", "ps"]:
            return self._module.CommandResult(0, stdout="autostopcrm\n")
        if command[:4] == ["docker", "inspect", "--format", "{{.Id}}"]:
            value = command[-1] if len(command[-1]) == 64 else "a" * 64
            return self._module.CommandResult(0, stdout=value)
        if command[:2] == ["docker", "inspect"]:
            return self._module.CommandResult(0, stdout="running/healthy\n")
        return self._module.CommandResult(0, stdout="")


class ExperimentalProductionWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.lock_path = Path(self._temp.name) / "deploy.lock"

    def test_missing_host_ui_does_not_restart_container_without_global_fence(self) -> None:
        module = load_watchdog_module()
        config = module.WatchdogConfig(
            deploy_lock_path=self.lock_path,
            local_api_health_url="http://127.0.0.1:8000/api/health",
            local_mcp_url="http://127.0.0.1:8001/mcp",
            public_site_url="https://crm.autostopcrm.ru",
            post_recovery_delay_seconds=0,
        )
        runner = ExperimentalFakeCommandRunner(module)
        probe = FakeHttpProbe(
            module,
            {
                config.local_api_health_url: [False, True],
                config.local_mcp_url: [True, True],
                config.public_site_url: [True],
            },
        )

        exit_code = module.ExperimentalProductionWatchdog(
            config=config,
            run_command=runner,
            check_endpoint=probe,
            sleep=lambda seconds: None,
            log=lambda message: None,
        ).run_once()

        self.assertEqual(exit_code, 1)
        self.assertNotIn(("docker", "restart", "a" * 64), runner.commands)
        self.assertFalse(
            self.lock_path.with_name(self.lock_path.name + ".watchdog-unknown").exists()
        )

    def test_skips_recovery_while_deploy_lock_is_held(self) -> None:
        module = load_watchdog_module()
        config = module.WatchdogConfig(
            deploy_lock_path=self.lock_path,
            local_api_health_url="http://127.0.0.1:8000/api/health",
            local_mcp_url="http://127.0.0.1:8001/mcp",
            public_site_url="https://crm.autostopcrm.ru",
            post_recovery_delay_seconds=0,
        )
        runner = ExperimentalFakeCommandRunner(module)
        probe = FakeHttpProbe(
            module,
            {
                config.local_api_health_url: [False],
                config.local_mcp_url: [False],
                config.public_site_url: [False],
            },
        )

        exit_code = module.ExperimentalProductionWatchdog(
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
            deploy_lock_path=self.lock_path,
            local_api_health_url="http://127.0.0.1:8000/api/health",
            local_mcp_url="http://127.0.0.1:8001/mcp",
            public_site_url="https://crm.autostopcrm.ru",
            post_recovery_delay_seconds=0,
        )
        runner = ExperimentalFakeCommandRunner(module)
        probe = FakeHttpProbe(
            module,
            {
                config.local_api_health_url: [True, True],
                config.local_mcp_url: [True, True],
                config.public_site_url: [False, True],
            },
        )

        exit_code = module.ExperimentalProductionWatchdog(
            config=config,
            run_command=runner,
            check_endpoint=probe,
            sleep=lambda seconds: None,
            log=lambda message: None,
        ).run_once()

        self.assertEqual(exit_code, 1)
        self.assertIn(("nginx", "-t"), runner.commands)
        self.assertIn(("systemctl", "reload", "nginx"), runner.commands)
        self.assertNotIn(("docker", "restart", "a" * 64), runner.commands)
        # HTTP success is not bound to the exact container ID.
        self.assertTrue(
            self.lock_path.with_name(self.lock_path.name + ".watchdog-unknown").exists()
        )

    def test_effect_and_readback_hold_lock_and_keep_marker_until_verified(self) -> None:
        module = load_watchdog_module()
        config = module.WatchdogConfig(
            deploy_lock_path=self.lock_path, post_recovery_delay_seconds=0
        )
        entered, release = threading.Event(), threading.Event()
        base = ExperimentalFakeCommandRunner(module)

        def run(command, *, timeout):
            if command == ["systemctl", "reload", "nginx"]:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("synthetic test timeout")
            return base(command, timeout=timeout)

        probe = FakeHttpProbe(module, {config.public_site_url: [False, True]})
        watchdog = module.ExperimentalProductionWatchdog(
            config=config,
            run_command=run,
            check_endpoint=probe,
            sleep=lambda _: None,
            log=lambda _: None,
        )
        outcomes = []
        worker = threading.Thread(target=lambda: outcomes.append(watchdog.run_once()))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            marker = self.lock_path.with_name(self.lock_path.name + ".watchdog-unknown")
            self.assertTrue(marker.exists())
            with self.assertRaisesRegex(module.WatchdogFenceError, "lock_busy"):
                with module.watchdog_fence(self.lock_path):
                    pass
        finally:
            release.set()
            worker.join(3)
        self.assertEqual(outcomes, [1])
        self.assertTrue(marker.exists())

    def test_failed_effect_marker_persists_and_blocks_next_cycle(self) -> None:
        module = load_watchdog_module()
        config = module.WatchdogConfig(
            deploy_lock_path=self.lock_path, post_recovery_delay_seconds=0
        )
        base = ExperimentalFakeCommandRunner(module)

        def run(command, *, timeout):
            if command == ["systemctl", "reload", "nginx"]:
                return module.CommandResult(1, stderr="synthetic unknown outcome")
            return base(command, timeout=timeout)

        probe = FakeHttpProbe(module, {config.public_site_url: [False]})
        watchdog = module.ExperimentalProductionWatchdog(
            config=config,
            run_command=run,
            check_endpoint=probe,
            sleep=lambda _: None,
            log=lambda _: None,
        )
        self.assertEqual(watchdog.run_once(), 1)
        marker = self.lock_path.with_name(self.lock_path.name + ".watchdog-unknown")
        original = marker.read_bytes()
        with module.watchdog_fence(self.lock_path) as fence:
            with self.assertRaisesRegex(
                module.WatchdogFenceError, "unknown_effect_requires_review"
            ):
                fence.assert_clear()
        before = list(base.commands)
        self.assertEqual(watchdog.run_once(), 1)
        self.assertEqual(base.commands, before)
        self.assertEqual(marker.read_bytes(), original)

    def test_interruption_during_effect_leaves_recoverable_marker(self) -> None:
        module = load_watchdog_module()
        config = module.WatchdogConfig(
            deploy_lock_path=self.lock_path, post_recovery_delay_seconds=0
        )
        base = ExperimentalFakeCommandRunner(module)

        def run(command, *, timeout):
            if command == ["systemctl", "reload", "nginx"]:
                raise KeyboardInterrupt("synthetic interrupt")
            return base(command, timeout=timeout)

        probe = FakeHttpProbe(module, {config.public_site_url: [False]})
        watchdog = module.ExperimentalProductionWatchdog(
            config=config,
            run_command=run,
            check_endpoint=probe,
            sleep=lambda _: None,
            log=lambda _: None,
        )
        with self.assertRaises(KeyboardInterrupt):
            watchdog.run_once()
        with module.watchdog_fence(self.lock_path) as fence:
            with self.assertRaisesRegex(
                module.WatchdogFenceError, "unknown_effect_requires_review"
            ):
                fence.assert_clear()

    def test_changed_container_identity_during_recovery_keeps_marker(self) -> None:
        module = load_watchdog_module()
        config = module.WatchdogConfig(
            deploy_lock_path=self.lock_path, post_recovery_delay_seconds=0
        )
        base = ExperimentalFakeCommandRunner(module)
        ids = iter(["a" * 64, "a" * 64, "b" * 64])

        def run(command, *, timeout):
            if command[:3] == ["docker", "compose", "ps"]:
                return module.CommandResult(0, stdout=next(ids))
            return base(command, timeout=timeout)

        probe = FakeHttpProbe(module, {config.public_site_url: [False, True]})
        watchdog = module.ExperimentalProductionWatchdog(
            config=config,
            run_command=run,
            check_endpoint=probe,
            sleep=lambda _: None,
            log=lambda _: None,
        )
        self.assertEqual(watchdog.run_once(), 1)
        self.assertTrue(
            self.lock_path.with_name(self.lock_path.name + ".watchdog-unknown").exists()
        )

    def test_replaced_lock_inode_before_effect_does_not_run_recovery(self) -> None:
        module = load_watchdog_module()
        config = module.WatchdogConfig(
            deploy_lock_path=self.lock_path, post_recovery_delay_seconds=0
        )
        runner = ExperimentalFakeCommandRunner(module)
        probe = FakeHttpProbe(module, {})

        def change_lock(url, **kwargs):
            if url == config.public_site_url:
                self.lock_path.rename(self.lock_path.with_name("old-lock"))
                self.lock_path.touch(mode=0o600)
                return module.EndpointResult(ok=False)
            return probe(url, **kwargs)

        watchdog = module.ExperimentalProductionWatchdog(
            config=config,
            run_command=runner,
            check_endpoint=change_lock,
            sleep=lambda _: None,
            log=lambda _: None,
        )
        self.assertEqual(watchdog.run_once(), 1)
        self.assertNotIn(("systemctl", "reload", "nginx"), runner.commands)

    def test_failed_nginx_reload_does_not_blindly_restart(self) -> None:
        module = load_watchdog_module()
        config = module.WatchdogConfig(
            deploy_lock_path=self.lock_path, post_recovery_delay_seconds=0
        )
        base = ExperimentalFakeCommandRunner(module)

        def run(command, *, timeout):
            if command == ["systemctl", "reload", "nginx"]:
                return module.CommandResult(1, stderr="synthetic unknown outcome")
            return base(command, timeout=timeout)

        probe = FakeHttpProbe(module, {config.public_site_url: [False]})
        watchdog = module.ExperimentalProductionWatchdog(
            config=config,
            run_command=run,
            check_endpoint=probe,
            sleep=lambda _: None,
            log=lambda _: None,
        )
        self.assertEqual(watchdog.run_once(), 1)
        self.assertNotIn(("systemctl", "restart", "nginx"), base.commands)
        self.assertTrue(
            self.lock_path.with_name(self.lock_path.name + ".watchdog-unknown").exists()
        )

    def test_stopped_absent_or_ambiguous_target_never_triggers_mutation(self) -> None:
        module = load_watchdog_module()
        for target in ("absent", "exited", "paused", "starting", "unhealthy", "ambiguous"):
            with self.subTest(target=target):
                path = self.lock_path.with_name("lock-" + target)
                config = module.WatchdogConfig(deploy_lock_path=path, post_recovery_delay_seconds=0)
                base = ExperimentalFakeCommandRunner(module)

                def run(command, *, timeout):
                    if command[:3] == ["docker", "compose", "ps"]:
                        self.assertIn("--all", command)
                        if target == "absent":
                            return module.CommandResult(0, stdout="")
                        if target == "ambiguous":
                            return module.CommandResult(0, stdout="a\nb\n")
                    if command[:4] == [
                        "docker",
                        "inspect",
                        "--format",
                        "{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
                    ]:
                        return module.CommandResult(
                            0,
                            stdout="running/unhealthy\n"
                            if target == "unhealthy"
                            else f"{target}/none\n",
                        )
                    return base(command, timeout=timeout)

                watchdog = module.ExperimentalProductionWatchdog(
                    config=config,
                    run_command=run,
                    check_endpoint=FakeHttpProbe(module, {}),
                    sleep=lambda _: None,
                    log=lambda _: None,
                )
                self.assertEqual(watchdog.run_once(), 1)
                self.assertFalse(
                    any(command[:2] == ("docker", "restart") for command in base.commands)
                )
                self.assertFalse(path.with_name(path.name + ".watchdog-unknown").exists())

    def test_service_rebind_during_nginx_effect_leaves_marker(self) -> None:
        module = load_watchdog_module()
        config = module.WatchdogConfig(
            deploy_lock_path=self.lock_path, post_recovery_delay_seconds=0
        )
        base = ExperimentalFakeCommandRunner(module)
        ids = iter(["a" * 64, "a" * 64, "b" * 64])

        def run(command, *, timeout):
            if command[:3] == ["docker", "compose", "ps"]:
                return module.CommandResult(0, stdout=next(ids))
            return base(command, timeout=timeout)

        probe = FakeHttpProbe(module, {config.public_site_url: [False, True]})
        watchdog = module.ExperimentalProductionWatchdog(
            config=config,
            run_command=run,
            check_endpoint=probe,
            sleep=lambda _: None,
            log=lambda _: None,
        )
        self.assertEqual(watchdog.run_once(), 1)
        self.assertIn(("systemctl", "reload", "nginx"), base.commands)
        self.assertNotIn(("docker", "restart", "a" * 64), base.commands)
        self.assertNotIn(("docker", "restart", "b" * 64), base.commands)
        self.assertTrue(
            self.lock_path.with_name(self.lock_path.name + ".watchdog-unknown").exists()
        )

    def test_http_200_does_not_clear_marker_if_container_is_unhealthy(self) -> None:
        module = load_watchdog_module()
        config = module.WatchdogConfig(
            deploy_lock_path=self.lock_path, post_recovery_delay_seconds=0
        )
        base = ExperimentalFakeCommandRunner(module)
        checks = iter(["running/healthy", "running/healthy", "running/unhealthy"])

        def run(command, *, timeout):
            if command[:4] == [
                "docker",
                "inspect",
                "--format",
                "{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
            ]:
                return module.CommandResult(0, stdout=next(checks))
            return base(command, timeout=timeout)

        probe = FakeHttpProbe(module, {config.public_site_url: [False, True]})
        watchdog = module.ExperimentalProductionWatchdog(
            config=config,
            run_command=run,
            check_endpoint=probe,
            sleep=lambda _: None,
            log=lambda _: None,
        )
        self.assertEqual(watchdog.run_once(), 1)
        self.assertTrue(
            self.lock_path.with_name(self.lock_path.name + ".watchdog-unknown").exists()
        )

    def test_stale_fence_cannot_be_reused_after_descriptors_are_reopened(self) -> None:
        module = load_watchdog_module()
        with module.watchdog_fence(self.lock_path) as stale:
            stale.assert_clear()
        with module.watchdog_fence(self.lock_path) as current:
            current.assert_clear()
            with self.assertRaisesRegex(module.WatchdogFenceError, "fence_closed"):
                stale.assert_clear()

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX fork")
    def test_child_cannot_use_fence_or_unlock_parents_lock(self) -> None:
        module = load_watchdog_module()
        context = module.watchdog_fence(self.lock_path)
        fence = context.__enter__()
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                try:
                    fence.assert_clear()
                except module.WatchdogFenceError as error:
                    result = str(error)
                else:
                    result = "unexpected_success"
                context.__exit__(None, None, None)
                os.write(write_fd, result.encode("ascii"))
                os._exit(0)
            except BaseException:
                os._exit(1)
        os.close(write_fd)
        try:
            self.assertEqual(os.read(read_fd, 128), b"watchdog_fence_wrong_process")
            _, status = os.waitpid(pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)
            with self.assertRaisesRegex(module.WatchdogFenceError, "lock_busy"):
                with module.watchdog_fence(self.lock_path):
                    pass
        finally:
            os.close(read_fd)
            context.__exit__(None, None, None)

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX fork")
    def test_process_death_after_effect_intent_leaves_marker(self) -> None:
        module = load_watchdog_module()
        pid = os.fork()
        if pid == 0:
            try:
                with module.watchdog_fence(self.lock_path) as fence:
                    fence.begin_effect("container_restart", "synthetic", "a" * 64)
                    os._exit(0)
            except BaseException:
                os._exit(1)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        with module.watchdog_fence(self.lock_path) as fence:
            with self.assertRaisesRegex(
                module.WatchdogFenceError, "unknown_effect_requires_review"
            ):
                fence.assert_clear()


if __name__ == "__main__":
    unittest.main()
