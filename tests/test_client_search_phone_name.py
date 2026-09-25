from __future__ import annotations

import logging
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.logging_setup import close_logger
from minimal_kanban.models import ClientVehicle, normalize_text
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


class ClientSearchPhoneNameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.logger = logging.getLogger(f"test.client_phone_name.{self._testMethodName}")
        close_logger(self.logger)
        self.addCleanup(close_logger, self.logger)
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.service = CardService(
            JsonStore(state_file=self.state_file, logger=self.logger), self.logger
        )

    def test_phone_like_client_name_is_found_by_phone_digits_without_phone_field(self) -> None:
        client = self.service.create_client({"display_name": "89504235457"})["client"]
        competitor = self.service.create_client({"display_name": "История с таким телефоном"})[
            "client"
        ]
        for index in range(15):
            self.service.create_card(
                {
                    "title": f"Косвенная история по телефону {index}",
                    "vehicle": "Toyota Corolla",
                    "client_id": competitor["id"],
                    "vehicle_profile": {"customer_phone": "8 950 423-54-57"},
                    "deadline": {"hours": 1},
                }
            )

        search = self.service.search_clients({"query": "89504235457", "limit": 5})

        self.assertTrue(search["clients"])
        self.assertEqual(search["clients"][0]["id"], client["id"])
        self.assertIn(competitor["id"], {item["id"] for item in search["clients"]})

    def test_search_index_reused_and_invalidated_for_saves_and_external_reload(self) -> None:
        client = self.service.create_client({"display_name": "Первый клиент"})["client"]
        with patch.object(
            self.service,
            "_client_search_index_for",
            wraps=self.service._client_search_index_for,
        ) as build_index:
            self.assertEqual(
                self.service.search_clients({"query": "Первый"})["clients"][0]["id"],
                client["id"],
            )
            self.assertEqual(build_index.call_count, 1)
            self.service.search_clients({"query": "клиент"})
            self.assertEqual(build_index.call_count, 1)

            self.service.update_client({"client_id": client["id"], "display_name": "Второй клиент"})
            self.assertEqual(self.service.search_clients({"query": "Первый"})["clients"], [])
            self.assertEqual(
                self.service.search_clients({"query": "Второй"})["clients"][0]["id"],
                client["id"],
            )
            self.assertEqual(build_index.call_count, 2)

            external = CardService(
                JsonStore(state_file=self.state_file, logger=self.logger), self.logger
            )
            external.update_client({"client_id": client["id"], "display_name": "Третий клиент"})
            self.assertEqual(self.service.search_clients({"query": "Второй"})["clients"], [])
            self.assertEqual(
                self.service.search_clients({"query": "Третий"})["clients"][0]["id"],
                client["id"],
            )
            self.assertEqual(build_index.call_count, 3)
            external.create_card(
                {
                    "title": "Внешняя карточка",
                    "vehicle": "Subaru Forester",
                    "client_id": client["id"],
                    "deadline": {"hours": 1},
                }
            )
            self.assertEqual(
                self.service.search_clients({"query": "Forester"})["clients"][0]["id"],
                client["id"],
            )
            self.assertEqual(build_index.call_count, 4)

    def test_cached_search_preserves_original_ranking_for_name_phone_and_vehicle(self) -> None:
        first = self.service.create_client(
            {"display_name": "Анна Иванова", "phone": "+7 950 423-54-57"}
        )["client"]
        self.service.create_client({"display_name": "Анна Петрова", "phone": "+7 950 423-54-58"})
        self.service.create_client({"display_name": "Третье лицо"})
        self.service.create_card(
            {
                "title": "Toyota Camry",
                "vehicle": "Toyota Camry",
                "client_id": first["id"],
                "vehicle_profile": {"vin": "JTNBE46K403123456"},
                "deadline": {"hours": 1},
            }
        )
        for query in ("Анна", "anna", "9504235457", "Toyota", "JTNBE46K403123456", ""):
            with self.subTest(query=query):
                bundle = self.service._store.read_bundle()
                clients = self.service._ordered_clients(bundle["clients"])
                original = self.service._rank_client_matches(clients, query, bundle["cards"])
                actual = self.service.search_clients({"query": query, "limit": 100})
                self.assertEqual(
                    [client.id for _, client in original],
                    [client["id"] for client in actual["clients"]],
                )
                self.assertEqual(actual["meta"]["total"], len(original))

    def test_weighted_search_fields_preserve_legacy_scores_and_order(self) -> None:
        first = self.service.create_client(
            {"display_name": "Анна Иванова", "phone": "+7 950 423-54-57"}
        )["client"]
        tied = self.service.create_client(
            {"display_name": "Анна Иванова", "phone": "+7 950 423-54-58"}
        )["client"]
        vehicle_owner = self.service.create_client(
            {"display_name": "Владелец Toyota", "phone": "+7 999 111-22-33"}
        )["client"]

        bundle = self.service._store.read_bundle()
        tied_at = "2025-01-01T00:00:00+00:00"
        for client in bundle["clients"]:
            if client.id in {first["id"], tied["id"]}:
                client.created_at = tied_at
                client.updated_at = tied_at
            if client.id == vehicle_owner["id"]:
                client.vehicles.append(
                    ClientVehicle.from_value(
                        {
                            "id": "synthetic-toyota-corolla",
                            "vehicle": "Toyota Corolla",
                            "brand": "Toyota",
                            "model": "Corolla",
                            "vin": "JTDBR32E720123456",
                        }
                    )
                )
        self.service._store.write_bundle(**bundle)
        self.service.create_card(
            {
                "title": "Synthetic Corolla service history",
                "vehicle": "Toyota Corolla",
                "client_id": vehicle_owner["id"],
                "vehicle_profile": {
                    "make_display": "Toyota",
                    "model_display": "Corolla",
                    "customer_name": "Владелец Toyota",
                    "customer_phone": "+7 999 111-22-33",
                },
                "deadline": {"hours": 1},
            }
        )

        queries = (
            "Анна",
            "Анна",
            "anna",
            "9504235457",
            "9991112233",
            "Toyota",
            "Corolla",
            "",
            "Иванова",
        )
        for query in queries:
            with self.subTest(query=query):
                bundle = self.service._store.read_bundle()
                clients = self.service._ordered_clients(bundle["clients"])
                expected = self._legacy_rank_clients(clients, bundle["cards"], query)
                actual = self.service._rank_client_matches(clients, query, bundle["cards"])
                self.assertEqual(
                    [(score, client.id) for score, client in actual],
                    expected,
                )

        tied_result = self.service.search_clients({"query": "Анна", "limit": 10})
        self.assertEqual(
            [item["id"] for item in tied_result["clients"][:2]],
            sorted((first["id"], tied["id"])),
        )

    def _legacy_rank_clients(self, clients, cards, query: str) -> list[tuple[int, str]]:
        normalized_query = normalize_text(query, default="", limit=500)
        if not normalized_query:
            return [(1, client.id) for client in self.service._ordered_clients(clients)]

        query_variants = self.service._search_text_variants(normalized_query)
        query_digits = re.sub(r"\D+", "", normalized_query)
        query_phone_variants = self.service._phone_search_variants(normalized_query)
        phone_like_query = bool(query_digits) and not re.search(r"[A-Za-zА-Яа-я]", normalized_query)
        client_index = self.service._client_search_index_for(clients)
        related_fields = self.service._client_related_vehicle_fields_index_for(clients, cards)
        related_index = {}
        for client_id, fields in related_fields.items():
            searchable = [self.service._normalize_search_text(value) for value in fields if value]
            related_index[client_id] = {
                "searchable": searchable,
                "compact_searchable": [
                    re.sub(r"[\W_]+", "", value) for value in searchable if value
                ],
                "phone_keys": {
                    key for value in fields for key in self.service._phone_match_keys(value)
                },
            }

        ranked = []
        for client in clients:
            if phone_like_query:
                score = self.service._score_client_phone_like_match(
                    client,
                    query_digits=query_digits,
                    query_phone_variants=query_phone_variants,
                    client_search_index=client_index,
                    related_search_index_by_client_id=related_index,
                    query_variants=query_variants,
                )
            else:
                indexed = client_index.get(client.id, {})
                score = 0
                for variant in query_variants:
                    if not variant:
                        continue
                    parts = tuple(variant.split())
                    compact_variant = re.sub(r"[\W_]+", "", variant)
                    for value in indexed.get("searchable", []):
                        if value == variant:
                            score += 8
                        elif variant in value:
                            score += 4
                        elif all(part in value for part in parts):
                            score += 2
                    for value in indexed.get("vehicle_searchable", []):
                        if value == variant:
                            score += 7
                        elif variant in value:
                            score += 5
                        elif all(part in value for part in parts):
                            score += 3
                    if compact_variant and any(
                        compact_variant in value for value in indexed.get("compact_searchable", [])
                    ):
                        score += 5
                if len(query_digits) >= 4:
                    phone_digits = " ".join(re.sub(r"\D+", "", phone) for phone in client.phones)
                    if query_digits in phone_digits:
                        score += 10
                if query_phone_variants:
                    client_phone_variants = indexed.get("phone_variants", set())
                    if client_phone_variants and any(
                        query_variant in client_variant or client_variant in query_variant
                        for query_variant in query_phone_variants
                        for client_variant in client_phone_variants
                    ):
                        score += 10
                    elif query_phone_variants.intersection(indexed.get("match_keys", set())):
                        score += 10
                score += self.service._score_client_related_search_fields(
                    related_index.get(client.id),
                    query_variants=query_variants,
                    query_digits=query_digits,
                    query_phone_variants=query_phone_variants,
                )
            if score > 0:
                ranked.append((score, client))
        ranked.sort(key=lambda item: (item[0], item[1].updated_at, item[1].name()), reverse=True)
        return [(score, client.id) for score, client in ranked]


if __name__ == "__main__":
    unittest.main()
