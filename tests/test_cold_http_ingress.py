"""Artificial early-denial, unchanged authentication and generation URL checks."""

from __future__ import annotations

import gzip
import http.client
import json
import os
import socket
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx

from minimal_kanban import cold_http_ingress as ingress
from minimal_kanban.cold_board_ingress import ColdBoardIngress, html_body
from minimal_kanban.mcp.auth import StaticBearerTokenVerifier, build_auth_settings
from minimal_kanban.mcp.cold_ingress import ColdAwareFastMCP, ColdMcpIngress
from scripts.j1_forward_cold_http import expected_prefixes
from tests.mcp_credentials import MCP_TEST_TOKEN


def fixture():
    epoch = "00000000-0000-4000-8000-000000000002"
    document = {
        "schema": ingress.SCHEMA,
        "scope": "production_native",
        "attempt_id": "00000000-0000-4000-8000-000000000003",
        "boot_id": "00000000-0000-4000-8000-000000000004",
        "generation": epoch,
        "epoch_id": epoch,
        "candidate_crm_sha": "1" * 40,
        "candidate_manager_sha": "2" * 40,
        "public_origin": ingress.PUBLIC_ORIGIN,
        "board_prefix": "/__cold/" + "a" * 64,
        "mcp_prefix": "/mcp/__cold/" + "b" * 64,
    }
    binding = {**document, "role": "crm-embedded", "source_revision": "2" * 40}
    document.update(expected_prefixes(document))
    return ingress.parse_document(document, binding), binding


