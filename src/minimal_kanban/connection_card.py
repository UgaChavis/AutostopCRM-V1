from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import get_log_file, get_settings_file, get_state_file
from .integration_runtime import McpRuntimeState
from .models import utc_now_iso
from .settings_models import IntegrationSettings

# Low-level registry retained for local implementation diagnostics only. It is
# never advertised to Codex, ChatGPT, or Responses API clients.
_HIDDEN_RAW_MCP_TOOL_NAMES = [
    "get_connector_identity",
    "ping_connector",
    "bootstrap_context",
    "get_runtime_status",
    "list_columns",
    "create_column",
    "rename_column",
    "delete_column",
    "create_sticky",
    "get_cards",
    "get_card",
    "list_card_attachments",
    "get_card_attachment",
    "read_card_attachment",
    "list_shared_files",
    "get_shared_file_info",
    "download_shared_file",
    "upload_shared_file",
    "delete_shared_file",
    "update_shared_file_position",
    "get_card_context",
    "list_clients",
    "search_clients",
    "get_client",
    "get_client_stats",
    "create_client",
    "update_client",
    "delete_client",
    "link_card_to_client",
    "upsert_client_vehicle",
    "delete_client_vehicle",
    "unlink_card_from_client",
    "suggest_clients_for_card",
    "get_board_snapshot",
    "get_board_context",
    "review_board",
    "manager_board_scan",
    "list_ready_unpaid_cards",
    "triage_inbox_cards",
    "list_cards_missing_manager_data",
    "audit_repair_order_consistency",
    "audit_client_links",
    "bulk_set_deadline_if_below",
    "bulk_refresh_board_summaries",
    "cleanup_card",
    "apply_ready_unpaid_followups",
    "run_manager_operation",
    "rollback_manager_run",
    "list_cashboxes",
    "get_cash_journal",
    "get_cashbox",
    "create_cashbox",
    "delete_cashbox",
    "create_cash_transaction",
    "update_board_settings",
    "get_board_content",
    "get_board_events",
    "get_gpt_wall",
    "get_card_log",
    "list_repair_orders",
    "get_repair_order",
    "get_repair_order_text",
    "download_repair_order_print_pdf",
    "create_document_without_card_pdf",
    "list_archived_cards",
    "search_cards",
    "create_card",
    "update_card",
    "set_card_board_summary",
    "update_repair_order",
    "set_repair_order_status",
    "replace_repair_order_works",
    "replace_repair_order_materials",
    "list_inventory_items",
    "search_inventory_items",
    "get_inventory_item",
    "list_inventory_movements",
    "save_inventory_item",
    "replenish_inventory_item",
    "write_off_inventory_item",
    "return_inventory_movement",
    "update_sticky",
    "move_sticky",
    "delete_sticky",
    "set_card_deadline",
    "set_card_indicator",
    "move_card",
    "mark_card_ready",
    "bulk_move_cards",
    "archive_card",
    "restore_card",
    "list_overdue_cards",
]


def _json_safe_value(value: object, *, depth: int = 8) -> object:
    if depth <= 0:
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item, depth=depth - 1)
            for key, item in value.items()
            if key is not None
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    return str(value)


def _json_dumps(payload: object, *, indent: int = 2) -> str:
    return json.dumps(_json_safe_value(payload), ensure_ascii=False, indent=indent, allow_nan=False)


_HIDDEN_MANAGER_CAPABILITY_NAMES = [
    "remember",
    "recall",
    "learn_from_feedback",
    "recall_lessons",
    "memory_map",
    "memory_topics",
    "memory_context_for",
    "memory_gaps",
    "add_manager_task",
    "today_context",
    "prepare_manager_context",
    "agent_brief",
    "manager_journal",
    "sync_knowledge_base",
    "probe_knowledge_base",
    "search_knowledge_base",
    "audit_knowledge_base",
    "audit_knowledge_annotations",
    "audit_skill_registry",
    "cleanup_audit",
    "system_audit",
    "crm_health_plan",
    "audit_memory",
    "curate_memory",
    "start_manager_run",
    "record_manager_run_event",
    "finish_manager_run",
    "list_manager_runs",
    "estimate_repair_work_cost",
    "lookup_original_parts",
    "recommend_automotive_sources",
    "recommend_fluid_maintenance_sources",
    "recommend_service_management_actions",
]

