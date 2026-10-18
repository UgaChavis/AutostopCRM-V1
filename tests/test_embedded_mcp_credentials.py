from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minimal_kanban.api.server import ApiServer
from minimal_kanban.integration_runtime import McpRuntimeController
from minimal_kanban.logging_setup import close_logger
from minimal_kanban.mcp.client import BoardApiClient
from minimal_kanban.mcp.main import run
from minimal_kanban.mcp.server import create_mcp_server
from minimal_kanban.services.card_service import CardService
from minimal_kanban.settings_models import IntegrationSettings
from minimal_kanban.storage.json_store import JsonStore

STRONG_TOKEN = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-"


class EmbeddedMcpCredentialTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        environment = patch.dict(
            os.environ,
            {
                "APPDATA": str(self.root),
                "AUTOSTOP_DEPLOYMENT_ENV": "development",
                "AUTOSTOP_AGENT_GATEWAY_ENABLED": "0",
                "AUTOSTOP_MCP_OAUTH_ENABLED": "0",
                "MINIMAL_KANBAN_API_HOST": "127.0.0.1",
                "MINIMAL_KANBAN_API_BEARER_TOKEN": "",
                "MINIMAL_KANBAN_MCP_BEARER_TOKEN": "",
                "MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL": "",
                "MINIMAL_KANBAN_AGENT_ENABLED": "0",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.logger = logging.getLogger(self.id())
        close_logger(self.logger)
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.addCleanup(close_logger, self.logger)
        self.store = JsonStore(state_file=self.root / "state.json", logger=self.logger)
        self.addCleanup(self.store.close)
        self.service = CardService(self.store, self.logger)
        self.api = ApiServer(
            self.service, self.logger, host="127.0.0.1", start_port=0, bearer_token=""
        )
        self.api.start()
        self.addCleanup(self.api.stop)
        self.settings = IntegrationSettings.defaults()

    def assert_anonymous_denied(self, base_url: str) -> None:
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(base_url + "/api/list_cashboxes", timeout=5)
        self.assertEqual(401, denied.exception.code)
        self.assertEqual("unauthorized", json.loads(denied.exception.read())["error"]["code"])
        denied.exception.close()

    def test_desktop_mcp_uses_owned_api_credential_across_settings_restart(self) -> None:
        controller = McpRuntimeController(
            board_api_url=self.api.base_url,
            api_bearer_token=self.api.agent_bearer_token,
            logger=self.logger,
        )
        self.addCleanup(controller.stop)
        clients = []

        def create_server(client, *_args, **_kwargs):
            self.assertTrue(client.list_cashboxes()["ok"])
            self.assert_anonymous_denied(client.base_url)
            clients.append(client)
            return Mock()

        changed_settings = replace(
            self.settings,
            local_api=replace(
                self.settings.local_api,
                local_api_auth_mode="bearer",
                local_api_bearer_token="pending-restart-settings-token",
            ),
        )
        with (
            patch(
                "minimal_kanban.integration_runtime.create_mcp_server", side_effect=create_server
            ),
            patch("minimal_kanban.integration_runtime.McpServerRuntime"),
        ):
            self.assertTrue(controller.start(self.settings).running)
            self.assertTrue(controller.restart(changed_settings).running)
        self.assertEqual(2, len(clients))

    def test_controller_without_owned_api_keeps_configured_external_credential(self) -> None:
        settings = replace(
            self.settings,
            local_api=replace(
                self.settings.local_api,
                local_api_auth_mode="bearer",
                local_api_bearer_token=self.api.agent_bearer_token,
            ),
        )
        controller = McpRuntimeController(board_api_url=self.api.base_url, logger=self.logger)
        self.addCleanup(controller.stop)

        def create_server(client, *_args, **_kwargs):
            self.assertTrue(client.list_cashboxes()["ok"])
            return Mock()

        with (
            patch(
                "minimal_kanban.integration_runtime.create_mcp_server", side_effect=create_server
            ),
            patch("minimal_kanban.integration_runtime.McpServerRuntime"),
        ):
            self.assertTrue(controller.start(settings).running)

    def test_main_mcp_owned_api_uses_private_credential_for_real_reads(self) -> None:
        self.run_main_with_api(embedded=True)

    def test_main_mcp_external_api_retains_supplied_credential_and_ownership(self) -> None:
        self.run_main_with_api(embedded=False)
        self.assertTrue(BoardApiClient(self.api.base_url).health()["ok"])

    def run_main_with_api(self, *, embedded: bool) -> None:
        settings = replace(
            self.settings, local_api=replace(self.settings.local_api, local_api_port=0)
        )
        clients = []

        def create_server(client, *_args, **_kwargs):
            self.assertTrue(client.list_cashboxes()["ok"])
            self.assert_anonymous_denied(client.base_url)
            clients.append(client)
            return Mock()

        with (
            patch("minimal_kanban.mcp.main.configure_logging", return_value=self.logger),
            patch("minimal_kanban.mcp.main.SettingsStore"),
            patch("minimal_kanban.mcp.main.SettingsService") as settings_service,
            patch("minimal_kanban.mcp.main.get_api_port", return_value=0),
            patch(
                "minimal_kanban.mcp.main._resolve_api_bearer_token",
                return_value=None if embedded else self.api.agent_bearer_token,
            ),
            patch(
                "minimal_kanban.mcp.main._resolve_api_base_url",
                return_value=None if embedded else self.api.base_url,
            ),
            patch(
                "minimal_kanban.mcp.main.JsonStore",
                side_effect=lambda **_kwargs: JsonStore(
                    state_file=self.root / "owned-api" / "state.json", logger=self.logger
                ),
            ) as store_factory,
            patch("minimal_kanban.mcp.main.start_embedded_agent_runtime"),
            patch("minimal_kanban.mcp.main.create_mcp_server", side_effect=create_server),
            patch("minimal_kanban.mcp.main.McpServerRuntime"),
            patch("minimal_kanban.mcp.main._install_mcp_stop_handlers"),
            patch("minimal_kanban.mcp.main._wait_for_mcp_shutdown"),
        ):
            settings_service.return_value.load.return_value = settings
            self.assertEqual(0, run())
        self.assertEqual(1, len(clients))
        self.assertEqual(1 if embedded else 0, store_factory.call_count)

    def test_network_mcp_requires_its_own_strong_credential_before_initialization(self) -> None:
        client = BoardApiClient(self.api.base_url, bearer_token=self.api.agent_bearer_token)
        for host in ("0.0.0.0", "::", "192.0.2.10", "crm.example"):
            for token in (None, "", "weak", "x" * 64):
                with self.subTest(host=host, token=token):
                    with patch("minimal_kanban.mcp.server.FastMCP") as sdk:
                        sdk.side_effect = AssertionError("MCP initialized before auth validation")
                        with self.assertRaises(ValueError):
                            create_mcp_server(client, self.logger, host=host, bearer_token=token)
                    sdk.assert_not_called()

    def test_network_mcp_retains_supported_authenticated_configuration(self) -> None:
        client = BoardApiClient(self.api.base_url, bearer_token=self.api.agent_bearer_token)
        for host, public_url in (("0.0.0.0", None), ("127.0.0.1", "https://crm.example/mcp")):
            with self.subTest(host=host, public_url=public_url):
                with patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools"):
                    server = create_mcp_server(
                        client,
                        self.logger,
                        host=host,
                        bearer_token=STRONG_TOKEN,
                        public_endpoint_url=public_url,
                    )
                self.assertEqual(host, server.settings.host)

    def test_public_mcp_urls_require_auth_even_when_listener_is_loopback(self) -> None:
        client = BoardApiClient(self.api.base_url, bearer_token=self.api.agent_bearer_token)
        for field in ("public_base_url", "tunnel_url", "public_endpoint_url"):
            for token in (None, "weak"):
                with self.subTest(field=field, token=token):
                    with patch("minimal_kanban.mcp.server.FastMCP") as sdk:
                        sdk.side_effect = AssertionError("MCP initialized before auth validation")
                        with self.assertRaises(ValueError):
                            create_mcp_server(
                                client,
                                self.logger,
                                host="127.0.0.1",
                                bearer_token=token,
                                **{field: "https://crm.example/mcp"},
                            )
                    sdk.assert_not_called()

    def test_local_mcp_metadata_preserves_local_unauthenticated_configuration(self) -> None:
        with patch("minimal_kanban.mcp.server._try_register_autostop_manager_tools"):
            server = create_mcp_server(
                BoardApiClient(self.api.base_url),
                self.logger,
                host="127.0.0.1",
                bearer_token=None,
                public_base_url="http://localhost:41831",
            )
        self.assertIsNone(server.settings.auth)

    def test_environment_public_base_url_also_requires_a_transport_credential(self) -> None:
        with (
            patch.dict(os.environ, {"MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL": "https://crm.example"}),
            patch("minimal_kanban.mcp.server.FastMCP") as sdk,
        ):
            sdk.side_effect = AssertionError("MCP initialized before auth validation")
            with self.assertRaises(ValueError):
                create_mcp_server(
                    BoardApiClient(self.api.base_url),
                    self.logger,
                    host="127.0.0.1",
                    bearer_token=None,
                )
        sdk.assert_not_called()


if __name__ == "__main__":
    unittest.main()
