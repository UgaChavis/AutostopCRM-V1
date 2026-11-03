"""Archived previews preserve visibility and counts when scoping event work."""

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
import json
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.models import AuditEvent, Card
from minimal_kanban.services.operator_visibility import visible_audit_events


class ArchiveEventScopeTests(CardServiceCase):
    def make_bundle(self, *, empty=False):
        bundle = dict(self.store.read_bundle())
        bundle["cards"] = (
            []
            if empty
            else [
                Card.from_dict(
                    {
                        "id": "a",
                        "title": "A",
                        "archived": True,
                        "updated_at": "2026-01-03T00:00:00+00:00",
                    }
                ),
                Card.from_dict(
                    {
                        "id": "b",
                        "title": "B",
                        "archived": True,
                        "updated_at": "2026-01-02T00:00:00+00:00",
                    }
                ),
                Card.from_dict(
                    {
                        "id": "c",
                        "title": "C",
                        "archived": True,
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                ),
            ]
        )

        def event(event_id, card_id, action, details=None):
            return AuditEvent(
                id=event_id,
                timestamp="2026-01-01T00:00:00+00:00",
                actor_name="synthetic",
                source="ui",
                action=action,
                message="synthetic",
                card_id=card_id,
                details=details or {},
            )

        bundle["events"] = [
            event("same", "a", "card_updated"),
            event("same", "a", "card_updated"),  # Duplicate events still count individually.
            event("private", "a", "cashbox_updated", {"amount_minor": "SECRET"}),
            event("order", "a", "repair_order_updated", {"number": "SYN", "payment": "SECRET"}),
            event("b", "b", "card_updated"),
            event("other", "c", "repair_order_updated", {"payment": "SECRET"}),
            event("unlinked", None, "repair_order_updated", {"payment": "SECRET"}),
        ]
        return bundle

    def legacy_result(self, bundle, payload):
        service = self.service._snapshot_service
        events = visible_audit_events(payload, bundle["events"])
        archived = service._archived_cards(bundle["cards"], limit=payload["limit"])
        labels, counts = service._card_serialization_context(
            archived, columns=bundle["columns"], events=events
        )
        return {
            "cards": service._serialize_cards_payload(
                archived,
                events=events,
                column_labels=labels,
                event_counts=counts,
                viewer_username=service._viewer_username(payload),
                compact=payload["compact"],
            ),
            "meta": {
                "limit": payload["limit"],
                "compact": payload["compact"],
                "total": sum(1 for card in bundle["cards"] if card.archived),
                "returned": len(archived),
                "has_more": sum(1 for card in bundle["cards"] if card.archived) > len(archived),
            },
        }

    def test_full_response_and_visibility_for_both_roles(self):
        bundle = self.make_bundle()
        for permissions in ([], ["employees_cashboxes_access"]):
            for compact in (False, True):
                with self.subTest(permissions=permissions, compact=compact):
                    payload = {
                        "limit": 2,
                        "compact": compact,
                        "_operator_session": {"permissions": permissions},
                    }
                    expected = self.legacy_result(bundle, payload)
                    seen = []

                    def inspect_visible(request, events):
                        seen.extend(events)
                        return visible_audit_events(request, seen)

                    with (
                        patch.object(self.store, "read_bundle", return_value=bundle),
                        patch(
                            "minimal_kanban.services.snapshot_service.visible_audit_events",
                            side_effect=inspect_visible,
                        ),
                    ):
                        result = self.service.list_archived_cards(payload)
                    self.assertEqual(result, expected)
                    self.assertEqual(
                        [event.id for event in seen], ["same", "same", "private", "order", "b"]
                    )
                    self.assertEqual(result["cards"][0]["events_count"], 4 if permissions else 3)
                    self.assertNotIn("SECRET", json.dumps(result))

    def test_empty_archive_does_not_prepare_any_events(self):
        bundle = self.make_bundle(empty=True)
        payload = {"limit": 2, "compact": True, "_operator_session": {"permissions": []}}
        expected = self.legacy_result(bundle, payload)
        seen = []

        def inspect_visible(request, events):
            seen.extend(events)
            return visible_audit_events(request, seen)

        with (
            patch.object(self.store, "read_bundle", return_value=bundle),
            patch(
                "minimal_kanban.services.snapshot_service.visible_audit_events",
                side_effect=inspect_visible,
            ),
        ):
            result = self.service.list_archived_cards(payload)
        self.assertEqual(result, expected)
        self.assertEqual(seen, [])
