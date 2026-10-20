"""Bounded technical runtime checks for a candidate CRM container.

Only operator login, read routes and logout are used. Credentials and API
response payloads never appear in the JSON report.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit

MAX_BYTES = 4 * 1024 * 1024
READ_ROUTES = (
    "/api/get_board_revision",
    "/api/get_board_context",
    "/api/get_board_snapshot?compact=1&include_archive=0",
    "/api/get_gpt_wall?compact=1&include_archived=0&event_limit=10",
    "/api/list_repair_orders?compact=true&redact_private=true",
)
SOURCE = "j1_forward_runtime_probe"
SCHEMA = "autostop.crm.j1-forward-runtime-probe.v1"


class ProbeError(ValueError):
    """A fixed technical code, never a response body or transport exception."""


def require(condition: object, code: str) -> None:
    if not condition:
        raise ProbeError(code)


def origin(value: str, *, local: bool) -> str:
    try:
        require(isinstance(value, str) and not any(ord(c) < 33 for c in value), "url_invalid")
        parts = urlsplit(value)
        require(
            parts.hostname
            and parts.username is None
            and parts.password is None
            and parts.path in {"", "/"}
            and not parts.query
            and not parts.fragment
            and (parts.port is None or 0 < parts.port <= 65535),
            "url_invalid",
        )
        if local:
            require(
                parts.scheme == "http" and ipaddress.ip_address(parts.hostname).is_loopback,
                "local_api_loopback_required",
            )
        else:
            require(parts.scheme == "https", "public_https_required")
        return value.rstrip("/")
    except (ValueError, TypeError) as exc:
        if isinstance(exc, ProbeError):
            raise
        raise ProbeError("url_invalid") from None


def header_value(value: str) -> str:
    require(
        isinstance(value, str)
        and 0 < len(value) <= 4096
        and all(33 <= ord(character) <= 126 for character in value),
        "credential_header_invalid",
    )
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class Response:
    status: int
    content_type: str
    body: bytes = field(repr=False)


class Client:
    def __init__(self, deadline: float):
        self.deadline = deadline
        # Local credentials must never follow environment proxies or redirects.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        headers: dict | None = None,
        deadline: float | None = None,
    ) -> Response:
        until = self.deadline if deadline is None else deadline
        remaining = until - time.monotonic()
        require(remaining > 0, "runtime_probe_deadline")
        request_headers = {
            "Accept": "application/json,text/html,application/javascript",
            "Accept-Encoding": "identity",
            "User-Agent": "AutoStopCRM-forward-runtime/1",
            **(headers or {}),
        }
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            try:
                response = self.opener.open(request, timeout=min(10, remaining))
            except urllib.error.HTTPError as error:
                response = error
            with response:
                chunks = bytearray()
                while True:
                    require(time.monotonic() < until, "runtime_probe_deadline")
                    chunk = response.read1(min(65536, MAX_BYTES + 1 - len(chunks)))
                    chunks.extend(chunk)
                    require(len(chunks) <= MAX_BYTES, "response_too_large")
                    if not chunk:
                        break
                return Response(
                    int(response.code),
                    str(response.headers.get("Content-Type", "")).split(";", 1)[0].lower(),
                    bytes(chunks),
                )
        except ProbeError:
            raise
        except (OSError, ValueError, urllib.error.URLError):
            raise ProbeError("http_transport_failed") from None


def _unique(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "response_duplicate_json_key")
        value[key] = item
    return value


def _constant(_value):
    raise ProbeError("response_nonfinite_json")


def envelope(response: Response, code: str) -> dict:
    require(response.status == 200, code)
    require(response.content_type == "application/json", "response_content_type_invalid")
    try:
        value = json.loads(response.body, object_pairs_hook=_unique, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ProbeError("response_json_invalid") from None
    require(
        type(value) is dict and value.get("ok") is True and type(value.get("data")) is dict,
        code,
    )
    return value["data"]


class BoardScripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            for key, value in attrs:
                if key.lower() == "src" and value:
                    require(len(self.sources) < 16, "ui_script_limit")
                    self.sources.append(value)


def public_checks(
    client: Client, public_url: str, api_url: str, *, mcp_url: str | None = None
) -> dict:
    health = client.request(public_url + "/api/health")
    envelope(health, "public_health_failed")
    shell = client.request(public_url + "/")
    require(shell.status == 200 and shell.content_type == "text/html", "public_ui_failed")
    parser = BoardScripts()
    try:
        parser.feed(shell.body.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise ProbeError("public_ui_invalid") from None
    prefix = urlsplit(public_url).path.rstrip("/")
    assets = [
        source
        for source in parser.sources
        if re.fullmatch(re.escape(prefix) + r"/assets/board\.[0-9a-f]{64}\.js", source)
    ]
    require(len(assets) == 1, "public_board_asset_missing")
    origin = public_url.removesuffix(prefix) if prefix else public_url
    script = client.request(origin + assets[0])
    require(
        script.status == 200
        and script.content_type in {"application/javascript", "text/javascript"},
        "public_board_asset_failed",
    )
    digest = hashlib.sha256(script.body).hexdigest()
    require(
        digest == assets[0].split(".")[1] and b"/api/login_operator" in script.body,
        "public_board_asset_mismatch",
    )
    guards = {}
    for name, url in (
        ("public_api", public_url + "/api/get_board_revision"),
        ("local_api", api_url + "/api/get_board_revision"),
        ("public_mcp", mcp_url or public_url + "/mcp"),
    ):
        result = client.request(url)
        require(result.status == 401, "anonymous_guard_failed_" + name)
        guards[name] = result.status
    return {
        "health_http": health.status,
        "ui_http": shell.status,
        "board_asset_sha256": digest,
        "anonymous_guards": guards,
    }


def _logout(client: Client, api_url: str, headers: dict) -> None:
    deadline = time.monotonic() + 15
    try:
        response = client.request(
            api_url + "/api/logout_operator",
            method="POST",
            payload={"source": SOURCE},
            headers=headers,
            deadline=deadline,
        )
        require(
            envelope(response, "operator_logout_failed").get("logged_out") is True,
            "operator_logout_failed",
        )
        response = client.request(
            api_url + "/api/get_operator_profile", headers=headers, deadline=deadline
        )
        require(response.status == 401, "operator_session_still_active")
    except Exception:
        raise ProbeError("operator_cleanup_failed") from None


def operator_checks(client: Client, api_url: str, environment: dict) -> dict:
    username = environment.get("AUTOSTOP_SMOKE_OPERATOR_USERNAME", "").strip()
    password = environment.get("AUTOSTOP_SMOKE_OPERATOR_PASSWORD", "")
    require(username and password, "smoke_operator_credentials_missing")
    bearer = environment.get("MINIMAL_KANBAN_API_BEARER_TOKEN", "").strip()
    headers = {"Authorization": "Bearer " + header_value(bearer)} if bearer else {}
    login = client.request(
        api_url + "/api/login_operator",
        method="POST",
        payload={"username": username, "password": password, "source": SOURCE},
        headers=headers,
    )
    data = envelope(login, "operator_login_failed")
    session = data.get("session")
    require(
        type(session) is dict and type(session.get("token")) is str and session["token"],
        "operator_session_missing",
    )
    session_token = session["token"]
    try:
        headers = {**headers, "X-Operator-Session": header_value(session_token)}
        require(session.get("is_admin") is True, "operator_is_not_admin")
        profile = envelope(
            client.request(api_url + "/api/get_operator_profile", headers=headers),
            "operator_profile_failed",
        )
        require(
            type(profile.get("user")) is dict and profile["user"].get("is_admin") is True,
            "operator_is_not_admin",
        )
        checked = []
        for route in READ_ROUTES:
            response = client.request(api_url + route, headers=headers)
            envelope(response, "authorized_api_read_failed")
            checked.append({"route": route.split("?", 1)[0], "status": response.status})
        automation = envelope(
            client.request(api_url + "/api/automation_center/status", headers=headers),
            "automation_status_failed",
        )
        controller = automation.get("controller")
        require(
            automation.get("format") == "crm_automation_center_status_v1"
            and automation.get("can_manage") is True
            and type(controller) is dict,
            "automation_status_invalid",
        )
        require(
            controller.get("overall_state") in {"on", "off", "applying"}
            and type(controller.get("error_count")) is int
            and controller["error_count"] == 0,
            "automation_status_unhealthy",
        )
        result = {
            "admin_authenticated": True,
            "authorized_reads": checked,
            "automation": {
                "can_manage": True,
                "overall_state": controller["overall_state"],
                "error_count": 0,
            },
        }
    finally:
        # Cleanup has its own small budget even when the main probe timed out.
        _logout(client, api_url, {**headers, "X-Operator-Session": session_token})
    return {**result, "logout_confirmed": True, "session_invalidated": True}


def run_probe(public_url: str, api_url: str, *, environment: dict | None = None) -> dict:
    public_url, api_url = origin(public_url, local=False), origin(api_url, local=True)
    environment = dict(os.environ) if environment is None else environment
    require(
        environment.get("AUTOSTOP_SMOKE_OPERATOR_USERNAME", "").strip()
        and environment.get("AUTOSTOP_SMOKE_OPERATOR_PASSWORD", ""),
        "smoke_operator_credentials_missing",
    )
    # Keep room within the host's 120s process budget for bounded socket reads
    # and the independent logout reserve, even after the main deadline expires.
    client = Client(time.monotonic() + 80)
    public = public_checks(client, public_url, api_url)
    operator = operator_checks(client, api_url, environment)
    return {"schema": SCHEMA, "ok": True, "public": public, "operator": operator}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ProbeError("runtime_arguments_invalid")


def main(argv: list[str] | None = None) -> int:
    parser = Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--public-url", default="https://crm.autostopcrm.ru")
    parser.add_argument("--api-url", default="http://127.0.0.1:41731")
    try:
        args = parser.parse_args(argv)
        result = run_probe(args.public_url, args.api_url)
    except ProbeError as error:
        print(json.dumps({"schema": SCHEMA, "ok": False, "error": str(error)}))
        return 2
    except Exception:
        print(json.dumps({"schema": SCHEMA, "ok": False, "error": "runtime_probe_failed"}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
