# ruff: noqa: E402
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.agent.store_context import StoreQuotePartContext
from minimal_kanban.agent.tools import AgentToolExecutor


class _FakeStoreClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def search(
        self,
        *,
        entity: str,
        query_text: str = "",
        filters: dict[str, object] | None = None,
        cursor: str | None = None,
        limit: int = 4,
    ) -> dict[str, object]:
        self.calls.append({"entity": entity, "query_text": query_text, "limit": limit})
        if entity == "store_quote_request":
            return {
                "ok": True,
                "items": [
                    {
                        "id": "quote-1",
                        "request_number": "Q-7",
                        "status": "NEW",
                        "items_count": 1,
                        "customer_name": "private",
                        "phone": "private",
                    }
                ],
            }
        return {
            "ok": True,
            "items": [
                {
                    "id": "part-1",
                    "sku": "ABC-1",
                    "name": "Filter",
                    "available_qty": 2,
                    "supplier_secret": "private",
                }
            ],
        }


class StoreQuotePartContextTests(unittest.TestCase):
    def test_executor_exposes_bounded_redacted_store_context(self) -> None:
        client = _FakeStoreClient()
        bridge = StoreQuotePartContext(client_factory=lambda: client)
        executor = AgentToolExecutor(object(), store_context=bridge)

        result = executor.execute(
            "GET_STORE_QUOTE_PART_CONTEXT",
            {"query": "  ABC-1 ", "intent": "quote", "limit": 99},
        )

        self.assertIn(
            "get_store_quote_part_context",
            {item.name for item in executor.definitions},
        )
        self.assertEqual(
            [
                {"entity": "store_quote_request", "query_text": "ABC-1 quote", "limit": 8},
                {"entity": "store_part", "query_text": "ABC-1 quote", "limit": 8},
            ],
            client.calls,
        )
        self.assertTrue(result["ok"])
        self.assertEqual("Q-7", result["data"]["quote_requests"][0]["request_number"])
        self.assertEqual("ABC-1", result["data"]["parts"][0]["sku"])
        self.assertNotIn("private", json.dumps(result))
        self.assertTrue(result["meta"]["read_only"])


if __name__ == "__main__":
    unittest.main()