_HISTORICAL_CONNECTOR_TOOL_NAMES = [
    "ping_connector",
    "bootstrap_context",
    "get_connector_identity",
    "get_runtime_status",
    "get_board_context",
    "get_card_context",
    "get_cards",
    "get_board_snapshot",
    "get_board_content",
    "get_board_events",
    "get_gpt_wall",
    "list_cashboxes",
    "get_cash_journal",
    "list_columns",
    "get_cashbox",
    "create_cashbox",
    "delete_cashbox",
    "create_cash_transaction",
    "list_repair_orders",
    "get_repair_order",
    "get_repair_order_text",
    "download_repair_order_print_pdf",
    "create_document_without_card_pdf",
    "search_cards",
    "get_card",
    "list_card_attachments",
    "get_card_attachment",
    "read_card_attachment",
    "get_card_log",
    "review_board",
    "manager_board_scan",
    "list_ready_unpaid_cards",
    "triage_inbox_cards",
    "list_cards_missing_manager_data",
    "audit_repair_order_consistency",
    "audit_client_links",
    "bulk_set_deadline_if_below",
    "bulk_refresh_board_summaries",
    "cleanup_card",
    "apply_ready_unpaid_followups",
    "run_manager_operation",
    "rollback_manager_run",
    "list_clients",
    "search_clients",
    "get_client",
    "get_client_stats",
    "create_client",
    "update_client",
    "delete_client",
    "link_card_to_client",
    "upsert_client_vehicle",
    "delete_client_vehicle",
    "unlink_card_from_client",
    "suggest_clients_for_card",
    "create_card",
    "update_card",
    "set_card_board_summary",
    "update_repair_order",
    "set_repair_order_status",
    "mark_card_ready",
    "replace_repair_order_works",
    "replace_repair_order_materials",
    "list_inventory_items",
    "search_inventory_items",
    "get_inventory_item",
    "list_inventory_movements",
    "save_inventory_item",
    "replenish_inventory_item",
    "write_off_inventory_item",
    "return_inventory_movement",
    "move_card",
    "bulk_move_cards",
    "archive_card",
    "restore_card",
    "list_archived_cards",
    "list_overdue_cards",
    "delete_column",
    "create_column",
    "rename_column",
    "update_board_settings",
    "create_sticky",
    "update_sticky",
    "move_sticky",
    "delete_sticky",
    "set_card_deadline",
    "set_card_indicator",
]

# This is the sole external tool manifest. Low-level CRM and Manager
# capabilities remain available only through named workflows or guarded raw
# discovery and therefore cannot leak into a stale connector manifest.
MCP_TOOL_NAMES = [
    "agent_board_digest",
    "agent_board_workflow",
    "agent_bootstrap",
    "agent_document_workflow",
    "agent_entity_context",
    "agent_finance_workflow",
    "agent_inventory_workflow",
    "agent_search",
    "call_raw_capability",
    "complete_external_step",
    "discover_raw_capabilities",
    "get_connector_identity",
    "get_raw_capability_schema",
    "get_runtime_status",
    "list_agent_workflows",
    "ping_connector",
    "prepare_action_contract",
    "start_workflow",
    "workflow_cancel",
    "workflow_checkpoint",
    "workflow_resume",
    "workflow_status",
    "workflow_transition",
    "workflow_wait_for_external",
]
GPT_CONNECTOR_REQUIRED_TOOL_NAMES = list(MCP_TOOL_NAMES)
OPTIONAL_MANAGER_MCP_TOOL_NAMES: list[str] = []

