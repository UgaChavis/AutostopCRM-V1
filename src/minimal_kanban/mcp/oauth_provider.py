from __future__ import annotations

import hmac
import json
import math
import secrets
import threading
import time
from logging import Logger
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import ValidationError

from ..config import get_app_data_dir, get_mcp_oauth_state_file
from ..json_safety import reject_deeply_nested_json
from ..storage.file_lock import ProcessFileLock
from ..storage.limited_io import read_text_limited

DEFAULT_KANBAN_SCOPES = ("kanban:read", "kanban:write")
_DEFAULT_KANBAN_SCOPE_SET = set(DEFAULT_KANBAN_SCOPES)
AUTHORIZATION_CODE_TTL_SECONDS = 300
ACCESS_TOKEN_TTL_SECONDS = 12 * 60 * 60
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
OAUTH_EXPIRATION_MAX_SECONDS = 4_102_444_800  # 2100-01-01T00:00:00Z
OAUTH_STATE_MAX_BYTES = 1 * 1024 * 1024
CHATGPT_OAUTH_REDIRECT_PATH_PREFIX = "/connector/oauth/"
CHATGPT_LEGACY_OAUTH_REDIRECT_PATH = "/connector_platform_oauth_redirect"


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON numeric constant: {value}")


class EmbeddedOAuthAuthorizationServerProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Lightweight OAuth provider for ChatGPT MCP connector flows.

    The board is a single shared workspace with equal permissions for all users,
    so this provider intentionally auto-approves registered clients and issues
    scoped Bearer tokens for the MCP server itself. It is designed to satisfy
    MCP/ChatGPT connector requirements such as DCR, PKCE, resource metadata,
    and token verification without introducing a separate external IdP.
    """

    def __init__(
        self,
        *,
        issuer_url: str,
        resource_url: str,
        legacy_bearer_token: str | None = None,
        state_file: Path | None = None,
        logger: Logger | None = None,
    ) -> None:
        self._issuer_url = issuer_url.rstrip("/")
        self._resource_url = resource_url.rstrip("/")
        self._legacy_bearer_token = (legacy_bearer_token or "").strip()
        self._state_file = state_file or get_mcp_oauth_state_file()
        self._logger = logger
        self._lock = threading.RLock()
        self._process_lock = ProcessFileLock(self._state_file.with_suffix(".lock"))
        get_app_data_dir().mkdir(parents=True, exist_ok=True)
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._state_file.exists():
            with self._process_lock.acquire():
                if not self._state_file.exists():
                    self._write_state_unlocked(self._default_state())

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        state = self._read_state()
        payload = state["clients"].get(client_id)
        if not isinstance(payload, dict):
            return None
        try:
            return OAuthClientInformationFull.model_validate(payload)
        except ValidationError:
            return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise ValueError("client_id is required")
        self._validate_client_redirect_uris(client_info)

        state = self._read_state()
        state["clients"][client_info.client_id] = client_info.model_dump(
            mode="json", exclude_none=True
        )
        self._write_state(state)
        self._log("oauth.register_client client_id=%s", client_info.client_id)

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if not client.client_id:
            raise ValueError("client_id is required")

        scopes = self._normalize_scopes(params.scopes, client)
        code_value = self._generate_secret("mkac")
        authorization_code = AuthorizationCode(
            code=code_value,
            scopes=scopes,
            expires_at=time.time() + AUTHORIZATION_CODE_TTL_SECONDS,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource or self._resource_url,
        )

        state = self._read_state()
        state["authorization_codes"][code_value] = authorization_code.model_dump(
            mode="json", exclude_none=True
        )
        self._write_state(state)
        self._log(
            "oauth.authorize client_id=%s scopes=%s resource=%s",
            client.client_id,
            ",".join(scopes),
            authorization_code.resource,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code_value, state=params.state)

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        state = self._read_state()
        payload = state["authorization_codes"].get(authorization_code)
        if not isinstance(payload, dict):
            return None
        try:
            return AuthorizationCode.model_validate(payload)
        except ValidationError:
            return None

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        if not client.client_id:
            raise ValueError("client_id is required")

        token_error: TokenError | None = None
        with self._lock:
            with self._process_lock.acquire():
                state = self._prune_state(self._read_state_unlocked())
                stored_code = self._load_stored_authorization_code(state, authorization_code.code)
                if stored_code is None or stored_code.client_id != client.client_id:
                    token_error = TokenError(
                        "invalid_grant",
                        "authorization code does not exist",
                    )
                elif stored_code.expires_at < time.time():
                    state["authorization_codes"].pop(stored_code.code, None)
                    self._write_state_unlocked(state)
                    token_error = TokenError(
                        "invalid_grant",
                        "authorization code has expired",
                    )
                else:
                    access_token = self._issue_access_token(
                        client_id=client.client_id,
                        scopes=stored_code.scopes,
                        resource=stored_code.resource,
                    )
                    refresh_token = self._issue_refresh_token(
                        client_id=client.client_id,
                        scopes=stored_code.scopes,
                        resource=stored_code.resource,
                    )

                    state["authorization_codes"].pop(stored_code.code, None)
                    state["access_tokens"][access_token.token] = access_token.model_dump(
                        mode="json", exclude_none=True
                    )
                    state["refresh_tokens"][refresh_token.token] = refresh_token.model_dump(
                        mode="json", exclude_none=True
                    )
                    self._write_state_unlocked(state)

        if token_error is not None:
            raise token_error

        self._log(
            "oauth.exchange_code client_id=%s resource=%s",
            client.client_id,
            stored_code.resource,
        )
        return OAuthToken(
            access_token=access_token.token,
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=refresh_token.token,
            scope=" ".join(access_token.scopes),
        )

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        state = self._read_state()
        payload = state["refresh_tokens"].get(refresh_token)
        if not isinstance(payload, dict):
            return None
        try:
            return RefreshToken.model_validate(payload)
        except ValidationError:
            return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if not client.client_id:
            raise ValueError("client_id is required")

        token_error: TokenError | None = None
        with self._lock:
            with self._process_lock.acquire():
                state = self._prune_state(self._read_state_unlocked())
                stored_refresh = self._load_stored_refresh_token(state, refresh_token.token)
                if stored_refresh is None or stored_refresh.client_id != client.client_id:
                    token_error = TokenError("invalid_grant", "refresh token does not exist")
                elif stored_refresh.expires_at and stored_refresh.expires_at < int(time.time()):
                    state["refresh_tokens"].pop(stored_refresh.token, None)
                    self._write_state_unlocked(state)
                    token_error = TokenError("invalid_grant", "refresh token has expired")
                else:
                    try:
                        scopes = self._normalize_refresh_scopes(scopes, stored_refresh)
                    except TokenError as exc:
                        token_error = exc
                    else:
                        access_token = self._issue_access_token(
                            client_id=client.client_id,
                            scopes=scopes,
                            resource=self._resource_url,
                        )
                        rotated_refresh = self._issue_refresh_token(
                            client_id=client.client_id,
                            scopes=scopes,
                            resource=self._resource_url,
                        )

                        state["refresh_tokens"].pop(stored_refresh.token, None)
                        state["access_tokens"][access_token.token] = access_token.model_dump(
                            mode="json", exclude_none=True
                        )
                        state["refresh_tokens"][rotated_refresh.token] = rotated_refresh.model_dump(
                            mode="json", exclude_none=True
                        )
                        self._write_state_unlocked(state)

        if token_error is not None:
            raise token_error

        self._log(
            "oauth.exchange_refresh client_id=%s scopes=%s", client.client_id, ",".join(scopes)
        )
        return OAuthToken(
            access_token=access_token.token,
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=rotated_refresh.token,
            scope=" ".join(scopes),
        )

    def _load_stored_authorization_code(
        self, state: dict[str, dict], code: str
    ) -> AuthorizationCode | None:
        payload = state["authorization_codes"].get(code)
        if not isinstance(payload, dict):
            return None
        try:
            return AuthorizationCode.model_validate(payload)
        except ValidationError:
            return None

    def _load_stored_refresh_token(self, state: dict[str, dict], token: str) -> RefreshToken | None:
        payload = state["refresh_tokens"].get(token)
        if not isinstance(payload, dict):
            return None
        try:
            return RefreshToken.model_validate(payload)
        except ValidationError:
            return None

    def _normalize_refresh_scopes(
        self, requested_scopes: list[str], refresh_token: RefreshToken
    ) -> list[str]:
        scopes = self._normalize_scope_values(requested_scopes)
        if requested_scopes and not scopes:
            raise TokenError("invalid_scope", "requested scopes are not valid")

        refresh_scopes = self._normalize_scope_values(refresh_token.scopes)
        if scopes:
            refresh_scope_set = set(refresh_scopes)
            for scope in scopes:
                if scope not in refresh_scope_set:
                    raise TokenError(
                        "invalid_scope",
                        f"cannot request scope `{scope}` not provided by refresh token",
                    )
            return scopes
        return refresh_scopes

    def _validate_client_redirect_uris(self, client_info: OAuthClientInformationFull) -> None:
        redirect_uris = client_info.redirect_uris or []
        if not redirect_uris:
            raise RegistrationError(
                "invalid_redirect_uri",
                "at least one ChatGPT connector redirect_uri is required",
            )
        rejected = [
            str(redirect_uri)
            for redirect_uri in redirect_uris
            if not self._is_allowed_chatgpt_redirect_uri(redirect_uri)
        ]
        if rejected:
            raise RegistrationError(
                "invalid_redirect_uri",
                "redirect_uri must be a ChatGPT connector OAuth redirect URI",
            )

    def _is_allowed_chatgpt_redirect_uri(self, redirect_uri: object) -> bool:
        raw_uri = str(redirect_uri or "").strip()
        if not raw_uri:
            return False
        try:
            parsed = urlsplit(raw_uri)
        except ValueError:
            return False
        if parsed.scheme.lower() != "https":
            return False
        if (parsed.hostname or "").lower() != "chatgpt.com":
            return False
        try:
            port = parsed.port
        except ValueError:
            return False
        if port is not None or parsed.fragment:
            return False
        return parsed.path == CHATGPT_LEGACY_OAUTH_REDIRECT_PATH or parsed.path.startswith(
            CHATGPT_OAUTH_REDIRECT_PATH_PREFIX
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        secret = (token or "").strip()
        if not secret:
            return None

        if self._legacy_bearer_token and hmac.compare_digest(secret, self._legacy_bearer_token):
            return AccessToken(
                token=secret,
                client_id="minimal-kanban-legacy",
                scopes=list(DEFAULT_KANBAN_SCOPES),
                expires_at=None,
                resource=self._resource_url,
            )

        state = self._read_state()
        payload = state["access_tokens"].get(secret)
        if not isinstance(payload, dict):
            return None
        try:
            return AccessToken.model_validate(payload)
        except ValidationError:
            return None

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        state = self._read_state()
        removed = False
        if isinstance(token, AccessToken):
            removed = state["access_tokens"].pop(token.token, None) is not None
        else:
            removed = state["refresh_tokens"].pop(token.token, None) is not None
        if removed:
            self._write_state(state)
            self._log("oauth.revoke token_removed=true")

    def _issue_access_token(
        self, *, client_id: str, scopes: list[str], resource: str | None
    ) -> AccessToken:
        scopes = self._normalize_scope_values(scopes)
        return AccessToken(
            token=self._generate_secret("mkat"),
            client_id=client_id,
            scopes=list(scopes),
            expires_at=int(time.time()) + ACCESS_TOKEN_TTL_SECONDS,
            resource=resource or self._resource_url,
        )

    def _issue_refresh_token(
        self, *, client_id: str, scopes: list[str], resource: str | None
    ) -> RefreshToken:
        return RefreshToken(
            token=self._generate_secret("mkrt"),
            client_id=client_id,
            scopes=list(scopes),
            expires_at=int(time.time()) + REFRESH_TOKEN_TTL_SECONDS,
        )

    def _normalize_scopes(
        self,
        requested_scopes: list[str] | None,
        client: OAuthClientInformationFull,
    ) -> list[str]:
        scopes = self._normalize_scope_values(requested_scopes)
        if scopes:
            return scopes
        scopes = self._normalize_scope_values(client.scope)
        if scopes:
            return scopes
        return list(DEFAULT_KANBAN_SCOPES)

    def _normalize_scope_values(self, value: object) -> list[str]:
        if isinstance(value, str):
            raw_values: object = value.replace(",", " ").split()
        elif isinstance(value, (list, tuple, set)):
            raw_values = value
        else:
            raw_values = []
        scopes: list[str] = []
        seen: set[str] = set()
        for item in raw_values:
            scope = str(item or "").strip()
            if scope not in _DEFAULT_KANBAN_SCOPE_SET or scope in seen:
                continue
            seen.add(scope)
            scopes.append(scope)
        return scopes

    def _read_state(self) -> dict[str, dict]:
        with self._lock:
            with self._process_lock.acquire():
                state = self._read_state_unlocked()
                pruned = self._prune_state(state)
                if pruned != state:
                    self._write_state_unlocked(pruned)
                return pruned

    def _write_state(self, state: dict[str, dict]) -> None:
        with self._lock:
            with self._process_lock.acquire():
                self._write_state_unlocked(self._prune_state(state))

    def _read_state_unlocked(self) -> dict[str, dict]:
        if not self._state_file.exists():
            return self._default_state()
        try:
            payload = json.loads(
                self._read_state_text(),
                parse_constant=_reject_json_constant,
            )
            reject_deeply_nested_json(payload)
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            backup = self._corrupted_backup_path()
            self._state_file.replace(backup)
            self._log("oauth.state_corrupted backup=%s", backup.name)
            return self._default_state()

        if not isinstance(payload, dict):
            return self._default_state()

        return {
            "clients": payload.get("clients", {})
            if isinstance(payload.get("clients"), dict)
            else {},
            "authorization_codes": (
                payload.get("authorization_codes", {})
                if isinstance(payload.get("authorization_codes"), dict)
                else {}
            ),
            "access_tokens": payload.get("access_tokens", {})
            if isinstance(payload.get("access_tokens"), dict)
            else {},
            "refresh_tokens": (
                payload.get("refresh_tokens", {})
                if isinstance(payload.get("refresh_tokens"), dict)
                else {}
            ),
        }

    def _read_state_text(self) -> str:
        return read_text_limited(
            self._state_file,
            max_bytes=OAUTH_STATE_MAX_BYTES,
            label="OAuth state file",
        )

    def _corrupted_backup_path(self) -> Path:
        backup = self._state_file.with_suffix(".corrupted.json")
        if not backup.exists():
            return backup
        stem = self._state_file.with_suffix("").name
        for index in range(2, 1000):
            candidate = self._state_file.with_name(f"{stem}.corrupted-{index}.json")
            if not candidate.exists():
                return candidate
        return self._state_file.with_name(f"{stem}.corrupted-{time.time_ns()}.json")

    def _state_payload_text(self, state: dict[str, dict]) -> str:
        payload = json.dumps(
            self._json_safe_value(state),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        if len(payload.encode("utf-8")) > OAUTH_STATE_MAX_BYTES:
            raise ValueError("OAuth state file is too large")
        return payload

    def _write_state_unlocked(self, state: dict[str, dict]) -> None:
        payload = self._state_payload_text(state)
        temp_file = self._state_file.with_name(
            f".{self._state_file.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            temp_file.write_text(payload, encoding="utf-8")
            temp_file.replace(self._state_file)
        finally:
            temp_file.unlink(missing_ok=True)

    def _prune_state(self, state: dict[str, dict]) -> dict[str, dict]:
        now = int(time.time())
        pruned = self._default_state()
        pruned["clients"] = dict(state.get("clients", {}))

        for key, value in state.get("authorization_codes", {}).items():
            if not isinstance(value, dict):
                continue
            expires_at = self._float_or_zero(value.get("expires_at"))
            if expires_at > time.time():
                pruned["authorization_codes"][key] = value

        for key, value in state.get("access_tokens", {}).items():
            if not isinstance(value, dict):
                continue
            expires_at = value.get("expires_at")
            if expires_at is None or self._int_or_zero(expires_at) >= now:
                pruned["access_tokens"][key] = value

        for key, value in state.get("refresh_tokens", {}).items():
            if not isinstance(value, dict):
                continue
            expires_at = value.get("expires_at")
            if expires_at is None or self._int_or_zero(expires_at) >= now:
                pruned["refresh_tokens"][key] = value

        return pruned

    def _json_safe_value(self, value: object, *, depth: int = 8) -> object:
        if depth <= 0:
            return str(value)
        if value is None or isinstance(value, str | bool | int):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, dict):
            return {
                str(key): self._json_safe_value(item, depth=depth - 1)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe_value(item, depth=depth - 1) for item in value]
        return str(value)

    def _float_or_zero(self, value: object) -> float:
        if isinstance(value, bool):
            return 0.0
        try:
            parsed = float(0 if value is None or value == "" else value)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if not math.isfinite(parsed) or parsed > OAUTH_EXPIRATION_MAX_SECONDS:
            return 0.0
        return parsed

    def _int_or_zero(self, value: object) -> int:
        if isinstance(value, bool):
            return 0
        try:
            parsed = float(0 if value is None or value == "" else value)
        except (TypeError, ValueError, OverflowError):
            return 0
        if not math.isfinite(parsed) or parsed > OAUTH_EXPIRATION_MAX_SECONDS:
            return 0
        if parsed <= 0:
            return 0
        try:
            return int(parsed)
        except (TypeError, ValueError, OverflowError):
            return 0

    def _default_state(self) -> dict[str, dict]:
        return {
            "clients": {},
            "authorization_codes": {},
            "access_tokens": {},
            "refresh_tokens": {},
        }

    def _generate_secret(self, prefix: str) -> str:
        return f"{prefix}_{secrets.token_urlsafe(32)}"

    def _log(self, message: str, *args) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)
