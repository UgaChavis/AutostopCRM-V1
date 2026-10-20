from __future__ import annotations

import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.deployment_security import (
    DeploymentSecurityError,
    assert_production_environment,
    is_maintenance_mode,
    load_agent_gateway_security_policy,
    validate_production_environment,
    validate_store_integration_environment,
)

STRONG_TOKEN = "aB3_dE5-fG7.hJ9~kL2_mN4-pQ6.rS8~tU1_vW3-xY5.zA7~bC9_dF2-gH4.jK6~mP8"
STRONG_STORE_READ_TOKEN = "rB4_eF6-gH8.jK1~mN3_pQ5-rS7.tU9~vW2_xY4-zA6.bC8~dE1_fG3-hJ5.kL7~mP9"
STRONG_STORE_QUOTE_TOKEN = "qD6_gH8-jK1.lM3~pR5_sT7-uV9.wX2~yZ4_aB6-cD8.eF1~gH3_jK5-lM7.nP9~rS2"
STRONG_STORE_MANAGE_TOKEN = "mC5_fG7-hJ9.kL2~nP4_qR6-sT8.uV1~wX3_yZ5-aB7.cD9~eF2_gH4-jK6.lM8~pQ1"
STRONG_STORE_OWNER_TOKEN = "oE7_hJ9-kL2.mN4~qS6_uV8-wX1.yZ3~bD5_fG7-hJ9.kL2~nP4_qR6-sT8.uV1~wX3_yZ5"
VALID_OAUTH_STATE_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


def valid_production_env(marker: Path) -> dict[str, str]:
    return {
        "AUTOSTOP_DEPLOYMENT_ENV": "production",
        "AUTOSTOP_MCP_OAUTH_ENABLED": "1",
        "AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED": "0",
        "AUTOSTOP_MCP_OAUTH_STATE_KEY": VALID_OAUTH_STATE_KEY,
        "AUTOSTOP_AGENT_SERVICE_IDENTITY": "codex-owner-agent",
        "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
        "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
        "AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED": "1",
        "AUTOSTOP_AGENT_GATEWAY_MAIL_ENABLED": "1",
        "AUTOSTOP_AGENT_GATEWAY_DESTRUCTIVE_ENABLED": "1",
        "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
        "AUTOSTOP_STORE_API_URL": "http://autostop-app:8000",
        "AUTOSTOP_STORE_READ_TOKEN": STRONG_STORE_READ_TOKEN,
        "AUTOSTOP_STORE_QUOTE_TOKEN": STRONG_STORE_QUOTE_TOKEN,
        "AUTOSTOP_STORE_MANAGE_TOKEN": STRONG_STORE_MANAGE_TOKEN,
        "AUTOSTOP_STORE_OWNER_TOKEN": STRONG_STORE_OWNER_TOKEN,
        "AUTOSTOP_MAINTENANCE_MARKER": str(marker),
        "MINIMAL_KANBAN_MCP_BEARER_TOKEN": STRONG_TOKEN,
        "MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL": "https://crm.autostopcrm.ru",
        "MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL": "https://crm.autostopcrm.ru/mcp",
    }


