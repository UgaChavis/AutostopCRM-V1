from __future__ import annotations

import json
import secrets
import threading
import time
from logging import Logger
from pathlib import Path

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from ..config import get_app_data_dir, get_mcp_oauth_state_file
from ..storage.file_lock import ProcessFileLock


DEFAULT_KANBAN_SCOPES = ("kanban:read", "kanban:write")
AUTHORIZATION_CODE_TTL_SECONDS = 300
ACCESS_TOKEN_TTL_SECONDS = 12 * 60 * 60
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60


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
        return OAuthClientInformationFull.model_validate(payload)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        state = self._read_state()
        if not client_info.client_id:
            raise ValueError("client_id is required")
        state["clients"][client_info.client_id] = client_info.model_dump(mode="json", exclude_none=True)
        self._write_state(state)
        self._log("oauth.register_client client_id=%s", client_info.client_id)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
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
        state["authorization_codes"][code_value] = authorization_code.model_dump(mode="json", exclude_none=True)
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
        return AuthorizationCode.model_validate(payload)

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        if not client.client_id:
            raise ValueError("client_id is required")

        access_token = self._issue_access_token(
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            resource=authorization_code.resource,
        )
        refresh_token = self._issue_refresh_token(
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            resource=authorization_code.resource,
        )

        state = self._read_state()
        state["authorization_codes"].pop(authorization_code.code, None)
        state["access_tokens"][access_token.token] = access_token.model_dump(mode="json", exclude_none=True)
        state["refresh_tokens"][refresh_token.token] = refresh_token.model_dump(mode="json", exclude_none=True)
        self._write_state(state)
        self._log(
            "oauth.exchange_code client_id=%s resource=%s",
            client.client_id,
            authorization_code.resource,
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
        return RefreshToken.model_validate(payload)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if not client.client_id:
            raise ValueError("client_id is required")

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

        state = self._read_state()
        state["refresh_tokens"].pop(refresh_token.token, None)
        state["access_tokens"][access_token.token] = access_token.model_dump(mode="json", exclude_none=True)
        state["refresh_tokens"][rotated_refresh.token] = rotated_refresh.model_dump(mode="json", exclude_none=True)
        self._write_state(state)
        self._log("oauth.exchange_refresh client_id=%s scopes=%s", client.client_id, ",".join(scopes))
        return OAuthToken(
            access_token=access_token.token,
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            refresh_token=rotated_refresh.token,
            scope=" ".join(scopes),
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        secret = (token or "").strip()
        if not secret:
            return None

        if self._legacy_bearer_token and secret == self._legacy_bearer_token:
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
        return AccessToken.model_validate(payload)

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

    def _issue_access_token(self, *, client_id: str, scopes: list[str], resource: str | None) -> AccessToken:
        return AccessToken(
            token=self._generate_secret("mkat"),
            client_id=client_id,
            scopes=list(scopes),
            expires_at=int(time.time()) + ACCESS_TOKEN_TTL_SECONDS,
            resource=resource or self._resource_url,
        )

    def _issue_refresh_token(self, *, client_id: str, scopes: list[str], resource: str | None) -> RefreshToken:
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
        if requested_scopes:
            return list(requested_scopes)
        if client.scope:
            scopes = [scope.strip() for scope in client.scope.split(" ") if scope.strip()]
            if scopes:
                return scopes
        return list(DEFAULT_KANBAN_SCOPES)

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
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            backup = self._state_file.with_suffix(".corrupted.json")
            if backup.exists():
                backup.unlink()
            self._state_file.replace(backup)
            self._log("oauth.state_corrupted backup=%s", backup.name)
            return self._default_state()

        if not isinstance(payload, dict):
            return self._default_state()

        return {
            "clients": payload.get("clients", {}) if isinstance(payload.get("clients"), dict) else {},
            "authorization_codes": (
                payload.get("authorization_codes", {}) if isinstance(payload.get("authorization_codes"), dict) else {}
            ),
            "access_tokens": payload.get("access_tokens", {}) if isinstance(payload.get("access_tokens"), dict) else {},
            "refresh_tokens": (
                payload.get("refresh_tokens", {}) if isinstance(payload.get("refresh_tokens"), dict) else {}
            ),
        }

    def _write_state_unlocked(self, state: dict[str, dict]) -> None:
        payload = json.dumps(state, ensure_ascii=False, indent=2)
        temp_file = self._state_file.with_suffix(".tmp")
        temp_file.write_text(payload, encoding="utf-8")
        temp_file.replace(self._state_file)

    def _prune_state(self, state: dict[str, dict]) -> dict[str, dict]:
        now = int(time.time())
        pruned = self._default_state()
        pruned["clients"] = dict(state.get("clients", {}))

        for key, value in state.get("authorization_codes", {}).items():
            if not isinstance(value, dict):
                continue
            expires_at = float(value.get("expires_at") or 0)
            if expires_at > time.time():
                pruned["authorization_codes"][key] = value

        for key, value in state.get("access_tokens", {}).items():
            if not isinstance(value, dict):
                continue
            expires_at = value.get("expires_at")
            if expires_at is None or int(expires_at) >= now:
                pruned["access_tokens"][key] = value

        for key, value in state.get("refresh_tokens", {}).items():
            if not isinstance(value, dict):
                continue
            expires_at = value.get("expires_at")
            if expires_at is None or int(expires_at) >= now:
                pruned["refresh_tokens"][key] = value

        return pruned

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
