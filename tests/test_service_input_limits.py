from __future__ import annotations

# The fixture import sets up the src path before importing the application module.
# ruff: noqa: I001
from tests.services_case import CardServiceCase

from minimal_kanban.models import CARD_DESCRIPTION_LIMIT
from minimal_kanban.services.card_service import ServiceError


class ServiceInputLimitTests(CardServiceCase):
    def test_service_numeric_limits_reject_overflowing_values_as_validation_errors(self) -> None:
        with self.assertRaises(ServiceError) as limit_error:
            self.service.list_inventory_items({"limit": 1e308})

        self.assertEqual(limit_error.exception.code, "validation_error")
        self.assertEqual(limit_error.exception.details.get("field"), "limit")

        with self.assertRaises(ServiceError) as numeric_limit_error:
            self.service._validated_numeric_limit(
                1e308, field="max_chars", default=1000, maximum=5000
            )

        self.assertEqual(numeric_limit_error.exception.code, "validation_error")
        self.assertEqual(numeric_limit_error.exception.details.get("field"), "max_chars")

    def test_service_numeric_limits_reject_bool_and_fractional_values(self) -> None:
        for value in (True, 1.5, "2.5"):
            with self.subTest(value=value):
                with self.assertRaises(ServiceError) as limit_error:
                    self.service.list_inventory_items({"limit": value})
                self.assertEqual(limit_error.exception.code, "validation_error")
                self.assertEqual(limit_error.exception.details.get("field"), "limit")

        for value in (True, 12.5, "12.5"):
            with self.subTest(value=value):
                with self.assertRaises(ServiceError) as numeric_limit_error:
                    self.service._validated_numeric_limit(
                        value, field="max_chars", default=1000, maximum=5000
                    )
                self.assertEqual(numeric_limit_error.exception.code, "validation_error")
                self.assertEqual(numeric_limit_error.exception.details.get("field"), "max_chars")

    def test_supports_large_card_description(self) -> None:
        large_description = "А" * 12000

        created = self.service.create_card(
            {
                "title": "Длинное описание",
                "description": large_description,
                "deadline": {"days": 0, "hours": 2},
            }
        )

        self.assertEqual(created["card"]["description"], large_description)
        self.assertGreater(len(created["card"]["description"]), 5000)

    def test_rejects_card_description_above_limit(self) -> None:
        too_large_description = "Б" * (CARD_DESCRIPTION_LIMIT + 1)

        with self.assertRaises(ServiceError) as description_error:
            self.service.create_card(
                {
                    "title": "Слишком длинное описание",
                    "description": too_large_description,
                    "deadline": {"days": 0, "hours": 2},
                }
            )

        self.assertEqual(description_error.exception.code, "validation_error")
