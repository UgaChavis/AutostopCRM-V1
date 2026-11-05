"""Search both displayed and full years without changing legacy date parsing."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from unittest.mock import patch

from tests.services_case import CardServiceCase


class RepairOrderDateSearchTests(CardServiceCase):
    def setUp(self):
        appdata = tempfile.TemporaryDirectory(prefix="synthetic-order-search-")
        self.addCleanup(appdata.cleanup)
        environment = patch.dict(
            os.environ,
            {"APPDATA": appdata.name, "AUTOSTOPCRM_BUSINESS_TIMEZONE": "Asia/Krasnoyarsk"},
        )
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()
        self.service = self._build_service()
        self.service.create_card({"title": "SYNTHETIC"})
        state = json.loads(self.state_file.read_text())
        seed = state["cards"][0]
        state["cards"] = []
        for number, status, opened_at, closed_at in (
            ("FOUR", "open", "31.10.2026 12:00:30", ""),
            ("ISO", "open", "2026-10-30T17:30:00Z", ""),
            ("SHORT", "open", "31.10.26 12:00", ""),
            ("FUTURE", "open", "31.10.2080 12:00", ""),
            ("JAN2020", "open", "31.01.2020 12:00", ""),
            ("JAN2026", "open", "31.01.2026 12:00", ""),
            ("READY", "ready", "31.10.2026 12:00", ""),
            ("CLOSED", "closed", "01.01.2020 10:00", "31.10.2026 12:00:00"),
            ("FALLBACK", "closed", "31.10.2026 11:00:00", ""),
        ):
            card = deepcopy(seed)
            card["id"] = "synthetic-" + number
            card["repair_order"] = {
                "number": number,
                "status": status,
                "opened_at": opened_at,
                "closed_at": closed_at,
            }
            state["cards"].append(card)
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

    def test_search_full_and_short_years_without_cross_variant_matches(self):
        before = self.state_file.read_bytes()
        cases = (
            ("open", "31.10.2026", {"FOUR", "ISO"}),
            ("open", "31102026", {"FOUR", "ISO"}),
            ("open", "31.10.26", {"FOUR", "ISO", "SHORT"}),
            ("open", "31.10.2080", {"FUTURE"}),
            ("open", "31.10.80", {"FUTURE"}),
            ("open", "20", {"JAN2020"}),
            ("open", "01.20", {"JAN2020"}),
            ("open", "10.20.26", set()),
            ("open", "2026", {"FOUR", "ISO", "JAN2026"}),
            ("open", "20263110", set()),
            ("ready", "31.10.2026", {"READY"}),
            ("closed", "31.10.2026", {"CLOSED", "FALLBACK"}),
            ("closed", "01.01.2020", set()),
        )
        for compact in (False, True):
            for redact_private in (False, True):
                for status, query, expected in cases:
                    with self.subTest(
                        compact=compact, redact=redact_private, status=status, query=query
                    ):
                        result = self.service.list_repair_orders(
                            {
                                "status": status,
                                "search_field": "date",
                                "query": query,
                                "limit": 300,
                                "compact": compact,
                                "redact_private": redact_private,
                            }
                        )
                        if redact_private:
                            # Restricted projection removes both card ID and order number.
                            self.assertEqual(len(result["repair_orders"]), len(expected))
                        else:
                            self.assertEqual(
                                {item["number"] for item in result["repair_orders"]}, expected
                            )
        self.assertEqual(self.state_file.read_bytes(), before)
