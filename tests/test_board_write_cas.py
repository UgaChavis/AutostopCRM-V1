from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from minimal_kanban.services.card_service import CardService  # noqa: E402
from minimal_kanban.services.errors import ServiceError  # noqa: E402
from minimal_kanban.storage.json_store import JsonStore  # noqa: E402


class BoardWriteCasTests(unittest.TestCase):
    def _service(self, state_file: Path, logger: logging.Logger) -> CardService:
        return CardService(
            JsonStore(state_file, logger),
            logger,
            attachments_dir=state_file.parent / "attachments",
            repair_orders_dir=state_file.parent / "repair-orders",
        )

    def _logger(self) -> logging.Logger:
        logger = logging.getLogger(self.id())
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        return logger

    def _persisted(self, state_file: Path, logger: logging.Logger) -> dict:
        return JsonStore(state_file, logger).read_bundle()

    def _concurrent_card(self, service: CardService) -> str:
        return service.create_card({"title": "Concurrent card", "deadline": {"hours": 2}})["card"][
            "id"
        ]

    def test_board_settings_conflict_preserves_concurrent_write(self) -> None:
        for fast_writes in (True, False):
            with self.subTest(fast_writes=fast_writes), tempfile.TemporaryDirectory() as tmp:
                state_file = Path(tmp) / "state.json"
                logger = self._logger()
                primary = self._service(state_file, logger)
                secondary = self._service(state_file, logger)
                before_settings = deepcopy(primary._store.read_bundle()["settings"])
                original_read = primary._read_bundle_for_update
                concurrent: dict[str, str] = {}

                def read_then_write(*domains, **kwargs):
                    draft = original_read(*domains, **kwargs)
                    concurrent["card_id"] = self._concurrent_card(secondary)
                    return draft

                with (
                    patch(
                        "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                        return_value=fast_writes,
                    ),
                    patch.object(primary, "_read_bundle_for_update", side_effect=read_then_write),
                    self.assertRaises(ServiceError) as raised,
                ):
                    primary.update_board_settings(
                        {"board_scale": 1.25, "actor_name": "STALE WRITER"}
                    )

                self.assertEqual(raised.exception.code, "state_write_conflict")
                persisted = self._persisted(state_file, logger)
                self.assertEqual(persisted["settings"], before_settings)
                self.assertIn(concurrent["card_id"], {card.id for card in persisted["cards"]})
                self.assertNotIn(
                    "board_scale_changed",
                    {event.action for event in persisted["events"]},
                )

    def test_unchanged_board_settings_do_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            logger = self._logger()
            service = self._service(state_file, logger)
            service._store.read_bundle()
            service.update_board_settings({"actor_name": "OPERATOR"})
            before = state_file.read_bytes()

            with patch.object(service, "_save_bundle") as save_bundle:
                result = service.update_board_settings({"actor_name": "OPERATOR"})

            save_bundle.assert_not_called()
            self.assertFalse(result["meta"]["changed"])
            self.assertEqual(state_file.read_bytes(), before)

    def test_invalid_stored_board_scale_is_repaired_without_business_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            logger = self._logger()
            service = self._service(state_file, logger)
            service._store.set_setting("board_scale", "invalid-scale")
            before_event_ids = {event.id for event in service._store.read_bundle()["events"]}

            result = service.update_board_settings({"actor_name": "OPERATOR"})

            persisted = self._persisted(state_file, logger)
            self.assertEqual(persisted["settings"]["board_scale"], 1.0)
            self.assertFalse(result["meta"]["changed"])
            self.assertEqual(
                {event.id for event in persisted["events"]},
                before_event_ids,
            )

    def test_column_writes_conflict_instead_of_overwriting_concurrent_card(self) -> None:
        scenarios = (
            ("create", self._create_column_operation, "column_created"),
            ("rename", self._rename_column_operation, "column_renamed"),
            ("move", self._move_column_operation, "column_moved"),
            ("delete", self._delete_column_operation, "column_deleted"),
        )
        for name, prepare_operation, rejected_action in scenarios:
            for fast_writes in (True, False):
                with (
                    self.subTest(operation=name, fast_writes=fast_writes),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    self._assert_column_write_conflict(
                        Path(tmp) / "state.json",
                        prepare_operation,
                        rejected_action,
                        fast_writes=fast_writes,
                    )

    def _assert_column_write_conflict(
        self,
        state_file: Path,
        prepare_operation,
        rejected_action: str,
        *,
        fast_writes: bool,
    ) -> None:
        logger = self._logger()
        primary = self._service(state_file, logger)
        operation = prepare_operation(primary)
        secondary = self._service(state_file, logger)
        before_columns = [
            column.to_dict() for column in self._persisted(state_file, logger)["columns"]
        ]
        original_read = primary._column_service._read_bundle_for_update
        concurrent: dict[str, str] = {}

        def read_then_write(*domains, **kwargs):
            draft = original_read(*domains, **kwargs)
            concurrent["card_id"] = self._concurrent_card(secondary)
            return draft

        with (
            patch(
                "minimal_kanban.services.card_service.get_fast_state_writes_enabled",
                return_value=fast_writes,
            ),
            patch.object(
                primary._column_service,
                "_read_bundle_for_update",
                side_effect=read_then_write,
            ),
            self.assertRaises(ServiceError) as raised,
        ):
            operation()

        self.assertEqual(raised.exception.code, "state_write_conflict")
        persisted = self._persisted(state_file, logger)
        self.assertEqual(
            [column.to_dict() for column in persisted["columns"]],
            before_columns,
        )
        self.assertIn(concurrent["card_id"], {card.id for card in persisted["cards"]})
        self.assertNotIn(rejected_action, {event.action for event in persisted["events"]})

    def test_ready_column_repair_conflicts_with_concurrent_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            logger = self._logger()
            primary = self._service(state_file, logger)
            cached = primary._store.read_bundle()
            persisted_before = self._persisted(state_file, logger)
            cached["settings"]["ready_column_id"] = ""
            secondary = self._service(state_file, logger)
            original_read = primary._column_service._read_bundle_for_update
            concurrent: dict[str, str] = {}

            def read_then_write(*domains, **kwargs):
                draft = original_read(*domains, **kwargs)
                concurrent["card_id"] = self._concurrent_card(secondary)
                return draft

            with (
                patch.object(
                    primary._column_service,
                    "_read_bundle_for_update",
                    side_effect=read_then_write,
                ),
                self.assertRaises(ServiceError) as raised,
            ):
                primary.list_columns()

            self.assertEqual(raised.exception.code, "state_write_conflict")
            persisted = self._persisted(state_file, logger)
            self.assertEqual(persisted["settings"], persisted_before["settings"])
            self.assertIn(concurrent["card_id"], {card.id for card in persisted["cards"]})
            self.assertNotIn(
                "ready_column_synchronized",
                {event.action for event in persisted["events"]},
            )

    def test_onboarding_setting_conflicts_instead_of_overwriting_concurrent_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            logger = self._logger()
            primary = self._service(state_file, logger)
            secondary = self._service(state_file, logger)
            original_read = primary._read_bundle_for_update
            concurrent: dict[str, str] = {}

            def read_then_write(*domains, **kwargs):
                draft = original_read(*domains, **kwargs)
                concurrent["card_id"] = self._concurrent_card(secondary)
                return draft

            with (
                patch.object(primary, "_read_bundle_for_update", side_effect=read_then_write),
                self.assertRaises(ServiceError) as raised,
            ):
                primary.set_onboarding_seen(True)

            self.assertEqual(raised.exception.code, "state_write_conflict")
            persisted = self._persisted(state_file, logger)
            self.assertFalse(persisted["settings"]["has_seen_onboarding"])
            self.assertIn(concurrent["card_id"], {card.id for card in persisted["cards"]})

    def test_demo_seed_retries_without_overwriting_concurrent_user_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            logger = self._logger()
            primary = self._service(state_file, logger)
            secondary = self._service(state_file, logger)
            original_write = primary._store.write_bundle
            concurrent: dict[str, str] = {}

            def write_after_concurrent_card(**write_arguments):
                if not concurrent:
                    concurrent["card_id"] = self._concurrent_card(secondary)
                return original_write(**write_arguments)

            with patch.object(
                primary._store,
                "write_bundle",
                side_effect=write_after_concurrent_card,
            ):
                seeded = primary.ensure_demo_board()

            persisted = self._persisted(state_file, logger)
            self.assertFalse(seeded)
            self.assertTrue(persisted["settings"]["demo_seeded"])
            self.assertEqual([card.id for card in persisted["cards"]], [concurrent["card_id"]])

    def _create_column_operation(self, service: CardService):
        return lambda: service.create_column({"label": "STALE CREATE"})

    def _rename_column_operation(self, service: CardService):
        column = service.create_column({"label": "RENAME SOURCE"})["column"]
        return lambda: service.rename_column({"column_id": column["id"], "label": "STALE RENAME"})

    def _move_column_operation(self, service: CardService):
        first = service.create_column({"label": "MOVE FIRST"})["column"]
        second = service.create_column({"label": "MOVE SECOND"})["column"]
        return lambda: service.move_column(
            {
                "column_id": second["id"],
                "before_column_id": first["id"],
            }
        )

    def _delete_column_operation(self, service: CardService):
        column = service.create_column({"label": "STALE DELETE"})["column"]
        return lambda: service.delete_column({"column_id": column["id"]})


if __name__ == "__main__":
    unittest.main()
