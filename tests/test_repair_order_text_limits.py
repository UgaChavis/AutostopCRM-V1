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

from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class RepairOrderTextLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        state_file = Path(self.temp_dir.name) / "state.json"
        logger = logging.getLogger(f"test.repair-order-text-limit.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.service = CardService(JsonStore(state_file=state_file, logger=logger), logger)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_restricted_json_and_context_reject_oversized_render(self) -> None:
        created = self.service.create_card(
            {"vehicle": "BMW X5", "title": "Заказ-наряд", "deadline": {"hours": 2}}
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000"}],
                },
            }
        )
        restricted_session = {
            "_operator_session": {
                "username": "restricted-user",
                "role": "operator",
                "permissions": [],
            }
        }

        for action, payload in (
            (
                self.service.get_repair_order_text,
                {"card_id": card_id, **restricted_session},
            ),
            (
                self.service.get_card_context,
                {
                    "card_id": card_id,
                    "include_repair_order_text": True,
                    **restricted_session,
                },
            ),
        ):
            with (
                self.subTest(action=action.__name__),
                patch("minimal_kanban.services.card_service.REPAIR_ORDER_TEXT_FILE_MAX_BYTES", 8),
                self.assertRaises(ServiceError) as raised,
            ):
                action(payload)

            self.assertEqual(raised.exception.code, "repair_order_text_too_large")
            self.assertEqual(raised.exception.status_code, 413)


if __name__ == "__main__":
    unittest.main()
