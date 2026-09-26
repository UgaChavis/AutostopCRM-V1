"""Fixture-only service test base; intentionally contains no discoverable tests."""

from __future__ import annotations

# ruff: noqa: E402
import logging
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

_SOURCE_PATH = str(Path(__file__).resolve().parents[1] / "src")
sys.path[:] = [entry for entry in sys.path if entry != _SOURCE_PATH]
sys.path.insert(0, _SOURCE_PATH)

from minimal_kanban.logging_setup import close_logger
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


class CardServiceCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.logger = logging.getLogger(f"test.service.{self._testMethodName}")
        close_logger(self.logger)
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(state_file=self.state_file, logger=self.logger)
        self.service = CardService(self.store, self.logger)

    def _build_service(self) -> CardService:
        return CardService(
            self.store,
            self.logger,
            attachments_dir=Path(self.temp_dir.name) / "attachments",
            repair_orders_dir=Path(self.temp_dir.name) / "repair-orders",
        )

    def _patch_time(self, moment: datetime):
        return (
            patch("minimal_kanban.services.card_service.utc_now", return_value=moment),
            patch(
                "minimal_kanban.services.card_service.utc_now_iso", return_value=moment.isoformat()
            ),
            patch("minimal_kanban.models.utc_now", return_value=moment),
        )
