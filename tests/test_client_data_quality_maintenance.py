from __future__ import annotations

import importlib.util
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPT_PATH = ROOT / "scripts" / "client_data_quality_maintenance.py"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


def load_client_data_quality_module():
    spec = importlib.util.spec_from_file_location("client_data_quality_maintenance", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("client_data_quality_maintenance.py is importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ClientDataQualityMaintenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.logger = logging.getLogger(f"test.client_data_quality.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(state_file=self.state_file, logger=self.logger)
        self.service = CardService(self.store, self.logger)
        self.module = load_client_data_quality_module()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_dry_run_reports_placeholder_vehicle_vins_without_mutating_state(self) -> None:
        self.service.create_client(
            {
                "client_id": "placeholder-vins",
                "display_name": "Клиент с мусорными VIN",
                "vehicles": [
                    {"vehicle": "Toyota", "vin": "1111111111111"},
                    {"vehicle": "Honda", "vin": "-"},
                    {"vehicle": "Nissan", "vin": "UNKNOWN"},
                    {"vehicle": "Mazda", "vin": "NCP165-0033993"},
                ],
            }
        )
        before_state = json.loads(self.state_file.read_text(encoding="utf-8"))

        plan = self.module.build_client_data_quality_plan(self.state_file)
        after_state = json.loads(self.state_file.read_text(encoding="utf-8"))

        self.assertTrue(plan["read_only"])
        self.assertEqual(plan["summary"]["invalid_vehicle_vins"], 3)
        self.assertEqual(plan["summary"]["safe_fixes_available"], 3)
        self.assertEqual(
            {operation["reason"] for operation in plan["operations"]},
            {"repeated_character", "empty_compact", "placeholder"},
        )
        self.assertEqual(before_state, after_state)

    def test_apply_clears_only_invalid_vehicle_vins_and_creates_backup(self) -> None:
        self.service.create_client(
            {
                "client_id": "cleanup-client",
                "display_name": "Клиент для очистки VIN",
                "vehicles": [
                    {"vehicle": "Toyota", "vin": "1"},
                    {"vehicle": "Mazda", "vin": "NCP165-0033993"},
                ],
            }
        )

        result = self.module.apply_client_data_quality_plan(self.state_file, backup=True)
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        vehicles = state["clients"][0]["vehicles"]

        self.assertFalse(result["read_only"])
        self.assertTrue(result["applied"])
        self.assertTrue(Path(result["backup_file"]).exists())
        self.assertEqual(result["summary"]["applied_fixes"], 1)
        self.assertEqual(vehicles[0]["vin"], "")
        self.assertEqual(vehicles[1]["vin"], "NCP165-0033993")
        self.assertTrue(
            any(
                event.get("action") == "client_vehicle_vin_placeholders_cleared"
                for event in state["events"]
            )
        )

    def test_apply_requires_backup(self) -> None:
        with self.assertRaisesRegex(ValueError, "backup"):
            self.module.apply_client_data_quality_plan(self.state_file, backup=False)


if __name__ == "__main__":
    unittest.main()
