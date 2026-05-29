from __future__ import annotations

import importlib.util
import logging
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPT_PATH = ROOT / "scripts" / "client_duplicates_maintenance.py"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


def load_client_duplicates_module():
    spec = importlib.util.spec_from_file_location("client_duplicates_maintenance", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("client_duplicates_maintenance.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ClientDuplicatesMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.logger = logging.getLogger(f"test.client_duplicates.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(state_file=self.state_file, logger=self.logger)
        self.service = CardService(self.store, self.logger)
        self.module = load_client_duplicates_module()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_dry_run_reports_exact_phone_name_duplicates(self) -> None:
        first = self.service.create_client(
            {
                "display_name": "Точный дубль",
                "phone": "8 983 154-66-68",
                "vehicles": [{"vehicle": "Kia Spectra", "license_plate": "Т896ТЕ124"}],
            }
        )["client"]
        second = self.service.create_client(
            {
                "client_id": "explicit-duplicate",
                "display_name": "Точный дубль",
                "phone": "+7 983 154-66-68",
                "vehicles": [{"vehicle": "Kia Spectra", "license_plate": "т896те124"}],
            }
        )["client"]
        self.service.create_client(
            {"display_name": "Другой клиент", "phone": "+7 983 154-66-68"}
        )

        plan = self.module.build_client_duplicate_plan(self.state_file)

        self.assertTrue(plan["read_only"])
        self.assertEqual(plan["summary"]["groups_total"], 1)
        group = plan["groups"][0]
        self.assertEqual({group["canonical_id"], *group["duplicate_ids"]}, {first["id"], second["id"]})
        self.assertEqual(group["phone_key"], "79831546668")

    def test_apply_merges_duplicate_clients_relinks_cards_and_creates_backup(self) -> None:
        first = self.service.create_client(
            {
                "display_name": "Клиент для merge",
                "phone": "8 923 378-61-81",
                "vehicles": [{"vehicle": "Nissan Murano", "license_plate": "Х660ТЕ"}],
            }
        )["client"]
        second = self.service.create_client(
            {
                "client_id": "explicit-duplicate",
                "display_name": "Клиент для merge",
                "phone": "+7 923 378-61-81",
                "vehicles": [{"vehicle": "Nissan Murano", "license_plate": "х660те"}],
            }
        )["client"]
        duplicate_card = self.service.create_card(
            {
                "title": "Связанная карточка дубля",
                "vehicle": "Nissan Murano",
                "deadline": {"hours": 2},
            }
        )["card"]
        canonical_card_one = self.service.create_card(
            {
                "title": "Первая карточка канонического клиента",
                "vehicle": "Nissan Murano",
                "deadline": {"hours": 2},
            }
        )["card"]
        canonical_card_two = self.service.create_card(
            {
                "title": "Вторая карточка канонического клиента",
                "vehicle": "Nissan Murano",
                "deadline": {"hours": 2},
            }
        )["card"]
        self.service.link_card_to_client({"card_id": duplicate_card["id"], "client_id": first["id"]})
        self.service.link_card_to_client(
            {"card_id": canonical_card_one["id"], "client_id": second["id"]}
        )
        self.service.link_card_to_client(
            {"card_id": canonical_card_two["id"], "client_id": second["id"]}
        )

        result = self.module.apply_client_duplicate_plan(self.state_file, backup=True)
        bundle = self.store.read_bundle()
        clients = {client.id: client for client in bundle["clients"]}
        cards = {stored_card.id: stored_card for stored_card in bundle["cards"]}

        self.assertFalse(result["read_only"])
        self.assertTrue(result["applied"])
        self.assertTrue(Path(result["backup_file"]).exists())
        self.assertEqual(result["summary"]["clients_removed"], 1)
        self.assertEqual(result["summary"]["cards_relinked"], 1)
        self.assertEqual(len(clients), 1)
        self.assertIn(second["id"], clients)
        self.assertNotIn(first["id"], clients)
        self.assertEqual(cards[duplicate_card["id"]].client_id, second["id"])
        self.assertTrue(
            any(event.action == "client_duplicates_merged" for event in bundle["events"])
        )

    def test_apply_requires_backup(self) -> None:
        with self.assertRaisesRegex(ValueError, "backup"):
            self.module.apply_client_duplicate_plan(self.state_file, backup=False)


if __name__ == "__main__":
    unittest.main()
