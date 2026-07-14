from __future__ import annotations

import hmac

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

from .oauth_provider import DEFAULT_KANBAN_SCOPES


def build_auth_settings(
    server_base_url: str,
    *,
    path: str,
    resource_url: str | None = None,
    embedded_oauth_enabled: bool = True,
) -> AuthSettings:
    normalized_base_url = str(server_base_url or "").strip().rstrip("/")
    normalized_path = str(path or "").strip() or "/mcp"
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    mcp_url = str(resource_url or "").strip().rstrip("/") or (
        f"{normalized_base_url}{normalized_path}"
    )
    return AuthSettings(
        issuer_url=normalized_base_url,
        resource_server_url=mcp_url,
        required_scopes=[],
        client_registration_options=ClientRegistrationOptions(
            enabled=bool(embedded_oauth_enabled),
            valid_scopes=list(DEFAULT_KANBAN_SCOPES),
            default_scopes=list(DEFAULT_KANBAN_SCOPES),
        ),
    )


class StaticBearerTokenVerifier:
    """Owner-agent verifier that exposes no DCR or auto-approved OAuth flow."""

    def __init__(
        self,
        token: str,
        *,
        resource_url: str,
        client_id: str = "codex-owner-agent",
    ) -> None:
        self._token = str(token or "").strip()
        self._resource_url = str(resource_url or "").strip()
        self._client_id = str(client_id or "codex-owner-agent").strip()

    async def verify_token(self, token: str) -> AccessToken | None:
        candidate = str(token or "").strip()
        if not self._token or not hmac.compare_digest(candidate, self._token):
            return None
        return AccessToken(
            token=candidate,
            client_id=self._client_id,
            scopes=list(DEFAULT_KANBAN_SCOPES),
            expires_at=None,
            resource=self._resource_url,
        )


__all__ = ["StaticBearerTokenVerifier", "build_auth_settings"]
