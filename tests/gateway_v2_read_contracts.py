from __future__ import annotations

from unittest.mock import patch


class GatewayV2ReadContractTestsMixin:
    async def test_card_search_preserves_validation_error_and_failed_status(self) -> None:
        error = {
            "code": "validation_error",
            "message": "A query or filter is required",
            "details": {"fields": ["query", "column"]},
        }
        with patch.object(
            self.board_api, "search_cards", return_value={"ok": False, "error": error}
        ):
            result = await self._call("agent_search", {"entity": "card", "query": ""})

        payload = result.structuredContent
        self.assertTrue(result.isError)
        self.assertFalse(payload["ok"])
        self.assertEqual("failed", payload["status"])
        self.assertEqual(error, payload["data"]["error"])
        self.assertEqual([], payload["data"]["items"])
        self.assertIn("validation_error", payload["warnings"])

        success = await self._call("agent_search", {"entity": "card", "query": "Synthetic"})
        self.assertTrue(success.structuredContent["ok"])
        self.assertEqual("completed", success.structuredContent["status"])
        self.assertNotIn("error", success.structuredContent["data"])

    async def test_raw_read_failure_does_not_request_nonexistent_workflow(self) -> None:
        schema = await self._call("get_raw_capability_schema", {"name": "search_cards"})
        error = {"code": "validation_error", "message": "A query or filter is required"}
        with patch.object(
            self.board_api, "search_cards", return_value={"ok": False, "error": error}
        ):
            result = await self._call(
                "call_raw_capability",
                {
                    "name": "search_cards",
                    "arguments": {"query": ""},
                    "schema_hash": schema.structuredContent["summary"]["schema_hash"],
                },
            )

        payload = result.structuredContent
        self.assertFalse(payload["ok"])
        self.assertEqual("failed", payload["status"])
        self.assertIsNone(payload["run_id"])
        self.assertEqual(error, payload["data"]["error"])
        self.assertTrue(payload["next_actions"])
        self.assertNotIn("workflow_status", " ".join(payload["next_actions"]))

    async def test_repair_order_search_applies_exact_filters_and_fails_closed(self) -> None:
        exact = await self._call(
            "agent_search",
            {
                "entity": "repair_order",
                "query": "18",
                "include_archived": True,
                "limit": 1,
                "filters": {"number": "18"},
            },
        )
        archived = await self._call(
            "agent_search",
            {
                "entity": "repair_order",
                "include_archived": True,
                "filters": {"card_id": "card-closed-99"},
            },
        )
        intersection = await self._call(
            "agent_search",
            {
                "entity": "repair_order",
                "query": "несовпадающий текст",
                "include_archived": True,
                "filters": {"number": "18"},
            },
        )
        unsupported = await self._call(
            "agent_search",
            {
                "entity": "repair_order",
                "filters": {"vehicle": "КамАЗ"},
            },
        )

        self.assertEqual(
            ["18"], [item["number"] for item in exact.structuredContent["data"]["items"]]
        )
        self.assertEqual("all", exact.structuredContent["summary"]["applied_filters"]["status"])
        self.assertEqual(
            ["card-closed-99"],
            [item["card_id"] for item in archived.structuredContent["data"]["items"]],
        )
        self.assertEqual([], intersection.structuredContent["data"]["items"])
        self.assertFalse(unsupported.structuredContent["ok"])
        self.assertIn("unsupported_search_filters", unsupported.structuredContent["warnings"])
