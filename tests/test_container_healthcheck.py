from __future__ import annotations

import importlib.util
import sys
import unittest
from functools import cache
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

if __package__:
    from tests.http_fixture_support import FakeReadableResponse
else:
    from http_fixture_support import FakeReadableResponse

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "container_healthcheck.py"


@cache
def load_container_healthcheck_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("container_healthcheck", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("container_healthcheck.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse(FakeReadableResponse):
    status = 200


class ContainerHealthcheckTests(unittest.TestCase):
    def test_api_check_rejects_nonstandard_json_constants(self) -> None:
        module = load_container_healthcheck_module()

        with (
            patch.object(
                module,
                "_urlopen_no_redirect",
                return_value=FakeResponse(b'{"ok": NaN}'),
            ),
            self.assertRaisesRegex(ValueError, "Unsupported JSON constant: NaN"),
        ):
            module._check_api()

    def test_api_check_rejects_deeply_nested_json(self) -> None:
        module = load_container_healthcheck_module()
        deep_json = ("[" * 5000 + "0" + "]" * 5000).encode("utf-8")

        with (
            patch.object(
                module,
                "_urlopen_no_redirect",
                return_value=FakeResponse(deep_json),
            ),
            self.assertRaisesRegex(
                ValueError,
                "API health response JSON is too deeply nested",
            ),
        ):
            module._check_api()

    def test_api_check_rejects_non_object_json(self) -> None:
        module = load_container_healthcheck_module()

        with patch.object(
            module,
            "_urlopen_no_redirect",
            return_value=FakeResponse(b"[]"),
        ):
            self.assertFalse(module._check_api())

    def test_api_check_rejects_oversized_response(self) -> None:
        module = load_container_healthcheck_module()

        with (
            patch.object(module, "API_HEALTH_RESPONSE_MAX_BYTES", 4),
            patch.object(
                module,
                "_urlopen_no_redirect",
                return_value=FakeResponse(b"12345"),
            ),
            self.assertRaisesRegex(ValueError, "API health response is too large"),
        ):
            module._check_api()

    def test_api_redirect_makes_healthcheck_fail_without_following_it(self) -> None:
        module = load_container_healthcheck_module()
        redirect = module.urllib.error.HTTPError(
            url=module.API_HEALTH_URL,
            code=302,
            msg="Found",
            hdrs={"Location": "https://example.test/api/health"},
            fp=None,
        )

        with patch.object(module, "_urlopen_no_redirect", side_effect=redirect):
            self.assertEqual(module.main(), 1)


if __name__ == "__main__":
    unittest.main()
