"""Single-card reads scope events before applying the existing permission filter."""

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
import base64
import json
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.models import AuditEvent
from minimal_kanban.services.operator_visibility import visible_audit_events
from minimal_kanban.services.readonly_context_helpers import detached_card_response
from minimal_kanban.storage.readonly_context_snapshot import ReadonlyContextSnapshot


class CardEventScopeTests(CardServiceCase):
    def test_fallback_response_matches_previous_algorithm_for_both_roles(self):
        card_id = self.service.create_card({"title": "Synthetic card"})["card"]["id"]
        self.service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "synthetic.txt",
                "content_base64": base64.b64encode(b"synthetic").decode(),
            }
        )
        source = self.store._read_context_bundle_snapshot(domains=("events",))

        def event(event_id, target, action, details=None):
            return AuditEvent(
                id=event_id,
                timestamp="2026-01-01T00:00:00+00:00",
                actor_name="synthetic",
                source="ui",
                action=action,
                message="synthetic",
                card_id=target,
                details=details or {},
            )

        data = dict(source)
        data["events"] = [
            event("duplicate", card_id, "card_updated"),
            event("duplicate", card_id, "card_updated"),
            event("private", card_id, "cashbox_updated", {"amount_minor": "SECRET"}),
            event("order", card_id, "repair_order_updated", {"payment": "SECRET"}),
            event("other", "other-card", "repair_order_updated", {"payment": "SECRET"}),
            event("unlinked", None, "repair_order_updated", {"payment": "SECRET"}),
        ]
        bundle = ReadonlyContextSnapshot(
            data, {}, source_indexes=(source._cards, source._clients, source._events)
        )
        snapshot = self.service._snapshot_service
        card = snapshot._find_card(bundle["cards"], card_id)
        for permissions in ([], ["employees_cashboxes_access"]):
            for attachment_status in (False, True):
                with self.subTest(permissions=permissions, attachment_status=attachment_status):
                    payload = {
                        "card_id": card_id,
                        "include_attachment_status": attachment_status,
                        "_operator_session": {"permissions": permissions},
                    }
                    full_events = visible_audit_events(payload, bundle["events"])
                    expected_card = snapshot._serialize_card(
                        card,
                        full_events,
                        column_labels=snapshot._column_labels(bundle["columns"]),
                        include_removed_attachments=True,
                        include_attachment_status=attachment_status,
                        viewer_username=snapshot._viewer_username(payload),
                    )
                    expected = detached_card_response(expected_card, ["synthetic_warning"])
                    seen = []

                    def inspect_visible(request, events):
                        seen.extend(events)
                        return visible_audit_events(request, seen)

                    with (
                        patch.object(self.store, "_read_single_card_snapshot", return_value=bundle),
                        patch.object(bundle, "card_warnings", return_value=["synthetic_warning"]),
                        patch.object(bundle, "card_link_warnings", return_value=[]),
                        patch(
                            "minimal_kanban.services.snapshot_service.visible_audit_events",
                            side_effect=inspect_visible,
                        ),
                    ):
                        result = self.service.get_card(payload)
                    self.assertEqual(result, expected)
                    self.assertEqual(
                        [entry.id for entry in seen], ["duplicate", "duplicate", "private", "order"]
                    )
                    self.assertEqual(result["card"]["events_count"], 4 if permissions else 3)
                    self.assertEqual(result["read_warnings"], ["synthetic_warning"])
                    self.assertEqual(
                        "exists_on_disk" in result["card"]["attachments"][0], attachment_status
                    )
                    self.assertNotIn("SECRET", json.dumps(result))
