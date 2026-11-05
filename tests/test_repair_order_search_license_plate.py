"""Plate search preserves legacy sources without joining unrelated fields."""

from __future__ import annotations

import json
from copy import deepcopy

from tests.services_case import CardServiceCase


class RepairOrderPlateSearchTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.service = self._build_service()
        self.service.create_card({"title": "SYNTHETIC"})
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        seed = state["cards"][0]
        state["cards"] = []
        for number, plate, description, title, reason in (
            ("split", "A123", "BC456", "other", ""),
            ("plate", "A 123-BC 456", "other", "other", ""),
            ("summary", "", "A 123-BC 456", "other", ""),
            ("reason", "", "other", "other", "A 123-BC 456"),
            ("heading", "", "other", "A 123-BC 456", ""),
            ("cyrillic", "А 123-ВС 456", "other", "other", ""),
            ("empty", "", "", "other", ""),
        ):
            card = deepcopy(seed)
            card.update(id="synthetic-" + number, title=title, description=description)
            card["repair_order"] = {
                "number": number,
                "status": "open",
                "license_plate": plate,
                "reason": reason,
            }
            state["cards"].append(card)
        self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def test_sources_and_separator_variants_without_cross_field_match(self):
        before = self.state_file.read_bytes()
        expected = {"plate", "summary", "reason", "heading"}
        cases = (
            ("A123BC456", expected),
            ("A 123-BC 456", expected),
            ("A-123-BC-456", expected),
            ("a123bc456", expected),
            ("А123ВС456", {"cyrillic"}),
            ("A123", expected | {"split"}),
            ("BC456", expected | {"split"}),
            ("MISSING", set()),
            ("", expected | {"split", "cyrillic", "empty"}),
        )
        for compact in (False, True):
            for query, matches in cases:
                with self.subTest(compact=compact, query=query):
                    result = self.service.list_repair_orders(
                        {
                            "status": "open",
                            "search_field": "license_plate",
                            "query": query,
                            "limit": 300,
                            "compact": compact,
                        }
                    )
                    self.assertEqual({item["number"] for item in result["repair_orders"]}, matches)
        self.assertEqual(self.state_file.read_bytes(), before)
