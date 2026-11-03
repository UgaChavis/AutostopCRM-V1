"""Page projection preserves visibility and counts across the full card history."""

# The fixture selects this checkout before importing application modules.
# ruff: noqa: I001
import json
from dataclasses import replace
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.models import AuditEvent, Card
from minimal_kanban.services.operator_visibility import (
    restricted_operator_event_visible,
    visible_audit_events,
)


class CardLogPageVisibilityTests(CardServiceCase):
    def test_full_output_matches_previous_visibility_before_paging(self):
        card = Card.from_dict({"id": "target", "title": "Synthetic card"})
        bundle = dict(self.store.read_bundle())
        bundle["cards"] = [card]

        def event(event_id, day, action, details=None, card_id="target"):
            return AuditEvent(
                id=event_id,
                timestamp=f"2026-01-{day:02d}T00:00:00+00:00",
                actor_name="synthetic",
                source="ui",
                action=action,
                message="Synthetic event",
                details=details or {},
                card_id=card_id,
            )

        bundle["events"] = [
            event("private-first", 9, "cashbox_updated", {"amount_minor": "SECRET"}),
            event("duplicate", 8, "card_updated"),
            event(
                "order",
                7,
                "repair_order_updated",
                {"number": "SYN", "payment": "SECRET", "works": [{"name": "Synthetic"}]},
            ),
            event("duplicate", 6, "card_updated"),
            event("private-detail", 5, "card_updated", {"salary": "SECRET"}),
            event("tail", 4, "card_updated"),
            event("other", 10, "card_updated", card_id="other"),
            event("none", 10, "card_updated", card_id=None),
        ]
        snapshot = self.service._snapshot_service
        restricted = {"_operator_session": {"permissions": []}}
        for item in bundle["events"]:
            self.assertEqual(
                restricted_operator_event_visible(item),
                bool(visible_audit_events(restricted, [item])),
            )

        def hydrate(item):
            return replace(item, message=item.message + " hydrated")

        for permissions in ([], ["employees_cashboxes_access"]):
            for compact, limit in ((False, None), (False, 2), (True, None), (True, 2)):
                for full_details in (False, True):
                    with self.subTest(
                        permissions=permissions,
                        compact=compact,
                        limit=limit,
                        full_details=full_details,
                    ):
                        payload = {
                            "card_id": card.id,
                            "compact": compact,
                            "include_full_details": full_details,
                            "_operator_session": {"permissions": permissions},
                        }
                        if limit is not None:
                            payload["limit"] = limit
                        ordered = snapshot._events_for_card(bundle["events"], card.id)
                        old_visible = visible_audit_events(payload, ordered)
                        # Model the previous algorithm: sanitize the whole history first.
                        with (
                            patch.object(self.store, "read_bundle", return_value=bundle),
                            patch.object(snapshot, "_hydrate_event_details", side_effect=hydrate),
                            patch.object(snapshot, "_events_for_card", return_value=old_visible),
                            patch(
                                "minimal_kanban.services.snapshot_service.restricted_operator_event_visible",
                                return_value=True,
                            ),
                        ):
                            expected = self.service.get_card_log(payload)
                        with (
                            patch.object(self.store, "read_bundle", return_value=bundle),
                            patch.object(
                                snapshot, "_hydrate_event_details", side_effect=hydrate
                            ) as hydration,
                        ):
                            actual = self.service.get_card_log(payload)
                        self.assertEqual(actual, expected)
                        self.assertEqual(actual["meta"]["events_total"], 6 if permissions else 4)
                        self.assertEqual(
                            actual["meta"]["include_full_details"],
                            bool(permissions and full_details),
                        )
                        if not compact and limit == 2:
                            self.assertEqual(
                                [item["id"] for item in actual["events"]],
                                ["private-first", "duplicate"]
                                if permissions
                                else ["duplicate", "order"],
                            )
                        if not permissions:
                            self.assertNotIn("SECRET", json.dumps(actual))
                            hydration.assert_not_called()
                            if not compact and limit is None:
                                order = next(
                                    item for item in actual["events"] if item["id"] == "order"
                                )
                                order["details"]["works"][0]["name"] = "Changed"
                                self.assertEqual(
                                    bundle["events"][2].details["works"][0]["name"], "Synthetic"
                                )
                        elif full_details:
                            self.assertEqual(
                                hydration.call_count, actual["meta"]["events_returned"]
                            )

    def test_empty_history_keeps_empty_totals(self):
        bundle = dict(self.store.read_bundle())
        card = Card.from_dict({"id": "target", "title": "Synthetic"})
        bundle["cards"] = [card]
        bundle["events"] = []
        with patch.object(self.store, "read_bundle", return_value=bundle):
            result = self.service.get_card_log({"card_id": card.id, "compact": True})
        self.assertEqual(result["meta"]["events_total"], 0)
        self.assertEqual(result["meta"]["events_returned"], 0)
        self.assertEqual(result["entries"], [])
