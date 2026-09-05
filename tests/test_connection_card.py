from __future__ import annotations

# ruff: noqa: E402
import json
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.connection_card import (
    GPT_CONNECTOR_REQUIRED_TOOL_NAMES,
    MCP_TOOL_NAMES,
    OPTIONAL_MANAGER_MCP_TOOL_NAMES,
    build_board_share_url,
    build_chatgpt_connect_payload,
    build_chatgpt_connector_payload,
    build_connection_card,
    build_responses_api_payload,
    build_settings_export,
    derive_board_root_url,
    derive_connector_display_name,
    get_mcp_python_entry_path,
    get_mcp_script_path,
    get_mcp_setup_doc_path,
    get_project_root,
    get_release_exe_path,
    resolve_connector_auth_mode,
)
from minimal_kanban.integration_runtime import McpRuntimeState
from minimal_kanban.settings_models import IntegrationSettings


class ConnectionCardTests(unittest.TestCase):
    def test_connection_card_masks_secrets_by_default(self) -> None:
        settings = IntegrationSettings.defaults()
        settings = settings.__class__.from_dict(
            {
                **settings.to_dict(),
                "auth": {
                    "auth_mode": "bearer",
                    "access_token": "access-secret",
                    "local_api_bearer_token": "local-secret",
                    "mcp_bearer_token": "mcp-secret",
                    "openai_api_key": "sk-secret",
                },
                "mcp": {
                    **settings.mcp.to_dict(),
                    "mcp_enabled": True,
                    "public_https_base_url": "https://public.example",
                },
            }
        )

        text = build_connection_card(
            settings,
            runtime_api_url="http://127.0.0.1:41731",
            runtime_state=McpRuntimeState(True, "http://127.0.0.1:41831/mcp", "running", ""),
            include_secrets=False,
        )

        self.assertIn("effective_mcp_url = https://public.example/mcp", text)
        self.assertIn("openai_api_key = [скрыто]", text)
        self.assertIn("КАРТОЧКА ПОДКЛЮЧЕНИЯ GPT / MCP", text)
        self.assertNotIn("Settings ->", text)
        self.assertNotIn("sk-secret", text)
        self.assertNotIn("local-secret", text)

    def test_settings_export_can_include_secrets(self) -> None:
        settings = IntegrationSettings.from_dict(
            {
                "auth": {
                    "auth_mode": "bearer",
                    "access_token": "access-secret",
                    "local_api_bearer_token": "local-secret",
                    "mcp_bearer_token": "mcp-secret",
                    "openai_api_key": "sk-secret",
                }
            }
        )

        redacted = build_settings_export(settings, include_secrets=False)
        full = build_settings_export(settings, include_secrets=True)

        self.assertIn("[скрыто]", redacted)
        self.assertIn("sk-secret", full)
        self.assertIn("local-secret", full)

    def test_chatgpt_connect_payload_contains_key_values(self) -> None:
        settings = IntegrationSettings.from_dict(
            {
                "mcp": {
                    "mcp_enabled": True,
                    "public_https_base_url": "https://public.example",
                    "mcp_auth_mode": "bearer",
                    "mcp_bearer_token": "mcp-secret",
                },
                "auth": {
                    "mcp_bearer_token": "mcp-secret",
                },
            }
        )

        text = build_chatgpt_connect_payload(
            settings,
            runtime_api_url="http://127.0.0.1:41731",
            runtime_state=McpRuntimeState(False, "", "", ""),
        )

        self.assertIn("effective_mcp_url = https://public.example/mcp", text)
        self.assertIn("local_mcp_url = http://127.0.0.1:41831/mcp", text)
        self.assertIn("effective_local_api_url = http://127.0.0.1:41731", text)
        self.assertNotIn("mcp_bearer_token =", text)
        self.assertNotIn("mcp-secret", text)
        self.assertIn("Internal bearer compatibility is not included", text)
        self.assertIn(
            "connector_display_name = AutoStop CRM / This Board Only (public.example)", text
        )
        self.assertIn("connector_scope_rule = current AutoStop CRM board only", text)
        self.assertIn("chatgpt_home = https://chatgpt.com/", text)
        self.assertIn("Connection flow:", text)
        self.assertNotIn("Settings ->", text)
        self.assertIn("[GPT-CRITICAL TOOLS]", text)
        self.assertIn("- ping_connector", text)
        self.assertIn("- agent_bootstrap", text)
        self.assertIn("- agent_board_digest", text)
        self.assertIn("- prepare_action_contract", text)
        self.assertIn("- get_connector_identity", text)
        self.assertIn("- get_runtime_status", text)
        self.assertNotIn("- bootstrap_context", text)
        self.assertNotIn("- manager_board_scan", text)
        self.assertNotIn("- update_card", text)
        self.assertIn("[RECOMMENDED FIRST PROMPT]", text)
        self.assertIn(
            "Understand the customer goal from the available CRM, Store, and conversation context.",
            text,
        )
        self.assertIn("routes are hints, not a call order", text)
        self.assertIn("native guard and exact verification", text)
        self.assertIn("Never call hidden legacy tools", text)
        self.assertIn("[HIDDEN CAPABILITIES]", text)
        mcp_section = text.split("[MCP TOOLS]\n", 1)[1].split("\n\n[HIDDEN", 1)[0]
        self.assertEqual(
            len([line for line in mcp_section.splitlines() if line.startswith("- ")]), 24
        )

    def test_board_share_url_and_connection_card_expose_public_board_link(self) -> None:
        settings = IntegrationSettings.from_dict(
            {
                "local_api": {
                    "local_api_base_url_override": "https://board.example/api",
                    "local_api_auth_mode": "bearer",
                    "local_api_bearer_token": "board-secret",
                }
            }
        )

        self.assertEqual(
            derive_board_root_url("https://board.example/api"), "https://board.example"
        )
        self.assertEqual(
            derive_board_root_url("https://board.example/api?source=desk"),
            "https://board.example?source=desk",
        )
        self.assertEqual(
            build_board_share_url("https://board.example/api?source=desk", "board-secret"),
            "https://board.example?source=desk&access_token=board-secret",
        )
        self.assertEqual(
            build_board_share_url("https://board.example/api", "board-secret"),
            "https://board.example?access_token=board-secret",
        )

        text = build_connection_card(
            settings,
            runtime_api_url="http://127.0.0.1:41731",
            runtime_state=McpRuntimeState(False, "", "", ""),
            include_secrets=False,
        )

        self.assertIn("public_board_url = https://board.example", text)
        self.assertIn("public_board_share_url = [скрыто]", text)
        self.assertNotIn("board-secret", text)

        full_text = build_connection_card(
            settings,
            runtime_api_url="http://127.0.0.1:41731",
            runtime_state=McpRuntimeState(False, "", "", ""),
            include_secrets=True,
        )

        self.assertIn(
            "public_board_share_url = https://board.example?access_token=board-secret",
            full_text,
        )

    def test_connection_payloads_tolerate_malformed_urls_without_fake_share_links(self) -> None:
        self.assertEqual(
            build_board_share_url("https://[broken/api", "board-secret"),
            "https://[broken",
        )
        self.assertEqual(build_board_share_url("not-a-url", "board-secret"), "not-a-url")

        settings = IntegrationSettings.from_dict(
            {
                "mcp": {
                    "mcp_enabled": True,
                    "full_mcp_url_override": "https://[broken",
                }
            }
        )

        self.assertEqual(derive_connector_display_name(settings), "AutoStop CRM / This Board Only")

        connector_data = json.loads(build_chatgpt_connector_payload(settings))
        self.assertEqual(connector_data["name"], "AutoStop CRM / This Board Only")
        self.assertEqual(connector_data["connector_url"], "https://[broken")

    def test_connection_card_uses_localized_empty_mcp_token_warning(self) -> None:
        settings = IntegrationSettings.from_dict(
            {
                "mcp": {
                    "mcp_enabled": True,
                    "mcp_auth_mode": "bearer",
                    "mcp_bearer_token": "",
                },
                "auth": {
                    "mcp_bearer_token": "",
                },
            }
        )

        text = build_connection_card(
            settings,
            runtime_api_url="http://127.0.0.1:41731",
            runtime_state=McpRuntimeState(False, "", "", ""),
            include_secrets=False,
        )

        self.assertIn("В MCP выбран bearer, но токен пустой.", text)
        self.assertNotIn("MCP is marked as bearer", text)

    def test_connector_and_responses_payloads_are_built_from_live_settings(self) -> None:
        settings = IntegrationSettings.from_dict(
            {
                "mcp": {
                    "mcp_enabled": True,
                    "public_https_base_url": "https://kanban.example",
                    "mcp_auth_mode": "bearer",
                    "mcp_bearer_token": "mcp-secret",
                },
                "openai": {
                    "model": "gpt-5.4-mini",
                },
                "auth": {
                    "mcp_bearer_token": "mcp-secret",
                },
            }
        )

        self.assertEqual(resolve_connector_auth_mode(settings), "oauth_2_1_pkce")

        connector_payload = build_chatgpt_connector_payload(settings)
        connector_data = json.loads(connector_payload)
        self.assertEqual(
            derive_connector_display_name(settings),
            "AutoStop CRM / This Board Only (kanban.example)",
        )
        self.assertEqual(connector_data["name"], "AutoStop CRM / This Board Only (kanban.example)")
        self.assertEqual(connector_data["connector_url"], "https://kanban.example/mcp")
        self.assertEqual(connector_data["auth_mode"], "oauth_2_1_pkce")
        self.assertIn("Single-board connector", connector_data["description"])
        notes = "\n".join(connector_data["notes"])
        self.assertIn("relevant CRM, Store, and conversation context", notes)
        self.assertIn("ask only a real blocker", notes)
        self.assertIn("Native action confirmation", notes)
        self.assertIn("24 Gateway v2 tools", notes)
        self.assertNotIn("bootstrap_context", notes)
        self.assertEqual(len(MCP_TOOL_NAMES), 24)
        self.assertEqual(GPT_CONNECTOR_REQUIRED_TOOL_NAMES, MCP_TOOL_NAMES)
        self.assertEqual(OPTIONAL_MANAGER_MCP_TOOL_NAMES, [])
        self.assertIn("agent_bootstrap", MCP_TOOL_NAMES)
        self.assertIn("agent_board_digest", MCP_TOOL_NAMES)
        self.assertIn("prepare_action_contract", MCP_TOOL_NAMES)
        self.assertNotIn("bootstrap_context", MCP_TOOL_NAMES)
        self.assertNotIn("update_card", MCP_TOOL_NAMES)

        responses_payload = build_responses_api_payload(settings)
        responses_data = json.loads(responses_payload)
        self.assertEqual(responses_data["model"], "gpt-5.4-mini")
        self.assertEqual(responses_data["tools"][0]["server_url"], "https://kanban.example/mcp")
        self.assertEqual(responses_data["tools"][0]["authorization"], "mcp-secret")
        self.assertEqual(responses_data["tools"][0]["allowed_tools"], MCP_TOOL_NAMES)

    def test_connector_display_name_strips_host_trailing_dot(self) -> None:
        settings = IntegrationSettings.from_dict(
            {
                "mcp": {
                    "mcp_enabled": True,
                    "full_mcp_url_override": "https://Example.COM.:443/mcp",
                }
            }
        )

        connector_data = json.loads(build_chatgpt_connector_payload(settings))

        self.assertEqual(
            derive_connector_display_name(settings),
            "AutoStop CRM / This Board Only (example.com)",
        )
        self.assertEqual(connector_data["name"], "AutoStop CRM / This Board Only (example.com)")

    def test_responses_payload_sanitizes_non_finite_values(self) -> None:
        settings = IntegrationSettings.defaults()

        payload = build_responses_api_payload(
            settings,
            prompt=math.nan,
            allowed_tools=["ping_connector", math.inf],
        )

        self.assertNotIn("NaN", payload)
        self.assertNotIn("Infinity", payload)
        data = json.loads(payload)
        self.assertIsNone(data["input"])
        self.assertEqual(data["tools"][0]["allowed_tools"], ["ping_connector", None])

    def test_connector_auth_falls_back_to_none_when_bearer_token_is_missing(self) -> None:
        settings = IntegrationSettings.from_dict(
            {
                "mcp": {
                    "mcp_enabled": True,
                    "public_https_base_url": "https://kanban.example",
                    "mcp_auth_mode": "bearer",
                    "mcp_bearer_token": "",
                },
                "auth": {
                    "mcp_bearer_token": "",
                },
            }
        )

        self.assertEqual(resolve_connector_auth_mode(settings), "none")

        connector_payload = build_chatgpt_connector_payload(settings)
        connector_data = json.loads(connector_payload)
        self.assertEqual(connector_data["auth_mode"], "none")

        connect_payload = build_chatgpt_connect_payload(
            settings,
            runtime_api_url="http://127.0.0.1:41731",
            runtime_state=McpRuntimeState(True, "http://127.0.0.1:41831/mcp", "running", ""),
        )
        self.assertIn("connector_auth_mode = none", connect_payload)
        self.assertNotIn("mcp_bearer_token =", connect_payload)

    def test_frozen_release_paths_resolve_inside_portable_folder(self) -> None:
        fake_executable = ROOT / "release" / "Start Kanban.exe"

        with (
            patch("minimal_kanban.connection_card.sys.frozen", True, create=True),
            patch("minimal_kanban.connection_card.sys.executable", str(fake_executable)),
        ):
            self.assertEqual(get_project_root(), ROOT / "release")
            self.assertEqual(get_release_exe_path(), fake_executable)
            self.assertEqual(get_mcp_script_path(), fake_executable)
            self.assertEqual(get_mcp_python_entry_path(), fake_executable)
            self.assertEqual(
                get_mcp_setup_doc_path(),
                ROOT / "release" / "CHATGPT_CONNECTOR_SETUP.md",
            )


if __name__ == "__main__":
    unittest.main()
