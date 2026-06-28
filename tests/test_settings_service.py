from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import unittest
import urllib.error

# ruff: noqa: E402
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.settings_models import (
    AuthSettings,
    IntegrationSettings,
    LocalApiSettings,
    McpSettings,
    derive_allowed_hosts,
    derive_allowed_origins,
    is_external_http_url,
    is_http_url,
    normalize_host,
    normalize_int,
)
from minimal_kanban.settings_service import (
    ConnectionCheckResult,
    SettingsService,
    SettingsValidationError,
)
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
        self.assertTrue(settings.general.auto_connect_on_startup)
        self.assertEqual(settings.local_api.local_api_host, "127.0.0.1")
        self.assertEqual(settings.local_api.local_api_port, 41731)
        self.assertEqual(settings.local_api.runtime_local_api_url, "http://127.0.0.1:41731")
        self.assertEqual(settings.local_api.effective_local_api_url, "http://127.0.0.1:41731")
        self.assertEqual(
            settings.local_api.local_api_health_url, "http://127.0.0.1:41731/api/health"
        )
        self.assertEqual(settings.mcp.mcp_host, "127.0.0.1")
        self.assertEqual(settings.mcp.mcp_port, 41831)
        self.assertEqual(settings.mcp.mcp_path, "/mcp")
        self.assertEqual(settings.mcp.local_mcp_url, "http://127.0.0.1:41831/mcp")
        self.assertIn("127.0.0.1:*", settings.mcp.resolved_allowed_hosts)
        self.assertEqual(settings.openai.provider, "openai")
        self.assertEqual(settings.openai.model, "gpt-5.4-mini")
        self.assertEqual(settings.openai.base_url, "https://api.openai.com/v1")
        self.assertEqual(settings.openai.timeout_seconds, 30)

    def test_runtime_urls_bracket_ipv6_hosts(self) -> None:
        local_api = LocalApiSettings(local_api_host="::1", local_api_port=41731)
        mcp = McpSettings(mcp_host="::1", mcp_port=41831, mcp_path="/mcp")

        self.assertEqual(local_api.runtime_local_api_url, "http://[::1]:41731")
        self.assertEqual(local_api.local_api_health_url, "http://[::1]:41731/api/health")
        self.assertEqual(mcp.local_mcp_url, "http://[::1]:41831/mcp")
        self.assertIn("[::1]:41831", mcp.resolved_allowed_hosts)
        self.assertIn("http://[::1]:41831", mcp.resolved_allowed_origins)

    def test_external_http_url_treats_loopback_and_wildcard_hosts_as_local(self) -> None:
        local_urls = (
            "http://127.0.0.1:41731/api/health",
            "http://localhost:41831/mcp",
            "http://[::1]:41831/mcp",
            "http://0.0.0.0:41831/mcp",
            "http://[::]:41831/mcp",
            "https://board.localhost/mcp",
        )

        for url in local_urls:
            with self.subTest(url=url):
                self.assertFalse(is_external_http_url(url))

        self.assertTrue(is_external_http_url("https://crm.autostopcrm.ru/mcp"))
        self.assertTrue(is_external_http_url("http://192.168.1.20:41831/mcp"))

    def test_invalid_url_ports_are_not_treated_as_http_urls(self) -> None:
        bad_urls = (
            "https://crm.example:bad/mcp",
            "https://crm.example:99999/mcp",
        )

        for url in bad_urls:
            with self.subTest(url=url):
                self.assertFalse(is_http_url(url))
                self.assertFalse(is_external_http_url(url))
                self.assertNotIn("crm.example", derive_allowed_hosts(url))
                self.assertNotIn("https://crm.example", derive_allowed_origins(url))

    def test_settings_integer_normalizer_falls_back_for_overflow(self) -> None:
        self.assertEqual(
            normalize_int(float("inf"), default=30, minimum=1, maximum=600),
            30,
        )
        self.assertEqual(normalize_int(1e308, default=30, minimum=1, maximum=600), 30)
        self.assertEqual(normalize_int(1e308, default=30, minimum=1), 30)
        self.assertEqual(normalize_int(True, default=30, minimum=1, maximum=600), 30)
        self.assertEqual(normalize_int(1.5, default=30, minimum=1, maximum=600), 30)
        self.assertEqual(normalize_int(2.0, default=30, minimum=1, maximum=600), 2)
        self.assertEqual(
            IntegrationSettings.from_dict({"schema_version": 1e308}).schema_version,
            3,
        )

    def test_settings_host_normalizer_rejects_url_parts_and_ports(self) -> None:
        default = "127.0.0.1"

        self.assertEqual(normalize_host("127.0.0.1/api", default=default), default)
        self.assertEqual(normalize_host("127.0.0.1?debug=1", default=default), default)
        self.assertEqual(normalize_host("localhost:41731", default=default), default)
        self.assertEqual(normalize_host("[::1]:41731", default=default), default)
        self.assertEqual(normalize_host("::1", default=default), "::1")
        self.assertEqual(
            LocalApiSettings.from_dict({"local_api_host": "127.0.0.1/api"}).local_api_host,
            default,
        )

    def test_secret_normalizers_reject_internal_whitespace(self) -> None:
        local_api = LocalApiSettings.from_dict({"local_api_bearer_token": "good\r\nX-Bad: yes"})
        mcp = McpSettings.from_dict({"mcp_bearer_token": "mcp secret"})
        auth = AuthSettings.from_dict(
            {
                "access_token": "access\tsecret",
                "local_api_bearer_token": "api\nsecret",
                "mcp_bearer_token": " mcp-secret ",
                "openai_api_key": "sk-live\r\nX-Bad: yes",
            }
        )

        self.assertEqual(local_api.local_api_bearer_token, "")
        self.assertEqual(mcp.mcp_bearer_token, "")
        self.assertEqual(auth.access_token, "")
        self.assertEqual(auth.local_api_bearer_token, "")
        self.assertEqual(auth.mcp_bearer_token, "mcp-secret")
        self.assertEqual(auth.openai_api_key, "")

    def test_missing_auto_connect_setting_defaults_to_true(self) -> None:
        self.settings_file.write_text("{}", encoding="utf-8")

        loaded = self.service.load()

        self.assertTrue(loaded.general.auto_connect_on_startup)

    def test_derived_allowed_hosts_and_origins_accept_tuple_inputs(self) -> None:
        hosts = derive_allowed_hosts(
            "http://0.0.0.0:41831/mcp",
            "https://crm.autostopcrm.ru",
            None,
            "https://crm.autostopcrm.ru/mcp",
            extra_hosts=("185.42.164.2", "185.42.164.2:*"),
        )
        origins = derive_allowed_origins(
            "http://0.0.0.0:41831/mcp",
            "https://crm.autostopcrm.ru",
            None,
            "https://crm.autostopcrm.ru/mcp",
            extra_origins=("http://185.42.164.2", "http://185.42.164.2:*"),
        )

        self.assertIn("185.42.164.2", hosts)
        self.assertIn("185.42.164.2:*", hosts)
        self.assertIn("http://185.42.164.2", origins)
        self.assertIn("http://185.42.164.2:*", origins)

    def test_derived_allowed_hosts_and_origins_strip_trailing_dot(self) -> None:
        hosts = derive_allowed_hosts("https://crm.example.:443/mcp")
        origins = derive_allowed_origins("https://crm.example.:443/mcp")

        self.assertIn("crm.example:443", hosts)
        self.assertNotIn("crm.example.:443", hosts)
        self.assertIn("https://crm.example:443", origins)
        self.assertNotIn("https://crm.example.:443", origins)

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
        self.assertEqual(
            loaded.mcp.derived_tunnel_mcp_url, "https://demo.trycloudflare.com/custom-mcp"
        )
        self.assertEqual(loaded.mcp.effective_mcp_url, "https://agent.example/tools/mcp")
        self.assertIn("kanban.example", loaded.mcp.allowed_hosts)
        self.assertIn("demo.trycloudflare.com", loaded.mcp.resolved_allowed_hosts)
        self.assertIn("https://demo.trycloudflare.com", loaded.mcp.resolved_allowed_origins)
        self.assertEqual(loaded.auth.mcp_bearer_token, "mcp-secret")

    def test_normalize_prefers_auth_tokens_when_section_tokens_disagree(self) -> None:
        settings = self.service.load()
        mismatched = replace(
            settings,
            local_api=replace(
                settings.local_api,
                local_api_auth_mode="bearer",
                local_api_bearer_token="section-api-token",
            ),
            mcp=replace(
                settings.mcp,
                mcp_auth_mode="bearer",
                mcp_bearer_token="section-mcp-token",
            ),
            auth=replace(
                settings.auth,
                local_api_bearer_token="auth-api-token",
                mcp_bearer_token="auth-mcp-token",
            ),
        )

        normalized = self.service.normalize(mismatched)

        self.assertEqual(normalized.local_api.local_api_bearer_token, "auth-api-token")
        self.assertEqual(normalized.auth.local_api_bearer_token, "auth-api-token")
        self.assertEqual(normalized.mcp.mcp_bearer_token, "auth-mcp-token")
        self.assertEqual(normalized.auth.mcp_bearer_token, "auth-mcp-token")

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
        reloaded = SettingsService(
            SettingsStore(settings_file=self.settings_file, logger=self.logger), self.logger
        )

        settings = reloaded.load()

        self.assertTrue(settings.general.integration_enabled)
        self.assertEqual(settings.local_api.local_api_port, 41731)
        self.assertTrue((self.settings_file.with_suffix(".corrupted.json")).exists())

    def test_nonstandard_json_constants_in_config_are_backed_up(self) -> None:
        self.settings_file.write_text('{"openai":{"timeout_seconds":NaN}}', encoding="utf-8")
        reloaded = SettingsService(
            SettingsStore(settings_file=self.settings_file, logger=self.logger), self.logger
        )

        settings = reloaded.load()

        self.assertEqual(settings.openai.timeout_seconds, 30)
        backup = self.settings_file.with_suffix(".corrupted.json")
        self.assertIn("NaN", backup.read_text(encoding="utf-8"))
        self.assertNotIn("NaN", self.settings_file.read_text(encoding="utf-8"))

    def test_oversized_config_is_backed_up_before_defaults_are_written(self) -> None:
        self.settings_file.write_text(
            '{"general":{},"padding":"' + ("x" * 4000) + '"}', encoding="utf-8"
        )

        with patch("minimal_kanban.settings_store.SETTINGS_FILE_MAX_BYTES", 3000):
            reloaded = SettingsService(
                SettingsStore(settings_file=self.settings_file, logger=self.logger), self.logger
            )
            settings = reloaded.load()

        self.assertTrue(settings.general.integration_enabled)
        backup = self.settings_file.with_suffix(".corrupted.json")
        self.assertIn("padding", backup.read_text(encoding="utf-8"))
        self.assertNotIn("padding", self.settings_file.read_text(encoding="utf-8"))

    def test_settings_write_rejects_payload_that_reader_would_ignore_as_oversized(self) -> None:
        settings = self.service.load()
        before = self.settings_file.read_text(encoding="utf-8")

        with (
            patch("minimal_kanban.settings_store.SETTINGS_FILE_MAX_BYTES", 64),
            self.assertRaisesRegex(ValueError, "settings file is too large"),
        ):
            self.service.save(settings)

        self.assertEqual(self.settings_file.read_text(encoding="utf-8"), before)
        self.assertEqual(list(self.settings_file.parent.glob(".settings.json.*.tmp")), [])

    def test_deeply_nested_config_is_backed_up_before_defaults_are_written(self) -> None:
        deep_json = "[" * 5000 + "]" * 5000
        self.settings_file.write_text(deep_json, encoding="utf-8")
        reloaded = SettingsService(
            SettingsStore(settings_file=self.settings_file, logger=self.logger), self.logger
        )

        settings = reloaded.load()

        self.assertTrue(settings.general.integration_enabled)
        backup = self.settings_file.with_suffix(".corrupted.json")
        self.assertEqual(backup.read_text(encoding="utf-8"), deep_json)

    def test_broken_config_backup_does_not_overwrite_previous_backup(self) -> None:
        previous_backup = self.settings_file.with_suffix(".corrupted.json")
        previous_backup.write_text("previous broken config", encoding="utf-8")
        self.settings_file.write_text("{broken", encoding="utf-8")
        reloaded = SettingsService(
            SettingsStore(settings_file=self.settings_file, logger=self.logger), self.logger
        )

        settings = reloaded.load()

        self.assertTrue(settings.general.integration_enabled)
        self.assertEqual(previous_backup.read_text(encoding="utf-8"), "previous broken config")
        self.assertEqual(
            self.settings_file.with_name("settings.corrupted-2.json").read_text(encoding="utf-8"),
            "{broken",
        )

    def test_non_object_config_is_backed_up_before_defaults_are_written(self) -> None:
        self.settings_file.write_text("[]", encoding="utf-8")
        reloaded = SettingsService(
            SettingsStore(settings_file=self.settings_file, logger=self.logger), self.logger
        )

        settings = reloaded.load()

        self.assertTrue(settings.general.integration_enabled)
        backup = self.settings_file.with_suffix(".corrupted.json")
        self.assertEqual(backup.read_text(encoding="utf-8"), "[]")
        self.assertNotEqual(self.settings_file.read_text(encoding="utf-8"), "[]")

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
        with (
            patch.object(
                self.service,
                "test_local_api",
                return_value=ConnectionCheckResult(
                    "local_api", "success", "ok", "2026-03-24T10:00:00Z"
                ),
            ),
            patch.object(
                self.service,
                "test_mcp_endpoint",
                return_value=ConnectionCheckResult(
                    "mcp", "success", "mcp ok", "2026-03-24T10:00:01Z"
                ),
            ),
            patch.object(
                self.service,
                "test_external_endpoint",
                return_value=ConnectionCheckResult(
                    "external",
                    "failed",
                    "external failed",
                    "2026-03-24T10:00:02Z",
                    errors=("external failed",),
                ),
            ),
            patch.object(
                self.service,
                "test_openai_endpoint",
                return_value=ConnectionCheckResult(
                    "openai", "skipped", "openai skipped", "2026-03-24T10:00:03Z"
                ),
            ),
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

        updated = self.service.apply_test_result(
            settings, "external", result, tested_at="2026-03-24T10:00:00Z"
        )

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
            side_effect=RuntimeError(
                "MCP runtime отклоняет внешний Host header. Нужно разрешить host из Tunnel URL / external domain."
            ),
        ):
            result = self.service.test_external_endpoint(settings)

        self.assertEqual(result.status, "failed")
        self.assertIn("Host header", result.message)

    def test_openai_check_reports_non_object_json_as_failed_connection(self) -> None:
        settings = self.service.update_section(
            "auth",
            {"openai_api_key": "sk-test"},
            settings=self.service.load(),
            persist=False,
        )

        with patch.object(
            self.service,
            "_request_json",
            side_effect=RuntimeError("Endpoint https://example.test/models вернул JSON не-объект."),
        ):
            result = self.service.test_openai_endpoint(settings)

        self.assertEqual(result.status, "failed")
        self.assertIn("JSON не-объект", result.message)

    def test_request_json_rejects_success_non_object_payload(self) -> None:
        with self.assertRaises(RuntimeError):
            self.service._parse_json_object(b"[]", url="https://api.example/models")

    def test_request_json_rejects_success_non_utf8_payload(self) -> None:
        with self.assertRaises(RuntimeError):
            self.service._parse_json_object(b"\xff", url="https://api.example/models")

    def test_request_json_rejects_nonstandard_json_constants(self) -> None:
        with self.assertRaises(RuntimeError):
            self.service._parse_json_object(
                b'{"ok": true, "data": NaN}',
                url="https://api.example/models",
            )

    def test_request_json_rejects_oversized_success_body(self) -> None:
        class HugeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, size: int = -1) -> bytes:
                return b"x" * max(0, size)

        with (
            patch("minimal_kanban.settings_service._MAX_SETTINGS_HTTP_RESPONSE_BYTES", 4),
            patch(
                "minimal_kanban.settings_service._urlopen_no_redirect",
                return_value=HugeResponse(),
            ),
            self.assertRaises(RuntimeError) as error,
        ):
            self.service._request_json(
                "https://api.example/models",
                method="GET",
                headers={},
                timeout_seconds=1,
            )

        self.assertIn("слишком большой JSON", str(error.exception))

    def test_request_json_ignores_oversized_error_body(self) -> None:
        class HugeHttpError(urllib.error.HTTPError):
            def __init__(self) -> None:
                super().__init__(
                    url="https://api.example/models",
                    code=503,
                    msg="Service Unavailable",
                    hdrs=None,
                    fp=None,
                )

            def read(self, size: int = -1) -> bytes:
                return b"x" * max(0, size)

        with (
            patch("minimal_kanban.settings_service._MAX_SETTINGS_HTTP_RESPONSE_BYTES", 4),
            patch(
                "minimal_kanban.settings_service._urlopen_no_redirect",
                side_effect=HugeHttpError(),
            ),
        ):
            status, payload = self.service._request_json(
                "https://api.example/models",
                method="GET",
                headers={},
                timeout_seconds=1,
            )

        self.assertEqual(status, 503)
        self.assertEqual(payload, {})

    def test_request_json_rejects_redirect_responses(self) -> None:
        redirect = urllib.error.HTTPError(
            url="https://api.example/models",
            code=302,
            msg="Found",
            hdrs={"Location": "https://elsewhere.example/models"},
            fp=None,
        )

        with (
            patch("minimal_kanban.settings_service._urlopen_no_redirect", side_effect=redirect),
            self.assertRaisesRegex(RuntimeError, "HTTP redirect"),
        ):
            self.service._request_json(
                "https://api.example/models",
                method="GET",
                headers={"Authorization": "Bearer secret"},
                timeout_seconds=1,
            )

    def test_request_json_rejects_header_breaks_before_urlopen(self) -> None:
        with patch("minimal_kanban.settings_service._urlopen_no_redirect") as urlopen:
            with self.assertRaisesRegex(RuntimeError, "Некорректный HTTP header"):
                self.service._request_json(
                    "https://api.example/models",
                    method="GET",
                    headers={"Authorization": "Bearer good\r\nX-Bad: yes"},
                    timeout_seconds=1,
                )

        urlopen.assert_not_called()

    def test_oauth_metadata_origin_brackets_ipv6_hosts(self) -> None:
        origin = self.service._httpx_url_origin(httpx.URL("http://[::1]:41831/mcp"))

        self.assertEqual(origin, "http://[::1]:41831")

    def test_mcp_probe_rejects_external_redirects(self) -> None:
        class RedirectResponse:
            status_code = 302
            text = ""

        class FakeAsyncClient:
            init_kwargs: dict[str, object] = {}

            def __init__(self, *args, **kwargs) -> None:
                _ = args
                type(self).init_kwargs = dict(kwargs)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

            async def get(self, url: str) -> RedirectResponse:
                _ = url
                return RedirectResponse()

        with patch("minimal_kanban.settings_service.httpx.AsyncClient", FakeAsyncClient):
            with self.assertRaisesRegex(RuntimeError, "HTTP redirect"):
                asyncio.run(
                    self.service._probe_mcp_server(
                        "https://crm.example/mcp",
                        bearer_token=None,
                        timeout_seconds=1.0,
                    )
                )

        self.assertIs(FakeAsyncClient.init_kwargs["follow_redirects"], False)

    def test_probe_helpers_ignore_non_dict_structured_content(self) -> None:
        class Result:
            structuredContent = ["not", "a", "dict"]

        self.assertEqual(self.service._structured_content_payload(Result()), {})

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
                "board_content_ok": True,
                "board_events_ok": True,
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
                "board_content_ok": True,
                "board_events_ok": True,
                "gpt_wall_ok": True,
                "oauth_authorization_server_ok": True,
                "oauth_protected_resource_ok": True,
            },
        ):
            result = self.service.test_mcp_endpoint(settings)

        self.assertEqual(result.status, "failed")
        self.assertIn("get_gpt_wall", result.message)

    def test_external_check_does_not_require_oauth_metadata_when_bearer_token_is_missing(
        self,
    ) -> None:
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
                "board_content_ok": True,
                "board_events_ok": True,
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