class ColdHttpContractTests(unittest.TestCase):
    def test_standalone_container_imports_from_unrelated_directory_without_pythonpath(self):
        import tempfile

        root = Path(__file__).resolve().parents[1]
        environment = {
            key: value
            for key, value in os.environ.items()
            if key != "PYTHONPATH"
            and not key.startswith("AUTOSTOP_COLD_")
            and key != "AUTOSTOP_MANAGER_PATH"
        }
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        with tempfile.TemporaryDirectory() as temporary:
            for name in (
                "container_entrypoint.py",
                "container_healthcheck.py",
                "browser_smoke_runtime.py",
            ):
                with self.subTest(name=name):
                    script = (
                        "import runpy; d=runpy.run_path("
                        + repr(str(root / "scripts" / name))
                        + ",run_name='import_only'); "
                    )
                    if name == "container_healthcheck.py":
                        script += "assert d['_cold_url'](d['API_HEALTH_URL'],'board')==d['API_HEALTH_URL']; "
                    script += "print('IMPORTED')"
                    result = subprocess.run(
                        [os.sys.executable, "-B", "-c", script],
                        cwd=temporary,
                        env=environment,
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr[-4096:])
                    self.assertEqual(result.stdout.strip(), "IMPORTED")

    def test_new_epoch_cannot_reuse_old_address_generation(self):
        config, binding = fixture()
        generation = "00000000-0000-4000-8000-000000000005"
        changed = {**config.document, "generation": generation, "epoch_id": generation}
        new_binding = {**binding, "generation": generation, "epoch_id": generation}
        with self.assertRaises(ingress.ColdHttpIngressUnavailableError):
            ingress.parse_document(changed, new_binding)
        changed.update(expected_prefixes(changed))
        fresh = ingress.parse_document(changed, new_binding)
        self.assertIsNone(fresh.strip_path(config.prefix("board") + "/api/create_card", "board"))
        self.assertNotEqual(fresh.prefix("board"), config.prefix("board"))
        self.assertNotEqual(fresh.prefix("mcp"), config.prefix("mcp"))

    def test_real_board_starts_in_its_namespace_and_preserves_authentication(self):
        import logging
        import tempfile

        from minimal_kanban.api.server import ApiServer
        from minimal_kanban.services.card_service import CardService
        from minimal_kanban.storage.json_store import JsonStore

        config, _ = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            logger = logging.getLogger("synthetic-cold-http")
            logger.addHandler(logging.NullHandler())
            service = CardService(
                JsonStore(state_file=root / "state.json", logger=logger),
                logger,
                attachments_dir=root / "attachments",
                repair_orders_dir=root / "orders",
            )
            with patch("minimal_kanban.api.server.load_http_ingress", return_value=config):
                server = ApiServer(
                    service, logger, start_port=0, bearer_token="synthetic-board-token-" + "x" * 40
                )
            try:
                server.start()
                self.assertEqual(server._fallback_limit, 1)
                self.assertTrue(server.base_url.endswith(config.prefix("board")))
                connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
                try:
                    connection.request("GET", "/api/health")
                    self.assertEqual(connection.getresponse().status, 409)
                    connection.close()
                    connection.request(
                        "GET", config.prefix("board") + "/api/get_board_revision?compact=1"
                    )
                    self.assertEqual(connection.getresponse().status, 401)
                    connection.close()
                    connection.request(
                        "GET",
                        config.prefix("board") + "/api/get_board_revision?compact=1",
                        headers={"Authorization": "Bearer " + server._bearer_token},
                    )
                    self.assertEqual(connection.getresponse().status, 200)
                    connection.close()
                    connection.request("GET", config.prefix("board") + "/")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertIn(
                        (config.prefix("board") + "/assets/board.").encode(), response.read()
                    )
                finally:
                    connection.close()
            finally:
                server.stop()

    def test_exact_binding_and_separate_nonces_are_required(self):
        config, binding = fixture()
        mutations = {
            "scope": "native_fixture",
            "epoch_id": binding["attempt_id"],
            "candidate_manager_sha": "3" * 40,
            "candidate_crm_sha": "HEAD",
            "board_prefix": "/__cold/" + "A" * 64,
            "mcp_prefix": "/mcp/__cold/" + "a" * 64,
            "public_origin": "https://crm.autostopcrm.ru/",
            "extra": True,
        }
        for key, value in mutations.items():
            with self.subTest(key=key):
                changed = {**config.document, key: value}
                with self.assertRaises(ingress.ColdHttpIngressUnavailableError):
                    ingress.parse_document(changed, binding)

    def test_old_encoded_aliased_and_traversing_targets_cannot_be_upgraded(self):
        config, _ = fixture()
        prefix = config.prefix("board")
        for value in (
            "/api/create_card",
            "/__cold/" + "c" * 64 + "/api/create_card",
            prefix.replace("/__cold/", "/%5f_cold/") + "/api/create_card",
            prefix + "x/api/create_card",
            prefix + "/../api/create_card",
            prefix + "/%2e%2e/api/create_card",
            prefix + "/%252fapi/create_card",
            "//crm.autostopcrm.ru" + prefix + "/api/create_card",
            "https://crm.autostopcrm.ru" + prefix + "/api/create_card",
            prefix + "/api\\create_card",
        ):
            with self.subTest(value=value):
                self.assertIsNone(config.strip_path(value, "board"))
        self.assertEqual(
            config.strip_path(prefix + "/api/get_cards?q=%20", "board"), "/api/get_cards?q=%20"
        )

    def test_warm_absence_and_incomplete_cold_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(ingress.load_http_ingress())
        for values in (
            {ingress.CONFIG_ENV: "/missing"},
            {ingress.HASH_ENV: "a" * 64},
            {ingress.cold_manager.BINDING_ENV: "/missing"},
            {
                "AUTOSTOP_MANAGER_PATH": "/opt/autostop-manager-cold-releases/"
                + "2" * 40
                + "/source"
            },
        ):
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True):
                with self.assertRaises(ingress.ColdHttpIngressUnavailableError):
                    ingress.load_http_ingress()

    def test_html_attributes_and_gzip_keep_one_generation(self):
        config, _ = fixture()
        handler = Mock(COLD_HTTP_INGRESS=config)
        original = b'<!DOCTYPE html><html><head><script>const text="href="/not-an-attribute"";</script></head><body><a href="/">Home</a><script src="/assets/x.js"></script><a href="https://example.com/">External</a></body></html>'
        prepared = html_body(handler, original)
        self.assertIn(('href="' + config.prefix("board") + '/"').encode(), prepared)
        self.assertIn(b'const text="href="/not-an-attribute"";', prepared)
        self.assertIn(b'href="https://example.com/"', prepared)
        self.assertEqual(
            gzip.decompress(html_body(handler, gzip.compress(original), compressed=True)), prepared
        )

    def test_browser_helper_freezes_address_and_does_not_upgrade_explicit_old_urls(self):
        config, _ = fixture()
        helper = Path("src/minimal_kanban/web_app_assets/source/cold_board_url.js").read_text()
        script = (
            "const window={location:{pathname:"
            + json.dumps(config.prefix("board") + "/")
            + "}};\n"
            + helper
        )
        script += "\nwindow.location.pathname='/__cold/'+'c'.repeat(64);console.log(JSON.stringify([autostopBoardUrl('/api/a'),autostopBoardUrl('/__cold/'+'d'.repeat(64)+'/api/a'),autostopBoardUrl('https://example.com/a')]));"
        result = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, timeout=10, check=True
        )
        self.assertEqual(
            json.loads(result.stdout),
            [
                config.prefix("board") + "/api/a",
                "/__cold/" + "d" * 64 + "/api/a",
                "https://example.com/a",
            ],
        )


