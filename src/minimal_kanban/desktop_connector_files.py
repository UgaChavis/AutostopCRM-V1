from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from urllib.parse import urlsplit

CONNECTION_CARD_FILENAME = "GPT_MCP_CONNECTION_CARD.txt"
CONNECTOR_JSON_FILENAME = "chatgpt-connector.json"
AUTH_NOTE_FILENAME = "AutoStop CRM Auth Note.txt"
URL_FILENAME = "AutoStop CRM URL.txt"
WAITING_MESSAGE = ""
DISPLAY_PRODUCT_NAME = "AutoStop CRM"
DESKTOP_CONNECTOR_FILE_MAX_BYTES = 256 * 1024
CONNECTOR_AUTH_LABELS = {
    "none": "No authentication",
    "bearer": "Bearer token",
    "oauth_embedded": "Embedded OAuth / DCR",
}


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


def _resolve_desktop_path(desktop_path: Path | None = None) -> Path:
    return Path(desktop_path) if desktop_path is not None else Path.home() / "Desktop"


def _write_text_no_bom(path: Path, value: str) -> None:
    payload = value.encode("utf-8")
    if len(payload) > DESKTOP_CONNECTOR_FILE_MAX_BYTES:
        raise ValueError(
            f"desktop connector file is too large ({DESKTOP_CONNECTOR_FILE_MAX_BYTES} byte limit)"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_bytes(payload)
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _normalized_auth_mode(auth_mode: str) -> str:
    normalized = str(auth_mode or "").strip().lower()
    return normalized if normalized in CONNECTOR_AUTH_LABELS else "none"


def _connector_auth_label(auth_mode: str) -> str:
    return CONNECTOR_AUTH_LABELS[_normalized_auth_mode(auth_mode)]


def _connector_host_label(value: str) -> str:
    try:
        host = urlsplit(str(value or "").strip()).hostname or ""
    except ValueError:
        host = ""
    return host.strip().lower().rstrip(".") or "current-connector"


def build_connector_file_contents(
    mcp_url: str, local_api_url: str, *, auth_mode: str = "none"
) -> dict[str, str]:
    normalized_mcp_url = str(mcp_url or "").strip()
    normalized_local_api_url = str(local_api_url or "").strip()
    normalized_auth_mode = _normalized_auth_mode(auth_mode)
    auth_label = _connector_auth_label(normalized_auth_mode)
    host_label = _connector_host_label(normalized_mcp_url)

    connection_card = (
        f"{DISPLAY_PRODUCT_NAME} / This Board Only ({host_label}) -> ChatGPT / MCP\n\n"
        "[KEY VALUES]\n"
        f"connector_auth_mode = {normalized_auth_mode}\n"
        f"effective_mcp_url = {normalized_mcp_url}\n"
        f"effective_local_api_url = {normalized_local_api_url}\n\n"
        "Connection flow:\n"
        "1. Запустите приложение с ярлыка.\n"
        "2. В ChatGPT откройте настройки, раздел Apps & Connectors, и создайте connector.\n"
        "3. Вставьте effective_mcp_url.\n"
        f"4. Выберите режим {auth_label}.\n"
        "5. Создайте connector.\n"
        "6. В новом чате вызовите ping_connector, затем bootstrap_context.\n"
    )
    connector_payload = {
        "name": f"{DISPLAY_PRODUCT_NAME} / This Board Only ({host_label})",
        "description": "Single-board connector for the current AutoStop CRM board only.",
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
        CONNECTOR_JSON_FILENAME: _json_dumps(connector_payload) + "\n",
        AUTH_NOTE_FILENAME: auth_note,
        URL_FILENAME: normalized_mcp_url,
    }


def build_pending_connector_file_contents(
    *, auth_mode: str = "none", local_api_url: str = "http://127.0.0.1:41731"
) -> dict[str, str]:
    normalized_auth_mode = _normalized_auth_mode(auth_mode)
    auth_label = _connector_auth_label(normalized_auth_mode)
    normalized_local_api_url = str(local_api_url or "").strip() or "http://127.0.0.1:41731"
    return {
        CONNECTION_CARD_FILENAME: (
            f"{DISPLAY_PRODUCT_NAME} / This Board Only (current-connector) -> ChatGPT / MCP\n\n"
            "[KEY VALUES]\n"
            f"connector_auth_mode = {normalized_auth_mode}\n"
            "effective_mcp_url = \n"
            f"effective_local_api_url = {normalized_local_api_url}\n\n"
            "Connection flow:\n"
            "1. Запустите приложение с ярлыка.\n"
            "2. В ChatGPT откройте настройки, раздел Apps & Connectors, и создайте connector.\n"
            "3. Вставьте effective_mcp_url после появления публичного HTTPS MCP URL.\n"
            f"4. Выберите режим {auth_label}.\n"
            "5. Создайте connector.\n"
            "6. В новом чате вызовите ping_connector, затем bootstrap_context.\n"
        ),
        CONNECTOR_JSON_FILENAME: (
            "{\n"
            f'  "name": "{DISPLAY_PRODUCT_NAME} / This Board Only (current-connector)",\n'
            '  "description": "Single-board connector for the current AutoStop CRM board only.",\n'
            '  "connector_url": "",\n'
            f'  "auth_mode": "{normalized_auth_mode}",\n'
            '  "notes": [\n'
            '    "Wait for the public HTTPS /mcp URL to appear.",\n'
            f'    "Authentication mode: {auth_label}.",\n'
            '    "First call should be ping_connector.",\n'
            '    "Second call should be bootstrap_context."\n'
            "  ]\n"
            "}"
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
    for filename, content in build_connector_file_contents(
        mcp_url, local_api_url, auth_mode=auth_mode
    ).items():
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
