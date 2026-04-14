from __future__ import annotations

import json
import logging
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.knowledge import build_ai_chat_knowledge_packet, get_curated_documents
from minimal_kanban.api.server import ApiServer
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _FakeLookupService:
    def search_web(self, *, query: str, limit: int = 5, allowed_domains: list[str] | None = None) -> dict:
        return {
            "query": query,
            "results": [
                {
                    "title": "VIN decode reference",
                    "url": "https://example.com/vin-decode",
                    "snippet": "VIN decode reference snippet",
                    "domain": "example.com",
                }
            ],
        }

    def fetch_page_excerpt(self, *, url: str, max_chars: int = 2500) -> dict:
        return {
            "url": url,
            "excerpt": "VIN decode reference excerpt for controlled lookup.",
        }


class KnowledgeLayerTests(unittest.TestCase):
    def test_curated_document_registry_has_core_entries(self) -> None:
        documents = get_curated_documents()

        self.assertGreaterEqual(len(documents), 3)
        self.assertTrue(any(item["document_id"] == "master_plan" for item in documents))
        self.assertTrue(any(item["source_path"].endswith("MASTER-PLAN.md") for item in documents))

    def test_knowledge_packet_defaults_to_crm_when_not_requested(self) -> None:
        packet = build_ai_chat_knowledge_packet(
            prompt="Привет, что видно по карточке?",
            context={
                "kind": "compact_context",
                "surface": "ai_chat",
                "card_label": "CARD-1 · KIA RIO",
                "context_label": "CARD-1 · KIA RIO",
            },
            lookup_service=_FakeLookupService(),
        )

        self.assertEqual(packet["source_labels"], ["CRM"])
        self.assertFalse(packet["documents"]["requested"])
        self.assertFalse(packet["documents"]["used"])
        self.assertFalse(packet["internet"]["requested"])
        self.assertFalse(packet["internet"]["used"])

    def test_knowledge_packet_combines_crm_documents_and_internet(self) -> None:
        packet = build_ai_chat_knowledge_packet(
            prompt="master plan settings api guide vin lookup",
            context={
                "kind": "compact_context",
                "surface": "ai_chat",
                "card_label": "CARD-1 · Audi A6 · Плавающий холостой ход",
                "context_label": "CARD-1 · Audi A6 · Плавающий холостой ход",
            },
            lookup_service=_FakeLookupService(),
        )

        self.assertEqual(packet["kind"], "ai_chat_knowledge")
        self.assertIn("CRM", packet["source_labels"])
        self.assertIn("documents", packet["source_labels"])
        self.assertIn("internet", packet["source_labels"])
        self.assertTrue(packet["documents"]["used"])
        self.assertGreater(packet["documents"]["count"], 0)
        self.assertTrue(packet["internet"]["used"])
        self.assertGreater(packet["internet"]["count"], 0)
        self.assertTrue(packet["documents"]["items"][0]["excerpt"])
        self.assertTrue(packet["internet"]["items"][0]["excerpt"])


class KnowledgeApiRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        state_file = Path(self.temp_dir.name) / "state.json"
        logger = logging.getLogger(f"test.knowledge.api.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        self.store = JsonStore(state_file=state_file, logger=logger)
        self.service = CardService(
            self.store,
            logger,
            attachments_dir=Path(self.temp_dir.name) / "attachments",
            repair_orders_dir=Path(self.temp_dir.name) / "repair-orders",
        )
        self.port = reserve_port()
        self.server = ApiServer(self.service, logger, start_port=self.port, fallback_limit=1)
        self.server.start()
        self.base_url = self.server.base_url

    def tearDown(self) -> None:
        self.server.stop()
        self.temp_dir.cleanup()

    def request(self, path: str, payload: dict | None = None, *, method: str = "POST") -> tuple[int, dict]:
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_get_ai_chat_knowledge_route_returns_labels_and_retrieval(self) -> None:
        with mock.patch(
            "minimal_kanban.agent.knowledge.AutomotiveLookupService.search_web",
            return_value={
                "query": "master plan settings api guide vin lookup",
                "results": [
                    {
                        "title": "VIN decode reference",
                        "url": "https://example.com/vin-decode",
                        "snippet": "VIN decode reference snippet",
                        "domain": "example.com",
                    }
                ],
            },
        ), mock.patch(
            "minimal_kanban.agent.knowledge.AutomotiveLookupService.fetch_page_excerpt",
            return_value={
                "url": "https://example.com/vin-decode",
                "excerpt": "VIN decode reference excerpt for controlled lookup.",
            },
        ):
            status, response = self.request(
                "/api/get_ai_chat_knowledge",
                {
                    "prompt": "master plan settings api guide vin lookup",
                    "context": {
                        "kind": "compact_context",
                        "surface": "ai_chat",
                        "card_label": "CARD-2 · BMW X5",
                        "context_label": "CARD-2 · BMW X5",
                    },
                },
            )

        self.assertEqual(status, 200)
        self.assertTrue(response["ok"])
        data = response["data"]
        self.assertEqual(data["kind"], "ai_chat_knowledge")
        self.assertIn("CRM", data["source_labels"])
        self.assertIn("documents", data["source_labels"])
        self.assertIn("internet", data["source_labels"])
        self.assertTrue(data["documents"]["used"])
        self.assertTrue(data["internet"]["used"])
        self.assertGreater(len(data["documents"]["items"]), 0)
        self.assertGreater(len(data["internet"]["items"]), 0)