class ColdBoardEarlyCutTests(unittest.TestCase):
    def setUp(self):
        config, _ = fixture()
        self.config = config
        self.dispatched = []
        dispatched = self.dispatched

        class Handler(ColdBoardIngress, BaseHTTPRequestHandler):
            COLD_HTTP_INGRESS = config
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                dispatched.append((self.command, self.path))
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_GET = do_POST

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())

    def test_old_post_without_a_body_is_denied_immediately_without_dispatch(self):
        # A buffered header-only request cannot make the new application wait
        # for a body or invoke any route/auth/DB collaborator.
        with socket.create_connection(self.server.server_address, timeout=2) as client:
            client.sendall(
                b"POST /api/create_card HTTP/1.1\r\nHost: localhost\r\nContent-Length: 99999\r\nExpect: 100-continue\r\n\r\n"
            )
            response = client.recv(4096)
        self.assertIn(b"409", response)
        self.assertNotIn(b"100 Continue", response)
        self.assertEqual(self.dispatched, [])

    def test_current_namespace_preserves_downstream_auth_and_status_is_static(self):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        self.addCleanup(connection.close)
        connection.request("GET", self.config.prefix("board") + "/api/get_cards")
        self.assertEqual(connection.getresponse().status, 401)
        self.assertEqual(self.dispatched, [("GET", "/api/get_cards")])
        connection.close()
        connection.request("GET", self.config.prefix("board") + ingress.STATUS_PATH)
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.read()), self.config.status("board"))
        self.assertEqual(len(self.dispatched), 1)


class ColdMcpCutTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_oauth_registration_and_consent_use_same_generation(self):
        import base64
        import hashlib
        import logging
        import tempfile
        from urllib.parse import parse_qs, urlsplit

        from minimal_kanban.mcp.server import create_mcp_server

        config, _ = fixture()
        prefix = config.prefix("mcp")
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(
                os.environ,
                {
                    "AUTOSTOP_MCP_OAUTH_ENABLED": "1",
                    "AUTOSTOP_DEPLOYMENT_ENV": "development",
                    "MINIMAL_KANBAN_DATA_DIR": temporary,
                },
                clear=True,
            ),
            patch("minimal_kanban.mcp.server.load_http_ingress", return_value=config),
        ):
            server = create_mcp_server(
                Mock(base_url="http://127.0.0.1:1"),
                logging.getLogger("synthetic-oauth-cut"),
                bearer_token=MCP_TEST_TOKEN,
                oauth_state_file=Path(temporary) / "oauth.json",
            )
            app = server.streamable_http_app()
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url=ingress.PUBLIC_ORIGIN,
                ) as client,
            ):
                registration = await client.post(
                    prefix + "/register",
                    json={
                        "redirect_uris": ["https://chatgpt.com/connector/oauth/synthetic"],
                        "grant_types": ["authorization_code", "refresh_token"],
                        "response_types": ["code"],
                        "token_endpoint_auth_method": "none",
                        "scope": "kanban:read kanban:write",
                        "client_name": "Artificial cold generation",
                    },
                )
                self.assertEqual(registration.status_code, 201, registration.text)
                challenge = (
                    base64.urlsafe_b64encode(hashlib.sha256(b"synthetic-verifier").digest())
                    .decode()
                    .rstrip("=")
                )
                authorize = await client.get(
                    prefix + "/authorize",
                    params={
                        "client_id": registration.json()["client_id"],
                        "redirect_uri": "https://chatgpt.com/connector/oauth/synthetic",
                        "response_type": "code",
                        "code_challenge": challenge,
                        "code_challenge_method": "S256",
                        "state": "artificial-state",
                        "resource": config.base_url("mcp") + "/mcp",
                    },
                )
                self.assertEqual(authorize.status_code, 302, authorize.text)
                consent_url = authorize.headers["location"]
                self.assertEqual(urlsplit(consent_url).path, prefix + "/oauth/authorize")
                consent = await client.get(consent_url)
                self.assertEqual(consent.status_code, 200)
                self.assertIn('action="' + prefix + '/oauth/authorize"', consent.text)
                request_id = parse_qs(urlsplit(consent_url).query)["request_id"][0]
                form = {
                    "request_id": request_id,
                    "username": "synthetic",
                    "password": "artificial-only",
                }
                with patch(
                    "minimal_kanban.mcp.server._authenticate_oauth_owner", return_value=None
                ) as owner:
                    stale = await client.post(
                        "/oauth/authorize", data=form, headers={"Origin": ingress.PUBLIC_ORIGIN}
                    )
                    self.assertEqual(stale.status_code, 409)
                    owner.assert_not_called()
                    denied = await client.post(
                        prefix + "/oauth/authorize",
                        data=form,
                        headers={"Origin": ingress.PUBLIC_ORIGIN},
                    )
                    self.assertEqual(denied.status_code, 401)
                    owner.assert_called_once()
                    self.assertIn('action="' + prefix + '/oauth/authorize"', denied.text)

    async def test_stale_post_never_reads_body_or_enters_auth_application(self):
        config, _ = fixture()
        application, receive = AsyncMock(), AsyncMock(side_effect=AssertionError("body read"))
        send = AsyncMock()
        guarded = ColdMcpIngress(application, config=config, auth=None)
        for path in (
            "/mcp",
            "/token",
            "/register",
            "/revoke",
            "/authorize",
            "/mcp/__cold/" + "c" * 64 + "/mcp",
        ):
            await guarded(
                {"type": "http", "method": "POST", "path": path, "raw_path": path.encode()},
                receive,
                send,
            )
        self.assertEqual(application.await_count, 0)
        self.assertEqual(receive.await_count, 0)
        self.assertEqual(
            [
                item.args[0]["status"]
                for item in send.await_args_list
                if item.args[0]["type"] == "http.response.start"
            ],
            [409] * 6,
        )

    async def test_real_sdk_keeps_bearer_checks_and_generation_metadata(self):
        config, _ = fixture()
        base, resource, path = config.mcp_urls()
        token = MCP_TEST_TOKEN
        from mcp.server.transport_security import TransportSecuritySettings

        auth = build_auth_settings(base, path=path, resource_url=resource, oauth_enabled=False)
        server = ColdAwareFastMCP(
            cold_http_ingress=config,
            name="synthetic",
            streamable_http_path=path,
            auth=auth,
            token_verifier=StaticBearerTokenVerifier(token, resource_url=resource),
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=["crm.autostopcrm.ru"],
                allowed_origins=[ingress.PUBLIC_ORIGIN],
            ),
        )
        app = server.streamable_http_app()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=ingress.PUBLIC_ORIGIN
            ) as client:
                current = config.prefix("mcp") + "/mcp"
                denied = await client.get(current)
                self.assertEqual(denied.status_code, 401)
                self.assertIn(
                    "/.well-known/oauth-protected-resource" + current,
                    denied.headers["www-authenticate"],
                )
                metadata = await client.get("/.well-known/oauth-protected-resource" + current)
                self.assertEqual(metadata.status_code, 200)
                self.assertEqual(metadata.json()["resource"], resource)
                issuer = await client.get(
                    "/.well-known/oauth-authorization-server" + config.prefix("mcp")
                )
                self.assertEqual(issuer.json()["issuer"], base)
                self.assertEqual(issuer.json()["token_endpoint"], base + "/token")
                self.assertEqual((await client.post("/token", content=b"stale")).status_code, 409)
                authenticated = await client.post(
                    current,
                    headers={
                        "Authorization": "Bearer " + token,
                        "Accept": "application/json, text/event-stream",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "artificial-cold-client", "version": "1"},
                        },
                    },
                )
                self.assertEqual(authenticated.status_code, 200, authenticated.text)
                self.assertEqual(authenticated.json()["result"]["serverInfo"]["name"], "synthetic")


