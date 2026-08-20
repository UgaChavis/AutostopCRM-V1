from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.models import Card, ClientProfile  # noqa: E402
from minimal_kanban.printing import service as printing_service_module  # noqa: E402
from minimal_kanban.printing.errors import PrintModuleError  # noqa: E402
from minimal_kanban.printing.models import (  # noqa: E402
    PrintModuleSettings,
    PrintServiceProfile,
)
from minimal_kanban.printing.service import PrintModuleService  # noqa: E402
from minimal_kanban.printing.template_engine import render_template  # noqa: E402
from minimal_kanban.storage.change_feed_projection import project_print_module  # noqa: E402
from minimal_kanban.storage.change_feed_store import ChangeFeedStore  # noqa: E402


def build_card(*, client_id: str = "client-exact", row_count: int = 2) -> Card:
    works = [
        {
            "id": f"work-{index + 1}",
            "name": f"Работа {index + 1}",
            "quantity": "1",
            "price": "1000",
        }
        for index in range(row_count)
    ]
    return Card.from_dict(
        {
            "id": "card-act-1",
            "client_id": client_id,
            "vehicle": "Test vehicle",
            "title": "Act test",
            "description": "Synthetic test card",
            "column": "inbox",
            "archived": False,
            "created_at": "2026-08-20T10:00:00+00:00",
            "updated_at": "2026-08-20T10:00:00+00:00",
            "deadline_timestamp": "2026-08-21T10:00:00+00:00",
            "repair_order": {
                "number": "10700",
                "date": "20.08.2026",
                "opened_at": "2026-08-20T10:00:00+00:00",
                "client": "Заказчик из заказ-наряда",
                "works": works,
                "materials": [
                    {
                        "id": "material-1",
                        "name": "Материал 1",
                        "inventory_unit": "шт",
                        "quantity": "2",
                        "price": "500",
                    }
                ],
            },
        }
    )


def build_client(*, client_id: str = "client-exact") -> ClientProfile:
    return ClientProfile.from_dict(
        {
            "id": client_id,
            "client_type": "ooo",
            "display_name": "ООО Точный клиент",
            "legal_name": "ООО Точный клиент",
            "inn": "2468000000",
            "kpp": "246801001",
            "legal_address": "Красноярск, ул. Тестовая, 1",
            "contact_position": "Директор",
            "contact_person": "Иванов И.И.",
        }
    )


class CompletionActBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        self.service = PrintModuleService(self.base_dir)
        self.card = build_card()
        self.client = build_client()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_defaults_use_exact_linked_client_and_fixed_vat(self) -> None:
        loaded = self.service.get_completion_act_form(self.card, client=self.client)

        self.assertEqual(loaded["form"]["document_number"], "10700")
        self.assertEqual(loaded["form"]["basis"], "")
        self.assertEqual(loaded["sources"]["basis"], "empty")
        self.assertEqual(
            loaded["form"]["acceptance_text"],
            "Вышеперечисленные работы (услуги) выполнены полностью и в срок. "
            "Заказчик претензий по объему, качеству и срокам оказания услуг не имеет.",
        )
        self.assertEqual(loaded["form"]["customer"]["legal_name"], "ООО Точный клиент")
        self.assertEqual(loaded["form"]["customer"]["inn"], "2468000000")
        self.assertEqual(
            [item["section"] for item in loaded["form"]["items"]],
            [
                "works",
                "works",
                "materials",
            ],
        )
        self.assertEqual(loaded["totals"]["base"], "3000.00")
        self.assertEqual(loaded["totals"]["vat"], "150.00")
        self.assertEqual(loaded["totals"]["gross"], "3150.00")

        unrelated = build_client(client_id="client-other")
        fallback = self.service.get_completion_act_form(self.card, client=unrelated)
        self.assertEqual(
            fallback["form"]["customer"]["legal_name"],
            "Заказчик из заказ-наряда",
        )
        self.assertEqual(fallback["form"]["customer"]["inn"], "")

    def test_sparse_draft_persists_is_stale_and_does_not_mutate_card(self) -> None:
        before = deepcopy(self.card.to_storage_dict())
        initial = self.service.get_completion_act_form(self.card, client=self.client)
        changed = deepcopy(initial["form"])
        changed["document_number"] = "ACT-MANUAL"
        saved = self.service.save_completion_act_form(
            self.card,
            client=self.client,
            form_data=changed,
            expected_version=0,
            idempotency_key="save-act-1",
            filled_by="tester",
        )

        self.assertEqual(before, self.card.to_storage_dict())
        self.assertEqual(saved["draft"]["version"], 1)
        self.assertEqual(saved["form"]["document_number"], "ACT-MANUAL")
        cycle_key = self.service._completion_act_cycle_key(self.card, self.card.repair_order)
        record_path = self.service._completion_act_record_path(cycle_key)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["overrides"], {"document_number": "ACT-MANUAL"})

        restarted = PrintModuleService(self.base_dir)
        persisted = restarted.get_completion_act_form(self.card, client=self.client)
        self.assertEqual(persisted["form"]["document_number"], "ACT-MANUAL")
        self.assertFalse(persisted["draft"]["is_stale"])

        renamed_card = Card.from_dict({**self.card.to_storage_dict(), "title": "New title"})
        renamed_payload = renamed_card.to_storage_dict()
        renamed_payload["column"] = "working"
        renamed_payload["tags"] = [{"label": "Не влияет", "color": "blue"}]
        renamed_card = Card.from_dict(renamed_payload)
        renamed = restarted.get_completion_act_form(renamed_card, client=self.client)
        self.assertFalse(renamed["draft"]["is_stale"])
        self.assertEqual(
            persisted["draft"]["current_source_fingerprint"],
            renamed["draft"]["current_source_fingerprint"],
        )
        unrelated_order_changes = self.card.repair_order.to_storage_dict()
        unrelated_order_changes.update(
            {
                "status": "closed",
                "vin": "UNRELATEDVIN123456",
                "phone": "+7 900 000-00-00",
                "payment_method": "cashless",
                "payments": [
                    {
                        "id": "unrelated-payment",
                        "amount": "10",
                        "payment_method": "cashless",
                    }
                ],
            }
        )
        unrelated_card = Card.from_dict(
            {**self.card.to_storage_dict(), "repair_order": unrelated_order_changes}
        )
        unrelated_client_payload = self.client.to_storage_dict()
        unrelated_client_payload.update(
            {"phone": "+7 901 000-00-00", "email": "unrelated@example.test"}
        )
        unrelated_client = ClientProfile.from_dict(unrelated_client_payload)
        unrelated = restarted.get_completion_act_form(
            unrelated_card,
            client=unrelated_client,
        )
        self.assertFalse(unrelated["draft"]["is_stale"])
        self.assertEqual(
            persisted["draft"]["current_source_fingerprint"],
            unrelated["draft"]["current_source_fingerprint"],
        )
        changed_order = self.card.repair_order.to_storage_dict()
        changed_order["works"][0]["price"] = "1250"
        changed_card = Card.from_dict(
            {**self.card.to_storage_dict(), "repair_order": changed_order}
        )
        stale = restarted.get_completion_act_form(changed_card, client=self.client)
        self.assertTrue(stale["draft"]["is_stale"])
        self.assertEqual(stale["form"]["document_number"], "ACT-MANUAL")
        self.assertEqual(stale["fresh_form"]["items"][0]["price"], "1250")

    def test_save_rejects_source_change_after_editor_read(self) -> None:
        initial = self.service.get_completion_act_form(self.card, client=self.client)
        submitted = deepcopy(initial["form"])
        submitted["basis"] = "Only the basis was edited"
        changed_order = self.card.repair_order.to_storage_dict()
        changed_order["works"][0]["price"] = "1250"
        changed_card = Card.from_dict(
            {**self.card.to_storage_dict(), "repair_order": changed_order}
        )

        with self.assertRaises(PrintModuleError) as captured:
            self.service.save_completion_act_form(
                changed_card,
                client=self.client,
                form_data=submitted,
                expected_version=0,
                expected_source_fingerprint=initial["draft"]["current_source_fingerprint"],
                idempotency_key="source-conflict-save",
            )

        self.assertEqual(captured.exception.code, "completion_act_source_conflict")
        self.assertEqual(captured.exception.status_code, 409)
        current = self.service.get_completion_act_form(changed_card, client=self.client)
        self.assertFalse(current["draft"]["exists"])
        self.assertEqual(current["draft"]["version"], 0)
        self.assertEqual(current["fresh_form"]["items"][0]["price"], "1250")

    def test_changed_items_are_stored_as_one_full_snapshot(self) -> None:
        initial = self.service.get_completion_act_form(self.card, client=self.client)
        changed = deepcopy(initial["form"])
        changed["items"] = [
            deepcopy(changed["items"][2]),
            {**deepcopy(changed["items"][0]), "name": "Ручная работа"},
        ]

        saved = self.service.save_completion_act_form(
            self.card,
            client=self.client,
            form_data=changed,
            expected_version=0,
            idempotency_key="items-full-snapshot",
        )

        cycle_key = self.service._completion_act_cycle_key(self.card, self.card.repair_order)
        record = json.loads(
            self.service._completion_act_record_path(cycle_key).read_text(encoding="utf-8")
        )
        self.assertEqual(set(record["overrides"]), {"items"})
        self.assertEqual(record["overrides"]["items"], saved["form"]["items"])
        self.assertEqual(
            [item["name"] for item in saved["form"]["items"]],
            ["Материал 1", "Ручная работа"],
        )

    def test_completion_act_store_write_is_atomic_and_bounded(self) -> None:
        self.assertEqual(printing_service_module.PRINT_JSON_FILE_MAX_BYTES, 1024 * 1024)
        self.assertEqual(
            printing_service_module.COMPLETION_ACT_FORMS_FILE_MAX_BYTES,
            64 * 1024 * 1024,
        )
        self.assertEqual(
            printing_service_module.COMPLETION_ACT_FORM_RECORD_MAX_BYTES,
            1024 * 1024,
        )
        form = self.service.get_completion_act_form(self.card, client=self.client)["form"]
        form["basis"] = "Первая версия"
        self.service.save_completion_act_form(
            self.card,
            client=self.client,
            form_data=form,
            expected_version=0,
            idempotency_key="atomic-baseline",
        )
        cycle_key = self.service._completion_act_cycle_key(self.card, self.card.repair_order)
        store_path = self.service._completion_act_record_path(cycle_key)
        baseline = store_path.read_bytes()
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(store_path.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(store_path.stat().st_mode), 0o600)

        changed = deepcopy(form)
        changed["basis"] = "Вторая версия"
        original_replace = os.replace

        def fail_completion_store_replace(source: Path, target: Path) -> None:
            if Path(target) == store_path and Path(source).name.startswith(f".{store_path.stem}."):
                raise OSError("simulated atomic replace failure")
            return original_replace(source, target)

        with patch.object(
            printing_service_module.os,
            "replace",
            side_effect=fail_completion_store_replace,
        ):
            with self.assertRaisesRegex(OSError, "atomic replace"):
                self.service.save_completion_act_form(
                    self.card,
                    client=self.client,
                    form_data=changed,
                    expected_version=1,
                    idempotency_key="atomic-failure",
                )
        self.assertEqual(store_path.read_bytes(), baseline)
        self.assertEqual(list(store_path.parent.glob(f".{store_path.stem}.*.tmp")), [])

        oversized_form = deepcopy(changed)
        oversized_form["basis"] = "x" * 500
        with patch.object(
            printing_service_module,
            "COMPLETION_ACT_FORM_RECORD_MAX_BYTES",
            len(baseline) + 64,
        ):
            with self.assertRaises(PrintModuleError) as oversized:
                self.service.save_completion_act_form(
                    self.card,
                    client=self.client,
                    form_data=oversized_form,
                    expected_version=1,
                    idempotency_key="bounded-write",
                )
        self.assertEqual(oversized.exception.code, "validation_error")
        self.assertEqual(store_path.read_bytes(), baseline)

        store_path.write_text(json.dumps({"padding": "x" * 256}), encoding="utf-8")
        oversized_store = store_path.read_bytes()
        with patch.object(printing_service_module, "COMPLETION_ACT_FORM_RECORD_MAX_BYTES", 64):
            with self.assertRaises(PrintModuleError) as corrupt_read:
                self.service.get_completion_act_form(self.card, client=self.client)
            with self.assertRaises(PrintModuleError) as corrupt_save:
                self.service.save_completion_act_form(
                    self.card,
                    client=self.client,
                    form_data=form,
                    expected_version=0,
                    idempotency_key="must-not-overwrite-corrupt-store",
                )
        self.assertEqual(corrupt_read.exception.code, "completion_act_store_corrupt")
        self.assertEqual(corrupt_read.exception.status_code, 503)
        self.assertEqual(corrupt_save.exception.code, "completion_act_store_corrupt")
        self.assertEqual(store_path.read_bytes(), oversized_store)

        store_path.write_text("{broken-json", encoding="utf-8")
        malformed_store = store_path.read_bytes()
        with self.assertRaises(PrintModuleError) as malformed:
            self.service.get_completion_act_form(self.card, client=self.client)
        self.assertEqual(malformed.exception.code, "completion_act_store_corrupt")
        self.assertEqual(malformed.exception.status_code, 503)
        self.assertEqual(store_path.read_bytes(), malformed_store)

    def test_legacy_store_migrates_idempotently_and_preserves_newer_shard(self) -> None:
        cycle_key = self.service._completion_act_cycle_key(self.card, self.card.repair_order)
        legacy_path = self.service._completion_act_forms_path
        legacy_record = {
            "version": 1,
            "overrides": {"document_number": "LEGACY-1"},
            "source_fingerprint": "legacy-source",
            "updated_at": "2026-08-20T12:00:00+00:00",
            "filled_by": "migration-test",
            "source": "manual",
            "idempotency_key": "legacy-save",
            "request_fingerprint": "legacy-request",
            "operation": "save",
            "deleted": False,
        }
        legacy_path.write_text(
            json.dumps({cycle_key: legacy_record}, ensure_ascii=False),
            encoding="utf-8",
        )
        if os.name != "nt":
            legacy_path.chmod(0o600)

        migrated = PrintModuleService(self.base_dir)
        loaded = migrated.get_completion_act_form(self.card, client=self.client)

        self.assertEqual(loaded["draft"]["version"], 1)
        self.assertEqual(loaded["form"]["document_number"], "LEGACY-1")
        self.assertFalse(legacy_path.exists())
        shard_path = migrated._completion_act_record_path(cycle_key)
        shard_payload = json.loads(shard_path.read_text(encoding="utf-8"))
        self.assertEqual(shard_payload["cycle_key"], cycle_key)
        self.assertEqual(shard_payload["version"], 1)

        newer_payload = dict(shard_payload)
        newer_payload.update(
            {
                "version": 2,
                "overrides": {"document_number": "SHARD-2"},
                "idempotency_key": "newer-save",
            }
        )
        shard_path.write_text(json.dumps(newer_payload, ensure_ascii=False), encoding="utf-8")
        legacy_path.write_text(
            json.dumps({cycle_key: legacy_record}, ensure_ascii=False),
            encoding="utf-8",
        )
        if os.name != "nt":
            legacy_path.chmod(0o600)

        retried = PrintModuleService(self.base_dir)
        retry_loaded = retried.get_completion_act_form(self.card, client=self.client)
        self.assertEqual(retry_loaded["draft"]["version"], 2)
        self.assertEqual(retry_loaded["form"]["document_number"], "SHARD-2")
        self.assertFalse(legacy_path.exists())

    def test_shard_paths_are_hashed_and_symlinks_fail_closed(self) -> None:
        traversal_key = "../../outside:cycle:1"
        traversal_path = self.service._completion_act_record_path(traversal_key)
        self.assertEqual(traversal_path.parent, self.service._completion_act_forms_dir)
        self.assertNotIn("..", traversal_path.name)
        self.assertRegex(traversal_path.name, r"\A[0-9a-f]{64}\.json\Z")

        cycle_key = self.service._completion_act_cycle_key(self.card, self.card.repair_order)
        shard_path = self.service._completion_act_record_path(cycle_key)
        outside = self.base_dir / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        shard_path.symlink_to(outside)
        with self.assertRaises(PrintModuleError) as shard_symlink:
            self.service.get_completion_act_form(self.card, client=self.client)
        self.assertEqual(shard_symlink.exception.code, "completion_act_store_corrupt")
        self.assertEqual(outside.read_text(encoding="utf-8"), "{}")

        shard_path.unlink()
        self.service._completion_act_forms_dir.rmdir()
        outside_dir = self.base_dir / "outside-dir"
        outside_dir.mkdir()
        self.service._completion_act_forms_dir.symlink_to(outside_dir, target_is_directory=True)
        with self.assertRaises(PrintModuleError) as directory_symlink:
            PrintModuleService(self.base_dir)
        self.assertEqual(directory_symlink.exception.code, "completion_act_store_corrupt")

    def test_shard_count_and_aggregate_quota_are_bounded(self) -> None:
        form = self.service.get_completion_act_form(self.card, client=self.client)["form"]
        saved = self.service.save_completion_act_form(
            self.card,
            client=self.client,
            form_data=form,
            expected_version=0,
            idempotency_key="quota-baseline",
        )
        cycle_key = saved["draft"]["cycle_key"]
        shard_path = self.service._completion_act_record_path(cycle_key)
        baseline_size = shard_path.stat().st_size

        other_card = Card.from_dict({**self.card.to_storage_dict(), "id": "card-act-other"})
        other_form = self.service.get_completion_act_form(other_card, client=self.client)["form"]
        with (
            patch.object(printing_service_module, "COMPLETION_ACT_FORMS_MAX_RECORDS", 1),
            self.assertRaises(PrintModuleError) as count_error,
        ):
            self.service.save_completion_act_form(
                other_card,
                client=self.client,
                form_data=other_form,
                expected_version=0,
                idempotency_key="quota-second-record",
            )
        self.assertEqual(count_error.exception.code, "validation_error")
        self.assertEqual(count_error.exception.details["max_records"], 1)

        changed = deepcopy(form)
        changed["basis"] = "x" * 500
        with (
            patch.object(
                printing_service_module,
                "COMPLETION_ACT_FORMS_FILE_MAX_BYTES",
                baseline_size + 16,
            ),
            self.assertRaises(PrintModuleError) as aggregate_error,
        ):
            self.service.save_completion_act_form(
                self.card,
                client=self.client,
                form_data=changed,
                expected_version=1,
                idempotency_key="quota-aggregate",
            )
        self.assertEqual(aggregate_error.exception.code, "validation_error")
        self.assertEqual(shard_path.stat().st_size, baseline_size)

        unexpected = self.service._completion_act_forms_dir / "unexpected.txt"
        unexpected.write_text("must fail closed", encoding="utf-8")
        with self.assertRaises(PrintModuleError) as unexpected_entry:
            self.service.save_completion_act_form(
                self.card,
                client=self.client,
                form_data=form,
                expected_version=1,
                idempotency_key="quota-unexpected-entry",
            )
        self.assertEqual(unexpected_entry.exception.code, "completion_act_store_corrupt")

    def test_hot_paths_read_and_replace_only_the_target_shard(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            feed = ChangeFeedStore(base_dir / "change_feed.sqlite3")
            service = PrintModuleService(base_dir, change_feed_store=feed)
            form = service.get_completion_act_form(self.card, client=self.client)["form"]
            saved = service.save_completion_act_form(
                self.card,
                client=self.client,
                form_data=form,
                expected_version=0,
                idempotency_key="target-only-baseline",
            )
            target = service._completion_act_record_path(saved["draft"]["cycle_key"])
            decoy = service._completion_act_forms_dir / ("f" * 64 + ".json")
            decoy.write_text("{malformed-decoy", encoding="utf-8")
            if os.name != "nt":
                decoy.chmod(0o600)

            with patch.object(
                service,
                "_read_completion_act_json_file",
                wraps=service._read_completion_act_json_file,
            ) as reads:
                loaded = service.get_completion_act_form(self.card, client=self.client)
            self.assertEqual(loaded["draft"]["version"], 1)
            self.assertEqual([call.args[0] for call in reads.call_args_list], [target])

            changed = deepcopy(form)
            changed["basis"] = "Только целевая запись"
            replaced_targets: list[Path] = []
            original_replace = os.replace

            def record_replace(source: Path, destination: Path) -> None:
                replaced_targets.append(Path(destination))
                return original_replace(source, destination)

            with (
                patch.object(
                    service,
                    "_read_completion_act_form_map",
                    side_effect=AssertionError("hot path must not load every shard"),
                ),
                patch.object(printing_service_module.os, "replace", side_effect=record_replace),
            ):
                updated = service.save_completion_act_form(
                    self.card,
                    client=self.client,
                    form_data=changed,
                    expected_version=1,
                    idempotency_key="target-only-update",
                )
                service.reconcile_change_feed()
            self.assertEqual(updated["draft"]["version"], 2)
            self.assertEqual(replaced_targets, [target])
            self.assertEqual(decoy.read_text(encoding="utf-8"), "{malformed-decoy")
            with self.assertRaises(PrintModuleError) as full_snapshot:
                service._read_completion_act_form_map()
            self.assertEqual(full_snapshot.exception.code, "completion_act_store_corrupt")

    def test_deferred_completion_feed_reconciliation_remains_targeted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            feed = ChangeFeedStore(base_dir / "change_feed.sqlite3")
            service = PrintModuleService(base_dir, change_feed_store=feed)
            initial = service.get_completion_act_form(self.card, client=self.client)
            form = deepcopy(initial["form"])
            form["basis"] = "Отложенная проекция"

            with patch.object(
                feed,
                "reconcile_external_projection_slice",
                side_effect=RuntimeError("simulated feed outage"),
            ):
                saved = service.save_completion_act_form(
                    self.card,
                    client=self.client,
                    form_data=form,
                    expected_version=0,
                    idempotency_key="deferred-feed-save",
                )
            cycle_key = saved["draft"]["cycle_key"]
            self.assertEqual(service._completion_act_feed_pending, {cycle_key})

            with patch.object(
                service,
                "_read_completion_act_form_map",
                side_effect=AssertionError("feed-read route must not load every shard"),
            ):
                service.reconcile_change_feed()
            self.assertEqual(service._completion_act_feed_pending, set())

    def test_optimistic_concurrency_idempotency_and_reset(self) -> None:
        form = self.service.get_completion_act_form(self.card, client=self.client)["form"]
        form["basis"] = "Ручное основание"
        saved = self.service.save_completion_act_form(
            self.card,
            client=self.client,
            form_data=form,
            expected_version=0,
            idempotency_key="same-request",
        )
        replay = self.service.save_completion_act_form(
            self.card,
            client=self.client,
            form_data=form,
            expected_version=0,
            idempotency_key="same-request",
        )
        self.assertEqual(saved["draft"]["version"], 1)
        self.assertEqual(replay["draft"]["version"], 1)
        self.assertTrue(replay["draft"]["idempotent_replay"])

        changed = deepcopy(form)
        changed["basis"] = "Другое основание"
        with self.assertRaises(PrintModuleError) as conflict:
            self.service.save_completion_act_form(
                self.card,
                client=self.client,
                form_data=changed,
                expected_version=1,
                idempotency_key="same-request",
            )
        self.assertEqual(conflict.exception.status_code, 409)

        with self.assertRaises(PrintModuleError) as version_conflict:
            self.service.save_completion_act_form(
                self.card,
                client=self.client,
                form_data=changed,
                expected_version=0,
                idempotency_key="new-request",
            )
        self.assertEqual(version_conflict.exception.status_code, 409)

        reset = self.service.reset_completion_act_form(
            self.card,
            client=self.client,
            expected_version=1,
            idempotency_key="reset-request",
        )
        self.assertFalse(reset["draft"]["exists"])
        self.assertEqual(reset["draft"]["version"], 2)
        self.assertEqual(reset["form"], reset["fresh_form"])

    def test_parallel_service_instances_allow_one_save_and_one_conflict(self) -> None:
        services = [PrintModuleService(self.base_dir), PrintModuleService(self.base_dir)]
        forms = [
            service.get_completion_act_form(self.card, client=self.client)["form"]
            for service in services
        ]
        forms[0]["basis"] = "Параллельная версия A"
        forms[1]["basis"] = "Параллельная версия B"
        barrier = threading.Barrier(2)
        results: list[tuple[str, int, str]] = []
        results_lock = threading.Lock()

        def save(index: int) -> None:
            barrier.wait(timeout=5)
            try:
                saved = services[index].save_completion_act_form(
                    self.card,
                    client=self.client,
                    form_data=forms[index],
                    expected_version=0,
                    idempotency_key=f"parallel-save-{index}",
                )
                result = ("ok", saved["draft"]["version"], saved["form"]["basis"])
            except PrintModuleError as exc:
                result = (exc.code, exc.status_code, "")
            with results_lock:
                results.append(result)

        threads = [threading.Thread(target=save, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(1, sum(result[0] == "ok" for result in results))
        self.assertEqual(
            1,
            sum(result[:2] == ("completion_act_version_conflict", 409) for result in results),
        )
        final = self.service.get_completion_act_form(self.card, client=self.client)
        self.assertEqual(final["draft"]["version"], 1)
        self.assertIn(final["form"]["basis"], {forms[0]["basis"], forms[1]["basis"]})

    def test_override_recomputes_totals_rejects_invalid_and_escapes_html(self) -> None:
        form = self.service.get_completion_act_form(self.card, client=self.client)["form"]
        form["customer"]["legal_name"] = "<script>alert(1)</script>"
        form["items"] = [
            {
                "id": "manual-1",
                "section": "manual",
                "name": "Эталонная сумма",
                "unit": "шт",
                "quantity": "1",
                "price": "372983",
            }
        ]
        form["totals"] = {"gross": "1"}
        context = self.service._completion_act_document_context(
            self.card,
            self.card.repair_order,
            client=self.client,
            settings=PrintModuleSettings(),
            document_overrides={"completion_act": form},
        )

        self.assertEqual(context["totals"]["base_display"], "372 983,00")
        self.assertEqual(context["totals"]["vat_display"], "18 649,15")
        self.assertEqual(context["totals"]["gross_display"], "391 632,15")
        rendered = render_template(
            "{{completion_act.customer.legal_name_display}}", {"completion_act": context}
        )
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

        for invalid_price in ("-1", "NaN", "Infinity", "-Infinity"):
            form["items"][0]["price"] = invalid_price
            with self.subTest(invalid_price=invalid_price), self.assertRaises(PrintModuleError):
                self.service._completion_act_document_context(
                    self.card,
                    self.card.repair_order,
                    client=self.client,
                    settings=PrintModuleSettings(),
                    document_overrides={"completion_act": form},
                )
        form["items"] = [dict(form["items"][0], price="1") for _ in range(301)]
        with self.assertRaises(PrintModuleError) as too_many_items:
            self.service.save_completion_act_form(
                self.card,
                client=self.client,
                form_data=form,
                expected_version=0,
                idempotency_key="too-many-items",
            )
        self.assertEqual(too_many_items.exception.details["max_items"], 300)

        form["items"] = [
            {
                "id": "overflow",
                "section": "works",
                "name": "Недопустимо большая сумма",
                "unit": "ч",
                "quantity": "99999.999",
                "price": "999999999.99",
            }
        ]
        with self.assertRaises(PrintModuleError) as overflow:
            self.service._completion_act_document_context(
                self.card,
                self.card.repair_order,
                client=self.client,
                settings=PrintModuleSettings(),
                document_overrides={"completion_act": form},
            )
        self.assertEqual(overflow.exception.code, "validation_error")
        self.assertEqual(overflow.exception.status_code, 400)
        self.assertEqual(overflow.exception.details["field"], "items[0].total")

        for field, value, expected_detail in (
            ("price", "1000000000", "max_amount"),
            ("quantity", "100000", "max_quantity"),
            ("quantity", "1.0001", "max_decimal_places"),
            ("unit", "A" * 25, "max_length"),
        ):
            bounded_form = self.service.get_completion_act_form(self.card, client=self.client)[
                "form"
            ]
            bounded_form["items"][0][field] = value
            with self.assertRaises(PrintModuleError) as bounded_error:
                self.service._completion_act_document_context(
                    self.card,
                    self.card.repair_order,
                    client=self.client,
                    settings=PrintModuleSettings(),
                    document_overrides={"completion_act": bounded_form},
                )
            self.assertEqual(bounded_error.exception.details["field"], f"items[0].{field}")
            self.assertIn(expected_detail, bounded_error.exception.details)

        canonical_quantity = self.service.get_completion_act_form(self.card, client=self.client)[
            "form"
        ]
        canonical_quantity["items"][0]["quantity"] = "000001.5000"
        canonical_context = self.service._completion_act_document_context(
            self.card,
            self.card.repair_order,
            client=self.client,
            settings=PrintModuleSettings(),
            document_overrides={"completion_act": canonical_quantity},
        )
        self.assertEqual(canonical_context["items"][0]["quantity_display"], "1,5")

        printable_money = self.service.get_completion_act_form(self.card, client=self.client)[
            "form"
        ]
        printable_money["items"] = [
            {
                "id": "printable-money-boundary",
                "section": "works",
                "name": "Печатная денежная граница",
                "unit": "ч",
                "quantity": "1",
                "price": "900000000",
            }
        ]
        printable_context = self.service._completion_act_document_context(
            self.card,
            self.card.repair_order,
            client=self.client,
            settings=PrintModuleSettings(),
            document_overrides={"completion_act": printable_money},
        )
        self.assertEqual(
            printable_context["items"][0]["price_without_vat_display"],
            "900 000 000,00",
        )
        self.assertEqual(printable_context["totals"]["gross_display"], "945 000 000,00")

        oversized_text = deepcopy(form)
        oversized_text["acceptance_text"] = "x" * 1001
        with self.assertRaises(PrintModuleError) as too_long:
            self.service._completion_act_document_context(
                self.card,
                self.card.repair_order,
                client=self.client,
                settings=PrintModuleSettings(),
                document_overrides={"completion_act": oversized_text},
            )
        self.assertEqual(too_long.exception.code, "validation_error")
        self.assertEqual(too_long.exception.details["field"], "acceptance_text")

        invalid_layouts = (
            (("а\n" * 500)[:1000], None),
            ("\n".join("а" for _ in range(35)), None),
            ("🚚" * 1000, 1.0),
            ("🚚" * 1000, 0.35),
            ("🚚" * 750, 0.40),
            ("🚚" * 700, 0.50),
            ("🚚" * 300, 0.55),
        )
        for layout_text, party_scale in invalid_layouts:
            invalid_layout = self.service.get_completion_act_form(self.card, client=self.client)[
                "form"
            ]
            invalid_layout["acceptance_text"] = layout_text
            if party_scale is not None:
                for party_name in ("performer", "customer"):
                    party = invalid_layout[party_name]
                    party["legal_name"] = "🚚" * int(240 * party_scale)
                    party["address"] = "🚚" * int(320 * party_scale)
                    party["bank_name"] = "🚚" * int(240 * party_scale)
                    party["signer_position"] = "🚚" * int(120 * party_scale)
                    party["signer_name"] = "🚚" * int(160 * party_scale)
            with self.assertRaises(PrintModuleError) as layout_error:
                self.service._completion_act_document_context(
                    self.card,
                    self.card.repair_order,
                    client=self.client,
                    settings=PrintModuleSettings(),
                    document_overrides={"completion_act": invalid_layout},
                )
            self.assertEqual(layout_error.exception.code, "validation_error")
            self.assertEqual(
                layout_error.exception.details["field"],
                "completion_act.final_block",
            )

        for wide_glyph in ("W", "Ж", "Н", "О", "А"):
            wide_layout = self.service.get_completion_act_form(self.card, client=self.client)[
                "form"
            ]
            wide_layout["acceptance_text"] = wide_glyph * 1000
            for party_name in ("performer", "customer"):
                party = wide_layout[party_name]
                for field, limit in (
                    ("legal_name", 240),
                    ("address", 320),
                    ("bank_name", 240),
                    ("signer_position", 120),
                    ("signer_name", 160),
                ):
                    party[field] = wide_glyph * limit
            with self.assertRaises(PrintModuleError) as wide_error:
                self.service._completion_act_document_context(
                    self.card,
                    self.card.repair_order,
                    client=self.client,
                    settings=PrintModuleSettings(),
                    document_overrides={"completion_act": wide_layout},
                )
            self.assertEqual(
                wide_error.exception.details["field"],
                "completion_act.final_block",
            )

        with self.assertRaises(PrintModuleError) as save_layout_error:
            self.service.save_completion_act_form(
                self.card,
                client=self.client,
                form_data=invalid_layout,
                expected_version=0,
                idempotency_key="invalid-final-layout",
            )
        self.assertEqual(save_layout_error.exception.code, "validation_error")
        self.assertEqual(
            save_layout_error.exception.details["field"],
            "completion_act.final_block",
        )

        accepted_layout = self.service.get_completion_act_form(self.card, client=self.client)[
            "form"
        ]
        accepted_layout["acceptance_text"] = "\n".join("а" for _ in range(30))
        accepted_context = self.service._completion_act_document_context(
            self.card,
            self.card.repair_order,
            client=self.client,
            settings=PrintModuleSettings(),
            document_overrides={"completion_act": accepted_layout},
        )
        self.assertEqual(len(accepted_context["pages"]), 2)

    def test_vat_is_rounded_once_from_aggregate_base(self) -> None:
        form = self.service.get_completion_act_form(self.card, client=self.client)["form"]
        form["items"] = [
            {
                "id": f"rounding-{index}",
                "section": "manual",
                "name": f"Строка {index}",
                "unit": "шт",
                "quantity": "1",
                "price": "0.10",
            }
            for index in range(3)
        ]
        context = self.service._completion_act_document_context(
            self.card,
            self.card.repair_order,
            client=self.client,
            settings=PrintModuleSettings(),
            document_overrides={"completion_act": form},
        )

        self.assertEqual(context["totals"]["base"], "0.30")
        self.assertEqual(context["totals"]["vat"], "0.02")
        self.assertEqual(context["totals"]["gross"], "0.32")

        form["items"] = [
            {
                "id": "fractional-price",
                "section": "manual",
                "name": "Цена нормализуется до копеек",
                "unit": "шт",
                "quantity": "3",
                "price": "0.333",
            }
        ]
        with self.assertRaises(PrintModuleError) as fractional_price:
            self.service._completion_act_document_context(
                self.card,
                self.card.repair_order,
                client=self.client,
                settings=PrintModuleSettings(),
                document_overrides={"completion_act": form},
            )
        self.assertEqual(fractional_price.exception.code, "validation_error")
        self.assertEqual(fractional_price.exception.details["field"], "items[0].price")
        self.assertEqual(fractional_price.exception.details["max_decimal_places"], 2)

    def test_fully_empty_rows_are_omitted_and_printed_rows_are_renumbered(self) -> None:
        form = self.service.get_completion_act_form(self.card, client=self.client)["form"]
        form["items"] = [
            {
                "id": "empty-row",
                "section": "manual",
                "name": "",
                "unit": "",
                "quantity": "",
                "price": "",
            },
            {
                "id": "partial-row",
                "section": "manual",
                "name": "Нужна оценка",
                "unit": "",
                "quantity": "",
                "price": "",
            },
            {
                "id": "complete-row",
                "section": "manual",
                "name": "Готовая работа",
                "unit": "ч",
                "quantity": "1",
                "price": "100",
            },
        ]

        context = self.service._completion_act_document_context(
            self.card,
            self.card.repair_order,
            client=self.client,
            settings=PrintModuleSettings(),
            document_overrides={"completion_act": form},
        )

        self.assertEqual(
            [item["id"] for item in context["items"]],
            ["partial-row", "complete-row"],
        )
        self.assertEqual([item["index"] for item in context["items"]], [1, 2])
        self.assertEqual(context["totals"]["item_count"], 2)
        self.assertEqual(context["totals"]["base"], "100.00")
        self.assertTrue(context["warnings"])
        self.assertIn("Строка 1: не указана единица измерения.", context["warnings"])

    def test_pagination_and_cycle_key_contract(self) -> None:
        card = build_card(row_count=25)
        context = self.service._completion_act_document_context(
            card,
            card.repair_order,
            client=self.client,
            settings=PrintModuleSettings(),
            document_overrides=None,
        )

        self.assertEqual(len(context["pages"]), 2)
        self.assertEqual(len(context["pages"][0]["items"]), 26)
        self.assertEqual(len(context["pages"][1]["items"]), 0)
        self.assertFalse(context["pages"][0]["page_break_before"])
        self.assertTrue(context["pages"][1]["page_break_before"])
        self.assertIn("AUTOSTOPCRM_PAGE_BREAK", context["pages"][1]["page_break_marker"])
        first_key = self.service._completion_act_cycle_key(card, card.repair_order)
        changed_order = card.repair_order.to_storage_dict()
        changed_order["number"] = "10700-A"
        edited = Card.from_dict({**card.to_storage_dict(), "repair_order": changed_order})
        self.assertEqual(
            first_key,
            self.service._completion_act_cycle_key(edited, edited.repair_order),
        )
        next_order = card.repair_order.to_storage_dict()
        next_order["cycles"] = [{"id": "closed-cycle-1"}]
        next_order["status"] = "open"
        next_card = Card.from_dict({**card.to_storage_dict(), "repair_order": next_order})
        self.assertNotEqual(
            first_key,
            self.service._completion_act_cycle_key(next_card, next_card.repair_order),
        )

    def test_all_repair_order_rows_are_preserved_up_to_combined_limit(self) -> None:
        card_121 = build_card(row_count=120)
        loaded_121 = self.service.get_completion_act_form(card_121, client=self.client)
        self.assertEqual(len(loaded_121["form"]["items"]), 121)
        self.assertEqual(loaded_121["form"]["items"][-1]["name"], "Материал 1")

        maximum_storage = build_card(row_count=150).to_storage_dict()
        maximum_storage["repair_order"]["materials"] = [
            {
                "id": f"material-{index + 1}",
                "name": f"Материал {index + 1}",
                "inventory_unit": "шт",
                "quantity": "1",
                "price": "10",
            }
            for index in range(150)
        ]
        maximum_card = Card.from_dict(maximum_storage)
        loaded_300 = self.service.get_completion_act_form(maximum_card, client=self.client)
        self.assertEqual(len(maximum_card.repair_order.works), 150)
        self.assertEqual(len(maximum_card.repair_order.materials), 150)
        self.assertEqual(len(loaded_300["form"]["items"]), 300)
        self.assertEqual(loaded_300["form"]["items"][-1]["name"], "Материал 150")

        with patch.object(
            printing_service_module,
            "_completion_act_item_page_weight",
            wraps=printing_service_module._completion_act_item_page_weight,
        ) as item_weight:
            context = self.service._completion_act_document_context(
                maximum_card,
                maximum_card.repair_order,
                client=self.client,
                settings=PrintModuleSettings(),
                document_overrides=None,
            )
        self.assertEqual(len(context["items"]), 300)
        self.assertEqual(context["totals"]["item_count"], 300)
        self.assertEqual(item_weight.call_count, 300)
        self.assertLessEqual(len(context["pages"]), 40)

        oversized_form = deepcopy(loaded_300["form"])
        oversized_form["items"].append(
            {
                "id": "row-301",
                "section": "manual",
                "name": "Лишняя строка",
                "unit": "шт",
                "quantity": "1",
                "price": "1",
            }
        )
        with self.assertRaises(PrintModuleError) as oversized:
            self.service._completion_act_document_context(
                maximum_card,
                maximum_card.repair_order,
                client=self.client,
                settings=PrintModuleSettings(),
                document_overrides={"completion_act": oversized_form},
            )
        self.assertEqual(oversized.exception.code, "validation_error")
        self.assertEqual(oversized.exception.details["field"], "items")
        self.assertEqual(oversized.exception.details["max_items"], 300)

        class ExplodingRow:
            def __str__(self) -> str:
                raise AssertionError("oversized rows must not be normalized")

        oversized_form["items"] = [ExplodingRow(), *({} for _ in range(300))]
        with self.assertRaises(PrintModuleError) as bounded_before_rows:
            self.service._completion_act_document_context(
                maximum_card,
                maximum_card.repair_order,
                client=self.client,
                settings=PrintModuleSettings(),
                document_overrides={"completion_act": oversized_form},
            )
        self.assertEqual(bounded_before_rows.exception.details["field"], "items")
        self.assertEqual(bounded_before_rows.exception.details["max_items"], 300)

        for malformed_items, field in (({}, "items"), (["bad-row"], "items[0]")):
            malformed_form = deepcopy(loaded_300["form"])
            malformed_form["items"] = malformed_items
            with (
                self.subTest(malformed_items=malformed_items),
                self.assertRaises(PrintModuleError) as malformed,
            ):
                self.service._completion_act_document_context(
                    maximum_card,
                    maximum_card.repair_order,
                    client=self.client,
                    settings=PrintModuleSettings(),
                    document_overrides={"completion_act": malformed_form},
                )
            self.assertEqual(malformed.exception.code, "validation_error")
            self.assertEqual(malformed.exception.details["field"], field)

        class ExplodingContainer(list):
            def __str__(self) -> str:
                raise AssertionError("container-valued scalar must not be stringified")

        container_form = deepcopy(loaded_300["form"])
        container_form["document_number"] = ExplodingContainer([0] * 100_000)
        with self.assertRaises(PrintModuleError) as container_error:
            self.service._completion_act_document_context(
                maximum_card,
                maximum_card.repair_order,
                client=self.client,
                settings=PrintModuleSettings(),
                document_overrides={"completion_act": container_form},
            )
        self.assertEqual(container_error.exception.code, "validation_error")
        self.assertEqual(container_error.exception.details["field"], "document_number")

        excessive_layout = deepcopy(loaded_300["form"])
        for item in excessive_layout["items"]:
            item["name"] = "W" * 500
        with self.assertRaises(PrintModuleError) as layout_error:
            self.service._completion_act_document_context(
                maximum_card,
                maximum_card.repair_order,
                client=self.client,
                settings=PrintModuleSettings(),
                document_overrides={"completion_act": excessive_layout},
            )
        self.assertEqual(layout_error.exception.details["field"], "completion_act.items_layout")
        self.assertEqual(layout_error.exception.details["max_pages"], 40)

    def test_variable_height_closing_block_stays_wholly_on_final_a4_page(self) -> None:
        form = self.service.get_completion_act_form(self.card, client=self.client)["form"]
        form["acceptance_text"] = (
            "Работы выполнены полностью, заказчик претензий не имеет. " * 100
        )[:1000]
        for party_name in ("performer", "customer"):
            party = form[party_name]
            party["legal_name"] = ("Организация " + "Очень Длинное Название " * 20)[:240]
            party["address"] = ("Красноярский край, " + "улица Длинная дом 123 офис 456; " * 20)[
                :320
            ]
            party["bank_name"] = ("Банк " + "Очень Длинное Название Банка " * 20)[:240]
            party["signer_position"] = ("Старший " + "руководитель " * 20)[:120]
            party["signer_name"] = ("Очень Длинное ФИО " * 20)[:160]

        context = self.service._completion_act_document_context(
            self.card,
            self.card.repair_order,
            client=self.client,
            settings=PrintModuleSettings(),
            document_overrides={"completion_act": form},
        )

        self.assertEqual(len(context["pages"]), 2)
        self.assertFalse(context["pages"][0]["show_totals"])
        self.assertFalse(context["pages"][0]["show_requisites"])
        self.assertTrue(context["pages"][1]["show_totals"])
        self.assertTrue(context["pages"][1]["show_summary"])
        self.assertTrue(context["pages"][1]["show_acceptance"])
        self.assertTrue(context["pages"][1]["show_requisites"])
        self.assertEqual(context["pages"][1]["acceptance_text"], form["acceptance_text"])

        oversized_details = deepcopy(form)
        for party_name in ("performer", "customer"):
            party = oversized_details[party_name]
            for field, limit in (
                ("inn", 32),
                ("kpp", 32),
                ("bik", 32),
                ("settlement_account", 64),
                ("correspondent_account", 64),
            ):
                party[field] = "W" * limit
        with self.assertRaises(PrintModuleError) as details_error:
            self.service._completion_act_document_context(
                self.card,
                self.card.repair_order,
                client=self.client,
                settings=PrintModuleSettings(),
                document_overrides={"completion_act": oversized_details},
            )
        self.assertEqual(
            details_error.exception.details["field"],
            "completion_act.final_block",
        )

    def test_optional_service_signers_can_be_cleared_but_missing_keys_keep_defaults(self) -> None:
        defaults = PrintServiceProfile.from_dict({})
        cleared = PrintServiceProfile.from_dict({"signer_position": "", "signer_name": ""})
        cleared_settings = PrintModuleSettings.from_dict(
            {"service_profile": {"signer_position": "", "signer_name": ""}}
        )

        self.assertTrue(defaults.signer_position)
        self.assertTrue(defaults.signer_name)
        self.assertEqual(cleared.signer_position, "")
        self.assertEqual(cleared.signer_name, "")
        self.assertEqual(cleared_settings.service_profile.signer_position, "")
        self.assertEqual(cleared_settings.service_profile.signer_name, "")

    def test_change_feed_projection_retains_only_technical_digests(self) -> None:
        projected = project_print_module(
            settings={},
            templates=[],
            inspection_sheet_forms={},
            completion_act_forms={
                "card-act-1:cycle:1": {
                    "version": 1,
                    "source_fingerprint": "source-hash",
                    "overrides": {"customer": {"legal_name": "PRIVATE CUSTOMER"}},
                    "operation": "save",
                    "deleted": False,
                }
            },
        )
        entity = projected[("completion_act_form", "card-act-1:cycle:1")]
        self.assertEqual(entity.entity_type, "completion_act_form")
        self.assertNotIn("PRIVATE CUSTOMER", repr(entity))

    def test_completion_act_template_is_locked_to_builtin(self) -> None:
        workspace = self.service.workspace(self.card)
        document = next(item for item in workspace["documents"] if item["id"] == "completion_act")
        self.assertTrue(document["template_locked"])
        self.assertEqual(document["template_count"], 1)
        with self.assertRaises(PrintModuleError) as locked:
            self.service.save_template(
                document_type="completion_act",
                name="Forbidden custom act",
                content="<div>must not be used</div>",
            )
        self.assertEqual(locked.exception.code, "completion_act_template_locked")

        preview = self.service.preview_documents(
            self.card,
            client=self.client,
            selected_document_ids=["completion_act"],
            selected_template_ids={"completion_act": "custom:completion_act:ignored"},
            template_overrides={"completion_act": "<div>FORBIDDEN OVERRIDE</div>"},
        )
        document_preview = preview["documents"][0]
        self.assertEqual(document_preview["template"]["source"], "builtin")
        self.assertNotIn("FORBIDDEN OVERRIDE", document_preview["pages"][0]["html"])

    def test_export_filename_uses_manual_completion_act_number(self) -> None:
        form = self.service.get_completion_act_form(self.card, client=self.client)["form"]
        form["document_number"] = "ACT-MANUAL-42"
        with patch(
            "minimal_kanban.printing.service.render_html_to_pdf_bytes",
            return_value=b"%PDF-1.4\n%%EOF",
        ):
            _pdf, file_name, _meta = self.service.export_documents_pdf(
                self.card,
                client=self.client,
                selected_document_ids=["completion_act"],
                document_overrides={"completion_act": form},
            )

        self.assertEqual(file_name, "autostopcrm-completion_act-ACT-MANUAL-42.pdf")


if __name__ == "__main__":
    unittest.main()
