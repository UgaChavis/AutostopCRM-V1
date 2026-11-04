"""Cashbox identity normalization must not discard ledger parents."""

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import os
import tempfile
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.storage.json_store import StateFileCorruptedError


class RawCashboxIdentityTests(CardServiceCase):
    def setUp(self):
        app_data = tempfile.TemporaryDirectory(prefix="synthetic-cashbox-raw-")
        self.addCleanup(app_data.cleanup)
        environment = patch.dict(os.environ, {"APPDATA": app_data.name})
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()
        self.addCleanup(self.store.close)
        self.service = self._build_service()
        self.box = self.service.create_cashbox({"name": "Synthetic one"})["cashbox"]["id"]
        self.service.create_cash_transaction(
            {"cashbox_id": self.box, "amount_minor": 100, "note": "Synthetic"}
        )
        self.original = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.backup = self.state_file.with_suffix(".corrupted.json")
        self.backup.write_bytes(b"synthetic backup")

    def replace_state(self, state):
        path = self.state_file.with_name("synthetic-replacement.tmp")
        path.write_text(json.dumps(state), encoding="utf-8")
        os.replace(path, self.state_file)

    def protected_files(self):
        paths = [
            self.state_file,
            self.backup,
            *self.state_file.parent.glob("*.sqlite3"),
            *self.state_file.parent.glob("*.sqlite3-wal"),
        ]
        return {
            str(path): (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes())
            for path in paths
            if path.exists()
        }

    def test_raw_invalid_identity_does_not_discard_cashboxes(self):
        for case in (
            "same_id",
            "space_id",
            "id_truncated",
            "same_name",
            "name_truncated",
            "missing_name",
            "missing_id",
        ):
            with self.subTest(case=case):
                state = deepcopy(self.original)
                first = state["cashboxes"][0]
                second = dict(first, id="synthetic-second", name="Synthetic two", order=1)
                if case == "same_id":
                    second["id"] = first["id"]
                elif case == "space_id":
                    second["id"] = " " + first["id"] + " "
                elif case == "id_truncated":
                    first["id"], second["id"] = "x" * 128 + "A", "x" * 128 + "B"
                elif case == "same_name":
                    second["name"] = "  SYNTHETIC ONE  "
                elif case == "name_truncated":
                    first["name"], second["name"] = "x" * 80 + "A", "x" * 80 + "B"
                elif case == "missing_name":
                    second.pop("name")
                else:
                    second.pop("id")
                state["cashboxes"].append(second)
                self.replace_state(state)
                before = self.protected_files()
                with self.assertRaises(StateFileCorruptedError):
                    self.store.read_bundle()
                self.assertEqual(self.protected_files(), before)

    def test_distinct_cashbox_ids_and_names_remain_valid(self):
        state = deepcopy(self.original)
        state["cashboxes"][0]["id"] = "Case-id"
        state["cash_transactions"][0]["cashbox_id"] = "Case-id"
        state["cashboxes"].append(
            dict(state["cashboxes"][0], id="case-id", name="Synthetic two", order=1)
        )
        self.replace_state(state)
        bundle = self.store.read_bundle()
        self.assertEqual({box.id for box in bundle["cashboxes"]}, {"Case-id", "case-id"})

    def test_unique_name_length_is_checked_before_raw_normalization(self):
        state = deepcopy(self.original)
        state["cashboxes"][0]["name"] = "X" * 80
        self.replace_state(state)
        self.assertEqual(self.store.read_bundle()["cashboxes"][0].name, "X" * 80)
        state["cashboxes"][0]["name"] += "Y"
        self.replace_state(state)
        before = self.protected_files()
        with self.assertRaises(StateFileCorruptedError):
            self.store.read_bundle()
        self.assertEqual(self.protected_files(), before)

    def test_unique_name_length_is_checked_on_both_write_paths(self):
        for cached in (False, True):
            with self.subTest(cached=cached):
                source, signature = self.store.read_bundle_with_signature()
                payload = {**source, "cashboxes": [replace(source["cashboxes"][0], name="X" * 80)]}
                if cached:
                    self.store.write_cached_bundle(source, **payload, expected_signature=signature)
                else:
                    self.store.write_bundle(**payload, expected_signature=signature)
                source, signature = self.store.read_bundle_with_signature()
                self.assertEqual(source["cashboxes"][0].name, "X" * 80)
                payload = {**source, "cashboxes": [replace(source["cashboxes"][0], name="X" * 81)]}
                before = self.protected_files()
                with self.assertRaises(ValueError):
                    if cached:
                        self.store.write_cached_bundle(
                            source, **payload, expected_signature=signature
                        )
                    else:
                        self.store.write_bundle(**payload, expected_signature=signature)
                self.assertEqual(self.protected_files(), before)

    def test_write_paths_reject_cashbox_collisions_and_missing_identity(self):
        for case in ("same_id", "space_id", "same_name", "missing_name", "missing_id"):
            for cached in (False, True):
                with self.subTest(case=case, cached=cached):
                    source, signature = self.store.read_bundle_with_signature()
                    first = source["cashboxes"][0]
                    second = replace(first, id="synthetic-second", name="Synthetic two", order=1)
                    if case == "same_id":
                        second.id = first.id
                    elif case == "space_id":
                        second.id = " " + first.id + " "
                    elif case == "same_name":
                        second.name = "  SYNTHETIC ONE  "
                    elif case == "missing_name":
                        second.name = ""
                    else:
                        second.id = ""
                    payload = {**source, "cashboxes": [first, second]}
                    before = self.protected_files()
                    with self.assertRaises(ValueError):
                        if cached:
                            self.store.write_cached_bundle(
                                source, **payload, expected_signature=signature
                            )
                        else:
                            self.store.write_bundle(**payload, expected_signature=signature)
                    self.assertEqual(self.protected_files(), before)
