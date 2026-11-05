"""Search handles only confirmed boundary timezone overflows."""

from __future__ import annotations

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from tests.services_case import CardServiceCase
from minimal_kanban.repair_order import RepairOrder


class RepairOrderSearchOverflowTests(CardServiceCase):
    def setUp(self):
        appdata = tempfile.TemporaryDirectory(prefix="synthetic-order-overflow-")
        self.addCleanup(appdata.cleanup)
        self.appdata = Path(appdata.name)
        environment = patch.dict(
            os.environ,
            {"APPDATA": appdata.name, "AUTOSTOPCRM_BUSINESS_TIMEZONE": "America/New_York"},
        )
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()
        self.service = self._build_service()
        self.card_id = self.service.create_card({"title": "SYNTHETIC"})["card"]["id"]

    def files(self):
        roots = {"state": Path(self.temp_dir.name), "appdata": self.appdata}
        return {
            (name, str(path.relative_to(root))): path.read_bytes()
            for name, root in roots.items()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_extreme_search_dates_leave_other_fields_searchable(self):
        bundle = self.store.read_bundle()
        card = next(card for card in bundle["cards"] if card.id == self.card_id)
        extremes = (
            "0001-01-01T00:00:00+14:00",
            "9999-12-31T23:59:59-12:00",
            "01.01.0001 00:00",
        )
        for field in ("opened_at", "closed_at", "created_at"):
            for value in extremes:
                with self.subTest(field=field, value=value):
                    card.created_at = value if field == "created_at" else "2025-01-01T12:00:00Z"
                    card.repair_order = RepairOrder.from_dict(
                        {
                            "number": "1",
                            "status": "closed" if field == "closed_at" else "open",
                            "opened_at": value if field == "opened_at" else "",
                            "closed_at": value if field == "closed_at" else "",
                        }
                    )
                    self.store.write_bundle(**bundle)
                    before = self.files()
                    for compact in (True, False):
                        # The existing full serializer independently fails on this legacy format.
                        if not compact and field == "created_at" and value.startswith("01."):
                            continue
                        params = {"status": card.repair_order.status, "compact": compact}
                        by_date = self.service.list_repair_orders(
                            {**params, "search_field": "date", "query": "0001"}
                        )
                        self.assertEqual(by_date["meta"]["returned"], 0)
                        by_offset = self.service.list_repair_orders(
                            {**params, "search_field": "date", "query": "14"}
                        )
                        self.assertEqual(by_offset["meta"]["returned"], 0)
                        by_title = self.service.list_repair_orders(
                            {**params, "search_field": "all", "query": "SYNTHETIC"}
                        )
                        self.assertEqual(by_title["meta"]["returned"], 1)
                    self.assertEqual(self.files(), before)

    def test_closed_date_remains_searchable_when_created_at_overflows(self):
        bundle = self.store.read_bundle()
        card = next(card for card in bundle["cards"] if card.id == self.card_id)
        card.created_at = "0001-01-01T00:00:00+14:00"
        card.repair_order = RepairOrder.from_dict(
            {"number": "1", "status": "closed", "closed_at": "10.06.2025 12:00"}
        )
        self.store.write_bundle(**bundle)
        before = self.files()
        for compact in (False, True):
            result = self.service.list_repair_orders(
                {"status": "closed", "compact": compact, "search_field": "date", "query": "2025"}
            )
            self.assertEqual(result["meta"]["returned"], 1)
        self.assertEqual(self.files(), before)

    def test_unexpected_overflows_are_not_suppressed(self):
        for value in ("0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-12:00"):
            with (
                self.subTest(value=value),
                self.assertRaises(OverflowError),
            ):
                self.service._repair_order_card_datetime(value)
        parsed = Mock(year=2025)
        parsed.astimezone.side_effect = OverflowError("unexpected conversion")
        with (
            patch("minimal_kanban.services.card_service.parse_datetime", return_value=parsed),
            self.assertRaises(OverflowError),
        ):
            self.service._repair_order_card_datetime("ignored", search_overflow_none=True)
        parsed.year = 1
        parsed.astimezone.side_effect = None
        parsed.astimezone.return_value.strftime.side_effect = OverflowError("unexpected format")
        with (
            patch("minimal_kanban.services.card_service.parse_datetime", return_value=parsed),
            self.assertRaises(OverflowError),
        ):
            self.service._repair_order_card_datetime("ignored", search_overflow_none=True)
        for target in ("parse_datetime", "business_timezone"):
            with (
                self.subTest(target=target),
                patch("minimal_kanban.services.card_service." + target, side_effect=OverflowError),
                self.assertRaises(OverflowError),
            ):
                self.service._repair_order_card_datetime(
                    "0001-01-01T00:00:00+14:00", search_overflow_none=True
                )

    def test_adjacent_dates_keep_normal_conversion(self):
        for value in ("0001-01-02T12:00:00Z", "9999-12-30T12:00:00Z", "2025-01-01T12:00:00Z"):
            with self.subTest(value=value):
                self.assertEqual(
                    self.service._repair_order_card_datetime(value, search_overflow_none=True),
                    self.service._repair_order_card_datetime(value),
                )
