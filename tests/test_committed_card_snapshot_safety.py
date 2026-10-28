from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import json
import math
import os
import random
import struct
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import orjson

from tests.services_case import CardServiceCase
from minimal_kanban.json_safety import DEFAULT_JSON_MAX_DEPTH, reject_deeply_nested_json
from minimal_kanban.models import AuditEvent, utc_now, utc_now_iso
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage import json_store
from minimal_kanban.storage import readonly_context_snapshot
from minimal_kanban.storage.json_store import StateFileCorruptedError
from minimal_kanban.storage.readonly_context_snapshot import reject_deeply_nested_native_json


class CommittedCardSnapshotSafetyTests(CardServiceCase):
    def setUp(self):
        self.app_data = tempfile.TemporaryDirectory(prefix="synthetic-committed-card-appdata-")
        self.addCleanup(self.app_data.cleanup)
        env = patch.dict(os.environ, {"APPDATA": self.app_data.name})
        env.start()
        self.addCleanup(env.stop)
        super().setUp()
        self.service = self._build_service()
        self.client_a = self.service.create_client(
            {"display_name": "SYNTHETIC_A", "vehicles": [{"vehicle": "SYNTHETIC_A_CAR"}]}
        )["client"]
        self.client_b = self.service.create_client(
            {"display_name": "SYNTHETIC_B", "vehicles": [{"vehicle": "SYNTHETIC_B_CAR"}]}
        )["client"]
        self.card_id = self.service.create_card(
            {
                "title": "SYNTHETIC_TITLE_ONE",
                "client_id": self.client_a["id"],
                "client_vehicle_id": self.client_a["vehicles"][0]["id"],
            }
        )["card"]["id"]
        self.commit_bundle()

    def commit_bundle(self):
        bundle = self.store.read_bundle()
        return self.store.write_cached_bundle(bundle, **bundle)

    def files(self):
        roots = (Path(self.temp_dir.name), Path(self.app_data.name))
        return {
            (index, str(path.relative_to(root))): (path.stat().st_mtime_ns, path.read_bytes())
            for index, root in enumerate(roots)
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_confirmed_write_serves_detached_card_without_a_second_state_read(self):
        before = self.files()
        with patch.object(self.store, "_read_state", wraps=self.store._read_state) as read:
            first = self.service.get_card({"card_id": self.card_id})
            first["card"]["title"] = "SYNTHETIC_CALLER_EDIT"
            first["card"]["repair_order"]["works"].append({"name": "SYNTHETIC_EDIT"})
            second = self.service.get_card({"card_id": self.card_id})
            if sys.platform != "win32":
                self.assertEqual(
                    read.call_count, 2 if json_store._SNAPSHOT_REQUIRES_FILE_LOCK else 0
                )
        self.assertEqual(second["card"]["title"], "SYNTHETIC_TITLE_ONE")
        self.assertEqual(second["card"]["repair_order"]["works"], [])
        self.assertEqual(second["card"]["client_vehicle_id"], self.client_a["vehicles"][0]["id"])
        self.assertEqual(before, self.files())

    def test_mutating_the_writable_bundle_does_not_change_committed_reads(self):
        bundle = self.store.read_bundle()
        card = next(card for card in bundle["cards"] if card.id == self.card_id)
        client = next(client for client in bundle["clients"] if client.id == self.client_a["id"])
        event = next(event for event in bundle["events"] if event.card_id == self.card_id)
        expected_events = self.service.get_card({"card_id": self.card_id})["card"]["events_count"]
        before = self.files()
        card.title = "SYNTHETIC_NOT_COMMITTED"
        card.client_vehicle_id = self.client_b["vehicles"][0]["id"]
        client.vehicles.clear()
        event.card_id = None
        result = self.service.get_card({"card_id": self.card_id})
        self.assertEqual(result["card"]["title"], "SYNTHETIC_TITLE_ONE")
        self.assertEqual(result["card"]["client_vehicle_id"], self.client_a["vehicles"][0]["id"])
        self.assertEqual(result["card"]["events_count"], expected_events)
        self.assertEqual(before, self.files())

    def test_fast_and_standard_card_responses_match(self):
        moment = utc_now()
        before = self.files()
        with patch("minimal_kanban.models.utc_now", return_value=moment):
            fast = self.service.get_card({"card_id": self.card_id})
            with patch.object(self.store, "_committed_card_source", None):
                slow = self.service.get_card({"card_id": self.card_id})
        self.assertEqual(fast, slow)
        self.assertEqual(before, self.files())

    def test_exact_id_and_client_links_are_checked_after_write(self):
        with self.assertRaises(ServiceError) as missing:
            self.service.get_card({"card_id": self.card_id[:8]})
        self.assertEqual(missing.exception.code, "not_found")

        for client_id, vehicle_id in (
            (self.client_a["id"], self.client_b["vehicles"][0]["id"]),
            ("SYNTHETIC_MISSING_CLIENT", ""),
        ):
            with self.subTest(client_id=client_id):
                bundle = self.store.read_bundle()
                cards = list(bundle["cards"])
                index = next(i for i, card in enumerate(cards) if card.id == self.card_id)
                cards[index] = deepcopy(cards[index])
                cards[index].client_id = client_id
                cards[index].client_vehicle_id = vehicle_id
                self.store.write_cached_bundle(bundle, **{**bundle, "cards": cards})
                before = self.files()
                with self.assertRaises(StateFileCorruptedError):
                    self.service.get_card({"card_id": self.card_id})
                self.assertEqual(before, self.files())

    def test_cache_preparation_failure_does_not_reclassify_a_committed_write(self):
        with patch.object(self.store, "_prime_committed_card_source", side_effect=OSError):
            result = self.service.update_card(
                {"card_id": self.card_id, "title": "SYNTHETIC_COMMITTED"}
            )
        self.assertEqual(result["card"]["title"], "SYNTHETIC_COMMITTED")
        self.assertIsNone(self.store._committed_card_source)
        self.assertEqual(
            self.service.get_card({"card_id": self.card_id})["card"]["title"],
            "SYNTHETIC_COMMITTED",
        )

    def test_failed_write_discards_any_previously_committed_cache(self):
        bundle = self.store.read_bundle()
        before = self.files()
        with patch.object(self.store, "_write_state", side_effect=OSError):
            with self.assertRaises(OSError):
                self.store.write_cached_bundle(bundle, **bundle)
        self.assertIsNone(self.store._committed_card_source)
        self.assertEqual(
            self.service.get_card({"card_id": self.card_id})["card"]["id"], self.card_id
        )
        self.assertEqual(before, self.files())

    def test_external_edit_during_fast_read_retries_from_disk(self):
        cached = self.store._committed_card_source
        self.assertIsNotNone(cached)
        source = cached[2]
        old = self.state_file.stat()
        data = self.state_file.read_bytes()
        self.assertIn(b"SYNTHETIC_TITLE_ONE", data)
        changed = data.replace(b"SYNTHETIC_TITLE_ONE", b"SYNTHETIC_TITLE_TWO")
        original = source.for_card
        changed_during_read = []

        def replace_after_snapshot(*args):
            snapshot = original(*args)
            if sys.platform == "win32":
                # The open verifier must exclude a writer until this snapshot is built.
                with self.assertRaises(OSError):
                    self.state_file.write_bytes(changed)
            else:
                self.state_file.write_bytes(changed)
                os.utime(self.state_file, ns=(old.st_atime_ns, old.st_mtime_ns))
            changed_during_read.append(True)
            return snapshot

        with patch.object(source, "for_card", side_effect=replace_after_snapshot):
            result = self.service.get_card({"card_id": self.card_id})
        self.assertTrue(changed_during_read)
        self.assertEqual(
            result["card"]["title"],
            "SYNTHETIC_TITLE_ONE" if sys.platform == "win32" else "SYNTHETIC_TITLE_TWO",
        )
        self.assertEqual(self.state_file.read_bytes(), data if sys.platform == "win32" else changed)

    def test_external_edit_during_cache_verification_prevents_fast_path(self):
        cached = self.store._committed_card_source
        self.assertIsNotNone(cached)
        payload = cached[1]
        old = self.state_file.stat()
        changed = payload.replace(b"SYNTHETIC_TITLE_ONE", b"SYNTHETIC_TITLE_TWO")
        original_read = json_store.read_bytes_limited
        self.store._committed_card_source = None

        def read_then_external_edit(*args, **kwargs):
            result = original_read(*args, **kwargs)
            self.state_file.write_bytes(changed)
            os.utime(self.state_file, ns=(old.st_atime_ns, old.st_mtime_ns))
            return result

        with patch(
            "minimal_kanban.storage.json_store.read_bytes_limited",
            side_effect=read_then_external_edit,
        ):
            self.store._prime_committed_card_source(payload)
        if sys.platform != "win32":
            self.assertIsNone(self.store._committed_card_source)
        self.assertEqual(
            self.service.get_card({"card_id": self.card_id})["card"]["title"],
            "SYNTHETIC_TITLE_TWO",
        )
        self.assertEqual(self.state_file.read_bytes(), changed)

    def test_event_visibility_matches_the_standard_snapshot_for_each_operator(self):
        bundle = self.store.read_bundle()
        timestamp = utc_now_iso()
        events = list(bundle["events"])
        for event_id, action, details in (
            ("synthetic-public", "card_updated", {}),
            ("synthetic-private", "cashbox_updated", {}),
            ("synthetic-order", "repair_order_updated", {"payment": "SYNTHETIC"}),
        ):
            events.append(
                AuditEvent(
                    event_id,
                    timestamp,
                    "SYNTHETIC",
                    "api",
                    action,
                    "SYNTHETIC",
                    details,
                    self.card_id,
                )
            )
        self.store.write_cached_bundle(bundle, **{**bundle, "events": events})
        before = self.files()
        counts = []
        for session in (None, {"permissions": []}):
            payload = {"card_id": self.card_id, "_operator_session": session}
            fast = self.service.get_card(payload)
            with patch.object(self.store, "_committed_card_source", None):
                slow = self.service.get_card(payload)
            self.assertEqual(fast["card"]["events_count"], slow["card"]["events_count"])
            counts.append(fast["card"]["events_count"])
        self.assertEqual(counts[0], counts[1] + 1)
        self.assertEqual(before, self.files())

    def test_unverified_bytes_do_not_prime_the_fast_path(self):
        cached = self.store._committed_card_source
        self.assertIsNotNone(cached)
        payload = cached[1]
        self.store._committed_card_source = None
        before = self.files()
        with patch("minimal_kanban.storage.json_store.read_bytes_limited", return_value=b"wrong"):
            self.store._prime_committed_card_source(payload)
        self.assertIsNone(self.store._committed_card_source)
        self.assertEqual(
            self.service.get_card({"card_id": self.card_id})["card"]["id"], self.card_id
        )
        self.assertEqual(before, self.files())

    def test_out_of_range_integer_uses_the_standard_snapshot_path(self):
        bundle = self.store.read_bundle()
        settings = {**bundle["settings"], "synthetic_large_integer": 10**100}
        self.store.write_cached_bundle(bundle, **{**bundle, "settings": settings})
        self.assertIsNone(self.store._committed_card_source)
        before = self.files()
        with patch.object(self.store, "_read_state", wraps=self.store._read_state) as read:
            result = self.service.get_card({"card_id": self.card_id})
            self.assertEqual(read.call_count, 1)
        self.assertEqual(result["card"]["id"], self.card_id)
        self.assertEqual(
            json.loads(self.state_file.read_text())["settings"]["synthetic_large_integer"], 10**100
        )
        self.assertEqual(before, self.files())

    def test_depth_is_walked_when_the_encoder_cannot_prove_its_limit(self):
        cached = self.store._committed_card_source
        self.assertIsNotNone(cached)
        self.store._committed_card_source = None
        with (
            patch.object(readonly_context_snapshot, "_ENCODER_ENFORCES_STATE_DEPTH", False),
            patch.object(
                readonly_context_snapshot,
                "reject_deeply_nested_native_json",
                wraps=reject_deeply_nested_native_json,
            ) as check,
        ):
            self.store._prime_committed_card_source(cached[1])
        self.assertIsNotNone(self.store._committed_card_source)
        check.assert_called_once()

    def test_native_depth_check_matches_the_general_json_depth_limit(self):
        for depth in (
            0,
            1,
            DEFAULT_JSON_MAX_DEPTH - 1,
            DEFAULT_JSON_MAX_DEPTH,
            DEFAULT_JSON_MAX_DEPTH + 1,
        ):
            for kind in ("dict", "list", "mixed"):
                value = "SYNTHETIC"
                for index in range(depth):
                    value = (
                        {"child": value}
                        if kind == "dict" or (kind == "mixed" and index % 2)
                        else [value]
                    )
                outcomes = []
                for check in (reject_deeply_nested_json, reject_deeply_nested_native_json):
                    try:
                        check(value)
                        outcomes.append("ok")
                    except ValueError:
                        outcomes.append("rejected")
                self.assertEqual(outcomes[0], outcomes[1], (depth, kind))

    def test_fast_encoder_decoder_preserves_standard_json_number_values(self):
        randomizer = random.Random(20261028)
        values = [-(2**63), 2**64 - 1, 2**53 + 1, -0.0, "SYNTHETIC_Юникод"]
        for _ in range(2048):
            value = struct.unpack("!d", randomizer.getrandbits(64).to_bytes(8, "big"))[0]
            if math.isfinite(value):
                values.append(value)
        payload = orjson.dumps({"values": values})
        standard = json.loads(payload)["values"]
        accelerated = orjson.loads(payload)["values"]
        for expected, actual in zip(standard, accelerated, strict=True):
            self.assertIs(type(actual), type(expected))
            if isinstance(expected, float):
                self.assertEqual(actual.hex(), expected.hex())
            else:
                self.assertEqual(actual, expected)
