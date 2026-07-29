from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

SCOPES = "kanban:read kanban:write"
MAX_STATE_BYTES = 64 * 1024


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _write_state(path: Path, payload: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        with temp_path.open("x", encoding="utf-8") as handle:
            os.chmod(temp_path, 0o600)
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)
        os.chmod(path, 0o600)
    finally:
        temp_path.unlink(missing_ok=True)


def _read_state(path: Path) -> dict[str, str]:
    if path.stat().st_size > MAX_STATE_BYTES:
        raise ValueError("OAuth smoke state is too large")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("OAuth smoke state must be an object")
    return {str(key): str(value) for key, value in payload.items()}


def _metadata(client: httpx.Client, mcp_url: str) -> dict[str, object]:
    origin = _origin(mcp_url)
    protected_url = f"{origin}/.well-known/oauth-protected-resource{urlsplit(mcp_url).path}"
    protected_response = client.get(protected_url)
    protected_response.raise_for_status()
    protected = protected_response.json()
    authorization_server = str((protected.get("authorization_servers") or [""])[0]).rstrip("/")
    if authorization_server != origin:
        raise RuntimeError("protected resource authorization server mismatch")
    if str(protected.get("resource") or "").rstrip("/") != mcp_url.rstrip("/"):
        raise RuntimeError("protected resource audience mismatch")
    metadata_response = client.get(f"{authorization_server}/.well-known/oauth-authorization-server")
    metadata_response.raise_for_status()
    metadata = metadata_response.json()
    if "S256" not in metadata.get("code_challenge_methods_supported", []):
        raise RuntimeError("OAuth metadata does not advertise PKCE S256")
    if "refresh_token" not in metadata.get("grant_types_supported", []):
        raise RuntimeError("OAuth metadata does not advertise refresh tokens")
    return metadata


def _new_authorization(
    client: httpx.Client,
    *,
    mcp_url: str,
    username: str,
    password: str,
) -> dict[str, str]:
    metadata = _metadata(client, mcp_url)
    registration_endpoint = str(metadata.get("registration_endpoint") or "")
    authorization_endpoint = str(metadata.get("authorization_endpoint") or "")
    token_endpoint = str(metadata.get("token_endpoint") or "")
    callback = "http://127.0.0.1:18765/callback"
    registered_response = client.post(
        registration_endpoint,
        json={
            "client_name": "AutoStop CRM OAuth production smoke",
            "redirect_uris": [callback],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "scope": SCOPES,
        },
    )
    registered_response.raise_for_status()
    client_id = str(registered_response.json().get("client_id") or "")
    if not client_id:
        raise RuntimeError("DCR did not return client_id")

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    authorize_response = client.get(
        authorization_endpoint,
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": callback,
            "scope": SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": mcp_url,
        },
    )
    if authorize_response.status_code != 302:
        raise RuntimeError(f"authorization start returned {authorize_response.status_code}")
    consent_url = authorize_response.headers.get("location", "")
    if urlsplit(consent_url).path != "/oauth/authorize":
        raise RuntimeError("authorization did not enter the owner consent route")
    consent_response = client.post(
        consent_url,
        headers={"Origin": _origin(mcp_url)},
        data={
            "request_id": (parse_qs(urlsplit(consent_url).query).get("request_id") or [""])[0],
            "username": username,
            "password": password,
        },
    )
    if consent_response.status_code != 302:
        raise RuntimeError(f"owner consent returned {consent_response.status_code}")
    callback_url = consent_response.headers.get("location", "")
    callback_query = parse_qs(urlsplit(callback_url).query)
    if (callback_query.get("state") or [""])[0] != state:
        raise RuntimeError("OAuth state mismatch")
    code = (callback_query.get("code") or [""])[0]
    if not code:
        raise RuntimeError("authorization code is missing")
    token_response = client.post(
        token_endpoint,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": callback,
            "code_verifier": verifier,
            "resource": mcp_url,
        },
    )
    token_response.raise_for_status()
    tokens = token_response.json()
    return {
        "mcp_url": mcp_url,
        "client_id": client_id,
        "token_endpoint": token_endpoint,
        "access_token": str(tokens.get("access_token") or ""),
        "refresh_token": str(tokens.get("refresh_token") or ""),
    }


