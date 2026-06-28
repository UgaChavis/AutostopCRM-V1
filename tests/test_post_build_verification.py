from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "post_build_verification.py"


def load_post_build_verification_module():
    spec = importlib.util.spec_from_file_location("post_build_verification", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("post_build_verification.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    status = 200

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._body
        return self._body[:size]


class OversizedResponse(FakeResponse):
    def __init__(self) -> None:
        super().__init__(b"")

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = 1
        return b"x" * size


class PostBuildVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_post_build_verification_module()

    def test_operator_credentials_generate_strong_throwaway_default_without_admin_password(
        self,
    ) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "AUTOSTOP_SMOKE_OPERATOR_USERNAME": "",
                    "AUTOSTOP_SMOKE_OPERATOR_PASSWORD": "",
                },
                clear=False,
            ),
            patch.object(self.module.secrets, "token_urlsafe", return_value="token"),
        ):
            username, password = self.module._operator_credentials()

        self.assertEqual(username, "release-smoke-admin")
        self.assertEqual(password, "ReleaseSmoke-token1!")
        self.assertNotEqual(password, "admin")

    def test_operator_credentials_use_smoke_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AUTOSTOP_SMOKE_OPERATOR_USERNAME": "smoke-admin",
                "AUTOSTOP_SMOKE_OPERATOR_PASSWORD": "smoke-secret",
            },
            clear=False,
        ):
            username, password = self.module._operator_credentials()

        self.assertEqual(username, "smoke-admin")
        self.assertEqual(password, "smoke-secret")

    def test_login_operator_headers_uses_provided_credentials(self) -> None:
        with patch.object(
            self.module,
            "send_request",
            return_value=(200, {"ok": True, "data": {"session": {"token": "session-token"}}}),
        ) as send_request:
            headers = self.module.login_operator_headers(
                "http://127.0.0.1:41731",
                username="verify-admin",
                password="verify-secret",
            )

        self.assertEqual(headers, {"X-Operator-Session": "session-token"})
        send_request.assert_called_once_with(
            "http://127.0.0.1:41731",
            "/api/login_operator",
            {"username": "verify-admin", "password": "verify-secret"},
        )

    def test_send_request_rejects_nonstandard_json_constants(self) -> None:
        with patch.object(
            self.module,
            "_urlopen_no_redirect",
            return_value=FakeResponse(b'{"ok": true, "data": NaN}'),
        ):
            with self.assertRaisesRegex(ValueError, "Unsupported JSON constant: NaN"):
                self.module.send_request("http://127.0.0.1:41731", "/api/health")

    def test_send_request_rejects_deeply_nested_response(self) -> None:
        deep_json = ("[" * 5000 + "0" + "]" * 5000).encode("utf-8")

        with patch.object(
            self.module,
            "_urlopen_no_redirect",
            return_value=FakeResponse(deep_json),
        ):
            with self.assertRaisesRegex(ValueError, "API response JSON is too deeply nested"):
                self.module.send_request("http://127.0.0.1:41731", "/api/health")

    def test_send_request_rejects_oversized_response(self) -> None:
        with patch.object(
            self.module,
            "_urlopen_no_redirect",
            return_value=OversizedResponse(),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "Post-build verification response is too large",
            ):
                self.module.send_request("http://127.0.0.1:41731", "/api/health")

    def test_send_request_rejects_redirect_response(self) -> None:
        redirect = self.module.urllib.error.HTTPError(
            url="http://127.0.0.1:41731/api/login_operator",
            code=302,
            msg="Found",
            hdrs={"Location": "https://example.test/api/login_operator"},
            fp=None,
        )

        with patch.object(self.module, "_urlopen_no_redirect", side_effect=redirect):
            with self.assertRaisesRegex(ValueError, "Post-build verification request redirected"):
                self.module.send_request(
                    "http://127.0.0.1:41731",
                    "/api/login_operator",
                    {"username": "verify-admin", "password": "secret"},
                )

    def test_json_dumps_sanitizes_nonfinite_values(self) -> None:
        encoded = self.module._json_dumps({"ok": True, "value": float("inf")})

        self.assertNotIn("Infinity", encoded)
        self.assertEqual(json.loads(encoded), {"ok": True, "value": None})

    def test_json_dumps_handles_self_referential_payload(self) -> None:
        payload: dict[str, object] = {"ok": True}
        payload["self"] = payload

        encoded = self.module._json_dumps(payload)
        decoded = json.loads(encoded)
        node = decoded
        for _ in range(9):
            node = node["self"]

        self.assertIsInstance(node, str)

    def test_board_scale_value_rejects_invalid_values_with_context(self) -> None:
        self.assertEqual(
            self.module._board_scale_value("1.25", context="snapshot"),
            1.25,
        )

        for value in ("bad", float("inf"), True, 1e308):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    self.module.VerificationError,
                    "snapshot returned invalid board_scale",
                ):
                    self.module._board_scale_value(value, context="snapshot")

    def test_launch_app_detaches_child_stdin(self) -> None:
        executable = Path("C:/AutostopCRM/app.exe")
        appdata_root = Path("C:/AutostopCRM/appdata")

        with patch.object(self.module.subprocess, "Popen") as popen:
            self.module.launch_app(executable, appdata_root, api_port=41731)

        self.assertEqual(popen.call_args.args[0], [str(executable)])
        self.assertEqual(popen.call_args.kwargs["stdin"], self.module.subprocess.DEVNULL)

    def test_read_log_tail_text_reads_bounded_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = Path(temp_dir) / "minimal-kanban.log"
            log_file.write_text(
                ("older line\n" * 20) + "failed_to_start_api: blocked port\n",
                encoding="utf-8",
            )

            with patch.object(self.module, "POST_BUILD_LOG_TAIL_MAX_BYTES", 64):
                text = self.module._read_log_tail_text(log_file)

        self.assertIn("failed_to_start_api", text)
        self.assertNotIn("older line\nolder line\nolder line", text)


if __name__ == "__main__":
    unittest.main()
