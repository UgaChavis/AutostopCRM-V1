from __future__ import annotations

# The fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.models import short_entity_id
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.json_store import StateFileCorruptedError


class ClientContextReadSafetyTests(CardServiceCase):
    def setUp(self):
        self.app_data = tempfile.TemporaryDirectory(prefix="m2-client-context-appdata-")
        self.addCleanup(self.app_data.cleanup)
        environment = patch.dict(os.environ, {"APPDATA": self.app_data.name})
        environment.start()
        self.addCleanup(environment.stop)
        super().setUp()
        self.service = self._build_service()
        client = self.service.create_client(
            {
                "display_name": "SYNTHETIC_CLIENT",
                "vehicles": [{"vehicle": "SYNTHETIC_VEHICLE"}],
            }
        )["client"]
        self.client_id = client["id"]
        self.vehicle_id = client["vehicles"][0]["id"]
        self.card_ids = []
        for index in range(3):
            card = self.service.create_card(
                {
                    "title": f"SYNTHETIC_ORDER_{index}",
                    "client_id": self.client_id,
                    "client_vehicle_id": self.vehicle_id,
                }
            )["card"]
            self.card_ids.append(card["id"])
            self.service.update_card(
                {
                    "card_id": card["id"],
                    "repair_order": {
                        "client": "SYNTHETIC_CLIENT",
                        "works": [
                            {
                                "name": "SYNTHETIC_WORK",
                                "quantity": "1",
                                "price": str((index + 1) * 100),
                            }
                        ],
                    },
                }
            )

        # Put two derived files into states that the old read would rewrite.
        cards = {card.id: card for card in self.store.read_bundle()["cards"]}
        self.newest_path = self.service._repair_order_text_path(cards[self.card_ids[-1]])
        oldest_path = self.service._repair_order_text_path(cards[self.card_ids[0]])
        self.newest_path.unlink(missing_ok=True)
        oldest_path.write_text("SYNTHETIC_STALE_ARTIFACT", encoding="utf-8")
        (oldest_path.parent / "unrelated.txt").write_text("SYNTHETIC_UNRELATED", encoding="utf-8")

    def snapshot(self):
        result = {}
        for label, root in (
            ("state", Path(self.temp_dir.name)),
            ("appdata", Path(self.app_data.name)),
        ):
            for path in root.rglob("*"):
                stat = path.stat()
                key = (label, str(path.relative_to(root)))
                metadata = (stat.st_ino, stat.st_mode, stat.st_mtime_ns, stat.st_ctime_ns)
                if path.is_file():
                    result[key] = ("file", metadata, path.read_bytes())
                elif path.is_dir():
                    result[key] = ("directory", metadata)
        return result

    def edit_state(self, update):
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        update(state)
        self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def selected_client(self, state):
        return next(client for client in state["clients"] if client["id"] == self.client_id)

    def test_profile_preserves_order_limit_shape_totals_links_and_all_files(self):
        before = self.snapshot()
        self.assertFalse(self.newest_path.exists())
        for limit in (1, 3, 1):
            with self.subTest(order_limit=limit):
                result = self.service.get_client(
                    {"client_id": self.client_id, "order_limit": limit}
                )
                self.assertEqual(set(result), {"client", "vehicles", "repair_orders", "meta"})
                self.assertEqual(result["client"]["id"], self.client_id)
                self.assertEqual(result["client"]["display_name"], "SYNTHETIC_CLIENT")
                self.assertEqual(result["client"]["stats"]["repair_orders_total"], 3)
                self.assertTrue(
                    any(vehicle["id"] == self.vehicle_id for vehicle in result["vehicles"])
                )
                self.assertEqual(
                    [order["card_id"] for order in result["repair_orders"]],
                    list(reversed(self.card_ids))[:limit],
                )
                self.assertEqual(
                    [order["grand_total"] for order in result["repair_orders"]],
                    ["300", "200", "100"][:limit],
                )
                self.assertEqual(result["meta"]["repair_orders_total"], 3)
                self.assertEqual(result["meta"]["repair_orders_returned"], limit)
                self.assertEqual(result["meta"]["order_limit"], limit)
                self.assertEqual(result["repair_orders"][0]["file_path"], str(self.newest_path))
                self.assertFalse(self.newest_path.exists())
                self.assertTrue(before == self.snapshot(), "Profile read changed persisted files")

    def test_legacy_unnormalized_state_is_not_repaired_by_client_read(self):
        self.edit_state(lambda state: state.update({"settings": "SYNTHETIC_LEGACY"}))
        before = self.snapshot()
        for _ in range(2):
            result = self.service.get_client({"client_id": self.client_id, "order_limit": 1})
            self.assertEqual(result["client"]["id"], self.client_id)
            self.assertEqual(result["meta"]["repair_orders_total"], 3)
            self.assertTrue(before == self.snapshot(), "Profile read changed persisted files")

    def test_unknown_client_does_not_repair_legacy_state_or_change_feed(self):
        self.edit_state(lambda state: state.update({"settings": "SYNTHETIC_LEGACY"}))
        before = self.snapshot()
        for _ in range(2):
            with self.assertRaises(ServiceError) as raised:
                self.service.get_client({"client_id": "SYNTHETIC_UNKNOWN_CLIENT"})
            self.assertEqual(raised.exception.code, "not_found")
            self.assertTrue(before == self.snapshot(), "Profile read changed persisted files")

    def test_missing_selected_client_id_does_not_invent_or_persist_identity(self):
        self.edit_state(lambda state: self.selected_client(state).update({"id": ""}))
        before = self.snapshot()
        for _ in range(2):
            with self.assertRaises((StateFileCorruptedError, ServiceError)):
                self.service.get_client({"client_id": self.client_id})
            self.assertTrue(before == self.snapshot(), "Profile read changed persisted files")

    def test_missing_selected_client_timestamps_fail_without_invented_revisions(self):
        original = json.loads(self.state_file.read_text(encoding="utf-8"))
        for field in ("created_at", "updated_at"):
            with self.subTest(field=field):
                state = deepcopy(original)
                self.selected_client(state)[field] = ""
                self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                before = self.snapshot()
                for _ in range(2):
                    with self.assertRaises((StateFileCorruptedError, ServiceError)):
                        self.service.get_client({"client_id": self.client_id})
                    self.assertTrue(
                        before == self.snapshot(), "Profile read changed persisted files"
                    )

    def test_missing_stored_vehicle_id_fails_without_invented_identity(self):
        self.edit_state(lambda state: self.selected_client(state)["vehicles"][0].update({"id": ""}))
        before = self.snapshot()
        for _ in range(2):
            with self.assertRaises((StateFileCorruptedError, ServiceError)):
                self.service.get_client({"client_id": self.client_id})
            self.assertTrue(before == self.snapshot(), "Profile read changed persisted files")

    def test_duplicate_or_invalid_selected_client_fails_without_writes(self):
        original = json.loads(self.state_file.read_text(encoding="utf-8"))
        for scenario in ("duplicate_client", "duplicate_vehicle", "invalid_phones"):
            with self.subTest(scenario=scenario):
                state = deepcopy(original)
                client = self.selected_client(state)
                if scenario == "duplicate_client":
                    state["clients"].append(deepcopy(client))
                elif scenario == "duplicate_vehicle":
                    client["vehicles"].append(deepcopy(client["vehicles"][0]))
                else:
                    client["phones"] = 3
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                with self.assertRaises((StateFileCorruptedError, ServiceError)):
                    self.service.get_client({"client_id": self.client_id})
                self.assertEqual(before, self.snapshot())

    def test_stored_legacy_vehicle_id_alias_is_stable_without_persisting_it(self):
        original = json.loads(self.state_file.read_text(encoding="utf-8"))
        for include_empty_id in (False, True):
            with self.subTest(include_empty_id=include_empty_id):
                state = deepcopy(original)
                vehicle = self.selected_client(state)["vehicles"][0]
                vehicle["client_vehicle_id"] = vehicle.pop("id")
                if include_empty_id:
                    vehicle["id"] = ""
                self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                before = self.snapshot()
                for _ in range(2):
                    result = self.service.get_client(
                        {"client_id": self.client_id, "order_limit": 1}
                    )
                    self.assertEqual(result["client"]["vehicles"][0]["id"], self.vehicle_id)
                    self.assertTrue(
                        any(item["id"] == self.vehicle_id for item in result["vehicles"])
                    )
                    self.assertEqual(result["repair_orders"][0]["card_id"], self.card_ids[-1])
                    self.assertTrue(
                        before == self.snapshot(), "Profile read changed persisted files"
                    )

    def test_missing_related_card_identity_or_timestamps_do_not_invent_links(self):
        original = json.loads(self.state_file.read_text(encoding="utf-8"))
        for field in ("id", "created_at", "updated_at"):
            with self.subTest(field=field):
                state = deepcopy(original)
                card = next(card for card in state["cards"] if card["id"] == self.card_ids[0])
                card[field] = ""
                self.state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                before = self.snapshot()
                for _ in range(2):
                    with self.assertRaises((StateFileCorruptedError, ServiceError)):
                        self.service.get_client({"client_id": self.client_id})
                    self.assertTrue(
                        before == self.snapshot(), "Profile read changed persisted files"
                    )

    def test_related_legacy_payment_is_signalled_and_output_is_detached(self):
        def update(state):
            card = next(card for card in state["cards"] if card["id"] == self.card_ids[0])
            card["repair_order"]["payments"] = [{"amount": "7"}]

        self.edit_state(update)
        before = self.snapshot()
        first = self.service.get_client({"client_id": self.client_id, "order_limit": 3})
        self.assertIn("repair_order_payment_identity_unverified", first["meta"]["read_warnings"])
        first["client"]["vehicles"][0]["vehicle"] = "MUTATED"
        first["repair_orders"][0]["payments"].append({"id": "MUTATED"})
        second = self.service.get_client({"client_id": self.client_id, "order_limit": 3})
        self.assertEqual(second["client"]["vehicles"][0]["vehicle"], "SYNTHETIC_VEHICLE")
        self.assertFalse(
            any(item.get("id") == "MUTATED" for item in second["repair_orders"][0]["payments"])
        )
        self.assertEqual(before, self.snapshot())

    def test_related_card_with_truncated_link_fails_closed(self):
        state = json.loads(self.state_file.read_text())
        client = self.selected_client(state)
        client["id"] = "x" * 128
        card = next(card for card in state["cards"] if card["id"] == self.card_ids[0])
        card["client_id"] = "x" * 128 + "A"
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(StateFileCorruptedError):
            self.service.get_client({"client_id": "x" * 128})
        self.assertEqual(before, self.snapshot())

    def test_text_match_without_exact_client_link_is_marked_unverified(self):
        for unrelated_id in ("", "SYNTHETIC_DIFFERENT_CLIENT"):
            with self.subTest(client_id=unrelated_id):
                self.edit_state(
                    lambda state: next(
                        card for card in state["cards"] if card["id"] == self.card_ids[0]
                    ).update({"client_id": unrelated_id})
                )
                before = self.snapshot()
                result = self.service.get_client({"client_id": self.client_id})
                self.assertIn(self.card_ids[0], [row["card_id"] for row in result["repair_orders"]])
                self.assertIn(
                    "related_card_client_match_unverified", result["meta"]["read_warnings"]
                )
                self.assertEqual(before, self.snapshot())

    def test_profile_excludes_confirmed_other_owner_but_keeps_unverified_links(self):
        original = json.loads(self.state_file.read_text(encoding="utf-8"))
        for scenario in ("exact", "empty", "unknown", "short", "exact_over_short"):
            with self.subTest(scenario=scenario):
                state = deepcopy(original)
                selected = self.selected_client(state)
                selected_id = self.client_id
                other_id = "12345678-0000-0000-0000-000000000002"
                if scenario == "exact_over_short":
                    selected_id = "12345678-0000-0000-0000-000000000001"
                    selected["id"] = selected_id
                    for card in state["cards"]:
                        if card["client_id"] == self.client_id:
                            card["client_id"] = selected_id
                    other_id = "CL-12345678"
                other = deepcopy(selected)
                other["id"] = other_id
                state["clients"].append(other)
                card = next(card for card in state["cards"] if card["id"] == self.card_ids[0])
                card["client_id"] = {
                    "empty": "",
                    "unknown": "SYNTHETIC-MISSING",
                    "short": "CL-12345678",
                }.get(scenario, other_id)
                card["repair_order"]["vehicle"] = "SYNTHETIC FOREIGN VEHICLE"
                card["vehicle"] = "SYNTHETIC FOREIGN VEHICLE"
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.state_file.read_bytes()
                profile = self.service.get_client({"client_id": selected_id})
                stats = self.service.get_client_stats({"client_id": selected_id})
                excluded = scenario in ("exact", "exact_over_short")
                orders = profile["repair_orders"]
                self.assertEqual(
                    self.card_ids[0] not in [row["card_id"] for row in orders], excluded
                )
                self.assertEqual(profile["meta"]["repair_orders_total"], 2 if excluded else 3)
                self.assertEqual(stats["stats"]["repair_orders_total"], 2 if excluded else 3)
                self.assertEqual(
                    sum(int(row["due_total"]) for row in orders), 500 if excluded else 600
                )
                self.assertEqual(
                    any(
                        vehicle["vehicle"] == "SYNTHETIC FOREIGN VEHICLE"
                        for vehicle in profile["vehicles"]
                    ),
                    not excluded,
                )
                self.assertIn(
                    "related_card_client_match_unverified", profile["meta"].get("read_warnings", [])
                )
                strict = self.service.get_client(
                    {"client_id": selected_id, "strict_client_links": True}
                )
                self.assertNotIn(
                    self.card_ids[0], [row["card_id"] for row in strict["repair_orders"]]
                )
                self.assertEqual(strict["meta"]["repair_orders_total"], 2)
                self.assertIn(
                    "related_card_client_match_unverified", strict["meta"].get("read_warnings", [])
                )
                self.assertEqual(before, self.state_file.read_bytes())

    def test_profile_rejects_damaged_confirmed_owner_or_raw_card_link(self):
        original = json.loads(self.state_file.read_text(encoding="utf-8"))
        for scenario in (
            "duplicate",
            "bad_model",
            "bad_date",
            "bad_vehicle_id",
            "truncated_card_link",
        ):
            with self.subTest(scenario=scenario):
                state = deepcopy(original)
                other = deepcopy(self.selected_client(state))
                other["id"] = "x" * 128 if scenario == "truncated_card_link" else "SYNTHETIC-OTHER"
                if scenario == "bad_model":
                    other["phones"] = 123
                elif scenario == "bad_date":
                    other["created_at"] = ""
                elif scenario == "bad_vehicle_id":
                    other["vehicles"][0]["id"] = "x" * 129
                state["clients"].append(other)
                if scenario == "duplicate":
                    state["clients"].append(deepcopy(other))
                card = next(card for card in state["cards"] if card["id"] == self.card_ids[0])
                card["client_id"] = other["id"] + ("A" if scenario == "truncated_card_link" else "")
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.state_file.read_bytes()
                for operation in (self.service.get_client, self.service.get_client_stats):
                    with self.assertRaises(StateFileCorruptedError):
                        operation({"client_id": self.client_id})
                strict = self.service.get_client(
                    {"client_id": self.client_id, "strict_client_links": True}
                )
                self.assertNotIn(
                    self.card_ids[0], [row["card_id"] for row in strict["repair_orders"]]
                )
                self.assertIn(
                    "related_card_client_match_unverified", strict["meta"].get("read_warnings", [])
                )
                self.assertEqual(before, self.state_file.read_bytes())

    def test_profile_checks_each_other_owner_and_preserves_warnings(self):
        original = json.loads(self.state_file.read_text(encoding="utf-8"))
        for bad_second in (False, True):
            with self.subTest(bad_second=bad_second):
                state = deepcopy(original)
                for index in range(2):
                    other = deepcopy(self.selected_client(state))
                    other["id"] = f"SYNTHETIC-OTHER-{index}"
                    if index == 1 and bad_second:
                        other["created_at"] = ""
                    state["clients"].append(other)
                for index, card_id in enumerate(self.card_ids):
                    card = next(row for row in state["cards"] if row["id"] == card_id)
                    card["client_id"] = f"SYNTHETIC-OTHER-{1 if index == 2 else 0}"
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.state_file.read_bytes()
                for operation in (self.service.get_client, self.service.get_client_stats):
                    if bad_second:
                        with self.assertRaises(StateFileCorruptedError):
                            operation({"client_id": self.client_id})
                    else:
                        result = operation({"client_id": self.client_id})
                        stats = result.get("stats", result["client"]["stats"])
                        self.assertEqual(stats["repair_orders_total"], 0)
                        self.assertIn(
                            "related_card_client_match_unverified", result["meta"]["read_warnings"]
                        )
                self.assertEqual(before, self.state_file.read_bytes())

    def test_client_stats_preserves_legacy_shape_and_short_id_without_writes(self):
        bundle = self.store.read_bundle()
        client = self.service._find_client(bundle["clients"], self.client_id)
        expected = {
            "client": self.service._serialize_client(
                client, bundle["cards"], include_stats=True, compact=True
            ),
            "stats": self.service._client_stats(client, bundle["cards"]),
        }
        before = self.snapshot()
        result = self.service.get_client_stats(
            {"client_id": short_entity_id(self.client_id, prefix="CL")}
        )
        self.assertEqual(result, expected)
        result["stats"]["cards_total"] = -1
        self.assertEqual(result["client"]["stats"], expected["stats"])
        result["client"]["vehicles_preview"][0]["vehicle"] = "SYNTHETIC_MUTATION"
        self.assertEqual(
            self.service.get_client_stats({"client_id": self.client_id})["client"][
                "vehicles_preview"
            ][0]["vehicle"],
            "SYNTHETIC_VEHICLE",
        )
        self.assertEqual(before, self.snapshot())

    def test_client_stats_does_not_repair_legacy_or_unrelated_corruption(self):
        self.edit_state(
            lambda state: (
                state["cards"][0].update({"position": -123}),
                state.update({"settings": "SYNTHETIC_LEGACY"}),
            )
        )
        before = self.snapshot()
        result = self.service.get_client_stats({"client_id": self.client_id})
        self.assertEqual(result["stats"]["repair_orders_total"], 3)
        self.assertEqual(before, self.snapshot())

    def test_client_stats_related_validation_and_warnings(self):
        original = json.loads(self.state_file.read_text(encoding="utf-8"))
        for scenario in (
            "duplicate_client",
            "duplicate_card",
            "missing_client_version",
            "missing_card_version",
            "invalid_amount",
        ):
            with self.subTest(scenario=scenario):
                state = deepcopy(original)
                client = self.selected_client(state)
                card = next(item for item in state["cards"] if item["id"] == self.card_ids[0])
                if scenario == "duplicate_client":
                    state["clients"].append(deepcopy(client))
                elif scenario == "duplicate_card":
                    state["cards"].append(deepcopy(card))
                elif scenario == "missing_client_version":
                    client["updated_at"] = ""
                elif scenario == "missing_card_version":
                    card["updated_at"] = ""
                else:
                    card["repair_order"]["works"][0]["price"] = {"synthetic": "invalid"}
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                with self.assertRaises((StateFileCorruptedError, ServiceError)):
                    self.service.get_client_stats({"client_id": self.client_id})
                self.assertEqual(before, self.snapshot())

        state = deepcopy(original)
        card = next(item for item in state["cards"] if item["id"] == self.card_ids[0])
        card["client_id"] = ""
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.snapshot()
        result = self.service.get_client_stats({"client_id": self.client_id})
        self.assertEqual(result["stats"]["cards_total"], 3)
        self.assertIn("related_card_client_match_unverified", result["meta"]["read_warnings"])
        self.assertEqual(before, self.snapshot())

    def test_client_stats_preserves_legacy_vehicle_id_alias(self):
        def use_alias(state):
            vehicle = self.selected_client(state)["vehicles"][0]
            vehicle["client_vehicle_id"] = vehicle.pop("id")

        self.edit_state(use_alias)
        before = self.snapshot()
        result = self.service.get_client_stats({"client_id": self.client_id})
        self.assertEqual(result["client"]["vehicles"][0]["id"], self.vehicle_id)
        self.assertEqual(before, self.snapshot())

    def test_compact_client_list_matches_legacy_shape_and_is_detached(self):
        self.service.create_client(
            {
                "client_type": "company",
                "display_name": "SYNTHETIC ORG",
                "vehicles": [{"vehicle": "SYNTHETIC ORG CAR"}],
            }
        )
        bundle = self.store.read_bundle()
        clients = self.service._ordered_clients(bundle["clients"])
        before = self.snapshot()
        for limit in (1, 2, 35):
            with self.subTest(limit=limit):
                expected = {
                    "clients": [
                        self.service._serialize_client(
                            client,
                            bundle["cards"],
                            include_stats=False,
                            compact=True,
                            include_vehicle_preview=False,
                        )
                        for client in clients[:limit]
                    ],
                    "meta": {
                        "total": len(clients),
                        "returned": min(len(clients), limit),
                        "has_more": len(clients) > limit,
                        "include_stats": False,
                    },
                }
                result = self.service.list_clients({"include_stats": False, "limit": limit})
                self.assertEqual(result, expected)
        result["clients"][0]["vehicles_preview"][0]["vehicle"] = "SYNTHETIC_MUTATION"
        self.assertNotEqual(
            self.service.list_clients({"include_stats": False, "limit": 35})["clients"][0][
                "vehicles_preview"
            ][0]["vehicle"],
            "SYNTHETIC_MUTATION",
        )
        self.assertEqual(before, self.snapshot())

    def test_compact_client_list_ignores_unrelated_damage_without_repair(self):
        self.edit_state(
            lambda state: (
                state["cards"][0].update({"position": -123}),
                state.update({"settings": "SYNTHETIC_LEGACY"}),
            )
        )
        before = self.snapshot()
        result = self.service.list_clients({"include_stats": False, "limit": 35})
        self.assertEqual(result["meta"]["total"], 1)
        self.assertEqual(before, self.snapshot())

    def test_compact_client_list_rejects_invalid_identities_without_repair(self):
        original = json.loads(self.state_file.read_text(encoding="utf-8"))
        for scenario in (
            "duplicate_client",
            "missing_client_id",
            "duplicate_unselected",
            "missing_unselected",
            "invalid_phones",
            "missing_version",
            "duplicate_vehicle",
        ):
            with self.subTest(scenario=scenario):
                state = deepcopy(original)
                client = self.selected_client(state)
                if scenario == "duplicate_client":
                    state["clients"].append(deepcopy(client))
                elif scenario == "missing_client_id":
                    client["id"] = ""
                elif scenario in ("duplicate_unselected", "missing_unselected"):
                    other = deepcopy(client)
                    other.update(
                        {
                            "id": "synthetic-other" if scenario == "duplicate_unselected" else "",
                            "display_name": "ZZZ SYNTHETIC",
                        }
                    )
                    state["clients"].append(other)
                    if scenario == "duplicate_unselected":
                        state["clients"].append(deepcopy(other))
                elif scenario == "invalid_phones":
                    client["phones"] = 42
                elif scenario == "missing_version":
                    client["updated_at"] = ""
                else:
                    client["vehicles"].append(deepcopy(client["vehicles"][0]))
                self.state_file.write_text(json.dumps(state), encoding="utf-8")
                before = self.snapshot()
                with self.assertRaises(StateFileCorruptedError):
                    self.service.list_clients(
                        {
                            "include_stats": False,
                            "limit": 1 if scenario.endswith("unselected") else 35,
                        }
                    )
                self.assertEqual(before, self.snapshot())

    def test_compact_client_list_invalidates_snapshot_after_file_replace(self):
        first = self.service.list_clients({"include_stats": False})
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.selected_client(state)["display_name"] = "SYNTHETIC REPLACED"
        replacement = self.state_file.with_suffix(".synthetic-replacement")
        replacement.write_text(json.dumps(state), encoding="utf-8")
        replacement.replace(self.state_file)
        before = self.snapshot()
        second = self.service.list_clients({"include_stats": False})
        self.assertNotEqual(
            first["clients"][0]["display_name"], second["clients"][0]["display_name"]
        )
        self.assertEqual(second["clients"][0]["display_name"], "SYNTHETIC REPLACED")
        self.assertEqual(before, self.snapshot())

    def test_compact_client_list_checks_sort_dates_beyond_limit(self):
        def add_unreliable_sort_key(state):
            other = deepcopy(self.selected_client(state))
            other["id"] = "SYNTHETIC_OTHER_CLIENT"
            other["created_at"] = ""
            state["clients"].append(other)

        self.edit_state(add_unreliable_sort_key)
        before = self.snapshot()
        with self.assertRaises(StateFileCorruptedError):
            self.service.list_clients({"include_stats": False, "limit": 1})
        self.assertEqual(before, self.snapshot())

    def test_compact_client_list_preserves_legacy_vehicle_id_alias(self):
        def use_alias(state):
            vehicle = self.selected_client(state)["vehicles"][0]
            vehicle["client_vehicle_id"] = vehicle.pop("id")

        self.edit_state(use_alias)
        before = self.snapshot()
        result = self.service.list_clients({"include_stats": False})
        self.assertEqual(result["clients"][0]["vehicles"][0]["id"], self.vehicle_id)
        self.assertEqual(before, self.snapshot())
