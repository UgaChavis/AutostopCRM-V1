"""Single-card reads count and project only the events needed for each response."""

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
import base64
import json
from datetime import timedelta
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.models import AuditEvent, utc_now
from minimal_kanban.services.operator_visibility import audit_event_counts, visible_audit_events
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

                    def inspect_counts(events, *, include_private):
                        seen.extend(events)
                        return audit_event_counts(seen, include_private=include_private)

                    with (
                        patch.object(self.store, "_read_single_card_snapshot", return_value=bundle),
                        patch.object(bundle, "card_warnings", return_value=["synthetic_warning"]),
                        patch.object(bundle, "card_link_warnings", return_value=[]),
                        patch(
                            "minimal_kanban.services.snapshot_service.visible_audit_events",
                            side_effect=AssertionError("single-card read only needs event counts"),
                        ),
                        patch(
                            "minimal_kanban.services.snapshot_service._event_counts",
                            side_effect=inspect_counts,
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

    def test_card_context_redacts_only_the_requested_visible_events(self):
        card_id = self.service.create_card({"title": "Synthetic card"})["card"]["id"]
        now = utc_now()
        self.store.write_events(
            [
                AuditEvent(
                    id=event_id,
                    timestamp=(now - timedelta(seconds=offset)).isoformat(),
                    actor_name="Operator",
                    source="api",
                    action=action,
                    message="Synthetic event",
                    details={"status": "draft", "payroll_postings": ["private test detail"]},
                    card_id=card_id,
                )
                for event_id, offset, action in (
                    ("older", 2, "repair_order_updated"),
                    ("selected", 1, "repair_order_updated"),
                    ("private", 0, "cashbox_updated"),
                )
            ]
        )
        payload = {
            "card_id": card_id,
            "read_only": True,
            "event_limit": 1,
            "include_repair_order_text": False,
        }
        with patch(
            "minimal_kanban.services.readonly_context_helpers.visible_audit_events",
            wraps=visible_audit_events,
        ) as project:
            result = self.service.get_card_context(
                {**payload, "_operator_session": {"permissions": []}}
            )

        project.assert_called_once()
        self.assertEqual([event.id for event in project.call_args.args[1]], ["selected"])
        self.assertEqual(result["card"]["events_count"], 2)
        self.assertEqual(result["meta"]["events_total"], 2)
        self.assertTrue(result["meta"]["has_more_events"])
        self.assertEqual(result["events"][0]["details"], {"status": "draft"})
        self.assertNotIn("private test detail", json.dumps(result))

        full = self.service.get_card_context(payload)
        self.assertEqual(full["meta"]["events_total"], 3)
        self.assertEqual(full["events"][0]["id"], "private")
