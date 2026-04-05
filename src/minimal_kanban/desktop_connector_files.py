from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit


CONNECTION_CARD_FILENAME = "GPT_MCP_CONNECTION_CARD.txt"
CONNECTOR_JSON_FILENAME = "chatgpt-connector.json"
AUTH_NOTE_FILENAME = "Minimal Kanban Auth Note.txt"
URL_FILENAME = "Minimal Kanban URL.txt"
WAITING_MESSAGE = ""


def _resolve_desktop_path(desktop_path: Path | None = None) -> Path:
    return Path(desktop_path) if desktop_path is not None else Path.home() / "Desktop"


def _write_text_no_bom(path: Path, value: str) -> None:
    path.write_bytes(value.encode("utf-8"))


def _normalized_auth_mode(auth_mode: str) -> str:
    return "bearer" if str(auth_mode or "").strip().lower() == "bearer" else "none"


def _connector_auth_label(auth_mode: str) -> str:
    return "Bearer token" if _normalized_auth_mode(auth_mode) == "bearer" else "No authentication"


def build_connector_file_contents(mcp_url: str, local_api_url: str, *, auth_mode: str = "none") -> dict[str, str]:
    normalized_mcp_url = str(mcp_url or "").strip()
    normalized_local_api_url = str(local_api_url or "").strip()
    normalized_auth_mode = _normalized_auth_mode(auth_mode)
    auth_label = _connector_auth_label(normalized_auth_mode)
    host_label = (urlsplit(normalized_mcp_url).hostname or "current-connector").strip().lower() or "current-connector"

    connection_card = (
        f"Minimal Kanban / This Board Only ({host_label}) -> ChatGPT / MCP\n\n"
        "[KEY VALUES]\n"
        f"connector_auth_mode = {normalized_auth_mode}\n"
        f"effective_mcp_url = {normalized_mcp_url}\n"
        f"effective_local_api_url = {normalized_local_api_url}\n\n"
        "Connection flow:\n"
        "1. Start the app from the desktop shortcut.\n"
        "2. Open ChatGPT -> Settings -> Apps & Connectors -> Connectors -> Create.\n"
        "3. Paste effective_mcp_url.\n"
        f"4. Choose {auth_label}.\n"
        "5. Create the connector.\n"
        "6. In a new chat call ping_connector, then bootstrap_context.\n"
    )
    connector_payload = {
        "name": f"Minimal Kanban / This Board Only ({host_label})",
        "description": "Single-board connector for the current Minimal Kanban board only.",
        "connector_url": normalized_mcp_url,
        "auth_mode": normalized_auth_mode,
        "notes": [
            "Use the public HTTPS /mcp URL.",
            f"Authentication mode: {auth_label}.",
            "First call should be ping_connector.",
            "Second call should be bootstrap_context.",
        ],
    }
    auth_note = (
        "ChatGPT connector\n\n"
        "URL:\n"
        f"{normalized_mcp_url}\n\n"
        "Authentication:\n"
        f"{auth_label}\n\n"
        "First checks:\n"
        "1. ping_connector\n"
        "2. bootstrap_context\n"
    )
    return {
        CONNECTION_CARD_FILENAME: connection_card,
        CONNECTOR_JSON_FILENAME: json.dumps(connector_payload, ensure_ascii=False, indent=2) + "\n",
        AUTH_NOTE_FILENAME: auth_note,
        URL_FILENAME: normalized_mcp_url,
    }


def build_pending_connector_file_contents(*, auth_mode: str = "none", local_api_url: str = "http://127.0.0.1:41731") -> dict[str, str]:
    normalized_auth_mode = _normalized_auth_mode(auth_mode)
    auth_label = _connector_auth_label(normalized_auth_mode)
    normalized_local_api_url = str(local_api_url or "").strip() or "http://127.0.0.1:41731"
    return {
        CONNECTION_CARD_FILENAME: (
            "Minimal Kanban / This Board Only (current-connector) -> ChatGPT / MCP\n\n"
            "[KEY VALUES]\n"
            f"connector_auth_mode = {normalized_auth_mode}\n"
            "effective_mcp_url = \n"
            f"effective_local_api_url = {normalized_local_api_url}\n\n"
            "Connection flow:\n"
            "1. Start the app from the desktop shortcut.\n"
            "2. Open ChatGPT -> Settings -> Apps & Connectors -> Connectors -> Create.\n"
            "3. Paste effective_mcp_url after the public HTTPS MCP URL appears.\n"
            f"4. Choose {auth_label}.\n"
            "5. Create the connector.\n"
            "6. In a new chat call ping_connector, then bootstrap_context.\n"
        ),
        CONNECTOR_JSON_FILENAME: (
            '{\n'
            '  "name": "Minimal Kanban / This Board Only (current-connector)",\n'
            '  "description": "Single-board connector for the current Minimal Kanban board only.",\n'
            '  "connector_url": "",\n'
            f'  "auth_mode": "{normalized_auth_mode}",\n'
            '  "notes": [\n'
            '    "Wait for the public HTTPS /mcp URL to appear.",\n'
            f'    "Authentication mode: {auth_label}.",\n'
            '    "First call should be ping_connector.",\n'
            '    "Second call should be bootstrap_context."\n'
            '  ]\n'
            '}'
        ),
        AUTH_NOTE_FILENAME: (
            "ChatGPT connector\n\n"
            "URL:\n\n\n"
            "Authentication:\n"
            f"{auth_label}\n\n"
            "First checks:\n"
            "1. ping_connector\n"
            "2. bootstrap_context\n"
        ),
        URL_FILENAME: WAITING_MESSAGE,
    }


def write_connector_files(
    mcp_url: str,
    local_api_url: str,
    *,
    auth_mode: str = "none",
    desktop_path: Path | None = None,
) -> dict[str, Path]:
    target_directory = _resolve_desktop_path(desktop_path)
    target_directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for filename, content in build_connector_file_contents(mcp_url, local_api_url, auth_mode=auth_mode).items():
        path = target_directory / filename
        _write_text_no_bom(path, content)
        written[filename] = path
    return written


def write_pending_connector_files(
    *,
    auth_mode: str = "none",
    local_api_url: str = "http://127.0.0.1:41731",
    desktop_path: Path | None = None,
) -> dict[str, Path]:
    target_directory = _resolve_desktop_path(desktop_path)
    target_directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for filename, content in build_pending_connector_file_contents(
        auth_mode=auth_mode,
        local_api_url=local_api_url,
    ).items():
        path = target_directory / filename
        _write_text_no_bom(path, content)
        written[filename] = path
    return written
