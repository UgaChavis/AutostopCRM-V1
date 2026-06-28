from __future__ import annotations

import importlib.util
import json
import logging
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

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
        self.service.create_client({"display_name": "Другой клиент", "phone": "+7 983 154-66-68"})

        plan = self.module.build_client_duplicate_plan(self.state_file)

        self.assertTrue(plan["read_only"])
        self.assertEqual(plan["summary"]["groups_total"], 1)
        group = plan["groups"][0]
        self.assertEqual(
            {group["canonical_id"], *group["duplicate_ids"]}, {first["id"], second["id"]}
        )
        self.assertEqual(group["phone_key"], "79831546668")

    def test_dry_run_reports_phone_only_name_duplicate_with_real_client_phone(self) -> None:
        phone_only = self.service.create_client(
            {
                "display_name": "89504235457",
                "vehicles": [{"vin": "JTMBH31V905017850"}],
            }
        )["client"]
        named = self.service.create_client(
            {
                "display_name": "Анцифиров Вячеслав Геннадьевич",
                "phone": "8 950 423-54-57",
                "vehicles": [
                    {
                        "vehicle": "Toyota Rav 4 2006",
                        "vin": "JTMBH31V905017850",
                        "license_plate": "н104кт124",
                    }
                ],
            }
        )["client"]

        plan = self.module.build_client_duplicate_plan(self.state_file)

        self.assertEqual(plan["summary"]["groups_total"], 1)
        group = plan["groups"][0]
        self.assertEqual(group["phone_key"], "79504235457")
        self.assertEqual(group["name_key"], "phone-only-name:79504235457")
        self.assertEqual(group["canonical_id"], named["id"])
        self.assertEqual(group["duplicate_ids"], [phone_only["id"]])
        self.assertEqual(group["vehicles_to_merge"], 0)

    def test_apply_phone_only_name_duplicate_keeps_richer_existing_vehicle(self) -> None:
        phone_only = self.service.create_client(
            {
                "display_name": "89504235457",
                "vehicles": [{"vin": "JTMBH31V905017850"}],
            }
        )["client"]
        named = self.service.create_client(
            {
                "display_name": "Анцифиров Вячеслав Геннадьевич",
                "phone": "8 950 423-54-57",
                "vehicles": [
                    {
                        "vehicle": "Toyota Rav 4 2006",
                        "vin": "JTMBH31V905017850",
                        "license_plate": "н104кт124",
                    }
                ],
            }
        )["client"]

        result = self.module.apply_client_duplicate_plan(self.state_file, backup=True)
        bundle = self.store.read_bundle()
        clients = {client.id: client for client in bundle["clients"]}

        self.assertTrue(result["applied"])
        self.assertIn(named["id"], clients)
        self.assertNotIn(phone_only["id"], clients)
        self.assertEqual(len(clients[named["id"]].vehicles), 1)
        self.assertEqual(clients[named["id"]].vehicles[0].vehicle, "Toyota Rav 4 2006")
        self.assertEqual(clients[named["id"]].vehicles[0].vin, "JTMBH31V905017850")

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
        self.service.link_card_to_client(
            {"card_id": duplicate_card["id"], "client_id": first["id"]}
        )
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

    def test_apply_backup_does_not_overwrite_existing_backup(self) -> None:
        self.service.create_client(
            {
                "display_name": "Клиент для merge",
                "phone": "8 923 378-61-81",
                "vehicles": [{"vehicle": "Nissan Murano", "license_plate": "Х660ТЕ"}],
            }
        )
        self.service.create_client(
            {
                "client_id": "explicit-duplicate",
                "display_name": "Клиент для merge",
                "phone": "+7 923 378-61-81",
                "vehicles": [{"vehicle": "Nissan Murano", "license_plate": "х660те"}],
            }
        )
        existing_backup = self.state_file.with_name(
            "state.json.backup-client-duplicates-20260601T010203Z"
        )
        existing_backup.write_text("previous backup", encoding="utf-8")
        fixed_now = datetime(2026, 6, 1, 1, 2, 3, tzinfo=UTC)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

        with patch.object(self.module, "datetime", FixedDateTime):
            result = self.module.apply_client_duplicate_plan(self.state_file, backup=True)

        backup_file = self.state_file.with_name(
            "state.json.backup-client-duplicates-20260601T010203Z-002"
        )
        self.assertEqual(existing_backup.read_text(encoding="utf-8"), "previous backup")
        self.assertEqual(Path(result["backup_file"]), backup_file)
        self.assertTrue(backup_file.exists())

    def test_apply_requires_backup(self) -> None:
        with self.assertRaisesRegex(ValueError, "backup"):
            self.module.apply_client_duplicate_plan(self.state_file, backup=False)

    def test_dry_run_invalid_json_does_not_mutate_state_file(self) -> None:
        self.state_file.write_text("{broken", encoding="utf-8")

        with self.assertRaises(json.JSONDecodeError):
            self.module.build_client_duplicate_plan(self.state_file)

        self.assertEqual(self.state_file.read_text(encoding="utf-8"), "{broken")
        self.assertEqual(list(self.state_file.parent.glob("state.corrupted*.json")), [])

    def test_dry_run_oversized_state_does_not_mutate_state_file(self) -> None:
        self.state_file.write_text("x" * 16, encoding="utf-8")

        with patch.object(self.module, "STATE_FILE_MAX_BYTES", 8):
            with self.assertRaisesRegex(ValueError, "client duplicates state file is too large"):
                self.module.build_client_duplicate_plan(self.state_file)

        self.assertEqual(self.state_file.read_text(encoding="utf-8"), "x" * 16)
        self.assertEqual(list(self.state_file.parent.glob("state.corrupted*.json")), [])

    def test_state_model_loaders_skip_overflow_records(self) -> None:
        with patch.object(self.module.ClientProfile, "from_dict", side_effect=OverflowError):
            self.assertEqual(self.module._clients_from_state({"clients": [{"id": "bad"}]}), [])

        with patch.object(self.module.Card, "from_dict", side_effect=OverflowError):
            self.assertEqual(self.module._cards_from_state({"cards": [{"id": "bad"}]}), [])

    def test_json_dumps_sanitizes_nonfinite_values(self) -> None:
        encoded = self.module._json_dumps({"ok": True, "value": float("nan"), "ratio": 1.25})

        self.assertNotIn("NaN", encoded)
        self.assertEqual(json.loads(encoded), {"ok": True, "value": None, "ratio": 1.25})

    def test_json_dumps_handles_self_referential_payload(self) -> None:
        payload: dict[str, object] = {"ok": True}
        payload["self"] = payload

        encoded = self.module._json_dumps(payload)
        decoded = json.loads(encoded)
        node = decoded
        for _ in range(8):
            node = node["self"]

        self.assertIsInstance(node, str)

    def test_main_reports_json_error_without_traceback(self) -> None:
        output = StringIO()

        with (
            patch.object(
                self.module,
                "build_client_duplicate_plan",
                side_effect=ValueError("bad state"),
            ),
            redirect_stdout(output),
        ):
            exit_code = self.module.main(["--state-file", str(self.state_file), "--format", "json"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertFalse(payload["ok"])
        self.assertIn("bad state", payload["error"])


if __name__ == "__main__":
    unittest.main()
