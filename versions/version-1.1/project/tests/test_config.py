from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.config import (
    DEFAULT_API_HOST,
    DEFAULT_API_PORT,
    DEFAULT_MCP_HOST,
    DEFAULT_MCP_PORT,
    get_api_base_url,
    get_api_host,
    get_api_bearer_token,
    get_api_port,
    get_api_port_fallback_limit,
    get_board_api_url,
    get_mcp_bearer_token,
    get_mcp_host,
    get_mcp_path,
    get_mcp_port,
    get_mcp_port_fallback_limit,
    get_mcp_public_base_url,
    get_mcp_public_endpoint_url,
)


class ConfigTests(unittest.TestCase):
    def test_reads_api_overrides_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MINIMAL_KANBAN_API_HOST": "127.0.0.1",
                "MINIMAL_KANBAN_API_PORT": "45123",
                "MINIMAL_KANBAN_API_PORT_FALLBACK_LIMIT": "2",
            },
            clear=False,
        ):
            self.assertEqual(get_api_host(), "127.0.0.1")
            self.assertEqual(get_api_port(), 45123)
            self.assertEqual(get_api_port_fallback_limit(), 2)

    def test_invalid_api_env_values_fall_back_to_defaults(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MINIMAL_KANBAN_API_HOST": "",
                "MINIMAL_KANBAN_API_PORT": "not-a-number",
                "MINIMAL_KANBAN_API_PORT_FALLBACK_LIMIT": "0",
            },
            clear=False,
        ):
            self.assertEqual(get_api_host(), DEFAULT_API_HOST)
            self.assertEqual(get_api_port(), DEFAULT_API_PORT)
            self.assertEqual(get_api_port_fallback_limit(), 1)

    def test_reads_mcp_overrides_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MINIMAL_KANBAN_MCP_HOST": "127.0.0.1",
                "MINIMAL_KANBAN_MCP_PORT": "48123",
                "MINIMAL_KANBAN_MCP_PORT_FALLBACK_LIMIT": "3",
                "MINIMAL_KANBAN_MCP_PATH": "custom-mcp",
                "MINIMAL_KANBAN_MCP_BEARER_TOKEN": "mcp-secret",
                "MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL": "https://example.test/mcp-app",
                "MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL": "https://example.test/mcp-app/custom-mcp",
                "MINIMAL_KANBAN_BOARD_API_URL": "https://board.test/api",
                "MINIMAL_KANBAN_API_BEARER_TOKEN": "api-secret",
            },
            clear=False,
        ):
            self.assertEqual(get_mcp_host(), "127.0.0.1")
            self.assertEqual(get_mcp_port(), 48123)
            self.assertEqual(get_mcp_port_fallback_limit(), 3)
            self.assertEqual(get_mcp_path(), "/custom-mcp")
            self.assertEqual(get_mcp_bearer_token(), "mcp-secret")
            self.assertEqual(get_mcp_public_base_url(), "https://example.test/mcp-app")
            self.assertEqual(get_mcp_public_endpoint_url(), "https://example.test/mcp-app/custom-mcp")
            self.assertEqual(get_api_base_url(), "https://board.test/api")
            self.assertEqual(get_board_api_url(), "https://board.test/api")
            self.assertEqual(get_api_bearer_token(), "api-secret")

    def test_invalid_mcp_env_values_fall_back_to_defaults(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MINIMAL_KANBAN_MCP_HOST": "",
                "MINIMAL_KANBAN_MCP_PORT": "bad",
                "MINIMAL_KANBAN_MCP_PORT_FALLBACK_LIMIT": "0",
                "MINIMAL_KANBAN_MCP_PATH": "",
                "MINIMAL_KANBAN_MCP_BEARER_TOKEN": "   ",
                "MINIMAL_KANBAN_MCP_PUBLIC_BASE_URL": "   ",
                "MINIMAL_KANBAN_MCP_PUBLIC_ENDPOINT_URL": "   ",
                "MINIMAL_KANBAN_API_BASE_URL": "   ",
                "MINIMAL_KANBAN_BOARD_API_URL": "   ",
                "MINIMAL_KANBAN_API_BEARER_TOKEN": "   ",
            },
            clear=False,
        ):
            self.assertEqual(get_mcp_host(), DEFAULT_MCP_HOST)
            self.assertEqual(get_mcp_port(), DEFAULT_MCP_PORT)
            self.assertEqual(get_mcp_port_fallback_limit(), 1)
            self.assertEqual(get_mcp_path(), "/mcp")
            self.assertIsNone(get_mcp_bearer_token())
            self.assertIsNone(get_mcp_public_base_url())
            self.assertIsNone(get_mcp_public_endpoint_url())
            self.assertIsNone(get_api_base_url())
            self.assertIsNone(get_board_api_url())
            self.assertIsNone(get_api_bearer_token())


if __name__ == "__main__":
    unittest.main()