CHATGPT_HOME_URL = "https://chatgpt.com/"
OPENAI_MCP_CONNECTORS_GUIDE_URL = (
    "https://developers.openai.com/api/docs/guides/tools-connectors-mcp"
)
OPENAI_APPS_CONNECT_GUIDE_URL = "https://developers.openai.com/apps-sdk/connect-from-chatgpt"
DISPLAY_PRODUCT_NAME = "AutoStop CRM"
SINGLE_BOARD_SCOPE_LABEL = "current AutoStop CRM board only"


def resolve_connector_auth_mode(settings: IntegrationSettings) -> str:
    bearer_enabled = settings.mcp.mcp_auth_mode == "bearer" and bool(
        resolve_mcp_bearer_token(settings)
    )
    return "oauth_2_1_pkce" if bearer_enabled else "none"


def resolve_mcp_bearer_token(settings: IntegrationSettings) -> str:
    return (
        settings.auth.mcp_bearer_token
        or settings.mcp.mcp_bearer_token
        or settings.auth.access_token
        or ""
    ).strip()


def resolve_local_api_bearer_token(settings: IntegrationSettings) -> str:
    return (
        settings.auth.local_api_bearer_token
        or settings.local_api.local_api_bearer_token
        or settings.auth.access_token
        or ""
    ).strip()


