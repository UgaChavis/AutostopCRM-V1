"""The security warning checks each distinct default credential at most once."""

import unittest
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban import operator_auth  # noqa: E402
from minimal_kanban.operator_auth import OperatorAuthService  # noqa: E402


class DefaultAdminWarningTests(unittest.TestCase):
    def test_security_warning_with_current_and_legacy_defaults(self):
        service = object.__new__(OperatorAuthService)
        cases = (
            ("admin", "admin", True, ["admin"]),
            ("admin", "admin123", True, ["admin", "admin123"]),
            ("admin", "synthetic", False, ["admin", "admin123"]),
            ("admin123", "admin123", True, ["admin123"]),
            ("admin123", "synthetic", False, ["admin123"]),
            ("synthetic", "admin123", True, ["admin123"]),
            ("synthetic", "synthetic", False, ["admin123"]),
        )
        for configured, stored, expected, checks in cases:
            with (
                self.subTest(configured=configured, stored=stored),
                patch(
                    "minimal_kanban.operator_auth.get_default_admin_username", return_value="admin"
                ),
                patch(
                    "minimal_kanban.operator_auth.get_default_admin_password",
                    return_value=configured,
                ),
                patch(
                    "minimal_kanban.operator_auth._verify_password",
                    side_effect=lambda password, password_hash: password == password_hash,
                ) as verify,
            ):
                actual = service._uses_default_admin_credentials(
                    {"role": "admin", "username": "ADMIN", "password_hash": stored}
                )
                self.assertEqual(actual, expected)
                self.assertEqual([call.args[0] for call in verify.call_args_list], checks)

    def test_other_users_and_malformed_hashes_do_not_trigger_warning(self):
        service = object.__new__(OperatorAuthService)
        users = (
            {"role": "operator", "username": "ADMIN", "password_hash": "anything"},
            {"role": "admin", "username": "OTHER", "password_hash": "anything"},
            {"role": "admin", "username": "ADMIN", "password_hash": None},
        )
        with (
            patch("minimal_kanban.operator_auth.get_default_admin_username", return_value="admin"),
            patch(
                "minimal_kanban.operator_auth.get_default_admin_password", return_value="admin123"
            ),
            patch("minimal_kanban.operator_auth._verify_password") as verify,
        ):
            for user in users:
                with self.subTest(user=user):
                    self.assertFalse(service._uses_default_admin_credentials(user))
            verify.assert_not_called()
        with (
            patch("minimal_kanban.operator_auth.get_default_admin_username", return_value="admin"),
            patch(
                "minimal_kanban.operator_auth.get_default_admin_password", return_value="admin123"
            ),
            patch(
                "minimal_kanban.operator_auth._verify_password",
                wraps=operator_auth._verify_password,
            ) as verify,
        ):
            for invalid in ("malformed", 123, {"bad": True}):
                with self.subTest(invalid=invalid):
                    self.assertFalse(
                        service._uses_default_admin_credentials(
                            {"role": "admin", "username": "ADMIN", "password_hash": invalid}
                        )
                    )
            self.assertEqual(verify.call_count, 3)
