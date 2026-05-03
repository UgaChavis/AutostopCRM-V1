from __future__ import annotations

# ruff: noqa: E402
import json
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
        self.assertIn("mcp_bearer_token = mcp-secret", text)
        self.assertIn(
            "connector_display_name = AutoStop CRM / This Board Only (public.example)", text
        )
        self.assertIn("connector_scope_rule = current AutoStop CRM board only", text)
        self.assertIn("chatgpt_home = https://chatgpt.com/", text)
        self.assertIn("[GPT-CRITICAL TOOLS]", text)
        self.assertIn("- ping_connector", text)
        self.assertIn("- bootstrap_context", text)
        self.assertIn("- get_connector_identity", text)
        self.assertIn("- get_runtime_status", text)
        self.assertIn("- get_board_context", text)
        self.assertIn("- get_board_content", text)
        self.assertIn("- get_board_events", text)
        self.assertIn("- get_gpt_wall", text)
        self.assertNotIn("- cleanup_card_content", text)
        self.assertIn("- get_card_context", text)
        self.assertIn("- list_columns", text)
        self.assertIn("- rename_column", text)
        self.assertIn("- delete_column", text)
        self.assertIn("- bulk_move_cards", text)
        self.assertIn("- search_clients", text)
        self.assertIn("- suggest_clients_for_card", text)
        self.assertIn("- link_card_to_client", text)
        self.assertNotIn("autofill_repair_order", text)
        self.assertIn("[RECOMMENDED FIRST PROMPT]", text)
        self.assertIn("First call ping_connector.", text)
        self.assertIn("Then call bootstrap_context.", text)
        self.assertIn("call get_runtime_status", text)
        self.assertIn("Use MCP tool names directly, not resource URLs", text)
        self.assertIn("call get_card_context first for the focused read", text)
        self.assertIn("get_board_content(include_archived=true)", text)
        self.assertIn("get_board_events(event_limit=100)", text)
        self.assertIn("get_board_snapshot(compact=true)", text)
        self.assertIn(
            "make_display, model_display, production_year, vin, engine_model, gearbox_model, drivetrain, and oem_notes",
            text,
        )
        self.assertIn("bulk_move_cards instead of many sequential move_card calls", text)
        self.assertIn("rename_column changes only the label and keeps the same column id", text)

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
        self.assertIn(
            "public_board_share_url = https://board.example?access_token=board-secret", text
        )

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

        self.assertEqual(resolve_connector_auth_mode(settings), "oauth_embedded")

        connector_payload = build_chatgpt_connector_payload(settings)
        connector_data = json.loads(connector_payload)
        self.assertEqual(
            derive_connector_display_name(settings),
            "AutoStop CRM / This Board Only (kanban.example)",
        )
        self.assertEqual(connector_data["name"], "AutoStop CRM / This Board Only (kanban.example)")
        self.assertEqual(connector_data["connector_url"], "https://kanban.example/mcp")
        self.assertEqual(connector_data["auth_mode"], "oauth_embedded")
        self.assertIn("Single-board connector", connector_data["description"])
        self.assertTrue(any("ping_connector" in note for note in connector_data["notes"]))
        self.assertTrue(any("bootstrap_context" in note for note in connector_data["notes"]))
        self.assertTrue(
            any(
                "Use MCP tool names directly, not resource URLs" in note
                for note in connector_data["notes"]
            )
        )
        self.assertTrue(any("get_board_content" in note for note in connector_data["notes"]))
        self.assertTrue(any("get_board_events" in note for note in connector_data["notes"]))
        self.assertTrue(any("search_clients" in note for note in connector_data["notes"]))
        self.assertTrue(any("link_card_to_client" in note for note in connector_data["notes"]))
        self.assertTrue(any("delete_column" in note for note in connector_data["notes"]))
        self.assertTrue(any("bulk_move_cards" in note for note in connector_data["notes"]))
        self.assertTrue(any("card_content_first" in note for note in connector_data["notes"]))
        self.assertTrue(any("oem_notes" in note for note in connector_data["notes"]))
        self.assertTrue(any("delete and recreate" in note for note in connector_data["notes"]))
        self.assertTrue(any("make/model only" in note for note in connector_data["notes"]))
        self.assertTrue(
            any(
                "short essence of the issue, task, or result" in note
                for note in connector_data["notes"]
            )
        )
        self.assertEqual(len(MCP_TOOL_NAMES), 64)
        self.assertIn("get_board_content", MCP_TOOL_NAMES)
        self.assertIn("get_board_events", MCP_TOOL_NAMES)
        self.assertNotIn("cleanup_card_content", MCP_TOOL_NAMES)
        self.assertNotIn("autofill_repair_order", MCP_TOOL_NAMES)
        self.assertNotIn("autofill_vehicle_data", MCP_TOOL_NAMES)
        self.assertIn("get_card_context", MCP_TOOL_NAMES)
        self.assertIn("list_card_attachments", MCP_TOOL_NAMES)
        self.assertIn("get_card_attachment", MCP_TOOL_NAMES)
        self.assertIn("read_card_attachment", MCP_TOOL_NAMES)
        self.assertIn("list_clients", MCP_TOOL_NAMES)
        self.assertIn("search_clients", MCP_TOOL_NAMES)
        self.assertIn("get_client", MCP_TOOL_NAMES)
        self.assertIn("get_client_stats", MCP_TOOL_NAMES)
        self.assertIn("create_client", MCP_TOOL_NAMES)
        self.assertIn("update_client", MCP_TOOL_NAMES)
        self.assertIn("delete_client", MCP_TOOL_NAMES)
        self.assertIn("link_card_to_client", MCP_TOOL_NAMES)
        self.assertIn("upsert_client_vehicle", MCP_TOOL_NAMES)
        self.assertIn("delete_client_vehicle", MCP_TOOL_NAMES)
        self.assertIn("unlink_card_from_client", MCP_TOOL_NAMES)
        self.assertIn("suggest_clients_for_card", MCP_TOOL_NAMES)
        self.assertIn("get_repair_order_text", MCP_TOOL_NAMES)
        self.assertIn("mark_card_ready", MCP_TOOL_NAMES)
        self.assertIn("set_card_board_summary", MCP_TOOL_NAMES)
        self.assertIn("replace_repair_order_works", MCP_TOOL_NAMES)
        self.assertIn("replace_repair_order_materials", MCP_TOOL_NAMES)
        self.assertTrue(set(GPT_CONNECTOR_REQUIRED_TOOL_NAMES).issubset(set(MCP_TOOL_NAMES)))

        responses_payload = build_responses_api_payload(settings)
        responses_data = json.loads(responses_payload)
        self.assertEqual(responses_data["model"], "gpt-5.4-mini")
        self.assertEqual(responses_data["tools"][0]["server_url"], "https://kanban.example/mcp")
        self.assertEqual(responses_data["tools"][0]["authorization"], "mcp-secret")
        self.assertIn("delete_column", responses_data["tools"][0]["allowed_tools"])
        self.assertIn("bulk_move_cards", responses_data["tools"][0]["allowed_tools"])
        self.assertIn("search_clients", responses_data["tools"][0]["allowed_tools"])
        self.assertIn("link_card_to_client", responses_data["tools"][0]["allowed_tools"])
        self.assertIn("upsert_client_vehicle", responses_data["tools"][0]["allowed_tools"])

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
                get_mcp_setup_doc_path(), ROOT / "release" / "CHATGPT_CONNECTOR_SETUP.md"
            )


if __name__ == "__main__":
    unittest.main()
