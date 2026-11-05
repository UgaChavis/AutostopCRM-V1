"""General order search treats е and ё consistently in both directions."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from tests.services_case import CardServiceCase


class RepairOrderGeneralSearchTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.service = self._build_service()
        self.service.create_card({"title": "SYNTHETIC"})
        state = json.loads(self.state_file.read_text())
        seed = state["cards"][0]
        state["cards"] = []
        for number, title in (("TREE", "МОЙКА ЁЛКИ"), ("WHEELS", "ПРОВЕРКА КОЛЕС")):
            card = deepcopy(seed)
            card["id"] = "synthetic-" + number
            card["title"] = title
            card["description"] = ""
            card["repair_order"] = {"number": number, "status": "open"}
            state["cards"].append(card)
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

    def files(self):
        root = Path(self.temp_dir.name)
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_general_search_folds_both_sides_without_writes(self):
        before = self.files()
        cases = (
            ("елки", {"TREE"}),
            ("ЁЛКИ", {"TREE"}),
            ("колес", {"WHEELS"}),
            ("КОЛЁС", {"WHEELS"}),
            ("мойка, елки", {"TREE"}),
            ("ёлки мойка", {"TREE"}),
            ("мойка колес", set()),
            ("", {"TREE", "WHEELS"}),
            ("   ", {"TREE", "WHEELS"}),
        )
        for compact in (False, True):
            for explicit_field in (False, True):
                for query, expected in cases:
                    with self.subTest(compact=compact, explicit=explicit_field, query=query):
                        params = {"status": "open", "query": query, "compact": compact}
                        if explicit_field:
                            params["search_field"] = "all"
                        result = self.service.list_repair_orders(params)
                        self.assertEqual(
                            {item["number"] for item in result["repair_orders"]}, expected
                        )
        self.assertEqual(self.files(), before)
