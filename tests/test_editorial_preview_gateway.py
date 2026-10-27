from __future__ import annotations

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
from copy import deepcopy

from tests.services_case import CardServiceCase
from minimal_kanban.mcp.agent_gateway_support import _compact_object
from minimal_kanban.services.errors import ServiceError


class EditorialPreviewGatewayTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        self.card = self.service.create_card(
            {"title": "Synthetic repair", "description": "Old text"}
        )["card"]

    def payload(self, **changes):
        return {
            "card_id": self.card["id"],
            "content_only": True,
            "mode": "dry_run",
            "actor_name": "DIRECTOR",
            "source": "mcp",
            "response_mode": "full",
            "expected_updated_at": self.card["updated_at"],
            "idempotency_key": "synthetic-preview",
            "description": "New " + "x" * 6000,
            "title": "Updated synthetic repair",
            "vehicle_profile": {
                "engine_model": "Candidate",
                "tentative_fields": ["engine_model"],
                "field_sources": {"engine_model": "Synthetic evidence"},
            },
            **changes,
        }

    def test_preview_preserves_fields_and_values_without_writing(self):
        before = deepcopy(self.store.read_bundle())
        request = self.payload()
        result = self.service.cleanup_card(request)
        # Raw MCP capability wraps the service result in data before the named
        # workflow compacts it; this nesting caused the production regression.
        compact = _compact_object(
            {"ok": True, "data": result},
            max_depth=12,
            item_limit=500,
            key_limit=500,
            string_limit=20000,
        )
        item = compact["data"]["items"][0]
        self.assertEqual(set(item["planned_fields"]), {"title", "description", "vehicle_profile"})
        self.assertEqual(item["card"]["title"], request["title"])
        self.assertEqual(item["card"]["description"], request["description"])
        self.assertEqual(item["card"]["vehicle_profile"]["engine_model"], "Candidate")
        self.assertEqual(item["card"]["vehicle_profile"]["tentative_fields"], ["engine_model"])
        self.assertEqual(result["changed"], 0)
        self.assertEqual(before, self.store.read_bundle())

    def test_forbidden_fields_and_stale_revision_are_atomic(self):
        before = deepcopy(self.store.read_bundle())
        for fields in (
            {"deadline": {"hours": 2}},
            {"repair_order": {"works": []}},
            {"vehicle_profile": {"customer_name": "Forbidden"}},
            {"expected_updated_at": "stale"},
        ):
            with self.subTest(fields=fields), self.assertRaises(ServiceError):
                self.service.cleanup_card(self.payload(**fields))
            self.assertEqual(before, self.store.read_bundle())
