from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.client import BoardApiTransportError
from minimal_kanban.mcp.main import _reachable_board_api_url, _runtime_bind_host
from minimal_kanban.settings_models import IntegrationSettings


class ReachableBoardApiUrlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger(f"test.mcp.main.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False

    def test_returns_none_when_url_is_missing(self) -> None:
        self.assertIsNone(_reachable_board_api_url(None, bearer_token=None, logger=self.logger))

    def test_returns_url_when_health_check_is_ok(self) -> None:
        with patch("minimal_kanban.mcp.main.BoardApiClient") as client_cls:
            client_cls.return_value.health.return_value = {"ok": True}
            self.assertEqual(
                _reachable_board_api_url("http://127.0.0.1:41731", bearer_token="token", logger=self.logger),
                "http://127.0.0.1:41731",
            )

    def test_returns_none_when_transport_error_is_raised(self) -> None:
        with patch("minimal_kanban.mcp.main.BoardApiClient") as client_cls:
            client_cls.return_value.health.side_effect = BoardApiTransportError("offline")
            self.assertIsNone(
                _reachable_board_api_url("http://127.0.0.1:41731", bearer_token="token", logger=self.logger)
            )

    def test_propagates_non_transport_errors(self) -> None:
        with patch("minimal_kanban.mcp.main.BoardApiClient") as client_cls:
            client_cls.return_value.health.side_effect = ValueError("broken payload")
            with self.assertRaises(ValueError):
                _reachable_board_api_url("http://127.0.0.1:41731", bearer_token="token", logger=self.logger)


class RuntimeBindHostTests(unittest.TestCase):
    def test_converts_localhost_to_wildcard_when_host_is_not_explicit(self) -> None:
        self.assertEqual(_runtime_bind_host("127.0.0.1", env_explicit=False), "0.0.0.0")
        self.assertEqual(_runtime_bind_host("localhost", env_explicit=False), "0.0.0.0")

    def test_keeps_host_when_env_is_explicit(self) -> None:
        self.assertEqual(_runtime_bind_host("127.0.0.1", env_explicit=True), "127.0.0.1")
        self.assertEqual(_runtime_bind_host("0.0.0.0", env_explicit=True), "0.0.0.0")


class McpMainRunTests(unittest.TestCase):
    def test_run_seeds_demo_board_before_starting_embedded_api(self) -> None:
        logger = logging.getLogger("test.mcp.main.run")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        settings = IntegrationSettings.defaults()
        card_service = Mock()
        card_service.ensure_demo_board.return_value = True
        api_server = Mock()
        api_server.base_url = "http://127.0.0.1:41731"
        runtime = Mock()
        agent_service = Mock()

        def fake_signal(_signum, handler):
            handler(None, None)

        with patch("minimal_kanban.mcp.main.configure_logging", return_value=logger), patch(
            "minimal_kanban.mcp.main.close_logger"
        ), patch(
            "minimal_kanban.mcp.main.SettingsStore"
        ), patch(
            "minimal_kanban.mcp.main.SettingsService"
        ) as settings_service_cls, patch(
            "minimal_kanban.mcp.main._reachable_board_api_url", return_value=None
        ), patch(
            "minimal_kanban.mcp.main.get_board_api_url", return_value=None
        ), patch(
            "minimal_kanban.mcp.main.discover_board_api", return_value=None
        ), patch(
            "minimal_kanban.mcp.main.JsonStore"
        ), patch(
            "minimal_kanban.mcp.main.CardService", return_value=card_service
        ), patch(
            "minimal_kanban.mcp.main.OperatorAuthService"
        ), patch(
            "minimal_kanban.mcp.main.AgentControlService", return_value=agent_service
        ), patch(
            "minimal_kanban.mcp.main.ApiServer", return_value=api_server
        ), patch(
            "minimal_kanban.mcp.main.BoardApiClient"
        ), patch(
            "minimal_kanban.mcp.main.create_mcp_server", return_value=Mock()
        ), patch(
            "minimal_kanban.mcp.main.McpServerRuntime", return_value=runtime
        ), patch(
            "minimal_kanban.mcp.main.signal.signal", side_effect=fake_signal
        ):
            settings_service_cls.return_value.load.return_value = settings

            from minimal_kanban.mcp.main import run

            result = run()

        self.assertEqual(result, 0)
        card_service.ensure_demo_board.assert_called_once_with()
        card_service.attach_agent_control.assert_called_once_with(agent_service)
        agent_service.bind_board_service.assert_called_once_with(card_service)
        api_server.start.assert_called_once_with()
        runtime.start.assert_called_once_with()
        runtime.stop.assert_called_once_with()
        api_server.stop.assert_called_once_with()
