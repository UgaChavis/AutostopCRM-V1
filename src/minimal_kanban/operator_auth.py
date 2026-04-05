from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from copy import deepcopy
from datetime import timedelta
from logging import Logger
from pathlib import Path
from typing import Any

from .config import get_app_data_dir, get_default_admin_password, get_default_admin_username, get_users_file
from .models import normalize_actor_name, normalize_int, parse_datetime, utc_now, utc_now_iso
from .services.card_service import CardService, ServiceError
from .storage.file_lock import ProcessFileLock
from .storage.json_store import JsonStore


USER_ROLE_VALUES = frozenset({"operator", "admin"})
PASSWORD_MIN_LENGTH = 4
PASSWORD_HASH_ITERATIONS = 200_000
SESSION_TTL_DAYS = 30
STATS_WINDOW_DAYS = 15
OPEN_COUNT_KEY = "cards_opened"
ACTION_HISTORY_KEY = "action_history"
ACTION_HISTORY_RETENTION_DAYS = 15
LEGACY_DEFAULT_ADMIN_PASSWORDS = ("admin123",)
ACTION_TO_STAT_KEY = {
    "card_created": "cards_created",
    "card_archived": "cards_archived",
    "card_moved": "card_moves",
    "repair_order_updated": "repair_orders_updated",
    "repair_order_autofilled": "repair_orders_updated",
    "attachment_added": "attachments_added",
    "attachment_removed": "attachments_removed",
}


def _normalized_username(value) -> str:
    return normalize_actor_name(value, default="").upper()


def _password_hash(password: str, *, salt: str | None = None) -> str:
    resolved_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        resolved_salt.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    )
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${resolved_salt}${digest.hex()}"


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, raw_iterations, salt, expected = password_hash.split("$", 3)
        iterations = int(raw_iterations)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256" or iterations < 1 or not salt or not expected:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return hmac.compare_digest(actual, expected)


