from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.repair_order import REPAIR_ORDER_ROWS_LIMIT, normalize_repair_order_rows


class RepairOrderRowsTests(unittest.TestCase):
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
