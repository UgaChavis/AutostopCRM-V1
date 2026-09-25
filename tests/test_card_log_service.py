from __future__ import annotations

import json

from tests.services_case import CardServiceCase


class CardLogServiceTests(CardServiceCase):
    def test_get_card_log_supports_limit_and_meta(self) -> None:
        created = self.service.create_card(
            {
                "title": "ЛОГ КАРТОЧКИ",
                "description": "Проверка limit",
                "deadline": {"hours": 2},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "description": "Первое изменение",
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        self.service.update_card(
            {
                "card_id": card_id,
                "description": "Второе изменение",
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )

        log = self.service.get_card_log({"card_id": card_id, "limit": 2})

        self.assertEqual(log["meta"]["schema_version"], "card_journal.v2")
        self.assertEqual(log["meta"]["limit"], 2)
        self.assertEqual(log["meta"]["events_returned"], 2)
        self.assertGreaterEqual(log["meta"]["events_total"], 3)
        self.assertTrue(log["meta"]["has_more"])
        self.assertEqual(log["meta"]["event_order"], "newest_first")
        self.assertEqual(len(log["events"]), 2)
        self.assertEqual(len(log["entries"]), 2)
        self.assertGreaterEqual(len(log["days"]), 1)
        self.assertGreaterEqual(len(log["weeks"]), 1)
        self.assertGreaterEqual(len(log["months"]), 1)
        self.assertEqual(log["timeline"], log["entries"])
        self.assertIn("markdown", log)
        self.assertIn("text", log)
        self.assertEqual(log["text"], log["markdown"])
        self.assertTrue(log["markdown"].startswith("# 🧾 Журнал карточки"))
        self.assertIn("## 📊 Итоги карточки", log["markdown"])
        self.assertIn("## 🗓️ По месяцам", log["markdown"])
        self.assertIn("## 📅 По неделям", log["markdown"])
        self.assertIn("## 🧾 События по дням", log["markdown"])
        self.assertNotIn("ID:", log["markdown"])
        self.assertNotIn("inbox", log["markdown"])
        self.assertNotIn("событ.", log["markdown"])
        self.assertNotIn("изм.", log["markdown"])
        self.assertNotIn("участн.", log["markdown"])
        self.assertNotIn("T", log["markdown"].split("## 🗓️ По месяцам", 1)[0])
        self.assertEqual(log["entries"][0]["schema_version"], "card_journal.entry.v2")
        self.assertIn("display_line", log["entries"][0])
        self.assertIn("detail_lines", log["entries"][0])
        self.assertIn("journal_blocks", log["entries"][0])

        compact_log = self.service.get_card_log({"card_id": card_id, "compact": True, "limit": 2})

        self.assertEqual(compact_log["meta"]["schema_version"], "card_journal.v2")
        self.assertTrue(compact_log["meta"]["compact"])
        self.assertEqual(compact_log["meta"]["format"], "json_compact")
        self.assertEqual(compact_log["meta"]["limit"], 2)
        self.assertEqual(compact_log["meta"]["events_returned"], 2)
        self.assertEqual(len(compact_log["entries"]), 2)
        self.assertEqual(compact_log["timeline"], compact_log["entries"])
        self.assertIn("days", compact_log)
        self.assertIn("totals", compact_log)
        self.assertNotIn("events", compact_log)
        self.assertNotIn("markdown", compact_log)
        self.assertNotIn("text", compact_log)

    def test_get_card_log_compact_defaults_to_50_and_truncates_heavy_values(self) -> None:
        original_description = "Исходная строка. " * 120
        updated_description = "Обновленная длинная строка журнала. " * 120
        created = self.service.create_card(
            {
                "title": "КОМПАКТНЫЙ ЖУРНАЛ",
                "description": original_description,
                "deadline": {"hours": 2},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "description": updated_description,
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )

        compact_log = self.service.get_card_log({"card_id": card_id, "compact": True})
        entry = compact_log["entries"][0]
        block = entry["journal_blocks"][0]

        self.assertEqual(compact_log["meta"]["limit"], 50)
        self.assertTrue(compact_log["meta"]["compact"])
        self.assertLessEqual(len(block["text"]), 1200)
        raw_state = json.loads(self.state_file.read_text(encoding="utf-8"))
        raw_event = next(
            item for item in raw_state["events"] if item["action"] == "description_changed"
        )
        self.assertTrue(raw_event["details"]["full_details_archived"])
        self.assertNotIn("before", raw_event["details"])
        self.assertNotIn("after", raw_event["details"])
        self.assertNotIn("published_text", entry)
        self.assertNotIn("published_blocks", entry)
        self.assertNotIn("details_text", entry)
        self.assertNotIn("entries", compact_log["days"][0])
        self.assertNotIn("events", compact_log)

    def test_get_card_log_exposes_full_before_after_changes(self) -> None:
        created = self.service.create_card(
            {
                "title": "ЖУРНАЛ ИЗМЕНЕНИЙ",
                "description": "Первая строка\nВторая строка с важной информацией",
                "deadline": {"hours": 2},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "description": "Новая строка\nВторая строка заменена",
                "actor_name": "ПРИЁМЩИК",
                "source": "ui",
            }
        )

        full_log = self.service.get_card_log({"card_id": card_id})
        created_entry = next(
            item for item in full_log["entries"] if item["action"] == "card_created"
        )
        created_description = next(
            block for block in created_entry["journal_blocks"] if block["field"] == "description"
        )
        self.assertEqual(
            created_description["text"], "Первая строка\nВторая строка с важной информацией"
        )

        log = self.service.get_card_log(
            {"card_id": card_id, "limit": 1, "include_full_details": True}
        )
        entry = next(item for item in log["entries"] if item["action"] == "description_changed")

        self.assertEqual(entry["icon"], "📝")
        self.assertEqual(entry["action_label"], "Изменено описание")
        self.assertEqual(entry["source_label"], "интерфейс")
        self.assertEqual(entry["change_count"], 1)
        self.assertFalse(entry["has_deletion"])
        self.assertEqual(len(entry["changes"]), 1)
        change = entry["changes"][0]
        self.assertEqual(change["field"], "description")
        self.assertEqual(change["label"], "Описание")
        self.assertEqual(change["schema_version"], "card_journal.change.v2")
        self.assertEqual(change["before"], "Первая строка\nВторая строка с важной информацией")
        self.assertEqual(change["after"], "Новая строка\nВторая строка заменена")
        self.assertEqual(len(entry["journal_blocks"]), 1)
        self.assertEqual(entry["journal_blocks"][0]["title"], "Описание обновлено")
        self.assertEqual(entry["journal_blocks"][0]["text"], "Новая строка\nВторая строка заменена")
        self.assertTrue(entry["journal_blocks"][0]["is_full_value"])
        self.assertIn("📝", log["markdown"])
        self.assertIn("Описание обновлено", log["markdown"])
        self.assertNotIn("Изменено поле", log["markdown"])
        self.assertNotIn("до:", log["markdown"])
        self.assertNotIn("после:", log["markdown"])
        self.assertNotIn("Первая строка", log["markdown"])
        self.assertNotIn("Вторая строка с важной информацией", log["markdown"])
        self.assertIn("Новая строка", log["markdown"])
        self.assertIn("Вторая строка заменена", log["markdown"])
        self.assertIn("ПРИЁМЩИК", entry["display_line"])
        self.assertFalse(any("до:" in line or "после:" in line for line in entry["detail_lines"]))

    def test_get_card_log_marks_cleared_fields_as_deletions(self) -> None:
        created = self.service.create_card(
            {
                "title": "ЖУРНАЛ УДАЛЕНИЯ",
                "description": "Текст, который нельзя потерять",
                "deadline": {"hours": 2},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "description": "",
                "actor_name": "GPT",
                "source": "mcp",
            }
        )

        log = self.service.get_card_log({"card_id": card_id, "limit": 1})
        entry = next(item for item in log["entries"] if item["action"] == "description_changed")

        self.assertTrue(entry["has_deletion"])
        self.assertEqual(entry["source_label"], "MCP/GPT")
        self.assertEqual(entry["changes"][0]["kind"], "removed")
        self.assertEqual(entry["changes"][0]["before"], "Текст, который нельзя потерять")
        self.assertEqual(entry["changes"][0]["after"], "")
        self.assertEqual(entry["journal_blocks"][0]["title"], "⚠️ Описание очищено")
        self.assertEqual(entry["journal_blocks"][0]["text"], "")
        self.assertGreaterEqual(log["totals"]["deletions"], 1)
        self.assertIn("⚠️ Описание очищено", log["markdown"])
        self.assertNotIn("Очищено поле", log["markdown"])
        self.assertNotIn("Текст, который нельзя потерять", log["text"])

    def test_heavy_description_event_archives_full_details_and_hydrates_on_request(
        self,
    ) -> None:
        original_description = "Исходная строка. " * 80
        updated_description = "Новая строка. " * 80
        created = self.service.create_card(
            {
                "title": "АРХИВ АУДИТА",
                "description": original_description,
                "deadline": {"hours": 2},
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "description": updated_description,
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )

        raw_state = json.loads(self.state_file.read_text(encoding="utf-8"))
        event = next(
            item for item in raw_state["events"] if item["action"] == "description_changed"
        )
        details = event["details"]

        self.assertTrue(details["full_details_archived"])
        self.assertNotIn("before", details)
        self.assertNotIn("after", details)
        archive_ref = details["full_details_ref"]
        archive_file = self.state_file.parent / "audit-archive" / archive_ref.split("#", 1)[0]
        self.assertTrue(archive_file.exists())

        default_log = self.service.get_card_log({"card_id": card_id, "limit": 1})
        default_entry = default_log["entries"][0]
        self.assertTrue(default_entry["details"]["full_details_archived"])
        self.assertNotIn("before", default_entry["details"])
        self.assertNotIn("after", default_entry["details"])
        self.assertIn("Новая строка", default_entry["journal_blocks"][0]["text"])

        full_log = self.service.get_card_log(
            {"card_id": card_id, "limit": 1, "include_full_details": True}
        )
        full_entry = full_log["entries"][0]
        self.assertEqual(full_entry["details"]["before"], original_description.strip())
        self.assertEqual(full_entry["details"]["after"], updated_description)
        self.assertEqual(full_log["meta"]["include_full_details"], True)

    def test_get_card_log_humanizes_vehicle_profile_snapshots(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "Volkswagen Golf",
                "title": "ТЕХКАРТА В ЖУРНАЛЕ",
                "description": "Проверка читаемости техкарты",
                "deadline": {"hours": 2},
                "vehicle_profile": {"customer_name": ","},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "vehicle_profile": {
                    "make_display": "Volkswagen",
                    "model_display": "Golf",
                    "vin": "WVWZZZAUZFP518988",
                    "registration_plate": "М276УВ124",
                    "customer_name": "Иван",
                    "customer_phone": "89080162605",
                    "field_sources": {"vin": "manual_ui"},
                    "source_confidence": 0.95,
                    "warnings": [],
                },
                "actor_name": "ADMIN",
                "source": "ui",
            }
        )

        log = self.service.get_card_log({"card_id": card_id, "include_full_details": True})
        entry = next(item for item in log["entries"] if item["action"] == "vehicle_profile_updated")
        change = entry["changes"][0]

        self.assertIn('"field_sources"', change["after"])
        self.assertIn("Техкарта автомобиля заполнена", log["markdown"])
        self.assertIn("Марка: Volkswagen", log["markdown"])
        self.assertIn("Модель: Golf", log["markdown"])
        self.assertIn("Госномер: М276УВ124", log["markdown"])
        self.assertIn("Клиент: Иван", log["markdown"])
        self.assertNotIn("Клиент: ,", log["markdown"])
        self.assertNotIn("field_sources", log["markdown"])
        self.assertNotIn("source_confidence", log["markdown"])
        self.assertNotIn("manual_ui", log["markdown"])
        self.assertNotIn("{", log["markdown"])

    def test_get_card_log_humanizes_client_link_details(self) -> None:
        client = self.service.create_client(
            {"display_name": "Иван Клиент", "phone": "+7 913 111-22-33"}
        )["client"]
        created = self.service.create_card(
            {
                "vehicle": "Nissan X-Trail",
                "title": "ПРИВЯЗКА КЛИЕНТА",
                "description": "Проверка журнала клиента",
                "deadline": {"hours": 2},
                "vehicle_profile": {
                    "make_display": "Nissan",
                    "model_display": "X-Trail",
                    "vin": "JN1TANT32U0012345",
                    "registration_plate": "Н111НН124",
                },
            }
        )["card"]

        self.service.link_card_to_client(
            {
                "card_id": created["id"],
                "client_id": client["id"],
                "create_vehicle_from_card": True,
                "actor_name": "ADMIN",
                "source": "api",
            }
        )

        log = self.service.get_card_log({"card_id": created["id"]})
        entry = next(item for item in log["entries"] if item["action"] == "card_client_linked")

        self.assertIn("Клиент: Иван Клиент", log["markdown"])
        self.assertIn("Автомобиль клиента: создан из карточки", log["markdown"])
        self.assertNotIn("client id", log["markdown"].lower())
        self.assertNotIn("client vehicle id", log["markdown"].lower())
        self.assertNotIn("vehicle created", log["markdown"].lower())
        self.assertNotIn(client["id"], log["markdown"])
        self.assertEqual(entry["details"]["client_id"], client["id"])

    def test_repair_order_updates_keep_previous_snapshot_in_card_log(self) -> None:
        created = self.service.create_card(
            {
                "vehicle": "TOYOTA CAMRY",
                "title": "ЗАКАЗ-НАРЯД ЖУРНАЛ",
                "description": "Проверка заказ-наряда",
                "deadline": {"hours": 2},
            }
        )
        card_id = created["card"]["id"]
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван",
                    "reason": "Первичная причина",
                    "works": [{"name": "Диагностика", "qty": "1", "price": "1000"}],
                },
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )
        self.service.update_card(
            {
                "card_id": card_id,
                "repair_order": {
                    "client": "Иван",
                    "reason": "Причина изменена",
                    "works": [],
                },
                "actor_name": "МАСТЕР",
                "source": "api",
            }
        )

        log = self.service.get_card_log({"card_id": card_id, "include_full_details": True})
        entry = next(item for item in log["entries"] if item["action"] == "repair_order_updated")
        repair_order_change = next(
            change for change in entry["changes"] if change["field"] == "repair_order"
        )

        self.assertEqual(repair_order_change["label"], "Заказ-наряд")
        self.assertIn("Первичная причина", repair_order_change["before"])
        self.assertIn("Диагностика", repair_order_change["before"])
        self.assertIn("Причина изменена", repair_order_change["after"])
        self.assertIn("Диагностика", log["markdown"])
        self.assertIn("Заказ-наряд обновлён", log["markdown"])
        self.assertIn("Клиент: Иван", log["markdown"])
        self.assertIn("Причина обращения: Первичная причина", log["markdown"])
        self.assertIn("Работы: 1 позиция", log["markdown"])
        self.assertNotIn('"works"', log["markdown"])
        self.assertNotIn('"client"', log["markdown"])
        self.assertNotIn("Оплата: cash", log["markdown"])
        self.assertNotIn("через API", log["markdown"])
        self.assertNotIn("API", log["markdown"])
        self.assertNotIn("inbox", log["markdown"])
        self.assertNotIn("{", log["markdown"])
        self.assertIn('"works"', repair_order_change["before"])