class OperatorAuthService:
    def __init__(
        self,
        state_store: JsonStore,
        card_service: CardService,
        *,
        users_file: Path | None = None,
        logger: Logger | None = None,
    ) -> None:
        self._state_store = state_store
        self._card_service = card_service
        self._users_file = users_file or get_users_file()
        self._logger = logger
        self._lock = threading.RLock()
        self._process_lock = ProcessFileLock(self._users_file.with_suffix(".lock"))
        get_app_data_dir().mkdir(parents=True, exist_ok=True)
        self._users_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._users_file.exists():
            with self._process_lock.acquire():
                self._write_state(self._default_state())

    def login(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        username = self._validated_username(payload.get("username"))
        password = self._validated_password(payload.get("password"))
        with self._lock:
            state = self._read_normalized_state()
            user = self._find_user(state["users"], username)
            password_hash = str(user.get("password_hash", "")) if user is not None else ""
            password_ok = user is not None and _verify_password(password, password_hash)
            if not password_ok and user is not None and self._can_upgrade_default_admin_password(user, password, password_hash):
                user["password_hash"] = _password_hash(password)
                user["updated_at"] = utc_now_iso()
                password_ok = True
            if user is None or not password_ok:
                self._fail("unauthorized", "Неверный логин или пароль.", status_code=401)
            token = secrets.token_urlsafe(32)
            now = utc_now()
            state["sessions"] = [
                session
                for session in state["sessions"]
                if session.get("token") != token
            ]
            state["sessions"].append(
                {
                    "token": token,
                    "username": user["username"],
                    "created_at": now.isoformat(),
                    "expires_at": (now + timedelta(days=SESSION_TTL_DAYS)).isoformat(),
                }
            )
            self._write_state(state)
            snapshot = deepcopy(user)
        return self._build_profile_payload(snapshot, token=token)

    def logout(self, payload: dict | None = None) -> dict:
        session = self._required_session(payload)
        with self._lock:
            state = self._read_normalized_state()
            before = len(state["sessions"])
            state["sessions"] = [item for item in state["sessions"] if item.get("token") != session["token"]]
            if len(state["sessions"]) != before:
                self._write_state(state)
        return {"logged_out": True}

    def _can_upgrade_default_admin_password(self, user: dict[str, Any], password: str, password_hash: str) -> bool:
        if user.get("role") != "admin":
            return False
        if user.get("username") != _normalized_username(get_default_admin_username()):
            return False
        if password != get_default_admin_password():
            return False
        return any(
            legacy_password != password and _verify_password(legacy_password, password_hash)
            for legacy_password in LEGACY_DEFAULT_ADMIN_PASSWORDS
        )

    def get_profile(self, payload: dict | None = None) -> dict:
        session = self._required_session(payload)
        with self._lock:
            state = self._read_normalized_state()
            user = self._find_user(state["users"], session["username"])
            if user is None:
                self._fail("unauthorized", "Сессия больше не связана с пользователем.", status_code=401)
            snapshot = deepcopy(user)
        return self._build_profile_payload(snapshot, token=session["token"])

    def list_users(self, payload: dict | None = None) -> dict:
        self._required_admin_session(payload)
        with self._lock:
            state = self._read_normalized_state()
            users = [deepcopy(item) for item in state["users"]]
        bundle = self._state_store.read_bundle()
        event_activity_index = self._build_event_activity_index(bundle["events"])
        rows = [
            self._serialize_user_summary(user, bundle=bundle, event_activity_index=event_activity_index)
            for user in users
        ]
        rows.sort(key=lambda item: (0 if item["role"] == "admin" else 1, item["username"]))
        return {"users": rows, "meta": {"total": len(rows)}}

    def save_user(self, payload: dict | None = None) -> dict:
        self._required_admin_session(payload)
        payload = payload or {}
        username = self._validated_username(payload.get("username"))
        password = self._validated_password(payload.get("password"))
        now_iso = utc_now_iso()
        with self._lock:
            state = self._read_normalized_state()
            existing = self._find_user(state["users"], username)
            created = existing is None
            if created:
                existing = {
                    "username": username,
                    "password_hash": _password_hash(password),
                    "role": "operator",
                    "created_at": now_iso,
                    "updated_at": now_iso,
                    "stats": {OPEN_COUNT_KEY: 0},
                    ACTION_HISTORY_KEY: [],
                }
                state["users"].append(existing)
            else:
                existing["password_hash"] = _password_hash(password)
                existing["updated_at"] = now_iso
                if not isinstance(existing.get("stats"), dict):
                    existing["stats"] = {OPEN_COUNT_KEY: 0}
                if not isinstance(existing.get(ACTION_HISTORY_KEY), list):
                    existing[ACTION_HISTORY_KEY] = []
            self._write_state(state)
            snapshot = deepcopy(existing)
        return {
            "user": self._serialize_user_summary(snapshot),
            "meta": {
                "created": created,
                "updated": not created,
            },
        }

    def delete_user(self, payload: dict | None = None) -> dict:
        session = self._required_admin_session(payload)
        payload = payload or {}
        username = self._validated_username(payload.get("username"))
        if username == session["username"]:
            self._fail("validation_error", "Нельзя удалить текущую активную учётную запись.", status_code=409)
        with self._lock:
            state = self._read_normalized_state()
            target = self._find_user(state["users"], username)
            if target is None:
                self._fail("not_found", "Пользователь не найден.", status_code=404)
            if target["role"] == "admin":
                admins_total = sum(1 for user in state["users"] if user.get("role") == "admin")
                if admins_total <= 1:
                    self._fail("validation_error", "Нельзя удалить последнего администратора.", status_code=409)
            state["users"] = [user for user in state["users"] if user.get("username") != username]
            state["sessions"] = [item for item in state["sessions"] if item.get("username") != username]
            self._write_state(state)
        return {"deleted": True, "username": username}

    def open_card(self, payload: dict | None = None) -> dict:
        session = self._required_session(payload)
        payload = payload or {}
        card_id = str(payload.get("card_id", "") or "").strip()
        if not card_id:
            self._fail("validation_error", "Нужно передать card_id.", details={"field": "card_id"})
        actor_name = session["username"]
        self._card_service.mark_card_seen({"card_id": card_id, "actor_name": actor_name})
        result = self._card_service.get_card({"card_id": card_id, "actor_name": actor_name})
        self._record_user_action(
            session["username"],
            action="card_opened",
            message="Открыл карточку.",
            card_id=card_id,
            counter_key=OPEN_COUNT_KEY,
        )
        return result

    def resolve_session(self, token: str | None) -> dict | None:
        raw_token = str(token or "").strip()
        if not raw_token:
            return None
        with self._lock:
            state = self._read_normalized_state()
            for item in state["sessions"]:
                if item.get("token") != raw_token:
                    continue
                user = self._find_user(state["users"], item.get("username"))
                if user is None:
                    return None
                return self._session_payload(token=raw_token, user=user)
        return None

    def _required_session(self, payload: dict | None) -> dict:
        session = (payload or {}).get("_operator_session")
        if isinstance(session, dict) and session.get("username"):
            return session
        self._fail(
            "unauthorized",
            "Нужен вход оператора.",
            status_code=401,
            details={"auth_type": "operator_session"},
        )

    def _required_admin_session(self, payload: dict | None) -> dict:
        session = self._required_session(payload)
        if session.get("is_admin"):
            return session
        self._fail(
            "forbidden",
            "Нужны права администратора.",
            status_code=403,
            details={"auth_type": "operator_session"},
        )

    def _record_user_action(
        self,
        username: str,
        *,
        action: str,
        message: str,
        card_id: str | None = None,
        counter_key: str | None = None,
    ) -> None:
        with self._lock:
            state = self._read_normalized_state()
            user = self._find_user(state["users"], username)
            if user is None:
                return
            if counter_key:
                stats = user.setdefault("stats", {})
                stats[counter_key] = normalize_int(stats.get(counter_key), default=0, minimum=0) + 1
            history = self._prune_action_history(user.get(ACTION_HISTORY_KEY))
            history.append(
                {
                    "timestamp": utc_now_iso(),
                    "action": str(action or "").strip() or "operator_action",
                    "message": str(message or "").strip() or "Действие оператора.",
                    "card_id": str(card_id or "").strip(),
                }
            )
            user[ACTION_HISTORY_KEY] = self._prune_action_history(history)
            user["updated_at"] = utc_now_iso()
            self._write_state(state)

    def _user_base_payload(self, user: dict[str, Any]) -> dict[str, Any]:
        return {
            "username": user["username"],
            "role": user["role"],
            "is_admin": user["role"] == "admin",
            "created_at": user["created_at"],
            "updated_at": user["updated_at"],
        }

    def _session_payload(self, *, token: str, user: dict[str, Any]) -> dict[str, Any]:
        return {
            "token": token,
            "username": user["username"],
            "role": user["role"],
            "is_admin": user["role"] == "admin",
        }

    def _build_profile_payload(self, user: dict[str, Any], *, token: str) -> dict:
        user_payload = self._user_payload_with_stats(user)
        return {
            "session": self._session_payload(token=token, user=user),
            "user": user_payload["user"],
            "stats": user_payload["stats"],
            "recent_actions": user_payload["recent_actions"],
        }

    def _serialize_user_summary(
        self,
        user: dict[str, Any],
        *,
        bundle: dict[str, Any] | None = None,
        event_activity_index: dict[str, dict[str, Any]] | None = None,
    ) -> dict:
        user_payload = self._user_payload_with_stats(
            user,
            bundle=bundle,
            event_activity_index=event_activity_index,
        )
        return {
            **user_payload["user"],
            "stats": user_payload["stats"],
            "recent_actions": user_payload["recent_actions"],
        }

    def _user_payload_with_stats(
        self,
        user: dict[str, Any],
        *,
        bundle: dict[str, Any] | None = None,
        event_activity_index: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        stats_payload = self._build_user_stats(
            user,
            bundle=bundle,
            event_activity_index=event_activity_index,
        )
        return {
            "user": self._user_base_payload(user),
            "stats": stats_payload["stats"],
            "recent_actions": stats_payload["recent_actions"],
            "all_actions": stats_payload["all_actions"],
        }

    def _build_user_stats(
        self,
        user: dict[str, Any],
        *,
        bundle: dict[str, Any] | None = None,
        event_activity_index: dict[str, dict[str, Any]] | None = None,
    ) -> dict:
        username = user["username"]
        bundle = bundle or self._state_store.read_bundle()
        if event_activity_index is None:
            event_activity_index = self._build_event_activity_index(bundle["events"])
        actor = _normalized_username(username)
        window_start = utc_now() - timedelta(days=STATS_WINDOW_DAYS)
        stats = {
            "cards_opened": 0,
            "cards_created": 0,
            "cards_archived": 0,
            "card_moves": 0,
            "repair_orders_updated": 0,
            "attachments_added": 0,
            "attachments_removed": 0,
            "board_actions_total": 0,
        }
        action_entries: list[dict[str, Any]] = []
        for item in self._prune_action_history(user.get(ACTION_HISTORY_KEY)):
            timestamp = parse_datetime(item.get("timestamp"))
            if timestamp is None or timestamp < window_start:
                continue
            action = str(item.get("action") or "").strip() or "operator_action"
            if action == "card_opened":
                stats["cards_opened"] += 1
            action_entries.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "action": action,
                    "message": str(item.get("message") or "Действие оператора.").strip() or "Действие оператора.",
                    "card_id": str(item.get("card_id") or "").strip(),
                }
            )
        event_activity = event_activity_index.get(actor)
        if event_activity:
            event_stats = event_activity.get("stats") or {}
            for key in (
                "cards_created",
                "cards_archived",
                "card_moves",
                "repair_orders_updated",
                "attachments_added",
                "attachments_removed",
                "board_actions_total",
            ):
                stats[key] += normalize_int(event_stats.get(key), default=0, minimum=0)
            action_entries.extend(event_activity.get("actions") or [])
        action_entries = self._sort_action_entries(action_entries, reverse=True)
        recent_actions = action_entries[:12]
        stats["activity_total"] = stats["board_actions_total"] + stats["cards_opened"]
        return {"stats": stats, "recent_actions": recent_actions, "all_actions": action_entries}

    def _build_event_activity_index(self, events: list[Any]) -> dict[str, dict[str, Any]]:
        window_start = utc_now() - timedelta(days=STATS_WINDOW_DAYS)
        index: dict[str, dict[str, Any]] = {}
        for event in events:
            actor = _normalized_username(getattr(event, "actor_name", ""))
            if not actor:
                continue
            timestamp = parse_datetime(getattr(event, "timestamp", None))
            if timestamp is None or timestamp < window_start:
                continue
            payload = index.setdefault(
                actor,
                {
                    "stats": {
                        "cards_created": 0,
                        "cards_archived": 0,
                        "card_moves": 0,
                        "repair_orders_updated": 0,
                        "attachments_added": 0,
                        "attachments_removed": 0,
                        "board_actions_total": 0,
                    },
                    "actions": [],
                },
            )
            payload["stats"]["board_actions_total"] += 1
            stat_key = ACTION_TO_STAT_KEY.get(getattr(event, "action", ""))
            if stat_key:
                payload["stats"][stat_key] += 1
            payload["actions"].append(
                {
                    "timestamp": timestamp.isoformat(),
                    "action": getattr(event, "action", ""),
                    "message": getattr(event, "message", ""),
                    "card_id": getattr(event, "card_id", ""),
                }
            )
        return index

    def get_user_report(self, payload: dict | None = None) -> dict:
        self._required_admin_session(payload)
        payload = payload or {}
        username = self._validated_username(payload.get("username"))
        with self._lock:
            state = self._read_normalized_state()
            user = self._user_snapshot(state["users"], username)
        if user is None:
            self._fail("not_found", "Пользователь не найден.", status_code=404)
        bundle = self._state_store.read_bundle()
        user_payload = self._user_payload_with_stats(user, bundle=bundle)
        return {
            "username": user["username"],
            "file_name": self._user_report_file_name(user["username"]),
            "text": self._build_user_report_text(user, user_payload),
            "stats": user_payload["stats"],
            "meta": {"window_days": STATS_WINDOW_DAYS},
        }

    def _validated_username(self, value) -> str:
        username = _normalized_username(value)
        if not username:
            self._fail("validation_error", "Нужно указать логин.", details={"field": "username"})
        return username

    def _validated_password(self, value) -> str:
        password = str(value or "").strip()
        if len(password) < PASSWORD_MIN_LENGTH:
            self._fail(
                "validation_error",
                f"Пароль должен содержать минимум {PASSWORD_MIN_LENGTH} символа.",
                details={"field": "password"},
            )
        return password

    def _validated_role(self, value) -> str:
        role = str(value or "operator").strip().lower()
        if role not in USER_ROLE_VALUES:
            self._fail("validation_error", "Некорректная роль пользователя.", details={"field": "role"})
        return role

    def _sort_action_entries(self, entries: list[dict[str, Any]], *, reverse: bool = False) -> list[dict[str, Any]]:
        fallback_timestamp = utc_now()
        return sorted(
            entries,
            key=lambda item: (
                parse_datetime(item.get("timestamp")) or fallback_timestamp,
                str(item.get("action") or ""),
                str(item.get("card_id") or ""),
            ),
            reverse=reverse,
        )

    def _prune_action_history(self, raw_history) -> list[dict[str, Any]]:
        if not isinstance(raw_history, list):
            raw_history = []
        window_start = utc_now() - timedelta(days=ACTION_HISTORY_RETENTION_DAYS)
        normalized: list[dict[str, Any]] = []
        for item in raw_history:
            if not isinstance(item, dict):
                continue
            timestamp = parse_datetime(item.get("timestamp"))
            if timestamp is None or timestamp < window_start:
                continue
            normalized.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "action": str(item.get("action") or "").strip() or "operator_action",
                    "message": str(item.get("message") or "").strip() or "Действие оператора.",
                    "card_id": str(item.get("card_id") or "").strip(),
                }
            )
        normalized = self._sort_action_entries(normalized)
        return normalized[-400:]

    def _user_report_file_name(self, username: str) -> str:
        return f"operator-report-{_normalized_username(username).lower()}-15-days.txt"

    def _build_user_report_text(self, user: dict[str, Any], stats_payload: dict[str, Any]) -> str:
        stats = stats_payload.get("stats") or {}
        actions = stats_payload.get("all_actions") or []
        lines = [
            "ОТЧЁТ ПО ПОЛЬЗОВАТЕЛЮ",
            f"Пользователь: {user.get('username') or '-'}",
            f"Роль: {'АДМИНИСТРАТОР' if user.get('role') == 'admin' else 'ОПЕРАТОР'}",
            f"Окно статистики: последние {STATS_WINDOW_DAYS} дней",
            "",
            "СВОДКА",
            f"- Открыто карточек: {stats.get('cards_opened', 0)}",
            f"- Создано карточек: {stats.get('cards_created', 0)}",
            f"- Закрыто карточек: {stats.get('cards_archived', 0)}",
            f"- Перемещений: {stats.get('card_moves', 0)}",
            f"- Обновлений заказ-нарядов: {stats.get('repair_orders_updated', 0)}",
            f"- Добавлено вложений: {stats.get('attachments_added', 0)}",
            f"- Удалено вложений: {stats.get('attachments_removed', 0)}",
            f"- Всего действий на доске: {stats.get('board_actions_total', 0)}",
            "",
            "ОСНОВНЫЕ ДЕЙСТВИЯ",
        ]
        if not actions:
            lines.append("- Действий за выбранный период нет.")
        else:
            for item in actions:
                timestamp = parse_datetime(item.get("timestamp"))
                formatted = timestamp.strftime("%d.%m.%Y %H:%M:%S") if timestamp else str(item.get("timestamp") or "-")
                action = str(item.get("action") or "-").strip() or "-"
                message = str(item.get("message") or "Действие оператора.").strip() or "Действие оператора."
                card_id = str(item.get("card_id") or "").strip()
                suffix = f" | card_id={card_id}" if card_id else ""
                lines.append(f"- {formatted} | {action} | {message}{suffix}")
        return "\n".join(lines) + "\n"

    def _default_state(self) -> dict[str, Any]:
        now_iso = utc_now_iso()
        return {
            "schema_version": 1,
            "users": [
                {
                    "username": _normalized_username(get_default_admin_username()),
                    "password_hash": _password_hash(get_default_admin_password()),
                    "role": "admin",
                    "created_at": now_iso,
                    "updated_at": now_iso,
                    "stats": {OPEN_COUNT_KEY: 0},
                    ACTION_HISTORY_KEY: [],
                }
            ],
            "sessions": [],
        }

    def _read_normalized_state(self) -> dict[str, Any]:
        if not self._users_file.exists():
            state = self._default_state()
            self._write_state(state)
            return state
        changed = False
        try:
            payload = json.loads(self._users_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            backup = self._users_file.with_suffix(".corrupted.json")
            if backup.exists():
                backup.unlink()
            self._users_file.replace(backup)
            state = self._default_state()
            self._write_state(state)
            return state
        if not isinstance(payload, dict):
            payload = {}
            changed = True
        raw_users = payload.get("users")
        raw_sessions = payload.get("sessions")
        users = self._normalize_users(raw_users)
        user_names = {user["username"] for user in users}
        sessions = self._normalize_sessions(raw_sessions, valid_usernames=user_names)
        if not isinstance(raw_users, list) or len(users) != len(raw_users):
            changed = True
        if not isinstance(raw_sessions, list) or len(sessions) != len(raw_sessions):
            changed = True
        if not any(user["role"] == "admin" for user in users):
            default_admin = self._default_state()["users"][0]
            users.insert(0, default_admin)
            user_names.add(default_admin["username"])
            changed = True
        state = {
            "schema_version": 1,
            "users": users,
            "sessions": sessions,
        }
        if changed or payload.get("schema_version") != 1:
            self._write_state(state)
        return state

    def _normalize_users(self, raw_users) -> list[dict[str, Any]]:
        if not isinstance(raw_users, list):
            raw_users = []
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw_users:
            if not isinstance(item, dict):
                continue
            username = _normalized_username(item.get("username"))
            password_hash = str(item.get("password_hash") or "").strip()
            role = str(item.get("role") or "operator").strip().lower()
            if not username or not password_hash or role not in USER_ROLE_VALUES or username in seen:
                continue
            stats = item.get("stats")
            normalized.append(
                {
                    "username": username,
                    "password_hash": password_hash,
                    "role": role,
                    "created_at": (parse_datetime(item.get("created_at")) or utc_now()).isoformat(),
                    "updated_at": (parse_datetime(item.get("updated_at")) or parse_datetime(item.get("created_at")) or utc_now()).isoformat(),
                    "stats": {
                        OPEN_COUNT_KEY: normalize_int((stats or {}).get(OPEN_COUNT_KEY), default=0, minimum=0)
                    },
                    ACTION_HISTORY_KEY: self._prune_action_history(item.get(ACTION_HISTORY_KEY)),
                }
            )
            seen.add(username)
        return normalized

    def _normalize_sessions(self, raw_sessions, *, valid_usernames: set[str]) -> list[dict[str, Any]]:
        if not isinstance(raw_sessions, list):
            raw_sessions = []
        now = utc_now()
        normalized: list[dict[str, Any]] = []
        seen_tokens: set[str] = set()
        for item in raw_sessions:
            if not isinstance(item, dict):
                continue
            token = str(item.get("token") or "").strip()
            username = _normalized_username(item.get("username"))
            expires_at = parse_datetime(item.get("expires_at"))
            created_at = parse_datetime(item.get("created_at")) or utc_now()
            if not token or token in seen_tokens or username not in valid_usernames or expires_at is None or expires_at <= now:
                continue
            normalized.append(
                {
                    "token": token,
                    "username": username,
                    "created_at": created_at.isoformat(),
                    "expires_at": expires_at.isoformat(),
                }
            )
            seen_tokens.add(token)
        return normalized

    def _write_state(self, state: dict[str, Any]) -> None:
        payload = json.dumps(state, ensure_ascii=False, indent=2)
        temp_file = self._users_file.with_suffix(".tmp")
        temp_file.write_text(payload, encoding="utf-8")
        temp_file.replace(self._users_file)

    def _user_snapshot(self, users: list[dict[str, Any]], username) -> dict[str, Any] | None:
        user = self._find_user(users, username)
        if user is None:
            return None
        return deepcopy(user)

    def _find_user(self, users: list[dict[str, Any]], username) -> dict[str, Any] | None:
        normalized = _normalized_username(username)
        for user in users:
            if user.get("username") == normalized:
                return user
        return None

    def _fail(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        raise ServiceError(code, message, status_code=status_code, details=details)
