from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.mcp.server import (  # noqa: E402
    RepairOrderPatchPayload,
    _resolved_create_card_deadline,
)


class McpRepairOrderPatchPayloadTests(unittest.TestCase):
    def test_create_card_deadline_resolver_defaults_invalid_parts(self) -> None:
        deadline = SimpleNamespace(
            model_dump=lambda: {
                "total_seconds": float("inf"),
                "days": True,
                "hours": "2.5",
                "minutes": "",
                "seconds": None,
            }
        )

        self.assertEqual(
            _resolved_create_card_deadline(deadline),
            {"days": 1, "hours": 0, "minutes": 0, "seconds": 0},
        )

    def test_create_card_deadline_resolver_clamps_large_finite_parts(self) -> None:
        deadline = SimpleNamespace(
            model_dump=lambda: {
                "total_seconds": 1e308,
                "days": 1e308,
                "hours": 1e308,
                "minutes": 1e308,
                "seconds": 1e308,
            }
        )

        self.assertEqual(
            _resolved_create_card_deadline(deadline),
            {
                "days": 365,
                "hours": 23,
                "minutes": 59,
                "seconds": 59,
                "total_seconds": 31_536_000,
            },
        )

    def test_repair_order_patch_payload_keeps_api_fields_and_common_aliases(self) -> None:
        payload = RepairOrderPatchPayload.model_validate(
            {
                "comment": "Комментарий для клиента",
                "clientInformation": "История для клиента",
                "master_comment": "Комментарий мастера",
                "internalComment": "Внутренняя заметка",
                "advancePayment": "500",
                "payment_history": [{"amount": "500", "payment_method": "cash"}],
                "licensePlate": "А123АА124",
                "odometer": "120000",
            }
        )

        self.assertEqual(
            payload.model_dump(exclude_none=True),
            {
                "licensePlate": "А123АА124",
                "odometer": "120000",
                "advancePayment": "500",
                "payment_history": [{"amount": "500", "payment_method": "cash"}],
                "comment": "Комментарий для клиента",
                "clientInformation": "История для клиента",
                "master_comment": "Комментарий мастера",
                "internalComment": "Внутренняя заметка",
            },
        )

    def test_repair_order_patch_schema_exposes_natural_manager_fields(self) -> None:
        properties = RepairOrderPatchPayload.model_json_schema()["properties"]

        for field_name in (
            "comment",
            "client_information",
            "clientInformation",
            "note",
            "master_comment",
            "masterComment",
            "internal_comment",
            "internalComment",
            "advance_payment",
            "advancePayment",
            "payment_history",
            "licensePlate",
            "odometer",
        ):
            self.assertIn(field_name, properties)


if __name__ == "__main__":
    unittest.main()
