from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_PORTS = [f"http://127.0.0.1:{port}" for port in range(41731, 41741)]


class VerificationError(RuntimeError):
    pass


def send_request(
    base_url: str,
    path: str,
    payload: dict | list | None = None,
    *,
    method: str = "POST",
    raw_body: bytes | None = None,
) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    data = raw_body
    if raw_body is None and payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


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
    raise VerificationError(f"Local API kept responding after the application was closed: {base_url}")


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
    return subprocess.Popen([str(executable)], env=env)


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
        raise VerificationError(f"{context}: expected successful API response, got {status} {response}")
    return response["data"]


def fetch_card(base_url: str, card_id: str, *, context: str) -> tuple[dict, dict]:
    status, response = send_request(base_url, "/api/get_card", {"card_id": card_id})
    card = assert_ok(status, response, context=context)["card"]
    return response, card


def wait_for_status(base_url: str, card_id: str, *, expected_status: str, timeout_seconds: int = 10) -> dict:
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
    raise VerificationError(f"Card remaining time did not continue to decrease. Last state: {last_card}")


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
    raise VerificationError(f"Sticky remaining time did not continue to decrease. Last state: {last_sticky}")


def run_positive_api_checks(base_url: str) -> dict:
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
    listed_columns = assert_ok(status, columns_after_create, context="list_columns_after_create")["columns"]
    if len(listed_columns) <= len(initial_columns) or not any(column["id"] == custom_column_id for column in listed_columns):
        raise VerificationError("Custom column was not added to the board.")

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
    if card["status"] != "ok" or card["indicator"] != "green":
        raise VerificationError("New card did not start in the expected green state.")
    if "remaining_display" not in card or "deadline_timestamp" not in card:
        raise VerificationError("Card response is missing countdown fields.")

    status, moved = send_request(base_url, "/api/move_card", {"card_id": card_id, "column": custom_column_id})
    report["move_card"] = moved
    moved_card = assert_ok(status, moved, context="move_card")["card"]
    if moved_card["column"] != custom_column_id:
        raise VerificationError("Card did not move into the custom column.")

    warning_card = wait_for_status(base_url, card_id, expected_status="warning")
    report["warning_state"] = warning_card
    if warning_card["indicator"] != "yellow":
        raise VerificationError("Warning state did not turn the signal yellow.")

    expired_card = wait_for_status(base_url, card_id, expected_status="expired")
    report["expired_state"] = expired_card
    if expired_card["indicator"] != "red":
        raise VerificationError("Expired state did not turn the signal red.")
    if expired_card["remaining_seconds"] != 0:
        raise VerificationError("Expired card must have remaining_seconds = 0.")

    status, archived = send_request(base_url, "/api/archive_card", {"card_id": card_id})
    report["archive_card"] = archived
    archived_card = assert_ok(status, archived, context="archive_card")["card"]
    if not archived_card["archived"]:
        raise VerificationError("Archiving the card did not set archived=true.")

    status, visible_cards = send_request(base_url, "/api/get_cards", {"include_archived": False})
    report["get_cards"] = visible_cards
    active_cards = assert_ok(status, visible_cards, context="get_cards")["cards"]
    if any(item["id"] == card_id for item in active_cards):
        raise VerificationError("Archived card is still visible in active cards.")

    status, with_archived = send_request(base_url, "/api/get_cards", {"include_archived": True})
    report["get_cards_with_archived"] = with_archived
    all_cards = assert_ok(status, with_archived, context="get_cards_with_archived")["cards"]
    if not any(item["id"] == card_id and item["archived"] for item in all_cards):
        raise VerificationError("Archived card is missing from include_archived=true.")

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
    if len(long_card["title"]) != 120 or len(long_card["description"]) != 5000:
        raise VerificationError("Boundary-sized title/description were not saved intact.")

    status, found_cards = send_request(
        base_url,
        "/api/search_cards",
        {"query": "verification", "limit": 5, "include_archived": True},
    )
    report["search_cards"] = found_cards
    search_payload = assert_ok(status, found_cards, context="search_cards")
    if not any(item["id"] == card_id for item in search_payload["cards"]):
        raise VerificationError("Search did not find the created verification card.")

    status, board_snapshot_response = send_request(base_url, "/api/get_board_snapshot", method="GET")
    report["board_snapshot"] = board_snapshot_response
    board_snapshot = assert_ok(status, board_snapshot_response, context="get_board_snapshot")
    if float(board_snapshot["settings"].get("board_scale", 0)) != 1.0:
        raise VerificationError("A fresh board should start with board_scale = 1.0.")
    _ = find_sticky(board_snapshot, sticky_id, context="get_board_snapshot")

    status, wall_response = send_request(base_url, "/api/get_gpt_wall", {"include_archived": True, "event_limit": 50})
    report["gpt_wall"] = wall_response
    wall = assert_ok(status, wall_response, context="get_gpt_wall")
    wall_text = str(wall.get("text", ""))
    if "[ТЕКУЩЕЕ СОСТОЯНИЕ ДОСКИ]" not in wall_text or "[ЛЕНТА СОБЫТИЙ]" not in wall_text:
        raise VerificationError("GPT wall is missing required state/event sections.")
    if not any(item["id"] == card_id for item in wall["cards"]):
        raise VerificationError("GPT wall does not contain the created card.")
    if not any(item["id"] == sticky_id for item in wall["stickies"]):
        raise VerificationError("GPT wall does not contain the created sticky.")

    status, board_scale_response = send_request(
        base_url,
        "/api/update_board_settings",
        {"board_scale": 1.25, "actor_name": "INSPECTOR", "source": "ui"},
    )
    report["update_board_settings"] = board_scale_response
    board_scale_payload = assert_ok(status, board_scale_response, context="update_board_settings")
    if float(board_scale_payload["settings"].get("board_scale", 0)) != 1.25:
        raise VerificationError("Board scale 1.25 was not persisted.")

    status, moved_sticky_response = send_request(
        base_url,
        "/api/move_sticky",
        {"sticky_id": sticky_id, "x": 260, "y": 180, "actor_name": "INSPECTOR", "source": "api"},
    )
    report["move_sticky"] = moved_sticky_response
    moved_sticky = assert_ok(status, moved_sticky_response, context="move_sticky")["sticky"]
    if moved_sticky["x"] != 260 or moved_sticky["y"] != 180:
        raise VerificationError("Sticky was not moved to the new coordinates.")

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
    if "15:00" not in updated_sticky["text"]:
        raise VerificationError("Sticky text update was not applied.")

    status, updated_snapshot_response = send_request(base_url, "/api/get_board_snapshot", method="GET")
    report["board_snapshot_after_updates"] = updated_snapshot_response
    updated_snapshot = assert_ok(status, updated_snapshot_response, context="get_board_snapshot_after_updates")
    if float(updated_snapshot["settings"].get("board_scale", 0)) != 1.25:
        raise VerificationError("Board snapshot does not reflect updated board scale.")
    updated_snapshot_sticky = find_sticky(updated_snapshot, sticky_id, context="get_board_snapshot_after_updates")
    if "15:00" not in updated_snapshot_sticky["text"]:
        raise VerificationError("Board snapshot does not reflect updated sticky text.")

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
    persistence_card = assert_ok(status, persistence_card_response, context="create_persistence_card")["card"]
    persistence_card_id = persistence_card["id"]
    time.sleep(2)
    persistence_before_restart_response, persistence_before_restart_card = fetch_card(
        base_url,
        persistence_card_id,
        context="persistence_card_before_restart",
    )
    report["persistence_card_before_restart"] = persistence_before_restart_response

    status, persistence_snapshot_response = send_request(base_url, "/api/get_board_snapshot", method="GET")
    report["persistence_snapshot_before_restart"] = persistence_snapshot_response
    persistence_snapshot = assert_ok(status, persistence_snapshot_response, context="persistence_snapshot_before_restart")
    persistence_sticky_before_restart = find_sticky(
        persistence_snapshot,
        sticky_id,
        context="persistence_snapshot_before_restart",
    )

    return {
        "archived_card_id": card_id,
        "persistence_card_id": persistence_card_id,
        "persistence_remaining_before_restart": persistence_before_restart_card["remaining_seconds"],
        "persistence_deadline_timestamp": persistence_before_restart_card["deadline_timestamp"],
        "sticky_id": sticky_id,
        "sticky_remaining_before_restart": persistence_sticky_before_restart["remaining_seconds"],
        "sticky_deadline_timestamp": persistence_sticky_before_restart["deadline_timestamp"],
        "board_scale": float(persistence_snapshot["settings"].get("board_scale", 1.0)),
        "custom_column_id": custom_column_id,
        "report": report,
    }


