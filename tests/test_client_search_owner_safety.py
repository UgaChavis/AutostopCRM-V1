"""Legacy UI search must not attribute a confirmed owner's cards to another client."""

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import os
import tempfile
from copy import deepcopy
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.storage.json_store import StateFileCorruptedError


class ClientSearchOwnerSafetyTests(CardServiceCase):
    def setUp(self):
        appdata = tempfile.TemporaryDirectory(prefix="synthetic-search-owner-")
        self.addCleanup(appdata.cleanup)
        env = patch.dict(os.environ, {"APPDATA": appdata.name})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()
        self.service = self._build_service()
        self.addCleanup(self.store.close)
        for client_id in ("synthetic-a", "synthetic-b"):
            self.service.create_client({"id": client_id, "display_name": "SYNTHETIC SHARED"})
        self.card_id = self.service.create_card(
            {
                "title": "SYNTHETIC B",
                "client_id": "synthetic-b",
                "vehicle": "RAREVEHICLE SYNTHETIC",
            }
        )["card"]["id"]
        self.service.update_card(
            {
                "card_id": self.card_id,
                "repair_order": {
                    "client": "SYNTHETIC SHARED",
                    "works": [{"name": "SYNTHETIC WORK", "quantity": "1", "price": "100"}],
                },
            }
        )
        self.original = json.loads(self.state_file.read_text(encoding="utf-8"))

    def install(self, state):
        path = self.state_file.with_name("synthetic-swap.tmp")
        path.write_text(json.dumps(state), encoding="utf-8")
        os.replace(path, self.state_file)

    def test_search_and_list_exclude_confirmed_owner_outside_limit(self):
        before = self.state_file.read_bytes()
        listed = self.service.list_clients({"include_stats": True, "limit": 1})
        selected = listed["clients"][0]
        self.assertEqual(selected["id"], "synthetic-a")
        self.assertEqual(selected["vehicles_preview"], [])
        self.assertEqual(selected["stats"]["repair_orders_total"], 0)
        self.assertIn("related_card_client_match_unverified", selected["read_warnings"])
        result = self.service.search_clients({"query": "RAREVEHICLE"})
        self.assertEqual([item["id"] for item in result["clients"]], ["synthetic-b"])
        self.assertEqual(
            result["clients"][0]["vehicles_preview"][0]["vehicle"], "RAREVEHICLE SYNTHETIC"
        )
        no_stats = self.service.list_clients({"include_stats": False, "limit": 1})
        self.assertEqual(no_stats["clients"][0]["vehicles_preview"], [])
        self.assertEqual(before, self.state_file.read_bytes())

    def test_unconfirmed_legacy_links_remain_searchable_and_warn(self):
        for client_id in ("", "SYNTHETIC-MISSING", "CL-SYNTHETI"):
            with self.subTest(client_id=client_id):
                state = deepcopy(self.original)
                next(card for card in state["cards"] if card["id"] == self.card_id)["client_id"] = (
                    client_id
                )
                self.install(state)
                before = self.state_file.read_bytes()
                found = self.service.search_clients({"query": "RAREVEHICLE"})
                selected = next(item for item in found["clients"] if item["id"] == "synthetic-a")
                self.assertEqual(
                    selected["vehicles_preview"][0]["vehicle"], "RAREVEHICLE SYNTHETIC"
                )
                self.assertIn("related_card_client_match_unverified", selected["read_warnings"])
                self.assertEqual(before, self.state_file.read_bytes())

    def test_exact_owner_wins_over_another_clients_short_alias(self):
        state = deepcopy(self.original)
        a = next(client for client in state["clients"] if client["id"] == "synthetic-a")
        b = next(client for client in state["clients"] if client["id"] == "synthetic-b")
        a["id"] = "12345678-0000-0000-0000-000000000001"
        b["id"] = "CL-12345678"
        next(card for card in state["cards"] if card["id"] == self.card_id)["client_id"] = b["id"]
        self.install(state)
        before = self.state_file.read_bytes()
        found = self.service.search_clients({"query": "RAREVEHICLE"})
        self.assertEqual([item["id"] for item in found["clients"]], [b["id"]])
        listed = self.service.list_clients({"include_stats": True})
        selected = next(item for item in listed["clients"] if item["id"] == a["id"])
        self.assertEqual(selected["vehicles_preview"], [])
        self.assertIn("related_card_client_match_unverified", selected["read_warnings"])
        self.assertEqual(before, self.state_file.read_bytes())

    def test_bad_exact_owner_and_link_fail_without_writing(self):
        for scenario in ("duplicate", "invalid_owner", "truncated_link"):
            with self.subTest(scenario=scenario):
                state = deepcopy(self.original)
                owner = next(client for client in state["clients"] if client["id"] == "synthetic-b")
                card = next(card for card in state["cards"] if card["id"] == self.card_id)
                if scenario == "duplicate":
                    state["clients"].append(deepcopy(owner))
                elif scenario == "invalid_owner":
                    owner["phones"] = 123
                else:
                    owner["id"] = "x" * 128
                    card["client_id"] = "x" * 129
                self.install(state)
                before = self.state_file.read_bytes()
                for operation, payload in (
                    (self.service.search_clients, {"query": "RAREVEHICLE"}),
                    (self.service.list_clients, {"include_stats": True, "limit": 1}),
                ):
                    with self.assertRaises(StateFileCorruptedError):
                        operation(payload)
                self.assertEqual(before, self.state_file.read_bytes())

    def test_unselected_client_with_bad_sort_key_fails_without_writing(self):
        for field in ("id", "created_at", "bad_model"):
            with self.subTest(field=field):
                state = deepcopy(self.original)
                owner = next(client for client in state["clients"] if client["id"] == "synthetic-b")
                if field == "bad_model":
                    owner["phones"] = 123
                else:
                    owner.pop(field)
                self.install(state)
                before = self.state_file.read_bytes()
                for operation, payload in (
                    (self.service.search_clients, {"query": "SYNTHETIC SHARED", "limit": 1}),
                    (self.service.list_clients, {"include_stats": True, "limit": 1}),
                ):
                    with self.assertRaises(StateFileCorruptedError):
                        operation(payload)
                if field != "bad_model":
                    with self.assertRaises(StateFileCorruptedError):
                        self.service.list_clients({"include_stats": False, "limit": 1})
                self.assertEqual(before, self.state_file.read_bytes())

    def test_unselected_match_with_bad_update_date_cannot_affect_ranking(self):
        state = deepcopy(self.original)
        owner = next(client for client in state["clients"] if client["id"] == "synthetic-b")
        owner.pop("updated_at")
        self.install(state)
        before = self.state_file.read_bytes()
        with self.assertRaises(StateFileCorruptedError):
            self.service.search_clients({"query": "SYNTHETIC SHARED", "limit": 1})
        self.assertEqual(before, self.state_file.read_bytes())

    def test_duplicate_candidate_before_archive_retention_fails_closed(self):
        state = deepcopy(self.original)
        template = state["cards"][0]

        def card(card_id, *, archived, date, customer):
            item = deepcopy(template)
            item.update(
                id=card_id,
                archived=archived,
                created_at=date,
                updated_at=date,
                client_id="",
                client_vehicle_id="",
                repair_order={},
                vehicle="UNRELATED VEHICLE",
            )
            item["vehicle_profile"]["customer_name"] = customer
            return item

        recent = "2026-10-10T12:00:00+00:00"
        old = "2020-01-01T12:00:00+00:00"
        state["cards"] = [card("duplicate-card", archived=False, date=recent, customer="OTHER")]
        state["cards"].extend(
            card(f"archive-{index}", archived=True, date=recent, customer="OTHER")
            for index in range(300)
        )
        state["cards"].append(
            card("duplicate-card", archived=True, date=old, customer="SYNTHETIC SHARED")
        )
        self.install(state)
        before = self.state_file.read_bytes()
        for operation, payload in (
            (self.service.search_clients, {"query": "SYNTHETIC SHARED"}),
            (self.service.list_clients, {"include_stats": True, "limit": 1}),
        ):
            with self.assertRaises(StateFileCorruptedError):
                operation(payload)
        self.assertEqual(before, self.state_file.read_bytes())

    def test_cached_snapshot_is_detached_and_invalidated_after_change(self):
        first = self.service.search_clients({"query": "RAREVEHICLE"})
        first["clients"][0]["vehicles_preview"][0]["vehicle"] = "MUTATED"
        second = self.service.search_clients({"query": "RAREVEHICLE"})
        self.assertEqual(
            second["clients"][0]["vehicles_preview"][0]["vehicle"], "RAREVEHICLE SYNTHETIC"
        )
        state = deepcopy(self.original)
        next(card for card in state["cards"] if card["id"] == self.card_id)["client_id"] = (
            "synthetic-a"
        )
        self.install(state)
        third = self.service.search_clients({"query": "RAREVEHICLE"})
        self.assertEqual([item["id"] for item in third["clients"]], ["synthetic-a"])

    def test_suggest_reuses_checked_search_and_preserves_legacy(self):
        vin = "JF1SJ5LC5DG012345"
        source = self.service.create_card(
            {"title": "SYNTHETIC SOURCE", "vehicle_profile": {"vin": vin}}
        )["card"]["id"]
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        foreign = next(card for card in state["cards"] if card["id"] == self.card_id)
        foreign["vehicle_profile"]["vin"] = vin
        self.install(state)
        before = self.state_file.read_bytes()
        with patch.object(
            self.service,
            "_client_checked_search_cards",
            wraps=self.service._client_checked_search_cards,
        ) as build:
            self.service.search_clients({"query": "RAREVEHICLE"})
            for payload in ({"query": "RAREVEHICLE"}, {}):
                result = self.service.suggest_clients_for_card({"card_id": source, **payload})
                self.assertEqual([item["id"] for item in result["clients"]], ["synthetic-b"])
                self.assertEqual(result["card"]["id"], source)
                self.assertGreater(result["card"]["events_count"], 0)
            self.assertEqual(build.call_count, 1)
        self.assertEqual(before, self.state_file.read_bytes())
        foreign["client_id"] = ""
        self.install(state)
        result = self.service.suggest_clients_for_card({"card_id": source})
        candidate = next(row for row in result["clients"] if row["id"] == "synthetic-a")
        self.assertIn("related_card_client_match_unverified", candidate["read_warnings"])
        self.assertEqual(candidate["vehicles_preview"][0]["vehicle"], "RAREVEHICLE SYNTHETIC")

    def test_suggest_empty_query_and_invalid_events(self):
        source = self.service.create_card({"title": "SYNTHETIC EMPTY"})["card"]["id"]
        before = self.state_file.read_bytes()
        result = self.service.suggest_clients_for_card({"card_id": source})
        self.assertEqual(result["clients"], [])
        self.assertEqual(result["meta"], {"query": "", "total": 0, "returned": 0})
        self.assertGreater(result["card"]["events_count"], 0)
        self.assertEqual(before, self.state_file.read_bytes())
        state = json.loads(before)
        state["events"] = "SYNTHETIC INVALID"
        self.install(state)
        before = self.state_file.read_bytes()
        with self.assertRaises(StateFileCorruptedError):
            self.service.suggest_clients_for_card({"card_id": source})
        self.assertEqual(before, self.state_file.read_bytes())

    def test_suggest_does_not_truncate_long_derived_query(self):
        source = self.service.create_card({"title": "SYNTHETIC LONG QUERY"})["card"]["id"]
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        card = next(card for card in state["cards"] if card["id"] == source)
        card["vehicle_profile"]["customer_name"] = "A" * 180
        card["repair_order"]["client"] = "B" * 180
        self.install(state)
        before = self.state_file.read_bytes()
        with patch.object(
            self.service, "_rank_client_matches", wraps=self.service._rank_client_matches
        ) as rank:
            result = self.service.suggest_clients_for_card({"card_id": source})
        self.assertGreater(len(result["meta"]["query"]), 240)
        self.assertEqual(rank.call_args.args[1], result["meta"]["query"])
        self.assertEqual(before, self.state_file.read_bytes())

    def test_suggest_rejects_ambiguous_source_before_archive_retention(self):
        state = deepcopy(self.original)
        source = deepcopy(state["cards"][0])
        source["id"] = "synthetic-source"
        duplicate = deepcopy(source)
        duplicate["archived"] = True
        duplicate["updated_at"] = "2020-01-01T12:00:00+00:00"
        state["cards"].extend([source, duplicate])
        self.install(state)
        before = self.state_file.read_bytes()
        with self.assertRaises(StateFileCorruptedError):
            self.service.suggest_clients_for_card({"card_id": source["id"]})
        self.assertEqual(before, self.state_file.read_bytes())
