"""Literal deduplication preserves legacy matching and ambiguous links."""

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.models import Card, ClientProfile


class ClientMatchValueDedupTests(CardServiceCase):
    def legacy_values(self, card):
        values = [
            card.vehicle_profile.customer_name,
            card.vehicle_profile.customer_phone,
            *list(card.vehicle_profile.customer_phones or []),
            card.repair_order.client,
            card.repair_order.phone,
        ]
        result = set()
        for value in values:
            if not value:
                continue
            normalized = self.service._normalize_search_text(value)
            if normalized:
                result.add(normalized)
            result.update(self.service._phone_match_keys(value))
        return result

    def card(self):
        return Card.from_dict({"id": "synthetic-card", "title": "Synthetic"})

    def test_values_match_legacy_for_phones_unicode_and_unhashable_values(self):
        cases = [
            (
                "Synthetic",
                "+7 900 123-45-67",
                ["+7 900 123-45-67"],
                "Synthetic",
                "+7 900 123-45-67",
            ),
            (
                "Компания １２３",
                "１２３４５６７８９０",
                ["１２３４５６７８９０", "123-45"],
                "Компания １２３",
                "123-45",
            ),
            ("  TEST  ", "1234567", ["1234567", "123456"], "test", ""),
            (
                {"legacy": "Synthetic"},
                ["12345678"],
                [["12345678"], {"phone": 12345678}, 12345678],
                None,
                12345678,
            ),
            ("", "", [], "", ""),
        ]
        for name, phone, phones, order_name, order_phone in cases:
            with self.subTest(values=(name, phone, phones, order_name, order_phone)):
                card = self.card()
                card.vehicle_profile.customer_name = name
                card.vehicle_profile.customer_phone = phone
                card.vehicle_profile.customer_phones = phones
                card.repair_order.client = order_name
                card.repair_order.phone = order_phone
                self.assertEqual(self.service._card_client_values(card), self.legacy_values(card))

    def test_duplicate_strings_are_normalized_once(self):
        card = self.card()
        card.vehicle_profile.customer_name = "Synthetic"
        card.vehicle_profile.customer_phone = "+7 900 123-45-67"
        card.vehicle_profile.customer_phones = ["+7 900 123-45-67"]
        card.repair_order.client = "Synthetic"
        card.repair_order.phone = "+7 900 123-45-67"
        expected = self.legacy_values(card)
        with patch.object(
            self.service, "_phone_match_keys", wraps=self.service._phone_match_keys
        ) as calls:
            self.assertEqual(self.service._card_client_values(card), expected)
        self.assertEqual(calls.call_count, 2)

    def test_ambiguous_client_links_and_order_match_legacy(self):
        clients = [
            ClientProfile.from_dict({"id": identity, "display_name": "Synthetic shared"})
            for identity in ("client-a", "client-b")
        ]
        cards = [
            Card.from_dict(
                {
                    "id": f"card-{i}",
                    "title": "Synthetic",
                    "client_id": "client-a" if i == 0 else "",
                    "vehicle_profile": {
                        "customer_name": "Synthetic shared",
                        "customer_phone": "+7 900 123-45-67",
                    },
                    "updated_at": f"2026-01-0{i + 1}T00:00:00+00:00",
                }
            )
            for i in range(3)
        ]
        for client in clients:
            with patch.object(self.service, "_card_client_values", side_effect=self.legacy_values):
                expected = self.service._client_related_cards(client, cards)
            actual = self.service._client_related_cards(client, cards)
            self.assertEqual([item.id for item in actual], [item.id for item in expected])
            self.assertEqual(len(actual), 3)