class ColdHttpOpeningProofTests(unittest.TestCase):
    def test_fresh_cut_checks_both_transports_and_refuses_stale_or_unheld_state(self):
        from scripts import j1_forward_cold_runtime as runtime
        from scripts import j1_forward_io as control_io
        from scripts import j1_forward_runtime as http

        config, _ = fixture()
        candidate = {
            "image": "sha256:" + "3" * 64,
            "revision": config.document["candidate_crm_sha"],
            "tree": "4" * 40,
        }

        class Peer:
            def __init__(self, deadline, failure=None):
                self.failure, self.requests = failure, []

            def request(self, url, **kwargs):
                self.requests.append((url, kwargs))
                for role, local in (("board", runtime.API), ("mcp", "http://127.0.0.1:41831")):
                    if url in {
                        config.base_url(role) + ingress.STATUS_PATH,
                        local + config.prefix(role) + ingress.STATUS_PATH,
                    }:
                        status = config.status(role)
                        if self.failure == "status" and role == "mcp":
                            status["epoch_id"] = config.document["attempt_id"]
                        return http.Response(200, "application/json", control_io.canonical(status))
                if url == config.base_url("board") + "/api/health":
                    return http.Response(
                        200,
                        "application/json",
                        control_io.canonical(
                            {"ok": True, "data": {"maintenance_mode": self.failure != "unheld"}}
                        ),
                    )
                return http.Response(
                    200 if self.failure == "old-path" else 409, "application/json", b"{}"
                )

        with (
            patch.object(runtime, "active_http_ingress", return_value=config),
            patch.dict(
                os.environ,
                {
                    ingress.CONFIG_ENV: "/etc/autostop-manager-cold/"
                    + config.document["generation"]
                    + "/crm-embedded/http-ingress.json",
                },
            ),
        ):
            peer = Peer(0)
            report = runtime.http_cut(
                candidate, config.document["attempt_id"], client_factory=lambda _: peer
            )
            self.assertEqual(len(peer.requests), 17)
            self.assertTrue(all(not kwargs for _, kwargs in peer.requests))
            self.assertTrue(report["maintenance_mode"])
            for failure in ("status", "unheld", "old-path"):
                with self.subTest(failure=failure), self.assertRaises(control_io.Refusal):
                    runtime.http_cut(
                        candidate,
                        config.document["attempt_id"],
                        client_factory=lambda _: Peer(0, failure),
                    )


if __name__ == "__main__":
    unittest.main()
