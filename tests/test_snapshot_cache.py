from __future__ import annotations

import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.logging_setup import close_logger  # noqa: E402
from minimal_kanban.services import snapshot_service as snapshot_service_module  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.snapshot_cache import (  # noqa: E402
    SNAPSHOT_CACHE_MAX_ENTRIES,
    PreparedSnapshotData,
    SnapshotResponseCache,
)
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class SnapshotCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base_dir = Path(self.temp_dir.name)
        self.state_file = self.base_dir / "state.json"
        self.logger = logging.getLogger(f"test.snapshot_cache.{self._testMethodName}")
        close_logger(self.logger)
        self.addCleanup(close_logger, self.logger)
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(state_file=self.state_file, logger=self.logger)
        self.service = CardService(
            self.store,
            self.logger,
            attachments_dir=self.base_dir / "attachments",
            repair_orders_dir=self.base_dir / "repair-orders",
        )
        self.snapshot_service = self.service._snapshot_service

    @staticmethod
    def _compact_view(*, actor_name: str = "ALICE") -> dict[str, object]:
        return {
            "actor_name": actor_name,
            "compact": True,
            "include_archive": False,
        }

    def _create_card(self, *, title: str = "Snapshot cache card") -> str:
        result = self.service.create_card(
            {
                "title": title,
                "deadline": {"hours": 2},
                "actor_name": "ALICE",
            }
        )
        return str(result["card"]["id"])

    def test_repeated_revision_uses_cached_revision(self) -> None:
        view = self._compact_view()

        with patch.object(
            self.snapshot_service,
            "_snapshot_revision",
            wraps=self.snapshot_service._snapshot_revision,
        ) as build_revision:
            first = self.service.get_board_revision(view)
            second = self.service.get_board_revision(view)

        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(build_revision.call_count, 1)

    def test_cold_snapshot_counts_events_once_and_matches_revision_only_read(self) -> None:
        for state in ("empty", "active", "archived"):
            if state == "active":
                card_id = self._create_card()
            elif state == "archived":
                self.service.archive_card({"card_id": card_id, "actor_name": "ALICE"})
            for compact in (False, True):
                for include_archive in (False, True):
                    with self.subTest(
                        state=state, compact=compact, include_archive=include_archive
                    ):
                        view = {
                            "actor_name": "ALICE",
                            "compact": compact,
                            "include_archive": include_archive,
                        }
                        self.snapshot_service._snapshot_cache = SnapshotResponseCache()
                        with patch.object(
                            snapshot_service_module,
                            "_event_counts",
                            wraps=snapshot_service_module._event_counts,
                        ) as count_events:
                            snapshot = self.service.get_board_snapshot(view)
                        expected_passes = int(bool(snapshot["cards"] or snapshot["archive"]))
                        self.assertEqual(count_events.call_count, expected_passes)
                        self.snapshot_service._snapshot_cache = SnapshotResponseCache()
                        revision = self.service.get_board_revision(view)
                        self.assertEqual(snapshot["meta"]["revision"], revision["revision"])

    def test_repeated_revision_skips_board_projection_on_cache_hit(self) -> None:
        view = {
            "actor_name": "ALICE",
            "compact": True,
            "include_archive": True,
            "archive_limit": 10,
        }

        with (
            patch.object(
                self.snapshot_service,
                "_visible_cards",
                wraps=self.snapshot_service._visible_cards,
            ) as visible_cards,
            patch.object(
                self.snapshot_service,
                "_archived_cards",
                wraps=self.snapshot_service._archived_cards,
            ) as archived_cards,
            patch.object(
                self.snapshot_service,
                "_stickies",
                wraps=self.snapshot_service._stickies,
            ) as stickies,
        ):
            first = self.service.get_board_revision(view)
            second = self.service.get_board_revision(view)

        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(visible_cards.call_count, 1)
        self.assertEqual(archived_cards.call_count, 1)
        self.assertEqual(stickies.call_count, 1)

    def test_external_store_write_changes_signature_and_invalidates_revision(self) -> None:
        view = self._compact_view()
        _, before_signature = self.store.read_bundle_with_signature()
        external_store = JsonStore(state_file=self.state_file, logger=self.logger)
        external_service = CardService(
            external_store,
            self.logger,
            attachments_dir=self.base_dir / "external-attachments",
            repair_orders_dir=self.base_dir / "external-repair-orders",
        )

        with patch.object(
            self.snapshot_service,
            "_snapshot_revision",
            wraps=self.snapshot_service._snapshot_revision,
        ) as build_revision:
            before = self.service.get_board_revision(view)
            external_service.create_card(
                {
                    "title": "Written by another store instance",
                    "deadline": {"hours": 2},
                    "actor_name": "BOB",
                }
            )
            after = self.service.get_board_revision(view)

        _, after_signature = self.store.read_bundle_with_signature()
        self.assertNotEqual(after_signature, before_signature)
        self.assertNotEqual(after["revision"], before["revision"])
        self.assertEqual(after["counts"]["cards"], 1)
        self.assertEqual(build_revision.call_count, 2)

    def test_viewer_and_snapshot_options_have_separate_cache_entries(self) -> None:
        views = [
            self._compact_view(actor_name="ALICE"),
            self._compact_view(actor_name="BOB"),
            {"actor_name": "ALICE", "compact": False, "include_archive": False},
            {
                "actor_name": "ALICE",
                "compact": True,
                "include_archive": True,
                "archive_limit": 1,
            },
            {
                "actor_name": "ALICE",
                "compact": True,
                "include_archive": True,
                "archive_limit": 2,
            },
        ]

        with patch.object(
            self.snapshot_service,
            "_snapshot_revision",
            wraps=self.snapshot_service._snapshot_revision,
        ) as build_revision:
            first_results = [self.service.get_board_revision(view) for view in views]
            second_results = [self.service.get_board_revision(view) for view in views]

        self.assertEqual(build_revision.call_count, len(views))
        self.assertEqual(
            [item["revision"] for item in first_results],
            [item["revision"] for item in second_results],
        )
        self.assertEqual(len({item["revision"] for item in first_results}), len(views))

    def test_cache_preserves_case_sensitive_viewer_seen_state_and_revision(self) -> None:
        self.service.create_card({"title": "Viewer state", "actor_name": "Alice", "source": "ui"})
        alice = self._compact_view(actor_name="Alice")
        upper = self._compact_view(actor_name="ALICE")
        self.snapshot_service._snapshot_cache = SnapshotResponseCache()
        cold_upper = self.service.get_board_snapshot(upper)
        cold_upper_revision = self.service.get_board_revision(upper)["revision"]
        self.snapshot_service._snapshot_cache = SnapshotResponseCache()
        first = self.service.get_board_snapshot(alice)
        warmed_upper = self.service.get_board_snapshot(upper)
        warmed_upper_revision = self.service.get_board_revision(upper)["revision"]

        self.assertFalse(first["cards"][0]["is_unread"])
        self.assertTrue(cold_upper["cards"][0]["is_unread"])
        self.assertEqual(warmed_upper["cards"], cold_upper["cards"])
        self.assertEqual(warmed_upper["meta"]["revision"], cold_upper["meta"]["revision"])
        self.assertEqual(warmed_upper_revision, cold_upper_revision)
        self.assertNotEqual(first["meta"]["revision"], warmed_upper["meta"]["revision"])

        self.snapshot_service._snapshot_cache = SnapshotResponseCache()
        self.service.get_board_revision(alice)
        revision_after_other_viewer = self.service.get_board_revision(upper)["revision"]
        prepared = self.snapshot_service.get_board_snapshot_for_http(upper)
        self.assertIsInstance(prepared, PreparedSnapshotData)
        prepared_snapshot = json.loads(prepared.render(generated_at="2026-01-01T00:00:00+00:00"))
        self.assertTrue(prepared_snapshot["cards"][0]["is_unread"])
        self.assertEqual(revision_after_other_viewer, cold_upper_revision)
        self.assertEqual(prepared_snapshot["meta"]["revision"], cold_upper_revision)

    def test_cache_keeps_revision_for_distinct_viewer_spellings(self) -> None:
        views = [
            self._compact_view(actor_name="Alice  Smith"),
            self._compact_view(actor_name="Alice Smith"),
        ]
        self.snapshot_service._snapshot_cache = SnapshotResponseCache()
        cold_revision = self.service.get_board_revision(views[1])["revision"]
        self.snapshot_service._snapshot_cache = SnapshotResponseCache()
        first_revision = self.service.get_board_revision(views[0])["revision"]
        warmed_revision = self.service.get_board_revision(views[1])["revision"]
        self.assertNotEqual(first_revision, cold_revision)
        self.assertEqual(warmed_revision, cold_revision)

    def test_first_snapshot_settings_do_not_alias_store_cache(self) -> None:
        for compact in (False, True):
            with self.subTest(compact=compact):
                self.snapshot_service._snapshot_cache = SnapshotResponseCache()
                view = {"actor_name": "ALICE", "compact": compact}
                first = self.service.get_board_snapshot(view)
                original = first["settings"]["ai_board_control"]["enabled"]
                revision = first["meta"]["revision"]
                disk = self.state_file.read_bytes()
                first["settings"]["ai_board_control"]["enabled"] = not original

                self.snapshot_service._snapshot_cache = SnapshotResponseCache()
                repeated = self.service.get_board_snapshot(view)
                fresh_store = JsonStore(state_file=self.state_file, logger=self.logger)
                self.assertEqual(repeated["settings"]["ai_board_control"]["enabled"], original)
                self.assertEqual(
                    fresh_store.read_bundle()["settings"]["ai_board_control"]["enabled"], original
                )
                self.assertEqual(repeated["meta"]["revision"], revision)
                self.assertEqual(self.state_file.read_bytes(), disk)
                self.service.create_card({"title": "Unrelated write"})
                reread = JsonStore(state_file=self.state_file, logger=self.logger).read_bundle()
                self.assertEqual(reread["settings"]["ai_board_control"]["enabled"], original)

    def test_compact_snapshot_cache_does_not_expose_mutable_entry(self) -> None:
        card_id = self._create_card()
        view = self._compact_view()

        with patch.object(snapshot_service_module.time, "monotonic", return_value=100.0):
            first = self.service.get_board_snapshot(view)
            original_revision = first["meta"]["revision"]
            first["cards"][0]["title"] = "tampered"
            first["columns"].clear()
            first["settings"]["ai_board_control"]["enabled"] = True
            first["meta"]["revision"] = "tampered"

            second = self.service.get_board_snapshot(view)

        restored_card = next(card for card in second["cards"] if card["id"] == card_id)
        self.assertEqual(restored_card["title"], "Snapshot cache card")
        self.assertTrue(second["columns"])
        self.assertFalse(second["settings"]["ai_board_control"]["enabled"])
        self.assertEqual(second["meta"]["revision"], original_revision)

    def test_compact_snapshot_skips_board_projection_on_cache_hit(self) -> None:
        view = {
            "actor_name": "ALICE",
            "compact": True,
            "include_archive": True,
            "archive_limit": 10,
        }

        with (
            patch.object(
                self.snapshot_service,
                "_visible_cards",
                wraps=self.snapshot_service._visible_cards,
            ) as visible_cards,
            patch.object(
                self.snapshot_service,
                "_archived_cards",
                wraps=self.snapshot_service._archived_cards,
            ) as archived_cards,
            patch.object(
                self.snapshot_service,
                "_stickies",
                wraps=self.snapshot_service._stickies,
            ) as stickies,
            patch.object(snapshot_service_module.time, "monotonic", return_value=100.0),
        ):
            first = self.service.get_board_snapshot(view)
            second = self.service.get_board_snapshot(view)

        self.assertEqual(first["meta"]["revision"], second["meta"]["revision"])
        self.assertEqual(visible_cards.call_count, 1)
        self.assertEqual(archived_cards.call_count, 1)
        self.assertEqual(stickies.call_count, 1)

    def test_compact_http_snapshot_reuses_prepared_payload_and_preserves_public_contract(
        self,
    ) -> None:
        card_id = self._create_card()
        view = self._compact_view()

        with (
            patch.object(snapshot_service_module.time, "monotonic", return_value=100.0),
            patch.object(
                snapshot_service_module,
                "build_prepared_snapshot_data",
                wraps=snapshot_service_module.build_prepared_snapshot_data,
            ) as build_prepared,
        ):
            first = self.service.get_board_snapshot_for_http(view)
            second = self.service.get_board_snapshot_for_http(view)
            public = self.service.get_board_snapshot(view)

        self.assertIsInstance(first, PreparedSnapshotData)
        self.assertIs(first, second)
        self.assertEqual(build_prepared.call_count, 1)
        self.assertIsInstance(public, dict)
        self.assertEqual(
            json.loads(first.render(generated_at=public["meta"]["generated_at"])),
            public,
        )
        card = next(item for item in public["cards"] if item["id"] == card_id)
        self.assertEqual(card["title"], "Snapshot cache card")

    def test_compact_snapshot_cache_expires_after_monotonic_ttl(self) -> None:
        self._create_card()
        view = self._compact_view()

        with patch.object(
            self.snapshot_service,
            "_serialize_cards_payload",
            wraps=self.snapshot_service._serialize_cards_payload,
        ) as serialize_cards:
            with patch.object(snapshot_service_module.time, "monotonic", return_value=100.0):
                first = self.service.get_board_snapshot(view)
            with patch.object(snapshot_service_module.time, "monotonic", return_value=101.0):
                cached = self.service.get_board_snapshot(view)
            with patch.object(snapshot_service_module.time, "monotonic", return_value=102.0):
                refreshed = self.service.get_board_snapshot(view)

        self.assertEqual(first["meta"]["revision"], cached["meta"]["revision"])
        self.assertEqual(first["meta"]["revision"], refreshed["meta"]["revision"])
        self.assertEqual(serialize_cards.call_count, 4)

    def test_full_snapshot_payload_is_not_cached(self) -> None:
        self._create_card()
        view = {"actor_name": "ALICE", "compact": False, "include_archive": False}

        with (
            patch.object(
                self.snapshot_service,
                "_snapshot_revision",
                wraps=self.snapshot_service._snapshot_revision,
            ) as build_revision,
            patch.object(
                self.snapshot_service,
                "_serialize_cards_payload",
                wraps=self.snapshot_service._serialize_cards_payload,
            ) as serialize_cards,
        ):
            first = self.service.get_board_snapshot(view)
            second = self.service.get_board_snapshot(view)

        self.assertEqual(first["meta"]["revision"], second["meta"]["revision"])
        self.assertEqual(build_revision.call_count, 1)
        self.assertEqual(serialize_cards.call_count, 4)

    def test_snapshot_cache_is_bounded_by_lru_limit(self) -> None:
        for index in range(SNAPSHOT_CACHE_MAX_ENTRIES + 7):
            self.service.get_board_revision(self._compact_view(actor_name=f"VIEWER-{index}"))

        self.assertEqual(len(self.snapshot_service._snapshot_cache), SNAPSHOT_CACHE_MAX_ENTRIES)
        cached_viewers = {str(key[0]) for key in self.snapshot_service._snapshot_cache}
        self.assertNotIn("VIEWER-0", cached_viewers)
        self.assertIn(f"VIEWER-{SNAPSHOT_CACHE_MAX_ENTRIES + 6}", cached_viewers)


if __name__ == "__main__":
    unittest.main()
