"""Differential tests against the previous whole-record deepcopy path."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from unittest.mock import patch

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.storage import readonly_context_snapshot as snapshot_module  # noqa: E402
from minimal_kanban.storage.json_store import StateFileCorruptedError  # noqa: E402
from tests.services_case import CardServiceCase  # noqa: E402


def deep_value(depth=500):
    value = []
    for _ in range(depth):
        value = [value]
    return value


def outcome(call):
    try:
        return ("ok", call())
    except Exception as error:
        return ("error", type(error))


class DeepSnapshotCompatibilityTests(CardServiceCase):
    def setUp(self):
        appdata = tempfile.TemporaryDirectory(prefix="deep-snapshot-appdata-")
        self.addCleanup(appdata.cleanup)
        environment = patch.dict(os.environ, {"APPDATA": appdata.name})
        environment.start()
        self.addCleanup(environment.stop)
        snapshot_mode = patch(
            "minimal_kanban.storage.json_store._SNAPSHOT_REQUIRES_FILE_LOCK", False
        )
        snapshot_mode.start()
        self.addCleanup(snapshot_mode.stop)
        super().setUp()
        self.client_id = self.service.create_client(
            {
                "display_name": "Synthetic client",
                "vehicles": [{"vehicle": "Synthetic car"}],
            }
        )["client"]["id"]
        self.card_id = self.service.create_card({"title": "Synthetic card"})["card"]["id"]
        self.base_json = self.state_file.read_text(encoding="utf-8")

    def write_state(self, state):
        replacement = self.state_file.with_suffix(".replacement")
        replacement.write_text(json.dumps(state), encoding="utf-8")
        replacement.replace(self.state_file)

    def clear_snapshot_cache(self):
        self.store._context_snapshot_signature = None
        self.store._context_snapshot_state = None
        self.store._context_snapshots.clear()

    def clients_result(self, *, reference):
        self.clear_snapshot_cache()
        original = snapshot_module._search_clients
        if reference:

            def old_search(rows):
                return original(deepcopy(rows))

            with patch.object(snapshot_module, "_search_clients", old_search):
                return outcome(
                    lambda: self.service.list_clients({"limit": 1, "include_stats": False})
                )
        return outcome(lambda: self.service.list_clients({"limit": 1, "include_stats": False}))

    def client_profile_result(self, *, reference):
        self.clear_snapshot_cache()
        original = self.store._normalize_cards
        if reference:

            def old_cards(state, columns, *, detach_order_history=False, **kwargs):
                return original({"cards": deepcopy(state["cards"])}, columns, **kwargs)

            with patch.object(self.store, "_normalize_cards", old_cards):
                return outcome(lambda: self.service.get_client({"client_id": self.client_id}))
        return outcome(lambda: self.service.get_client({"client_id": self.client_id}))

    def test_deep_client_fields_match_whole_clients_copy(self):
        for field in ("phones", "comment", "vehicle_notes"):
            for invalid_identity in (False, True):
                with self.subTest(field=field, invalid_identity=invalid_identity):
                    state = json.loads(self.base_json)
                    client = next(item for item in state["clients"] if item["id"] == self.client_id)
                    if field == "phones":
                        client["phones"] = [deep_value()]
                    elif field == "comment":
                        client["comment"] = deep_value()
                    else:
                        client["vehicles"][0]["notes"] = deep_value()
                    if invalid_identity:
                        client["id"] = " " + self.client_id + " "
                    self.write_state(state)
                    before = self.state_file.read_bytes()
                    expected = self.clients_result(reference=True)
                    actual = self.clients_result(reference=False)
                    if invalid_identity:
                        self.assertEqual(expected, ("error", StateFileCorruptedError))
                    else:
                        self.assertEqual(expected, ("error", RecursionError))
                    self.assertEqual(actual, expected)
                    self.assertEqual(self.state_file.read_bytes(), before)

    def test_deep_card_fields_match_whole_cards_copy_with_guards(self):
        for field in ("title", "vehicle_raw_input", "order_note"):
            for corruption in ("none", "identity", "finance", "identity_and_finance"):
                with self.subTest(field=field, corruption=corruption):
                    state = json.loads(self.base_json)
                    card = next(item for item in state["cards"] if item["id"] == self.card_id)
                    card["client_id"] = self.client_id
                    card["repair_order"]["number"] = "synthetic-order"
                    if field == "title":
                        card["title"] = deep_value()
                    elif field == "vehicle_raw_input":
                        card["vehicle_profile"]["raw_input_text"] = deep_value()
                    else:
                        card["repair_order"]["note"] = deep_value()
                    if corruption in ("identity", "identity_and_finance"):
                        card["client_id"] = " " + self.client_id + " "
                    if corruption in ("finance", "identity_and_finance"):
                        card["repair_order"]["payments"] = [
                            {"id": "synthetic-payment", "amount": "not-a-number"}
                        ]
                    self.write_state(state)
                    before = self.state_file.read_bytes()
                    expected = self.client_profile_result(reference=True)
                    actual = self.client_profile_result(reference=False)
                    self.assertEqual(expected, ("error", RecursionError))
                    self.assertEqual(actual, expected)
                    self.assertEqual(self.state_file.read_bytes(), before)
