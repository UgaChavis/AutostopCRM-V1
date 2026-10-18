from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

from ..config import get_mcp_bearer_token
from ..deployment_security import bearer_token_is_strong, is_loopback_host


def has_api_credential(
    handler: BaseHTTPRequestHandler, bearer_token: str | None, query: dict | None = None
) -> bool:
    if not bearer_token:
        return False
    authorization = str(handler.headers.get("Authorization", "") or "")
    if hmac.compare_digest(authorization.encode(), f"Bearer {bearer_token}".encode()):
        return True
    query_payload = (
        query if query is not None else handler._query_payload(urlsplit(handler.path).query)
    )
    access_token = str(query_payload.get("access_token", "") or "").strip()
    return hmac.compare_digest(access_token.encode(), bearer_token.encode())


def validate_api_listener(
    host: str, bearer_token: str | None, operator_auth_available: bool
) -> None:
    if is_loopback_host(host):
        return
    api_credential = bearer_token_is_strong(bearer_token or "")
    operator_and_scoped_service = (
        not bearer_token
        and operator_auth_available
        and bearer_token_is_strong(get_mcp_bearer_token() or "")
    )
    if not (api_credential or operator_and_scoped_service):
        raise ValueError(
            "Non-loopback API listeners require a strong API bearer token or "
            "operator authentication with a strong scoped MCP service token."
        )
