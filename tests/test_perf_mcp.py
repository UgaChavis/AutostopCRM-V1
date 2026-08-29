from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import socket
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def gateway_result(*, data=None, ok: bool = True, is_error: bool = False):
    return SimpleNamespace(
        structuredContent={
            "ok": ok,
            "format": "agent_envelope_v2",
            "run_id": None,
            "status": "completed" if ok else "failed",
            "summary": {},
            "data": data,
            "changes": [],
            "verification": {},
            "warnings": [],
            "next_actions": [],
            "page": {},
            "meta": {"response_mode": "agent_compact"},
        },
        isError=is_error,
    )


class RecordingGatewaySession:
    def __init__(self, tool_names, *, card_id: str = "remote-card-id") -> None:
        self.tool_names = list(tool_names)
        self.card_id = card_id
        self.list_calls = 0
        self.calls: list[tuple[str, dict]] = []

    async def list_tools(self):
        self.list_calls += 1
        return SimpleNamespace(
            tools=[
                SimpleNamespace(name=name, description="", inputSchema={})
                for name in self.tool_names
            ]
        )

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, dict(arguments)))
        data = {"cards": [{"id": self.card_id}]} if tool_name == "agent_board_digest" else {}
        return gateway_result(data=data)


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
            token_env="TEST_MCP_TOKEN",
        )
        with (
            patch.dict(module.os.environ, {"TEST_MCP_TOKEN": secret}),
            patch(
                "minimal_kanban.mcp.manager_registration.preflight_autostop_manager_registrar"
            ) as preflight,
            patch.object(module, "_run_mcp_perf_payload", side_effect=fake_run),
        ):
            result = asyncio.run(module.run_mcp_perf(args))

        self.assertEqual(captured_headers, {"Authorization": f"Bearer {secret}"})
        self.assertNotIn(secret, module._json_dumps(result))
        preflight.assert_not_called()

    def test_remote_http_is_rejected_before_a_bearer_can_be_attached(self) -> None:
        module = load_perf_mcp_module()
        secret = "must-not-cross-plaintext-http"
        args = SimpleNamespace(
            mcp_url="http://198.51.100.7/mcp",
            local_temp_server=False,
            token_env="TEST_MCP_TOKEN",
        )
        run_payload = Mock()

        with (
            patch.dict(module.os.environ, {"TEST_MCP_TOKEN": secret}),
            patch.object(module, "_run_mcp_perf_payload", run_payload),
            self.assertRaisesRegex(ValueError, "HTTPS"),
        ):
            asyncio.run(module.run_mcp_perf(args))

        run_payload.assert_not_called()
        self.assertEqual(
            "http://127.0.0.1:42831/mcp",
            module._validated_mcp_url("http://127.0.0.1:42831/mcp"),
        )

    def test_benchmark_http_client_ignores_ambient_proxy_configuration(self) -> None:
        module = load_perf_mcp_module()
        client_factory = Mock(side_effect=RuntimeError("stop after client construction"))

        with (
            patch.dict(
                module.os.environ,
                {"HTTP_PROXY": "http://198.51.100.9:3128", "NO_PROXY": ""},
            ),
            patch.object(module.httpx, "AsyncClient", client_factory),
            self.assertRaisesRegex(RuntimeError, "stop after client construction"),
        ):
            asyncio.run(
                module._run_mcp_perf_payload(
                    "http://127.0.0.1:42831/mcp",
                    {"Authorization": "Bearer SENTINEL"},
                    SimpleNamespace(iterations=1),
                    None,
                )
            )

        self.assertFalse(client_factory.call_args.kwargs["trust_env"])

    def test_local_mode_ignores_remote_token_environment(self) -> None:
        module = load_perf_mcp_module()
        secret = "must-not-reach-local-mcp"
        captured_headers: dict[str, str] = {}
        local_runtime = SimpleNamespace(mcp_url="http://127.0.0.1:42831/mcp", close=Mock())

        async def fake_run(mcp_url, headers, args, runtime):
            _ = (mcp_url, args)
            self.assertIs(runtime, local_runtime)
            captured_headers.update(headers)
            return {"rows": []}

        args = SimpleNamespace(
            mcp_url="https://crm.autostopcrm.ru/mcp",
            local_temp_server=True,
            token_env="TEST_MCP_TOKEN",
        )
        with (
            patch.dict(module.os.environ, {"TEST_MCP_TOKEN": secret}),
            patch.object(module, "start_local_mcp_runtime", return_value=local_runtime),
            patch.object(module, "_run_mcp_perf_payload", side_effect=fake_run),
        ):
            result = asyncio.run(module.run_mcp_perf(args))

        self.assertEqual({}, captured_headers)
        self.assertNotIn(secret, module._json_dumps(result))
        local_runtime.close.assert_called_once_with()

    def test_local_runtime_closes_after_surface_failure(self) -> None:
        module = load_perf_mcp_module()
        args = SimpleNamespace(
            mcp_url="https://crm.autostopcrm.ru/mcp",
            local_temp_server=True,
            token_env="TEST_MCP_TOKEN",
        )

        local_runtime = SimpleNamespace(
            mcp_url="http://127.0.0.1:42831/mcp",
            close=Mock(),
        )

        async def failing_run(*_args):
            raise module.GatewayV2SurfaceMismatchError(actual_tool_count=23)

        with (
            patch.object(module, "start_local_mcp_runtime", return_value=local_runtime),
            patch.object(module, "_run_mcp_perf_payload", side_effect=failing_run),
            self.assertRaises(module.GatewayV2SurfaceMismatchError),
        ):
            asyncio.run(module.run_mcp_perf(args))

        local_runtime.close.assert_called_once_with()

    def test_cancellation_closes_every_owned_local_runtime_resource(self) -> None:
        module = load_perf_mcp_module()
        marker = "AUTOSTOP_PERF_MCP_CANCEL_LEASE"
        original_environment = dict(module.os.environ)
        lease = module._EnvironmentLease.apply({marker: "isolated"})
        api_runtime = SimpleNamespace(close=Mock())
        mcp_runtime = SimpleNamespace(stop=Mock())
        runtime = module.LocalMcpRuntime(
            mcp_url="http://127.0.0.1:42831/mcp",
            card_id="temporary-card-id",
            api_runtime=api_runtime,
            mcp_runtime=mcp_runtime,
            environment_lease=lease,
        )
        module._ACTIVE_LOCAL_RUNTIMES[id(runtime)] = runtime
        args = SimpleNamespace(
            mcp_url="https://crm.autostopcrm.ru/mcp",
            local_temp_server=True,
            token_env="TEST_MCP_TOKEN",
        )

        async def cancelled_run(*_args):
            raise asyncio.CancelledError()

        with (
            patch.object(module, "start_local_mcp_runtime", return_value=runtime),
            patch.object(module, "_run_mcp_perf_payload", side_effect=cancelled_run),
            self.assertRaises(asyncio.CancelledError),
        ):
            asyncio.run(module.run_mcp_perf(args))

        mcp_runtime.stop.assert_called_once_with()
        api_runtime.close.assert_called_once_with()
        self.assertEqual({}, module._ACTIVE_LOCAL_RUNTIMES)
        self.assertEqual([], module._RETAINED_FAILED_LOCAL_RUNTIMES)
        self.assertIsNone(module._ACTIVE_ENVIRONMENT_LEASE)
        self.assertEqual(original_environment, dict(module.os.environ))

    def test_removed_sensitive_cli_flags_are_rejected(self) -> None:
        module = load_perf_mcp_module()
        parser = module._build_parser()
        sentinel = "SENTINEL-DO-NOT-LOG"

        for arguments in (
            ["--allow-live-writes"],
            ["--bearer-token", sentinel],
            ["--card-id", sentinel],
            ["--bearer-tok", sentinel],
            ["--card-i", sentinel],
        ):
            with self.subTest(arguments=arguments):
                stderr = io.StringIO()
                with redirect_stderr(stderr), self.assertRaises(SystemExit):
                    parser.parse_args(arguments)

                self.assertNotIn(sentinel, stderr.getvalue())
                self.assertIn("supported", stderr.getvalue().lower())

    def test_environment_lease_is_process_exclusive(self) -> None:
        module = load_perf_mcp_module()
        marker = "AUTOSTOP_PERF_MCP_EXCLUSIVE_LEASE"
        original = module.os.environ.get(marker)
        module.os.environ[marker] = "production"
        first = module._EnvironmentLease.apply({marker: "first-local"})

        try:
            with self.assertRaisesRegex(RuntimeError, "already active"):
                module._EnvironmentLease.apply({marker: "second-local"})

            self.assertEqual("first-local", module.os.environ[marker])
            self.assertIs(first, module._ACTIVE_ENVIRONMENT_LEASE)
        finally:
            first.restore()
            if original is None:
                module.os.environ.pop(marker, None)
            else:
                module.os.environ[marker] = original

    def test_environment_lease_rejects_a_parallel_second_owner(self) -> None:
        module = load_perf_mcp_module()
        marker = "AUTOSTOP_PERF_MCP_PARALLEL_LEASE"
        original = module.os.environ.get(marker)
        barrier = threading.Barrier(2)

        def acquire(value: str):
            barrier.wait()
            try:
                return module._EnvironmentLease.apply({marker: value})
            except RuntimeError:
                return None

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                leases = list(executor.map(acquire, ("first-local", "second-local")))

            acquired = [lease for lease in leases if lease is not None]
            self.assertEqual(1, len(acquired))
            self.assertIs(acquired[0], module._ACTIVE_ENVIRONMENT_LEASE)
            self.assertTrue(acquired[0].active)
            acquired[0].restore()
        finally:
            active = module._ACTIVE_ENVIRONMENT_LEASE
            if active is not None and active.active:
                active.restore()
            if original is None:
                module.os.environ.pop(marker, None)
            else:
                module.os.environ[marker] = original

    def test_environment_lease_restores_only_keys_it_changed(self) -> None:
        module = load_perf_mcp_module()
        leased_key = "AUTOSTOP_PERF_MCP_LEASED_KEY"
        unrelated_key = "AUTOSTOP_PERF_MCP_UNRELATED_KEY"
        original_leased = module.os.environ.get(leased_key)
        original_unrelated = module.os.environ.get(unrelated_key)
        module.os.environ[leased_key] = "production"
        module.os.environ[unrelated_key] = "before"
        lease = module._EnvironmentLease.apply({leased_key: "temporary"})

        try:
            module.os.environ[unrelated_key] = "changed-during-runtime"
            lease.restore()

            self.assertEqual("production", module.os.environ[leased_key])
            self.assertEqual("changed-during-runtime", module.os.environ[unrelated_key])
        finally:
            if lease.active:
                lease.restore()
            for key, original in (
                (leased_key, original_leased),
                (unrelated_key, original_unrelated),
            ):
                if original is None:
                    module.os.environ.pop(key, None)
                else:
                    module.os.environ[key] = original

    def test_local_runtime_isolates_manager_and_api_credentials_until_close(self) -> None:
        module = load_perf_mcp_module()
        poison = {
            "AUTOSTOP_MANAGER_DB": "production-manager.sqlite3",
            "AUTOSTOP_MANAGER_ENV_FILE": "production-manager.env",
            "AUTOSTOP_DEPLOYMENT_ENV": "production",
            "AUTOSTOP_AGENT_GATEWAY_ENABLED": "0",
            "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "0",
            "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED": "0",
            "AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED": "1",
            "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
            "AUTOSTOP_AGENT_SERVICE_IDENTITY": "production-agent",
            "AUTOSTOP_MCP_OAUTH_ENABLED": "1",
            "AUTOSTOP_MAINTENANCE_MARKER": "production-maintenance.marker",
            "AUTOSTOP_STORE_API_URL": "http://autostop-app:8000",
            "AUTOSTOP_STORE_READ_TOKEN": "read-secret",
            "AUTOSTOP_STORE_QUOTE_TOKEN": "quote-secret",
            "AUTOSTOP_STORE_MANAGE_TOKEN": "manage-secret",
            "AUTOSTOP_STORE_OWNER_TOKEN": "owner-secret",
            "MINIMAL_KANBAN_MCP_BEARER_TOKEN": "mcp-secret",
        }
        original_environment = dict(module.os.environ)

        with tempfile.TemporaryDirectory() as temp_dir:
            api_runtime = SimpleNamespace(
                base_url="http://127.0.0.1:42731",
                api_token="temporary-api-token",
                card_id="temporary-card-id",
                temp_dir=SimpleNamespace(name=temp_dir),
                close=Mock(),
            )
            board_api = object()
            mcp_server = object()
            mcp_runtime = Mock()
            mcp_runtime.base_url = "http://127.0.0.1:42831/mcp"
            args = SimpleNamespace(start_port=42731, mcp_start_port=42831)

            with (
                patch.dict(module.os.environ, poison, clear=False),
                patch(
                    "minimal_kanban.mcp.manager_registration.preflight_autostop_manager_registrar"
                ),
                patch("browser_smoke_runtime.start_temp_runtime", return_value=api_runtime),
                patch(
                    "minimal_kanban.mcp.client.BoardApiClient",
                    return_value=board_api,
                ) as board_api_factory,
                patch(
                    "minimal_kanban.mcp.server.create_mcp_server",
                    return_value=mcp_server,
                ) as server_factory,
                patch(
                    "minimal_kanban.mcp.runtime.McpServerRuntime",
                    return_value=mcp_runtime,
                ),
            ):
                poisoned_snapshot = dict(module.os.environ)
                runtime = module.start_local_mcp_runtime(args)

                manager_db = Path(module.os.environ["AUTOSTOP_MANAGER_DB"])
                manager_env = Path(module.os.environ["AUTOSTOP_MANAGER_ENV_FILE"])
                self.assertEqual(Path(temp_dir), manager_db.parent)
                self.assertEqual(Path(temp_dir), manager_env.parent)
                self.assertTrue(manager_env.is_file())
                for key, value in module._LOCAL_ISOLATED_ENVIRONMENT.items():
                    self.assertEqual(value, module.os.environ[key])
                self.assertEqual(
                    Path(temp_dir) / "maintenance-disabled",
                    Path(module.os.environ["AUTOSTOP_MAINTENANCE_MARKER"]),
                )
                board_api_factory.assert_called_once_with(
                    api_runtime.base_url,
                    bearer_token=api_runtime.api_token,
                )
                self.assertEqual("", server_factory.call_args.kwargs["bearer_token"])
                self.assertTrue(module._writes_enabled(runtime, runtime.mcp_url))
                self.assertNotIn(runtime.mcp_url, repr(runtime))

                runtime.close()
                runtime.close()

                self.assertEqual(poisoned_snapshot, dict(module.os.environ))
                self.assertFalse(module._writes_enabled(runtime, runtime.mcp_url))
                mcp_runtime.stop.assert_called_once_with()
                api_runtime.close.assert_called_once_with()

        self.assertEqual(original_environment, dict(module.os.environ))

    def test_fake_manager_fixture_runs_real_local_gateway_transport(self) -> None:
        module = load_perf_mcp_module()
        from minimal_kanban.mcp.agent_gateway_support import MANAGER_GATEWAY_DEPENDENCY_NAMES
        from tests.test_agent_gateway_v2 import register_fake_store_manager_tools

        original_environment = dict(module.os.environ)
        manager_state: dict = {}

        def compatible_registrar(server, *, include_tools):
            self.assertEqual(MANAGER_GATEWAY_DEPENDENCY_NAMES, frozenset(include_tools))
            register_fake_store_manager_tools(server, module._logger(), manager_state)

        start_port = free_loopback_port()
        mcp_start_port = free_loopback_port()
        while mcp_start_port == start_port:
            mcp_start_port = free_loopback_port()
        args = SimpleNamespace(
            mcp_url="https://crm.autostopcrm.ru/mcp",
            iterations=1,
            token_env="MINIMAL_KANBAN_MCP_BEARER_TOKEN",
            local_temp_server=True,
            start_port=start_port,
            mcp_start_port=mcp_start_port,
        )

        with patch(
            "minimal_kanban.mcp.manager_registration._resolve_autostop_manager_registrar",
            return_value=compatible_registrar,
        ):
            result = asyncio.run(module.run_mcp_perf(args))

        self.assertTrue(result["ok"])
        self.assertEqual(len(module.PERMANENT_AGENT_GATEWAY_TOOL_NAMES), result["tool_count"])
        self.assertEqual("passed", result["threshold_status"])
        self.assertEqual([], result["violations"])
        self.assertEqual("local_temp", result["target_kind"])
        self.assertEqual("<local-temp>", result["mcp_url"])
        self.assertEqual(
            [
                "mcp.tools_list",
                "mcp.agent_bootstrap",
                "mcp.agent_board_digest",
                "mcp.agent_entity_context",
                "mcp.agent_board_workflow_dry_run",
            ],
            [row["scenario"] for row in result["rows"]],
        )
        encoded = module._json_dumps(result)
        self.assertNotIn("browser-smoke-local-token", encoded)
        self.assertNotIn("card_id", encoded)
        self.assertEqual({}, module._ACTIVE_LOCAL_RUNTIMES)
        self.assertEqual([], module._RETAINED_FAILED_LOCAL_RUNTIMES)
        self.assertEqual(original_environment, dict(module.os.environ))

    def test_real_transport_unwraps_gateway_surface_preflight_error(self) -> None:
        module = load_perf_mcp_module()
        from minimal_kanban.mcp.agent_gateway_support import MANAGER_GATEWAY_DEPENDENCY_NAMES
        from tests.test_agent_gateway_v2 import register_fake_store_manager_tools

        original_environment = dict(module.os.environ)

        def fake_manager_registrar(server, *, include_tools):
            self.assertEqual(MANAGER_GATEWAY_DEPENDENCY_NAMES, frozenset(include_tools))
            register_fake_store_manager_tools(server, module._logger(), {})

        expected_surface = frozenset(module.PERMANENT_AGENT_GATEWAY_TOOL_NAMES)
        intentionally_incomplete_surface = frozenset(sorted(expected_surface)[:-1])
        start_port = free_loopback_port()
        mcp_start_port = free_loopback_port()
        while mcp_start_port == start_port:
            mcp_start_port = free_loopback_port()
        args = SimpleNamespace(
            mcp_url="https://crm.autostopcrm.ru/mcp",
            iterations=1,
            token_env="MINIMAL_KANBAN_MCP_BEARER_TOKEN",
            local_temp_server=True,
            start_port=start_port,
            mcp_start_port=mcp_start_port,
        )

        with (
            patch(
                "minimal_kanban.mcp.manager_registration._resolve_autostop_manager_registrar",
                return_value=fake_manager_registrar,
            ),
            patch.object(
                module,
                "PERMANENT_AGENT_GATEWAY_TOOL_NAMES",
                intentionally_incomplete_surface,
            ),
            self.assertRaises(module.GatewayV2SurfaceMismatchError) as raised,
        ):
            asyncio.run(module.run_mcp_perf(args))

        self.assertEqual(len(expected_surface), raised.exception.actual_tool_count)
        self.assertEqual({}, module._ACTIVE_LOCAL_RUNTIMES)
        self.assertEqual([], module._RETAINED_FAILED_LOCAL_RUNTIMES)
        self.assertIsNone(module._ACTIVE_ENVIRONMENT_LEASE)
        self.assertEqual(original_environment, dict(module.os.environ))

    def test_partial_local_start_restores_environment_and_closes_api(self) -> None:
        module = load_perf_mcp_module()
        original_environment = dict(module.os.environ)

        with tempfile.TemporaryDirectory() as temp_dir:
            api_runtime = SimpleNamespace(
                base_url="http://127.0.0.1:42731",
                api_token="temporary-api-token",
                card_id="temporary-card-id",
                temp_dir=SimpleNamespace(name=temp_dir),
                close=Mock(),
            )
            args = SimpleNamespace(start_port=42731, mcp_start_port=42831)
            with (
                patch(
                    "minimal_kanban.mcp.manager_registration.preflight_autostop_manager_registrar"
                ),
                patch("browser_smoke_runtime.start_temp_runtime", return_value=api_runtime),
                patch(
                    "minimal_kanban.mcp.server.create_mcp_server",
                    side_effect=RuntimeError("registration failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "registration failed"),
            ):
                module.start_local_mcp_runtime(args)

            api_runtime.close.assert_called_once_with()
            self.assertEqual(original_environment, dict(module.os.environ))

    def test_manager_preflight_fails_before_local_api_or_environment_start(self) -> None:
        module = load_perf_mcp_module()
        original_environment = dict(module.os.environ)
        args = SimpleNamespace(start_port=42731, mcp_start_port=42831)
        preflight_error = module.AutostopManagerCompatibilityError(
            "PRIVATE-INCOMPATIBLE-MANAGER-DETAIL"
        )

        with (
            patch(
                "minimal_kanban.mcp.manager_registration.preflight_autostop_manager_registrar",
                side_effect=preflight_error,
            ) as preflight,
            patch("browser_smoke_runtime.start_temp_runtime") as start_temp_runtime,
            self.assertRaises(module.AutostopManagerCompatibilityError),
        ):
            module.start_local_mcp_runtime(args)

        preflight.assert_called_once_with(module._logger(), strict=True)
        start_temp_runtime.assert_not_called()
        self.assertEqual(original_environment, dict(module.os.environ))
        self.assertEqual({}, module._ACTIVE_LOCAL_RUNTIMES)
        self.assertEqual([], module._RETAINED_FAILED_LOCAL_RUNTIMES)

    def test_failed_mcp_runtime_start_stops_every_owned_resource(self) -> None:
        module = load_perf_mcp_module()
        original_environment = dict(module.os.environ)

        with tempfile.TemporaryDirectory() as temp_dir:
            api_runtime = SimpleNamespace(
                base_url="http://127.0.0.1:42731",
                api_token="temporary-api-token",
                card_id="temporary-card-id",
                temp_dir=SimpleNamespace(name=temp_dir),
                close=Mock(),
            )
            mcp_runtime = Mock()
            mcp_runtime.start.side_effect = RuntimeError("mcp start failed")
            args = SimpleNamespace(start_port=42731, mcp_start_port=42831)
            with (
                patch(
                    "minimal_kanban.mcp.manager_registration.preflight_autostop_manager_registrar"
                ),
                patch("browser_smoke_runtime.start_temp_runtime", return_value=api_runtime),
                patch("minimal_kanban.mcp.server.create_mcp_server", return_value=object()),
                patch(
                    "minimal_kanban.mcp.runtime.McpServerRuntime",
                    return_value=mcp_runtime,
                ),
                self.assertRaisesRegex(RuntimeError, "mcp start failed"),
            ):
                module.start_local_mcp_runtime(args)

            mcp_runtime.stop.assert_called_once_with()
            api_runtime.close.assert_called_once_with()
            self.assertEqual(original_environment, dict(module.os.environ))

    def test_local_runtime_close_keeps_isolation_after_stop_failure(self) -> None:
        module = load_perf_mcp_module()
        original_environment = dict(module.os.environ)
        marker = "AUTOSTOP_PERF_MCP_TEST_MARKER"
        environment_lease = module._EnvironmentLease.apply({marker: "temporary"})
        mcp_runtime = SimpleNamespace(stop=Mock(side_effect=[RuntimeError("stop failed"), None]))
        api_runtime = SimpleNamespace(close=Mock())
        runtime = module.LocalMcpRuntime(
            mcp_url="http://127.0.0.1:42831/mcp",
            card_id="temporary-card-id",
            api_runtime=api_runtime,
            mcp_runtime=mcp_runtime,
            environment_lease=environment_lease,
        )

        try:
            module._ACTIVE_LOCAL_RUNTIMES[id(runtime)] = runtime
            with self.assertRaisesRegex(RuntimeError, "stop failed"):
                runtime.close()

            api_runtime.close.assert_called_once_with()
            self.assertEqual("temporary", module.os.environ[marker])
            self.assertTrue(environment_lease.active)
            self.assertFalse(runtime._closed)
            self.assertIn(runtime, module._RETAINED_FAILED_LOCAL_RUNTIMES)
            self.assertFalse(module._writes_enabled(runtime, runtime.mcp_url))

            runtime.close()

            self.assertNotIn(runtime, module._RETAINED_FAILED_LOCAL_RUNTIMES)
            self.assertFalse(environment_lease.active)
            self.assertTrue(runtime._closed)
            self.assertEqual(2, api_runtime.close.call_count)
        finally:
            module._ACTIVE_LOCAL_RUNTIMES.pop(id(runtime), None)
            if environment_lease.active:
                environment_lease.restore()
            module._RETAINED_FAILED_LOCAL_RUNTIMES.clear()
        self.assertEqual(original_environment, dict(module.os.environ))

    def test_failed_start_retains_runtime_when_cleanup_is_uncertain(self) -> None:
        module = load_perf_mcp_module()
        original_environment = dict(module.os.environ)

        with tempfile.TemporaryDirectory() as temp_dir:
            api_runtime = SimpleNamespace(
                base_url="http://127.0.0.1:42731",
                api_token="temporary-api-token",
                card_id="temporary-card-id",
                temp_dir=SimpleNamespace(name=temp_dir),
                close=Mock(),
            )
            mcp_runtime = Mock()
            mcp_runtime.base_url = "http://127.0.0.1:42831/mcp"
            mcp_runtime.start.side_effect = RuntimeError("mcp start failed")
            mcp_runtime.stop.side_effect = [RuntimeError("mcp stop failed"), None]
            args = SimpleNamespace(start_port=42731, mcp_start_port=42831)

            try:
                with (
                    patch(
                        "minimal_kanban.mcp.manager_registration.preflight_autostop_manager_registrar"
                    ),
                    patch("browser_smoke_runtime.start_temp_runtime", return_value=api_runtime),
                    patch("minimal_kanban.mcp.server.create_mcp_server", return_value=object()),
                    patch(
                        "minimal_kanban.mcp.runtime.McpServerRuntime",
                        return_value=mcp_runtime,
                    ),
                    self.assertRaisesRegex(RuntimeError, "mcp start failed"),
                ):
                    module.start_local_mcp_runtime(args)

                self.assertEqual(1, len(module._RETAINED_FAILED_LOCAL_RUNTIMES))
                retained = module._RETAINED_FAILED_LOCAL_RUNTIMES[0]
                self.assertTrue(retained.environment_lease.active)

                retained.close()

                self.assertEqual([], module._RETAINED_FAILED_LOCAL_RUNTIMES)
                self.assertEqual(original_environment, dict(module.os.environ))
            finally:
                for retained in tuple(module._RETAINED_FAILED_LOCAL_RUNTIMES):
                    retained.mcp_runtime.stop.side_effect = None
                    retained.close()
                module._RETAINED_FAILED_LOCAL_RUNTIMES.clear()

    def test_gateway_surface_requires_exact_permanent_names(self) -> None:
        module = load_perf_mcp_module()
        expected = sorted(module.PERMANENT_AGENT_GATEWAY_TOOL_NAMES)

        module._require_gateway_v2_surface(expected)
        for tool_names in (
            expected[:-1],
            [*expected, "legacy_raw_tool"],
            [*expected, expected[0]],
        ):
            with self.subTest(tool_count=len(tool_names)):
                with self.assertRaises(module.GatewayV2SurfaceMismatchError):
                    module._require_gateway_v2_surface(tool_names)

    def test_tools_list_metric_counts_schema_without_reporting_it(self) -> None:
        module = load_perf_mcp_module()
        sentinel = "SENTINEL-PRIVATE-SCHEMA-DETAIL"
        tool = SimpleNamespace(
            name="agent_bootstrap",
            description=f"description-{sentinel}",
            inputSchema={"type": "object", "properties": {sentinel: {"type": "string"}}},
        )

        class SchemaSession:
            async def list_tools(self):
                return SimpleNamespace(tools=[tool])

        sample, names = asyncio.run(module._list_tools_sample(SchemaSession()))
        expected_payload = [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
            }
        ]

        self.assertEqual([tool.name], names)
        self.assertEqual(module.payload_size(expected_payload), sample["payload_bytes"])
        self.assertGreater(sample["payload_bytes"], module.payload_size(names))
        self.assertNotIn(sentinel, module._json_dumps(sample))

    def test_gateway_contract_requires_complete_mode_aware_rows(self) -> None:
        module = load_perf_mcp_module()
        iterations = 2
        missing = module._contract_violations(
            [],
            iterations=iterations,
            writes_enabled=True,
        )
        self.assertEqual(
            [f"{scenario}:missing_scenario" for scenario in module._GATEWAY_SCENARIOS],
            missing,
        )

        rows = [
            module.summarize(
                [{"duration_ms": 1, "payload_bytes": 1, "error": None}] * iterations,
                scenario,
            )
            for scenario in module._GATEWAY_SCENARIOS[:-1]
        ]
        rows.append(module.skipped_row(module._LOCAL_WORKFLOW_SCENARIO, "read only"))
        local_violations = module._contract_violations(
            rows,
            iterations=iterations,
            writes_enabled=True,
        )
        self.assertIn(
            f"{module._LOCAL_WORKFLOW_SCENARIO}:required_scenario_skipped",
            local_violations,
        )
        self.assertEqual(
            [],
            module._contract_violations(
                rows,
                iterations=iterations,
                writes_enabled=False,
            ),
        )

    def test_gateway_surface_mismatch_stops_before_first_tool_call(self) -> None:
        module = load_perf_mcp_module()
        sentinel = "SENTINEL-UNTRUSTED-TOOL"
        tool_names = sorted(module.PERMANENT_AGENT_GATEWAY_TOOL_NAMES)[:-1] + [sentinel]
        session = RecordingGatewaySession(tool_names)

        with self.assertRaises(module.GatewayV2SurfaceMismatchError) as raised:
            asyncio.run(
                module._run_gateway_session(
                    session,
                    mcp_url="https://crm.example/mcp",
                    args=SimpleNamespace(iterations=1),
                    local_runtime=None,
                )
            )

        self.assertEqual([], session.calls)
        self.assertEqual(len(tool_names), raised.exception.actual_tool_count)
        self.assertNotIn(sentinel, str(raised.exception))

    def test_unowned_local_runtime_stops_before_transport_calls(self) -> None:
        module = load_perf_mcp_module()
        session = RecordingGatewaySession(sorted(module.PERMANENT_AGENT_GATEWAY_TOOL_NAMES))
        runtime = module.LocalMcpRuntime(
            mcp_url="http://127.0.0.1:42831/mcp",
            card_id="temporary-card-id",
            api_runtime=object(),
            mcp_runtime=object(),
            environment_lease=module._EnvironmentLease(snapshot={}),
        )

        with self.assertRaises(module.LocalMcpOwnershipError):
            asyncio.run(
                module._run_gateway_session(
                    session,
                    mcp_url=runtime.mcp_url,
                    args=SimpleNamespace(iterations=1),
                    local_runtime=runtime,
                )
            )

        self.assertEqual(0, session.list_calls)
        self.assertEqual([], session.calls)

    def test_remote_gateway_sequence_is_read_only_and_report_safe(self) -> None:
        module = load_perf_mcp_module()
        card_id = "SENTINEL-REMOTE-CARD-ID"
        session = RecordingGatewaySession(
            sorted(module.PERMANENT_AGENT_GATEWAY_TOOL_NAMES),
            card_id=card_id,
        )

        result = asyncio.run(
            module._run_gateway_session(
                session,
                mcp_url="https://crm.example/mcp/private-path",
                args=SimpleNamespace(iterations=2),
                local_runtime=None,
            )
        )

        self.assertEqual(
            [
                "agent_bootstrap",
                "agent_board_digest",
                "agent_entity_context",
                "agent_bootstrap",
                "agent_board_digest",
                "agent_entity_context",
            ],
            [name for name, _arguments in session.calls],
        )
        self.assertTrue(result["ok"])
        self.assertEqual("passed", result["threshold_status"])
        self.assertEqual([], result["violations"])
        self.assertEqual("https://crm.example", result["mcp_url"])
        self.assertTrue(result["safe_mode"]["remote_read_only"])
        self.assertTrue(result["rows"][-1]["skipped"])
        self.assertEqual("mcp.agent_board_workflow_dry_run", result["rows"][-1]["scenario"])
        encoded = module._json_dumps(result)
        self.assertNotIn(card_id, encoded)
        self.assertNotIn("private-path", encoded)
        self.assertFalse(
            {
                "bootstrap_context",
                "get_cards",
                "get_card",
                "get_card_log",
                "list_columns",
                "update_card",
                "move_card",
                "call_raw_capability",
            }
            & {name for name, _arguments in session.calls}
        )

    def test_empty_digest_fails_required_context_without_raw_fallback(self) -> None:
        module = load_perf_mcp_module()
        session = RecordingGatewaySession(
            sorted(module.PERMANENT_AGENT_GATEWAY_TOOL_NAMES),
            card_id="",
        )

        result = asyncio.run(
            module._run_gateway_session(
                session,
                mcp_url="https://crm.example/mcp",
                args=SimpleNamespace(iterations=1),
                local_runtime=None,
            )
        )

        self.assertFalse(result["ok"])
        self.assertEqual("failed", result["threshold_status"])
        self.assertIn(
            "mcp.agent_entity_context:fixture_unavailable",
            result["violations"],
        )
        self.assertEqual(
            ["agent_bootstrap", "agent_board_digest"],
            [name for name, _arguments in session.calls],
        )

    def test_malformed_gateway_result_fails_session_without_payload_leak(self) -> None:
        module = load_perf_mcp_module()
        sentinel = "SENTINEL-MALFORMED-GATEWAY-PAYLOAD"

        class MalformedSession(RecordingGatewaySession):
            async def call_tool(self, tool_name, arguments):
                self.calls.append((tool_name, dict(arguments)))
                if tool_name == "agent_bootstrap":
                    return SimpleNamespace(
                        structuredContent={"format": "legacy", "secret": sentinel},
                        isError=False,
                    )
                data = (
                    {"cards": [{"id": self.card_id}]} if tool_name == "agent_board_digest" else {}
                )
                return gateway_result(data=data)

        session = MalformedSession(sorted(module.PERMANENT_AGENT_GATEWAY_TOOL_NAMES))
        result = asyncio.run(
            module._run_gateway_session(
                session,
                mcp_url="https://crm.example/mcp",
                args=SimpleNamespace(iterations=1),
                local_runtime=None,
            )
        )

        self.assertFalse(result["ok"])
        self.assertIn(
            "mcp.agent_bootstrap:invalid_gateway_envelope",
            result["violations"],
        )
        self.assertNotIn(sentinel, module._json_dumps(result))

    def test_local_gateway_sequence_owns_one_bounded_dry_run_workflow(self) -> None:
        module = load_perf_mcp_module()
        marker = "AUTOSTOP_PERF_MCP_GATEWAY_SESSION"
        lease = module._EnvironmentLease.apply({marker: "isolated"})
        runtime = module.LocalMcpRuntime(
            mcp_url="http://127.0.0.1:42831/mcp",
            card_id="SENTINEL-LOCAL-CARD-ID",
            api_runtime=object(),
            mcp_runtime=object(),
            environment_lease=lease,
        )
        session = RecordingGatewaySession(sorted(module.PERMANENT_AGENT_GATEWAY_TOOL_NAMES))

        try:
            module._ACTIVE_LOCAL_RUNTIMES[id(runtime)] = runtime
            result = asyncio.run(
                module._run_gateway_session(
                    session,
                    mcp_url=runtime.mcp_url,
                    args=SimpleNamespace(iterations=2),
                    local_runtime=runtime,
                )
            )
        finally:
            module._ACTIVE_LOCAL_RUNTIMES.pop(id(runtime), None)
            lease.restore()

        workflows = [
            arguments for name, arguments in session.calls if name == "agent_board_workflow"
        ]
        self.assertEqual(
            [
                {
                    "operation": "manager_board_scan",
                    "payload": {"limit": 5},
                    "idempotency_key": "perf-mcp-manager-board-scan-0",
                    "mode": "dry_run",
                },
                {
                    "operation": "manager_board_scan",
                    "payload": {"limit": 5},
                    "idempotency_key": "perf-mcp-manager-board-scan-1",
                    "mode": "dry_run",
                },
            ],
            workflows,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["safe_mode"]["local_temp_server"])
        self.assertFalse(any(row.get("skipped") for row in result["rows"]))
        encoded = module._json_dumps(result)
        self.assertNotIn("SENTINEL-LOCAL-CARD-ID", encoded)
        self.assertNotIn("perf-mcp-manager-board-scan", encoded)

    def test_report_summary_drops_untrusted_payload_meta(self) -> None:
        module = load_perf_mcp_module()
        sentinel = "private-card-id"

        summary = module.summarize(
            [
                {
                    "duration_ms": 1,
                    "payload_bytes": 100,
                    "meta": {"card_id": sentinel, "payload": "private"},
                }
            ],
            scenario="demo",
        )

        self.assertNotIn("meta", summary)
        self.assertNotIn(sentinel, module._json_dumps(summary))

    def test_invalid_tool_error_payload_is_rejected_without_detail_leak(self) -> None:
        module = load_perf_mcp_module()
        sentinel = "private-card-id"

        class FakeSession:
            async def call_tool(self, _tool_name, _arguments):
                return SimpleNamespace(
                    structuredContent={
                        "error": {"code": "not_found", "message": sentinel},
                        "meta": {"card_id": sentinel},
                    },
                    isError=True,
                )

        sample, envelope = asyncio.run(
            module._call_gateway_tool_sample(FakeSession(), "agent_entity_context", {})
        )

        self.assertEqual("invalid_gateway_envelope", sample["error"])
        self.assertIsNone(envelope)
        self.assertNotIn("meta", sample)
        self.assertNotIn(sentinel, module._json_dumps(sample))

    def test_gateway_envelope_parser_rejects_untrusted_fallback_shapes(self) -> None:
        module = load_perf_mcp_module()
        sentinel = "SENTINEL-MODEL-DUMP"

        malformed_results = (
            SimpleNamespace(structuredContent=None, isError=False),
            SimpleNamespace(structuredContent=[{"ok": True}], isError=False),
            SimpleNamespace(structuredContent="agent_envelope_v2", isError=False),
            SimpleNamespace(
                structuredContent={"ok": True, "format": "legacy_envelope"},
                isError=False,
            ),
            SimpleNamespace(
                structuredContent={**gateway_result().structuredContent, "ok": 1},
                isError=False,
            ),
            SimpleNamespace(
                structuredContent=None,
                isError=False,
                model_dump=lambda **_kwargs: {"ok": True, "secret": sentinel},
            ),
        )

        for result in malformed_results:
            with self.subTest(result=result):
                envelope, error = module._strict_gateway_envelope(result)
                self.assertIsNone(envelope)
                self.assertIn(
                    error,
                    {"invalid_structured_content", "invalid_gateway_envelope"},
                )
                self.assertNotIn(sentinel, module._json_dumps({"error": error}))

    def test_application_error_is_counted_without_leaking_its_payload(self) -> None:
        module = load_perf_mcp_module()
        sentinel = "private-application-error"

        class FakeSession:
            async def call_tool(self, _tool_name, _arguments):
                result = gateway_result(ok=False, is_error=True, data={"secret": sentinel})
                result.structuredContent["warnings"] = [sentinel]
                return result

        sample, envelope = asyncio.run(
            module._call_gateway_tool_sample(FakeSession(), "agent_entity_context", {})
        )
        summary = module.summarize([sample], "mcp.agent_entity_context")

        self.assertEqual("application_error", sample["error"])
        self.assertIsNotNone(envelope)
        self.assertEqual(["application_error"], summary["failed_requests"])
        self.assertNotIn(sentinel, module._json_dumps(summary))

    def test_success_envelope_with_tool_error_flag_is_protocol_failure(self) -> None:
        module = load_perf_mcp_module()

        class InconsistentSession:
            async def call_tool(self, _tool_name, _arguments):
                return gateway_result(ok=True, is_error=True)

        sample, envelope = asyncio.run(
            module._call_gateway_tool_sample(InconsistentSession(), "agent_bootstrap", {})
        )
        self.assertEqual("tool_result_inconsistent", sample["error"])
        self.assertIsNone(envelope)

    def test_writes_require_an_actual_local_runtime(self) -> None:
        module = load_perf_mcp_module()
        environment_lease = module._EnvironmentLease(snapshot={})
        runtime = module.LocalMcpRuntime(
            mcp_url="http://127.0.0.1:42831/mcp",
            card_id="temporary-card-id",
            api_runtime=object(),
            mcp_runtime=object(),
            environment_lease=environment_lease,
        )

        self.assertFalse(module._writes_enabled(None, runtime.mcp_url))
        self.assertFalse(module._writes_enabled(object(), runtime.mcp_url))
        self.assertFalse(module._writes_enabled(runtime, "https://crm.autostopcrm.ru/mcp"))
        self.assertFalse(module._writes_enabled(runtime, runtime.mcp_url))
        environment_lease.active = False
        self.assertFalse(module._writes_enabled(runtime, runtime.mcp_url))
        environment_lease.active = True
        runtime._closed = True
        self.assertFalse(module._writes_enabled(runtime, runtime.mcp_url))

    def test_writes_require_the_process_owned_environment_lease(self) -> None:
        module = load_perf_mcp_module()
        marker = "AUTOSTOP_PERF_MCP_WRITE_LEASE"
        owned_lease = module._EnvironmentLease.apply({marker: "isolated"})
        foreign_lease = module._EnvironmentLease(snapshot={})
        runtime = module.LocalMcpRuntime(
            mcp_url="http://127.0.0.1:42831/mcp",
            card_id="temporary-card-id",
            api_runtime=object(),
            mcp_runtime=object(),
            environment_lease=foreign_lease,
        )

        try:
            module._ACTIVE_LOCAL_RUNTIMES[id(runtime)] = runtime

            self.assertFalse(module._writes_enabled(runtime, runtime.mcp_url))
            runtime.environment_lease = owned_lease
            self.assertTrue(module._writes_enabled(runtime, runtime.mcp_url))
        finally:
            module._ACTIVE_LOCAL_RUNTIMES.pop(id(runtime), None)
            foreign_lease.active = False
            owned_lease.restore()

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
        self.assertEqual("remote_read_only", payload["target_kind"])
        self.assertEqual(payload["mcp_url"], "https://example.invalid")
        self.assertEqual(payload["error"], "RuntimeError")

    def test_main_local_setup_failure_does_not_report_default_remote_url(self) -> None:
        module = load_perf_mcp_module()

        async def failing_run(_args):
            raise RuntimeError("private-local-detail")

        stdout = io.StringIO()
        with (
            patch.object(module, "run_mcp_perf", side_effect=failing_run),
            patch.object(sys, "argv", ["perf_mcp.py", "--local-temp-server"]),
            redirect_stdout(stdout),
        ):
            exit_code = module.main()

        encoded = stdout.getvalue()
        payload = json.loads(encoded)
        self.assertEqual(2, exit_code)
        self.assertEqual("local_temp", payload["target_kind"])
        self.assertNotIn("mcp_url", payload)
        self.assertNotIn("crm.autostopcrm.ru", encoded)
        self.assertNotIn("private-local-detail", encoded)

    def test_main_rejects_and_redacts_credential_bearing_mcp_url(self) -> None:
        module = load_perf_mcp_module()
        secret = "private-token"
        stdout = io.StringIO()
        raw_url = f"https://user:{secret}@crm.example/mcp?access_token={secret}#private"

        with (
            patch.object(sys, "argv", ["perf_mcp.py", "--mcp-url", raw_url]),
            redirect_stdout(stdout),
        ):
            exit_code = module.main()

        encoded = stdout.getvalue()
        payload = json.loads(encoded)
        self.assertEqual(2, exit_code)
        self.assertEqual("https://crm.example", payload["mcp_url"])
        self.assertEqual("ValueError", payload["error"])
        self.assertNotIn(secret, encoded)

    def test_main_reports_fixed_local_manager_preflight_failure(self) -> None:
        module = load_perf_mcp_module()
        sentinel = "PRIVATE-INCOMPATIBLE-MANAGER-DETAIL"

        for error_type, error_code in (
            (module.AutostopManagerCompatibilityError, "autostop_manager_incompatible"),
            (module.AutostopManagerUnavailableError, "autostop_manager_unavailable"),
        ):
            with self.subTest(error_code=error_code):
                stdout = io.StringIO()

                async def failing_run(_args):
                    raise error_type(sentinel)

                with (
                    patch.object(module, "run_mcp_perf", side_effect=failing_run),
                    patch.object(sys, "argv", ["perf_mcp.py", "--local-temp-server"]),
                    redirect_stdout(stdout),
                ):
                    exit_code = module.main()

                encoded = stdout.getvalue()
                self.assertEqual(2, exit_code)
                self.assertEqual(
                    {
                        "ok": False,
                        "error": error_code,
                        "stage": "local_preflight",
                    },
                    json.loads(encoded),
                )
                self.assertNotIn(sentinel, encoded)
                self.assertNotIn("crm.autostopcrm.ru", encoded)

    def test_main_reports_fixed_gateway_surface_failure(self) -> None:
        module = load_perf_mcp_module()
        stdout = io.StringIO()

        async def failing_run(_args):
            raise module.GatewayV2SurfaceMismatchError(actual_tool_count=98)

        with (
            patch.object(module, "run_mcp_perf", side_effect=failing_run),
            patch.object(sys, "argv", ["perf_mcp.py", "--local-temp-server"]),
            redirect_stdout(stdout),
        ):
            exit_code = module.main()

        self.assertEqual(2, exit_code)
        self.assertEqual(
            {
                "ok": False,
                "error": "gateway_v2_surface_mismatch",
                "stage": "mcp_preflight",
                "expected_tool_count": len(module.PERMANENT_AGENT_GATEWAY_TOOL_NAMES),
                "actual_tool_count": 98,
            },
            json.loads(stdout.getvalue()),
        )

    def test_main_reports_fixed_local_ownership_failure(self) -> None:
        module = load_perf_mcp_module()
        stdout = io.StringIO()

        async def failing_run(_args):
            raise module.LocalMcpOwnershipError("PRIVATE-OWNERSHIP-DETAIL")

        with (
            patch.object(module, "run_mcp_perf", side_effect=failing_run),
            patch.object(sys, "argv", ["perf_mcp.py", "--local-temp-server"]),
            redirect_stdout(stdout),
        ):
            exit_code = module.main()

        self.assertEqual(2, exit_code)
        encoded = stdout.getvalue()
        self.assertEqual(
            {
                "ok": False,
                "error": "local_runtime_ownership_invalid",
                "stage": "local_preflight",
            },
            json.loads(encoded),
        )
        self.assertNotIn("PRIVATE-OWNERSHIP-DETAIL", encoded)

    def test_report_url_drops_a_potentially_sensitive_path(self) -> None:
        module = load_perf_mcp_module()
        sentinel = "SENTINEL-PATH-TOKEN"

        reported = module._report_mcp_url(f"https://crm.example/mcp/{sentinel}")

        self.assertEqual("https://crm.example", reported)
        self.assertNotIn(sentinel, reported)

    def test_main_sanitizes_success_report_nonfinite_values(self) -> None:
        module = load_perf_mcp_module()

        async def fake_run(_args):
            rows = [
                module.summarize(
                    [{"duration_ms": 1, "payload_bytes": 1, "error": None}],
                    scenario,
                )
                for scenario in module._GATEWAY_SCENARIOS[:-1]
            ]
            rows[0]["avg_ms"] = float("nan")
            rows.append(
                module.skipped_row(
                    module._LOCAL_WORKFLOW_SCENARIO,
                    "Remote MCP targets are read-only.",
                )
            )
            return {
                "ok": True,
                "target_kind": "remote_read_only",
                "rows": rows,
                "violations": [],
                "threshold_status": "passed",
            }

        stdout = io.StringIO()
        with (
            patch.object(module, "run_mcp_perf", side_effect=fake_run),
            patch.object(sys, "argv", ["perf_mcp.py", "--iterations", "1"]),
            redirect_stdout(stdout),
        ):
            exit_code = module.main()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["rows"][0]["avg_ms"], None)

    def test_main_revalidates_mode_aware_gateway_contract(self) -> None:
        module = load_perf_mcp_module()

        async def fake_run(_args):
            return {
                "ok": True,
                "target_kind": "remote_read_only",
                "rows": [
                    {
                        "scenario": "demo",
                        "iterations": 1,
                        "failed_requests": [],
                    }
                ],
                "violations": [],
                "threshold_status": "passed",
            }

        stdout = io.StringIO()
        with (
            patch.object(module, "run_mcp_perf", side_effect=fake_run),
            patch.object(sys, "argv", ["perf_mcp.py", "--iterations", "1"]),
            redirect_stdout(stdout),
        ):
            exit_code = module.main()

        self.assertEqual(1, exit_code)
        self.assertEqual("passed", json.loads(stdout.getvalue())["threshold_status"])

    def test_main_fails_closed_on_inconsistent_top_level_status(self) -> None:
        module = load_perf_mcp_module()

        for result in (
            {"ok": False, "rows": [], "violations": [], "threshold_status": "failed"},
            {"ok": True, "rows": [], "violations": [], "threshold_status": "failed"},
            {"ok": True, "rows": [], "violations": [], "threshold_status": "passed"},
        ):
            with self.subTest(result=result):
                stdout = io.StringIO()

                async def fake_run(_args, *, payload=result):
                    return payload

                with (
                    patch.object(module, "run_mcp_perf", side_effect=fake_run),
                    patch.object(sys, "argv", ["perf_mcp.py"]),
                    redirect_stdout(stdout),
                ):
                    exit_code = module.main()

                self.assertEqual(1, exit_code)

    def test_main_fails_when_gateway_contract_has_violations(self) -> None:
        module = load_perf_mcp_module()

        async def fake_run(_args):
            return {
                "ok": False,
                "rows": [],
                "violations": ["mcp.agent_entity_context:fixture_unavailable"],
                "threshold_status": "failed",
            }

        stdout = io.StringIO()
        with (
            patch.object(module, "run_mcp_perf", side_effect=fake_run),
            patch.object(sys, "argv", ["perf_mcp.py"]),
            redirect_stdout(stdout),
        ):
            exit_code = module.main()

        self.assertEqual(1, exit_code)
        self.assertEqual("failed", json.loads(stdout.getvalue())["threshold_status"])


if __name__ == "__main__":
    unittest.main()
