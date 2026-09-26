from __future__ import annotations

import json
import ntpath
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, patch

if __package__:
    from tests.http_fixture_support import FakeReadableResponse
    from tests.module_loader_support import load_module_from_file
else:
    from http_fixture_support import FakeReadableResponse
    from module_loader_support import load_module_from_file

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "post_build_verification.py"


def load_post_build_verification_module() -> ModuleType:
    return load_module_from_file("post_build_verification", SCRIPT_PATH)


class FakeResponse(FakeReadableResponse):
    status = 200

    def __init__(self, body: bytes, *, content_type: str = "application/json") -> None:
        super().__init__(body)
        self.headers = {"Content-Type": content_type}


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

    def test_loader_restores_previous_module_entry(self) -> None:
        module_name = "post_build_verification"
        previous_module = sys.modules.get(module_name)
        had_previous_module = module_name in sys.modules
        sentinel = ModuleType(module_name)
        sys.modules[module_name] = sentinel

        try:
            loaded_module = load_post_build_verification_module()

            self.assertIsNot(loaded_module, sentinel)
            self.assertIs(sys.modules[module_name], sentinel)
        finally:
            if had_previous_module:
                sys.modules[module_name] = previous_module
            else:
                sys.modules.pop(module_name, None)

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

    def test_wait_for_api_shutdown_throttles_every_http_response(self) -> None:
        base_url = "http://127.0.0.1:41731"
        for response in ((200, {"ok": True}), (200, {"ok": False}), (503, {"ok": False})):
            with self.subTest(response=response):
                with (
                    patch.object(self.module.time, "time", side_effect=[0, 0, 1]),
                    patch.object(self.module.time, "sleep") as sleep,
                    patch.object(self.module, "send_request", return_value=response) as request,
                    self.assertRaises(self.module.VerificationError),
                ):
                    self.module.wait_for_api_shutdown(base_url, timeout_seconds=1)
                request.assert_called_once_with(base_url, "/api/health", method="GET")
                sleep.assert_called_once_with(0.5)

        with (
            patch.object(self.module.time, "time", side_effect=[0, 0]),
            patch.object(self.module.time, "sleep") as sleep,
            patch.object(self.module, "send_request", side_effect=OSError("closed")) as request,
        ):
            self.module.wait_for_api_shutdown(base_url, timeout_seconds=1)
        request.assert_called_once_with(base_url, "/api/health", method="GET")
        sleep.assert_not_called()

    def test_send_request_rejects_nonstandard_json_constants(self) -> None:
        with (
            patch.object(
                self.module,
                "_urlopen_no_redirect",
                return_value=FakeResponse(b'{"ok": true, "data": NaN}'),
            ),
            self.assertRaisesRegex(ValueError, "Unsupported JSON constant: NaN"),
        ):
            self.module.send_request("http://127.0.0.1:41731", "/api/health")

    def test_send_request_rejects_deeply_nested_response(self) -> None:
        deep_json = ("[" * 5000 + "0" + "]" * 5000).encode("utf-8")

        with (
            patch.object(
                self.module,
                "_urlopen_no_redirect",
                return_value=FakeResponse(deep_json),
            ),
            self.assertRaisesRegex(ValueError, "API response JSON is too deeply nested"),
        ):
            self.module.send_request("http://127.0.0.1:41731", "/api/health")

    def test_send_request_rejects_oversized_response(self) -> None:
        with (
            patch.object(
                self.module,
                "_urlopen_no_redirect",
                return_value=OversizedResponse(),
            ),
            self.assertRaisesRegex(
                ValueError,
                "Post-build verification response is too large",
            ),
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

        with (
            patch.object(self.module, "_urlopen_no_redirect", side_effect=redirect),
            self.assertRaisesRegex(ValueError, "Post-build verification request redirected"),
        ):
            self.module.send_request(
                "http://127.0.0.1:41731",
                "/api/login_operator",
                {"username": "verify-admin", "password": "secret"},
            )

    def test_verify_static_asset_checks_packaged_file_contract(self) -> None:
        with patch.object(
            self.module,
            "_urlopen_no_redirect",
            return_value=FakeResponse(b"\x89PNG\r\n\x1a\nicon", content_type="image/png"),
        ):
            result = self.module.verify_static_asset(
                "http://127.0.0.1:41731",
                "/favicon.png",
                expected_content_type="image/png",
                expected_signature=b"\x89PNG\r\n\x1a\n",
            )

        self.assertEqual(result["status"], 200)
        self.assertEqual(result["content_type"], "image/png")

    def test_verify_static_asset_rejects_invalid_signature(self) -> None:
        with (
            patch.object(
                self.module,
                "_urlopen_no_redirect",
                return_value=FakeResponse(b"not-an-icon", content_type="image/png"),
            ),
            self.assertRaisesRegex(
                self.module.VerificationError,
                "invalid file signature",
            ),
        ):
            self.module.verify_static_asset(
                "http://127.0.0.1:41731",
                "/favicon.png",
                expected_content_type="image/png",
                expected_signature=b"\x89PNG\r\n\x1a\n",
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
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(
                    self.module.VerificationError,
                    "snapshot returned invalid board_scale",
                ),
            ):
                self.module._board_scale_value(value, context="snapshot")

    def test_launch_app_detaches_child_stdin(self) -> None:
        executable = Path("C:/AutostopCRM/app.exe")
        appdata_root = Path("C:/AutostopCRM/appdata")

        with patch.object(self.module.subprocess, "Popen") as popen:
            self.module.launch_app(executable, appdata_root, api_port=41731)

        self.assertEqual(popen.call_args.args[0], [str(executable)])
        self.assertEqual(popen.call_args.kwargs["stdin"], self.module.subprocess.DEVNULL)

    def test_launch_app_isolates_roaming_local_and_desktop_without_changing_parent(self) -> None:
        appdata_root = Path("C:/fixture/verification/AppData/Roaming")
        inherited_paths = {
            "APPDATA": "C:/fixture/real-user/roaming",
            "LOCALAPPDATA": "C:/fixture/real-user/local",
            "USERPROFILE": "C:/fixture/real-user",
        }
        with (
            patch.dict(os.environ, inherited_paths),
            patch.object(self.module.subprocess, "Popen") as popen,
        ):
            original_environment = dict(os.environ)
            self.module.launch_app(Path("C:/fixture/app.exe"), appdata_root, api_port=41739)
            self.assertEqual(original_environment, dict(os.environ))

        child_env = popen.call_args.kwargs["env"]
        self.assertEqual(str(appdata_root), child_env["APPDATA"])
        self.assertEqual(str(appdata_root / "local"), child_env["LOCALAPPDATA"])
        self.assertEqual(str(appdata_root / "profile"), child_env["USERPROFILE"])
        self.assertEqual(original_environment.get("HOME"), child_env.get("HOME"))
        self.assertEqual("41739", child_env["MINIMAL_KANBAN_API_PORT"])
        self.assertEqual("1", child_env["MINIMAL_KANBAN_API_PORT_FALLBACK_LIMIT"])
        with patch.dict(os.environ, child_env, clear=True):
            self.assertEqual(str(appdata_root / "profile"), ntpath.expanduser("~"))
            if os.name == "nt":
                from minimal_kanban.desktop_connector_files import _resolve_desktop_path

                self.assertEqual(appdata_root / "profile" / "Desktop", _resolve_desktop_path())

    def test_launch_options_cannot_override_disposable_profile_paths(self) -> None:
        appdata_root = Path("C:/fixture/verification/StartupErrorAppData")
        extra_env = {
            "APPDATA": "C:/fixture/real-user/roaming",
            "LOCALAPPDATA": "C:/fixture/real-user/local",
            "USERPROFILE": "C:/fixture/real-user",
            "MINIMAL_KANBAN_SUPPRESS_ERROR_DIALOGS": "1",
        }
        original_options = dict(extra_env)
        original_environment = dict(os.environ)
        with patch.object(self.module.subprocess, "Popen") as popen:
            self.module.launch_app(
                Path("C:/fixture/app.exe"), appdata_root, api_port=41739, extra_env=extra_env
            )

        child_env = popen.call_args.kwargs["env"]
        self.assertEqual(str(appdata_root), child_env["APPDATA"])
        self.assertEqual(str(appdata_root / "local"), child_env["LOCALAPPDATA"])
        self.assertEqual(str(appdata_root / "profile"), child_env["USERPROFILE"])
        self.assertEqual("1", child_env["MINIMAL_KANBAN_SUPPRESS_ERROR_DIALOGS"])
        self.assertEqual(original_options, extra_env)
        self.assertEqual(original_environment, dict(os.environ))

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

    def test_startup_error_verification_rejects_a_healthy_blocked_port(self) -> None:
        process = Mock()
        blocker = Mock()
        with (
            patch.object(self.module, "block_port", return_value=(blocker, 41739)),
            patch.object(self.module, "launch_app", return_value=process),
            patch.object(self.module, "_wait_for_process_return_code", return_value=1),
            patch.object(self.module, "send_request", return_value=(200, {"ok": True})),
            patch.object(self.module, "_wait_for_log_file") as wait_for_log,
            patch.object(self.module, "stop_process"),
            self.assertRaisesRegex(
                self.module.VerificationError,
                "unexpectedly started API on a blocked port",
            ),
        ):
            self.module.verify_startup_error_handling(
                Path("C:/fixture/app.exe"),
                Path("C:/fixture/appdata"),
            )

        wait_for_log.assert_not_called()
        blocker.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
