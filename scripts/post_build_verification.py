from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.json_safety import reject_deeply_nested_json  # noqa: E402

DEFAULT_PORTS = [f"http://127.0.0.1:{port}" for port in range(41731, 41741)]
POST_BUILD_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
POST_BUILD_LOG_TAIL_MAX_BYTES = 512 * 1024


class VerificationError(RuntimeError):
    pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)


def _urlopen_no_redirect(request: urllib.request.Request, *, timeout: float):
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


def _json_safe_value(value, *, depth: int = 8):
    if depth < 0:
        return str(value)
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item, depth=depth - 1)
            for key, item in value.items()
            if key is not None
        }
    if isinstance(value, list | tuple | set):
        return [_json_safe_value(item, depth=depth - 1) for item in value]
    return str(value)


def _json_dumps(payload) -> str:
    return json.dumps(_json_safe_value(payload), ensure_ascii=True, indent=2, allow_nan=False)


def _board_scale_value(value: Any, *, context: str) -> float:
    if isinstance(value, bool):
        raise VerificationError(f"{context} returned invalid board_scale.")
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise VerificationError(f"{context} returned invalid board_scale.") from exc
    if not math.isfinite(parsed) or parsed < 0.5 or parsed > 1.5:
        raise VerificationError(f"{context} returned invalid board_scale.")
    return parsed


def _read_response_body(
    response: Any, *, limit_bytes: int = POST_BUILD_RESPONSE_MAX_BYTES
) -> bytes:
    body = response.read(limit_bytes + 1)
    if len(body) > limit_bytes:
        raise ValueError(
            f"Post-build verification response is too large ({limit_bytes} byte limit)"
        )
    return body


def _load_response_json(raw: bytes, *, context: str) -> dict:
    try:
        decoded = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
    except RecursionError as exc:
        raise ValueError(f"{context} JSON is too deeply nested") from exc
    reject_deeply_nested_json(decoded, message=f"{context} JSON is too deeply nested")
    if not isinstance(decoded, dict):
        raise ValueError(f"{context} must be a JSON object")
    return decoded


def _read_log_tail_text(path: Path) -> str:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > POST_BUILD_LOG_TAIL_MAX_BYTES:
            handle.seek(size - POST_BUILD_LOG_TAIL_MAX_BYTES)
        raw = handle.read(POST_BUILD_LOG_TAIL_MAX_BYTES)
    return raw.decode("utf-8", errors="ignore")


def send_request(
    base_url: str,
    path: str,
    payload: dict | list | None = None,
    *,
    method: str = "POST",
    raw_body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    data = raw_body
    if raw_body is None and payload is not None:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}", data=data, headers=request_headers, method=method
    )
    try:
        with _urlopen_no_redirect(request, timeout=5) as response:
            decoded = _load_response_json(_read_response_body(response), context="API response")
            return response.status, decoded
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ValueError(f"Post-build verification request redirected: {path}") from exc
        decoded = _load_response_json(_read_response_body(exc), context="API error response")
        return exc.code, decoded


def login_operator_headers(base_url: str, *, username: str, password: str) -> dict[str, str]:
    status, response = send_request(
        base_url,
        "/api/login_operator",
        {"username": username, "password": password},
    )
    payload = assert_ok(status, response, context="login_operator")
    token = str(payload["session"]["token"])
    return {"X-Operator-Session": token}


def _operator_credentials(*, username: str = "", password: str = "") -> tuple[str, str]:
    resolved_username = (
        username or os.environ.get("AUTOSTOP_SMOKE_OPERATOR_USERNAME") or "release-smoke-admin"
    )
    resolved_password = password or os.environ.get("AUTOSTOP_SMOKE_OPERATOR_PASSWORD")
    if not resolved_password:
        resolved_password = f"ReleaseSmoke-{secrets.token_urlsafe(18)}1!"
    return resolved_username, resolved_password


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_api(timeout_seconds: int = 30, *, base_urls: list[str] | None = None) -> str:
    deadline = time.time() + timeout_seconds
    last_error = None
    candidates = base_urls or DEFAULT_PORTS
    while time.time() < deadline:
        for base_url in candidates:
            try:
                status, response = send_request(base_url, "/api/health", method="GET")
                if status == 200 and response.get("ok"):
                    return base_url
            except Exception as exc:  # pragma: no cover
                last_error = exc
        time.sleep(1)
    raise VerificationError(f"Local API did not start in time: {last_error}")


