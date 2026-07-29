from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import threading
from copy import deepcopy
from datetime import timedelta
from logging import Logger
from pathlib import Path
from typing import Any

from .config import (
    get_app_data_dir,
    get_default_admin_password,
    get_default_admin_username,
    get_users_file,
)
from .models import (
    VALID_TAG_COLORS,
    normalize_actor_name,
    normalize_int,
    normalize_tag_color,
    normalize_tag_label,
    normalize_text,
    parse_datetime,
    utc_now,
    utc_now_iso,
)
from .operator_activity import OperatorActivityService
from .services.card_service import CardService
from .services.errors import ServiceError
from .storage.change_feed_projection import project_operator_users
from .storage.file_lock import ProcessFileLock
from .storage.json_store import JsonStore
from .storage.limited_io import read_text_limited

USER_ROLE_VALUES = frozenset({"operator", "admin"})
OPERATOR_AUTH_STATE_MAX_BYTES = 1 * 1024 * 1024
PASSWORD_MIN_LENGTH = 4
PASSWORD_HASH_ITERATIONS = 200_000
PASSWORD_HASH_MAX_ITERATIONS = 1_000_000
SESSION_TTL_DAYS = 30
STATS_WINDOW_DAYS = 15
OPEN_COUNT_KEY = "cards_opened"
OPERATOR_STAT_MAX = 1_000_000_000
ACTION_HISTORY_KEY = "action_history"
ACTION_HISTORY_RETENTION_DAYS = 15
PERSONAL_BOARD_PREFERENCES_KEY = "board_preferences"
EXTRA_BOARD_COLUMN_DEFAULT_TAG_LABEL = "НАДО ЧТО ТО СДЕЛАТЬ"
EXTRA_BOARD_COLUMN_DEFAULT_TAG_COLOR = "red"
LEGACY_DEFAULT_ADMIN_PASSWORDS = ("admin123",)
INSECURE_DEFAULT_ADMIN_PASSWORDS = ("admin", *LEGACY_DEFAULT_ADMIN_PASSWORDS)
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
        if not raw_iterations.isdecimal() or len(raw_iterations) > len(
            str(PASSWORD_HASH_MAX_ITERATIONS)
        ):
            return False
        iterations = int(raw_iterations)
    except (OverflowError, ValueError):
        return False
    if (
        algorithm != "pbkdf2_sha256"
        or iterations < 1
        or iterations > PASSWORD_HASH_MAX_ITERATIONS
        or not salt
        or not expected
    ):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return hmac.compare_digest(actual, expected)


