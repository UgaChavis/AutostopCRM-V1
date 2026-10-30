from __future__ import annotations

# The fixture import sets up the source path.
# ruff: noqa: I001
from tests.services_case import CardServiceCase
from minimal_kanban.services.errors import ServiceError


class RepairOrderRowRevisionTests(CardServiceCase):
    def test_replace_rows_rejects_stale_revision_without_writing(self) -> None:
        for field, operation in (
            ("works", self.service.replace_repair_order_works),
            ("materials", self.service.replace_repair_order_materials),
        ):
            with self.subTest(field=field):
                card = self.service.create_card({"title": f"Revision {field}"})["card"]
                initial_revision = card["updated_at"]
                updated = self.service.update_repair_order(
                    {
                        "card_id": card["id"],
                        "repair_order": {
                            field: [{"name": "Newer", "quantity": "1", "price": "200"}]
                        },
                        "expected_updated_at": initial_revision,
                    }
                )
                current_revision = updated["card"]["updated_at"]
                self.assertNotEqual(initial_revision, current_revision)
                before = self.state_file.read_bytes()
                with self.assertRaises(ServiceError) as error:
                    operation(
                        {
                            "card_id": card["id"],
                            "rows": [{"name": "Stale", "quantity": "1", "price": "100"}],
                            "expected_updated_at": initial_revision,
                        }
                    )
                self.assertEqual(error.exception.code, "card_update_conflict")
                self.assertEqual(error.exception.status_code, 409)
                self.assertEqual(self.state_file.read_bytes(), before)
                self.assertEqual(
                    self.service.get_repair_order({"card_id": card["id"]})["repair_order"][field][
                        0
                    ]["name"],
                    "Newer",
                )

                accepted = operation(
                    {
                        "card_id": card["id"],
                        "rows": [{"name": "Current", "quantity": "1", "price": "300"}],
                        "expected_updated_at": current_revision,
                    }
                )
                self.assertEqual(accepted["repair_order"][field][0]["name"], "Current")
                legacy = operation(
                    {
                        "card_id": card["id"],
                        "rows": [{"name": "Legacy", "quantity": "1", "price": "400"}],
                    }
                )
                self.assertEqual(legacy["repair_order"][field][0]["name"], "Legacy")