def derive_board_root_url(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    try:
        parts = list(urlsplit(text))
    except ValueError:
        if text.endswith("/api"):
            return text[:-4].rstrip("/")
        return text
    if parts[0] in {"http", "https"} and parts[1] and parts[2].rstrip("/") == "/api":
        parts[2] = ""
        return urlunsplit(parts).rstrip("/")
    if text.endswith("/api"):
        return text[:-4].rstrip("/")
    return text


def build_board_share_url(base_url: str, token: str) -> str:
    clean_base = derive_board_root_url(base_url)
    secret = str(token or "").strip()
    if not clean_base or not secret:
        return clean_base
    try:
        parts = list(urlsplit(clean_base))
    except ValueError:
        return clean_base
    if parts[0] not in {"http", "https"} or not parts[1]:
        return clean_base
    query = dict(parse_qsl(parts[3], keep_blank_values=True))
    query["access_token"] = secret
    parts[3] = urlencode(query)
    return urlunsplit(parts)


def derive_connector_display_name(settings: IntegrationSettings) -> str:
    mcp_url = (settings.mcp.effective_mcp_url or "").strip()
    try:
        host = (urlsplit(mcp_url).hostname or "").strip().lower().rstrip(".")
    except ValueError:
        host = ""
    if host:
        return f"{DISPLAY_PRODUCT_NAME} / This Board Only ({host})"
    return f"{DISPLAY_PRODUCT_NAME} / This Board Only"


def build_chatgpt_connect_payload(
    settings: IntegrationSettings,
    *,
    runtime_api_url: str,
    runtime_state: McpRuntimeState | None = None,
) -> str:
    def render_value(value: str) -> str:
        text = str(value or "").strip()
        return text or "<не задан>"

    connector_auth_mode = resolve_connector_auth_mode(settings)
    lines = [
        f"{derive_connector_display_name(settings)} -> ChatGPT / MCP",
        "",
        "Quick links:",
        f"- chatgpt_home = {CHATGPT_HOME_URL}",
        f"- openai_mcp_guide = {OPENAI_MCP_CONNECTORS_GUIDE_URL}",
        f"- openai_apps_guide = {OPENAI_APPS_CONNECT_GUIDE_URL}",
        "",
        "Connection flow:",
        "1. Start the local API and the MCP server.",
        "2. Open a new clean chat in ChatGPT and, when possible, enable only this connector for the session.",
        "3. В ChatGPT откройте настройки, раздел Apps & Connectors, и создайте connector.",
        "4. Add an MCP Server and paste effective_mcp_url.",
        "5. Complete the CRM administrator approval page; credentials are checked by CRM and are not stored by the connector.",
        "6. First call agent_bootstrap, then agent_board_digest.",
        "7. Use agent_search and agent_entity_context to confirm the exact target.",
        "8. Before writes, call prepare_action_contract, then a named workflow in dry_run and apply modes.",
        "9. Reread the exact target and verify the applied result before reporting success.",
        "10. Use discover_raw_capabilities -> get_raw_capability_schema -> call_raw_capability only when no named workflow covers the task.",
        "11. Never invoke or expect a hidden low-level CRM/Manager tool directly; the public manifest must contain exactly 24 Gateway v2 tools.",
        "12. Use get_runtime_status only for auth/runtime diagnostics.",
        "13. Do not move, archive, delete, or change finance/order/file state without exact owner intent.",
    ]
    if connector_auth_mode == "oauth_2_1_pkce":
        lines.extend(
            [
                "17. OAuth 2.1 uses PKCE S256, owner approval, rotating refresh tokens, and exact audience/scopes.",
                "18. ChatGPT/Codex refresh access automatically; relink only after explicit revocation or an unrecoverable client-store loss.",
                "19. Internal bearer compatibility is not included in this ChatGPT payload.",
            ]
        )
    else:
        lines.extend(
            [
                "17. Bearer token is not required because MCP auth is disabled.",
                "18. Press Connect and verify that the tool list is visible.",
            ]
        )

    lines.extend(
        [
            "",
            "[KEY VALUES]",
            f"connector_display_name = {derive_connector_display_name(settings)}",
            f"connector_scope_rule = {SINGLE_BOARD_SCOPE_LABEL}",
            f"connector_auth_mode = {connector_auth_mode}",
            f"effective_mcp_url = {render_value(settings.mcp.effective_mcp_url)}",
            f"local_mcp_url = {render_value(settings.mcp.local_mcp_url)}",
            f"effective_local_api_url = {render_value(settings.local_api.effective_local_api_url)}",
            f"runtime_local_api_url = {render_value(runtime_api_url)}",
            f"runtime_state = {'running' if runtime_state and runtime_state.running else 'stopped'}",
        ]
    )
    lines.extend(["", "[GPT-CRITICAL TOOLS]"])
    lines.extend(f"- {tool}" for tool in GPT_CONNECTOR_REQUIRED_TOOL_NAMES)
    lines.extend(
        [
            "",
            "[RECOMMENDED FIRST PROMPT]",
            "Call agent_bootstrap, then agent_board_digest. Use agent_search and "
            "agent_entity_context for the exact target. Before writes call "
            "prepare_action_contract, then the named workflow in dry_run and apply "
            "modes, and finally reread the exact target. Never call hidden legacy tools.",
            "",
            "[MCP TOOLS]",
        ]
    )
    lines.extend(f"- {tool}" for tool in MCP_TOOL_NAMES)
    lines.extend(
        [
            "",
            "[HIDDEN CAPABILITIES]",
            "AutostopManager and low-level CRM capabilities are routed behind named workflows or guarded raw discovery; they are not extra visible tools.",
        ]
    )
    lines.extend(f"- {tool}" for tool in OPTIONAL_MANAGER_MCP_TOOL_NAMES)
    return "\n".join(lines) + "\n"


def build_chatgpt_connector_payload(settings: IntegrationSettings) -> str:
    payload = {
        "name": derive_connector_display_name(settings),
        "description": (
            "Single-board connector for the current AutoStop CRM board only. "
            "Use it for this local board instance and not for Trello, YouGile, or any other kanban connector."
        ),
        "connector_url": settings.mcp.effective_mcp_url,
        "auth_mode": resolve_connector_auth_mode(settings),
        "notes": [
            "Use the public HTTPS /mcp URL.",
            "Complete the owner-approved OAuth 2.1 authorization flow with PKCE S256.",
            "The public manifest contains exactly 24 Gateway v2 tools and no low-level legacy tools.",
            "Call agent_bootstrap, then agent_board_digest.",
            "Use agent_search and agent_entity_context for the exact live target.",
            "Before a write, call prepare_action_contract and the applicable named workflow in dry_run mode, then apply.",
            "Reread the exact target and verify the result after apply.",
            "Use guarded raw discovery only when no named workflow covers the operation.",
            "Use get_runtime_status only when authentication or runtime state is unclear.",
            "oauth_2_1_pkce uses owner approval and rotating refresh tokens; it is not the old development OAuth mode.",
            "After a schema/manifest change, reconnect the app only if the client does not refresh its cached tool surface.",
        ],
    }
    return _json_dumps(payload) + "\n"


def build_responses_api_payload(
    settings: IntegrationSettings,
    *,
    prompt: str | None = None,
    allowed_tools: list[str] | None = None,
) -> str:
    tool_payload: dict[str, object] = {
        "type": "mcp",
        "server_label": "minimal_kanban",
        "server_url": settings.mcp.effective_mcp_url,
        "allowed_tools": allowed_tools or MCP_TOOL_NAMES,
        "require_approval": "never",
    }
    bearer_token = resolve_mcp_bearer_token(settings)
    if resolve_connector_auth_mode(settings) == "oauth_2_1_pkce" and bearer_token:
        tool_payload["authorization"] = bearer_token

    payload = {
        "model": settings.openai.model,
        "input": prompt or "Покажи просроченные карточки и кратко объясни, что требует внимания.",
        "tools": [tool_payload],
    }
    return _json_dumps(payload) + "\n"


def get_project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _portable_release_entry(project_root: Path) -> Path | None:
    if getattr(sys, "frozen", False):
        return project_root / "Start Kanban.exe"
    direct_release = project_root / "Start Kanban.exe"
    if direct_release.exists():
        return direct_release
    return None


def get_release_exe_path() -> Path:
    project_root = get_project_root()
    direct_release = _portable_release_entry(project_root)
    if direct_release is not None:
        return direct_release
    return project_root / "release" / "Start Kanban.exe"


def get_mcp_script_path() -> Path:
    project_root = get_project_root()
    direct_release = _portable_release_entry(project_root)
    if direct_release is not None:
        return direct_release
    return project_root / "scripts" / "run_mcp_server.ps1"


def get_mcp_python_entry_path() -> Path:
    project_root = get_project_root()
    direct_release = _portable_release_entry(project_root)
    if direct_release is not None:
        return direct_release
    return project_root / "main_mcp.py"


def get_mcp_setup_doc_path() -> Path:
    project_root = get_project_root()
    return project_root / "CHATGPT_CONNECTOR_SETUP.md"


def build_settings_export(settings: IntegrationSettings, *, include_secrets: bool = False) -> str:
    return _json_dumps(
        settings.to_dict(redact_secrets=not include_secrets),
    )


def build_connection_warnings(
    settings: IntegrationSettings, runtime_state: McpRuntimeState | None
) -> list[str]:
    warnings: list[str] = []
    public_board_url = derive_board_root_url(settings.local_api.local_api_base_url_override)
    if not settings.general.integration_enabled:
        warnings.append("Интеграция с GPT и MCP отключена в настройках.")
    if not settings.general.use_local_api:
        warnings.append(
            "Использование локального API отключено. Внешние инструменты могут не увидеть доску."
        )
    if not public_board_url:
        warnings.append(
            "Внешний URL доски не задан. Для удалённого веб-доступа укажите public/tunnel URL в override локального API."
        )
    elif settings.local_api.local_api_auth_mode != "bearer":
        warnings.append(
            "Внешний URL доски задан, но bearer-защита отключена. Для интернет-доступа это небезопасно."
        )
    if not settings.mcp.mcp_enabled:
        warnings.append("MCP отключён. Для ChatGPT его нужно включить и запустить.")
    if (
        not settings.mcp.public_https_base_url
        and not settings.mcp.tunnel_url
        and not settings.mcp.full_mcp_url_override
    ):
        warnings.append("Не задан внешний HTTPS URL. ChatGPT не сможет подключиться к localhost.")
    if settings.mcp.effective_mcp_url.startswith(
        "http://127.0.0.1"
    ) or settings.mcp.effective_mcp_url.startswith("http://localhost"):
        warnings.append(
            "Итоговый MCP URL локальный. Для удалённого подключения нужен внешний HTTPS endpoint."
        )
    if settings.mcp.mcp_auth_mode == "bearer" and not resolve_mcp_bearer_token(settings):
        warnings.append(
            "В MCP выбран bearer, но токен пустой. Endpoint фактически работает без MCP-авторизации, пока bearer token не настроен."
        )
    if runtime_state is not None and runtime_state.error:
        warnings.append(f"Последняя ошибка MCP runtime: {runtime_state.error}")
    return warnings


def build_connection_card(
    settings: IntegrationSettings,
    *,
    runtime_api_url: str,
    runtime_state: McpRuntimeState | None = None,
    include_secrets: bool = False,
) -> str:
    def render_secret(value: str) -> str:
        text = (value or "").strip()
        if not text:
            return "<не задан>"
        return text if include_secrets else "[скрыто]"

    warnings = build_connection_warnings(settings, runtime_state)
    runtime_mcp_url = (
        runtime_state.runtime_url
        if runtime_state and runtime_state.running
        else settings.mcp.local_mcp_url
    )
    public_board_url = derive_board_root_url(settings.local_api.local_api_base_url_override)
    local_api_token = resolve_local_api_bearer_token(settings)
    if public_board_url and local_api_token and include_secrets:
        public_board_share_url = build_board_share_url(public_board_url, local_api_token)
    elif public_board_url and local_api_token:
        public_board_share_url = "[скрыто]"
    else:
        public_board_share_url = public_board_url
    connector_auth_mode = resolve_connector_auth_mode(settings)

    lines = [
        "AUTOSTOP CRM — КАРТОЧКА ПОДКЛЮЧЕНИЯ GPT / MCP",
        "",
        f"exported_at = {utc_now_iso()}",
        "",
        "[PROJECT]",
        "name = AutoStop CRM",
        f"app_exe = {get_release_exe_path()}",
        f"mcp_entry_ps1 = {get_mcp_script_path()}",
        f"mcp_entry_py = {get_mcp_python_entry_path()}",
        f"connector_setup_doc = {get_mcp_setup_doc_path()}",
        "",
        "[FILES]",
        f"settings_json = {get_settings_file()}",
        f"state_json = {get_state_file()}",
        f"log_file = {get_log_file()}",
        "",
        "[LOCAL API]",
        f"local_api_host = {settings.local_api.local_api_host}",
        f"local_api_port = {settings.local_api.local_api_port}",
        f"runtime_local_api_url = {settings.local_api.runtime_local_api_url}",
        f"current_runtime_api_url = {runtime_api_url}",
        f"local_api_base_url_override = {settings.local_api.local_api_base_url_override or '<не задан>'}",
        f"public_board_url = {public_board_url or '<не задан>'}",
        f"public_board_share_url = {public_board_share_url or '<не задан>'}",
        f"effective_local_api_url = {settings.local_api.effective_local_api_url}",
        f"local_api_health_url = {settings.local_api.local_api_health_url}",
        f"local_api_auth_mode = {settings.local_api.local_api_auth_mode}",
        f"local_api_bearer_token = {render_secret(resolve_local_api_bearer_token(settings))}",
        "",
        "[MCP]",
        f"connector_display_name = {derive_connector_display_name(settings)}",
        f"connector_scope_rule = {SINGLE_BOARD_SCOPE_LABEL}",
        f"connector_auth_mode = {connector_auth_mode}",
        f"mcp_enabled = {str(settings.mcp.mcp_enabled).lower()}",
        f"mcp_host = {settings.mcp.mcp_host}",
        f"mcp_port = {settings.mcp.mcp_port}",
        f"mcp_path = {settings.mcp.mcp_path}",
        f"local_mcp_url = {settings.mcp.local_mcp_url}",
        f"current_runtime_mcp_url = {runtime_mcp_url}",
        f"public_https_base_url = {settings.mcp.public_https_base_url or '<не задан>'}",
        f"tunnel_url = {settings.mcp.tunnel_url or '<не задан>'}",
        f"full_mcp_url_override = {settings.mcp.full_mcp_url_override or '<не задан>'}",
        f"derived_public_mcp_url = {settings.mcp.derived_public_mcp_url or '<не задан>'}",
        f"derived_tunnel_mcp_url = {settings.mcp.derived_tunnel_mcp_url or '<не задан>'}",
        f"effective_mcp_url = {settings.mcp.effective_mcp_url}",
        f"allowed_hosts = {', '.join(settings.mcp.allowed_hosts) or '<авто>'}",
        f"allowed_origins = {', '.join(settings.mcp.allowed_origins) or '<авто>'}",
        f"resolved_allowed_hosts = {', '.join(settings.mcp.resolved_allowed_hosts)}",
        f"resolved_allowed_origins = {', '.join(settings.mcp.resolved_allowed_origins)}",
        f"mcp_auth_mode = {settings.mcp.mcp_auth_mode}",
        f"mcp_bearer_token = {render_secret(resolve_mcp_bearer_token(settings))}",
        f"mcp_runtime_status = {'running' if runtime_state and runtime_state.running else 'stopped'}",
        "",
        "[OPENAI / GPT]",
        f"provider = {settings.openai.provider}",
        f"model = {settings.openai.model}",
        f"base_url = {settings.openai.base_url}",
        f"organization_id = {settings.openai.organization_id or '<не задан>'}",
        f"project_id = {settings.openai.project_id or '<не задан>'}",
        f"timeout_seconds = {settings.openai.timeout_seconds}",
        f"openai_api_key = {render_secret(settings.auth.openai_api_key)}",
        f"access_token = {render_secret(settings.auth.access_token)}",
        "",
        "[AUTH]",
        f"auth_mode = {settings.auth.auth_mode}",
        "",
        "[MCP TOOLS]",
    ]
    lines.extend(f"- {tool}" for tool in MCP_TOOL_NAMES)
    lines.extend(
        [
            "",
            "[CHECKLIST]",
            "1. Запустить приложение.",
            "2. Открыть раздел Интеграция / GPT / MCP в настройках приложения.",
            "3. Проверить локальный API.",
            "4. При интернет-доступе указать public/tunnel URL доски в override локального API.",
            "5. Скопировать public_board_url или public_board_share_url.",
            "6. Проверить локальный MCP.",
            "7. Проверить внешний endpoint MCP.",
            "8. Скопировать effective_mcp_url.",
            "9. Открыть новый чистый чат в ChatGPT и подключить только этот коннектор, если это возможно.",
            "10. Вызвать agent_bootstrap, затем agent_board_digest.",
            "11. Найти и перечитать точную цель через agent_search/agent_entity_context.",
            "12. Перед записью: prepare_action_contract и named workflow dry_run/apply.",
            "13. После apply обязательно перечитать цель и проверить результат.",
            "14. ChatGPT/Codex используют OAuth 2.1 PKCE; внутренний bearer в connector payload не передаётся.",
            "15. Внешняя поверхность должна содержать ровно 24 Gateway v2 tools без legacy names.",
            "",
            "[WARNINGS]",
        ]
    )
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- Критичных предупреждений нет.")
    return "\n".join(lines) + "\n"