def _truthy_env(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


class OperatorAuthService:
    def __init__(
        self,
        state_store: JsonStore,
        card_service: CardService,
        *,
        users_file: Path | None = None,
        activity_service: OperatorActivityService | None = None,
        logger: Logger | None = None,
    ) -> None:
        self._state_store = state_store
        self._card_service = card_service
        self._activity_service = activity_service
        self._uses_default_users_file = users_file is None
        self._users_file = users_file or get_users_file()
        self._logger = logger
        self._change_feed_store = state_store.change_feed_store
        self._lock = threading.RLock()
        self._process_lock = ProcessFileLock(self._users_file.with_suffix(".lock"))
        get_app_data_dir().mkdir(parents=True, exist_ok=True)
        self._users_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._users_file.exists():
            with self._process_lock.acquire():
                self._write_state(self._bootstrap_state())
        self._sync_change_feed(self._read_normalized_state(), initialize=True)

    def login(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        username = self._validated_username(payload.get("username"))
        password = self._validated_password(payload.get("password"))
        with self._lock:
            state = self._read_normalized_state()
            user = self._find_user(state["users"], username)
            password_hash = str(user.get("password_hash", "")) if user is not None else ""
            password_ok = user is not None and _verify_password(password, password_hash)
            if (
                not password_ok
                and user is not None
                and self._can_upgrade_default_admin_password(user, password, password_hash)
            ):
                user["password_hash"] = _password_hash(password)
                user["updated_at"] = utc_now_iso()
                password_ok = True
            if user is None or not password_ok:
                self._fail("unauthorized", "Неверный логин или пароль.", status_code=401)
            token = secrets.token_urlsafe(32)
            now = utc_now()
            state["sessions"] = [
                session for session in state["sessions"] if session.get("token") != token
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
        profile = self._build_profile_payload(snapshot, token=token)
        self._record_activity_safe(
            username=snapshot["username"],
            module="auth",
            action="login",
            action_label="Вошел в систему",
            object_type="operator",
            object_id=snapshot["username"],
            object_label=snapshot["username"],
            summary="Вход оператора в CRM.",
            source=str(payload.get("source") or "ui"),
        )
        return profile

    def logout(self, payload: dict | None = None) -> dict:
        session = self._required_session(payload)
        with self._lock:
            state = self._read_normalized_state()
            before = len(state["sessions"])
            state["sessions"] = [
                item for item in state["sessions"] if item.get("token") != session["token"]
            ]
            if len(state["sessions"]) != before:
                self._write_state(state)
        self._record_activity_safe(
            username=session["username"],
            module="auth",
            action="logout",
            action_label="Вышел из системы",
            object_type="operator",
            object_id=session["username"],
            object_label=session["username"],
            summary="Выход оператора из CRM.",
            source=str((payload or {}).get("source") or "ui"),
        )
        return {"logged_out": True}

    def _can_upgrade_default_admin_password(
        self, user: dict[str, Any], password: str, password_hash: str
    ) -> bool:
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
        if session.get("service_identity"):
            self._fail(
                "forbidden",
                "Личный профиль оператора доступен только в сеансе оператора.",
                status_code=403,
                details={"auth_type": "operator_session"},
            )
        with self._lock:
            state = self._read_normalized_state()
            user = self._find_user(state["users"], session["username"])
            if user is None:
                self._fail(
                    "unauthorized", "Сессия больше не связана с пользователем.", status_code=401
                )
            snapshot = deepcopy(user)
        return self._build_profile_payload(snapshot, token=session["token"])

    def update_personal_board_preferences(self, payload: dict | None = None) -> dict:
        """Save only the authenticated operator's board-view preferences.

        These preferences deliberately live with the operator account rather
        than the shared board settings. A personal virtual column therefore
        never changes what another operator sees on the common board.
        """

        session = self._required_session(payload)
        if session.get("service_identity"):
            self._fail(
                "forbidden",
                "Личные настройки доски доступны только в сеансе оператора.",
                status_code=403,
                details={"auth_type": "operator_session"},
            )
        payload = payload or {}
        preferences = self._validated_personal_board_preferences(
            payload.get(PERSONAL_BOARD_PREFERENCES_KEY)
        )
        with self._lock:
            state = self._read_normalized_state()
            user = self._find_user(state["users"], session["username"])
            if user is None:
                self._fail(
                    "unauthorized",
                    "Сессия больше не связана с пользователем.",
                    status_code=401,
                )
            default_preferences = self._default_personal_board_preferences()
            previous = self._personal_board_preferences(user)
            stored_preferences = user.get(PERSONAL_BOARD_PREFERENCES_KEY)
            should_store_preferences = preferences != default_preferences
            changed = (
                previous != preferences
                or (should_store_preferences and stored_preferences != preferences)
                or (not should_store_preferences and PERSONAL_BOARD_PREFERENCES_KEY in user)
            )
            if changed:
                if should_store_preferences:
                    user[PERSONAL_BOARD_PREFERENCES_KEY] = preferences
                else:
                    user.pop(PERSONAL_BOARD_PREFERENCES_KEY, None)
                self._write_state(state)

        return {
            PERSONAL_BOARD_PREFERENCES_KEY: preferences,
            "meta": {"changed": changed},
        }

    def list_users(self, payload: dict | None = None) -> dict:
        self._required_admin_session(payload)
        with self._lock:
            state = self._read_normalized_state()
            users = [deepcopy(item) for item in state["users"]]
        bundle = self._state_store.read_bundle()
        event_activity_index = self._build_event_activity_index(bundle["events"])
        rows = [
            self._serialize_user_summary(
                user, bundle=bundle, event_activity_index=event_activity_index
            )
            for user in users
        ]
        rows.sort(key=lambda item: (0 if item["role"] == "admin" else 1, item["username"]))
        return {"users": rows, "meta": {"total": len(rows)}}

    def save_user(self, payload: dict | None = None) -> dict:
        session = self._required_admin_session(payload)
        payload = payload or {}
        username = self._validated_username(payload.get("username"))
        password_provided = "password" in payload and str(payload.get("password") or "").strip()
        requested_role = self._validated_role(payload.get("role")) if "role" in payload else None
        now_iso = utc_now_iso()
        with self._lock:
            state = self._read_normalized_state()
            existing = self._find_user(state["users"], username)
            created = existing is None
            if created:
                password = self._validated_password(payload.get("password"))
                existing = {
                    "username": username,
                    "password_hash": _password_hash(password),
                    "role": requested_role or "operator",
                    "created_at": now_iso,
                    "updated_at": now_iso,
                    "employee_id": "",
                    "stats": {OPEN_COUNT_KEY: 0},
                    ACTION_HISTORY_KEY: [],
                }
                state["users"].append(existing)
            else:
                if password_provided:
                    existing["password_hash"] = _password_hash(
                        self._validated_password(payload.get("password"))
                    )
                next_role = requested_role or existing.get("role") or "operator"
                if (
                    existing.get("role") == "admin"
                    and next_role != "admin"
                    and sum(1 for user in state["users"] if user.get("role") == "admin") <= 1
                ):
                    self._fail(
                        "validation_error",
                        "Нельзя снять права с последнего администратора.",
                        status_code=409,
                    )
                existing["role"] = next_role
                existing["updated_at"] = now_iso
                existing["employee_id"] = normalize_text(
                    existing.get("employee_id"), default="", limit=64
                )
                if not isinstance(existing.get("stats"), dict):
                    existing["stats"] = {OPEN_COUNT_KEY: 0}
                if not isinstance(existing.get(ACTION_HISTORY_KEY), list):
                    existing[ACTION_HISTORY_KEY] = []
                if password_provided:
                    current_session_token = (
                        str(session.get("token") or "") if session["username"] == username else ""
                    )
                    state["sessions"] = [
                        item
                        for item in state["sessions"]
                        if item.get("username") != username
                        or (
                            current_session_token
                            and hmac.compare_digest(
                                str(item.get("token") or ""), current_session_token
                            )
                        )
                    ]
            self._write_state(state)
            snapshot = deepcopy(existing)
        self._record_activity_safe(
            username=session["username"],
            module="admin",
            action="operator_user_saved",
            action_label="Сохранил пользователя",
            object_type="operator",
            object_id=snapshot["username"],
            object_label=snapshot["username"],
            summary=(
                "Создан пользователь."
                if created
                else "Обновлены права пользователя."
                if requested_role is not None and not password_provided
                else "Обновлены пользователь и права."
                if requested_role is not None
                else "Обновлен пароль пользователя."
            ),
            source=str(payload.get("source") or "ui"),
        )
        return {
            "user": self._serialize_user_summary(snapshot),
            "meta": {
                "created": created,
                "updated": not created,
            },
        }

    def set_user_employee(self, payload: dict | None = None) -> dict:
        session = self._required_admin_session(payload)
        payload = payload or {}
        username = self._validated_username(payload.get("username"))
        employee_id = normalize_text(payload.get("employee_id"), default="", limit=64)
        employee = self._employee_for_binding(employee_id) if employee_id else None
        now_iso = utc_now_iso()
        with self._lock:
            state = self._read_normalized_state()
            target = self._find_user(state["users"], username)
            if target is None:
                self._fail("not_found", "Пользователь не найден.", status_code=404)
            if employee_id:
                duplicate = next(
                    (
                        user
                        for user in state["users"]
                        if user.get("username") != username
                        and normalize_text(user.get("employee_id"), default="", limit=64)
                        == employee_id
                    ),
                    None,
                )
                if duplicate is not None:
                    self._fail(
                        "validation_error",
                        "Этот сотрудник уже привязан к другому пользователю.",
                        status_code=409,
                        details={
                            "field": "employee_id",
                            "employee_id": employee_id,
                            "username": duplicate.get("username", ""),
                        },
                    )
            target["employee_id"] = employee_id
            target["updated_at"] = now_iso
            self._write_state(state)
            snapshot = deepcopy(target)
        self._record_activity_safe(
            username=session["username"],
            module="admin",
            action="operator_user_employee_bound"
            if employee_id
            else "operator_user_employee_unbound",
            action_label="Привязал сотрудника" if employee_id else "Отвязал сотрудника",
            object_type="operator",
            object_id=snapshot["username"],
            object_label=snapshot["username"],
            summary=(
                f"Пользователь привязан к сотруднику {employee.get('name', '')}."
                if employee
                else "Привязка сотрудника к пользователю снята."
            ),
            source=str(payload.get("source") or "ui"),
            details={"employee_id": employee_id},
        )
        return {
            "user": self._serialize_user_summary(snapshot),
            "employee": employee,
            "meta": {"bound": bool(employee_id)},
        }

    def delete_user(self, payload: dict | None = None) -> dict:
        session = self._required_admin_session(payload)
        payload = payload or {}
        username = self._validated_username(payload.get("username"))
        if username == session["username"]:
            self._fail(
                "validation_error",
                "Нельзя удалить текущую активную учётную запись.",
                status_code=409,
            )
        with self._lock:
            state = self._read_normalized_state()
            target = self._find_user(state["users"], username)
            if target is None:
                self._fail("not_found", "Пользователь не найден.", status_code=404)
            if target["role"] == "admin":
                admins_total = sum(1 for user in state["users"] if user.get("role") == "admin")
                if admins_total <= 1:
                    self._fail(
                        "validation_error",
                        "Нельзя удалить последнего администратора.",
                        status_code=409,
                    )
            state["users"] = [user for user in state["users"] if user.get("username") != username]
            state["sessions"] = [
                item for item in state["sessions"] if item.get("username") != username
            ]
            self._write_state(state)
        self._record_activity_safe(
            username=session["username"],
            module="admin",
            action="operator_user_deleted",
            action_label="Удалил пользователя",
            object_type="operator",
            object_id=username,
            object_label=username,
            summary="Удалена учетная запись оператора.",
            source=str(payload.get("source") or "ui"),
        )
        return {"deleted": True, "username": username}

    def open_card(self, payload: dict | None = None) -> dict:
        session = self._required_session(payload)
        payload = payload or {}
        card_id = str(payload.get("card_id", "") or "").strip()
        if not card_id:
            self._fail("validation_error", "Нужно передать card_id.", details={"field": "card_id"})
        actor_name = session["username"]
        mark_seen = self._validated_optional_bool(payload, "mark_seen", default=True)
        return_card = self._validated_optional_bool(payload, "return_card", default=True)
        marked_seen = False
        if mark_seen:
            seen_result = self._card_service.mark_card_seen(
                {"card_id": card_id, "actor_name": actor_name}
            )
            marked_seen = bool((seen_result.get("meta") or {}).get("changed"))
        result = (
            self._card_service.get_card({"card_id": card_id, "actor_name": actor_name})
            if return_card
            else {
                "card_id": card_id,
                "opened": True,
                "meta": {
                    "return_card": False,
                    "mark_seen": mark_seen,
                    "marked_seen": marked_seen,
                },
            }
        )
        self._record_user_action(
            session["username"],
            action="card_opened",
            message="Открыл карточку.",
            card_id=card_id,
            counter_key=OPEN_COUNT_KEY,
        )
        self._record_activity_safe(
            username=session["username"],
            module="card",
            action="card_opened",
            action_label="Открыл карточку",
            object_type="card",
            object_id=card_id,
            object_label=self._card_activity_label(card_id),
            summary="Просмотр без изменения данных",
            source=str(payload.get("source") or "ui"),
            details={"card_id": card_id, "marked_seen": marked_seen},
        )
        return result

    def list_activity(self, payload: dict | None = None) -> dict:
        session = self._required_session(payload)
        service = self._required_activity_service()
        return service.list_activity(self._activity_payload_for_session(payload, session))

    def get_activity_details(self, payload: dict | None = None) -> dict:
        session = self._required_session(payload)
        service = self._required_activity_service()
        result = service.get_activity_details(payload)
        if (
            not session.get("is_admin")
            and result["activity"].get("username") != session["username"]
        ):
            self._fail(
                "forbidden",
                "Нельзя просматривать действия другого пользователя.",
                status_code=403,
                details={"auth_type": "operator_session"},
            )
        return result

    def get_activity_aggregates(self, payload: dict | None = None) -> dict:
        session = self._required_session(payload)
        service = self._required_activity_service()
        return service.get_activity_aggregates(self._activity_payload_for_session(payload, session))

    def export_activity(self, payload: dict | None = None) -> dict:
        session = self._required_session(payload)
        service = self._required_activity_service()
        scoped_payload = self._activity_payload_for_session(payload, session)
        result = service.export_activity(scoped_payload)
        self._record_activity_safe(
            username=session["username"],
            module="admin" if session.get("is_admin") else "activity",
            action="operator_activity_exported",
            action_label="Экспортировал журнал",
            object_type="operator_activity",
            object_id=str(scoped_payload.get("username") or "all"),
            object_label=str(scoped_payload.get("username") or "Все пользователи"),
            summary="Экспорт журнала действий операторов.",
            source=str((payload or {}).get("source") or "ui"),
        )
        return result

    def resolve_session(self, token: str | None) -> dict | None:
        raw_token = str(token or "").strip()
        if not raw_token:
            return None
        with self._lock:
            state = self._read_normalized_state()
            for item in state["sessions"]:
                if not hmac.compare_digest(str(item.get("token") or ""), raw_token):
                    continue
                user = self._find_user(state["users"], item.get("username"))
                if user is None:
                    return None
                return self._session_payload(token=raw_token, user=user)
        return None

    def resolve_oauth_audit_admin(self, username: str | None) -> str | None:
        """Resolve a current administrator solely for trusted audit attribution.

        This does not create a human session or grant privileges.  The local
        Gateway keeps using its service principal for authorization; the API
        uses this narrow lookup only after it verifies an OAuth-bound internal
        assertion.
        """

        normalized = _normalized_username(username)
        if not normalized:
            return None
        with self._lock:
            state = self._read_normalized_state()
            user = self._find_user(state["users"], normalized)
            if user is None or user.get("role") != "admin":
                return None
            return str(user["username"])

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
                stats[counter_key] = min(
                    OPERATOR_STAT_MAX,
                    normalize_int(
                        stats.get(counter_key),
                        default=0,
                        minimum=0,
                        maximum=OPERATOR_STAT_MAX,
                    )
                    + 1,
                )
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

    def _record_activity_safe(
        self,
        *,
        username: str,
        module: str,
        action: str,
        action_label: str,
        object_type: str = "",
        object_id: str = "",
        object_label: str = "",
        summary: str = "",
        amount: str = "",
        source: str = "ui",
        severity: str = "normal",
        details: dict[str, Any] | None = None,
    ) -> None:
        if self._activity_service is None:
            return
        payload: dict[str, Any] = {
            "username": username,
            "module": module,
            "action": action,
            "action_label": action_label,
            "object_type": object_type,
            "object_id": object_id,
            "object_label": object_label,
            "summary": summary,
            "amount": amount,
            "source": source,
            "severity": severity,
        }
        if details:
            payload["details"] = details
        try:
            self._activity_service.record_activity(payload)
        except Exception as exc:
            if self._logger is not None:
                self._logger.warning(
                    "operator_activity_record_failed username=%s action=%s error=%s",
                    username,
                    action,
                    exc,
                )

    def _required_activity_service(self) -> OperatorActivityService:
        if self._activity_service is None:
            self._fail("not_found", "Журнал действий операторов недоступен.", status_code=404)
        return self._activity_service

    def _activity_payload_for_session(self, payload: dict | None, session: dict) -> dict:
        payload = dict(payload or {})
        requested_username = _normalized_username(payload.get("username"))
        if session.get("is_admin"):
            if requested_username:
                payload["username"] = requested_username
            return payload
        if requested_username and requested_username != session["username"]:
            self._fail(
                "forbidden",
                "Нельзя просматривать действия другого пользователя.",
                status_code=403,
                details={"auth_type": "operator_session"},
            )
        payload["username"] = session["username"]
        return payload

    def _card_activity_label(self, card_id: str) -> str:
        try:
            bundle = self._state_store.read_bundle()
            for card in bundle.get("cards", []):
                if getattr(card, "id", "") != card_id:
                    continue
                title = str(getattr(card, "title", "") or "").strip()
                vehicle = str(getattr(card, "vehicle", "") or "").strip()
                return " / ".join(part for part in (vehicle, title) if part) or card_id
        except Exception as exc:
            if self._logger is not None:
                self._logger.warning(
                    "operator_activity_card_label_failed card_id=%s error=%s", card_id, exc
                )
        return card_id

    def _user_base_payload(self, user: dict[str, Any]) -> dict[str, Any]:
        return {
            "username": user["username"],
            "role": user["role"],
            "is_admin": user["role"] == "admin",
            "created_at": user["created_at"],
            "updated_at": user["updated_at"],
            "employee_id": normalize_text(user.get("employee_id"), default="", limit=64),
        }

    @staticmethod
    def _default_personal_board_preferences() -> dict[str, Any]:
        return {
            "extra_column": {
                "is_open": False,
                "is_detached": False,
                "position": {"x": 0, "y": 0},
                "filter": {
                    "tag_label": EXTRA_BOARD_COLUMN_DEFAULT_TAG_LABEL,
                    "tag_color": EXTRA_BOARD_COLUMN_DEFAULT_TAG_COLOR,
                },
            }
        }

    @staticmethod
    def _normalized_stored_bool(value: Any, *, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y"}:
                return True
            if normalized in {"false", "0", "no", "n"}:
                return False
        return default

    def _personal_board_preferences(self, user: dict[str, Any]) -> dict[str, Any]:
        """Return a safe, complete personal-view preference payload.

        Legacy operator records have no preferences, and malformed persisted
        values must never prevent an operator from logging in.
        """

        defaults = self._default_personal_board_preferences()
        raw = user.get(PERSONAL_BOARD_PREFERENCES_KEY)
        source = raw if isinstance(raw, dict) else {}
        raw_extra_column = source.get("extra_column")
        extra_column = raw_extra_column if isinstance(raw_extra_column, dict) else {}
        raw_filter = extra_column.get("filter")
        filter_payload = raw_filter if isinstance(raw_filter, dict) else {}
        tag_label = normalize_tag_label(filter_payload.get("tag_label"))
        if not tag_label:
            tag_label = defaults["extra_column"]["filter"]["tag_label"]
        raw_color = str(filter_payload.get("tag_color") or "").strip().lower()
        tag_color = (
            normalize_tag_color(raw_color)
            if raw_color in VALID_TAG_COLORS
            else defaults["extra_column"]["filter"]["tag_color"]
        )
        raw_position = extra_column.get("position")
        position = raw_position if isinstance(raw_position, dict) else {}
        return {
            "extra_column": {
                "is_open": self._normalized_stored_bool(extra_column.get("is_open"), default=False),
                "is_detached": self._normalized_stored_bool(
                    extra_column.get("is_detached"), default=False
                ),
                "position": {
                    "x": normalize_int(position.get("x"), default=0, minimum=0, maximum=100_000),
                    "y": normalize_int(position.get("y"), default=0, minimum=0, maximum=100_000),
                },
                "filter": {"tag_label": tag_label, "tag_color": tag_color},
            }
        }

    def _validated_personal_board_preferences(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            self._fail(
                "validation_error",
                "Нужно передать личные настройки доски объектом.",
                details={"field": PERSONAL_BOARD_PREFERENCES_KEY},
            )
        extra_column = value.get("extra_column")
        if not isinstance(extra_column, dict):
            self._fail(
                "validation_error",
                "Нужно передать настройки дополнительной колонки объектом.",
                details={"field": "board_preferences.extra_column"},
            )
        is_open = extra_column.get("is_open")
        if not isinstance(is_open, bool):
            self._fail(
                "validation_error",
                "Параметр открытия дополнительной колонки должен иметь тип boolean.",
                details={"field": "board_preferences.extra_column.is_open"},
            )
        is_detached = extra_column.get("is_detached", False)
        if not isinstance(is_detached, bool):
            self._fail(
                "validation_error",
                "Параметр открепления дополнительной колонки должен иметь тип boolean.",
                details={"field": "board_preferences.extra_column.is_detached"},
            )
        raw_position = extra_column.get("position", {"x": 0, "y": 0})
        if not isinstance(raw_position, dict):
            self._fail(
                "validation_error",
                "Позиция дополнительной колонки должна быть объектом.",
                details={"field": "board_preferences.extra_column.position"},
            )
        position: dict[str, int] = {}
        for axis in ("x", "y"):
            raw_value = raw_position.get(axis, 0)
            if isinstance(raw_value, bool):
                self._fail(
                    "validation_error",
                    "Координаты дополнительной колонки должны быть целыми числами.",
                    details={"field": f"board_preferences.extra_column.position.{axis}"},
                )
            try:
                numeric_value = float(raw_value)
            except (OverflowError, TypeError, ValueError):
                numeric_value = -1
            if not numeric_value.is_integer() or numeric_value < 0 or numeric_value > 100_000:
                self._fail(
                    "validation_error",
                    "Координаты дополнительной колонки должны быть от 0 до 100000.",
                    details={"field": f"board_preferences.extra_column.position.{axis}"},
                )
            position[axis] = int(numeric_value)
        filter_payload = extra_column.get("filter")
        if not isinstance(filter_payload, dict):
            self._fail(
                "validation_error",
                "Нужно передать фильтр дополнительной колонки объектом.",
                details={"field": "board_preferences.extra_column.filter"},
            )
        tag_label = normalize_tag_label(filter_payload.get("tag_label"))
        if not tag_label:
            self._fail(
                "validation_error",
                "Нужно выбрать метку для дополнительной колонки.",
                details={"field": "board_preferences.extra_column.filter.tag_label"},
            )
        raw_color = filter_payload.get("tag_color")
        tag_color = str(raw_color or "").strip().lower()
        if tag_color not in VALID_TAG_COLORS:
            self._fail(
                "validation_error",
                "Цвет метки дополнительной колонки не поддерживается.",
                details={"field": "board_preferences.extra_column.filter.tag_color"},
            )
        return {
            "extra_column": {
                "is_open": is_open,
                "is_detached": is_detached,
                "position": position,
                "filter": {"tag_label": tag_label, "tag_color": tag_color},
            }
        }

    def _session_payload(self, *, token: str, user: dict[str, Any]) -> dict[str, Any]:
        return {
            "token": token,
            "username": user["username"],
            "role": user["role"],
            "is_admin": user["role"] == "admin",
            "employee_id": normalize_text(user.get("employee_id"), default="", limit=64),
        }

    def _build_profile_payload(self, user: dict[str, Any], *, token: str) -> dict:
        user_payload = self._user_payload_with_stats(user)
        return {
            "session": self._session_payload(token=token, user=user),
            "user": user_payload["user"],
            "stats": user_payload["stats"],
            "recent_actions": user_payload["recent_actions"],
            "security": self._security_payload(user),
            PERSONAL_BOARD_PREFERENCES_KEY: self._personal_board_preferences(user),
        }

    def _security_payload(self, user: dict[str, Any]) -> dict[str, Any]:
        using_default_admin_credentials = self._uses_default_admin_credentials(user)
        warning = ""
        if using_default_admin_credentials:
            warning = (
                "Используется небезопасный дефолтный админ-доступ. Перед постоянной публикацией CRM "
                "смените MINIMAL_KANBAN_DEFAULT_ADMIN_USERNAME и "
                "MINIMAL_KANBAN_DEFAULT_ADMIN_PASSWORD, затем выполните deploy."
            )
        return {
            "using_default_admin_credentials": using_default_admin_credentials,
            "warning": warning,
        }

    def _uses_default_admin_credentials(self, user: dict[str, Any]) -> bool:
        if user.get("role") != "admin":
            return False
        if user.get("username") != _normalized_username(get_default_admin_username()):
            return False
        password_hash = str(user.get("password_hash") or "")
        if not password_hash:
            return False
        default_password = get_default_admin_password()
        if default_password in INSECURE_DEFAULT_ADMIN_PASSWORDS and _verify_password(
            default_password, password_hash
        ):
            return True
        return any(
            _verify_password(legacy_password, password_hash)
            for legacy_password in LEGACY_DEFAULT_ADMIN_PASSWORDS
        )

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
        action_entries, opened_cards = self._collect_user_action_entries(
            username, window_start=window_start, fallback_history=user.get(ACTION_HISTORY_KEY)
        )
        stats["cards_opened"] += opened_cards
        event_activity = event_activity_index.get(actor)
        self._merge_event_activity_stats(stats, action_entries, event_activity=event_activity)
        action_entries = self._sort_action_entries(action_entries, reverse=True)
        recent_actions = action_entries[:12]
        stats["activity_total"] = stats["board_actions_total"] + stats["cards_opened"]
        return {"stats": stats, "recent_actions": recent_actions, "all_actions": action_entries}

    def _collect_user_action_entries(
        self,
        username: str,
        *,
        window_start,
        fallback_history,
    ) -> tuple[list[dict[str, Any]], int]:
        action_entries: list[dict[str, Any]] = []
        opened_cards = 0
        activity_rows = self._recent_activity_rows(username)
        rows = activity_rows if activity_rows else self._prune_action_history(fallback_history)
        for item in rows:
            timestamp = parse_datetime(item.get("timestamp"))
            if timestamp is None or timestamp < window_start:
                continue
            action = str(item.get("action") or "").strip() or "operator_action"
            if action == "card_opened":
                opened_cards += 1
            message = (
                str(
                    item.get("summary") or item.get("action_label") or "Действие оператора."
                ).strip()
                if activity_rows
                else str(item.get("message") or "Действие оператора.").strip()
            ) or "Действие оператора."
            card_id = (
                str(item.get("object_id") or "").strip()
                if activity_rows and item.get("object_type") == "card"
                else str(item.get("card_id") or "").strip()
            )
            action_entries.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "action": action,
                    "message": message,
                    "card_id": card_id,
                }
            )
        return action_entries, opened_cards

    def _merge_event_activity_stats(
        self,
        stats: dict[str, int],
        action_entries: list[dict[str, Any]],
        *,
        event_activity: dict[str, Any] | None,
    ) -> None:
        if not event_activity:
            return
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
            stats[key] = min(
                OPERATOR_STAT_MAX,
                stats[key]
                + normalize_int(
                    event_stats.get(key),
                    default=0,
                    minimum=0,
                    maximum=OPERATOR_STAT_MAX,
                ),
            )
        action_entries.extend(event_activity.get("actions") or [])

    def _recent_activity_rows(self, username: str) -> list[dict[str, Any]]:
        if self._activity_service is None:
            return []
        try:
            result = self._activity_service.list_activity(
                {"username": username, "days": STATS_WINDOW_DAYS, "limit": 400}
            )
            rows = result.get("activities")
            return rows if isinstance(rows, list) else []
        except Exception as exc:
            if self._logger is not None:
                self._logger.warning(
                    "operator_activity_profile_stats_failed username=%s error=%s",
                    username,
                    exc,
                )
            return []

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

    def _employee_for_binding(self, employee_id: str) -> dict[str, Any]:
        try:
            result = self._card_service.list_employees({})
        except ServiceError:
            raise
        except Exception as exc:
            self._fail(
                "internal_error",
                "Не удалось прочитать список сотрудников.",
                status_code=500,
                details={"error": str(exc)},
            )
        employees = result.get("employees")
        if not isinstance(employees, list):
            employees = []
        employee = next(
            (
                item
                for item in employees
                if isinstance(item, dict)
                and normalize_text(item.get("id"), default="", limit=64) == employee_id
            ),
            None,
        )
        if employee is None:
            self._fail(
                "not_found",
                "Сотрудник не найден.",
                status_code=404,
                details={"field": "employee_id", "employee_id": employee_id},
            )
        if not employee.get("is_active"):
            self._fail(
                "validation_error",
                "Нельзя привязать выключенного сотрудника.",
                status_code=409,
                details={"field": "employee_id", "employee_id": employee_id},
            )
        return deepcopy(employee)

    def _validated_role(self, value) -> str:
        role = str(value or "operator").strip().lower()
        if role not in USER_ROLE_VALUES:
            self._fail(
                "validation_error", "Некорректная роль пользователя.", details={"field": "role"}
            )
        return role

    def _validated_optional_bool(self, payload: dict, field: str, *, default: bool) -> bool:
        if field not in payload:
            return default
        value = payload.get(field)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "y"}:
                return True
            if lowered in {"false", "0", "no", "n"}:
                return False
        self._fail(
            "validation_error",
            f"Поле {field} должно иметь тип boolean.",
            details={"field": field},
        )

    def _sort_action_entries(
        self, entries: list[dict[str, Any]], *, reverse: bool = False
    ) -> list[dict[str, Any]]:
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
                formatted = (
                    timestamp.strftime("%d.%m.%Y %H:%M:%S")
                    if timestamp
                    else str(item.get("timestamp") or "-")
                )
                action = str(item.get("action") or "-").strip() or "-"
                message = (
                    str(item.get("message") or "Действие оператора.").strip()
                    or "Действие оператора."
                )
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
                    "employee_id": "",
                    "stats": {OPEN_COUNT_KEY: 0},
                    ACTION_HISTORY_KEY: [],
                }
            ],
            "sessions": [],
        }

    def _empty_bootstrap_state(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "users": [],
            "sessions": [],
            "bootstrap_required": True,
        }

    def _bootstrap_state(self) -> dict[str, Any]:
        if self._can_bootstrap_default_admin():
            return self._default_state()
        return self._empty_bootstrap_state()

    def _can_bootstrap_default_admin(self) -> bool:
        if not self._uses_default_users_file:
            return True
        if _truthy_env("MINIMAL_KANBAN_ALLOW_INSECURE_DEFAULT_ADMIN"):
            return True
        return get_default_admin_password() not in INSECURE_DEFAULT_ADMIN_PASSWORDS

    def _corrupted_users_backup_path(self) -> Path:
        backup = self._users_file.with_suffix(".corrupted.json")
        if not backup.exists():
            return backup
        stem = self._users_file.with_suffix("").name
        for index in range(2, 1000):
            candidate = self._users_file.with_name(f"{stem}.corrupted-{index}.json")
            if not candidate.exists():
                return candidate
        return self._users_file.with_name(
            f"{stem}.corrupted-{utc_now().strftime('%Y%m%d%H%M%S%f')}.json"
        )

    def _read_normalized_state(self) -> dict[str, Any]:
        if not self._users_file.exists():
            state = self._bootstrap_state()
            self._write_state(state)
            return state
        changed = False
        try:
            payload = json.loads(
                self._read_users_text(),
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError, RecursionError):
            backup = self._corrupted_users_backup_path()
            self._users_file.replace(backup)
            state = self._bootstrap_state()
            self._write_state(state)
            return state
        if not isinstance(payload, dict):
            backup = self._corrupted_users_backup_path()
            self._users_file.replace(backup)
            state = self._bootstrap_state()
            self._write_state(state)
            return state
        raw_users = payload.get("users")
        raw_sessions = payload.get("sessions")
        users = self._normalize_users(raw_users)
        user_names = {user["username"] for user in users}
        sessions = self._normalize_sessions(raw_sessions, valid_usernames=user_names)
        users_corrupted = not isinstance(raw_users, list) or len(users) != len(raw_users)
        if users_corrupted:
            changed = True
        if not isinstance(raw_sessions, list) or len(sessions) != len(raw_sessions):
            changed = True
        if (
            not any(user["role"] == "admin" for user in users)
            and self._can_bootstrap_default_admin()
        ):
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
            if users_corrupted:
                shutil.copy2(self._users_file, self._corrupted_users_backup_path())
            self._write_state(state)
        return state

    def _read_users_text(self) -> str:
        return read_text_limited(
            self._users_file,
            max_bytes=OPERATOR_AUTH_STATE_MAX_BYTES,
            label="operator users file",
        )

    def _state_payload_text(self, state: dict[str, Any]) -> str:
        payload = json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False)
        if len(payload.encode("utf-8")) > OPERATOR_AUTH_STATE_MAX_BYTES:
            raise ValueError("operator users file is too large")
        return payload

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
            if (
                not username
                or not password_hash
                or role not in USER_ROLE_VALUES
                or username in seen
            ):
                continue
            stats = item.get("stats")
            employee_id = normalize_text(item.get("employee_id"), default="", limit=64)
            normalized_user = {
                "username": username,
                "password_hash": password_hash,
                "role": role,
                "created_at": (parse_datetime(item.get("created_at")) or utc_now()).isoformat(),
                "updated_at": (
                    parse_datetime(item.get("updated_at"))
                    or parse_datetime(item.get("created_at"))
                    or utc_now()
                ).isoformat(),
                "employee_id": employee_id,
                "stats": {
                    OPEN_COUNT_KEY: normalize_int(
                        (stats or {}).get(OPEN_COUNT_KEY),
                        default=0,
                        minimum=0,
                        maximum=OPERATOR_STAT_MAX,
                    )
                },
                ACTION_HISTORY_KEY: self._prune_action_history(item.get(ACTION_HISTORY_KEY)),
            }
            preferences = self._personal_board_preferences(item)
            if preferences != self._default_personal_board_preferences():
                normalized_user[PERSONAL_BOARD_PREFERENCES_KEY] = preferences
            normalized.append(normalized_user)
            seen.add(username)
        return normalized

    def _normalize_sessions(
        self, raw_sessions, *, valid_usernames: set[str]
    ) -> list[dict[str, Any]]:
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
            if (
                not token
                or token in seen_tokens
                or username not in valid_usernames
                or expires_at is None
                or expires_at <= now
            ):
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
        payload = self._state_payload_text(state)
        temp_file = self._users_file.with_name(
            f".{self._users_file.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            temp_file.write_text(payload, encoding="utf-8")
            temp_file.replace(self._users_file)
            self._sync_change_feed(state)
        finally:
            temp_file.unlink(missing_ok=True)

    def reconcile_change_feed(self) -> None:
        with self._lock:
            self._sync_change_feed(self._read_normalized_state())

    def _sync_change_feed(self, state: dict[str, Any], *, initialize: bool = False) -> None:
        projected = project_operator_users(state)
        try:
            if initialize:
                self._change_feed_store.initialize_external_projection("operator_users", projected)
            else:
                self._change_feed_store.reconcile_external_projection("operator_users", projected)
        except Exception as exc:  # pragma: no cover - next feed read reconciles users file
            if self._logger is not None:
                self._logger.warning("operator_change_feed_deferred error=%s", exc)

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
