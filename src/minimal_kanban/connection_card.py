from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import get_log_file, get_settings_file, get_state_file
from .integration_runtime import McpRuntimeState
from .models import utc_now_iso
from .settings_models import IntegrationSettings

MCP_TOOL_NAMES = [
    "get_connector_identity",
    "ping_connector",
    "bootstrap_context",
    "get_runtime_status",
    "cleanup_card_content",
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
    "list_archived_cards",
    "search_cards",
    "create_card",
    "update_card",
    "update_repair_order",
    "set_repair_order_status",
    "replace_repair_order_works",
    "replace_repair_order_materials",
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

GPT_CONNECTOR_REQUIRED_TOOL_NAMES = [
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
    "cleanup_card_content",
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
    "search_cards",
    "get_card",
    "list_card_attachments",
    "get_card_attachment",
    "read_card_attachment",
    "get_card_log",
    "review_board",
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
    "update_repair_order",
    "set_repair_order_status",
    "mark_card_ready",
    "replace_repair_order_works",
    "replace_repair_order_materials",
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
    return "oauth_embedded" if bearer_enabled else "none"


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
    if text.endswith("/api"):
        return text[:-4].rstrip("/")
    return text


def build_board_share_url(base_url: str, token: str) -> str:
    clean_base = derive_board_root_url(base_url)
    secret = str(token or "").strip()
    if not clean_base or not secret:
        return clean_base
    parts = list(urlsplit(clean_base))
    query = dict(parse_qsl(parts[3], keep_blank_values=True))
    query["access_token"] = secret
    parts[3] = urlencode(query)
    return urlunsplit(parts)


def derive_connector_display_name(settings: IntegrationSettings) -> str:
    mcp_url = (settings.mcp.effective_mcp_url or "").strip()
    host = (urlsplit(mcp_url).hostname or "").strip().lower()
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
    token = resolve_mcp_bearer_token(settings)
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
        "3. Open ChatGPT -> Settings -> Apps & Connectors -> Create.",
        "4. Add an MCP Server and paste effective_mcp_url.",
        "5. First call inside ChatGPT should be ping_connector.",
        "6. Second call inside ChatGPT should be bootstrap_context.",
        "7. If diagnostics are needed, call get_runtime_status before any write operation.",
        "7a. Use MCP tool names directly, not resource URLs. If metadata shows /AutoStopCRM/link_.../tool_name, normalize it internally to /AutoStopCRM/tool_name.",
        "8. If the task is about a vehicle card, call get_card_context first for the focused read; if the exact card_id is still unclear or get_card_context misses the target, escalate to get_board_snapshot(compact=true) or get_board_content(include_archived=true) before retrying. Use get_board_events(event_limit=100) for the latest journal, or get_gpt_wall only when both hidden machine wall sections are needed in one response and you accept a compacted agent payload.",
        "8a. If the task is about operational load, overdue work, stale cards, or manager triage, call review_board before reading the full wall.",
        "8b. For wide board scans prefer get_cards(compact=true) or get_board_snapshot(compact=true) before switching to full/export tools.",
        "8c. For client work, call suggest_clients_for_card(card_id) or search_clients before create_client. Clients support up to 3 phone numbers in phones[]; phone is always the first/main one, and search_clients matches any saved phone. Choose a concrete vehicles_preview[].id when known and pass it as client_vehicle_id to link_card_to_client; use upsert_client_vehicle to add or correct a client's car; use delete_client_vehicle only when the operator wants to remove a car from the client profile without deleting cards. Cards may stay unlinked when the customer is a one-off manual entry. delete_client is destructive and rejects linked cards unless allow_linked=true is explicitly confirmed.",
        "9. The compact 1.1 vehicle profile for GPT should focus on make_display, model_display, production_year, vin, engine_model, gearbox_model, drivetrain, and oem_notes.",
        "10. Do not ask GPT to edit cards until it confirms connector_name, resource_url, board_name, and scope_rule.",
        "11. For mass column migrations, use bulk_move_cards instead of many sequential move_card calls.",
        "11b. If the operator says a vehicle is ready, call mark_card_ready; do not close the repair order unless payment/closure is explicitly requested.",
        "11a. create_column accepts label, and the name alias is also supported for compatibility.",
        "12. rename_column changes only the label and keeps the same column id.",
        "13. delete_column can remove only an empty column; if cards still point to it, clear that column first.",
    ]
    if connector_auth_mode == "oauth_embedded":
        lines.extend(
            [
                "17. For the ChatGPT connector you normally do not need to paste a bearer token manually: the server exposes embedded OAuth / DCR metadata.",
                "18. If ChatGPT asks for linking, complete the embedded OAuth flow.",
                "19. The legacy bearer token below is only for Responses API or a manual MCP client.",
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
    if connector_auth_mode == "oauth_embedded":
        lines.append(f"mcp_bearer_token = {render_value(token)}")

    lines.extend(["", "[GPT-CRITICAL TOOLS]"])
    lines.extend(f"- {tool}" for tool in GPT_CONNECTOR_REQUIRED_TOOL_NAMES)
    lines.extend(
        [
            "",
            "[RECOMMENDED FIRST PROMPT]",
            "First call ping_connector. "
            "Then call bootstrap_context. "
            "If there is any doubt about the tunnel, auth, or runtime state, call get_runtime_status. "
            "Briefly confirm connector_name, resource_url, board_name, and scope_rule. "
            "Do not modify any data until that confirmation is complete.",
            "",
            "[MCP TOOLS]",
        ]
    )
    lines.extend(f"- {tool}" for tool in MCP_TOOL_NAMES)
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
            "Open a new clean chat when validating this connector.",
            "First call should be ping_connector.",
            "Second call should be bootstrap_context.",
            "Use MCP tool names directly, not resource URLs. If metadata shows /AutoStopCRM/link_.../tool_name, normalize it internally to /AutoStopCRM/tool_name.",
            "For vehicle cards, call get_board_snapshot first when you need a quick board-wide fallback, or get_board_content when broad context or recent history matters; use get_gpt_wall only when you need both hidden machine wall sections in one response and a compact agent dump is acceptable.",
            "For vehicle cards, call get_board_events when you need the latest change journal in Markdown.",
            "When creating or updating a card, keep vehicle limited to make/model only, and keep title limited to the short essence of the issue, task, or result.",
            "The 1.1 compact vehicle profile should focus on make_display, model_display, production_year, vin, engine_model, gearbox_model, drivetrain, and oem_notes.",
            "For client work, use suggest_clients_for_card or search_clients before create_client; clients can store up to 3 phones in phones[] with phone as the first/main number; select a concrete client vehicle when possible; use upsert_client_vehicle for a new or corrected vehicle of an existing client; use delete_client_vehicle to remove a car from the client profile without deleting cards; link_card_to_client should not overwrite manual client fields unless the operator confirms it; delete_client is destructive and should not be used on linked clients without explicit confirmation.",
            "rename_column changes only the label and keeps the same column id.",
            "delete_column removes only empty columns; if a column still contains cards, move or archive them first.",
            "vehicle_profile supports manual fields, source_summary, source_confidence, source_links_or_refs, data_completion_state, OCR traces, and per-field provenance.",
            "Manual vehicle profile corrections must not be silently overwritten by later enrichment attempts.",
            "An image payload remains an optional fallback for external clients, but the main 1.1 UI flow is card_content_first.",
            "Use get_runtime_status when the tunnel, auth, or runtime state is unclear.",
            "For mass card migrations prefer bulk_move_cards over many sequential move_card calls.",
            "If the operator says a vehicle is ready, call mark_card_ready; do not close the repair order unless payment/closure is explicitly requested.",
            "oauth_embedded means built-in OAuth/DCR for the ChatGPT connector.",
            "none means an open MCP endpoint without auth.",
            "After MCP schema, tool, or metadata changes, delete and recreate the ChatGPT connector to refresh the manifest safely.",
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


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
    if resolve_connector_auth_mode(settings) == "oauth_embedded" and bearer_token:
        tool_payload["authorization"] = bearer_token

    payload = {
        "model": settings.openai.model,
        "input": prompt or "Покажи просроченные карточки и кратко объясни, что требует внимания.",
        "tools": [tool_payload],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


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
    return json.dumps(
        settings.to_dict(redact_secrets=not include_secrets),
        ensure_ascii=False,
        indent=2,
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
            "MCP is marked as bearer, but the token is empty. The endpoint effectively runs without MCP auth until a bearer token is configured."
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
    public_board_share_url = (
        build_board_share_url(public_board_url, resolve_local_api_bearer_token(settings))
        if public_board_url
        else ""
    )
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
            "2. Открыть Settings -> Integration / GPT / MCP.",
            "3. Проверить локальный API.",
            "4. При интернет-доступе указать public/tunnel URL доски в override локального API.",
            "5. Скопировать public_board_url или public_board_share_url.",
            "6. Проверить локальный MCP.",
            "7. Проверить внешний endpoint MCP.",
            "8. Скопировать effective_mcp_url.",
            "9. Открыть новый чистый чат в ChatGPT и подключить только этот коннектор, если это возможно.",
            "10. Первый вызов: bootstrap_context.",
            "11. Если есть сомнения по tunnel/auth/runtime, вызвать get_runtime_status.",
            "12. Только после подтверждения identity/scope просить GPT менять данные.",
            "13. Для ChatGPT connector при connector_auth_mode = oauth_embedded bearer token вручную обычно не нужен.",
            "14. Для Responses API или ручных MCP клиентов можно использовать mcp_bearer_token.",
            "15. После изменения набора MCP tools безопаснее удалить приложение в ChatGPT и создать его заново.",
            "",
            "[WARNINGS]",
        ]
    )
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- Критичных предупреждений нет.")
    return "\n".join(lines) + "\n"
