from __future__ import annotations

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
import itertools
from types import SimpleNamespace
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.models import Card
from minimal_kanban.services.readonly_context_helpers import client_readonly_related_cards
from minimal_kanban.storage.json_store import StateFileCorruptedError


class ClientSearchRetentionAmbiguityTests(CardServiceCase):
    def test_duplicate_variants_match_all_possible_retention_outcomes(self):
        def card(card_id, *, archived=True, date="2020-01-01T00:00:00+00:00", order=False):
            return Card.from_dict(
                {
                    "id": card_id,
                    "client_id": "A" if card_id == "A" else "B",
                    "archived": archived,
                    "created_at": date,
                    "updated_at": date,
                    "repair_order": {"client": "synthetic"} if order else {},
                }
            )

        client = SimpleNamespace(id="A")
        service = SimpleNamespace(
            _store=self.store,
            _client_ids_by_match_key=lambda clients: {},
            _card_client_values=lambda card: set(),
        )
        bundle = SimpleNamespace(
            search_cards_need_retention=True, require_card_identity=lambda card: None
        )
        selected = card("A")
        newer = card("D", date="2040-01-01T00:00:00+00:00")

        def variants(card_id):
            return (
                card(card_id, archived=False),
                card(card_id, order=True),
                card(card_id, date="1990-01-01T00:00:00+00:00"),
                card(card_id),
                card(card_id, date="2030-01-01T00:00:00+00:00"),
            )

        def contains_selected(cards):
            retained, _ = self.store._apply_card_retention(cards)
            return any(card.id == "A" for card in retained)

        options_b, options_c = variants("B"), variants("C")
        self.assertFalse(options_b[0].archived)
        self.assertTrue(options_b[1].archived)
        self.assertFalse(options_b[1].repair_order.is_empty())
        self.assertTrue(all(card.repair_order.is_empty() for card in options_b[2:]))
        with patch("minimal_kanban.storage.json_store.ARCHIVED_CARD_RETENTION_LIMIT", 2):
            for b1, b2, c1, c2 in itertools.product(range(5), repeat=4):
                b = (options_b[b1], options_b[b2])
                c = (options_c[c1], options_c[c2])
                source = [selected, newer, *b, *c]
                current = contains_selected(source)
                possible = any(
                    contains_selected([selected, newer, one_b, one_c])
                    for one_b, one_c in itertools.product(b, c)
                )
                if current != possible:
                    with self.assertRaises(StateFileCorruptedError):
                        client_readonly_related_cards(service, bundle, [client], source)
                else:
                    result = client_readonly_related_cards(service, bundle, [client], source)
                    self.assertEqual([card.id for card in result["A"]], ["A"] if current else [])
