"""Client visit dates compare instants without changing their stored text."""

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
from datetime import timedelta, timezone
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.models import Card, ClientProfile


class ClientLastVisitOrderTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.client = ClientProfile(id="synthetic-client", display_name="Synthetic")

    def card(self, card_id, *, opened="", closed="", has_order=True):
        return Card.from_dict(
            {
                "id": card_id,
                "title": "Synthetic",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-02T00:00:00+00:00",
                "repair_order": {
                    "number": card_id if has_order else "",
                    "opened_at": opened,
                    "closed_at": closed,
                },
            }
        )

    def last_visit(self, cards):
        return self.service._client_stats_from_related(self.client, cards, cards, vehicles_total=0)[
            "last_visit"
        ]

    @patch.dict("os.environ", {"AUTOSTOPCRM_BUSINESS_TIMEZONE": "Asia/Krasnoyarsk"})
    def test_orders_use_chronological_instants_and_keep_source_text(self):
        older = self.card("a", opened="31.01.2024 09:00")
        newer = self.card("b", opened="01.11.2026 10:00")
        self.assertEqual(self.last_visit([older, newer]), "01.11.2026 10:00")

        business = self.card("a", closed="31.12.2026 23:00")
        aware = self.card("b", opened="2026-12-31T20:00:00+00:00")
        self.assertEqual(self.last_visit([business, aware]), "2026-12-31T20:00:00+00:00")

        same_business_time = self.card("a", opened="01.01.2026 10:00")
        same_utc_time = self.card("z", opened="2026-01-01T03:00:00Z")
        self.assertEqual(
            self.last_visit([same_business_time, same_utc_time]), "2026-01-01T03:00:00Z"
        )

        first_fold = self.card("a", opened="2026-11-01T01:30:00-04:00")
        second_fold = self.card("b", opened="2026-11-01T01:30:00-05:00")
        self.assertEqual(self.last_visit([first_fold, second_fold]), "2026-11-01T01:30:00-05:00")

    def test_invalid_or_unrepresentable_time_does_not_claim_a_last_visit(self):
        valid = self.card("valid", opened="01.11.2026 10:00")
        for date in ("invalid", "0001-01-01T00:00:00+14:00"):
            with self.subTest(date=date):
                self.assertEqual(self.last_visit([valid, self.card("bad", opened=date)]), "")
                self.assertEqual(self.last_visit([self.card("bad", opened=date), valid]), "")

        corrupted = self.card("corrupted")
        corrupted.repair_order.opened_at = ["invalid"]
        self.assertEqual(self.last_visit([corrupted]), "")

    @patch.dict("os.environ", {"AUTOSTOPCRM_BUSINESS_TIMEZONE": "America/New_York"})
    def test_ambiguous_and_nonexistent_local_times_are_not_claimed(self):
        aware = self.card("aware", opened="2025-11-02T06:00:00Z")
        for date in ("02.11.2025 01:30", "09.03.2025 02:30", "2025-11-02T01:30:00"):
            with self.subTest(date=date):
                self.assertEqual(self.last_visit([aware, self.card("local", opened=date)]), "")
        first = self.card("first", opened="2025-11-02T01:30:00-04:00")
        second = self.card("second", opened="2025-11-02T01:30:00-05:00")
        self.assertEqual(self.last_visit([first, second]), "2025-11-02T01:30:00-05:00")

    @patch.dict("os.environ", {"AUTOSTOPCRM_BUSINESS_TIMEZONE": "Synthetic/Unavailable"})
    @patch(
        "minimal_kanban.models._business_timezone_for_name",
        return_value=timezone(timedelta(hours=-5)),
    )
    def test_timezone_fallback_requires_explicit_iso_offset(self, _timezone_provider):
        for date in ("02.11.2025 01:30", "2025-11-02T01:30:00"):
            with self.subTest(date=date):
                self.assertEqual(self.last_visit([self.card("local", opened=date)]), "")
        first = self.card("first", opened="2025-11-02T01:30:00-04:00")
        second = self.card("second", opened="2025-11-02T01:30:00-05:00")
        self.assertEqual(self.last_visit([first, second]), "2025-11-02T01:30:00-05:00")

    def test_orders_keep_priority_over_plain_cards_and_empty_is_empty(self):
        order = self.card("order", opened="01.01.2020 12:00")
        plain = self.card("plain", has_order=False)
        self.assertEqual(self.last_visit([plain, order]), "01.01.2020 12:00")
        self.assertEqual(self.last_visit([plain]), "2026-01-02T00:00:00+00:00")
        self.assertEqual(self.last_visit([]), "")

    def test_repeated_local_dates_respect_timezone_changes_between_calls(self):
        cards = [
            self.card("a", opened="01.01.2026 12:00"),
            self.card("z", opened="01.01.2026 12:00"),
            self.card("b", opened="2026-01-01T07:00:00+00:00"),
        ]
        with patch.dict("os.environ", {"AUTOSTOPCRM_BUSINESS_TIMEZONE": "Asia/Krasnoyarsk"}):
            self.assertEqual(self.last_visit(cards), "2026-01-01T07:00:00+00:00")
        with patch.dict("os.environ", {"AUTOSTOPCRM_BUSINESS_TIMEZONE": "America/New_York"}):
            self.assertEqual(self.last_visit(cards), "01.01.2026 12:00")
        with patch.dict("os.environ", {"AUTOSTOPCRM_BUSINESS_TIMEZONE": "Asia/Krasnoyarsk"}):
            self.assertEqual(self.last_visit(cards), "2026-01-01T07:00:00+00:00")
