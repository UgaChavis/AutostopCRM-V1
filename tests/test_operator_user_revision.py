from __future__ import annotations

import json
import logging
import sys
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.operator_auth import OperatorAuthService
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


class OperatorUserRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        logger = logging.getLogger(f"test.operator_user_revision.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(state_file=root / "state.json", logger=logger)
        self.cards = CardService(
            self.store,
            logger,
            attachments_dir=root / "attachments",
            repair_orders_dir=root / "repair-orders",
        )
        self.users_file = root / "users.json"
        self.service = self._new_service(logger)
        self.admin_session = {"username": "ADMIN", "is_admin": True, "token": "test"}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _new_service(self, logger: logging.Logger) -> OperatorAuthService:
        service = OperatorAuthService(
            self.store,
            self.cards,
            users_file=self.users_file,
            logger=logger,
        )
        service._sync_change_feed = Mock()
        return service

    def _user(self, username: str) -> dict:
        state = self.service._read_normalized_state()
        return next(user for user in state["users"] if user["username"] == username)

    def test_concurrent_user_saves_timestamp_inside_lock_and_follow_commit_order(self) -> None:
        logger = logging.getLogger(f"test.operator_user_revision.peer.{self._testMethodName}")
        second = self._new_service(logger)
        first_write_started = threading.Event()
        allow_first_write = threading.Event()
        second_started = threading.Event()
        second_clock_called = threading.Event()
        errors: list[BaseException] = []
        results: dict[str, dict] = {}
        original_first_write = self.service._write_state

        def paused_first_write(state: dict) -> None:
            first_write_started.set()
            if not allow_first_write.wait(timeout=5):
                raise TimeoutError("test did not release the first operator-user write")
            original_first_write(state)

        self.service._write_state = paused_first_write
        first_time = datetime(2090, 1, 1, 12, 0, tzinfo=UTC)
        reversed_second_time = datetime(2089, 1, 1, 12, 0, tzinfo=UTC)

        def fake_utc_now() -> datetime:
            if threading.current_thread().name == "operator-revision-second":
                second_clock_called.set()
                return reversed_second_time
            return first_time

        def save(service: OperatorAuthService, key: str) -> None:
            if key == "second":
                second_started.set()
            try:
                results[key] = service.save_user(
                    {
                        "_operator_session": self.admin_session,
                        "username": "ADMIN",
                        "role": "admin",
                    }
                )
            except BaseException as exc:
                errors.append(exc)

        with (
            patch("minimal_kanban.operator_auth.utc_now", side_effect=fake_utc_now),
            patch(
                "minimal_kanban.operator_auth.utc_now_iso",
                side_effect=lambda: fake_utc_now().isoformat(),
            ),
        ):
            first_thread = threading.Thread(
                target=save,
                args=(self.service, "first"),
                name="operator-revision-first",
            )
            second_thread = threading.Thread(
                target=save,
                args=(second, "second"),
                name="operator-revision-second",
            )
            first_thread.start()
            self.assertTrue(first_write_started.wait(timeout=5))
            second_thread.start()
            self.assertTrue(second_started.wait(timeout=5))
            try:
                self.assertFalse(second_clock_called.wait(timeout=0.5))
            finally:
                allow_first_write.set()
            first_thread.join(timeout=5)
            second_thread.join(timeout=5)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(errors, [])
        first_revision = results["first"]["user"]["updated_at"]
        second_revision = results["second"]["user"]["updated_at"]
        self.assertLess(first_revision, second_revision)
        self.assertGreater(
            datetime.fromisoformat(second_revision),
            datetime.fromisoformat(first_revision),
        )
        persisted = json.loads(self.users_file.read_text(encoding="utf-8"))
        admin = next(user for user in persisted["users"] if user["username"] == "ADMIN")
        self.assertEqual(admin["updated_at"], second_revision)

    def test_backward_clock_keeps_all_user_summary_mutations_monotonic(self) -> None:
        with patch(
            "minimal_kanban.operator_auth._password_hash",
            return_value="pbkdf2_sha256$1$test$00",
        ):
            created = self.service.save_user(
                {
                    "_operator_session": self.admin_session,
                    "username": "WORKER",
                    "password": "safe-password",
                }
            )
        created_revision = created["user"]["updated_at"]
        reversed_time = datetime(2001, 1, 1, tzinfo=UTC)

        with (
            patch("minimal_kanban.operator_auth.utc_now", return_value=reversed_time),
            patch(
                "minimal_kanban.operator_auth.utc_now_iso",
                return_value=reversed_time.isoformat(),
            ),
        ):
            bound = self.service.set_user_employee(
                {
                    "_operator_session": self.admin_session,
                    "username": "WORKER",
                    "employee_id": "",
                }
            )
            self.service._record_user_action(
                "WORKER",
                action="card_opened",
                message="Открыл карточку.",
                counter_key="cards_opened",
            )

        bound_revision = bound["user"]["updated_at"]
        action_revision = self._user("WORKER")["updated_at"]
        self.assertLess(created_revision, bound_revision)
        self.assertLess(bound_revision, action_revision)

        with self.service._locked_state() as state:
            admin = next(user for user in state["users"] if user["username"] == "ADMIN")
            admin["password_hash"] = "legacy-admin-password"
            previous_admin_revision = admin["updated_at"]
            self.service._write_state(state)

        def verify_password(password: str, password_hash: str) -> bool:
            return password_hash == "legacy-admin-password" and password == "admin123"

        with (
            patch("minimal_kanban.operator_auth.utc_now", return_value=reversed_time),
            patch(
                "minimal_kanban.operator_auth.utc_now_iso",
                return_value=reversed_time.isoformat(),
            ),
            patch("minimal_kanban.operator_auth._verify_password", side_effect=verify_password),
            patch(
                "minimal_kanban.operator_auth._password_hash",
                return_value="pbkdf2_sha256$1$upgraded$00",
            ),
        ):
            logged_in = self.service.login({"username": "admin", "password": "admin"})

        self.assertLess(previous_admin_revision, logged_in["user"]["updated_at"])

    def test_normalized_user_revision_is_canonical_utc_for_lexical_comparison(self) -> None:
        users = self.service._normalize_users(
            [
                {
                    "username": "OFFSET",
                    "password_hash": "pbkdf2_sha256$1$test$00",
                    "role": "operator",
                    "created_at": "2030-01-01T23:00:00+14:00",
                    "updated_at": "2030-01-01T23:00:00+14:00",
                }
            ]
        )

        self.assertEqual(users[0]["updated_at"], "2030-01-01T09:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
