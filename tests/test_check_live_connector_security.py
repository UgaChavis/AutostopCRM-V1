from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

if __package__:
    from tests.module_loader_support import load_module_from_file
else:
    from module_loader_support import load_module_from_file

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "check_live_connector.py"


def load_module() -> ModuleType:
    return load_module_from_file("check_live_connector_security", SCRIPT_PATH)


class PublicAuthSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_loader_restores_previous_module_entry(self) -> None:
        module_name = "check_live_connector_security"
        previous_module = sys.modules.get(module_name)
        had_previous_module = module_name in sys.modules
        sentinel = ModuleType(module_name)
        sys.modules[module_name] = sentinel

        try:
            loaded_module = load_module()

            self.assertIsNot(loaded_module, sentinel)
            self.assertIs(sys.modules[module_name], sentinel)
        finally:
            if had_previous_module:
                sys.modules[module_name] = previous_module
            else:
                sys.modules.pop(module_name, None)

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
