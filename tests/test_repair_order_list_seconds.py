"""List sorting understands legacy seconds without changing other date paths."""

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.models import parse_business_datetime


class RepairOrderListSecondsTests(CardServiceCase):
    def setUp(self):
        appdata = tempfile.TemporaryDirectory(prefix="synthetic-order-sort-")
        self.addCleanup(appdata.cleanup)
        environment = patch.dict(
            os.environ,
            {
                "APPDATA": appdata.name,
                "AUTOSTOPCRM_BUSINESS_TIMEZONE": "Asia/Krasnoyarsk",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()
        self.service = self._build_service()
        self.service.create_card({"title": "SYNTHETIC"})
        state = json.loads(self.state_file.read_text())
        seed = state["cards"][0]
        state["cards"] = []
        dates = (
            ("OLDER", "31.10.2026 12:00:30"),
            ("NORMAL", "01.11.2026 12:00"),
            ("SHORT", "1.11.26, 12:00:15"),
            ("ISO", "2026-11-01T06:00:00Z"),
        )
        for status in ("open", "closed"):
            for number, date in dates:
                card = deepcopy(seed)
                card["id"] = f"synthetic-{status}-{number}"
                card["repair_order"] = {
                    "number": number,
                    "status": status,
                    "opened_at": date if status == "open" else "01.01.2026 00:00",
                    "closed_at": date if status == "closed" else "",
                }
                state["cards"].append(card)
        self.state_file.write_text(json.dumps(state), encoding="utf-8")

    def files(self):
        root = Path(self.temp_dir.name)
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_open_and_closed_dates_sort_chronologically_without_writes(self):
        before = self.files()
        for status, sort_by in (("open", "opened_at"), ("closed", "closed_at")):
            with self.subTest(status=status):
                for direction, expected in (
                    ("desc", ["ISO", "SHORT", "NORMAL", "OLDER"]),
                    ("asc", ["OLDER", "NORMAL", "SHORT", "ISO"]),
                ):
                    result = self.service.list_repair_orders(
                        {
                            "status": status,
                            "sort_by": sort_by,
                            "sort_dir": direction,
                            "compact": True,
                        }
                    )
                    self.assertEqual([item["number"] for item in result["repair_orders"]], expected)
        self.assertEqual(before, self.files())

    def test_invalid_seconds_and_general_parser_keep_existing_behavior(self):
        invalid = "31.02.2026 12:00:01"
        valid = "31.10.2026 12:00:30"
        self.assertEqual(
            self.service._repair_order_list_date_sort_value(invalid),
            self.service._repair_order_sortable_datetime(invalid),
        )
        self.assertIsNone(parse_business_datetime(valid))
        self.assertEqual(self.service._repair_order_sortable_datetime(valid), valid)

    def test_seconds_dates_with_years_under_1000_sort_chronologically(self):
        state = json.loads(self.state_file.read_text())
        cards = state["cards"][:2]
        for card, number, opened_at in zip(
            cards,
            ("OLDER", "NEWER"),
            ("31.12.0099 12:00:30", "01.01.0100 12:00:30"),
            strict=True,
        ):
            card["repair_order"] = {"number": number, "status": "open", "opened_at": opened_at}
        state["cards"] = cards
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.files()
        result = self.service.list_repair_orders({"sort_by": "opened_at", "sort_dir": "desc"})
        self.assertEqual([item["number"] for item in result["repair_orders"]], ["NEWER", "OLDER"])
        self.assertEqual(before, self.files())
