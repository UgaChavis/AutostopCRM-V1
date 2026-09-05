from __future__ import annotations

import json
import logging
import os
import stat
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.api.server import ApiServer  # noqa: E402
from minimal_kanban.deployment_security import release_smoke_proof  # noqa: E402
from minimal_kanban.models import AuditEvent, utc_now_iso  # noqa: E402
from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.change_feed_service import ChangeFeedService  # noqa: E402
from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.storage.change_feed_store import (  # noqa: E402
    CHANGE_FEED_PAGE_MAX,
    ChangeFeedStore,
)
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class ChangeFeedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.state_file = self.base_dir / "state.json"
        self.logger = logging.getLogger(f"test.change_feed.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(self.state_file, logger=self.logger)
        self.feed = ChangeFeedService(
            self.store.change_feed_store,
            reconcile=self.store.reconcile_change_feed,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def append_event(
        self,
        event_id: str,
        *,
        action: str = "card_updated",
        card_id: str | None = "card-1",
        details: dict | None = None,
        actor_name: str = "OWNER",
        message: str = "updated",
    ) -> None:
        events = list(self.store.read_events())
        events.append(
            AuditEvent(
                id=event_id,
                timestamp=utc_now_iso(),
                actor_name=actor_name,
                source="api",
                action=action,
                message=message,
                details=dict(details or {}),
                card_id=card_id,
            )
        )
        self.store.write_events(events)


class ChangeFeedStorageContractTests(ChangeFeedTestCase):
    def test_sequence_is_monotonic_gapless_duplicate_safe_and_projection_is_compact(self) -> None:
        private_name = "PRIVATE-CUSTOMER-NAME"
        private_phone = "+79999999999"
        private_vin = "WVWPRIVATEVIN00001"
        self.append_event(
            "event-1",
            details={"customer_name": private_name, "phone": private_phone, "vin": private_vin},
            actor_name=private_name,
            message=f"{private_phone} {private_vin}",
        )
        self.append_event("event-2", action="card_moved", details={"column": "work"})

        # Rewriting the same legacy audit rows must not allocate more sequences.
        self.store.write_events(list(self.store.read_events()))
        rows = self.store.change_feed_store.raw_events_for_test()

        self.assertEqual([1, 2], [row["sequence"] for row in rows])
        self.assertEqual(["event-1", "event-2"], [row["event_id"] for row in rows])
        self.assertEqual(
            {
                "sequence",
                "event_id",
                "occurred_at",
                "action",
                "entity_type",
                "entity_id",
                "change_type",
                "tombstone",
                "correlation_ref",
                "idempotency_ref",
                "producer",
            },
            set(rows[0]),
        )
        self.assertEqual("audit_event", rows[0]["producer"])
        self.assertRegex(rows[0]["correlation_ref"], r"^corr:[0-9a-f]{24}$")
        self.assertRegex(rows[0]["idempotency_ref"], r"^idem:[0-9a-f]{24}$")
        database_bytes = (self.base_dir / "change_feed.sqlite3").read_bytes()
        if os.name != "nt":
            self.assertEqual(
                0o600,
                stat.S_IMODE((self.base_dir / "change_feed.sqlite3").stat().st_mode),
            )
        for private_value in (private_name, private_phone, private_vin):
            self.assertNotIn(private_value.encode("utf-8"), database_bytes)

    def test_existing_audit_history_becomes_baseline_without_owner_replay(self) -> None:
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        state["events"].append(
            {
                "id": "historical-event",
                "timestamp": utc_now_iso(),
                "actor_name": "OWNER",
                "source": "api",
                "action": "card_updated",
                "message": "historical",
                "details": {},
                "card_id": "card-old",
            }
        )
        replacement_dir = self.base_dir / "replacement"
        replacement_dir.mkdir()
        replacement_state = replacement_dir / "state.json"
        replacement_state.write_text(json.dumps(state), encoding="utf-8")

        replacement = JsonStore(replacement_state, logger=self.logger)
        initial = replacement.change_feed_store.bootstrap("owner")
        self.assertEqual(0, initial["high_water"])
        self.assertFalse(initial["has_unacked"])

        events = list(replacement.read_events())
        events.append(
            AuditEvent(
                id="new-event",
                timestamp=utc_now_iso(),
                actor_name="OWNER",
                source="api",
                action="card_updated",
                message="new",
                details={},
                card_id="card-new",
            )
        )
        replacement.write_events(events)
        page = replacement.change_feed_store.read_page("owner")
        self.assertEqual(["new-event"], [event["event_id"] for event in page["events"]])

    def test_external_state_change_without_timestamp_or_audit_is_reconciled_from_authority(
        self,
    ) -> None:
        service = CardService(self.store, self.logger)
        card_id = service.create_card({"title": "External projection baseline"})["card"]["id"]
        before = self.store.change_feed_store.raw_events_for_test()[-1]["sequence"]

        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        card = next(item for item in state["cards"] if item["id"] == card_id)
        unchanged_updated_at = card["updated_at"]
        card["title"] = "PRIVATE-EXTERNAL-STATE-CHANGE-72A1"
        self.assertEqual(unchanged_updated_at, card["updated_at"])
        self.state_file.write_text(
            json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )

        self.feed.read({"consumer_id": "external-reconcile"})
        rows = [
            row
            for row in self.store.change_feed_store.raw_events_for_test()
            if row["sequence"] > before
        ]
        updates = [
            row
            for row in rows
            if row["entity_type"] == "card"
            and row["entity_id"] == card_id
            and row["change_type"] == "update"
        ]
        self.assertEqual(1, len(updates), rows)
        self.assertEqual("state_projection", updates[0]["producer"])
        self.assertNotIn(
            b"PRIVATE-EXTERNAL-STATE-CHANGE-72A1",
            (self.base_dir / "change_feed.sqlite3").read_bytes(),
        )

    def test_durable_outbox_recovery_commits_only_matching_state_fingerprint(self) -> None:
        path = self.base_dir / "outbox.sqlite3"
        store = ChangeFeedStore(path)
        store.initialize_baseline([])
        event = {
            "id": "event-recovered",
            "timestamp": utc_now_iso(),
            "actor_name": "PRIVATE",
            "source": "api",
            "action": "card_updated",
            "message": "PRIVATE MESSAGE",
            "details": {"phone": "+79999999999"},
            "card_id": "card-1",
        }

        store.prepare_state_write("fingerprint-applied", [event])
        recovered = ChangeFeedStore(path)
        recovered.reconcile_state("fingerprint-applied", [event])
        self.assertEqual([1], [row["sequence"] for row in recovered.raw_events_for_test()])

        second = dict(event, id="event-not-applied")
        recovered.prepare_state_write("fingerprint-not-applied", [event, second])
        recovered.reconcile_state("different-current-state", [event])
        self.assertEqual(
            ["event-recovered"],
            [row["event_id"] for row in recovered.raw_events_for_test()],
        )

        third = dict(event, id="event-after-aborted-stage")
        recovered.prepare_state_write("fingerprint-next", [event, third])
        recovered.commit_state_write("fingerprint-next")
        rows = recovered.raw_events_for_test()
        self.assertEqual([1, 2], [row["sequence"] for row in rows])
        self.assertEqual(
            ["event-recovered", "event-after-aborted-stage"],
            [row["event_id"] for row in rows],
        )

    def test_identical_state_fingerprint_skips_repeated_projection_work(self) -> None:
        path = self.base_dir / "no-op.sqlite3"
        store = ChangeFeedStore(path)
        store.initialize_baseline([])
        state = {"cards": [], "events": []}

        store.prepare_state_write("same-state", [], state=state)
        store.commit_state_write("same-state")

        with patch.object(store, "_source_state", side_effect=AssertionError("unexpected scan")):
            self.assertEqual(0, store.prepare_state_write("same-state", [], state=state))
        self.assertEqual(0, store.commit_state_write("same-state"))
        self.assertEqual([], store.raw_events_for_test())

    def test_restart_publishes_durable_outbox_when_post_state_commit_was_interrupted(
        self,
    ) -> None:
        with patch.object(
            self.store.change_feed_store,
            "commit_state_write",
            side_effect=OSError("simulated publish interruption"),
        ):
            self.append_event("event-after-state-replace")
        self.assertEqual([], self.store.change_feed_store.raw_events_for_test())

        reloaded = JsonStore(self.state_file, logger=self.logger)
        reloaded_feed = ChangeFeedService(
            reloaded.change_feed_store,
            reconcile=reloaded.reconcile_change_feed,
        )
        page = reloaded_feed.read({"consumer_id": "owner"})
        self.assertEqual(
            ["event-after-state-replace"],
            [event["event_id"] for event in page["events"]],
        )

    def test_archive_delete_and_restore_have_explicit_lifecycle_records(self) -> None:
        self.append_event("archive", action="card_archived", card_id="card-9")
        self.append_event(
            "delete",
            action="client_deleted",
            card_id=None,
            details={"client_id": "client-7", "client_name": "PRIVATE"},
        )
        self.append_event("restore", action="card_restored", card_id="card-9")

        events = self.feed.read({"consumer_id": "owner"})["events"]
        self.assertEqual([1, 2, 3], [event["sequence"] for event in events])
        self.assertEqual(
            [
                ("archive", True, "card", "card-9"),
                ("delete", True, "client", "client-7"),
                ("restore", False, "card", "card-9"),
            ],
            [
                (
                    event["change_type"],
                    event["tombstone"],
                    event["entity_type"],
                    event["entity_id"],
                )
                for event in events
            ],
        )

    def test_card_service_archive_and_restore_emit_feed_lifecycle_without_legacy_consumption(
        self,
    ) -> None:
        service = CardService(self.store, self.logger)
        created = service.create_card(
            {"title": "Feed lifecycle", "vehicle": "Test", "actor_name": "OWNER"}
        )
        card_id = created["card"]["id"]
        service.archive_card({"card_id": card_id, "actor_name": "OWNER"})
        service.restore_card({"card_id": card_id, "actor_name": "OWNER"})

        page = self.feed.read({"consumer_id": "owner"})
        lifecycle = [
            (event["action"], event["change_type"], event["tombstone"])
            for event in page["events"]
            if event["action"] in {"card_archived", "card_restored"}
        ]
        self.assertEqual(
            [("card_archived", "archive", True), ("card_restored", "restore", False)],
            lifecycle,
        )
        legacy = service.get_board_events({"event_limit": 10})
        self.assertTrue(
            {"card_archived", "card_restored"} <= {e["action"] for e in legacy["events"]}
        )


class ChangeFeedDeliveryContractTests(ChangeFeedTestCase):
    def test_bootstrap_does_not_open_or_ack_owner_delivery(self) -> None:
        self.append_event("event-1")

        first = self.feed.bootstrap({"consumer_id": "owner"})
        second = self.feed.bootstrap({"consumer_id": "owner"})

        self.assertEqual(0, first["acked_sequence"])
        self.assertIsNone(first["pending_high_water"])
        self.assertEqual(first, second)
        page = self.feed.read({"consumer_id": "owner"})
        self.assertEqual(["event-1"], [event["event_id"] for event in page["events"]])

    def test_replayed_pages_are_stable_and_new_events_wait_for_next_delivery(self) -> None:
        self.append_event("event-1")
        self.append_event("event-2")

        first = self.feed.read({"consumer_id": "owner", "limit": 1})
        first_replay = self.feed.read({"consumer_id": "owner", "limit": 1})
        self.assertEqual(first, first_replay)
        opaque_replay = self.feed.read(
            {"consumer_id": "owner", "cursor": first["replay_cursor"], "limit": 25}
        )
        self.assertEqual(first, opaque_replay)
        self.assertEqual(2, first["high_water"])
        self.assertFalse(first["caught_up"])

        self.append_event("event-3")
        frozen_replay = self.feed.read({"consumer_id": "owner", "limit": 1})
        self.assertEqual(first, frozen_replay)
        second = self.feed.read(
            {"consumer_id": "owner", "limit": 1, "cursor": first["next_cursor"]}
        )
        self.assertEqual(["event-2"], [event["event_id"] for event in second["events"]])
        self.assertTrue(second["caught_up"])

        first_ack = self.feed.ack({"consumer_id": "owner", "ack": first["ack"]})
        self.assertFalse(first_ack["delivery_complete"])
        final_ack = self.feed.ack({"consumer_id": "owner", "ack": second["ack"]})
        self.assertTrue(final_ack["delivery_complete"])
        next_delivery = self.feed.read({"consumer_id": "owner"})
        self.assertEqual(["event-3"], [event["event_id"] for event in next_delivery["events"]])

    def test_ack_is_ordered_idempotent_and_final_page_closes_delivery(self) -> None:
        self.append_event("event-1")
        self.append_event("event-2")
        first = self.feed.read({"consumer_id": "owner", "limit": 1})
        second = self.feed.read(
            {"consumer_id": "owner", "limit": 1, "cursor": first["next_cursor"]}
        )

        with self.assertRaises(ServiceError) as out_of_order:
            self.feed.ack({"consumer_id": "owner", "ack": second["ack"]})
        self.assertEqual("ack_out_of_order", out_of_order.exception.code)

        first_ack = self.feed.ack({"consumer_id": "owner", "ack": first["ack"]})
        first_replay = self.feed.ack({"consumer_id": "owner", "ack": first["ack"]})
        self.assertFalse(first_ack["delivery_complete"])
        self.assertFalse(first_replay["changed"])
        self.assertFalse(first_replay["delivery_complete"])
        final = self.feed.ack({"consumer_id": "owner", "ack": second["ack"]})
        replay = self.feed.ack({"consumer_id": "owner", "ack": second["ack"]})
        self.assertTrue(final["changed"])
        self.assertTrue(final["delivery_complete"])
        self.assertFalse(replay["changed"])
        self.assertEqual(2, replay["acked_sequence"])

        empty = self.feed.read({"consumer_id": "owner"})
        self.assertEqual([], empty["events"])
        self.assertIsNone(empty["ack"])
        self.assertTrue(empty["caught_up"])

    def test_generation_cursor_consumer_and_cursor_tampering_fail_closed(self) -> None:
        self.append_event("event-1")
        self.append_event("event-2")
        first = self.feed.read({"consumer_id": "owner", "limit": 1})
        cursor = first["next_cursor"]
        assert isinstance(cursor, str)

        with self.assertRaises(ServiceError) as wrong_consumer:
            self.feed.read({"consumer_id": "replica", "cursor": cursor})
        self.assertEqual("cursor_consumer_mismatch", wrong_consumer.exception.code)

        replacement = "A" if cursor[-1] != "A" else "B"
        with self.assertRaises(ServiceError) as tampered:
            self.feed.read({"consumer_id": "owner", "cursor": cursor[:-1] + replacement})
        self.assertEqual("invalid_cursor", tampered.exception.code)

        self.store.change_feed_store.rotate_generation()
        with self.assertRaises(ServiceError) as stale_generation:
            self.feed.read({"consumer_id": "owner", "cursor": cursor})
        self.assertEqual("stale_generation", stale_generation.exception.code)
        with self.assertRaises(ServiceError) as stale_ack:
            self.feed.ack({"consumer_id": "owner", "ack": first["ack"]})
        self.assertEqual("stale_generation", stale_ack.exception.code)

    def test_cursor_becomes_stale_after_final_ack(self) -> None:
        self.append_event("event-1")
        self.append_event("event-2")
        first = self.feed.read({"consumer_id": "owner", "limit": 1})
        cursor = first["next_cursor"]
        second = self.feed.read({"consumer_id": "owner", "cursor": cursor, "limit": 1})
        self.feed.ack({"consumer_id": "owner", "ack": first["ack"]})
        self.feed.ack({"consumer_id": "owner", "ack": second["ack"]})

        with self.assertRaises(ServiceError) as stale:
            self.feed.read({"consumer_id": "owner", "cursor": cursor})
        self.assertEqual("stale_cursor", stale.exception.code)

    def test_generation_sequence_delivery_and_ack_survive_restart(self) -> None:
        self.append_event("event-1")
        self.append_event("event-2")
        first = self.feed.read({"consumer_id": "owner", "limit": 1})
        self.feed.ack({"consumer_id": "owner", "ack": first["ack"]})
        generation = first["generation"]

        reloaded_store = JsonStore(self.state_file, logger=self.logger)
        reloaded_feed = ChangeFeedService(
            reloaded_store.change_feed_store,
            reconcile=reloaded_store.reconcile_change_feed,
        )
        status = reloaded_feed.bootstrap({"consumer_id": "owner"})
        self.assertEqual(generation, status["generation"])
        self.assertEqual(2, status["high_water"])
        self.assertEqual(1, status["acked_sequence"])
        self.assertEqual(2, status["pending_high_water"])
        page = reloaded_feed.read({"consumer_id": "owner"})
        self.assertEqual([2], [event["sequence"] for event in page["events"]])

    def test_consumers_are_independent_and_page_size_is_bounded(self) -> None:
        self.append_event("event-1")
        owner_page = self.feed.read({"consumer_id": "owner"})
        replica_page = self.feed.read({"consumer_id": "replica"})
        self.feed.ack({"consumer_id": "owner", "ack": owner_page["ack"]})

        self.assertEqual(1, self.feed.bootstrap({"consumer_id": "owner"})["acked_sequence"])
        self.assertEqual(0, self.feed.bootstrap({"consumer_id": "replica"})["acked_sequence"])
        self.assertEqual(owner_page["events"], replica_page["events"])
        with self.assertRaises(ServiceError) as too_large:
            self.feed.read({"consumer_id": "owner", "limit": CHANGE_FEED_PAGE_MAX + 1})
        self.assertEqual("invalid_limit", too_large.exception.code)


class ChangeFeedHttpContractTests(ChangeFeedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.card_service = CardService(self.store, self.logger)
        self.server = ApiServer(
            self.card_service,
            self.logger,
            start_port=0,
            fallback_limit=10,
            bearer_token="feed-secret",
        )
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        super().tearDown()

    def post(
        self,
        path: str,
        payload: dict,
        *,
        authenticate: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        headers = {"Content-Type": "application/json"}
        if authenticate:
            headers["Authorization"] = "Bearer feed-secret"
        headers.update(extra_headers or {})
        request = urllib.request.Request(
            self.server.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_authenticated_http_contract_and_legacy_event_log_are_independent(self) -> None:
        self.append_event("event-http")
        legacy_before = self.card_service.get_board_events({"event_limit": 10})

        unauthorized_status, unauthorized = self.post(
            "/api/change_feed/bootstrap", {"consumer_id": "owner"}, authenticate=False
        )
        self.assertEqual(401, unauthorized_status)
        self.assertEqual("unauthorized", unauthorized["error"]["code"])

        status, bootstrap = self.post("/api/change_feed/bootstrap", {"consumer_id": "owner"})
        self.assertEqual(200, status)
        self.assertEqual("crm_change_feed_bootstrap_v1", bootstrap["data"]["format"])
        self.assertEqual(0, bootstrap["data"]["acked_sequence"])

        status, page = self.post("/api/change_feed/read", {"consumer_id": "owner"})
        self.assertEqual(200, status)
        self.assertEqual("crm_change_feed_page_v1", page["data"]["format"])
        self.assertEqual(["event-http"], [event["event_id"] for event in page["data"]["events"]])
        status, ack = self.post(
            "/api/change_feed/ack",
            {"consumer_id": "owner", "ack": page["data"]["ack"]},
        )
        self.assertEqual(200, status)
        self.assertEqual("crm_change_feed_ack_v1", ack["data"]["format"])
        self.assertTrue(ack["data"]["delivery_complete"])

        legacy_after = self.card_service.get_board_events({"event_limit": 10})
        self.assertEqual(legacy_before["events"], legacy_after["events"])
        self.assertEqual("event-http", legacy_after["events"][0]["id"])

    def test_maintenance_blocks_checkpoint_writes_but_keeps_feed_reads_available(self) -> None:
        self.append_event("event-maintenance")
        marker = self.base_dir / "maintenance.marker"
        marker.write_text("maintenance", encoding="utf-8")

        mcp_token = "maintenance-release-token"
        revision = "a" * 40
        with patch.dict(
            os.environ,
            {
                "AUTOSTOP_MAINTENANCE_MARKER": str(marker),
                "MINIMAL_KANBAN_MCP_BEARER_TOKEN": mcp_token,
                "AUTOSTOP_AGENT_GATEWAY_ENABLED": "1",
                "AUTOSTOP_AGENT_GATEWAY_WRITES_ENABLED": "1",
                "AUTOSTOP_AGENT_GATEWAY_RAW_ENABLED": "1",
            },
        ):
            status, bootstrap = self.post(
                "/api/change_feed/bootstrap", {"consumer_id": "maintenance-audit"}
            )
            self.assertEqual(503, status)
            self.assertEqual("maintenance_mode", bootstrap["error"]["code"])

            status, ack = self.post(
                "/api/change_feed/ack",
                {"consumer_id": "maintenance-audit", "ack": "not-a-real-ack"},
            )
            self.assertEqual(503, status)
            self.assertEqual("maintenance_mode", ack["error"]["code"])

            status, page = self.post("/api/change_feed/read", {"consumer_id": "maintenance-audit"})
            self.assertEqual(200, status)
            self.assertEqual(
                ["event-maintenance"], [event["event_id"] for event in page["data"]["events"]]
            )

            smoke_headers = {
                "X-Autostop-Agent-Identity": "codex-owner-agent",
                "X-Autostop-Agent-Token": mcp_token,
                "X-Autostop-Release-Smoke-Revision": revision,
                "X-Autostop-Release-Smoke-Proof": release_smoke_proof(mcp_token, revision),
            }
            status, permitted = self.post(
                "/api/change_feed/bootstrap",
                {
                    "consumer_id": "gateway-release-smoke",
                    "source": "mcp_agent_gateway_v2",
                },
                extra_headers=smoke_headers,
            )
            self.assertEqual(200, status)
            self.assertEqual("gateway-release-smoke", permitted["data"]["consumer_id"])

            status, smoke_page = self.post(
                "/api/change_feed/read", {"consumer_id": "gateway-release-smoke"}
            )
            self.assertEqual(200, status)
            smoke_ack = smoke_page["data"]["ack"]
            self.assertIsInstance(smoke_ack, str)

            status, acknowledged = self.post(
                "/api/change_feed/ack",
                {
                    "consumer_id": "gateway-release-smoke",
                    "ack": smoke_ack,
                    "source": "mcp_agent_gateway_v2",
                },
                extra_headers=smoke_headers,
            )
            self.assertEqual(200, status)
            self.assertTrue(acknowledged["data"]["delivery_complete"])

    def test_http_protocol_errors_use_stable_codes(self) -> None:
        status, missing_consumer = self.post("/api/change_feed/read", {})
        self.assertEqual(400, status)
        self.assertEqual("validation_error", missing_consumer["error"]["code"])

        status, invalid_consumer = self.post("/api/change_feed/read", {"consumer_id": 123})
        self.assertEqual(400, status)
        self.assertEqual("invalid_consumer", invalid_consumer["error"]["code"])

        status, bad_cursor = self.post(
            "/api/change_feed/read", {"consumer_id": "owner", "cursor": "not-a-cursor"}
        )
        self.assertEqual(400, status)
        self.assertEqual("invalid_cursor", bad_cursor["error"]["code"])

        status, empty_cursor = self.post(
            "/api/change_feed/read", {"consumer_id": "owner", "cursor": ""}
        )
        self.assertEqual(400, status)
        self.assertEqual("invalid_cursor", empty_cursor["error"]["code"])


if __name__ == "__main__":
    unittest.main()
