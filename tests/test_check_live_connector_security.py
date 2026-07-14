from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "check_live_connector.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_live_connector_security", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("check_live_connector.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PublicAuthSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_anonymous_public_read_requires_explicit_auth_rejection(self) -> None:
        with patch.object(
            self.module,
            "_api_request",
            return_value=(401, {"ok": False, "error": {"code": "unauthorized"}}),
        ):
            result = self.module.check_public_read_protection(
                "https://crm.autostopcrm.ru", require_https=True
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status_code"], 401)

    def test_reachable_but_unprotected_public_read_fails(self) -> None:
        with patch.object(
            self.module,
            "_api_request",
            return_value=(200, {"ok": True, "data": {"cards": []}}),
        ):
            result = self.module.check_public_read_protection(
                "https://crm.autostopcrm.ru", require_https=True
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "anonymous_public_read_not_blocked")

    def test_anonymous_public_write_requires_explicit_auth_rejection(self) -> None:
        with patch.object(
            self.module,
            "_api_request",
            return_value=(403, {"ok": False, "error": {"code": "forbidden"}}),
        ):
            result = self.module.check_public_write_protection(
                "https://crm.autostopcrm.ru", require_https=True
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status_code"], 403)

    def test_anonymous_public_write_does_not_treat_maintenance_as_auth(self) -> None:
        with patch.object(
            self.module,
            "_api_request",
            return_value=(503, {"ok": False, "error": {"code": "maintenance_mode"}}),
        ):
            result = self.module.check_public_write_protection(
                "https://crm.autostopcrm.ru", require_https=True
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status_code"], 503)
        self.assertEqual(result["error_code"], "maintenance_mode")
        self.assertEqual(result["error"], "anonymous_public_write_not_blocked")

    def test_public_auth_probes_refuse_plain_http_when_https_is_required(self) -> None:
        read = self.module.check_public_read_protection(
            "http://crm.autostopcrm.ru", require_https=True
        )
        write = self.module.check_public_write_protection(
            "http://crm.autostopcrm.ru", require_https=True
        )

        self.assertFalse(read["ok"])
        self.assertFalse(write["ok"])
        self.assertIn("requires_https", str(read["error"]))
        self.assertIn("requires_https", str(write["error"]))


if __name__ == "__main__":
    unittest.main()
