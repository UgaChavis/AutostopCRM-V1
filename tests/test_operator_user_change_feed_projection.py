from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

if __package__:
    from tests.source_path_support import ensure_source_path
else:
    from source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.storage.change_feed_projection import (  # noqa: E402
    ProjectedEntity,
    project_operator_users,
)
from minimal_kanban.storage.change_feed_store import ChangeFeedStore  # noqa: E402


class OperatorUserChangeFeedProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.feed = ChangeFeedStore(Path(temporary.name) / "change_feed.sqlite3")
        self.feed.initialize_baseline([])

    @staticmethod
    def user(**overrides: object) -> dict[str, object]:
        user: dict[str, object] = {
            "username": "projection-operator",
            "password_hash": "scrypt$legacy-private-hash",
            "role": "operator",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "employee_id": "",
            "permissions": [],
            "stats": {"cards_opened": 0},
            "action_history": [],
            "board_preferences": {"parts_store_column": {"is_open": False}},
        }
        user.update(overrides)
        return user

    @staticmethod
    def projection(user: dict[str, object]):
        return project_operator_users({"users": [user], "sessions": []})

    def events(self) -> list[dict]:
        return self.feed.raw_events_for_test()

    def test_projection_version_change_silently_rebaselines_external_state(self) -> None:
        legacy_key = ("operator_user", "operator-legacy")
        legacy = {
            legacy_key: ProjectedEntity(
                *legacy_key,
                "legacy-private-field-digest",
                "legacy-role-digest",
                "active",
            )
        }
        current = self.projection(self.user())
        self.feed.initialize_external_projection("operator_users", legacy, projection_version="1")

        unrelated_key = ("shared_file", "unrelated-file")
        self.feed.initialize_external_projection("unrelated", {}, projection_version="1")
        self.feed.reconcile_external_projection(
            "unrelated",
            {unrelated_key: ProjectedEntity(*unrelated_key, "digest", "routing", "active")},
        )
        before = self.events()
        before_high_water = before[-1]["sequence"]

        self.feed.initialize_external_projection("operator_users", current, projection_version="2")

        self.assertEqual(before, self.events())
        self.assertEqual(before_high_water, self.events()[-1]["sequence"])
        reconciled = self.feed.reconcile_external_projection("operator_users", current)
        self.assertEqual(0, reconciled["published"])
        self.assertEqual(before, self.events())

    def test_private_and_runtime_fields_do_not_change_operator_projection(self) -> None:
        baseline_user = self.user()
        baseline = self.projection(baseline_user)
        variants = {
            "password_hash": "scrypt$rotated-private-hash",
            "stats": {"cards_opened": 97, "cards_archived": 12},
            "action_history": [
                {
                    "action": "card_opened",
                    "timestamp": "2026-02-01T00:00:00+00:00",
                }
            ],
            "board_preferences": {"parts_store_column": {"is_open": True}},
            "future_runtime_field": {"ephemeral": True},
        }

        for field, value in variants.items():
            with self.subTest(field=field):
                candidate = deepcopy(baseline_user)
                candidate[field] = value
                self.assertEqual(baseline, self.projection(candidate))

    def test_business_account_fields_and_create_delete_still_publish_events(self) -> None:
        current_user = self.user()
        self.feed.initialize_external_projection(
            "operator_users", self.projection(current_user), projection_version="2"
        )

        changes = (
            ("role", "admin", "move", False),
            ("permissions", ["cards.edit"], "update", False),
            ("employee_id", "employee-technical-id", "update", False),
        )
        for field, value, expected_change_type, expected_tombstone in changes:
            with self.subTest(field=field):
                before_sequence = self.events()[-1]["sequence"] if self.events() else 0
                current_user[field] = value
                result = self.feed.reconcile_external_projection(
                    "operator_users", self.projection(current_user)
                )
                events = [event for event in self.events() if event["sequence"] > before_sequence]
                self.assertEqual(1, result["published"])
                self.assertEqual(1, len(events), events)
                self.assertEqual("operator_users", events[0]["producer"])
                self.assertEqual("operator_user", events[0]["entity_type"])
                self.assertEqual(expected_change_type, events[0]["change_type"])
                self.assertIs(expected_tombstone, events[0]["tombstone"])

        entity_id = next(iter(self.projection(current_user)))[1]
        before_sequence = self.events()[-1]["sequence"]
        deleted = self.feed.reconcile_external_projection("operator_users", {})
        delete_events = [event for event in self.events() if event["sequence"] > before_sequence]
        self.assertEqual(1, deleted["published"])
        self.assertEqual(1, len(delete_events), delete_events)
        self.assertEqual("operator_users", delete_events[0]["producer"])
        self.assertEqual("operator_user", delete_events[0]["entity_type"])
        self.assertEqual(entity_id, delete_events[0]["entity_id"])
        self.assertEqual("delete", delete_events[0]["change_type"])
        self.assertIs(True, delete_events[0]["tombstone"])

        before_sequence = delete_events[0]["sequence"]
        created = self.feed.reconcile_external_projection(
            "operator_users", self.projection(current_user)
        )
        create_events = [event for event in self.events() if event["sequence"] > before_sequence]
        self.assertEqual(1, created["published"])
        self.assertEqual(1, len(create_events), create_events)
        self.assertEqual("operator_users", create_events[0]["producer"])
        self.assertEqual("operator_user", create_events[0]["entity_type"])
        self.assertEqual(entity_id, create_events[0]["entity_id"])
        self.assertEqual("create", create_events[0]["change_type"])
        self.assertIs(False, create_events[0]["tombstone"])


if __name__ == "__main__":
    unittest.main()
