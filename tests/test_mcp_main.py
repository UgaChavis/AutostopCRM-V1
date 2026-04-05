from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.client import BoardApiTransportError
from minimal_kanban.mcp.main import _reachable_board_api_url, _runtime_bind_host


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
