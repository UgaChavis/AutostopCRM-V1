from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication, QPushButton

from minimal_kanban.settings_service import SettingsService
from minimal_kanban.settings_store import SettingsStore
from minimal_kanban.services.card_service import CardService
from minimal_kanban.storage.json_store import JsonStore
from minimal_kanban.texts import (
    API_LABEL_PREFIX,
    APP_DISPLAY_NAME,
    BUTTON_HELP,
    BUTTON_NEW_CARD,
    BUTTON_NEW_COLUMN,
    CARD_STATUS_TOOLTIP_TEMPLATE,
    COLUMN_LABELS_RU,
    STATUS_LABELS_RU,
    TOOLTIP_SETTINGS,
)
from minimal_kanban.ui.main_window import MainWindow


class MainWindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        state_file = Path(self.temp_dir.name) / "state.json"
        settings_file = Path(self.temp_dir.name) / "settings.json"
        logger = logging.getLogger(f"test.ui.{self._testMethodName}")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        store = JsonStore(state_file=state_file, logger=logger)
        settings_store = SettingsStore(settings_file=settings_file, logger=logger)
        self.settings_service = SettingsService(settings_store, logger)
        self.service = CardService(store, logger)
        self.service.set_onboarding_seen(True)
        self.window = MainWindow(self.service, "http://127.0.0.1:41731", self.settings_service)

    def tearDown(self) -> None:
        self.window.close()
        self.temp_dir.cleanup()

    def test_main_texts_are_localized(self) -> None:
        self.assertIn(APP_DISPLAY_NAME, self.window.windowTitle())
        self.assertEqual(self.window.help_button.text(), BUTTON_HELP)
        self.assertEqual(self.window.new_card_button.text(), BUTTON_NEW_CARD)
        self.assertEqual(self.window.new_column_button.text(), BUTTON_NEW_COLUMN)
        self.assertEqual(self.window.settings_button.toolTip(), TOOLTIP_SETTINGS)
        self.assertEqual(self.window.api_label.text(), f"{API_LABEL_PREFIX} http://127.0.0.1:41731")
        self.assertEqual(self.window.columns["inbox"].title_label.text(), COLUMN_LABELS_RU["inbox"])
        self.assertGreater(len(self.window.columns["inbox"].empty_label.text()), 0)
        self.assertEqual(self.window.local_state_value_label.text(), "АКТИВЕН")
        self.assertEqual(self.window.access_state_value_label.text(), "ГОТОВО")
        self.assertEqual(self.window.mcp_state_value_label.text(), "ОЖИДАНИЕ")
        self.assertEqual(self.window.mcp_value_label.text(), "")

    def test_board_updates_after_creating_card(self) -> None:
        self.service.create_card({"title": "Карточка из теста", "deadline": {"days": 0, "hours": 2}})
        self.window.refresh_board(force=True)
        self.assertEqual(self.window.columns["inbox"].count_label.text(), "1")
        self.assertEqual(self.window.cards_total_value_label.text(), "1")
        self.assertEqual(self.window.columns_total_value_label.text(), str(len(self.window.columns)))

    def test_card_renders_readable_preview_and_has_no_old_buttons(self) -> None:
        base = datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc)
        long_title = "Очень длинный заголовок карточки для проверки новой читаемой двухстрочной шапки"
        long_description = "\n".join(
            [
                "Первая строка описания карточки.",
                "Вторая строка описания карточки.",
                "Третья строка описания карточки.",
                "Четвертая строка описания карточки.",
                "Пятая строка описания карточки.",
                "Шестая строка описания карточки.",
                "Седьмая строка описания карточки.",
                "Восьмая строка описания карточки.",
                "Девятая строка описания карточки.",
                "Десятая строка описания карточки.",
                "Одиннадцатая строка описания карточки.",
            ]
        )
        with patch("minimal_kanban.services.card_service.utc_now", return_value=base), patch(
            "minimal_kanban.services.card_service.utc_now_iso", return_value=base.isoformat()
        ), patch("minimal_kanban.models.utc_now", return_value=base):
            self.service.create_card(
                {
                    "title": long_title,
                    "description": long_description,
                    "deadline": {"seconds": 5},
                }
            )
            self.window.refresh_board(force=True)

        widget = next(iter(self.window._card_widgets.values()))
        description_line_height = widget.description_label.fontMetrics().lineSpacing()

        self.assertTrue(widget.title_label.wordWrap())
        self.assertNotEqual(widget.title_label.text(), long_title)
        self.assertGreaterEqual(widget.minimumHeight(), 200)
        self.assertGreaterEqual(widget.description_label.maximumHeight(), description_line_height * 8)
        self.assertGreaterEqual(widget.description_label.minimumHeight(), description_line_height * 5)
        self.assertIn("0д 00:00:", widget.timer_label.text())
        self.assertTrue(widget.deadline_label.text().startswith("до "))
        self.assertEqual(len(widget.findChildren(QPushButton)), 0)

        warning_time = base + timedelta(seconds=4)
        with patch("minimal_kanban.models.utc_now", return_value=warning_time):
            self.window.refresh_board(force=True)
        widget = next(iter(self.window._card_widgets.values()))
        expected_tooltip = CARD_STATUS_TOOLTIP_TEMPLATE.format(label=STATUS_LABELS_RU["warning"])
        self.assertEqual(widget.indicator_badge.toolTip(), expected_tooltip)

        expired_time = base + timedelta(seconds=6)
        with patch("minimal_kanban.models.utc_now", return_value=expired_time):
            self.window.refresh_board(force=True)
        widget = next(iter(self.window._card_widgets.values()))
        self.assertEqual(widget.property("status"), "expired")

    def test_card_heat_properties_follow_deadline_buckets(self) -> None:
        base = datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc)
        with patch("minimal_kanban.services.card_service.utc_now", return_value=base), patch(
            "minimal_kanban.services.card_service.utc_now_iso", return_value=base.isoformat()
        ), patch("minimal_kanban.models.utc_now", return_value=base):
            self.service.create_card({"title": "Цветовой шаг", "deadline": {"seconds": 5}})
            self.window.refresh_board(force=True)

        widget = next(iter(self.window._card_widgets.values()))
        self.assertEqual(widget.property("deadlineBucket"), 0)
        self.assertEqual(widget.property("deadlineStep"), 0)
        self.assertTrue(str(widget.property("deadlineHeatColor")).startswith("#"))

        warning_time = base + timedelta(seconds=4)
        with patch("minimal_kanban.models.utc_now", return_value=warning_time):
            self.window.refresh_board(force=True)
        widget = next(iter(self.window._card_widgets.values()))
        self.assertEqual(widget.property("deadlineBucket"), 16)
        self.assertEqual(widget.property("deadlineStep"), 80)

        expired_time = base + timedelta(seconds=6)
        with patch("minimal_kanban.models.utc_now", return_value=expired_time):
            self.window.refresh_board(force=True)
        widget = next(iter(self.window._card_widgets.values()))
        self.assertEqual(widget.property("status"), "expired")
        self.assertEqual(widget.property("deadlineBucket"), 20)
        self.assertEqual(widget.property("deadlineStep"), 100)

    def test_dynamic_column_is_rendered(self) -> None:
        column = self.service.create_column({"label": "Блокеры"})["column"]
        self.service.create_card(
            {
                "title": "Карточка в новом столбце",
                "column": column["id"],
                "deadline": {"days": 0, "hours": 6},
            }
        )
        self.window.refresh_board(force=True)

        self.assertIn(column["id"], self.window.columns)
        self.assertEqual(self.window.columns[column["id"]].title_label.text(), "Блокеры")
        self.assertEqual(self.window.columns[column["id"]].count_label.text(), "1")


    def test_access_link_updates_when_public_board_url_is_saved(self) -> None:
        settings = self.settings_service.load()
        saved = self.settings_service.save(
            settings.__class__.from_dict(
                {
                    **settings.to_dict(),
                    "local_api": {
                        **settings.local_api.to_dict(),
                        "local_api_base_url_override": "https://board.example/api",
                        "local_api_auth_mode": "bearer",
                        "local_api_bearer_token": "board-secret",
                    },
                }
            )
        )

        self.window._on_settings_saved(saved)

        self.assertEqual(self.window.access_value_label.text(), "https://board.example?access_token=board-secret")
        self.assertTrue(self.window.access_open_button.isEnabled())
        self.assertTrue(self.window.access_copy_button.isEnabled())
        self.assertEqual(self.window.access_state_value_label.text(), "ГОТОВО")

    def test_mcp_url_updates_when_public_mcp_url_is_saved(self) -> None:
        settings = self.settings_service.load()
        saved = self.settings_service.save(
            settings.__class__.from_dict(
                {
                    **settings.to_dict(),
                    "general": {
                        **settings.general.to_dict(),
                        "integration_enabled": True,
                    },
                    "mcp": {
                        **settings.mcp.to_dict(),
                        "mcp_enabled": True,
                        "public_https_base_url": "https://mcp.example",
                    },
                }
            )
        )

        self.window._on_settings_saved(saved)

        self.assertEqual(self.window.mcp_value_label.text(), "https://mcp.example/mcp")
        self.assertTrue(self.window.mcp_open_button.isEnabled())
        self.assertTrue(self.window.mcp_copy_button.isEnabled())
        self.assertEqual(self.window.mcp_state_value_label.text(), "ГОТОВО")


if __name__ == "__main__":
    unittest.main()
