from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.models import Card
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


class CardSearchIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_file = Path(self.temp_dir.name) / "state.json"
        logger = logging.getLogger(self.id())
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(self.state_file, logger)
        self.service = CardService(self.store, logger)
        bundle = self.store.read_bundle()
        self.column = bundle["columns"][0].id
        self.other_column = bundle["columns"][1].id
        bundle["cards"] = [
            Card.from_dict(
                {
                    "id": f"search-card-{index}",
                    "title": "[MCP TEST] Диагностика" if index < 4 else "Замена фильтра",
                    "vehicle": "Ниссан Тиида" if index % 2 else "Nissan Tiida",
                    "description": "Проверить давление масла и K12B",
                    "column": self.column if index < 3 else self.other_column,
                    "archived": index == 4,
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-02T00:00:00+00:00",
                    "tags": ["СРОЧНО", "SEARCH-CHECK"] if index < 2 else [],
                    "vehicle_profile": {
                        "vin": f"JSAZC72S00123456{index}",
                        "customer_name": "Иван Иванов",
                        "customer_phone": "+7 (999) 123-45-67",
                        "engine_code": "K12B",
                    },
                    "repair_order": {
                        "number": str(index + 1),
                        "client": "Иван Иванов",
                        "license_plate": "В003НК124",
                        "works": [{"name": "Замена свечей", "quantity": "1", "price": "100"}],
                    },
                }
            )
            for index in range(6)
        ]
        self.store.write_bundle(**bundle)

    def test_index_preserves_uncached_scores_fields_order_filters_and_response(self) -> None:
        bundle = self.store.read_bundle()
        for payload in (
            {"query": "Nissan Tiida"},
            {"query": "Тиида", "limit": 2, "include_archived": True},
            {"query": "mcp-test", "column": self.column, "tag": "СРОЧНО"},
            {"query": "K12B давление"},
            {"query": "Иван Иванов"},
            {"query": "В003НК124"},
            {"query": "замена свечей"},
            {"query": "search check"},
            {"query": "totally-missing"},
            {"query": "--- []"},
            {"column": self.column},
            {"status": "ok"},
            {"indicator": "green"},
        ):
            with self.subTest(payload=payload):
                result = self.service.search_cards(payload)
                expected = []
                for card in bundle["cards"]:
                    if card.archived and not payload.get("include_archived"):
                        continue
                    if payload.get("column") and card.column != payload["column"]:
                        continue
                    if payload.get("tag") and payload["tag"] not in card.tag_labels():
                        continue
                    if payload.get("status") and card.status() != payload["status"]:
                        continue
                    if payload.get("indicator") and card.indicator() != payload["indicator"]:
                        continue
                    score, fields = self.service._search_card_match(card, payload.get("query", ""))
                    if payload.get("query") and not score:
                        continue
                    expected.append((score, card.updated_at, card.id, fields))
                expected.sort(key=lambda row: row[:2], reverse=True)
                self.assertEqual(result["meta"]["total_matches"], len(expected))
                limited = expected[: payload.get("limit", 20)]
                self.assertEqual(
                    [
                        (row["match"]["score"], row["id"], row["match"]["fields"])
                        for row in result["cards"]
                    ],
                    [(score, card_id, fields) for score, _, card_id, fields in limited],
                )
                self.assertEqual(self.service.search_cards(payload), result)
                for row in result["cards"]:
                    canonical = self.service.get_card({"card_id": row["id"]})["card"]
                    # get_card additionally includes attachment state by default.
                    self.assertEqual(
                        {key: canonical[key] for key in row if key != "match"},
                        {key: value for key, value in row.items() if key != "match"},
                    )

    def test_reuses_normalization_and_invalidates_on_local_save(self) -> None:
        snapshots = self.service._snapshot_service
        with patch.object(
            snapshots._card_search_index,
            "_prepare_fields",
            wraps=snapshots._card_search_index._prepare_fields,
        ) as prepare:
            self.service.search_cards({"query": "Tiida", "include_archived": True})
            self.assertEqual(prepare.call_count, 6)
            self.service.search_cards({"query": "K12B", "include_archived": True})
            self.assertEqual(prepare.call_count, 6)
            self.service.update_card({"card_id": "search-card-0", "vehicle": "UniqueNewVehicle"})
            changed = self.service.search_cards({"query": "UniqueNewVehicle"})
            self.assertEqual([row["id"] for row in changed["cards"]], ["search-card-0"])
            self.assertGreater(prepare.call_count, 6)

    def test_external_reload_with_unchanged_card_revision_and_deletion_invalidates(self) -> None:
        self.service.search_cards({"query": "Диагностика"})
        external = JsonStore(self.state_file)
        bundle = external.read_bundle()
        card = bundle["cards"][0]
        previous_revision = card.updated_at
        card.title = "ExternalEditedTitle"
        bundle["cards"] = [item for item in bundle["cards"] if item.id != "search-card-1"]
        external.write_bundle(**bundle)
        found = self.service.search_cards({"query": "ExternalEditedTitle"})
        self.assertEqual(found["cards"][0]["updated_at"], previous_revision)
        self.assertEqual(found["cards"][0]["id"], card.id)
        self.assertNotIn(
            "search-card-1",
            [row["id"] for row in self.service.search_cards({"query": "search-card-1"})["cards"]],
        )

    def test_filter_only_skips_normalization_and_clock_filters_remain_live(self) -> None:
        snapshots = self.service._snapshot_service
        with patch.object(
            snapshots._card_search_index,
            "_prepare_fields",
            side_effect=AssertionError("filter-only search"),
        ):
            result = self.service.search_cards({"column": self.column})
            self.assertEqual(len(result["cards"]), 3)
            self.assertEqual(self.service.search_cards({"query": "--- []"})["cards"], [])
        self.service.search_cards({"query": "Tiida"})
        with patch.object(Card, "status", return_value="expired"):
            expired = self.service.search_cards({"query": "Tiida", "status": "expired"})
        with patch.object(Card, "status", return_value="ok"):
            current = self.service.search_cards({"query": "Tiida", "status": "expired"})
        self.assertEqual(len(expired["cards"]), 5)
        self.assertEqual(current["cards"], [])

    def test_new_bundle_identity_invalidates_even_with_identical_file_signature(self) -> None:
        self.service.search_cards({"query": "Диагностика"})
        bundle, signature = self.store.read_bundle_with_signature()
        replacement = deepcopy(bundle)
        replacement["cards"][0].title = "ReNormalizedTitle"
        with patch.object(
            self.store, "read_bundle_with_signature", return_value=(replacement, signature)
        ):
            result = self.service.search_cards({"query": "ReNormalizedTitle"})
        self.assertEqual([card["id"] for card in result["cards"]], ["search-card-0"])


if __name__ == "__main__":
    unittest.main()
