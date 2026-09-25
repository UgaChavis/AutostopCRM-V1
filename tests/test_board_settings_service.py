from __future__ import annotations

# The fixture import sets up the src path before importing the application module.
# ruff: noqa: I001
from tests.services_case import CardServiceCase
from minimal_kanban.services.card_service import ServiceError


class CardServiceBoardSettingsTests(CardServiceCase):
    def test_board_settings_are_exported_in_snapshot(self) -> None:
        snapshot = self.service.get_board_snapshot()

        self.assertIn("settings", snapshot)
        self.assertEqual(snapshot["settings"]["board_scale"], 1.0)

    def test_board_scale_updates_are_saved_and_audited(self) -> None:
        updated = self.service.update_board_settings(
            {"board_scale": 1.25, "actor_name": "ОПЕРАТОР"}
        )
        snapshot = self.service.get_board_snapshot()
        events = self.store.read_bundle()["events"]

        self.assertEqual(updated["settings"]["board_scale"], 1.25)
        self.assertEqual(updated["meta"]["previous_board_scale"], 1.0)
        self.assertTrue(updated["meta"]["changed"])
        self.assertEqual(snapshot["settings"]["board_scale"], 1.25)
        self.assertTrue(any(event.action == "board_scale_changed" for event in events))

    def test_board_control_settings_are_saved_and_audited(self) -> None:
        updated = self.service.update_board_settings(
            {
                "actor_name": "ОПЕРАТОР",
                "ai_board_control": {
                    "enabled": True,
                    "interval_minutes": 30,
                    "cooldown_minutes": 90,
                },
            }
        )
        snapshot = self.service.get_board_snapshot()
        events = self.store.read_bundle()["events"]

        self.assertEqual(
            updated["settings"]["ai_board_control"],
            {"enabled": True, "interval_minutes": 30, "cooldown_minutes": 90},
        )
        self.assertEqual(
            updated["meta"]["previous_ai_board_control"],
            {"enabled": False, "interval_minutes": 20, "cooldown_minutes": 60},
        )
        self.assertTrue(updated["meta"]["board_control_changed"])
        self.assertEqual(
            snapshot["settings"]["ai_board_control"],
            {"enabled": True, "interval_minutes": 30, "cooldown_minutes": 90},
        )
        self.assertTrue(any(event.action == "board_ai_control_changed" for event in events))

    def test_board_scale_update_preserves_board_control_settings(self) -> None:
        expected = {"enabled": True, "interval_minutes": 30, "cooldown_minutes": 90}
        self.service.update_board_settings(
            {
                "actor_name": "ОПЕРАТОР",
                "ai_board_control": expected,
            }
        )

        updated = self.service.update_board_settings(
            {"board_scale": 1.15, "actor_name": "ОПЕРАТОР"}
        )
        snapshot = self.service.get_board_snapshot()

        self.assertEqual(updated["settings"]["board_scale"], 1.15)
        self.assertEqual(updated["settings"]["ai_board_control"], expected)
        self.assertFalse(updated["meta"]["board_control_changed"])
        self.assertEqual(snapshot["settings"]["ai_board_control"], expected)

    def test_rejects_invalid_board_scale(self) -> None:
        with self.assertRaises(ServiceError) as invalid_scale:
            self.service.update_board_settings({"board_scale": 2.0})
        self.assertEqual(invalid_scale.exception.code, "validation_error")

    def test_board_scale_rejects_nan_and_board_control_settings_fall_back(self) -> None:
        with self.assertRaises(ServiceError) as scale_error:
            self.service.update_board_settings({"board_scale": float("nan")})

        self.assertEqual(scale_error.exception.code, "validation_error")
        self.assertEqual(scale_error.exception.details.get("field"), "board_scale")

        with self.assertRaises(ServiceError) as bool_scale_error:
            self.service.update_board_settings({"board_scale": True})

        self.assertEqual(bool_scale_error.exception.code, "validation_error")
        self.assertEqual(bool_scale_error.exception.details.get("field"), "board_scale")

        updated = self.service.update_board_settings(
            {
                "ai_board_control": {
                    "enabled": True,
                    "interval_minutes": 1e308,
                    "cooldown_minutes": 1e308,
                }
            }
        )

        self.assertEqual(
            updated["settings"]["ai_board_control"],
            {"enabled": True, "interval_minutes": 240, "cooldown_minutes": 1440},
        )

    def test_invalid_stored_board_scale_falls_back_in_update_and_context(self) -> None:
        bundle = self.store.read_bundle()
        settings = dict(bundle["settings"])
        settings["board_scale"] = "bad-scale"
        self.store.write_bundle(
            columns=bundle["columns"],
            cards=bundle["cards"],
            stickies=bundle["stickies"],
            cashboxes=bundle["cashboxes"],
            cash_transactions=bundle["cash_transactions"],
            events=bundle["events"],
            settings=settings,
        )

        context = self.service.get_board_context()["context"]
        updated = self.service.update_board_settings(
            {"ai_board_control": {"enabled": True}, "actor_name": "ОПЕРАТОР"}
        )

        self.assertEqual(context["board_scale"], 1.0)
        self.assertEqual(updated["meta"]["previous_board_scale"], 1.0)
        self.assertEqual(updated["settings"]["board_scale"], 1.0)

    def test_rename_column_updates_label_but_keeps_id(self) -> None:
        created = self.service.create_column({"label": "TEMP RENAME"})
        column_id = created["column"]["id"]

        renamed = self.service.rename_column({"column_id": column_id, "label": "READY FOR WORK"})

        self.assertEqual(renamed["column"]["id"], column_id)
        self.assertEqual(renamed["column"]["label"], "READY FOR WORK")
        self.assertTrue(renamed["meta"]["changed"])
        self.assertEqual(renamed["meta"]["previous_label"], "TEMP RENAME")
        listed = self.service.list_columns()["columns"]
        self.assertTrue(
            any(
                column["id"] == column_id and column["label"] == "READY FOR WORK"
                for column in listed
            )
        )

    def test_rename_column_rejects_duplicate_label(self) -> None:
        self.service.create_column({"label": "FIRST CUSTOM"})
        created = self.service.create_column({"label": "SECOND CUSTOM"})

        with self.assertRaises(ServiceError) as duplicate_label:
            self.service.rename_column(
                {"column_id": created["column"]["id"], "label": "FIRST CUSTOM"}
            )
        self.assertEqual(duplicate_label.exception.code, "validation_error")

    def test_rename_column_allows_noop_for_same_label(self) -> None:
        created = self.service.create_column({"label": "UNCHANGED"})

        renamed = self.service.rename_column(
            {"column_id": created["column"]["id"], "label": "UNCHANGED"}
        )

        self.assertFalse(renamed["meta"]["changed"])
        self.assertEqual(renamed["column"]["label"], "UNCHANGED")
