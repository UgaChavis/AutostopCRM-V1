from __future__ import annotations

import hashlib
import json
from pathlib import Path

if __package__:
    from tests.source_path_support import prepend_source_path
else:
    from source_path_support import prepend_source_path

prepend_source_path()

from minimal_kanban.storage.audit_archive import AuditArchiveStore, compact_audit_event_details
from scripts import clear_financial_history, client_data_quality_maintenance, compact_audit_events
from tests.services_case import CardServiceCase


class MaintenanceStateIntegrityTests(CardServiceCase):
    def setUp(self) -> None:
        super().setUp()
        self.service = self._build_service()
        box = self.service.create_cashbox({"name": "Synthetic maintenance cash"})["cashbox"]
        card = self.service.create_card({"title": "Synthetic closed order"})["card"]
        self.service.update_repair_order(
            {
                "card_id": card["id"],
                "repair_order": {
                    "works": [{"name": "Synthetic work", "quantity": "1", "price": "100"}],
                    "payments": [{"amount": "100", "cashbox_id": box["id"]}],
                },
            }
        )
        self.service.set_repair_order_status({"card_id": card["id"], "status": "closed"})
        self.state = json.loads(self.state_file.read_bytes())
        self.assertTrue(self.state["cards"][0]["repair_order"]["cycles"])
        self.writers = (
            clear_financial_history._write_state_file,
            compact_audit_events.write_state_file,
            client_data_quality_maintenance._write_state,
        )

    def test_all_maintenance_writers_preserve_real_closed_cycle_and_json_types(self) -> None:
        for writer in self.writers:
            with self.subTest(writer=writer.__module__):
                writer(self.state_file, self.state)
                self.assertEqual(json.loads(self.state_file.read_bytes()), self.state)

    def test_writers_reject_nonfinite_values_before_changing_state(self) -> None:
        before = self.state_file.read_bytes()
        for writer in self.writers:
            with self.subTest(writer=writer.__module__):
                with self.assertRaises(ValueError):
                    writer(self.state_file, {**self.state, "invalid": float("inf")})
                self.assertEqual(self.state_file.read_bytes(), before)

    def test_writers_reject_excessive_depth_instead_of_converting_it_to_text(self) -> None:
        nested = {}
        for _ in range(520):
            nested = {"child": nested}
        before = self.state_file.read_bytes()
        for writer in self.writers:
            with self.subTest(writer=writer.__module__):
                with self.assertRaisesRegex(ValueError, "deeply nested"):
                    writer(self.state_file, {**self.state, "invalid": nested})
                self.assertEqual(self.state_file.read_bytes(), before)

    def test_compaction_apply_preserves_cycle_and_matches_dry_run_size(self) -> None:
        event = {
            "id": "synthetic-heavy-event",
            "timestamp": "2026-09-01T00:00:00+00:00",
            "action": "description_changed",
            "details": {"before": "old" * 500, "after": "new" * 500},
        }
        state = {**self.state, "events": [event]}
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.state_file.read_bytes()
        dry_run = compact_audit_events.compact_state_file(self.state_file)
        self.assertEqual(self.state_file.read_bytes(), before)
        applied = compact_audit_events.compact_state_file(self.state_file, apply=True, backup=True)
        actual = json.loads(self.state_file.read_bytes())
        self.assertEqual(Path(applied.backup_file).read_bytes(), before)
        self.assertEqual(applied.events_compacted, 1)
        self.assertEqual(
            {key: value for key, value in actual.items() if key != "events"},
            {key: value for key, value in state.items() if key != "events"},
        )
        self.assertEqual(dry_run.estimated_state_bytes_after, self.state_file.stat().st_size)

    def test_client_quality_apply_preserves_closed_order_and_finance(self) -> None:
        state = {
            **self.state,
            "clients": [
                {
                    "id": "synthetic-client",
                    "display_name": "Synthetic client",
                    "vehicles": [{"id": "synthetic-vehicle", "vin": "NONE"}],
                }
            ],
        }
        self.state_file.write_text(json.dumps(state), encoding="utf-8")
        before = self.state_file.read_bytes()
        applied = client_data_quality_maintenance.apply_client_data_quality_plan(
            self.state_file, backup=True
        )
        actual = json.loads(self.state_file.read_bytes())
        self.assertTrue(applied["applied"])
        self.assertEqual(Path(applied["backup_file"]).read_bytes(), before)
        self.assertEqual(actual["clients"][0]["vehicles"][0]["vin"], "")
        self.assertEqual(
            {key: value for key, value in actual.items() if key not in {"clients", "events"}},
            {key: value for key, value in state.items() if key not in {"clients", "events"}},
        )

    def test_archive_keeps_full_closed_order_details_and_matching_fingerprint(self) -> None:
        archive = AuditArchiveStore(Path(self.temp_dir.name) / "synthetic-archive")
        order = self.state["cards"][0]["repair_order"]
        details = {"before": order, "after": order}
        result = archive.archive_details(
            event_id="synthetic-archive-event",
            action="repair_order_updated",
            card_id="synthetic-card",
            timestamp="2026-09-01T00:00:00+00:00",
            details=details,
        )
        self.assertEqual(archive.load_details(result.ref), details)
        compact = compact_audit_event_details(
            action="repair_order_updated", details=details, archive_ref=result.ref
        )
        self.assertEqual(
            compact["after_sha256"],
            hashlib.sha256(
                json.dumps(order, ensure_ascii=False, sort_keys=True, allow_nan=False).encode(
                    "utf-8"
                )
            ).hexdigest(),
        )

    def test_archive_reader_skips_nonobject_json_without_rewriting_file(self) -> None:
        archive_dir = Path(self.temp_dir.name) / "synthetic-archive"
        archive_dir.mkdir()
        path = archive_dir / "2026-09.jsonl"
        records = [[], None, 42, True, "text", {"event_id": "wanted", "details": {"ok": True}}]
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        before = path.read_bytes()
        archive = AuditArchiveStore(archive_dir)
        self.assertEqual(archive.load_details("2026-09.jsonl#wanted"), {"ok": True})
        self.assertEqual(path.read_bytes(), before)
