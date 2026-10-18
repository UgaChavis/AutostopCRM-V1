from __future__ import annotations

import hmac
import os
from urllib.parse import urlsplit

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.transport_security import TransportSecuritySettings

from ..deployment_security import bearer_token_is_strong, is_loopback_host
from ..settings_models import derive_allowed_hosts, derive_allowed_origins
from .oauth_provider import DEFAULT_KANBAN_SCOPES


def _remote_http_url(value: str | None) -> bool:
    try:
        parsed = urlsplit(value or "")
        _ = parsed.port
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not is_loopback_host(parsed.hostname)
        )
    except ValueError:
        return False


def build_mcp_transport_security(
    *,
    host: str,
    port: int,
    path: str,
    bearer_token: str | None,
    public_base_url: str | None,
    tunnel_url: str | None,
    public_endpoint_url: str | None,
    advertised_base_url: str,
    allowed_hosts: list[str] | tuple[str, ...] | None,
    allowed_origins: list[str] | tuple[str, ...] | None,
) -> TransportSecuritySettings:
    public_route = any(
        _remote_http_url(value)
        for value in (public_base_url, tunnel_url, public_endpoint_url, advertised_base_url)
    )
    if (not is_loopback_host(host) or public_route) and not bearer_token_is_strong(
        bearer_token or ""
    ):
        raise ValueError("Network and public MCP endpoints require a strong MCP bearer token.")
    local_url = f"http://{host}:{port}{path}"
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=derive_allowed_hosts(
            local_url, public_base_url, tunnel_url, public_endpoint_url, extra_hosts=allowed_hosts
        ),
        allowed_origins=derive_allowed_origins(
            local_url,
            public_base_url,
            tunnel_url,
            public_endpoint_url,
            extra_origins=allowed_origins,
        ),
    )


def _truthy_environment(name: str, *, default: bool = False) -> bool:
    raw = str(os.environ.get(name) or "").strip().casefold()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def build_auth_settings(
    server_base_url: str,
    *,
    path: str,
    resource_url: str | None = None,
    oauth_enabled: bool = True,
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
        required_scopes=list(DEFAULT_KANBAN_SCOPES),
        client_registration_options=ClientRegistrationOptions(
            enabled=bool(oauth_enabled),
            valid_scopes=list(DEFAULT_KANBAN_SCOPES),
            default_scopes=list(DEFAULT_KANBAN_SCOPES),
        ),
        revocation_options=RevocationOptions(enabled=bool(oauth_enabled)),
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
