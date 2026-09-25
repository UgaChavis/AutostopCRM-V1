from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import math
from typing import Any
from unittest.mock import patch

# The fixture import sets up the src path before importing the application module.
# ruff: noqa: I001
from tests.services_case import CardServiceCase
from minimal_kanban.models import Card
from minimal_kanban.services.errors import ServiceError


class CardServiceTimingTests(CardServiceCase):
    def _run_without_expensive_snapshot_prep(self, operation: Callable[[], Any]) -> Any:
        snapshot_service = self.service._snapshot_service
        with (
            patch.object(
                snapshot_service,
                "_column_labels",
                wraps=snapshot_service._column_labels,
            ) as column_labels,
            patch.object(
                snapshot_service,
                "_event_counts",
                wraps=snapshot_service._event_counts,
            ) as event_counts,
        ):
            result = operation()

        self.assertEqual(column_labels.call_count, 0)
        self.assertEqual(event_counts.call_count, 0)
        return result

    def test_card_ai_run_count_normalizes_invalid_runtime_values(self) -> None:
        card = Card.from_dict({"id": "card-1", "title": "Диагностика"})

        card.ai_run_count = float("nan")
        self.assertEqual(self.service._card_ai_run_count(card), 0)
        self.assertEqual(self.service._card_ai_next_interval_minutes(card, changed=False), 60)

        card.ai_run_count = "4"
        self.assertEqual(self.service._card_ai_run_count(card), 4)
        self.assertEqual(self.service._card_ai_next_interval_minutes(card, changed=False), 90)

        card.ai_run_count = 1e308
        self.assertEqual(self.service._card_ai_run_count(card), 1_000_000)

    def test_apply_indicator_defaults_invalid_deadline_total_seconds(self) -> None:
        card = Card.from_dict({"id": "card-1", "title": "Сигнал"})
        card.deadline_total_seconds = math.nan

        self.service._apply_indicator(card, "yellow")

        self.assertEqual(card.deadline_total_seconds, 10)
        self.assertEqual(card.indicator(), "yellow")

        card.deadline_total_seconds = 1e308
        self.service._apply_indicator(card, "green")
        self.assertEqual(card.deadline_total_seconds, 31_536_000)

    def test_set_card_deadline_indicator_and_list_overdue(self) -> None:
        base = datetime(2026, 3, 24, 12, 0, 0, tzinfo=UTC)
        patches = self._patch_time(base)
        with patches[0], patches[1], patches[2]:
            created = self.service.create_card(
                {"title": "Удалённая задача", "deadline": {"total_seconds": 3 * 3600}}
            )
        card_id = created["card"]["id"]

        later = base + timedelta(minutes=5)
        patches = self._patch_time(later)
        with patches[0], patches[1], patches[2]:
            deadline_updated = self.service.set_card_deadline(
                {"card_id": card_id, "deadline": {"total_seconds": 60}}
            )
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

    def test_rejects_invalid_indicator(self) -> None:
        created = self.service.create_card({"title": "Индикатор", "deadline": {"hours": 1}})
        card_id = created["card"]["id"]
        with self.assertRaises(ServiceError) as invalid_indicator:
            self.service.set_card_indicator({"card_id": card_id, "indicator": "blue"})
        self.assertEqual(invalid_indicator.exception.code, "validation_error")

    def test_list_overdue_cards_skips_expensive_prep_when_empty(self) -> None:
        overdue = self._run_without_expensive_snapshot_prep(self.service.list_overdue_cards)

        self.assertEqual(overdue["cards"], [])

    def test_get_cards_skips_expensive_prep_when_board_is_empty(self) -> None:
        cards_payload = self._run_without_expensive_snapshot_prep(self.service.get_cards)
        cards = cards_payload["cards"]

        self.assertEqual(cards, [])
        self.assertEqual(cards_payload["meta"]["total"], 0)
        self.assertEqual(cards_payload["meta"]["returned"], 0)
        self.assertFalse(cards_payload["meta"]["has_more"])

    def test_board_snapshot_skips_expensive_prep_when_there_are_no_cards(self) -> None:
        snapshot = self._run_without_expensive_snapshot_prep(self.service.get_board_snapshot)

        self.assertEqual(snapshot["cards"], [])
        self.assertEqual(snapshot["archive"], [])

    def test_list_archived_cards_skips_expensive_prep_when_archive_is_empty(self) -> None:
        archived = self._run_without_expensive_snapshot_prep(self.service.list_archived_cards)

        self.assertEqual(archived["cards"], [])
        self.assertEqual(archived["meta"]["total"], 0)
        self.assertEqual(archived["meta"]["returned"], 0)
        self.assertFalse(archived["meta"]["has_more"])

    def test_card_lifecycle_with_deadline(self) -> None:
        base = datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC)
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
        self.assertTrue(updated["meta"]["changed"])
        self.assertEqual(
            set(updated["meta"]["changed_fields"]), {"vehicle", "title", "description", "deadline"}
        )

        archived = self.service.archive_card({"card_id": card_id})
        self.assertTrue(archived["card"]["archived"])

    def test_card_timer_is_inactive_by_default_and_start_stop_are_explicit(self) -> None:
        created = self.service.create_card(
            {"title": "Таймер по необходимости", "actor_name": "ALICE", "source": "ui"}
        )["card"]
        card_id = created["id"]
        self.assertEqual(created["timer_state"], "inactive")
        self.assertFalse(created["timer_active"])
        self.assertEqual(created["remaining_seconds"], 0)
        self.assertEqual(created["deadline_progress_bucket"], 0)
        self.assertEqual(self.service.list_overdue_cards()["cards"], [])

        self.service.mark_card_seen({"card_id": card_id, "actor_name": "BOB"})
        started = self.service.start_card_timer(
            {
                "card_id": card_id,
                "deadline": {"hours": 2},
                "actor_name": "ALICE",
                "source": "ui",
            }
        )
        started_card = started["card"]
        self.assertEqual(started["meta"]["action"], "started")
        self.assertEqual(started_card["timer_state"], "running")
        self.assertTrue(started_card["timer_active"])
        self.assertGreater(started_card["remaining_seconds"], 0)
        self.assertFalse(
            self.service.get_card({"card_id": card_id, "actor_name": "BOB"})["card"][
                "has_unseen_update"
            ]
        )

        deadline_timestamp = started_card["deadline_timestamp"]
        content_updated = self.service.update_card(
            {
                "card_id": card_id,
                "description": "Обычная правка не перезапускает таймер",
                "actor_name": "ALICE",
                "source": "ui",
            }
        )["card"]
        self.assertEqual(content_updated["deadline_timestamp"], deadline_timestamp)

        self.service.mark_card_seen({"card_id": card_id, "actor_name": "BOB"})
        stopped = self.service.stop_card_timer(
            {"card_id": card_id, "actor_name": "ALICE", "source": "ui"}
        )
        self.assertEqual(stopped["meta"]["action"], "stopped")
        self.assertEqual(stopped["card"]["timer_state"], "inactive")
        self.assertEqual(stopped["card"]["remaining_seconds"], 0)
        self.assertFalse(
            self.service.get_card({"card_id": card_id, "actor_name": "BOB"})["card"][
                "has_unseen_update"
            ]
        )

        bulk = self.service.bulk_set_deadline_if_below(
            {
                "mode": "apply",
                "card_ids": [card_id],
                "min_total_seconds": 3600,
                "target_total_seconds": 7200,
                "actor_name": "ALICE",
            }
        )
        self.assertEqual(bulk["eligible"], 0)
        self.assertEqual(bulk["changed"], 0)

        restarted = self.service.start_card_timer(
            {"card_id": card_id, "actor_name": "ALICE", "source": "ui"}
        )
        self.assertEqual(restarted["card"]["deadline_total_seconds"], 7200)
        self.assertEqual(restarted["card"]["timer_state"], "running")
