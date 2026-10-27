from __future__ import annotations

# Fixture import selects this checkout before importing application modules.
# ruff: noqa: I001
import os
from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import patch

from tests.services_case import CardServiceCase
from minimal_kanban.models import CashBox, CashTransaction, InventoryItem, InventoryMovement
from minimal_kanban.repair_order import REPAIR_ORDER_COMMENT_LIMIT, RepairOrder
from minimal_kanban.services.card_service import CardService
from minimal_kanban.services.errors import ServiceError
from minimal_kanban.storage.audit_archive import hydrate_audit_event_details
from minimal_kanban.storage.json_store import JsonStore, StateWriteConflictError

FEATURE_FLAG = "AUTOSTOP_CRM_REPAIR_ORDER_COMMENT_EDITORIAL_ENABLED"


class EditorialRepairOrderCommentTests(CardServiceCase):
    def setUp(self):
        super().setUp()
        feature = patch.dict(os.environ, {FEATURE_FLAG: "1"})
        feature.start()
        self.addCleanup(feature.stop)
        self.service = self._build_service()
        client = self.service.create_client({"display_name": "Synthetic client"})["client"]
        self.card_id = self.service.create_card(
            {"title": "Synthetic repair", "client_id": client["id"]}
        )["card"]["id"]
        self.other_card_id = self.service.create_card({"title": "Other synthetic repair"})["card"][
            "id"
        ]

        timestamp = "2026-10-27T12:00:00+00:00"
        bundle = deepcopy(self.store.read_bundle())
        card = next(item for item in bundle["cards"] if item.id == self.card_id)
        card.archived = True
        card.repair_order = RepairOrder.from_dict(
            {
                "number": "123",
                "date": "27.10.2026",
                "status": "closed",
                "opened_at": "27.10.2026 10:00",
                "closed_at": "27.10.2026 12:00",
                "reason": "Synthetic reason",
                "comment": "Old customer comment",
                "note": "Synthetic internal note",
                "works": [
                    {
                        "id": "work-1",
                        "name": "Diagnostic work",
                        "quantity": "2",
                        "price": "1500",
                        "total": "3000",
                        "salary_amount": "300",
                        "salary_accrued_at": "27.10.2026 12:00",
                        "work_price_snapshot": "1500",
                        "executor_id": "synthetic-mechanic",
                    }
                ],
                "materials": [
                    {
                        "id": "material-1",
                        "name": "Synthetic filter",
                        "quantity": "1",
                        "cost_price": "200",
                        "price": "500",
                        "total": "500",
                        "inventory_item_id": "item-1",
                        "inventory_movement_id": "movement-1",
                        "catalog_number": "SYNTHETIC-1",
                    }
                ],
                "payments": [
                    {
                        "id": "payment-1",
                        "amount": "3500",
                        "cashbox_id": "cashbox-1",
                        "cash_transaction_id": "transaction-1",
                    }
                ],
                "cycles": [{"id": "cycle-1", "status": "closed"}],
                "payroll_postings": [{"id": "payroll-1", "amount": "300"}],
                "active_correction": {"id": "correction-1"},
            }
        )
        bundle["cashboxes"] = [CashBox("cashbox-1", "Synthetic cashbox", 0, timestamp, timestamp)]
        bundle["cash_transactions"] = [
            CashTransaction(
                "transaction-1",
                "cashbox-1",
                "income",
                350000,
                "Synthetic",
                timestamp,
                "DIRECTOR",
                "api",
            )
        ]
        bundle["inventory_items"] = [
            InventoryItem(
                "item-1",
                "Synthetic filter",
                "SYNTHETIC-1",
                "шт",
                "9",
                "200",
                "500",
                timestamp,
                timestamp,
            )
        ]
        bundle["inventory_movements"] = [
            InventoryMovement(
                "movement-1",
                "item-1",
                "write_off",
                "1",
                "-1",
                "шт",
                "200",
                "500",
                timestamp,
                "DIRECTOR",
                "api",
                card_id=self.card_id,
                repair_order_number="123",
            )
        ]
        self.store.write_bundle(**bundle)

    def card(self, card_id=None):
        return next(
            item
            for item in self.store.read_bundle()["cards"]
            if item.id == (card_id or self.card_id)
        )

    def request(self, **updates):
        return {
            "card_id": self.card_id,
            "content_only": True,
            "mode": "apply",
            "actor_name": "DIRECTOR",
            "source": "mcp",
            "response_mode": "full",
            "expected_updated_at": self.card().updated_at,
            "idempotency_key": "synthetic-editorial-comment-1",
            "repair_order_comment": "  New customer text\nSecond line  ",
            **updates,
        }

    def assert_unrelated_unchanged(self, before, after, expected_comment):
        before_card = next(item for item in before["cards"] if item.id == self.card_id)
        after_card = next(item for item in after["cards"] if item.id == self.card_id)
        before_storage = before_card.to_storage_dict()
        after_storage = after_card.to_storage_dict()
        self.assertEqual(after_storage["repair_order"]["comment"], expected_comment)
        after_storage["repair_order"]["comment"] = before_storage["repair_order"]["comment"]
        for field in ("updated_at", "notification_updated_at", "seen_by_users"):
            before_storage.pop(field)
            after_storage.pop(field)
        self.assertEqual(before_storage, after_storage)
        self.assertEqual(before_card.archived, after_card.archived)
        self.assertEqual(before_card.column, after_card.column)
        self.assertEqual(before_card.repair_order.status, after_card.repair_order.status)
        before_view = before_card.repair_order.to_dict()
        after_view = after_card.repair_order.to_dict()
        for field in ("comment", "client_information"):
            before_view.pop(field)
            after_view.pop(field)
        self.assertEqual(before_view, after_view)
        for domain in before:
            if domain not in {"cards", "events", "settings"}:
                self.assertEqual(before[domain], after[domain], domain)
        self.assertEqual(
            next(
                item for item in before["cards"] if item.id == self.other_card_id
            ).to_storage_dict(),
            next(
                item for item in after["cards"] if item.id == self.other_card_id
            ).to_storage_dict(),
        )
        before_settings, after_settings = deepcopy(before["settings"]), deepcopy(after["settings"])
        before_settings.pop("card_editorial_receipts", None)
        after_settings.pop("card_editorial_receipts", None)
        self.assertEqual(before_settings, after_settings)

    def test_preview_and_apply_only_touch_the_requested_comment(self):
        before = deepcopy(self.store.read_bundle())
        before_bytes = self.state_file.read_bytes()
        request = self.request()
        archive_dir = self.service._audit_archive.archive_dir
        archive_before = {
            str(path.relative_to(archive_dir)): path.read_bytes()
            for path in archive_dir.rglob("*")
            if path.is_file()
        }
        with ExitStack() as stack:
            for name in (
                "update_card",
                "_update_repair_order",
                "_apply_ready_column_side_effects",
                "_sync_linked_client_vehicle_from_card",
                "_sync_client_vehicle_linked_cards",
                "_synchronize_repair_order_numbers",
            ):
                stack.enter_context(
                    patch.object(self.service, name, side_effect=AssertionError(name))
                )
            preview = self.service.cleanup_card({**request, "mode": "dry_run"})
            self.assertEqual(preview["changed"], 0)
            self.assertEqual(before_bytes, self.state_file.read_bytes())
            self.assertEqual(before, self.store.read_bundle())
            self.assertEqual(
                archive_before,
                {
                    str(path.relative_to(archive_dir)): path.read_bytes()
                    for path in archive_dir.rglob("*")
                    if path.is_file()
                },
            )
            item = preview["items"][0]
            self.assertEqual(item["planned_fields"], ["repair_order_comment"])
            self.assertEqual(
                item["card"]["repair_order"]["comment"], "New customer text\nSecond line"
            )
            self.assertEqual(
                item["card"]["repair_order"]["client_information"], "New customer text\nSecond line"
            )
            applied = self.service.cleanup_card(request)
        self.assertEqual(applied["changed"], 1)
        after = self.store.read_bundle()
        self.assert_unrelated_unchanged(before, after, "New customer text\nSecond line")
        prior_event_ids = {event.id for event in before["events"]}
        new_events = [event for event in after["events"] if event.id not in prior_event_ids]
        self.assertEqual(len(new_events), 1)
        self.assertEqual(new_events[0].action, "repair_order_updated")
        self.assertTrue(new_events[0].details["editorial_only"])
        details = hydrate_audit_event_details(
            action=new_events[0].action,
            details=new_events[0].details,
            archive_store=self.service._audit_archive,
            event_id=new_events[0].id,
        )
        self.assertEqual(details["before"]["comment"], "Old customer comment")
        self.assertEqual(details["after"]["comment"], "New customer text\nSecond line")

    def test_stale_invalid_type_and_oversized_values_never_write(self):
        before = deepcopy(self.store.read_bundle())
        before_bytes = self.state_file.read_bytes()
        invalid = [
            {"expected_updated_at": "stale"},
            {"repair_order_comment": None},
            {"repair_order_comment": True},
            {"repair_order_comment": 5},
            {"repair_order_comment": ["not text"]},
            {"repair_order_comment": "x" * (REPAIR_ORDER_COMMENT_LIMIT + 1)},
            {"repair_order": {"status": "open"}},
        ]
        for mode in ("dry_run", "apply"):
            for changes in invalid:
                with self.subTest(mode=mode, changes=changes), self.assertRaises(ServiceError):
                    self.service.cleanup_card(self.request(mode=mode, **changes))
                self.assertEqual(before_bytes, self.state_file.read_bytes())
                self.assertEqual(before, self.store.read_bundle())

    def test_comment_preview_and_apply_preserve_other_artifacts_without_cleanup(self):
        order_dir = self.service._repair_orders_dir
        attachment_dir = self.service._attachments_dir
        archive_dir = self.service._audit_archive.archive_dir
        target = self.service._repair_order_text_path(self.card())
        old_same_card = order_dir / f"old__{target.name.rsplit('__', 1)[-1]}"
        other_order = order_dir / "other__C-UNTOUCHED.txt"
        other_attachment = attachment_dir / "unrelated" / "synthetic.bin"
        other_attachment.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("Previous derivative", encoding="utf-8")
        old_same_card.write_text("Keep old same-card file", encoding="utf-8")
        other_order.write_text("Keep unrelated file", encoding="utf-8")
        other_attachment.write_bytes(b"Keep synthetic attachment")
        self.service._last_runtime_cleanup_at = 0

        def snapshot(directory):
            return {
                str(path.relative_to(directory)): path.read_bytes()
                for path in directory.rglob("*")
                if path.is_file()
            }

        before_orders = snapshot(order_dir)
        before_attachments = snapshot(attachment_dir)
        before_archive = snapshot(archive_dir)
        before_state = self.state_file.read_bytes()
        request = self.request(repair_order_comment="Fresh synthetic customer text")
        with ExitStack() as stack:
            cleanup_mocks = [
                stack.enter_context(
                    patch.object(self.service, name, side_effect=AssertionError(name))
                )
                for name in (
                    "_cleanup_runtime_artifacts_if_due",
                    "_cleanup_repair_orders_directory",
                    "_cleanup_attachment_directories",
                    "_cleanup_repair_order_text_files",
                )
            ]
            preview = self.service.cleanup_card({**request, "mode": "dry_run"})
            self.assertEqual(preview["changed"], 0)
            self.assertEqual(before_state, self.state_file.read_bytes())
            self.assertEqual(before_orders, snapshot(order_dir))
            self.assertEqual(before_attachments, snapshot(attachment_dir))
            self.assertEqual(before_archive, snapshot(archive_dir))
            applied = self.service.cleanup_card(request)
            self.assertEqual(applied["changed"], 1)
            for cleanup in cleanup_mocks:
                cleanup.assert_not_called()

        self.assertIn("Fresh synthetic customer text", target.read_text(encoding="utf-8"))
        self.assertEqual(old_same_card.read_text(encoding="utf-8"), "Keep old same-card file")
        self.assertEqual(other_order.read_text(encoding="utf-8"), "Keep unrelated file")
        self.assertEqual(before_attachments, snapshot(attachment_dir))

    def test_write_conflict_does_not_publish_proposed_derivative(self):
        target = self.service._repair_order_text_path(self.card())
        target.write_text("Existing derivative", encoding="utf-8")
        before_state = self.state_file.read_bytes()
        before_bundle = deepcopy(self.store.read_bundle())
        with (
            patch.object(
                self.store,
                "write_cached_bundle",
                side_effect=StateWriteConflictError("synthetic concurrent write"),
            ),
            self.assertRaises(ServiceError) as conflict,
        ):
            self.service.cleanup_card(self.request(repair_order_comment="Uncommitted text"))
        self.assertEqual(conflict.exception.code, "state_write_conflict")
        self.assertEqual(before_state, self.state_file.read_bytes())
        self.assertEqual(before_bundle, self.store.read_bundle())
        self.assertEqual(target.read_text(encoding="utf-8"), "Existing derivative")

    def test_exact_limit_and_explicit_clear_preserve_existing_order(self):
        text = "x" * REPAIR_ORDER_COMMENT_LIMIT
        preview = self.service.cleanup_card(self.request(mode="dry_run", repair_order_comment=text))
        self.assertEqual(preview["items"][0]["card"]["repair_order"]["comment"], text)
        before = deepcopy(self.store.read_bundle())
        applied = self.service.cleanup_card(self.request(repair_order_comment=""))
        self.assertEqual(applied["changed"], 1)
        self.assert_unrelated_unchanged(before, self.store.read_bundle(), "")
        self.assertTrue(self.service._card_has_repair_order(self.card()))

    def test_absent_order_is_rejected_for_preview_and_apply(self):
        before = deepcopy(self.store.read_bundle())
        before_bytes = self.state_file.read_bytes()
        for mode in ("dry_run", "apply"):
            with self.subTest(mode=mode), self.assertRaises(ServiceError):
                self.service.cleanup_card(
                    self.request(
                        card_id=self.other_card_id,
                        expected_updated_at=self.card(self.other_card_id).updated_at,
                        mode=mode,
                    )
                )
            self.assertEqual(before_bytes, self.state_file.read_bytes())
            self.assertEqual(before, self.store.read_bundle())

    def test_feature_is_disabled_by_default_and_payload_cannot_enable_it(self):
        before = deepcopy(self.store.read_bundle())
        before_bytes = self.state_file.read_bytes()
        with patch.dict(os.environ):
            os.environ.pop(FEATURE_FLAG, None)
            for mode in ("dry_run", "apply"):
                for extra in ({}, {FEATURE_FLAG: "1"}, {"feature_enabled": True}):
                    with self.subTest(mode=mode, extra=extra):
                        with (
                            patch.object(
                                self.service,
                                "_read_bundle_for_update",
                                side_effect=AssertionError("disabled feature read backend"),
                            ),
                            self.assertRaises(ServiceError) as rejected,
                        ):
                            self.service.cleanup_card(self.request(mode=mode, **extra))
                        if not extra:
                            self.assertEqual(rejected.exception.code, "editorial_comment_disabled")
                    self.assertEqual(before_bytes, self.state_file.read_bytes())
                    self.assertEqual(before, self.store.read_bundle())
            existing_editorial_request = self.request(mode="dry_run", description="Allowed preview")
            existing_editorial_request.pop("repair_order_comment")
            preview = self.service.cleanup_card(existing_editorial_request)
            self.assertEqual(preview["items"][0]["planned_fields"], ["description"])
            self.assertEqual(before_bytes, self.state_file.read_bytes())

    def test_cannot_clear_comment_only_order_out_of_existence(self):
        bundle = deepcopy(self.store.read_bundle())
        card = next(item for item in bundle["cards"] if item.id == self.card_id)
        card.archived = False
        card.repair_order = RepairOrder.from_dict({"comment": "Old comment only"})
        self.store.write_bundle(**bundle)
        self.assertTrue(self.service._card_has_repair_order(self.card()))
        before = deepcopy(self.store.read_bundle())
        before_bytes = self.state_file.read_bytes()
        for mode in ("dry_run", "apply"):
            with self.subTest(mode=mode), self.assertRaises(ServiceError):
                self.service.cleanup_card(self.request(mode=mode, repair_order_comment=""))
            self.assertEqual(before_bytes, self.state_file.read_bytes())
            self.assertEqual(before, self.store.read_bundle())
        self.service.cleanup_card(self.request(repair_order_comment="Updated comment only"))
        self.assertTrue(self.service._card_has_repair_order(self.card()))
        self.assertEqual(self.card().repair_order.comment, "Updated comment only")

    def test_retry_after_newer_edit_is_idempotent_and_key_reuse_conflicts(self):
        request = self.request()
        first = self.service.cleanup_card(request)
        second = self.request(
            idempotency_key="synthetic-editorial-comment-2",
            expected_updated_at=first["verification"]["updated_at"],
            repair_order_comment="Later comment",
        )
        self.service.cleanup_card(second)
        before = self.state_file.read_bytes()
        reloaded = CardService(JsonStore(self.state_file, self.logger), self.logger)
        replay = reloaded.cleanup_card(request)
        self.assertTrue(replay["meta"]["idempotent_replay"])
        self.assertEqual(replay["changed"], 0)
        self.assertEqual(replay["items"][0]["card"]["repair_order"]["comment"], "Later comment")
        self.assertEqual(before, self.state_file.read_bytes())
        with self.assertRaises(ServiceError) as conflict:
            reloaded.cleanup_card({**request, "repair_order_comment": "Different intent"})
        self.assertEqual(conflict.exception.code, "editorial_idempotency_conflict")
        self.assertEqual(before, self.state_file.read_bytes())

    def test_concurrent_storage_write_rejects_patch_and_receipt(self):
        before = self.card().to_storage_dict()
        save = self.service._save_bundle

        def concurrent_save(*args, **kwargs):
            concurrent = CardService(JsonStore(self.state_file, self.logger), self.logger)
            concurrent.update_card({"card_id": self.other_card_id, "description": "Parallel edit"})
            return save(*args, **kwargs)

        with patch.object(self.service, "_save_bundle", side_effect=concurrent_save):
            with self.assertRaises(ServiceError) as conflict:
                self.service.cleanup_card(self.request())
        self.assertEqual(conflict.exception.code, "state_write_conflict")
        self.assertEqual(before, self.card().to_storage_dict())
        applied = self.service.cleanup_card(self.request())
        self.assertFalse(applied["meta"]["idempotent_replay"])
        self.assertEqual(applied["changed"], 1)