class DeploymentSecurityTests(unittest.TestCase):
    def test_valid_production_policy_is_explicit_and_contains_no_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = valid_production_env(Path(temp_dir) / "maintenance")
            self.assertEqual(validate_production_environment(env), [])
            policy = load_agent_gateway_security_policy(env)

        self.assertTrue(policy.production)
        self.assertTrue(policy.finance_enabled)
        self.assertEqual(policy.service_identity, "codex-owner-agent")
        self.assertNotIn("token", policy.public_dict())

    def test_production_rejects_missing_auth_placeholders_and_implicit_switches(self) -> None:
        env = {
            "AUTOSTOP_DEPLOYMENT_ENV": "production",
            "AUTOSTOP_AGENT_SERVICE_IDENTITY": "Codex Owner Agent",
            "MINIMAL_KANBAN_MCP_BEARER_TOKEN": "change-me",
            "MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL": "http://crm.example",
            "MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL": "https://crm.example/not-mcp",
            "AUTOSTOP_MAINTENANCE_MARKER": "relative-marker",
        }
        errors = validate_production_environment(env)

        self.assertTrue(any("MINIMAL_KANBAN_MCP_BEARER_TOKEN" in item for item in errors))
        self.assertTrue(any("AUTOSTOP_AGENT_GATEWAY_FINANCE_ENABLED" in item for item in errors))
        self.assertTrue(any("HTTPS" in item for item in errors))
        self.assertTrue(any("absolute path" in item for item in errors))
        with self.assertRaises(DeploymentSecurityError):
            assert_production_environment(env)

    def test_nonproduction_defaults_all_agent_capabilities_off(self) -> None:
        policy = load_agent_gateway_security_policy({"AUTOSTOP_DEPLOYMENT_ENV": "development"})

        self.assertFalse(policy.production)
        self.assertFalse(policy.gateway_enabled)
        self.assertFalse(policy.writes_enabled)
        self.assertFalse(policy.raw_enabled)
        with self.assertRaises(DeploymentSecurityError):
            assert_production_environment(
                {"AUTOSTOP_DEPLOYMENT_ENV": "development"}, require_production=True
            )

    def test_production_rejects_url_safe_but_low_entropy_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = valid_production_env(Path(temp_dir) / "maintenance")
            env["MINIMAL_KANBAN_MCP_BEARER_TOKEN"] = "a" * 64

            errors = validate_production_environment(env)

        self.assertTrue(any("estimated entropy" in item for item in errors))

    def test_production_requires_stable_oauth_and_rejects_development_oauth(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = valid_production_env(Path(temp_dir) / "maintenance")
            env["AUTOSTOP_MCP_OAUTH_ENABLED"] = "0"
            env["AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED"] = "1"
            env["AUTOSTOP_MCP_OAUTH_STATE_KEY"] = "invalid"

            errors = validate_production_environment(env)

        self.assertTrue(any("AUTOSTOP_MCP_OAUTH_ENABLED" in item for item in errors))
        self.assertTrue(any("AUTOSTOP_MCP_EMBEDDED_OAUTH_ENABLED" in item for item in errors))
        self.assertTrue(any("AUTOSTOP_MCP_OAUTH_STATE_KEY" in item for item in errors))

    def test_production_requires_public_endpoint_to_match_public_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = valid_production_env(Path(temp_dir) / "maintenance")
            env["MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL"] = "https://other.example/mcp"

            errors = validate_production_environment(env)

        self.assertTrue(any("public base authority" in item for item in errors))

    def test_retired_release_configuration_cannot_fall_back_to_normal_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            for field in (
                "AUTOSTOP_COLD_HTTP_INGRESS_CONFIG",
                "AUTOSTOP_COLD_HTTP_INGRESS_CONFIG_SHA256",
                "AUTOSTOP_COLD_RUNTIME_BINDING",
                "AUTOSTOP_COLD_RUNTIME_BINDING_SHA256",
            ):
                for value in ("", "artificial-retired-binding"):
                    with self.subTest(field=field, value=value):
                        env = valid_production_env(Path(temp_dir) / "maintenance")
                        env[field] = value
                        expected = f"Retired release configuration is not supported: {field}"
                        self.assertEqual(validate_production_environment(env), [expected])
                        self.assertEqual(
                            validate_production_environment({field: value}), [expected]
                        )
                        with self.assertRaisesRegex(DeploymentSecurityError, field):
                            assert_production_environment(env, require_production=True)
                        with self.assertRaisesRegex(DeploymentSecurityError, field):
                            load_agent_gateway_security_policy(env)

    def test_retired_manager_source_is_rejected_before_import(self) -> None:
        from minimal_kanban.mcp.manager_registration import _add_autostop_manager_path

        environment = {"AUTOSTOP_MANAGER_PATH": "/opt/autostop-manager-cold-releases/old/source"}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(DeploymentSecurityError, "retired release source"):
                _add_autostop_manager_path()
        self.assertEqual(len(validate_production_environment(environment)), 1)

    def test_api_constructor_refuses_retired_binding_before_starting(self) -> None:
        from minimal_kanban.api.server import ApiServer

        with patch.dict(os.environ, {"AUTOSTOP_COLD_RUNTIME_BINDING": ""}, clear=True):
            with self.assertRaisesRegex(DeploymentSecurityError, "Retired release configuration"):
                ApiServer(Mock(), Mock())

    def test_normal_manager_source_resolution_does_not_require_full_gateway_policy(self) -> None:
        from minimal_kanban.mcp.manager_registration import _add_autostop_manager_path

        with tempfile.TemporaryDirectory() as directory:
            environment = {
                "AUTOSTOP_DEPLOYMENT_ENV": "production",
                "AUTOSTOP_MANAGER_PATH": directory,
            }
            with patch.dict(os.environ, environment, clear=True), patch.object(sys, "path", []):
                _add_autostop_manager_path()
                self.assertEqual(sys.path, [directory])

    def test_standalone_container_imports_from_unrelated_directory_without_pythonpath(self):
        root = Path(__file__).resolve().parents[1]
        environment = {
            key: value
            for key, value in os.environ.items()
            if key != "PYTHONPATH"
            and not key.startswith("AUTOSTOP_COLD_")
            and key != "AUTOSTOP_MANAGER_PATH"
        }
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        with tempfile.TemporaryDirectory() as temporary:
            for name in (
                "scripts/container_entrypoint.py",
                "scripts/container_healthcheck.py",
                "scripts/browser_smoke_runtime.py",
                "main_mcp.py",
            ):
                with self.subTest(name=name):
                    script = (
                        (
                            "import runpy, sys; sys.path.insert(0, " + repr(str(root)) + "); "
                            if name == "main_mcp.py"
                            else "import runpy; "
                        )
                        + "runpy.run_path("
                        + repr(str(root / name))
                        + ",run_name='import_only'); print('IMPORTED')"
                    )
                    result = subprocess.run(
                        [sys.executable, "-B", "-c", script],
                        cwd=temporary,
                        env=environment,
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr[-4096:])
                    self.assertEqual(result.stdout.strip(), "IMPORTED")

    def test_main_entrypoints_refuse_retired_configuration_before_starting(self) -> None:
        root = Path(__file__).resolve().parents[1]
        env = {"AUTOSTOP_COLD_RUNTIME_BINDING_SHA256": ""}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(DeploymentSecurityError, "Retired release configuration"):
                runpy.run_path(str(root / "main_mcp.py"), run_name="import_only")
            entrypoint = runpy.run_path(
                str(root / "scripts/container_entrypoint.py"), run_name="import_only"
            )
            with patch.object(os, "execv") as execute, patch("sys.stderr", Mock()):
                self.assertEqual(entrypoint["main"](), 78)
            execute.assert_not_called()

    def test_production_store_identity_is_internal_strong_and_split_by_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = valid_production_env(Path(temp_dir) / "maintenance")
            env["AUTOSTOP_STORE_API_URL"] = "https://autostop24.shop"
            env["AUTOSTOP_STORE_READ_TOKEN"] = STRONG_STORE_QUOTE_TOKEN

            errors = validate_store_integration_environment(env)

        self.assertTrue(any("AUTOSTOP_STORE_API_URL" in item for item in errors))
        self.assertTrue(any("must be pairwise distinct" in item for item in errors))

    def test_production_requires_all_store_tokens_without_exposing_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = valid_production_env(Path(temp_dir) / "maintenance")
            env.pop("AUTOSTOP_STORE_READ_TOKEN")
            env.pop("AUTOSTOP_STORE_QUOTE_TOKEN")
            env["AUTOSTOP_STORE_MANAGE_TOKEN"] = "weak"
            env.pop("AUTOSTOP_STORE_OWNER_TOKEN")

            errors = validate_store_integration_environment(env)

        self.assertTrue(any("AUTOSTOP_STORE_READ_TOKEN is required for" in item for item in errors))
        self.assertTrue(
            any("AUTOSTOP_STORE_QUOTE_TOKEN is required for" in item for item in errors)
        )
        self.assertTrue(
            any("AUTOSTOP_STORE_MANAGE_TOKEN must be a strong" in item for item in errors)
        )
        self.assertTrue(
            any("AUTOSTOP_STORE_OWNER_TOKEN is required for" in item for item in errors)
        )
        self.assertFalse(any(STRONG_STORE_MANAGE_TOKEN in item for item in errors))

    def test_store_misconfiguration_does_not_block_crm_production_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = valid_production_env(Path(temp_dir) / "maintenance")
            env.pop("AUTOSTOP_STORE_READ_TOKEN")
            env.pop("AUTOSTOP_STORE_QUOTE_TOKEN")
            env.pop("AUTOSTOP_STORE_MANAGE_TOKEN")
            env.pop("AUTOSTOP_STORE_OWNER_TOKEN")

            crm_errors = validate_production_environment(env)
            store_errors = validate_store_integration_environment(env)

        self.assertEqual([], crm_errors)
        self.assertTrue(store_errors)

    def test_maintenance_marker_is_checked_dynamically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / "maintenance"
            env = {"AUTOSTOP_MAINTENANCE_MARKER": str(marker)}
            self.assertFalse(is_maintenance_mode(env))
            marker.touch()
            self.assertTrue(is_maintenance_mode(env))


if __name__ == "__main__":
    unittest.main()