def _refresh(client: httpx.Client, state: dict[str, str]) -> tuple[dict[str, str], bool]:
    previous_refresh = state["refresh_token"]
    response = client.post(
        state["token_endpoint"],
        data={
            "grant_type": "refresh_token",
            "client_id": state["client_id"],
            "refresh_token": previous_refresh,
            "scope": SCOPES,
            "resource": state["mcp_url"],
        },
    )
    response.raise_for_status()
    payload = response.json()
    updated = {
        **state,
        "access_token": str(payload.get("access_token") or ""),
        "refresh_token": str(payload.get("refresh_token") or ""),
    }
    replay = client.post(
        state["token_endpoint"],
        data={
            "grant_type": "refresh_token",
            "client_id": state["client_id"],
            "refresh_token": previous_refresh,
            "scope": SCOPES,
            "resource": state["mcp_url"],
        },
    )
    return updated, replay.status_code == 400 and replay.json().get("error") == "invalid_grant"


def _revoke(client: httpx.Client, state: dict[str, str]) -> bool:
    response = client.post(
        f"{_origin(state['mcp_url'])}/revoke",
        data={
            "client_id": state["client_id"],
            # MCP SDK 1.26 models this optional RFC 7009 field as
            # nullable-but-required. Public clients send it empty.
            "client_secret": "",
            "token": state["refresh_token"],
            "token_type_hint": "refresh_token",
        },
    )
    return response.status_code == 200


async def _check_gateway(state: dict[str, str]) -> dict[str, bool | int]:
    headers = {"Authorization": f"Bearer {state['access_token']}"}
    timeout = httpx.Timeout(45.0, connect=10.0, read=45.0, write=45.0, pool=45.0)
    async with httpx.AsyncClient(
        headers=headers, timeout=timeout, follow_redirects=False
    ) as client:
        async with streamable_http_client(state["mcp_url"], http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                bootstrap = await session.call_tool(
                    "agent_bootstrap", {"query": "oauth production smoke"}
                )
                digest = await session.call_tool("agent_board_digest", {"limit": 1})
                search = await session.call_tool(
                    "agent_search", {"entity": "card", "query": "C-", "limit": 1}
                )
    return {
        "tool_count": len(names),
        "legacy_absent": not bool(
            names.intersection({"get_cards", "search_cards", "bootstrap_context", "update_card"})
        ),
        "bootstrap_ok": not bool(bootstrap.isError),
        "board_digest_ok": not bool(digest.isError),
        "search_ok": not bool(search.isError),
    }


async def run(args: argparse.Namespace) -> dict[str, object]:
    with httpx.Client(timeout=20.0, follow_redirects=False) as client:
        if args.refresh_from:
            state = _read_state(args.refresh_from)
            _metadata(client, state["mcp_url"])
        else:
            username = str(os.environ.get(args.username_env) or "")
            password = str(os.environ.get(args.password_env) or "")
            if not username or not password:
                raise RuntimeError("OAuth smoke administrator credentials are not configured")
            state = _new_authorization(
                client,
                mcp_url=args.mcp_url,
                username=username,
                password=password,
            )
        state, replay_blocked = _refresh(client, state)
        anonymous = client.get(state["mcp_url"])
        challenge = anonymous.headers.get("www-authenticate", "")
        clear_unauthorized = bool(
            anonymous.status_code == 401
            and "resource_metadata=" in challenge
            and "invalid_token" in anonymous.text
        )
    gateway = await _check_gateway(state)
    revoked = False
    if args.state_out:
        _write_state(args.state_out, state)
    else:
        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            revoked = _revoke(client, state)
    ok = bool(
        replay_blocked
        and clear_unauthorized
        and gateway["tool_count"] == 24
        and gateway["legacy_absent"]
        and gateway["bootstrap_ok"]
        and gateway["board_digest_ok"]
        and gateway["search_ok"]
        and (bool(args.state_out) or revoked)
    )
    return {
        "ok": ok,
        "oauth_metadata": True,
        "authorization_code_pkce": not bool(args.refresh_from),
        "refresh_rotation": True,
        "refresh_replay_blocked": replay_blocked,
        "clear_401_challenge": clear_unauthorized,
        **gateway,
        "state_saved": bool(args.state_out),
        "smoke_token_revoked": revoked,
        "data_included": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify production MCP OAuth without printing credentials or CRM data."
    )
    parser.add_argument("--mcp-url", default="https://crm.autostopcrm.ru/mcp")
    parser.add_argument("--username-env", default="AUTOSTOP_SMOKE_OPERATOR_USERNAME")
    parser.add_argument("--password-env", default="AUTOSTOP_SMOKE_OPERATOR_PASSWORD")
    parser.add_argument("--state-out", type=Path)
    parser.add_argument("--refresh-from", type=Path)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
