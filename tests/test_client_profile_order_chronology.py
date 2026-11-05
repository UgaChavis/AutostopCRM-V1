"""Profile order slices use actual instants without changing related vehicles."""

from __future__ import annotations

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.storage.json_store import StateFileCorruptedError


class ClientProfileOrderChronologyTests(CardServiceCase):
    def setUp(self):
        appdata = tempfile.TemporaryDirectory(prefix="synthetic-profile-chronology-")
        self.addCleanup(appdata.cleanup)
        self.appdata = Path(appdata.name)
        environment = patch.dict(os.environ, {"APPDATA": appdata.name})
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()
        self.service = self._build_service()
        self.client_id = self.service.create_client({"display_name": "SYNTHETIC"})["client"]["id"]
        self.service.create_card({"title": "SYNTHETIC SEED"})
        self.state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.seed = self.state["cards"][0]

    def install(self, rows):
        state = deepcopy(self.state)
        state["cards"] = []
        for number, updated, created in rows:
            card = deepcopy(self.seed)
            card.update(
                id="synthetic-" + number,
                client_id=self.client_id,
                vehicle="SYNTHETIC " + number,
                updated_at=updated,
                created_at=created,
                repair_order={"number": number, "status": "open", "opened_at": "04.11.2026 10:00"},
            )
            state["cards"].append(card)
        temporary = self.state_file.with_name("synthetic-chronology.tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, self.state_file)

    def files(self):
        roots = {"state": Path(self.temp_dir.name), "appdata": self.appdata}
        return {
            (name, str(path.relative_to(root))): path.read_bytes()
            for name, root in roots.items()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_instant_order_microseconds_ties_and_extremes(self):
        cases = (
            (
                ("OLD", "2026-11-05T01:00:00+14:00", "2026-01-01T00:00:00Z"),
                ("NEW", "2026-11-04T23:00:00-12:00", "2026-01-01T00:00:00Z"),
                "NEW",
            ),
            (
                ("OLD", "2026-01-01T00:00:00", "2026-01-01T00:00:00Z"),
                ("NEW", "2026-01-01T07:00:00.000001+07:00", "2026-01-01T00:00:00Z"),
                "NEW",
            ),
            (
                ("OLD", "2026-01-01T07:00:00+07:00", "2025-01-01T00:00:00Z"),
                ("NEW", "2026-01-01T00:00:00Z", "2025-01-01T00:00:00.000001Z"),
                "NEW",
            ),
            (
                ("A", "2026-01-01T07:00:00+07:00", "2025-01-01T01:00:00+01:00"),
                ("Z", "2026-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
                "Z",
            ),
            (
                ("OLD", "0001-01-01T00:00:00+14:00", "2025-01-01T00:00:00Z"),
                ("NEW", "0001-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
                "NEW",
            ),
            (
                ("OLD", "9999-12-31T23:59:59Z", "2025-01-01T00:00:00Z"),
                ("NEW", "9999-12-31T23:59:59-12:00", "2025-01-01T00:00:00Z"),
                "NEW",
            ),
        )
        for first, second, expected in cases:
            with self.subTest(first=first, second=second):
                self.install([first, second])
                before = self.files()
                for strict in (False, True):
                    for _ in range(2):
                        result = self.service.get_client(
                            {
                                "client_id": self.client_id,
                                "order_limit": 1,
                                "strict_client_links": strict,
                            }
                        )
                        self.assertEqual(result["repair_orders"][0]["number"], expected)
                        self.assertEqual(result["meta"]["repair_orders_total"], 2)
                        self.assertEqual(result["meta"]["repair_orders_returned"], 1)
                self.assertEqual(self.files(), before)

    def test_slice_uses_moments_without_reordering_vehicle_projection(self):
        old = ("OLD", "2026-11-05T01:00:00+14:00", "2026-01-01T00:00:00Z")
        new = ("NEW", "2026-11-04T23:00:00-12:00", "2026-01-01T00:00:00Z")
        self.install([old, new])
        before = self.files()
        result = self.service.get_client({"client_id": self.client_id, "order_limit": 1})
        self.assertEqual(result["repair_orders"][0]["number"], "NEW")
        self.assertEqual(
            [v["vehicle"] for v in result["vehicles"]], ["SYNTHETIC OLD", "SYNTHETIC NEW"]
        )
        self.assertEqual(self.files(), before)

        middle = [
            (f"MID-{i:02d}", f"2026-11-04T20:00:{i:02d}Z", "2026-01-01T00:00:00Z")
            for i in range(29)
        ]
        self.install([old, new, *middle])
        before = self.files()
        for strict in (False, True):
            for limit in (30, 31):
                result = self.service.get_client(
                    {
                        "client_id": self.client_id,
                        "order_limit": limit,
                        "strict_client_links": strict,
                    }
                )
                numbers = [order["number"] for order in result["repair_orders"]]
                self.assertEqual(numbers[0], "NEW")
                self.assertEqual("OLD" in numbers, limit == 31)
                self.assertEqual(result["meta"]["repair_orders_total"], 31)
                self.assertEqual(result["meta"]["repair_orders_returned"], limit)
        self.assertEqual(self.files(), before)

    def test_invalid_raw_timestamp_fails_closed_without_write(self):
        for field in ("created_at", "updated_at"):
            row = ["BAD", "2026-01-01T00:00:00Z", "2025-01-01T00:00:00Z"]
            row[1 if field == "updated_at" else 2] = "not-a-date"
            self.install([row])
            before = self.files()
            for strict in (False, True):
                with (
                    self.subTest(field=field, strict=strict),
                    self.assertRaises(StateFileCorruptedError),
                ):
                    self.service.get_client(
                        {"client_id": self.client_id, "strict_client_links": strict}
                    )
            self.assertEqual(self.files(), before)

    def test_strict_links_exclude_legacy_match_before_order_sort(self):
        self.install(
            [
                ("DIRECT", "2026-11-04T20:00:00Z", "2025-01-01T00:00:00Z"),
                ("LEGACY", "2026-11-05T20:00:00Z", "2025-01-01T00:00:00Z"),
            ]
        )
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        legacy = next(card for card in state["cards"] if card["id"] == "synthetic-LEGACY")
        legacy["client_id"] = ""
        legacy["repair_order"]["client"] = "SYNTHETIC"
        temporary = self.state_file.with_name("synthetic-chronology.tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, self.state_file)
        before = self.files()
        for strict, expected, count in ((False, "LEGACY", 2), (True, "DIRECT", 1)):
            with self.subTest(strict=strict):
                result = self.service.get_client(
                    {"client_id": self.client_id, "order_limit": 1, "strict_client_links": strict}
                )
                self.assertEqual(result["repair_orders"][0]["number"], expected)
                self.assertEqual(result["meta"]["repair_orders_total"], count)
                self.assertIn(
                    "related_card_client_match_unverified", result["meta"]["read_warnings"]
                )
        self.assertEqual(self.files(), before)