def wait_for_api_shutdown(base_url: str, timeout_seconds: int = 15) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            status, response = send_request(base_url, "/api/health", method="GET")
            if status == 200 and response.get("ok"):
                time.sleep(0.5)
                continue
        except Exception:
            return
    raise VerificationError(
        f"Local API kept responding after the application was closed: {base_url}"
    )


def launch_app(
    executable: Path,
    appdata_root: Path,
    *,
    api_port: int,
    api_fallback_limit: int = 1,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen:
    env = os.environ.copy()
    env["APPDATA"] = str(appdata_root)
    env["MINIMAL_KANBAN_API_PORT"] = str(api_port)
    env["MINIMAL_KANBAN_API_PORT_FALLBACK_LIMIT"] = str(api_fallback_limit)
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen([str(executable)], env=env, stdin=subprocess.DEVNULL)


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def wait_for_process_exit(process: subprocess.Popen, timeout_seconds: int = 15) -> int:
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        stop_process(process)
        raise VerificationError("Application did not exit in the expected time.") from exc


def block_port() -> tuple[socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # The packaged desktop app binds the board API to 0.0.0.0 in normal host mode
    # so the verifier must reserve the wildcard address as well; blocking only
    # 127.0.0.1 can still leave the wildcard bind available on Windows.
    sock.bind(("0.0.0.0", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def assert_ok(status: int, response: dict, *, context: str) -> dict:
    if status != 200 or not response.get("ok"):
        raise VerificationError(
            f"{context}: expected successful API response, got {status} {response}"
        )
    return response["data"]


def assert_markdown_gpt_wall(wall: dict, *, context: str) -> None:
    text = str(wall.get("text") or "")
    meta = wall.get("meta") if isinstance(wall.get("meta"), dict) else {}
    sections = wall.get("sections") if isinstance(wall.get("sections"), dict) else {}
    board_content = (
        sections.get("board_content") if isinstance(sections.get("board_content"), dict) else {}
    )
    event_log = sections.get("event_log") if isinstance(sections.get("event_log"), dict) else {}
    board_text = str(board_content.get("text") or "")
    event_text = str(event_log.get("text") or "")

    has_combined_markdown = (
        "# AutoStop CRM Board Content" in text and "# AutoStop CRM Event Log" in text
    )
    has_section_markdown = board_text.startswith(
        "# AutoStop CRM Board Content"
    ) and event_text.startswith("# AutoStop CRM Event Log")
    if not has_combined_markdown and not has_section_markdown:
        raise VerificationError(
            f"{context}: GPT wall is missing Markdown board-content/event-log sections."
        )
    if meta.get("text_format") != "markdown":
        raise VerificationError(f"{context}: GPT wall text_format should be markdown.")


def fetch_card(base_url: str, card_id: str, *, context: str) -> tuple[dict, dict]:
    status, response = send_request(base_url, "/api/get_card", {"card_id": card_id})
    card = assert_ok(status, response, context=context)["card"]
    return response, card


def wait_for_status(
    base_url: str, card_id: str, *, expected_status: str, timeout_seconds: int = 10
) -> dict:
    deadline = time.time() + timeout_seconds
    last_card: dict | None = None
    while time.time() < deadline:
        _, card = fetch_card(base_url, card_id, context=f"status_wait_{expected_status}")
        last_card = card
        if card["status"] == expected_status:
            return card
        time.sleep(0.5)
    raise VerificationError(
        f"Card did not transition to status {expected_status} in time. Last state: {last_card}"
    )


def wait_for_remaining_drop(
    base_url: str,
    card_id: str,
    *,
    lower_than: int,
    timeout_seconds: int = 8,
) -> dict:
    deadline = time.time() + timeout_seconds
    last_card: dict | None = None
    while time.time() < deadline:
        _, card = fetch_card(base_url, card_id, context="remaining_drop")
        last_card = card
        if card["remaining_seconds"] < lower_than:
            return card
        time.sleep(0.5)
    raise VerificationError(
        f"Card remaining time did not continue to decrease. Last state: {last_card}"
    )


def find_sticky(snapshot: dict, sticky_id: str, *, context: str) -> dict:
    stickies = snapshot.get("stickies", [])
    sticky = next((item for item in stickies if item.get("id") == sticky_id), None)
    if sticky is None:
        raise VerificationError(f"{context}: expected sticky {sticky_id} in board snapshot")
    return sticky


def wait_for_sticky_remaining_drop(
    base_url: str,
    sticky_id: str,
    *,
    lower_than: int,
    timeout_seconds: int = 8,
) -> dict:
    deadline = time.time() + timeout_seconds
    last_sticky: dict | None = None
    while time.time() < deadline:
        status, snapshot_response = send_request(base_url, "/api/get_board_snapshot", method="GET")
        snapshot = assert_ok(status, snapshot_response, context="sticky_remaining_drop")
        sticky = find_sticky(snapshot, sticky_id, context="sticky_remaining_drop")
        last_sticky = sticky
        if sticky["remaining_seconds"] < lower_than:
            return sticky
        time.sleep(0.5)
    raise VerificationError(
        f"Sticky remaining time did not continue to decrease. Last state: {last_sticky}"
    )


def _require_api_condition(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def run_positive_api_checks(base_url: str, *, operator_headers: dict[str, str]) -> dict:
    report: dict[str, object] = {}

    status, health = send_request(base_url, "/api/health", method="GET")
    report["health"] = health
    assert_ok(status, health, context="health")

    status, columns = send_request(base_url, "/api/list_columns", method="GET")
    report["list_columns"] = columns
    initial_columns = assert_ok(status, columns, context="list_columns")["columns"]

    status, created_column = send_request(base_url, "/api/create_column", {"label": "BLOCKERS"})
    report["create_column"] = created_column
    custom_column = assert_ok(status, created_column, context="create_column")["column"]
    custom_column_id = custom_column["id"]

    status, columns_after_create = send_request(base_url, "/api/list_columns", method="GET")
    report["list_columns_after_create"] = columns_after_create
    listed_columns = assert_ok(status, columns_after_create, context="list_columns_after_create")[
        "columns"
    ]
    _require_api_condition(
        len(listed_columns) > len(initial_columns)
        and any(column["id"] == custom_column_id for column in listed_columns),
        "Custom column was not added to the board.",
    )

    status, created_sticky = send_request(
        base_url,
        "/api/create_sticky",
        {
            "text": "Call client about ordered parts after 15:00",
            "x": 120,
            "y": 90,
            "deadline": {"days": 0, "hours": 8},
            "actor_name": "INSPECTOR",
            "source": "api",
        },
    )
    report["create_sticky"] = created_sticky
    sticky = assert_ok(status, created_sticky, context="create_sticky")["sticky"]
    sticky_id = sticky["id"]

    status, created = send_request(
        base_url,
        "/api/create_card",
        {
            "title": "Verification card",
            "description": "Verification of countdown and movement logic",
            "deadline": {"seconds": 7},
        },
    )
    report["create_card"] = created
    card = assert_ok(status, created, context="create_card")["card"]
    card_id = card["id"]
    _require_api_condition(
        card["status"] == "ok" and card["indicator"] == "green",
        "New card did not start in the expected green state.",
    )
    _require_api_condition(
        "remaining_display" in card and "deadline_timestamp" in card,
        "Card response is missing countdown fields.",
    )

    status, moved = send_request(
        base_url, "/api/move_card", {"card_id": card_id, "column": custom_column_id}
    )
    report["move_card"] = moved
    moved_card = assert_ok(status, moved, context="move_card")["card"]
    _require_api_condition(
        moved_card["column"] == custom_column_id,
        "Card did not move into the custom column.",
    )

    warning_card = wait_for_status(base_url, card_id, expected_status="warning")
    report["warning_state"] = warning_card
    _require_api_condition(
        warning_card["indicator"] == "yellow",
        "Warning state did not turn the signal yellow.",
    )

    expired_card = wait_for_status(base_url, card_id, expected_status="expired")
    report["expired_state"] = expired_card
    _require_api_condition(
        expired_card["indicator"] == "red",
        "Expired state did not turn the signal red.",
    )
    _require_api_condition(
        expired_card["remaining_seconds"] == 0,
        "Expired card must have remaining_seconds = 0.",
    )

    status, archived = send_request(base_url, "/api/archive_card", {"card_id": card_id})
    report["archive_card"] = archived
    archived_card = assert_ok(status, archived, context="archive_card")["card"]
    _require_api_condition(
        archived_card["archived"],
        "Archiving the card did not set archived=true.",
    )

    status, visible_cards = send_request(base_url, "/api/get_cards", {"include_archived": False})
    report["get_cards"] = visible_cards
    active_cards = assert_ok(status, visible_cards, context="get_cards")["cards"]
    _require_api_condition(
        not any(item["id"] == card_id for item in active_cards),
        "Archived card is still visible in active cards.",
    )

    status, with_archived = send_request(base_url, "/api/get_cards", {"include_archived": True})
    report["get_cards_with_archived"] = with_archived
    all_cards = assert_ok(status, with_archived, context="get_cards_with_archived")["cards"]
    _require_api_condition(
        any(item["id"] == card_id and item["archived"] for item in all_cards),
        "Archived card is missing from include_archived=true.",
    )

    long_title = "T" * 120
    long_description = "O" * 5000
    status, long_card_response = send_request(
        base_url,
        "/api/create_card",
        {
            "title": long_title,
            "description": long_description,
            "deadline": {"days": 0, "hours": 1},
        },
    )
    report["create_long_card"] = long_card_response
    long_card = assert_ok(status, long_card_response, context="create_long_card")["card"]
    _require_api_condition(
        len(long_card["title"]) == 120 and len(long_card["description"]) == 5000,
        "Boundary-sized title/description were not saved intact.",
    )

    status, found_cards = send_request(
        base_url,
        "/api/search_cards",
        {"query": "verification", "limit": 5, "include_archived": True},
    )
    report["search_cards"] = found_cards
    search_payload = assert_ok(status, found_cards, context="search_cards")
    _require_api_condition(
        any(item["id"] == card_id for item in search_payload["cards"]),
        "Search did not find the created verification card.",
    )

    status, board_snapshot_response = send_request(
        base_url, "/api/get_board_snapshot", method="GET"
    )
    report["board_snapshot"] = board_snapshot_response
    board_snapshot = assert_ok(status, board_snapshot_response, context="get_board_snapshot")
    _require_api_condition(
        _board_scale_value(
            board_snapshot["settings"].get("board_scale", 0), context="get_board_snapshot"
        )
        == 1.0,
        "A fresh board should start with board_scale = 1.0.",
    )
    _ = find_sticky(board_snapshot, sticky_id, context="get_board_snapshot")

    status, wall_response = send_request(
        base_url, "/api/get_gpt_wall", {"include_archived": True, "event_limit": 50}
    )
    report["gpt_wall"] = wall_response
    wall = assert_ok(status, wall_response, context="get_gpt_wall")
    assert_markdown_gpt_wall(wall, context="get_gpt_wall")
    _require_api_condition(
        any(item["id"] == card_id for item in wall["cards"]),
        "GPT wall does not contain the created card.",
    )
    _require_api_condition(
        any(item["id"] == sticky_id for item in wall["stickies"]),
        "GPT wall does not contain the created sticky.",
    )

    status, board_scale_response = send_request(
        base_url,
        "/api/update_board_settings",
        {"board_scale": 1.25, "actor_name": "INSPECTOR", "source": "ui"},
        headers=operator_headers,
    )
    report["update_board_settings"] = board_scale_response
    board_scale_payload = assert_ok(status, board_scale_response, context="update_board_settings")
    _require_api_condition(
        _board_scale_value(
            board_scale_payload["settings"].get("board_scale", 0), context="update_board_settings"
        )
        == 1.25,
        "Board scale 1.25 was not persisted.",
    )

    status, moved_sticky_response = send_request(
        base_url,
        "/api/move_sticky",
        {"sticky_id": sticky_id, "x": 260, "y": 180, "actor_name": "INSPECTOR", "source": "api"},
    )
    report["move_sticky"] = moved_sticky_response
    moved_sticky = assert_ok(status, moved_sticky_response, context="move_sticky")["sticky"]
    _require_api_condition(
        moved_sticky["x"] == 260 and moved_sticky["y"] == 180,
        "Sticky was not moved to the new coordinates.",
    )

    status, updated_sticky_response = send_request(
        base_url,
        "/api/update_sticky",
        {
            "sticky_id": sticky_id,
            "text": "Call client about ordered parts after 15:00 and confirm pickup",
            "deadline": {"days": 0, "hours": 6},
            "actor_name": "INSPECTOR",
            "source": "api",
        },
    )
    report["update_sticky"] = updated_sticky_response
    updated_sticky = assert_ok(status, updated_sticky_response, context="update_sticky")["sticky"]
    _require_api_condition(
        "15:00" in updated_sticky["text"],
        "Sticky text update was not applied.",
    )

    status, updated_snapshot_response = send_request(
        base_url, "/api/get_board_snapshot", method="GET"
    )
    report["board_snapshot_after_updates"] = updated_snapshot_response
    updated_snapshot = assert_ok(
        status, updated_snapshot_response, context="get_board_snapshot_after_updates"
    )
    _require_api_condition(
        _board_scale_value(
            updated_snapshot["settings"].get("board_scale", 0),
            context="get_board_snapshot_after_updates",
        )
        == 1.25,
        "Board snapshot does not reflect updated board scale.",
    )
    updated_snapshot_sticky = find_sticky(
        updated_snapshot, sticky_id, context="get_board_snapshot_after_updates"
    )
    _require_api_condition(
        "15:00" in updated_snapshot_sticky["text"],
        "Board snapshot does not reflect updated sticky text.",
    )

    status, persistence_card_response = send_request(
        base_url,
        "/api/create_card",
        {
            "title": "Persistence card",
            "description": "Verify deadline persistence across restart",
            "column": custom_column_id,
            "deadline": {"seconds": 12},
        },
    )
    report["create_persistence_card"] = persistence_card_response
    persistence_card = assert_ok(
        status, persistence_card_response, context="create_persistence_card"
    )["card"]
    persistence_card_id = persistence_card["id"]
    time.sleep(2)
    persistence_before_restart_response, persistence_before_restart_card = fetch_card(
        base_url,
        persistence_card_id,
        context="persistence_card_before_restart",
    )
    report["persistence_card_before_restart"] = persistence_before_restart_response

    status, persistence_snapshot_response = send_request(
        base_url, "/api/get_board_snapshot", method="GET"
    )
    report["persistence_snapshot_before_restart"] = persistence_snapshot_response
    persistence_snapshot = assert_ok(
        status, persistence_snapshot_response, context="persistence_snapshot_before_restart"
    )
    persistence_sticky_before_restart = find_sticky(
        persistence_snapshot,
        sticky_id,
        context="persistence_snapshot_before_restart",
    )

    return {
        "archived_card_id": card_id,
        "persistence_card_id": persistence_card_id,
        "persistence_remaining_before_restart": persistence_before_restart_card[
            "remaining_seconds"
        ],
        "persistence_deadline_timestamp": persistence_before_restart_card["deadline_timestamp"],
        "sticky_id": sticky_id,
        "sticky_remaining_before_restart": persistence_sticky_before_restart["remaining_seconds"],
        "sticky_deadline_timestamp": persistence_sticky_before_restart["deadline_timestamp"],
        "board_scale": _board_scale_value(
            persistence_snapshot["settings"].get("board_scale", 1.0),
            context="persistence_snapshot_before_restart",
        ),
        "custom_column_id": custom_column_id,
        "report": report,
    }


def _assert_api_error_response(
    status: int,
    response: dict,
    *,
    expected_status: int,
    expected_code: str,
    message: str,
) -> None:
    error = response.get("error") if isinstance(response.get("error"), dict) else {}
    actual_code = str(error.get("code") or "")
    if status != expected_status or actual_code != expected_code:
        raise VerificationError(message)


def run_negative_api_checks(base_url: str, *, operator_headers: dict[str, str]) -> dict:
    report: dict[str, object] = {}
    cases = [
        (
            "invalid_json",
            "/api/create_card",
            {"raw_body": b"{broken"},
            400,
            "invalid_json",
            "API did not reject malformed JSON.",
        ),
        (
            "invalid_payload_type",
            "/api/create_card",
            {"payload": ["not", "object"]},
            400,
            "validation_error",
            "API did not reject a non-object JSON body.",
        ),
        (
            "empty_title",
            "/api/create_card",
            {"payload": {"title": "   ", "deadline": {"days": 1, "hours": 0}}},
            400,
            "validation_error",
            "API accepted an empty card title.",
        ),
        (
            "invalid_bool",
            "/api/get_cards",
            {"payload": {"include_archived": "false"}},
            400,
            "validation_error",
            "API accepted an invalid boolean field.",
        ),
        (
            "empty_column_label",
            "/api/create_column",
            {"payload": {"label": "   "}},
            400,
            "validation_error",
            "API accepted an empty column label.",
        ),
        (
            "invalid_deadline_zero",
            "/api/create_card",
            {"payload": {"title": "Zero deadline", "deadline": {"days": 0, "hours": 0}}},
            400,
            "validation_error",
            "API accepted a zero card deadline.",
        ),
        (
            "invalid_deadline_hour",
            "/api/create_card",
            {"payload": {"title": "Broken deadline", "deadline": {"days": 0, "hours": 24}}},
            400,
            "validation_error",
            "API accepted an invalid hours value.",
        ),
        (
            "too_long_title",
            "/api/create_card",
            {"payload": {"title": "Z" * 121, "deadline": {"days": 1, "hours": 0}}},
            400,
            "validation_error",
            "API accepted an overlong title.",
        ),
        (
            "invalid_sticky_deadline",
            "/api/create_sticky",
            {"payload": {"text": "Broken sticky", "deadline": {"days": 0, "hours": 0}}},
            400,
            "validation_error",
            "API accepted a zero sticky deadline.",
        ),
        (
            "invalid_board_scale",
            "/api/update_board_settings",
            {"payload": {"board_scale": 2.0}, "headers": operator_headers},
            400,
            "validation_error",
            "API accepted an out-of-range board scale.",
        ),
        (
            "unknown_route",
            "/api/unknown",
            {"payload": {}, "method": "POST"},
            404,
            "not_found",
            "API did not report unknown routes correctly.",
        ),
    ]
    for key, path, request_kwargs, expected_status, expected_code, message in cases:
        status, response = send_request(base_url, path, **request_kwargs)
        report[key] = response
        _assert_api_error_response(
            status,
            response,
            expected_status=expected_status,
            expected_code=expected_code,
            message=message,
        )
    return report


def verify_persistence(
    base_url: str,
    archived_card_id: str,
    persistence_card_id: str,
    remaining_before_restart: int,
    custom_column_id: str,
    deadline_timestamp: str,
    sticky_id: str,
    sticky_remaining_before_restart: int,
    sticky_deadline_timestamp: str,
    board_scale: float,
) -> dict:
    report: dict[str, object] = {}

    status, columns = send_request(base_url, "/api/list_columns", method="GET")
    report["list_columns_after_restart"] = columns
    restored_columns = assert_ok(status, columns, context="persistence_list_columns")["columns"]
    if not any(column["id"] == custom_column_id for column in restored_columns):
        raise VerificationError("Custom column was not restored after restart.")

    status, archived_cards = send_request(base_url, "/api/get_cards", {"include_archived": True})
    report["get_cards_with_archived_after_restart"] = archived_cards
    cards = assert_ok(status, archived_cards, context="persistence_get_cards")["cards"]
    archived_card = next((card for card in cards if card["id"] == archived_card_id), None)
    if archived_card is None or not archived_card["archived"]:
        raise VerificationError("Archived card was not restored as archived after restart.")

    persistence_card_response, persistence_card = fetch_card(
        base_url,
        persistence_card_id,
        context="persistence_card_after_restart",
    )
    report["persistence_card_after_restart_initial"] = persistence_card_response
    if persistence_card["column"] != custom_column_id:
        raise VerificationError("Persistence card changed column after restart.")
    if persistence_card["deadline_timestamp"] != deadline_timestamp:
        raise VerificationError("Persistence card deadline timestamp changed after restart.")
    if persistence_card["remaining_seconds"] >= remaining_before_restart:
        raise VerificationError("Persistence card remaining time did not decrease after restart.")

    progressed_card = wait_for_remaining_drop(
        base_url,
        persistence_card_id,
        lower_than=persistence_card["remaining_seconds"],
    )
    report["persistence_card_after_restart_progress"] = progressed_card

    status, snapshot_response = send_request(base_url, "/api/get_board_snapshot", method="GET")
    report["board_snapshot_after_restart"] = snapshot_response
    snapshot = assert_ok(status, snapshot_response, context="board_snapshot_after_restart")
    if (
        _board_scale_value(
            snapshot["settings"].get("board_scale", 0), context="board_snapshot_after_restart"
        )
        != board_scale
    ):
        raise VerificationError("Board scale was not preserved after restart.")
    persisted_sticky = find_sticky(snapshot, sticky_id, context="board_snapshot_after_restart")
    if persisted_sticky["deadline_timestamp"] != sticky_deadline_timestamp:
        raise VerificationError("Sticky deadline timestamp changed after restart.")
    if persisted_sticky["remaining_seconds"] >= sticky_remaining_before_restart:
        raise VerificationError("Sticky remaining time did not decrease after restart.")

    progressed_sticky = wait_for_sticky_remaining_drop(
        base_url,
        sticky_id,
        lower_than=persisted_sticky["remaining_seconds"],
    )
    report["sticky_after_restart_progress"] = progressed_sticky

    status, wall_response = send_request(
        base_url, "/api/get_gpt_wall", {"include_archived": True, "event_limit": 100}
    )
    report["gpt_wall_after_restart"] = wall_response
    wall = assert_ok(status, wall_response, context="gpt_wall_after_restart")
    assert_markdown_gpt_wall(wall, context="gpt_wall_after_restart")
    if not any(item["id"] == sticky_id for item in wall["stickies"]):
        raise VerificationError("GPT wall lost the sticky after restart.")

    return report


def _wait_for_process_return_code(
    process: subprocess.Popen, *, timeout_seconds: float, interval_seconds: float = 0.5
) -> int | None:
    deadline = time.time() + timeout_seconds
    return_code = None
    while time.time() < deadline:
        return_code = process.poll()
        if return_code is not None:
            break
        time.sleep(interval_seconds)
    return return_code


def _wait_for_log_file(log_file: Path, *, timeout_seconds: float) -> Path:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline and not log_file.exists():
        time.sleep(0.5)
    if not log_file.exists():
        raise VerificationError("No log file was created after the forced startup failure.")
    return log_file


def _assert_startup_failure_logged(log_file: Path) -> None:
    log_text = _read_log_tail_text(log_file)
    if "failed_to_start_api" not in log_text:
        raise VerificationError("Startup failure was not recorded in application logs.")


def verify_startup_error_handling(executable: Path, appdata_root: Path) -> dict:
    blocker = None
    process = None
    try:
        blocker, blocked_port = block_port()
        process = launch_app(
            executable,
            appdata_root,
            api_port=blocked_port,
            api_fallback_limit=1,
            extra_env={"MINIMAL_KANBAN_SUPPRESS_ERROR_DIALOGS": "1"},
        )
        return_code = _wait_for_process_return_code(process, timeout_seconds=10)
        if return_code == 0:
            raise VerificationError("Application exited with code 0 during forced startup failure.")

        base_url = f"http://127.0.0.1:{blocked_port}"
        try:
            status, response = send_request(base_url, "/api/health", method="GET")
            if status == 200 and response.get("ok"):
                raise VerificationError("Application unexpectedly started API on a blocked port.")
        except Exception:
            pass

        log_file = appdata_root / "Minimal Kanban" / "logs" / "minimal-kanban.log"
        _assert_startup_failure_logged(_wait_for_log_file(log_file, timeout_seconds=10))

        forced_termination = False
        if return_code is None:
            forced_termination = True
            stop_process(process)
            process = None
            return_code = -15

        return {
            "blocked_port": blocked_port,
            "return_code": return_code,
            "forced_termination": forced_termination,
            "log_file": str(log_file),
        }
    finally:
        if process is not None:
            stop_process(process)
        if blocker is not None:
            blocker.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-executable", type=Path, required=True)
    parser.add_argument("--operator-username", default="")
    parser.add_argument("--operator-password", default="")
    args = parser.parse_args()

    workspace = Path(tempfile.mkdtemp(prefix="minimal-kanban-verification-"))
    appdata_root = workspace / "AppData" / "Roaming"
    startup_error_appdata_root = workspace / "StartupErrorAppData"
    api_port = reserve_port()
    expected_base_url = f"http://127.0.0.1:{api_port}"
    process = None
    process_after_restart = None
    report: dict[str, object] = {"workspace": str(workspace)}

    try:
        executable = args.app_executable
        if not executable.exists():
            raise VerificationError(f"Executable was not found for verification: {executable}")

        operator_username, operator_password = _operator_credentials(
            username=args.operator_username,
            password=args.operator_password,
        )
        operator_env = {
            "MINIMAL_KANBAN_DEFAULT_ADMIN_USERNAME": operator_username,
            "MINIMAL_KANBAN_DEFAULT_ADMIN_PASSWORD": operator_password,
        }

        process = launch_app(
            executable,
            appdata_root,
            api_port=api_port,
            extra_env=operator_env,
        )
        base_url = wait_for_api(base_urls=[expected_base_url])
        operator_headers = login_operator_headers(
            base_url,
            username=operator_username,
            password=operator_password,
        )
        positive = run_positive_api_checks(base_url, operator_headers=operator_headers)
        negative = run_negative_api_checks(base_url, operator_headers=operator_headers)
        report["base_url"] = base_url
        report["smoke"] = positive["report"]
        report["api_negative"] = negative

        stop_process(process)
        process = None
        wait_for_api_shutdown(base_url)

        process_after_restart = launch_app(
            executable,
            appdata_root,
            api_port=api_port,
            extra_env=operator_env,
        )
        base_url = wait_for_api(base_urls=[expected_base_url])
        report["persistence"] = verify_persistence(
            base_url,
            str(positive["archived_card_id"]),
            str(positive["persistence_card_id"]),
            int(positive["persistence_remaining_before_restart"]),
            str(positive["custom_column_id"]),
            str(positive["persistence_deadline_timestamp"]),
            str(positive["sticky_id"]),
            int(positive["sticky_remaining_before_restart"]),
            str(positive["sticky_deadline_timestamp"]),
            _board_scale_value(positive["board_scale"], context="positive_checks"),
        )

        stop_process(process_after_restart)
        process_after_restart = None
        wait_for_api_shutdown(base_url)

        report["startup_error"] = verify_startup_error_handling(
            executable, startup_error_appdata_root
        )

        print(_json_dumps(report))
        return 0
    finally:
        if process is not None:
            stop_process(process)
        if process_after_restart is not None:
            stop_process(process_after_restart)
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationError as exc:
        print(_json_dumps({"ok": False, "error": str(exc)}))
        raise SystemExit(1)
