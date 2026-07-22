from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "check_live_connector.py"


def load_live_connector_module():
    spec = importlib.util.spec_from_file_location("check_live_connector_for_tests", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load check_live_connector.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LegacyConsoleStdout:
    encoding = "cp1251"

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, text: str) -> None:
        self.buffer.write(text.encode(self.encoding))


class FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        status: int = 200,
        url: str = "http://example.test",
        content_type: str = "text/html; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.status = status
        self._url = url
        self.headers = {"Content-Type": content_type}
        self.headers.update(headers or {})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._payload
        return self._payload[:size]

    def geturl(self) -> str:
        return self._url


class OversizedResponse(FakeResponse):
    def __init__(self, *, status: int = 200) -> None:
        super().__init__(b"", status=status)

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = 1
        return b"x" * size


class LiveConnectorOutputTests(unittest.TestCase):
    def test_emit_output_writes_json_as_utf8_even_on_legacy_console(self) -> None:
        module = load_live_connector_module()
        fake_stdout = LegacyConsoleStdout()
        payload = json.dumps(
            {"ok": True, "message": "Проверка MCP 🚗", "amount": "100 ₽"},
            ensure_ascii=False,
        )

        with patch.object(sys, "stdout", fake_stdout):
            module._emit_output(payload)

        raw = fake_stdout.buffer.getvalue()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(json.loads(raw.decode("utf-8")), json.loads(payload))

    def test_api_request_rejects_oversized_response(self) -> None:
        module = load_live_connector_module()

        with patch.object(
            module,
            "_urlopen_no_redirect",
            return_value=OversizedResponse(),
        ):
            with self.assertRaisesRegex(ValueError, "Live connector response is too large"):
                module._api_request("http://127.0.0.1:41731", "/api/health")

    def test_api_request_rejects_nonstandard_json_constants(self) -> None:
        module = load_live_connector_module()

        with patch.object(
            module,
            "_urlopen_no_redirect",
            return_value=FakeResponse(b'{"ok": true, "score": NaN}'),
        ):
            with self.assertRaisesRegex(ValueError, "Unsupported JSON constant: NaN"):
                module._api_request("http://127.0.0.1:41731", "/api/health")

    def test_api_request_rejects_deeply_nested_json_response(self) -> None:
        module = load_live_connector_module()
        deep_json = ("[" * 5000 + "0" + "]" * 5000).encode("utf-8")

        with patch.object(
            module,
            "_urlopen_no_redirect",
            return_value=FakeResponse(deep_json),
        ):
            with self.assertRaisesRegex(ValueError, "API response JSON is too deeply nested"):
                module._api_request("http://127.0.0.1:41731", "/api/health")

    def test_api_request_rejects_non_object_json_response(self) -> None:
        module = load_live_connector_module()

        with patch.object(
            module,
            "_urlopen_no_redirect",
            return_value=FakeResponse(b"[]"),
        ):
            with self.assertRaisesRegex(ValueError, "API response must be a JSON object"):
                module._api_request("http://127.0.0.1:41731", "/api/health")

    def test_api_request_rejects_redirect_response(self) -> None:
        module = load_live_connector_module()
        redirect = module.urllib.error.HTTPError(
            url="http://127.0.0.1:41731/api/login_operator",
            code=302,
            msg="Found",
            hdrs={"Location": "https://example.test/login"},
            fp=None,
        )

        with patch.object(module, "_urlopen_no_redirect", side_effect=redirect):
            with self.assertRaisesRegex(ValueError, "API request redirected"):
                module._api_request(
                    "http://127.0.0.1:41731",
                    "/api/login_operator",
                    method="POST",
                    payload={"username": "admin", "password": "secret"},
                )

    def test_check_api_surface_uses_compact_read_routes(self) -> None:
        module = load_live_connector_module()
        calls: list[str] = []

        def fake_api_request(base_url: str, path: str, **kwargs):
            self.assertEqual(base_url, "http://127.0.0.1:41731")
            self.assertIsNone(kwargs.get("bearer_token"))
            calls.append(path)
            if path == "/api/get_board_context":
                return 200, {
                    "ok": True,
                    "data": {"context": {"board_name": "AutoStop", "columns_total": 3}},
                }
            if path == "/api/get_board_snapshot?compact=1&include_archive=0":
                return 200, {"ok": True, "data": {"cards": [], "columns": []}}
            if path == "/api/get_gpt_wall?compact=1&include_archived=0&event_limit=10":
                return 200, {"ok": True, "data": {"meta": {"active_cards": 0}}}
            if path == "/api/list_repair_orders?compact=true&redact_private=true":
                return 200, {"ok": True, "data": {"repair_orders": []}}
            return 200, {"ok": True, "data": {}}

        with patch.object(module, "_api_request", side_effect=fake_api_request):
            result = module.check_api_surface("http://127.0.0.1:41731")

        self.assertTrue(result["ok"])
        self.assertIn("/api/get_board_snapshot?compact=1&include_archive=0", calls)
        self.assertIn("/api/get_gpt_wall?compact=1&include_archived=0&event_limit=10", calls)
        self.assertIn("/api/list_repair_orders?compact=true&redact_private=true", calls)
        self.assertNotIn("/api/get_board_snapshot", calls)
        self.assertNotIn("/api/get_gpt_wall", calls)
        self.assertNotIn("/api/list_repair_orders", calls)

    def test_unconfigured_api_and_mcp_surfaces_are_printed_as_skipped(self) -> None:
        module = load_live_connector_module()
        api_surface = module.check_api_surface("")
        api_surface.update(
            {
                "base_url": "https://crm.autostopcrm.ru",
                "surface_kind": "public",
                "error": "public_api_credentials_not_provided",
            }
        )
        mcp_surface = {
            "checked": False,
            "ok": False,
            "mcp_url": "",
            "error": "mcp_check_skipped",
        }

        output = io.StringIO()
        with patch.object(sys, "stdout", output):
            module._print_api_surface(api_surface)
            module._print_mcp(mcp_surface)

        printed = output.getvalue()
        self.assertEqual(printed.count("status: skipped"), 2)
        self.assertNotIn("status: failed", printed)
        self.assertIn("public_api_credentials_not_provided", printed)
        self.assertIn("mcp_check_skipped", printed)

    def test_check_site_rejects_redirect_without_following_it(self) -> None:
        module = load_live_connector_module()
        redirect = module.urllib.error.HTTPError(
            url="http://example.test/",
            code=302,
            msg="Found",
            hdrs={"Location": "https://example.test/login"},
            fp=None,
        )

        with (
            patch.object(module, "_urlopen_no_redirect", side_effect=redirect) as opener,
            patch.object(module.urllib.request, "urlopen") as urlopen,
        ):
            result = module.check_site("http://example.test")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status_code"], 302)
        self.assertEqual(result["error"], "site_probe_redirected")
        self.assertEqual(opener.call_count, 1)
        urlopen.assert_not_called()

    def test_check_site_follows_same_origin_fingerprinted_board_script_contract(self) -> None:
        module = load_live_connector_module()
        script = b'fetch("/api/login_operator")'
        digest = module.hashlib.sha256(script).hexdigest()
        site_url = "https://crm.example.test"
        asset_url = f"{site_url}/assets/board.{digest}.js"
        document = (
            "<html><head><title>AutoStop CRM</title></head>"
            f'<body><script defer src="/assets/board.{digest}.js"></script></body></html>'
        ).encode()
        responses = (
            FakeResponse(
                document,
                url=f"{site_url}/",
                headers={"Content-Type": "text/html; charset=utf-8"},
            ),
            FakeResponse(
                script,
                url=asset_url,
                headers={"Content-Type": "application/javascript; charset=utf-8"},
            ),
        )

        with (
            patch.object(module, "BOARD_WEB_APP_JS_PATH", f"/assets/board.{digest}.js"),
            patch.object(module, "_urlopen_no_redirect", side_effect=responses) as opener,
        ):
            result = module.check_site(site_url, expect_https=True)

        self.assertTrue(result["ok"])
        self.assertTrue(result["contains_login_route"])
        self.assertEqual(result["login_route_source"], "asset")
        self.assertEqual(result["asset_probe_url"], asset_url)
        self.assertEqual(result["script_assets_checked"], 1)
        self.assertIsNone(result["script_asset_error"])
        self.assertEqual(opener.call_count, 2)

    def test_check_site_does_not_fetch_cross_origin_board_script(self) -> None:
        module = load_live_connector_module()
        digest = module.hashlib.sha256(b'fetch("/api/login_operator")').hexdigest()
        site_url = "https://crm.example.test"
        document = (
            "<html><head><title>AutoStop CRM</title></head>"
            f'<body><script src="https://cdn.example.test/assets/board.{digest}.js">'
            "</script></body></html>"
        ).encode()

        with (
            patch.object(module, "BOARD_WEB_APP_JS_PATH", f"/assets/board.{digest}.js"),
            patch.object(
                module,
                "_urlopen_no_redirect",
                return_value=FakeResponse(
                    document,
                    url=f"{site_url}/",
                    headers={"Content-Type": "text/html; charset=utf-8"},
                ),
            ) as opener,
        ):
            result = module.check_site(site_url, expect_https=True)

        self.assertFalse(result["ok"])
        self.assertFalse(result["contains_login_route"])
        self.assertEqual(result["script_assets_checked"], 0)
        self.assertEqual(result["script_asset_error"], "board_script_not_found")
        self.assertEqual(opener.call_count, 1)

    def test_check_site_does_not_follow_unfingerprinted_asset(self) -> None:
        module = load_live_connector_module()
        site_url = "https://crm.example.test"
        document = (
            b"<html><head><title>AutoStop CRM</title></head>"
            b'<body><script src="/assets/board.js"></script></body></html>'
        )

        with patch.object(
            module,
            "_urlopen_no_redirect",
            return_value=FakeResponse(document, url=f"{site_url}/"),
        ) as opener:
            result = module.check_site(site_url, expect_https=True)

        self.assertFalse(result["ok"])
        self.assertFalse(result["contains_login_route"])
        self.assertEqual(result["asset_probe_url"], "")
        self.assertEqual(result["script_assets_checked"], 0)
        self.assertEqual(result["script_asset_error"], "board_script_not_found")
        self.assertEqual(opener.call_count, 1)

    def test_check_site_rejects_board_script_with_wrong_fingerprint(self) -> None:
        module = load_live_connector_module()
        site_url = "https://crm.example.test"
        asset_url = f"{site_url}/assets/board.{'c' * 64}.js"
        document = (
            "<html><head><title>AutoStop CRM</title></head>"
            f'<body><script src="{asset_url}"></script></body></html>'
        ).encode()
        responses = (
            FakeResponse(
                document,
                url=f"{site_url}/",
                headers={"Content-Type": "text/html; charset=utf-8"},
            ),
            FakeResponse(
                b'fetch("/api/login_operator")',
                url=asset_url,
                headers={"Content-Type": "application/javascript; charset=utf-8"},
            ),
        )

        with (
            patch.object(module, "BOARD_WEB_APP_JS_PATH", f"/assets/board.{'c' * 64}.js"),
            patch.object(module, "_urlopen_no_redirect", side_effect=responses),
        ):
            result = module.check_site(site_url, expect_https=True)

        self.assertFalse(result["ok"])
        self.assertFalse(result["contains_login_route"])
        self.assertEqual(result["script_assets_checked"], 1)
        self.assertEqual(result["script_asset_error"], "board_script_fingerprint_mismatch")

    def test_check_site_accepts_current_external_board_shell(self) -> None:
        module = load_live_connector_module()
        from minimal_kanban.web_assets import (
            BOARD_WEB_APP_HTML,
            BOARD_WEB_APP_JS,
            BOARD_WEB_APP_JS_PATH,
        )

        site_url = "https://crm.example.test"
        responses = (
            FakeResponse(
                BOARD_WEB_APP_HTML.encode(),
                url=f"{site_url}/",
                headers={"Content-Type": "text/html; charset=utf-8"},
            ),
            FakeResponse(
                BOARD_WEB_APP_JS.encode(),
                url=f"{site_url}{BOARD_WEB_APP_JS_PATH}",
                headers={"Content-Type": "application/javascript; charset=utf-8"},
            ),
        )

        with patch.object(module, "_urlopen_no_redirect", side_effect=responses):
            result = module.check_site(site_url, expect_https=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["login_route_source"], "asset")
        self.assertEqual(result["asset_probe_url"], f"{site_url}{BOARD_WEB_APP_JS_PATH}")
        self.assertEqual(result["script_assets_checked"], 1)

    def test_check_site_keeps_support_for_inline_login_route(self) -> None:
        module = load_live_connector_module()
        site_url = "https://crm.example.test"
        document = (
            b"<html><head><title>AutoStop CRM</title></head>"
            b'<body><script>fetch("/api/login_operator")</script></body></html>'
        )

        with patch.object(
            module,
            "_urlopen_no_redirect",
            return_value=FakeResponse(
                document,
                url=f"{site_url}/",
                headers={"Content-Type": "text/html; charset=utf-8"},
            ),
        ) as opener:
            result = module.check_site(site_url, expect_https=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["login_route_source"], "html")
        self.assertEqual(result["script_assets_checked"], 0)
        self.assertEqual(opener.call_count, 1)

    def test_url_helpers_handle_malformed_urls_without_crashing(self) -> None:
        module = load_live_connector_module()

        self.assertEqual(module._fallback_http_url("https://[::1"), "")
        self.assertEqual(module._classify_probe_url("http://[::1"), "unknown")
        self.assertEqual(module._classify_probe_url("http://localhost.:41731"), "local")

        result = module.check_site("http://[::1", expect_https=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "site_url_invalid")

    def test_json_dumps_sanitizes_non_finite_numbers(self) -> None:
        module = load_live_connector_module()

        encoded = module._json_dumps({"ok": True, "score": float("nan"), "items": [float("inf")]})

        self.assertEqual(json.loads(encoded), {"ok": True, "score": None, "items": [None]})
        self.assertNotIn("NaN", encoded)
        self.assertNotIn("Infinity", encoded)

    def test_json_dumps_handles_self_referential_payload(self) -> None:
        module = load_live_connector_module()
        payload: dict[str, object] = {"ok": True}
        payload["self"] = payload

        encoded = module._json_dumps(payload)
        decoded = json.loads(encoded)
        node = decoded
        for _ in range(9):
            node = node["self"]

        self.assertIsInstance(node, str)

    def test_load_settings_uses_defaults_for_oversized_settings_file(self) -> None:
        module = load_live_connector_module()
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_file = Path(temp_dir) / "settings.json"
            settings_file.write_text("x" * 16, encoding="utf-8")

            with (
                patch.object(module, "LIVE_CONNECTOR_SETTINGS_MAX_BYTES", 8),
                patch.object(module, "get_settings_file", return_value=settings_file),
            ):
                settings = module.load_settings()

        self.assertEqual(settings, module.IntegrationSettings.defaults())


if __name__ == "__main__":
    unittest.main()
