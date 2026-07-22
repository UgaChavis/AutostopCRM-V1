from __future__ import annotations

import hmac
import json
import math
import os
import secrets
import threading
import time
from logging import Logger
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from cryptography.fernet import Fernet, InvalidToken
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
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
ACCESS_TOKEN_TTL_SECONDS = 60 * 60
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
OAUTH_EXPIRATION_MAX_SECONDS = 4_102_444_800  # 2100-01-01T00:00:00Z
OAUTH_STATE_MAX_BYTES = 2 * 1024 * 1024
OAUTH_STATE_PLAINTEXT_MAX_BYTES = 1 * 1024 * 1024
CHATGPT_OAUTH_REDIRECT_PATH_PREFIX = "/connector/oauth/"
CHATGPT_LEGACY_OAUTH_REDIRECT_PATH = "/connector_platform_oauth_redirect"
OAUTH_CONSENT_PATH = "/oauth/authorize"
OAUTH_STATE_KEY_ENV = "AUTOSTOP_MCP_OAUTH_STATE_KEY"


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON numeric constant: {value}")


def _is_production() -> bool:
    return str(os.environ.get("AUTOSTOP_DEPLOYMENT_ENV") or "").strip().casefold() in {
        "prod",
        "production",
    }


class OwnerAuthorizationCode(AuthorizationCode):
    subject: str


class OwnerAccessToken(AccessToken):
    subject: str
    family_id: str


class OwnerRefreshToken(RefreshToken):
    subject: str
    family_id: str
    resource: str