def run_negative_api_checks(base_url: str) -> dict:
    report: dict[str, object] = {}

    status, invalid_json = send_request(base_url, "/api/create_card", raw_body=b"{broken")
    report["invalid_json"] = invalid_json
    if status != 400 or invalid_json["error"]["code"] != "invalid_json":
        raise VerificationError("API did not reject malformed JSON.")

    status, invalid_type = send_request(base_url, "/api/create_card", ["not", "object"])
    report["invalid_payload_type"] = invalid_type
    if status != 400 or invalid_type["error"]["code"] != "validation_error":
        raise VerificationError("API did not reject a non-object JSON body.")

    status, empty_title = send_request(base_url, "/api/create_card", {"title": "   ", "deadline": {"days": 1, "hours": 0}})
    report["empty_title"] = empty_title
    if status != 400 or empty_title["error"]["code"] != "validation_error":
        raise VerificationError("API accepted an empty card title.")

    status, invalid_bool = send_request(base_url, "/api/get_cards", {"include_archived": "false"})
    report["invalid_bool"] = invalid_bool
    if status != 400 or invalid_bool["error"]["code"] != "validation_error":
        raise VerificationError("API accepted an invalid boolean field.")

    status, empty_column_label = send_request(base_url, "/api/create_column", {"label": "   "})
    report["empty_column_label"] = empty_column_label
    if status != 400 or empty_column_label["error"]["code"] != "validation_error":
        raise VerificationError("API accepted an empty column label.")

    status, invalid_deadline_zero = send_request(
        base_url,
        "/api/create_card",
        {"title": "Zero deadline", "deadline": {"days": 0, "hours": 0}},
    )
    report["invalid_deadline_zero"] = invalid_deadline_zero
    if status != 400 or invalid_deadline_zero["error"]["code"] != "validation_error":
        raise VerificationError("API accepted a zero card deadline.")

    status, invalid_deadline_hour = send_request(
        base_url,
        "/api/create_card",
        {"title": "Broken deadline", "deadline": {"days": 0, "hours": 24}},
    )
    report["invalid_deadline_hour"] = invalid_deadline_hour
    if status != 400 or invalid_deadline_hour["error"]["code"] != "validation_error":
        raise VerificationError("API accepted an invalid hours value.")

    status, long_title = send_request(
        base_url,
        "/api/create_card",
        {"title": "Z" * 121, "deadline": {"days": 1, "hours": 0}},
    )
    report["too_long_title"] = long_title
    if status != 400 or long_title["error"]["code"] != "validation_error":
        raise VerificationError("API accepted an overlong title.")

    status, invalid_sticky_deadline = send_request(
        base_url,
        "/api/create_sticky",
        {"text": "Broken sticky", "deadline": {"days": 0, "hours": 0}},
    )
    report["invalid_sticky_deadline"] = invalid_sticky_deadline
    if status != 400 or invalid_sticky_deadline["error"]["code"] != "validation_error":
        raise VerificationError("API accepted a zero sticky deadline.")

    status, invalid_board_scale = send_request(base_url, "/api/update_board_settings", {"board_scale": 2.0})
    report["invalid_board_scale"] = invalid_board_scale
    if status != 400 or invalid_board_scale["error"]["code"] != "validation_error":
        raise VerificationError("API accepted an out-of-range board scale.")

    status, missing_route = send_request(base_url, "/api/unknown", {}, method="POST")
    report["unknown_route"] = missing_route
    if status != 404 or missing_route["error"]["code"] != "not_found":
        raise VerificationError("API did not report unknown routes correctly.")

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
    if float(snapshot["settings"].get("board_scale", 0)) != board_scale:
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

    status, wall_response = send_request(base_url, "/api/get_gpt_wall", {"include_archived": True, "event_limit": 100})
    report["gpt_wall_after_restart"] = wall_response
    wall = assert_ok(status, wall_response, context="gpt_wall_after_restart")
    wall_text = str(wall.get("text", ""))
    if "[ТЕКУЩЕЕ СОСТОЯНИЕ ДОСКИ]" not in wall_text or "[ЛЕНТА СОБЫТИЙ]" not in wall_text:
        raise VerificationError("GPT wall lost its expected structure after restart.")
    if not any(item["id"] == sticky_id for item in wall["stickies"]):
        raise VerificationError("GPT wall lost the sticky after restart.")

    return report


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
        deadline = time.time() + 10
        return_code = None
        while time.time() < deadline:
            return_code = process.poll()
            if return_code is not None:
                break
            time.sleep(0.5)
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
        log_deadline = time.time() + 10
        while time.time() < log_deadline and not log_file.exists():
            time.sleep(0.5)
        if not log_file.exists():
            raise VerificationError("No log file was created after the forced startup failure.")
        log_text = log_file.read_text(encoding="utf-8", errors="ignore")
        if "failed_to_start_api" not in log_text:
            raise VerificationError("Startup failure was not recorded in application logs.")

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

        process = launch_app(executable, appdata_root, api_port=api_port)
        base_url = wait_for_api(base_urls=[expected_base_url])
        positive = run_positive_api_checks(base_url)
        negative = run_negative_api_checks(base_url)
        report["base_url"] = base_url
        report["smoke"] = positive["report"]
        report["api_negative"] = negative

        stop_process(process)
        process = None
        wait_for_api_shutdown(base_url)

        process_after_restart = launch_app(executable, appdata_root, api_port=api_port)
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
            float(positive["board_scale"]),
        )

        stop_process(process_after_restart)
        process_after_restart = None
        wait_for_api_shutdown(base_url)

        report["startup_error"] = verify_startup_error_handling(executable, startup_error_appdata_root)

        print(json.dumps(report, ensure_ascii=True, indent=2))
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
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True, indent=2))
        raise SystemExit(1)
