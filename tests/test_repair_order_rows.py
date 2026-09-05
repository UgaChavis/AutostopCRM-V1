from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.repair_order import REPAIR_ORDER_ROWS_LIMIT, normalize_repair_order_rows
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import JsonStore


class RepairOrderRowsTests(unittest.TestCase):
    def test_replacement_preserves_other_rows_and_rejects_invalid_input_without_writes(
        self,
    ) -> None:
        for field in ("works", "materials"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                logger = logging.getLogger(__name__)
                state_file = Path(temp_dir) / "state.json"
                service = CardService(JsonStore(state_file, logger), logger)
                card_id = service.create_card({"vehicle": "KIA RIO", "title": "Ремонт"})["card"][
                    "id"
                ]
                other_field = "materials" if field == "works" else "works"
                service.update_repair_order(
                    {
                        "card_id": card_id,
                        "repair_order": {other_field: [{"name": "Сохранить", "price": "100"}]},
                    }
                )
                replace = getattr(service, f"replace_repair_order_{field}")
                rows = [{"name": "Замена", "quantity": "2", "price": "150"}]
                result = replace({"card_id": card_id, "rows": rows})
                self.assertTrue(result["meta"]["changed"])
                self.assertEqual(result["meta"]["rows"], 1)
                self.assertEqual(result["repair_order"][field][0]["name"], "Замена")
                self.assertEqual(result["repair_order"][other_field][0]["name"], "Сохранить")
                stored = state_file.read_bytes()
                self.assertFalse(
                    replace({"card_id": card_id, "rows": result["repair_order"][field]})["meta"][
                        "changed"
                    ]
                )
                self.assertEqual(state_file.read_bytes(), stored)
                with self.assertRaises(ServiceError) as raised:
                    replace({"card_id": card_id, "rows": "invalid"})
                self.assertEqual(raised.exception.code, "validation_error")
                self.assertEqual(raised.exception.details["field"], "rows")
                self.assertEqual(state_file.read_bytes(), stored)

    def test_normalize_repair_order_rows_keeps_up_to_150_rows(self) -> None:
        payload = [
            {"name": f"Позиция {index}", "quantity": "1", "price": "10"}
            for index in range(1, REPAIR_ORDER_ROWS_LIMIT + 2)
        ]

        rows = normalize_repair_order_rows(payload)

        self.assertEqual(REPAIR_ORDER_ROWS_LIMIT, 150)
        self.assertEqual(len(rows), 150)
        self.assertEqual(rows[0].name, "Позиция 1")
        self.assertEqual(rows[-1].name, "Позиция 150")


if __name__ == "__main__":
    unittest.main()
