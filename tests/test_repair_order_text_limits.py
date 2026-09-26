from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

# The fixture import sets up the src path before importing the application module.
# ruff: noqa: I001
if __package__:
    from tests.services_case import CardServiceCase
else:
    from services_case import CardServiceCase

from minimal_kanban.services.errors import ServiceError


class RepairOrderTextLimitTests(CardServiceCase):
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

    def test_repair_order_text_file_name_sanitizes_windows_unsafe_characters(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "BMW X5",
                "title": "Диагностика: ограничение мощности / DSC?",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        updated = self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1000", "total": ""}
                    ],
                },
            }
        )

        path, file_name = self.service.get_repair_order_text_download(card_id)

        self.assertTrue(path.exists())
        self.assertEqual(path.name, file_name)
        self.assertNotIn(":", file_name)
        self.assertNotIn("?", file_name)
        self.assertNotIn("/", file_name)
        self.assertTrue(file_name.endswith(".txt"))
        self.assertIn("__", file_name)
        self.assertEqual(updated["card"]["repair_order"]["number"], "1")

    def test_repair_order_text_write_does_not_overwrite_existing_fixed_tmp_file(self) -> None:
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
        path, _ = self.service.get_repair_order_text_download(card_id)
        fixed_tmp = path.with_suffix(path.suffix + ".tmp")
        fixed_tmp.write_text("sentinel", encoding="utf-8")

        self.service.get_repair_order_text_download(card_id)

        self.assertEqual(fixed_tmp.read_text(encoding="utf-8"), "sentinel")

    def test_repair_order_text_payload_rejects_oversized_disk_file_before_reading(self) -> None:
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
        path, _ = self.service.get_repair_order_text_download(card_id)
        path.write_text("x" * 16, encoding="utf-8")

        with (
            patch("minimal_kanban.services.card_service.REPAIR_ORDER_TEXT_FILE_MAX_BYTES", 8),
            self.assertRaises(ServiceError) as raised,
        ):
            self.service.get_repair_order_text({"card_id": card_id})

        self.assertEqual(raised.exception.code, "repair_order_text_too_large")
        self.assertEqual(raised.exception.status_code, 413)

    def test_repair_order_text_write_rejects_oversized_render_without_clobbering_file(
        self,
    ) -> None:
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
        path, _ = self.service.get_repair_order_text_download(card_id)
        original = path.read_text(encoding="utf-8")

        with (
            patch("minimal_kanban.services.card_service.REPAIR_ORDER_TEXT_FILE_MAX_BYTES", 8),
            self.assertRaises(ServiceError) as raised,
        ):
            self.service.get_repair_order_text_download(card_id)

        self.assertEqual(raised.exception.code, "repair_order_text_too_large")
        self.assertEqual(raised.exception.status_code, 413)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_list_repair_orders_creates_text_files_and_sorts_by_latest_number(self) -> None:
        first = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "Первый заказ", "deadline": {"hours": 2}}
        )
        second = self.service.create_card(
            {"vehicle": "LADA VESTA", "title": "Второй заказ", "deadline": {"hours": 2}}
        )

        first_id = first["card"]["id"]
        second_id = second["card"]["id"]

        self.service.update_card(
            {
                "card_id": first_id,
                "repair_order": {
                    "client": "Иван",
                    "comment": "Первый текстовый заказ-наряд",
                    "works": [
                        {"name": "Диагностика", "quantity": "1", "price": "1000", "total": "1000"}
                    ],
                },
            }
        )
        self.service.update_card(
            {
                "card_id": second_id,
                "repair_order": {
                    "client": "Петр",
                    "comment": "Второй текстовый заказ-наряд",
                    "materials": [
                        {"name": "Масло", "quantity": "4", "price": "700", "total": "2800"}
                    ],
                },
            }
        )

        listed = self.service.list_repair_orders()
        self.assertEqual(listed["meta"]["limit"], 300)
        self.assertEqual(listed["repair_orders"][0]["number"], "2")
        self.assertEqual(listed["repair_orders"][1]["number"], "1")
        self.assertEqual(listed["repair_orders"][0]["grand_total"], "2800")
        self.assertEqual(listed["repair_orders"][0]["paid_total"], "0")
        self.assertEqual(listed["repair_orders"][0]["payment_status"], "unpaid")
        self.assertEqual(listed["repair_orders"][0]["vehicle"], "LADA VESTA")
        self.assertEqual(listed["repair_orders"][0]["created_at"], second["card"]["created_at"])

        file_path = Path(listed["repair_orders"][0]["file_path"])
        self.assertTrue(file_path.exists())
        text = file_path.read_text(encoding="utf-8")
        self.assertIn("2", text)
        self.assertIn("2800", text)
        self.assertIn("LADA VESTA", text)
        self.assertIn("JSON:", text)

        download_path, file_name = self.service.get_repair_order_text_download(second_id)
        self.assertEqual(download_path.name, file_name)
        self.assertEqual(download_path, file_path)

    def test_list_repair_orders_cleans_up_old_text_files_beyond_retention_limit(self) -> None:
        first = self.service.create_card(
            {"vehicle": "KIA RIO", "title": "Order one", "deadline": {"hours": 2}}
        )
        second = self.service.create_card(
            {"vehicle": "LADA VESTA", "title": "Order two", "deadline": {"hours": 2}}
        )

        self.service.update_card(
            {
                "card_id": first["card"]["id"],
                "repair_order": {
                    "client": "A",
                    "works": [{"name": "W1", "quantity": "1", "price": "1", "total": "1"}],
                },
            }
        )
        self.service.update_card(
            {
                "card_id": second["card"]["id"],
                "repair_order": {
                    "client": "B",
                    "works": [{"name": "W2", "quantity": "1", "price": "2", "total": "2"}],
                },
            }
        )

        first_path, _ = self.service.get_repair_order_text_download(first["card"]["id"])
        second_path, _ = self.service.get_repair_order_text_download(second["card"]["id"])
        self.assertTrue(first_path.exists())
        self.assertTrue(second_path.exists())

        with patch("minimal_kanban.services.card_service.REPAIR_ORDER_FILE_RETENTION_LIMIT", 1):
            self.service.list_repair_orders()

        self.assertFalse(first_path.exists())
        self.assertTrue(second_path.exists())

    def test_list_repair_orders_serializes_only_requested_limit(self) -> None:
        for index in range(3):
            created = self.service.create_card(
                {"vehicle": f"CAR-{index}", "title": f"Order {index}", "deadline": {"hours": 2}}
            )
            self.service.update_card(
                {
                    "card_id": created["card"]["id"],
                    "repair_order": {
                        "client": f"Client {index}",
                        "works": [
                            {
                                "name": f"Work {index}",
                                "quantity": "1",
                                "price": "1000",
                                "total": "1000",
                            }
                        ],
                    },
                }
            )

        with patch.object(
            self.service,
            "_serialize_repair_order_list_item",
            wraps=self.service._serialize_repair_order_list_item,
        ) as serialize_item:
            listed = self.service.list_repair_orders({"limit": 2})

        self.assertEqual(listed["meta"]["total"], 3)
        self.assertEqual(listed["meta"]["limit"], 2)
        self.assertEqual(len(listed["repair_orders"]), 2)
        self.assertEqual(serialize_item.call_count, 2)
        self.assertEqual([item["number"] for item in listed["repair_orders"]], ["3", "2"])


if __name__ == "__main__":
    unittest.main()