class ProductionOAuthAuthorizationServerProvider(
    OAuthAuthorizationServerProvider[OwnerAuthorizationCode, OwnerRefreshToken, OwnerAccessToken]
):
    """Owner-approved OAuth 2.1 provider for the AutoStop CRM MCP resource.

    Authorization never auto-approves. ``authorize`` creates a short-lived
    transaction which the custom consent route completes only after an active
    CRM administrator signs in. OAuth client metadata and opaque credentials
    are encrypted at rest with a deployment key kept outside this state file.
    """

    def __init__(
        self,
        *,
        issuer_url: str,
        resource_url: str,
        legacy_bearer_token: str | None = None,
        state_file: Path | None = None,
        state_key: str | bytes | None = None,
        logger: Logger | None = None,
    ) -> None:
        self._issuer_url = issuer_url.rstrip("/")
        self._resource_url = resource_url.rstrip("/")
        self._legacy_bearer_token = (legacy_bearer_token or "").strip()
        self._state_file = state_file or get_mcp_oauth_state_file()
        self._logger = logger
        self._lock = threading.RLock()
        self._process_lock = ProcessFileLock(self._state_file.with_suffix(".lock"))
        self._cipher = Fernet(self._resolve_state_key(state_key))
        get_app_data_dir().mkdir(parents=True, exist_ok=True)
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._protect_path(self._state_file.parent, directory=True)
        if not self._state_file.exists():
            with self._process_lock.acquire():
                if not self._state_file.exists():
                    self._write_state_unlocked(self._default_state())

    @property
    def issuer_url(self) -> str:
        return self._issuer_url

    @property
    def resource_url(self) -> str:
        return self._resource_url

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
        requested = self._scope_values(client_info.scope)
        if requested and set(requested) != _DEFAULT_KANBAN_SCOPE_SET:
            raise RegistrationError(
                "invalid_client_metadata",
                "client must request the complete AutoStop CRM scope set",
            )

        with self._lock:
            with self._process_lock.acquire():
                state = self._prune_state(self._read_state_unlocked())
                state["clients"][client_info.client_id] = client_info.model_dump(
                    mode="json", exclude_none=True
                )
                self._write_state_unlocked(state)
        self._log("oauth.register_client completed=true")

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if not client.client_id:
            raise AuthorizeError("invalid_request", "client_id is required")
        resource = str(params.resource or self._resource_url).strip().rstrip("/")
        if not hmac.compare_digest(resource, self._resource_url):
            raise AuthorizeError("invalid_request", "resource does not match the MCP audience")
        scopes = self._validated_complete_scopes(params.scopes or client.scope)
        if not str(params.code_challenge or "").strip():
            raise AuthorizeError("invalid_request", "PKCE S256 code_challenge is required")

        request_id = self._generate_secret("mkar")
        pending = {
            "request_id": request_id,
            "client_id": client.client_id,
            "client_name": str(client.client_name or "Codex / ChatGPT"),
            "scopes": scopes,
            "expires_at": time.time() + AUTHORIZATION_CODE_TTL_SECONDS,
            "code_challenge": params.code_challenge,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "resource": self._resource_url,
            "state": params.state,
        }
        with self._lock:
            with self._process_lock.acquire():
                state = self._prune_state(self._read_state_unlocked())
                state["pending_authorizations"][self._token_digest(request_id)] = pending
                self._write_state_unlocked(state)
        return f"{self._issuer_url}{OAUTH_CONSENT_PATH}?{urlencode({'request_id': request_id})}"

    def get_pending_authorization(self, request_id: str) -> dict[str, object] | None:
        secret = str(request_id or "").strip()
        if not secret:
            return None
        state = self._read_state()
        payload = state["pending_authorizations"].get(self._token_digest(secret))
        if not isinstance(payload, dict):
            return None
        if self._float_or_zero(payload.get("expires_at")) <= time.time():
            return None
        return {
            "client_name": str(payload.get("client_name") or "Codex / ChatGPT")[:120],
            "scopes": self._validated_complete_scopes(payload.get("scopes"), token_error=True),
            "expires_at": payload.get("expires_at"),
        }

    def approve_authorization(self, request_id: str, *, subject: str) -> str:
        secret = str(request_id or "").strip()
        normalized_subject = str(subject or "").strip()
        if not secret or not normalized_subject:
            raise AuthorizeError("access_denied", "administrator approval is required")

        with self._lock:
            with self._process_lock.acquire():
                state = self._prune_state(self._read_state_unlocked())
                payload = state["pending_authorizations"].pop(self._token_digest(secret), None)
                if not isinstance(payload, dict):
                    raise AuthorizeError(
                        "invalid_request", "authorization request is missing or expired"
                    )
                if self._float_or_zero(payload.get("expires_at")) <= time.time():
                    self._write_state_unlocked(state)
                    raise AuthorizeError("invalid_request", "authorization request has expired")
                if str(payload.get("resource") or "").rstrip("/") != self._resource_url:
                    self._write_state_unlocked(state)
                    raise AuthorizeError("invalid_request", "authorization audience mismatch")
                scopes = self._validated_complete_scopes(payload.get("scopes"))
                code_value = self._generate_secret("mkac")
                authorization_code = OwnerAuthorizationCode(
                    code=code_value,
                    scopes=scopes,
                    expires_at=time.time() + AUTHORIZATION_CODE_TTL_SECONDS,
                    client_id=str(payload.get("client_id") or ""),
                    code_challenge=str(payload.get("code_challenge") or ""),
                    redirect_uri=str(payload.get("redirect_uri") or ""),
                    redirect_uri_provided_explicitly=bool(
                        payload.get("redirect_uri_provided_explicitly")
                    ),
                    resource=self._resource_url,
                    subject=normalized_subject,
                )
                state["authorization_codes"][self._token_digest(code_value)] = (
                    self._stored_secret_model(authorization_code)
                )
                self._write_state_unlocked(state)

        self._log("oauth.authorize approved=true")
        return construct_redirect_uri(
            str(authorization_code.redirect_uri),
            code=code_value,
            state=payload.get("state"),
        )

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> OwnerAuthorizationCode | None:
        secret = str(authorization_code or "").strip()
        state = self._read_state()
        payload = state["authorization_codes"].get(self._token_digest(secret))
        if not isinstance(payload, dict):
            return None
        try:
            loaded = OwnerAuthorizationCode.model_validate({**payload, "code": secret})
        except ValidationError:
            return None
        if loaded.client_id != client.client_id or loaded.resource != self._resource_url:
            return None
        return loaded

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: OwnerAuthorizationCode,
    ) -> OAuthToken:
        if not client.client_id:
            raise TokenError("invalid_client", "client_id is required")

        token_error: TokenError | None = None
        with self._lock:
            with self._process_lock.acquire():
                state = self._prune_state(self._read_state_unlocked())
                stored_code = self._load_stored_authorization_code(state, authorization_code.code)
                if stored_code is None or stored_code.client_id != client.client_id:
                    token_error = TokenError("invalid_grant", "authorization code does not exist")
                elif stored_code.expires_at < time.time():
                    state["authorization_codes"].pop(self._token_digest(stored_code.code), None)
                    self._write_state_unlocked(state)
                    token_error = TokenError("invalid_grant", "authorization code has expired")
                elif stored_code.resource != self._resource_url:
                    token_error = TokenError("invalid_target", "authorization audience mismatch")
                else:
                    scopes = self._validated_complete_scopes(stored_code.scopes, token_error=True)
                    family_id = self._generate_secret("mkaf")
                    access_token = self._issue_access_token(
                        client_id=client.client_id,
                        subject=stored_code.subject,
                        scopes=scopes,
                        family_id=family_id,
                    )
                    refresh_token = self._issue_refresh_token(
                        client_id=client.client_id,
                        subject=stored_code.subject,
                        scopes=scopes,
                        family_id=family_id,
                    )
                    state["authorization_codes"].pop(self._token_digest(stored_code.code), None)
                    state["access_tokens"][self._token_digest(access_token.token)] = (
                        self._stored_secret_model(access_token)
                    )
                    state["refresh_tokens"][self._token_digest(refresh_token.token)] = (
                        self._stored_secret_model(refresh_token)
                    )
                    self._write_state_unlocked(state)

        if token_error is not None:
            raise token_error
        self._log("oauth.exchange_code completed=true")
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
    ) -> OwnerRefreshToken | None:
        secret = str(refresh_token or "").strip()
        state = self._read_state()
        payload = state["refresh_tokens"].get(self._token_digest(secret))
        if not isinstance(payload, dict):
            return None
        try:
            loaded = OwnerRefreshToken.model_validate({**payload, "token": secret})
        except ValidationError:
            return None
        if loaded.client_id != client.client_id or loaded.resource != self._resource_url:
            return None
        return loaded

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: OwnerRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if not client.client_id:
            raise TokenError("invalid_client", "client_id is required")

        token_error: TokenError | None = None
        with self._lock:
            with self._process_lock.acquire():
                state = self._prune_state(self._read_state_unlocked())
                stored_refresh = self._load_stored_refresh_token(state, refresh_token.token)
                if stored_refresh is None or stored_refresh.client_id != client.client_id:
                    token_error = TokenError("invalid_grant", "refresh token does not exist")
                elif stored_refresh.expires_at and stored_refresh.expires_at < int(time.time()):
                    state["refresh_tokens"].pop(self._token_digest(stored_refresh.token), None)
                    self._write_state_unlocked(state)
                    token_error = TokenError("invalid_grant", "refresh token has expired")
                elif stored_refresh.resource != self._resource_url:
                    token_error = TokenError("invalid_target", "refresh token audience mismatch")
                else:
                    try:
                        normalized_scopes = self._validated_complete_scopes(
                            scopes or stored_refresh.scopes, token_error=True
                        )
                    except TokenError as exc:
                        token_error = exc
                    else:
                        access_token = self._issue_access_token(
                            client_id=client.client_id,
                            subject=stored_refresh.subject,
                            scopes=normalized_scopes,
                            family_id=stored_refresh.family_id,
                        )
                        rotated_refresh = self._issue_refresh_token(
                            client_id=client.client_id,
                            subject=stored_refresh.subject,
                            scopes=normalized_scopes,
                            family_id=stored_refresh.family_id,
                        )
                        state["refresh_tokens"].pop(self._token_digest(stored_refresh.token), None)
                        state["access_tokens"][self._token_digest(access_token.token)] = (
                            self._stored_secret_model(access_token)
                        )
                        state["refresh_tokens"][self._token_digest(rotated_refresh.token)] = (
                            self._stored_secret_model(rotated_refresh)
                        )
                        self._write_state_unlocked(state)

        if token_error is not None:
            raise token_error
        self._log("oauth.exchange_refresh completed=true")
        return OAuthToken(
            access_token=access_token.token,
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=rotated_refresh.token,
            scope=" ".join(normalized_scopes),
        )

    async def load_access_token(self, token: str) -> OwnerAccessToken | AccessToken | None:
        secret = str(token or "").strip()
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
        payload = state["access_tokens"].get(self._token_digest(secret))
        if not isinstance(payload, dict):
            return None
        try:
            loaded = OwnerAccessToken.model_validate({**payload, "token": secret})
        except ValidationError:
            return None
        if loaded.resource != self._resource_url:
            return None
        if loaded.expires_at and loaded.expires_at < int(time.time()):
            return None
        try:
            self._validated_complete_scopes(loaded.scopes, token_error=True)
        except TokenError:
            return None
        return loaded

    async def revoke_token(self, token: OwnerAccessToken | OwnerRefreshToken) -> None:
        removed = False
        with self._lock:
            with self._process_lock.acquire():
                state = self._prune_state(self._read_state_unlocked())
                family_id = str(getattr(token, "family_id", "") or "")
                for bucket_name in ("access_tokens", "refresh_tokens"):
                    bucket = state[bucket_name]
                    for key, payload in list(bucket.items()):
                        if not isinstance(payload, dict):
                            continue
                        same_secret = key == self._token_digest(token.token)
                        same_family = family_id and hmac.compare_digest(
                            str(payload.get("family_id") or ""), family_id
                        )
                        if same_secret or same_family:
                            bucket.pop(key, None)
                            removed = True
                if removed:
                    self._write_state_unlocked(state)
        if removed:
            self._log("oauth.revoke token_family_removed=true")

    def _issue_access_token(
        self, *, client_id: str, subject: str, scopes: list[str], family_id: str
    ) -> OwnerAccessToken:
        return OwnerAccessToken(
            token=self._generate_secret("mkat"),
            client_id=client_id,
            subject=subject,
            scopes=self._validated_complete_scopes(scopes, token_error=True),
            expires_at=int(time.time()) + ACCESS_TOKEN_TTL_SECONDS,
            resource=self._resource_url,
            family_id=family_id,
        )

    def _issue_refresh_token(
        self, *, client_id: str, subject: str, scopes: list[str], family_id: str
    ) -> OwnerRefreshToken:
        return OwnerRefreshToken(
            token=self._generate_secret("mkrt"),
            client_id=client_id,
            subject=subject,
            scopes=self._validated_complete_scopes(scopes, token_error=True),
            expires_at=int(time.time()) + REFRESH_TOKEN_TTL_SECONDS,
            resource=self._resource_url,
            family_id=family_id,
        )

    def _validated_complete_scopes(self, value: object, *, token_error: bool = False) -> list[str]:
        scopes = self._scope_values(value)
        if set(scopes) != _DEFAULT_KANBAN_SCOPE_SET:
            if token_error:
                raise TokenError("invalid_scope", "complete AutoStop CRM scopes are required")
            raise AuthorizeError("invalid_scope", "complete AutoStop CRM scopes are required")
        return [scope for scope in DEFAULT_KANBAN_SCOPES if scope in scopes]

    def _scope_values(self, value: object) -> list[str]:
        if isinstance(value, str):
            raw_values: object = value.replace(",", " ").split()
        elif isinstance(value, (list, tuple, set)):
            raw_values = value
        else:
            raw_values = []
        return list(
            dict.fromkeys(str(item or "").strip() for item in raw_values if str(item or "").strip())
        )

    def _validate_client_redirect_uris(self, client_info: OAuthClientInformationFull) -> None:
        redirect_uris = client_info.redirect_uris or []
        if not redirect_uris:
            raise RegistrationError("invalid_redirect_uri", "at least one redirect_uri is required")
        if any(not self._is_allowed_redirect_uri(uri) for uri in redirect_uris):
            raise RegistrationError(
                "invalid_redirect_uri",
                "redirect_uri must be a ChatGPT connector, local Codex, or configured Codex relay callback",
            )

    def _is_allowed_redirect_uri(self, redirect_uri: object) -> bool:
        raw_uri = str(redirect_uri or "").strip()
        if not raw_uri:
            return False
        try:
            parsed = urlsplit(raw_uri)
            port = parsed.port
        except ValueError:
            return False
        if parsed.username or parsed.password or parsed.fragment:
            return False
        if parsed.query:
            return False
        hostname = (parsed.hostname or "").casefold()
        if parsed.scheme.casefold() == "https" and hostname == "chatgpt.com" and port is None:
            return parsed.path == CHATGPT_LEGACY_OAUTH_REDIRECT_PATH or parsed.path.startswith(
                CHATGPT_OAUTH_REDIRECT_PATH_PREFIX
            )
        issuer = urlsplit(self._issuer_url)
        issuer_hostname = (issuer.hostname or "").casefold()
        if (
            parsed.scheme.casefold() == "https"
            and hostname == issuer_hostname
            and port is None
            and parsed.path == "/codex-oauth"
        ):
            return True
        relay_callback_id = parsed.path.removeprefix("/codex-oauth/callback/")
        if (
            parsed.scheme.casefold() == "https"
            and hostname == issuer_hostname
            and port is None
            and parsed.path.startswith("/codex-oauth/callback/")
            and len(relay_callback_id) == 12
            and all(
                character.isascii() and (character.isalnum() or character in "-_")
                for character in relay_callback_id
            )
        ):
            return True
        if parsed.scheme.casefold() != "http" or hostname not in {
            "127.0.0.1",
            "::1",
            "localhost",
        }:
            return False
        if port is None or not 1024 <= port <= 65535:
            return False
        if parsed.path == "/callback":
            return True
        callback_id = parsed.path.removeprefix("/callback/")
        return (
            parsed.path.startswith("/callback/")
            and len(callback_id) == 12
            and all(
                character.isascii() and (character.isalnum() or character in "-_")
                for character in callback_id
            )
        )

    def _load_stored_authorization_code(
        self, state: dict[str, object], code: str
    ) -> OwnerAuthorizationCode | None:
        payload = state["authorization_codes"].get(self._token_digest(code))
        if not isinstance(payload, dict):
            return None
        try:
            return OwnerAuthorizationCode.model_validate({**payload, "code": code})
        except ValidationError:
            return None

    def _load_stored_refresh_token(
        self, state: dict[str, object], token: str
    ) -> OwnerRefreshToken | None:
        payload = state["refresh_tokens"].get(self._token_digest(token))
        if not isinstance(payload, dict):
            return None
        try:
            return OwnerRefreshToken.model_validate({**payload, "token": token})
        except ValidationError:
            return None

    def _stored_secret_model(self, value: object) -> dict[str, object]:
        payload = value.model_dump(mode="json", exclude_none=True)
        secret = str(payload.get("token") or payload.get("code") or "")
        if "token" in payload:
            payload["token"] = self._token_digest(secret)
        if "code" in payload:
            payload["code"] = self._token_digest(secret)
        return payload

    def _read_state(self) -> dict[str, object]:
        with self._lock:
            with self._process_lock.acquire():
                state = self._read_state_unlocked()
                pruned = self._prune_state(state)
                if pruned != state:
                    self._write_state_unlocked(pruned)
                return pruned

    def _write_state(self, state: dict[str, object]) -> None:
        with self._lock:
            with self._process_lock.acquire():
                self._write_state_unlocked(self._prune_state(state))

    def _read_state_unlocked(self) -> dict[str, object]:
        if not self._state_file.exists():
            return self._default_state()
        try:
            raw = self._read_state_text()
            legacy_plaintext = raw.lstrip().startswith("{")
            if not legacy_plaintext:
                raw = self._cipher.decrypt(raw.encode("ascii")).decode("utf-8")
            payload = json.loads(raw, parse_constant=_reject_json_constant)
            reject_deeply_nested_json(payload)
        except (
            InvalidToken,
            OSError,
            UnicodeDecodeError,
            UnicodeEncodeError,
            ValueError,
            RecursionError,
        ):
            backup = self._corrupted_backup_path()
            self._state_file.replace(backup)
            self._log("oauth.state_corrupted backup=%s", backup.name)
            state = self._default_state()
            return state

        if not isinstance(payload, dict):
            return self._default_state()
        normalized = {
            "clients": payload.get("clients", {})
            if isinstance(payload.get("clients"), dict)
            else {},
            "pending_authorizations": payload.get("pending_authorizations", {})
            if isinstance(payload.get("pending_authorizations"), dict)
            else {},
            "authorization_codes": payload.get("authorization_codes", {})
            if isinstance(payload.get("authorization_codes"), dict)
            else {},
            "access_tokens": payload.get("access_tokens", {})
            if isinstance(payload.get("access_tokens"), dict)
            else {},
            "refresh_tokens": payload.get("refresh_tokens", {})
            if isinstance(payload.get("refresh_tokens"), dict)
            else {},
        }
        if legacy_plaintext:
            self._write_state_unlocked(normalized)
        return normalized

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

    def _state_payload_bytes(self, state: dict[str, object]) -> bytes:
        payload = json.dumps(
            self._json_safe_value(state),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(payload) > OAUTH_STATE_PLAINTEXT_MAX_BYTES:
            raise ValueError("OAuth state file is too large")
        encrypted = self._cipher.encrypt(payload)
        if len(encrypted) > OAUTH_STATE_MAX_BYTES:
            raise ValueError("Encrypted OAuth state file is too large")
        return encrypted

    def _write_state_unlocked(self, state: dict[str, object]) -> None:
        payload = self._state_payload_bytes(state)
        temp_file = self._state_file.with_name(
            f".{self._state_file.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            with temp_file.open("xb") as handle:
                os.chmod(temp_file, 0o600)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temp_file.replace(self._state_file)
            self._protect_path(self._state_file)
        finally:
            temp_file.unlink(missing_ok=True)

    def _prune_state(self, state: dict[str, object]) -> dict[str, object]:
        now = int(time.time())
        pruned = self._default_state()
        pruned["clients"] = dict(state.get("clients", {}))
        for bucket_name in (
            "pending_authorizations",
            "authorization_codes",
            "access_tokens",
            "refresh_tokens",
        ):
            bucket = state.get(bucket_name, {})
            if not isinstance(bucket, dict):
                continue
            for key, value in bucket.items():
                if not isinstance(value, dict):
                    continue
                expires_at = value.get("expires_at")
                if expires_at is None or self._int_or_zero(expires_at) >= now:
                    pruned[bucket_name][key] = value
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
        parsed = self._float_or_zero(value)
        if parsed <= 0:
            return 0
        try:
            return int(parsed)
        except (TypeError, ValueError, OverflowError):
            return 0

    def _default_state(self) -> dict[str, object]:
        return {
            "clients": {},
            "pending_authorizations": {},
            "authorization_codes": {},
            "access_tokens": {},
            "refresh_tokens": {},
        }

    def _resolve_state_key(self, supplied: str | bytes | None) -> bytes:
        raw = supplied or os.environ.get(OAUTH_STATE_KEY_ENV) or b""
        encoded = raw.encode("ascii") if isinstance(raw, str) else bytes(raw)
        if encoded:
            try:
                Fernet(encoded)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{OAUTH_STATE_KEY_ENV} is not a valid Fernet key") from exc
            return encoded
        if _is_production():
            raise ValueError(f"{OAUTH_STATE_KEY_ENV} is required in production")
        key_file = self._state_file.with_suffix(".key")
        if key_file.exists():
            encoded = key_file.read_bytes().strip()
            Fernet(encoded)
            return encoded
        encoded = Fernet.generate_key()
        key_file.write_bytes(encoded)
        self._protect_path(key_file)
        return encoded

    def _protect_path(self, path: Path, *, directory: bool = False) -> None:
        try:
            os.chmod(path, 0o700 if directory else 0o600)
        except OSError:
            if _is_production():
                raise

    def _token_digest(self, secret: str) -> str:
        import hashlib

        return hashlib.sha256(str(secret or "").encode("utf-8")).hexdigest()

    def _generate_secret(self, prefix: str) -> str:
        return f"{prefix}_{secrets.token_urlsafe(32)}"

    def _log(self, message: str, *args) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)


# Compatibility import for old local tests and callers. Production code uses
# the explicit production name and never enables the former auto-approved flow.
EmbeddedOAuthAuthorizationServerProvider = ProductionOAuthAuthorizationServerProvider


__all__ = [
    "ACCESS_TOKEN_TTL_SECONDS",
    "DEFAULT_KANBAN_SCOPES",
    "OAUTH_CONSENT_PATH",
    "OAUTH_STATE_KEY_ENV",
    "ProductionOAuthAuthorizationServerProvider",
]
