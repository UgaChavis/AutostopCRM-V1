from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import json
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.storage import json_store, readonly_context_snapshot


class ClientSearchIndexingTests(CardServiceCase):
    def test_client_search_reuses_related_cards_for_selected_results(self) -> None:
        client = self.service.create_client(
            {
                "display_name": "Оптимизация поиска",
                "vehicles": [
                    {
                        "vehicle": "Toyota Prado",
                        "vin": "JTEBU3FJX05027767",
                        "license_plate": "О777ОО124",
                    }
                ],
            }
        )["client"]
        self.service.create_card(
            {
                "title": "Связанная история",
                "vehicle": "Toyota Prado",
                "vehicle_profile": {
                    "customer_name": "Оптимизация поиска",
                    "vin": "JTEBU3FJX05027767",
                    "registration_plate": "О777ОО124",
                },
                "deadline": {"hours": 1},
            }
        )

        with patch.object(
            self.service,
            "_client_related_cards",
            wraps=self.service._client_related_cards,
        ) as related_cards:
            search = self.service.search_clients({"query": "Toyota Prado", "limit": 5})

        self.assertEqual(search["clients"][0]["id"], client["id"])
        self.assertEqual(related_cards.call_count, 0)

    def test_client_search_builds_related_vehicle_index_once_on_miss(self) -> None:
        self.service.create_client({"display_name": "Клиент без совпадения"})["client"]
        self.service.create_card(
            {
                "title": "История без совпадения",
                "vehicle": "Toyota Corolla",
                "description": "Проверка промаха поиска",
                "deadline": {"hours": 1},
                "vehicle_profile": {"vin": "NOMATCH0000000001"},
            }
        )

        with patch.object(
            self.service,
            "_client_checked_search_cards",
            wraps=self.service._client_checked_search_cards,
        ) as related_index:
            search = self.service.search_clients({"query": "ZZZ-UNKNOWN-999", "limit": 5})

        self.assertEqual(search["clients"], [])
        self.assertEqual(related_index.call_count, 1)

    def test_client_search_reuses_related_vehicle_index_between_queries(self) -> None:
        client = self.service.create_client({"display_name": "Клиент с кэшем поиска"})["client"]
        self.service.create_card(
            {
                "title": "История для кэша поиска",
                "vehicle": "Nissan Note",
                "description": "Проверка повторного поиска",
                "deadline": {"hours": 1},
                "client_id": client["id"],
                "vehicle_profile": {
                    "vin": "SJNFAAE11U0123456",
                    "registration_plate": "К456КК124",
                },
            }
        )

        with patch.object(
            self.service,
            "_client_checked_search_cards",
            wraps=self.service._client_checked_search_cards,
        ) as related_index:
            first = self.service.search_clients({"query": "SJNFAAE11U0123456", "limit": 5})
            second = self.service.search_clients({"query": "К456КК124", "limit": 5})

        self.assertEqual(first["clients"][0]["id"], client["id"])
        self.assertEqual(second["clients"][0]["id"], client["id"])
        self.assertEqual(related_index.call_count, 1)

    def test_profile_index_reuse_requires_unchanged_search_fields(self) -> None:
        client = self.service.create_client(
            {"client_type": "company", "short_name": "ALBATROSS", "legal_name": "SYNTHETIC LLC"}
        )["client"]
        unchanged_id = self.service.create_client({"display_name": "SYNTHETIC OTHER PROFILE"})[
            "client"
        ]["id"]
        card = self.service.create_card({"title": "Synthetic history"})["card"]
        query = {"query": "ALBATROSS"}
        checked = self.service.search_clients(query)
        index = self.service._client_search_index_cache[1]
        self.assertEqual(checked["clients"][0]["id"], client["id"])
        self.assertTrue(
            self.service.mark_card_seen({"card_id": card["id"], "actor_name": "TEST"})["meta"][
                "changed"
            ]
        )

        with patch.object(
            self.service, "_client_match_keys", wraps=self.service._client_match_keys
        ) as keys:
            self.assertEqual(self.service.search_clients(query), checked)
            self.assertEqual(
                self.service.search_clients_readonly(query)["clients"][0]["id"], client["id"]
            )
            keys.assert_not_called()
        self.assertIs(self.service._client_search_index_cache[1], index)

        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        row = next(row for row in state["clients"] if row["id"] == client["id"])
        row["short_name"] = "JUNIPER"
        replacement = self.state_file.with_suffix(".new")
        replacement.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        replacement.replace(self.state_file)

        # A replacement may preserve updated_at. Search must still see the new name.
        with patch.object(
            self.service, "_client_match_keys", wraps=self.service._client_match_keys
        ) as keys:
            for search in (self.service.search_clients, self.service.search_clients_readonly):
                with self.subTest(route=search.__name__):
                    self.assertEqual(search(query)["clients"], [])
                    self.assertEqual(search({"query": "JUNIPER"})["clients"][0]["id"], client["id"])
            self.assertEqual([call.args[0].id for call in keys.call_args_list], [client["id"]])
        new_index = self.service._client_search_index_cache[1]
        self.assertIsNot(new_index, index)
        self.assertIs(new_index[unchanged_id], index[unchanged_id])

    def test_snapshot_reuses_profiles_but_reads_current_card_relations(self) -> None:
        client = self.service.create_client({"display_name": "SYNTHETIC PROFILE"})["client"]
        self.service.create_client({"display_name": "SYNTHETIC ANOTHER PROFILE"})
        query = {"query": "SYNTHETIC PROFILE"}
        search_clients = readonly_context_snapshot._search_clients
        with (
            patch.object(json_store, "_SNAPSHOT_REQUIRES_FILE_LOCK", False),
            patch.object(
                readonly_context_snapshot, "_safe_to_detach_shallow_json", return_value=True
            ),
            patch.object(
                readonly_context_snapshot, "_search_clients", wraps=search_clients
            ) as normalize,
        ):
            before = self.service.search_clients(query)["clients"][0]
            self.service.create_card(
                {
                    "title": "SYNTHETIC CARD",
                    "vehicle_profile": {"customer_name": "SYNTHETIC PROFILE"},
                }
            )
            after = self.service.search_clients(query)["clients"][0]
            self.assertEqual(before["stats"]["cards_total"], 0)
            self.assertEqual(after["stats"]["cards_total"], 1)
            self.assertEqual(after["id"], client["id"])
            normalize.assert_called_once()

            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            row = next(row for row in state["clients"] if row["id"] == client["id"])
            row["display_name"] = "RENAMED PROFILE"
            replacement = self.state_file.with_suffix(".new")
            replacement.write_text(json.dumps(state), encoding="utf-8")
            replacement.replace(self.state_file)
            profile_model = readonly_context_snapshot.ClientProfile
            with patch.object(profile_model, "from_dict", wraps=profile_model.from_dict) as parse:
                renamed = self.service.search_clients({"query": "RENAMED PROFILE"})["clients"][0]
                parse.assert_called_once_with(row)
            self.assertEqual(renamed["id"], client["id"])
            self.assertEqual(renamed["name"], "RENAMED PROFILE")
            self.assertEqual(normalize.call_count, 2)

    def test_legacy_profiles_keep_synthesizing_missing_dates_and_vehicles(self) -> None:
        self.service.create_client({"display_name": "SYNTHETIC LEGACY"})
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        client = state["clients"][0]
        original = dict(client)
        for missing in ("created_at", "vehicle_id"):
            with self.subTest(missing=missing):
                client.update(original)
                if missing == "created_at":
                    del client["created_at"]
                else:
                    client["vehicles"] = [{"vehicle": "SYNTHETIC VEHICLE"}]
                text = json.dumps(state)

                def read_snapshot():
                    replacement = self.state_file.with_suffix(".new")
                    replacement.write_text(text, encoding="utf-8")
                    replacement.replace(self.state_file)
                    return self.store._read_context_bundle_snapshot(
                        domains=("clients", "client_search")
                    )["clients"][0]

                with (
                    patch.object(json_store, "_SNAPSHOT_REQUIRES_FILE_LOCK", False),
                    patch.object(
                        readonly_context_snapshot, "_safe_to_detach_shallow_json", return_value=True
                    ),
                    patch(
                        "minimal_kanban.models.utc_now_iso",
                        side_effect=("2026-01-01", "2026-01-02"),
                    ),
                ):
                    before, after = read_snapshot(), read_snapshot()
                if missing == "created_at":
                    self.assertNotEqual(before.created_at, after.created_at)
                else:
                    self.assertNotEqual(before.vehicles[0].id, after.vehicles[0].id)

    def test_manager_client_link_query_prefers_one_strong_identifier(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Toyota Camry",
                "title": "Диагностика",
                "description": "Клиент Тестовый, госномер А123ВС124",
                "deadline": {"hours": 1},
                "vehicle_profile": {
                    "customer_name": "Клиент Тестовый",
                    "customer_phone": "+7 913 000-11-22",
                    "registration_plate": "А123ВС124",
                },
            }
        )
        card = next(item for item in self.store.read_cards() if item.id == created["card"]["id"])

        query = self.service._manager_client_link_query(card)

        self.assertEqual(query, "+7 913 000-11-22")
        self.assertNotIn("Toyota", query)
        self.assertNotIn("Клиент", query)

    def test_audit_client_links_prepares_search_indexes_once(self) -> None:
        client = self.service.create_client(
            {
                "client_type": "person",
                "last_name": "Тестовый",
                "first_name": "Клиент",
                "phone": "+7 913 000-11-22",
            }
        )["client"]
        for suffix in ("22", "23"):
            self.service.create_card(
                {
                    "vehicle": "Toyota Camry",
                    "title": f"Диагностика {suffix}",
                    "deadline": {"hours": 1},
                    "vehicle_profile": {
                        "customer_phone": f"+7 913 000-11-{suffix}",
                    },
                }
            )

        with (
            patch.object(
                self.service,
                "_client_search_index_for",
                wraps=self.service._client_search_index_for,
            ) as search_index,
            patch.object(
                self.service,
                "_client_related_vehicle_fields_index_for",
                wraps=self.service._client_related_vehicle_fields_index_for,
            ) as related_index,
            patch.object(
                self.service,
                "_client_related_search_index",
                wraps=self.service._client_related_search_index,
            ) as related_search_index,
        ):
            result = self.service.audit_client_links({"limit": 10})

        self.assertEqual(search_index.call_count, 1)
        self.assertEqual(related_index.call_count, 1)
        self.assertEqual(related_search_index.call_count, 1)
        self.assertEqual(result["cards"][0]["candidates"][0]["client"]["id"], client["id"])

    def test_list_clients_batches_related_cards_when_stats_requested(self) -> None:
        clients = [
            self.service.create_client(
                {
                    "display_name": f"Клиент пакетной статистики {index}",
                    "phone": f"+7 900 000-00-0{index}",
                }
            )["client"]
            for index in range(1, 4)
        ]
        for index, client in enumerate(clients, start=1):
            self.service.create_card(
                {
                    "vehicle": f"Toyota Test {index}",
                    "title": f"Работа {index}",
                    "description": "Проверка пакетной клиентской статистики",
                    "deadline": {"hours": 1},
                    "client_id": client["id"],
                    "vehicle_profile": {"vin": f"TESTVIN000000000{index}"},
                }
            )

        with patch.object(
            self.service,
            "_client_related_cards",
            side_effect=AssertionError("list_clients must use batched related-card lookup"),
        ):
            listed = self.service.list_clients({"limit": 10, "include_stats": True})

        listed_by_id = {client["id"]: client for client in listed["clients"]}
        self.assertEqual(listed["meta"]["returned"], 3)
        for client in clients:
            row = listed_by_id[client["id"]]
            self.assertEqual(row["stats"]["cards_total"], 1)
            self.assertEqual(row["stats"]["vehicles_total"], 1)
            self.assertEqual(
                row["vehicles_preview"][0]["vin"],
                "TESTVIN000000000" + client["phone"][-1],
            )
