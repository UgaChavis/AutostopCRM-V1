from __future__ import annotations

import json
import logging
import tempfile
import threading
import unittest
from pathlib import Path

if __package__:
    from tests.source_path_support import prepend_source_path
else:
    from source_path_support import prepend_source_path

prepend_source_path()

from minimal_kanban.operator_auth import OperatorAuthService
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore

READ = "employees_read_access"
MANAGE = "employees_cashboxes_access"


class OperatorPermissionConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.logger = logging.getLogger(self.id())
        self.store = JsonStore(root / "state.json", self.logger)
        self.cards = CardService(self.store, self.logger)
        self.users_file = root / "users.json"
        self.service = self.peer()
        self.admin = {"username": "ADMIN", "is_admin": True, "token": "synthetic"}
        self.save(password="initial-password", permissions=[])

    def peer(self) -> OperatorAuthService:
        return OperatorAuthService(self.store, self.cards, users_file=self.users_file)

    def save(self, service: OperatorAuthService | None = None, **fields) -> dict:
        return (service or self.service).save_user(
            {"_operator_session": self.admin, "username": "review-user", **fields}
        )

    def test_stale_permissions_cannot_erase_concurrent_grant_or_change_password(self) -> None:
        token = self.service.login({"username": "review-user", "password": "initial-password"})[
            "session"
        ]["token"]
        self.save(permissions=[READ])
        before = self.users_file.read_bytes()
        with self.assertRaises(ServiceError) as raised:
            self.save(permissions=[MANAGE], expected_permissions=[], password="stale-password")
        self.assertEqual(raised.exception.code, "operator_user_conflict")
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.users_file.read_bytes(), before)
        self.assertIn(READ, self.service.resolve_session(token)["permissions"])

    def test_permission_cas_allows_unrelated_password_and_binding_changes(self) -> None:
        self.save(password="new-password")
        employee = self.cards.save_employee({"name": "Synthetic review employee"})["employee"]
        self.peer().set_user_employee(
            {
                "_operator_session": self.admin,
                "username": "review-user",
                "employee_id": employee["id"],
            }
        )
        result = self.save(permissions=[READ], expected_permissions=[])
        self.assertEqual(result["user"]["employee_id"], employee["id"])
        profile = self.peer().login({"username": "review-user", "password": "new-password"})
        self.assertEqual(profile["user"]["permissions"], [READ])

    def test_parallel_password_save_preserves_grant_and_revokes_old_session(self) -> None:
        token = self.service.login({"username": "review-user", "password": "initial-password"})[
            "session"
        ]["token"]
        peer = self.peer()
        barrier = threading.Barrier(2)
        errors = []

        def save(service, fields):
            try:
                barrier.wait(timeout=5)
                self.save(service, **fields)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=save, args=(self.service, {"permissions": [READ]})),
            threading.Thread(target=save, args=(peer, {"password": "new-password"})),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertIsNone(self.service.resolve_session(token))
        fresh = self.peer()
        profile = fresh.login({"username": "review-user", "password": "new-password"})
        self.assertEqual(profile["user"]["permissions"], [READ])
        raw = json.loads(self.users_file.read_text(encoding="utf-8"))
        user = next(
            u for u in fresh._normalize_users(raw["users"]) if u["username"] == "REVIEW-USER"
        )
        self.assertEqual(user["permissions"], [READ])
