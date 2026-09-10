from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.models import Card
from minimal_kanban.printing.errors import PrintModuleError
from minimal_kanban.printing.service import PrintModuleService
from minimal_kanban.storage.change_feed_store import ChangeFeedStore


def _card(card_id: str) -> Card:
    timestamp = "2026-09-10T10:00:00+00:00"
    return Card(
        id=card_id,
        title=f"Карточка {card_id}",
        description="",
        column="inbox",
        archived=False,
        created_at=timestamp,
        updated_at=timestamp,
        deadline_timestamp=timestamp,
    )


class PrintingStateLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _assert_serialized_interleaving(
        self,
        *,
        read_method: str,
        first_action: Callable[[], Any],
        second_action: Callable[[], Any],
        first_service: PrintModuleService,
        second_service: PrintModuleService,
    ) -> None:
        first_read = threading.Event()
        release_first = threading.Event()
        second_lock_attempted = threading.Event()
        second_lock_acquired = threading.Event()
        errors: list[BaseException] = []
        errors_lock = threading.Lock()
        original_first_read = getattr(first_service, read_method)
        original_second_acquire = second_service._print_state_process_lock.acquire

        def paused_first_read() -> Any:
            snapshot = original_first_read()
            first_read.set()
            if not release_first.wait(timeout=5):
                raise AssertionError("first mutation was not released")
            return snapshot

        @contextmanager
        def observed_second_acquire() -> Iterator[None]:
            second_lock_attempted.set()
            with original_second_acquire():
                second_lock_acquired.set()
                yield

        def run(action: Callable[[], Any]) -> None:
            try:
                action()
            except BaseException as exc:
                with errors_lock:
                    errors.append(exc)

        first_thread = threading.Thread(target=run, args=(first_action,))
        second_thread = threading.Thread(target=run, args=(second_action,))
        second_started = False
        with (
            patch.object(first_service, read_method, side_effect=paused_first_read),
            patch.object(
                second_service._print_state_process_lock,
                "acquire",
                side_effect=observed_second_acquire,
            ),
        ):
            first_thread.start()
            try:
                self.assertTrue(first_read.wait(timeout=5))
                second_thread.start()
                second_started = True
                self.assertTrue(second_lock_attempted.wait(timeout=5))
                self.assertFalse(second_lock_acquired.is_set())
            finally:
                release_first.set()
            first_thread.join(timeout=10)
            if second_started:
                second_thread.join(timeout=10)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(second_lock_acquired.is_set())

    def test_distinct_template_creates_from_two_instances_preserve_both(self) -> None:
        first = PrintModuleService(self.base_dir)
        second = PrintModuleService(self.base_dir)

        self._assert_serialized_interleaving(
            read_method="_read_custom_templates",
            first_action=lambda: first.save_template(
                document_type="repair_order",
                name="Параллельный шаблон A",
                content='<div class="document-page">A</div>',
            ),
            second_action=lambda: second.save_template(
                document_type="repair_order",
                name="Параллельный шаблон B",
                content='<div class="document-page">B</div>',
            ),
            first_service=first,
            second_service=second,
        )

        templates = PrintModuleService(self.base_dir)._read_custom_templates()
        self.assertEqual(
            {record.name for record in templates},
            {"Параллельный шаблон A", "Параллельный шаблон B"},
        )

    def test_distinct_inspection_forms_from_two_instances_preserve_both(self) -> None:
        first = PrintModuleService(self.base_dir)
        second = PrintModuleService(self.base_dir)
        first_card = _card("inspection-card-a")
        second_card = _card("inspection-card-b")

        self._assert_serialized_interleaving(
            read_method="_read_inspection_sheet_form_map",
            first_action=lambda: first.save_inspection_sheet_form(
                first_card,
                form_data={"findings": "Результат A"},
            ),
            second_action=lambda: second.save_inspection_sheet_form(
                second_card,
                form_data={"findings": "Результат B"},
            ),
            first_service=first,
            second_service=second,
        )

        forms = PrintModuleService(self.base_dir)._read_inspection_sheet_form_map()
        self.assertEqual(forms[first_card.id]["findings"], "Результат A")
        self.assertEqual(forms[second_card.id]["findings"], "Результат B")

    def test_disjoint_settings_updates_from_two_instances_preserve_both(self) -> None:
        first = PrintModuleService(self.base_dir)
        second = PrintModuleService(self.base_dir)

        with patch("minimal_kanban.printing.service.list_printers", return_value=[]):
            self._assert_serialized_interleaving(
                read_method="_read_settings",
                first_action=lambda: first.save_settings({"default_printer": "Printer A"}),
                second_action=lambda: second.save_settings({"orientation": "landscape"}),
                first_service=first,
                second_service=second,
            )

        settings = PrintModuleService(self.base_dir)._read_settings()
        self.assertEqual(settings.default_printer, "Printer A")
        self.assertEqual(settings.orientation, "landscape")

    def test_stale_reconcile_cannot_tombstone_a_concurrent_template_save(self) -> None:
        feed_path = self.base_dir / "change_feed.sqlite3"
        reconcile_feed = ChangeFeedStore(feed_path)
        save_feed = ChangeFeedStore(feed_path)
        reconciler = PrintModuleService(self.base_dir, change_feed_store=reconcile_feed)
        saver = PrintModuleService(self.base_dir, change_feed_store=save_feed)
        before = reconcile_feed.raw_events_for_test()
        before_sequence = before[-1]["sequence"] if before else 0
        saved: list[dict[str, Any]] = []

        self._assert_serialized_interleaving(
            read_method="_read_custom_templates",
            first_action=reconciler.reconcile_change_feed,
            second_action=lambda: saved.append(
                saver.save_template(
                    document_type="repair_order",
                    name="Шаблон после сверки",
                    content='<div class="document-page">current</div>',
                )
            ),
            first_service=reconciler,
            second_service=saver,
        )

        template_id = saved[0]["template"]["id"]
        events = [
            event
            for event in reconcile_feed.raw_events_for_test()
            if event["sequence"] > before_sequence and event["entity_type"] == "print_template"
        ]
        self.assertEqual(
            [(event["entity_id"], event["change_type"], event["tombstone"]) for event in events],
            [(template_id, "create", False)],
        )
        with reconcile_feed._connection() as connection:
            stored = connection.execute(
                """
                SELECT entity_id FROM external_entity_state
                WHERE producer = 'print_module' AND entity_type = 'print_template'
                """
            ).fetchall()
        self.assertIn(template_id, {str(row["entity_id"]) for row in stored})

    def test_constructor_snapshot_serializes_with_completion_act_save(self) -> None:
        feed_path = self.base_dir / "change_feed.sqlite3"
        writer_feed = ChangeFeedStore(feed_path)
        writer = PrintModuleService(self.base_dir, change_feed_store=writer_feed)
        card = _card("completion-card")
        initial = writer.get_completion_act_form(card)
        form = initial["form"]
        form["basis"] = "Параллельный акт"
        before = writer_feed.raw_events_for_test()
        before_sequence = before[-1]["sequence"] if before else 0
        snapshot_read = threading.Event()
        release_snapshot = threading.Event()
        writer_lock_attempted = threading.Event()
        writer_lock_acquired = threading.Event()
        constructed: list[PrintModuleService] = []
        saved: list[dict[str, Any]] = []
        errors: list[BaseException] = []
        errors_lock = threading.Lock()
        original_read = PrintModuleService._read_completion_act_form_map
        original_writer_acquire = writer._completion_act_process_lock.acquire

        def paused_snapshot(service: PrintModuleService, lock_held: bool = False) -> Any:
            snapshot = original_read(service, lock_held)
            snapshot_read.set()
            if not release_snapshot.wait(timeout=5):
                raise AssertionError("constructor snapshot was not released")
            return snapshot

        @contextmanager
        def observed_writer_acquire() -> Iterator[None]:
            writer_lock_attempted.set()
            with original_writer_acquire():
                writer_lock_acquired.set()
                yield

        def run(action: Callable[[], Any]) -> None:
            try:
                action()
            except BaseException as exc:
                with errors_lock:
                    errors.append(exc)

        constructor_thread = threading.Thread(
            target=run,
            args=(
                lambda: constructed.append(
                    PrintModuleService(
                        self.base_dir,
                        change_feed_store=ChangeFeedStore(feed_path),
                    )
                ),
            ),
        )
        writer_thread = threading.Thread(
            target=run,
            args=(
                lambda: saved.append(
                    writer.save_completion_act_form(
                        card,
                        form_data=form,
                        expected_version=0,
                        idempotency_key="constructor-race-save",
                    )
                ),
            ),
        )
        writer_started = False
        with (
            patch.object(
                PrintModuleService,
                "_read_completion_act_form_map",
                new=paused_snapshot,
            ),
            patch.object(
                writer._completion_act_process_lock,
                "acquire",
                side_effect=observed_writer_acquire,
            ),
        ):
            constructor_thread.start()
            try:
                self.assertTrue(snapshot_read.wait(timeout=5))
                writer_thread.start()
                writer_started = True
                self.assertTrue(writer_lock_attempted.wait(timeout=5))
                self.assertFalse(writer_lock_acquired.is_set())
            finally:
                release_snapshot.set()
            constructor_thread.join(timeout=10)
            if writer_started:
                writer_thread.join(timeout=10)

        self.assertFalse(constructor_thread.is_alive())
        self.assertFalse(writer_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(constructed), 1)
        self.assertEqual(saved[0]["draft"]["version"], 1)
        self.assertTrue(writer_lock_acquired.is_set())
        cycle_key = writer._completion_act_cycle_key(card, card.repair_order)
        events = [
            event
            for event in writer_feed.raw_events_for_test()
            if event["sequence"] > before_sequence and event["entity_type"] == "completion_act_form"
        ]
        self.assertEqual(
            [(event["entity_id"], event["change_type"], event["tombstone"]) for event in events],
            [(cycle_key, "create", False)],
        )
        with writer_feed._connection() as connection:
            stored = connection.execute(
                """
                SELECT entity_id FROM external_entity_state
                WHERE producer = 'print_module' AND entity_type = 'completion_act_form'
                """
            ).fetchall()
        self.assertIn(cycle_key, {str(row["entity_id"]) for row in stored})

    def test_pending_completion_reconcile_cannot_overwrite_a_newer_save(self) -> None:
        feed_path = self.base_dir / "change_feed.sqlite3"
        reconcile_feed = ChangeFeedStore(feed_path)
        writer_feed = ChangeFeedStore(feed_path)
        reconciler = PrintModuleService(self.base_dir, change_feed_store=reconcile_feed)
        writer = PrintModuleService(self.base_dir, change_feed_store=writer_feed)
        card = _card("pending-completion-card")
        initial = reconciler.get_completion_act_form(card)
        initial_form = initial["form"]
        initial_form["basis"] = "Активная версия 1"
        reconciler.save_completion_act_form(
            card,
            form_data=initial_form,
            expected_version=0,
            idempotency_key="pending-active-v1",
        )
        cycle_key = reconciler._completion_act_cycle_key(card, card.repair_order)
        with patch.object(
            reconcile_feed,
            "reconcile_external_projection_slice",
            side_effect=RuntimeError("defer reset projection"),
        ):
            reset = reconciler.reset_completion_act_form(
                card,
                expected_version=1,
                idempotency_key="pending-reset-v2",
            )
        self.assertEqual(reset["draft"]["version"], 2)
        self.assertIn(cycle_key, reconciler._completion_act_feed_pending)

        current = writer.get_completion_act_form(card)
        current_form = current["form"]
        current_form["basis"] = "Активная версия 3"
        before = reconcile_feed.raw_events_for_test()
        before_sequence = before[-1]["sequence"] if before else 0
        stale_record_read = threading.Event()
        release_stale_record = threading.Event()
        writer_lock_attempted = threading.Event()
        writer_lock_acquired = threading.Event()
        saved: list[dict[str, Any]] = []
        errors: list[BaseException] = []
        errors_lock = threading.Lock()
        original_read = reconciler._read_completion_act_record
        original_writer_acquire = writer._completion_act_process_lock.acquire

        def paused_read(record_key: str, lock_held: bool = False) -> Any:
            record = original_read(record_key, lock_held=lock_held)
            stale_record_read.set()
            if not release_stale_record.wait(timeout=5):
                raise AssertionError("stale completion record was not released")
            return record

        @contextmanager
        def observed_writer_acquire() -> Iterator[None]:
            writer_lock_attempted.set()
            with original_writer_acquire():
                writer_lock_acquired.set()
                yield

        def run(action: Callable[[], Any]) -> None:
            try:
                action()
            except BaseException as exc:
                with errors_lock:
                    errors.append(exc)

        reconcile_thread = threading.Thread(target=run, args=(reconciler.reconcile_change_feed,))
        writer_thread = threading.Thread(
            target=run,
            args=(
                lambda: saved.append(
                    writer.save_completion_act_form(
                        card,
                        form_data=current_form,
                        expected_version=2,
                        idempotency_key="pending-active-v3",
                    )
                ),
            ),
        )
        writer_started = False
        with (
            patch.object(
                reconciler,
                "_read_completion_act_record",
                side_effect=paused_read,
            ),
            patch.object(
                writer._completion_act_process_lock,
                "acquire",
                side_effect=observed_writer_acquire,
            ),
        ):
            reconcile_thread.start()
            try:
                self.assertTrue(
                    stale_record_read.wait(timeout=10),
                    f"pending read was not reached; errors={errors!r}; "
                    f"pending={reconciler._completion_act_feed_pending!r}",
                )
                writer_thread.start()
                writer_started = True
                self.assertTrue(writer_lock_attempted.wait(timeout=5))
                self.assertFalse(writer_lock_acquired.is_set())
            finally:
                release_stale_record.set()
            reconcile_thread.join(timeout=10)
            if writer_started:
                writer_thread.join(timeout=10)

        self.assertFalse(reconcile_thread.is_alive())
        self.assertFalse(writer_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(saved[0]["draft"]["version"], 3)
        self.assertTrue(writer_lock_acquired.is_set())
        events = [
            event
            for event in reconcile_feed.raw_events_for_test()
            if event["sequence"] > before_sequence and event["entity_type"] == "completion_act_form"
        ]
        self.assertGreaterEqual(len(events), 2)
        self.assertTrue(events[0]["tombstone"])
        self.assertFalse(events[-1]["tombstone"])
        self.assertTrue(all(event["entity_id"] == cycle_key for event in events))
        with reconcile_feed._connection() as connection:
            stored = connection.execute(
                """
                SELECT lifecycle FROM external_entity_state
                WHERE producer = 'print_module'
                  AND entity_type = 'completion_act_form' AND entity_id = ?
                """,
                (cycle_key,),
            ).fetchone()
        self.assertIsNotNone(stored)
        self.assertEqual(str(stored["lifecycle"]), "active")

    def test_process_lock_timeout_is_stable_and_does_not_attempt_a_write(self) -> None:
        service = PrintModuleService(self.base_dir)
        with (
            patch.object(
                service._print_state_process_lock,
                "acquire",
                side_effect=TimeoutError("busy"),
            ) as acquire,
            patch.object(service, "_write_custom_templates") as write_templates,
            self.assertRaises(PrintModuleError) as raised,
        ):
            service.save_template(
                document_type="repair_order",
                name="Не должен сохраниться",
                content='<div class="document-page">blocked</div>',
            )

        self.assertEqual(raised.exception.code, "print_state_lock_timeout")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(
            raised.exception.message,
            "Данные печатного модуля временно заняты другим процессом; повторите действие.",
        )
        acquire.assert_called_once_with()
        write_templates.assert_not_called()
        self.assertFalse(service._templates_path.exists())

    def test_timeout_from_mutation_body_is_not_reclassified_as_lock_timeout(self) -> None:
        service = PrintModuleService(self.base_dir)
        body_timeout = TimeoutError("writer timed out")
        with (
            patch.object(service, "_write_custom_templates", side_effect=body_timeout),
            self.assertRaises(TimeoutError) as raised,
        ):
            service.save_template(
                document_type="repair_order",
                name="Ошибка записи",
                content='<div class="document-page">timeout</div>',
            )

        self.assertIs(raised.exception, body_timeout)


if __name__ == "__main__":
    unittest.main()
