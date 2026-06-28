from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "container_healthcheck.py"


def load_container_healthcheck_module():
    spec = importlib.util.spec_from_file_location("container_healthcheck", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("container_healthcheck.py is importable")
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


class ContainerHealthcheckTests(unittest.TestCase):
    def test_api_check_rejects_nonstandard_json_constants(self) -> None:
        module = load_container_healthcheck_module()

        with patch.object(
            module,
            "_urlopen_no_redirect",
            return_value=FakeResponse(b'{"ok": NaN}'),
        ):
            with self.assertRaisesRegex(ValueError, "Unsupported JSON constant: NaN"):
                module._check_api()

    def test_api_check_rejects_deeply_nested_json(self) -> None:
        module = load_container_healthcheck_module()
        deep_json = ("[" * 5000 + "0" + "]" * 5000).encode("utf-8")

        with patch.object(
            module,
            "_urlopen_no_redirect",
            return_value=FakeResponse(deep_json),
        ):
            with self.assertRaisesRegex(
                ValueError,
                "API health response JSON is too deeply nested",
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
        ):
            with self.assertRaisesRegex(ValueError, "API health response is too large"):
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
