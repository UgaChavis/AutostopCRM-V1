from __future__ import annotations

from dataclasses import replace
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.settings_service import ConnectionCheckResult, ConnectionTestSummary, SettingsService, SettingsValidationError
from minimal_kanban.settings_store import SettingsStore


class SettingsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings_file = Path(self.temp_dir.name) / "settings.json"
        self.logger = logging.getLogger(f"test.settings.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = SettingsStore(settings_file=self.settings_file, logger=self.logger)
        self.service = SettingsService(self.store, self.logger)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_defaults_are_created_in_separate_settings_file(self) -> None:
        settings = self.service.load()

        self.assertTrue(self.settings_file.exists())
        self.assertTrue(settings.general.integration_enabled)
        self.assertTrue(settings.general.use_local_api)
        self.assertEqual(settings.local_api.local_api_host, "127.0.0.1")
        self.assertEqual(settings.local_api.local_api_port, 41731)
        self.assertEqual(settings.local_api.runtime_local_api_url, "http://127.0.0.1:41731")
        self.assertEqual(settings.local_api.effective_local_api_url, "http://127.0.0.1:41731")
        self.assertEqual(settings.local_api.local_api_health_url, "http://127.0.0.1:41731/api/health")
        self.assertEqual(settings.mcp.mcp_host, "127.0.0.1")
        self.assertEqual(settings.mcp.mcp_port, 41831)
        self.assertEqual(settings.mcp.mcp_path, "/mcp")
        self.assertEqual(settings.mcp.local_mcp_url, "http://127.0.0.1:41831/mcp")
        self.assertIn("127.0.0.1:*", settings.mcp.resolved_allowed_hosts)
        self.assertEqual(settings.openai.provider, "openai")
        self.assertEqual(settings.openai.model, "gpt-5.4-mini")
        self.assertEqual(settings.openai.base_url, "https://api.openai.com/v1")
        self.assertEqual(settings.openai.timeout_seconds, 30)

    def test_save_load_cycle_preserves_extended_values(self) -> None:
        settings = self.service.load()
        customized = replace(
            settings,
            general=replace(
                settings.general,
                integration_enabled=True,
                use_local_api=False,
                auto_connect_on_startup=True,
                test_mode=False,
            ),
            local_api=replace(
                settings.local_api,
                local_api_host="127.0.0.1",
                local_api_port=43001,
                local_api_base_url_override="https://board.example/api",
                local_api_auth_mode="bearer",
                local_api_bearer_token="board-secret",
            ),
            auth=replace(
                settings.auth,
                auth_mode="bearer",
                access_token="agent-secret",
                local_api_bearer_token="board-secret",
                mcp_bearer_token="mcp-secret",
                openai_api_key="sk-live",
            ),
            openai=replace(
                settings.openai,
                provider="openai-compatible",
                model="gpt-test",
                base_url="https://example.test/v1",
                organization_id="org-demo",
                project_id="proj-demo",
                timeout_seconds=45,
            ),
            mcp=replace(
                settings.mcp,
                mcp_enabled=True,
                mcp_host="127.0.0.1",
                mcp_port=41840,
                mcp_path="/custom-mcp",
                public_https_base_url="https://public.example",
                tunnel_url="https://demo.trycloudflare.com",
                full_mcp_url_override="https://agent.example/tools/mcp",
                allowed_hosts=("kanban.example",),
                allowed_origins=("https://kanban.example",),
                mcp_auth_mode="bearer",
                mcp_bearer_token="mcp-secret",
            ),
        )

        self.service.save(customized)
        loaded = self.service.load()

        self.assertTrue(loaded.general.integration_enabled)
        self.assertFalse(loaded.general.use_local_api)
        self.assertTrue(loaded.general.auto_connect_on_startup)
        self.assertEqual(loaded.local_api.runtime_local_api_url, "http://127.0.0.1:43001")
        self.assertEqual(loaded.local_api.effective_local_api_url, "https://board.example/api")
        self.assertEqual(loaded.local_api.local_api_bearer_token, "board-secret")
        self.assertEqual(loaded.auth.local_api_bearer_token, "board-secret")
        self.assertEqual(loaded.auth.auth_mode, "bearer")
        self.assertEqual(loaded.auth.access_token, "agent-secret")
        self.assertEqual(loaded.openai.model, "gpt-test")
        self.assertEqual(loaded.mcp.local_mcp_url, "http://127.0.0.1:41840/custom-mcp")
        self.assertEqual(loaded.mcp.derived_public_mcp_url, "https://public.example/custom-mcp")
        self.assertEqual(loaded.mcp.derived_tunnel_mcp_url, "https://demo.trycloudflare.com/custom-mcp")
        self.assertEqual(loaded.mcp.effective_mcp_url, "https://agent.example/tools/mcp")
        self.assertIn("kanban.example", loaded.mcp.allowed_hosts)
        self.assertIn("demo.trycloudflare.com", loaded.mcp.resolved_allowed_hosts)
        self.assertIn("https://demo.trycloudflare.com", loaded.mcp.resolved_allowed_origins)
        self.assertEqual(loaded.auth.mcp_bearer_token, "mcp-secret")

    def test_public_mcp_url_beats_tunnel_url_when_no_full_override(self) -> None:
        settings = self.service.load()
        self.service.save(
            replace(
                settings,
                mcp=replace(
                    settings.mcp,
                    public_https_base_url="https://public.example",
                    tunnel_url="https://demo.trycloudflare.com",
                    full_mcp_url_override="",
                ),
            )
        )

        loaded = self.service.load()

        self.assertEqual(loaded.mcp.effective_mcp_url, "https://public.example/mcp")

    def test_broken_config_falls_back_to_defaults(self) -> None:
        self.settings_file.write_text("{broken", encoding="utf-8")
        reloaded = SettingsService(SettingsStore(settings_file=self.settings_file, logger=self.logger), self.logger)

        settings = reloaded.load()

        self.assertTrue(settings.general.integration_enabled)
        self.assertEqual(settings.local_api.local_api_port, 41731)
        self.assertTrue((self.settings_file.with_suffix(".corrupted.json")).exists())

    def test_validation_rejects_invalid_values(self) -> None:
        defaults = self.service.load()
        broken = replace(
            defaults,
            auth=replace(defaults.auth, auth_mode="oauth"),
            local_api=replace(
                defaults.local_api,
                local_api_host="",
                local_api_port=70000,
                local_api_base_url_override="ftp://bad",
                local_api_auth_mode="oauth",
            ),
            openai=replace(
                defaults.openai,
                provider="",
                model="",
                base_url="ftp://bad",
                timeout_seconds=0,
            ),
            mcp=replace(
                defaults.mcp,
                mcp_host="",
                mcp_port=70000,
                mcp_path="bad",
                public_https_base_url="bad-url",
                tunnel_url="bad-url",
                full_mcp_url_override="bad-url",
                mcp_auth_mode="oauth",
            ),
        )

        errors = self.service.validate(broken)
        self.assertIn("local_api.local_api_base_url_override", errors)
        self.assertIn("openai.base_url", errors)
        self.assertIn("mcp.public_https_base_url", errors)
        self.assertIn("mcp.full_mcp_url_override", errors)

        with self.assertRaises(SettingsValidationError):
            self.service.save(broken)

    def test_update_section_can_persist_new_values(self) -> None:
        updated = self.service.update_section(
            "local_api",
            {
                "local_api_port": 43002,
                "local_api_base_url_override": "https://board.example/v1",
                "local_api_auth_mode": "bearer",
                "local_api_bearer_token": "api-token",
            },
            persist=True,
        )

        self.assertEqual(updated.local_api.local_api_port, 43002)
        self.assertEqual(updated.local_api.effective_local_api_url, "https://board.example/v1")
        self.assertEqual(updated.auth.local_api_bearer_token, "api-token")

        loaded = self.service.load()
        self.assertEqual(loaded.local_api.local_api_port, 43002)
        self.assertEqual(loaded.local_api.effective_local_api_url, "https://board.example/v1")
        self.assertEqual(loaded.auth.local_api_bearer_token, "api-token")

    def test_generate_token_returns_non_empty_secret(self) -> None:
        token = self.service.generate_token()

        self.assertGreaterEqual(len(token), 20)
        self.assertNotIn(" ", token)

    def test_test_connections_aggregates_results(self) -> None:
        settings = self.service.load()
        with patch.object(
            self.service,
            "test_local_api",
            return_value=ConnectionCheckResult("local_api", "success", "ok", "2026-03-24T10:00:00Z"),
        ), patch.object(
            self.service,
            "test_mcp_endpoint",
            return_value=ConnectionCheckResult("mcp", "success", "mcp ok", "2026-03-24T10:00:01Z"),
        ), patch.object(
            self.service,
            "test_external_endpoint",
            return_value=ConnectionCheckResult("external", "failed", "external failed", "2026-03-24T10:00:02Z", errors=("external failed",)),
        ), patch.object(
            self.service,
            "test_openai_endpoint",
            return_value=ConnectionCheckResult("openai", "skipped", "openai skipped", "2026-03-24T10:00:03Z"),
        ):
            summary = self.service.test_connections(settings)

        self.assertEqual(summary.overall_status, "failed")
        self.assertEqual(summary.local_api.status, "success")
        self.assertEqual(summary.mcp.status, "success")
        self.assertEqual(summary.external.status, "failed")
        self.assertEqual(summary.openai.status, "skipped")
        self.assertIn("external failed", summary.errors)

        updated = self.service.apply_test_summary(settings, summary)
        self.assertEqual(updated.diagnostics.external_status, "failed")
        self.assertEqual(updated.diagnostics.last_full_check, summary.tested_at)

    def test_apply_test_result_updates_single_target_status(self) -> None:
        settings = self.service.load()
        result = ConnectionCheckResult(
            target="external",
            status="skipped",
            message="Внешний MCP URL не указан.",
            checked_at="2026-03-24T09:59:00Z",
            warnings=("Нужен внешний HTTPS endpoint.",),
        )

        updated = self.service.apply_test_result(settings, "external", result, tested_at="2026-03-24T10:00:00Z")

        self.assertEqual(updated.diagnostics.external_status, "skipped")
        self.assertEqual(updated.diagnostics.external_message, "Внешний MCP URL не указан.")
        self.assertEqual(updated.diagnostics.last_external_endpoint_check, "2026-03-24T10:00:00Z")
        self.assertIn("Нужен внешний HTTPS endpoint.", updated.diagnostics.last_warnings)

    def test_external_check_reports_invalid_host_header_explicitly(self) -> None:
        settings = self.service.update_section(
            "mcp",
            {
                "mcp_enabled": True,
                "tunnel_url": "https://demo.ngrok-free.app",
            },
            settings=self.service.load(),
            persist=False,
        )

        with patch.object(
            self.service,
            "_probe_mcp_server",
            side_effect=RuntimeError("MCP runtime отклоняет внешний Host header. Нужно разрешить host из Tunnel URL / external domain."),
        ):
            result = self.service.test_external_endpoint(settings)

        self.assertEqual(result.status, "failed")
        self.assertIn("Host header", result.message)

    def test_local_api_smoke_uses_existing_column_instead_of_legacy_done(self) -> None:
        settings = self.service.load()

        class FakeClient:
            def list_columns(self):
                return {
                    "ok": True,
                    "data": {
                        "columns": [
                            {"id": "priemka"},
                            {"id": "diagnostics"},
                        ]
                    },
                }

            def create_card(self, **_kwargs):
                return {"ok": True, "data": {"card": {"id": "card-1", "column": "priemka"}}}

            def move_card(self, *, card_id, column):
                return {"ok": card_id == "card-1" and column == "diagnostics"}

            def archive_card(self, *, card_id):
                return {"ok": card_id == "card-1"}

        smoke = self.service._run_local_api_smoke(FakeClient(), settings)

        self.assertEqual(smoke["errors"], [])
        self.assertIn("move_card", smoke["steps"])

    def test_mcp_check_does_not_require_oauth_metadata_when_bearer_token_is_missing(self) -> None:
        settings = self.service.update_section(
            "mcp",
            {
                "mcp_enabled": True,
                "mcp_auth_mode": "bearer",
                "mcp_bearer_token": "",
            },
            settings=self.service.load(),
            persist=False,
        )
        settings = self.service.update_section(
            "auth",
            {
                "mcp_bearer_token": "",
                "access_token": "",
            },
            settings=settings,
            persist=False,
        )

        with patch.object(
            self.service,
            "_probe_mcp_server",
            return_value={
                "tools_count": 29,
                "tool_names": [],
                "list_columns_ok": True,
                "missing_required_tools": [],
                "gpt_wall_ok": True,
                "oauth_authorization_server_ok": False,
                "oauth_protected_resource_ok": False,
            },
        ):
            result = self.service.test_mcp_endpoint(settings)

        self.assertEqual(result.status, "success")
        self.assertNotIn("OAuth metadata", result.message)

    def test_mcp_check_reports_missing_required_tools(self) -> None:
        settings = self.service.update_section(
            "mcp",
            {
                "mcp_enabled": True,
            },
            settings=self.service.load(),
            persist=False,
        )

        with patch.object(
            self.service,
            "_probe_mcp_server",
            return_value={
                "tools_count": 28,
                "tool_names": [],
                "list_columns_ok": True,
                "missing_required_tools": ["get_gpt_wall"],
                "gpt_wall_ok": True,
                "oauth_authorization_server_ok": True,
                "oauth_protected_resource_ok": True,
            },
        ):
            result = self.service.test_mcp_endpoint(settings)

        self.assertEqual(result.status, "failed")
        self.assertIn("get_gpt_wall", result.message)

    def test_external_check_does_not_require_oauth_metadata_when_bearer_token_is_missing(self) -> None:
        settings = self.service.update_section(
            "mcp",
            {
                "mcp_enabled": True,
                "tunnel_url": "https://demo.ngrok-free.app",
                "mcp_auth_mode": "bearer",
                "mcp_bearer_token": "",
            },
            settings=self.service.load(),
            persist=False,
        )
        settings = self.service.update_section(
            "auth",
            {
                "mcp_bearer_token": "",
                "access_token": "",
            },
            settings=settings,
            persist=False,
        )

        with patch.object(
            self.service,
            "_probe_mcp_server",
            return_value={
                "tools_count": 29,
                "tool_names": [],
                "list_columns_ok": True,
                "missing_required_tools": [],
                "gpt_wall_ok": True,
                "oauth_authorization_server_ok": False,
                "oauth_protected_resource_ok": False,
            },
        ):
            result = self.service.test_external_endpoint(settings)

        self.assertEqual(result.status, "success")
        self.assertNotIn("OAuth metadata", result.message)


if __name__ == "__main__":
    unittest.main()
