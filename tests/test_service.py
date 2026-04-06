from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.models import CARD_DESCRIPTION_LIMIT, AuditEvent, Card, utc_now
from minimal_kanban.services.card_service import CardService, ServiceError
from minimal_kanban.storage.json_store import JsonStore
from minimal_kanban.vehicle_profile import VehicleProfile


class CardServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "state.json"
        self.logger = logging.getLogger(f"test.service.{self._testMethodName}")
        self.logger.handlers.clear()
        self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False
        self.store = JsonStore(state_file=self.state_file, logger=self.logger)
        self.service = CardService(self.store, self.logger)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _patch_time(self, moment: datetime):
        return (
            patch("minimal_kanban.services.card_service.utc_now", return_value=moment),
            patch("minimal_kanban.services.card_service.utc_now_iso", return_value=moment.isoformat()),
            patch("minimal_kanban.models.utc_now", return_value=moment),
        )

    def test_card_lifecycle_with_deadline(self) -> None:
        base = datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc)
        patches = self._patch_time(base)
        with patches[0], patches[1], patches[2]:
            created = self.service.create_card(
                {
                    "vehicle": "KIA RIO",
                    "title": "Задача",
                    "description": "Текст",
                    "deadline": {"days": 1, "hours": 4},
                }
            )
        card_id = created["card"]["id"]
        self.assertEqual(created["card"]["vehicle"], "KIA RIO")
        self.assertEqual(created["card"]["status"], "ok")
        self.assertEqual(created["card"]["indicator"], "green")

        moved = self.service.move_card({"card_id": card_id, "column": "in_progress"})
        self.assertEqual(moved["card"]["column"], "in_progress")

        update_time = base + timedelta(hours=1)
        patches = self._patch_time(update_time)
        with patches[0], patches[1], patches[2]:
            updated = self.service.update_card(
                {
                    "card_id": card_id,
                    "vehicle": "KIA RIO X",
                    "title": "Задача 2",
                    "description": "Новый текст",
                    "deadline": {"days": 0, "hours": 3},
                }
            )
        self.assertEqual(updated["card"]["vehicle"], "KIA RIO X")
        self.assertEqual(updated["card"]["title"], "Задача 2")
        self.assertEqual(updated["card"]["description"], "Новый текст")
        self.assertEqual(updated["card"]["status"], "ok")

        archived = self.service.archive_card({"card_id": card_id})
        self.assertTrue(archived["card"]["archived"])

    def test_supports_large_card_description(self) -> None:
        large_description = "А" * 12000

        created = self.service.create_card(
            {
                "title": "Длинное описание",
                "description": large_description,
                "deadline": {"days": 0, "hours": 2},
            }
        )

        self.assertEqual(created["card"]["description"], large_description)
        self.assertGreater(len(created["card"]["description"]), 5000)

    def test_cashbox_lifecycle_tracks_balance_and_transactions(self) -> None:
        created = self.service.create_cashbox({"name": "Наличный", "actor_name": "ADMIN"})
        cashbox = created["cashbox"]
        self.assertEqual(cashbox["name"], "Наличный")
        self.assertEqual(cashbox["statistics"]["transactions_total"], 0)

        income = self.service.create_cash_transaction(
            {
                "cashbox_id": cashbox["id"],
                "direction": "income",
                "amount": "1500,50",
                "note": "Предоплата",
                "actor_name": "ADMIN",
            }
        )
        self.assertEqual(income["transaction"]["direction"], "income")
        self.assertEqual(income["transaction"]["amount_minor"], 150050)

        expense = self.service.create_cash_transaction(
            {
                "cashbox_id": cashbox["short_id"],
                "direction": "expense",
                "amount_minor": 5050,
                "note": "Расходник",
                "actor_name": "ADMIN",
            }
        )
        self.assertEqual(expense["transaction"]["direction"], "expense")

        listed = self.service.list_cashboxes()
        self.assertEqual(listed["meta"]["total"], 1)
        listed_cashbox = listed["cashboxes"][0]
        self.assertEqual(listed_cashbox["statistics"]["transactions_total"], 2)
        self.assertEqual(listed_cashbox["statistics"]["balance_minor"], 145000)

        details = self.service.get_cashbox({"cashbox_id": cashbox["id"], "transaction_limit": 10})
        self.assertEqual(details["cashbox"]["id"], cashbox["id"])
        self.assertEqual(len(details["transactions"]), 2)
        self.assertEqual(details["transactions"][0]["note"], "Расходник")

        deleted = self.service.delete_cashbox({"cashbox_id": cashbox["short_id"], "actor_name": "ADMIN"})
        self.assertTrue(deleted["meta"]["deleted"])
        self.assertEqual(deleted["meta"]["removed_transactions"], 2)
        self.assertEqual(self.service.list_cashboxes()["meta"]["total"], 0)

    def test_move_card_can_reorder_within_same_column(self) -> None:
        first = self.service.create_card({"title": "First", "column": "inbox", "deadline": {"hours": 2}})
        second = self.service.create_card({"title": "Second", "column": "inbox", "deadline": {"hours": 2}})
        third = self.service.create_card({"title": "Third", "column": "inbox", "deadline": {"hours": 2}})

        moved = self.service.move_card(
            {
                "card_id": third["card"]["id"],
                "column": "inbox",
                "before_card_id": second["card"]["id"],
            }
        )

        self.assertEqual(moved["card"]["column"], "inbox")
        self.assertEqual(moved["card"]["position"], 1)
        self.assertEqual(moved["affected_column_ids"], ["inbox"])
        self.assertEqual([card["id"] for card in moved["affected_cards"][:3]], [first["card"]["id"], third["card"]["id"], second["card"]["id"]])
        self.assertTrue(all("repair_order" not in card for card in moved["affected_cards"]))
        self.assertTrue(moved["meta"]["changed"])

        snapshot = self.service.get_board_snapshot()
        inbox_cards = sorted(
            [card for card in snapshot["cards"] if card["column"] == "inbox"],
            key=lambda item: item["position"],
        )
        self.assertEqual(
            [card["id"] for card in inbox_cards[:3]],
            [first["card"]["id"], third["card"]["id"], second["card"]["id"]],
        )

    def test_move_card_can_insert_before_card_in_another_column(self) -> None:
        source = self.service.create_card({"title": "Source", "column": "inbox", "deadline": {"hours": 2}})
        first_target = self.service.create_card({"title": "Target A", "column": "in_progress", "deadline": {"hours": 2}})
        second_target = self.service.create_card({"title": "Target B", "column": "in_progress", "deadline": {"hours": 2}})

        moved = self.service.move_card(
            {
                "card_id": source["card"]["id"],
                "column": "in_progress",
                "before_card_id": second_target["card"]["id"],
            }
        )

        self.assertEqual(moved["card"]["column"], "in_progress")
        self.assertEqual(moved["card"]["position"], 1)
        self.assertEqual(moved["affected_column_ids"], ["inbox", "in_progress"])
        self.assertEqual(
            [card["id"] for card in moved["affected_cards"]],
            [first_target["card"]["id"], source["card"]["id"], second_target["card"]["id"]],
        )

        snapshot = self.service.get_board_snapshot()
        target_cards = sorted(
            [card for card in snapshot["cards"] if card["column"] == "in_progress"],
            key=lambda item: item["position"],
        )
        self.assertEqual(
            [card["id"] for card in target_cards[:3]],
            [first_target["card"]["id"], source["card"]["id"], second_target["card"]["id"]],
        )

    def test_rejects_card_description_above_limit(self) -> None:
        too_large_description = "Б" * (CARD_DESCRIPTION_LIMIT + 1)

        with self.assertRaises(ServiceError) as description_error:
            self.service.create_card(
                {
                    "title": "Слишком длинное описание",
                    "description": too_large_description,
                    "deadline": {"days": 0, "hours": 2},
                }
            )

        self.assertEqual(description_error.exception.code, "validation_error")

    def test_create_card_repairs_autofill_profile_metadata(self) -> None:
        created = self.service.create_card(
            {
                "title": "Mazda CX-5",
                "description": "VIN JM3KF123456789012",
                "deadline": {"days": 1},
                "vehicle_profile": {
                    "make_display": "Mazda",
                    "model_display": "CX-5",
                    "vin": "JM3KF123456789012",
                    "autofilled_fields": ["make_display", "model_display", "vin"],
                    "field_sources": {
                        "make_display": "official_vin_decode_nhtsa",
                        "model_display": "official_vin_decode_nhtsa",
                        "vin": "official_vin_decode_nhtsa",
                    },
                    "source_links_or_refs": ["https://vpic.nhtsa.dot.gov/api/vehicles/example"],
                    "data_completion_state": "mostly_autofilled",
                },
            }
        )

        profile = created["card"]["vehicle_profile"]
        self.assertEqual(profile["source_summary"], "official VIN decode")
        self.assertGreater(profile["source_confidence"], 0.0)
        self.assertEqual(profile["model_display"], "CX-5")

    def test_mcp_created_card_is_unread_until_marked_seen(self) -> None:
        created = self.service.create_card(
            {
                "title": "Через GPT",
                "description": "Карточка из MCP",
                "deadline": {"hours": 1},
                "source": "mcp",
            }
        )
        card = created["card"]
        card_id = card["id"]
        self.assertTrue(card["is_unread"])
        self.assertEqual(card["events_count"], 1)
        updated_at = card["updated_at"]

        marked = self.service.mark_card_seen({"card_id": card_id})
        self.assertTrue(marked["meta"]["changed"])
        self.assertFalse(marked["card"]["is_unread"])
        self.assertEqual(marked["card"]["updated_at"], updated_at)

        marked_again = self.service.mark_card_seen({"card_id": card_id})
        self.assertFalse(marked_again["meta"]["changed"])
        self.assertFalse(marked_again["card"]["is_unread"])

    def test_seen_user_gets_updated_badge_after_other_user_edits_card(self) -> None:
        created = self.service.create_card(
            {
                "title": "Seen card",
                "description": "Initial",
                "deadline": {"hours": 2},
                "actor_name": "ALICE",
            }
        )
        card_id = created["card"]["id"]

        seen = self.service.mark_card_seen({"card_id": card_id, "actor_name": "ALICE"})
        self.assertFalse(seen["card"]["is_unread"])
        self.assertFalse(seen["card"]["has_unseen_update"])

        updated = self.service.update_card(
            {
                "card_id": card_id,
                "description": "Updated by Bob",
                "actor_name": "BOB",
            }
        )
        self.assertFalse(updated["card"]["has_unseen_update"])

        alice_view = self.service.get_card({"card_id": card_id, "actor_name": "ALICE"})["card"]
        bob_view = self.service.get_card({"card_id": card_id, "actor_name": "BOB"})["card"]
        self.assertTrue(alice_view["has_unseen_update"])
        self.assertFalse(alice_view["is_unread"])
        self.assertFalse(bob_view["has_unseen_update"])

        marked = self.service.mark_card_seen({"card_id": card_id, "actor_name": "ALICE"})
        self.assertTrue(marked["meta"]["changed"])
        self.assertFalse(marked["card"]["has_unseen_update"])

    def test_deadline_status_transitions(self) -> None:
        base = datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc)
        patches = self._patch_time(base)
        with patches[0], patches[1], patches[2]:
            created = self.service.create_card({"title": "Срочная задача", "deadline": {"minutes": 1, "seconds": 40}})
        card_id = created["card"]["id"]
        self.assertEqual(created["card"]["remaining_seconds"], 100)
        self.assertEqual(created["card"]["status"], "ok")

        warning_time = base + timedelta(seconds=40)
        with patch("minimal_kanban.models.utc_now", return_value=warning_time):
            warning = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(warning["remaining_seconds"], 60)
        self.assertEqual(warning["status"], "warning")
        self.assertEqual(warning["indicator"], "yellow")
        self.assertFalse(warning["is_blinking"])

        critical_time = base + timedelta(seconds=85)
        with patch("minimal_kanban.models.utc_now", return_value=critical_time):
            critical = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(critical["remaining_seconds"], 15)
        self.assertEqual(critical["status"], "critical")
        self.assertEqual(critical["indicator"], "red")
        self.assertFalse(critical["is_blinking"])

        blinking_time = base + timedelta(seconds=95)
        with patch("minimal_kanban.models.utc_now", return_value=blinking_time):
            blinking = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(blinking["remaining_seconds"], 5)
        self.assertEqual(blinking["indicator"], "red")
        self.assertTrue(blinking["is_blinking"])

        expired_time = base + timedelta(seconds=101)
        with patch("minimal_kanban.models.utc_now", return_value=expired_time):
            expired = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(expired["remaining_seconds"], 0)
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(expired["indicator"], "red")
        self.assertTrue(expired["is_blinking"])

    def test_deadline_heat_progress_uses_five_percent_steps_and_resets_after_deadline_change(self) -> None:
        base = datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc)
        patches = self._patch_time(base)
        with patches[0], patches[1], patches[2]:
            created = self.service.create_card({"title": "Тепловая шкала", "deadline": {"minutes": 1, "seconds": 40}})
        card_id = created["card"]["id"]

        self.assertEqual(created["card"]["deadline_progress_bucket"], 0)
        self.assertEqual(created["card"]["deadline_progress_step_percent"], 0)

        almost_first_step = base + timedelta(seconds=4)
        with patch("minimal_kanban.models.utc_now", return_value=almost_first_step):
            early = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(early["deadline_progress_bucket"], 0)
        self.assertEqual(early["deadline_heat_color"], created["card"]["deadline_heat_color"])

        first_step_time = base + timedelta(seconds=5)
        with patch("minimal_kanban.models.utc_now", return_value=first_step_time):
            first_step = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(first_step["deadline_progress_bucket"], 1)
        self.assertEqual(first_step["deadline_progress_step_percent"], 5)

        same_bucket_time = base + timedelta(seconds=9)
        with patch("minimal_kanban.models.utc_now", return_value=same_bucket_time):
            same_bucket = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(same_bucket["deadline_progress_bucket"], 1)
        self.assertEqual(same_bucket["deadline_heat_color"], first_step["deadline_heat_color"])

        later_time = base + timedelta(seconds=26)
        with patch("minimal_kanban.models.utc_now", return_value=later_time):
            later = self.service.get_card({"card_id": card_id})["card"]
        self.assertEqual(later["deadline_progress_bucket"], 5)
        self.assertEqual(later["deadline_progress_step_percent"], 25)
        self.assertNotEqual(later["deadline_heat_color"], created["card"]["deadline_heat_color"])

        reset_patches = self._patch_time(later_time)
        with reset_patches[0], reset_patches[1], reset_patches[2]:
            reset = self.service.update_card({"card_id": card_id, "deadline": {"minutes": 3, "seconds": 20}})
        self.assertEqual(reset["card"]["deadline_progress_bucket"], 0)
        self.assertEqual(reset["card"]["deadline_progress_step_percent"], 0)
        self.assertEqual(reset["card"]["deadline_heat_color"], created["card"]["deadline_heat_color"])

    def test_rejects_invalid_input(self) -> None:
        with self.assertRaises(ServiceError) as empty_title:
            self.service.create_card({"title": "   ", "deadline": {"days": 1, "hours": 0}})
        self.assertEqual(empty_title.exception.code, "validation_error")

        created = self.service.create_card({"title": "Валидная карточка", "deadline": {"days": 1, "hours": 0}})
        card_id = created["card"]["id"]

        with self.assertRaises(ServiceError) as invalid_bool:
            self.service.get_cards({"include_archived": "false"})
        self.assertEqual(invalid_bool.exception.code, "validation_error")

        with self.assertRaises(ServiceError) as update_without_fields:
            self.service.update_card({"card_id": card_id})
        self.assertEqual(update_without_fields.exception.code, "validation_error")

        with self.assertRaises(ServiceError) as invalid_column:
            self.service.move_card({"card_id": card_id, "column": "trash"})
        self.assertEqual(invalid_column.exception.code, "validation_error")

        with self.assertRaises(ServiceError) as invalid_deadline:
            self.service.create_card({"title": "Сломанный срок", "deadline": {"days": 0, "hours": 0}})
        self.assertEqual(invalid_deadline.exception.code, "validation_error")

        with self.assertRaises(ServiceError) as invalid_deadline_part:
            self.service.create_card({"title": "Сломанный срок", "deadline": {"days": 0, "hours": 24}})
        self.assertEqual(invalid_deadline_part.exception.code, "validation_error")

        self.service.create_column({"label": "Новый этап"})
        with self.assertRaises(ServiceError) as duplicate_column:
            self.service.create_column({"label": "новый этап"})
        self.assertEqual(duplicate_column.exception.code, "validation_error")

    def test_archived_card_cannot_be_modified(self) -> None:
        created = self.service.create_card({"title": "Архив", "deadline": {"days": 1, "hours": 0}})
        card_id = created["card"]["id"]
        self.service.archive_card({"card_id": card_id})

        with self.assertRaises(ServiceError) as archived_error:
            self.service.update_card({"card_id": card_id, "title": "Нельзя"})
        self.assertEqual(archived_error.exception.code, "archived_card")

    def test_deadline_survives_service_reload(self) -> None:
        base = datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc)
        patches = self._patch_time(base)
        with patches[0], patches[1], patches[2]:
            created = self.service.create_card({"title": "Срок после перезапуска", "deadline": {"seconds": 10}})
        card_id = created["card"]["id"]

        reloaded_store = JsonStore(state_file=self.state_file, logger=self.logger)
        reloaded_service = CardService(reloaded_store, self.logger)

        later = base + timedelta(seconds=3)
        with patch("minimal_kanban.models.utc_now", return_value=later):
            reloaded_card = reloaded_service.get_card({"card_id": card_id})["card"]
        self.assertEqual(reloaded_card["remaining_seconds"], 7)
        self.assertEqual(reloaded_card["status"], "ok")

        much_later = base + timedelta(seconds=11)
        with patch("minimal_kanban.models.utc_now", return_value=much_later):
            expired_card = reloaded_service.get_card({"card_id": card_id})["card"]
        self.assertEqual(expired_card["remaining_seconds"], 0)
        self.assertEqual(expired_card["status"], "expired")

    def test_custom_column_survives_reload(self) -> None:
        created_column = self.service.create_column({"label": "Блокеры"})
        column_id = created_column["column"]["id"]
        self.assertEqual(created_column["column"]["label"], "Блокеры")

        created_card = self.service.create_card(
            {
                "title": "Проверка столбца",
                "deadline": {"days": 0, "hours": 6},
                "column": column_id,
            }
        )
        card_id = created_card["card"]["id"]
        self.assertEqual(created_card["card"]["column"], column_id)

        reloaded_store = JsonStore(state_file=self.state_file, logger=self.logger)
        reloaded_service = CardService(reloaded_store, self.logger)

        columns = reloaded_service.list_columns()["columns"]
        self.assertTrue(any(column["id"] == column_id and column["label"] == "Блокеры" for column in columns))

        card = reloaded_service.get_card({"card_id": card_id})["card"]
        self.assertEqual(card["column"], column_id)
        self.assertIn("deadline_timestamp", card)

    def test_delete_empty_column_removes_it_and_reorders_positions(self) -> None:
        created = self.service.create_column({"label": "TEMP DELETE"})
        column_id = created["column"]["id"]

        deleted = self.service.delete_column({"column_id": column_id})

        self.assertEqual(deleted["deleted_column"]["id"], column_id)
        remaining_ids = [column["id"] for column in deleted["columns"]]
        self.assertNotIn(column_id, remaining_ids)
        self.assertEqual([column["position"] for column in deleted["columns"]], list(range(len(deleted["columns"]))))

    def test_rename_column_updates_label_but_keeps_id(self) -> None:
        created = self.service.create_column({"label": "TEMP RENAME"})
        column_id = created["column"]["id"]

        renamed = self.service.rename_column({"column_id": column_id, "label": "READY FOR WORK"})

        self.assertEqual(renamed["column"]["id"], column_id)
        self.assertEqual(renamed["column"]["label"], "READY FOR WORK")
        self.assertTrue(renamed["meta"]["changed"])
        self.assertEqual(renamed["meta"]["previous_label"], "TEMP RENAME")
        listed = self.service.list_columns()["columns"]
        self.assertTrue(any(column["id"] == column_id and column["label"] == "READY FOR WORK" for column in listed))

    def test_rename_column_rejects_duplicate_label(self) -> None:
        self.service.create_column({"label": "FIRST CUSTOM"})
        created = self.service.create_column({"label": "SECOND CUSTOM"})

        with self.assertRaises(ServiceError) as duplicate_label:
            self.service.rename_column({"column_id": created["column"]["id"], "label": "FIRST CUSTOM"})
        self.assertEqual(duplicate_label.exception.code, "validation_error")

    def test_rename_column_allows_noop_for_same_label(self) -> None:
        created = self.service.create_column({"label": "UNCHANGED"})

        renamed = self.service.rename_column({"column_id": created["column"]["id"], "label": "UNCHANGED"})

        self.assertFalse(renamed["meta"]["changed"])
        self.assertEqual(renamed["column"]["label"], "UNCHANGED")

    def test_delete_column_rejects_non_empty(self) -> None:
        created_column = self.service.create_column({"label": "BLOCKED DELETE"})
        column_id = created_column["column"]["id"]
        self.service.create_card(
            {
                "title": "BOUND CARD",
                "deadline": {"hours": 2},
                "column": column_id,
            }
        )

        with self.assertRaises(ServiceError) as non_empty_error:
            self.service.delete_column({"column_id": column_id})
        self.assertEqual(non_empty_error.exception.code, "column_not_empty")

    def test_delete_native_column_allows_only_archived_cards(self) -> None:
        created_card = self.service.create_card(
            {
                "title": "ARCHIVED IN DONE",
                "deadline": {"hours": 2},
                "column": "done",
            }
        )
        card_id = created_card["card"]["id"]
        self.service.archive_card({"card_id": card_id})

        deleted = self.service.delete_column({"column_id": "done"})

        self.assertEqual(deleted["deleted_column"]["id"], "done")
        self.assertTrue(all(column["id"] != "done" for column in deleted["columns"]))
        archived_card = self.service.get_card({"card_id": card_id})["card"]
        self.assertTrue(archived_card["archived"])
        self.assertEqual(archived_card["column"], "inbox")
        self.assertEqual(archived_card["column_label"], "Входящие")

    def test_board_snapshot_returns_last_30_archived_cards_by_default(self) -> None:
        archived_ids: list[str] = []
        for index in range(35):
            created = self.service.create_card(
                {
                    "title": f"ARCHIVE {index}",
                    "description": f"Archived card {index}",
                    "deadline": {"hours": 1},
                }
            )
            card_id = created["card"]["id"]
            self.service.archive_card({"card_id": card_id})
            archived_ids.append(card_id)

        snapshot = self.service.get_board_snapshot()

        self.assertEqual(snapshot["meta"]["archive_limit"], 30)
        self.assertEqual(len(snapshot["archive"]), 30)
        self.assertEqual(
            [card["id"] for card in snapshot["archive"][:3]],
            archived_ids[-1:-4:-1],
        )

    def test_board_snapshot_compact_mode_skips_heavy_card_payload_fields(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "LEXUS IS F",
                "title": "Compact snapshot",
                "description": "Board card preview",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {"client": "Ivan", "phone": "+79001234567"},
            }
        )

        snapshot = self.service.get_board_snapshot({"compact": True})

        self.assertTrue(snapshot["meta"]["compact_cards"])
        compact_card = next(card for card in snapshot["cards"] if card["id"] == card_id)
        self.assertIn("tag_items", compact_card)
        self.assertIn("attachment_count", compact_card)
        self.assertNotIn("repair_order", compact_card)
        self.assertNotIn("vehicle_profile", compact_card)
        self.assertNotIn("attachments", compact_card)

    def test_board_snapshot_revision_stays_stable_until_board_changes(self) -> None:
        first_snapshot = self.service.get_board_snapshot({"compact": True})
        second_snapshot = self.service.get_board_snapshot({"compact": True})

        self.assertEqual(first_snapshot["meta"]["revision"], second_snapshot["meta"]["revision"])

        self.service.create_card(
            {
                "vehicle": "Lexus IS F",
                "title": "Revision test",
                "deadline": {"hours": 2},
            }
        )
        changed_snapshot = self.service.get_board_snapshot({"compact": True})

        self.assertNotEqual(first_snapshot["meta"]["revision"], changed_snapshot["meta"]["revision"])

    def test_board_snapshot_skips_expensive_prep_when_there_are_no_cards(self) -> None:
        snapshot_service = self.service._snapshot_service
        snapshot_service._column_labels = Mock(wraps=snapshot_service._column_labels)
        snapshot_service._event_counts = Mock(wraps=snapshot_service._event_counts)

        snapshot = self.service.get_board_snapshot()

        self.assertEqual(snapshot["cards"], [])
        self.assertEqual(snapshot["archive"], [])
        self.assertEqual(snapshot_service._column_labels.call_count, 0)
        self.assertEqual(snapshot_service._event_counts.call_count, 0)

    def test_list_archived_cards_skips_expensive_prep_when_archive_is_empty(self) -> None:
        snapshot_service = self.service._snapshot_service
        snapshot_service._column_labels = Mock(wraps=snapshot_service._column_labels)
        snapshot_service._event_counts = Mock(wraps=snapshot_service._event_counts)

        archived = self.service.list_archived_cards()

        self.assertEqual(archived["cards"], [])
        self.assertEqual(snapshot_service._column_labels.call_count, 0)
        self.assertEqual(snapshot_service._event_counts.call_count, 0)

    def test_store_keeps_only_latest_archived_cards_within_retention_limit(self) -> None:
        with patch("minimal_kanban.storage.json_store.ARCHIVED_CARD_RETENTION_LIMIT", 2):
            archived_ids: list[str] = []
            for index in range(3):
                created = self.service.create_card(
                    {
                        "title": f"RETENTION ARCHIVE {index}",
                        "deadline": {"hours": 1},
                    }
                )
                card_id = created["card"]["id"]
                self.service.archive_card({"card_id": card_id})
                archived_ids.append(card_id)

        archived_cards = [card for card in self.store.read_bundle()["cards"] if card.archived]

        self.assertEqual(len(archived_cards), 2)
        self.assertEqual({card.id for card in archived_cards}, set(archived_ids[-2:]))

    def test_store_prunes_old_audit_events_outside_retention_window(self) -> None:
        bundle = self.store.read_bundle()
        now = utc_now()
        events = [
            AuditEvent(
                id="old-event",
                timestamp=(now - timedelta(days=61)).isoformat(),
                actor_name="ADMIN",
                source="ui",
                action="card_archived",
                message="Old archived event",
                card_id="old-card",
                details={},
            ),
            AuditEvent(
                id="recent-event",
                timestamp=(now - timedelta(days=2)).isoformat(),
                actor_name="ADMIN",
                source="ui",
                action="card_moved",
                message="Recent move event",
                card_id="recent-card",
                details={},
            ),
        ]

        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            events=events,
            settings=bundle["settings"],
        )

        stored_events = self.store.read_bundle()["events"]

        self.assertEqual(len(stored_events), 1)
        self.assertEqual(stored_events[0].id, "recent-event")

    def test_delete_column_rejects_last_remaining_column(self) -> None:
        for doomed_id in ["done", "control", "in_progress"]:
            deleted = self.service.delete_column({"column_id": doomed_id})
            self.assertTrue(all(column["id"] != doomed_id for column in deleted["columns"]))

        with self.assertRaises(ServiceError) as last_column_error:
            self.service.delete_column({"column_id": "inbox"})
        self.assertEqual(last_column_error.exception.code, "last_column")

    def test_set_card_deadline_indicator_and_list_overdue(self) -> None:
        base = datetime(2026, 3, 24, 12, 0, 0, tzinfo=timezone.utc)
        patches = self._patch_time(base)
        with patches[0], patches[1], patches[2]:
            created = self.service.create_card({"title": "Удалённая задача", "deadline": {"hours": 3}})
        card_id = created["card"]["id"]

        later = base + timedelta(minutes=5)
        patches = self._patch_time(later)
        with patches[0], patches[1], patches[2]:
            deadline_updated = self.service.set_card_deadline({"card_id": card_id, "deadline": {"minutes": 1}})
        self.assertLessEqual(deadline_updated["card"]["remaining_seconds"], 60)

        indicator_time = later + timedelta(seconds=5)
        patches = self._patch_time(indicator_time)
        with patches[0], patches[1], patches[2]:
            yellow = self.service.set_card_indicator({"card_id": card_id, "indicator": "yellow"})
        self.assertEqual(yellow["card"]["indicator"], "yellow")
        self.assertEqual(yellow["card"]["status"], "warning")

        expired_time = indicator_time + timedelta(seconds=1)
        patches = self._patch_time(expired_time)
        with patches[0], patches[1], patches[2]:
            red = self.service.set_card_indicator({"card_id": card_id, "indicator": "red"})
            overdue = self.service.list_overdue_cards()
        self.assertEqual(red["card"]["indicator"], "red")
        self.assertEqual(red["card"]["status"], "expired")
        self.assertTrue(any(card["id"] == card_id for card in overdue["cards"]))

    def test_list_overdue_cards_skips_expensive_prep_when_empty(self) -> None:
        snapshot_service = self.service._snapshot_service
        snapshot_service._column_labels = Mock(wraps=snapshot_service._column_labels)
        snapshot_service._event_counts = Mock(wraps=snapshot_service._event_counts)

        overdue = self.service.list_overdue_cards()

        self.assertEqual(overdue["cards"], [])
        self.assertEqual(snapshot_service._column_labels.call_count, 0)
        self.assertEqual(snapshot_service._event_counts.call_count, 0)

    def test_review_board_returns_operational_summary(self) -> None:
        base = datetime(2026, 4, 5, 10, 0, 0, tzinfo=timezone.utc)
        patches = self._patch_time(base)
        with patches[0], patches[1], patches[2]:
            overdue_card = self.service.create_card(
                {
                    "vehicle": "Toyota Camry",
                    "title": "Шум АКПП",
                    "description": "Проверить гидроблок",
                    "deadline": {"hours": 1},
                }
            )
            self.service.create_card(
                {
                    "vehicle": "Kia Rio",
                    "title": "Стук подвески",
                    "description": "Осмотр передней оси",
                    "deadline": {"days": 3},
                }
            )
            self.service.create_card(
                {
                    "vehicle": "Mazda CX-5",
                    "title": "Диагностика ABS",
                    "description": "Горит ABS",
                    "deadline": {"days": 3},
                }
            )
            archived_card = self.service.create_card(
                {
                    "vehicle": "Nissan X-Trail",
                    "title": "Архивный заказ",
                    "description": "Закрытая работа",
                    "deadline": {"hours": 6},
                }
            )
            self.service.archive_card({"card_id": archived_card["card"]["id"]})

        review_moment = base + timedelta(hours=50)
        with patch("minimal_kanban.services.snapshot_service.utc_now", return_value=review_moment), patch(
            "minimal_kanban.services.snapshot_service.utc_now_iso",
            return_value=review_moment.isoformat(),
        ):
            review = self.service.review_board(
                {
                    "stale_hours": 24,
                    "overload_threshold": 2,
                    "priority_limit": 5,
                    "recent_event_limit": 5,
                }
            )

        self.assertEqual(review["summary"]["active_cards"], 3)
        self.assertEqual(review["summary"]["archived_cards"], 1)
        self.assertEqual(review["summary"]["overdue_cards"], 1)
        self.assertGreaterEqual(review["summary"]["critical_cards"], 1)
        self.assertEqual(review["summary"]["stale_cards"], 3)
        self.assertTrue(any(item["column_id"] == "inbox" and item["count"] == 3 for item in review["by_column"]))
        self.assertTrue(any("перегружена" in item for item in review["alerts"]))
        self.assertEqual(review["priority_cards"][0]["card_id"], overdue_card["card"]["id"])
        self.assertIn("Просрочена", review["priority_cards"][0]["short_reason"])
        self.assertTrue(any(item["type"] == "card_archived" for item in review["recent_events"]))
        self.assertIn("[BOARD REVIEW]", review["text"])

    def test_rejects_invalid_indicator(self) -> None:
        created = self.service.create_card({"title": "Индикатор", "deadline": {"hours": 1}})
        card_id = created["card"]["id"]
        with self.assertRaises(ServiceError) as invalid_indicator:
            self.service.set_card_indicator({"card_id": card_id, "indicator": "blue"})
        self.assertEqual(invalid_indicator.exception.code, "validation_error")

    def test_legacy_combined_title_is_split_on_load(self) -> None:
        card = Card.from_dict(
            {
                "id": "legacy-card",
                "title": "CAMRY 70 / НЕТ ЗАПУСКА",
                "description": "Проверить АКБ",
                "column": "inbox",
            },
            valid_columns={"inbox"},
        )
        self.assertEqual(card.vehicle, "CAMRY 70")
        self.assertEqual(card.title, "НЕТ ЗАПУСКА")


    def test_explicit_empty_vehicle_preserves_title_with_separator(self) -> None:
        card = Card.from_dict(
            {
                "id": "modern-card",
                "vehicle": "",
                "title": "MCP write flow / updated",
                "description": "Smoke test",
                "column": "inbox",
            },
            valid_columns={"inbox"},
        )
        self.assertEqual(card.vehicle, "")
        self.assertEqual(card.title, "MCP write flow / updated")

    def test_seeds_demo_board_once_for_pristine_store(self) -> None:
        seeded = self.service.ensure_demo_board()
        snapshot = self.service.get_board_snapshot()

        self.assertTrue(seeded)
        self.assertGreaterEqual(len(snapshot["columns"]), 6)
        self.assertGreaterEqual(len(snapshot["cards"]), 10)
        self.assertGreaterEqual(len(snapshot["archive"]), 2)
        self.assertTrue(any(column["label"] == "ПРИЁМКА" for column in snapshot["columns"]))
        self.assertTrue(any(card["vehicle"] == "CAMRY 70" and card["title"] == "НЕТ ЗАПУСКА" for card in snapshot["cards"]))
        self.assertFalse(self.service.ensure_demo_board())

    def test_does_not_seed_demo_board_when_user_data_exists(self) -> None:
        created = self.service.create_card({"title": "Моя карточка", "deadline": {"hours": 2}})
        seeded = self.service.ensure_demo_board()
        cards = self.service.get_cards()["cards"]

        self.assertFalse(seeded)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["id"], created["card"]["id"])

    def test_seeds_demo_board_for_empty_generic_board_with_only_setup_events(self) -> None:
        bundle = self.store.read_bundle()
        bundle["columns"] = [column for column in bundle["columns"] if column.id != "control"]
        bundle["events"].append(
            AuditEvent(
                id="setup-column-delete",
                timestamp=utc_now().isoformat(),
                actor_name="ADMIN",
                source="ui",
                action="column_deleted",
                message="ADMIN удалил столбец",
                card_id=None,
                details={"column_id": "control", "label": "На контроле"},
            )
        )
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        seeded = self.service.ensure_demo_board()
        snapshot = self.service.get_board_snapshot()

        self.assertTrue(seeded)
        self.assertGreaterEqual(len(snapshot["columns"]), 6)
        self.assertTrue(any(column["id"] == "priemka" for column in snapshot["columns"]))

    def test_get_cards_skips_expensive_prep_when_board_is_empty(self) -> None:
        snapshot_service = self.service._snapshot_service
        snapshot_service._column_labels = Mock(wraps=snapshot_service._column_labels)
        snapshot_service._event_counts = Mock(wraps=snapshot_service._event_counts)

        cards = self.service.get_cards()["cards"]

        self.assertEqual(cards, [])
        self.assertEqual(snapshot_service._column_labels.call_count, 0)
        self.assertEqual(snapshot_service._event_counts.call_count, 0)

    def test_search_cards_supports_query_filters_and_archive(self) -> None:
        created_column = self.service.create_column({"label": "ЭЛЕКТРИКИ"})
        column_id = created_column["column"]["id"]

        active = self.service.create_card(
            {
                "vehicle": "KIA RIO",
                "title": "ПЛАВАЕТ ХОЛОСТОЙ ХОД",
                "description": "Проверить дроссель и датчик холостого хода",
                "column": column_id,
                "tags": ["СРОЧНО", "ДИАГНОСТИКА"],
                "deadline": {"hours": 12},
            }
        )
        archived = self.service.create_card(
            {
                "vehicle": "LADA VESTA",
                "title": "АРХИВНАЯ ПРОВЕРКА",
                "description": "Старый кейс для возврата из архива",
                "tags": ["АРХИВ"],
                "deadline": {"hours": 4},
            }
        )
        self.service.archive_card({"card_id": archived["card"]["id"]})

        found = self.service.search_cards(
            {
                "query": "rio дроссель",
                "column": column_id,
                "tag": "срочно",
                "limit": 10,
            }
        )
        self.assertEqual(found["meta"]["total_matches"], 1)
        self.assertEqual(found["cards"][0]["id"], active["card"]["id"])
        self.assertEqual(found["cards"][0]["column_label"], "ЭЛЕКТРИКИ")
        self.assertEqual(found["cards"][0]["heading"], "KIA RIO / ПЛАВАЕТ ХОЛОСТОЙ ХОД")
        self.assertIn("vehicle", found["cards"][0]["match"]["fields"])

        archived_found = self.service.search_cards({"query": "архивная", "include_archived": True})
        self.assertEqual(archived_found["meta"]["total_matches"], 1)
        self.assertTrue(archived_found["cards"][0]["archived"])

        with self.assertRaises(ServiceError) as empty_search:
            self.service.search_cards({})
        self.assertEqual(empty_search.exception.code, "validation_error")

    def test_search_cards_skips_event_count_build_when_no_matches(self) -> None:
        self.service.create_card(
            {
                "vehicle": "KIA RIO",
                "title": "SEARCH MISS",
                "description": "Card for empty search result optimization check.",
                "deadline": {"hours": 2},
            }
        )
        snapshot_service = self.service._snapshot_service
        snapshot_service._column_labels = Mock(wraps=snapshot_service._column_labels)
        snapshot_service._event_counts = Mock(wraps=snapshot_service._event_counts)

        found = self.service.search_cards({"query": "totally-missing-query", "limit": 5})

        self.assertEqual(found["cards"], [])
        self.assertEqual(found["meta"]["total_matches"], 0)
        self.assertEqual(snapshot_service._column_labels.call_count, 0)
        self.assertEqual(snapshot_service._event_counts.call_count, 0)

    def test_search_cards_normalizes_punctuation_and_service_markers(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "TEST-CAR",
                "title": "[MCP TEST] Поиск по маркерам",
                "description": "Проверка поиска по mcp-test, скобкам и дефисам.",
                "tags": ["MCP_TEST", "SEARCH-CHECK"],
                "deadline": {"hours": 3},
            }
        )
        card_id = created["card"]["id"]

        by_plain_text = self.service.search_cards({"query": "mcp test", "limit": 5})
        self.assertEqual(by_plain_text["meta"]["total_matches"], 1)
        self.assertEqual(by_plain_text["cards"][0]["id"], card_id)

        by_hyphenated = self.service.search_cards({"query": "mcp-test", "limit": 5})
        self.assertEqual(by_hyphenated["meta"]["total_matches"], 1)
        self.assertEqual(by_hyphenated["cards"][0]["id"], card_id)

        by_tag_variant = self.service.search_cards({"query": "search check", "limit": 5})
        self.assertEqual(by_tag_variant["meta"]["total_matches"], 1)
        self.assertEqual(by_tag_variant["cards"][0]["id"], card_id)
        self.assertIn("tags", by_tag_variant["cards"][0]["match"]["fields"])

    def test_colored_tags_roundtrip_and_search_by_label(self) -> None:
        created = self.service.create_card(
            {
                "title": "Цветные метки",
                "description": "Проверка цветов",
                "tags": [
                    {"label": "СРОЧНО", "color": "red"},
                    {"label": "СОГЛАСОВАТЬ", "color": "yellow"},
                ],
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        self.assertEqual(created["card"]["tags"], ["СРОЧНО", "СОГЛАСОВАТЬ"])
        self.assertEqual(created["card"]["tag_items"][0]["color"], "red")
        self.assertEqual(created["card"]["tag_items"][1]["color"], "yellow")

        found = self.service.search_cards({"query": "согласовать", "tag": "срочно", "limit": 5})
        self.assertEqual(found["meta"]["total_matches"], 1)
        self.assertEqual(found["cards"][0]["id"], card_id)

        updated = self.service.update_card(
            {
                "card_id": card_id,
                "tags": [
                    {"label": "СРОЧНО", "color": "yellow"},
                    {"label": "СОГЛАСОВАТЬ", "color": "green"},
                ],
            }
        )
        self.assertEqual(updated["card"]["tag_items"][0]["color"], "yellow")
        self.assertEqual(updated["card"]["tag_items"][1]["color"], "green")
        events = self.service.get_card_log({"card_id": card_id})["events"]
        self.assertTrue(
            any(event["action"] == "tag_color_changed" and "изменил цвет метки" in event["message"] for event in events)
        )

    def test_rejects_more_than_three_tags(self) -> None:
        with self.assertRaises(ServiceError) as tag_limit_error:
            self.service.create_card(
                {
                    "title": "Слишком много меток",
                    "description": "Проверка ограничения",
                    "tags": ["СРОЧНО", "ЖДЁМ", "СОГЛАСОВАТЬ", "ЗАКАЗАТЬ"],
                    "deadline": {"hours": 2},
                }
            )

        self.assertEqual(tag_limit_error.exception.code, "validation_error")

    def test_create_card_supports_vehicle_profile_and_resolves_vehicle_label(self) -> None:
        created = self.service.create_card(
            {
                "title": "Техкарта Swift",
                "description": "Нужно собрать данные по автомобилю",
                "deadline": {"hours": 6},
                "vehicle_profile": {
                    "make_display": "Suzuki",
                    "model_display": "Swift",
                    "production_year": 2014,
                    "vin": "JSAZC72S001234567",
                    "engine_code": "K12B",
                },
            }
        )

        self.assertEqual(created["card"]["vehicle"], "Suzuki Swift 2014")
        self.assertEqual(created["card"]["vehicle_profile"]["vin"], "JSAZC72S001234567")
        self.assertEqual(created["card"]["vehicle_profile_compact"]["vin"], "JSAZC72S001234567")
        self.assertEqual(created["card"]["vehicle_profile_compact"]["display_name"], "Suzuki Swift 2014")
        self.assertIn("make_display", created["card"]["vehicle_profile"]["manual_fields"])
        self.assertIn("engine_code", created["card"]["vehicle_profile"]["manual_fields"])

    def test_update_card_stores_repair_order_and_persists_it(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "KIA RIO",
                "title": "Замена масла",
                "description": "Клиент просит срочное обслуживание",
                "deadline": {"hours": 4},
            }
        )
        card_id = created["card"]["id"]

        updated = self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "vehicle": "KIA RIO",
                    "license_plate": "А123АА124",
                    "client_information": "Кратко объяснить клиенту объём работ и следующие шаги",
                    "works": [{"name": "Замена масла", "quantity": "1", "price": "2500", "total": ""}],
                    "materials": [{"name": "Масло 5W-30", "quantity": "4", "price": "700", "total": "9999"}],
                },
            }
        )

        order = updated["card"]["repair_order"]
        self.assertEqual(order["number"], "1")
        self.assertRegex(order["date"], r"^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$")
        self.assertEqual(order["client"], "Иван Иванов")
        self.assertEqual(order["comment"], "Кратко объяснить клиенту объём работ и следующие шаги")
        self.assertEqual(order["client_information"], order["comment"])
        self.assertEqual(order["works"][0]["name"], "Замена масла")
        self.assertEqual(order["works"][0]["total"], "2500")
        self.assertEqual(order["materials"][0]["total"], "2800")
        self.assertEqual(order["works_total"], "2500")
        self.assertEqual(order["materials_total"], "2800")
        self.assertEqual(order["grand_total"], "5300")

        reloaded = CardService(JsonStore(state_file=self.state_file, logger=self.logger), self.logger)
        stored = reloaded.get_card({"card_id": card_id})["card"]["repair_order"]
        self.assertEqual(stored["number"], "1")
        self.assertEqual(stored["license_plate"], "А123АА124")
        self.assertEqual(stored["client_information"], "Кратко объяснить клиенту объём работ и следующие шаги")
        self.assertEqual(stored["works"][0]["quantity"], "1")
        self.assertEqual(stored["grand_total"], "5300")

    def test_list_repair_orders_creates_text_files_and_sorts_by_latest_number(self) -> None:
        first = self.service.create_card({"vehicle": "KIA RIO", "title": "Первый заказ", "deadline": {"hours": 2}})
        second = self.service.create_card({"vehicle": "LADA VESTA", "title": "Второй заказ", "deadline": {"hours": 2}})

        first_id = first["card"]["id"]
        second_id = second["card"]["id"]

        self.service.update_card(
            {
                "card_id": first_id,
                "repair_order": {
                    "client": "Иван",
                    "comment": "Первый текстовый заказ-наряд",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000", "total": "1000"}],
                },
            }
        )
        self.service.update_card(
            {
                "card_id": second_id,
                "repair_order": {
                    "client": "Петр",
                    "comment": "Второй текстовый заказ-наряд",
                    "materials": [{"name": "Масло", "quantity": "4", "price": "700", "total": "2800"}],
                },
            }
        )

        listed = self.service.list_repair_orders()
        self.assertEqual(listed["meta"]["limit"], 300)
        self.assertEqual(listed["repair_orders"][0]["number"], "2")
        self.assertEqual(listed["repair_orders"][1]["number"], "1")
        self.assertEqual(listed["repair_orders"][0]["grand_total"], "2800")
        self.assertEqual(listed["repair_orders"][0]["vehicle"], "LADA VESTA")
        self.assertEqual(listed["repair_orders"][0]["created_at"], second["card"]["created_at"])

        file_path = Path(listed["repair_orders"][0]["file_path"])
        self.assertTrue(file_path.exists())
        text = file_path.read_text(encoding="utf-8")
        self.assertIn("ЗАКАЗ-НАРЯД", text)
        self.assertIn("Информация для клиента:", text)
        self.assertIn("Материалы:", text)
        self.assertIn("Итого материалы: 2800", text)
        self.assertIn("Итого к оплате: 2800", text)
        self.assertIn("Второй текстовый заказ-наряд", text)

        download_path, file_name = self.service.get_repair_order_text_download(second_id)
        self.assertEqual(download_path.name, file_name)
        self.assertEqual(download_path, file_path)

    def test_list_repair_orders_serializes_only_requested_limit(self) -> None:
        for index in range(3):
            created = self.service.create_card(
                {"vehicle": f"CAR-{index}", "title": f"Order {index}", "deadline": {"hours": 2}}
            )
            self.service.update_card(
                {
                    "card_id": created["card"]["id"],
                    "repair_order": {
                        "client": f"Client {index}",
                        "works": [{"name": f"Work {index}", "quantity": "1", "price": "1000", "total": "1000"}],
                    },
                }
            )

        with patch.object(
            self.service,
            "_serialize_repair_order_list_item",
            wraps=self.service._serialize_repair_order_list_item,
        ) as serialize_item:
            listed = self.service.list_repair_orders({"limit": 2})

        self.assertEqual(listed["meta"]["total"], 3)
        self.assertEqual(listed["meta"]["limit"], 2)
        self.assertEqual(len(listed["repair_orders"]), 2)
        self.assertEqual(serialize_item.call_count, 2)
        self.assertEqual([item["number"] for item in listed["repair_orders"]], ["3", "2"])

    def test_list_repair_orders_cleans_up_old_text_files_beyond_retention_limit(self) -> None:
        first = self.service.create_card({"vehicle": "KIA RIO", "title": "Order one", "deadline": {"hours": 2}})
        second = self.service.create_card({"vehicle": "LADA VESTA", "title": "Order two", "deadline": {"hours": 2}})

        self.service.update_card(
            {
                "card_id": first["card"]["id"],
                "repair_order": {"client": "A", "works": [{"name": "W1", "quantity": "1", "price": "1", "total": "1"}]},
            }
        )
        self.service.update_card(
            {
                "card_id": second["card"]["id"],
                "repair_order": {"client": "B", "works": [{"name": "W2", "quantity": "1", "price": "2", "total": "2"}]},
            }
        )

        first_path, _ = self.service.get_repair_order_text_download(first["card"]["id"])
        second_path, _ = self.service.get_repair_order_text_download(second["card"]["id"])
        self.assertTrue(first_path.exists())
        self.assertTrue(second_path.exists())

        with patch("minimal_kanban.services.card_service.REPAIR_ORDER_FILE_RETENTION_LIMIT", 1):
            self.service.list_repair_orders()

        self.assertFalse(first_path.exists())
        self.assertTrue(second_path.exists())

    def test_repair_order_text_file_name_sanitizes_windows_unsafe_characters(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "BMW X5",
                "title": "Диагностика: ограничение мощности / DSC?",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]

        updated = self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000", "total": ""}],
                },
            }
        )

        path, file_name = self.service.get_repair_order_text_download(card_id)

        self.assertTrue(path.exists())
        self.assertEqual(path.name, file_name)
        self.assertNotIn(":", file_name)
        self.assertNotIn("?", file_name)
        self.assertNotIn("/", file_name)
        self.assertTrue(file_name.endswith(".txt"))
        self.assertIn("__", file_name)
        self.assertEqual(updated["card"]["repair_order"]["number"], "1")

    def test_list_repair_orders_separates_open_and_closed_orders(self) -> None:
        first = self.service.create_card({"vehicle": "KIA RIO", "title": "Open order", "deadline": {"hours": 2}})
        second = self.service.create_card({"vehicle": "LADA VESTA", "title": "Closed order", "deadline": {"hours": 2}})

        self.service.update_card(
            {
                "card_id": first["card"]["id"],
                "repair_order": {
                    "client": "Иван",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1000", "total": ""}],
                },
            }
        )
        self.service.update_card(
            {
                "card_id": second["card"]["id"],
                "repair_order": {
                    "client": "Пётр",
                    "works": [{"name": "Ремонт", "quantity": "1", "price": "2000", "total": ""}],
                },
            }
        )
        self.service.set_repair_order_status({"card_id": second["card"]["id"], "status": "closed"})

        active = self.service.list_repair_orders()
        archived = self.service.list_repair_orders({"status": "closed"})
        all_orders = self.service.list_repair_orders({"status": "all"})

        self.assertEqual(active["meta"]["status"], "open")
        self.assertEqual(active["meta"]["active_total"], 1)
        self.assertEqual(active["meta"]["archived_total"], 1)
        self.assertEqual([item["card_id"] for item in active["repair_orders"]], [first["card"]["id"]])

        self.assertEqual(archived["meta"]["status"], "closed")
        self.assertEqual([item["card_id"] for item in archived["repair_orders"]], [second["card"]["id"]])
        self.assertEqual(archived["repair_orders"][0]["status"], "closed")

        self.assertEqual(all_orders["meta"]["total"], 2)
        self.assertEqual(all_orders["repair_orders"][0]["card_id"], second["card"]["id"])

    def test_repair_order_numbers_follow_card_open_time_not_update_order(self) -> None:
        first = self.service.create_card({"vehicle": "KIA RIO", "title": "First card", "deadline": {"hours": 2}})
        second = self.service.create_card({"vehicle": "LADA VESTA", "title": "Second card", "deadline": {"hours": 2}})

        self.service.update_card(
            {
                "card_id": second["card"]["id"],
                "repair_order": {
                    "client": "Пётр",
                    "works": [{"name": "Поздняя в списке первая", "quantity": "1", "price": "1000", "total": ""}],
                },
            }
        )
        self.service.update_card(
            {
                "card_id": first["card"]["id"],
                "repair_order": {
                    "client": "Иван",
                    "works": [{"name": "Хронологически первая карточка", "quantity": "1", "price": "1000", "total": ""}],
                },
            }
        )

        listed = self.service.list_repair_orders({"status": "all"})
        by_card_id = {item["card_id"]: item for item in listed["repair_orders"]}

        self.assertEqual(by_card_id[first["card"]["id"]]["number"], "1")
        self.assertEqual(by_card_id[second["card"]["id"]]["number"], "2")

    def test_list_repair_orders_supports_query_sort_and_tags(self) -> None:
        first = self.service.create_card({"vehicle": "Audi A6", "title": "Диагностика DSG", "deadline": {"hours": 2}})
        second = self.service.create_card({"vehicle": "BMW X5", "title": "Замена масла", "deadline": {"hours": 2}})

        self.service.update_repair_order(
            {
                "card_id": first["card"]["id"],
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "comment": "Проверить DSG и согласовать диагностику",
                    "tags": [
                        {"label": "Срочно", "color": "yellow"},
                        {"label": "DSG", "color": "green"},
                    ],
                    "works": [{"name": "Диагностика DSG", "quantity": "1", "price": "2500", "total": ""}],
                },
            }
        )
        self.service.update_repair_order(
            {
                "card_id": second["card"]["id"],
                "repair_order": {
                    "client": "Петр Петров",
                    "phone": "+7 901 000-11-22",
                    "comment": "Стандартное ТО",
                    "works": [{"name": "Замена масла", "quantity": "1", "price": "1500", "total": ""}],
                },
            }
        )

        filtered = self.service.list_repair_orders(
            {
                "status": "all",
                "query": "срочно иван dsg",
                "sort_by": "number",
                "sort_dir": "asc",
            }
        )

        self.assertEqual(filtered["meta"]["status"], "all")
        self.assertEqual(filtered["meta"]["query"], "срочно иван dsg")
        self.assertEqual(filtered["meta"]["sort_by"], "number")
        self.assertEqual(filtered["meta"]["sort_dir"], "asc")
        self.assertEqual(len(filtered["repair_orders"]), 1)
        self.assertEqual(filtered["repair_orders"][0]["card_id"], first["card"]["id"])
        self.assertEqual(
            filtered["repair_orders"][0]["tags"],
            [
                {"label": "СРОЧНО", "color": "yellow"},
                {"label": "DSG", "color": "green"},
            ],
        )

        ordered = self.service.list_repair_orders({"status": "all", "sort_by": "number", "sort_dir": "asc"})
        self.assertEqual([item["number"] for item in ordered["repair_orders"]], ["1", "2"])

    def test_archived_card_retention_cleans_up_orphan_attachment_directories(self) -> None:
        attachments_dir = Path(self.temp_dir.name) / "attachments"
        service = CardService(self.store, self.logger, attachments_dir=attachments_dir)

        with patch("minimal_kanban.storage.json_store.ARCHIVED_CARD_RETENTION_LIMIT", 1):
            first = service.create_card({"vehicle": "KIA RIO", "title": "Archive one", "deadline": {"hours": 2}})
            second = service.create_card({"vehicle": "LADA VESTA", "title": "Archive two", "deadline": {"hours": 2}})

            service.add_card_attachment(
                {
                    "card_id": first["card"]["id"],
                    "file_name": "first.txt",
                    "mime_type": "text/plain",
                    "content_base64": base64.b64encode(b"first").decode("ascii"),
                }
            )
            service.add_card_attachment(
                {
                    "card_id": second["card"]["id"],
                    "file_name": "second.txt",
                    "mime_type": "text/plain",
                    "content_base64": base64.b64encode(b"second").decode("ascii"),
                }
            )

            first_dir = attachments_dir / first["card"]["id"]
            second_dir = attachments_dir / second["card"]["id"]
            self.assertTrue(first_dir.exists())
            self.assertTrue(second_dir.exists())

            service.archive_card({"card_id": first["card"]["id"]})
            self.assertTrue(first_dir.exists())

            service.archive_card({"card_id": second["card"]["id"]})

        self.assertFalse(first_dir.exists())
        self.assertTrue(second_dir.exists())

    def test_remove_card_attachment_deletes_file_and_empty_card_directory(self) -> None:
        attachments_dir = Path(self.temp_dir.name) / "attachments"
        service = CardService(self.store, self.logger, attachments_dir=attachments_dir)
        created = service.create_card({"vehicle": "KIA RIO", "title": "Attachment remove", "deadline": {"hours": 2}})
        card_id = created["card"]["id"]

        added = service.add_card_attachment(
            {
                "card_id": card_id,
                "file_name": "report.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(b"hello").decode("ascii"),
            }
        )
        attachment_id = added["attachment"]["id"]
        file_path, _ = service.get_attachment_download(card_id, attachment_id)

        self.assertTrue(file_path.exists())
        self.assertTrue(file_path.parent.exists())

        removed = service.remove_card_attachment({"card_id": card_id, "attachment_id": attachment_id})

        self.assertFalse(file_path.exists())
        self.assertFalse(file_path.parent.exists())
        self.assertEqual(removed["card"]["attachment_count"], 0)

    def test_get_card_context_returns_repair_order_text_and_board_context(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "BMW 320i",
                "title": "Горит чек",
                "description": "Клиент жалуется на нестабильную работу двигателя",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "works": [{"name": "Диагностика", "quantity": "1", "price": "1200", "total": ""}],
                },
            }
        )

        context = self.service.get_card_context({"card_id": card_id, "event_limit": 10})

        self.assertEqual(context["card"]["id"], card_id)
        self.assertEqual(context["card"]["events_count"], 2)
        self.assertTrue(context["meta"]["has_repair_order"])
        self.assertEqual(context["meta"]["events_returned"], 2)
        self.assertIn("Current AutoStop CRM Board", context["board_context"]["text"])
        self.assertIn("repair_order_updated", {event["action"] for event in context["events"]})
        self.assertIn("ЗАКАЗ-НАРЯД", context["repair_order_text"]["text"])
        self.assertIn("Итого к оплате: 1200", context["repair_order_text"]["text"])

    def test_repair_order_patch_and_row_replacement_tools_update_order(self) -> None:
        created = self.service.create_card({"vehicle": "KIA RIO", "title": "Ремонт", "deadline": {"hours": 2}})
        card_id = created["card"]["id"]

        patched = self.service.update_repair_order(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Петров Пётр",
                    "phone": "+7 999 123-45-67",
                    "client_information": "Нужно согласовать объём работ",
                },
            }
        )
        self.assertEqual(patched["repair_order"]["client"], "Петров Пётр")
        self.assertEqual(patched["repair_order"]["comment"], "Нужно согласовать объём работ")

        works = self.service.replace_repair_order_works(
            {
                "card_id": card_id,
                "rows": [
                    {"name": "Диагностика", "quantity": "1", "price": "1500", "total": ""},
                    {"name": "Снятие ошибок", "quantity": "1", "price": "500", "total": ""},
                ],
            }
        )
        self.assertEqual(len(works["repair_order"]["works"]), 2)
        self.assertEqual(works["repair_order"]["works_total"], "2000")

        materials = self.service.replace_repair_order_materials(
            {
                "card_id": card_id,
                "rows": [
                    {"name": "Очиститель контактов", "quantity": "2", "price": "350", "total": ""},
                ],
            }
        )
        self.assertEqual(materials["repair_order"]["materials_total"], "700")
        self.assertEqual(materials["repair_order"]["grand_total"], "2700")

    def test_search_cards_matches_repair_order_fields(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Nissan Teana J32",
                "title": "АКПП",
                "description": "Госномер В003НК124",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван Иванов",
                    "phone": "+7 900 123-45-67",
                    "license_plate": "В003НК124",
                    "works": [{"name": "Диагностика АКПП", "quantity": "1", "price": "2000", "total": ""}],
                },
            }
        )

        by_number = self.service.search_cards({"query": "1", "limit": 10})
        self.assertTrue(any(card["id"] == card_id for card in by_number["cards"]))

        by_client = self.service.search_cards({"query": "Иван Иванов", "limit": 10})
        self.assertEqual(by_client["cards"][0]["id"], card_id)
        self.assertIn("repair_order_client", by_client["cards"][0]["match"]["fields"])

        by_plate = self.service.search_cards({"query": "В003НК124", "limit": 10})
        self.assertEqual(by_plate["cards"][0]["id"], card_id)
        self.assertIn("repair_order_license_plate", by_plate["cards"][0]["match"]["fields"])

    def test_autofill_repair_order_preserves_manual_values_and_fills_missing_fields(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Volkswagen Tiguan II",
                "title": "ТО DSG/АКПП",
                "description": "Госномер А123АА124. Выполнить обслуживание и замену расходников.",
                "deadline": {"hours": 6},
                "vehicle_profile": {
                    "customer_name": "Петров Пётр",
                    "customer_phone": "+7 999 000-11-22",
                    "make_display": "Volkswagen",
                    "model_display": "Tiguan II",
                    "production_year": 2019,
                    "mileage": 98000,
                },
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "РУЧНОЙ КЛИЕНТ",
                    "materials": [{"name": "ATF", "quantity": "1", "price": "", "total": ""}],
                },
            }
        )

        autofilled = self.service.autofill_repair_order({"card_id": card_id})

        order = autofilled["repair_order"]
        self.assertEqual(order["number"], "1")
        self.assertEqual(order["client"], "РУЧНОЙ КЛИЕНТ")
        self.assertEqual(order["phone"], "+7 999 000-11-22")
        self.assertEqual(order["vehicle"], "Volkswagen Tiguan II")
        self.assertEqual(order["mileage"], "98000")
        self.assertEqual(order["license_plate"], "А123АА124")
        self.assertIn("замену расходников", order["comment"].lower())
        self.assertEqual(order["works"][0]["name"], "ТО DSG/АКПП")
        self.assertEqual(order["works"][0]["quantity"], "1")
        self.assertEqual(order["materials"][0]["name"], "ATF")

    def test_autofill_repair_order_extracts_structured_rows_and_client_summary_from_text(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Volkswagen Tiguan II",
                "title": "Пинки АКПП на 2-3 передаче",
                "description": (
                    "Клиент: Иван Иванов\n"
                    "Телефон: +7 900 123-45-67\n"
                    "Госномер А123АА124\n"
                    "VIN WVWZZZ1KZBP123456\n"
                    "Пробег: 145000\n"
                    "Жалоба: пинки DSG на 2-3 передаче, течь поддона.\n"
                    "Обнаружили: загрязнение масла и запотевание поддона.\n"
                    "Работы: диагностика DSG, адаптация DSG, замена масла АКПП\n"
                    "Материалы: ATF 6 л, фильтр АКПП 1 шт, прокладка поддона 1 шт\n"
                    "Рекомендовано: контрольный осмотр через 1000 км."
                ),
                "deadline": {"hours": 6},
                "vehicle_profile": {
                    "make_display": "Volkswagen",
                    "model_display": "Tiguan II",
                    "production_year": 2019,
                },
            }
        )

        autofilled = self.service.autofill_repair_order({"card_id": created["card"]["id"]})

        order = autofilled["repair_order"]
        self.assertEqual(order["client"], "Иван Иванов")
        self.assertEqual(order["phone"], "+7 900 123-45-67")
        self.assertEqual(order["license_plate"], "А123АА124")
        self.assertEqual(order["vin"], "WVWZZZ1KZBP123456")
        self.assertEqual(order["mileage"], "145000")
        self.assertIn("пинки dsg", order["reason"].lower())
        self.assertEqual([row["name"] for row in order["works"][:3]], ["Диагностика DSG", "Адаптация DSG", "Замена масла АКПП"])
        self.assertEqual([row["name"] for row in order["materials"][:3]], ["ATF", "Фильтр АКПП", "Прокладка поддона"])
        self.assertEqual(order["materials"][0]["quantity"], "6")
        self.assertEqual(order["materials"][1]["quantity"], "1")
        self.assertIn("Клиент обратился с запросом", order["client_information"])
        self.assertIn("Выполнены работы", order["client_information"])
        self.assertIn("Рекомендовано далее", order["client_information"])
        self.assertIn("Технические замечания", order["note"])

    def test_autofill_repair_order_uses_history_prices_and_merges_existing_rows(self) -> None:
        vin = "WVWZZZ1KZBP123456"
        for index in range(2):
            created = self.service.create_card(
                {
                    "vehicle": "Volkswagen Tiguan II",
                    "title": f"История DSG {index}",
                    "description": "Ранее выполненные работы",
                    "deadline": {"hours": 4},
                    "vehicle_profile": {"vin": vin},
                }
            )
            self.service.update_card(
                {
                    "card_id": created["card"]["id"],
                    "repair_order": {
                        "client": "Иван Иванов",
                        "works": [{"name": "Диагностика DSG", "quantity": "1", "price": "2500", "total": ""}],
                        "materials": [{"name": "ATF", "quantity": "6", "price": "950", "total": ""}],
                    },
                }
            )

        current = self.service.create_card(
            {
                "vehicle": "Volkswagen Tiguan II",
                "title": "Жалоба DSG",
                "description": "VIN WVWZZZ1KZBP123456\nЖалоба: пинки DSG.\nРаботы: Диагностика DSG\nМатериалы: ATF 6 л",
                "deadline": {"hours": 4},
                "vehicle_profile": {"vin": vin},
            }
        )
        card_id = current["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "materials": [{"name": "ATF", "quantity": "", "price": "", "total": ""}],
                },
            }
        )

        autofilled = self.service.autofill_repair_order({"card_id": card_id})

        order = autofilled["repair_order"]
        self.assertEqual(order["works"][0]["name"], "Диагностика DSG")
        self.assertEqual(order["works"][0]["price"], "2500")
        self.assertEqual(order["works"][0]["total"], "2500")
        self.assertEqual(len(order["materials"]), 1)
        self.assertEqual(order["materials"][0]["name"], "ATF")
        self.assertEqual(order["materials"][0]["quantity"], "6")
        self.assertEqual(order["materials"][0]["price"], "950")
        self.assertEqual(order["materials"][0]["total"], "5700")
        self.assertEqual(order["grand_total"], "8200")
        self.assertEqual(len(autofilled["meta"]["autofill_report"]["prices_applied"]), 2)

    def test_search_cards_matches_vehicle_profile_fields(self) -> None:
        created = self.service.create_card(
            {
                "title": "Проверка поиска по техкарте",
                "description": "Карточка без явного текста в описании по VIN",
                "deadline": {"hours": 4},
                "vehicle_profile": {
                    "make_display": "Suzuki",
                    "model_display": "Swift",
                    "production_year": 2014,
                    "vin": "JSAZC72S001234567",
                    "engine_code": "K12B",
                },
            }
        )
        card_id = created["card"]["id"]

        by_vin = self.service.search_cards({"query": "JSAZC72S001234567", "limit": 5})
        self.assertEqual(by_vin["meta"]["total_matches"], 1)
        self.assertEqual(by_vin["cards"][0]["id"], card_id)
        self.assertIn("vin", by_vin["cards"][0]["match"]["fields"])

        by_engine = self.service.search_cards({"query": "K12B", "limit": 5})
        self.assertEqual(by_engine["meta"]["total_matches"], 1)
        self.assertEqual(by_engine["cards"][0]["id"], card_id)
        self.assertIn("engine_code", by_engine["cards"][0]["match"]["fields"])

    def test_autofill_vehicle_data_preserves_manual_fields_and_enriches_missing_values(self) -> None:
        with patch.object(
            self.service._vehicle_profiles,
            "_enrich_from_vin_decode",
            return_value=VehicleProfile.from_dict(
                {
                    "gearbox_model": "A6GF1",
                    "gearbox_type": "automatic",
                    "source_summary": "VIN decoded",
                    "source_confidence": 0.91,
                    "autofilled_fields": ["gearbox_model", "gearbox_type"],
                    "field_sources": {
                        "gearbox_model": "official_vin_decode_nhtsa",
                        "gearbox_type": "official_vin_decode_nhtsa",
                    },
                    "source_links_or_refs": ["vin:https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended"],
                }
            ),
        ):
            autofilled = self.service.autofill_vehicle_data(
                {
                    "raw_text": "Suzuki Swift 2014 VIN JSAZC72S001234567, нужен осмотр подвески",
                    "existing_profile": {
                        "make_display": "Suzuki",
                        "model_display": "Swift",
                        "production_year": 2014,
                        "engine_code": "CUSTOM-ENGINE",
                        "manual_fields": ["engine_code", "make_display", "model_display", "production_year"],
                    },
                    "explicit_description": "Клиент жалуется на стук спереди",
                }
            )

        profile = autofilled["vehicle_profile"]
        self.assertEqual(profile["engine_code"], "CUSTOM-ENGINE")
        self.assertEqual(profile["gearbox_model"], "A6GF1")
        self.assertEqual(profile["gearbox_type"], "automatic")
        self.assertIn("engine_code", profile["manual_fields"])
        self.assertNotIn("engine_code", profile["autofilled_fields"])
        self.assertIn("gearbox_model", profile["autofilled_fields"])
        self.assertEqual(autofilled["card_draft"]["vehicle"], "Suzuki Swift 2014")

    def test_autofill_vehicle_data_extracts_contact_platform_and_transmission_details(self) -> None:
        with patch.object(self.service._vehicle_profiles, "_enrich_from_vin_decode", return_value=None):
            autofilled = self.service.autofill_vehicle_data(
                {
                    "raw_text": (
                        "Toyota Camry XV70 2019\n"
                        "Пробег: 185000\n"
                        "Клиент: Иван Петров\n"
                        "Телефон: +7 (900) 123-45-67\n"
                        "VIN JTNB11HK103456789\n"
                        "Двигатель: A25A-FKS\n"
                        "АКПП UA80E\n"
                        "Передний привод, бензин"
                    ),
                }
            )

        profile = autofilled["vehicle_profile"]
        self.assertEqual(profile["make_display"], "Toyota")
        self.assertEqual(profile["model_display"], "Camry")
        self.assertEqual(profile["generation_or_platform"], "XV70")
        self.assertEqual(profile["mileage"], 185000)
        self.assertEqual(profile["customer_name"], "Иван Петров")
        self.assertEqual(profile["customer_phone"], "+7 900 123-45-67")
        self.assertEqual(profile["gearbox_model"], "UA80E")
        self.assertEqual(profile["gearbox_type"], "automatic")
        self.assertEqual(profile["drivetrain"], "FWD")
        self.assertEqual(profile["fuel_type"], "gasoline")

    def test_autofill_vehicle_data_handles_bad_image_payload_without_crash(self) -> None:
        autofilled = self.service.autofill_vehicle_data(
            {
                "raw_text": "Toyota Camry 2019, мотор 2.5",
                "image_base64": "%%%broken-base64%%%",
                "image_filename": "vehicle.png",
                "image_mime_type": "image/png",
            }
        )

        self.assertEqual(autofilled["image_parse_status"], "image_decode_error")
        self.assertTrue(autofilled["warnings"])
        self.assertEqual(autofilled["vehicle_profile"]["make_display"], "Toyota")
        self.assertEqual(autofilled["vehicle_profile"]["model_display"], "Camry")

    def test_autofill_vehicle_data_uses_card_fields_when_raw_text_is_empty(self) -> None:
        with patch.object(self.service._vehicle_profiles, "_enrich_from_vin_decode", return_value=None):
            autofilled = self.service.autofill_vehicle_data(
                {
                    "vehicle": "Suzuki Swift 2014",
                    "title": "Suzuki Swift 2014 / подбор запчастей",
                    "description": "VIN JSAZC72S001234567\nДвигатель: K12B\nКоробка: Aisin\nПередний привод.",
                    "existing_profile": {},
                }
            )

        profile = autofilled["vehicle_profile"]
        self.assertEqual(profile["make_display"], "Suzuki")
        self.assertEqual(profile["model_display"], "Swift")
        self.assertEqual(profile["production_year"], 2014)
        self.assertEqual(profile["vin"], "JSAZC72S001234567")
        self.assertEqual(profile["engine_model"], "K12B")
        self.assertEqual(profile["gearbox_model"], "Aisin")
        self.assertEqual(profile["drivetrain"], "FWD")

    def test_autofill_vehicle_data_skips_vin_decode_when_text_already_identifies_vehicle(self) -> None:
        with patch.object(self.service._vehicle_profiles, "_enrich_from_vin_decode", return_value=None) as enrich:
            autofilled = self.service.autofill_vehicle_data(
                {
                    "raw_text": "Mazda CX 5 2019 VIN JM3KF123456789012",
                }
            )

        enrich.assert_not_called()
        profile = autofilled["vehicle_profile"]
        self.assertEqual(profile["make_display"], "Mazda")
        self.assertEqual(profile["model_display"], "CX-5")
        self.assertEqual(profile["production_year"], 2019)
        self.assertEqual(profile["vin"], "JM3KF123456789012")

    def test_autofill_vehicle_data_uses_vin_decode_when_identity_fields_are_missing(self) -> None:
        with patch.object(
            self.service._vehicle_profiles,
            "_enrich_from_vin_decode",
            return_value=VehicleProfile.from_dict(
                {
                    "make_display": "Mazda",
                    "model_display": "CX-5",
                    "production_year": 2019,
                    "vin": "JM3KF123456789012",
                    "source_summary": "VIN decoded",
                    "source_confidence": 0.91,
                    "autofilled_fields": ["make_display", "model_display", "production_year", "vin"],
                }
            ),
        ) as enrich:
            autofilled = self.service.autofill_vehicle_data(
                {
                    "raw_text": "VIN JM3KF123456789012",
                }
            )

        enrich.assert_called_once_with("JM3KF123456789012")
        profile = autofilled["vehicle_profile"]
        self.assertEqual(profile["make_display"], "Mazda")
        self.assertEqual(profile["model_display"], "CX-5")
        self.assertEqual(profile["production_year"], 2019)

    def test_bulk_move_cards_moves_many_cards_and_reports_partial_failures(self) -> None:
        created_column = self.service.create_column({"label": "MCP TEST COLUMN"})
        target_column = created_column["column"]["id"]

        first = self.service.create_card({"vehicle": "CAR-1", "title": "Bulk one", "column": "inbox", "deadline": {"hours": 3}})
        second = self.service.create_card({"vehicle": "CAR-2", "title": "Bulk two", "column": "in_progress", "deadline": {"hours": 3}})
        already_there = self.service.create_card({"vehicle": "CAR-3", "title": "Bulk three", "column": target_column, "deadline": {"hours": 3}})
        archived = self.service.create_card({"vehicle": "CAR-4", "title": "Bulk archived", "column": "done", "deadline": {"hours": 3}})
        self.service.archive_card({"card_id": archived["card"]["id"]})

        moved = self.service.bulk_move_cards(
            {
                "card_ids": [
                    first["card"]["id"],
                    second["card"]["id"],
                    already_there["card"]["id"],
                    archived["card"]["id"],
                    "missing-card",
                    first["card"]["id"],
                ],
                "column": target_column,
                "actor_name": "MCP TEST",
                "source": "mcp",
            }
        )

        self.assertEqual(moved["meta"]["requested"], 5)
        self.assertEqual(moved["meta"]["moved"], 2)
        self.assertEqual(moved["meta"]["unchanged"], 1)
        self.assertEqual(moved["meta"]["errors"], 2)
        self.assertTrue(moved["meta"]["partial_failure"])
        self.assertTrue(all(card["column"] == target_column for card in moved["moved_cards"]))
        self.assertTrue(any(card["id"] == already_there["card"]["id"] for card in moved["unchanged_cards"]))
        self.assertTrue(any(item["card_id"] == archived["card"]["id"] and item["code"] == "archived_card" for item in moved["errors"]))
        self.assertTrue(any(item["card_id"] == "missing-card" and item["code"] == "not_found" for item in moved["errors"]))

        first_after = self.service.get_card({"card_id": first["card"]["id"]})["card"]
        second_after = self.service.get_card({"card_id": second["card"]["id"]})["card"]
        self.assertEqual(first_after["column"], target_column)
        self.assertEqual(second_after["column"], target_column)

        first_log = self.service.get_card_log({"card_id": first["card"]["id"]})["events"]
        self.assertTrue(any(event["action"] == "card_moved" for event in first_log))

    def test_bulk_move_cards_handles_large_batches(self) -> None:
        created_column = self.service.create_column({"label": "BATCH TARGET"})
        target_column = created_column["column"]["id"]
        source_columns = ["inbox", "in_progress", "done"]

        card_ids: list[str] = []
        for index in range(24):
            created = self.service.create_card(
                {
                    "vehicle": f"CAR-{index}",
                    "title": f"Batch {index}",
                    "column": source_columns[index % len(source_columns)],
                    "deadline": {"hours": 2},
                }
            )
            card_ids.append(created["card"]["id"])

        moved = self.service.bulk_move_cards({"card_ids": card_ids, "column": target_column, "actor_name": "MCP TEST", "source": "mcp"})

        self.assertEqual(moved["meta"]["requested"], 24)
        self.assertEqual(moved["meta"]["moved"], 24)
        self.assertEqual(moved["meta"]["errors"], 0)
        self.assertFalse(moved["meta"]["partial_failure"])

        snapshot_cards = self.service.get_cards()["cards"]
        moved_ids = {card["id"] for card in moved["moved_cards"]}
        self.assertEqual(moved_ids, set(card_ids))
        self.assertTrue(all(card["column"] == target_column for card in snapshot_cards if card["id"] in moved_ids))

    def test_board_settings_are_exported_in_snapshot(self) -> None:
        snapshot = self.service.get_board_snapshot()

        self.assertIn("settings", snapshot)
        self.assertEqual(snapshot["settings"]["board_scale"], 1.0)

    def test_board_scale_updates_are_saved_and_audited(self) -> None:
        updated = self.service.update_board_settings({"board_scale": 1.25, "actor_name": "ОПЕРАТОР"})
        snapshot = self.service.get_board_snapshot()
        events = self.store.read_bundle()["events"]

        self.assertEqual(updated["settings"]["board_scale"], 1.25)
        self.assertEqual(updated["meta"]["previous_board_scale"], 1.0)
        self.assertTrue(updated["meta"]["changed"])
        self.assertEqual(snapshot["settings"]["board_scale"], 1.25)
        self.assertTrue(any(event.action == "board_scale_changed" for event in events))

    def test_rejects_invalid_board_scale(self) -> None:
        with self.assertRaises(ServiceError) as invalid_scale:
            self.service.update_board_settings({"board_scale": 2.0})
        self.assertEqual(invalid_scale.exception.code, "validation_error")

    def test_sticky_notes_are_created_moved_updated_and_deleted(self) -> None:
        created = self.service.create_sticky(
            {
                "text": "Проверить сход-развал",
                "x": 120,
                "y": 80,
                "deadline": {"hours": 4},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        sticky_id = created["sticky"]["id"]
        self.assertTrue(created["sticky"]["short_id"].startswith("S-"))

        snapshot = self.service.get_board_snapshot()
        self.assertIn("stickies", snapshot)
        self.assertTrue(any(item["id"] == sticky_id for item in snapshot["stickies"]))
        self.assertGreater(snapshot["meta"]["stickies_total"], 0)

        moved = self.service.move_sticky({"sticky_id": sticky_id, "x": 240, "y": 160, "actor_name": "МАСТЕР", "source": "api"})
        self.assertEqual(moved["sticky"]["x"], 240)
        self.assertEqual(moved["sticky"]["y"], 160)

        updated = self.service.update_sticky(
            {
                "sticky_id": sticky_id,
                "text": "Проверить сход-развал после замены рулевых тяг",
                "deadline": {"hours": 6},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        self.assertIn("после замены", updated["sticky"]["text"])

        deleted = self.service.delete_sticky({"sticky_id": sticky_id, "actor_name": "МАСТЕР", "source": "api"})
        self.assertTrue(deleted["deleted"])
        self.assertFalse(any(item["id"] == sticky_id for item in deleted["stickies"]))

        events = self.store.read_bundle()["events"]
        self.assertTrue(any(event.action == "sticky_created" for event in events))
        self.assertTrue(any(event.action == "sticky_moved" for event in events))
        self.assertTrue(any(event.action == "sticky_text_changed" for event in events))
        self.assertTrue(any(event.action == "sticky_deleted" for event in events))

    def test_sticky_notes_accept_total_seconds_and_short_id_lookup(self) -> None:
        created = self.service.create_sticky(
            {
                "text": "Перезвонить клиенту",
                "deadline": {"total_seconds": 3600},
                "x": 10,
                "y": 20,
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        sticky_id = created["sticky"]["id"]
        sticky_short_id = created["sticky"]["short_id"]
        self.assertGreater(created["sticky"]["remaining_seconds"], 0)

        updated = self.service.update_sticky(
            {
                "sticky_id": sticky_short_id,
                "text": "Перезвонить клиенту после согласования",
                "deadline": {"minutes": 45},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        self.assertEqual(updated["sticky"]["id"], sticky_id)
        self.assertIn("после согласования", updated["sticky"]["text"])

        deleted = self.service.delete_sticky({"sticky_id": sticky_short_id, "actor_name": "МАСТЕР", "source": "api"})
        self.assertTrue(deleted["deleted"])
        self.assertFalse(any(item["id"] == sticky_id for item in deleted["stickies"]))


    def test_gpt_wall_returns_full_context_layer(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "KIA RIO",
                "title": "ПЛАВАЕТ ХОЛОСТОЙ ХОД",
                "description": "Проверить дроссель и датчик холостого хода",
                "tags": ["СРОЧНО"],
                "deadline": {"hours": 6},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        card_short_id = created["card"]["short_id"]
        self.service.move_card({"card_id": card_id, "column": "in_progress", "actor_name": "МАСТЕР", "source": "api"})

        self.service.archive_card({"card_id": card_id, "actor_name": "MASTER", "source": "api"})
        wall = self.service.get_gpt_wall({"include_archived": True, "event_limit": 50})
        searched = self.service.search_cards({"query": card_short_id, "limit": 5, "include_archived": True})

        self.assertIn("text", wall)
        self.assertIn("board_context", wall)
        self.assertIn("sections", wall)
        self.assertIn("board_content", wall["sections"])
        self.assertIn("event_log", wall["sections"])
        self.assertIn(card_short_id, wall["text"])
        self.assertTrue(any(card["id"] == card_id for card in wall["cards"]))
        wall_card = next(card for card in wall["cards"] if card["id"] == card_id)
        self.assertIn("vehicle_profile_compact", wall_card)
        self.assertFalse(wall_card["vehicle_profile_compact"]["has_any_data"])
        self.assertTrue(any(event["card_id"] == card_id for event in wall["events"]))
        self.assertIn(card_short_id, wall["sections"]["board_content"]["text"])
        self.assertTrue(any(event["card_id"] == card_id for event in wall["sections"]["event_log"]["events"]))
        self.assertEqual(wall["board_context"]["context"]["board_scope"], "single_local_board_instance")
        self.assertEqual(wall["meta"]["active_cards"], wall["board_context"]["context"]["active_cards_total"])
        self.assertEqual(wall["meta"]["archived_cards"], wall["board_context"]["context"]["archived_cards_total"])
        self.assertEqual(searched["cards"][0]["id"], card_id)
        self.assertIn("short_id: " + card_short_id, wall["text"])
        self.assertIn(card_short_id, wall["sections"]["event_log"]["text"])
        self.assertIn("СТЕНА GPT", wall["text"])
        self.assertIn("KIA RIO / ПЛАВАЕТ ХОЛОСТОЙ ХОД", wall["text"])
        self.assertIn("МАСТЕР", wall["text"])

    def test_gpt_wall_event_log_uses_structured_lines(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "TEST CAR",
                "title": "LOG FORMAT",
                "description": "Проверка читаемости журнала",
                "deadline": {"hours": 1},
                "actor_name": "MASTER",
                "source": "api",
            }
        )
        wall = self.service.get_gpt_wall({"include_archived": True, "event_limit": 20})
        event_text = wall["sections"]["event_log"]["text"]

        self.assertIn("[event 1]", event_text)
        self.assertIn("time:", event_text)
        self.assertIn("actor:", event_text)
        self.assertIn("source:", event_text)
        self.assertIn("action:", event_text)
        self.assertIn("message:", event_text)
        self.assertIn(created["card"]["short_id"], event_text)

    def test_gpt_wall_repairs_mojibake_event_text(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "TEST CAR",
                "title": "ENCODING CHECK",
                "description": "Проверка repair для event log",
                "deadline": {"hours": 1},
                "actor_name": "MASTER",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        broken_message = "CHATGPT_AUDIT удалил столбец".encode("utf-8").decode("cp1251")
        broken_detail = "Диагностика".encode("utf-8").decode("cp1251")
        bundle = self.store.read_bundle()
        bundle["events"].append(
            AuditEvent(
                id="encoding-event",
                timestamp=utc_now().isoformat(),
                actor_name="CHATGPT_AUDIT",
                source="mcp",
                action="column_deleted",
                message=broken_message,
                details={"after": broken_detail},
                card_id=card_id,
            )
        )
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        wall = self.service.get_gpt_wall({"include_archived": True, "event_limit": 20})
        repaired_event = next(event for event in wall["events"] if event["id"] == "encoding-event")

        self.assertEqual(repaired_event["message"], "CHATGPT_AUDIT удалил столбец")
        self.assertIn("Диагностика", repaired_event["details_text"])
        self.assertIn("CHATGPT_AUDIT удалил столбец", wall["sections"]["event_log"]["text"])

    def test_gpt_wall_includes_customer_contact_fields(self) -> None:
        self.service.create_card(
            {
                "vehicle": "AUDI A4",
                "title": "КЛИЕНТ НА СВЯЗИ",
                "description": "Проверить контакты в стене GPT",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "make_display": "Audi",
                    "model_display": "A4",
                    "customer_phone": "+7 900 123-45-67",
                    "customer_name": "Иван Иванов",
                },
            }
        )

        wall = self.service.get_gpt_wall({"include_archived": True, "event_limit": 20})

        self.assertIn("клиент / телефон: +7 900 123-45-67", wall["text"])
        self.assertIn("клиент / ФИО: Иван Иванов", wall["text"])


    def test_gpt_wall_text_is_limited_to_3000_lines(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "TEST CAR",
                "title": "Много событий",
                "description": "Проверка усечения стены",
                "deadline": {"hours": 4},
            }
        )
        card_id = created["card"]["id"]

        bundle = self.store.read_bundle()
        for index in range(3600):
            bundle["events"].append(
                AuditEvent(
                    id=f"event-{index}",
                    timestamp=f"2026-04-02T12:00:00+00:00#{index:04d}",
                    actor_name="ТЕСТ",
                    source="api",
                    action="bulk_log",
                    message=f"Событие {index}",
                    details={"step": index},
                    card_id=card_id,
                )
            )
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            events=bundle["events"],
            settings=bundle["settings"],
        )

        wall = self.service.get_gpt_wall({"include_archived": True, "event_limit": 5000})

        self.assertLessEqual(len(wall["text"].splitlines()), 3000)
        self.assertIn("[СТЕНА УСЕЧЕНА]", wall["text"])
        self.assertIn("СОБЫТИЕ 1", wall["text"])
        self.assertIn("  время:", wall["text"])
        self.assertIn("  пользователь:", wall["text"])
        self.assertIn("  действие:", wall["text"])

    def test_board_context_describes_current_board_only(self) -> None:
        created_column = self.service.create_column({"label": "КУЗОВНОЙ ЦЕХ"})
        column_id = created_column["column"]["id"]
        self.service.create_card(
            {
                "vehicle": "VW POLO",
                "title": "ПОДТЯНУТЬ ГЕОМЕТРИЮ ДВЕРИ",
                "column": column_id,
                "deadline": {"hours": 6},
            }
        )
        self.service.create_sticky(
            {
                "text": "Согласовать покраску с клиентом",
                "deadline": {"hours": 2},
                "x": 80,
                "y": 120,
            }
        )

        context = self.service.get_board_context()

        self.assertEqual(context["context"]["product_name"], "AutoStop CRM")
        self.assertEqual(context["context"]["board_name"], "Current AutoStop CRM Board")
        self.assertEqual(context["context"]["board_scope"], "single_local_board_instance")
        self.assertIn("Do not use it for Trello, YouGile", context["context"]["scope_rule"])
        self.assertEqual(context["context"]["vehicle_profile_autofill_mode"], "card_content_first")
        self.assertIn("vin", context["context"]["vehicle_profile_compact_fields"])
        self.assertGreaterEqual(context["context"]["columns_total"], 1)
        self.assertEqual(context["context"]["stickies_total"], 1)
        self.assertTrue(any(column["id"] == column_id for column in context["context"]["columns"]))
        body_column = next(column for column in context["context"]["columns"] if column["id"] == column_id)
        self.assertEqual(body_column["active_cards"], 1)
        self.assertEqual(body_column["archived_cards"], 0)
        self.assertIn("[BOARD CONTEXT]", context["text"])
        self.assertIn("allowed_columns:", context["text"])
        self.assertIn("vehicle_profile_compact_fields:", context["text"])

    def test_vehicle_profile_preserves_customer_contact_fields(self) -> None:
        profile = VehicleProfile.from_dict(
            {
                "make_display": "Audi",
                "model_display": "A4",
                "mileage": 185000,
                "customer_phone": "+7 900 123-45-67",
                "customer_name": "Иван Иванов",
            }
        )

        payload = profile.to_dict()
        stored = profile.to_storage_dict()

        self.assertEqual(payload["mileage"], 185000)
        self.assertEqual(payload["customer_phone"], "+7 900 123-45-67")
        self.assertEqual(payload["customer_name"], "Иван Иванов")
        self.assertEqual(stored["mileage"], 185000)
        self.assertEqual(stored["customer_phone"], "+7 900 123-45-67")
        self.assertEqual(stored["customer_name"], "Иван Иванов")
        self.assertTrue(payload["has_any_data"])


if __name__ == "__main__":
    unittest.main()
