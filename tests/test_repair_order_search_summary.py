"""Summary search keeps useful word fragments without short reverse matches."""

from __future__ import annotations

import json
from copy import deepcopy

from tests.services_case import CardServiceCase


class RepairOrderSummarySearchTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.service = self._build_service()
        self.service.create_card({"title": "SYNTHETIC"})
        state = json.loads(self.state_file.read_text())
        seed = state["cards"][0]
        state["cards"] = []
        for number, text in (
            ("AND", "и"),
            ("ON", "на"),
            ("WITHOUT", "без"),
            ("STEMS", "масл замен"),
            ("WORDS", "зарядка аккумулятора"),
            ("BMW", "BMW"),
            ("GAS", "ГАЗ"),
            ("CASE", "МОЙКА ЁЛКИ"),
        ):
            card = deepcopy(seed)
            card["id"] = "synthetic-" + number
            card["title"] = text
            card["description"] = ""
            card["repair_order"] = {"number": number, "status": "open"}
            state["cards"].append(card)
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

    def test_summary_word_matching(self):
        cases = (
            ("свечи", set()),
            ("наконечник", set()),
            ("безопасность", set()),
            ("масло замена", {"STEMS"}),
            ("аккум заряд", {"WORDS"}),
            ("и", {"AND", "CASE"}),
            ("на", {"ON"}),
            ("без", {"WITHOUT"}),
            ("bmw", {"BMW"}),
            ("газ", {"GAS"}),
            ("bmwx5", set()),
            ("газель", set()),
            ("мойка, елки", {"CASE"}),
        )
        for compact in (False, True):
            for query, expected in cases:
                with self.subTest(compact=compact, query=query):
                    result = self.service.list_repair_orders(
                        {
                            "status": "open",
                            "search_field": "summary",
                            "query": query,
                            "limit": 300,
                            "compact": compact,
                        }
                    )
                    self.assertEqual({item["number"] for item in result["repair_orders"]}, expected)
