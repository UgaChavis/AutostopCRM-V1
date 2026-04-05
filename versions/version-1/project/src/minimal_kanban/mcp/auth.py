from __future__ import annotations

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

from .oauth_provider import DEFAULT_KANBAN_SCOPES


def build_auth_settings(
    server_base_url: str,
    *,
    path: str,
    resource_url: str | None = None,
) -> AuthSettings:
    mcp_url = resource_url or f"{server_base_url}{path}"
    return AuthSettings(
        issuer_url=server_base_url,
        resource_server_url=mcp_url,
        required_scopes=[],
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=list(DEFAULT_KANBAN_SCOPES),
            default_scopes=list(DEFAULT_KANBAN_SCOPES),
        ),
    )
