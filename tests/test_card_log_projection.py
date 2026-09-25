from __future__ import annotations

# The shared fixture configures src/ before the application import.
# ruff: noqa: I001
from tests.services_case import CardServiceCase

from minimal_kanban.models import Card  # noqa: E402


class CardLogProjectionTests(CardServiceCase):
    def test_formatters_tolerate_invalid_counts_and_deadlines(self) -> None:
        projection = self.service._snapshot_service._card_log_projection
        card = Card.from_dict({"id": "card-1", "title": "ЛОГ"})
        totals = {
            "count": float("inf"),
            "actors": True,
            "actions": "3",
            "changes": "bad",
            "deletions": 1.5,
        }
        markdown = projection._card_log_markdown(
            card=card,
            entries=[],
            days=[],
            weeks=[],
            months=[],
            totals=totals,
            meta={"events_total": "bad"},
        )

        self.assertEqual(
            projection._card_log_count_summary({"count": float("inf")}).split(" | ")[0],
            "0 событий",
        )
        self.assertEqual(
            projection._card_log_count_summary({"count": 1e308}).split(" | ")[0],
            "1000000000 событий",
        )
        self.assertEqual(projection._card_log_deadline_human_value(float("inf")), "inf")
        self.assertEqual(
            projection._card_log_deadline_human_value(1e308),
            "365 дней",
        )
        self.assertEqual(
            projection._card_log_deadline_human_value(-1e308),
            "без срока",
        )
        huge_week = f"{'9' * 32}-W1"
        self.assertEqual(projection._card_log_week_label(huge_week), huge_week)
        self.assertEqual(self.service._cash_journal_week_label(huge_week), huge_week)
        self.assertIn("- Показано: 0 событий из bad", markdown)
        self.assertIn("- Разных действий: 3 типа", markdown)
