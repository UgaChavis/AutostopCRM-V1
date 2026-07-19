from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.models import (  # noqa: E402
    Attachment,
    Card,
    InventoryMovement,
    _clamp_ratio,
    _rgb_to_rgba,
    calculate_deadline_progress_bucket,
    calculate_deadline_progress_ratio,
    deadline_heat_border_color_for_bucket,
    deadline_heat_color_for_bucket,
    format_money_minor,
    format_remaining_seconds,
    normalize_int,
    normalize_money_minor,
    split_seconds_to_days_hours,
)


class ModelsTests(unittest.TestCase):
    def test_clamp_ratio_rejects_non_finite_and_invalid_values(self) -> None:
        self.assertEqual(_clamp_ratio(True), 0.0)
        self.assertEqual(_clamp_ratio(float("nan")), 0.0)
        self.assertEqual(_clamp_ratio(float("inf")), 0.0)
        self.assertEqual(_clamp_ratio("bad"), 0.0)
        self.assertEqual(_clamp_ratio(-0.5), 0.0)
        self.assertEqual(_clamp_ratio(1.5), 1.0)

    def test_deadline_progress_helpers_tolerate_non_finite_numbers(self) -> None:
        self.assertEqual(calculate_deadline_progress_ratio(0, math.nan), 1.0)
        self.assertEqual(calculate_deadline_progress_ratio(math.nan, 3600), 1.0)
        self.assertEqual(calculate_deadline_progress_bucket(math.nan), 0)
        self.assertEqual(_rgb_to_rgba((1, 2, 3), math.nan), "rgba(1, 2, 3, 0.000)")

    def test_deadline_heat_and_duration_helpers_tolerate_invalid_numbers(self) -> None:
        self.assertEqual(deadline_heat_color_for_bucket(math.nan), "#53bf7a")
        self.assertEqual(deadline_heat_color_for_bucket(1e308), "#d46262")
        self.assertTrue(deadline_heat_border_color_for_bucket("bad").startswith("rgba("))
        self.assertEqual(format_remaining_seconds(math.nan), "0д 00:00:00")
        self.assertEqual(format_remaining_seconds(1e308), "365д 00:00:00")
        self.assertEqual(split_seconds_to_days_hours(math.nan), (0, 1))
        self.assertEqual(format_remaining_seconds(3661), "0д 01:01:01")

    def test_normalize_int_clamps_large_finite_values_when_maximum_is_set(self) -> None:
        self.assertEqual(normalize_int(1e308, default=7, minimum=1, maximum=365), 365)
        self.assertEqual(normalize_int(-1e308, default=7, minimum=1, maximum=365), 1)
        self.assertEqual(normalize_int(1e308, default=7, minimum=1), 7)

    def test_card_from_dict_clamps_oversized_deadline_total_seconds(self) -> None:
        card = Card.from_dict(
            {
                "id": "card-deadline",
                "title": "Диагностика",
                "vehicle": "Toyota",
                "deadline_total_seconds": 1e308,
            }
        )

        self.assertEqual(card.deadline_total_seconds, 31_536_000)

    def test_card_timer_state_defaults_legacy_to_running_and_supports_inactive(self) -> None:
        legacy = Card.from_dict({"id": "legacy-card", "title": "Старый таймер"})
        inactive = Card.from_dict(
            {"id": "inactive-card", "title": "Без таймера", "timer_state": "inactive"}
        )

        self.assertEqual(legacy.timer_state, "running")
        self.assertTrue(legacy.timer_is_running())
        self.assertEqual(inactive.timer_state, "inactive")
        self.assertFalse(inactive.timer_is_running())
        self.assertEqual(inactive.remaining_seconds(), 0)
        self.assertEqual(inactive.status(), "ok")
        self.assertFalse(inactive.is_blinking())
        self.assertEqual(inactive.deadline_progress_bucket(), 0)
        self.assertEqual(inactive.to_dict()["timer_active"], False)
        self.assertEqual(inactive.to_storage_dict()["timer_state"], "inactive")

    def test_money_formatter_and_card_event_count_tolerate_invalid_numbers(self) -> None:
        card = Card.from_dict({"id": "card-1", "title": "Диагностика", "vehicle": "Toyota"})

        self.assertEqual(normalize_money_minor(1e308, default=500), 500)
        self.assertEqual(normalize_money_minor(10**30), 100_000_000_000_000)
        self.assertEqual(normalize_money_minor(-(10**30)), -100_000_000_000_000)
        self.assertEqual(format_money_minor(1e308), "1 000 000 000 000,00 ₽")
        self.assertEqual(format_money_minor(math.nan), "0,00 ₽")
        self.assertEqual(format_money_minor(-12345), "-123,45 ₽")
        self.assertEqual(card.to_dict(events_count=math.nan)["events_count"], 0)
        self.assertEqual(card.to_dict(events_count="3")["events_count"], 3)
        self.assertEqual(card.to_dict(events_count=1e308)["events_count"], 1_000_000)

    def test_model_from_dict_clamps_oversized_numeric_fields(self) -> None:
        attachment = Attachment.from_dict(
            {
                "id": "attachment-1",
                "file_name": "scan.pdf",
                "stored_name": "scan.pdf",
                "size_bytes": 1e308,
            }
        )
        movement = InventoryMovement.from_dict(
            {
                "id": "movement-1",
                "item_id": "item-1",
                "repair_order_row_index": 1e308,
            }
        )
        card = Card.from_dict(
            {
                "id": "card-numbers",
                "title": "Диагностика",
                "vehicle": "Toyota",
                "position": 1e308,
                "ai_run_count": 1e308,
            }
        )

        self.assertEqual(attachment.size_bytes, 15 * 1024 * 1024)
        self.assertEqual(movement.repair_order_row_index, 100_000)
        self.assertEqual(card.position, 1_000_000)
        self.assertEqual(card.ai_run_count, 1_000_000)

    def test_card_from_dict_skips_attachment_overflow_records(self) -> None:
        with patch("minimal_kanban.models.Attachment.from_dict", side_effect=OverflowError):
            card = Card.from_dict(
                {
                    "id": "card-1",
                    "title": "Диагностика",
                    "vehicle": "Toyota",
                    "attachments": [{"id": "bad"}],
                }
            )

        self.assertEqual(card.attachments, [])

    def test_board_summary_fingerprint_tolerates_non_finite_numbers(self) -> None:
        card = Card.from_dict({"id": "card-1", "title": "Диагностика", "vehicle": "Toyota"})
        card.deadline_total_seconds = math.nan

        first = card.board_summary_content_fingerprint()
        second = card.board_summary_content_fingerprint()

        self.assertRegex(first, r"^[0-9a-f]{24}$")
        self.assertEqual(first, second)

    def test_card_deadline_methods_tolerate_non_finite_total_seconds(self) -> None:
        card = Card.from_dict({"id": "card-1", "title": "Диагностика", "vehicle": "Toyota"})
        card.deadline_total_seconds = math.nan

        self.assertGreaterEqual(card.remaining_ratio(), 0.0)
        self.assertGreaterEqual(card.deadline_progress_ratio(), 0.0)
        self.assertGreaterEqual(card.deadline_progress_bucket(), 0)


if __name__ == "__main__":
    unittest.main()
