from __future__ import annotations

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

from minimal_kanban.services import snapshot_service as snapshot_service_module  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.snapshot_cache import SNAPSHOT_CACHE_MAX_ENTRIES  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class SnapshotCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.state_file = self.base_dir / "state.json"
        self.logger = logging.getLogger(f"test.snapshot_cache.{self._testMethodName}")
        self.logger.handlers.clear()
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

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

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
        self.assertNotIn("viewer-0", cached_viewers)
        self.assertIn(f"viewer-{SNAPSHOT_CACHE_MAX_ENTRIES + 6}", cached_viewers)


if __name__ == "__main__":
    unittest.main()
